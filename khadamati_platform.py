"""Production-oriented platform capabilities shared across Khadamati roles.

The services in this module deliberately keep policy decisions on the server,
preserve existing rows, and expose only aggregate or owner-scoped data.
External integrations are represented by disabled adapters until an approved
contract and configuration exist.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json
import secrets
from typing import Any

from khadamati_domain import DomainError


FEATURE_DEFAULTS = {
    "growth_hub": {"enabled": True, "rollout": 100, "audiences": ["user", "provider", "company", "admin"]},
    "request_assistant": {"enabled": False, "rollout": 0, "audiences": ["user"]},
    "enterprise_api": {"enabled": False, "rollout": 0, "audiences": ["organization", "admin"]},
    "insurance_adapter": {"enabled": False, "rollout": 0, "audiences": ["admin"]},
    "government_adapter": {"enabled": False, "rollout": 0, "audiences": ["admin"]},
    "national_reports": {"enabled": False, "rollout": 0, "audiences": ["admin"]},
}


def install_platform_schema(con) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS conversation_threads(
          request_id TEXT PRIMARY KEY,
          status TEXT NOT NULL DEFAULT 'open',
          ended_by_kind TEXT NOT NULL DEFAULT '',
          ended_by_id TEXT NOT NULL DEFAULT '',
          end_reason TEXT NOT NULL DEFAULT '',
          ended_at TEXT NOT NULL DEFAULT '',
          reopened_at TEXT NOT NULL DEFAULT '',
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS conversation_preferences(
          request_id TEXT NOT NULL,
          actor_kind TEXT NOT NULL,
          actor_id TEXT NOT NULL,
          muted_until TEXT NOT NULL DEFAULT '',
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY(request_id,actor_kind,actor_id)
        );
        CREATE TABLE IF NOT EXISTS provider_legal_profiles(
          provider_id TEXT PRIMARY KEY,
          pathway TEXT NOT NULL DEFAULT 'individual_omani',
          nationality TEXT NOT NULL DEFAULT '',
          residency_status TEXT NOT NULL DEFAULT 'not_applicable',
          employer_name TEXT NOT NULL DEFAULT '',
          employer_authorization_status TEXT NOT NULL DEFAULT 'not_applicable',
          work_permit_expiry TEXT NOT NULL DEFAULT '',
          residency_expiry TEXT NOT NULL DEFAULT '',
          commercial_expiry TEXT NOT NULL DEFAULT '',
          activity_license_expiry TEXT NOT NULL DEFAULT '',
          review_status TEXT NOT NULL DEFAULT 'pending',
          review_note TEXT NOT NULL DEFAULT '',
          reviewed_by TEXT NOT NULL DEFAULT '',
          reviewed_at TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS customer_organizations(
          id TEXT PRIMARY KEY,
          owner_user_id TEXT NOT NULL,
          name TEXT NOT NULL,
          organization_type TEXT NOT NULL DEFAULT 'business',
          status TEXT NOT NULL DEFAULT 'active',
          approval_mode TEXT NOT NULL DEFAULT 'owner',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS organization_members(
          id TEXT PRIMARY KEY,
          organization_id TEXT NOT NULL,
          user_id TEXT NOT NULL DEFAULT '',
          name TEXT NOT NULL,
          phone TEXT NOT NULL DEFAULT '',
          role TEXT NOT NULL DEFAULT 'requester',
          active INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(organization_id,phone)
        );
        CREATE TABLE IF NOT EXISTS organization_locations(
          id TEXT PRIMARY KEY,
          organization_id TEXT NOT NULL,
          name TEXT NOT NULL,
          gov TEXT NOT NULL DEFAULT '',
          wilayah TEXT NOT NULL DEFAULT '',
          address TEXT NOT NULL DEFAULT '',
          active INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS maintenance_contracts(
          id TEXT PRIMARY KEY,
          owner_user_id TEXT NOT NULL,
          organization_id TEXT NOT NULL DEFAULT '',
          provider_id TEXT NOT NULL,
          request_id TEXT NOT NULL DEFAULT '',
          service_asset_id TEXT NOT NULL DEFAULT '',
          service_value TEXT NOT NULL,
          title TEXT NOT NULL,
          frequency_days INTEGER NOT NULL,
          amount REAL NOT NULL DEFAULT 0,
          next_due_at TEXT NOT NULL,
          auto_renew INTEGER NOT NULL DEFAULT 0,
          status TEXT NOT NULL DEFAULT 'active',
          last_completed_at TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS provider_crm_records(
          id TEXT PRIMARY KEY,
          provider_id TEXT NOT NULL,
          user_id TEXT NOT NULL,
          request_id TEXT NOT NULL,
          display_name TEXT NOT NULL DEFAULT '',
          stage TEXT NOT NULL DEFAULT 'active',
          next_action_at TEXT NOT NULL DEFAULT '',
          note TEXT NOT NULL DEFAULT '',
          invoice_status TEXT NOT NULL DEFAULT 'not_issued',
          warranty_until TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(provider_id,request_id)
        );
        CREATE TABLE IF NOT EXISTS referrals(
          id TEXT PRIMARY KEY,
          code TEXT NOT NULL UNIQUE,
          referrer_kind TEXT NOT NULL,
          referrer_id TEXT NOT NULL,
          referred_kind TEXT NOT NULL DEFAULT '',
          referred_id TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'created',
          risk_status TEXT NOT NULL DEFAULT 'clear',
          qualified_at TEXT NOT NULL DEFAULT '',
          reward_status TEXT NOT NULL DEFAULT 'not_eligible',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(referred_kind,referred_id)
        );
        CREATE TABLE IF NOT EXISTS training_modules(
          id TEXT PRIMARY KEY,
          title_ar TEXT NOT NULL,
          title_en TEXT NOT NULL DEFAULT '',
          audience TEXT NOT NULL DEFAULT 'provider',
          content_ar TEXT NOT NULL DEFAULT '',
          content_en TEXT NOT NULL DEFAULT '',
          pass_score INTEGER NOT NULL DEFAULT 70,
          sort_order INTEGER NOT NULL DEFAULT 0,
          active INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS training_progress(
          module_id TEXT NOT NULL,
          provider_id TEXT NOT NULL,
          score INTEGER NOT NULL DEFAULT 0,
          status TEXT NOT NULL DEFAULT 'started',
          completed_at TEXT NOT NULL DEFAULT '',
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY(module_id,provider_id)
        );
        CREATE TABLE IF NOT EXISTS provider_achievements(
          id TEXT PRIMARY KEY,
          provider_id TEXT NOT NULL,
          code TEXT NOT NULL,
          evidence TEXT NOT NULL DEFAULT '{}',
          earned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(provider_id,code)
        );
        CREATE TABLE IF NOT EXISTS demand_alerts(
          id TEXT PRIMARY KEY,
          target_kind TEXT NOT NULL,
          target_id TEXT NOT NULL,
          service_value TEXT NOT NULL,
          gov TEXT NOT NULL DEFAULT '',
          wilayah TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'active',
          last_notified_at TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(target_kind,target_id,service_value,gov,wilayah)
        );
        CREATE TABLE IF NOT EXISTS platform_feature_flags(
          key TEXT PRIMARY KEY,
          enabled INTEGER NOT NULL DEFAULT 0,
          rollout_percentage INTEGER NOT NULL DEFAULT 0,
          audiences TEXT NOT NULL DEFAULT '[]',
          config TEXT NOT NULL DEFAULT '{}',
          updated_by TEXT NOT NULL DEFAULT '',
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS risk_reviews(
          id TEXT PRIMARY KEY,
          subject_kind TEXT NOT NULL,
          subject_id TEXT NOT NULL,
          signal_type TEXT NOT NULL,
          score INTEGER NOT NULL DEFAULT 0,
          signals TEXT NOT NULL DEFAULT '[]',
          status TEXT NOT NULL DEFAULT 'pending_review',
          reviewer_id TEXT NOT NULL DEFAULT '',
          decision TEXT NOT NULL DEFAULT '',
          note TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS enterprise_api_clients(
          id TEXT PRIMARY KEY,
          organization_id TEXT NOT NULL,
          name TEXT NOT NULL,
          key_prefix TEXT NOT NULL,
          key_hash TEXT NOT NULL UNIQUE,
          scopes TEXT NOT NULL DEFAULT '[]',
          rate_limit INTEGER NOT NULL DEFAULT 60,
          active INTEGER NOT NULL DEFAULT 1,
          expires_at TEXT NOT NULL DEFAULT '',
          last_used_at TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS enterprise_api_audit(
          id TEXT PRIMARY KEY,
          client_id TEXT NOT NULL,
          scope TEXT NOT NULL,
          resource TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS integration_adapters(
          key TEXT PRIMARY KEY,
          kind TEXT NOT NULL,
          enabled INTEGER NOT NULL DEFAULT 0,
          mode TEXT NOT NULL DEFAULT 'disabled',
          legal_status TEXT NOT NULL DEFAULT 'not_approved',
          config TEXT NOT NULL DEFAULT '{}',
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS financial_scenarios(
          id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          assumptions TEXT NOT NULL DEFAULT '{}',
          results TEXT NOT NULL DEFAULT '{}',
          created_by TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_conversation_preferences_actor
          ON conversation_preferences(actor_kind,actor_id,updated_at);
        CREATE INDEX IF NOT EXISTS idx_legal_review
          ON provider_legal_profiles(review_status,updated_at);
        CREATE INDEX IF NOT EXISTS idx_org_owner
          ON customer_organizations(owner_user_id,status);
        CREATE INDEX IF NOT EXISTS idx_contract_owner
          ON maintenance_contracts(owner_user_id,status,next_due_at);
        CREATE INDEX IF NOT EXISTS idx_contract_provider
          ON maintenance_contracts(provider_id,status,next_due_at);
        CREATE INDEX IF NOT EXISTS idx_crm_provider
          ON provider_crm_records(provider_id,stage,updated_at);
        CREATE INDEX IF NOT EXISTS idx_training_provider
          ON training_progress(provider_id,status);
        CREATE INDEX IF NOT EXISTS idx_demand_alert
          ON demand_alerts(service_value,gov,wilayah,status);
        CREATE INDEX IF NOT EXISTS idx_risk_queue
          ON risk_reviews(status,score,created_at);
        """
    )
    for key, value in FEATURE_DEFAULTS.items():
        con.execute(
            """INSERT OR IGNORE INTO platform_feature_flags(
            key,enabled,rollout_percentage,audiences,config)
            VALUES(?,?,?,?,?)""",
            (
                key,
                int(value["enabled"]),
                int(value["rollout"]),
                _dump(value["audiences"]),
                "{}",
            ),
        )
    adapters = (
        ("payments", "payment", 0, "manual", "configuration_required"),
        ("insurance", "insurance", 0, "disabled", "contract_required"),
        ("government", "government", 0, "disabled", "agreement_required"),
    )
    for item in adapters:
        con.execute(
            """INSERT OR IGNORE INTO integration_adapters(
            key,kind,enabled,mode,legal_status) VALUES(?,?,?,?,?)""",
            item,
        )
    modules = (
        (
            "safe_service_delivery",
            "تنفيذ الخدمة بأمان",
            "Safe service delivery",
            "provider",
            "تأكيد النطاق والموعد وحماية بيانات العميل قبل بدء العمل.",
            "Confirm scope, appointment, and customer privacy before work starts.",
            70,
            10,
        ),
        (
            "professional_quotes",
            "كتابة عرض احترافي",
            "Professional quotations",
            "provider",
            "افصل الأجور والمواد والمدة والضمان واكتب نطاقًا واضحًا.",
            "Separate labor, materials, duration, warranty, and scope.",
            70,
            20,
        ),
        (
            "customer_communication",
            "التواصل المحترف",
            "Professional communication",
            "all_providers",
            "استخدم المحادثة الداخلية ولا تطلب بيانات لا يحتاجها تنفيذ الخدمة.",
            "Use in-app chat and request only data needed to deliver the service.",
            75,
            30,
        ),
    )
    for module in modules:
        con.execute(
            """INSERT OR IGNORE INTO training_modules(
            id,title_ar,title_en,audience,content_ar,content_en,pass_score,sort_order)
            VALUES(?,?,?,?,?,?,?,?)""",
            module,
        )


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _text(value: Any, limit: int = 240) -> str:
    return str(value or "").strip()[:limit]


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).astimezone(UTC).isoformat()


def _parse(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=parsed.tzinfo or UTC).astimezone(UTC)


def _id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(12)}"


def _bounded_int(value: Any, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise DomainError("invalid_integer", 400) from exc
    if number < minimum or number > maximum:
        raise DomainError("number_out_of_range", 400)
    return number


def _bounded_amount(value: Any) -> float:
    try:
        number = round(float(value or 0), 3)
    except (TypeError, ValueError) as exc:
        raise DomainError("invalid_amount", 400) from exc
    if number < 0 or number > 1_000_000:
        raise DomainError("invalid_amount", 400)
    return number


def _row(row) -> dict[str, Any]:
    return dict(row) if row else {}


class ConversationControlService:
    def __init__(self, con, *, now: datetime | None = None):
        self.con = con
        self.now = (now or _now()).astimezone(UTC)

    def _request(self, request_id: str):
        row = self.con.execute(
            "SELECT * FROM customer_requests WHERE id=? AND status!='deleted'",
            (request_id,),
        ).fetchone()
        if not row:
            raise DomainError("request_not_found", 404)
        return row

    def assert_member(self, request_id: str, actor_kind: str, actor_id: str):
        request = self._request(request_id)
        allowed = (
            actor_kind == "admin"
            or (actor_kind == "user" and request["user_id"] == actor_id)
            or (
                actor_kind == "provider"
                and request["accepted_provider_id"] == actor_id
            )
        )
        if not allowed:
            raise DomainError("conversation_access_denied", 403)
        return request

    def summary(self, request_id: str, actor_kind: str, actor_id: str) -> dict[str, Any]:
        self.assert_member(request_id, actor_kind, actor_id)
        thread = self.con.execute(
            "SELECT * FROM conversation_threads WHERE request_id=?", (request_id,)
        ).fetchone()
        preference = self.con.execute(
            """SELECT muted_until FROM conversation_preferences
            WHERE request_id=? AND actor_kind=? AND actor_id=?""",
            (request_id, actor_kind, actor_id),
        ).fetchone()
        muted_until = preference["muted_until"] if preference else ""
        muted = bool(_parse(muted_until) and _parse(muted_until) > self.now)
        return {
            "requestId": request_id,
            "status": thread["status"] if thread else "open",
            "endedAt": thread["ended_at"] if thread else "",
            "endReason": thread["end_reason"] if thread else "",
            "endedByKind": thread["ended_by_kind"] if thread else "",
            "canReopen": bool(
                thread
                and thread["status"] == "ended"
                and (
                    actor_kind == "admin"
                    or (
                        thread["ended_by_kind"] == actor_kind
                        and thread["ended_by_id"] == actor_id
                    )
                )
            ),
            "muted": muted,
            "mutedUntil": muted_until if muted else "",
        }

    def assert_open(self, request_id: str, actor_kind: str, actor_id: str) -> None:
        state = self.summary(request_id, actor_kind, actor_id)
        if state["status"] == "ended":
            raise DomainError("conversation_ended", 409)

    def notifications_muted(self, request_id: str, actor_kind: str, actor_id: str) -> bool:
        try:
            return bool(self.summary(request_id, actor_kind, actor_id)["muted"])
        except DomainError:
            return False

    def update(
        self,
        request_id: str,
        actor_kind: str,
        actor_id: str,
        action: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        self.assert_member(request_id, actor_kind, actor_id)
        if action == "mute":
            hours = _bounded_int(data.get("hours", 24), 1, 24 * 30)
            muted_until = _iso(self.now + timedelta(hours=hours))
            self.con.execute(
                """INSERT INTO conversation_preferences(
                request_id,actor_kind,actor_id,muted_until,updated_at)
                VALUES(?,?,?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(request_id,actor_kind,actor_id) DO UPDATE SET
                muted_until=excluded.muted_until,updated_at=CURRENT_TIMESTAMP""",
                (request_id, actor_kind, actor_id, muted_until),
            )
        elif action == "unmute":
            self.con.execute(
                """DELETE FROM conversation_preferences
                WHERE request_id=? AND actor_kind=? AND actor_id=?""",
                (request_id, actor_kind, actor_id),
            )
        elif action == "end":
            reason = _text(data.get("reason"), 300)
            self.con.execute(
                """INSERT INTO conversation_threads(
                request_id,status,ended_by_kind,ended_by_id,end_reason,ended_at,updated_at)
                VALUES(?,'ended',?,?,?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(request_id) DO UPDATE SET status='ended',
                ended_by_kind=excluded.ended_by_kind,ended_by_id=excluded.ended_by_id,
                end_reason=excluded.end_reason,ended_at=excluded.ended_at,
                updated_at=CURRENT_TIMESTAMP""",
                (request_id, actor_kind, actor_id, reason, _iso(self.now)),
            )
        elif action == "reopen":
            thread = self.con.execute(
                "SELECT * FROM conversation_threads WHERE request_id=?", (request_id,)
            ).fetchone()
            if not thread or thread["status"] != "ended":
                raise DomainError("conversation_not_ended", 409)
            if actor_kind != "admin" and not (
                thread["ended_by_kind"] == actor_kind
                and thread["ended_by_id"] == actor_id
            ):
                raise DomainError("conversation_reopen_denied", 403)
            self.con.execute(
                """UPDATE conversation_threads SET status='open',reopened_at=?,
                updated_at=CURRENT_TIMESTAMP WHERE request_id=?""",
                (_iso(self.now), request_id),
            )
        else:
            raise DomainError("invalid_conversation_action", 400)
        return self.summary(request_id, actor_kind, actor_id)


class ProviderLegalProfileService:
    PATHWAYS = {"individual_omani", "individual_foreign", "company"}
    REVIEW_STATES = {"pending", "needs_information", "approved", "rejected", "expired"}

    def __init__(self, con):
        self.con = con

    def get(self, provider_id: str, *, private: bool = False) -> dict[str, Any]:
        row = self.con.execute(
            "SELECT * FROM provider_legal_profiles WHERE provider_id=?", (provider_id,)
        ).fetchone()
        if not row:
            return {}
        item = _row(row)
        public = {
            "providerId": item["provider_id"],
            "pathway": item["pathway"],
            "reviewStatus": item["review_status"],
            "updatedAt": item["updated_at"],
        }
        if private:
            public.update(
                {
                    "nationality": item["nationality"],
                    "residencyStatus": item["residency_status"],
                    "employerName": item["employer_name"],
                    "employerAuthorizationStatus": item["employer_authorization_status"],
                    "workPermitExpiry": item["work_permit_expiry"],
                    "residencyExpiry": item["residency_expiry"],
                    "commercialExpiry": item["commercial_expiry"],
                    "activityLicenseExpiry": item["activity_license_expiry"],
                    "reviewNote": item["review_note"],
                    "reviewedAt": item["reviewed_at"],
                }
            )
        return public

    def save(self, provider_id: str, data: dict[str, Any]) -> dict[str, Any]:
        provider = self.con.execute(
            "SELECT provider_type,nationality FROM providers WHERE id=?", (provider_id,)
        ).fetchone()
        if not provider:
            raise DomainError("provider_not_found", 404)
        pathway = _text(data.get("pathway"), 40)
        if not pathway:
            pathway = "company" if provider["provider_type"] == "company" else "individual_omani"
        if pathway not in self.PATHWAYS:
            raise DomainError("invalid_legal_pathway", 400)
        if provider["provider_type"] == "company" and pathway != "company":
            raise DomainError("company_pathway_required", 400)
        if provider["provider_type"] != "company" and pathway == "company":
            raise DomainError("individual_pathway_required", 400)
        nationality = _text(data.get("nationality") or provider["nationality"], 80)
        employer_name = _text(data.get("employerName"), 160)
        work_permit_expiry = _text(data.get("workPermitExpiry"), 40)
        residency_expiry = _text(data.get("residencyExpiry"), 40)
        if pathway == "individual_foreign" and (
            not nationality or not employer_name or not work_permit_expiry
        ):
            raise DomainError("foreign_worker_details_required", 400)
        self.con.execute(
            """INSERT INTO provider_legal_profiles(
            provider_id,pathway,nationality,residency_status,employer_name,
            employer_authorization_status,work_permit_expiry,residency_expiry,
            commercial_expiry,activity_license_expiry,review_status,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,'pending',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
            ON CONFLICT(provider_id) DO UPDATE SET pathway=excluded.pathway,
            nationality=excluded.nationality,residency_status=excluded.residency_status,
            employer_name=excluded.employer_name,
            employer_authorization_status=excluded.employer_authorization_status,
            work_permit_expiry=excluded.work_permit_expiry,
            residency_expiry=excluded.residency_expiry,
            commercial_expiry=excluded.commercial_expiry,
            activity_license_expiry=excluded.activity_license_expiry,
            review_status='pending',review_note='',reviewed_by='',reviewed_at='',
            updated_at=CURRENT_TIMESTAMP""",
            (
                provider_id,
                pathway,
                nationality,
                _text(data.get("residencyStatus"), 40) or "not_applicable",
                employer_name,
                _text(data.get("employerAuthorizationStatus"), 40) or "not_applicable",
                work_permit_expiry,
                residency_expiry,
                _text(data.get("commercialExpiry"), 40),
                _text(data.get("activityLicenseExpiry"), 40),
            ),
        )
        return self.get(provider_id, private=True)

    def review(
        self, provider_id: str, status: str, reviewer_id: str, note: str = ""
    ) -> dict[str, Any]:
        if status not in self.REVIEW_STATES:
            raise DomainError("invalid_legal_review_status", 400)
        result = self.con.execute(
            """UPDATE provider_legal_profiles SET review_status=?,review_note=?,
            reviewed_by=?,reviewed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
            WHERE provider_id=?""",
            (status, _text(note, 600), reviewer_id, provider_id),
        )
        if result.rowcount != 1:
            raise DomainError("legal_profile_not_found", 404)
        return self.get(provider_id, private=True)

    def review_queue(self) -> list[dict[str, Any]]:
        return [
            self.get(row["provider_id"], private=True)
            for row in self.con.execute(
                """SELECT provider_id FROM provider_legal_profiles
                ORDER BY CASE review_status WHEN 'pending' THEN 0
                WHEN 'needs_information' THEN 1 ELSE 2 END,updated_at DESC"""
            )
        ]


class OrganizationService:
    MEMBER_ROLES = {"owner", "approver", "requester", "viewer"}

    def __init__(self, con):
        self.con = con

    def _owned(self, organization_id: str, user_id: str):
        row = self.con.execute(
            "SELECT * FROM customer_organizations WHERE id=? AND owner_user_id=?",
            (organization_id, user_id),
        ).fetchone()
        if not row:
            raise DomainError("organization_access_denied", 403)
        return row

    def save(self, user_id: str, data: dict[str, Any]) -> dict[str, Any]:
        organization_id = _text(data.get("id"), 120) or _id("org")
        name = _text(data.get("name"), 160)
        if len(name) < 2:
            raise DomainError("organization_name_required", 400)
        existing = self.con.execute(
            "SELECT owner_user_id FROM customer_organizations WHERE id=?",
            (organization_id,),
        ).fetchone()
        if existing and existing["owner_user_id"] != user_id:
            raise DomainError("organization_access_denied", 403)
        organization_type = _text(data.get("organizationType"), 40) or "business"
        if organization_type not in {"business", "nonprofit", "property_management", "other"}:
            raise DomainError("invalid_organization_type", 400)
        approval_mode = _text(data.get("approvalMode"), 40) or "owner"
        if approval_mode not in {"owner", "single_approver", "two_step"}:
            raise DomainError("invalid_approval_mode", 400)
        self.con.execute(
            """INSERT INTO customer_organizations(
            id,owner_user_id,name,organization_type,status,approval_mode)
            VALUES(?,?,?,?,'active',?)
            ON CONFLICT(id) DO UPDATE SET name=excluded.name,
            organization_type=excluded.organization_type,
            approval_mode=excluded.approval_mode,updated_at=CURRENT_TIMESTAMP""",
            (organization_id, user_id, name, organization_type, approval_mode),
        )
        self.con.execute(
            """INSERT OR IGNORE INTO organization_members(
            id,organization_id,user_id,name,role,active)
            SELECT ?,?,?,name,'owner',1 FROM app_users WHERE id=?""",
            (_id("org_member"), organization_id, user_id, user_id),
        )
        return self.get(organization_id, user_id)

    def add_member(self, organization_id: str, user_id: str, data: dict[str, Any]) -> dict[str, Any]:
        self._owned(organization_id, user_id)
        name = _text(data.get("name"), 120)
        phone = "".join(c for c in str(data.get("phone") or "") if c.isdigit())[:20]
        role = _text(data.get("role"), 40) or "requester"
        if not name or len(phone) < 8 or role not in self.MEMBER_ROLES - {"owner"}:
            raise DomainError("invalid_organization_member", 400)
        self.con.execute(
            """INSERT INTO organization_members(
            id,organization_id,name,phone,role,active)
            VALUES(?,?,?,?,?,1)
            ON CONFLICT(organization_id,phone) DO UPDATE SET name=excluded.name,
            role=excluded.role,active=1,updated_at=CURRENT_TIMESTAMP""",
            (_id("org_member"), organization_id, name, phone, role),
        )
        return self.get(organization_id, user_id)

    def add_location(self, organization_id: str, user_id: str, data: dict[str, Any]) -> dict[str, Any]:
        self._owned(organization_id, user_id)
        name = _text(data.get("name"), 120)
        if not name:
            raise DomainError("organization_location_name_required", 400)
        self.con.execute(
            """INSERT INTO organization_locations(
            id,organization_id,name,gov,wilayah,address,active)
            VALUES(?,?,?,?,?,?,1)""",
            (
                _id("org_location"),
                organization_id,
                name,
                _text(data.get("gov"), 80),
                _text(data.get("wilayah"), 80),
                _text(data.get("address"), 300),
            ),
        )
        return self.get(organization_id, user_id)

    def get(self, organization_id: str, user_id: str) -> dict[str, Any]:
        item = _row(self._owned(organization_id, user_id))
        return {
            "id": item["id"],
            "name": item["name"],
            "organizationType": item["organization_type"],
            "status": item["status"],
            "approvalMode": item["approval_mode"],
            "members": [
                {
                    "id": row["id"],
                    "name": row["name"],
                    "role": row["role"],
                    "active": bool(row["active"]),
                }
                for row in self.con.execute(
                    """SELECT * FROM organization_members
                    WHERE organization_id=? ORDER BY created_at""",
                    (organization_id,),
                )
            ],
            "locations": [
                {
                    "id": row["id"],
                    "name": row["name"],
                    "gov": row["gov"],
                    "wilayah": row["wilayah"],
                    "address": row["address"],
                    "active": bool(row["active"]),
                }
                for row in self.con.execute(
                    """SELECT * FROM organization_locations
                    WHERE organization_id=? ORDER BY created_at""",
                    (organization_id,),
                )
            ],
        }

    def list_for_user(self, user_id: str) -> list[dict[str, Any]]:
        return [
            self.get(row["id"], user_id)
            for row in self.con.execute(
                """SELECT id FROM customer_organizations
                WHERE owner_user_id=? AND status!='deleted' ORDER BY updated_at DESC""",
                (user_id,),
            )
        ]


class MaintenanceContractService:
    def __init__(self, con, *, now: datetime | None = None):
        self.con = con
        self.now = (now or _now()).astimezone(UTC)

    @staticmethod
    def public(row) -> dict[str, Any]:
        item = _row(row)
        return {
            "id": item["id"],
            "ownerUserId": item["owner_user_id"],
            "organizationId": item["organization_id"],
            "providerId": item["provider_id"],
            "requestId": item["request_id"],
            "serviceAssetId": item["service_asset_id"],
            "serviceValue": item["service_value"],
            "title": item["title"],
            "frequencyDays": int(item["frequency_days"]),
            "amount": float(item["amount"]),
            "nextDueAt": item["next_due_at"],
            "autoRenew": bool(item["auto_renew"]),
            "status": item["status"],
            "lastCompletedAt": item["last_completed_at"],
        }

    def create(self, user_id: str, data: dict[str, Any]) -> dict[str, Any]:
        provider_id = _text(data.get("providerId"), 120)
        request_id = _text(data.get("requestId"), 120)
        request = None
        if request_id:
            request = self.con.execute(
                """SELECT * FROM customer_requests WHERE id=? AND user_id=?
                AND accepted_provider_id=?""",
                (request_id, user_id, provider_id),
            ).fetchone()
            if not request:
                raise DomainError("contract_request_not_eligible", 409)
        provider = self.con.execute(
            "SELECT id,active,verified FROM providers WHERE id=?", (provider_id,)
        ).fetchone()
        if not provider or not provider["active"] or not provider["verified"]:
            raise DomainError("provider_not_available", 409)
        frequency_days = _bounded_int(data.get("frequencyDays", 30), 1, 730)
        next_due = _parse(_text(data.get("nextDueAt"), 60))
        if not next_due or next_due <= self.now:
            next_due = self.now + timedelta(days=frequency_days)
        title = _text(data.get("title"), 160)
        service_value = _text(
            data.get("serviceValue") or (request["service_value"] if request else ""),
            180,
        )
        if not title or not service_value:
            raise DomainError("contract_details_required", 400)
        contract_id = _id("contract")
        self.con.execute(
            """INSERT INTO maintenance_contracts(
            id,owner_user_id,organization_id,provider_id,request_id,service_asset_id,
            service_value,title,frequency_days,amount,next_due_at,auto_renew,status)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'active')""",
            (
                contract_id,
                user_id,
                _text(data.get("organizationId"), 120),
                provider_id,
                request_id,
                _text(data.get("serviceAssetId"), 120),
                service_value,
                title,
                frequency_days,
                _bounded_amount(data.get("amount", 0)),
                _iso(next_due),
                int(bool(data.get("autoRenew"))),
            ),
        )
        return self.get(contract_id, user_id=user_id)

    def get(self, contract_id: str, *, user_id: str = "", provider_id: str = "") -> dict[str, Any]:
        row = self.con.execute(
            """SELECT * FROM maintenance_contracts WHERE id=?
            AND ((?!='' AND owner_user_id=?) OR (?!='' AND provider_id=?))""",
            (contract_id, user_id, user_id, provider_id, provider_id),
        ).fetchone()
        if not row:
            raise DomainError("contract_access_denied", 403)
        return self.public(row)

    def update_status(
        self, contract_id: str, actor_kind: str, actor_id: str, status: str
    ) -> dict[str, Any]:
        if status not in {"active", "paused", "cancelled", "completed_cycle"}:
            raise DomainError("invalid_contract_status", 400)
        row = self.con.execute(
            "SELECT * FROM maintenance_contracts WHERE id=?", (contract_id,)
        ).fetchone()
        if not row:
            raise DomainError("contract_not_found", 404)
        if not (
            actor_kind == "admin"
            or (actor_kind == "user" and row["owner_user_id"] == actor_id)
            or (actor_kind == "provider" and row["provider_id"] == actor_id)
        ):
            raise DomainError("contract_access_denied", 403)
        if status == "completed_cycle":
            next_due = self.now + timedelta(days=int(row["frequency_days"]))
            self.con.execute(
                """UPDATE maintenance_contracts SET status='active',last_completed_at=?,
                next_due_at=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (_iso(self.now), _iso(next_due), contract_id),
            )
        else:
            self.con.execute(
                """UPDATE maintenance_contracts SET status=?,updated_at=CURRENT_TIMESTAMP
                WHERE id=?""",
                (status, contract_id),
            )
        return self.public(
            self.con.execute(
                "SELECT * FROM maintenance_contracts WHERE id=?", (contract_id,)
            ).fetchone()
        )

    def list_for(self, actor_kind: str, actor_id: str) -> list[dict[str, Any]]:
        column = "owner_user_id" if actor_kind == "user" else "provider_id"
        if actor_kind not in {"user", "provider"}:
            raise DomainError("contract_actor_not_supported", 400)
        return [
            self.public(row)
            for row in self.con.execute(
                f"SELECT * FROM maintenance_contracts WHERE {column}=? ORDER BY next_due_at",  # nosec B608
                (actor_id,),
            )
        ]


class ProviderCRMService:
    STAGES = {"lead", "active", "follow_up", "warranty", "completed", "archived"}

    def __init__(self, con):
        self.con = con

    def sync(self, provider_id: str) -> None:
        rows = self.con.execute(
            """SELECT r.id,r.user_id,r.customer_name,r.status,r.offers,r.updated_at
            FROM customer_requests r WHERE r.accepted_provider_id=?""",
            (provider_id,),
        )
        for row in rows:
            offers = _load(row["offers"], [])
            accepted = next((offer for offer in offers if offer.get("status") == "accepted"), {})
            warranty_days = int(accepted.get("warrantyDays") or 0)
            warranty_until = ""
            if warranty_days and row["status"] in {"completed", "closed", "archived"}:
                updated = _parse(row["updated_at"]) or _now()
                warranty_until = _iso(updated + timedelta(days=warranty_days))
            stage = "completed" if row["status"] in {"completed", "closed", "archived"} else "active"
            self.con.execute(
                """INSERT INTO provider_crm_records(
                id,provider_id,user_id,request_id,display_name,stage,warranty_until)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(provider_id,request_id) DO UPDATE SET
                display_name=excluded.display_name,
                stage=CASE WHEN provider_crm_records.stage IN ('follow_up','archived')
                THEN provider_crm_records.stage ELSE excluded.stage END,
                warranty_until=CASE WHEN excluded.warranty_until!=''
                THEN excluded.warranty_until ELSE provider_crm_records.warranty_until END,
                updated_at=CURRENT_TIMESTAMP""",
                (
                    _id("crm"),
                    provider_id,
                    row["user_id"],
                    row["id"],
                    row["customer_name"],
                    stage,
                    warranty_until,
                ),
            )

    def list_for_provider(self, provider_id: str) -> list[dict[str, Any]]:
        self.sync(provider_id)
        return [
            {
                "id": row["id"],
                "requestId": row["request_id"],
                "displayName": row["display_name"],
                "stage": row["stage"],
                "nextActionAt": row["next_action_at"],
                "note": row["note"],
                "invoiceStatus": row["invoice_status"],
                "warrantyUntil": row["warranty_until"],
                "updatedAt": row["updated_at"],
            }
            for row in self.con.execute(
                """SELECT * FROM provider_crm_records WHERE provider_id=?
                ORDER BY updated_at DESC LIMIT 300""",
                (provider_id,),
            )
        ]

    def update(self, provider_id: str, record_id: str, data: dict[str, Any]) -> dict[str, Any]:
        stage = _text(data.get("stage"), 40)
        if stage and stage not in self.STAGES:
            raise DomainError("invalid_crm_stage", 400)
        result = self.con.execute(
            """UPDATE provider_crm_records SET stage=COALESCE(NULLIF(?,''),stage),
            next_action_at=?,note=?,invoice_status=?,updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND provider_id=?""",
            (
                stage,
                _text(data.get("nextActionAt"), 60),
                _text(data.get("note"), 800),
                _text(data.get("invoiceStatus"), 40) or "not_issued",
                record_id,
                provider_id,
            ),
        )
        if result.rowcount != 1:
            raise DomainError("crm_record_not_found", 404)
        return next(
            item for item in self.list_for_provider(provider_id) if item["id"] == record_id
        )


class ReferralService:
    def __init__(self, con):
        self.con = con

    def create_code(self, actor_kind: str, actor_id: str) -> dict[str, Any]:
        if actor_kind not in {"user", "provider"}:
            raise DomainError("invalid_referrer", 400)
        existing = self.con.execute(
            """SELECT * FROM referrals WHERE referrer_kind=? AND referrer_id=?
            AND referred_id='' ORDER BY created_at DESC LIMIT 1""",
            (actor_kind, actor_id),
        ).fetchone()
        if existing:
            return self.public(existing)
        code = secrets.token_hex(4).upper()
        self.con.execute(
            """INSERT INTO referrals(
            id,code,referrer_kind,referrer_id,status,risk_status,reward_status)
            VALUES(?,?,?,?, 'created','clear','not_eligible')""",
            (_id("ref"), code, actor_kind, actor_id),
        )
        return self.public(
            self.con.execute("SELECT * FROM referrals WHERE code=?", (code,)).fetchone()
        )

    def claim(self, code: str, referred_kind: str, referred_id: str) -> dict[str, Any]:
        if referred_kind not in {"user", "provider"}:
            raise DomainError("invalid_referred_account", 400)
        source = self.con.execute(
            "SELECT * FROM referrals WHERE code=?", (_text(code, 20).upper(),)
        ).fetchone()
        if not source:
            raise DomainError("referral_code_not_found", 404)
        if source["referrer_kind"] == referred_kind and source["referrer_id"] == referred_id:
            raise DomainError("self_referral_not_allowed", 409)
        try:
            self.con.execute(
                """INSERT INTO referrals(
                id,code,referrer_kind,referrer_id,referred_kind,referred_id,
                status,risk_status,reward_status)
                VALUES(?,?,?,?,?,?,'claimed','pending_review','not_eligible')""",
                (
                    _id("ref"),
                    f"{source['code']}-{hashlib.sha256(referred_id.encode()).hexdigest()[:8]}",
                    source["referrer_kind"],
                    source["referrer_id"],
                    referred_kind,
                    referred_id,
                ),
            )
        except Exception as exc:
            if "UNIQUE" in str(exc).upper():
                raise DomainError("referral_already_claimed", 409) from exc
            raise
        row = self.con.execute(
            """SELECT * FROM referrals WHERE referred_kind=? AND referred_id=?""",
            (referred_kind, referred_id),
        ).fetchone()
        return self.public(row)

    @staticmethod
    def public(row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "code": row["code"].split("-", 1)[0],
            "status": row["status"],
            "riskStatus": row["risk_status"],
            "rewardStatus": row["reward_status"],
            "qualifiedAt": row["qualified_at"],
        }

    def list_for(self, actor_kind: str, actor_id: str) -> list[dict[str, Any]]:
        return [
            self.public(row)
            for row in self.con.execute(
                """SELECT * FROM referrals WHERE referrer_kind=? AND referrer_id=?
                ORDER BY created_at DESC""",
                (actor_kind, actor_id),
            )
        ]

    def qualify(self, referred_kind: str, referred_id: str) -> list[dict[str, Any]]:
        """Mark a reviewed referral eligible; reward delivery stays manual."""
        if referred_kind == "user":
            qualifying = int(
                self.con.execute(
                    """SELECT COUNT(*) n FROM customer_requests
                    WHERE user_id=? AND status IN ('completed','closed','archived')""",
                    (referred_id,),
                ).fetchone()["n"]
            ) > 0
        elif referred_kind == "provider":
            qualifying = int(
                self.con.execute(
                    """SELECT COUNT(*) n FROM customer_requests
                    WHERE accepted_provider_id=? AND status IN ('completed','closed','archived')""",
                    (referred_id,),
                ).fetchone()["n"]
            ) > 0
        else:
            raise DomainError("invalid_referred_account", 400)
        if qualifying:
            self.con.execute(
                """UPDATE referrals SET status='qualified',risk_status='pending_review',
                reward_status='eligible_for_review',qualified_at=CURRENT_TIMESTAMP,
                updated_at=CURRENT_TIMESTAMP WHERE referred_kind=? AND referred_id=?
                AND status='claimed'""",
                (referred_kind, referred_id),
            )
        row = self.con.execute(
            """SELECT referrer_kind,referrer_id FROM referrals
            WHERE referred_kind=? AND referred_id=? LIMIT 1""",
            (referred_kind, referred_id),
        ).fetchone()
        return self.list_for(row["referrer_kind"], row["referrer_id"]) if row else []


class TrainingAchievementService:
    def __init__(self, con):
        self.con = con

    def list_modules(self, provider_id: str, provider_type: str) -> list[dict[str, Any]]:
        audiences = ("all_providers", "company" if provider_type == "company" else "provider")
        rows = self.con.execute(
            """SELECT m.*,p.score,p.status progress_status,p.completed_at
            FROM training_modules m LEFT JOIN training_progress p
            ON p.module_id=m.id AND p.provider_id=?
            WHERE m.active=1 AND m.audience IN (?,?) ORDER BY m.sort_order,m.id""",
            (provider_id, *audiences),
        )
        return [
            {
                "id": row["id"],
                "titleAr": row["title_ar"],
                "titleEn": row["title_en"],
                "contentAr": row["content_ar"],
                "contentEn": row["content_en"],
                "passScore": int(row["pass_score"]),
                "score": int(row["score"] or 0),
                "status": row["progress_status"] or "not_started",
                "completedAt": row["completed_at"] or "",
            }
            for row in rows
        ]

    def complete(self, provider_id: str, module_id: str, score: int) -> dict[str, Any]:
        module = self.con.execute(
            "SELECT * FROM training_modules WHERE id=? AND active=1", (module_id,)
        ).fetchone()
        if not module:
            raise DomainError("training_module_not_found", 404)
        score = _bounded_int(score, 0, 100)
        status = "passed" if score >= int(module["pass_score"]) else "retry"
        self.con.execute(
            """INSERT INTO training_progress(
            module_id,provider_id,score,status,completed_at,updated_at)
            VALUES(?,?,?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(module_id,provider_id) DO UPDATE SET
            score=MAX(training_progress.score,excluded.score),
            status=CASE WHEN training_progress.status='passed' THEN 'passed'
            ELSE excluded.status END,
            completed_at=CASE WHEN excluded.status='passed' THEN excluded.completed_at
            ELSE training_progress.completed_at END,updated_at=CURRENT_TIMESTAMP""",
            (module_id, provider_id, score, status, _iso() if status == "passed" else ""),
        )
        self.recompute_achievements(provider_id)
        provider = self.con.execute(
            "SELECT provider_type FROM providers WHERE id=?", (provider_id,)
        ).fetchone()
        return next(
            item
            for item in self.list_modules(provider_id, provider["provider_type"])
            if item["id"] == module_id
        )

    def recompute_achievements(self, provider_id: str) -> list[dict[str, Any]]:
        provider = self.con.execute(
            """SELECT completed_jobs,reviews,rating,response_score FROM providers
            WHERE id=?""",
            (provider_id,),
        ).fetchone()
        if not provider:
            raise DomainError("provider_not_found", 404)
        passed = int(
            self.con.execute(
                """SELECT COUNT(*) n FROM training_progress
                WHERE provider_id=? AND status='passed'""",
                (provider_id,),
            ).fetchone()["n"]
        )
        rules = {
            "trained_provider": (passed >= 2, {"passedModules": passed}),
            "first_five_jobs": (int(provider["completed_jobs"] or 0) >= 5, {"completedJobs": int(provider["completed_jobs"] or 0)}),
            "responsive_provider": (int(provider["response_score"] or 0) >= 90, {"responseScore": int(provider["response_score"] or 0)}),
            "customer_favorite": (
                int(provider["reviews"] or 0) >= 10 and float(provider["rating"] or 0) >= 4.5,
                {"reviews": int(provider["reviews"] or 0), "rating": float(provider["rating"] or 0)},
            ),
        }
        for code, (earned, evidence) in rules.items():
            if earned:
                self.con.execute(
                    """INSERT OR IGNORE INTO provider_achievements(
                    id,provider_id,code,evidence) VALUES(?,?,?,?)""",
                    (_id("achievement"), provider_id, code, _dump(evidence)),
                )
        return self.achievements(provider_id)

    def achievements(self, provider_id: str) -> list[dict[str, Any]]:
        return [
            {
                "id": row["id"],
                "code": row["code"],
                "evidence": _load(row["evidence"], {}),
                "earnedAt": row["earned_at"],
                "earned": True,
            }
            for row in self.con.execute(
                """SELECT * FROM provider_achievements WHERE provider_id=?
                ORDER BY earned_at DESC""",
                (provider_id,),
            )
        ]


class DemandAlertService:
    def __init__(self, con):
        self.con = con

    def save(self, actor_kind: str, actor_id: str, data: dict[str, Any]) -> dict[str, Any]:
        if actor_kind not in {"user", "provider"}:
            raise DomainError("invalid_demand_alert_actor", 400)
        service_value = _text(data.get("serviceValue"), 180)
        if "|" not in service_value:
            raise DomainError("service_required", 400)
        alert_id = _id("alert")
        self.con.execute(
            """INSERT INTO demand_alerts(
            id,target_kind,target_id,service_value,gov,wilayah,status)
            VALUES(?,?,?,?,?,?,'active')
            ON CONFLICT(target_kind,target_id,service_value,gov,wilayah)
            DO UPDATE SET status='active',updated_at=CURRENT_TIMESTAMP""",
            (
                alert_id,
                actor_kind,
                actor_id,
                service_value,
                _text(data.get("gov"), 80),
                _text(data.get("wilayah"), 80),
            ),
        )
        return self.list_for(actor_kind, actor_id)[0]

    def cancel(self, actor_kind: str, actor_id: str, alert_id: str) -> None:
        result = self.con.execute(
            """UPDATE demand_alerts SET status='cancelled',updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND target_kind=? AND target_id=?""",
            (alert_id, actor_kind, actor_id),
        )
        if result.rowcount != 1:
            raise DomainError("demand_alert_not_found", 404)

    def list_for(self, actor_kind: str, actor_id: str) -> list[dict[str, Any]]:
        return [
            {
                "id": row["id"],
                "serviceValue": row["service_value"],
                "gov": row["gov"],
                "wilayah": row["wilayah"],
                "status": row["status"],
                "lastNotifiedAt": row["last_notified_at"],
            }
            for row in self.con.execute(
                """SELECT * FROM demand_alerts WHERE target_kind=? AND target_id=?
                ORDER BY updated_at DESC""",
                (actor_kind, actor_id),
            )
        ]

    def aggregate_gaps(self, *, minimum_count: int = 3) -> list[dict[str, Any]]:
        return [
            {
                "serviceValue": row["service_value"],
                "gov": row["gov"],
                "wilayah": row["wilayah"],
                "requests": int(row["requests"]),
                "matched": int(row["matched"]),
                "gap": int(row["requests"] - row["matched"]),
            }
            for row in self.con.execute(
                """SELECT service_value,gov,wilayah,COUNT(*) requests,
                SUM(CASE WHEN accepted_provider_id!='' THEN 1 ELSE 0 END) matched
                FROM customer_requests WHERE status!='deleted'
                GROUP BY service_value,gov,wilayah HAVING COUNT(*)>=?
                ORDER BY (COUNT(*)-SUM(CASE WHEN accepted_provider_id!='' THEN 1 ELSE 0 END)) DESC
                LIMIT 100""",
                (max(3, minimum_count),),
            )
        ]


class FeatureFlagService:
    def __init__(self, con):
        self.con = con

    @staticmethod
    def public(row) -> dict[str, Any]:
        return {
            "key": row["key"],
            "enabled": bool(row["enabled"]),
            "rolloutPercentage": int(row["rollout_percentage"]),
            "audiences": _load(row["audiences"], []),
            "config": _load(row["config"], {}),
            "updatedAt": row["updated_at"],
        }

    def list_admin(self) -> list[dict[str, Any]]:
        return [self.public(row) for row in self.con.execute("SELECT * FROM platform_feature_flags ORDER BY key")]

    def is_enabled(self, key: str, actor_kind: str, actor_id: str) -> bool:
        row = self.con.execute(
            "SELECT * FROM platform_feature_flags WHERE key=?", (key,)
        ).fetchone()
        if not row or not row["enabled"]:
            return False
        audiences = _load(row["audiences"], [])
        if audiences and actor_kind not in audiences and "all" not in audiences:
            return False
        rollout = int(row["rollout_percentage"])
        if rollout >= 100:
            return True
        bucket = int(hashlib.sha256(f"{key}:{actor_id}".encode()).hexdigest()[:8], 16) % 100
        return bucket < rollout

    def update(self, key: str, data: dict[str, Any], admin_id: str) -> dict[str, Any]:
        key = _text(key, 80)
        if not key:
            raise DomainError("feature_key_required", 400)
        audiences = data.get("audiences", [])
        if not isinstance(audiences, list):
            raise DomainError("invalid_feature_audiences", 400)
        allowed = {"user", "provider", "company", "organization", "admin", "all"}
        audiences = list(dict.fromkeys(_text(item, 40) for item in audiences if _text(item, 40)))
        if any(item not in allowed for item in audiences):
            raise DomainError("invalid_feature_audiences", 400)
        config = data.get("config", {})
        if not isinstance(config, dict):
            raise DomainError("invalid_feature_config", 400)
        self.con.execute(
            """INSERT INTO platform_feature_flags(
            key,enabled,rollout_percentage,audiences,config,updated_by,updated_at)
            VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET enabled=excluded.enabled,
            rollout_percentage=excluded.rollout_percentage,
            audiences=excluded.audiences,config=excluded.config,
            updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP""",
            (
                key,
                int(bool(data.get("enabled"))),
                _bounded_int(data.get("rolloutPercentage", 0), 0, 100),
                _dump(audiences),
                _dump(config),
                admin_id,
            ),
        )
        return self.public(
            self.con.execute("SELECT * FROM platform_feature_flags WHERE key=?", (key,)).fetchone()
        )

    def public_for(self, actor_kind: str, actor_id: str) -> dict[str, bool]:
        return {
            row["key"]: self.is_enabled(row["key"], actor_kind, actor_id)
            for row in self.con.execute("SELECT key FROM platform_feature_flags")
        }


class RiskReviewService:
    def __init__(self, con):
        self.con = con

    def record(
        self,
        subject_kind: str,
        subject_id: str,
        signal_type: str,
        signals: list[str],
        score: int,
    ) -> dict[str, Any]:
        if subject_kind not in {"user", "provider", "organization", "request", "payment"}:
            raise DomainError("invalid_risk_subject", 400)
        score = _bounded_int(score, 0, 100)
        review_id = _id("risk")
        self.con.execute(
            """INSERT INTO risk_reviews(
            id,subject_kind,subject_id,signal_type,score,signals,status)
            VALUES(?,?,?,?,?,?,'pending_review')""",
            (
                review_id,
                subject_kind,
                _text(subject_id, 160),
                _text(signal_type, 80),
                score,
                _dump([_text(item, 120) for item in signals[:20]]),
            ),
        )
        return self.get(review_id)

    def get(self, review_id: str) -> dict[str, Any]:
        row = self.con.execute("SELECT * FROM risk_reviews WHERE id=?", (review_id,)).fetchone()
        if not row:
            raise DomainError("risk_review_not_found", 404)
        return {
            "id": row["id"],
            "subjectKind": row["subject_kind"],
            "subjectId": row["subject_id"],
            "signalType": row["signal_type"],
            "score": int(row["score"]),
            "signals": _load(row["signals"], []),
            "status": row["status"],
            "decision": row["decision"],
            "note": row["note"],
            "createdAt": row["created_at"],
        }

    def list_queue(self) -> list[dict[str, Any]]:
        return [
            self.get(row["id"])
            for row in self.con.execute(
                """SELECT id FROM risk_reviews ORDER BY
                CASE status WHEN 'pending_review' THEN 0 ELSE 1 END,score DESC,created_at DESC"""
            )
        ]

    def resolve(self, review_id: str, reviewer_id: str, decision: str, note: str) -> dict[str, Any]:
        if decision not in {"clear", "monitor", "restrict", "escalate"}:
            raise DomainError("invalid_risk_decision", 400)
        result = self.con.execute(
            """UPDATE risk_reviews SET status='reviewed',reviewer_id=?,decision=?,
            note=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (reviewer_id, decision, _text(note, 600), review_id),
        )
        if result.rowcount != 1:
            raise DomainError("risk_review_not_found", 404)
        return self.get(review_id)


class EnterpriseAPIService:
    ALLOWED_SCOPES = {"organization:read", "locations:read", "requests:read", "reports:read"}

    def __init__(self, con, *, now: datetime | None = None):
        self.con = con
        self.now = (now or _now()).astimezone(UTC)

    def create_client(
        self, organization_id: str, name: str, scopes: list[str], rate_limit: int
    ) -> dict[str, Any]:
        organization = self.con.execute(
            "SELECT id FROM customer_organizations WHERE id=? AND status='active'",
            (organization_id,),
        ).fetchone()
        if not organization:
            raise DomainError("organization_not_found", 404)
        normalized = list(dict.fromkeys(_text(scope, 80) for scope in scopes))
        if not normalized or any(scope not in self.ALLOWED_SCOPES for scope in normalized):
            raise DomainError("invalid_enterprise_scopes", 400)
        raw_key = "khd_" + secrets.token_urlsafe(36)
        client_id = _id("api_client")
        self.con.execute(
            """INSERT INTO enterprise_api_clients(
            id,organization_id,name,key_prefix,key_hash,scopes,rate_limit,active)
            VALUES(?,?,?,?,?,?,?,1)""",
            (
                client_id,
                organization_id,
                _text(name, 120),
                raw_key[:12],
                hashlib.sha256(raw_key.encode()).hexdigest(),
                _dump(normalized),
                _bounded_int(rate_limit, 1, 600),
            ),
        )
        return {**self.public(client_id), "apiKey": raw_key}

    def public(self, client_id: str) -> dict[str, Any]:
        row = self.con.execute(
            "SELECT * FROM enterprise_api_clients WHERE id=?", (client_id,)
        ).fetchone()
        if not row:
            raise DomainError("enterprise_client_not_found", 404)
        return {
            "id": row["id"],
            "organizationId": row["organization_id"],
            "name": row["name"],
            "keyPrefix": row["key_prefix"],
            "scopes": _load(row["scopes"], []),
            "rateLimit": int(row["rate_limit"]),
            "active": bool(row["active"]),
            "expiresAt": row["expires_at"],
            "lastUsedAt": row["last_used_at"],
        }

    def authenticate(self, raw_key: str, required_scope: str) -> dict[str, Any]:
        row = self.con.execute(
            """SELECT * FROM enterprise_api_clients WHERE key_hash=? AND active=1""",
            (hashlib.sha256(str(raw_key or "").encode()).hexdigest(),),
        ).fetchone()
        if not row:
            raise DomainError("enterprise_api_key_invalid", 401)
        expires = _parse(row["expires_at"])
        if expires and expires <= self.now:
            raise DomainError("enterprise_api_key_expired", 401)
        scopes = _load(row["scopes"], [])
        if required_scope not in scopes:
            raise DomainError("enterprise_scope_denied", 403)
        recent = int(
            self.con.execute(
                """SELECT COUNT(*) n FROM enterprise_api_audit
                WHERE client_id=? AND created_at>=datetime('now','-1 minute')""",
                (row["id"],),
            ).fetchone()["n"]
        )
        if recent >= int(row["rate_limit"]):
            raise DomainError("enterprise_rate_limit", 429)
        self.con.execute(
            """UPDATE enterprise_api_clients SET last_used_at=CURRENT_TIMESTAMP
            WHERE id=?""",
            (row["id"],),
        )
        self.con.execute(
            """INSERT INTO enterprise_api_audit(id,client_id,scope)
            VALUES(?,?,?)""",
            (_id("api_audit"), row["id"], required_scope),
        )
        return self.public(row["id"])

    def revoke(self, client_id: str) -> None:
        result = self.con.execute(
            "UPDATE enterprise_api_clients SET active=0,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (client_id,),
        )
        if result.rowcount != 1:
            raise DomainError("enterprise_client_not_found", 404)


class FinancialScenarioService:
    def __init__(self, con):
        self.con = con

    def baseline(self) -> dict[str, float]:
        revenue = float(
            self.con.execute(
                """SELECT COALESCE(SUM(amount),0) n FROM payments
                WHERE status='paid' AND kind IN ('revenue','subscription','promotion')"""
            ).fetchone()["n"]
            or 0
        )
        active_providers = int(
            self.con.execute(
                "SELECT COUNT(*) n FROM providers WHERE active=1"
            ).fetchone()["n"]
        )
        active_subscriptions = int(
            self.con.execute(
                """SELECT COUNT(*) n FROM subscriptions
                WHERE status IN ('foundation','active','expiring','grace')"""
            ).fetchone()["n"]
        )
        return {
            "confirmedRevenue": round(revenue, 3),
            "activeProviders": active_providers,
            "activeSubscriptions": active_subscriptions,
        }

    def calculate(self, assumptions: dict[str, Any]) -> dict[str, Any]:
        baseline = self.baseline()
        provider_count = _bounded_int(
            assumptions.get("providerCount", baseline["activeProviders"]), 0, 10_000_000
        )
        paid_ratio = _bounded_int(assumptions.get("paidRatio", 0), 0, 100)
        average_monthly_plan = _bounded_amount(assumptions.get("averageMonthlyPlan", 0))
        promotion_revenue = _bounded_amount(assumptions.get("promotionRevenue", 0))
        monthly = round(provider_count * paid_ratio / 100 * average_monthly_plan + promotion_revenue, 3)
        return {
            "baseline": baseline,
            "assumptions": {
                "providerCount": provider_count,
                "paidRatio": paid_ratio,
                "averageMonthlyPlan": average_monthly_plan,
                "promotionRevenue": promotion_revenue,
            },
            "results": {"projectedMonthlyRevenue": monthly, "projectedAnnualRevenue": round(monthly * 12, 3)},
            "disclaimer": "scenario_not_accounting_record",
        }

    def save(self, name: str, assumptions: dict[str, Any], admin_id: str) -> dict[str, Any]:
        result = self.calculate(assumptions)
        scenario_id = _id("scenario")
        self.con.execute(
            """INSERT INTO financial_scenarios(
            id,name,assumptions,results,created_by) VALUES(?,?,?,?,?)""",
            (
                scenario_id,
                _text(name, 160) or "Scenario",
                _dump(result["assumptions"]),
                _dump(result["results"]),
                admin_id,
            ),
        )
        return {"id": scenario_id, "name": _text(name, 160), **result}

    def list_admin(self) -> list[dict[str, Any]]:
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "assumptions": _load(row["assumptions"], {}),
                "results": _load(row["results"], {}),
                "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
            }
            for row in self.con.execute(
                "SELECT * FROM financial_scenarios ORDER BY updated_at DESC LIMIT 100"
            )
        ]


def adapter_snapshot(con) -> list[dict[str, Any]]:
    return [
        {
            "key": row["key"],
            "kind": row["kind"],
            "enabled": bool(row["enabled"]),
            "mode": row["mode"],
            "legalStatus": row["legal_status"],
            "configured": bool(_load(row["config"], {})),
        }
        for row in con.execute("SELECT * FROM integration_adapters ORDER BY key")
    ]


def platform_snapshot(con, session: dict[str, Any] | None) -> dict[str, Any]:
    session = session or {}
    kind = session.get("kind", "guest")
    actor_id = session.get("userId") or session.get("providerId") or session.get("id") or "guest"
    flags = FeatureFlagService(con).public_for(kind, actor_id)
    result: dict[str, Any] = {"featureFlags": flags}
    if kind == "user":
        result.update(
            {
                "organizations": OrganizationService(con).list_for_user(actor_id),
                "maintenanceContracts": MaintenanceContractService(con).list_for("user", actor_id),
                "referrals": ReferralService(con).list_for("user", actor_id),
                "demandAlerts": DemandAlertService(con).list_for("user", actor_id),
            }
        )
    elif kind == "provider":
        provider = con.execute(
            "SELECT provider_type FROM providers WHERE id=?", (actor_id,)
        ).fetchone()
        training = TrainingAchievementService(con)
        result.update(
            {
                "legalProfile": ProviderLegalProfileService(con).get(actor_id, private=True),
                "maintenanceContracts": MaintenanceContractService(con).list_for("provider", actor_id),
                "crm": ProviderCRMService(con).list_for_provider(actor_id),
                "referrals": ReferralService(con).list_for("provider", actor_id),
                "training": training.list_modules(actor_id, provider["provider_type"] if provider else "individual"),
                "achievements": training.recompute_achievements(actor_id) if provider else [],
                "demandAlerts": DemandAlertService(con).list_for("provider", actor_id),
            }
        )
    elif kind == "admin":
        result.update(
            {
                "featureFlagDetails": FeatureFlagService(con).list_admin(),
                "legalReviewQueue": ProviderLegalProfileService(con).review_queue(),
                "riskReviewQueue": RiskReviewService(con).list_queue(),
                "demandGapReport": DemandAlertService(con).aggregate_gaps(),
                "financialScenarios": FinancialScenarioService(con).list_admin(),
                "integrationAdapters": adapter_snapshot(con),
                "enterpriseClients": [
                    EnterpriseAPIService(con).public(row["id"])
                    for row in con.execute(
                        "SELECT id FROM enterprise_api_clients ORDER BY created_at DESC"
                    )
                ],
            }
        )
    return result
