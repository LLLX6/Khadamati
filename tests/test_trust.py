from __future__ import annotations

from datetime import UTC, datetime, timedelta
import sqlite3
import unittest

from khadamati_domain import DomainError
from khadamati_trust import (
    ComplaintCaseService,
    InteractionBlockService,
    ProviderVerificationService,
    install_trust_schema,
)


class TrustServiceTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 30, 8, 0, tzinfo=UTC)
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        self.con.executescript(
            """
            CREATE TABLE providers(
              id TEXT PRIMARY KEY,
              provider_type TEXT DEFAULT 'individual',
              verified INTEGER DEFAULT 0,
              verification_expiry TEXT DEFAULT '',
              status TEXT DEFAULT 'available',
              listing_enabled INTEGER DEFAULT 1,
              request_enabled INTEGER DEFAULT 1,
              updated_at TEXT DEFAULT ''
            );
            CREATE TABLE app_users(
              id TEXT PRIMARY KEY,
              status TEXT DEFAULT 'active'
            );
            CREATE TABLE complaints(
              id TEXT PRIMARY KEY,
              provider_id TEXT DEFAULT '',
              customer_name TEXT DEFAULT '',
              phone TEXT DEFAULT '',
              reason TEXT DEFAULT '',
              detail TEXT DEFAULT '',
              status TEXT DEFAULT 'open',
              priority TEXT DEFAULT 'normal',
              resolution TEXT DEFAULT '',
              created_at TEXT DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
              request_id TEXT DEFAULT '',
              user_id TEXT DEFAULT ''
            );
            """
        )
        install_trust_schema(self.con)
        self.con.execute(
            """INSERT INTO providers(
            id,provider_type,verified,verification_expiry,status)
            VALUES('p1','individual',0,'','available')"""
        )
        self.con.execute(
            """INSERT INTO providers(
            id,provider_type,verified,verification_expiry,status)
            VALUES('c1','company',1,'2027-07-30T00:00:00+00:00','available')"""
        )
        self.con.execute(
            "INSERT INTO app_users(id) VALUES('u1')"
        )

    def tearDown(self):
        self.con.close()

    def test_verification_distinguishes_identity_entity_and_activity(self):
        service = ProviderVerificationService(self.con, now=self.now)
        provider = self.con.execute(
            "SELECT * FROM providers WHERE id='p1'"
        ).fetchone()
        case = service.ensure_case(provider)
        self.assertEqual("unverified", case["status"])
        with self.assertRaises(DomainError) as context:
            service.review(
                "p1",
                {
                    "status": "verified",
                    "identityStatus": "verified",
                    "activityStatus": "pending",
                },
                reviewer_id="admin-1",
            )
        self.assertEqual("verification_checks_incomplete", context.exception.code)

        verified = service.review(
            "p1",
            {
                "status": "verified",
                "identityStatus": "verified",
                "activityStatus": "verified",
                "expiresAt": "2027-07-30T00:00:00+00:00",
                "decisionNote": "Documents reviewed.",
            },
            reviewer_id="admin-1",
        )
        self.assertTrue(verified["verified"])
        self.assertEqual("not_applicable", verified["entityStatus"])
        self.assertEqual("professional_verified", verified["badge"]["key"])
        provider_state = self.con.execute(
            "SELECT verified FROM providers WHERE id='p1'"
        ).fetchone()
        self.assertEqual(1, provider_state["verified"])

    def test_company_case_keeps_a_distinct_entity_check(self):
        service = ProviderVerificationService(self.con, now=self.now)
        service.backfill()
        case = service.get("c1")
        self.assertEqual("business_verified", case["badge"]["key"])
        self.assertEqual("verified", case["entityStatus"])

    def test_only_managed_cases_expire_automatically(self):
        service = ProviderVerificationService(self.con, now=self.now)
        provider = self.con.execute(
            "SELECT * FROM providers WHERE id='p1'"
        ).fetchone()
        service.ensure_case(provider)
        service.review(
            "p1",
            {
                "status": "verified",
                "identityStatus": "verified",
                "activityStatus": "verified",
                "expiresAt": (self.now - timedelta(days=1)).isoformat(),
            },
            reviewer_id="admin-1",
        )
        expired = service.expire_managed_cases()
        self.assertEqual(["p1"], expired)
        provider_state = self.con.execute(
            """SELECT verified,listing_enabled,request_enabled
            FROM providers WHERE id='p1'"""
        ).fetchone()
        self.assertEqual((0, 0, 0), tuple(provider_state))

    def test_complaint_has_evidence_timeline_and_guarded_transitions(self):
        self.con.execute(
            """INSERT INTO complaints(
            id,provider_id,customer_name,phone,reason,detail,status,priority,
            request_id,user_id)
            VALUES('cmp1','p1','User','96890000000','quality','Incomplete work',
            'open','high','r1','u1')"""
        )
        service = ComplaintCaseService(self.con, now=self.now)
        opened = service.open_existing(
            "cmp1",
            actor_kind="user",
            actor_id="u1",
            category="quality",
        )
        self.assertEqual("open", opened["status"])
        service.add_evidence(
            "cmp1",
            ["uploads/cmp1-problem1-evidence.png"],
            uploader_kind="user",
            uploader_id="u1",
        )
        investigating = service.update(
            "cmp1",
            {
                "status": "investigating",
                "priority": "high",
                "assignedAdminId": "admin-1",
            },
            admin_id="admin-1",
        )
        self.assertEqual("investigating", investigating["status"])
        with self.assertRaises(DomainError) as context:
            service.update(
                "cmp1",
                {"status": "closed", "priority": "high"},
                admin_id="admin-1",
            )
        self.assertEqual("complaint_resolution_required", context.exception.code)
        closed = service.update(
            "cmp1",
            {
                "status": "closed",
                "priority": "high",
                "resolution": "Provider returned and completed the work.",
                "outcome": "remedied",
            },
            admin_id="admin-1",
        )
        self.assertEqual("closed", closed["status"])
        reopened = service.reopen(
            "cmp1",
            "The issue returned.",
            actor_kind="user",
            actor_id="u1",
        )
        self.assertEqual("reopened", reopened["status"])
        self.assertEqual(1, len(reopened["evidence"]))
        self.assertGreaterEqual(len(reopened["timeline"]), 5)

    def test_blocks_are_idempotent_and_apply_in_both_directions(self):
        service = InteractionBlockService(self.con)
        first = service.block(
            "user",
            "u1",
            "provider",
            "p1",
            reason="Unwanted contact",
            request_id="r1",
        )
        second = service.block(
            "user",
            "u1",
            "provider",
            "p1",
            reason="Repeated",
            request_id="r1",
        )
        self.assertEqual(first["id"], second["id"])
        self.assertTrue(service.is_blocked("u1", "p1"))
        with self.assertRaises(DomainError) as context:
            service.assert_allowed("u1", "p1")
        self.assertEqual("interaction_blocked", context.exception.code)
        self.assertTrue(
            service.unblock("user", "u1", "provider", "p1")
        )
        self.assertFalse(service.is_blocked("u1", "p1"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
