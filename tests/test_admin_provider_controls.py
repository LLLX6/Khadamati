from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TEMP = tempfile.TemporaryDirectory(prefix="khadamati-provider-controls-")
os.environ["KHADAMATI_DB_PATH"] = str(Path(TEMP.name) / "controls.sqlite3")
os.environ["KHADAMATI_UPLOAD_DIR"] = str(Path(TEMP.name) / "uploads")
os.environ["KHADAMATI_BACKUP_DIR"] = str(Path(TEMP.name) / "backups")
os.environ["KHADAMATI_ENV"] = "test"
os.environ["KHADAMATI_SEED_SAMPLE_DATA"] = "false"

# Some locked-down Windows hosts block cryptography's native Rust DLL before
# tests can import the server.  This suite does not exercise encryption; use
# the same deterministic test-only shim as the source-of-truth tests there.
try:
    from cryptography.fernet import Fernet as _CryptographyProbe  # noqa: F401
except (ImportError, OSError):
    class _TestFernet:
        @staticmethod
        def generate_key():
            return base64.urlsafe_b64encode(b"k" * 32)

        def __init__(self, _key):
            pass

        def encrypt(self, value):
            return base64.urlsafe_b64encode(value)

        def decrypt(self, value):
            return base64.urlsafe_b64decode(value)

    cryptography_module = sys.modules.get("cryptography") or types.ModuleType("cryptography")
    fernet_module = types.ModuleType("cryptography.fernet")
    fernet_module.Fernet = _TestFernet
    fernet_module.InvalidToken = ValueError
    sys.modules["cryptography"] = cryptography_module
    sys.modules["cryptography.fernet"] = fernet_module

import server  # noqa: E402
from khadamati_domain import RequestMarketplace  # noqa: E402


class AdminProviderControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        server.init_db()

    def setUp(self):
        self.con = sqlite3.connect(server.DB_PATH)
        self.con.row_factory = sqlite3.Row
        for table in (
            "request_matching_diagnostics",
            "request_dispatches",
            "app_notifications",
            "audit_logs",
            "customer_requests",
            "provider_team_members",
            "provider_branches",
            "providers",
            "app_users",
        ):
            self.con.execute(f"DELETE FROM {table}")
        settings = json.loads(
            self.con.execute(
                "SELECT value FROM settings WHERE key='platform'"
            ).fetchone()["value"]
        )
        settings["subscriptionsEnabled"] = False
        self.con.execute(
            "UPDATE settings SET value=? WHERE key='platform'",
            (json.dumps(settings, ensure_ascii=False),),
        )
        self.session = {"kind": "admin", "id": "admin-test", "name": "Admin"}

    def tearDown(self):
        self.con.rollback()
        self.con.close()

    def provider(self, provider_id="provider-tech", service_id="networks"):
        server.upsert_provider(
            self.con,
            {
                "id": provider_id,
                "name": "مزود تقني للاختبار",
                "phone": "96891112233",
                "email": "provider@example.test",
                "pin": "7349",
                "gov": "مسقط",
                "wilayah": "السيب",
                "areas": ["مسقط", "السيب"],
                "bio": "مزود تقني محترف ومعتمد",
                "hours": "08:00-20:00",
                "status": "available",
                "active": True,
                "verified": True,
                "services": [
                    {
                        "catId": "tech",
                        "serviceId": service_id,
                        "priceFrom": 10,
                        "active": True,
                        "areas": ["السيب"],
                    }
                ],
            },
        )
        return provider_id

    def request(self, request_id="request-tech", service_value="tech|tech_support"):
        user_id = f"user-{request_id}"
        self.con.execute(
            """INSERT INTO app_users(id,phone,name,pin_hash,status)
            VALUES(?,?,?,?, 'active')""",
            (
                user_id,
                f"96895{abs(hash(request_id)) % 1_000_000:06d}",
                "مستخدم اختبار",
                server.hash_pin("7349"),
            ),
        )
        self.con.execute(
            """INSERT INTO customer_requests(
            id,user_id,customer_name,phone,service_value,service_name,gov,wilayah,
            status,offers_open,requested_at,created_at)
            VALUES(?,?,?,?,?,?,?,?, 'matching',1,?,?)""",
            (
                request_id,
                user_id,
                "مستخدم اختبار",
                "96895550123",
                service_value,
                "خدمة اختبار",
                "مسقط",
                "السيب",
                datetime(2026, 8, 10, 10, tzinfo=UTC).isoformat(),
                datetime(2026, 8, 10, 10, tzinfo=UTC).isoformat(),
            ),
        )
        return request_id

    def test_lifecycle_is_recoverable_before_irreversible_anonymization(self):
        provider_id = self.provider()
        suspended = server.provider_lifecycle_transition(
            self.con,
            self.session,
            provider_id,
            "suspend",
            reason="مراجعة إدارية مطلوبة",
        )
        self.assertTrue(suspended["transitioned"])
        self.assertEqual("suspended", suspended["toState"])
        row = self.con.execute(
            "SELECT * FROM providers WHERE id=?", (provider_id,)
        ).fetchone()
        self.assertEqual(0, row["active"])
        self.assertEqual(0, row["listing_enabled"])
        self.assertEqual(0, row["request_enabled"])
        self.assertEqual(
            1,
            self.con.execute(
                """SELECT COUNT(*) n FROM app_notifications
                WHERE target_kind='provider' AND target_id=?""",
                (provider_id,),
            ).fetchone()["n"],
        )

        duplicate = server.provider_lifecycle_transition(
            self.con,
            self.session,
            provider_id,
            "suspend",
            reason="مراجعة إدارية مطلوبة",
        )
        self.assertFalse(duplicate["transitioned"])
        restored = server.provider_lifecycle_transition(
            self.con, self.session, provider_id, "restore"
        )
        self.assertEqual("active", restored["toState"])
        self.assertTrue(restored["provider"]["active"])
        self.assertEqual("unavailable", restored["provider"]["status"])
        self.assertFalse(restored["provider"]["listingEnabled"])

        archived = server.provider_lifecycle_transition(
            self.con,
            self.session,
            provider_id,
            "archive",
            reason="أرشفة قابلة للاستعادة",
        )
        self.assertEqual("archived", archived["toState"])
        server.anonymize_provider_account(
            self.con, provider_id, reason="explicit_permanent_delete"
        )
        deleted = self.con.execute(
            "SELECT * FROM providers WHERE id=?", (provider_id,)
        ).fetchone()
        self.assertEqual("deleted", deleted["lifecycle_state"])
        self.assertEqual("", deleted["email"])
        self.assertEqual("[]", deleted["services"])
        self.assertTrue(deleted["phone"].startswith("deleted-"))

    def test_admin_dispatch_notifies_but_never_accepts_for_customer(self):
        provider_id = self.provider()
        request_id = self.request()
        result = server.admin_dispatch_request(
            self.con, self.session, request_id, provider_id
        )
        self.assertTrue(result["ok"])
        self.assertFalse(result["alreadyDispatched"])
        self.assertEqual("related", result["diagnostic"]["matchType"])
        request = self.con.execute(
            "SELECT * FROM customer_requests WHERE id=?", (request_id,)
        ).fetchone()
        self.assertEqual("", request["accepted_provider_id"])
        self.assertIn(provider_id, json.loads(request["matching_provider_ids"]))
        self.assertEqual(
            "notified",
            self.con.execute(
                """SELECT status FROM request_dispatches
                WHERE request_id=? AND provider_id=?""",
                (request_id, provider_id),
            ).fetchone()["status"],
        )
        notification_count = self.con.execute(
            "SELECT COUNT(*) n FROM app_notifications"
        ).fetchone()["n"]
        replay = server.admin_dispatch_request(
            self.con, self.session, request_id, provider_id
        )
        self.assertTrue(replay["alreadyDispatched"])
        self.assertEqual(
            notification_count,
            self.con.execute(
                "SELECT COUNT(*) n FROM app_notifications"
            ).fetchone()["n"],
        )

    def test_matching_contract_and_admin_reports_are_aggregate(self):
        self.provider()
        availability = server.service_availability_snapshot(self.con)
        self.assertEqual("matching_v3", availability["contractVersion"])
        self.assertEqual(1, availability["services"]["tech|tech_support"])
        self.assertEqual(1, availability["exactServices"]["tech|networks"])
        self.assertNotIn("tech|design", availability["services"])

        unavailable_id = self.request("request-design", "tech|design")
        self.assertEqual([], RequestMarketplace(self.con).schedule(unavailable_id))
        diagnostics = self.con.execute(
            """SELECT * FROM request_matching_diagnostics
            WHERE request_id=?""",
            (unavailable_id,),
        ).fetchone()
        self.assertIsNotNone(diagnostics)
        self.assertGreaterEqual(
            json.loads(diagnostics["reasons"]).get("service_mismatch", 0), 1
        )

        offered_id = self.request("request-offered", "tech|tech_support")
        self.con.execute(
            """INSERT INTO request_dispatches(
            id,request_id,provider_id,rank,score,score_breakdown,wave,release_at,
            status,offered_at)
            VALUES(?,?,?,?,?,'{}',1,?,'offered',?)""",
            (
                "dispatch-offered",
                offered_id,
                "provider-tech",
                1,
                80,
                datetime(2026, 8, 10, 10, tzinfo=UTC).isoformat(),
                (datetime(2026, 8, 10, 10, tzinfo=UTC) + timedelta(minutes=30)).isoformat(),
            ),
        )
        reports = server.admin_request_reporting(self.con)
        self.assertGreaterEqual(reports["customerRequests"]["total"], 2)
        self.assertEqual(1, reports["customerRequests"]["unavailable"])
        self.assertGreaterEqual(
            reports["requestMatching"]["noMatchReasons"].get(
                "service_mismatch", 0
            ),
            1,
        )
        self.assertTrue(reports["requestMatching"]["privacySafe"])
        self.assertEqual(1, reports["offerPerformance"]["requestsWithOffer"])
        self.assertAlmostEqual(
            30.0,
            reports["offerPerformance"]["averageTimeToFirstOfferMinutes"],
            places=1,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
