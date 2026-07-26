"""Central request workflow services for Khadamati.

The legacy application stores the request itself in ``customer_requests``.
This module extends that model without replacing it: existing columns remain
the source of the current snapshot while the new tables keep auditable events,
agreements, customer-owned service assets, and completion evidence.
"""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import re
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
AGREEMENT_STATES = {"draft", "pending_confirmation", "confirmed"}
IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


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
        CREATE INDEX IF NOT EXISTS idx_request_events_timeline
          ON request_events(request_id,created_at,id);
        CREATE INDEX IF NOT EXISTS idx_service_assets_user
          ON service_assets(user_id,active,updated_at);
        CREATE INDEX IF NOT EXISTS idx_completion_provider
          ON request_completion_evidence(provider_id,submitted_at);
        """
    )
    columns = {row["name"] for row in con.execute("PRAGMA table_info(customer_requests)")}
    if "asset_id" not in columns:
        con.execute("ALTER TABLE customer_requests ADD COLUMN asset_id TEXT DEFAULT ''")
    asset_columns = {row["name"] for row in con.execute("PRAGMA table_info(service_assets)")}
    if "details_json" not in asset_columns:
        con.execute("ALTER TABLE service_assets ADD COLUMN details_json TEXT NOT NULL DEFAULT '{}'")
    con.execute(
        """CREATE INDEX IF NOT EXISTS idx_request_asset
        ON customer_requests(asset_id,status,created_at)"""
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
            VALUES(?,?,?,?,?,?,'OMR',?,?,'pending_confirmation',0,0,?,?,?,?)
            ON CONFLICT(request_id) DO UPDATE SET
            provider_id=excluded.provider_id,version=excluded.version,
            appointment_at=excluded.appointment_at,duration_minutes=excluded.duration_minutes,
            price_amount=excluded.price_amount,currency='OMR',notes=excluded.notes,
            location_text=excluded.location_text,status='pending_confirmation',
            user_confirmed_version=0,provider_confirmed_version=0,
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
        column = (
            "user_confirmed_version"
            if actor_kind == "user"
            else "provider_confirmed_version"
        )
        self.con.execute(
            f"""UPDATE request_agreements SET {column}=?,status='pending_confirmation',
            updated_by_kind=?,updated_by_id=?,updated_at=? WHERE request_id=?""",
            (agreement["version"], actor_kind, actor_id, iso(self.now), request_id),
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
        condition = "" if include_archived else " AND active=1"
        rows = self.con.execute(
            f"""SELECT * FROM service_assets WHERE user_id=?{condition}
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
        note: str = "",
    ) -> dict[str, Any]:
        request = _request(self.con, request_id)
        if request.get("accepted_provider_id") != provider_id:
            raise DomainError("completion_access_denied", 403)
        if request.get("status") not in {"inProgress", "awaitingConfirmation"}:
            raise DomainError("completion_stage_not_allowed", 409)
        before = [str(value) for value in before_images if value][:5]
        after = [str(value) for value in after_images if value][:5]
        if not after:
            raise DomainError("completion_after_image_required")
        submitted_at = iso(self.now)
        self.con.execute(
            """INSERT INTO request_completion_evidence(
            request_id,provider_id,before_images,after_images,note,submitted_at,
            customer_decision,customer_note,decided_at,created_at,updated_at)
            VALUES(?,?,?,?,?,?,'','','',?,?)
            ON CONFLICT(request_id) DO UPDATE SET
            before_images=excluded.before_images,after_images=excluded.after_images,
            note=excluded.note,submitted_at=excluded.submitted_at,
            customer_decision='',customer_note='',decided_at='',
            updated_at=excluded.updated_at""",
            (
                request_id,
                provider_id,
                dump(before),
                dump(after),
                _safe_text(note, 600),
                submitted_at,
                submitted_at,
                submitted_at,
            ),
        )
        if request["status"] == "inProgress":
            RequestLifecycleService(self.con, now=self.now).transition(
                request_id,
                "awaitingConfirmation",
                actor_kind="provider",
                actor_id=provider_id,
                event_type="completion_submitted",
                detail={"beforeCount": len(before), "afterCount": len(after)},
            )
        return self.get(request_id) or {}

    def decide(
        self, request_id: str, user_id: str, decision: str, note: str = ""
    ) -> dict[str, Any]:
        request = _request(self.con, request_id)
        if request.get("user_id") != user_id:
            raise DomainError("completion_access_denied", 403)
        evidence = self.get(request_id)
        if not evidence:
            raise DomainError("completion_evidence_required", 409)
        if request.get("status") != "awaitingConfirmation":
            raise DomainError("completion_stage_not_allowed", 409)
        if decision not in {"resolved", "issue"}:
            raise DomainError("invalid_completion_decision")
        decided_at = iso(self.now)
        self.con.execute(
            """UPDATE request_completion_evidence SET customer_decision=?,
            customer_note=?,decided_at=?,updated_at=? WHERE request_id=?""",
            (decision, _safe_text(note, 600), decided_at, decided_at, request_id),
        )
        lifecycle = RequestLifecycleService(self.con, now=self.now)
        if decision == "resolved":
            lifecycle.transition(
                request_id,
                "closed",
                actor_kind="user",
                actor_id=user_id,
                event_type="completion_confirmed",
            )
            self.con.execute(
                """UPDATE providers SET completed_jobs=completed_jobs+1,
                updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (request["accepted_provider_id"],),
            )
            self.con.execute(
                """UPDATE customer_requests SET latitude=NULL,longitude=NULL,
                contact_consent='{}',updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (request_id,),
            )
        else:
            lifecycle.transition(
                request_id,
                "qualityReview",
                actor_kind="user",
                actor_id=user_id,
                event_type="completion_issue_reported",
                detail={"note": _safe_text(note, 240)},
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


def attach_workflow_data(
    con,
    request: dict[str, Any],
    *,
    asset_visible: bool = False,
) -> dict[str, Any]:
    """Attach role-filtered workflow details to one serialized request."""
    request_id = request.get("id", "")
    request["timeline"] = RequestLifecycleService(con).timeline(request_id)
    request["agreement"] = RequestAgreementService(con).get(request_id)
    request["completionEvidence"] = CompletionEvidenceService(con).get(request_id)
    asset_id = request.get("assetId") or request.get("asset_id") or ""
    request["assetId"] = asset_id
    request.pop("asset_id", None)
    request["serviceAsset"] = None
    if asset_id and asset_visible:
        row = con.execute("SELECT * FROM service_assets WHERE id=?", (asset_id,)).fetchone()
        if row:
            request["serviceAsset"] = ServiceAssetService(con)._serialize(row)
    return request
