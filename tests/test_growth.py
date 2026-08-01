from __future__ import annotations

from datetime import UTC, datetime, timedelta
import sqlite3
import unittest
from unittest.mock import patch

from khadamati_domain import DomainError
from khadamati_growth import KnownProviderInvitationService, install_growth_schema


class _AllowedEntitlements:
    def __init__(self, *_args, **_kwargs):
        pass

    def can_receive(self, _provider_id):
        return True, "ok", {"planId": "foundation_12m"}


class KnownProviderInvitationTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        self.con.executescript(
            """
            CREATE TABLE app_users(id TEXT PRIMARY KEY);
            CREATE TABLE customer_requests(
              id TEXT PRIMARY KEY,user_id TEXT NOT NULL,service_value TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'matching',accepted_provider_id TEXT DEFAULT '',
              matching_provider_ids TEXT DEFAULT '[]',marketplace_status TEXT DEFAULT '',
              waitlisted INTEGER DEFAULT 0,updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE providers(
              id TEXT PRIMARY KEY,phone TEXT NOT NULL,active INTEGER DEFAULT 1,
              verified INTEGER DEFAULT 1,status TEXT DEFAULT 'available',
              listing_enabled INTEGER DEFAULT 1,request_enabled INTEGER DEFAULT 1,
              services TEXT DEFAULT '[]',created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE request_dispatches(
              id TEXT PRIMARY KEY,request_id TEXT NOT NULL,provider_id TEXT NOT NULL,
              rank INTEGER DEFAULT 0,score REAL DEFAULT 0,score_breakdown TEXT DEFAULT '{}',
              wave INTEGER DEFAULT 1,release_at TEXT,status TEXT,notified_at TEXT,
              updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
              UNIQUE(request_id,provider_id)
            );
            INSERT INTO app_users(id) VALUES('u1');
            INSERT INTO customer_requests(id,user_id,service_value)
            VALUES('r1','u1','homecare|electrician');
            """
        )
        install_growth_schema(self.con)

    def tearDown(self):
        self.con.close()

    @patch("khadamati_growth.EntitlementService", _AllowedEntitlements)
    @patch(
        "khadamati_growth.RankingService.exact_service_match",
        return_value=True,
    )
    def test_existing_provider_is_attached_only_to_the_selected_request(self, _match):
        self.con.execute(
            """INSERT INTO providers(id,phone,services)
            VALUES('p1','96891234567','[{"catId":"homecare","serviceId":"electrician"}]')"""
        )
        invitation = KnownProviderInvitationService(
            self.con, now=self.now
        ).create("u1", "r1", "96891234567")
        self.assertEqual("matched", invitation["status"])
        self.assertEqual("p1", invitation["providerId"])
        request = self.con.execute(
            "SELECT matching_provider_ids FROM customer_requests WHERE id='r1'"
        ).fetchone()
        self.assertEqual('["p1"]', request["matching_provider_ids"])
        dispatches = self.con.execute(
            "SELECT COUNT(*) n FROM request_dispatches WHERE request_id='r1'"
        ).fetchone()["n"]
        self.assertEqual(1, dispatches)

    def test_new_provider_token_is_phone_bound_and_expires(self):
        invitation = KnownProviderInvitationService(
            self.con, now=self.now
        ).create("u1", "r1", "96892345678")
        service = KnownProviderInvitationService(self.con, now=self.now)
        resolved = service.resolve_for_registration(
            invitation["token"], "96892345678"
        )
        self.assertEqual("r1", resolved["requestId"])
        with self.assertRaises(DomainError) as context:
            service.resolve_for_registration(invitation["token"], "96890000000")
        self.assertEqual("provider_invitation_not_found", context.exception.code)

        expired = KnownProviderInvitationService(
            self.con, now=self.now + timedelta(days=15)
        )
        with self.assertRaises(DomainError) as context:
            expired.resolve_for_registration(invitation["token"], "96892345678")
        self.assertEqual("provider_invitation_expired", context.exception.code)

    def test_cancel_is_scoped_to_the_request_owner(self):
        invitation = KnownProviderInvitationService(
            self.con, now=self.now
        ).create("u1", "r1", "96893456789")
        service = KnownProviderInvitationService(self.con, now=self.now)
        self.assertFalse(service.cancel("another-user", invitation["id"]))
        self.assertTrue(service.cancel("u1", invitation["id"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
