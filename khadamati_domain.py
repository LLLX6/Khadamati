from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import hmac
import json
import math
import os
import secrets
import sqlite3
from typing import Any, Callable, Iterable


SUPPORT_EMAIL = os.environ.get("KHADAMATI_SUPPORT_EMAIL", "om.khadamati@gmail.com").strip()
POLICY_VERSION = "2026-08-02.1"
MIGRATION_KEY = "KHADAMATI_SUBSCRIPTION_MIGRATION_V2"
RANKING_VERSION = "khadamati-ranking-v3"
OMR = "OMR"
OMAN_TZ = timezone(timedelta(hours=4), "Asia/Muscat")


# Keep service-family matching explicit and reviewable.  A related service is
# considered only inside the same requested category, so a coincidentally
# reused service id cannot dispatch work across unrelated categories.
RELATED_SERVICE_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"ac", "ac_repair", "ac_clean", "ac_install", "ac_gas", "emergency_ac"}),
    frozenset({"plumber", "water_leak"}),
    frozenset({"electrician"}),
    frozenset({"furniture_move", "items_delivery", "loading", "small_truck", "large_truck"}),
    frozenset({"home_clean", "apt_clean", "deep_clean", "post_build", "rental_clean"}),
    # One transitive technology family keeps matching and availability counts
    # identical for tech_support, networks, pc, and printer.
    frozenset({"pc", "tech_support", "printer", "networks"}),
    frozenset({"mechanic", "inspection", "car_electric", "battery", "tires", "tow", "ac_car"}),
    frozenset({"photo", "commercial_photo", "product_photo", "property_photo"}),
    frozenset({"building", "renovation", "tiles", "gypsum", "insulation"}),
)


PLAN_DEFINITIONS: tuple[dict[str, Any], ...] = (
    # Individual plans
    {"id": "individual_free_3m", "account_scope": "individual", "ar": "التجربة المجانية", "en": "Free trial", "price": 0, "currency": OMR, "duration_days": 90, "max_services": 2, "max_categories": 1, "max_images": 2, "max_wilayats": 1, "max_governorates": 1, "monthly_response_limit": 0, "lead_delay_seconds": 120, "max_team_members": 1, "max_branches": 1, "shared_inbox": 0, "advanced_reports": 0, "community_package_quota": 1, "community_package_days": 30, "badge_ar": "3 أشهر مجانًا", "badge_en": "3 months free", "foundation_once": 1, "verified_required": 1},
    {"id": "individual_silver_6m", "account_scope": "individual", "ar": "الفضية", "en": "Silver", "price": 10, "currency": OMR, "duration_days": 183, "max_services": 2, "max_categories": 1, "max_images": 4, "max_wilayats": 1, "max_governorates": 1, "monthly_response_limit": 0, "lead_delay_seconds": 60, "max_team_members": 1, "max_branches": 1, "shared_inbox": 0, "advanced_reports": 0, "community_package_quota": 1, "community_package_days": 30, "badge_ar": "", "badge_en": "", "foundation_once": 0, "verified_required": 0},
    {"id": "individual_gold_6m", "account_scope": "individual", "ar": "الذهبية", "en": "Gold", "price": 15, "currency": OMR, "duration_days": 183, "max_services": 3, "max_categories": 2, "max_images": 8, "max_wilayats": 1, "max_governorates": 1, "monthly_response_limit": 0, "lead_delay_seconds": 30, "max_team_members": 1, "max_branches": 1, "shared_inbox": 0, "advanced_reports": 0, "community_package_quota": 2, "community_package_days": 60, "badge_ar": "الأكثر اختيارًا", "badge_en": "Most selected", "foundation_once": 0, "verified_required": 0},
    {"id": "individual_elite_6m", "account_scope": "individual", "ar": "النخبة", "en": "Elite", "price": 25, "currency": OMR, "duration_days": 183, "max_services": 6, "max_categories": 3, "max_images": 15, "max_wilayats": 2, "max_governorates": 2, "monthly_response_limit": 0, "lead_delay_seconds": 0, "max_team_members": 1, "max_branches": 1, "shared_inbox": 0, "advanced_reports": 1, "community_package_quota": 4, "community_package_days": 90, "badge_ar": "وصول فوري", "badge_en": "Instant access", "foundation_once": 0, "verified_required": 0},
    # Company plans. max_wilayats=0 means any wilayah inside the allowed governorates.
    {"id": "company_free_3m", "account_scope": "company", "ar": "تجربة الشركات", "en": "Company trial", "price": 0, "currency": OMR, "duration_days": 90, "max_services": 3, "max_categories": 3, "max_images": 2, "max_wilayats": 0, "max_governorates": 1, "monthly_response_limit": 0, "lead_delay_seconds": 60, "max_team_members": 3, "max_branches": 1, "shared_inbox": 1, "advanced_reports": 0, "community_package_quota": 2, "community_package_days": 30, "badge_ar": "3 أشهر مجانًا", "badge_en": "3 months free", "foundation_once": 1, "verified_required": 1},
    {"id": "company_silver_6m", "account_scope": "company", "ar": "فضية الشركات", "en": "Company Silver", "price": 30, "currency": OMR, "duration_days": 183, "max_services": 3, "max_categories": 3, "max_images": 4, "max_wilayats": 0, "max_governorates": 1, "monthly_response_limit": 0, "lead_delay_seconds": 0, "max_team_members": 5, "max_branches": 1, "shared_inbox": 1, "advanced_reports": 0, "community_package_quota": 2, "community_package_days": 30, "badge_ar": "", "badge_en": "", "foundation_once": 0, "verified_required": 0},
    {"id": "company_gold_6m", "account_scope": "company", "ar": "ذهبية الشركات", "en": "Company Gold", "price": 50, "currency": OMR, "duration_days": 183, "max_services": 5, "max_categories": 5, "max_images": 8, "max_wilayats": 0, "max_governorates": 2, "monthly_response_limit": 0, "lead_delay_seconds": 0, "max_team_members": 12, "max_branches": 3, "shared_inbox": 1, "advanced_reports": 1, "community_package_quota": 4, "community_package_days": 60, "badge_ar": "الأكثر اختيارًا", "badge_en": "Most selected", "foundation_once": 0, "verified_required": 0},
    {"id": "company_elite_6m", "account_scope": "company", "ar": "نخبة الشركات", "en": "Company Elite", "price": 85, "currency": OMR, "duration_days": 183, "max_services": 10, "max_categories": 8, "max_images": 20, "max_wilayats": 0, "max_governorates": 4, "monthly_response_limit": 0, "lead_delay_seconds": 0, "max_team_members": 30, "max_branches": 6, "shared_inbox": 1, "advanced_reports": 1, "community_package_quota": 8, "community_package_days": 90, "badge_ar": "أوسع صلاحيات", "badge_en": "Maximum access", "foundation_once": 0, "verified_required": 0},
)

PLAN_IDS = tuple(plan["id"] for plan in PLAN_DEFINITIONS)
PLAN_ACCOUNT_LIMITS: dict[str, dict[str, dict[str, int]]] = {
    plan["id"]: {
        plan["account_scope"]: {
            "maxServices": plan["max_services"],
            "maxCategories": plan["max_categories"],
            "maxImages": plan["max_images"],
            "maxWilayats": plan["max_wilayats"],
            "maxTeamMembers": plan["max_team_members"],
            "maxBranches": plan["max_branches"],
        }
    }
    for plan in PLAN_DEFINITIONS
}
LEGACY_PLAN_MAP = {
    "intro": "individual_free_3m", "intro_90": "individual_free_3m",
    "basic_90": "individual_silver_6m", "individual_6m": "individual_silver_6m",
    "active_90": "individual_silver_6m", "individual_year": "individual_silver_6m",
    "featured_90": "individual_gold_6m", "local_visibility": "individual_gold_6m",
    "service_priority": "individual_gold_6m", "plus": "individual_gold_6m",
    "basic": "individual_silver_6m", "growth": "individual_elite_6m",
    "company_90": "company_free_3m", "company_year": "company_gold_6m",
    "company_growth": "company_elite_6m",
}
LEGACY_PLAN_IDS = tuple(dict.fromkeys((
    "foundation_12m", "basic_6m", "basic_12m", "professional_12m", "business_12m",
    *LEGACY_PLAN_MAP.keys(),
)))

SUBSCRIPTION_STATES = {
    "foundation",
    "pending_payment",
    "active",
    "expiring",
    "grace",
    "expired",
    "suspended",
    "cancelled",
    "refunded",
}


class DomainError(ValueError):
    def __init__(self, code: str, status: int = 400, detail: str = ""):
        super().__init__(code)
        self.code = code
        self.status = status
        self.detail = detail


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime | None = None) -> str:
    return (value or utcnow()).isoformat()


def parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def parse_marketplace_datetime(value: Any) -> datetime | None:
    """Parse a service appointment as an instant, treating naive input as Oman time."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=OMAN_TZ)
    return parsed.astimezone(UTC)


def as_money(value: Any) -> Decimal:
    try:
        amount = Decimal(str(value or 0)).quantize(
            Decimal("0.001"), rounding=ROUND_HALF_UP
        )
        if not amount.is_finite():
            raise ValueError("money must be finite")
        return amount
    except Exception as exc:
        raise DomainError("invalid_amount") from exc


def dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def load(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def public_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


def normalized_phone(value: Any) -> str:
    phone = "".join(ch for ch in str(value or "") if ch.isdigit())
    if phone.startswith("0"):
        phone = "968" + phone[1:]
    if len(phone) == 8:
        phone = "968" + phone
    return phone


def row_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


class CommercialRecordService:
    """Server-owned, audited finance, sponsorship, and coupon records.

    Money is stored as integer thousandths of an Omani rial.  Every update is
    optimistic: the caller must send the version it read, preventing silent
    overwrites when two administrators edit the same record.
    """

    FINANCE_KINDS = {"revenue", "expense", "adjustment", "refund"}
    FINANCE_STATUSES = {"draft", "posted", "voided"}
    SPONSORSHIP_STATUSES = {
        "draft", "scheduled", "active", "paused", "completed", "cancelled"
    }
    LEGACY_MIGRATION_KEY = "COMMERCIAL_RECORDS_SERVER_V1"
    SCHEMA_MIGRATION_KEY = "COMMERCIAL_RECORDS_SCHEMA_V2"

    def __init__(self, con, *, now: datetime | None = None):
        self.con = con
        self.now = now or utcnow()

    @staticmethod
    def install_schema(con) -> None:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS finance_entries(
              id TEXT PRIMARY KEY, kind TEXT NOT NULL, amount_milli INTEGER NOT NULL,
              currency TEXT NOT NULL DEFAULT 'OMR', source TEXT NOT NULL DEFAULT '',
              reference_kind TEXT NOT NULL DEFAULT '', reference_id TEXT NOT NULL DEFAULT '',
              external_key TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL DEFAULT 'posted', occurred_at TEXT NOT NULL,
              created_by TEXT NOT NULL, updated_by TEXT NOT NULL,
              version INTEGER NOT NULL DEFAULT 1, archived_at TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              CHECK(kind IN ('revenue','expense','adjustment','refund')),
              CHECK(status IN ('draft','posted','voided')), CHECK(currency='OMR'),
              CHECK(amount_milli>=0), CHECK(version>=1)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_finance_external_key
              ON finance_entries(external_key) WHERE external_key!='';
            CREATE INDEX IF NOT EXISTS idx_finance_occurred
              ON finance_entries(status,occurred_at,kind);
            CREATE TABLE IF NOT EXISTS finance_entry_events(
              id TEXT PRIMARY KEY, entry_id TEXT NOT NULL, event_type TEXT NOT NULL,
              actor_id TEXT NOT NULL, version INTEGER NOT NULL, snapshot TEXT NOT NULL,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              FOREIGN KEY(entry_id) REFERENCES finance_entries(id)
            );
            CREATE INDEX IF NOT EXISTS idx_finance_events_entry
              ON finance_entry_events(entry_id,created_at);
            CREATE TABLE IF NOT EXISTS sponsorships(
              id TEXT PRIMARY KEY, sponsor_name TEXT NOT NULL,
              contact_phone TEXT NOT NULL DEFAULT '', placement TEXT NOT NULL DEFAULT 'home',
              amount_milli INTEGER NOT NULL DEFAULT 0, currency TEXT NOT NULL DEFAULT 'OMR',
              starts_at TEXT NOT NULL DEFAULT '', ends_at TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL DEFAULT 'draft', external_key TEXT NOT NULL DEFAULT '',
              note TEXT NOT NULL DEFAULT '',
              created_by TEXT NOT NULL, updated_by TEXT NOT NULL,
              version INTEGER NOT NULL DEFAULT 1, archived_at TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              CHECK(status IN ('draft','scheduled','active','paused','completed','cancelled')),
              CHECK(currency='OMR'), CHECK(amount_milli>=0), CHECK(version>=1)
            );
            CREATE INDEX IF NOT EXISTS idx_sponsorship_status_dates
              ON sponsorships(status,starts_at,ends_at);
            CREATE TABLE IF NOT EXISTS sponsorship_events(
              id TEXT PRIMARY KEY, sponsorship_id TEXT NOT NULL, event_type TEXT NOT NULL,
              actor_id TEXT NOT NULL, version INTEGER NOT NULL, snapshot TEXT NOT NULL,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              FOREIGN KEY(sponsorship_id) REFERENCES sponsorships(id)
            );
            CREATE TABLE IF NOT EXISTS coupon_events(
              id TEXT PRIMARY KEY, coupon_id TEXT NOT NULL, event_type TEXT NOT NULL,
              actor_id TEXT NOT NULL, version INTEGER NOT NULL, snapshot TEXT NOT NULL,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              FOREIGN KEY(coupon_id) REFERENCES coupons(id)
            );
            """
        )
        for table, additions in {
            "sponsorships": {
                "external_key": "TEXT NOT NULL DEFAULT ''",
            },
            "coupons": {
                "external_key": "TEXT NOT NULL DEFAULT ''",
                "created_by": "TEXT NOT NULL DEFAULT ''",
                "updated_by": "TEXT NOT NULL DEFAULT ''",
                "version": "INTEGER NOT NULL DEFAULT 1",
                "archived_at": "TEXT NOT NULL DEFAULT ''",
            },
        }.items():
            columns = {
                row["name"] if hasattr(row, "keys") else row[1]
                for row in con.execute(f"PRAGMA table_info({table})")  # nosec B608
            }
            for name, definition in additions.items():
                if name not in columns:
                    # Table, column, and definitions are fixed literals above.
                    con.execute(  # nosec B608
                        f"ALTER TABLE {table} ADD COLUMN {name} {definition}"
                    )
        con.executescript(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_sponsorship_external_key
              ON sponsorships(external_key) WHERE external_key!='';
            CREATE UNIQUE INDEX IF NOT EXISTS idx_coupon_external_key
              ON coupons(external_key) WHERE external_key!='';
            """
        )
        CommercialRecordService.migrate_legacy_records(con)
        tables = {
            str(row[0]) for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "settings" in tables:
            con.execute(
                "INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)",
                (
                    CommercialRecordService.SCHEMA_MIGRATION_KEY,
                    dump({"version": 2, "installedAt": iso(utcnow())}),
                ),
            )

    @classmethod
    def migrate_legacy_records(cls, con) -> dict[str, int]:
        """Copy legacy local records once without deleting their rollback source."""
        tables = {
            str(row[0]) for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        has_settings = "settings" in tables
        if has_settings and con.execute(
            "SELECT 1 FROM settings WHERE key=?", (cls.LEGACY_MIGRATION_KEY,)
        ).fetchone():
            return {"finance": 0, "sponsorships": 0, "coupons": 0}

        service = cls(con)
        migrated = {"finance": 0, "sponsorships": 0, "coupons": 0}
        seen_finance_ids: set[str] = set()

        def source_id(source: str, item: dict[str, Any], index: int) -> str:
            raw = str(item.get("id") or "").strip()
            if raw:
                return raw[:120]
            digest = hashlib.sha256(
                f"{source}:{index}:{dump(item)}".encode("utf-8")
            ).hexdigest()[:24]
            return digest

        def canonical_id(kind: str, original_id: str) -> str:
            digest = hashlib.sha256(
                f"{kind}:{original_id}".encode("utf-8")
            ).hexdigest()[:24]
            return f"migrated-{kind}-{digest}"

        def safe_date(value: Any) -> str:
            parsed = parse_datetime(value)
            return iso(parsed) if parsed else iso(service.now)

        def migrate_finance(
            item: dict[str, Any], index: int, source: str, default_kind: str = ""
        ) -> None:
            original_id = source_id(source, item, index)
            if original_id in seen_finance_ids:
                return
            seen_finance_ids.add(original_id)
            record_id = canonical_id("finance", original_id)
            if con.execute(
                "SELECT 1 FROM finance_entries WHERE id=?", (record_id,)
            ).fetchone():
                return
            try:
                amount = as_money(item.get("amount", 0))
            except DomainError:
                amount = Decimal("0")
            if not amount.is_finite():
                amount = Decimal("0")
            raw_kind = str(item.get("kind") or item.get("type") or default_kind).lower()
            if raw_kind not in cls.FINANCE_KINDS:
                raw_kind = "expense" if amount < 0 or default_kind == "expense" else "revenue"
            service.save_finance(
                {
                    "id": record_id,
                    "kind": raw_kind,
                    "amount": str(abs(amount)),
                    "source": str(item.get("source") or source)[:120],
                    "referenceKind": source,
                    "referenceId": original_id,
                    "externalKey": f"migration:{source}:{original_id}"[:160],
                    "note": str(item.get("note") or "")[:1000],
                    "status": "posted",
                    "occurredAt": safe_date(
                        item.get("occurredAt") or item.get("date")
                        or item.get("created_at") or item.get("createdAt")
                    ),
                },
                "system:migration",
            )
            migrated["finance"] += 1

        if "finance" in tables:
            for index, row in enumerate(con.execute("SELECT * FROM finance")):
                migrate_finance(row_dict(row), index, "legacy_finance")

        classic_state: dict[str, Any] = {}
        if has_settings:
            row = con.execute(
                "SELECT value FROM settings WHERE key='classicState'"
            ).fetchone()
            loaded = load(row[0], {}) if row else {}
            classic_state = loaded if isinstance(loaded, dict) else {}
        for key, default_kind in (("finance", ""), ("expenses", "expense")):
            rows = classic_state.get(key, [])
            if not isinstance(rows, list):
                continue
            for index, item in enumerate(rows):
                if isinstance(item, dict):
                    migrate_finance(item, index, "classic_finance", default_kind)

        sponsorship_rows: list[Any] = []
        for key in ("sponsorships", "sponsors"):
            rows = classic_state.get(key, [])
            if isinstance(rows, list):
                sponsorship_rows.extend(rows)
        seen_sponsorship_ids: set[str] = set()
        if sponsorship_rows:
            for index, item in enumerate(sponsorship_rows):
                if not isinstance(item, dict):
                    continue
                original_id = source_id("classic_sponsorship", item, index)
                if original_id in seen_sponsorship_ids:
                    continue
                seen_sponsorship_ids.add(original_id)
                record_id = canonical_id("sponsorship", original_id)
                if con.execute(
                    "SELECT 1 FROM sponsorships WHERE id=?", (record_id,)
                ).fetchone():
                    continue
                raw_status = str(item.get("status") or "").strip().lower()
                if raw_status not in cls.SPONSORSHIP_STATUSES:
                    raw_status = "active" if item.get("active") else "draft"
                start = parse_datetime(item.get("startsAt") or item.get("start"))
                end = parse_datetime(item.get("endsAt") or item.get("end"))
                if start and end and start > end:
                    end = None
                try:
                    amount = as_money(item.get("amount", 0))
                except DomainError:
                    amount = Decimal("0")
                if not amount.is_finite() or amount < 0:
                    amount = Decimal("0")
                service.save_sponsorship(
                    {
                        "id": record_id,
                        "name": item.get("sponsorName") or item.get("name")
                        or "Legacy sponsorship",
                        "phone": item.get("phone", ""),
                        "placement": item.get("placement", "home"),
                        "amount": str(amount),
                        "startsAt": iso(start) if start else "",
                        "endsAt": iso(end) if end else "",
                        "status": raw_status,
                        "note": item.get("note", ""),
                    },
                    "system:migration",
                )
                migrated["sponsorships"] += 1

        coupon_rows = classic_state.get("coupons", [])
        if isinstance(coupon_rows, list):
            for index, item in enumerate(coupon_rows):
                if not isinstance(item, dict):
                    continue
                code = "".join(
                    ch for ch in str(item.get("code") or "").upper()
                    if ch.isascii() and (ch.isalnum() or ch in "_-")
                )[:32]
                if not code or con.execute(
                    "SELECT 1 FROM coupons WHERE code=?", (code,)
                ).fetchone():
                    continue
                original_id = source_id("classic_coupon", item, index)
                record_id = canonical_id("coupon", original_id)
                start = parse_datetime(item.get("startsAt"))
                end = parse_datetime(item.get("endsAt"))
                if start and end and start > end:
                    end = None
                discount_type = str(item.get("discountType") or "fixed").lower()
                if discount_type not in {"fixed", "percent"}:
                    discount_type = "fixed"
                try:
                    discount_value = as_money(
                        item.get("discountValue", item.get("value", 0))
                    )
                except DomainError:
                    discount_value = Decimal("0")
                if not discount_value.is_finite() or discount_value < 0:
                    discount_value = Decimal("0")
                discount_value = min(
                    discount_value,
                    Decimal("100") if discount_type == "percent" else Decimal("1000000"),
                )
                applies_to = item.get("appliesTo", [])
                if not isinstance(applies_to, list):
                    applies_to = []
                try:
                    max_uses = max(0, min(int(item.get("maxUses", 0) or 0), 1_000_000))
                except (TypeError, ValueError):
                    max_uses = 0
                try:
                    active = cls._boolean(item.get("active"), default=True)
                except DomainError:
                    active = True
                service.save_coupon(
                    {
                        "id": record_id,
                        "code": code,
                        "nameAr": item.get("nameAr", ""),
                        "nameEn": item.get("nameEn", ""),
                        "discountType": discount_type,
                        "discountValue": str(discount_value),
                        "appliesTo": applies_to,
                        "startsAt": iso(start) if start else "",
                        "endsAt": iso(end) if end else "",
                        "maxUses": max_uses,
                        "active": active,
                    },
                    "system:migration",
                    allowed_plan_ids=PLAN_IDS,
                )
                migrated["coupons"] += 1

        if has_settings:
            con.execute(
                "INSERT INTO settings(key,value) VALUES(?,?)",
                (cls.LEGACY_MIGRATION_KEY, dump(migrated)),
            )
        return migrated

    @staticmethod
    def _text(value: Any, limit: int) -> str:
        return str(value or "").strip()[:limit]

    @staticmethod
    def _milli(value: Any) -> int:
        amount = as_money(value)
        if amount < 0 or amount > Decimal("1000000000"):
            raise DomainError("amount_out_of_range", 400)
        return int(amount * 1000)

    @staticmethod
    def _expected(value: Any, *, required: bool) -> int:
        if value in (None, ""):
            if required:
                raise DomainError("expected_version_required", 428)
            return 0
        try:
            result = int(value)
        except (TypeError, ValueError) as exc:
            raise DomainError("invalid_expected_version", 400) from exc
        if result < 0:
            raise DomainError("invalid_expected_version", 400)
        return result

    @staticmethod
    def _timestamp(value: Any, *, required: bool = False) -> str:
        text = str(value or "").strip()
        if not text:
            if required:
                raise DomainError("commercial_record_date_required", 400)
            return ""
        parsed = parse_datetime(text)
        if not parsed:
            raise DomainError("invalid_commercial_record_date", 400)
        return iso(parsed)

    @staticmethod
    def _boolean(value: Any, *, default: bool = False) -> bool:
        if value in (None, ""):
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1"}:
                return True
            if normalized in {"false", "0"}:
                return False
        raise DomainError("invalid_commercial_record_boolean", 400)

    @staticmethod
    def finance_public(row: Any) -> dict[str, Any]:
        item = row_dict(row)
        amount = float(Decimal(int(item["amount_milli"])) / 1000)
        return {
            "id": item["id"], "kind": item["kind"], "type": item["kind"],
            "amount": amount, "currency": item["currency"], "source": item["source"],
            "referenceKind": item["reference_kind"], "referenceId": item["reference_id"],
            "externalKey": item["external_key"], "note": item["note"],
            "status": item["status"], "occurredAt": item["occurred_at"],
            "date": str(item["occurred_at"] or "")[:10], "version": int(item["version"]),
            "archivedAt": item["archived_at"], "createdAt": item["created_at"],
            "updatedAt": item["updated_at"],
        }

    @staticmethod
    def sponsorship_public(row: Any) -> dict[str, Any]:
        item = row_dict(row)
        return {
            "id": item["id"], "name": item["sponsor_name"],
            "sponsorName": item["sponsor_name"], "phone": item["contact_phone"],
            "placement": item["placement"],
            "amount": float(Decimal(int(item["amount_milli"])) / 1000),
            "currency": item["currency"], "start": item["starts_at"],
            "end": item["ends_at"], "startsAt": item["starts_at"],
            "endsAt": item["ends_at"], "status": item["status"],
            "active": item["status"] == "active" and not item["archived_at"],
            "externalKey": item.get("external_key", ""),
            "note": item["note"], "version": int(item["version"]),
            "archivedAt": item["archived_at"], "createdAt": item["created_at"],
            "updatedAt": item["updated_at"],
        }

    @staticmethod
    def coupon_public(row: Any) -> dict[str, Any]:
        item = row_dict(row)
        value = float(item.get("discount_value") or 0)
        return {
            "id": item["id"], "code": item["code"], "nameAr": item.get("name_ar", ""),
            "nameEn": item.get("name_en", ""), "discountType": item["discount_type"],
            "discountValue": value, "value": value,
            "appliesTo": load(item.get("applies_to"), []),
            "startsAt": item.get("starts_at", ""), "endsAt": item.get("ends_at", ""),
            "maxUses": int(item.get("max_uses") or 0),
            "usesCount": int(item.get("uses_count") or 0),
            "active": bool(item.get("active")) and not item.get("archived_at"),
            "externalKey": item.get("external_key", ""),
            "version": int(item.get("version") or 1),
            "archivedAt": item.get("archived_at", ""),
            "createdAt": item.get("created_at", ""), "updatedAt": item.get("updated_at", ""),
        }

    def list_finance(self) -> list[dict[str, Any]]:
        return [self.finance_public(row) for row in self.con.execute(
            "SELECT * FROM finance_entries ORDER BY occurred_at DESC,created_at DESC"
        )]

    def list_sponsorships(self) -> list[dict[str, Any]]:
        return [self.sponsorship_public(row) for row in self.con.execute(
            "SELECT * FROM sponsorships ORDER BY starts_at DESC,created_at DESC"
        )]

    def list_coupons(self) -> list[dict[str, Any]]:
        return [self.coupon_public(row) for row in self.con.execute(
            "SELECT * FROM coupons ORDER BY created_at DESC"
        )]

    def save_finance(self, data: dict[str, Any], actor_id: str) -> dict[str, Any]:
        supplied_id = self._text(data.get("id"), 120)
        record_id = supplied_id or public_id("fin")
        actor_id = self._text(actor_id, 120) or "system"
        external_key = self._text(
            data.get("externalKey", data.get("clientKey")), 160
        )
        current = self.con.execute("SELECT * FROM finance_entries WHERE id=?", (record_id,)).fetchone()
        replay_via_external_key = False
        if not current and external_key:
            current = self.con.execute(
                "SELECT * FROM finance_entries WHERE external_key=?", (external_key,)
            ).fetchone()
            if current:
                record_id = current["id"]
                replay_via_external_key = True
        kind = self._text(data.get("kind", data.get("type")), 24)
        status = self._text(data.get("status", "posted"), 24)
        if kind not in self.FINANCE_KINDS:
            raise DomainError("invalid_finance_kind", 400)
        if status not in self.FINANCE_STATUSES:
            raise DomainError("invalid_finance_status", 400)
        values = (
            kind, self._milli(data.get("amount")), self._text(data.get("source"), 120),
            self._text(data.get("referenceKind"), 60),
            self._text(data.get("referenceId"), 120),
            external_key, self._text(data.get("note"), 1000),
            status, self._timestamp(
                data.get("occurredAt") or data.get("date") or iso(self.now),
                required=True,
            ),
        )
        if current:
            expected_supplied = data.get("expectedVersion") not in (None, "")
            current_values = (
                current["kind"], int(current["amount_milli"]), current["source"],
                current["reference_kind"], current["reference_id"],
                current["external_key"], current["note"], current["status"],
                current["occurred_at"],
            )
            if not expected_supplied and current_values == values:
                return self.finance_public(current)
            if replay_via_external_key:
                raise DomainError("idempotency_key_reused", 409)
            expected = self._expected(
                data.get("expectedVersion"), required=True
            )
            if expected != int(current["version"]):
                raise DomainError("record_version_conflict", 409)
            self.con.execute(
                """UPDATE finance_entries SET kind=?,amount_milli=?,source=?,reference_kind=?,
                reference_id=?,external_key=?,note=?,status=?,occurred_at=?,updated_by=?,
                version=version+1,archived_at=CASE WHEN ?!='voided' THEN ''
                ELSE archived_at END,updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND version=?""",
                (*values, actor_id, status, record_id, expected),
            )
            if self.con.execute("SELECT changes()").fetchone()[0] != 1:
                raise DomainError("record_version_conflict", 409)
            event = "updated"
        else:
            expected = self._expected(data.get("expectedVersion"), required=False)
            if expected:
                raise DomainError("record_version_conflict", 409)
            self.con.execute(
                """INSERT INTO finance_entries(id,kind,amount_milli,currency,source,
                reference_kind,reference_id,external_key,note,status,occurred_at,created_by,updated_by)
                VALUES(?,?,?,'OMR',?,?,?,?,?,?,?,?,?)""",
                (record_id, *values, actor_id, actor_id),
            )
            event = "created"
        result = self.finance_public(
            self.con.execute("SELECT * FROM finance_entries WHERE id=?", (record_id,)).fetchone()
        )
        self.con.execute(
            "INSERT INTO finance_entry_events(id,entry_id,event_type,actor_id,version,snapshot) VALUES(?,?,?,?,?,?)",
            (public_id("finevt"), record_id, event, actor_id, result["version"], dump(result)),
        )
        return result

    def save_sponsorship(self, data: dict[str, Any], actor_id: str) -> dict[str, Any]:
        supplied_id = self._text(data.get("id"), 120)
        record_id = supplied_id or public_id("sponsor")
        actor_id = self._text(actor_id, 120) or "system"
        external_key = self._text(
            data.get("externalKey", data.get("clientKey")), 160
        )
        current = self.con.execute("SELECT * FROM sponsorships WHERE id=?", (record_id,)).fetchone()
        replay_via_external_key = False
        if not current and external_key:
            current = self.con.execute(
                "SELECT * FROM sponsorships WHERE external_key=?", (external_key,)
            ).fetchone()
            if current:
                record_id = current["id"]
                replay_via_external_key = True
        name = self._text(data.get("sponsorName", data.get("name")), 160)
        status = self._text(data.get("status", "draft"), 24)
        start = self._timestamp(data.get("startsAt", data.get("start")))
        end = self._timestamp(data.get("endsAt", data.get("end")))
        if not name:
            raise DomainError("sponsor_name_required", 400)
        if status not in self.SPONSORSHIP_STATUSES:
            raise DomainError("invalid_sponsorship_status", 400)
        if start and end and start > end:
            raise DomainError("invalid_sponsorship_dates", 400)
        values = (
            name, self._text(data.get("phone"), 20),
            self._text(data.get("placement", "home"), 60) or "home",
            self._milli(data.get("amount", 0)), start, end, status,
            external_key, self._text(data.get("note"), 1000),
        )
        if current:
            expected_supplied = data.get("expectedVersion") not in (None, "")
            current_values = (
                current["sponsor_name"], current["contact_phone"],
                current["placement"], int(current["amount_milli"]),
                current["starts_at"], current["ends_at"], current["status"],
                current["external_key"], current["note"],
            )
            if not expected_supplied and current_values == values:
                return self.sponsorship_public(current)
            if replay_via_external_key or (
                not expected_supplied
                and external_key
                and current["external_key"] == external_key
            ):
                raise DomainError("idempotency_key_reused", 409)
            expected = self._expected(
                data.get("expectedVersion"), required=True
            )
            if expected != int(current["version"]):
                raise DomainError("record_version_conflict", 409)
            self.con.execute(
                """UPDATE sponsorships SET sponsor_name=?,contact_phone=?,placement=?,amount_milli=?,
                starts_at=?,ends_at=?,status=?,external_key=?,note=?,updated_by=?,version=version+1,
                archived_at=CASE WHEN ?!='cancelled' THEN '' ELSE archived_at END,
                updated_at=CURRENT_TIMESTAMP WHERE id=? AND version=?""",
                (*values, actor_id, status, record_id, expected),
            )
            if self.con.execute("SELECT changes()").fetchone()[0] != 1:
                raise DomainError("record_version_conflict", 409)
            event = "updated"
        else:
            expected = self._expected(data.get("expectedVersion"), required=False)
            if expected:
                raise DomainError("record_version_conflict", 409)
            self.con.execute(
                """INSERT INTO sponsorships(id,sponsor_name,contact_phone,placement,amount_milli,
                currency,starts_at,ends_at,status,external_key,note,created_by,updated_by)
                VALUES(?,?,?,?,?,'OMR',?,?,?,?,?,?,?)""",
                (record_id, *values, actor_id, actor_id),
            )
            event = "created"
        result = self.sponsorship_public(
            self.con.execute("SELECT * FROM sponsorships WHERE id=?", (record_id,)).fetchone()
        )
        self.con.execute(
            "INSERT INTO sponsorship_events(id,sponsorship_id,event_type,actor_id,version,snapshot) VALUES(?,?,?,?,?,?)",
            (public_id("spevt"), record_id, event, actor_id, result["version"], dump(result)),
        )
        return result

    def save_coupon(
        self, data: dict[str, Any], actor_id: str, *, allowed_plan_ids: Iterable[str]
    ) -> dict[str, Any]:
        supplied_id = self._text(data.get("id"), 120)
        record_id = supplied_id or public_id("coupon")
        actor_id = self._text(actor_id, 120) or "system"
        external_key = self._text(
            data.get("externalKey", data.get("clientKey")), 160
        )
        current = self.con.execute("SELECT * FROM coupons WHERE id=?", (record_id,)).fetchone()
        replay_via_external_key = False
        if not current and external_key:
            current = self.con.execute(
                "SELECT * FROM coupons WHERE external_key=?", (external_key,)
            ).fetchone()
            if current:
                record_id = current["id"]
                replay_via_external_key = True
        code = "".join(ch for ch in self._text(data.get("code"), 32).upper()
                       if ch.isascii() and (ch.isalnum() or ch in "_-"))
        discount_type = self._text(data.get("discountType", "fixed"), 16)
        value = as_money(data.get("discountValue", data.get("value", 0)))
        if not code or discount_type not in {"fixed", "percent"}:
            raise DomainError("invalid_coupon", 400)
        if value < 0 or value > Decimal("1000000") or (discount_type == "percent" and value > 100):
            raise DomainError("invalid_coupon_value", 400)
        raw_plans = data.get("appliesTo", [])
        if not isinstance(raw_plans, list):
            raise DomainError("invalid_coupon_plans", 400)
        allowed = set(allowed_plan_ids)
        plans = list(dict.fromkeys(str(item) for item in raw_plans if str(item) in allowed))
        try:
            max_uses = int(data.get("maxUses", 0) or 0)
        except (TypeError, ValueError) as exc:
            raise DomainError("invalid_coupon_limit", 400) from exc
        if not 0 <= max_uses <= 1_000_000:
            raise DomainError("invalid_coupon_limit", 400)
        start = self._timestamp(data.get("startsAt"))
        end = self._timestamp(data.get("endsAt"))
        if start and end and start > end:
            raise DomainError("invalid_coupon_dates", 400)
        active = int(self._boolean(data.get("active"), default=True))
        values = (
            code, self._text(data.get("nameAr"), 120), self._text(data.get("nameEn"), 120),
            discount_type, float(value), dump(plans), start, end, max_uses, active,
            external_key,
        )
        if current:
            expected_supplied = data.get("expectedVersion") not in (None, "")
            current_values = (
                current["code"], current["name_ar"], current["name_en"],
                current["discount_type"], float(current["discount_value"]),
                current["applies_to"], current["starts_at"], current["ends_at"],
                int(current["max_uses"] or 0), int(current["active"] or 0),
                current["external_key"],
            )
            if not expected_supplied and current_values == values:
                return self.coupon_public(current)
            if replay_via_external_key or (
                not expected_supplied
                and external_key
                and current["external_key"] == external_key
            ):
                raise DomainError("idempotency_key_reused", 409)
            expected = self._expected(data.get("expectedVersion"), required=True)
            if expected != int(current["version"] or 1):
                raise DomainError("record_version_conflict", 409)
            self.con.execute(
                """UPDATE coupons SET code=?,name_ar=?,name_en=?,discount_type=?,discount_value=?,
                applies_to=?,starts_at=?,ends_at=?,max_uses=?,active=?,external_key=?,
                updated_by=?,version=version+1,
                archived_at=CASE WHEN ?=1 THEN '' ELSE archived_at END,
                updated_at=CURRENT_TIMESTAMP WHERE id=? AND version=?""",
                (*values, actor_id, active, record_id, expected),
            )
            if self.con.execute("SELECT changes()").fetchone()[0] != 1:
                raise DomainError("record_version_conflict", 409)
            event = "updated"
        else:
            expected = self._expected(data.get("expectedVersion"), required=False)
            if expected:
                raise DomainError("record_version_conflict", 409)
            self.con.execute(
                """INSERT INTO coupons(id,code,name_ar,name_en,discount_type,discount_value,
                applies_to,starts_at,ends_at,max_uses,active,external_key,created_by,updated_by,version)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
                (record_id, *values, actor_id, actor_id),
            )
            event = "created"
        result = self.coupon_public(
            self.con.execute("SELECT * FROM coupons WHERE id=?", (record_id,)).fetchone()
        )
        self.con.execute(
            "INSERT INTO coupon_events(id,coupon_id,event_type,actor_id,version,snapshot) VALUES(?,?,?,?,?,?)",
            (public_id("cpevt"), record_id, event, actor_id, result["version"], dump(result)),
        )
        return result


class PlanCatalog:
    @staticmethod
    def foundation_for(account_type: str) -> str:
        return "company_free_3m" if account_type == "company" else "individual_free_3m"

    @staticmethod
    def scope(plan: dict[str, Any] | None) -> str:
        plan = plan or {}
        entitlements = plan.get("entitlements") if isinstance(plan.get("entitlements"), dict) else {}
        scope = str(plan.get("account_scope") or entitlements.get("accountScope") or "all")
        return scope if scope in {"individual", "company", "all"} else "all"

    @staticmethod
    def supports_account(plan: dict[str, Any] | None, account_type: str) -> bool:
        scope = PlanCatalog.scope(plan)
        normalized = "company" if account_type == "company" else "individual"
        return scope in {"all", normalized}

    @staticmethod
    def compatible_id(plan_id: str, account_type: str) -> str:
        normalized = "company" if account_type == "company" else "individual"
        special = {
            "foundation_12m": PlanCatalog.foundation_for(normalized),
            "basic_6m": "company_silver_6m" if normalized == "company" else "individual_silver_6m",
            "basic_12m": "company_silver_6m" if normalized == "company" else "individual_silver_6m",
            "professional_12m": "company_gold_6m" if normalized == "company" else "individual_gold_6m",
            "business_12m": "company_elite_6m" if normalized == "company" else "individual_elite_6m",
        }
        mapped = special.get(str(plan_id), LEGACY_PLAN_MAP.get(str(plan_id), str(plan_id)))
        if normalized == "company" and mapped.startswith("individual_"):
            tier = mapped.removeprefix("individual_")
            company_candidate = f"company_{tier}"
            if company_candidate in PLAN_IDS:
                return company_candidate
        return mapped

    @staticmethod
    def account_limits(plan: dict[str, Any] | None, account_type: str) -> dict[str, int]:
        plan = plan or {}
        normalized_type = "company" if account_type == "company" else "individual"
        entitlements = plan.get("entitlements") if isinstance(plan.get("entitlements"), dict) else {}
        limits = entitlements.get("accountLimits", {}).get(normalized_type, {})
        result = {
            "maxServices": int(limits.get("maxServices") or plan.get("max_services") or 0),
            "maxCategories": int(limits.get("maxCategories") or plan.get("max_categories") or 0),
            "maxImages": int(limits.get("maxImages") or plan.get("max_images") or 0),
            "maxWilayats": int(limits.get("maxWilayats") or plan.get("max_wilayats") or 0),
            "maxGovernorates": int(
                limits.get("maxGovernorates") or plan.get("max_governorates") or 0
            ),
            "maxTeamMembers": int(limits.get("maxTeamMembers") or plan.get("max_team_members") or entitlements.get("teamMembers") or 1),
            "maxBranches": int(limits.get("maxBranches") or plan.get("max_branches") or entitlements.get("branches") or 1),
        }
        if normalized_type == "individual":
            result["maxTeamMembers"] = 1
        return result

    @staticmethod
    def seed(con) -> None:
        for plan in PLAN_DEFINITIONS:
            entitlements = {
                "maxServices": plan["max_services"],
                "maxCategories": plan["max_categories"],
                "maxImages": plan["max_images"],
                "maxWilayats": plan["max_wilayats"],
                "maxGovernorates": plan["max_governorates"],
                "monthlyResponses": plan["monthly_response_limit"],
                "leadDelaySeconds": plan["lead_delay_seconds"],
                "leadDelayMinutes": math.ceil(plan["lead_delay_seconds"] / 60),
                "teamMembers": plan["max_team_members"],
                "branches": plan["max_branches"],
                "sharedInbox": bool(plan["shared_inbox"]),
                "advancedReports": bool(plan["advanced_reports"]),
                "accountScope": plan["account_scope"],
                "communityPackageQuota": plan["community_package_quota"],
                "communityPackageDays": plan["community_package_days"],
                "accountLimits": PLAN_ACCOUNT_LIMITS.get(plan["id"], {}),
            }
            con.execute(
                """INSERT INTO packages(
                id,ar,en,price,duration_days,featured_boost,max_services,max_images,active,
                currency,max_categories,max_wilayats,max_governorates,monthly_response_limit,
                lead_delay_minutes,lead_delay_seconds,max_team_members,max_branches,shared_inbox,
                advanced_reports,badge_ar,badge_en,foundation_once,verified_required,
                legacy,account_scope,community_package_quota,community_package_days,entitlements)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO NOTHING""",
                (
                    plan["id"], plan["ar"], plan["en"], plan["price"],
                    plan["duration_days"], 0, plan["max_services"], plan["max_images"], 1,
                    plan["currency"], plan["max_categories"], plan["max_wilayats"], plan["max_governorates"],
                    plan["monthly_response_limit"], math.ceil(plan["lead_delay_seconds"] / 60),
                    plan["lead_delay_seconds"],
                    plan["max_team_members"], plan["max_branches"], plan["shared_inbox"],
                    plan["advanced_reports"], plan["badge_ar"], plan["badge_en"],
                    plan["foundation_once"], plan["verified_required"], 0, plan["account_scope"],
                    plan["community_package_quota"], plan["community_package_days"], dump(entitlements),
                ),
            )
            current = con.execute(
                "SELECT entitlements FROM packages WHERE id=?", (plan["id"],)
            ).fetchone()
            current_entitlements = load(current["entitlements"], {}) if current else {}
            if not isinstance(current_entitlements, dict):
                current_entitlements = {}
            changed = False
            for key, value in entitlements.items():
                if key not in current_entitlements:
                    current_entitlements[key] = value
                    changed = True
            if not isinstance(current_entitlements.get("accountLimits"), dict):
                current_entitlements["accountLimits"] = PLAN_ACCOUNT_LIMITS.get(plan["id"], {})
                changed = True
            if changed:
                con.execute(
                    "UPDATE packages SET entitlements=? WHERE id=?",
                    (dump(current_entitlements), plan["id"]),
                )
        con.executemany(
            "UPDATE packages SET active=0,legacy=1 WHERE id=?",
            ((plan_id,) for plan_id in LEGACY_PLAN_IDS),
        )

    @staticmethod
    def get(con, plan_id: str, active_only: bool = True) -> dict[str, Any] | None:
        if active_only:
            row = con.execute(
                "SELECT * FROM packages WHERE id=? AND active=1 AND legacy=0", (plan_id,)
            ).fetchone()
        else:
            row = con.execute("SELECT * FROM packages WHERE id=?", (plan_id,)).fetchone()
        if not row:
            return None
        result = row_dict(row)
        result["entitlements"] = load(result.get("entitlements"), {})
        return result

    @staticmethod
    def active(con) -> list[dict[str, Any]]:
        return [PlanCatalog.get(con, row["id"], False) for row in con.execute(
            "SELECT id FROM packages WHERE active=1 AND legacy=0 ORDER BY account_scope,price,duration_days"
        )]


class SubscriptionService:
    ACTIVE_ACCESS_STATES = {"foundation", "active", "expiring", "grace"}
    HARD_STOP_STATES = {"expired", "suspended", "cancelled", "refunded", "pending_payment"}

    def __init__(self, con, *, now: datetime | None = None, grace_days: int = 14):
        self.con = con
        self.now = (now or utcnow()).astimezone(UTC)
        self.grace_days = max(0, int(grace_days))

    def latest(self, provider_id: str) -> dict[str, Any] | None:
        row = self.con.execute(
            """SELECT * FROM subscriptions WHERE provider_id=?
            ORDER BY CASE status
              WHEN 'active' THEN 1 WHEN 'foundation' THEN 1 WHEN 'expiring' THEN 1
              WHEN 'grace' THEN 2 WHEN 'pending_payment' THEN 3 ELSE 4 END,
              COALESCE(activated_at,created_at) DESC LIMIT 1""",
            (provider_id,),
        ).fetchone()
        return row_dict(row) or None

    def computed_state(self, subscription: dict[str, Any] | Any) -> str:
        item = row_dict(subscription)
        stored = str(item.get("status") or "pending_payment")
        if stored in {"suspended", "cancelled", "refunded", "pending_payment"}:
            return stored
        end = parse_datetime(item.get("end_date"))
        if not end:
            return "pending_payment" if stored not in {"foundation", "active"} else stored
        remaining = (end.date() - self.now.date()).days
        if stored == "foundation" and remaining < 0:
            return "expired"
        if remaining < -self.grace_days:
            return "expired"
        if remaining < 0:
            return "grace"
        if remaining <= 14:
            return "expiring"
        if stored == "foundation":
            return "foundation"
        return "active"

    def synchronize_provider(self, provider_id: str) -> dict[str, Any]:
        subscription = self.latest(provider_id)
        if not subscription:
            self.con.execute(
                """UPDATE providers SET subscription_state='expired',listing_enabled=0,
                request_enabled=0 WHERE id=?""",
                (provider_id,),
            )
            return {"state": "expired", "changed": False, "subscription": None}
        old_state = subscription.get("status") or "pending_payment"
        state = self.computed_state(subscription)
        if state != old_state and old_state not in {"suspended", "cancelled", "refunded", "pending_payment"}:
            self.con.execute(
                "UPDATE subscriptions SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (state, subscription["id"]),
            )
            self._event(subscription["id"], "state_changed", old_state, state, "system")
            subscription["status"] = state
        allowed = state in self.ACTIVE_ACCESS_STATES
        self.con.execute(
            """UPDATE providers SET package_id=?,subscription_state=?,listing_enabled=?,
            request_enabled=?,subscription_start=?,subscription_until=? WHERE id=?""",
            (
                subscription["package_id"], state, int(allowed), int(allowed),
                subscription.get("start_date") or "", subscription.get("end_date") or "", provider_id,
            ),
        )
        return {"state": state, "changed": state != old_state, "subscription": subscription}

    def synchronize_all(self) -> list[dict[str, Any]]:
        changes = []
        for row in self.con.execute("SELECT id FROM providers"):
            result = self.synchronize_provider(row["id"])
            if result["changed"]:
                changes.append({"providerId": row["id"], **result})
        return changes

    def foundation_eligible(self, provider_id: str) -> tuple[bool, str]:
        provider = self.con.execute("SELECT * FROM providers WHERE id=?", (provider_id,)).fetchone()
        if not provider:
            return False, "provider_not_found"
        if not int(provider["verified"] or 0):
            return False, "foundation_requires_verification"
        phone = normalized_phone(provider["phone"])
        commercial = str(provider["commercial_no"] or "").strip().casefold()
        fingerprint = hashlib.sha256(f"{phone}|{commercial}".encode("utf-8")).hexdigest()
        used = self.con.execute(
            """SELECT id FROM foundation_claims WHERE provider_id=? OR phone=?
            OR (commercial_no!='' AND commercial_no=?) OR fingerprint=? LIMIT 1""",
            (provider_id, phone, commercial, fingerprint),
        ).fetchone()
        return (not bool(used), "foundation_already_used" if used else "")

    def request_plan(
        self,
        provider_id: str,
        plan_id: str,
        *,
        coupon_code: str = "",
        payment_required: bool = True,
        actor: str = "provider",
    ) -> dict[str, Any]:
        provider = self.con.execute("SELECT * FROM providers WHERE id=?", (provider_id,)).fetchone()
        if not provider:
            raise DomainError("provider_not_found", 404)
        account_type = "company" if str(provider["provider_type"] or "individual") == "company" else "individual"
        plan_id = PlanCatalog.compatible_id(plan_id, account_type)
        plan = PlanCatalog.get(self.con, plan_id)
        if not plan:
            raise DomainError("package_not_found", 404)
        if not PlanCatalog.supports_account(plan, account_type):
            raise DomainError("package_account_type_mismatch", 409)
        if int(plan.get("foundation_once") or 0):
            eligible, reason = self.foundation_eligible(provider_id)
            if not eligible:
                raise DomainError(reason, 409)
        current = self.latest(provider_id)
        current_state = self.computed_state(current) if current else "expired"
        current_plan = PlanCatalog.get(self.con, current.get("package_id", ""), False) if current else None
        is_upgrade = bool(
            current and current_state in self.ACTIVE_ACCESS_STATES
            and as_money(plan["price"]) > as_money((current_plan or {}).get("price"))
        )
        is_downgrade = bool(
            current and current_state in self.ACTIVE_ACCESS_STATES
            and as_money(plan["price"]) < as_money((current_plan or {}).get("price"))
            and not int(plan.get("foundation_once") or 0)
        )
        is_renewal = bool(
            current and current_state in self.ACTIVE_ACCESS_STATES
            and plan_id == current.get("package_id")
        )
        if is_downgrade:
            self.con.execute(
                "UPDATE subscriptions SET renewal_package_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (plan_id, current["id"]),
            )
            self._event(current["id"], "downgrade_scheduled", current["package_id"], plan_id, actor)
            return {
                "subscriptionId": current["id"],
                "status": current_state,
                "renewalPackageId": plan_id,
                "effective": "next_renewal",
                "amount": float(as_money(plan["price"])),
                "currency": plan["currency"],
            }
        quote = self.upgrade_quote(current, plan) if is_upgrade else {
            "amountDue": as_money(plan["price"]), "credit": Decimal("0.000")
        }
        discount = self._coupon_discount(coupon_code, provider_id, plan_id, quote["amountDue"])
        amount_due = max(Decimal("0.000"), quote["amountDue"] - discount)
        subscription_id = public_id("sub")
        status = "foundation" if int(plan.get("foundation_once") or 0) else "pending_payment"
        start = self.now
        end = start + timedelta(days=int(plan["duration_days"]))
        self.con.execute(
            """INSERT INTO subscriptions(
            id,provider_id,package_id,amount,status,start_date,end_date,note,currency,
            grace_days,previous_package_id,proration_amount,credit_amount,activated_at,
            grace_until,metadata,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
            (
                subscription_id, provider_id, plan_id, float(amount_due), status,
                start.date().isoformat() if status == "foundation" else "",
                end.date().isoformat() if status == "foundation" else "",
                "", plan["currency"], self.grace_days,
                current.get("package_id", "") if current else "",
                float(quote["amountDue"]), float(quote["credit"]),
                iso(start) if status == "foundation" else "",
                (end + timedelta(days=self.grace_days)).date().isoformat() if status == "foundation" else "",
                dump({
                    "couponCode": coupon_code,
                    "discount": float(discount),
                    "upgrade": is_upgrade,
                    "renewal": is_renewal,
                }),
            ),
        )
        if discount > 0 and coupon_code:
            coupon = self.con.execute(
                "SELECT id FROM coupons WHERE UPPER(code)=?",
                (str(coupon_code).strip().upper(),),
            ).fetchone()
            if coupon:
                self.con.execute(
                    """INSERT INTO coupon_redemptions(
                    id,coupon_id,provider_id,subscription_id,amount)
                    VALUES(?,?,?,?,?)""",
                    (
                        public_id("cred"), coupon["id"], provider_id,
                        subscription_id, float(discount),
                    ),
                )
                self.con.execute(
                    """UPDATE coupons SET uses_count=uses_count+1,
                    updated_at=CURRENT_TIMESTAMP WHERE id=? AND active=1
                    AND (max_uses=0 OR uses_count<max_uses)""",
                    (coupon["id"],),
                )
                if self.con.execute("SELECT changes()").fetchone()[0] != 1:
                    # The redemption insert and subscription request live in the
                    # same transaction, so raising here rolls both back.
                    raise DomainError("coupon_limit_reached", 409)
        if status == "foundation":
            self._claim_foundation(provider_id, subscription_id)
            if current and current.get("id") != subscription_id:
                self.con.execute(
                    "UPDATE subscriptions SET status='cancelled',cancelled_at=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (iso(self.now), current["id"]),
                )
            self.synchronize_provider(provider_id)
        elif not payment_required:
            self.activate(subscription_id, actor=actor)
            status = "active"
        self._event(subscription_id, "requested", "", status, actor)
        return {
            "subscriptionId": subscription_id,
            "status": status,
            "amount": float(amount_due),
            "currency": plan["currency"],
            "durationDays": plan["duration_days"],
            "proration": float(quote["amountDue"]),
            "credit": float(quote["credit"]),
            "discount": float(discount),
            "requiresPayment": status == "pending_payment",
        }

    def upgrade_quote(self, current: dict[str, Any] | None, target_plan: dict[str, Any]) -> dict[str, Decimal]:
        if not current:
            return {"amountDue": as_money(target_plan["price"]), "credit": Decimal("0.000")}
        old_plan = PlanCatalog.get(self.con, current.get("package_id", ""), False) or {}
        end = parse_datetime(current.get("end_date"))
        remaining = max(0, (end.date() - self.now.date()).days) if end else 0
        old_duration = max(1, int(old_plan.get("duration_days") or 1))
        credit = (as_money(old_plan.get("price")) * Decimal(remaining) / Decimal(old_duration)).quantize(
            Decimal("0.001"), rounding=ROUND_HALF_UP
        )
        return {
            "amountDue": max(Decimal("0.000"), as_money(target_plan["price"]) - credit),
            "credit": credit,
        }

    def activate(self, subscription_id: str, *, payment_id: str = "", actor: str = "admin") -> dict[str, Any]:
        row = self.con.execute("SELECT * FROM subscriptions WHERE id=?", (subscription_id,)).fetchone()
        if not row:
            raise DomainError("subscription_not_found", 404)
        subscription = row_dict(row)
        if subscription["status"] in {"refunded", "cancelled"}:
            raise DomainError("subscription_cannot_be_activated", 409)
        plan = PlanCatalog.get(self.con, subscription["package_id"], False)
        if not plan:
            raise DomainError("package_not_found", 404)
        metadata = load(subscription.get("metadata"), {})
        old = self.latest(subscription["provider_id"])
        old_end = parse_datetime(old.get("end_date")) if old and old.get("id") != subscription_id else None
        start = max(self.now, old_end) if metadata.get("renewal") and old_end else self.now
        end = start + timedelta(days=int(plan["duration_days"]))
        if old and old["id"] != subscription_id and self.computed_state(old) in self.ACTIVE_ACCESS_STATES:
            self.con.execute(
                "UPDATE subscriptions SET status='cancelled',cancelled_at=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (iso(start), old["id"]),
            )
            self._event(old["id"], "superseded", old["status"], "cancelled", actor)
        state = "foundation" if int(plan.get("foundation_once") or 0) else "active"
        self.con.execute(
            """UPDATE subscriptions SET status=?,start_date=?,end_date=?,activated_at=?,
            grace_until=?,payment_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (
                state, start.date().isoformat(), end.date().isoformat(), iso(start),
                (end + timedelta(days=self.grace_days)).date().isoformat(), payment_id, subscription_id,
            ),
        )
        if state == "foundation":
            self._claim_foundation(subscription["provider_id"], subscription_id)
        self.synchronize_provider(subscription["provider_id"])
        self._event(subscription_id, "activated", subscription["status"], state, actor)
        return row_dict(self.con.execute("SELECT * FROM subscriptions WHERE id=?", (subscription_id,)).fetchone())

    def extend(self, subscription_id: str, *, days: int | None = None, actor: str = "admin") -> dict[str, Any]:
        row = self.con.execute("SELECT * FROM subscriptions WHERE id=?", (subscription_id,)).fetchone()
        if not row:
            raise DomainError("subscription_not_found", 404)
        subscription = row_dict(row)
        plan = PlanCatalog.get(self.con, subscription["package_id"], False)
        if not plan:
            raise DomainError("package_not_found", 404)
        extension_days = max(1, int(days or plan["duration_days"]))
        current_end = parse_datetime(subscription.get("end_date"))
        base = max(self.now, current_end) if current_end else self.now
        new_end = base + timedelta(days=extension_days)
        old_state = subscription.get("status", "")
        state = "foundation" if int(plan.get("foundation_once") or 0) else "active"
        self.con.execute(
            """UPDATE subscriptions SET status=?,end_date=?,grace_until=?,
            updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (
                state, new_end.date().isoformat(),
                (new_end + timedelta(days=self.grace_days)).date().isoformat(), subscription_id,
            ),
        )
        self._event(subscription_id, "extended", old_state, state, actor, f"{extension_days} days")
        self.synchronize_provider(subscription["provider_id"])
        return row_dict(self.con.execute("SELECT * FROM subscriptions WHERE id=?", (subscription_id,)).fetchone())

    def suspend(self, subscription_id: str, *, actor: str = "admin", reason: str = "") -> None:
        row = self.con.execute("SELECT * FROM subscriptions WHERE id=?", (subscription_id,)).fetchone()
        if not row:
            raise DomainError("subscription_not_found", 404)
        old = row["status"]
        self.con.execute(
            "UPDATE subscriptions SET status='suspended',note=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (reason[:500], subscription_id),
        )
        self._event(subscription_id, "suspended", old, "suspended", actor, reason)
        self.synchronize_provider(row["provider_id"])

    def cancel(self, subscription_id: str, *, actor: str = "admin", reason: str = "") -> None:
        row = self.con.execute("SELECT * FROM subscriptions WHERE id=?", (subscription_id,)).fetchone()
        if not row:
            raise DomainError("subscription_not_found", 404)
        if row["status"] == "refunded":
            raise DomainError("refunded_subscription_cannot_be_cancelled", 409)
        self.con.execute(
            """UPDATE subscriptions SET status='cancelled',cancelled_at=?,note=?,
            updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (iso(self.now), reason[:500], subscription_id),
        )
        self._event(subscription_id, "cancelled", row["status"], "cancelled", actor, reason)
        self.synchronize_provider(row["provider_id"])

    def refund(self, subscription_id: str, *, actor: str = "admin", reason: str = "") -> None:
        row = self.con.execute("SELECT * FROM subscriptions WHERE id=?", (subscription_id,)).fetchone()
        if not row:
            raise DomainError("subscription_not_found", 404)
        self.con.execute(
            """UPDATE subscriptions SET status='refunded',refunded_at=?,note=?,
            updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (iso(self.now), reason[:500], subscription_id),
        )
        self._event(subscription_id, "refunded", row["status"], "refunded", actor, reason)
        self.synchronize_provider(row["provider_id"])

    def _event(self, subscription_id: str, event_type: str, before: str, after: str, actor: str, detail: str = "") -> None:
        self.con.execute(
            """INSERT INTO subscription_events(
            id,subscription_id,event_type,from_state,to_state,actor,detail)
            VALUES(?,?,?,?,?,?,?)""",
            (public_id("sevt"), subscription_id, event_type, before, after, actor, detail[:900]),
        )

    def _claim_foundation(self, provider_id: str, subscription_id: str) -> None:
        provider = self.con.execute("SELECT phone,commercial_no FROM providers WHERE id=?", (provider_id,)).fetchone()
        if not provider:
            raise DomainError("provider_not_found", 404)
        phone = normalized_phone(provider["phone"])
        commercial = str(provider["commercial_no"] or "").strip().casefold()
        fingerprint = hashlib.sha256(f"{phone}|{commercial}".encode("utf-8")).hexdigest()
        try:
            self.con.execute(
                """INSERT INTO foundation_claims(
                id,provider_id,phone,commercial_no,fingerprint,subscription_id)
                VALUES(?,?,?,?,?,?)""",
                (public_id("fnd"), provider_id, phone, commercial, fingerprint, subscription_id),
            )
        except Exception as exc:
            if "UNIQUE" not in str(exc).upper():
                raise

    def _coupon_discount(self, code: str, provider_id: str, plan_id: str, amount: Decimal) -> Decimal:
        code = str(code or "").strip().upper()
        if not code:
            return Decimal("0.000")
        row = self.con.execute("SELECT * FROM coupons WHERE UPPER(code)=? AND active=1", (code,)).fetchone()
        if not row:
            raise DomainError("coupon_invalid", 400)
        now = self.now
        starts = parse_datetime(row["starts_at"])
        ends = parse_datetime(row["ends_at"])
        if starts and starts > now or ends and ends < now:
            raise DomainError("coupon_expired", 409)
        if int(row["max_uses"] or 0) and int(row["uses_count"] or 0) >= int(row["max_uses"]):
            raise DomainError("coupon_limit_reached", 409)
        allowed = load(row["applies_to"], [])
        if allowed and plan_id not in allowed:
            raise DomainError("coupon_not_for_plan", 409)
        used = self.con.execute(
            "SELECT id FROM coupon_redemptions WHERE coupon_id=? AND provider_id=?",
            (row["id"], provider_id),
        ).fetchone()
        if used:
            raise DomainError("coupon_already_used", 409)
        value = as_money(row["discount_value"])
        discount = amount * value / Decimal("100") if row["discount_type"] == "percent" else value
        return min(amount, discount.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))


class EntitlementService:
    def __init__(self, con, *, now: datetime | None = None):
        self.con = con
        self.now = now or utcnow()

    def for_provider(self, provider_id: str) -> dict[str, Any]:
        subscription_service = SubscriptionService(self.con, now=self.now)
        sync = subscription_service.synchronize_provider(provider_id)
        subscription = sync.get("subscription")
        plan = PlanCatalog.get(self.con, subscription.get("package_id", ""), False) if subscription else None
        provider = self.con.execute(
            "SELECT provider_type FROM providers WHERE id=?", (provider_id,)
        ).fetchone()
        account_type = (
            "company"
            if provider and str(provider["provider_type"] or "individual") == "company"
            else "individual"
        )
        plan_entitlements = (plan or {}).get("entitlements") or {}
        account_limits = (
            plan_entitlements.get("accountLimits", {}).get(account_type, {})
            if isinstance(plan_entitlements, dict)
            else {}
        )
        return {
            "providerId": provider_id,
            "accountType": account_type,
            "state": sync["state"],
            "planId": plan.get("id", "") if plan else "",
            "allowed": sync["state"] in SubscriptionService.ACTIVE_ACCESS_STATES,
            "maxServices": int(account_limits.get("maxServices") or (plan or {}).get("max_services") or 0),
            "maxCategories": int(account_limits.get("maxCategories") or (plan or {}).get("max_categories") or 0),
            "maxImages": int(account_limits.get("maxImages") or (plan or {}).get("max_images") or 0),
            "maxWilayats": int(account_limits.get("maxWilayats") or (plan or {}).get("max_wilayats") or 0),
            "maxGovernorates": int((plan or {}).get("max_governorates") or 0),
            "monthlyResponses": int((plan or {}).get("monthly_response_limit") or 0),
            "leadDelayMinutes": int((plan or {}).get("lead_delay_minutes") or 0),
            "leadDelaySeconds": int(
                (plan or {}).get("lead_delay_seconds")
                or plan_entitlements.get("leadDelaySeconds")
                or int((plan or {}).get("lead_delay_minutes") or 0) * 60
            ),
            "teamMembers": int((plan or {}).get("max_team_members") or 1),
            "branches": int((plan or {}).get("max_branches") or 1),
            "sharedInbox": bool((plan or {}).get("shared_inbox")),
            "advancedReports": bool((plan or {}).get("advanced_reports")),
            "communityPackageQuota": int(
                (plan or {}).get("community_package_quota")
                or plan_entitlements.get("communityPackageQuota")
                or 0
            ),
            "communityPackageDays": int(
                (plan or {}).get("community_package_days")
                or plan_entitlements.get("communityPackageDays")
                or 30
            ),
            "badgeAr": (plan or {}).get("badge_ar", ""),
            "badgeEn": (plan or {}).get("badge_en", ""),
        }

    def profile_limits(self, provider_id: str, *, preserve_existing: bool = True) -> dict[str, Any]:
        """Return enforceable limits without deleting data retained from an older plan."""
        entitlements = self.for_provider(provider_id)
        if not preserve_existing:
            return entitlements
        provider = self.con.execute(
            "SELECT provider_type,services,areas,governorates FROM providers WHERE id=?",
            (provider_id,),
        ).fetchone()
        if not provider:
            return entitlements
        existing_services = load(provider["services"], [])
        existing_categories = {
            str(item.get("catId", "")).strip()
            for item in existing_services
            if isinstance(item, dict) and str(item.get("catId", "")).strip()
        }
        existing_service_count = len({
            f"{item.get('catId')}|{item.get('serviceId')}"
            for item in existing_services
            if isinstance(item, dict) and item.get("catId") and item.get("serviceId")
        })
        existing_governorates = {
            str(item).strip()
            for item in load(provider["governorates"], [])
            if str(item).strip()
        }
        existing_area_count = len({
            str(area).strip()
            for area in load(provider["areas"], [])
            if str(area).strip() and str(area).strip() not in existing_governorates
        })
        existing_governorate_count = len(existing_governorates)
        is_company = str(provider["provider_type"] or "individual") == "company"
        service_limit = int(entitlements.get("maxServices") or 0)
        wilayah_limit = int(entitlements.get("maxWilayats") or 0)
        return {
            **entitlements,
            "accountType": "company" if is_company else "individual",
            "maxServices": max(service_limit, existing_service_count),
            "maxCategories": max(int(entitlements.get("maxCategories") or 0), len(existing_categories)),
            "maxWilayats": 0 if wilayah_limit == 0 else max(wilayah_limit, existing_area_count),
            "maxGovernorates": max(
                int(entitlements.get("maxGovernorates") or 0),
                existing_governorate_count,
            ),
            "grandfathered": bool(
                existing_service_count > service_limit
                or len(existing_categories) > int(entitlements.get("maxCategories") or 0)
                or (wilayah_limit > 0 and existing_area_count > wilayah_limit)
                or existing_governorate_count
                > int(entitlements.get("maxGovernorates") or 0)
            ),
        }

    def validate_profile(
        self,
        provider_id: str,
        *,
        services: Iterable[Any],
        areas: Iterable[Any],
        governorates: Iterable[Any] = (),
    ) -> dict[str, Any]:
        entitlements = self.profile_limits(provider_id, preserve_existing=True)
        categories = {
            str(item.get("catId", "")).strip()
            for item in services
            if isinstance(item, dict) and str(item.get("catId", "")).strip()
        }
        services_count = len({
            f"{item.get('catId')}|{item.get('serviceId')}"
            for item in services if isinstance(item, dict) and item.get("catId") and item.get("serviceId")
        })
        areas_count = len({str(area).strip() for area in areas if str(area).strip()})
        governorates_count = len(
            {str(item).strip() for item in governorates if str(item).strip()}
        )
        if entitlements["maxCategories"] and len(categories) > entitlements["maxCategories"]:
            raise DomainError("provider_category_limit", 409)
        if entitlements["maxServices"] and services_count > entitlements["maxServices"]:
            raise DomainError("service_limit_exceeded", 409)
        if entitlements["maxWilayats"] and areas_count > entitlements["maxWilayats"]:
            raise DomainError("wilayah_limit_exceeded", 409)
        if (
            entitlements["maxGovernorates"]
            and governorates_count > entitlements["maxGovernorates"]
        ):
            raise DomainError("governorate_limit_exceeded", 409)
        return entitlements

    def can_receive(
        self,
        provider_id: str,
        *,
        enforce_subscription: bool = True,
    ) -> tuple[bool, str, dict[str, Any]]:
        """Return request eligibility without weakening provider safeguards.

        ``enforce_subscription=False`` is reserved for the platform launch mode
        in which management has disabled subscriptions globally.  It bypasses
        only subscription access and plan response limits; provider approval,
        availability, listing visibility, and request opt-in remain mandatory.
        The default retains the historical subscription-enforced behaviour.
        """
        provider = self.con.execute(
            """SELECT active,verified,status,listing_enabled,request_enabled,
            provider_type FROM providers WHERE id=?""",
            (provider_id,),
        ).fetchone()
        if enforce_subscription:
            entitlements = self.for_provider(provider_id)
        else:
            account_type = (
                "company"
                if provider and str(provider["provider_type"] or "individual") == "company"
                else "individual"
            )
            # Stable response shape for callers while intentionally applying no
            # package priority, delay, or quota in open-access launch mode.
            entitlements = {
                "providerId": provider_id,
                "accountType": account_type,
                "state": "not_enforced",
                "planId": "",
                "allowed": True,
                "monthlyResponses": 0,
                "leadDelayMinutes": 0,
                "leadDelaySeconds": 0,
                "subscriptionEnforced": False,
            }
        if not provider or not int(provider["active"] or 0):
            return False, "provider_inactive", entitlements
        if not int(provider["verified"] or 0) or not int(provider["listing_enabled"] or 0):
            return False, "provider_not_approved", entitlements
        if provider["status"] != "available" or not int(provider["request_enabled"] or 0):
            return False, "provider_unavailable", entitlements
        if enforce_subscription and not entitlements["allowed"]:
            return False, "subscription_inactive", entitlements
        limit = entitlements["monthlyResponses"] if enforce_subscription else 0
        if limit:
            month = self.now.strftime("%Y-%m")
            count = self.con.execute(
                """SELECT COUNT(*) n FROM request_dispatches
                WHERE provider_id=? AND status IN ('notified','opened','offered','accepted')
                AND substr(COALESCE(notified_at,created_at),1,7)=?""",
                (provider_id, month),
            ).fetchone()["n"]
            if int(count or 0) >= limit:
                return False, "monthly_response_limit", entitlements
        return True, "", entitlements


class ContactConsentService:
    CHANNELS = {"chat", "whatsapp", "call"}

    def __init__(self, con, *, now: datetime | None = None, lifetime_days: int = 90):
        self.con = con
        self.now = now or utcnow()
        self.lifetime_days = max(1, int(lifetime_days))

    def set_channel(
        self,
        request_id: str,
        user_id: str,
        provider_id: str,
        channel: str,
        granted: bool,
    ) -> dict[str, Any]:
        if channel not in self.CHANNELS:
            raise DomainError("invalid_contact_channel")
        request = self.con.execute(
            "SELECT user_id,accepted_provider_id FROM customer_requests WHERE id=?", (request_id,)
        ).fetchone()
        if not request:
            raise DomainError("request_not_found", 404)
        if request["user_id"] != user_id or request["accepted_provider_id"] != provider_id:
            raise DomainError("contact_consent_not_allowed", 403)
        status = "granted" if granted else "revoked"
        expires = self.now + timedelta(days=self.lifetime_days) if granted else None
        consent_id = public_id("consent")
        self.con.execute(
            """INSERT INTO contact_consents(
            id,request_id,user_id,provider_id,channel,status,granted_at,expires_at,revoked_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(request_id,provider_id,channel) DO UPDATE SET
            user_id=excluded.user_id,status=excluded.status,granted_at=excluded.granted_at,
            expires_at=excluded.expires_at,revoked_at=excluded.revoked_at,updated_at=CURRENT_TIMESTAMP""",
            (
                consent_id, request_id, user_id, provider_id, channel, status,
                iso(self.now) if granted else "", iso(expires) if expires else "",
                "" if granted else iso(self.now),
            ),
        )
        return self.summary(request_id, provider_id)

    def allowed(self, request_id: str, provider_id: str, channel: str) -> bool:
        if channel not in self.CHANNELS:
            return False
        row = self.con.execute(
            """SELECT status,expires_at FROM contact_consents
            WHERE request_id=? AND provider_id=? AND channel=?""",
            (request_id, provider_id, channel),
        ).fetchone()
        if not row or row["status"] != "granted":
            return False
        expires = parse_datetime(row["expires_at"])
        return not expires or expires >= self.now

    def summary(self, request_id: str, provider_id: str) -> dict[str, Any]:
        rows = list(self.con.execute(
            """SELECT channel,status,granted_at,expires_at,revoked_at FROM contact_consents
            WHERE request_id=? AND provider_id=?""",
            (request_id, provider_id),
        ))
        result: dict[str, Any] = {channel: False for channel in self.CHANNELS}
        result["updatedAt"] = ""
        result["expiresAt"] = ""
        for row in rows:
            granted = row["status"] == "granted"
            expires = parse_datetime(row["expires_at"])
            if expires and expires < self.now:
                granted = False
            result[row["channel"]] = granted
            result["updatedAt"] = max(result["updatedAt"], row["granted_at"] or row["revoked_at"] or "")
            if granted and row["expires_at"]:
                result["expiresAt"] = max(result["expiresAt"], row["expires_at"])
        return result


class RankingService:
    WEIGHTS = {
        "match": 0.30,
        "availability": 0.20,
        "quality": 0.20,
        "response": 0.10,
        "profile": 0.08,
        "verification": 0.05,
        "recency": 0.03,
        "plan": 0.04,
    }
    PLAN_PRIORITY = {
        "individual_free_3m": 0.10,
        "individual_silver_6m": 0.40,
        "individual_gold_6m": 0.75,
        "individual_elite_6m": 1.00,
        "company_free_3m": 0.10,
        "company_silver_6m": 0.40,
        "company_gold_6m": 0.75,
        "company_elite_6m": 1.00,
    }
    MATCH_QUALITY = {"exact": 1.0, "related": 0.82, "category": 0.65}
    MATCH_PRIORITY = {"exact": 0, "related": 1, "category": 2}

    @staticmethod
    def _request_service(request: dict[str, Any]) -> tuple[str, str]:
        value = str(request.get("service_value") or request.get("serviceValue") or "").strip()
        if "|" in value:
            category_id, service_id = value.split("|", 1)
            return category_id.strip(), service_id.strip()
        return "", value

    @staticmethod
    def _provider_services(provider: dict[str, Any]) -> list[dict[str, Any]]:
        services = (
            load(provider.get("services"), [])
            if isinstance(provider.get("services"), str)
            else provider.get("services", [])
        )
        return [item for item in services if isinstance(item, dict) and item.get("active", True)]

    @classmethod
    def related_service_match(cls, first: str, second: str) -> bool:
        first = str(first or "").strip()
        second = str(second or "").strip()
        if not first or not second or first == second:
            return False
        return any(first in group and second in group for group in RELATED_SERVICE_GROUPS)

    @classmethod
    def service_family_members(cls, service_id: str) -> tuple[str, ...]:
        """Return the canonical related-service family for availability APIs."""
        service_id = str(service_id or "").strip()
        if not service_id:
            return ()
        for group in RELATED_SERVICE_GROUPS:
            if service_id in group:
                return tuple(sorted(group))
        return (service_id,)

    @classmethod
    def service_match_details(
        cls, request: dict[str, Any], provider: dict[str, Any]
    ) -> dict[str, Any]:
        """Describe the best catalog match without exposing provider identity."""
        requested_cat, requested_service = cls._request_service(request)
        services = cls._provider_services(provider)

        for service in services:
            service_id = str(service.get("serviceId") or "").strip()
            category_id = str(service.get("catId") or "").strip()
            if requested_service and service_id == requested_service and (
                not requested_cat or category_id == requested_cat
            ):
                return {
                    "matched": True,
                    "kind": "exact",
                    "quality": cls.MATCH_QUALITY["exact"],
                    "matchedServiceId": service_id,
                }

        # Family matching is intentionally category-bound.  In particular,
        # design is not in a related family and therefore remains exact-only.
        if requested_cat and requested_service:
            for service in services:
                service_id = str(service.get("serviceId") or "").strip()
                category_id = str(service.get("catId") or "").strip()
                if category_id == requested_cat and cls.related_service_match(
                    requested_service, service_id
                ):
                    return {
                        "matched": True,
                        "kind": "related",
                        "quality": cls.MATCH_QUALITY["related"],
                        "matchedServiceId": service_id,
                    }

        if requested_cat and not requested_service:
            for service in services:
                if str(service.get("catId") or "").strip() == requested_cat:
                    return {
                        "matched": True,
                        "kind": "category",
                        "quality": cls.MATCH_QUALITY["category"],
                        "matchedServiceId": str(service.get("serviceId") or "").strip(),
                    }
        return {
            "matched": False,
            "kind": "none",
            "quality": 0.0,
            "matchedServiceId": "",
        }

    @classmethod
    def exact_service_match(cls, request: dict[str, Any], provider: dict[str, Any]) -> bool:
        """Retain the legacy exact-match helper for existing callers."""
        return cls.service_match_details(request, provider)["kind"] in {"exact", "category"}

    @classmethod
    def service_match(cls, request: dict[str, Any], provider: dict[str, Any]) -> bool:
        return bool(cls.service_match_details(request, provider)["matched"])

    @classmethod
    def exclusion_reason(
        cls,
        request: dict[str, Any],
        provider: dict[str, Any],
        now: datetime | None = None,
    ) -> str:
        """Return a stable, privacy-safe catalog/area exclusion code."""
        if not cls.service_match(request, provider):
            return "service_mismatch"
        if not cls.area_match(request, provider):
            return "area_mismatch"
        if not cls.availability_match(provider, now or utcnow()):
            return "outside_availability"
        return ""

    @classmethod
    def area_match(cls, request: dict[str, Any], provider: dict[str, Any]) -> bool:
        request_wilayah = str(request.get("wilayah") or "").strip()
        request_gov = str(request.get("gov") or "").strip()
        raw_areas = provider.get("areas", [])
        areas = load(raw_areas, []) if isinstance(raw_areas, str) else raw_areas
        if not isinstance(areas, list):
            areas = []

        # A service can deliberately have narrower coverage than the provider
        # profile.  Prefer those areas for the service that actually matched.
        match = cls.service_match_details(request, provider)
        requested_cat, requested_service = cls._request_service(request)
        matched_service_areas: list[Any] = []
        if match["matched"]:
            for service in cls._provider_services(provider):
                category_id = str(service.get("catId") or "").strip()
                service_id = str(service.get("serviceId") or "").strip()
                same_category = not requested_cat or category_id == requested_cat
                same_service = service_id == requested_service or (
                    match["kind"] == "related"
                    and cls.related_service_match(requested_service, service_id)
                )
                if not same_category or not same_service:
                    continue
                raw_service_areas = service.get("areas", [])
                service_areas = (
                    load(raw_service_areas, [])
                    if isinstance(raw_service_areas, str)
                    else raw_service_areas
                )
                if isinstance(service_areas, list):
                    matched_service_areas.extend(service_areas)
        selected_areas = matched_service_areas or areas
        provider_areas = {
            str(value).strip()
            for value in selected_areas
            if str(value or "").strip()
        }
        raw_governorates = provider.get("governorates", [])
        governorates = (
            load(raw_governorates, [])
            if isinstance(raw_governorates, str)
            else raw_governorates
        )
        if not isinstance(governorates, list):
            governorates = []
        declared_governorates = {
            str(value).strip() for value in governorates if str(value or "").strip()
        }
        is_company = str(
            provider.get("provider_type", provider.get("providerType", "individual"))
            or "individual"
        ) == "company"
        if request_wilayah:
            if provider_areas:
                return request_wilayah in provider_areas
            # Company plans declare governorate-wide coverage separately.  A
            # provider's profile governorate is only its location and never
            # silently expands an individual's service area.  Governorate
            # fallback is valid only when no narrower wilayah list exists.
            return bool(
                is_company
                and request_gov
                and request_gov in declared_governorates
            )
        # An individual must match a declared wilayah.  A governorate-only
        # request can therefore be routed only to a company that explicitly
        # declared governorate-wide coverage; profile location is never
        # interpreted as service coverage.
        return bool(
            not provider_areas
            and is_company
            and request_gov
            and request_gov in declared_governorates
        )

    @classmethod
    def availability_match(cls, provider: dict[str, Any], now: datetime) -> bool:
        """Apply provider status, working days, and hours as one hard gate."""
        status = str(provider.get("status") or "").strip()
        if status and status != "available":
            return False
        raw_availability = provider.get("availability")
        availability = (
            load(raw_availability, {})
            if isinstance(raw_availability, str)
            else raw_availability or {}
        )
        if not isinstance(availability, dict) or not availability:
            return True
        service_now = (
            now.replace(tzinfo=OMAN_TZ)
            if now.tzinfo is None
            else now.astimezone(OMAN_TZ)
        )
        days = availability.get("days") or []
        allowed_days = {str(day) for day in days}
        # The mobile/web contract stores Sunday=0 through Saturday=6.  Python's
        # datetime.weekday() is Monday=0, so normalize before every comparison.
        service_weekday = (service_now.weekday() + 1) % 7
        start = str(availability.get("start") or "").strip()
        end = str(availability.get("end") or "").strip()
        if not start and not end:
            return not allowed_days or str(service_weekday) in allowed_days
        if not start or not end:
            return False
        current = service_now.strftime("%H:%M")
        if start <= end:
            return (
                (not allowed_days or str(service_weekday) in allowed_days)
                and start <= current <= end
            )
        # Overnight windows, for example 20:00-03:00.
        if current >= start:
            service_day = service_weekday
        elif current <= end:
            service_day = (service_weekday - 1) % 7
        else:
            return False
        return not allowed_days or str(service_day) in allowed_days

    @classmethod
    def availability_score(cls, provider: dict[str, Any], now: datetime) -> float:
        return 1.0 if cls.availability_match(provider, now) else 0.0

    @classmethod
    def score(cls, request: dict[str, Any], provider: dict[str, Any], plan_id: str, now: datetime) -> tuple[float, dict[str, float]]:
        match = cls.service_match_details(request, provider)
        if (
            not match["matched"]
            or not cls.area_match(request, provider)
            or not cls.availability_match(provider, now)
        ):
            return 0.0, {key: 0.0 for key in cls.WEIGHTS}
        services = load(provider.get("services"), []) if isinstance(provider.get("services"), str) else provider.get("services", [])
        areas = load(provider.get("areas"), []) if isinstance(provider.get("areas"), str) else provider.get("areas", [])
        work_images = load(provider.get("work_images"), []) if isinstance(provider.get("work_images"), str) else provider.get("workImages", [])
        profile_fields = [provider.get("image_path") or provider.get("imagePath"), provider.get("bio"), provider.get("hours"), services, areas, work_images]
        profile = sum(bool(value) for value in profile_fields) / len(profile_fields)
        response_score = float(provider.get("response_score") or provider.get("responseScore") or 70) / 100
        response_minutes = int(provider.get("response_minutes") or provider.get("responseMinutes") or 30)
        response = max(0.0, min(1.0, (response_score + max(0, 1 - response_minutes / 120)) / 2))
        created = parse_datetime(provider.get("created_at") or provider.get("createdAt"))
        age = max(0, (now - created).days) if created else 730
        breakdown = {
            "match": float(match["quality"]),
            "availability": cls.availability_score(provider, now),
            "quality": max(0.0, min(1.0, float(provider.get("quality_score") or provider.get("qualityScore") or 0) / 100)),
            "response": response,
            "profile": profile,
            "verification": 1.0 if int(provider.get("verified") or 0) else 0.0,
            "recency": max(0.0, 1 - age / 730),
            "plan": cls.PLAN_PRIORITY.get(plan_id, 0.0),
        }
        total = sum(breakdown[key] * cls.WEIGHTS[key] for key in cls.WEIGHTS)
        return round(total * 100, 3), {key: round(value, 4) for key, value in breakdown.items()}


class RequestMarketplace:
    TERMINAL_REQUEST_STATUSES = {
        "accepted",
        "appointmentConfirmed",
        "inProgress",
        "awaitingConfirmation",
        "qualityReview",
        "closed",
        "archived",
        "completed",
        "cancelled",
        "deleted",
        "expired",
    }

    def __init__(self, con, *, now: datetime | None = None, expansion_minutes: int = 20, min_offers: int = 2):
        self.con = con
        self.now = now or utcnow()
        self.expansion_minutes = max(5, int(expansion_minutes))
        self.min_offers = max(1, int(min_offers))

    def subscription_delays_enabled(self) -> bool:
        """Apply plan lead delays only after management enables subscriptions."""
        try:
            row = self.con.execute(
                "SELECT value FROM settings WHERE key='platform'"
            ).fetchone()
            settings = load(row["value"], {}) if row else {}
            value = settings.get("subscriptionsEnabled")
            if isinstance(value, str):
                return value.strip().lower() in {"1", "true", "yes", "on"}
            return bool(value)
        except (TypeError, ValueError, KeyError, sqlite3.Error):
            return False

    def _persist_diagnostics(
        self,
        request_id: str,
        *,
        total_providers: int,
        eligible_providers: int,
        reasons: dict[str, int],
    ) -> None:
        """Cache aggregate-only diagnostics when the host schema supports it."""
        table = self.con.execute(
            """SELECT 1 FROM sqlite_master
            WHERE type='table' AND name='request_matching_diagnostics'"""
        ).fetchone()
        if not table:
            return
        self.con.execute(
            """INSERT INTO request_matching_diagnostics(
            request_id,total_providers,eligible_providers,reasons,ranking_version,captured_at)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(request_id) DO UPDATE SET
            total_providers=excluded.total_providers,
            eligible_providers=excluded.eligible_providers,
            reasons=excluded.reasons,
            ranking_version=excluded.ranking_version,
            captured_at=excluded.captured_at""",
            (
                request_id,
                int(total_providers),
                int(eligible_providers),
                dump(dict(sorted(reasons.items()))),
                RANKING_VERSION,
                iso(self.now),
            ),
        )

    def provider_match_reason(
        self,
        request: dict[str, Any],
        provider: dict[str, Any],
        *,
        requested_at: datetime | None = None,
        request_id: str = "",
    ) -> str:
        """Apply the canonical service, area, hours, and capacity gates."""
        requested_at = requested_at or parse_marketplace_datetime(request.get("requested_at")) or self.now
        reason = RankingService.exclusion_reason(request, provider, requested_at)
        if reason:
            return reason
        if not self.provider_has_capacity(
            provider, requested_at, request_id or str(request.get("id") or "")
        ):
            return "daily_capacity_reached"
        return ""

    def diagnostics(self, request_id: str) -> dict[str, Any]:
        """Return aggregate matching reasons suitable for an admin surface.

        Provider identifiers, names, contact details, locations, and profile
        fields are deliberately omitted.  Counts are recomputed from the same
        server-side eligibility and matching rules used by scheduling.
        """
        request_row = self.con.execute(
            "SELECT * FROM customer_requests WHERE id=?", (request_id,)
        ).fetchone()
        if not request_row:
            raise DomainError("request_not_found", 404)
        request = row_dict(request_row)
        subscriptions_enabled = self.subscription_delays_enabled()
        entitlements = EntitlementService(self.con, now=self.now)
        requested_at = parse_marketplace_datetime(request.get("requested_at")) or self.now
        counts: dict[str, int] = {}
        eligible = 0
        total = 0
        for provider_row in self.con.execute("SELECT * FROM providers"):
            total += 1
            provider = row_dict(provider_row)
            allowed, reason, _ = entitlements.can_receive(
                provider["id"], enforce_subscription=subscriptions_enabled
            )
            if not allowed:
                counts[reason] = counts.get(reason, 0) + 1
                continue
            reason = self.provider_match_reason(
                request,
                provider,
                requested_at=requested_at,
                request_id=request_id,
            )
            if reason:
                counts[reason] = counts.get(reason, 0) + 1
                continue
            eligible += 1
        self._persist_diagnostics(
            request_id,
            total_providers=total,
            eligible_providers=eligible,
            reasons=counts,
        )
        return {
            "requestId": request_id,
            "privacySafe": True,
            "subscriptionsEnabled": subscriptions_enabled,
            "totalProviders": total,
            "eligibleProviders": eligible,
            "excludedProviders": max(0, total - eligible),
            "reasons": dict(sorted(counts.items())),
        }

    def provider_has_capacity(
        self, provider: dict[str, Any], requested_at: datetime, request_id: str
    ) -> bool:
        """Respect an optional per-day capacity without changing legacy profiles."""
        raw_availability = provider.get("availability")
        availability = (
            load(raw_availability, {})
            if isinstance(raw_availability, str)
            else raw_availability or {}
        )
        try:
            daily_capacity = int(availability.get("dailyCapacity") or 0)
        except (TypeError, ValueError):
            daily_capacity = 0
        if daily_capacity <= 0:
            return True
        count = self.con.execute(
            """SELECT COUNT(*) n FROM customer_requests
            WHERE accepted_provider_id=? AND id!=?
            AND CASE
              WHEN COALESCE(NULLIF(requested_at,''),'')='' THEN date(datetime(created_at,'+4 hours'))
              WHEN instr(substr(requested_at,11),'Z')>0
                OR instr(substr(requested_at,11),'+')>0
                OR instr(substr(requested_at,11),'-')>0
                THEN date(datetime(requested_at,'+4 hours'))
              ELSE substr(requested_at,1,10)
            END=?
            AND status IN (
              'accepted','appointmentConfirmed','inProgress','awaitingConfirmation',
              'qualityReview','closed','archived','completed'
            )""",
            (
                provider["id"],
                request_id,
                requested_at.astimezone(OMAN_TZ).date().isoformat(),
            ),
        ).fetchone()["n"]
        return int(count or 0) < daily_capacity

    def _interaction_blocked(self, user_id: str, provider_id: str) -> bool:
        if not user_id or not provider_id:
            return False
        return bool(
            self.con.execute(
                """SELECT 1 FROM interaction_blocks WHERE active=1 AND (
                  (blocker_kind='user' AND blocker_id=? AND blocked_kind='provider'
                    AND blocked_id=?)
                  OR
                  (blocker_kind='provider' AND blocker_id=? AND blocked_kind='user'
                    AND blocked_id=?)
                ) LIMIT 1""",
                (user_id, provider_id, provider_id, user_id),
            ).fetchone()
        )

    def _mark_dispatch_stale(self, dispatch_id: str) -> None:
        self.con.execute(
            """UPDATE request_dispatches SET status='stale',
            notified_at='',updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND status='scheduled'""",
            (dispatch_id,),
        )

    def _request_is_dispatchable(self, request: dict[str, Any]) -> bool:
        if str(request.get("status") or "") in self.TERMINAL_REQUEST_STATUSES:
            return False
        if str(request.get("accepted_provider_id") or "").strip():
            return False
        return int(request.get("offers_open", 1) or 0) == 1

    def _release_candidate_is_eligible(
        self,
        request: dict[str, Any],
        provider_id: str,
        *,
        enforce_subscription: bool,
    ) -> bool:
        """Re-run every intake gate against rows loaded at dispatch time."""
        allowed, _, _ = EntitlementService(self.con, now=self.now).can_receive(
            provider_id, enforce_subscription=enforce_subscription
        )
        if not allowed:
            return False
        # can_receive may synchronize an expired subscription and its provider
        # flags, so match only against the canonical post-sync provider row.
        provider_row = self.con.execute(
            "SELECT * FROM providers WHERE id=?", (provider_id,)
        ).fetchone()
        if not provider_row:
            return False
        provider = row_dict(provider_row)
        if str(provider.get("lifecycle_state") or "active") in {
            "suspended",
            "archived",
            "deleted",
        }:
            return False
        requested_at = (
            parse_marketplace_datetime(request.get("requested_at")) or self.now
        )
        if self.provider_match_reason(
            request,
            provider,
            requested_at=requested_at,
            request_id=str(request.get("id") or ""),
        ):
            return False
        raw_declined = request.get("declined_provider_ids")
        declined = (
            load(raw_declined, [])
            if isinstance(raw_declined, str)
            else raw_declined or []
        )
        if provider_id in declined:
            return False
        return not self._interaction_blocked(
            str(request.get("user_id") or ""), provider_id
        )

    def schedule(self, request_id: str) -> list[dict[str, Any]]:
        request_row = self.con.execute("SELECT * FROM customer_requests WHERE id=?", (request_id,)).fetchone()
        if not request_row:
            raise DomainError("request_not_found", 404)
        request = row_dict(request_row)
        entitlements = EntitlementService(self.con, now=self.now)
        apply_plan_delay = self.subscription_delays_enabled()
        requested_at = parse_marketplace_datetime(request.get("requested_at")) or self.now
        ranked: list[dict[str, Any]] = []
        for provider_row in self.con.execute(
            """SELECT * FROM providers WHERE active=1 AND status!='unavailable'
            AND COALESCE(listing_enabled,1)=1 AND COALESCE(request_enabled,1)=1"""
        ):
            provider = row_dict(provider_row)
            allowed, reason, grants = entitlements.can_receive(
                provider["id"], enforce_subscription=apply_plan_delay
            )
            if not allowed:
                continue
            if self.provider_match_reason(
                request,
                provider,
                requested_at=requested_at,
                request_id=request_id,
            ):
                continue
            score, breakdown = RankingService.score(
                request, provider, grants["planId"], requested_at
            )
            if score <= 0:
                continue
            match = RankingService.service_match_details(request, provider)
            ranked.append({
                "providerId": provider["id"],
                "score": score,
                "breakdown": breakdown,
                "matchType": match["kind"],
                "delaySeconds": grants["leadDelaySeconds"] if apply_plan_delay else 0,
                "planId": grants["planId"],
            })
        ranked.sort(key=lambda item: (
            RankingService.MATCH_PRIORITY.get(item["matchType"], 99),
            -item["score"],
            item["providerId"],
        ))
        ranked = ranked[:10]
        self.con.execute(
            "DELETE FROM request_dispatches WHERE request_id=? AND status='scheduled'", (request_id,)
        )
        expansion_at = self.now + timedelta(minutes=self.expansion_minutes)
        for index, item in enumerate(ranked):
            wave = 1 if index < 5 else 2
            base = self.now if wave == 1 else expansion_at
            release = base + timedelta(seconds=item["delaySeconds"])
            self.con.execute(
                """INSERT INTO request_dispatches(
                id,request_id,provider_id,rank,score,score_breakdown,wave,release_at,status)
                VALUES(?,?,?,?,?,?,?,?, 'scheduled')
                ON CONFLICT(request_id,provider_id) DO UPDATE SET rank=excluded.rank,
                score=excluded.score,score_breakdown=excluded.score_breakdown,wave=excluded.wave,
                release_at=excluded.release_at,status=CASE
                WHEN request_dispatches.status IN ('notified','opened','offered','accepted')
                THEN request_dispatches.status ELSE 'scheduled' END""",
                (
                    public_id("dispatch"), request_id, item["providerId"], index + 1,
                    item["score"], dump(item["breakdown"]), wave, iso(release),
                ),
            )
        if ranked:
            self.con.execute(
                """UPDATE customer_requests SET status='matching',marketplace_status='scheduled',
                dispatch_started_at=?,expansion_at=?,ranking_version=?,waitlisted=0,
                updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (iso(self.now), iso(expansion_at), RANKING_VERSION, request_id),
            )
        else:
            self.con.execute(
                """UPDATE customer_requests SET status='unavailable',marketplace_status='awaiting_provider',
                matching_provider_ids='[]',waitlisted=1,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (request_id,),
            )
            # No-match diagnostics are computed only on the failure path and
            # cached as aggregate counts, keeping normal scheduling lightweight.
            self.diagnostics(request_id)
        return ranked

    def release_due(self, request_id: str | None = None) -> list[dict[str, Any]]:
        params: list[Any] = [iso(self.now)]
        if request_id:
            params.append(request_id)
            rows = list(self.con.execute(
                """SELECT d.* FROM request_dispatches d
                WHERE d.status='scheduled' AND d.release_at<=? AND d.request_id=?
                ORDER BY d.request_id,d.rank""",
                params,
            ))
        else:
            rows = list(self.con.execute(
                """SELECT d.* FROM request_dispatches d
                WHERE d.status='scheduled' AND d.release_at<=?
                ORDER BY d.request_id,d.rank""",
                params,
            ))
        released = []
        by_request: dict[str, list[Any]] = {}
        for row in rows:
            by_request.setdefault(row["request_id"], []).append(row)
        for rid, candidates in by_request.items():
            current_request_row = self.con.execute(
                "SELECT * FROM customer_requests WHERE id=?", (rid,)
            ).fetchone()
            if not current_request_row:
                for candidate in candidates:
                    self._mark_dispatch_stale(candidate["id"])
                continue
            current_request = row_dict(current_request_row)
            if not self._request_is_dispatchable(current_request):
                for candidate in candidates:
                    self._mark_dispatch_stale(candidate["id"])
                continue
            current_ids = load(current_request.get("matching_provider_ids"), [])
            if not isinstance(current_ids, list):
                current_ids = []
            for row in candidates:
                # The scheduler's score is only a snapshot. Reload both sides
                # immediately before release and fail closed if either changed.
                request_row = self.con.execute(
                    "SELECT * FROM customer_requests WHERE id=?", (rid,)
                ).fetchone()
                provider_row = self.con.execute(
                    "SELECT * FROM providers WHERE id=?", (row["provider_id"],)
                ).fetchone()
                if not request_row or not provider_row:
                    self._mark_dispatch_stale(row["id"])
                    continue
                request = row_dict(request_row)
                if not self._request_is_dispatchable(request):
                    self._mark_dispatch_stale(row["id"])
                    continue
                if int(row["wave"] or 1) == 2:
                    expansion = parse_datetime(request.get("expansion_at"))
                    offers = load(request.get("offers"), [])
                    if not expansion or self.now < expansion:
                        continue
                    if len(offers) >= self.min_offers:
                        self._mark_dispatch_stale(row["id"])
                        continue
                enforce_subscription = self.subscription_delays_enabled()
                request_row = self.con.execute(
                    "SELECT * FROM customer_requests WHERE id=?", (rid,)
                ).fetchone()
                if not request_row:
                    self._mark_dispatch_stale(row["id"])
                    continue
                request = row_dict(request_row)
                if (
                    not self._request_is_dispatchable(request)
                    or not self._release_candidate_is_eligible(
                        request,
                        row["provider_id"],
                        enforce_subscription=enforce_subscription,
                    )
                ):
                    self._mark_dispatch_stale(row["id"])
                    continue
                self.con.execute(
                    """UPDATE request_dispatches SET status='notified',notified_at=?,
                    updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='scheduled'""",
                    (iso(self.now), row["id"]),
                )
                if self.con.execute("SELECT changes()").fetchone()[0] != 1:
                    continue
                if row["provider_id"] not in current_ids:
                    current_ids.append(row["provider_id"])
                released.append({
                    "requestId": rid,
                    "providerId": row["provider_id"],
                    "rank": row["rank"],
                    "score": row["score"],
                    "serviceName": request.get("service_name")
                    or request.get("service_value"),
                    "area": request.get("wilayah") or request.get("gov"),
                })
            self.con.execute(
                """UPDATE customer_requests SET matching_provider_ids=?,
                marketplace_status=CASE
                  WHEN ? THEN 'notified'
                  WHEN EXISTS(SELECT 1 FROM request_dispatches
                    WHERE request_id=? AND status='scheduled') THEN 'scheduled'
                  ELSE 'awaiting_provider' END,
                status=CASE WHEN NOT ? AND NOT EXISTS(
                  SELECT 1 FROM request_dispatches
                  WHERE request_id=? AND status='scheduled'
                ) AND status='matching' THEN 'unavailable' ELSE status END,
                waitlisted=CASE WHEN NOT ? AND NOT EXISTS(
                  SELECT 1 FROM request_dispatches
                  WHERE request_id=? AND status='scheduled'
                ) THEN 1 ELSE waitlisted END,
                updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (
                    dump(current_ids),
                    bool(current_ids),
                    rid,
                    bool(current_ids),
                    rid,
                    bool(current_ids),
                    rid,
                    rid,
                ),
            )
        return released


class PaymentAdapter:
    def __init__(self, con, *, environment: dict[str, str] | None = None, now: datetime | None = None):
        self.con = con
        self.environment = environment or os.environ
        self.now = now or utcnow()
        self.gateway = self.environment.get("KHADAMATI_PAYMENT_GATEWAY", "manual").strip().lower() or "manual"
        self.checkout_url = self.environment.get("KHADAMATI_PAYMENT_CHECKOUT_URL", "").strip()
        self.webhook_secret = self.environment.get("KHADAMATI_PAYMENT_WEBHOOK_SECRET", "").strip()

    @property
    def configured(self) -> bool:
        return self.gateway not in {"", "manual", "disabled"} and bool(self.checkout_url and self.webhook_secret)

    def create_intent(self, subscription_id: str, provider_id: str, *, client_amount: Any = None) -> dict[str, Any]:
        subscription = self.con.execute(
            "SELECT * FROM subscriptions WHERE id=? AND provider_id=?", (subscription_id, provider_id)
        ).fetchone()
        if not subscription:
            raise DomainError("subscription_not_found", 404)
        expected = as_money(subscription["amount"])
        if client_amount not in (None, "") and as_money(client_amount) != expected:
            raise DomainError("payment_amount_mismatch", 409)
        if subscription["status"] != "pending_payment":
            raise DomainError("subscription_not_waiting_for_payment", 409)
        payment_id = public_id("pay")
        external_id = public_id("checkout")
        self.con.execute(
            """INSERT INTO payments(
            id,provider_id,subscription_id,kind,amount,method,status,note,currency,
            external_id,gateway,metadata,updated_at)
            VALUES(?,?,?,'subscription',?,?, 'pending','',?,?,?,?,CURRENT_TIMESTAMP)""",
            (
                payment_id, provider_id, subscription_id, float(expected),
                self.gateway if self.configured else "manual",
                subscription["currency"] or OMR, external_id,
                self.gateway if self.configured else "manual",
                dump({"serverPriced": True}),
            ),
        )
        result = {
            "paymentId": payment_id,
            "reference": external_id,
            "amount": float(expected),
            "currency": subscription["currency"] or OMR,
            "status": "pending",
            "requiresAdminApproval": not self.configured,
            "gatewayConfigured": self.configured,
        }
        if self.configured:
            separator = "&" if "?" in self.checkout_url else "?"
            result["checkoutUrl"] = f"{self.checkout_url}{separator}reference={external_id}"
        return result

    def verify_webhook(self, raw_body: bytes, signature: str) -> dict[str, Any]:
        if not self.webhook_secret:
            raise DomainError("payment_webhook_not_configured", 503)
        expected = hmac.new(self.webhook_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        supplied = str(signature or "").removeprefix("sha256=")
        if not hmac.compare_digest(expected, supplied):
            raise DomainError("invalid_webhook_signature", 401)
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DomainError("invalid_webhook_payload") from exc
        event_id = str(payload.get("eventId") or "").strip()
        reference = str(payload.get("reference") or "").strip()
        if not event_id or not reference:
            raise DomainError("webhook_reference_required")
        old_event = self.con.execute("SELECT id FROM webhook_events WHERE event_id=?", (event_id,)).fetchone()
        if old_event:
            return {"ok": True, "duplicate": True}
        payment = self.con.execute("SELECT * FROM payments WHERE external_id=?", (reference,)).fetchone()
        if not payment:
            raise DomainError("payment_not_found", 404)
        amount = as_money(payload.get("amount"))
        currency = str(payload.get("currency") or "").upper()
        if amount != as_money(payment["amount"]) or currency != str(payment["currency"] or OMR).upper():
            raise DomainError("payment_amount_mismatch", 409)
        status = str(payload.get("status") or "").lower()
        if status not in {"paid", "failed", "cancelled", "refunded"}:
            raise DomainError("invalid_payment_status")
        current_status = str(payment["status"] or "pending").lower()
        if current_status == "refunded" and status != "refunded":
            raise DomainError("invalid_payment_transition", 409)
        if status == "refunded" and current_status != "paid":
            raise DomainError("invalid_payment_transition", 409)
        if current_status == "paid" and status in {"failed", "cancelled"}:
            raise DomainError("invalid_payment_transition", 409)
        self.con.execute(
            """INSERT INTO webhook_events(
            id,provider,event_id,signature_valid,payload_hash,processed)
            VALUES(?,?,?,?,?,1)""",
            (
                public_id("wh"), self.gateway, event_id, 1,
                hashlib.sha256(raw_body).hexdigest(),
            ),
        )
        verified_at = iso(self.now) if status == "paid" else ""
        self.con.execute(
            """UPDATE payments SET status=?,verified_at=?,failure_code=?,metadata=?,
            updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (
                status, verified_at, str(payload.get("failureCode") or "")[:120],
                dump({"eventId": event_id}), payment["id"],
            ),
        )
        if status == "paid":
            subscription = SubscriptionService(self.con, now=self.now).activate(
                payment["subscription_id"], payment_id=payment["id"], actor=f"webhook:{self.gateway}"
            )
            self._invoice(payment, subscription)
        elif status == "refunded":
            SubscriptionService(self.con, now=self.now).refund(
                payment["subscription_id"], actor=f"webhook:{self.gateway}"
            )
        return {"ok": True, "paymentId": payment["id"], "status": status}

    def confirm_manual(self, payment_id: str, *, actor: str = "admin") -> dict[str, Any]:
        payment = self.con.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()
        if not payment:
            raise DomainError("payment_not_found", 404)
        if payment["status"] == "paid":
            return row_dict(payment)
        if payment["status"] != "pending":
            raise DomainError("invalid_payment_transition", 409)
        self.con.execute(
            "UPDATE payments SET status='paid',verified_at=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (iso(self.now), payment_id),
        )
        subscription = SubscriptionService(self.con, now=self.now).activate(
            payment["subscription_id"], payment_id=payment_id, actor=actor
        )
        self._invoice(payment, subscription)
        return row_dict(self.con.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone())

    def _invoice(self, payment: Any, subscription: dict[str, Any]) -> None:
        number = f"KHA-{self.now.strftime('%Y%m')}-{secrets.token_hex(3).upper()}"
        self.con.execute(
            """INSERT OR IGNORE INTO invoices(
            id,payment_id,subscription_id,provider_id,number,currency,subtotal,total,status,issued_at,paid_at,metadata)
            VALUES(?,?,?,?,?,?,?,?, 'paid',?,?,?)""",
            (
                public_id("inv"), payment["id"], subscription["id"], subscription["provider_id"],
                number, payment["currency"] or OMR, payment["amount"], payment["amount"],
                iso(self.now), iso(self.now), dump({"source": payment["method"]}),
            ),
        )


class OTPService:
    def __init__(
        self,
        con,
        *,
        environment: dict[str, str] | None = None,
        now: datetime | None = None,
        deliver: Callable[[str, str], bool] | None = None,
    ):
        self.con = con
        self.environment = environment or os.environ
        self.now = now or utcnow()
        self.deliver = deliver
        self.app_env = self.environment.get("KHADAMATI_ENV", "development").lower()
        self.pepper = self.environment.get("KHADAMATI_OTP_PEPPER", "")
        self.ttl_minutes = max(2, int(self.environment.get("KHADAMATI_OTP_TTL_MINUTES", "5")))
        self.max_attempts = max(3, int(self.environment.get("KHADAMATI_OTP_MAX_ATTEMPTS", "5")))
        self.hourly_limit = max(1, int(self.environment.get("KHADAMATI_OTP_HOURLY_LIMIT", "5")))

    def request(self, phone: str, purpose: str, target_kind: str = "user") -> dict[str, Any]:
        phone = normalized_phone(phone)
        if len(phone) < 11:
            raise DomainError("valid_phone_required")
        since = iso(self.now - timedelta(hours=1))
        count = self.con.execute(
            "SELECT COUNT(*) n FROM otp_challenges WHERE phone=? AND created_at>=?",
            (phone, since),
        ).fetchone()["n"]
        if int(count or 0) >= self.hourly_limit:
            raise DomainError("otp_rate_limited", 429)
        challenge_id = public_id("otp")
        development_code = self.environment.get("KHADAMATI_DEV_OTP_CODE", "").strip()
        code = development_code if self.app_env != "production" and development_code else f"{secrets.randbelow(1_000_000):06d}"
        expires = self.now + timedelta(minutes=self.ttl_minutes)
        delivery_status = "pending"
        delivered = False
        if self.deliver:
            delivered = bool(self.deliver(phone, code))
            delivery_status = "sent" if delivered else "failed"
        elif self.app_env != "production" and development_code:
            delivered = True
            delivery_status = "development"
        self.con.execute(
            """INSERT INTO otp_challenges(
            id,phone,purpose,target_kind,code_hash,attempts,max_attempts,expires_at,delivery_status)
            VALUES(?,?,?,?,?,0,?,?,?)""",
            (
                challenge_id, phone, purpose[:80], target_kind[:40],
                self._hash(challenge_id, code), self.max_attempts, iso(expires), delivery_status,
            ),
        )
        if not delivered:
            raise DomainError("otp_delivery_unavailable", 503)
        result = {
            "challengeId": challenge_id,
            "expiresInSeconds": self.ttl_minutes * 60,
            "maxAttempts": self.max_attempts,
            "delivery": delivery_status,
        }
        if self.app_env != "production" and development_code:
            result["developmentCode"] = code
        return result

    def verify(self, challenge_id: str, code: str) -> dict[str, Any]:
        row = self.con.execute("SELECT * FROM otp_challenges WHERE id=?", (challenge_id,)).fetchone()
        if not row:
            raise DomainError("otp_not_found", 404)
        if row["verified_at"]:
            raise DomainError("otp_already_used", 409)
        if int(row["attempts"] or 0) >= int(row["max_attempts"] or self.max_attempts):
            raise DomainError("otp_attempts_exceeded", 429)
        expires = parse_datetime(row["expires_at"])
        if not expires or expires < self.now:
            raise DomainError("otp_expired", 410)
        if not hmac.compare_digest(self._hash(challenge_id, str(code)), row["code_hash"]):
            self.con.execute("UPDATE otp_challenges SET attempts=attempts+1 WHERE id=?", (challenge_id,))
            raise DomainError("otp_invalid", 403)
        self.con.execute("UPDATE otp_challenges SET verified_at=? WHERE id=?", (iso(self.now), challenge_id))
        return {"ok": True, "phone": row["phone"], "purpose": row["purpose"], "targetKind": row["target_kind"]}

    def _hash(self, challenge_id: str, code: str) -> str:
        return hashlib.sha256(f"{challenge_id}|{code}|{self.pepper}".encode("utf-8")).hexdigest()


def run_subscription_migration_v1(con) -> dict[str, Any]:
    existing = con.execute("SELECT value FROM settings WHERE key=?", (MIGRATION_KEY,)).fetchone()
    if existing:
        return load(existing["value"], {"alreadyApplied": True})
    PlanCatalog.seed(con)

    def mapped_plan(old_plan: str, provider_type: str) -> str:
        account_type = "company" if provider_type == "company" else "individual"
        if old_plan in PLAN_IDS:
            plan = PlanCatalog.get(con, old_plan, False)
            if PlanCatalog.supports_account(plan, account_type):
                return old_plan
        existing_plan = PlanCatalog.get(con, old_plan, False) if old_plan else None
        if existing_plan and not existing_plan.get("legacy") and PlanCatalog.supports_account(existing_plan, account_type):
            return old_plan
        if old_plan in {"foundation_12m", "intro", "intro_90"}:
            return f"{account_type}_free_3m"
        if old_plan in {"professional_12m", "featured_90", "local_visibility", "service_priority", "plus"}:
            return f"{account_type}_gold_6m"
        if old_plan in {"business_12m", "company_year"}:
            return "company_gold_6m" if account_type == "company" else "individual_gold_6m"
        if old_plan in {"company_growth", "growth"}:
            return f"{account_type}_elite_6m"
        return f"{account_type}_silver_6m"

    mapped_subscriptions = 0
    for row in list(con.execute(
        """SELECT s.id,s.package_id,s.status,COALESCE(p.provider_type,'individual') provider_type
        FROM subscriptions s LEFT JOIN providers p ON p.id=s.provider_id"""
    )):
        old_plan = row["package_id"]
        new_plan = mapped_plan(old_plan, row["provider_type"])
        old_status = str(row["status"] or "pending")
        status_map = {
            "pending": "pending_payment",
            "near_expiry": "expiring",
            "near-end": "expiring",
            "stopped": "suspended",
            "inactive": "expired",
        }
        new_status = status_map.get(old_status, old_status if old_status in SUBSCRIPTION_STATES else "active")
        migrated_plan = PlanCatalog.get(con, new_plan, False) or {}
        if int(migrated_plan.get("foundation_once") or 0) and new_status in {"active", "foundation"}:
            new_status = "foundation"
        con.execute(
            """UPDATE subscriptions SET package_id=?,legacy_package_id=CASE
            WHEN legacy_package_id='' THEN ? ELSE legacy_package_id END,status=?,currency='OMR',
            grace_days=COALESCE(grace_days,14),updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (new_plan, old_plan if old_plan != new_plan else "", new_status, row["id"]),
        )
        mapped_subscriptions += 1
    created_subscriptions = 0
    today = utcnow()
    for provider in list(con.execute("SELECT * FROM providers")):
        old_plan = str(provider["package_id"] or "")
        plan_id = mapped_plan(old_plan, str(provider["provider_type"] or "individual"))
        con.execute("UPDATE providers SET package_id=? WHERE id=?", (plan_id, provider["id"]))
        subscription = con.execute(
            "SELECT id FROM subscriptions WHERE provider_id=? ORDER BY created_at DESC LIMIT 1",
            (provider["id"],),
        ).fetchone()
        if subscription:
            continue
        plan = PlanCatalog.get(con, plan_id, False)
        start = parse_datetime(provider["subscription_start"]) or parse_datetime(provider["created_at"]) or today
        end = parse_datetime(provider["subscription_until"]) or (start + timedelta(days=int(plan["duration_days"])))
        state = "foundation" if int(plan.get("foundation_once") or 0) else "active"
        if end < today - timedelta(days=14):
            state = "expired"
        elif end < today:
            state = "grace"
        elif (end.date() - today.date()).days <= 14:
            state = "expiring"
        subscription_id = public_id("subm")
        con.execute(
            """INSERT INTO subscriptions(
            id,provider_id,package_id,amount,status,start_date,end_date,note,currency,
            grace_days,legacy_package_id,activated_at,grace_until,metadata,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
            (
                subscription_id, provider["id"], plan_id, plan["price"], state,
                start.date().isoformat(), end.date().isoformat(), "Migrated from provider profile",
                OMR, 14, old_plan if old_plan != plan_id else "", iso(start),
                (end + timedelta(days=14)).date().isoformat(), dump({"migration": MIGRATION_KEY}),
            ),
        )
        if int(plan.get("foundation_once") or 0) and int(provider["verified"] or 0):
            phone = normalized_phone(provider["phone"])
            commercial = str(provider["commercial_no"] or "").strip().casefold()
            fingerprint = hashlib.sha256(f"{phone}|{commercial}".encode("utf-8")).hexdigest()
            con.execute(
                """INSERT OR IGNORE INTO foundation_claims(
                id,provider_id,phone,commercial_no,fingerprint,subscription_id)
                VALUES(?,?,?,?,?,?)""",
                (public_id("fndm"), provider["id"], phone, commercial, fingerprint, subscription_id),
            )
        created_subscriptions += 1
    migrated_consents = 0
    consent_service = ContactConsentService(con)
    for request in list(con.execute(
        """SELECT id,user_id,accepted_provider_id,contact_consent FROM customer_requests
        WHERE COALESCE(accepted_provider_id,'')!=''"""
    )):
        legacy = load(request["contact_consent"], {})
        for channel in ContactConsentService.CHANNELS:
            if legacy.get(channel):
                try:
                    consent_service.set_channel(
                        request["id"], request["user_id"], request["accepted_provider_id"], channel, True
                    )
                    migrated_consents += 1
                except DomainError:
                    pass
    changes = SubscriptionService(con).synchronize_all()
    summary = {
        "version": 2,
        "completedAt": iso(),
        "mappedSubscriptions": mapped_subscriptions,
        "createdSubscriptions": created_subscriptions,
        "migratedConsents": migrated_consents,
        "accessChanges": len(changes),
        "activePlans": list(PLAN_IDS),
    }
    con.execute("INSERT INTO settings(key,value) VALUES(?,?)", (MIGRATION_KEY, dump(summary)))
    return summary
