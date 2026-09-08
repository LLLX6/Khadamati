"""Delivery failures must preserve valid subscriptions for an outbox retry."""

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import server
from khadamati_domain import DomainError


class PushDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="khadamati-push-delivery-")
        self.addCleanup(self.temp.cleanup)
        self.database = Path(self.temp.name) / "push.sqlite3"
        self.database_patch = patch.object(server, "DB_PATH", self.database)
        self.database_patch.start()
        self.addCleanup(self.database_patch.stop)
        with sqlite3.connect(self.database) as con:
            con.execute("""CREATE TABLE push_subscription_bindings(
                id TEXT PRIMARY KEY, target_kind TEXT, target_id TEXT,
                subscription_json TEXT, active INTEGER DEFAULT 1,
                updated_at TEXT, last_success_at TEXT)""")
            con.execute(
                """INSERT INTO push_subscription_bindings
                (id,target_kind,target_id,subscription_json) VALUES(?,?,?,?)""",
                ("push-test", "user", "user-test", json.dumps({"endpoint": "https://fcm.googleapis.com/test"})),
            )

    def subscription_active(self):
        with sqlite3.connect(self.database) as con:
            return con.execute("SELECT active FROM push_subscription_bindings").fetchone()[0]

    def test_temporary_dns_failure_keeps_subscription_and_requests_retry(self):
        with (
            patch.object(server, "push_ready", return_value=True),
            patch.object(server.time, "sleep"),
            patch.object(server, "validate_push_endpoint", side_effect=DomainError("push_endpoint_unresolvable", 400)),
            patch.object(server, "webpush") as send,
        ):
            delivered = server.deliver_push("user", "user-test", {"type": "chat"})
        self.assertFalse(delivered)
        self.assertEqual(1, self.subscription_active())
        send.assert_not_called()

    def test_unsafe_endpoint_is_still_disabled(self):
        with (
            patch.object(server, "push_ready", return_value=True),
            patch.object(server.time, "sleep"),
            patch.object(server, "validate_push_endpoint", side_effect=DomainError("push_endpoint_not_public", 400)),
            patch.object(server, "webpush") as send,
        ):
            delivered = server.deliver_push("user", "user-test", {"type": "chat"})
        self.assertTrue(delivered)
        self.assertEqual(0, self.subscription_active())
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
