"""Security extensions that are independent from the HTTP transport.

The module deliberately keeps the admin second factor server-side. TOTP
secrets are encrypted at rest and recovery codes are stored as hashes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import base64
import hashlib
import hmac
import json
import secrets
import struct
from typing import Any
from urllib.parse import quote

from cryptography.fernet import Fernet, InvalidToken

from khadamati_domain import DomainError


def _columns(con, table: str) -> set[str]:
    return {row["name"] for row in con.execute(f"PRAGMA table_info({table})")}


def install_security_schema(con) -> None:
    columns = _columns(con, "admin_users")
    if "two_factor_enabled" not in columns:
        con.execute(
            "ALTER TABLE admin_users ADD COLUMN two_factor_enabled INTEGER NOT NULL DEFAULT 0"
        )
    if "two_factor_secret" not in columns:
        con.execute(
            "ALTER TABLE admin_users ADD COLUMN two_factor_secret TEXT NOT NULL DEFAULT ''"
        )
    if "recovery_codes" not in columns:
        con.execute(
            "ALTER TABLE admin_users ADD COLUMN recovery_codes TEXT NOT NULL DEFAULT '[]'"
        )
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS admin_two_factor_challenges(
          id TEXT PRIMARY KEY,
          admin_id TEXT NOT NULL,
          secret_ciphertext TEXT NOT NULL,
          expires_at TEXT NOT NULL,
          used_at TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(admin_id) REFERENCES admin_users(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_admin_2fa_challenge
          ON admin_two_factor_challenges(admin_id,expires_at,used_at);
        """
    )


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _parse(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


class AdminTwoFactorService:
    PERIOD_SECONDS = 30
    DIGITS = 6

    def __init__(self, con, encryption_key: str, *, now: datetime | None = None):
        if not encryption_key:
            raise DomainError("admin_2fa_key_not_configured", 503)
        self.con = con
        self.now = (now or datetime.now(UTC)).astimezone(UTC)
        derived = base64.urlsafe_b64encode(
            hashlib.sha256(encryption_key.encode("utf-8")).digest()
        )
        self.cipher = Fernet(derived)

    @staticmethod
    def _new_secret() -> str:
        return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")

    def _encrypt(self, value: str) -> str:
        return self.cipher.encrypt(value.encode("utf-8")).decode("ascii")

    def _decrypt(self, value: str) -> str:
        try:
            return self.cipher.decrypt(value.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError) as exc:
            raise DomainError("admin_2fa_secret_unavailable", 503) from exc

    @classmethod
    def _totp(cls, secret: str, moment: datetime) -> str:
        padding = "=" * ((8 - len(secret) % 8) % 8)
        key = base64.b32decode((secret + padding).upper(), casefold=True)
        counter = int(moment.timestamp()) // cls.PERIOD_SECONDS
        digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
        offset = digest[-1] & 0x0F
        value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
        return str(value % (10**cls.DIGITS)).zfill(cls.DIGITS)

    def verify_totp(self, secret: str, code: Any) -> bool:
        candidate = "".join(ch for ch in str(code or "") if ch.isdigit())
        if len(candidate) != self.DIGITS:
            return False
        return any(
            hmac.compare_digest(
                candidate,
                self._totp(secret, self.now + timedelta(seconds=offset * self.PERIOD_SECONDS)),
            )
            for offset in (-1, 0, 1)
        )

    def begin(self, admin_id: str, admin_name: str) -> dict[str, str]:
        row = self.con.execute(
            "SELECT id FROM admin_users WHERE id=? AND active=1", (admin_id,)
        ).fetchone()
        if not row:
            raise DomainError("admin_account_not_found", 404)
        self.con.execute(
            "DELETE FROM admin_two_factor_challenges WHERE admin_id=?", (admin_id,)
        )
        challenge_id = "a2f_" + secrets.token_urlsafe(18)
        secret = self._new_secret()
        self.con.execute(
            """INSERT INTO admin_two_factor_challenges(
            id,admin_id,secret_ciphertext,expires_at)
            VALUES(?,?,?,?)""",
            (
                challenge_id,
                admin_id,
                self._encrypt(secret),
                _iso(self.now + timedelta(minutes=10)),
            ),
        )
        issuer = "Khadamati"
        label = quote(f"{issuer}:{admin_name or admin_id}")
        uri = (
            f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}"
            f"&digits={self.DIGITS}&period={self.PERIOD_SECONDS}"
        )
        return {"challengeId": challenge_id, "secret": secret, "uri": uri}

    def confirm(self, challenge_id: str, code: Any) -> dict[str, Any]:
        row = self.con.execute(
            """SELECT c.*,a.name FROM admin_two_factor_challenges c
            JOIN admin_users a ON a.id=c.admin_id
            WHERE c.id=? AND a.active=1""",
            (challenge_id,),
        ).fetchone()
        if not row or row["used_at"]:
            raise DomainError("admin_2fa_challenge_not_found", 404)
        expires_at = _parse(row["expires_at"])
        if not expires_at or expires_at <= self.now:
            raise DomainError("admin_2fa_challenge_expired", 410)
        secret = self._decrypt(row["secret_ciphertext"])
        if not self.verify_totp(secret, code):
            raise DomainError("admin_2fa_invalid", 403)
        recovery_codes = [
            f"{secrets.randbelow(1_0000):04d}-{secrets.randbelow(1_0000):04d}"
            for _ in range(8)
        ]
        recovery_hashes = [
            hashlib.sha256(item.encode("utf-8")).hexdigest()
            for item in recovery_codes
        ]
        self.con.execute(
            """UPDATE admin_users SET two_factor_enabled=1,two_factor_secret=?,
            recovery_codes=? WHERE id=?""",
            (self._encrypt(secret), json.dumps(recovery_hashes), row["admin_id"]),
        )
        self.con.execute(
            "UPDATE admin_two_factor_challenges SET used_at=? WHERE id=?",
            (_iso(self.now), challenge_id),
        )
        return {
            "adminId": row["admin_id"],
            "recoveryCodes": recovery_codes,
        }

    def verify_admin(self, admin_row, code: Any) -> bool:
        if not bool(admin_row["two_factor_enabled"]):
            return False
        candidate = str(code or "").strip()
        secret = self._decrypt(admin_row["two_factor_secret"])
        if self.verify_totp(secret, candidate):
            return True
        recovery_hash = hashlib.sha256(candidate.encode("utf-8")).hexdigest()
        try:
            hashes = json.loads(admin_row["recovery_codes"] or "[]")
        except json.JSONDecodeError:
            hashes = []
        if recovery_hash not in hashes:
            return False
        hashes.remove(recovery_hash)
        self.con.execute(
            "UPDATE admin_users SET recovery_codes=? WHERE id=?",
            (json.dumps(hashes), admin_row["id"]),
        )
        return True

    @staticmethod
    def public_status(admin_row) -> dict[str, Any]:
        try:
            recovery_count = len(json.loads(admin_row["recovery_codes"] or "[]"))
        except (json.JSONDecodeError, TypeError):
            recovery_count = 0
        return {
            "enabled": bool(admin_row["two_factor_enabled"]),
            "recoveryCodesRemaining": recovery_count,
        }
