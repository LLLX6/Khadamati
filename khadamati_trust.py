"""Trust, verification, dispute, and interaction-safety services for Khadamati.

The module intentionally contains no HTTP or UI concerns.  It owns the durable
rules so the web application, future mobile clients, and administration tools
all enforce the same decisions.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import secrets

from khadamati_domain import DomainError


VERIFICATION_STATUSES = {
    "unverified",
    "submitted",
    "under_review",
    "changes_required",
    "verified",
    "rejected",
    "suspended",
    "expired",
}
VERIFICATION_CHECK_STATUSES = {
    "pending",
    "verified",
    "rejected",
    "not_applicable",
}
VERIFICATION_LEVELS = {"basic", "identity", "professional", "business"}

COMPLAINT_STATUSES = {
    "open",
    "triaged",
    "investigating",
    "awaiting_user",
    "awaiting_provider",
    "resolved",
    "closed",
    "rejected",
    "reopened",
}
COMPLAINT_PRIORITIES = {"low", "normal", "high", "urgent"}
COMPLAINT_TRANSITIONS = {
    "open": {
        "triaged",
        "investigating",
        "awaiting_user",
        "awaiting_provider",
        "resolved",
        "closed",
        "rejected",
    },
    "triaged": {
        "investigating",
        "awaiting_user",
        "awaiting_provider",
        "resolved",
        "closed",
        "rejected",
    },
    "investigating": {
        "awaiting_user",
        "awaiting_provider",
        "resolved",
        "closed",
        "rejected",
    },
    "awaiting_user": {"investigating", "resolved", "closed", "rejected"},
    "awaiting_provider": {"investigating", "resolved", "closed", "rejected"},
    "resolved": {"closed", "reopened"},
    "closed": {"reopened"},
    "rejected": {"reopened"},
    "reopened": {
        "triaged",
        "investigating",
        "awaiting_user",
        "awaiting_provider",
        "resolved",
        "closed",
    },
}


def _id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(16)}"


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


def _json(value, fallback):
    if value in (None, ""):
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _safe_text(value, limit=1200) -> str:
    return str(value or "").strip()[:limit]


def _ensure_column(con, table: str, name: str, definition: str) -> None:
    columns = {row["name"] for row in con.execute(f"PRAGMA table_info({table})")}
    if name not in columns:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def install_trust_schema(con) -> None:
    """Install additive, backward-compatible trust tables and complaint fields."""

    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS provider_verification_cases(
          id TEXT PRIMARY KEY,
          provider_id TEXT NOT NULL UNIQUE,
          provider_kind TEXT NOT NULL DEFAULT 'individual',
          status TEXT NOT NULL DEFAULT 'unverified',
          level TEXT NOT NULL DEFAULT 'basic',
          identity_status TEXT NOT NULL DEFAULT 'pending',
          entity_status TEXT NOT NULL DEFAULT 'not_applicable',
          activity_status TEXT NOT NULL DEFAULT 'pending',
          requirements TEXT NOT NULL DEFAULT '[]',
          evidence TEXT NOT NULL DEFAULT '[]',
          reviewer_id TEXT DEFAULT '',
          decision_note TEXT DEFAULT '',
          submitted_at TEXT DEFAULT '',
          reviewed_at TEXT DEFAULT '',
          expires_at TEXT DEFAULT '',
          managed INTEGER NOT NULL DEFAULT 0,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS trust_case_events(
          id TEXT PRIMARY KEY,
          case_kind TEXT NOT NULL,
          case_id TEXT NOT NULL,
          actor_kind TEXT NOT NULL DEFAULT 'system',
          actor_id TEXT DEFAULT '',
          event_type TEXT NOT NULL,
          from_status TEXT DEFAULT '',
          to_status TEXT DEFAULT '',
          message TEXT DEFAULT '',
          metadata TEXT NOT NULL DEFAULT '{}',
          visible_to_subject INTEGER NOT NULL DEFAULT 1,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS complaint_evidence(
          id TEXT PRIMARY KEY,
          complaint_id TEXT NOT NULL,
          uploader_kind TEXT NOT NULL,
          uploader_id TEXT NOT NULL,
          media_path TEXT NOT NULL,
          media_type TEXT NOT NULL DEFAULT 'document',
          label TEXT DEFAULT '',
          created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS interaction_blocks(
          id TEXT PRIMARY KEY,
          blocker_kind TEXT NOT NULL,
          blocker_id TEXT NOT NULL,
          blocked_kind TEXT NOT NULL,
          blocked_id TEXT NOT NULL,
          request_id TEXT DEFAULT '',
          reason TEXT DEFAULT '',
          active INTEGER NOT NULL DEFAULT 1,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_verification_status
          ON provider_verification_cases(status,expires_at);
        CREATE INDEX IF NOT EXISTS idx_trust_events_case
          ON trust_case_events(case_kind,case_id,created_at);
        CREATE INDEX IF NOT EXISTS idx_complaint_evidence_case
          ON complaint_evidence(complaint_id,created_at);
        CREATE INDEX IF NOT EXISTS idx_interaction_blocks_actor
          ON interaction_blocks(blocker_kind,blocker_id,active);
        CREATE INDEX IF NOT EXISTS idx_interaction_blocks_target
          ON interaction_blocks(blocked_kind,blocked_id,active);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_interaction_blocks_unique_active
          ON interaction_blocks(blocker_kind,blocker_id,blocked_kind,blocked_id)
          WHERE active=1;
        """
    )
    for name, definition in (
        ("category", "TEXT DEFAULT ''"),
        ("source", "TEXT DEFAULT 'request'"),
        ("assigned_admin_id", "TEXT DEFAULT ''"),
        ("due_at", "TEXT DEFAULT ''"),
        ("outcome", "TEXT DEFAULT ''"),
        ("closed_at", "TEXT DEFAULT ''"),
        ("escalation_route", "TEXT DEFAULT ''"),
        ("reopen_count", "INTEGER NOT NULL DEFAULT 0"),
    ):
        _ensure_column(con, "complaints", name, definition)


def _verification_row(row, *, private=False) -> dict:
    if not row:
        return {}
    item = dict(row)
    item["providerId"] = item.pop("provider_id")
    item["providerKind"] = item.pop("provider_kind")
    item["identityStatus"] = item.pop("identity_status")
    item["entityStatus"] = item.pop("entity_status")
    item["activityStatus"] = item.pop("activity_status")
    item["reviewerId"] = item.pop("reviewer_id")
    item["decisionNote"] = item.pop("decision_note")
    item["submittedAt"] = item.pop("submitted_at")
    item["reviewedAt"] = item.pop("reviewed_at")
    item["expiresAt"] = item.pop("expires_at")
    item["createdAt"] = item.pop("created_at")
    item["updatedAt"] = item.pop("updated_at")
    item["managed"] = bool(item["managed"])
    item["requirements"] = _json(item["requirements"], [])
    item["evidence"] = _json(item["evidence"], [])
    item["verified"] = item["status"] == "verified"
    if not private:
        item.pop("reviewerId", None)
        item.pop("decisionNote", None)
        item.pop("requirements", None)
        item.pop("evidence", None)
        item.pop("managed", None)
    item["badge"] = verification_badge(item)
    return item


def verification_badge(case: dict) -> dict:
    status = case.get("status", "unverified")
    if status == "verified":
        checks = [
            case.get("identityStatus") == "verified",
            case.get("activityStatus") == "verified",
            case.get("entityStatus") in {"verified", "not_applicable"},
        ]
        if all(checks):
            if case.get("providerKind") == "company":
                return {
                    "key": "business_verified",
                    "ar": "تم التحقق من المنشأة والنشاط",
                    "en": "Business and activity verified",
                    "tone": "success",
                }
            return {
                "key": "professional_verified",
                "ar": "تم التحقق من الهوية والنشاط",
                "en": "Identity and activity verified",
                "tone": "success",
            }
    labels = {
        "submitted": ("بانتظار المراجعة", "Awaiting review", "pending"),
        "under_review": ("قيد التحقق", "Verification in progress", "pending"),
        "changes_required": ("يلزم تحديث التحقق", "Verification update required", "warning"),
        "rejected": ("تعذر التحقق", "Verification unsuccessful", "danger"),
        "suspended": ("التحقق موقوف", "Verification suspended", "danger"),
        "expired": ("انتهت صلاحية التحقق", "Verification expired", "warning"),
    }
    ar, en, tone = labels.get(
        status, ("لم يكتمل التحقق", "Verification incomplete", "neutral")
    )
    return {"key": status, "ar": ar, "en": en, "tone": tone}


class ProviderVerificationService:
    def __init__(self, con, *, now=None):
        self.con = con
        self.now = now or _now()

    def _event(
        self,
        case_id: str,
        event_type: str,
        *,
        actor_kind="system",
        actor_id="",
        from_status="",
        to_status="",
        message="",
        metadata=None,
        visible=True,
    ) -> None:
        self.con.execute(
            """INSERT INTO trust_case_events(
            id,case_kind,case_id,actor_kind,actor_id,event_type,from_status,to_status,
            message,metadata,visible_to_subject)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                _id("tev"),
                "verification",
                case_id,
                actor_kind,
                actor_id,
                event_type,
                from_status,
                to_status,
                _safe_text(message, 1200),
                _dump(metadata or {}),
                int(bool(visible)),
            ),
        )

    def ensure_case(self, provider) -> dict:
        provider = dict(provider)
        provider_id = _safe_text(provider.get("id"), 120)
        if not provider_id:
            raise DomainError("provider_not_found", 404)
        row = self.con.execute(
            "SELECT * FROM provider_verification_cases WHERE provider_id=?",
            (provider_id,),
        ).fetchone()
        if row:
            return _verification_row(row, private=True)
        provider_kind = (
            "company"
            if provider.get("provider_type", provider.get("providerType")) == "company"
            else "individual"
        )
        verified = bool(provider.get("verified"))
        status = "verified" if verified else "unverified"
        identity_status = "verified" if verified else "pending"
        entity_status = (
            "verified" if verified and provider_kind == "company"
            else "pending" if provider_kind == "company"
            else "not_applicable"
        )
        activity_status = "verified" if verified else "pending"
        level = (
            "business"
            if verified and provider_kind == "company"
            else "professional" if verified
            else "basic"
        )
        case_id = _id("vfy")
        expires_at = _safe_text(
            provider.get("verification_expiry", provider.get("verificationExpiry")), 80
        )
        self.con.execute(
            """INSERT INTO provider_verification_cases(
            id,provider_id,provider_kind,status,level,identity_status,entity_status,
            activity_status,expires_at,managed,reviewed_at)
            VALUES(?,?,?,?,?,?,?,?,?,0,?)""",
            (
                case_id,
                provider_id,
                provider_kind,
                status,
                level,
                identity_status,
                entity_status,
                activity_status,
                expires_at,
                _iso(self.now) if verified else "",
            ),
        )
        self._event(
            case_id,
            "legacy_state_imported",
            from_status="",
            to_status=status,
            message="Imported from the existing provider verification state.",
            visible=False,
        )
        return self.get(provider_id, private=True)

    def backfill(self) -> int:
        created = 0
        for provider in self.con.execute(
            """SELECT id,provider_type,verified,verification_expiry
            FROM providers WHERE COALESCE(status,'')!='deleted'"""
        ):
            exists = self.con.execute(
                "SELECT 1 FROM provider_verification_cases WHERE provider_id=?",
                (provider["id"],),
            ).fetchone()
            if not exists:
                self.ensure_case(provider)
                created += 1
        return created

    def get(self, provider_id: str, *, private=False) -> dict:
        row = self.con.execute(
            "SELECT * FROM provider_verification_cases WHERE provider_id=?",
            (_safe_text(provider_id, 120),),
        ).fetchone()
        return _verification_row(row, private=private)

    def list_admin(self) -> list[dict]:
        return [
            _verification_row(row, private=True)
            for row in self.con.execute(
                """SELECT * FROM provider_verification_cases
                ORDER BY CASE status
                  WHEN 'submitted' THEN 0 WHEN 'under_review' THEN 1
                  WHEN 'changes_required' THEN 2 WHEN 'expired' THEN 3
                  WHEN 'suspended' THEN 4 ELSE 5 END,
                updated_at DESC"""
            )
        ]

    def timeline(self, case_id: str, *, subject_view=False) -> list[dict]:
        query = (
            """SELECT * FROM trust_case_events
            WHERE case_kind='verification' AND case_id=? AND visible_to_subject=1
            ORDER BY created_at"""
            if subject_view
            else """SELECT * FROM trust_case_events
            WHERE case_kind='verification' AND case_id=? ORDER BY created_at"""
        )
        result = []
        for row in self.con.execute(query, (_safe_text(case_id, 120),)):
            item = dict(row)
            item["metadata"] = _json(item["metadata"], {})
            item["visibleToSubject"] = bool(item.pop("visible_to_subject"))
            result.append(item)
        return result

    def submit(
        self,
        provider_id: str,
        *,
        requirements=None,
        evidence=None,
        actor_id="",
    ) -> dict:
        provider = self.con.execute(
            """SELECT id,provider_type,verified,verification_expiry
            FROM providers WHERE id=? AND COALESCE(status,'')!='deleted'""",
            (_safe_text(provider_id, 120),),
        ).fetchone()
        if not provider:
            raise DomainError("provider_not_found", 404)
        case = self.ensure_case(provider)
        if case["status"] in {"submitted", "under_review"}:
            raise DomainError("verification_already_in_review", 409)
        required = [
            _safe_text(item, 100)
            for item in (requirements or [])
            if _safe_text(item, 100)
        ][:20]
        evidence_items = [
            _safe_text(item, 240)
            for item in (evidence or [])
            if _safe_text(item, 240)
        ][:20]
        old_status = case["status"]
        self.con.execute(
            """UPDATE provider_verification_cases SET
            status='submitted',requirements=?,evidence=?,decision_note='',
            submitted_at=?,reviewer_id='',reviewed_at='',managed=1,
            updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (
                _dump(required),
                _dump(evidence_items),
                _iso(self.now),
                case["id"],
            ),
        )
        self._event(
            case["id"],
            "verification_submitted",
            actor_kind="provider",
            actor_id=actor_id or provider_id,
            from_status=old_status,
            to_status="submitted",
            message="Verification information submitted for review.",
        )
        return self.get(provider_id, private=True)

    def review(self, provider_id: str, payload: dict, *, reviewer_id: str) -> dict:
        provider = self.con.execute(
            """SELECT id,provider_type,verified,verification_expiry,status
            FROM providers WHERE id=? AND COALESCE(status,'')!='deleted'""",
            (_safe_text(provider_id, 120),),
        ).fetchone()
        if not provider:
            raise DomainError("provider_not_found", 404)
        case = self.ensure_case(provider)
        status = _safe_text(payload.get("status"), 40)
        if status not in VERIFICATION_STATUSES - {"unverified", "submitted"}:
            raise DomainError("invalid_verification_status", 400)
        identity_status = _safe_text(
            payload.get("identityStatus", case["identityStatus"]), 40
        )
        entity_status = _safe_text(
            payload.get("entityStatus", case["entityStatus"]), 40
        )
        activity_status = _safe_text(
            payload.get("activityStatus", case["activityStatus"]), 40
        )
        if {
            identity_status,
            entity_status,
            activity_status,
        } - VERIFICATION_CHECK_STATUSES:
            raise DomainError("invalid_verification_check_status", 400)
        if case["providerKind"] == "individual":
            entity_status = "not_applicable"
        level = _safe_text(payload.get("level", case["level"]), 40)
        if level not in VERIFICATION_LEVELS:
            raise DomainError("invalid_verification_level", 400)
        if status == "verified":
            required_checks = [identity_status, activity_status]
            if case["providerKind"] == "company":
                required_checks.append(entity_status)
            if any(check != "verified" for check in required_checks):
                raise DomainError("verification_checks_incomplete", 409)
            level = (
                "business"
                if case["providerKind"] == "company"
                else "professional"
            )
        expires_at = _safe_text(payload.get("expiresAt"), 80)
        if status == "verified" and not expires_at:
            expires_at = (self.now + timedelta(days=365)).isoformat()
        note = _safe_text(payload.get("decisionNote"), 1200)
        old_status = case["status"]
        self.con.execute(
            """UPDATE provider_verification_cases SET
            status=?,level=?,identity_status=?,entity_status=?,activity_status=?,
            reviewer_id=?,decision_note=?,reviewed_at=?,expires_at=?,managed=1,
            updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (
                status,
                level,
                identity_status,
                entity_status,
                activity_status,
                reviewer_id,
                note,
                _iso(self.now),
                expires_at,
                case["id"],
            ),
        )
        if status == "verified":
            self.con.execute(
                """UPDATE providers SET verified=1,verification_expiry=?,
                status=CASE WHEN status IN ('under_review','pending','suspended')
                  THEN 'available' ELSE status END,
                listing_enabled=CASE WHEN status IN ('under_review','pending','suspended')
                  THEN 1 ELSE listing_enabled END,
                request_enabled=CASE WHEN status IN ('under_review','pending','suspended')
                  THEN 1 ELSE request_enabled END,
                updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (expires_at, provider_id),
            )
        elif status in {"rejected", "suspended", "expired"}:
            self.con.execute(
                """UPDATE providers SET verified=0,
                status=CASE WHEN ?='suspended' THEN 'suspended' ELSE status END,
                listing_enabled=CASE WHEN ?='suspended' THEN 0 ELSE listing_enabled END,
                request_enabled=CASE WHEN ?='suspended' THEN 0 ELSE request_enabled END,
                updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (status, status, status, provider_id),
            )
        else:
            self.con.execute(
                "UPDATE providers SET verified=0,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (provider_id,),
            )
        self._event(
            case["id"],
            "verification_reviewed",
            actor_kind="admin",
            actor_id=reviewer_id,
            from_status=old_status,
            to_status=status,
            message=note,
            metadata={
                "identityStatus": identity_status,
                "entityStatus": entity_status,
                "activityStatus": activity_status,
                "level": level,
                "expiresAt": expires_at,
            },
        )
        return self.get(provider_id, private=True)

    def expire_managed_cases(self) -> list[str]:
        expired = []
        now_iso = _iso(self.now)
        rows = self.con.execute(
            """SELECT id,provider_id,status FROM provider_verification_cases
            WHERE managed=1 AND status='verified' AND COALESCE(expires_at,'')!=''
            AND expires_at<?""",
            (now_iso,),
        ).fetchall()
        for row in rows:
            self.con.execute(
                """UPDATE provider_verification_cases SET status='expired',
                updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (row["id"],),
            )
            self.con.execute(
                """UPDATE providers SET verified=0,listing_enabled=0,request_enabled=0,
                updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (row["provider_id"],),
            )
            self._event(
                row["id"],
                "verification_expired",
                from_status="verified",
                to_status="expired",
                message="Verification validity ended; listing and new requests were paused.",
            )
            expired.append(row["provider_id"])
        return expired


def _complaint_status(value: str) -> str:
    value = _safe_text(value, 40)
    return "investigating" if value == "reviewing" else value or "open"


def _complaint_row(row) -> dict:
    if not row:
        return {}
    item = dict(row)
    item["status"] = _complaint_status(item.get("status"))
    item["providerId"] = item.get("provider_id", "")
    item["requestId"] = item.get("request_id", "")
    item["userId"] = item.get("user_id", "")
    item["customerName"] = item.get("customer_name", "")
    item["assignedAdminId"] = item.get("assigned_admin_id", "")
    item["dueAt"] = item.get("due_at", "")
    item["closedAt"] = item.get("closed_at", "")
    item["escalationRoute"] = item.get("escalation_route", "")
    item["reopenCount"] = int(item.get("reopen_count", 0) or 0)
    item["createdAt"] = item.get("created_at", "")
    item["updatedAt"] = item.get("updated_at", "")
    return item


class ComplaintCaseService:
    def __init__(self, con, *, now=None):
        self.con = con
        self.now = now or _now()

    def _event(
        self,
        complaint_id: str,
        event_type: str,
        *,
        actor_kind="system",
        actor_id="",
        from_status="",
        to_status="",
        message="",
        metadata=None,
        visible=True,
    ) -> None:
        self.con.execute(
            """INSERT INTO trust_case_events(
            id,case_kind,case_id,actor_kind,actor_id,event_type,from_status,to_status,
            message,metadata,visible_to_subject)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                _id("tev"),
                "complaint",
                complaint_id,
                actor_kind,
                actor_id,
                event_type,
                from_status,
                to_status,
                _safe_text(message, 1600),
                _dump(metadata or {}),
                int(bool(visible)),
            ),
        )

    def open_existing(
        self,
        complaint_id: str,
        *,
        actor_kind: str,
        actor_id: str,
        category="",
        source="request",
    ) -> dict:
        row = self.con.execute(
            "SELECT * FROM complaints WHERE id=?", (_safe_text(complaint_id, 120),)
        ).fetchone()
        if not row:
            raise DomainError("complaint_not_found", 404)
        due_at = (self.now + timedelta(days=3)).isoformat()
        self.con.execute(
            """UPDATE complaints SET category=?,source=?,due_at=?,
            status=CASE WHEN status='reviewing' THEN 'investigating' ELSE status END,
            updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (
                _safe_text(category or row["reason"], 80),
                _safe_text(source, 40) or "request",
                due_at,
                complaint_id,
            ),
        )
        existing = self.con.execute(
            """SELECT 1 FROM trust_case_events
            WHERE case_kind='complaint' AND case_id=? LIMIT 1""",
            (complaint_id,),
        ).fetchone()
        if not existing:
            self._event(
                complaint_id,
                "complaint_opened",
                actor_kind=actor_kind,
                actor_id=actor_id,
                to_status=_complaint_status(row["status"]),
                message=row["detail"],
                metadata={"category": category or row["reason"], "source": source},
            )
        return self.get(complaint_id, private=True)

    def add_evidence(
        self,
        complaint_id: str,
        paths: list[str],
        *,
        uploader_kind: str,
        uploader_id: str,
        labels=None,
    ) -> list[dict]:
        if not self.con.execute(
            "SELECT 1 FROM complaints WHERE id=?", (complaint_id,)
        ).fetchone():
            raise DomainError("complaint_not_found", 404)
        saved = []
        labels = labels or []
        for index, path in enumerate(paths[:5]):
            clean_path = _safe_text(path, 300)
            if not clean_path:
                continue
            evidence_id = _id("evi")
            media_type = (
                "image"
                if clean_path.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
                else "document"
            )
            label = _safe_text(labels[index] if index < len(labels) else "", 160)
            self.con.execute(
                """INSERT INTO complaint_evidence(
                id,complaint_id,uploader_kind,uploader_id,media_path,media_type,label)
                VALUES(?,?,?,?,?,?,?)""",
                (
                    evidence_id,
                    complaint_id,
                    uploader_kind,
                    uploader_id,
                    clean_path,
                    media_type,
                    label,
                ),
            )
            saved.append(
                {
                    "id": evidence_id,
                    "complaintId": complaint_id,
                    "uploaderKind": uploader_kind,
                    "uploaderId": uploader_id,
                    "mediaPath": clean_path,
                    "mediaType": media_type,
                    "label": label,
                }
            )
        if saved:
            self._event(
                complaint_id,
                "evidence_added",
                actor_kind=uploader_kind,
                actor_id=uploader_id,
                message=f"{len(saved)} evidence item(s) added.",
                metadata={"evidenceIds": [item["id"] for item in saved]},
            )
        return saved

    def add_message(
        self,
        complaint_id: str,
        message: str,
        *,
        actor_kind: str,
        actor_id: str,
        visible=True,
    ) -> None:
        clean = _safe_text(message, 1600)
        if not clean:
            raise DomainError("complaint_message_required", 400)
        if not self.con.execute(
            "SELECT 1 FROM complaints WHERE id=?", (complaint_id,)
        ).fetchone():
            raise DomainError("complaint_not_found", 404)
        self._event(
            complaint_id,
            "message_added",
            actor_kind=actor_kind,
            actor_id=actor_id,
            message=clean,
            visible=visible,
        )
        self.con.execute(
            "UPDATE complaints SET updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (complaint_id,),
        )

    def update(
        self,
        complaint_id: str,
        payload: dict,
        *,
        admin_id: str,
    ) -> dict:
        row = self.con.execute(
            "SELECT * FROM complaints WHERE id=?", (_safe_text(complaint_id, 120),)
        ).fetchone()
        if not row:
            raise DomainError("complaint_not_found", 404)
        current = _complaint_status(row["status"])
        status = _complaint_status(payload.get("status", current))
        if status not in COMPLAINT_STATUSES:
            raise DomainError("invalid_complaint_status", 400)
        if status != current and status not in COMPLAINT_TRANSITIONS.get(current, set()):
            raise DomainError("invalid_complaint_transition", 409)
        priority = _safe_text(payload.get("priority", row["priority"]), 30)
        if priority not in COMPLAINT_PRIORITIES:
            raise DomainError("invalid_complaint_priority", 400)
        resolution = _safe_text(
            payload.get("resolution", row["resolution"]), 1800
        )
        outcome = _safe_text(payload.get("outcome", row["outcome"]), 120)
        if status in {"resolved", "closed", "rejected"} and not resolution:
            raise DomainError("complaint_resolution_required", 400)
        assigned = _safe_text(
            payload.get("assignedAdminId", row["assigned_admin_id"] or admin_id), 120
        )
        due_at = _safe_text(payload.get("dueAt", row["due_at"]), 80)
        escalation_route = _safe_text(
            payload.get("escalationRoute", row["escalation_route"]), 240
        )
        closed_at = _iso(self.now) if status in {"closed", "rejected"} else ""
        reopen_increment = 1 if status == "reopened" and current != "reopened" else 0
        self.con.execute(
            """UPDATE complaints SET status=?,priority=?,resolution=?,outcome=?,
            assigned_admin_id=?,due_at=?,closed_at=?,escalation_route=?,
            reopen_count=COALESCE(reopen_count,0)+?,updated_at=CURRENT_TIMESTAMP
            WHERE id=?""",
            (
                status,
                priority,
                resolution,
                outcome,
                assigned,
                due_at,
                closed_at,
                escalation_route,
                reopen_increment,
                complaint_id,
            ),
        )
        note = _safe_text(payload.get("message") or resolution, 1600)
        self._event(
            complaint_id,
            "status_changed" if status != current else "case_updated",
            actor_kind="admin",
            actor_id=admin_id,
            from_status=current,
            to_status=status,
            message=note,
            metadata={
                "priority": priority,
                "assignedAdminId": assigned,
                "outcome": outcome,
                "dueAt": due_at,
                "escalationRoute": escalation_route,
            },
            visible=bool(payload.get("visibleToSubject", True)),
        )
        return self.get(complaint_id, private=True)

    def reopen(
        self,
        complaint_id: str,
        message: str,
        *,
        actor_kind: str,
        actor_id: str,
    ) -> dict:
        row = self.con.execute(
            "SELECT * FROM complaints WHERE id=?", (complaint_id,)
        ).fetchone()
        if not row:
            raise DomainError("complaint_not_found", 404)
        current = _complaint_status(row["status"])
        if current not in {"resolved", "closed", "rejected"}:
            raise DomainError("complaint_reopen_not_allowed", 409)
        if int(row["reopen_count"] or 0) >= 2:
            raise DomainError("complaint_reopen_limit_reached", 409)
        clean = _safe_text(message, 1600)
        if not clean:
            raise DomainError("complaint_message_required", 400)
        self.con.execute(
            """UPDATE complaints SET status='reopened',closed_at='',
            reopen_count=COALESCE(reopen_count,0)+1,updated_at=CURRENT_TIMESTAMP
            WHERE id=?""",
            (complaint_id,),
        )
        self._event(
            complaint_id,
            "complaint_reopened",
            actor_kind=actor_kind,
            actor_id=actor_id,
            from_status=current,
            to_status="reopened",
            message=clean,
        )
        return self.get(complaint_id, private=True)

    def get(self, complaint_id: str, *, private=False) -> dict:
        row = self.con.execute(
            "SELECT * FROM complaints WHERE id=?", (_safe_text(complaint_id, 120),)
        ).fetchone()
        if not row:
            return {}
        item = _complaint_row(row)
        evidence = []
        for evidence_row in self.con.execute(
            """SELECT * FROM complaint_evidence
            WHERE complaint_id=? ORDER BY created_at""",
            (complaint_id,),
        ):
            evidence_item = dict(evidence_row)
            evidence_item["complaintId"] = evidence_item.pop("complaint_id")
            evidence_item["uploaderKind"] = evidence_item.pop("uploader_kind")
            evidence_item["uploaderId"] = evidence_item.pop("uploader_id")
            evidence_item["mediaPath"] = evidence_item.pop("media_path")
            evidence_item["mediaType"] = evidence_item.pop("media_type")
            evidence_item["createdAt"] = evidence_item.pop("created_at")
            if not private:
                evidence_item.pop("uploaderId", None)
            evidence.append(evidence_item)
        events = []
        event_query = (
            """SELECT * FROM trust_case_events
            WHERE case_kind='complaint' AND case_id=?
            ORDER BY created_at"""
            if private
            else """SELECT * FROM trust_case_events
            WHERE case_kind='complaint' AND case_id=? AND visible_to_subject=1
            ORDER BY created_at"""
        )
        for event_row in self.con.execute(event_query, (complaint_id,)):
            event = dict(event_row)
            event["metadata"] = _json(event["metadata"], {})
            event["visibleToSubject"] = bool(event.pop("visible_to_subject"))
            if not private:
                event.pop("actor_id", None)
            events.append(event)
        item["evidence"] = evidence
        item["timeline"] = events
        if not private:
            item.pop("phone", None)
            item.pop("assigned_admin_id", None)
            item.pop("assignedAdminId", None)
        return item

    def list_admin(self) -> list[dict]:
        return [
            self.get(row["id"], private=True)
            for row in self.con.execute(
                """SELECT id FROM complaints
                ORDER BY CASE priority
                  WHEN 'urgent' THEN 0 WHEN 'high' THEN 1
                  WHEN 'normal' THEN 2 ELSE 3 END,
                CASE status
                  WHEN 'open' THEN 0 WHEN 'reopened' THEN 1
                  WHEN 'triaged' THEN 2 WHEN 'investigating' THEN 3
                  WHEN 'awaiting_user' THEN 4 WHEN 'awaiting_provider' THEN 5
                  ELSE 6 END,
                updated_at DESC"""
            )
        ]

    def list_for_user(self, user_id: str) -> list[dict]:
        return [
            self.get(row["id"])
            for row in self.con.execute(
                "SELECT id FROM complaints WHERE user_id=? ORDER BY updated_at DESC",
                (_safe_text(user_id, 120),),
            )
        ]

    def list_for_provider(self, provider_id: str) -> list[dict]:
        return [
            self.get(row["id"])
            for row in self.con.execute(
                """SELECT id FROM complaints WHERE provider_id=?
                ORDER BY updated_at DESC""",
                (_safe_text(provider_id, 120),),
            )
        ]


class InteractionBlockService:
    def __init__(self, con):
        self.con = con

    def _account_exists(self, kind: str, account_id: str) -> bool:
        if kind == "user":
            row = self.con.execute(
                "SELECT 1 FROM app_users WHERE id=?", (account_id,)
            ).fetchone()
        else:
            row = self.con.execute(
                "SELECT 1 FROM providers WHERE id=?", (account_id,)
            ).fetchone()
        return bool(row)

    def _validate_pair(
        self,
        blocker_kind: str,
        blocker_id: str,
        blocked_kind: str,
        blocked_id: str,
    ) -> tuple[str, str, str, str]:
        blocker_kind = _safe_text(blocker_kind, 20)
        blocked_kind = _safe_text(blocked_kind, 20)
        blocker_id = _safe_text(blocker_id, 120)
        blocked_id = _safe_text(blocked_id, 120)
        if (blocker_kind, blocked_kind) not in {
            ("user", "provider"),
            ("provider", "user"),
        }:
            raise DomainError("invalid_block_relationship", 400)
        if not blocker_id or not blocked_id:
            raise DomainError("block_accounts_required", 400)
        if not self._account_exists(blocker_kind, blocker_id):
            raise DomainError("blocker_account_not_found", 404)
        if not self._account_exists(blocked_kind, blocked_id):
            raise DomainError("blocked_account_not_found", 404)
        return blocker_kind, blocker_id, blocked_kind, blocked_id

    def block(
        self,
        blocker_kind: str,
        blocker_id: str,
        blocked_kind: str,
        blocked_id: str,
        *,
        reason="",
        request_id="",
    ) -> dict:
        blocker_kind, blocker_id, blocked_kind, blocked_id = self._validate_pair(
            blocker_kind, blocker_id, blocked_kind, blocked_id
        )
        existing = self.con.execute(
            """SELECT * FROM interaction_blocks WHERE blocker_kind=? AND blocker_id=?
            AND blocked_kind=? AND blocked_id=? AND active=1""",
            (blocker_kind, blocker_id, blocked_kind, blocked_id),
        ).fetchone()
        if existing:
            return self._row(existing)
        block_id = _id("blk")
        self.con.execute(
            """INSERT INTO interaction_blocks(
            id,blocker_kind,blocker_id,blocked_kind,blocked_id,request_id,reason,active)
            VALUES(?,?,?,?,?,?,?,1)""",
            (
                block_id,
                blocker_kind,
                blocker_id,
                blocked_kind,
                blocked_id,
                _safe_text(request_id, 120),
                _safe_text(reason, 500),
            ),
        )
        return self.get(block_id)

    def unblock(
        self,
        blocker_kind: str,
        blocker_id: str,
        blocked_kind: str,
        blocked_id: str,
    ) -> bool:
        result = self.con.execute(
            """UPDATE interaction_blocks SET active=0,updated_at=CURRENT_TIMESTAMP
            WHERE blocker_kind=? AND blocker_id=? AND blocked_kind=? AND blocked_id=?
            AND active=1""",
            (
                _safe_text(blocker_kind, 20),
                _safe_text(blocker_id, 120),
                _safe_text(blocked_kind, 20),
                _safe_text(blocked_id, 120),
            ),
        )
        return result.rowcount > 0

    def get(self, block_id: str) -> dict:
        return self._row(
            self.con.execute(
                "SELECT * FROM interaction_blocks WHERE id=?",
                (_safe_text(block_id, 120),),
            ).fetchone()
        )

    def _row(self, row) -> dict:
        if not row:
            return {}
        item = dict(row)
        item["blockerKind"] = item.pop("blocker_kind")
        item["blockerId"] = item.pop("blocker_id")
        item["blockedKind"] = item.pop("blocked_kind")
        item["blockedId"] = item.pop("blocked_id")
        item["requestId"] = item.pop("request_id")
        item["createdAt"] = item.pop("created_at")
        item["updatedAt"] = item.pop("updated_at")
        item["active"] = bool(item["active"])
        return item

    def list_for(self, actor_kind: str, actor_id: str) -> list[dict]:
        return [
            self._row(row)
            for row in self.con.execute(
                """SELECT * FROM interaction_blocks
                WHERE blocker_kind=? AND blocker_id=? AND active=1
                ORDER BY updated_at DESC""",
                (_safe_text(actor_kind, 20), _safe_text(actor_id, 120)),
            )
        ]

    def is_blocked(self, user_id: str, provider_id: str) -> bool:
        return bool(
            self.con.execute(
                """SELECT 1 FROM interaction_blocks WHERE active=1 AND (
                  (blocker_kind='user' AND blocker_id=? AND blocked_kind='provider'
                    AND blocked_id=?)
                  OR
                  (blocker_kind='provider' AND blocker_id=? AND blocked_kind='user'
                    AND blocked_id=?)
                ) LIMIT 1""",
                (
                    _safe_text(user_id, 120),
                    _safe_text(provider_id, 120),
                    _safe_text(provider_id, 120),
                    _safe_text(user_id, 120),
                ),
            ).fetchone()
        )

    def assert_allowed(self, user_id: str, provider_id: str) -> None:
        if self.is_blocked(user_id, provider_id):
            raise DomainError("interaction_blocked", 403)


def trust_statistics(con) -> dict:
    statuses = {
        row["status"]: int(row["n"])
        for row in con.execute(
            "SELECT status,COUNT(*) n FROM provider_verification_cases GROUP BY status"
        )
    }
    complaint_statuses = {
        _complaint_status(row["status"]): int(row["n"])
        for row in con.execute(
            "SELECT status,COUNT(*) n FROM complaints GROUP BY status"
        )
    }
    return {
        "verification": statuses,
        "complaints": complaint_statuses,
        "activeBlocks": int(
            con.execute(
                "SELECT COUNT(*) n FROM interaction_blocks WHERE active=1"
            ).fetchone()["n"]
        ),
    }
