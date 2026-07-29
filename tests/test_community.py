from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import sqlite3
import unittest

from khadamati_community import (
    CommunityService,
    install_community_schema,
    run_community_maintenance,
)
from khadamati_domain import DomainError


class CommunityServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        self.con.executescript(
            """
            CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE categories(
              id TEXT PRIMARY KEY,ar TEXT,en TEXT,active INTEGER DEFAULT 1,
              deleted_at TEXT DEFAULT ''
            );
            CREATE TABLE services(
              id TEXT,category_id TEXT,ar TEXT,en TEXT,active INTEGER DEFAULT 1,
              deleted_at TEXT DEFAULT '',PRIMARY KEY(id,category_id)
            );
            CREATE TABLE app_users(
              id TEXT PRIMARY KEY,name TEXT,avatar TEXT DEFAULT '',gov TEXT DEFAULT '',
              wilayah TEXT DEFAULT '',status TEXT DEFAULT 'active'
            );
            CREATE TABLE providers(
              id TEXT PRIMARY KEY,name TEXT,phone TEXT,gov TEXT DEFAULT '',
              wilayah TEXT DEFAULT '',image_path TEXT DEFAULT '',card_image TEXT DEFAULT '',
              verified INTEGER DEFAULT 1,rating REAL DEFAULT 5,
              provider_type TEXT DEFAULT 'individual',services TEXT DEFAULT '[]',
              package_id TEXT DEFAULT 'foundation_12m',
              subscription_start TEXT DEFAULT '',active INTEGER DEFAULT 1,
              status TEXT DEFAULT 'available',listing_enabled INTEGER DEFAULT 1,
              request_enabled INTEGER DEFAULT 1
            );
            CREATE TABLE subscriptions(
              id TEXT PRIMARY KEY,provider_id TEXT,package_id TEXT,status TEXT,
              start_date TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        settings = {
            "communityEnabled": True,
            "communityModerationRequired": False,
            "communityWantedExpiryDays": 30,
            "communityPackageExpiryDays": 30,
            "communityFirstPackageFreeDays": 30,
            "communityRenewalFee": 2,
            "communityPlanQuotas": {
                "foundation_12m": 1,
                "professional_12m": 4,
            },
            "communityPlanFreeMonths": {
                "foundation_12m": 0,
                "professional_12m": 2,
            },
        }
        self.con.execute(
            "INSERT INTO settings(key,value) VALUES('platform',?)",
            (json.dumps(settings),),
        )
        self.con.execute(
            "INSERT INTO categories(id,ar,en) VALUES('home','المنزل','Home')"
        )
        self.con.execute(
            """INSERT INTO services(id,category_id,ar,en)
            VALUES('electric','home','كهربائي','Electrician')"""
        )
        self.con.execute(
            """INSERT INTO services(id,category_id,ar,en)
            VALUES('paint','home','دهان','Painting')"""
        )
        self.con.executemany(
            "INSERT INTO app_users(id,name,gov,wilayah) VALUES(?,?,?,?)",
            [
                ("u1", "أحمد", "مسقط", "السيب"),
                ("u2", "مريم", "مسقط", "بوشر"),
            ],
        )
        electric = json.dumps(
            [{"catId": "home", "serviceId": "electric", "active": True}]
        )
        paint = json.dumps(
            [{"catId": "home", "serviceId": "paint", "active": True}]
        )
        self.con.executemany(
            """INSERT INTO providers(
            id,name,phone,services,package_id,subscription_start)
            VALUES(?,?,?,?,?,?)""",
            [
                (
                    "p1",
                    "نور التقنية",
                    "96890000001",
                    electric,
                    "professional_12m",
                    (self.now - timedelta(days=10)).isoformat(),
                ),
                (
                    "p2",
                    "ألوان عمان",
                    "96890000002",
                    paint,
                    "foundation_12m",
                    "",
                ),
            ],
        )
        install_community_schema(self.con)
        self.service = CommunityService(self.con, now=self.now)
        self.user = {"kind": "user", "userId": "u1"}
        self.other_user = {"kind": "user", "userId": "u2"}
        self.provider = {"kind": "provider", "providerId": "p1"}
        self.other_provider = {"kind": "provider", "providerId": "p2"}

    def tearDown(self) -> None:
        self.con.close()

    def wanted_payload(self, **changes):
        payload = {
            "kind": "wanted",
            "title": "تركيب إنارة للمنزل",
            "description": "أحتاج تركيب وحدات إنارة في غرفتين.",
            "serviceValue": "home|electric",
            "budgetMin": 20,
            "budgetMax": 35,
            "durationText": "يوم واحد",
            "gov": "مسقط",
            "wilayah": "السيب",
            "publish": True,
            "idempotencyKey": "wanted-u1-1",
        }
        payload.update(changes)
        return payload

    def package_payload(self, key="package-p1-1", **changes):
        payload = {
            "kind": "package",
            "title": "باقة صيانة كهربائية",
            "description": "فحص وتمديدات وإصلاح أعطال كهربائية منزلية.",
            "serviceValue": "home|electric",
            "priceAmount": 25,
            "billingPeriod": "monthly",
            "durationText": "خلال 24 ساعة",
            "gov": "مسقط",
            "wilayah": "السيب",
            "publish": True,
            "contactChannels": ["app", "whatsapp"],
            "idempotencyKey": key,
            "details": {
                "inclusions": ["فحص", "إصلاح بسيط"],
                "commitment": "موعد واضح قبل الزيارة",
            },
        }
        payload.update(changes)
        return payload

    def save_wanted(self):
        return self.service.save(
            self.user,
            self.wanted_payload(),
            listing_id="wanted-1",
        )

    def test_wanted_offer_requires_matching_provider_and_owner_accepts(self):
        listing = self.save_wanted()
        self.assertEqual("active", listing["status"])
        with self.assertRaises(DomainError) as mismatch:
            self.service.offer(
                self.other_provider,
                listing["id"],
                {"amount": 30, "durationText": "يوم", "idempotencyKey": "bad"},
                offer_id="offer-bad",
            )
        self.assertEqual("community_offer_service_mismatch", mismatch.exception.code)

        offer = self.service.offer(
            self.provider,
            listing["id"],
            {
                "amount": 28,
                "durationText": "يوم واحد",
                "note": "متاح غدًا",
                "idempotencyKey": "offer-p1",
            },
            offer_id="offer-1",
        )
        accepted = self.service.accept_offer(self.user, listing["id"], offer["id"])
        self.assertFalse(accepted["duplicate"])
        with self.assertRaises(DomainError) as denied:
            self.service.accept_offer(
                self.other_user, listing["id"], offer["id"]
            )
        self.assertEqual("community_listing_access_denied", denied.exception.code)
        self.service.complete_offer_acceptance(listing["id"], offer["id"], "req-1")
        duplicate = self.service.accept_offer(self.user, listing["id"], offer["id"])
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual("req-1", duplicate["requestId"])

    def test_package_first_free_then_plan_included_and_order_idempotent(self):
        first = self.service.save(
            self.provider,
            self.package_payload(),
            listing_id="package-1",
        )
        self.assertEqual("free_first", first["billingStatus"])
        second = self.service.save(
            self.provider,
            self.package_payload("package-p1-2", title="باقة طوارئ كهربائية"),
            listing_id="package-2",
        )
        self.assertEqual("plan_included", second["billingStatus"])
        started = self.service.begin_package_order(
            self.user,
            first["id"],
            "u1-package-1",
            order_id="order-1",
        )
        self.assertFalse(started["duplicate"])
        repeated = self.service.begin_package_order(
            self.user,
            first["id"],
            "u1-package-1",
            order_id="order-unused",
        )
        self.assertTrue(repeated["duplicate"])

    def test_monthly_plan_quota_is_enforced(self):
        first = self.service.save(
            self.other_provider,
            {
                **self.package_payload("p2-first"),
                "serviceValue": "home|paint",
            },
            listing_id="package-p2-1",
        )
        self.assertEqual("active", first["status"])
        with self.assertRaises(DomainError) as quota:
            self.service.save(
                self.other_provider,
                {
                    **self.package_payload("p2-second", title="باقة دهان شهرية"),
                    "serviceValue": "home|paint",
                },
                listing_id="package-p2-2",
            )
        self.assertEqual("community_package_quota_reached", quota.exception.code)

    def test_guest_snapshot_hides_drafts_and_expiry_runs_once(self):
        active = self.save_wanted()
        self.service.save(
            self.user,
            self.wanted_payload(
                idempotencyKey="wanted-draft",
                title="مسودة خاصة",
                publish=False,
            ),
            listing_id="wanted-draft",
        )
        guest = self.service.snapshot(None)
        self.assertEqual([active["id"]], [row["id"] for row in guest["listings"]])
        self.con.execute(
            "UPDATE community_listings SET expires_at=? WHERE id=?",
            ((self.now - timedelta(minutes=1)).isoformat(), active["id"]),
        )
        first = run_community_maintenance(self.con, now=self.now)
        second = run_community_maintenance(self.con, now=self.now)
        self.assertEqual(1, len(first["expired"]))
        self.assertEqual([], second["expired"])

    def test_disabled_community_blocks_new_business_actions(self):
        platform = json.loads(
            self.con.execute(
                "SELECT value FROM settings WHERE key='platform'"
            ).fetchone()["value"]
        )
        platform["communityEnabled"] = False
        self.con.execute(
            "UPDATE settings SET value=? WHERE key='platform'",
            (json.dumps(platform),),
        )
        with self.assertRaises(DomainError) as disabled:
            self.service.save(
                self.user,
                self.wanted_payload(),
                listing_id="wanted-disabled",
            )
        self.assertEqual("community_disabled", disabled.exception.code)


if __name__ == "__main__":
    unittest.main()
