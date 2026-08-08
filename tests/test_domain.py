import hashlib
import hmac
import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TEMP = tempfile.TemporaryDirectory(prefix="khadamati-domain-")
os.environ["KHADAMATI_DB_PATH"] = str(Path(TEMP.name) / "domain.sqlite3")
os.environ["KHADAMATI_UPLOAD_DIR"] = str(Path(TEMP.name) / "uploads")
os.environ["KHADAMATI_ENV"] = "test"
os.environ["KHADAMATI_ADMIN_CODE"] = "839174"

import server  # noqa: E402
from khadamati_domain import (  # noqa: E402
    ContactConsentService,
    DomainError,
    EntitlementService,
    OTPService,
    PLAN_IDS,
    PlanCatalog,
    PaymentAdapter,
    RequestMarketplace,
    SubscriptionService,
)
from khadamati_locations import (  # noqa: E402
    LocationCatalogService,
    location_snapshot,
    resolve_area,
)
from khadamati_rewards import (  # noqa: E402
    RewardCampaignService,
    loyalty_summary,
    record_loyalty_transaction,
)
from khadamati_workflow import (  # noqa: E402
    CompletionEvidenceService,
    RequestAgreementService,
    RequestIdempotencyService,
    RequestLifecycleService,
    ServiceAssetService,
)


class KhadamatiDomainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        server.init_db()

    def setUp(self):
        self.con = sqlite3.connect(server.DB_PATH)
        self.con.row_factory = sqlite3.Row

    def tearDown(self):
        self.con.rollback()
        self.con.close()

    def provider(self, suffix, *, cat="homecare", service="electrician", gov="مسقط", wilayah="السيب"):
        provider_id = f"test-provider-{suffix}"
        server.upsert_provider(
            self.con,
            {
                "id": provider_id,
                "name": f"مزود اختبار {suffix}",
                "phone": f"96891{int(suffix):06d}" if str(suffix).isdigit() else f"96892{abs(hash(suffix)) % 1_000_000:06d}",
                "pin": "7349",
                "gov": gov,
                "wilayah": wilayah,
                "areas": [gov, wilayah],
                "bio": "مزود مهني لخدمة العملاء",
                "hours": "الأحد 8:00 ص - 8:00 م",
                "status": "available",
                "active": True,
                "verified": True,
                "commercialNo": f"CR-{suffix}",
                "services": [
                    {
                        "id": f"service-{suffix}",
                        "catId": cat,
                        "serviceId": service,
                        "priceFrom": 5,
                        "active": True,
                        "areas": [wilayah],
                    }
                ],
                "workImages": [],
                "documents": [],
            },
        )
        return provider_id

    def activate(self, provider_id, plan_id="individual_gold_6m", now=None):
        return SubscriptionService(self.con, now=now).request_plan(
            provider_id, plan_id, payment_required=False, actor="test"
        )

    def user(self, suffix):
        user_id = f"test-user-{suffix}"
        self.con.execute(
            """INSERT INTO app_users(id,phone,name,pin_hash,status)
            VALUES(?,?,?,?, 'active')""",
            (user_id, f"96895{int(suffix):06d}", f"مستخدم {suffix}", server.hash_pin("7349")),
        )
        return user_id

    def customer_request(
        self,
        suffix,
        user_id,
        *,
        provider_id="",
        status="matching",
        requested_at="2026-08-01T10:00:00+00:00",
    ):
        request_id = f"test-request-{suffix}"
        self.con.execute(
            """INSERT INTO customer_requests(
            id,user_id,customer_name,phone,service_value,service_name,gov,wilayah,
            status,accepted_provider_id,matching_provider_ids,requested_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                request_id,
                user_id,
                f"مستخدم {suffix}",
                f"96895{int(suffix):06d}",
                "homecare|electrician",
                "كهربائي",
                "مسقط",
                "السيب",
                status,
                provider_id,
                json.dumps([provider_id] if provider_id else []),
                requested_at,
            ),
        )
        return request_id

    def test_only_current_split_plans_are_active(self):
        rows = self.con.execute("SELECT id FROM packages WHERE active=1 ORDER BY id").fetchall()
        self.assertEqual(sorted(PLAN_IDS), sorted(row["id"] for row in rows))
        self.assertEqual(len(PLAN_IDS), len(rows))

    def test_split_plan_entitlements_match_release_rules(self):
        individual_free = PlanCatalog.get(self.con, "individual_free_3m")
        individual_gold = PlanCatalog.get(self.con, "individual_gold_6m")
        company_silver = PlanCatalog.get(self.con, "company_silver_6m")
        company_gold = PlanCatalog.get(self.con, "company_gold_6m")
        self.assertEqual((90, 2, 1), (
            individual_free["duration_days"],
            individual_free["max_services"],
            individual_free["max_wilayats"],
        ))
        self.assertEqual((15, 3, 2, 30, 2, 60), (
            individual_gold["price"],
            individual_gold["max_services"],
            individual_gold["max_categories"],
            individual_gold["lead_delay_seconds"],
            individual_gold["community_package_quota"],
            individual_gold["community_package_days"],
        ))
        self.assertEqual((30, 3, 3, 1, 2, 30), (
            company_silver["price"],
            company_silver["max_services"],
            company_silver["max_categories"],
            company_silver["max_governorates"],
            company_silver["community_package_quota"],
            company_silver["community_package_days"],
        ))
        self.assertEqual((50, 5, 5, 2, 8, 4, 60), (
            company_gold["price"],
            company_gold["max_services"],
            company_gold["max_categories"],
            company_gold["max_governorates"],
            company_gold["max_images"],
            company_gold["community_package_quota"],
            company_gold["community_package_days"],
        ))

    def test_governorate_limit_is_enforced_by_subscription(self):
        provider_id = self.provider("1001")
        self.activate(provider_id, "individual_silver_6m")
        with self.assertRaises(DomainError) as caught:
            EntitlementService(self.con).validate_profile(
                provider_id,
                services=[{"catId": "homecare", "serviceId": "electrician"}],
                areas=["السيب"],
                governorates=["مسقط", "الداخلية"],
            )
        self.assertEqual("governorate_limit_exceeded", caught.exception.code)

    def test_seed_profiles_have_no_predictable_pin(self):
        seed_ids = [item["id"] for item in server.SEED_PROVIDERS]
        placeholders = ",".join("?" for _ in seed_ids)
        rows = self.con.execute(
            f"SELECT id,pin_hash FROM providers WHERE id IN ({placeholders})", seed_ids
        ).fetchall()
        for row in rows:
            self.assertFalse(server.verify_secret("1234", row["pin_hash"]))
            phone = next(item["phone"] for item in server.SEED_PROVIDERS if item["id"] == row["id"])
            self.assertFalse(server.verify_secret(str(phone)[-4:], row["pin_hash"]))

    def test_foundation_is_granted_once(self):
        provider_id = self.provider("101")
        first = self.activate(provider_id, "individual_free_3m")
        self.assertEqual("foundation", first["status"])
        with self.assertRaises(DomainError) as caught:
            self.activate(provider_id, "individual_free_3m")
        self.assertEqual("foundation_already_used", caught.exception.code)

    def test_expiry_grace_renewal_upgrade_and_downgrade(self):
        now = datetime(2026, 7, 18, 12, tzinfo=UTC)
        provider_id = self.provider("102")
        active = self.activate(provider_id, "individual_silver_6m", now)
        subscription_id = active["subscriptionId"]
        service = SubscriptionService(self.con, now=now)

        self.con.execute(
            "UPDATE subscriptions SET end_date=?,status='active' WHERE id=?",
            ((now - timedelta(days=1)).date().isoformat(), subscription_id),
        )
        self.assertEqual("grace", service.synchronize_provider(provider_id)["state"])
        self.con.execute(
            "UPDATE subscriptions SET end_date=?,status='active' WHERE id=?",
            ((now - timedelta(days=15)).date().isoformat(), subscription_id),
        )
        self.assertEqual("expired", service.synchronize_provider(provider_id)["state"])

        self.con.execute(
            "UPDATE subscriptions SET end_date=?,status='active' WHERE id=?",
            ((now + timedelta(days=90)).date().isoformat(), subscription_id),
        )
        service.synchronize_provider(provider_id)
        upgrade = service.request_plan(provider_id, "individual_gold_6m", actor="test")
        self.assertEqual("pending_payment", upgrade["status"])
        self.assertGreater(upgrade["amount"], 0)
        payment = PaymentAdapter(self.con).create_intent(upgrade["subscriptionId"], provider_id)
        PaymentAdapter(self.con, now=now).confirm_manual(payment["paymentId"], actor="test-admin")
        current = SubscriptionService(self.con, now=now).latest(provider_id)
        self.assertEqual("individual_gold_6m", current["package_id"])
        downgrade = SubscriptionService(self.con, now=now).request_plan(
            provider_id, "individual_silver_6m", actor="test"
        )
        self.assertEqual("next_renewal", downgrade["effective"])
        self.assertEqual("individual_silver_6m", downgrade["renewalPackageId"])

    def test_payment_amount_and_webhook_are_server_verified(self):
        provider_id = self.provider("103")
        pending = SubscriptionService(self.con).request_plan(provider_id, "individual_silver_6m")
        adapter = PaymentAdapter(self.con)
        with self.assertRaises(DomainError) as caught:
            adapter.create_intent(pending["subscriptionId"], provider_id, client_amount=0.1)
        self.assertEqual("payment_amount_mismatch", caught.exception.code)

        environment = {
            "KHADAMATI_PAYMENT_GATEWAY": "test-gateway",
            "KHADAMATI_PAYMENT_CHECKOUT_URL": "https://payments.invalid/checkout",
            "KHADAMATI_PAYMENT_WEBHOOK_SECRET": "unit-secret",
        }
        adapter = PaymentAdapter(self.con, environment=environment)
        intent = adapter.create_intent(pending["subscriptionId"], provider_id)
        payload = json.dumps(
            {
                "eventId": "event-domain-103",
                "reference": intent["reference"],
                "amount": intent["amount"],
                "currency": "OMR",
                "status": "paid",
            },
            separators=(",", ":"),
        ).encode()
        with self.assertRaises(DomainError) as caught:
            adapter.verify_webhook(payload, "bad-signature")
        self.assertEqual("invalid_webhook_signature", caught.exception.code)
        signature = hmac.new(b"unit-secret", payload, hashlib.sha256).hexdigest()
        result = adapter.verify_webhook(payload, signature)
        self.assertEqual("paid", result["status"])
        self.assertTrue(adapter.verify_webhook(payload, signature)["duplicate"])

    def test_matching_uses_exact_service_and_two_waves(self):
        now = datetime(2026, 7, 18, 12, tzinfo=UTC)
        exact_ids = []
        for index in range(201, 212):
            provider_id = self.provider(str(index))
            self.activate(provider_id, "individual_gold_6m", now)
            exact_ids.append(provider_id)
        wrong_id = self.provider("299", cat="tech", service="pc")
        self.activate(wrong_id, "individual_gold_6m", now)
        user_id = "domain-user-1"
        request_id = "domain-request-1"
        self.con.execute(
            "INSERT OR REPLACE INTO app_users(id,phone,name,pin_hash) VALUES(?,?,?,?)",
            (user_id, "96895550101", "مستخدم المطابقة", server.hash_pin("2468")),
        )
        self.con.execute(
            """INSERT OR REPLACE INTO customer_requests(
            id,user_id,customer_name,phone,service_value,service_name,gov,wilayah,status)
            VALUES(?,?,?,?,?,?,?,?, 'matching')""",
            (request_id, user_id, "مستخدم المطابقة", "96895550101", "homecare|electrician", "كهربائي", "مسقط", "السيب"),
        )
        marketplace = RequestMarketplace(self.con, now=now, expansion_minutes=20, min_offers=2)
        ranked = marketplace.schedule(request_id)
        self.assertEqual(10, len(ranked))
        self.assertNotIn(wrong_id, [item["providerId"] for item in ranked])
        first = marketplace.release_due(request_id)
        self.assertEqual(5, len(first))
        expanded = RequestMarketplace(
            self.con, now=now + timedelta(minutes=21), expansion_minutes=20, min_offers=2
        ).release_due(request_id)
        self.assertEqual(5, len(expanded))

    def test_plan_delay_starts_only_when_subscriptions_are_enabled(self):
        now = datetime(2026, 7, 18, 12, tzinfo=UTC)
        provider_id = self.provider("301")
        self.activate(provider_id, "individual_free_3m", now)
        user_id = "domain-user-delay"
        self.con.execute(
            "INSERT OR REPLACE INTO app_users(id,phone,name,pin_hash) VALUES(?,?,?,?)",
            (user_id, "96895550301", "مستخدم اختبار التأخير", server.hash_pin("2468")),
        )
        settings_row = self.con.execute(
            "SELECT value FROM settings WHERE key='platform'"
        ).fetchone()
        settings = json.loads(settings_row["value"])
        settings["subscriptionsEnabled"] = False
        self.con.execute(
            "UPDATE settings SET value=? WHERE key='platform'",
            (json.dumps(settings, ensure_ascii=False),),
        )
        self.con.execute(
            """INSERT OR REPLACE INTO customer_requests(
            id,user_id,customer_name,phone,service_value,service_name,gov,wilayah,status)
            VALUES(?,?,?,?,?,?,?,?, 'matching')""",
            (
                "domain-request-free-launch",
                user_id,
                "مستخدم اختبار التأخير",
                "96895550301",
                "homecare|electrician",
                "كهربائي",
                "مسقط",
                "السيب",
            ),
        )
        free_marketplace = RequestMarketplace(self.con, now=now)
        free_marketplace.schedule("domain-request-free-launch")
        released = free_marketplace.release_due("domain-request-free-launch")
        self.assertIn(provider_id, [item["providerId"] for item in released])

        settings["subscriptionsEnabled"] = True
        self.con.execute(
            "UPDATE settings SET value=? WHERE key='platform'",
            (json.dumps(settings, ensure_ascii=False),),
        )
        self.con.execute(
            """INSERT OR REPLACE INTO customer_requests(
            id,user_id,customer_name,phone,service_value,service_name,gov,wilayah,status)
            VALUES(?,?,?,?,?,?,?,?, 'matching')""",
            (
                "domain-request-paid-launch",
                user_id,
                "مستخدم اختبار التأخير",
                "96895550301",
                "homecare|electrician",
                "كهربائي",
                "مسقط",
                "السيب",
            ),
        )
        paid_marketplace = RequestMarketplace(self.con, now=now)
        paid_marketplace.schedule("domain-request-paid-launch")
        immediate_ids = [
            item["providerId"]
            for item in paid_marketplace.release_due("domain-request-paid-launch")
        ]
        self.assertNotIn(provider_id, immediate_ids)
        delayed = RequestMarketplace(
            self.con, now=now + timedelta(minutes=16)
        ).release_due("domain-request-paid-launch")
        self.assertIn(provider_id, [item["providerId"] for item in delayed])

    def test_contact_consent_is_scoped_and_revocable(self):
        provider_id = self.provider("104")
        user_id = "domain-user-2"
        request_id = "domain-request-2"
        self.con.execute(
            "INSERT OR REPLACE INTO app_users(id,phone,name,pin_hash) VALUES(?,?,?,?)",
            (user_id, "96895550102", "مستخدم الموافقة", server.hash_pin("2468")),
        )
        self.con.execute(
            """INSERT OR REPLACE INTO customer_requests(
            id,user_id,customer_name,phone,service_value,accepted_provider_id,status)
            VALUES(?,?,?,?,?,?,'accepted')""",
            (request_id, user_id, "مستخدم الموافقة", "96895550102", "homecare|electrician", provider_id),
        )
        consent = ContactConsentService(self.con)
        self.assertFalse(consent.allowed(request_id, provider_id, "whatsapp"))
        consent.set_channel(request_id, user_id, provider_id, "whatsapp", True)
        self.assertTrue(consent.allowed(request_id, provider_id, "whatsapp"))
        self.assertFalse(consent.allowed(request_id, provider_id, "call"))
        consent.set_channel(request_id, user_id, provider_id, "whatsapp", False)
        self.assertFalse(consent.allowed(request_id, provider_id, "whatsapp"))

    def test_individual_provider_respects_account_specific_plan_limits(self):
        provider_id = self.provider("105")
        self.activate(provider_id, "individual_silver_6m")
        entitlement = EntitlementService(self.con)
        valid = entitlement.validate_profile(
            provider_id,
            services=[
                {"catId": "cleaning", "serviceId": "home_cleaning"},
                {"catId": "cleaning", "serviceId": "carpet_cleaning"},
            ],
            areas=["السيب"],
        )
        self.assertEqual(2, valid["maxServices"])
        self.assertEqual(1, valid["maxCategories"])
        with self.assertRaises(DomainError) as wilayah_error:
            entitlement.validate_profile(
                provider_id,
                services=[{"catId": "cleaning", "serviceId": "home_cleaning"}],
                areas=["السيب", "بوشر"],
            )
        self.assertEqual("wilayah_limit_exceeded", wilayah_error.exception.code)
        with self.assertRaises(DomainError) as caught:
            entitlement.validate_profile(
                provider_id,
                services=[
                    {"catId": "cleaning", "serviceId": "home_cleaning"},
                    {"catId": "tech", "serviceId": "pc"},
                ],
                areas=["السيب"],
            )
        self.assertEqual("provider_category_limit", caught.exception.code)

    def test_foundation_limits_differ_for_individual_and_company_accounts(self):
        individual_id = self.provider("1051")
        company_id = self.provider("1052")
        self.con.execute(
            "UPDATE providers SET provider_type='company' WHERE id=?", (company_id,)
        )
        self.activate(individual_id, "individual_free_3m")
        self.activate(company_id, "company_free_3m")
        individual = EntitlementService(self.con).profile_limits(individual_id)
        company = EntitlementService(self.con).profile_limits(company_id)
        self.assertEqual(
            (2, 1, 2),
            (
                individual["maxServices"],
                individual["maxCategories"],
                individual["maxImages"],
            ),
        )
        self.assertEqual(
            (3, 3, 2),
            (
                company["maxServices"],
                company["maxCategories"],
                company["maxImages"],
            ),
        )
        self.assertEqual(0, company["maxWilayats"])

    def test_provider_persistence_rejects_a_second_individual_category(self):
        provider_id = self.provider("1053")
        provider = server.row_provider(
            self.con.execute("SELECT * FROM providers WHERE id=?", (provider_id,)).fetchone(),
            private=True,
        )
        provider["services"] = [
            {"catId": "homecare", "serviceId": "electrician", "active": True},
            {"catId": "tech", "serviceId": "pc", "active": True},
        ]
        with self.assertRaises(DomainError) as caught:
            server.upsert_provider(self.con, provider)
        self.assertEqual("provider_category_limit", caught.exception.code)

    def test_provider_persistence_grandfathers_existing_categories_without_data_loss(self):
        provider_id = self.provider("1054")
        legacy_services = [
            {"catId": "homecare", "serviceId": "electrician", "active": True},
            {"catId": "tech", "serviceId": "pc", "active": True},
        ]
        self.con.execute(
            "UPDATE providers SET services=? WHERE id=?",
            (json.dumps(legacy_services, ensure_ascii=False), provider_id),
        )
        provider = server.row_provider(
            self.con.execute("SELECT * FROM providers WHERE id=?", (provider_id,)).fetchone(),
            private=True,
        )
        provider["name"] = "مزود محفوظ بلا فقد بيانات"
        server.upsert_provider(self.con, provider)
        saved = server.row_provider(
            self.con.execute("SELECT * FROM providers WHERE id=?", (provider_id,)).fetchone(),
            private=True,
        )
        self.assertEqual(2, len(saved["services"]))

    def test_plan_seed_preserves_admin_account_limit_edits(self):
        plan = PlanCatalog.get(self.con, "individual_silver_6m", False)
        entitlements = plan["entitlements"]
        entitlements["accountLimits"]["individual"]["maxServices"] = 7
        self.con.execute(
            "UPDATE packages SET entitlements=? WHERE id=?",
            (json.dumps(entitlements, ensure_ascii=False), "individual_silver_6m"),
        )
        PlanCatalog.seed(self.con)
        refreshed = PlanCatalog.get(self.con, "individual_silver_6m", False)
        self.assertEqual(
            7,
            refreshed["entitlements"]["accountLimits"]["individual"]["maxServices"],
        )

    def test_business_plan_allows_multiple_services_and_categories_within_limit(self):
        provider_id = self.provider("106")
        self.con.execute("UPDATE providers SET provider_type='company' WHERE id=?", (provider_id,))
        self.activate(provider_id, "company_gold_6m")
        services = [
            {"catId": f"category-{index % 5}", "serviceId": f"service-{index}"}
            for index in range(5)
        ]
        limits = EntitlementService(self.con).validate_profile(
            provider_id, services=services, areas=["السيب", "بوشر"]
        )
        self.assertEqual(5, limits["maxServices"])
        self.assertEqual(5, limits["maxCategories"])
        with self.assertRaises(DomainError) as caught:
            EntitlementService(self.con).validate_profile(
                provider_id,
                services=services + [{"catId": "category-6", "serviceId": "service-6"}],
                areas=["السيب"],
            )
        self.assertEqual("provider_category_limit", caught.exception.code)

    def test_request_eligibility_requires_approved_available_provider(self):
        provider_id = self.provider("107")
        self.activate(provider_id, "individual_gold_6m")
        service = EntitlementService(self.con)
        self.assertTrue(service.can_receive(provider_id)[0])
        self.con.execute("UPDATE providers SET verified=0 WHERE id=?", (provider_id,))
        self.assertEqual("provider_not_approved", service.can_receive(provider_id)[1])
        self.con.execute("UPDATE providers SET verified=1,status='busy' WHERE id=?", (provider_id,))
        self.assertEqual("provider_unavailable", service.can_receive(provider_id)[1])

    def test_service_limits_cannot_be_bypassed_through_normalizer(self):
        rows = self.con.execute(
            "SELECT category_id,id FROM services WHERE active=1 ORDER BY category_id,id"
        ).fetchall()
        first = rows[0]
        same_category = next(
            row for row in rows if row["category_id"] == first["category_id"] and row["id"] != first["id"]
        )
        other_category = next(row for row in rows if row["category_id"] != first["category_id"])
        with self.assertRaises(DomainError) as caught:
            server.normalized_provider_services(
                self.con,
                [
                    {"catId": first["category_id"], "serviceId": first["id"]},
                    {"catId": same_category["category_id"], "serviceId": same_category["id"]},
                ],
                limit=1,
                category_limit=2,
            )
        self.assertEqual("service_limit_exceeded", caught.exception.code)
        with self.assertRaises(DomainError) as caught:
            server.normalized_provider_services(
                self.con,
                [
                    {"catId": first["category_id"], "serviceId": first["id"]},
                    {"catId": other_category["category_id"], "serviceId": other_category["id"]},
                ],
                limit=2,
                category_limit=1,
            )
        self.assertEqual("provider_category_limit", caught.exception.code)

    def test_map_location_is_exact_only_when_provider_allows_visibility(self):
        provider_id = self.provider("108")
        self.activate(provider_id, "individual_gold_6m")
        self.con.execute(
            "UPDATE providers SET latitude=?,longitude=?,map_visible=1 WHERE id=?",
            (23.612345, 58.241234, provider_id),
        )
        row = self.con.execute("SELECT * FROM providers WHERE id=?", (provider_id,)).fetchone()
        public = server.row_provider(row, private=False)
        self.assertEqual(23.612345, public["location"]["lat"])
        self.assertEqual(58.241234, public["location"]["lng"])
        self.con.execute("UPDATE providers SET map_visible=0 WHERE id=?", (provider_id,))
        row = self.con.execute("SELECT * FROM providers WHERE id=?", (provider_id,)).fetchone()
        self.assertIsNone(server.row_provider(row, private=False)["location"])

    def test_service_availability_excludes_unapproved_and_busy_providers(self):
        provider_id = self.provider("109")
        self.activate(provider_id, "individual_gold_6m")
        snapshot = server.service_availability_snapshot(self.con)
        self.assertGreater(snapshot["services"].get("homecare|electrician", 0), 0)
        self.con.execute("UPDATE providers SET status='busy' WHERE id=?", (provider_id,))
        busy = server.service_availability_snapshot(self.con)
        self.assertLess(
            busy["services"].get("homecare|electrician", 0),
            snapshot["services"].get("homecare|electrician", 0),
        )
        self.con.execute("UPDATE providers SET status='available',verified=0 WHERE id=?", (provider_id,))
        unapproved = server.service_availability_snapshot(self.con)
        self.assertEqual(
            busy["services"].get("homecare|electrician", 0),
            unapproved["services"].get("homecare|electrician", 0),
        )
    def test_production_otp_never_exposes_development_code(self):
        environment = {
            "KHADAMATI_ENV": "production",
            "KHADAMATI_DEV_OTP_CODE": "111111",
            "KHADAMATI_OTP_PEPPER": "pepper",
        }
        with self.assertRaises(DomainError) as caught:
            OTPService(self.con, environment=environment).request("96895550103", "login")
        self.assertEqual("otp_delivery_unavailable", caught.exception.code)

    def test_production_database_does_not_seed_sample_profiles(self):
        with tempfile.TemporaryDirectory(prefix="khadamati-production-seed-") as temp:
            old_db = server.DB_PATH
            old_uploads = server.UPLOAD_DIR
            old_sample = server.SAMPLE_DATA_ENABLED
            try:
                server.DB_PATH = Path(temp) / "production.sqlite3"
                server.UPLOAD_DIR = Path(temp) / "uploads"
                server.SAMPLE_DATA_ENABLED = False
                server.init_db()
                con = sqlite3.connect(server.DB_PATH)
                try:
                    provider_count = con.execute(
                        """SELECT COUNT(*) FROM providers
                        WHERE id IN ('p1','p2','p3','p4','p5','p6',
                                     'p7','p8','p9','p10','p11','p12')"""
                    ).fetchone()[0]
                    review_count = con.execute(
                        "SELECT COUNT(*) FROM reviews WHERE id IN ('rev_seed_1','rev_seed_2')"
                    ).fetchone()[0]
                finally:
                    con.close()
                self.assertEqual(0, provider_count)
                self.assertEqual(0, review_count)
            finally:
                server.DB_PATH = old_db
                server.UPLOAD_DIR = old_uploads
                server.SAMPLE_DATA_ENABLED = old_sample

    def test_admin_bootstrap_contains_only_admin_notifications(self):
        admin_related = f"admin-scope-{os.urandom(4).hex()}"
        user_related = f"user-scope-{os.urandom(4).hex()}"
        with server.db() as con:
            server.create_notification(
                con, "admin", "", "Admin scoped notification",
                related_id=admin_related,
            )
            server.create_notification(
                con, "user", "test-user", "User scoped notification",
                related_id=user_related,
            )
        try:
            data = server.get_bootstrap(
                {
                    "kind": "admin",
                    "id": "test-admin",
                    "name": "Test admin",
                    "role": "super_admin",
                    "permissions": server.ALL_PERMISSIONS,
                }
            )
            related_ids = {item["relatedId"] for item in data["notifications"]}
            self.assertIn(admin_related, related_ids)
            self.assertNotIn(user_related, related_ids)
            self.assertTrue(
                all(item["targetKind"] == "admin" for item in data["notifications"])
            )
        finally:
            with server.db() as con:
                con.execute(
                    "DELETE FROM app_notifications WHERE related_id IN (?,?)",
                    (admin_related, user_related),
                )

    def test_instant_request_creation_waits_for_slot_without_marketplace_dispatch(self):
        suffix = os.urandom(4).hex()
        user_id = f"test-instant-user-{suffix}"
        category_id = f"test-instant-category-{suffix}"
        service_id = f"test-instant-service-{suffix}"
        service_value = f"{category_id}|{service_id}"
        with server.db() as con:
            previous_flag = con.execute(
                "SELECT * FROM platform_feature_flags WHERE key='booking_v2'"
            ).fetchone()
            previous_flag = dict(previous_flag) if previous_flag else None
            con.execute(
                """INSERT INTO app_users(id,phone,name,pin_hash,status,gov,wilayah)
                VALUES(?,?,?,?, 'active','مسقط','السيب')""",
                (
                    user_id,
                    f"96897{int(suffix[:6], 16) % 1_000_000:06d}",
                    "مستخدم حجز فوري",
                    server.hash_pin("7349"),
                ),
            )
            con.execute(
                "INSERT INTO categories(id,icon,ar,en,active) VALUES(?, '',?,?,1)",
                (category_id, "اختبار فوري", "Instant test"),
            )
            con.execute(
                """INSERT INTO services(id,category_id,icon,ar,en,active)
                VALUES(?,?, '',?,?,1)""",
                (service_id, category_id, "خدمة فورية", "Instant service"),
            )
            server.FeatureFlagService(con).update(
                "booking_v2",
                {
                    "enabled": True,
                    "rolloutPercentage": 100,
                    "audiences": ["user"],
                },
                "test-admin",
            )
            server.BookingPolicyService(con).save(
                service_value,
                {
                    "fulfillmentMode": "instant",
                    "pricingMode": "fixed",
                    "fixedPriceAmount": 12,
                    "defaultDurationMinutes": 60,
                    "evidencePolicy": "optional",
                    "startVerificationMode": "none",
                    "autoCloseEnabled": True,
                    "completionWindowHours": 24,
                },
                "test-admin",
            )

        class DummyHandler:
            headers = {}

            def require_user(self):
                return {"kind": "user", "userId": user_id}

            @staticmethod
            def send_json(data, status=200, extra_headers=None):
                return status, data

            @staticmethod
            def send_domain_error(error):
                return error.status, {"error": error.code}

        request_id = ""
        try:
            payload = {
                    "customerName": "مستخدم حجز فوري",
                    "serviceValue": service_value,
                    "serviceName": "خدمة فورية",
                    "gov": "مسقط",
                    "wilayah": "السيب",
                    "urgency": "normal",
                    "scheduleType": "flexible",
                    "note": "اختبار عدم إرسال عروض",
                    "idempotencyKey": f"instant:create:{suffix}",
                }
            barrier = threading.Barrier(2)

            def create_once():
                barrier.wait(timeout=5)
                return server.Handler.user_post(
                    DummyHandler(), "/api/user/requests", payload
                )

            with ThreadPoolExecutor(max_workers=2) as executor:
                responses = list(executor.map(lambda _: create_once(), range(2)))
            self.assertEqual([200, 201], sorted(status for status, _ in responses))
            response_ids = {
                response["request"]["id"] for _, response in responses
            }
            self.assertEqual(1, len(response_ids))
            status, response = next(
                item for item in responses if item[0] == 201
            )
            request_id = response["request"]["id"]
            with server.db() as con:
                request_row = con.execute(
                    """SELECT status,offers_open,workflow_version,fulfillment_mode
                    FROM customer_requests WHERE id=?""",
                    (request_id,),
                ).fetchone()
                dispatch_count = con.execute(
                    "SELECT COUNT(*) n FROM request_dispatches WHERE request_id=?",
                    (request_id,),
                ).fetchone()["n"]
                booking_started = con.execute(
                    """SELECT detail FROM request_events
                    WHERE request_id=? AND event_type='booking_started'""",
                    (request_id,),
                ).fetchone()
            self.assertEqual("matching", request_row["status"])
            self.assertEqual(1, request_row["offers_open"])
            self.assertEqual("booking_v2", request_row["workflow_version"])
            self.assertEqual("instant", request_row["fulfillment_mode"])
            self.assertEqual(0, dispatch_count)
            self.assertEqual(
                {"fulfillmentMode": "instant"},
                json.loads(booking_started["detail"]),
            )
            self.assertEqual(0, response["matchedProviders"])
            self.assertEqual(0, response["notifiedProviders"])
            enabled_bootstrap = server.get_bootstrap(
                {"kind": "user", "userId": user_id}
            )
            enabled_service = next(
                service
                for category in enabled_bootstrap["categories"]
                if category["id"] == category_id
                for service in category["services"]
                if service["id"] == service_id
            )
            self.assertTrue(enabled_bootstrap["bookingV2Enabled"])
            self.assertEqual("instant", enabled_service["fulfillmentMode"])
            self.assertEqual(12, enabled_service["fixedPriceAmount"])
            self.assertEqual(60, enabled_service["defaultDurationMinutes"])
            with server.db() as con:
                server.FeatureFlagService(con).update(
                    "booking_v2",
                    {
                        "enabled": False,
                        "rolloutPercentage": 0,
                        "audiences": ["user"],
                    },
                    "test-admin",
                )
            disabled_bootstrap = server.get_bootstrap(
                {"kind": "user", "userId": user_id}
            )
            disabled_service = next(
                service
                for category in disabled_bootstrap["categories"]
                if category["id"] == category_id
                for service in category["services"]
                if service["id"] == service_id
            )
            self.assertFalse(disabled_bootstrap["bookingV2Enabled"])
            self.assertEqual("quoted", disabled_service["fulfillmentMode"])
        finally:
            with server.db() as con:
                if request_id:
                    con.execute(
                        "DELETE FROM app_notifications WHERE related_id=?", (request_id,)
                    )
                    con.execute(
                        "DELETE FROM customer_requests WHERE id=?", (request_id,)
                    )
                con.execute(
                    "DELETE FROM service_fulfillment_policies WHERE service_value=?",
                    (service_value,),
                )
                con.execute(
                    "DELETE FROM services WHERE id=? AND category_id=?",
                    (service_id, category_id),
                )
                con.execute("DELETE FROM categories WHERE id=?", (category_id,))
                con.execute("DELETE FROM app_users WHERE id=?", (user_id,))
                if previous_flag:
                    con.execute(
                        """UPDATE platform_feature_flags SET enabled=?,rollout_percentage=?,
                        audiences=?,config=?,updated_by=?,updated_at=? WHERE key='booking_v2'""",
                        (
                            previous_flag["enabled"],
                            previous_flag["rollout_percentage"],
                            previous_flag["audiences"],
                            previous_flag["config"],
                            previous_flag["updated_by"],
                            previous_flag["updated_at"],
                        ),
                    )
                else:
                    con.execute(
                        "DELETE FROM platform_feature_flags WHERE key='booking_v2'"
                    )

    def test_losing_matched_provider_cannot_bootstrap_private_workflow_data(self):
        suffix = str(700000 + int.from_bytes(os.urandom(3), "big") % 200000)
        user_id = self.user(suffix)
        winner_id = self.provider(f"{suffix}-winner")
        loser_id = self.provider(f"{suffix}-loser")
        request_id = self.customer_request(
            suffix,
            user_id,
            status="viewed",
        )
        offer = {
            "id": f"offer-{suffix}",
            "providerId": winner_id,
            "price": 20,
            "durationMinutes": 60,
            "scope": "Private accepted scope",
            "validUntil": "2027-01-01T00:00:00+00:00",
        }
        self.con.execute(
            """UPDATE customer_requests SET matching_provider_ids=?,offers=?,
            workflow_version='booking_v2',evidence_policy='optional'
            WHERE id=?""",
            (json.dumps([winner_id, loser_id]), json.dumps([offer]), request_id),
        )
        server.RequestWorkOrderService(self.con).accept_offer(
            request_id,
            user_id,
            offer["id"],
            offers=[offer],
        )
        self.con.execute(
            """INSERT INTO request_completion_evidence(
            request_id,provider_id,note,submitted_at)
            VALUES(?,?,?,CURRENT_TIMESTAMP)""",
            (request_id, winner_id, "Private completion evidence"),
        )
        server.RequestLifecycleService(self.con).record(
            request_id,
            "private_timeline_event",
            actor_kind="provider",
            actor_id=winner_id,
            detail={"private": "accepted-only"},
        )
        self.con.commit()
        try:
            bootstrap = server.get_bootstrap(
                {
                    "kind": "provider",
                    "providerId": loser_id,
                    "name": "Losing provider",
                    "permissions": ["requests"],
                }
            )
            item = next(
                request
                for request in bootstrap["customerRequests"]
                if request["id"] == request_id
            )
            self.assertEqual([], item["timeline"])
            self.assertIsNone(item["workOrder"])
            self.assertEqual([], item["workOrderVersions"])
            self.assertEqual([], item["changeOrders"])
            self.assertIsNone(item["completionEvidence"])
            self.assertEqual([], item["messages"])
            self.assertEqual([], item["offers"])
            self.assertNotIn("start_work", item["allowedActions"])
        finally:
            self.con.execute(
                "DELETE FROM customer_requests WHERE id=?", (request_id,)
            )
            self.con.execute("DELETE FROM providers WHERE id IN (?,?)", (winner_id, loser_id))
            self.con.execute("DELETE FROM app_users WHERE id=?", (user_id,))
            self.con.commit()

    def test_notification_get_supersedes_stale_request_action(self):
        suffix = str(650000 + int.from_bytes(os.urandom(3), "big") % 40000)
        user_id = self.user(suffix)
        provider_id = self.provider(f"stale-{suffix}")
        request_id = self.customer_request(suffix, user_id, status="viewed")
        offer = {
            "id": f"stale-offer-{suffix}",
            "providerId": provider_id,
            "price": 10,
            "durationMinutes": 60,
            "validUntil": "2027-01-01T00:00:00+00:00",
        }
        self.con.execute(
            """UPDATE customer_requests SET matching_provider_ids=?,offers=?,
            workflow_version='booking_v2' WHERE id=?""",
            (json.dumps([provider_id]), json.dumps([offer]), request_id),
        )
        server.RequestWorkOrderService(self.con).accept_offer(
            request_id, user_id, offer["id"], offers=[offer]
        )
        notification_id = server.create_notification(
            self.con,
            "provider",
            provider_id,
            "Open booking",
            entity_kind="request",
            entity_id=request_id,
            action_kind="open_booking",
            requires_action=True,
            state_version=99,
        )
        self.con.commit()

        class DummyHandler:
            path = f"/api/notifications/{notification_id}"

            @staticmethod
            def session():
                return {"kind": "provider", "providerId": provider_id}

            @staticmethod
            def send_json(data, status=200, extra_headers=None):
                return status, data

        try:
            status, response = server.Handler.do_GET(DummyHandler())
            self.assertEqual(200, status)
            self.assertTrue(response["stale"])
            self.assertEqual("state_version_changed", response["staleReason"])
            self.assertTrue(response["notification"]["supersededAt"])
            self.assertEqual(request_id, response["currentRequest"]["id"])
            self.assertEqual(1, response["currentRequest"]["stateVersion"])
        finally:
            with server.db() as con:
                con.execute(
                    "DELETE FROM app_notifications WHERE id=?", (notification_id,)
                )
                con.execute(
                    "DELETE FROM customer_requests WHERE id=?", (request_id,)
                )
                con.execute("DELETE FROM providers WHERE id=?", (provider_id,))
                con.execute("DELETE FROM app_users WHERE id=?", (user_id,))

    def test_notification_and_push_outbox_rollback_atomically(self):
        target_id = f"outbox-target-{os.urandom(4).hex()}"
        original_push_ready = server.push_ready
        server.push_ready = lambda: True
        con = sqlite3.connect(server.DB_PATH)
        con.row_factory = sqlite3.Row
        notification_id = ""
        try:
            con.execute("BEGIN IMMEDIATE")
            notification_id = server.create_notification(
                con,
                "user",
                target_id,
                "Action required",
                "Private details stay in app",
                type_="request",
                related_id="outbox-request",
                entity_kind="request",
                entity_id="outbox-request",
                action_kind="review_completion",
                requires_action=True,
                state_version=3,
            )
            outbox = con.execute(
                """SELECT payload_json,status FROM push_delivery_outbox
                WHERE notification_id=?""",
                (notification_id,),
            ).fetchone()
            self.assertIsNotNone(outbox)
            payload = json.loads(outbox["payload_json"])
            self.assertTrue(payload["requiresAction"])
            self.assertEqual("review_completion", payload["actionKind"])
            self.assertEqual("request", payload["entityKind"])
            self.assertEqual(3, payload["stateVersion"])
            self.assertEqual("خدماتي", payload["title"])
            self.assertNotIn("Private details", payload["body"])
            con.rollback()
            with server.db() as verify:
                self.assertIsNone(
                    verify.execute(
                        "SELECT id FROM app_notifications WHERE id=?",
                        (notification_id,),
                    ).fetchone()
                )
                self.assertIsNone(
                    verify.execute(
                        "SELECT id FROM push_delivery_outbox WHERE notification_id=?",
                        (notification_id,),
                    ).fetchone()
                )
        finally:
            con.rollback()
            con.close()
            server.push_ready = original_push_ready

    def test_push_outbox_dedupes_dispatches_committed_row_and_expires_old_row(self):
        suffix = os.urandom(4).hex()
        target_id = f"outbox-commit-{suffix}"
        dedupe_key = f"request:outbox-{suffix}:review_completion:v1:user:{target_id}"
        original_push_ready = server.push_ready
        original_deliver_push = server.deliver_push
        delivered = []
        server.push_ready = lambda: True
        notification_ids = []
        try:
            with server.db() as con:
                first = server.create_notification(
                    con,
                    "user",
                    target_id,
                    "Review completion",
                    "Open the app",
                    type_="request",
                    related_id=f"outbox-{suffix}",
                    dedupe_key=dedupe_key,
                    entity_kind="request",
                    entity_id=f"outbox-{suffix}",
                    action_kind="review_completion",
                    requires_action=True,
                    state_version=1,
                )
                duplicate = server.create_notification(
                    con,
                    "user",
                    target_id,
                    "Review completion",
                    "Open the app",
                    type_="request",
                    related_id=f"outbox-{suffix}",
                    dedupe_key=dedupe_key,
                    entity_kind="request",
                    entity_id=f"outbox-{suffix}",
                    action_kind="review_completion",
                    requires_action=True,
                    state_version=1,
                )
                expired = server.create_notification(
                    con,
                    "user",
                    target_id,
                    "Expired reminder",
                    "Open the app",
                    type_="request",
                    related_id=f"expired-{suffix}",
                    dedupe_key=f"expired:{suffix}",
                    entity_kind="request",
                    entity_id=f"expired-{suffix}",
                    action_kind="review_completion",
                    requires_action=True,
                    state_version=1,
                    expires_at=(datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
                )
                notification_ids.extend([first, expired])
                self.assertEqual(first, duplicate)
                self.assertEqual(
                    1,
                    con.execute(
                        "SELECT COUNT(*) n FROM push_delivery_outbox WHERE notification_id=?",
                        (first,),
                    ).fetchone()["n"],
                )
            server.deliver_push = lambda kind, target, payload: (
                delivered.append((kind, target, payload)) or True
            )
            result = server.process_push_outbox(limit=20)
            self.assertEqual(1, result["claimed"])
            self.assertEqual(1, result["delivered"])
            self.assertEqual(1, len(delivered))
            self.assertEqual(first, delivered[0][2]["id"])
            with server.db() as con:
                delivered_row = con.execute(
                    "SELECT status,attempts FROM push_delivery_outbox WHERE notification_id=?",
                    (first,),
                ).fetchone()
                expired_row = con.execute(
                    "SELECT status,last_error FROM push_delivery_outbox WHERE notification_id=?",
                    (expired,),
                ).fetchone()
            self.assertEqual("delivered", delivered_row["status"])
            self.assertEqual(1, delivered_row["attempts"])
            self.assertEqual("expired", expired_row["status"])
            self.assertEqual("notification_expired", expired_row["last_error"])
        finally:
            server.deliver_push = original_deliver_push
            server.push_ready = original_push_ready
            with server.db() as con:
                for notification_id in notification_ids:
                    con.execute(
                        "DELETE FROM app_notifications WHERE id=?", (notification_id,)
                    )

    def test_push_outbox_stops_after_eight_attempts(self):
        suffix = os.urandom(4).hex()
        original_push_ready = server.push_ready
        original_deliver_push = server.deliver_push
        server.push_ready = lambda: True
        notification_id = ""
        try:
            with server.db() as con:
                notification_id = server.create_notification(
                    con,
                    "user",
                    f"retry-{suffix}",
                    "Retry bounded",
                    "Open the app",
                    dedupe_key=f"retry:{suffix}",
                )
                con.execute(
                    """UPDATE push_delivery_outbox SET attempts=7,available_at=?
                    WHERE notification_id=?""",
                    ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), notification_id),
                )
            server.deliver_push = lambda kind, target, payload: False
            result = server.process_push_outbox(limit=20)
            self.assertEqual(1, result["retried"])
            with server.db() as con:
                row = con.execute(
                    "SELECT status,attempts FROM push_delivery_outbox WHERE notification_id=?",
                    (notification_id,),
                ).fetchone()
            self.assertEqual("dead", row["status"])
            self.assertEqual(8, row["attempts"])
        finally:
            server.deliver_push = original_deliver_push
            server.push_ready = original_push_ready
            if notification_id:
                with server.db() as con:
                    con.execute(
                        "DELETE FROM app_notifications WHERE id=?", (notification_id,)
                    )

    def test_review_tags_are_whitelisted_and_request_cannot_be_reviewed_twice(self):
        suffix = str(500000 + int.from_bytes(os.urandom(3), "big") % 150000)
        user_id = self.user(suffix)
        provider_id = self.provider(f"review-{suffix}")
        request_id = self.customer_request(
            suffix,
            user_id,
            provider_id=provider_id,
            status="closed",
        )
        self.con.commit()

        class DummyHandler:
            @staticmethod
            def require_user():
                return {"kind": "user", "userId": user_id}

            @staticmethod
            def send_json(data, status=200, extra_headers=None):
                return status, data

            @staticmethod
            def send_domain_error(error):
                return error.status, {"error": error.code}

        base = {
            "providerId": provider_id,
            "requestId": request_id,
            "rating": 5,
            "comment": "Excellent",
        }
        try:
            invalid_status, invalid = server.Handler.save_review(
                DummyHandler(), {**base, "tags": ["quality", "unknown"]}
            )
            self.assertEqual(400, invalid_status)
            self.assertEqual("invalid_review_tags", invalid["error"])
            status, created = server.Handler.save_review(
                DummyHandler(),
                {**base, "tags": ["quality", "punctual", "quality"]},
            )
            self.assertEqual(201, status, created)
            self.assertEqual(["quality", "punctual"], created["review"]["tags"])
            duplicate_status, duplicate = server.Handler.save_review(
                DummyHandler(), {**base, "tags": ["communication"]}
            )
            self.assertEqual(409, duplicate_status)
            self.assertEqual("request_already_reviewed", duplicate["error"])
            with server.db() as con:
                stored = con.execute(
                    "SELECT * FROM reviews WHERE request_id=? AND user_id=?",
                    (request_id, user_id),
                ).fetchone()
                public = server.row_review(stored)
                analytics = con.execute(
                    """SELECT detail FROM request_events
                    WHERE request_id=? AND event_type='rating_submitted'""",
                    (request_id,),
                ).fetchone()
            self.assertEqual(["quality", "punctual"], public["tags"])
            self.assertEqual(
                {"rating": 5, "tagCount": 2}, json.loads(analytics["detail"])
            )
            self.assertNotIn("Excellent", analytics["detail"])
        finally:
            with server.db() as con:
                con.execute("DELETE FROM reviews WHERE request_id=?", (request_id,))
                con.execute(
                    "DELETE FROM customer_requests WHERE id=?", (request_id,)
                )
                con.execute("DELETE FROM providers WHERE id=?", (provider_id,))
                con.execute("DELETE FROM app_users WHERE id=?", (user_id,))

    def test_whatsapp_audit_masks_the_target_phone(self):
        detail = f"audit-{os.urandom(4).hex()}"
        server.log_whatsapp("96895550177", "failed", detail)
        try:
            with server.db() as con:
                row = con.execute(
                    "SELECT target,detail FROM whatsapp_logs WHERE detail=?",
                    (detail,),
                ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual("***0177", row["target"])
            self.assertNotIn("96895550177", row["target"])
        finally:
            with server.db() as con:
                con.execute("DELETE FROM whatsapp_logs WHERE detail=?", (detail,))

    def test_push_binding_keeps_same_endpoint_for_user_and_provider_roles(self):
        endpoint = f"https://push.example.test/{os.urandom(8).hex()}"
        self.con.execute(
            """INSERT INTO push_subscription_bindings(
            id,target_kind,target_id,endpoint,subscription_json)
            VALUES('push-user-test','user','user-push-test',?,?)""",
            (endpoint, json.dumps({"endpoint": endpoint, "keys": {"auth": "a"}})),
        )
        self.con.execute(
            """INSERT INTO push_subscription_bindings(
            id,target_kind,target_id,endpoint,subscription_json)
            VALUES('push-provider-test','provider','provider-push-test',?,?)""",
            (endpoint, json.dumps({"endpoint": endpoint, "keys": {"auth": "b"}})),
        )
        rows = self.con.execute(
            """SELECT target_kind,target_id FROM push_subscription_bindings
            WHERE endpoint=? ORDER BY target_kind""",
            (endpoint,),
        ).fetchall()
        self.assertEqual(
            [("provider", "provider-push-test"), ("user", "user-push-test")],
            [(row["target_kind"], row["target_id"]) for row in rows],
        )
        self.con.execute(
            """INSERT INTO push_subscription_bindings(
            id,target_kind,target_id,endpoint,subscription_json)
            VALUES('push-user-retry','user','user-push-test',?,?)
            ON CONFLICT(target_kind,target_id,endpoint) DO UPDATE SET
            subscription_json=excluded.subscription_json,active=1""",
            (endpoint, json.dumps({"endpoint": endpoint, "keys": {"auth": "new"}})),
        )
        count = self.con.execute(
            "SELECT COUNT(*) n FROM push_subscription_bindings WHERE endpoint=?",
            (endpoint,),
        ).fetchone()["n"]
        self.assertEqual(2, count)

    def test_push_unsubscribe_disables_only_the_authenticated_role_binding(self):
        endpoint = f"https://push.example.test/{os.urandom(8).hex()}"
        with server.db() as con:
            con.execute(
                """INSERT INTO push_subscription_bindings(
                id,target_kind,target_id,endpoint,subscription_json)
                VALUES('unsubscribe-user','user','unsubscribe-user-id',?,?)""",
                (endpoint, json.dumps({"endpoint": endpoint})),
            )
            con.execute(
                """INSERT INTO push_subscription_bindings(
                id,target_kind,target_id,endpoint,subscription_json)
                VALUES('unsubscribe-provider','provider','unsubscribe-provider-id',?,?)""",
                (endpoint, json.dumps({"endpoint": endpoint})),
            )

        class DummyHandler:
            @staticmethod
            def session():
                return {"kind": "user", "userId": "unsubscribe-user-id"}

            @staticmethod
            def send_json(data, status=200, extra_headers=None):
                return status, data

        try:
            status, response = server.Handler.push_subscribe(
                DummyHandler(), {"action": "unsubscribe", "endpoint": endpoint}
            )
            self.assertEqual(200, status)
            self.assertEqual(1, response["disabledBindings"])
            self.assertEqual(1, response["remainingBindingsForEndpoint"])
            with server.db() as con:
                states = {
                    row["target_kind"]: bool(row["active"])
                    for row in con.execute(
                        """SELECT target_kind,active FROM push_subscription_bindings
                        WHERE endpoint=?""",
                        (endpoint,),
                    )
                }
            self.assertEqual({"user": False, "provider": True}, states)
        finally:
            with server.db() as con:
                con.execute(
                    "DELETE FROM push_subscription_bindings WHERE endpoint=?",
                    (endpoint,),
                )
    def test_request_lifecycle_rejects_invalid_stage_jump(self):
        user_id = self.user("701")
        request_id = self.customer_request("701", user_id)
        lifecycle = RequestLifecycleService(self.con)
        lifecycle.transition(
            request_id,
            "viewed",
            actor_kind="user",
            actor_id=user_id,
            event_type="test_viewed",
        )
        with self.assertRaises(DomainError) as caught:
            lifecycle.transition(
                request_id,
                "closed",
                actor_kind="user",
                actor_id=user_id,
            )
        self.assertEqual("invalid_request_transition", caught.exception.code)
        timeline = lifecycle.timeline(request_id)
        self.assertEqual("test_viewed", timeline[-1]["type"])
        self.assertEqual("viewed", timeline[-1]["toStatus"])

    def test_agreement_requires_current_version_and_both_parties(self):
        user_id = self.user("702")
        provider_id = self.provider("702")
        request_id = self.customer_request(
            "702", user_id, provider_id=provider_id, status="accepted"
        )
        service = RequestAgreementService(self.con)
        agreement = service.save(
            request_id,
            "user",
            user_id,
            {
                "appointmentAt": "2026-08-01T10:00:00+00:00",
                "durationMinutes": 90,
                "priceAmount": 12.5,
                "notes": "فحص وتنفيذ",
            },
        )
        pending = service.get(request_id)
        self.assertEqual("pending_confirmation", pending["status"])
        self.assertTrue(pending["userConfirmed"])
        with self.assertRaises(DomainError) as sender_attempt:
            service.confirm(request_id, "user", user_id, agreement["version"])
        self.assertEqual("agreement_sender_cannot_confirm", sender_attempt.exception.code)
        confirmed = service.confirm(
            request_id, "provider", provider_id, agreement["version"]
        )
        self.assertEqual("confirmed", confirmed["status"])
        status = self.con.execute(
            "SELECT status FROM customer_requests WHERE id=?", (request_id,)
        ).fetchone()["status"]
        self.assertEqual("appointmentConfirmed", status)
        with self.assertRaises(DomainError) as caught:
            service.confirm(request_id, "user", user_id, agreement["version"] - 1)
        self.assertEqual("agreement_version_changed", caught.exception.code)

    def test_service_asset_is_owned_and_keeps_request_history(self):
        owner_id = self.user("703")
        other_id = self.user("704")
        service = ServiceAssetService(self.con)
        asset = service.save(
            owner_id,
            {
                "name": "منزل السيب",
                "type": "home",
                "brand": "",
                "model": "",
                "location": {"lat": 23.59, "lng": 58.20},
                "details": {
                    "houseNumber": "142",
                    "wayNumber": "3812",
                    "plateNumber": "must-not-be-kept",
                },
            },
        )
        self.assertEqual(
            {"houseNumber": "142", "wayNumber": "3812"}, asset["details"]
        )
        request_id = self.customer_request("703", owner_id)
        service.attach(request_id, asset["id"], owner_id)
        history = service.history(asset["id"], owner_id)
        self.assertEqual(request_id, history[0]["id"])
        with self.assertRaises(DomainError):
            service.get_for_user(asset["id"], other_id)

    def test_request_idempotency_blocks_duplicate_and_key_reuse(self):
        user_id = self.user("705")
        request_id = self.customer_request("705", user_id)
        service = RequestIdempotencyService(self.con)
        payload = {
            "serviceValue": "homecare|electrician",
            "gov": "مسقط",
            "wilayah": "السيب",
        }
        key = "request:test:705"
        service.remember(user_id, key, request_id, payload)
        self.assertEqual(request_id, service.find(user_id, key, payload))
        with self.assertRaises(DomainError) as caught:
            service.find(user_id, key, {**payload, "wilayah": "بوشر"})
        self.assertEqual("idempotency_key_reused", caught.exception.code)

    def test_completion_evidence_waits_for_customer_decision(self):
        user_id = self.user("706")
        provider_id = self.provider("706")
        request_id = self.customer_request(
            "706", user_id, provider_id=provider_id, status="inProgress"
        )
        service = CompletionEvidenceService(self.con)
        service.submit(
            request_id,
            provider_id,
            before_images=["uploads/before.webp"],
            after_images=["uploads/after.webp"],
            note="اكتمل الإصلاح",
        )
        status = self.con.execute(
            "SELECT status FROM customer_requests WHERE id=?", (request_id,)
        ).fetchone()["status"]
        self.assertEqual("awaitingConfirmation", status)
        service.decide(request_id, user_id, "resolved")
        status = self.con.execute(
            "SELECT status FROM customer_requests WHERE id=?", (request_id,)
        ).fetchone()["status"]
        completed = self.con.execute(
            "SELECT completed_jobs FROM providers WHERE id=?", (provider_id,)
        ).fetchone()["completed_jobs"]
        self.assertEqual("closed", status)
        self.assertEqual(1, completed)

    def test_marketplace_respects_provider_daily_capacity(self):
        user_id = self.user("707")
        provider_id = self.provider("707")
        self.activate(provider_id)
        self.con.execute(
            "UPDATE providers SET availability=? WHERE id=?",
            (
                json.dumps(
                    {
                        "days": ["5"],
                        "start": "00:00",
                        "end": "23:59",
                        "dailyCapacity": 1,
                    }
                ),
                provider_id,
            ),
        )
        self.customer_request(
            "708",
            user_id,
            provider_id=provider_id,
            status="accepted",
            requested_at="2026-08-01T09:00:00+00:00",
        )
        request_id = self.customer_request(
            "709",
            user_id,
            requested_at="2026-08-01T11:00:00+00:00",
        )
        ranked = RequestMarketplace(
            self.con, now=datetime(2026, 8, 1, 8, tzinfo=UTC)
        ).schedule(request_id)
        self.assertNotIn(provider_id, {item["providerId"] for item in ranked})

    def test_reward_campaign_uses_server_progress_and_single_eligibility(self):
        user_id = self.user("801")
        self.customer_request("801", user_id, status="closed")
        self.customer_request("802", user_id, status="completed")
        now = datetime(2026, 8, 1, 8, tzinfo=UTC)
        service = RewardCampaignService(self.con, now=now)
        service.save(
            "test-campaign-user",
            {
                "nameAr": "حملة العملاء",
                "nameEn": "Customer campaign",
                "descriptionAr": "أكمل طلبين للتأهل",
                "descriptionEn": "Complete two requests to qualify",
                "audience": "user",
                "rewardType": "draw",
                "rewardLabelAr": "سحب تحدده الإدارة",
                "rewardLabelEn": "Management-defined draw",
                "metric": "completed_requests",
                "target": 2,
                "startsAt": (now - timedelta(hours=1)).isoformat(),
                "endsAt": (now + timedelta(days=2)).isoformat(),
                "status": "active",
            },
        )
        first = service.for_subject("user", user_id)
        second = service.for_subject("user", user_id)
        self.assertEqual(1, len(first))
        self.assertEqual(2, first[0]["progress"])
        self.assertTrue(first[0]["eligible"])
        self.assertEqual(first, second)
        count = self.con.execute(
            """SELECT COUNT(*) n FROM campaign_eligibility
            WHERE campaign_id=? AND subject_id=?""",
            ("test-campaign-user", user_id),
        ).fetchone()["n"]
        self.assertEqual(1, count)
        service.update_status("test-campaign-user", "paused")
        self.assertEqual([], service.for_subject("user", user_id))

    def test_loyalty_target_recalculates_without_duplicate_points(self):
        user_id = self.user("803")
        for suffix in ("803", "804", "805"):
            self.customer_request(suffix, user_id, status="closed")
        self.assertTrue(
            record_loyalty_transaction(
                self.con, user_id, 10, "completed_request", "test:loyalty:803"
            )
        )
        self.assertFalse(
            record_loyalty_transaction(
                self.con, user_id, 10, "completed_request", "test:loyalty:803"
            )
        )
        capped = loyalty_summary(self.con, user_id, target=8, cycle_mode="cap")
        smaller = loyalty_summary(self.con, user_id, target=3, cycle_mode="cap")
        repeating = loyalty_summary(
            self.con, user_id, target=2, cycle_mode="repeat"
        )
        self.assertEqual(3, capped["completedRequests"])
        self.assertEqual(37.5, capped["percent"])
        self.assertEqual(100, smaller["percent"])
        self.assertEqual(1, repeating["progress"])
        self.assertEqual(1, repeating["completedCycles"])
        self.assertEqual(10, capped["points"])

    def test_location_catalog_reverse_lookup_and_safe_pause(self):
        service = LocationCatalogService(self.con)
        area = resolve_area(self.con, 23.6703, 58.1891)
        self.assertIsNotNone(area)
        self.assertEqual("muscat", area["governorateId"])
        self.assertEqual("muscat-seeb", area["wilayahId"])
        service.apply(
            {
                "action": "set_wilayat_active",
                "id": "muscat-seeb",
                "active": False,
            }
        )
        active = location_snapshot(self.con)
        muscat = next(item for item in active if item["id"] == "muscat")
        self.assertNotIn("muscat-seeb", {item["id"] for item in muscat["w"]})
        historic = location_snapshot(self.con, include_inactive=True)
        historic_muscat = next(item for item in historic if item["id"] == "muscat")
        paused = next(
            item for item in historic_muscat["w"] if item["id"] == "muscat-seeb"
        )
        self.assertFalse(paused["active"])

    def test_location_catalog_rejects_duplicates_and_in_use_deletion(self):
        service = LocationCatalogService(self.con)
        service.apply(
            {
                "action": "save_governorate",
                "id": "test-governorate",
                "ar": "محافظة اختبار",
                "en": "Test Governorate",
                "sortOrder": 90,
            }
        )
        service.apply(
            {
                "action": "save_wilayat",
                "id": "test-governorate-test-wilayat",
                "governorateId": "test-governorate",
                "ar": "ولاية اختبار",
                "en": "Test Wilayat",
                "lat": 23.5,
                "lng": 58.1,
            }
        )
        with self.assertRaises(DomainError) as duplicate:
            service.apply(
                {
                    "action": "save_wilayat",
                    "id": "test-governorate-other",
                    "governorateId": "test-governorate",
                    "ar": "ولاية اختبار",
                    "en": "Another Wilayat",
                }
            )
        self.assertEqual("wilayat_already_exists", duplicate.exception.code)
        user_id = self.user("806")
        self.con.execute(
            "UPDATE app_users SET gov=?,wilayah=? WHERE id=?",
            ("محافظة اختبار", "ولاية اختبار", user_id),
        )
        with self.assertRaises(DomainError) as in_use:
            service.apply(
                {
                    "action": "delete_wilayat",
                    "id": "test-governorate-test-wilayat",
                }
            )
        self.assertEqual("location_in_use", in_use.exception.code)


if __name__ == "__main__":
    unittest.main(verbosity=2)
