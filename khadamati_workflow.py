"""Central request workflow services for Khadamati.

The legacy application stores the request itself in ``customer_requests``.
This module extends that model without replacing it: existing columns remain
the source of the current snapshot while the new tables keep auditable events,
agreements, customer-owned service assets, and completion evidence.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
from typing import Any

from khadamati_domain import DomainError, dump, iso, load, parse_datetime, public_id, row_dict, utcnow


REQUEST_STATES = {
    "matching",
    "viewed",
    "unavailable",
    "paused",
    "accepted",
    "appointmentConfirmed",
    "inProgress",
    "awaitingConfirmation",
    "qualityReview",
    "closed",
    "archived",
    "cancelled",
    "deleted",
}

REQUEST_TRANSITIONS = {
    "matching": {"viewed", "unavailable", "paused", "accepted", "cancelled", "deleted"},
    "viewed": {"matching", "unavailable", "paused", "accepted", "cancelled", "deleted"},
    "unavailable": {"matching", "paused", "cancelled", "deleted"},
    "paused": {"matching", "unavailable", "cancelled", "deleted"},
    "accepted": {"appointmentConfirmed", "inProgress", "cancelled"},
    "appointmentConfirmed": {"accepted", "inProgress", "cancelled"},
    "inProgress": {"awaitingConfirmation", "qualityReview", "cancelled"},
    "awaitingConfirmation": {"closed", "qualityReview"},
    "qualityReview": {"closed", "cancelled"},
    "closed": {"archived", "qualityReview"},
    "archived": set(),
    "cancelled": {"archived"},
    "deleted": set(),
}

ASSET_TYPES = {"home", "vehicle", "appliance", "property", "other"}
AGREEMENT_STATES = {"draft", "pending_confirmation", "confirmed", "rejected"}
IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
WORKFLOW_VERSIONS = {"legacy_v1", "booking_v2"}
FULFILLMENT_MODES = {"instant", "quoted", "project"}
PRICING_MODES = {"fixed", "quote", "project"}
EVIDENCE_POLICIES = {
    "none",
    "optional",
    "required_photo",
    "required_checklist",
    "photo_and_checklist",
}
START_VERIFICATION_MODES = {"none", "otp"}
CHANGE_ORDER_STATES = {"pending", "accepted", "rejected", "superseded"}
COMPLETION_WINDOW_MIN_HOURS = 1
COMPLETION_WINDOW_MAX_HOURS = 24 * 30


def _table_columns(con, table: str) -> set[str]:
    result: set[str] = set()
    for row in con.execute(f"PRAGMA table_info({table})"):  # nosec B608 - fixed callers
        try:
            result.add(str(row["name"]))
        except (IndexError, TypeError):
            result.add(str(row[1]))
    return result


def _ensure_column(con, table: str, column: str, definition: str) -> None:
    """Add one fixed migration column without depending on ``server`` helpers."""
    if column not in _table_columns(con, table):
        con.execute(
            f"ALTER TABLE {table} ADD COLUMN {column} {definition}"  # nosec B608 - fixed callers
        )


def install_workflow_schema(con) -> None:
    """Install additive, backward-compatible workflow storage."""
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS request_events(
          id TEXT PRIMARY KEY,
          request_id TEXT NOT NULL,
          event_type TEXT NOT NULL,
          from_status TEXT DEFAULT '',
          to_status TEXT DEFAULT '',
          actor_kind TEXT NOT NULL DEFAULT 'system',
          actor_id TEXT DEFAULT '',
          detail TEXT NOT NULL DEFAULT '{}',
          created_at TEXT DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(request_id) REFERENCES customer_requests(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS request_agreements(
          request_id TEXT PRIMARY KEY,
          provider_id TEXT NOT NULL,
          version INTEGER NOT NULL DEFAULT 1,
          appointment_at TEXT DEFAULT '',
          duration_minutes INTEGER NOT NULL DEFAULT 60,
          price_amount REAL NOT NULL DEFAULT 0,
          currency TEXT NOT NULL DEFAULT 'OMR',
          notes TEXT DEFAULT '',
          location_text TEXT DEFAULT '',
          status TEXT NOT NULL DEFAULT 'draft',
          user_confirmed_version INTEGER NOT NULL DEFAULT 0,
          provider_confirmed_version INTEGER NOT NULL DEFAULT 0,
          updated_by_kind TEXT DEFAULT '',
          updated_by_id TEXT DEFAULT '',
          created_at TEXT DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(request_id) REFERENCES customer_requests(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS service_assets(
          id TEXT PRIMARY KEY,
          user_id TEXT NOT NULL,
          name TEXT NOT NULL,
          asset_type TEXT NOT NULL DEFAULT 'other',
          category_id TEXT DEFAULT '',
          brand TEXT DEFAULT '',
          model TEXT DEFAULT '',
          year INTEGER,
          location_json TEXT NOT NULL DEFAULT '{}',
          details_json TEXT NOT NULL DEFAULT '{}',
          notes TEXT DEFAULT '',
          image_path TEXT DEFAULT '',
          active INTEGER NOT NULL DEFAULT 1,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(user_id) REFERENCES app_users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS request_completion_evidence(
          request_id TEXT PRIMARY KEY,
          provider_id TEXT NOT NULL,
          before_images TEXT NOT NULL DEFAULT '[]',
          after_images TEXT NOT NULL DEFAULT '[]',
          checklist TEXT NOT NULL DEFAULT '[]',
          note TEXT DEFAULT '',
          submitted_at TEXT DEFAULT '',
          customer_decision TEXT DEFAULT '',
          customer_note TEXT DEFAULT '',
          decided_at TEXT DEFAULT '',
          created_at TEXT DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(request_id) REFERENCES customer_requests(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS request_idempotency(
          user_id TEXT NOT NULL,
          idempotency_key TEXT NOT NULL,
          request_id TEXT NOT NULL,
          payload_hash TEXT NOT NULL,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY(user_id,idempotency_key),
          FOREIGN KEY(request_id) REFERENCES customer_requests(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS service_fulfillment_policies(
          service_value TEXT PRIMARY KEY,
          fulfillment_mode TEXT NOT NULL DEFAULT 'quoted',
          pricing_mode TEXT NOT NULL DEFAULT 'quote',
          fixed_price_amount REAL NOT NULL DEFAULT 0,
          default_duration_minutes INTEGER NOT NULL DEFAULT 60,
          evidence_policy TEXT NOT NULL DEFAULT 'optional',
          start_verification_mode TEXT NOT NULL DEFAULT 'none',
          auto_close_enabled INTEGER NOT NULL DEFAULT 0,
          completion_window_hours INTEGER NOT NULL DEFAULT 48,
          updated_by TEXT DEFAULT '',
          created_at TEXT DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS request_work_orders(
          id TEXT NOT NULL UNIQUE,
          request_id TEXT PRIMARY KEY,
          accepted_offer_id TEXT NOT NULL,
          provider_id TEXT NOT NULL,
          customer_id TEXT NOT NULL,
          fulfillment_mode TEXT NOT NULL DEFAULT 'quoted',
          service_value TEXT NOT NULL DEFAULT '',
          category_id TEXT NOT NULL DEFAULT '',
          service_id TEXT NOT NULL DEFAULT '',
          service_name TEXT NOT NULL DEFAULT '',
          price_total REAL NOT NULL DEFAULT 0,
          labor_amount REAL NOT NULL DEFAULT 0,
          materials_amount REAL NOT NULL DEFAULT 0,
          currency TEXT NOT NULL DEFAULT 'OMR',
          scope TEXT NOT NULL DEFAULT '',
          exclusions TEXT NOT NULL DEFAULT '',
          appointment_at TEXT NOT NULL DEFAULT '',
          duration_minutes INTEGER NOT NULL DEFAULT 60,
          duration_text TEXT NOT NULL DEFAULT '',
          location_snapshot TEXT NOT NULL DEFAULT '{}',
          warranty_days INTEGER NOT NULL DEFAULT 0,
          evidence_policy TEXT NOT NULL DEFAULT 'optional',
          start_verification_mode TEXT NOT NULL DEFAULT 'none',
          version INTEGER NOT NULL DEFAULT 1,
          status TEXT NOT NULL DEFAULT 'active',
          accepted_at TEXT NOT NULL,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(request_id) REFERENCES customer_requests(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS request_work_order_versions(
          request_id TEXT NOT NULL,
          version INTEGER NOT NULL,
          work_order_id TEXT NOT NULL,
          snapshot TEXT NOT NULL,
          source_kind TEXT NOT NULL DEFAULT 'offer',
          source_id TEXT NOT NULL DEFAULT '',
          actor_kind TEXT NOT NULL DEFAULT 'system',
          actor_id TEXT NOT NULL DEFAULT '',
          created_at TEXT DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY(request_id,version),
          FOREIGN KEY(request_id) REFERENCES customer_requests(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS request_change_orders(
          id TEXT PRIMARY KEY,
          request_id TEXT NOT NULL,
          proposed_by_kind TEXT NOT NULL,
          proposed_by_id TEXT NOT NULL,
          expected_version INTEGER NOT NULL,
          changes TEXT NOT NULL DEFAULT '{}',
          reason TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'pending',
          idempotency_key TEXT NOT NULL DEFAULT '',
          payload_hash TEXT NOT NULL DEFAULT '',
          decided_by_kind TEXT NOT NULL DEFAULT '',
          decided_by_id TEXT NOT NULL DEFAULT '',
          decision_idempotency_key TEXT NOT NULL DEFAULT '',
          decision_payload_hash TEXT NOT NULL DEFAULT '',
          decided_at TEXT NOT NULL DEFAULT '',
          created_at TEXT DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(request_id) REFERENCES customer_requests(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS provider_service_slots(
          id TEXT PRIMARY KEY,
          provider_id TEXT NOT NULL,
          service_value TEXT NOT NULL,
          starts_at TEXT NOT NULL,
          ends_at TEXT NOT NULL,
          duration_minutes INTEGER NOT NULL,
          price_amount REAL NOT NULL,
          currency TEXT NOT NULL DEFAULT 'OMR',
          active INTEGER NOT NULL DEFAULT 1,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(provider_id,starts_at)
        );
        CREATE TABLE IF NOT EXISTS request_slot_reservations(
          id TEXT PRIMARY KEY,
          slot_id TEXT NOT NULL,
          request_id TEXT NOT NULL UNIQUE,
          provider_id TEXT NOT NULL,
          user_id TEXT NOT NULL,
          starts_at TEXT NOT NULL,
          ends_at TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'active',
          idempotency_key TEXT NOT NULL,
          payload_hash TEXT NOT NULL,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(request_id) REFERENCES customer_requests(id) ON DELETE CASCADE,
          FOREIGN KEY(slot_id) REFERENCES provider_service_slots(id) ON DELETE RESTRICT
        );
        CREATE TABLE IF NOT EXISTS request_start_verifications(
          request_id TEXT PRIMARY KEY,
          user_id TEXT NOT NULL,
          provider_id TEXT NOT NULL,
          code_hash TEXT NOT NULL,
          expires_at TEXT NOT NULL,
          attempts INTEGER NOT NULL DEFAULT 0,
          max_attempts INTEGER NOT NULL DEFAULT 5,
          verified_at TEXT NOT NULL DEFAULT '',
          used_at TEXT NOT NULL DEFAULT '',
          created_at TEXT DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(request_id) REFERENCES customer_requests(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_request_events_timeline
          ON request_events(request_id,created_at,id);
        CREATE INDEX IF NOT EXISTS idx_service_assets_user
          ON service_assets(user_id,active,updated_at);
        CREATE INDEX IF NOT EXISTS idx_completion_provider
          ON request_completion_evidence(provider_id,submitted_at);
        CREATE INDEX IF NOT EXISTS idx_work_orders_provider
          ON request_work_orders(provider_id,status,appointment_at);
        CREATE INDEX IF NOT EXISTS idx_change_orders_request
          ON request_change_orders(request_id,status,created_at);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_change_order_pending
          ON request_change_orders(request_id) WHERE status='pending';
        CREATE UNIQUE INDEX IF NOT EXISTS idx_change_order_proposal_idempotency
          ON request_change_orders(proposed_by_kind,proposed_by_id,idempotency_key)
          WHERE idempotency_key!='';
        CREATE INDEX IF NOT EXISTS idx_provider_slots_lookup
          ON provider_service_slots(service_value,provider_id,starts_at,active);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_active_slot_reservation
          ON request_slot_reservations(slot_id) WHERE status='active';
        CREATE UNIQUE INDEX IF NOT EXISTS idx_slot_reservation_idempotency
          ON request_slot_reservations(user_id,idempotency_key);
        CREATE INDEX IF NOT EXISTS idx_provider_reservation_overlap
          ON request_slot_reservations(provider_id,status,starts_at,ends_at);
        CREATE TRIGGER IF NOT EXISTS trg_slot_reservation_history_insert
        BEFORE INSERT ON request_slot_reservations
        WHEN NEW.status IN ('active','completed') AND EXISTS(
          SELECT 1 FROM request_slot_reservations existing
          WHERE existing.slot_id=NEW.slot_id
          AND existing.status IN ('active','completed')
        )
        BEGIN
          SELECT RAISE(ABORT,'slot_historically_reserved');
        END;
        CREATE TRIGGER IF NOT EXISTS trg_slot_reservation_history_update
        BEFORE UPDATE OF slot_id,status ON request_slot_reservations
        WHEN NEW.status IN ('active','completed') AND EXISTS(
          SELECT 1 FROM request_slot_reservations existing
          WHERE existing.slot_id=NEW.slot_id AND existing.id!=NEW.id
          AND existing.status IN ('active','completed')
        )
        BEGIN
          SELECT RAISE(ABORT,'slot_historically_reserved');
        END;
        """
    )
    columns = _table_columns(con, "customer_requests")
    if "asset_id" not in columns:
        con.execute("ALTER TABLE customer_requests ADD COLUMN asset_id TEXT DEFAULT ''")
    _ensure_column(
        con, "customer_requests", "workflow_version", "TEXT NOT NULL DEFAULT 'legacy_v1'"
    )
    _ensure_column(
        con, "customer_requests", "fulfillment_mode", "TEXT NOT NULL DEFAULT 'quoted'"
    )
    _ensure_column(
        con, "customer_requests", "pricing_mode", "TEXT NOT NULL DEFAULT 'quote'"
    )
    _ensure_column(
        con, "customer_requests", "default_duration_minutes", "INTEGER NOT NULL DEFAULT 60"
    )
    # Legacy requests retain their former mandatory-after-photo behaviour. New
    # booking_v2 requests explicitly copy the configured service policy.
    _ensure_column(
        con, "customer_requests", "evidence_policy", "TEXT NOT NULL DEFAULT 'required_photo'"
    )
    _ensure_column(
        con, "customer_requests", "start_verification_mode", "TEXT NOT NULL DEFAULT 'none'"
    )
    _ensure_column(
        con, "customer_requests", "auto_close_enabled", "INTEGER NOT NULL DEFAULT 0"
    )
    _ensure_column(
        con, "customer_requests", "completion_window_hours", "INTEGER NOT NULL DEFAULT 48"
    )
    _ensure_column(con, "customer_requests", "completion_due_at", "TEXT DEFAULT ''")
    asset_columns = _table_columns(con, "service_assets")
    if "details_json" not in asset_columns:
        con.execute("ALTER TABLE service_assets ADD COLUMN details_json TEXT NOT NULL DEFAULT '{}'")
    _ensure_column(
        con, "request_completion_evidence", "checklist", "TEXT NOT NULL DEFAULT '[]'"
    )
    _ensure_column(
        con, "request_completion_evidence", "submit_idempotency_key", "TEXT DEFAULT ''"
    )
    _ensure_column(
        con, "request_completion_evidence", "submit_payload_hash", "TEXT DEFAULT ''"
    )
    _ensure_column(
        con, "request_completion_evidence", "decision_idempotency_key", "TEXT DEFAULT ''"
    )
    _ensure_column(
        con, "request_completion_evidence", "decision_payload_hash", "TEXT DEFAULT ''"
    )
    _ensure_column(
        con,
        "service_fulfillment_policies",
        "fixed_price_amount",
        "REAL NOT NULL DEFAULT 0",
    )
    notification_columns = {
        "dedupe_key": "TEXT DEFAULT ''",
        "entity_kind": "TEXT DEFAULT ''",
        "entity_id": "TEXT DEFAULT ''",
        "action_kind": "TEXT DEFAULT ''",
        "requires_action": "INTEGER NOT NULL DEFAULT 0",
        "state_version": "INTEGER NOT NULL DEFAULT 0",
        "seen_at": "TEXT DEFAULT ''",
        "read_at": "TEXT DEFAULT ''",
        "acknowledged_at": "TEXT DEFAULT ''",
        "acted_at": "TEXT DEFAULT ''",
        "dismissed_at": "TEXT DEFAULT ''",
        "snoozed_until": "TEXT DEFAULT ''",
        "superseded_at": "TEXT DEFAULT ''",
        "expires_at": "TEXT DEFAULT ''",
    }
    for column, definition in notification_columns.items():
        _ensure_column(con, "app_notifications", column, definition)
    con.execute(
        """CREATE INDEX IF NOT EXISTS idx_request_asset
        ON customer_requests(asset_id,status,created_at)"""
    )
    con.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_notification_dedupe
        ON app_notifications(dedupe_key) WHERE dedupe_key!=''"""
    )
    con.execute(
        """CREATE INDEX IF NOT EXISTS idx_notification_pending_action
        ON app_notifications(target_kind,target_id,requires_action,acted_at,
        superseded_at,dismissed_at,expires_at)"""
    )


def _safe_text(value: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _request(con, request_id: str) -> dict[str, Any]:
    row = con.execute("SELECT * FROM customer_requests WHERE id=?", (request_id,)).fetchone()
    if not row:
        raise DomainError("request_not_found", 404)
    return row_dict(row)


def _actor_owns_request(request: dict[str, Any], actor_kind: str, actor_id: str) -> bool:
    if actor_kind == "user":
        return str(request.get("user_id") or "") == str(actor_id or "")
    if actor_kind == "provider":
        return str(request.get("accepted_provider_id") or "") == str(actor_id or "")
    return actor_kind in {"admin", "system"}


def _bounded_int(value: Any, default: int, minimum: int, maximum: int, code: str) -> int:
    try:
        result = int(value if value not in (None, "") else default)
    except (TypeError, ValueError) as exc:
        raise DomainError(code) from exc
    if result < minimum or result > maximum:
        raise DomainError(code)
    return result


def _bounded_amount(value: Any, code: str = "invalid_work_order_amount") -> float:
    try:
        result = round(float(value or 0), 3)
    except (TypeError, ValueError) as exc:
        raise DomainError(code) from exc
    if result < 0 or result > 1_000_000:
        raise DomainError(code)
    return result


def _duration_minutes(value: Any, default: int = 60) -> int:
    if isinstance(value, (int, float)):
        return _bounded_int(value, default, 15, 30 * 24 * 60, "invalid_duration_minutes")
    text = _safe_text(value, 100).lower()
    match = re.search(r"(\d{1,4})", text)
    if not match:
        return default
    amount = int(match.group(1))
    if any(token in text for token in ("hour", "hours", "ساعة", "ساعات")):
        amount *= 60
    elif any(token in text for token in ("day", "days", "يوم", "أيام")):
        amount *= 24 * 60
    return min(max(amount, 15), 30 * 24 * 60)


class BookingPolicyService:
    """Server-owned service policy copied onto every new booking_v2 request."""

    def __init__(self, con):
        self.con = con

    @staticmethod
    def defaults(service_value: str) -> dict[str, Any]:
        category = str(service_value or "").split("|", 1)[0]
        evidence = "none" if category in {"education"} else "optional"
        return {
            "serviceValue": _safe_text(service_value, 160),
            "fulfillmentMode": "quoted",
            "pricingMode": "quote",
            "fixedPriceAmount": 0.0,
            "defaultDurationMinutes": 60,
            "evidencePolicy": evidence,
            "startVerificationMode": "none",
            "autoCloseEnabled": False,
            "completionWindowHours": 48,
            "updatedBy": "",
            "createdAt": "",
            "updatedAt": "",
        }

    @staticmethod
    def _serialize(row: Any) -> dict[str, Any]:
        item = row_dict(row)
        return {
            "serviceValue": item["service_value"],
            "fulfillmentMode": item["fulfillment_mode"],
            "pricingMode": item["pricing_mode"],
            "fixedPriceAmount": float(item.get("fixed_price_amount") or 0),
            "defaultDurationMinutes": int(item["default_duration_minutes"] or 60),
            "evidencePolicy": item["evidence_policy"],
            "startVerificationMode": item["start_verification_mode"],
            "autoCloseEnabled": bool(item["auto_close_enabled"]),
            "completionWindowHours": int(item["completion_window_hours"] or 48),
            "updatedBy": item["updated_by"],
            "createdAt": item["created_at"],
            "updatedAt": item["updated_at"],
        }

    def get(self, service_value: str) -> dict[str, Any]:
        row = self.con.execute(
            "SELECT * FROM service_fulfillment_policies WHERE service_value=?",
            (_safe_text(service_value, 160),),
        ).fetchone()
        return self._serialize(row) if row else self.defaults(service_value)

    def list(self) -> list[dict[str, Any]]:
        return [
            self._serialize(row)
            for row in self.con.execute(
                "SELECT * FROM service_fulfillment_policies ORDER BY service_value"
            )
        ]

    def save(self, service_value: str, data: dict[str, Any], actor_id: str) -> dict[str, Any]:
        service_value = _safe_text(service_value, 160)
        if not service_value or "|" not in service_value:
            raise DomainError("invalid_service_value")
        fulfillment = _safe_text(data.get("fulfillmentMode"), 24) or "quoted"
        pricing = _safe_text(data.get("pricingMode"), 24) or "quote"
        evidence = _safe_text(data.get("evidencePolicy"), 40) or "optional"
        verification = _safe_text(data.get("startVerificationMode"), 24) or "none"
        if fulfillment not in FULFILLMENT_MODES:
            raise DomainError("invalid_fulfillment_mode")
        if pricing not in PRICING_MODES:
            raise DomainError("invalid_pricing_mode")
        if evidence not in EVIDENCE_POLICIES:
            raise DomainError("invalid_evidence_policy")
        if verification not in START_VERIFICATION_MODES:
            raise DomainError("invalid_start_verification_mode")
        fixed_price = _bounded_amount(
            data.get("fixedPriceAmount"), "invalid_fixed_price"
        )
        if fulfillment == "instant" and (pricing != "fixed" or fixed_price <= 0):
            raise DomainError("instant_fixed_price_required")
        if fulfillment == "project" and pricing not in {"project", "quote"}:
            raise DomainError("invalid_project_pricing_mode")
        duration = _bounded_int(
            data.get("defaultDurationMinutes"), 60, 15, 30 * 24 * 60,
            "invalid_duration_minutes",
        )
        window = _bounded_int(
            data.get("completionWindowHours"), 48,
            COMPLETION_WINDOW_MIN_HOURS, COMPLETION_WINDOW_MAX_HOURS,
            "invalid_completion_window",
        )
        self.con.execute(
            """INSERT INTO service_fulfillment_policies(
            service_value,fulfillment_mode,pricing_mode,fixed_price_amount,default_duration_minutes,
            evidence_policy,start_verification_mode,auto_close_enabled,
            completion_window_hours,updated_by,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
            ON CONFLICT(service_value) DO UPDATE SET
            fulfillment_mode=excluded.fulfillment_mode,pricing_mode=excluded.pricing_mode,
            fixed_price_amount=excluded.fixed_price_amount,
            default_duration_minutes=excluded.default_duration_minutes,
            evidence_policy=excluded.evidence_policy,
            start_verification_mode=excluded.start_verification_mode,
            auto_close_enabled=excluded.auto_close_enabled,
            completion_window_hours=excluded.completion_window_hours,
            updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP""",
            (
                service_value, fulfillment, pricing, fixed_price, duration, evidence, verification,
                int(bool(data.get("autoCloseEnabled"))), window, _safe_text(actor_id, 120),
            ),
        )
        return self.get(service_value)


class RequestWorkOrderService:
    """Create an immutable accepted-offer snapshot for booking_v2 requests."""

    def __init__(self, con, *, now: datetime | None = None):
        self.con = con
        self.now = now or utcnow()

    @staticmethod
    def _serialize(row: Any) -> dict[str, Any]:
        item = row_dict(row)
        return {
            "id": item["id"],
            "requestId": item["request_id"],
            "acceptedOfferId": item["accepted_offer_id"],
            "providerId": item["provider_id"],
            "customerId": item["customer_id"],
            "fulfillmentMode": item["fulfillment_mode"],
            "serviceValue": item["service_value"],
            "categoryId": item["category_id"],
            "serviceId": item["service_id"],
            "serviceName": item["service_name"],
            "priceAmount": float(item["price_total"] or 0),
            "laborAmount": float(item["labor_amount"] or 0),
            "materialsAmount": float(item["materials_amount"] or 0),
            "currency": item["currency"] or "OMR",
            "scope": item["scope"] or "",
            "exclusions": item["exclusions"] or "",
            "appointmentAt": item["appointment_at"] or "",
            "durationMinutes": int(item["duration_minutes"] or 60),
            "durationText": item["duration_text"] or "",
            "location": load(item["location_snapshot"], {}),
            "warrantyDays": int(item["warranty_days"] or 0),
            "evidencePolicy": item["evidence_policy"],
            "startVerificationMode": item["start_verification_mode"],
            "version": int(item["version"] or 1),
            "status": item["status"],
            "acceptedAt": item["accepted_at"],
            "createdAt": item["created_at"],
            "updatedAt": item["updated_at"],
        }

    def get(self, request_id: str) -> dict[str, Any] | None:
        row = self.con.execute(
            "SELECT * FROM request_work_orders WHERE request_id=?", (request_id,)
        ).fetchone()
        return self._serialize(row) if row else None

    def versions(self, request_id: str) -> list[dict[str, Any]]:
        return [
            {
                "requestId": row["request_id"],
                "version": int(row["version"]),
                "workOrderId": row["work_order_id"],
                "snapshot": load(row["snapshot"], {}),
                "sourceKind": row["source_kind"],
                "sourceId": row["source_id"],
                "actorKind": row["actor_kind"],
                "actorId": row["actor_id"],
                "createdAt": row["created_at"],
            }
            for row in self.con.execute(
                """SELECT * FROM request_work_order_versions WHERE request_id=?
                ORDER BY version""",
                (request_id,),
            )
        ]

    def _remember_version(
        self,
        work_order: dict[str, Any],
        *,
        source_kind: str,
        source_id: str,
        actor_kind: str,
        actor_id: str,
    ) -> None:
        self.con.execute(
            """INSERT INTO request_work_order_versions(
            request_id,version,work_order_id,snapshot,source_kind,source_id,
            actor_kind,actor_id,created_at) VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                work_order["requestId"], work_order["version"], work_order["id"],
                dump(work_order), source_kind, source_id, actor_kind, actor_id,
                iso(self.now),
            ),
        )

    def accept_offer(
        self,
        request_id: str,
        user_id: str,
        offer_id: str,
        *,
        offers: list[dict[str, Any]],
        messages: list[dict[str, Any]] | None = None,
        contact_consent: dict[str, bool] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        request = _request(self.con, request_id)
        if request.get("user_id") != user_id:
            raise DomainError("request_access_denied", 403)
        if request.get("workflow_version") != "booking_v2":
            raise DomainError("booking_v2_required", 409)
        existing = self.get(request_id)
        if existing:
            if existing["acceptedOfferId"] == offer_id:
                return existing, True
            raise DomainError("offer_selection_conflict", 409)
        selected = next(
            (item for item in offers if str(item.get("id") or "") == str(offer_id or "")),
            None,
        )
        if not selected:
            raise DomainError("offer_not_found", 404)
        valid_until = parse_datetime(selected.get("validUntil"))
        if valid_until and valid_until <= self.now:
            raise DomainError("offer_expired", 409)
        provider_id = _safe_text(selected.get("providerId"), 120)
        if not provider_id:
            raise DomainError("offer_provider_required", 409)
        provider_row = self.con.execute(
            "SELECT * FROM providers WHERE id=?", (provider_id,)
        ).fetchone()
        if not provider_row:
            raise DomainError("provider_no_longer_available", 409)
        provider = row_dict(provider_row)
        service_value = _safe_text(request.get("service_value"), 160)
        category_id, separator, service_id = service_value.partition("|")
        provider_services = load(provider.get("services"), [])
        service_matches = any(
            isinstance(item, dict)
            and item.get("catId") == category_id
            and item.get("serviceId") == service_id
            and bool(item.get("active", True))
            for item in provider_services
        )
        if (
            not bool(provider.get("active"))
            or not bool(provider.get("verified"))
            or provider.get("status") != "available"
            or not bool(provider.get("request_enabled", 1))
            or bool(provider.get("deleted_at"))
            or not separator
            or not service_matches
        ):
            raise DomainError("provider_no_longer_available", 409)
        provider_areas = load(provider.get("areas"), [])
        accepted_areas = {
            str(value).strip()
            for value in [*provider_areas, provider.get("gov"), provider.get("wilayah")]
            if value
        }
        if request.get("wilayah") and request["wilayah"] not in accepted_areas:
            raise DomainError("provider_area_mismatch", 409)
        accepted_offers: list[dict[str, Any]] = []
        for raw in offers:
            item = dict(raw)
            item["status"] = "accepted" if item.get("id") == offer_id else "declined"
            accepted_offers.append(item)
        result = self.con.execute(
            """UPDATE customer_requests SET offers=?,accepted_provider_id=?,status='accepted',
            offers_open=0,waitlisted=0,contact_consent=?,messages=?,updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND user_id=? AND workflow_version='booking_v2'
            AND offers_open=1 AND COALESCE(accepted_provider_id,'')=''
            AND status IN ('matching','viewed')""",
            (
                dump(accepted_offers), provider_id,
                dump(contact_consent or {"chat": True, "whatsapp": False, "call": False}),
                dump((messages or load(request.get("messages"), []))[-120:]),
                request_id, user_id,
            ),
        )
        if result.rowcount != 1:
            existing = self.get(request_id)
            if existing and existing["acceptedOfferId"] == offer_id:
                return existing, True
            raise DomainError("offer_selection_conflict", 409)
        category_id, _, service_id = service_value.partition("|")
        labor = _bounded_amount(selected.get("laborAmount"))
        materials = _bounded_amount(selected.get("materialsAmount"))
        price = _bounded_amount(selected.get("price", selected.get("amount", 0)))
        if labor or materials:
            price = round(labor + materials, 3)
        duration_text = _safe_text(
            selected.get("duration") or selected.get("durationText"), 100
        )
        duration = _duration_minutes(
            selected.get("durationMinutes") or duration_text,
            int(request.get("default_duration_minutes") or 60),
        )
        accepted_at = iso(self.now)
        work_order_id = public_id("wo")
        self.con.execute(
            """INSERT INTO request_work_orders(
            id,request_id,accepted_offer_id,provider_id,customer_id,fulfillment_mode,
            service_value,category_id,service_id,service_name,price_total,labor_amount,
            materials_amount,currency,scope,exclusions,appointment_at,duration_minutes,
            duration_text,location_snapshot,warranty_days,evidence_policy,
            start_verification_mode,version,status,accepted_at,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'OMR',?,?,?,?,?,?,?,?,?,1,'active',?,?,?)""",
            (
                work_order_id, request_id, offer_id, provider_id, user_id,
                request.get("fulfillment_mode") or "quoted", service_value, category_id,
                service_id, _safe_text(request.get("service_name"), 160), price, labor,
                materials, _safe_text(selected.get("scope"), 1200),
                _safe_text(selected.get("exclusions"), 800),
                _safe_text(request.get("requested_at"), 80), duration, duration_text,
                dump(
                    {
                        "gov": _safe_text(request.get("gov"), 80),
                        "wilayah": _safe_text(request.get("wilayah"), 80),
                        "locationText": _safe_text(request.get("location_text"), 240),
                    }
                ),
                _bounded_int(selected.get("warrantyDays"), 0, 0, 3650, "invalid_warranty"),
                request.get("evidence_policy") or "optional",
                request.get("start_verification_mode") or "none",
                accepted_at, accepted_at, accepted_at,
            ),
        )
        work_order = self.get(request_id) or {}
        self._remember_version(
            work_order,
            source_kind="offer",
            source_id=offer_id,
            actor_kind="user",
            actor_id=user_id,
        )
        RequestLifecycleService(self.con, now=self.now).record(
            request_id,
            "work_order_created",
            actor_kind="user",
            actor_id=user_id,
            from_status=str(request.get("status") or ""),
            to_status="accepted",
            detail={"workOrderId": work_order_id, "offerId": offer_id, "version": 1},
        )
        RequestLifecycleService(self.con, now=self.now).record(
            request_id,
            "offer_accepted",
            actor_kind="user",
            actor_id=user_id,
            from_status=str(request.get("status") or ""),
            to_status="accepted",
            detail={"fulfillmentMode": request.get("fulfillment_mode") or "quoted"},
        )
        return work_order, False


class InstantBookingService:
    """Provider-published fixed-price slots and atomic direct reservations."""

    def __init__(self, con, *, now: datetime | None = None):
        self.con = con
        self.now = now or utcnow()

    @staticmethod
    def _slot(row: Any) -> dict[str, Any]:
        item = row_dict(row)
        return {
            "id": item["id"],
            "providerId": item["provider_id"],
            "serviceValue": item["service_value"],
            "startsAt": item["starts_at"],
            "endsAt": item["ends_at"],
            "durationMinutes": int(item["duration_minutes"]),
            "priceAmount": float(item["price_amount"]),
            "currency": item["currency"],
            "active": bool(item["active"]),
            "createdAt": item["created_at"],
            "updatedAt": item["updated_at"],
        }

    def _provider(self, provider_id: str, service_value: str) -> dict[str, Any]:
        row = self.con.execute(
            "SELECT * FROM providers WHERE id=?", (provider_id,)
        ).fetchone()
        if not row:
            raise DomainError("provider_not_found", 404)
        provider = row_dict(row)
        if (
            not bool(provider.get("active"))
            or not bool(provider.get("verified"))
            or provider.get("status") != "available"
            or not bool(provider.get("request_enabled", 1))
            or provider.get("deleted_at", "")
        ):
            raise DomainError("provider_no_longer_available", 409)
        category_id, separator, service_id = service_value.partition("|")
        if not separator:
            raise DomainError("invalid_service_value")
        services = load(provider.get("services"), [])
        if not any(
            isinstance(item, dict)
            and item.get("catId") == category_id
            and item.get("serviceId") == service_id
            and bool(item.get("active", True))
            for item in services
        ):
            raise DomainError("provider_service_not_available", 409)
        return provider

    @staticmethod
    def _assert_profile_availability(provider: dict[str, Any], starts_at: datetime, ends_at: datetime) -> int:
        availability = load(provider.get("availability"), {})
        days = {str(value) for value in availability.get("days", [])}
        start_clock = str(availability.get("start") or "")
        end_clock = str(availability.get("end") or "")
        try:
            daily_capacity = int(availability.get("dailyCapacity") or 0)
        except (TypeError, ValueError):
            daily_capacity = 0
        if not days or not start_clock or not end_clock or daily_capacity <= 0:
            raise DomainError("provider_availability_required", 409)
        # Provider availability is configured in Oman local time (UTC+4).
        local_start = starts_at + timedelta(hours=4)
        local_end = ends_at + timedelta(hours=4)
        if (
            str((local_start.weekday() + 1) % 7) not in days
            or local_start.date() != local_end.date()
            or local_start.strftime("%H:%M") < start_clock
            or local_end.strftime("%H:%M") > end_clock
        ):
            raise DomainError("slot_outside_provider_availability", 409)
        return daily_capacity

    def upsert_slot(
        self,
        provider_id: str,
        service_value: str,
        starts_at: str,
        *,
        slot_id: str = "",
    ) -> dict[str, Any]:
        if not self.con.in_transaction:
            self.con.execute("BEGIN IMMEDIATE")
        service_value = _safe_text(service_value, 160)
        policy = BookingPolicyService(self.con).get(service_value)
        if (
            policy["fulfillmentMode"] != "instant"
            or policy["pricingMode"] != "fixed"
            or float(policy["fixedPriceAmount"] or 0) <= 0
        ):
            raise DomainError("instant_booking_not_configured", 409)
        provider = self._provider(provider_id, service_value)
        start = parse_datetime(starts_at)
        if not start:
            raise DomainError("invalid_slot_time")
        if start < self.now + timedelta(minutes=15) or start > self.now + timedelta(days=365):
            raise DomainError("invalid_slot_time")
        duration = int(policy["defaultDurationMinutes"])
        end = start + timedelta(minutes=duration)
        self._assert_profile_availability(provider, start, end)
        slot_id = _safe_text(slot_id, 120)
        if slot_id:
            existing = self.con.execute(
                "SELECT * FROM provider_service_slots WHERE id=?", (slot_id,)
            ).fetchone()
            if not existing:
                raise DomainError("instant_slot_not_found", 404)
            if existing["provider_id"] != provider_id:
                raise DomainError("instant_slot_access_denied", 403)
            if self.con.execute(
                """SELECT 1 FROM request_slot_reservations
                WHERE slot_id=? AND status IN ('active','completed')""",
                (slot_id,),
            ).fetchone():
                raise DomainError("instant_slot_reserved", 409)
        else:
            slot_id = public_id("slot")
        overlap = self.con.execute(
            """SELECT id FROM provider_service_slots
            WHERE provider_id=? AND id!=? AND active=1
            AND starts_at<? AND ends_at>? LIMIT 1""",
            (provider_id, slot_id, iso(end), iso(start)),
        ).fetchone()
        if overlap:
            raise DomainError("instant_slot_overlap", 409)
        try:
            self.con.execute(
                """INSERT INTO provider_service_slots(
                id,provider_id,service_value,starts_at,ends_at,duration_minutes,
                price_amount,currency,active,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,'OMR',1,?,?)
                ON CONFLICT(id) DO UPDATE SET service_value=excluded.service_value,
                starts_at=excluded.starts_at,ends_at=excluded.ends_at,
                duration_minutes=excluded.duration_minutes,
                price_amount=excluded.price_amount,active=1,updated_at=excluded.updated_at""",
                (
                    slot_id, provider_id, service_value, iso(start), iso(end), duration,
                    float(policy["fixedPriceAmount"]), iso(self.now), iso(self.now),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise DomainError("instant_slot_overlap", 409) from exc
        row = self.con.execute(
            "SELECT * FROM provider_service_slots WHERE id=?", (slot_id,)
        ).fetchone()
        return self._slot(row)

    def cancel_slot(self, provider_id: str, slot_id: str) -> None:
        if not self.con.in_transaction:
            self.con.execute("BEGIN IMMEDIATE")
        row = self.con.execute(
            "SELECT * FROM provider_service_slots WHERE id=?", (slot_id,)
        ).fetchone()
        if not row:
            raise DomainError("instant_slot_not_found", 404)
        if row["provider_id"] != provider_id:
            raise DomainError("instant_slot_access_denied", 403)
        if self.con.execute(
            """SELECT 1 FROM request_slot_reservations
            WHERE slot_id=? AND status IN ('active','completed')""",
            (slot_id,),
        ).fetchone():
            raise DomainError("instant_slot_reserved", 409)
        self.con.execute(
            """UPDATE provider_service_slots SET active=0,updated_at=? WHERE id=?""",
            (iso(self.now), slot_id),
        )

    def available_slots(
        self,
        service_value: str,
        *,
        provider_id: str = "",
        starts_after: str = "",
        ends_before: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        service_value = _safe_text(service_value, 160)
        after = parse_datetime(starts_after) or self.now
        before = parse_datetime(ends_before) or (after + timedelta(days=30))
        if before <= after or before > after + timedelta(days=90):
            raise DomainError("invalid_slot_range")
        rows = self.con.execute(
            """SELECT s.* FROM provider_service_slots s
            JOIN providers p ON p.id=s.provider_id
            WHERE s.service_value=? AND (?='' OR s.provider_id=?)
            AND s.active=1 AND s.starts_at>=? AND s.ends_at<=?
            AND p.active=1 AND p.verified=1 AND p.status='available'
            AND COALESCE(p.request_enabled,1)=1 AND COALESCE(p.deleted_at,'')=''
            AND NOT EXISTS(
              SELECT 1 FROM request_slot_reservations r
              WHERE r.slot_id=s.id AND r.status IN ('active','completed')
            )
            ORDER BY s.starts_at,s.provider_id LIMIT ?""",
            (
                service_value, _safe_text(provider_id, 120), _safe_text(provider_id, 120),
                iso(after), iso(before), max(1, min(int(limit or 100), 200)),
            ),
        )
        return [self._slot(row) for row in rows]

    def provider_slots(
        self,
        provider_id: str,
        *,
        service_value: str = "",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Return one provider's slot inventory without customer information."""
        provider_id = _safe_text(provider_id, 120)
        service_value = _safe_text(service_value, 160)
        rows = self.con.execute(
            """SELECT * FROM provider_service_slots
            WHERE provider_id=? AND (?='' OR service_value=?)
            ORDER BY starts_at DESC,id DESC LIMIT ?""",
            (
                provider_id,
                service_value,
                service_value,
                max(1, min(int(limit or 200), 500)),
            ),
        )
        result: list[dict[str, Any]] = []
        for row in rows:
            item = self._slot(row)
            reservation = self.con.execute(
                """SELECT status FROM request_slot_reservations
                WHERE slot_id=? ORDER BY created_at DESC,id DESC LIMIT 1""",
                (item["id"],),
            ).fetchone()
            reservation_status = str(reservation["status"] or "") if reservation else ""
            historically_consumed = reservation_status in {"active", "completed"}
            item.update(
                {
                    "reservationStatus": reservation_status,
                    "reserved": historically_consumed,
                    "available": bool(item["active"] and not historically_consumed),
                }
            )
            result.append(item)
        return result

    def book(
        self,
        request_id: str,
        user_id: str,
        slot_id: str,
        *,
        idempotency_key: str,
    ) -> tuple[dict[str, Any], dict[str, Any], bool]:
        if not self.con.in_transaction:
            self.con.execute("BEGIN IMMEDIATE")
        request_id = _safe_text(request_id, 120)
        slot_id = _safe_text(slot_id, 120)
        idempotency_key = _safe_text(idempotency_key, 128)
        if not IDEMPOTENCY_RE.fullmatch(idempotency_key):
            raise DomainError("invalid_idempotency_key")
        payload_hash = hashlib.sha256(
            f"{request_id}:{slot_id}".encode("utf-8")
        ).hexdigest()
        prior = self.con.execute(
            """SELECT * FROM request_slot_reservations
            WHERE user_id=? AND idempotency_key=?""",
            (user_id, idempotency_key),
        ).fetchone()
        if prior:
            if prior["payload_hash"] != payload_hash:
                raise DomainError("idempotency_key_reused", 409)
            work_order = RequestWorkOrderService(self.con).get(prior["request_id"])
            slot = self.con.execute(
                "SELECT * FROM provider_service_slots WHERE id=?", (prior["slot_id"],)
            ).fetchone()
            if not work_order or not slot:
                raise DomainError("instant_booking_replay_unavailable", 409)
            return work_order, self._slot(slot), True
        request = _request(self.con, request_id)
        if request.get("user_id") != user_id:
            raise DomainError("request_access_denied", 403)
        if (
            request.get("workflow_version") != "booking_v2"
            or request.get("fulfillment_mode") != "instant"
        ):
            raise DomainError("instant_booking_required", 409)
        if request.get("status") not in {"matching", "viewed"} or request.get(
            "accepted_provider_id"
        ):
            raise DomainError("instant_booking_stage_not_allowed", 409)
        slot_row = self.con.execute(
            "SELECT * FROM provider_service_slots WHERE id=? AND active=1", (slot_id,)
        ).fetchone()
        if not slot_row:
            raise DomainError("instant_slot_not_found", 404)
        slot = self._slot(slot_row)
        if slot["serviceValue"] != request.get("service_value"):
            raise DomainError("instant_slot_service_mismatch", 409)
        start = parse_datetime(slot["startsAt"])
        end = parse_datetime(slot["endsAt"])
        if not start or not end or start <= self.now:
            raise DomainError("instant_slot_expired", 409)
        provider = self._provider(slot["providerId"], slot["serviceValue"])
        daily_capacity = self._assert_profile_availability(provider, start, end)
        policy = BookingPolicyService(self.con).get(slot["serviceValue"])
        if (
            policy["fulfillmentMode"] != "instant"
            or policy["pricingMode"] != "fixed"
            or float(policy["fixedPriceAmount"]) != slot["priceAmount"]
            or int(policy["defaultDurationMinutes"]) != slot["durationMinutes"]
        ):
            raise DomainError("instant_slot_policy_changed", 409)
        areas = load(provider.get("areas"), [])
        accepted_areas = {
            str(value).strip()
            for value in [*areas, provider.get("gov"), provider.get("wilayah")]
            if value
        }
        if request.get("wilayah") and request["wilayah"] not in accepted_areas:
            raise DomainError("provider_area_mismatch", 409)
        if self.con.execute(
            """SELECT 1 FROM request_slot_reservations
            WHERE provider_id=? AND status IN ('active','completed')
            AND starts_at<? AND ends_at>?
            LIMIT 1""",
            (slot["providerId"], slot["endsAt"], slot["startsAt"]),
        ).fetchone():
            raise DomainError("instant_slot_reserved", 409)
        local_service_date = (start + timedelta(hours=4)).date()
        daily_count = sum(
            1
            for row in self.con.execute(
                """SELECT starts_at FROM request_slot_reservations
                WHERE provider_id=? AND status IN ('active','completed')""",
                (slot["providerId"],),
            )
            if parse_datetime(row["starts_at"])
            and (parse_datetime(row["starts_at"]) + timedelta(hours=4)).date()
            == local_service_date
        )
        if int(daily_count or 0) >= daily_capacity:
            raise DomainError("provider_daily_capacity_reached", 409)
        synthetic_offer_id = f"instant:{slot_id}"
        synthetic_offer = {
            "id": synthetic_offer_id,
            "providerId": slot["providerId"],
            "price": slot["priceAmount"],
            "durationMinutes": slot["durationMinutes"],
            "duration": f"{slot['durationMinutes']} minutes",
            "scope": _safe_text(request.get("note"), 1200),
            "status": "accepted",
            "source": "instant",
            "createdAt": iso(self.now),
        }
        result = self.con.execute(
            """UPDATE customer_requests SET accepted_provider_id=?,status='accepted',
            offers=?,offers_open=0,waitlisted=0,requested_at=?,contact_consent=?,
            updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?
            AND workflow_version='booking_v2' AND fulfillment_mode='instant'
            AND status IN ('matching','viewed') AND offers_open=1
            AND COALESCE(accepted_provider_id,'')=''""",
            (
                slot["providerId"], dump([synthetic_offer]), slot["startsAt"],
                dump({"chat": True, "whatsapp": False, "call": False}),
                request_id, user_id,
            ),
        )
        if result.rowcount != 1:
            raise DomainError("instant_booking_conflict", 409)
        reservation_id = public_id("res")
        try:
            self.con.execute(
                """INSERT INTO request_slot_reservations(
                id,slot_id,request_id,provider_id,user_id,starts_at,ends_at,status,
                idempotency_key,payload_hash,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,'active',?,?,?,?)""",
                (
                    reservation_id, slot_id, request_id, slot["providerId"], user_id,
                    slot["startsAt"], slot["endsAt"], idempotency_key, payload_hash,
                    iso(self.now), iso(self.now),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise DomainError("instant_booking_conflict", 409) from exc
        service_value = slot["serviceValue"]
        category_id, _, service_id = service_value.partition("|")
        accepted_at = iso(self.now)
        work_order_id = public_id("wo")
        self.con.execute(
            """INSERT INTO request_work_orders(
            id,request_id,accepted_offer_id,provider_id,customer_id,fulfillment_mode,
            service_value,category_id,service_id,service_name,price_total,labor_amount,
            materials_amount,currency,scope,exclusions,appointment_at,duration_minutes,
            duration_text,location_snapshot,warranty_days,evidence_policy,
            start_verification_mode,version,status,accepted_at,created_at,updated_at)
            VALUES(?,?,?,?,?,'instant',?,?,?,?,?,0,0,'OMR',?,'',?,?,? ,?,0,?,?,1,
            'active',?,?,?)""",
            (
                work_order_id, request_id, synthetic_offer_id, slot["providerId"], user_id,
                service_value, category_id, service_id,
                _safe_text(request.get("service_name"), 160), slot["priceAmount"],
                _safe_text(request.get("note"), 1200), slot["startsAt"],
                slot["durationMinutes"], f"{slot['durationMinutes']} minutes",
                dump(
                    {
                        "gov": _safe_text(request.get("gov"), 80),
                        "wilayah": _safe_text(request.get("wilayah"), 80),
                        "locationText": _safe_text(request.get("location_text"), 240),
                    }
                ),
                request.get("evidence_policy") or "optional",
                request.get("start_verification_mode") or "none",
                accepted_at, accepted_at, accepted_at,
            ),
        )
        work_orders = RequestWorkOrderService(self.con, now=self.now)
        work_order = work_orders.get(request_id) or {}
        work_orders._remember_version(
            work_order,
            source_kind="instant_slot",
            source_id=slot_id,
            actor_kind="user",
            actor_id=user_id,
        )
        RequestLifecycleService(self.con, now=self.now).record(
            request_id,
            "instant_booking_confirmed",
            actor_kind="user",
            actor_id=user_id,
            from_status=str(request.get("status") or ""),
            to_status="accepted",
            detail={
                "workOrderId": work_order_id,
                "slotId": slot_id,
                "reservationId": reservation_id,
            },
        )
        RequestLifecycleService(self.con, now=self.now).record(
            request_id,
            "booking_confirmed",
            actor_kind="user",
            actor_id=user_id,
            from_status=str(request.get("status") or ""),
            to_status="accepted",
            detail={"fulfillmentMode": "instant"},
        )
        return work_order, slot, False

    def release_request(self, request_id: str) -> None:
        self.con.execute(
            """UPDATE request_slot_reservations SET status='cancelled',updated_at=?
            WHERE request_id=? AND status='active'""",
            (iso(self.now), request_id),
        )

    def complete_request(self, request_id: str) -> None:
        self.con.execute(
            """UPDATE request_slot_reservations SET status='completed',updated_at=?
            WHERE request_id=? AND status='active'""",
            (iso(self.now), request_id),
        )


def _hash_start_code(code: str) -> str:
    salt = secrets.token_hex(16)
    rounds = 160_000
    digest = hashlib.pbkdf2_hmac(
        "sha256", str(code).encode("utf-8"), salt.encode("ascii"), rounds
    ).hex()
    return f"pbkdf2_sha256${rounds}${salt}${digest}"


def _verify_start_code(code: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt, expected = str(encoded).split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", str(code).encode("utf-8"), salt.encode("ascii"), int(rounds)
        ).hex()
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError):
        return False


class StartVerificationService:
    """One-time start code returned only to the owning customer."""

    def __init__(self, con, *, now: datetime | None = None):
        self.con = con
        self.now = now or utcnow()

    def issue(self, request_id: str, user_id: str) -> dict[str, str]:
        request = _request(self.con, request_id)
        if request.get("user_id") != user_id:
            raise DomainError("start_verification_access_denied", 403)
        if (
            request.get("workflow_version") != "booking_v2"
            or request.get("start_verification_mode") != "otp"
            or request.get("status") not in {"accepted", "appointmentConfirmed"}
            or not request.get("accepted_provider_id")
        ):
            raise DomainError("start_verification_not_available", 409)
        code = f"{secrets.randbelow(1_000_000):06d}"
        expires_at = iso(self.now + timedelta(minutes=15))
        self.con.execute(
            """INSERT INTO request_start_verifications(
            request_id,user_id,provider_id,code_hash,expires_at,attempts,max_attempts,
            verified_at,used_at,created_at,updated_at)
            VALUES(?,?,?,?,?,0,5,'','',?,?)
            ON CONFLICT(request_id) DO UPDATE SET user_id=excluded.user_id,
            provider_id=excluded.provider_id,code_hash=excluded.code_hash,
            expires_at=excluded.expires_at,attempts=0,max_attempts=5,
            verified_at='',used_at='',updated_at=excluded.updated_at""",
            (
                request_id, user_id, request["accepted_provider_id"],
                _hash_start_code(code), expires_at, iso(self.now), iso(self.now),
            ),
        )
        return {"code": code, "expiresAt": expires_at}

    def consume(self, request_id: str, provider_id: str, code: str) -> None:
        request = _request(self.con, request_id)
        if request.get("accepted_provider_id") != provider_id:
            raise DomainError("start_verification_access_denied", 403)
        row = self.con.execute(
            "SELECT * FROM request_start_verifications WHERE request_id=?", (request_id,)
        ).fetchone()
        if not row:
            raise DomainError("start_verification_required", 409)
        if row["provider_id"] != provider_id:
            raise DomainError("start_verification_access_denied", 403)
        if row["used_at"] or row["verified_at"]:
            raise DomainError("start_verification_used", 409)
        expires_at = parse_datetime(row["expires_at"])
        if not expires_at or expires_at <= self.now:
            raise DomainError("start_verification_expired", 409)
        if int(row["attempts"] or 0) >= int(row["max_attempts"] or 5):
            raise DomainError("start_verification_locked", 429)
        code = str(code or "").strip()
        if not re.fullmatch(r"\d{6}", code) or not _verify_start_code(
            code, row["code_hash"]
        ):
            self.con.execute(
                """UPDATE request_start_verifications SET attempts=attempts+1,
                updated_at=? WHERE request_id=?""",
                (iso(self.now), request_id),
            )
            raise DomainError("invalid_start_verification_code", 403)
        stamp = iso(self.now)
        result = self.con.execute(
            """UPDATE request_start_verifications SET verified_at=?,used_at=?,
            updated_at=? WHERE request_id=? AND used_at='' AND verified_at=''""",
            (stamp, stamp, stamp, request_id),
        )
        if result.rowcount != 1:
            raise DomainError("start_verification_used", 409)


class RequestChangeOrderService:
    """Version-checked changes that never mutate an accepted snapshot in place."""

    CHANGE_FIELDS = {
        "appointmentAt",
        "durationMinutes",
        "priceAmount",
        "laborAmount",
        "materialsAmount",
        "scope",
        "exclusions",
        "warrantyDays",
    }

    def __init__(self, con, *, now: datetime | None = None):
        self.con = con
        self.now = now or utcnow()

    @staticmethod
    def _serialize(row: Any) -> dict[str, Any]:
        item = row_dict(row)
        return {
            "id": item["id"],
            "requestId": item["request_id"],
            "proposedByKind": item["proposed_by_kind"],
            "proposedById": item["proposed_by_id"],
            "expectedVersion": int(item["expected_version"]),
            "changes": load(item["changes"], {}),
            "reason": item["reason"],
            "status": item["status"],
            "decidedByKind": item["decided_by_kind"],
            "decidedById": item["decided_by_id"],
            "decidedAt": item["decided_at"],
            "createdAt": item["created_at"],
            "updatedAt": item["updated_at"],
        }

    def get(self, change_order_id: str) -> dict[str, Any] | None:
        row = self.con.execute(
            "SELECT * FROM request_change_orders WHERE id=?", (change_order_id,)
        ).fetchone()
        return self._serialize(row) if row else None

    def list_for_request(self, request_id: str) -> list[dict[str, Any]]:
        return [
            self._serialize(row)
            for row in self.con.execute(
                """SELECT * FROM request_change_orders WHERE request_id=?
                ORDER BY created_at DESC,id DESC""",
                (request_id,),
            )
        ]

    def _authorize(self, request: dict[str, Any], actor_kind: str, actor_id: str) -> None:
        if actor_kind not in {"user", "provider"} or not _actor_owns_request(
            request, actor_kind, actor_id
        ):
            raise DomainError("change_order_access_denied", 403)
        if request.get("workflow_version") != "booking_v2":
            raise DomainError("booking_v2_required", 409)

    @staticmethod
    def _hash(payload: dict[str, Any]) -> str:
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _validated_changes(
        self, current: dict[str, Any], raw_changes: dict[str, Any]
    ) -> dict[str, Any]:
        if not isinstance(raw_changes, dict):
            raise DomainError("invalid_change_order")
        changes: dict[str, Any] = {}
        for key in self.CHANGE_FIELDS:
            if key not in raw_changes:
                continue
            value = raw_changes[key]
            if key in {"priceAmount", "laborAmount", "materialsAmount"}:
                value = _bounded_amount(value, "invalid_change_order_amount")
            elif key == "durationMinutes":
                value = _bounded_int(
                    value, 60, 15, 30 * 24 * 60, "invalid_duration_minutes"
                )
            elif key == "warrantyDays":
                value = _bounded_int(value, 0, 0, 3650, "invalid_warranty")
            elif key == "appointmentAt":
                value = _safe_text(value, 80)
                if value and not parse_datetime(value):
                    raise DomainError("invalid_appointment_time")
            elif key == "scope":
                value = _safe_text(value, 1200)
            else:
                value = _safe_text(value, 800)
            if value != current.get(key):
                changes[key] = value
        if not changes:
            raise DomainError("change_order_no_changes")
        if "laborAmount" in changes or "materialsAmount" in changes:
            labor = float(changes.get("laborAmount", current.get("laborAmount", 0)) or 0)
            materials = float(
                changes.get("materialsAmount", current.get("materialsAmount", 0)) or 0
            )
            changes["priceAmount"] = round(labor + materials, 3)
        return changes

    def propose(
        self,
        request_id: str,
        actor_kind: str,
        actor_id: str,
        *,
        expected_version: int,
        changes: dict[str, Any],
        reason: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        request = _request(self.con, request_id)
        self._authorize(request, actor_kind, actor_id)
        if request.get("status") not in {"accepted", "appointmentConfirmed", "inProgress"}:
            raise DomainError("change_order_stage_not_allowed", 409)
        work_order_service = RequestWorkOrderService(self.con, now=self.now)
        work_order = work_order_service.get(request_id)
        if not work_order:
            raise DomainError("work_order_not_found", 404)
        if int(expected_version or 0) != work_order["version"]:
            raise DomainError("work_order_version_changed", 409)
        idempotency_key = _safe_text(idempotency_key, 128)
        if idempotency_key and not IDEMPOTENCY_RE.fullmatch(idempotency_key):
            raise DomainError("invalid_idempotency_key")
        validated = self._validated_changes(work_order, changes)
        if work_order.get("fulfillmentMode") == "instant" and {
            "appointmentAt",
            "durationMinutes",
        } & set(validated):
            # An instant appointment is bound to its reserved server slot.  A
            # safe schedule change therefore requires an explicit cancel and
            # rebooking flow instead of silently desynchronising the Work
            # Order from the reservation or bypassing overlap validation.
            raise DomainError("instant_schedule_change_requires_rebooking", 409)
        payload = {
            "requestId": request_id,
            "expectedVersion": expected_version,
            "changes": validated,
            "reason": _safe_text(reason, 600),
        }
        payload_hash = self._hash(payload)
        if idempotency_key:
            prior = self.con.execute(
                """SELECT * FROM request_change_orders WHERE proposed_by_kind=?
                AND proposed_by_id=? AND idempotency_key=?""",
                (actor_kind, actor_id, idempotency_key),
            ).fetchone()
            if prior:
                if prior["payload_hash"] != payload_hash:
                    raise DomainError("idempotency_key_reused", 409)
                return self._serialize(prior)
        change_order_id = public_id("chg")
        try:
            self.con.execute(
                """INSERT INTO request_change_orders(
                id,request_id,proposed_by_kind,proposed_by_id,expected_version,
                changes,reason,status,idempotency_key,payload_hash,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,'pending',?,?,?,?)""",
                (
                    change_order_id, request_id, actor_kind, actor_id,
                    expected_version, dump(validated), _safe_text(reason, 600),
                    idempotency_key, payload_hash, iso(self.now), iso(self.now),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise DomainError("change_order_pending", 409) from exc
        RequestLifecycleService(self.con, now=self.now).record(
            request_id,
            "change_order_proposed",
            actor_kind=actor_kind,
            actor_id=actor_id,
            detail={
                "changeOrderId": change_order_id,
                "expectedVersion": expected_version,
                "changedFields": sorted(validated),
            },
        )
        return self.get(change_order_id) or {}

    def decide(
        self,
        change_order_id: str,
        actor_kind: str,
        actor_id: str,
        *,
        decision: str,
        expected_version: int,
        idempotency_key: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        row = self.con.execute(
            "SELECT * FROM request_change_orders WHERE id=?", (change_order_id,)
        ).fetchone()
        if not row:
            raise DomainError("change_order_not_found", 404)
        request = _request(self.con, row["request_id"])
        self._authorize(request, actor_kind, actor_id)
        decision = _safe_text(decision, 20)
        if decision not in {"accepted", "rejected"}:
            raise DomainError("invalid_change_order_decision")
        idempotency_key = _safe_text(idempotency_key, 128)
        if idempotency_key and not IDEMPOTENCY_RE.fullmatch(idempotency_key):
            raise DomainError("invalid_idempotency_key")
        payload_hash = self._hash(
            {
                "changeOrderId": change_order_id,
                "decision": decision,
                "expectedVersion": expected_version,
            }
        )
        if row["status"] != "pending":
            if (
                row["status"] == decision
                and idempotency_key
                and row["decision_idempotency_key"] == idempotency_key
                and row["decision_payload_hash"] == payload_hash
            ):
                return self._serialize(row), RequestWorkOrderService(self.con).get(
                    row["request_id"]
                ) or {}
            raise DomainError("change_order_not_pending", 409)
        if actor_kind == row["proposed_by_kind"] and actor_id == row["proposed_by_id"]:
            raise DomainError("change_order_sender_cannot_decide", 409)
        work_orders = RequestWorkOrderService(self.con, now=self.now)
        current = work_orders.get(row["request_id"])
        if not current:
            raise DomainError("work_order_not_found", 404)
        if (
            int(expected_version or 0) != current["version"]
            or int(row["expected_version"]) != current["version"]
        ):
            raise DomainError("work_order_version_changed", 409)
        if current.get("fulfillmentMode") == "instant" and {
            "appointmentAt",
            "durationMinutes",
        } & set(load(row["changes"], {})):
            # Defensive guard for a pending row created by an older release.
            raise DomainError("instant_schedule_change_requires_rebooking", 409)
        decided_at = iso(self.now)
        if decision == "accepted":
            updated = {**current, **load(row["changes"], {})}
            next_version = current["version"] + 1
            result = self.con.execute(
                """UPDATE request_work_orders SET appointment_at=?,duration_minutes=?,
                price_total=?,labor_amount=?,materials_amount=?,scope=?,exclusions=?,
                warranty_days=?,version=?,updated_at=? WHERE request_id=? AND version=?""",
                (
                    updated["appointmentAt"], updated["durationMinutes"],
                    updated["priceAmount"], updated["laborAmount"],
                    updated["materialsAmount"], updated["scope"], updated["exclusions"],
                    updated["warrantyDays"], next_version, decided_at,
                    row["request_id"], current["version"],
                ),
            )
            if result.rowcount != 1:
                raise DomainError("work_order_version_changed", 409)
            current = work_orders.get(row["request_id"]) or {}
            work_orders._remember_version(
                current,
                source_kind="change_order",
                source_id=change_order_id,
                actor_kind=actor_kind,
                actor_id=actor_id,
            )
        result = self.con.execute(
            """UPDATE request_change_orders SET status=?,decided_by_kind=?,
            decided_by_id=?,decision_idempotency_key=?,decision_payload_hash=?,
            decided_at=?,updated_at=? WHERE id=? AND status='pending'""",
            (
                decision, actor_kind, actor_id, idempotency_key, payload_hash,
                decided_at, decided_at, change_order_id,
            ),
        )
        if result.rowcount != 1:
            raise DomainError("change_order_not_pending", 409)
        RequestLifecycleService(self.con, now=self.now).record(
            row["request_id"],
            f"change_order_{decision}",
            actor_kind=actor_kind,
            actor_id=actor_id,
            detail={
                "changeOrderId": change_order_id,
                "fromVersion": int(row["expected_version"]),
                "toVersion": current.get("version", int(row["expected_version"])),
            },
        )
        return self.get(change_order_id) or {}, current


class RequestLifecycleService:
    """Validate request stages and keep an immutable operational timeline."""

    def __init__(self, con, *, now: datetime | None = None):
        self.con = con
        self.now = now or utcnow()

    def record(
        self,
        request_id: str,
        event_type: str,
        *,
        actor_kind: str = "system",
        actor_id: str = "",
        from_status: str = "",
        to_status: str = "",
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event_id = public_id("evt")
        created_at = iso(self.now)
        self.con.execute(
            """INSERT INTO request_events(
            id,request_id,event_type,from_status,to_status,actor_kind,actor_id,detail,created_at)
            VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                event_id,
                request_id,
                _safe_text(event_type, 80),
                _safe_text(from_status, 40),
                _safe_text(to_status, 40),
                _safe_text(actor_kind, 24) or "system",
                _safe_text(actor_id, 120),
                dump(detail or {}),
                created_at,
            ),
        )
        return {
            "id": event_id,
            "requestId": request_id,
            "type": event_type,
            "fromStatus": from_status,
            "toStatus": to_status,
            "actorKind": actor_kind,
            "actorId": actor_id,
            "detail": detail or {},
            "createdAt": created_at,
        }

    def transition(
        self,
        request_id: str,
        to_status: str,
        *,
        actor_kind: str,
        actor_id: str = "",
        event_type: str = "status_changed",
        detail: dict[str, Any] | None = None,
        allowed_from: set[str] | None = None,
    ) -> dict[str, Any]:
        request = _request(self.con, request_id)
        current = str(request.get("status") or "matching")
        if to_status not in REQUEST_STATES:
            raise DomainError("invalid_request_status")
        if not _actor_owns_request(request, actor_kind, actor_id):
            raise DomainError("request_access_denied", 403)
        permitted = REQUEST_TRANSITIONS.get(current, set())
        if current == to_status:
            return request
        if (allowed_from is not None and current not in allowed_from) or to_status not in permitted:
            raise DomainError("invalid_request_transition", 409, f"{current}->{to_status}")
        self.con.execute(
            "UPDATE customer_requests SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (to_status, request_id),
        )
        if to_status == "cancelled":
            self.con.execute(
                """UPDATE request_slot_reservations SET status='cancelled',
                updated_at=CURRENT_TIMESTAMP WHERE request_id=? AND status='active'""",
                (request_id,),
            )
        elif to_status == "closed":
            self.con.execute(
                """UPDATE request_slot_reservations SET status='completed',
                updated_at=CURRENT_TIMESTAMP WHERE request_id=? AND status='active'""",
                (request_id,),
            )
        self.record(
            request_id,
            event_type,
            actor_kind=actor_kind,
            actor_id=actor_id,
            from_status=current,
            to_status=to_status,
            detail=detail,
        )
        return _request(self.con, request_id)

    def timeline(self, request_id: str) -> list[dict[str, Any]]:
        rows = self.con.execute(
            """SELECT * FROM request_events WHERE request_id=?
            ORDER BY created_at,id""",
            (request_id,),
        )
        return [
            {
                "id": row["id"],
                "requestId": row["request_id"],
                "type": row["event_type"],
                "fromStatus": row["from_status"],
                "toStatus": row["to_status"],
                "actorKind": row["actor_kind"],
                "actorId": row["actor_id"],
                "detail": load(row["detail"], {}),
                "createdAt": row["created_at"],
            }
            for row in rows
        ]


class RequestAgreementService:
    """Versioned appointment/price agreement confirmed by both parties."""

    def __init__(self, con, *, now: datetime | None = None):
        self.con = con
        self.now = now or utcnow()

    def _authorize(self, request: dict[str, Any], actor_kind: str, actor_id: str) -> None:
        if actor_kind not in {"user", "provider"} or not _actor_owns_request(
            request, actor_kind, actor_id
        ):
            raise DomainError("agreement_access_denied", 403)
        if not request.get("accepted_provider_id"):
            raise DomainError("accepted_provider_required", 409)

    def get(self, request_id: str) -> dict[str, Any] | None:
        row = self.con.execute(
            "SELECT * FROM request_agreements WHERE request_id=?", (request_id,)
        ).fetchone()
        if not row:
            return None
        item = row_dict(row)
        return {
            "requestId": item["request_id"],
            "providerId": item["provider_id"],
            "version": int(item["version"] or 1),
            "appointmentAt": item["appointment_at"],
            "durationMinutes": int(item["duration_minutes"] or 60),
            "priceAmount": float(item["price_amount"] or 0),
            "currency": item["currency"] or "OMR",
            "notes": item["notes"] or "",
            "locationText": item["location_text"] or "",
            "status": item["status"] if item["status"] in AGREEMENT_STATES else "draft",
            "userConfirmed": int(item["user_confirmed_version"] or 0) == int(item["version"]),
            "providerConfirmed": int(item["provider_confirmed_version"] or 0)
            == int(item["version"]),
            "updatedByKind": item["updated_by_kind"] or "",
            "updatedById": item["updated_by_id"] or "",
            "createdAt": item["created_at"],
            "updatedAt": item["updated_at"],
        }

    def save(
        self, request_id: str, actor_kind: str, actor_id: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        request = _request(self.con, request_id)
        self._authorize(request, actor_kind, actor_id)
        if request["status"] not in {"accepted", "appointmentConfirmed"}:
            raise DomainError("agreement_stage_not_allowed", 409)
        appointment_at = _safe_text(data.get("appointmentAt"), 80)
        if appointment_at and not parse_datetime(appointment_at):
            raise DomainError("invalid_appointment_time")
        try:
            duration = int(data.get("durationMinutes") or 60)
            price = round(float(data.get("priceAmount") or 0), 3)
        except (TypeError, ValueError) as exc:
            raise DomainError("invalid_agreement_values") from exc
        if duration < 15 or duration > 1440 or price < 0 or price > 1_000_000:
            raise DomainError("invalid_agreement_values")
        current = self.get(request_id)
        version = int(current["version"] + 1) if current else 1
        provider_id = str(request["accepted_provider_id"])
        self.con.execute(
            """INSERT INTO request_agreements(
            request_id,provider_id,version,appointment_at,duration_minutes,price_amount,
            currency,notes,location_text,status,user_confirmed_version,
            provider_confirmed_version,updated_by_kind,updated_by_id,created_at,updated_at)
            VALUES(?,?,?,?,?,?,'OMR',?,?,'pending_confirmation',?,?,?,?,?,?)
            ON CONFLICT(request_id) DO UPDATE SET
            provider_id=excluded.provider_id,version=excluded.version,
            appointment_at=excluded.appointment_at,duration_minutes=excluded.duration_minutes,
            price_amount=excluded.price_amount,currency='OMR',notes=excluded.notes,
            location_text=excluded.location_text,status='pending_confirmation',
            user_confirmed_version=excluded.user_confirmed_version,
            provider_confirmed_version=excluded.provider_confirmed_version,
            updated_by_kind=excluded.updated_by_kind,updated_by_id=excluded.updated_by_id,
            updated_at=excluded.updated_at""",
            (
                request_id,
                provider_id,
                version,
                appointment_at,
                duration,
                price,
                _safe_text(data.get("notes"), 500),
                _safe_text(data.get("locationText"), 240),
                version if actor_kind == "user" else 0,
                version if actor_kind == "provider" else 0,
                actor_kind,
                actor_id,
                iso(self.now),
                iso(self.now),
            ),
        )
        RequestLifecycleService(self.con, now=self.now).record(
            request_id,
            "agreement_updated",
            actor_kind=actor_kind,
            actor_id=actor_id,
            detail={"version": version},
        )
        return self.get(request_id) or {}

    def confirm(
        self, request_id: str, actor_kind: str, actor_id: str, version: int
    ) -> dict[str, Any]:
        request = _request(self.con, request_id)
        self._authorize(request, actor_kind, actor_id)
        agreement = self.get(request_id)
        if not agreement:
            raise DomainError("agreement_not_found", 404)
        if int(version or 0) != int(agreement["version"]):
            raise DomainError("agreement_version_changed", 409)
        if agreement.get("status") != "pending_confirmation":
            raise DomainError("agreement_not_pending", 409)
        if actor_kind == agreement.get("updatedByKind"):
            raise DomainError("agreement_sender_cannot_confirm", 409)
        update_params = (
            agreement["version"],
            actor_kind,
            actor_id,
            iso(self.now),
            request_id,
        )
        if actor_kind == "user":
            self.con.execute(
                """UPDATE request_agreements SET user_confirmed_version=?,
                status='pending_confirmation',updated_by_kind=?,updated_by_id=?,
                updated_at=? WHERE request_id=?""",
                update_params,
            )
        else:
            self.con.execute(
                """UPDATE request_agreements SET provider_confirmed_version=?,
                status='pending_confirmation',updated_by_kind=?,updated_by_id=?,
                updated_at=? WHERE request_id=?""",
                update_params,
            )
        agreement = self.get(request_id) or {}
        if agreement.get("userConfirmed") and agreement.get("providerConfirmed"):
            self.con.execute(
                """UPDATE request_agreements SET status='confirmed',updated_at=?
                WHERE request_id=?""",
                (iso(self.now), request_id),
            )
            if request["status"] == "accepted":
                RequestLifecycleService(self.con, now=self.now).transition(
                    request_id,
                    "appointmentConfirmed",
                    actor_kind=actor_kind,
                    actor_id=actor_id,
                    event_type="agreement_confirmed",
                    detail={"version": agreement["version"]},
                )
            else:
                RequestLifecycleService(self.con, now=self.now).record(
                    request_id,
                    "agreement_confirmed",
                    actor_kind=actor_kind,
                    actor_id=actor_id,
                    detail={"version": agreement["version"]},
                )
        else:
            RequestLifecycleService(self.con, now=self.now).record(
                request_id,
                "agreement_party_confirmed",
                actor_kind=actor_kind,
                actor_id=actor_id,
                detail={"version": agreement["version"]},
            )
        return self.get(request_id) or {}

    def reject(
        self, request_id: str, actor_kind: str, actor_id: str, version: int, reason: str = ""
    ) -> dict[str, Any]:
        request = _request(self.con, request_id)
        self._authorize(request, actor_kind, actor_id)
        agreement = self.get(request_id)
        if not agreement:
            raise DomainError("agreement_not_found", 404)
        if int(version or 0) != int(agreement["version"]):
            raise DomainError("agreement_version_changed", 409)
        if agreement.get("status") != "pending_confirmation":
            raise DomainError("agreement_not_pending", 409)
        if actor_kind == agreement.get("updatedByKind"):
            raise DomainError("agreement_sender_cannot_reject", 409)
        self.con.execute(
            """UPDATE request_agreements SET status='rejected',updated_at=?
            WHERE request_id=?""",
            (iso(self.now), request_id),
        )
        RequestLifecycleService(self.con, now=self.now).record(
            request_id,
            "agreement_rejected",
            actor_kind=actor_kind,
            actor_id=actor_id,
            detail={"version": agreement["version"], "reason": _safe_text(reason, 240)},
        )
        return self.get(request_id) or {}


class ServiceAssetService:
    """Customer-owned home, vehicle, appliance, or property profiles."""

    def __init__(self, con):
        self.con = con

    def _serialize(self, row: Any) -> dict[str, Any]:
        item = row_dict(row)
        return {
            "id": item["id"],
            "userId": item["user_id"],
            "name": item["name"],
            "type": item["asset_type"],
            "categoryId": item["category_id"],
            "brand": item["brand"],
            "model": item["model"],
            "year": item["year"],
            "location": load(item["location_json"], {}),
            "details": load(item.get("details_json"), {}),
            "notes": item["notes"],
            "imagePath": item["image_path"],
            "active": bool(item["active"]),
            "createdAt": item["created_at"],
            "updatedAt": item["updated_at"],
        }

    def list_for_user(self, user_id: str, *, include_archived: bool = False) -> list[dict[str, Any]]:
        if include_archived:
            rows = self.con.execute(
                """SELECT * FROM service_assets WHERE user_id=?
                ORDER BY active DESC,updated_at DESC""",
                (user_id,),
            )
        else:
            rows = self.con.execute(
                """SELECT * FROM service_assets WHERE user_id=? AND active=1
                ORDER BY active DESC,updated_at DESC""",
                (user_id,),
            )
        return [self._serialize(row) for row in rows]

    def get_for_user(self, asset_id: str, user_id: str) -> dict[str, Any]:
        row = self.con.execute(
            "SELECT * FROM service_assets WHERE id=? AND user_id=?",
            (asset_id, user_id),
        ).fetchone()
        if not row:
            raise DomainError("service_asset_not_found", 404)
        return self._serialize(row)

    def save(
        self,
        user_id: str,
        data: dict[str, Any],
        *,
        image_path: str | None = None,
    ) -> dict[str, Any]:
        asset_id = _safe_text(data.get("id"), 120) or public_id("asset")
        existing = self.con.execute(
            "SELECT * FROM service_assets WHERE id=?", (asset_id,)
        ).fetchone()
        if existing and existing["user_id"] != user_id:
            raise DomainError("service_asset_access_denied", 403)
        asset_type = _safe_text(data.get("type"), 40) or "other"
        if asset_type not in ASSET_TYPES:
            raise DomainError("invalid_service_asset_type")
        name = _safe_text(data.get("name"), 100)
        if not name:
            raise DomainError("service_asset_name_required")
        year_value = data.get("year")
        year = None
        if year_value not in (None, ""):
            try:
                year = int(year_value)
            except (TypeError, ValueError) as exc:
                raise DomainError("invalid_service_asset_year") from exc
            if year < 1900 or year > datetime.now(UTC).year + 1:
                raise DomainError("invalid_service_asset_year")
        location = data.get("location") if isinstance(data.get("location"), dict) else {}
        details = data.get("details") if isinstance(data.get("details"), dict) else {}
        allowed_detail_fields = {
            "home": {"houseNumber", "wayNumber", "buildingNumber", "floor", "unitNumber"},
            "vehicle": {"plateNumber", "vehicleType", "engine", "color"},
            "appliance": {"serialNumber", "applianceType", "purchaseYear"},
            "property": {"propertyNumber", "wayNumber", "buildingNumber", "floor", "unitNumber"},
            "other": {"referenceNumber"},
        }
        details = {
            key: _safe_text(value, 100)
            for key, value in details.items()
            if key in allowed_detail_fields.get(asset_type, set()) and value not in (None, "")
        }
        current_image = existing["image_path"] if existing else ""
        effective_image = current_image if image_path is None else image_path
        self.con.execute(
            """INSERT INTO service_assets(
            id,user_id,name,asset_type,category_id,brand,model,year,location_json,
            details_json,notes,image_path,active,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET name=excluded.name,asset_type=excluded.asset_type,
            category_id=excluded.category_id,brand=excluded.brand,model=excluded.model,
            year=excluded.year,location_json=excluded.location_json,
            details_json=excluded.details_json,notes=excluded.notes,
            image_path=excluded.image_path,active=1,updated_at=CURRENT_TIMESTAMP""",
            (
                asset_id,
                user_id,
                name,
                asset_type,
                _safe_text(data.get("categoryId"), 80),
                _safe_text(data.get("brand"), 80),
                _safe_text(data.get("model"), 80),
                year,
                dump(location),
                dump(details),
                _safe_text(data.get("notes"), 600),
                effective_image,
            ),
        )
        return self.get_for_user(asset_id, user_id)

    def archive(self, asset_id: str, user_id: str) -> None:
        result = self.con.execute(
            """UPDATE service_assets SET active=0,updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND user_id=?""",
            (asset_id, user_id),
        )
        if result.rowcount != 1:
            raise DomainError("service_asset_not_found", 404)

    def attach(self, request_id: str, asset_id: str, user_id: str) -> dict[str, Any]:
        request = _request(self.con, request_id)
        if request.get("user_id") != user_id:
            raise DomainError("request_access_denied", 403)
        if asset_id:
            asset = self.get_for_user(asset_id, user_id)
            if not asset["active"]:
                raise DomainError("service_asset_archived", 409)
        self.con.execute(
            "UPDATE customer_requests SET asset_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (asset_id, request_id),
        )
        return self.get_for_user(asset_id, user_id) if asset_id else {}

    def history(self, asset_id: str, user_id: str) -> list[dict[str, Any]]:
        self.get_for_user(asset_id, user_id)
        return [
            {
                "id": row["id"],
                "serviceValue": row["service_value"],
                "serviceName": row["service_name"],
                "status": row["status"],
                "providerId": row["accepted_provider_id"],
                "requestedAt": row["requested_at"],
                "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
            }
            for row in self.con.execute(
                """SELECT id,service_value,service_name,status,accepted_provider_id,
                requested_at,created_at,updated_at FROM customer_requests
                WHERE asset_id=? AND user_id=? ORDER BY created_at DESC""",
                (asset_id, user_id),
            )
        ]


class CompletionEvidenceService:
    """Before/after evidence and an explicit customer completion decision."""

    def __init__(self, con, *, now: datetime | None = None):
        self.con = con
        self.now = now or utcnow()

    def get(self, request_id: str) -> dict[str, Any] | None:
        row = self.con.execute(
            "SELECT * FROM request_completion_evidence WHERE request_id=?",
            (request_id,),
        ).fetchone()
        if not row:
            return None
        item = row_dict(row)
        return {
            "requestId": item["request_id"],
            "providerId": item["provider_id"],
            "beforeImages": load(item["before_images"], []),
            "afterImages": load(item["after_images"], []),
            "checklist": load(item.get("checklist"), []),
            "note": item["note"],
            "submittedAt": item["submitted_at"],
            "customerDecision": item["customer_decision"],
            "customerNote": item["customer_note"],
            "decidedAt": item["decided_at"],
            "createdAt": item["created_at"],
            "updatedAt": item["updated_at"],
        }

    def submit(
        self,
        request_id: str,
        provider_id: str,
        *,
        before_images: list[str],
        after_images: list[str],
        checklist: list[str] | None = None,
        note: str = "",
        idempotency_key: str = "",
        payload_hash_override: str = "",
    ) -> dict[str, Any]:
        if not self.con.in_transaction:
            self.con.execute("BEGIN IMMEDIATE")
        request = _request(self.con, request_id)
        if request.get("accepted_provider_id") != provider_id:
            raise DomainError("completion_access_denied", 403)
        before = [str(value) for value in before_images if value][:5]
        after = [str(value) for value in after_images if value][:5]
        checklist_items = [
            _safe_text(value, 160) for value in (checklist or []) if _safe_text(value, 160)
        ][:30]
        note = _safe_text(note, 600)
        idempotency_key = _safe_text(idempotency_key, 128)
        policy = str(request.get("evidence_policy") or "required_photo")
        if policy not in EVIDENCE_POLICIES:
            policy = "required_photo"
        if policy in {"required_photo", "photo_and_checklist"} and not after:
            raise DomainError("completion_after_image_required")
        if policy in {"required_checklist", "photo_and_checklist"} and not checklist_items:
            raise DomainError("completion_checklist_required")
        if not note:
            raise DomainError("completion_note_required")
        if request.get("workflow_version") == "booking_v2" and not idempotency_key:
            raise DomainError("completion_idempotency_required")
        if idempotency_key and not IDEMPOTENCY_RE.fullmatch(idempotency_key):
            raise DomainError("invalid_idempotency_key")
        calculated_payload_hash = hashlib.sha256(
            json.dumps(
                {
                    "requestId": request_id,
                    "beforeImages": before,
                    "afterImages": after,
                    "checklist": checklist_items,
                    "note": note,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        payload_hash = _safe_text(payload_hash_override, 64)
        if payload_hash and not re.fullmatch(r"[a-f0-9]{64}", payload_hash):
            raise DomainError("invalid_completion_payload_hash")
        payload_hash = payload_hash or calculated_payload_hash
        prior = self.con.execute(
            "SELECT * FROM request_completion_evidence WHERE request_id=?",
            (request_id,),
        ).fetchone()
        if prior:
            if (
                idempotency_key
                and prior["submit_idempotency_key"] == idempotency_key
                and prior["submit_payload_hash"] == payload_hash
            ):
                result = self.get(request_id) or {}
                result["_duplicate"] = True
                return result
            if idempotency_key and prior["submit_idempotency_key"] == idempotency_key:
                raise DomainError("idempotency_key_reused", 409)
            raise DomainError("completion_already_submitted", 409)
        if request.get("status") != "inProgress":
            raise DomainError("completion_stage_not_allowed", 409)
        submitted_at = iso(self.now)
        completion_window = _bounded_int(
            request.get("completion_window_hours"), 48,
            COMPLETION_WINDOW_MIN_HOURS, COMPLETION_WINDOW_MAX_HOURS,
            "invalid_completion_window",
        )
        completion_due_at = iso(self.now + timedelta(hours=completion_window))
        self.con.execute(
            """INSERT INTO request_completion_evidence(
            request_id,provider_id,before_images,after_images,checklist,note,submitted_at,
            customer_decision,customer_note,decided_at,submit_idempotency_key,
            submit_payload_hash,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,'','','',?,?,?,?)""",
            (
                request_id,
                provider_id,
                dump(before),
                dump(after),
                dump(checklist_items),
                note,
                submitted_at,
                idempotency_key,
                payload_hash,
                submitted_at,
                submitted_at,
            ),
        )
        transitioned = self.con.execute(
            """UPDATE customer_requests SET status='awaitingConfirmation',
            completion_due_at=?,updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND accepted_provider_id=? AND status='inProgress'""",
            (completion_due_at, request_id, provider_id),
        )
        if transitioned.rowcount != 1:
            raise DomainError("completion_stage_not_allowed", 409)
        RequestLifecycleService(self.con, now=self.now).record(
            request_id,
            "completion_submitted",
            actor_kind="provider",
            actor_id=provider_id,
            from_status="inProgress",
            to_status="awaitingConfirmation",
            detail={
                "beforeCount": len(before),
                "afterCount": len(after),
                "checklistCount": len(checklist_items),
                "evidencePolicy": policy,
                "dueAt": completion_due_at,
            },
        )
        return self.get(request_id) or {}

    def decide(
        self,
        request_id: str,
        user_id: str,
        decision: str,
        note: str = "",
        *,
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        if not self.con.in_transaction:
            self.con.execute("BEGIN IMMEDIATE")
        request = _request(self.con, request_id)
        if request.get("user_id") != user_id:
            raise DomainError("completion_access_denied", 403)
        row = self.con.execute(
            "SELECT * FROM request_completion_evidence WHERE request_id=?",
            (request_id,),
        ).fetchone()
        if not row:
            raise DomainError("completion_evidence_required", 409)
        if decision not in {"resolved", "issue"}:
            raise DomainError("invalid_completion_decision")
        note = _safe_text(note, 600)
        idempotency_key = _safe_text(idempotency_key, 128)
        if request.get("workflow_version") == "booking_v2" and not idempotency_key:
            raise DomainError("completion_idempotency_required")
        if idempotency_key and not IDEMPOTENCY_RE.fullmatch(idempotency_key):
            raise DomainError("invalid_idempotency_key")
        payload_hash = hashlib.sha256(
            json.dumps(
                {"requestId": request_id, "decision": decision, "note": note},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if row["customer_decision"]:
            if (
                row["customer_decision"] == decision
                and idempotency_key
                and row["decision_idempotency_key"] == idempotency_key
                and row["decision_payload_hash"] == payload_hash
            ):
                result = self.get(request_id) or {}
                result["_duplicate"] = True
                return result
            if idempotency_key and row["decision_idempotency_key"] == idempotency_key:
                raise DomainError("idempotency_key_reused", 409)
            raise DomainError("completion_already_decided", 409)
        if request.get("status") != "awaitingConfirmation":
            raise DomainError("completion_stage_not_allowed", 409)
        decided_at = iso(self.now)
        evidence_result = self.con.execute(
            """UPDATE request_completion_evidence SET customer_decision=?,
            customer_note=?,decision_idempotency_key=?,decision_payload_hash=?,
            decided_at=?,updated_at=? WHERE request_id=? AND customer_decision=''""",
            (
                decision,
                note,
                idempotency_key,
                payload_hash,
                decided_at,
                decided_at,
                request_id,
            ),
        )
        if evidence_result.rowcount != 1:
            raise DomainError("completion_already_decided", 409)
        lifecycle = RequestLifecycleService(self.con, now=self.now)
        if decision == "resolved":
            closed = self.con.execute(
                """UPDATE customer_requests SET status='closed',latitude=NULL,
                longitude=NULL,contact_consent='{}',completion_due_at='',
                updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?
                AND status='awaitingConfirmation'""",
                (request_id, user_id),
            )
            if closed.rowcount != 1:
                raise DomainError("completion_stage_not_allowed", 409)
            InstantBookingService(self.con, now=self.now).complete_request(request_id)
            lifecycle.record(
                request_id,
                "completion_confirmed",
                actor_kind="user",
                actor_id=user_id,
                from_status="awaitingConfirmation",
                to_status="closed",
            )
            lifecycle.record(
                request_id,
                "completion_resolved",
                actor_kind="user",
                actor_id=user_id,
                from_status="awaitingConfirmation",
                to_status="closed",
            )
            self.con.execute(
                """UPDATE providers SET completed_jobs=completed_jobs+1,
                updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (request["accepted_provider_id"],),
            )
        else:
            opened = self.con.execute(
                """UPDATE customer_requests SET status='qualityReview',
                completion_due_at='',updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND user_id=? AND status='awaitingConfirmation'""",
                (request_id, user_id),
            )
            if opened.rowcount != 1:
                raise DomainError("completion_stage_not_allowed", 409)
            lifecycle.record(
                request_id,
                "completion_issue_reported",
                actor_kind="user",
                actor_id=user_id,
                from_status="awaitingConfirmation",
                to_status="qualityReview",
            )
            lifecycle.record(
                request_id,
                "completion_issue_opened",
                actor_kind="user",
                actor_id=user_id,
                from_status="awaitingConfirmation",
                to_status="qualityReview",
            )
            exists = self.con.execute(
                "SELECT id FROM complaints WHERE request_id=? AND user_id=?",
                (request_id, user_id),
            ).fetchone()
            if not exists:
                self.con.execute(
                    """INSERT INTO complaints(
                    id,provider_id,customer_name,phone,reason,detail,status,priority,
                    resolution,request_id,user_id,created_at,updated_at)
                    VALUES(?,?,?,?,?,'','open','high','',?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
                    (
                        public_id("cmp"),
                        request["accepted_provider_id"],
                        request["customer_name"],
                        request["phone"],
                        _safe_text(note, 600) or "Service completion needs review",
                        request_id,
                        user_id,
                    ),
                )
        return self.get(request_id) or {}

    def auto_close_due(self, *, limit: int = 100) -> list[dict[str, str]]:
        """Close only explicitly eligible low-risk booking_v2 requests."""
        if not self.con.in_transaction:
            self.con.execute("BEGIN IMMEDIATE")
        limit = max(1, min(int(limit or 100), 500))
        rows = list(
            self.con.execute(
                """SELECT cr.id,cr.user_id,cr.accepted_provider_id
                FROM customer_requests cr
                WHERE cr.workflow_version='booking_v2'
                AND cr.status='awaitingConfirmation'
                AND cr.auto_close_enabled=1
                AND cr.fulfillment_mode!='project'
                AND cr.completion_due_at!='' AND cr.completion_due_at<=?
                AND NOT EXISTS(
                  SELECT 1 FROM request_change_orders co
                  WHERE co.request_id=cr.id AND co.status='pending'
                )
                AND NOT EXISTS(
                  SELECT 1 FROM complaints c
                  WHERE c.request_id=cr.id
                  AND c.status NOT IN ('resolved','closed','rejected')
                )
                ORDER BY cr.completion_due_at,cr.id LIMIT ?""",
                (iso(self.now), limit),
            )
        )
        closed: list[dict[str, str]] = []
        for row in rows:
            result = self.con.execute(
                """UPDATE customer_requests SET status='closed',latitude=NULL,
                longitude=NULL,contact_consent='{}',completion_due_at='',
                updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND workflow_version='booking_v2'
                AND status='awaitingConfirmation' AND auto_close_enabled=1
                AND fulfillment_mode!='project'
                AND completion_due_at!='' AND completion_due_at<=?
                AND EXISTS(
                  SELECT 1 FROM request_completion_evidence evidence
                  WHERE evidence.request_id=customer_requests.id
                  AND evidence.customer_decision=''
                )
                AND NOT EXISTS(
                  SELECT 1 FROM request_change_orders co
                  WHERE co.request_id=customer_requests.id AND co.status='pending'
                )
                AND NOT EXISTS(
                  SELECT 1 FROM complaints c
                  WHERE c.request_id=customer_requests.id
                  AND c.status NOT IN ('resolved','closed','rejected')
                )""",
                (row["id"], iso(self.now)),
            )
            if result.rowcount != 1:
                continue
            decided_at = iso(self.now)
            evidence_result = self.con.execute(
                """UPDATE request_completion_evidence
                SET customer_decision='auto_closed',decided_at=?,updated_at=?
                WHERE request_id=? AND customer_decision=''""",
                (decided_at, decided_at, row["id"]),
            )
            if evidence_result.rowcount != 1:
                raise DomainError("completion_auto_close_conflict", 409)
            self.con.execute(
                """UPDATE providers SET completed_jobs=completed_jobs+1,
                updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (row["accepted_provider_id"],),
            )
            InstantBookingService(self.con, now=self.now).complete_request(row["id"])
            RequestLifecycleService(self.con, now=self.now).record(
                row["id"],
                "completion_auto_closed",
                actor_kind="system",
                detail={"policy": "configured_response_window"},
                from_status="awaitingConfirmation",
                to_status="closed",
            )
            NotificationActionService(self.con, now=self.now).resolve(
                entity_kind="request",
                entity_id=row["id"],
                action_kind="review_completion",
                target_kind="user",
                target_id=row["user_id"],
            )
            closed.append(
                {
                    "requestId": row["id"],
                    "userId": row["user_id"],
                    "providerId": row["accepted_provider_id"],
                }
            )
        return closed


class NotificationActionService:
    """Keep notification visibility separate from completion of its business action."""

    ACTIONS = {"seen", "read", "ack", "snooze", "dismiss"}

    def __init__(self, con, *, now: datetime | None = None):
        self.con = con
        self.now = now or utcnow()

    def update(
        self,
        notification_id: str,
        target_kind: str,
        target_id: str,
        action: str,
        *,
        snooze_minutes: int = 60,
        admin_override: bool = False,
    ) -> None:
        row = self.con.execute(
            "SELECT * FROM app_notifications WHERE id=?", (notification_id,)
        ).fetchone()
        if not row:
            raise DomainError("notification_not_found", 404)
        if not admin_override and (
            row["target_kind"] != target_kind or row["target_id"] != target_id
        ):
            raise DomainError("notification_access_denied", 403)
        if action not in self.ACTIONS:
            raise DomainError("invalid_notification_action")
        stamp = iso(self.now)
        if action == "dismiss" and bool(row["requires_action"]):
            expires_at = parse_datetime(row["expires_at"])
            unresolved = not row["acted_at"] and not row["superseded_at"]
            if unresolved and (not expires_at or expires_at > self.now):
                raise DomainError("required_action_cannot_be_dismissed", 409)
        if action == "seen":
            self.con.execute(
                """UPDATE app_notifications SET seen_at=CASE WHEN seen_at=''
                THEN ? ELSE seen_at END WHERE id=?""",
                (stamp, notification_id),
            )
        elif action == "read":
            self.con.execute(
                """UPDATE app_notifications SET is_read=1,
                seen_at=CASE WHEN seen_at='' THEN ? ELSE seen_at END,
                read_at=CASE WHEN read_at='' THEN ? ELSE read_at END WHERE id=?""",
                (stamp, stamp, notification_id),
            )
        elif action == "ack":
            self.con.execute(
                """UPDATE app_notifications SET is_read=1,
                seen_at=CASE WHEN seen_at='' THEN ? ELSE seen_at END,
                read_at=CASE WHEN read_at='' THEN ? ELSE read_at END,
                acknowledged_at=CASE WHEN acknowledged_at='' THEN ?
                ELSE acknowledged_at END WHERE id=?""",
                (stamp, stamp, stamp, notification_id),
            )
        elif action == "snooze":
            minutes = _bounded_int(
                snooze_minutes, 60, 5, 7 * 24 * 60, "invalid_snooze_duration"
            )
            self.con.execute(
                """UPDATE app_notifications SET acknowledged_at=CASE
                WHEN acknowledged_at='' THEN ? ELSE acknowledged_at END,
                snoozed_until=? WHERE id=?""",
                (stamp, iso(self.now + timedelta(minutes=minutes)), notification_id),
            )
        else:
            self.con.execute(
                """UPDATE app_notifications SET dismissed_at=CASE
                WHEN dismissed_at='' THEN ? ELSE dismissed_at END WHERE id=?""",
                (stamp, notification_id),
            )

    def resolve(
        self,
        *,
        entity_kind: str,
        entity_id: str,
        action_kind: str = "",
        target_kind: str = "",
        target_id: str = "",
        supersede_others: bool = False,
    ) -> int:
        clauses = ["entity_kind=?", "entity_id=?", "acted_at=''", "superseded_at=''"]
        values: list[Any] = [entity_kind, entity_id]
        if action_kind:
            clauses.append("action_kind=?")
            values.append(action_kind)
        if target_kind:
            clauses.append("target_kind=?")
            values.append(target_kind)
        if target_id:
            clauses.append("target_id=?")
            values.append(target_id)
        stamp = iso(self.now)
        result = self.con.execute(
            f"""UPDATE app_notifications SET acted_at=?,is_read=1,
            read_at=CASE WHEN read_at='' THEN ? ELSE read_at END
            WHERE {' AND '.join(clauses)}""",  # nosec B608 - clauses are fixed
            (stamp, stamp, *values),
        )
        if supersede_others:
            self.con.execute(
                """UPDATE app_notifications SET superseded_at=?
                WHERE entity_kind=? AND entity_id=? AND acted_at=''
                AND superseded_at=''""",
                (stamp, entity_kind, entity_id),
            )
        return int(result.rowcount or 0)

    def pending_count(self, target_kind: str, target_id: str) -> int:
        row = self.con.execute(
            """SELECT COUNT(*) n FROM app_notifications
            WHERE target_kind=? AND target_id=? AND requires_action=1
            AND acted_at='' AND superseded_at=''
            AND (expires_at='' OR expires_at>?)""",
            (target_kind, target_id, iso(self.now)),
        ).fetchone()
        return int(row["n"] if row else 0)

    def prompt_due(
        self, target_kind: str, target_id: str, *, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Return interruptive prompts due now, distinct from badge pending state."""
        return [
            row_dict(row)
            for row in self.con.execute(
                """SELECT * FROM app_notifications
                WHERE target_kind=? AND target_id=? AND requires_action=1
                AND acted_at='' AND superseded_at='' AND dismissed_at=''
                AND (snoozed_until='' OR snoozed_until<=?)
                AND (expires_at='' OR expires_at>?)
                ORDER BY CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1
                WHEN 'normal' THEN 2 ELSE 3 END,
                CASE WHEN expires_at='' THEN 1 ELSE 0 END,expires_at,created_at
                LIMIT ?""",
                (
                    target_kind,
                    target_id,
                    iso(self.now),
                    iso(self.now),
                    max(1, min(int(limit or 50), 200)),
                ),
            )
        ]


def notification_request_state(
    con,
    notification: Any,
    *,
    actor_kind: str,
    actor_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate a request deep link against the server's current workflow state.

    Push payloads and stored routes are navigation hints only.  For the bounded
    set of request actions below, an old state version or an action that is no
    longer allowed supersedes the notification without treating it as acted.
    """
    item = row_dict(notification)
    if item.get("entity_kind") != "request" or not item.get("entity_id"):
        return {"stale": bool(item.get("superseded_at")), "currentRequest": None}
    request_id = str(item["entity_id"])
    row = con.execute(
        "SELECT * FROM customer_requests WHERE id=?", (request_id,)
    ).fetchone()
    if not row:
        stale = True
        current_request = None
        reason = "request_not_found"
    else:
        request = row_dict(row)
        authorized = actor_kind == "admin" or (
            actor_kind == "user" and request.get("user_id") == actor_id
        ) or (
            actor_kind == "provider"
            and request.get("accepted_provider_id") == actor_id
        )
        if not authorized:
            stale = True
            current_request = None
            reason = "request_access_changed"
        else:
            workflow = request_workflow_view(
                con, request, actor_kind=actor_kind, actor_id=actor_id
            )
            work_order = workflow.get("workOrderSummary") or {}
            action_kind = str(item.get("action_kind") or "")
            if action_kind in {"compare_offers", "review_change_order"}:
                revision = con.execute(
                    """SELECT MAX(state_version) n FROM app_notifications
                    WHERE entity_kind='request' AND entity_id=? AND action_kind=?
                    AND target_kind=? AND target_id=?""",
                    (request_id, action_kind, actor_kind, actor_id),
                ).fetchone()
                current_version = int(revision["n"] or 0) if revision else 0
                if not current_version and action_kind == "compare_offers":
                    current_version = len(load(request.get("offers"), []))
                elif not current_version:
                    current_version = int(work_order.get("version") or 0)
            elif action_kind in {"open_booking", "review_completion"}:
                current_version = int(work_order.get("version") or 0)
            else:
                current_version = 0
            current_request = {
                "id": request_id,
                "status": str(request.get("status") or ""),
                "stateVersion": current_version,
                **workflow,
            }
            allowed = set(workflow.get("allowedActions") or [])
            action_allowed = {
                "compare_offers": "compare_offers" in allowed,
                "open_booking": actor_kind == "provider" and "start_work" in allowed,
                "review_change_order": "review_change_order" in allowed,
                "review_completion": "review_completion" in allowed,
            }
            known_action = action_kind in action_allowed
            stale = bool(item.get("superseded_at") or item.get("acted_at"))
            reason = "already_resolved" if stale else ""
            if known_action and not action_allowed[action_kind]:
                stale = True
                reason = "action_no_longer_allowed"
            stored_version = int(item.get("state_version") or 0)
            if known_action and stored_version and stored_version != current_version:
                stale = True
                reason = "state_version_changed"
            clock = now or utcnow()
            expires_at = parse_datetime(item.get("expires_at"))
            if expires_at and expires_at <= clock:
                stale = True
                reason = "notification_expired"
    if stale and not item.get("acted_at") and not item.get("superseded_at"):
        con.execute(
            """UPDATE app_notifications SET superseded_at=?
            WHERE id=? AND acted_at='' AND superseded_at=''""",
            (iso(now or utcnow()), item.get("id")),
        )
    return {
        "stale": stale,
        "staleReason": reason,
        "currentRequest": current_request,
    }

class RequestIdempotencyService:
    """Prevent duplicate requests when a mobile retry follows a lost response."""

    def __init__(self, con):
        self.con = con

    @staticmethod
    def payload_hash(data: dict[str, Any]) -> str:
        canonical = {
            key: value
            for key, value in data.items()
            if key not in {"imagesData", "idempotencyKey", "id", "action"}
        }
        raw = json.dumps(
            canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def find(self, user_id: str, key: str, data: dict[str, Any]) -> str:
        if not key:
            return ""
        if not IDEMPOTENCY_RE.fullmatch(key):
            raise DomainError("invalid_idempotency_key")
        row = self.con.execute(
            """SELECT request_id,payload_hash FROM request_idempotency
            WHERE user_id=? AND idempotency_key=?""",
            (user_id, key),
        ).fetchone()
        if not row:
            return ""
        if row["payload_hash"] != self.payload_hash(data):
            raise DomainError("idempotency_key_reused", 409)
        return row["request_id"]

    def remember(
        self, user_id: str, key: str, request_id: str, data: dict[str, Any]
    ) -> None:
        if not key:
            return
        if not IDEMPOTENCY_RE.fullmatch(key):
            raise DomainError("invalid_idempotency_key")
        self.con.execute(
            """INSERT INTO request_idempotency(
            user_id,idempotency_key,request_id,payload_hash)
            VALUES(?,?,?,?)""",
            (user_id, key, request_id, self.payload_hash(data)),
        )


def request_workflow_view(
    con,
    request: dict[str, Any],
    *,
    actor_kind: str = "",
    actor_id: str = "",
) -> dict[str, Any]:
    """Return one server-authoritative presentation state and next action."""
    request_id = str(request.get("id") or "")
    status = str(request.get("status") or "matching")
    workflow_version = str(
        request.get("workflowVersion") or request.get("workflow_version") or "legacy_v1"
    )
    provider_id = str(
        request.get("acceptedProviderId") or request.get("accepted_provider_id") or ""
    )
    customer_id = str(request.get("userId") or request.get("user_id") or "")
    if actor_kind == "user" and actor_id != customer_id:
        actor_kind = ""
    if actor_kind == "provider" and actor_id != provider_id:
        actor_kind = ""
    visible_map = {
        "accepted": "booked",
        "appointmentConfirmed": "booked",
        "inProgress": "in_progress",
        "awaitingConfirmation": "awaiting_confirmation",
        "qualityReview": "awaiting_confirmation",
        "closed": "completed",
        "archived": "completed",
        "cancelled": "cancelled",
        "deleted": "cancelled",
    }
    visible_state = visible_map.get(status, "matching")
    exception_state = status if status in {
        "unavailable", "paused", "qualityReview", "cancelled", "deleted"
    } else ""
    work_order = RequestWorkOrderService(con).get(request_id)
    change_orders = (
        RequestChangeOrderService(con).list_for_request(request_id)
        if workflow_version == "booking_v2"
        else []
    )
    pending_change = next(
        (item for item in change_orders if item.get("status") == "pending"), None
    )
    allowed: list[str] = []
    next_action: dict[str, Any] = {
        "type": "open_request",
        "label": "فتح الطلب",
        "labelEn": "Open request",
        "enabled": True,
    }
    if actor_kind == "user":
        if status in {"matching", "viewed", "unavailable", "paused"}:
            allowed = ["compare_offers", "cancel_with_reason"]
            next_action = {
                "type": "compare_offers",
                "label": "مقارنة العروض",
                "labelEn": "Compare offers",
                "enabled": status in {"matching", "viewed"},
            }
        elif status in {"accepted", "appointmentConfirmed"}:
            allowed = ["open_chat", "request_change", "cancel_with_reason"]
            next_action = {
                "type": "open_request",
                "label": "عرض الحجز",
                "labelEn": "View booking",
                "enabled": True,
            }
        elif status == "inProgress":
            allowed = ["open_chat", "request_change", "report_issue"]
            next_action = {
                "type": "open_request",
                "label": "متابعة التنفيذ",
                "labelEn": "Track service",
                "enabled": True,
            }
        elif status == "awaitingConfirmation":
            allowed = ["review_completion", "open_chat"]
            next_action = {
                "type": "review_completion",
                "label": "راجع الإنجاز",
                "labelEn": "Review completion",
                "enabled": True,
            }
        elif status == "qualityReview":
            allowed = ["open_quality_review", "open_chat"]
            next_action = {
                "type": "open_quality_review",
                "label": "متابعة المراجعة",
                "labelEn": "Track review",
                "enabled": True,
            }
        elif status in {"closed", "archived"}:
            allowed = ["rate_provider", "rebook", "open_chat"]
            next_action = {
                "type": "rate_provider",
                "label": "قيّم الخدمة",
                "labelEn": "Rate service",
                "enabled": True,
            }
    elif actor_kind == "provider":
        if status in {"accepted", "appointmentConfirmed"}:
            allowed = ["start_work", "open_chat", "request_change"]
            next_action = {
                "type": "start_work",
                "label": "بدأت الخدمة",
                "labelEn": "Start service",
                "enabled": True,
            }
        elif status == "inProgress":
            allowed = ["submit_completion", "open_chat", "request_change"]
            next_action = {
                "type": "submit_completion",
                "label": "أنهيت الخدمة",
                "labelEn": "Finish service",
                "enabled": True,
            }
        elif status == "awaitingConfirmation":
            allowed = ["open_chat"]
            next_action = {
                "type": "wait_customer",
                "label": "بانتظار العميل",
                "labelEn": "Waiting for customer",
                "enabled": False,
            }
        elif status == "qualityReview":
            allowed = ["open_quality_review", "open_chat"]
            next_action = {
                "type": "open_quality_review",
                "label": "متابعة المراجعة",
                "labelEn": "Track review",
                "enabled": True,
            }
        elif status in {"closed", "archived"}:
            allowed = ["open_request", "open_chat"]
    elif actor_kind == "admin":
        allowed = ["open_request", "review_timeline", "review_quality"]
    if pending_change and actor_kind in {"user", "provider"}:
        if (
            pending_change["proposedByKind"] == actor_kind
            and pending_change["proposedById"] == actor_id
        ):
            next_action = {
                "type": "wait_change_order",
                "label": "بانتظار موافقة الطرف الآخر",
                "labelEn": "Waiting for change approval",
                "enabled": False,
                "changeOrderId": pending_change["id"],
                "expectedVersion": pending_change["expectedVersion"],
            }
        else:
            allowed = ["review_change_order", "open_chat"]
            next_action = {
                "type": "review_change_order",
                "label": "راجع التعديل",
                "labelEn": "Review change",
                "enabled": True,
                "changeOrderId": pending_change["id"],
                "expectedVersion": pending_change["expectedVersion"],
            }
    pending_actions = 0
    if actor_kind in {"user", "provider"} and actor_id:
        row = con.execute(
            """SELECT COUNT(*) n FROM app_notifications
            WHERE target_kind=? AND target_id=? AND entity_kind='request' AND entity_id=?
            AND requires_action=1 AND acted_at='' AND superseded_at=''
            AND (expires_at='' OR expires_at>?)""",
            (actor_kind, actor_id, request_id, iso(utcnow())),
        ).fetchone()
        pending_actions = int(row["n"] if row else 0)
    return {
        "workflowVersion": workflow_version,
        "visibleState": visible_state,
        "exceptionState": exception_state,
        "nextAction": next_action,
        "allowedActions": list(dict.fromkeys(allowed)),
        "pendingActionCount": pending_actions,
        "workOrderSummary": work_order,
        "dueAt": str(
            request.get("completionDueAt") or request.get("completion_due_at") or ""
        ),
    }


def attach_workflow_data(
    con,
    request: dict[str, Any],
    *,
    asset_visible: bool = False,
    actor_kind: str = "",
    actor_id: str = "",
) -> dict[str, Any]:
    """Attach role-filtered workflow details to one serialized request."""
    request_id = request.get("id", "")
    request["timeline"] = RequestLifecycleService(con).timeline(request_id)
    agreement = RequestAgreementService(con).get(request_id)
    workflow_version = str(
        request.get("workflowVersion") or request.get("workflow_version") or "legacy_v1"
    )
    request["agreement"] = None if workflow_version == "booking_v2" else agreement
    if actor_kind == "admin" and agreement:
        request["legacyAgreement"] = agreement
    work_orders = RequestWorkOrderService(con)
    request["workOrder"] = work_orders.get(request_id)
    request["workOrderVersions"] = (
        work_orders.versions(request_id) if request["workOrder"] else []
    )
    request["changeOrders"] = (
        RequestChangeOrderService(con).list_for_request(request_id)
        if workflow_version == "booking_v2"
        else []
    )
    request["completionEvidence"] = CompletionEvidenceService(con).get(request_id)
    asset_id = request.get("assetId") or request.get("asset_id") or ""
    request["assetId"] = asset_id
    request.pop("asset_id", None)
    request["serviceAsset"] = None
    if asset_id and asset_visible:
        row = con.execute("SELECT * FROM service_assets WHERE id=?", (asset_id,)).fetchone()
        if row:
            request["serviceAsset"] = ServiceAssetService(con)._serialize(row)
    request.update(
        request_workflow_view(
            con,
            request,
            actor_kind=actor_kind,
            actor_id=actor_id,
        )
    )
    return request
