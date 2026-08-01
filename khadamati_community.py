"""Server-owned community listings for Khadamati.

The community combines provider packages, customer wanted posts, and the
existing request marketplace without duplicating customer request or chat
state. This module owns listing validation, moderation, quotas, offers,
favorites, reports, expiry, and idempotent package orders.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import math
import sqlite3
from typing import Any

from khadamati_domain import DomainError


LISTING_KINDS = {"wanted", "package"}
LISTING_STATUSES = {
    "draft",
    "pending_review",
    "pending_payment",
    "active",
    "paused",
    "closed",
    "expired",
    "rejected",
    "archived",
    "deleted",
}
BILLING_PERIODS = {"one_time", "daily", "monthly", "yearly"}
CONTACT_CHANNELS = {"app", "whatsapp"}
DEFAULT_SETTINGS = {
    "communityEnabled": True,
    "communityPackagesEnabled": True,
    "communityBoardEnabled": True,
    "communityProviderOffersEnabled": True,
    "communityUserRecommendationsEnabled": True,
    "communityModerationRequired": False,
    "communityWantedExpiryDays": 30,
    "communityPackageExpiryDays": 30,
    "communityFirstPackageFreeDays": 30,
    "communityRenewalFee": 2.0,
    "communityPlanQuotas": {
        "foundation_12m": 1,
        "basic_6m": 1,
        "basic_12m": 2,
        "professional_12m": 4,
        "business_12m": 8,
    },
    "communityPlanFreeMonths": {
        "foundation_12m": 1,
        "basic_6m": 1,
        "basic_12m": 1,
        "professional_12m": 2,
        "business_12m": 3,
    },
}


def install_community_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS community_listings(
          id TEXT PRIMARY KEY,
          kind TEXT NOT NULL,
          owner_kind TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          title TEXT NOT NULL,
          description TEXT DEFAULT '',
          category_id TEXT NOT NULL,
          service_value TEXT NOT NULL,
          budget_min REAL NOT NULL DEFAULT 0,
          budget_max REAL NOT NULL DEFAULT 0,
          price_amount REAL NOT NULL DEFAULT 0,
          billing_period TEXT NOT NULL DEFAULT 'one_time',
          duration_text TEXT DEFAULT '',
          gov TEXT DEFAULT '',
          wilayah TEXT DEFAULT '',
          latitude REAL,
          longitude REAL,
          location_text TEXT DEFAULT '',
          image_path TEXT DEFAULT '',
          details TEXT NOT NULL DEFAULT '{}',
          contact_channels TEXT NOT NULL DEFAULT '["app"]',
          status TEXT NOT NULL DEFAULT 'draft',
          moderation_note TEXT DEFAULT '',
          billing_status TEXT NOT NULL DEFAULT 'included',
          featured INTEGER NOT NULL DEFAULT 0,
          expires_at TEXT DEFAULT '',
          published_at TEXT DEFAULT '',
          closed_at TEXT DEFAULT '',
          deleted_at TEXT DEFAULT '',
          request_id TEXT DEFAULT '',
          idempotency_key TEXT NOT NULL,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(owner_kind,owner_id,idempotency_key)
        );
        CREATE TABLE IF NOT EXISTS community_offers(
          id TEXT PRIMARY KEY,
          listing_id TEXT NOT NULL,
          provider_id TEXT NOT NULL,
          amount REAL NOT NULL DEFAULT 0,
          duration_text TEXT DEFAULT '',
          note TEXT DEFAULT '',
          status TEXT NOT NULL DEFAULT 'sent',
          request_id TEXT DEFAULT '',
          idempotency_key TEXT NOT NULL,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(listing_id,provider_id),
          UNIQUE(provider_id,idempotency_key)
        );
        CREATE TABLE IF NOT EXISTS community_orders(
          id TEXT PRIMARY KEY,
          listing_id TEXT NOT NULL,
          user_id TEXT NOT NULL,
          request_id TEXT DEFAULT '',
          status TEXT NOT NULL DEFAULT 'created',
          snapshot TEXT NOT NULL DEFAULT '{}',
          idempotency_key TEXT NOT NULL,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(user_id,idempotency_key)
        );
        CREATE TABLE IF NOT EXISTS community_favorites(
          id TEXT PRIMARY KEY,
          account_kind TEXT NOT NULL,
          account_id TEXT NOT NULL,
          listing_id TEXT NOT NULL,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(account_kind,account_id,listing_id)
        );
        CREATE TABLE IF NOT EXISTS community_reports(
          id TEXT PRIMARY KEY,
          listing_id TEXT NOT NULL,
          reporter_kind TEXT NOT NULL,
          reporter_id TEXT NOT NULL,
          reason TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'open',
          resolution TEXT DEFAULT '',
          created_at TEXT DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(listing_id,reporter_kind,reporter_id)
        );
        CREATE TABLE IF NOT EXISTS community_events(
          id TEXT PRIMARY KEY,
          listing_id TEXT NOT NULL,
          actor_kind TEXT NOT NULL,
          actor_id TEXT DEFAULT '',
          action TEXT NOT NULL,
          detail TEXT NOT NULL DEFAULT '{}',
          created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_community_feed
          ON community_listings(kind,status,featured,created_at);
        CREATE INDEX IF NOT EXISTS idx_community_owner
          ON community_listings(owner_kind,owner_id,status,updated_at);
        CREATE INDEX IF NOT EXISTS idx_community_service
          ON community_listings(service_value,status,expires_at);
        CREATE INDEX IF NOT EXISTS idx_community_expiry
          ON community_listings(status,expires_at);
        CREATE INDEX IF NOT EXISTS idx_community_offer_listing
          ON community_offers(listing_id,status,updated_at);
        CREATE INDEX IF NOT EXISTS idx_community_reports_status
          ON community_reports(status,created_at);
        """
    )


def _clean(value: Any, limit: int = 240) -> str:
    return str(value or "").strip()[:limit]


def _json_load(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback
    return parsed if isinstance(parsed, type(fallback)) else fallback


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _number(value: Any, *, minimum: float = 0, maximum: float = 1_000_000) -> float:
    try:
        result = float(value or 0)
    except (TypeError, ValueError) as error:
        raise DomainError("invalid_community_amount", 400) from error
    if not math.isfinite(result) or result < minimum or result > maximum:
        raise DomainError("invalid_community_amount", 400)
    return round(result, 3)


def _integer(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    return min(maximum, max(minimum, result))


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _parse_datetime(value: Any) -> datetime | None:
    text = _clean(value, 60)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise DomainError("invalid_community_date", 400) from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _iso(value: datetime | None) -> str:
    return value.isoformat() if value else ""


def community_settings(con: sqlite3.Connection) -> dict[str, Any]:
    row = con.execute("SELECT value FROM settings WHERE key='platform'").fetchone()
    platform = _json_load(row["value"], {}) if row else {}
    result = dict(DEFAULT_SETTINGS)
    for key in DEFAULT_SETTINGS:
        if key in platform:
            result[key] = platform[key]
    result["communityEnabled"] = _bool(result.get("communityEnabled"), True)
    result["communityPackagesEnabled"] = _bool(
        result.get("communityPackagesEnabled"), True
    )
    result["communityBoardEnabled"] = _bool(
        result.get("communityBoardEnabled"), True
    )
    result["communityProviderOffersEnabled"] = _bool(
        result.get("communityProviderOffersEnabled"), True
    )
    result["communityUserRecommendationsEnabled"] = _bool(
        result.get("communityUserRecommendationsEnabled"), True
    )
    result["communityModerationRequired"] = _bool(
        result.get("communityModerationRequired"), False
    )
    result["communityWantedExpiryDays"] = _integer(
        result.get("communityWantedExpiryDays"), 30, minimum=1, maximum=90
    )
    result["communityPackageExpiryDays"] = _integer(
        result.get("communityPackageExpiryDays"), 30, minimum=1, maximum=365
    )
    result["communityFirstPackageFreeDays"] = _integer(
        result.get("communityFirstPackageFreeDays"), 30, minimum=1, maximum=365
    )
    result["communityRenewalFee"] = _number(
        result.get("communityRenewalFee"), minimum=0, maximum=10_000
    )
    result["communityPlanQuotas"] = {
        _clean(key, 80): _integer(value, 1, minimum=0, maximum=100)
        for key, value in _json_load(result.get("communityPlanQuotas"), {}).items()
    } or dict(DEFAULT_SETTINGS["communityPlanQuotas"])
    result["communityPlanFreeMonths"] = {
        _clean(key, 80): _integer(value, 0, minimum=0, maximum=24)
        for key, value in _json_load(result.get("communityPlanFreeMonths"), {}).items()
    } or dict(DEFAULT_SETTINGS["communityPlanFreeMonths"])
    return result


def save_community_settings(
    con: sqlite3.Connection, payload: dict[str, Any]
) -> dict[str, Any]:
    row = con.execute("SELECT value FROM settings WHERE key='platform'").fetchone()
    platform = _json_load(row["value"], {}) if row else {}
    current = community_settings(con)
    current.update(
        {
            "communityEnabled": _bool(
                payload.get("communityEnabled"), current["communityEnabled"]
            ),
            "communityPackagesEnabled": _bool(
                payload.get("communityPackagesEnabled"),
                current["communityPackagesEnabled"],
            ),
            "communityBoardEnabled": _bool(
                payload.get("communityBoardEnabled"),
                current["communityBoardEnabled"],
            ),
            "communityProviderOffersEnabled": _bool(
                payload.get("communityProviderOffersEnabled"),
                current["communityProviderOffersEnabled"],
            ),
            "communityUserRecommendationsEnabled": _bool(
                payload.get("communityUserRecommendationsEnabled"),
                current["communityUserRecommendationsEnabled"],
            ),
            "communityModerationRequired": _bool(
                payload.get("communityModerationRequired"),
                current["communityModerationRequired"],
            ),
            "communityWantedExpiryDays": _integer(
                payload.get(
                    "communityWantedExpiryDays",
                    current["communityWantedExpiryDays"],
                ),
                current["communityWantedExpiryDays"],
                minimum=1,
                maximum=90,
            ),
            "communityPackageExpiryDays": _integer(
                payload.get(
                    "communityPackageExpiryDays",
                    current["communityPackageExpiryDays"],
                ),
                current["communityPackageExpiryDays"],
                minimum=1,
                maximum=365,
            ),
            "communityFirstPackageFreeDays": _integer(
                payload.get(
                    "communityFirstPackageFreeDays",
                    current["communityFirstPackageFreeDays"],
                ),
                current["communityFirstPackageFreeDays"],
                minimum=1,
                maximum=365,
            ),
            "communityRenewalFee": _number(
                payload.get("communityRenewalFee", current["communityRenewalFee"]),
                minimum=0,
                maximum=10_000,
            ),
        }
    )
    for key in ("communityPlanQuotas", "communityPlanFreeMonths"):
        if isinstance(payload.get(key), dict):
            maximum = 100 if key == "communityPlanQuotas" else 24
            current[key] = {
                _clean(plan_id, 80): _integer(value, 0, minimum=0, maximum=maximum)
                for plan_id, value in payload[key].items()
            }
    platform.update(current)
    con.execute(
        """INSERT INTO settings(key,value) VALUES('platform',?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
        (_json_dump(platform),),
    )
    return current


def run_community_maintenance(
    con: sqlite3.Connection, *, now: datetime | None = None
) -> dict[str, list[dict[str, Any]]]:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    expired_rows = con.execute(
        """
        SELECT id,kind,owner_kind,owner_id,title FROM community_listings
        WHERE status='active' AND COALESCE(expires_at,'')!='' AND expires_at<=?
        """,
        (_iso(now),),
    ).fetchall()
    for row in expired_rows:
        con.execute(
            """UPDATE community_listings SET status='expired',
            updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='active'""",
            (row["id"],),
        )
        con.execute(
            """INSERT OR IGNORE INTO community_events(
            id,listing_id,actor_kind,actor_id,action,detail)
            VALUES(?,?, 'system','', 'expired','{}')""",
            (f"community-expired-{row['id']}", row["id"]),
        )
    warning_before = _iso(now + timedelta(days=3))
    warning_rows = con.execute(
        """
        SELECT l.id,l.kind,l.owner_kind,l.owner_id,l.title,l.expires_at
        FROM community_listings l
        LEFT JOIN community_events e
          ON e.listing_id=l.id AND e.action='expiry_warning'
        WHERE l.status='active' AND COALESCE(l.expires_at,'')!=''
          AND l.expires_at>? AND l.expires_at<=? AND e.id IS NULL
        """,
        (_iso(now), warning_before),
    ).fetchall()
    for row in warning_rows:
        con.execute(
            """INSERT OR IGNORE INTO community_events(
            id,listing_id,actor_kind,actor_id,action,detail)
            VALUES(?,?, 'system','', 'expiry_warning',?)""",
            (
                f"community-warning-{row['id']}",
                row["id"],
                _json_dump({"expiresAt": row["expires_at"]}),
            ),
        )
    return {
        "expired": [dict(row) for row in expired_rows],
        "warnings": [dict(row) for row in warning_rows],
    }


class CommunityService:
    def __init__(self, con: sqlite3.Connection, *, now: datetime | None = None):
        self.con = con
        self.now = (now or datetime.now(UTC)).astimezone(UTC)

    def snapshot(self, session: dict[str, Any] | None) -> dict[str, Any]:
        run_community_maintenance(self.con, now=self.now)
        kind = (session or {}).get("kind", "")
        account_id = (
            (session or {}).get("userId")
            or (session or {}).get("providerId")
            or ""
        )
        is_admin = kind == "admin"
        where = ["COALESCE(l.deleted_at,'')=''"]
        params: list[Any] = []
        if not is_admin:
            owner_kind = "user" if kind == "user" else ""
            if kind == "provider":
                provider = self.con.execute(
                    "SELECT provider_type FROM providers WHERE id=?", (account_id,)
                ).fetchone()
                owner_kind = (
                    "company"
                    if provider and provider["provider_type"] == "company"
                    else "provider"
                )
            if account_id:
                where.append(
                    "(l.status='active' OR (l.owner_id=? AND l.owner_kind=?))"
                )
                params.extend([account_id, owner_kind])
            else:
                where.append("l.status='active'")
        rows = self.con.execute(
            f"""
            SELECT l.* FROM community_listings l
            WHERE {' AND '.join(where)}
            ORDER BY l.featured DESC,
              CASE l.status WHEN 'active' THEN 0 WHEN 'pending_review' THEN 1
                WHEN 'draft' THEN 2 ELSE 3 END,
              l.updated_at DESC,l.created_at DESC
            LIMIT 500
            """,
            tuple(params),
        ).fetchall()
        listings = [
            self.serialize(
                row,
                session=session,
                include_admin=is_admin,
            )
            for row in rows
        ]
        settings = community_settings(self.con)
        safe_settings = {
            key: settings[key]
            for key in (
                "communityEnabled",
                "communityModerationRequired",
                "communityWantedExpiryDays",
                "communityPackageExpiryDays",
                "communityFirstPackageFreeDays",
                "communityRenewalFee",
            )
        }
        result = {
            "listings": listings,
            "settings": settings if is_admin else safe_settings,
            "favorites": [],
            "stats": {},
        }
        if account_id and kind in {"user", "provider"}:
            result["favorites"] = [
                row["listing_id"]
                for row in self.con.execute(
                    """SELECT listing_id FROM community_favorites
                    WHERE account_kind=? AND account_id=?""",
                    (kind, account_id),
                )
            ]
        if is_admin:
            result["reports"] = [
                {
                    "id": row["id"],
                    "listingId": row["listing_id"],
                    "reporterKind": row["reporter_kind"],
                    "reporterId": row["reporter_id"],
                    "reason": row["reason"],
                    "status": row["status"],
                    "resolution": row["resolution"],
                    "createdAt": row["created_at"],
                    "updatedAt": row["updated_at"],
                }
                for row in self.con.execute(
                    "SELECT * FROM community_reports ORDER BY created_at DESC LIMIT 300"
                )
            ]
            result["stats"] = self.stats()
        return result

    def _require_enabled(self) -> None:
        if not community_settings(self.con)["communityEnabled"]:
            raise DomainError("community_disabled", 409)

    def serialize(
        self,
        row: sqlite3.Row | dict[str, Any],
        *,
        session: dict[str, Any] | None = None,
        include_admin: bool = False,
    ) -> dict[str, Any]:
        data = dict(row)
        owner = self._owner(data)
        actor_kind = (session or {}).get("kind", "")
        actor_id = (
            (session or {}).get("userId")
            or (session or {}).get("providerId")
            or ""
        )
        owner_match = actor_id == data["owner_id"] and (
            actor_kind == "user"
            or actor_kind == "provider"
        )
        offers = []
        if data["kind"] == "wanted" and (include_admin or owner_match):
            offers = [
                self._offer(row_)
                for row_ in self.con.execute(
                    """SELECT * FROM community_offers
                    WHERE listing_id=? ORDER BY updated_at DESC""",
                    (data["id"],),
                )
            ]
        elif data["kind"] == "wanted" and actor_kind == "provider":
            own_offer = self.con.execute(
                """SELECT * FROM community_offers
                WHERE listing_id=? AND provider_id=?""",
                (data["id"], actor_id),
            ).fetchone()
            offers = [self._offer(own_offer)] if own_offer else []
        item = {
            "id": data["id"],
            "kind": data["kind"],
            "ownerKind": data["owner_kind"],
            "ownerId": data["owner_id"],
            "owner": owner,
            "title": data["title"],
            "description": data["description"],
            "categoryId": data["category_id"],
            "serviceValue": data["service_value"],
            "budgetMin": float(data["budget_min"] or 0),
            "budgetMax": float(data["budget_max"] or 0),
            "priceAmount": float(data["price_amount"] or 0),
            "billingPeriod": data["billing_period"],
            "durationText": data["duration_text"],
            "gov": data["gov"],
            "wilayah": data["wilayah"],
            "location": (
                {"lat": data["latitude"], "lng": data["longitude"]}
                if data["latitude"] is not None and data["longitude"] is not None
                else None
            ),
            "locationText": data["location_text"],
            "imagePath": data["image_path"],
            "details": _json_load(data["details"], {}),
            "contactChannels": _json_load(data["contact_channels"], ["app"]),
            "status": data["status"],
            "billingStatus": data["billing_status"],
            "featured": bool(data["featured"]),
            "expiresAt": data["expires_at"],
            "publishedAt": data["published_at"],
            "closedAt": data["closed_at"],
            "requestId": data["request_id"],
            "createdAt": data["created_at"],
            "updatedAt": data["updated_at"],
            "offerCount": int(
                self.con.execute(
                    "SELECT COUNT(*) n FROM community_offers WHERE listing_id=?",
                    (data["id"],),
                ).fetchone()["n"]
            ),
            "offers": offers,
            "mine": owner_match,
        }
        if include_admin:
            item["moderationNote"] = data["moderation_note"]
            item["reportCount"] = int(
                self.con.execute(
                    """SELECT COUNT(*) n FROM community_reports
                    WHERE listing_id=? AND status='open'""",
                    (data["id"],),
                ).fetchone()["n"]
            )
        return item

    def save(
        self,
        session: dict[str, Any],
        payload: dict[str, Any],
        *,
        listing_id: str,
        image_path: str = "",
    ) -> dict[str, Any]:
        self._require_enabled()
        actor_kind = session.get("kind", "")
        actor_id = session.get("userId") or session.get("providerId") or ""
        listing_kind = _clean(payload.get("kind"), 20)
        if listing_kind not in LISTING_KINDS:
            raise DomainError("invalid_community_listing_kind", 400)
        settings = community_settings(self.con)
        if listing_kind == "wanted" and not settings["communityBoardEnabled"]:
            raise DomainError("community_board_disabled", 409)
        if listing_kind == "package" and not settings["communityPackagesEnabled"]:
            raise DomainError("community_packages_disabled", 409)
        if listing_kind == "wanted" and actor_kind != "user":
            raise DomainError("community_user_required", 403)
        if listing_kind == "package" and actor_kind != "provider":
            raise DomainError("community_provider_required", 403)
        existing = None
        requested_id = _clean(payload.get("id"), 120)
        if requested_id:
            existing = self.con.execute(
                "SELECT * FROM community_listings WHERE id=?", (requested_id,)
            ).fetchone()
            if not existing:
                raise DomainError("community_listing_not_found", 404)
            if existing["owner_id"] != actor_id:
                raise DomainError("community_listing_access_denied", 403)
            if existing["kind"] != listing_kind:
                raise DomainError("community_listing_kind_locked", 409)
            listing_id = requested_id
        title = _clean(payload.get("title"), 140)
        description = _clean(payload.get("description"), 1200)
        if len(title) < 3:
            raise DomainError("community_title_required", 400)
        if listing_kind == "wanted" and len(title.split()) > 3:
            raise DomainError("community_title_too_long", 400)
        if len(description) < 3:
            raise DomainError("community_description_required", 400)
        service_value = _clean(payload.get("serviceValue"), 180)
        if "|" not in service_value:
            raise DomainError("service_required", 400)
        category_id, service_id = service_value.split("|", 1)
        service = self.con.execute(
            """SELECT s.id FROM services s JOIN categories c ON c.id=s.category_id
            WHERE s.id=? AND s.category_id=? AND s.active=1 AND c.active=1
              AND COALESCE(s.deleted_at,'')='' AND COALESCE(c.deleted_at,'')=''""",
            (_clean(service_id, 80), _clean(category_id, 80)),
        ).fetchone()
        if not service:
            raise DomainError("service_not_found", 400)
        owner_kind = "user"
        provider = None
        if actor_kind == "provider":
            provider = self.con.execute(
                """SELECT * FROM providers WHERE id=? AND active=1 AND verified=1
                AND status NOT IN ('deleted','unavailable') AND COALESCE(listing_enabled,1)=1""",
                (actor_id,),
            ).fetchone()
            if not provider:
                raise DomainError("community_provider_not_eligible", 403)
            owner_kind = (
                "company" if provider["provider_type"] == "company" else "provider"
            )
            services = _json_load(provider["services"], [])
            if not any(
                item.get("active", True)
                and item.get("catId") == category_id
                and item.get("serviceId") == service_id
                for item in services
            ):
                raise DomainError("community_service_outside_provider_profile", 403)
            if not existing:
                self._check_package_quota(provider)
        budget_min = _number(payload.get("budgetMin"))
        budget_max = _number(payload.get("budgetMax"))
        if budget_max and budget_min > budget_max:
            raise DomainError("invalid_budget_range", 400)
        price_amount = _number(payload.get("priceAmount"))
        billing_period = _clean(payload.get("billingPeriod") or "one_time", 20)
        if billing_period not in BILLING_PERIODS:
            raise DomainError("invalid_billing_period", 400)
        if listing_kind == "package" and price_amount <= 0:
            raise DomainError("community_package_price_required", 400)
        channels = [
            value
            for value in payload.get("contactChannels", ["app"])
            if value in CONTACT_CHANNELS
        ] if isinstance(payload.get("contactChannels", ["app"]), list) else ["app"]
        if "app" not in channels:
            channels.insert(0, "app")
        details = payload.get("details") if isinstance(payload.get("details"), dict) else {}
        details = {
            "inclusions": [
                _clean(item, 120)
                for item in details.get("inclusions", [])
                if _clean(item, 120)
            ][:10],
            "commitment": _clean(details.get("commitment"), 300),
            "fulfillment": _clean(details.get("fulfillment"), 160),
        }
        settings = community_settings(self.con)
        max_days = 30 if listing_kind == "wanted" else 365
        default_days = (
            settings["communityWantedExpiryDays"]
            if listing_kind == "wanted"
            else settings["communityPackageExpiryDays"]
        )
        duration_days = _integer(
            payload.get("listingDays"), default_days, minimum=1, maximum=max_days
        )
        publish = _bool(payload.get("publish"), True)
        if not publish:
            status = "draft"
        elif settings["communityModerationRequired"]:
            status = "pending_review"
        else:
            status = "active"
        if existing and existing["status"] == "rejected" and publish:
            status = "pending_review"
        if (
            existing
            and existing["billing_status"] == "pending_payment"
            and publish
        ):
            status = "pending_payment"
        expires_at = _iso(self.now + timedelta(days=duration_days)) if publish else ""
        published_at = (
            existing["published_at"]
            if existing and existing["published_at"]
            else (_iso(self.now) if publish else "")
        )
        idempotency_key = _clean(
            payload.get("idempotencyKey")
            or (existing["idempotency_key"] if existing else listing_id),
            160,
        )
        location = payload.get("location") if isinstance(payload.get("location"), dict) else {}
        lat = self._coordinate(location.get("lat"), -90, 90)
        lng = self._coordinate(location.get("lng"), -180, 180)
        billing_status = existing["billing_status"] if existing else "included"
        if listing_kind == "package" and not existing:
            historic_count = int(
                self.con.execute(
                    """SELECT COUNT(*) n FROM community_listings
                    WHERE owner_id=? AND kind='package'""",
                    (actor_id,),
                ).fetchone()["n"]
            )
            free_months = _integer(
                settings["communityPlanFreeMonths"].get(
                    provider["package_id"] or "foundation_12m", 0
                ),
                0,
                minimum=0,
                maximum=24,
            )
            subscription_start = None
            start_text = provider["subscription_start"] or ""
            if not start_text:
                subscription = self.con.execute(
                    """SELECT start_date FROM subscriptions
                    WHERE provider_id=? AND status='active'
                    ORDER BY COALESCE(start_date,created_at) DESC LIMIT 1""",
                    (actor_id,),
                ).fetchone()
                start_text = subscription["start_date"] if subscription else ""
            if start_text:
                try:
                    subscription_start = _parse_datetime(start_text)
                except DomainError:
                    subscription_start = None
            in_plan_free_period = bool(
                subscription_start
                and free_months > 0
                and self.now
                < subscription_start + timedelta(days=30 * free_months)
            )
            if historic_count == 0:
                billing_status = "free_first"
            elif in_plan_free_period:
                billing_status = "plan_included"
            elif settings["communityRenewalFee"] > 0:
                billing_status = "pending_payment"
                if publish:
                    status = "pending_payment"
            else:
                billing_status = "included"
            if historic_count == 0:
                expires_at = _iso(
                    self.now
                    + timedelta(days=settings["communityFirstPackageFreeDays"])
                )
        self.con.execute(
            """
            INSERT INTO community_listings(
              id,kind,owner_kind,owner_id,title,description,category_id,
              service_value,budget_min,budget_max,price_amount,billing_period,
              duration_text,gov,wilayah,latitude,longitude,location_text,
              image_path,details,contact_channels,status,billing_status,
              expires_at,published_at,idempotency_key
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
              title=excluded.title,description=excluded.description,
              category_id=excluded.category_id,service_value=excluded.service_value,
              budget_min=excluded.budget_min,budget_max=excluded.budget_max,
              price_amount=excluded.price_amount,billing_period=excluded.billing_period,
              duration_text=excluded.duration_text,gov=excluded.gov,
              wilayah=excluded.wilayah,latitude=excluded.latitude,
              longitude=excluded.longitude,location_text=excluded.location_text,
              image_path=COALESCE(NULLIF(excluded.image_path,''),community_listings.image_path),
              details=excluded.details,contact_channels=excluded.contact_channels,
              status=excluded.status,expires_at=excluded.expires_at,
              published_at=COALESCE(NULLIF(community_listings.published_at,''),excluded.published_at),
              updated_at=CURRENT_TIMESTAMP
            """,
            (
                listing_id,
                listing_kind,
                owner_kind,
                actor_id,
                title,
                description,
                category_id,
                service_value,
                budget_min,
                budget_max,
                price_amount,
                billing_period,
                _clean(payload.get("durationText"), 180),
                _clean(payload.get("gov"), 80),
                _clean(payload.get("wilayah"), 80),
                lat,
                lng,
                _clean(payload.get("locationText"), 240),
                image_path,
                _json_dump(details),
                _json_dump(channels),
                status,
                billing_status,
                expires_at,
                published_at,
                idempotency_key,
            ),
        )
        self._event(
            listing_id,
            actor_kind,
            actor_id,
            "listing_updated" if existing else "listing_created",
            {"status": status, "kind": listing_kind},
        )
        row = self._get(listing_id)
        return self.serialize(row, session=session)

    def owner_action(
        self, session: dict[str, Any], listing_id: str, action: str
    ) -> dict[str, Any]:
        row = self._get(listing_id)
        actor_id = session.get("userId") or session.get("providerId") or ""
        if row["owner_id"] != actor_id:
            raise DomainError("community_listing_access_denied", 403)
        if action == "close":
            status, deleted_at = "closed", ""
        elif action == "delete":
            status, deleted_at = "deleted", _iso(self.now)
        elif action == "pause":
            status, deleted_at = "paused", ""
        elif action == "resume":
            status, deleted_at = (
                "pending_review"
                if community_settings(self.con)["communityModerationRequired"]
                else "active"
            ), ""
        elif action == "renew":
            settings = community_settings(self.con)
            fee = settings["communityRenewalFee"]
            status = "pending_payment" if fee > 0 else (
                "pending_review" if settings["communityModerationRequired"] else "active"
            )
            deleted_at = ""
            self.con.execute(
                """UPDATE community_listings SET billing_status=?,
                expires_at=?,closed_at='',updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (
                    "pending_payment" if fee > 0 else "included",
                    _iso(
                        self.now
                        + timedelta(days=settings["communityPackageExpiryDays"])
                    ),
                    listing_id,
                ),
            )
        else:
            raise DomainError("invalid_community_action", 400)
        self.con.execute(
            """UPDATE community_listings SET status=?,deleted_at=?,
            closed_at=CASE WHEN ? IN ('closed','deleted') THEN ? ELSE closed_at END,
            updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (status, deleted_at, status, _iso(self.now), listing_id),
        )
        self._event(listing_id, session["kind"], actor_id, action, {"status": status})
        return self.serialize(self._get(listing_id), session=session)

    def offer(
        self,
        session: dict[str, Any],
        listing_id: str,
        payload: dict[str, Any],
        *,
        offer_id: str,
    ) -> dict[str, Any]:
        self._require_enabled()
        if not community_settings(self.con)["communityProviderOffersEnabled"]:
            raise DomainError("community_offers_disabled", 409)
        if session.get("kind") != "provider":
            raise DomainError("community_provider_required", 403)
        provider_id = session["providerId"]
        listing = self._get(listing_id)
        if listing["kind"] != "wanted" or listing["status"] != "active":
            raise DomainError("community_wanted_not_available", 409)
        provider = self.con.execute(
            """SELECT * FROM providers WHERE id=? AND active=1 AND verified=1
            AND status NOT IN ('deleted','unavailable')
            AND COALESCE(request_enabled,1)=1""",
            (provider_id,),
        ).fetchone()
        if not provider:
            raise DomainError("community_provider_not_eligible", 403)
        category_id, service_id = listing["service_value"].split("|", 1)
        services = _json_load(provider["services"], [])
        if not any(
            item.get("active", True)
            and item.get("catId") == category_id
            and item.get("serviceId") == service_id
            for item in services
        ):
            raise DomainError("community_offer_service_mismatch", 403)
        amount = _number(payload.get("amount"), minimum=0.001)
        duration = _clean(payload.get("durationText"), 160)
        note = _clean(payload.get("note"), 500)
        if not duration:
            raise DomainError("community_offer_duration_required", 400)
        idempotency_key = _clean(
            payload.get("idempotencyKey") or f"{listing_id}:{provider_id}", 160
        )
        self.con.execute(
            """
            INSERT INTO community_offers(
              id,listing_id,provider_id,amount,duration_text,note,status,idempotency_key
            ) VALUES(?,?,?,?,?,?,'sent',?)
            ON CONFLICT(listing_id,provider_id) DO UPDATE SET
              amount=excluded.amount,duration_text=excluded.duration_text,
              note=excluded.note,status='sent',updated_at=CURRENT_TIMESTAMP
            """,
            (
                offer_id,
                listing_id,
                provider_id,
                amount,
                duration,
                note,
                idempotency_key,
            ),
        )
        self._event(
            listing_id,
            "provider",
            provider_id,
            "offer_sent",
            {"amount": amount},
        )
        row = self.con.execute(
            "SELECT * FROM community_offers WHERE listing_id=? AND provider_id=?",
            (listing_id, provider_id),
        ).fetchone()
        return self._offer(row)

    def accept_offer(
        self, session: dict[str, Any], listing_id: str, offer_id: str
    ) -> dict[str, Any]:
        self._require_enabled()
        if session.get("kind") != "user":
            raise DomainError("community_user_required", 403)
        listing = self._get(listing_id)
        if listing["owner_id"] != session["userId"]:
            raise DomainError("community_listing_access_denied", 403)
        if listing["request_id"]:
            return {
                "duplicate": True,
                "requestId": listing["request_id"],
                "listing": self.serialize(listing, session=session),
            }
        offer = self.con.execute(
            """SELECT * FROM community_offers
            WHERE id=? AND listing_id=? AND status='sent'""",
            (offer_id, listing_id),
        ).fetchone()
        if not offer:
            raise DomainError("community_offer_not_found", 404)
        return {
            "duplicate": False,
            "listing": dict(listing),
            "offer": dict(offer),
        }

    def complete_offer_acceptance(
        self, listing_id: str, offer_id: str, request_id: str
    ) -> None:
        self.con.execute(
            """UPDATE community_listings SET status='closed',request_id=?,
            closed_at=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (request_id, _iso(self.now), listing_id),
        )
        self.con.execute(
            """UPDATE community_offers SET status=CASE WHEN id=? THEN 'accepted'
            ELSE 'declined' END,request_id=CASE WHEN id=? THEN ? ELSE request_id END,
            updated_at=CURRENT_TIMESTAMP WHERE listing_id=?""",
            (offer_id, offer_id, request_id, listing_id),
        )
        self._event(
            listing_id,
            "user",
            "",
            "offer_accepted",
            {"offerId": offer_id, "requestId": request_id},
        )

    def begin_package_order(
        self,
        session: dict[str, Any],
        listing_id: str,
        idempotency_key: str,
        *,
        order_id: str,
    ) -> dict[str, Any]:
        self._require_enabled()
        if session.get("kind") != "user":
            raise DomainError("community_user_required", 403)
        listing = self._get(listing_id)
        if listing["kind"] != "package" or listing["status"] != "active":
            raise DomainError("community_package_not_available", 409)
        key = _clean(idempotency_key, 160)
        if not key:
            raise DomainError("idempotency_key_required", 400)
        existing = self.con.execute(
            """SELECT * FROM community_orders
            WHERE user_id=? AND idempotency_key=?""",
            (session["userId"], key),
        ).fetchone()
        if existing:
            return {
                "duplicate": True,
                "order": dict(existing),
                "listing": dict(listing),
            }
        snapshot = {
            "listingId": listing["id"],
            "title": listing["title"],
            "description": listing["description"],
            "serviceValue": listing["service_value"],
            "priceAmount": float(listing["price_amount"] or 0),
            "billingPeriod": listing["billing_period"],
            "durationText": listing["duration_text"],
            "providerId": listing["owner_id"],
            "details": _json_load(listing["details"], {}),
        }
        self.con.execute(
            """INSERT INTO community_orders(
            id,listing_id,user_id,snapshot,idempotency_key)
            VALUES(?,?,?,?,?)""",
            (order_id, listing_id, session["userId"], _json_dump(snapshot), key),
        )
        return {
            "duplicate": False,
            "order": {
                "id": order_id,
                "listing_id": listing_id,
                "user_id": session["userId"],
                "snapshot": _json_dump(snapshot),
            },
            "listing": dict(listing),
        }

    def complete_package_order(
        self, order_id: str, request_id: str, listing_id: str
    ) -> None:
        self.con.execute(
            """UPDATE community_orders SET request_id=?,status='created',
            updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (request_id, order_id),
        )
        self._event(
            listing_id,
            "user",
            "",
            "package_requested",
            {"orderId": order_id, "requestId": request_id},
        )

    def favorite(
        self, session: dict[str, Any], listing_id: str, enabled: bool, *, row_id: str
    ) -> bool:
        kind = session.get("kind")
        account_id = session.get("userId") or session.get("providerId") or ""
        if kind not in {"user", "provider"} or not account_id:
            raise DomainError("auth_required", 401)
        self._get(listing_id)
        if enabled:
            self.con.execute(
                """INSERT OR IGNORE INTO community_favorites(
                id,account_kind,account_id,listing_id) VALUES(?,?,?,?)""",
                (row_id, kind, account_id, listing_id),
            )
        else:
            self.con.execute(
                """DELETE FROM community_favorites
                WHERE account_kind=? AND account_id=? AND listing_id=?""",
                (kind, account_id, listing_id),
            )
        return enabled

    def report(
        self,
        session: dict[str, Any],
        listing_id: str,
        reason: str,
        *,
        report_id: str,
    ) -> dict[str, Any]:
        kind = session.get("kind")
        account_id = session.get("userId") or session.get("providerId") or ""
        if kind not in {"user", "provider"} or not account_id:
            raise DomainError("auth_required", 401)
        listing = self._get(listing_id)
        if listing["owner_id"] == account_id:
            raise DomainError("cannot_report_own_listing", 409)
        reason = _clean(reason, 500)
        if len(reason) < 3:
            raise DomainError("community_report_reason_required", 400)
        self.con.execute(
            """INSERT INTO community_reports(
            id,listing_id,reporter_kind,reporter_id,reason)
            VALUES(?,?,?,?,?)
            ON CONFLICT(listing_id,reporter_kind,reporter_id) DO UPDATE SET
              reason=excluded.reason,status='open',updated_at=CURRENT_TIMESTAMP""",
            (report_id, listing_id, kind, account_id, reason),
        )
        return {"id": report_id, "listingId": listing_id, "reason": reason}

    def moderate(
        self,
        session: dict[str, Any],
        listing_id: str,
        action: str,
        note: str = "",
    ) -> dict[str, Any]:
        row = self._get(listing_id)
        if action == "approve":
            status = "active"
        elif action == "reject":
            status = "rejected"
        elif action == "pause":
            status = "paused"
        elif action == "feature":
            self.con.execute(
                """UPDATE community_listings SET featured=1,
                updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (listing_id,),
            )
            status = row["status"]
        elif action == "unfeature":
            self.con.execute(
                """UPDATE community_listings SET featured=0,
                updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (listing_id,),
            )
            status = row["status"]
        elif action == "mark_paid":
            status = "active"
            self.con.execute(
                """UPDATE community_listings SET billing_status='paid',
                updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (listing_id,),
            )
        else:
            raise DomainError("invalid_community_moderation_action", 400)
        self.con.execute(
            """UPDATE community_listings SET status=?,moderation_note=?,
            published_at=CASE WHEN ?='active' AND COALESCE(published_at,'')=''
              THEN ? ELSE published_at END,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (status, _clean(note, 500), status, _iso(self.now), listing_id),
        )
        self._event(
            listing_id,
            "admin",
            session.get("id", ""),
            f"moderation_{action}",
            {"note": _clean(note, 500)},
        )
        return self.serialize(
            self._get(listing_id), session=session, include_admin=True
        )

    def resolve_report(
        self, session: dict[str, Any], report_id: str, resolution: str
    ) -> None:
        result = self.con.execute(
            """UPDATE community_reports SET status='resolved',resolution=?,
            updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (_clean(resolution, 500), report_id),
        )
        if result.rowcount != 1:
            raise DomainError("community_report_not_found", 404)

    def stats(self) -> dict[str, Any]:
        scalar = lambda query, params=(): int(
            self.con.execute(query, params).fetchone()["n"] or 0
        )
        return {
            "activePackages": scalar(
                "SELECT COUNT(*) n FROM community_listings WHERE kind='package' AND status='active'"
            ),
            "activeWanted": scalar(
                "SELECT COUNT(*) n FROM community_listings WHERE kind='wanted' AND status='active'"
            ),
            "pendingReview": scalar(
                "SELECT COUNT(*) n FROM community_listings WHERE status='pending_review'"
            ),
            "pendingPayment": scalar(
                "SELECT COUNT(*) n FROM community_listings WHERE status='pending_payment'"
            ),
            "openReports": scalar(
                "SELECT COUNT(*) n FROM community_reports WHERE status='open'"
            ),
            "offers": scalar("SELECT COUNT(*) n FROM community_offers"),
            "orders": scalar(
                "SELECT COUNT(*) n FROM community_orders WHERE COALESCE(request_id,'')!=''"
            ),
        }

    def _check_package_quota(self, provider: sqlite3.Row) -> None:
        settings = community_settings(self.con)
        package_id = provider["package_id"] or "foundation_12m"
        quota = _integer(
            settings["communityPlanQuotas"].get(package_id, 1),
            1,
            minimum=0,
            maximum=100,
        )
        used = int(
            self.con.execute(
                """SELECT COUNT(*) n FROM community_listings
                WHERE owner_id=? AND kind='package'
                  AND created_at>=datetime('now','start of month')
                  AND status NOT IN ('deleted','rejected')""",
                (provider["id"],),
            ).fetchone()["n"]
        )
        if used >= quota:
            raise DomainError(
                "community_package_quota_reached",
                409,
                _json_dump(
                    {"used": used, "limit": quota, "packageId": package_id}
                ),
            )

    def _owner(self, listing: dict[str, Any]) -> dict[str, Any]:
        if listing["owner_kind"] == "user":
            row = self.con.execute(
                "SELECT id,name,avatar,gov,wilayah FROM app_users WHERE id=?",
                (listing["owner_id"],),
            ).fetchone()
            if not row:
                return {"id": listing["owner_id"], "name": "مستخدم خدماتي"}
            return {
                "id": row["id"],
                "name": row["name"] or "مستخدم خدماتي",
                "imagePath": row["avatar"] or "",
                "gov": row["gov"] or "",
                "wilayah": row["wilayah"] or "",
                "verified": True,
            }
        row = self.con.execute(
            """SELECT id,name,image_path,card_image,gov,wilayah,verified,rating,
            provider_type,phone FROM providers WHERE id=?""",
            (listing["owner_id"],),
        ).fetchone()
        if not row:
            return {"id": listing["owner_id"], "name": "مزود خدماتي"}
        return {
            "id": row["id"],
            "name": row["name"],
            "imagePath": row["card_image"] or row["image_path"] or "",
            "gov": row["gov"] or "",
            "wilayah": row["wilayah"] or "",
            "verified": bool(row["verified"]),
            "rating": float(row["rating"] or 0),
            "providerType": row["provider_type"] or "individual",
            "whatsappAvailable": bool(
                row["phone"]
                and "whatsapp" in _json_load(
                    listing.get("contact_channels"), ["app"]
                )
            ),
        }

    def _offer(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        data = dict(row)
        provider = self.con.execute(
            """SELECT id,name,image_path,card_image,rating,verified,gov,wilayah
            FROM providers WHERE id=?""",
            (data["provider_id"],),
        ).fetchone()
        return {
            "id": data["id"],
            "listingId": data["listing_id"],
            "providerId": data["provider_id"],
            "provider": (
                {
                    "id": provider["id"],
                    "name": provider["name"],
                    "imagePath": provider["card_image"]
                    or provider["image_path"]
                    or "",
                    "rating": float(provider["rating"] or 0),
                    "verified": bool(provider["verified"]),
                    "gov": provider["gov"] or "",
                    "wilayah": provider["wilayah"] or "",
                }
                if provider
                else {"id": data["provider_id"], "name": "مزود خدماتي"}
            ),
            "amount": float(data["amount"] or 0),
            "durationText": data["duration_text"],
            "note": data["note"],
            "status": data["status"],
            "requestId": data["request_id"],
            "createdAt": data["created_at"],
            "updatedAt": data["updated_at"],
        }

    def _get(self, listing_id: str) -> sqlite3.Row:
        row = self.con.execute(
            """SELECT * FROM community_listings
            WHERE id=? AND COALESCE(deleted_at,'')=''""",
            (_clean(listing_id, 120),),
        ).fetchone()
        if not row:
            raise DomainError("community_listing_not_found", 404)
        return row

    def _event(
        self,
        listing_id: str,
        actor_kind: str,
        actor_id: str,
        action: str,
        detail: dict[str, Any],
    ) -> None:
        event_id = (
            f"cev-{listing_id[:40]}-{action[:32]}-"
            f"{int(self.now.timestamp() * 1_000_000)}"
        )
        self.con.execute(
            """INSERT INTO community_events(
            id,listing_id,actor_kind,actor_id,action,detail)
            VALUES(?,?,?,?,?,?)""",
            (
                event_id,
                listing_id,
                _clean(actor_kind, 30),
                _clean(actor_id, 120),
                _clean(action, 80),
                _json_dump(detail),
            ),
        )

    @staticmethod
    def _coordinate(value: Any, minimum: float, maximum: float) -> float | None:
        if value in (None, ""):
            return None
        return _number(value, minimum=minimum, maximum=maximum)
