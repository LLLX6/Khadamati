"""Growth features that must preserve marketplace trust boundaries."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json
import secrets
from typing import Any

from khadamati_domain import DomainError, EntitlementService, RankingService


def install_growth_schema(con) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS provider_invitations(
          id TEXT PRIMARY KEY,
          user_id TEXT NOT NULL,
          request_id TEXT NOT NULL,
          phone TEXT NOT NULL,
          token_hash TEXT NOT NULL UNIQUE,
          provider_id TEXT NOT NULL DEFAULT '',
          provider_request_id TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'pending',
          expires_at TEXT NOT NULL,
          matched_at TEXT NOT NULL DEFAULT '',
          cancelled_at TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(user_id,request_id,phone)
        );
        CREATE INDEX IF NOT EXISTS idx_provider_invitation_phone
          ON provider_invitations(phone,status,expires_at);
        CREATE INDEX IF NOT EXISTS idx_provider_invitation_owner
          ON provider_invitations(user_id,created_at);
        """
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


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _parse(value: str) -> datetime | None:
    try:
        result = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=UTC)
    return result.astimezone(UTC)


def _mask_phone(phone: str) -> str:
    digits = "".join(char for char in str(phone or "") if char.isdigit())
    return f"+968 •••• {digits[-4:]}" if len(digits) >= 4 else ""


class KnownProviderInvitationService:
    """Invite a known provider without weakening matching or verification."""

    ACTIVE_REQUEST_STATES = {"matching", "viewed", "unavailable", "paused", "open"}

    def __init__(self, con, *, now: datetime | None = None):
        self.con = con
        self.now = (now or datetime.now(UTC)).astimezone(UTC)

    def _request_for_owner(self, request_id: str, user_id: str):
        row = self.con.execute(
            "SELECT * FROM customer_requests WHERE id=? AND user_id=?",
            (request_id, user_id),
        ).fetchone()
        if not row:
            raise DomainError("request_not_found", 404)
        if row["status"] not in self.ACTIVE_REQUEST_STATES or row["accepted_provider_id"]:
            raise DomainError("request_not_open", 409)
        return row

    def _provider_by_phone(self, phone: str):
        return self.con.execute(
            """SELECT * FROM providers WHERE phone=? AND active=1 AND verified=1
            AND status NOT IN ('unavailable','deleted')
            AND COALESCE(listing_enabled,1)=1 AND COALESCE(request_enabled,1)=1
            ORDER BY created_at DESC LIMIT 1""",
            (phone,),
        ).fetchone()

    def _assert_eligible(self, request_row, provider_row) -> None:
        request = dict(request_row)
        provider = dict(provider_row)
        if not RankingService.exact_service_match(request, provider):
            raise DomainError("provider_not_eligible_for_request", 409)
        allowed, _, _ = EntitlementService(self.con, now=self.now).can_receive(
            provider["id"]
        )
        if not allowed:
            raise DomainError("provider_no_longer_available", 409)

    def attach(self, request_row, provider_row) -> dict[str, Any]:
        self._assert_eligible(request_row, provider_row)
        request_id = request_row["id"]
        provider_id = provider_row["id"]
        matching = _load(request_row["matching_provider_ids"], [])
        matching = list(dict.fromkeys([*matching, provider_id]))
        self.con.execute(
            """UPDATE customer_requests SET matching_provider_ids=?,status='matching',
            marketplace_status='notified',waitlisted=0,updated_at=CURRENT_TIMESTAMP
            WHERE id=?""",
            (_dump(matching), request_id),
        )
        self.con.execute(
            """INSERT INTO request_dispatches(
            id,request_id,provider_id,rank,score,score_breakdown,wave,release_at,
            status,notified_at)
            VALUES(?,?,?,?,?,'{}',1,?,'notified',?)
            ON CONFLICT(request_id,provider_id) DO UPDATE SET status='notified',
            release_at=excluded.release_at,notified_at=excluded.notified_at,
            updated_at=CURRENT_TIMESTAMP""",
            (
                "dispatch_" + secrets.token_urlsafe(12),
                request_id,
                provider_id,
                1,
                100.0,
                _iso(self.now),
                _iso(self.now),
            ),
        )
        return {"requestId": request_id, "providerId": provider_id}

    def create(
        self, user_id: str, request_id: str, phone: str, *, ttl_days: int = 14
    ) -> dict[str, Any]:
        request_row = self._request_for_owner(request_id, user_id)
        if len(phone) < 11:
            raise DomainError("valid_phone_required", 400)
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        invitation_id = "invite_" + secrets.token_urlsafe(12)
        provider_row = self._provider_by_phone(phone)
        provider_id = ""
        status = "pending"
        if provider_row:
            self.attach(request_row, provider_row)
            provider_id = provider_row["id"]
            status = "matched"
        self.con.execute(
            """INSERT INTO provider_invitations(
            id,user_id,request_id,phone,token_hash,provider_id,status,expires_at,
            matched_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(user_id,request_id,phone) DO UPDATE SET
            token_hash=excluded.token_hash,provider_id=excluded.provider_id,
            status=excluded.status,expires_at=excluded.expires_at,
            matched_at=excluded.matched_at,cancelled_at='',updated_at=CURRENT_TIMESTAMP""",
            (
                invitation_id,
                user_id,
                request_id,
                phone,
                token_hash,
                provider_id,
                status,
                _iso(self.now + timedelta(days=max(1, min(ttl_days, 30)))),
                _iso(self.now) if provider_id else "",
            ),
        )
        stored = self.con.execute(
            """SELECT * FROM provider_invitations
            WHERE user_id=? AND request_id=? AND phone=?""",
            (user_id, request_id, phone),
        ).fetchone()
        return {**self.public(stored), "token": raw_token}

    def resolve_for_registration(self, token: str, phone: str) -> dict[str, str]:
        token_hash = hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()
        row = self.con.execute(
            """SELECT * FROM provider_invitations WHERE token_hash=? AND phone=?
            AND status IN ('pending','joined_pending')""",
            (token_hash, phone),
        ).fetchone()
        if not row:
            raise DomainError("provider_invitation_not_found", 404)
        expiry = _parse(row["expires_at"])
        if not expiry or expiry <= self.now:
            self.con.execute(
                "UPDATE provider_invitations SET status='expired',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (row["id"],),
            )
            raise DomainError("provider_invitation_expired", 410)
        return {"id": row["id"], "requestId": row["request_id"]}

    def mark_registration(self, invitation_id: str, provider_request_id: str) -> None:
        self.con.execute(
            """UPDATE provider_invitations SET status='joined_pending',
            provider_request_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?
            AND status='pending'""",
            (provider_request_id, invitation_id),
        )

    def match_approved_provider(self, provider_id: str, phone: str) -> list[dict[str, str]]:
        provider_row = self.con.execute(
            "SELECT * FROM providers WHERE id=? AND phone=?", (provider_id, phone)
        ).fetchone()
        if not provider_row:
            return []
        matched: list[dict[str, str]] = []
        rows = list(
            self.con.execute(
                """SELECT * FROM provider_invitations WHERE phone=?
                AND status IN ('pending','joined_pending')""",
                (phone,),
            )
        )
        for invitation in rows:
            expiry = _parse(invitation["expires_at"])
            if not expiry or expiry <= self.now:
                self.con.execute(
                    "UPDATE provider_invitations SET status='expired',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (invitation["id"],),
                )
                continue
            request_row = self.con.execute(
                "SELECT * FROM customer_requests WHERE id=?", (invitation["request_id"],)
            ).fetchone()
            if not request_row or request_row["status"] not in self.ACTIVE_REQUEST_STATES:
                continue
            try:
                linked = self.attach(request_row, provider_row)
            except DomainError:
                continue
            self.con.execute(
                """UPDATE provider_invitations SET provider_id=?,status='matched',
                matched_at=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (provider_id, _iso(self.now), invitation["id"]),
            )
            matched.append(linked)
        return matched

    def cancel(self, user_id: str, invitation_id: str) -> bool:
        result = self.con.execute(
            """UPDATE provider_invitations SET status='cancelled',cancelled_at=?,
            updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?
            AND status IN ('pending','joined_pending')""",
            (_iso(self.now), invitation_id, user_id),
        )
        return result.rowcount == 1

    def list_for_user(self, user_id: str) -> list[dict[str, Any]]:
        return [
            self.public(row)
            for row in self.con.execute(
                """SELECT * FROM provider_invitations WHERE user_id=?
                ORDER BY created_at DESC LIMIT 100""",
                (user_id,),
            )
        ]

    @staticmethod
    def public(row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "requestId": row["request_id"],
            "phoneMasked": _mask_phone(row["phone"]),
            "providerId": row["provider_id"],
            "status": row["status"],
            "expiresAt": row["expires_at"],
            "createdAt": row["created_at"],
        }
