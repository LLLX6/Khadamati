import sqlite3
import unittest
from datetime import UTC, datetime, timedelta

from khadamati_domain import DomainError
from khadamati_platform import (
    ConversationControlService,
    EnterpriseAPIService,
    FeatureFlagService,
    FinancialScenarioService,
    MaintenanceContractService,
    OrganizationService,
    ProviderLegalProfileService,
    ReferralService,
    TrainingAchievementService,
    install_platform_schema,
)


class PlatformServiceTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        self.con.executescript(
            """
            CREATE TABLE app_users(
              id TEXT PRIMARY KEY,name TEXT,phone TEXT,status TEXT DEFAULT 'active'
            );
            CREATE TABLE providers(
              id TEXT PRIMARY KEY,name TEXT,provider_type TEXT,nationality TEXT,
              active INTEGER,verified INTEGER,completed_jobs INTEGER,reviews INTEGER,
              rating REAL,response_score INTEGER
            );
            CREATE TABLE customer_requests(
              id TEXT PRIMARY KEY,user_id TEXT,customer_name TEXT,status TEXT,
              service_value TEXT,accepted_provider_id TEXT,offers TEXT DEFAULT '[]',
              updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE payments(
              amount REAL,status TEXT,kind TEXT
            );
            CREATE TABLE subscriptions(
              provider_id TEXT,status TEXT
            );
            INSERT INTO app_users VALUES('u1','Customer','96890000001','active');
            INSERT INTO app_users VALUES('u2','Other','96890000002','active');
            INSERT INTO providers VALUES(
              'p1','Provider','individual','Omani',1,1,6,12,4.8,92
            );
            INSERT INTO customer_requests(
              id,user_id,customer_name,status,service_value,accepted_provider_id,offers
            ) VALUES(
              'r1','u1','Customer','completed','home|electric','p1',
              '[{"status":"accepted","warrantyDays":30}]'
            );
            """
        )
        install_platform_schema(self.con)

    def tearDown(self):
        self.con.close()

    def test_conversation_mute_end_and_owner_reopen(self):
        service = ConversationControlService(self.con)
        muted = service.update("r1", "user", "u1", "mute", {"hours": 2})
        self.assertTrue(muted["muted"])
        ended = service.update("r1", "user", "u1", "end", {"reason": "done"})
        self.assertEqual(ended["status"], "ended")
        with self.assertRaises(DomainError):
            service.assert_open("r1", "provider", "p1")
        with self.assertRaises(DomainError):
            service.update("r1", "provider", "p1", "reopen", {})
        reopened = service.update("r1", "user", "u1", "reopen", {})
        self.assertEqual(reopened["status"], "open")
        with self.assertRaises(DomainError):
            service.summary("r1", "user", "u2")

    def test_legal_paths_are_separate_and_admin_reviewed(self):
        service = ProviderLegalProfileService(self.con)
        with self.assertRaises(DomainError):
            service.save("p1", {"pathway": "individual_foreign", "nationality": "Indian"})
        profile = service.save(
            "p1",
            {
                "pathway": "individual_foreign",
                "nationality": "Indian",
                "employerName": "Example Establishment",
                "workPermitExpiry": "2028-01-01",
                "residencyExpiry": "2028-01-01",
                "employerAuthorizationStatus": "submitted",
            },
        )
        self.assertEqual(profile["reviewStatus"], "pending")
        reviewed = service.review("p1", "approved", "admin-1", "checked")
        self.assertEqual(reviewed["reviewStatus"], "approved")

    def test_organization_is_owner_scoped(self):
        service = OrganizationService(self.con)
        organization = service.save(
            "u1", {"name": "Operations", "approvalMode": "two_step"}
        )
        service.add_member(
            organization["id"],
            "u1",
            {"name": "Requester", "phone": "96891112222", "role": "requester"},
        )
        updated = service.add_location(
            organization["id"],
            "u1",
            {"name": "Muscat office", "gov": "Muscat", "wilayah": "Seeb"},
        )
        self.assertEqual(len(updated["members"]), 2)
        self.assertEqual(len(updated["locations"]), 1)
        with self.assertRaises(DomainError):
            service.get(organization["id"], "u2")

    def test_contract_requires_owner_provider_relationship(self):
        service = MaintenanceContractService(
            self.con, now=datetime(2026, 8, 1, tzinfo=UTC)
        )
        contract = service.create(
            "u1",
            {
                "providerId": "p1",
                "requestId": "r1",
                "serviceValue": "home|electric",
                "title": "Quarterly check",
                "frequencyDays": 90,
                "amount": 25,
            },
        )
        self.assertEqual(contract["status"], "active")
        self.assertEqual(contract["frequencyDays"], 90)
        paused = service.update_status(contract["id"], "user", "u1", "paused")
        self.assertEqual(paused["status"], "paused")
        with self.assertRaises(DomainError):
            service.update_status(contract["id"], "user", "u2", "cancelled")

    def test_referral_is_unique_and_qualification_is_review_only(self):
        service = ReferralService(self.con)
        code = service.create_code("user", "u1")
        with self.assertRaises(DomainError):
            service.claim(code["code"], "user", "u1")
        claimed = service.claim(code["code"], "provider", "p1")
        self.assertEqual(claimed["status"], "claimed")
        service.qualify("provider", "p1")
        rows = service.list_for("user", "u1")
        qualified = next(item for item in rows if item["status"] == "qualified")
        self.assertEqual(qualified["rewardStatus"], "eligible_for_review")
        with self.assertRaises(DomainError):
            service.claim(code["code"], "provider", "p1")

    def test_training_achievements_are_earned_from_data(self):
        service = TrainingAchievementService(self.con)
        modules = service.list_modules("p1", "individual")
        self.assertGreaterEqual(len(modules), 3)
        service.complete("p1", modules[0]["id"], 95)
        service.complete("p1", modules[1]["id"], 95)
        achievements = service.recompute_achievements("p1")
        codes = {item["code"] for item in achievements}
        self.assertIn("trained_provider", codes)
        self.assertIn("first_five_jobs", codes)
        self.assertIn("responsive_provider", codes)
        self.assertIn("customer_favorite", codes)

    def test_feature_rollout_is_deterministic(self):
        service = FeatureFlagService(self.con)
        service.update(
            "test_feature",
            {"enabled": True, "rolloutPercentage": 50, "audiences": ["user"]},
            "admin-1",
        )
        first = service.is_enabled("test_feature", "user", "u1")
        self.assertEqual(first, service.is_enabled("test_feature", "user", "u1"))
        self.assertFalse(service.is_enabled("test_feature", "provider", "p1"))

    def test_enterprise_key_is_one_time_scoped_and_revocable(self):
        organization = OrganizationService(self.con).save("u1", {"name": "Business"})
        service = EnterpriseAPIService(self.con)
        client = service.create_client(
            organization["id"], "Integration", ["reports:read"], 10
        )
        self.assertTrue(client["apiKey"].startswith("khd_"))
        authenticated = service.authenticate(client["apiKey"], "reports:read")
        self.assertEqual(authenticated["organizationId"], organization["id"])
        with self.assertRaises(DomainError):
            service.authenticate(client["apiKey"], "requests:read")
        service.revoke(client["id"])
        with self.assertRaises(DomainError):
            service.authenticate(client["apiKey"], "reports:read")

    def test_financial_scenario_keeps_actual_baseline_separate(self):
        self.con.execute("INSERT INTO payments VALUES(100,'paid','subscription')")
        self.con.execute("INSERT INTO subscriptions VALUES('p1','active')")
        result = FinancialScenarioService(self.con).calculate(
            {
                "providerCount": 100,
                "paidRatio": 25,
                "averageMonthlyPlan": 5,
                "promotionRevenue": 10,
            }
        )
        self.assertEqual(result["baseline"]["confirmedRevenue"], 100)
        self.assertEqual(result["results"]["projectedMonthlyRevenue"], 135)
        self.assertEqual(result["disclaimer"], "scenario_not_accounting_record")


if __name__ == "__main__":
    unittest.main()
