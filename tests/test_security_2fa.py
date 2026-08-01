from __future__ import annotations

from datetime import UTC, datetime, timedelta
import sqlite3
import unittest

from khadamati_domain import DomainError
from khadamati_security import AdminTwoFactorService, install_security_schema


class AdminTwoFactorTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        self.con.executescript(
            """
            CREATE TABLE admin_users(
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              code_hash TEXT NOT NULL,
              role TEXT NOT NULL,
              permissions TEXT NOT NULL,
              active INTEGER NOT NULL DEFAULT 1
            );
            INSERT INTO admin_users(id,name,code_hash,role,permissions)
            VALUES('admin-owner','Owner','unused','owner','[]');
            """
        )
        install_security_schema(self.con)
        self.service = AdminTwoFactorService(
            self.con, "test-only-encryption-key", now=self.now
        )

    def tearDown(self):
        self.con.close()

    def test_setup_encrypts_secret_and_returns_recovery_codes_once(self):
        setup = self.service.begin("admin-owner", "Owner")
        code = self.service._totp(setup["secret"], self.now)
        result = self.service.confirm(setup["challengeId"], code)
        self.assertEqual("admin-owner", result["adminId"])
        self.assertEqual(8, len(result["recoveryCodes"]))

        row = self.con.execute(
            "SELECT * FROM admin_users WHERE id='admin-owner'"
        ).fetchone()
        self.assertEqual(1, row["two_factor_enabled"])
        self.assertNotIn(setup["secret"], row["two_factor_secret"])
        self.assertTrue(self.service.verify_admin(row, code))

        with self.assertRaises(DomainError) as context:
            self.service.confirm(setup["challengeId"], code)
        self.assertEqual("admin_2fa_challenge_not_found", context.exception.code)

    def test_recovery_code_is_single_use(self):
        setup = self.service.begin("admin-owner", "Owner")
        result = self.service.confirm(
            setup["challengeId"], self.service._totp(setup["secret"], self.now)
        )
        recovery_code = result["recoveryCodes"][0]
        row = self.con.execute(
            "SELECT * FROM admin_users WHERE id='admin-owner'"
        ).fetchone()
        self.assertTrue(self.service.verify_admin(row, recovery_code))
        row = self.con.execute(
            "SELECT * FROM admin_users WHERE id='admin-owner'"
        ).fetchone()
        self.assertFalse(self.service.verify_admin(row, recovery_code))
        self.assertEqual(7, self.service.public_status(row)["recoveryCodesRemaining"])

    def test_expired_challenge_cannot_be_confirmed(self):
        setup = self.service.begin("admin-owner", "Owner")
        later = AdminTwoFactorService(
            self.con,
            "test-only-encryption-key",
            now=self.now + timedelta(minutes=11),
        )
        code = later._totp(setup["secret"], later.now)
        with self.assertRaises(DomainError) as context:
            later.confirm(setup["challengeId"], code)
        self.assertEqual("admin_2fa_challenge_expired", context.exception.code)


if __name__ == "__main__":
    unittest.main(verbosity=2)
