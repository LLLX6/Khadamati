import http.client
import base64
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock

try:
    from cryptography.fernet import Fernet as _CryptographyProbe  # noqa: F401
except (ImportError, OSError):
    # Some locked-down Windows test hosts block cryptography's native DLL.
    # The production module is unchanged; this deterministic test-only shim is
    # sufficient for schema/bootstrap paths that do not exercise encryption.
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

import server
from khadamati_domain import (
    CommercialRecordService,
    DomainError,
    OMAN_TZ,
    RankingService,
    RequestMarketplace,
    parse_marketplace_datetime,
)
from khadamati_growth import KnownProviderInvitationService
from khadamati_trust import ProviderVerificationService


class LanguageContractTests(unittest.TestCase):
    def test_server_accepts_the_five_interface_languages(self):
        expected = {"ar": "ar", "en": "en", "hi": "hi", "bn": "bn", "ur": "ur"}
        self.assertEqual(
            {language: server.normalize_language(language) for language in expected},
            expected,
        )
        self.assertEqual(server.normalize_language("ur-PK"), "ur")
        self.assertEqual(server.normalize_language("unsupported"), "ar")


class CommercialRecordTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        self.con.execute(
            """CREATE TABLE coupons(
            id TEXT PRIMARY KEY,code TEXT UNIQUE,name_ar TEXT,name_en TEXT,
            discount_type TEXT,discount_value REAL,applies_to TEXT,starts_at TEXT,
            ends_at TEXT,max_uses INTEGER DEFAULT 0,uses_count INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1,created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP)"""
        )
        CommercialRecordService.install_schema(self.con)

    def tearDown(self):
        self.con.close()

    def test_finance_is_exact_audited_and_stale_updates_fail(self):
        service = CommercialRecordService(self.con)
        created = service.save_finance(
            {
                "id": "fin-1", "kind": "revenue", "amount": "12.345",
                "source": "subscription", "externalKey": "gateway-1",
                "occurredAt": "2026-08-15T10:00:00+00:00",
            },
            "admin-finance",
        )
        self.assertEqual(12.345, created["amount"])
        self.assertEqual(1, created["version"])
        updated = service.save_finance(
            {
                **created, "amount": "15.001", "expectedVersion": 1,
            },
            "admin-finance",
        )
        self.assertEqual(2, updated["version"])
        with self.assertRaises(DomainError) as caught:
            service.save_finance(
                {**created, "amount": 99, "expectedVersion": 1}, "admin-other"
            )
        self.assertEqual("record_version_conflict", caught.exception.code)
        stored = self.con.execute(
            "SELECT amount_milli,version FROM finance_entries WHERE id='fin-1'"
        ).fetchone()
        self.assertEqual((15001, 2), tuple(stored))
        self.assertEqual(
            2,
            self.con.execute(
                "SELECT COUNT(*) FROM finance_entry_events WHERE entry_id='fin-1'"
            ).fetchone()[0],
        )

    def test_private_mutation_rolls_back_with_its_audit_event(self):
        service = CommercialRecordService(self.con)
        try:
            with self.con:
                service.save_sponsorship(
                    {"id": "s-rollback", "name": "راعي", "amount": 5},
                    "admin-finance",
                )
                raise RuntimeError("force rollback")
        except RuntimeError:
            pass
        self.assertIsNone(
            self.con.execute(
                "SELECT id FROM sponsorships WHERE id='s-rollback'"
            ).fetchone()
        )
        self.assertEqual(
            0,
            self.con.execute(
                "SELECT COUNT(*) FROM sponsorship_events WHERE sponsorship_id='s-rollback'"
            ).fetchone()[0],
        )

    def test_commercial_create_replays_are_idempotent(self):
        service = CommercialRecordService(self.con)
        finance_payload = {
            "kind": "revenue", "amount": "7.125", "source": "manual",
            "occurredAt": "2026-08-15T00:00:00+00:00",
            "clientKey": "finance-form-001",
        }
        first = service.save_finance(finance_payload, "admin-finance")
        replay = service.save_finance(
            {**finance_payload, "id": "a-different-retry-id"}, "admin-finance"
        )
        self.assertEqual(first["id"], replay["id"])
        self.assertEqual(
            1,
            self.con.execute(
                "SELECT COUNT(*) FROM finance_entries WHERE external_key='finance-form-001'"
            ).fetchone()[0],
        )

        sponsor_key_payload = {
            "sponsorName": "راعي بالمفتاح", "amount": "19.000",
            "status": "draft", "clientKey": "sponsor-form-001",
        }
        sponsor_by_key = service.save_sponsorship(
            sponsor_key_payload, "admin-finance"
        )
        sponsor_by_key_replay = service.save_sponsorship(
            {**sponsor_key_payload, "id": "ignored-retry-id"}, "admin-finance"
        )
        self.assertEqual(sponsor_by_key["id"], sponsor_by_key_replay["id"])
        self.assertEqual("sponsor-form-001", sponsor_by_key["externalKey"])
        with self.assertRaises(DomainError) as sponsor_reused:
            service.save_sponsorship(
                {**sponsor_key_payload, "amount": "20.000"}, "admin-finance"
            )
        self.assertEqual("idempotency_key_reused", sponsor_reused.exception.code)

        coupon_payload = {
            "code": "REPLAY10", "discountType": "percent", "value": 10,
            "active": True, "clientKey": "coupon-form-001",
        }
        coupon = service.save_coupon(
            coupon_payload,
            "admin-subscriptions",
            allowed_plan_ids=server.PLAN_IDS,
        )
        coupon_replay = service.save_coupon(
            {**coupon_payload, "id": "ignored-coupon-retry"},
            "admin-subscriptions",
            allowed_plan_ids=server.PLAN_IDS,
        )
        self.assertEqual(coupon["id"], coupon_replay["id"])
        self.assertEqual("coupon-form-001", coupon["externalKey"])
        self.assertEqual(
            1,
            self.con.execute(
                "SELECT COUNT(*) FROM coupon_events WHERE coupon_id=?",
                (coupon["id"],),
            ).fetchone()[0],
        )
        with self.assertRaises(DomainError) as coupon_reused:
            service.save_coupon(
                {**coupon_payload, "value": 11},
                "admin-subscriptions",
                allowed_plan_ids=server.PLAN_IDS,
            )
        self.assertEqual("idempotency_key_reused", coupon_reused.exception.code)
        coupon_id_payload = {
            "id": "coupon-client-id-001", "code": "IDREPLAY5",
            "discountType": "percent", "value": 5, "active": True,
        }
        coupon_by_id = service.save_coupon(
            coupon_id_payload,
            "admin-subscriptions",
            allowed_plan_ids=server.PLAN_IDS,
        )
        coupon_by_id_replay = service.save_coupon(
            coupon_id_payload,
            "admin-subscriptions",
            allowed_plan_ids=server.PLAN_IDS,
        )
        self.assertEqual(coupon_by_id["id"], coupon_by_id_replay["id"])
        self.assertEqual(
            1,
            self.con.execute(
                "SELECT COUNT(*) FROM coupon_events WHERE coupon_id=?",
                (coupon_by_id["id"],),
            ).fetchone()[0],
        )
        self.assertEqual(
            1,
            self.con.execute(
                "SELECT COUNT(*) FROM finance_entry_events WHERE entry_id=?",
                (first["id"],),
            ).fetchone()[0],
        )
        with self.assertRaises(DomainError) as reused:
            service.save_finance(
                {**finance_payload, "amount": "8.000"}, "admin-finance"
            )
        self.assertEqual("idempotency_key_reused", reused.exception.code)

        sponsorship_payload = {
            "id": "client-sponsorship-001", "sponsorName": "راعي موثوق",
            "amount": "12.000", "status": "draft",
        }
        sponsor = service.save_sponsorship(
            sponsorship_payload, "admin-finance"
        )
        sponsor_replay = service.save_sponsorship(
            sponsorship_payload, "admin-finance"
        )
        self.assertEqual(sponsor["id"], sponsor_replay["id"])
        self.assertEqual(
            1,
            self.con.execute(
                "SELECT COUNT(*) FROM sponsorship_events WHERE sponsorship_id=?",
                (sponsor["id"],),
            ).fetchone()[0],
        )

    def test_inactive_records_remain_visible_for_audit_and_inputs_are_validated(self):
        service = CommercialRecordService(self.con)
        finance = service.save_finance(
            {"id": "fin-void", "kind": "revenue", "amount": "1.250"},
            "admin-finance",
        )
        voided = service.save_finance(
            {**finance, "status": "voided", "expectedVersion": 1},
            "admin-finance",
        )
        self.assertEqual("voided", voided["status"])
        self.assertIn("fin-void", [item["id"] for item in service.list_finance()])

        sponsorship = service.save_sponsorship(
            {"id": "s-cancelled", "name": "راعي", "amount": 3},
            "admin-finance",
        )
        service.save_sponsorship(
            {**sponsorship, "status": "cancelled", "expectedVersion": 1},
            "admin-finance",
        )
        self.assertIn(
            "s-cancelled", [item["id"] for item in service.list_sponsorships()]
        )

        coupon = service.save_coupon(
            {
                "id": "c-inactive", "code": "AUDIT10", "discountType": "percent",
                "value": 10, "active": False,
            },
            "admin-subscriptions",
            allowed_plan_ids=server.PLAN_IDS,
        )
        self.assertFalse(coupon["active"])
        self.assertIn("c-inactive", [item["id"] for item in service.list_coupons()])
        with self.assertRaises(DomainError):
            service.save_finance(
                {"kind": "revenue", "amount": 1, "occurredAt": "not-a-date"},
                "admin-finance",
            )
        with self.assertRaises(DomainError):
            service.save_finance(
                {"kind": "revenue", "amount": "NaN"},
                "admin-finance",
            )
        with self.assertRaises(DomainError):
            service.save_coupon(
                {
                    "code": "BADBOOL", "discountType": "percent", "value": 5,
                    "active": "sometimes",
                },
                "admin-subscriptions",
                allowed_plan_ids=server.PLAN_IDS,
            )

    def test_legacy_commercial_records_are_copied_once_without_deletion(self):
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.executescript(
            """
            CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE finance(
              id TEXT PRIMARY KEY,kind TEXT,amount REAL,source TEXT,note TEXT,
              created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE coupons(
              id TEXT PRIMARY KEY,code TEXT UNIQUE,name_ar TEXT,name_en TEXT,
              discount_type TEXT,discount_value REAL,applies_to TEXT,starts_at TEXT,
              ends_at TEXT,max_uses INTEGER DEFAULT 0,uses_count INTEGER DEFAULT 0,
              active INTEGER DEFAULT 1,created_at TEXT DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO finance(id,kind,amount,source,note,created_at)
            VALUES('legacy-1','revenue',7.125,'legacy','kept','2026-08-01');
            """
        )
        classic = {
            "finance": [{"id": "legacy-1", "amount": 7.125, "type": "revenue"}],
            "expenses": [{"id": "legacy-2", "amount": 2.5, "note": "rent"}],
            "sponsors": [{"id": "legacy-sponsor", "name": "Sponsor", "amount": 4}],
            "coupons": [{
                "id": "legacy-coupon", "code": "LEGACY10",
                "discountType": "percent", "discountValue": 10,
            }],
        }
        con.execute(
            "INSERT INTO settings(key,value) VALUES('classicState',?)",
            (json.dumps(classic),),
        )
        CommercialRecordService.install_schema(con)
        CommercialRecordService.install_schema(con)
        self.assertEqual(2, con.execute("SELECT COUNT(*) FROM finance_entries").fetchone()[0])
        self.assertEqual(2, con.execute("SELECT COUNT(*) FROM finance_entry_events").fetchone()[0])
        self.assertEqual(1, con.execute("SELECT COUNT(*) FROM sponsorships").fetchone()[0])
        self.assertEqual(1, con.execute("SELECT COUNT(*) FROM coupons").fetchone()[0])
        self.assertEqual(1, con.execute("SELECT COUNT(*) FROM finance").fetchone()[0])
        self.assertIsNotNone(
            con.execute(
                "SELECT 1 FROM settings WHERE key=?",
                (CommercialRecordService.LEGACY_MIGRATION_KEY,),
            ).fetchone()
        )
        con.close()


class DatabaseResponseCommitTests(unittest.TestCase):
    class _Connection:
        def __init__(self, events, *, fail_commit=False):
            self.events = events
            self.fail_commit = fail_commit
            self.row_factory = None

        def execute(self, statement, *_args):
            self.events.append(("execute", statement))
            return self

        def __enter__(self):
            return self

        def __exit__(self, exc_type, _exc, _traceback):
            if exc_type is not None:
                self.events.append(("rollback", exc_type.__name__))
                return False
            self.events.append(("commit_attempt",))
            if self.fail_commit:
                raise sqlite3.OperationalError("simulated commit failure")
            self.events.append(("commit_success",))
            return False

        def close(self):
            self.events.append(("close",))

    def tearDown(self):
        if hasattr(server._DATABASE_RESPONSE_STATE, "stack"):
            del server._DATABASE_RESPONSE_STATE.stack

    @staticmethod
    def _handler(events):
        handler = server.Handler.__new__(server.Handler)
        handler._response_sent = False
        handler._deferred_json_response = None

        def capture_response(self, raw, status=200, extra_headers=None):
            if self._response_sent:
                return None
            self._response_sent = True
            events.append((
                "socket", status, json.loads(raw.decode("utf-8")),
                tuple(extra_headers or ()),
            ))
            return None

        handler._write_json_response = types.MethodType(capture_response, handler)
        return handler

    def test_json_success_flushes_once_and_only_after_commit(self):
        events = []
        connection = self._Connection(events)
        handler = self._handler(events)
        with mock.patch.object(server.sqlite3, "connect", return_value=connection):
            with server.db():
                handler.send_json({"ok": True, "record": {"id": "r-1"}}, 201)
                handler.send_json({"ok": True, "record": {"id": "duplicate"}}, 201)
                self.assertFalse(any(event[0] == "socket" for event in events))

        ordered = [
            event[0] for event in events
            if event[0] in {"commit_attempt", "commit_success", "close", "socket"}
        ]
        self.assertEqual(
            ["commit_attempt", "commit_success", "close", "socket"], ordered
        )
        socket_events = [event for event in events if event[0] == "socket"]
        self.assertEqual(1, len(socket_events))
        self.assertEqual("r-1", socket_events[0][2]["record"]["id"])

    def test_external_side_effect_runs_after_commit_and_before_response(self):
        events = []
        connection = self._Connection(events)
        handler = self._handler(events)
        with mock.patch.object(server.sqlite3, "connect", return_value=connection):
            with server.db():
                server._after_database_commit(
                    lambda: events.append(("external_side_effect",))
                )
                handler.send_json({"ok": True}, 200)
                self.assertFalse(any(item[0] == "external_side_effect" for item in events))

        ordered = [
            item[0] for item in events
            if item[0] in {
                "commit_attempt", "commit_success", "close",
                "external_side_effect", "socket",
            }
        ]
        self.assertEqual(
            [
                "commit_attempt", "commit_success", "close",
                "external_side_effect", "socket",
            ],
            ordered,
        )

    def test_commit_failure_discards_success_before_error_can_be_sent(self):
        events = []
        connection = self._Connection(events, fail_commit=True)
        handler = self._handler(events)
        with mock.patch.object(server.sqlite3, "connect", return_value=connection):
            with self.assertRaises(sqlite3.OperationalError):
                with server.db():
                    server._after_database_commit(
                        lambda: events.append(("external_side_effect",))
                    )
                    handler.send_json({"ok": True}, 200)
                    self.assertFalse(any(event[0] == "socket" for event in events))

        self.assertFalse(any(event[0] == "socket" for event in events))
        self.assertFalse(any(event[0] == "external_side_effect" for event in events))
        self.assertIsNone(handler._deferred_json_response)
        self.assertFalse(handler._response_sent)
        handler.send_json({"error": "transaction_failed"}, 503)
        socket_events = [event for event in events if event[0] == "socket"]
        self.assertEqual(1, len(socket_events))
        self.assertEqual(503, socket_events[0][1])
        self.assertEqual("transaction_failed", socket_events[0][2]["error"])

    def test_new_upload_is_removed_when_its_database_commit_fails(self):
        events = []
        connection = self._Connection(events, fail_commit=True)
        with tempfile.TemporaryDirectory(prefix="khadamati-upload-rollback-") as temp:
            upload_dir = Path(temp)
            with (
                mock.patch.object(server.sqlite3, "connect", return_value=connection),
                mock.patch.object(server, "UPLOAD_DIR", upload_dir),
                mock.patch.object(server, "upload_signature_matches", return_value=True),
            ):
                with self.assertRaises(sqlite3.OperationalError):
                    with server.db():
                        relative = server.save_upload_data(
                            "provider-1",
                            "data:image/png;base64,aGVsbG8=",
                            "avatar",
                            {"image/png": "png"},
                            1024,
                        )
                        target = upload_dir / Path(relative).name
                        self.assertTrue(target.is_file())
                self.assertFalse(target.exists())


class MatchingContractTests(unittest.TestCase):
    def test_related_technology_family_is_transitive_and_category_bound(self):
        request = {"service_value": "tech|tech_support"}

        def provider(service, category="tech"):
            return {
                "status": "available",
                "services": [{"catId": category, "serviceId": service, "active": True}],
                "areas": ["السيب"],
            }

        for related in ("networks", "pc", "printer"):
            self.assertEqual(
                "related",
                RankingService.service_match_details(request, provider(related))["kind"],
            )
            self.assertIn(related, RankingService.service_family_members("tech_support"))
        self.assertFalse(RankingService.service_match(request, provider("networks", "homecare")))
        self.assertFalse(RankingService.service_match(request, provider("design")))

    def test_matching_matrix_applies_area_and_availability_as_hard_gates(self):
        monday_ten = server.datetime(2026, 8, 17, 10, 0, tzinfo=OMAN_TZ)
        request = {
            "service_value": "tech|tech_support", "gov": "مسقط", "wilayah": "السيب"
        }
        provider = {
            "status": "available", "gov": "مسقط", "wilayah": "السيب",
            "areas": ["السيب"],
            "services": [{"catId": "tech", "serviceId": "networks", "active": True}],
            "availability": {"days": [1], "start": "08:00", "end": "17:00"},
        }
        self.assertEqual("", RankingService.exclusion_reason(request, provider, monday_ten))
        self.assertEqual(
            "area_mismatch",
            RankingService.exclusion_reason({**request, "wilayah": "بوشر", "gov": ""}, provider, monday_ten),
        )
        self.assertEqual(
            "outside_availability",
            RankingService.exclusion_reason(request, provider, monday_ten.replace(hour=20)),
        )
        sunday_ten = monday_ten.replace(day=16)
        self.assertFalse(RankingService.availability_match(provider, sunday_ten))
        sunday_provider = {
            **provider,
            "availability": {"days": [0], "start": "08:00", "end": "17:00"},
        }
        self.assertTrue(RankingService.availability_match(sunday_provider, sunday_ten))
        overnight = {**provider, "availability": {"days": [1], "start": "20:00", "end": "03:00"}}
        self.assertTrue(RankingService.availability_match(overnight, monday_ten.replace(hour=23)))
        tuesday_one = monday_ten.replace(day=18, hour=1)
        self.assertTrue(RankingService.availability_match(overnight, tuesday_one))
        self.assertFalse(RankingService.availability_match(overnight, tuesday_one.replace(hour=4)))
        naive_oman = parse_marketplace_datetime("2026-08-16T10:00")
        self.assertEqual(10, naive_oman.astimezone(OMAN_TZ).hour)
        saturday_utc = server.datetime(2026, 8, 15, 22, 30, tzinfo=server.UTC)
        sunday_overnight = {
            **provider,
            "availability": {"days": [6], "start": "20:00", "end": "03:00"},
        }
        self.assertTrue(
            RankingService.availability_match(sunday_overnight, saturday_utc)
        )

    def test_provider_location_does_not_expand_individual_coverage(self):
        request = {
            "service_value": "tech|tech_support", "gov": "مسقط", "wilayah": "بوشر"
        }
        base = {
            "status": "available", "gov": "مسقط", "wilayah": "السيب",
            "areas": ["السيب"],
            "services": [{"catId": "tech", "serviceId": "networks", "active": True}],
        }
        self.assertFalse(RankingService.area_match(request, base))
        self.assertFalse(RankingService.area_match(
            request, {**base, "wilayah": "بوشر", "areas": []}
        ))
        self.assertFalse(RankingService.area_match(
            request,
            {
                **base,
                "areas": ["السيب", "بوشر"],
                "services": [{
                    "catId": "tech", "serviceId": "networks", "active": True,
                    "areas": ["السيب"],
                }],
            },
        ))
        self.assertTrue(RankingService.area_match(
            request, {**base, "areas": ["بوشر"]}
        ))
        self.assertFalse(
            RankingService.area_match(
                request,
                {**base, "provider_type": "company", "governorates": ["الداخلية"]},
            )
        )
        self.assertFalse(
            RankingService.area_match(
                request,
                {**base, "provider_type": "company", "governorates": ["مسقط"]},
            )
        )
        self.assertTrue(RankingService.area_match(
            request,
            {
                **base, "areas": [], "provider_type": "company",
                "governorates": ["مسقط"],
            },
        ))
        self.assertFalse(RankingService.area_match(
            request,
            {
                **base, "areas": [], "provider_type": "company",
                "governorates": ["مسقط"],
                "services": [{
                    "catId": "tech", "serviceId": "networks", "active": True,
                    "areas": ["السيب"],
                }],
            },
        ))

        # A company's profile governorate is its address, not a declaration
        # that every wilayah in that governorate is covered.
        raw_company = {
            "id": "company-location-only", "name": "Location only",
            "phone": "96890000000", "gov": "مسقط", "wilayah": "السيب",
            "governorates": "[]", "areas": "[]", "services": "[]",
            "stats": "{}", "work_images": "[]", "documents": "[]",
            "before_after": "[]", "availability": "{}", "quote_templates": "[]",
            "active": 1, "verified": 1, "featured": 0, "provider_type": "company",
        }
        company_view = server.row_provider(raw_company)
        self.assertEqual([], company_view["governorates"])
        self.assertFalse(RankingService.area_match(request, company_view))
        governorate_only = {
            "service_value": "tech|tech_support", "gov": "مسقط", "wilayah": ""
        }
        self.assertFalse(RankingService.area_match(governorate_only, base))
        self.assertFalse(RankingService.area_match(
            governorate_only,
            {**base, "provider_type": "company", "governorates": ["مسقط"]},
        ))
        self.assertTrue(RankingService.area_match(
            governorate_only,
            {
                **base, "areas": [], "provider_type": "company",
                "governorates": ["مسقط"],
            },
        ))


class ServerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="khadamati-backend-contract-")
        root = Path(self.temp.name)
        self.old = {
            "db": server.DB_PATH,
            "uploads": server.UPLOAD_DIR,
            "backups": server.BACKUP_DIR,
            "sample": server.SAMPLE_DATA_ENABLED,
            "env": server.APP_ENV,
            "url": server.PUBLIC_APP_URL,
        }
        server.DB_PATH = root / "data" / "khadamati.sqlite3"
        server.UPLOAD_DIR = root / "uploads"
        server.BACKUP_DIR = root / "backups"
        server.SAMPLE_DATA_ENABLED = False
        server.init_db()

    def tearDown(self):
        server.DB_PATH = self.old["db"]
        server.UPLOAD_DIR = self.old["uploads"]
        server.BACKUP_DIR = self.old["backups"]
        server.SAMPLE_DATA_ENABLED = self.old["sample"]
        server.APP_ENV = self.old["env"]
        server.PUBLIC_APP_URL = self.old["url"]
        self.temp.cleanup()

    def test_provider_service_areas_cannot_expand_account_or_plan_scope(self):
        base = {
            "name": "Scoped provider",
            "phone": "96899000801",
            "pin": "7349",
            "providerType": "individual",
            "gov": "مسقط",
            "wilayah": "السيب",
            "governorates": ["مسقط"],
            "bio": "مزود تقني موثوق للاختبار",
            "status": "available",
            "active": True,
            "verified": True,
        }
        with server.db() as con:
            with self.assertRaises(DomainError) as outside_scope:
                server.upsert_provider(
                    con,
                    {
                        **base,
                        "id": "provider-hostile-area",
                        "areas": ["السيب"],
                        "services": [{
                            "id": "service-hostile-area",
                            "catId": "tech",
                            "serviceId": "networks",
                            "active": True,
                            "areas": ["بوشر"],
                        }],
                    },
                )
            self.assertEqual(
                "service_area_outside_coverage", outside_scope.exception.code
            )

            with self.assertRaises(DomainError) as plan_overflow:
                server.upsert_provider(
                    con,
                    {
                        **base,
                        "id": "provider-hostile-plan",
                        "phone": "96899000802",
                        "areas": ["السيب", "بوشر"],
                        "services": [{
                            "id": "service-hostile-plan",
                            "catId": "tech",
                            "serviceId": "networks",
                            "active": True,
                            "areas": ["السيب", "بوشر"],
                        }],
                    },
                )
            self.assertEqual("wilayah_limit_exceeded", plan_overflow.exception.code)

            with self.assertRaises(DomainError) as inherited_plan_overflow:
                server.upsert_provider(
                    con,
                    {
                        **base,
                        "id": "provider-hostile-inherited-plan",
                        "phone": "96899000804",
                        "areas": ["السيب", "بوشر"],
                        "services": [{
                            "id": "service-hostile-inherited-plan",
                            "catId": "tech",
                            "serviceId": "networks",
                            "active": True,
                            "areas": [],
                        }],
                    },
                )
            self.assertEqual(
                "wilayah_limit_exceeded", inherited_plan_overflow.exception.code
            )

    def test_company_service_areas_narrow_declared_governorate_coverage(self):
        company = {
            "id": "company-scoped-area",
            "name": "Scoped company",
            "phone": "96899000803",
            "pin": "7349",
            "providerType": "company",
            "gov": "مسقط",
            "wilayah": "السيب",
            "governorates": ["مسقط"],
            "areas": [],
            "bio": "شركة تقنية موثوقة للاختبار",
            "status": "available",
            "active": True,
            "verified": True,
        }
        with server.db() as con:
            server.upsert_provider(
                con,
                {
                    **company,
                    "services": [{
                        "id": "company-service-valid",
                        "catId": "tech",
                        "serviceId": "networks",
                        "active": True,
                        "areas": ["بوشر"],
                    }],
                },
            )
            saved = con.execute(
                "SELECT services FROM providers WHERE id=?", (company["id"],)
            ).fetchone()
            self.assertEqual(["بوشر"], json.loads(saved["services"])[0]["areas"])

            with self.assertRaises(DomainError) as other_governorate:
                server.upsert_provider(
                    con,
                    {
                        **company,
                        "services": [{
                            "id": "company-service-outside-governorate",
                            "catId": "tech",
                            "serviceId": "networks",
                            "active": True,
                            "areas": ["نزوى"],
                        }],
                    },
                )
            self.assertEqual(
                "service_area_outside_coverage",
                other_governorate.exception.code,
            )

            with self.assertRaises(DomainError) as outside_explicit_wilayahs:
                server.upsert_provider(
                    con,
                    {
                        **company,
                        "areas": ["السيب"],
                        "services": [{
                            "id": "company-service-outside-explicit-scope",
                            "catId": "tech",
                            "serviceId": "networks",
                            "active": True,
                            "areas": ["بوشر"],
                        }],
                    },
                )
            self.assertEqual(
                "service_area_outside_coverage",
                outside_explicit_wilayahs.exception.code,
            )

    def test_release_due_revalidates_unavailable_full_expired_and_blocked(self):
        now = server.datetime(2026, 8, 16, 6, 0, tzinfo=server.UTC)
        requested_at = "2026-08-16T10:00:00+04:00"

        def set_subscriptions(con, enabled):
            row = con.execute(
                "SELECT value FROM settings WHERE key='platform'"
            ).fetchone()
            settings = json.loads(row["value"])
            settings["subscriptionsEnabled"] = enabled
            con.execute(
                "UPDATE settings SET value=? WHERE key='platform'",
                (json.dumps(settings, ensure_ascii=False),),
            )

        def schedule_one(
            con,
            *,
            provider_id,
            provider_phone,
            request_id,
            user_id,
            user_phone,
            category_id,
            service_id,
            capacity=0,
        ):
            server.upsert_provider(
                con,
                {
                    "id": provider_id,
                    "name": f"Provider {provider_id}",
                    "phone": provider_phone,
                    "pin": "7349",
                    "providerType": "individual",
                    "gov": "مسقط",
                    "wilayah": "السيب",
                    "governorates": ["مسقط"],
                    "areas": ["السيب"],
                    "bio": "مزود موثوق لإعادة فحص الإرسال",
                    "status": "available",
                    "active": True,
                    "verified": True,
                    "availability": {
                        "days": ["0"],
                        "start": "08:00",
                        "end": "17:00",
                        "dailyCapacity": capacity,
                    },
                    "services": [{
                        "id": f"service-{provider_id}",
                        "catId": category_id,
                        "serviceId": service_id,
                        "active": True,
                        "areas": ["السيب"],
                    }],
                },
            )
            con.execute(
                """INSERT INTO app_users(id,phone,name,pin_hash,status)
                VALUES(?,?,?,?, 'active')""",
                (user_id, user_phone, f"User {user_id}", server.hash_pin("7349")),
            )
            con.execute(
                """INSERT INTO customer_requests(
                id,user_id,customer_name,phone,service_value,service_name,
                gov,wilayah,status,requested_at,offers_open)
                VALUES(?,?,?,?,?,?,?,?, 'matching',?,1)""",
                (
                    request_id,
                    user_id,
                    f"User {user_id}",
                    user_phone,
                    f"{category_id}|{service_id}",
                    service_id,
                    "مسقط",
                    "السيب",
                    requested_at,
                ),
            )
            ranked = RequestMarketplace(con, now=now).schedule(request_id)
            self.assertIn(provider_id, [item["providerId"] for item in ranked])

        def assert_stale(con, request_id, provider_id, released):
            self.assertNotIn(provider_id, [item["providerId"] for item in released])
            server.create_marketplace_notifications(con, released)
            dispatch = con.execute(
                """SELECT status,notified_at FROM request_dispatches
                WHERE request_id=? AND provider_id=?""",
                (request_id, provider_id),
            ).fetchone()
            self.assertEqual("stale", dispatch["status"])
            self.assertFalse(dispatch["notified_at"])
            notification_count = con.execute(
                """SELECT COUNT(*) n FROM app_notifications
                WHERE target_kind='provider' AND target_id=? AND related_id=?""",
                (provider_id, request_id),
            ).fetchone()["n"]
            self.assertEqual(0, notification_count)

        with server.db() as con:
            set_subscriptions(con, False)
            schedule_one(
                con,
                provider_id="release-unavailable-provider",
                provider_phone="96899000811",
                request_id="release-unavailable-request",
                user_id="release-unavailable-user",
                user_phone="96899000812",
                category_id="tech",
                service_id="networks",
            )
            con.execute(
                "UPDATE providers SET status='unavailable' WHERE id=?",
                ("release-unavailable-provider",),
            )
            released = RequestMarketplace(con, now=now).release_due(
                "release-unavailable-request"
            )
            assert_stale(
                con,
                "release-unavailable-request",
                "release-unavailable-provider",
                released,
            )

            schedule_one(
                con,
                provider_id="release-full-provider",
                provider_phone="96899000813",
                request_id="release-full-request",
                user_id="release-full-user",
                user_phone="96899000814",
                category_id="homecare",
                service_id="electrician",
                capacity=1,
            )
            con.execute(
                """INSERT INTO customer_requests(
                id,user_id,customer_name,phone,service_value,service_name,gov,wilayah,
                status,accepted_provider_id,requested_at,offers_open)
                VALUES(?,?,?,?,?,?,?,?, 'accepted',?,?,0)""",
                (
                    "release-existing-job",
                    "release-full-user",
                    "Existing job",
                    "96899000814",
                    "homecare|electrician",
                    "electrician",
                    "مسقط",
                    "السيب",
                    "release-full-provider",
                    requested_at,
                ),
            )
            released = RequestMarketplace(con, now=now).release_due(
                "release-full-request"
            )
            assert_stale(
                con, "release-full-request", "release-full-provider", released
            )

            schedule_one(
                con,
                provider_id="release-blocked-provider",
                provider_phone="96899000815",
                request_id="release-blocked-request",
                user_id="release-blocked-user",
                user_phone="96899000816",
                category_id="cars",
                service_id="mechanic",
            )
            server.InteractionBlockService(con).block(
                "user",
                "release-blocked-user",
                "provider",
                "release-blocked-provider",
                request_id="release-blocked-request",
            )
            released = RequestMarketplace(con, now=now).release_due(
                "release-blocked-request"
            )
            assert_stale(
                con,
                "release-blocked-request",
                "release-blocked-provider",
                released,
            )

            set_subscriptions(con, False)
            schedule_one(
                con,
                provider_id="release-expired-provider",
                provider_phone="96899000817",
                request_id="release-expired-request",
                user_id="release-expired-user",
                user_phone="96899000818",
                category_id="cleaning",
                service_id="home_clean",
            )
            subscription = server.SubscriptionService(con, now=now).request_plan(
                "release-expired-provider", "individual_free_3m", actor="test"
            )
            # Re-schedule after the subscription exists so the delayed dispatch
            # was genuinely eligible when it was created.
            set_subscriptions(con, True)
            ranked = RequestMarketplace(con, now=now).schedule(
                "release-expired-request"
            )
            self.assertIn(
                "release-expired-provider",
                [item["providerId"] for item in ranked],
            )
            con.execute(
                """UPDATE subscriptions SET status='foundation',end_date=?
                WHERE id=?""",
                ((now - server.timedelta(days=1)).date().isoformat(), subscription["subscriptionId"]),
            )
            released = RequestMarketplace(
                con, now=now + server.timedelta(minutes=3)
            ).release_due("release-expired-request")
            assert_stale(
                con,
                "release-expired-request",
                "release-expired-provider",
                released,
            )

    @staticmethod
    def _provider_handler(provider_id):
        handler = server.Handler.__new__(server.Handler)
        handler.require_provider = lambda _permission: {
            "kind": "provider",
            "providerId": provider_id,
            "name": "Atomic provider",
            "role": "provider_owner",
        }
        handler.send_json = lambda payload, status=200: (status, payload)
        return handler

    @staticmethod
    def _user_handler(user_id):
        handler = server.Handler.__new__(server.Handler)
        handler.session = lambda: {
            "kind": "user", "userId": user_id, "name": "Atomic customer"
        }
        handler.send_json = lambda payload, status=200: (status, payload)
        return handler

    @staticmethod
    def _domain_response(call):
        try:
            return call()
        except DomainError as error:
            return error.status, {"error": error.code, "detail": error.detail}

    def _seed_atomic_provider(self, provider_id="provider-atomic", *, capacity=1):
        with server.db() as con:
            server.upsert_provider(
                con,
                {
                    "id": provider_id,
                    "name": "Atomic provider",
                    "phone": "96899000881",
                    "pin": "7349",
                    "gov": "مسقط",
                    "wilayah": "السيب",
                    "areas": ["السيب"],
                    "bio": "مزود معتمد لاختبار قبول الطلب الذري",
                    "status": "available",
                    "active": True,
                    "verified": True,
                    "listingEnabled": True,
                    "requestEnabled": True,
                    "availability": {
                        "days": ["0"], "start": "09:00", "end": "17:00",
                        "dailyCapacity": capacity,
                    },
                    "services": [{
                        "id": "atomic-network-service",
                        "catId": "tech", "serviceId": "networks",
                        "active": True, "areas": ["السيب"],
                    }],
                },
            )
        return provider_id

    def test_verified_provider_identity_change_disables_matching_until_review(self):
        provider_id = "provider-evidence-change"
        with server.db() as con:
            server.upsert_provider(
                con,
                {
                    "id": provider_id,
                    "name": "Verified provider",
                    "phone": "96899000871",
                    "pin": "7349",
                    "providerType": "individual",
                    "commercialNo": "LIC-OLD-871",
                    "licenseExpiry": "2028-12-31",
                    "documents": ["uploads/front.webp", "uploads/back.webp"],
                    "gov": "مسقط",
                    "wilayah": "السيب",
                    "areas": ["السيب"],
                    "bio": "مزود معتمد يحدّث بيانات الترخيص",
                    "hours": "Sunday 09:00 - 17:00",
                    "availability": {
                        "days": ["0"], "start": "09:00", "end": "17:00",
                        "dailyCapacity": 2,
                    },
                    "status": "available",
                    "active": True,
                    "verified": True,
                    "listingEnabled": True,
                    "requestEnabled": True,
                    "services": [{
                        "id": "evidence-network-service",
                        "catId": "tech",
                        "serviceId": "networks",
                        "active": True,
                        "areas": ["السيب"],
                    }],
                },
            )

        handler = self._provider_handler(provider_id)
        status, response = handler.provider_post(
            "/api/provider/profile",
            {"commercialNo": "LIC-NEW-871"},
        )
        self.assertEqual(200, status, response)
        self.assertTrue(response["ok"])
        self.assertFalse(response["provider"]["verified"])
        self.assertEqual("under_review", response["provider"]["status"])
        with server.db() as con:
            row = con.execute(
                """SELECT verified,status,listing_enabled,request_enabled
                FROM providers WHERE id=?""",
                (provider_id,),
            ).fetchone()
            case = ProviderVerificationService(con).get(provider_id, private=True)
        self.assertEqual((0, "under_review", 0, 0), tuple(row))
        self.assertEqual("submitted", case["status"])

    def test_legacy_accept_and_offer_selection_serialize_daily_capacity(self):
        provider_id = self._seed_atomic_provider(capacity=1)
        requested_at = "2026-08-16T10:00:00+04:00"
        request_ids = ("atomic-capacity-a", "atomic-capacity-b")
        with server.db() as con:
            for index, request_id in enumerate(request_ids, start=1):
                con.execute(
                    """INSERT INTO customer_requests(
                    id,user_id,customer_name,phone,service_value,service_name,
                    gov,wilayah,requested_at,status,matching_provider_ids,
                    offers_open,workflow_version)
                    VALUES(?,?,?,?,?,?,?,?,?,'matching',?,1,'legacy_v1')""",
                    (
                        request_id, f"atomic-user-{index}", "Atomic customer",
                        f"9689900089{index}", "tech|networks", "Networks",
                        "مسقط", "السيب", requested_at,
                        server.jdump([provider_id]),
                    ),
                )
            con.execute(
                """UPDATE customer_requests SET status='viewed',offers=?
                WHERE id=?""",
                (
                    server.jdump([{
                        "id": "atomic-capacity-offer",
                        "providerId": provider_id,
                        "price": 8,
                        "duration": "One hour",
                        "status": "pending",
                        "validUntil": "2030-01-01T00:00:00+00:00",
                    }]),
                    request_ids[1],
                ),
            )

        barrier = threading.Barrier(2)
        results = []

        def accept(request_id):
            barrier.wait(timeout=5)
            if request_id == request_ids[0]:
                handler = self._provider_handler(provider_id)
                result = self._domain_response(
                    lambda: handler.request_action({
                        "id": request_id, "action": "accept",
                    })
                )
            else:
                handler = self._user_handler("atomic-user-2")
                result = self._domain_response(
                    lambda: handler.request_collaboration({
                        "id": request_id,
                        "action": "choose_offer",
                        "offerId": "atomic-capacity-offer",
                    })
                )
            results.append((request_id, result))

        workers = [threading.Thread(target=accept, args=(request_id,)) for request_id in request_ids]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)
            self.assertFalse(worker.is_alive(), "concurrent acceptance did not finish")

        self.assertEqual(2, len(results))
        successes = [entry for entry in results if entry[1][0] == 200]
        conflicts = [entry for entry in results if entry[1][0] == 409]
        self.assertEqual(1, len(successes), results)
        self.assertEqual(1, len(conflicts), results)
        self.assertEqual("provider_no_longer_available", conflicts[0][1][1]["error"])
        self.assertEqual("daily_capacity_reached", conflicts[0][1][1]["detail"])
        with server.db() as con:
            rows = list(con.execute(
                """SELECT id,accepted_provider_id,status FROM customer_requests
                WHERE id IN (?,?) ORDER BY id""",
                request_ids,
            ))
        self.assertEqual(1, sum(row["accepted_provider_id"] == provider_id for row in rows))
        self.assertEqual(1, sum(not row["accepted_provider_id"] for row in rows))

    def test_offer_selection_rejects_provider_that_became_unavailable(self):
        provider_id = self._seed_atomic_provider(capacity=1)
        user_id = "atomic-offer-user"
        request_id = "atomic-offer-request"
        offer_id = "atomic-offer"
        with server.db() as con:
            con.execute(
                """INSERT INTO customer_requests(
                id,user_id,customer_name,phone,service_value,service_name,
                gov,wilayah,requested_at,status,matching_provider_ids,offers,
                offers_open,workflow_version)
                VALUES(?,?,?,?,?,?,?,?,?,'viewed',?,?,1,'legacy_v1')""",
                (
                    request_id, user_id, "Atomic customer", "96899000899",
                    "tech|networks", "Networks", "مسقط", "السيب",
                    "2026-08-16T10:00:00+04:00", server.jdump([provider_id]),
                    server.jdump([{
                        "id": offer_id, "providerId": provider_id, "price": 12,
                        "duration": "One hour", "status": "pending",
                        "validUntil": "2030-01-01T00:00:00+00:00",
                    }]),
                ),
            )
            con.execute(
                """UPDATE providers SET status='unavailable',request_enabled=0,
                updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (provider_id,),
            )

        handler = self._user_handler(user_id)
        status, payload = self._domain_response(
            lambda: handler.request_collaboration({
                "id": request_id, "action": "choose_offer", "offerId": offer_id,
            })
        )
        self.assertEqual(409, status, payload)
        self.assertEqual("provider_no_longer_available", payload["error"])
        self.assertIn(payload["detail"], {"provider_unavailable", "provider_not_available"})
        with server.db() as con:
            row = con.execute(
                "SELECT accepted_provider_id,status,offers FROM customer_requests WHERE id=?",
                (request_id,),
            ).fetchone()
        self.assertEqual("", row["accepted_provider_id"])
        self.assertEqual("viewed", row["status"])
        self.assertEqual("pending", server.jload(row["offers"], [])[0]["status"])

    def test_provider_approval_preserves_registered_schedule_for_matching(self):
        request_id = "provider-request-schedule"
        payload = {
            "id": request_id,
            "name": "Scheduled provider",
            "phone": "96899000901",
            "email": "provider@example.com",
            "age": 30,
            "nationality": "عماني",
            "providerType": "individual",
            "companyName": "",
            "commercialNo": "LIC-SCHEDULE",
            "commercialExpiry": "",
            "licenseExpiry": "2027-08-31",
            "registrationVersion": 59,
            "companySize": "1",
            "businessRole": "فني شبكات",
            "legalPath": "individual_omani",
            "gender": "not_specified",
            "gov": "مسقط",
            "wilayah": "السيب",
            "location": None,
            "service": "tech|networks",
            "services": [{
                "id": "requested-network-service",
                "catId": "tech",
                "serviceId": "networks",
                "priceFrom": 5,
                "active": True,
                "areas": ["السيب"],
            }],
            "priceFrom": 5,
            "note": "مزود شبكات موثوق للاختبار",
            "bio": "مزود شبكات موثوق للاختبار",
            "hours": "Sunday 09:00 - 17:00",
            "availability": {
                "days": ["0"],
                "start": "09:00",
                "end": "17:00",
                "dailyCapacity": 2,
            },
            "imagePath": "",
            "workImages": [],
            "documents": ["uploads/id-front.webp", "uploads/id-back.webp"],
            "pinHash": server.hash_pin("7349"),
        }
        with server.db() as con:
            con.execute(
                "INSERT INTO provider_requests(id,payload) VALUES(?,?)",
                (request_id, server.jdump(payload)),
            )
        handler = server.Handler.__new__(server.Handler)
        handler.require_admin = lambda _permission: {
            "kind": "admin",
            "id": "admin-schedule",
            "name": "Schedule Admin",
            "role": "super_admin",
        }
        handler.send_json = lambda body, status=200: (status, body)
        with mock.patch.object(server, "send_whatsapp", return_value=False):
            status, response = handler.admin_post(
                "/api/admin/request-decision",
                {"id": request_id, "decision": "accept"},
            )
        self.assertEqual(200, status, response)
        self.assertTrue(response["ok"])
        provider_id = response["provider"]["id"]
        with server.db() as con:
            row = con.execute(
                "SELECT * FROM providers WHERE id=?", (provider_id,)
            ).fetchone()
            stored_availability = json.loads(row["availability"])
        self.assertEqual(payload["availability"], stored_availability)
        sunday_ten = server.datetime(2026, 8, 16, 10, 0, tzinfo=OMAN_TZ)
        self.assertTrue(RankingService.availability_match(dict(row), sunday_ten))
        self.assertFalse(
            RankingService.availability_match(
                dict(row), sunday_ten.replace(day=17)
            )
        )
        self.assertFalse(
            RankingService.availability_match(dict(row), sunday_ten.replace(hour=20))
        )

    def test_bootstrap_commercial_records_are_permission_scoped(self):
        with server.db() as con:
            service = CommercialRecordService(con)
            service.save_finance(
                {"id": "fin-private", "kind": "revenue", "amount": 4}, "finance-admin"
            )
            service.save_finance(
                {"id": "fin-refund", "kind": "refund", "amount": 1},
                "finance-admin",
            )
            service.save_sponsorship(
                {"id": "s-private", "name": "Private sponsor", "amount": 8},
                "finance-admin",
            )
            service.save_coupon(
                {"id": "c-private", "code": "PRIVATE10", "discountType": "percent", "value": 10},
                "subscription-admin",
                allowed_plan_ids=server.PLAN_IDS,
            )
            con.execute(
                "INSERT INTO finance(id,kind,amount,source,note) VALUES('legacy-kpi','revenue',999,'legacy','ignored')"
            )
            con.execute(
                """INSERT INTO payments(id,kind,amount,status)
                VALUES('payment-kpi','revenue',999,'paid')"""
            )
        finance = server.get_bootstrap({
            "kind": "admin", "id": "finance", "name": "Finance", "role": "finance",
            "permissions": ["manage_finance"],
        })
        support = server.get_bootstrap({
            "kind": "admin", "id": "support", "name": "Support", "role": "support",
            "permissions": ["review_requests"],
        })
        subscriptions = server.get_bootstrap({
            "kind": "admin", "id": "subs", "name": "Subscriptions", "role": "admin",
            "permissions": ["manage_subscriptions"],
        })
        self.assertEqual(
            {"fin-private", "fin-refund"},
            {item["id"] for item in finance["financeEntries"]},
        )
        self.assertEqual(3.0, finance["stats"]["revenue"])
        self.assertEqual(["s-private"], [item["id"] for item in finance["sponsorships"]])
        self.assertEqual([], finance["coupons"])
        self.assertEqual([], support["financeEntries"])
        self.assertEqual([], support["sponsorships"])
        self.assertEqual([], support["coupons"])
        self.assertEqual(["c-private"], [item["id"] for item in subscriptions["coupons"]])
        server.save_classic_state({
            "financeEntries": [{"id": "local-shadow"}],
            "finance": [{"id": "local-shadow"}],
            "offlineQueue": [{"secret": "pending"}],
            "supportNotes": [{"secret": "support"}],
            "activityEvents": [{"secret": "activity"}],
            "guestVisits": [{"secret": "guest"}],
            "communityListings": [{"secret": "listing"}],
            "communityFavorites": [{"secret": "favorite"}],
            "communityReports": [{"secret": "report"}],
            "communityStats": {"secret": "stats"},
            "communitySettings": {"secret": "settings"},
            "providers": [{"phone": "96800000000"}],
            "reviews": [{"phone": "96800000000"}],
            "user": {"phone": "96800000000"},
            "session": {"token": "private"},
            "requestDraft": {"phone": "96800000000"},
            "providerRegistrationDraft": {"commercialNo": "private"},
            "theme": "light",
        })
        classic = server.get_classic_state()
        for private_key in (
            "financeEntries", "finance", "offlineQueue", "supportNotes",
            "activityEvents", "guestVisits", "communityListings",
            "communityFavorites", "communityReports", "communityStats",
            "communitySettings", "providers", "reviews", "user", "session",
            "requestDraft", "providerRegistrationDraft",
        ):
            self.assertNotIn(private_key, classic)
        self.assertEqual("light", classic["theme"])
        with server.db() as con:
            raw = json.loads(con.execute(
                "SELECT value FROM settings WHERE key='classicState'"
            ).fetchone()["value"])
        self.assertNotIn("providers", raw)
        self.assertNotIn("user", raw)

        # Legacy values are retained only for rollback and can neither be read
        # nor replaced through the compatibility endpoint.
        with server.db() as con:
            con.execute(
                "UPDATE settings SET value=? WHERE key='classicState'",
                (json.dumps({
                    "providers": [{"id": "legacy-provider"}],
                    "theme": "light",
                }),),
            )
        server.save_classic_state({
            "providers": [{"id": "attacker-replacement"}], "theme": "dark"
        })
        with server.db() as con:
            retained = json.loads(con.execute(
                "SELECT value FROM settings WHERE key='classicState'"
            ).fetchone()["value"])
        self.assertEqual([{"id": "legacy-provider"}], retained["providers"])
        self.assertEqual("dark", server.get_classic_state()["theme"])
        self.assertNotIn("providers", server.get_classic_state())

    def test_zero_match_is_durable_and_provider_activation_redispatches(self):
        with server.db() as con:
            con.execute(
                "INSERT INTO app_users(id,phone,name,pin_hash,status) VALUES(?,?,?,?, 'active')",
                ("u-wait", "96899000001", "Waiting user", server.hash_pin("7349")),
            )
            con.execute(
                """INSERT INTO customer_requests(
                id,user_id,customer_name,phone,service_value,service_name,gov,wilayah,
                status,matching_provider_ids,requested_at,offers_open)
                VALUES(?,?,?,?,?,?,?,?, 'matching','[]',?,1)""",
                (
                    "r-wait", "u-wait", "Waiting user", "96899000001",
                    "tech|tech_support", "دعم تقني", "مسقط", "السيب",
                    "2026-08-18T10:00:00+00:00",
                ),
            )
            self.assertEqual([], RequestMarketplace(con).schedule("r-wait"))
            waiting = con.execute(
                "SELECT status,marketplace_status,waitlisted FROM customer_requests WHERE id='r-wait'"
            ).fetchone()
            self.assertEqual(("unavailable", "awaiting_provider", 1), tuple(waiting))
            server.upsert_provider(
                con,
                {
                    "id": "p-new", "name": "New provider", "phone": "96899000002",
                    "pin": "7349", "gov": "مسقط", "wilayah": "السيب",
                    "areas": ["مسقط", "السيب"], "bio": "مزود تقني مهني موثوق",
                    "hours": "08:00-17:00", "status": "available", "active": True,
                    "verified": True, "commercialNo": "CR-NEW",
                    "services": [{
                        "id": "ps-new", "catId": "tech", "serviceId": "networks",
                        "priceFrom": 5, "active": True, "areas": ["السيب"],
                    }],
                },
            )
            result = server.redispatch_waitlisted_requests(con, "p-new")
            self.assertEqual(1, result["matched"])
            updated = con.execute(
                "SELECT status,marketplace_status,waitlisted,matching_provider_ids FROM customer_requests WHERE id='r-wait'"
            ).fetchone()
            self.assertEqual("matching", updated["status"])
            self.assertEqual(0, updated["waitlisted"])
            self.assertIn("p-new", json.loads(updated["matching_provider_ids"]))

    def test_capacity_counts_completed_work_and_diagnostic_enforces_subscription(self):
        with server.db() as con:
            con.execute(
                "INSERT INTO app_users(id,phone,name,pin_hash,status) VALUES(?,?,?,?, 'active')",
                ("u-cap", "96899000011", "Capacity user", server.hash_pin("7349")),
            )
            server.upsert_provider(
                con,
                {
                    "id": "p-cap", "name": "Capacity provider", "phone": "96899000012",
                    "pin": "7349", "gov": "مسقط", "wilayah": "السيب",
                    "areas": ["السيب"], "bio": "مزود تقني معتمد للاختبار",
                    "hours": "08:00-17:00", "status": "available", "active": True,
                    "verified": True,
                    "availability": {"days": [2], "start": "08:00", "end": "17:00", "dailyCapacity": 1},
                    "services": [{
                        "id": "ps-cap", "catId": "tech", "serviceId": "networks",
                        "priceFrom": 5, "active": True, "areas": ["السيب"],
                    }],
                },
            )
            for request_id, status, accepted_provider in (
                ("r-complete", "completed", "p-cap"),
                ("r-candidate", "matching", ""),
            ):
                con.execute(
                    """INSERT INTO customer_requests(
                    id,user_id,customer_name,phone,service_value,service_name,gov,wilayah,
                    status,accepted_provider_id,requested_at,offers_open)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,1)""",
                    (
                        request_id, "u-cap", "Capacity user", "96899000011",
                        "tech|tech_support", "دعم تقني", "مسقط", "السيب", status,
                        accepted_provider, "2026-08-18T10:00:00+00:00",
                    ),
                )
            provider = con.execute("SELECT * FROM providers WHERE id='p-cap'").fetchone()
            candidate = con.execute(
                "SELECT * FROM customer_requests WHERE id='r-candidate'"
            ).fetchone()
            diagnostic = server.provider_request_diagnostic(con, candidate, provider)
            self.assertIn("daily_capacity_reached", diagnostic["reasons"])

            platform = json.loads(
                con.execute("SELECT value FROM settings WHERE key='platform'").fetchone()["value"]
            )
            platform["subscriptionsEnabled"] = True
            con.execute(
                "UPDATE settings SET value=? WHERE key='platform'",
                (json.dumps(platform, ensure_ascii=False),),
            )
            con.execute("DELETE FROM subscriptions WHERE provider_id='p-cap'")
            diagnostic = server.provider_request_diagnostic(con, candidate, provider)
            self.assertIn("subscription_inactive", diagnostic["reasons"])

    def test_legacy_open_leads_use_canonical_matching_gates(self):
        with server.db() as con:
            server.upsert_provider(
                con,
                {
                    "id": "p-lead", "name": "Lead provider",
                    "phone": "96899000021", "pin": "7349", "gov": "مسقط",
                    "wilayah": "بوشر", "areas": ["السيب"],
                    "bio": "مزود تقني معتمد", "status": "available",
                    "active": True, "verified": True,
                    "services": [{
                        "id": "ps-lead", "catId": "tech",
                        "serviceId": "networks", "priceFrom": 5,
                        "active": True, "areas": ["السيب"],
                    }],
                },
            )
            provider = con.execute(
                "SELECT * FROM providers WHERE id='p-lead'"
            ).fetchone()
            lead = {
                "id": "lead-1", "kind": "request", "status": "open",
                "service_value": "tech|tech_support", "gov": "مسقط",
                "wilayah": "السيب",
            }
            self.assertTrue(server.lead_matches_provider(con, lead, provider))
            self.assertFalse(server.lead_matches_provider(
                con, {**lead, "wilayah": "بوشر"}, provider
            ))
            self.assertFalse(server.lead_matches_provider(
                con, {**lead, "service_value": "tech|design"}, provider
            ))
            con.execute("UPDATE providers SET status='busy' WHERE id='p-lead'")
            provider = con.execute(
                "SELECT * FROM providers WHERE id='p-lead'"
            ).fetchone()
            self.assertFalse(server.lead_matches_provider(con, lead, provider))

    def test_lead_wilayah_is_written_and_blank_legacy_rows_are_backfilled(self):
        with server.db() as con:
            con.execute(
                """INSERT INTO app_users(id,phone,name,pin_hash,gov,wilayah,status)
                VALUES(?,?,?,?,?,?, 'active')""",
                (
                    "u-lead-location", "96899000031", "Lead location",
                    server.hash_pin("7349"), "مسقط", "السيب",
                ),
            )
        handler = server.Handler.__new__(server.Handler)
        handler.session = lambda: {"kind": "user", "userId": "u-lead-location"}
        handler.send_json = lambda payload, status=200: (status, payload)
        status, response = handler.save_lead({
            "kind": "request", "serviceValue": "tech|networks",
            "serviceName": "شبكات", "gov": "مسقط", "wilayah": "بوشر",
        })
        self.assertEqual(201, status, response)
        with server.db() as con:
            stored = con.execute(
                "SELECT gov,wilayah FROM leads WHERE id=?",
                (response["lead"]["id"],),
            ).fetchone()
            self.assertEqual(("مسقط", "بوشر"), tuple(stored))
            con.execute(
                """INSERT INTO leads(id,kind,customer_name,phone,note,gov,wilayah,status)
                VALUES('legacy-location','request','Lead location','96899000031','',
                'مسقط','','open')"""
            )
            con.execute(
                """INSERT INTO leads(id,kind,customer_name,phone,note,gov,wilayah,status)
                VALUES('legacy-explicit','request','Lead location','96899000031','',
                'مسقط','بوشر','open')"""
            )
            con.execute(
                "DELETE FROM settings WHERE key=?", (server.MATCHING_MIGRATION_KEY,)
            )
        server.init_db()
        with server.db() as con:
            migrated = con.execute(
                "SELECT wilayah FROM leads WHERE id='legacy-location'"
            ).fetchone()["wilayah"]
            preserved = con.execute(
                "SELECT wilayah FROM leads WHERE id='legacy-explicit'"
            ).fetchone()["wilayah"]
            marker = con.execute(
                "SELECT value FROM settings WHERE key=?",
                (server.MATCHING_MIGRATION_KEY,),
            ).fetchone()
        self.assertEqual("السيب", migrated)
        self.assertEqual("بوشر", preserved)
        self.assertIsNotNone(marker)

    def test_known_provider_invitation_uses_hours_and_capacity_gates(self):
        tuesday = server.datetime(2026, 8, 18, 10, 0, tzinfo=server.UTC)
        with server.db() as con:
            con.execute(
                "INSERT INTO app_users(id,phone,name,pin_hash,status) VALUES(?,?,?,?, 'active')",
                ("u-invite", "96899000041", "Invite user", server.hash_pin("7349")),
            )
            server.upsert_provider(
                con,
                {
                    "id": "p-invite", "name": "Invite provider",
                    "phone": "96899000042", "pin": "7349", "gov": "مسقط",
                    "wilayah": "السيب", "areas": ["السيب"],
                    "bio": "مزود شبكات موثوق للاختبار", "status": "available",
                    "active": True, "verified": True,
                    "availability": {
                        "days": [2], "start": "08:00", "end": "17:00",
                        "dailyCapacity": 1,
                    },
                    "services": [{
                        "id": "ps-invite", "catId": "tech",
                        "serviceId": "networks", "priceFrom": 5,
                        "active": True, "areas": ["السيب"],
                    }],
                },
            )
            for request_id, requested_at in (
                ("r-invite-hours", tuesday.replace(hour=20).isoformat()),
                ("r-invite-capacity", tuesday.isoformat()),
            ):
                con.execute(
                    """INSERT INTO customer_requests(
                    id,user_id,customer_name,phone,service_value,service_name,gov,wilayah,
                    status,requested_at,offers_open)
                    VALUES(?,?,?,?,?,?,?,?, 'matching',?,1)""",
                    (
                        request_id, "u-invite", "Invite user", "96899000041",
                        "tech|networks", "شبكات", "مسقط", "السيب", requested_at,
                    ),
                )
            provider = con.execute(
                "SELECT * FROM providers WHERE id='p-invite'"
            ).fetchone()
            outside = con.execute(
                "SELECT * FROM customer_requests WHERE id='r-invite-hours'"
            ).fetchone()
            with self.assertRaises(DomainError) as outside_error:
                KnownProviderInvitationService(con, now=tuesday).attach(outside, provider)
            self.assertEqual(
                "provider_not_eligible_for_request", outside_error.exception.code
            )
            self.assertEqual("outside_availability", outside_error.exception.detail)

            con.execute(
                """INSERT INTO customer_requests(
                id,user_id,customer_name,phone,service_value,service_name,gov,wilayah,
                status,accepted_provider_id,requested_at,offers_open)
                VALUES('r-invite-complete','u-invite','Invite user','96899000041',
                'tech|networks','شبكات','مسقط','السيب','completed','p-invite',?,1)""",
                (tuesday.isoformat(),),
            )
            capacity = con.execute(
                "SELECT * FROM customer_requests WHERE id='r-invite-capacity'"
            ).fetchone()
            with self.assertRaises(DomainError) as capacity_error:
                KnownProviderInvitationService(con, now=tuesday).attach(capacity, provider)
            self.assertEqual(
                "provider_not_eligible_for_request", capacity_error.exception.code
            )
            self.assertEqual(
                "daily_capacity_reached", capacity_error.exception.detail
            )

    def test_readiness_uses_configured_persistent_paths_and_real_backup_probe(self):
        with server.db() as con:
            tables_before = {
                row["name"] for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        environment = {
            "KHADAMATI_ENV": "production",
            "KHADAMATI_DB_PATH": str(server.DB_PATH),
            "KHADAMATI_UPLOAD_DIR": str(server.UPLOAD_DIR),
            "KHADAMATI_BACKUP_DIR": str(server.BACKUP_DIR),
        }
        result = server.storage_readiness(
            environment=environment,
            db_path=server.DB_PATH,
            upload_dir=server.UPLOAD_DIR,
            backup_dir=server.BACKUP_DIR,
        )
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["checks"]["databaseReadable"])
        self.assertTrue(result["checks"]["databaseWritable"])
        self.assertTrue(result["checks"]["backupOperational"])
        with server.db() as con:
            tables_after = {
                row["name"] for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            retained_probes = con.execute(
                "SELECT COUNT(*) FROM settings WHERE key GLOB '__khadamati_readiness_*'"
            ).fetchone()[0]
        self.assertEqual(tables_before, tables_after)
        self.assertEqual(0, retained_probes)
        self.assertFalse(any(
            name.startswith("khadamati_readiness_") for name in tables_after
        ))
        with mock.patch.object(
            server, "_database_write_rollback_probe", return_value=False
        ):
            read_only = server.storage_readiness(
                environment=environment,
                db_path=server.DB_PATH,
                upload_dir=server.UPLOAD_DIR,
                backup_dir=server.BACKUP_DIR,
            )
        self.assertFalse(read_only["ok"])
        self.assertIn("database_not_writable", read_only["issues"])
        missing = server.storage_readiness(
            environment={"KHADAMATI_ENV": "production"},
            db_path=server.DB_PATH,
            upload_dir=server.UPLOAD_DIR,
            backup_dir=server.BACKUP_DIR,
        )
        self.assertIn("database_path_not_configured", missing["issues"])

    def test_backup_service_creates_verified_snapshots_and_bounds_retention(self):
        service = server.BackupService(retention=2)
        created = [service.create(f"manual-{index}") for index in range(3)]
        self.assertTrue(all(item["verified"] for item in created))
        listed = service.list()
        self.assertEqual(2, len(listed))
        self.assertTrue(all(item["verified"] for item in listed))
        self.assertTrue(all(set(item) == {
            "id", "filename", "sizeBytes", "createdAt", "verified"
        } for item in listed))
        self.assertNotIn(created[0]["id"], {item["id"] for item in listed})
        con = sqlite3.connect(server.DB_PATH)
        try:
            self.assertEqual("ok", con.execute("PRAGMA quick_check").fetchone()[0])
        finally:
            con.close()
        previous_retention = os.environ.pop("KHADAMATI_BACKUP_RETENTION", None)
        try:
            no_delete_dir = Path(self.temp.name) / "backups-no-auto-delete"
            no_delete = server.BackupService(backup_dir=no_delete_dir)
            self.assertEqual(0, no_delete.retention)
            for index in range(3):
                no_delete.create(f"keep-{index}")
            self.assertEqual(3, len(no_delete.list()))
        finally:
            if previous_retention is not None:
                os.environ["KHADAMATI_BACKUP_RETENTION"] = previous_retention

    def test_pre_migration_backup_is_verified_and_failure_aborts_cleanly(self):
        created = server.create_pre_migration_backup("TEST_VERIFIED_MIGRATION")
        self.assertIsNotNone(created)
        self.assertTrue(created.exists())
        self.assertTrue(server.BackupService._verified(created))
        before = {path.name for path in server.BACKUP_DIR.glob("*.sqlite3")}
        with mock.patch.object(
            server.BackupService, "_verified", return_value=False
        ):
            with self.assertRaises(RuntimeError):
                server.create_pre_migration_backup("TEST_FAILED_MIGRATION")
        after = {path.name for path in server.BACKUP_DIR.glob("*.sqlite3")}
        self.assertEqual(before, after)

        legacy_db = Path(self.temp.name) / "legacy" / "before-migration.sqlite3"
        legacy_db.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(legacy_db)
        try:
            con.executescript(
                """CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE leads(id TEXT PRIMARY KEY,phone TEXT,gov TEXT);"""
            )
            con.commit()
        finally:
            con.close()
        active_db, active_backups = server.DB_PATH, server.BACKUP_DIR
        server.DB_PATH = legacy_db
        server.BACKUP_DIR = Path(self.temp.name) / "legacy" / "backups"
        try:
            with mock.patch.object(
                server.BackupService, "_verified", return_value=False
            ):
                with self.assertRaises(RuntimeError):
                    server.init_db()
        finally:
            server.DB_PATH, server.BACKUP_DIR = active_db, active_backups
        con = sqlite3.connect(legacy_db)
        try:
            columns = {
                row[1] for row in con.execute("PRAGMA table_info(leads)")
            }
        finally:
            con.close()
        self.assertNotIn("wilayah", columns)

    def test_admin_mutations_return_canonical_response_contracts(self):
        admin_code = "7349"
        admin_session = {
            "kind": "admin", "id": "admin-contract", "name": "Contract Admin",
            "role": "super_admin",
        }
        with server.db() as con:
            con.execute(
                """INSERT OR REPLACE INTO admin_users(
                id,name,code_hash,role,permissions,active)
                VALUES(?,?,?,?,?,1)""",
                (
                    admin_session["id"], admin_session["name"],
                    server.hash_pin(admin_code), "super_admin", "[]",
                ),
            )
            con.execute(
                """INSERT INTO app_users(
                id,phone,name,pin_hash,gov,wilayah,status)
                VALUES(?,?,?,?,?,?,?)""",
                (
                    "user-contract", "96899000191", "Before update",
                    server.hash_pin("4829"), "مسقط", "السيب", "active",
                ),
            )
            server.upsert_provider(
                con,
                {
                    "id": "provider-contract", "name": "Contract provider",
                    "phone": "96899000192", "pin": "7349", "gov": "مسقط",
                    "wilayah": "السيب", "areas": ["السيب"],
                    "bio": "مزود موثوق لاختبار عقد الاستجابة",
                    "status": "available", "active": True, "verified": True,
                    "services": [{
                        "id": "provider-contract-service", "catId": "tech",
                        "serviceId": "networks", "active": True,
                        "areas": ["السيب"],
                    }],
                },
            )
            con.execute(
                """INSERT INTO reviews(
                id,provider_id,rating,customer_name,phone,comment,approved)
                VALUES(?,?,?,?,?,?,1)""",
                (
                    "review-contract", "provider-contract", 4,
                    "Contract customer", "96899000193", "Useful review",
                ),
            )
            con.execute(
                """INSERT INTO advertisements(
                id,image_path,advertiser,active)
                VALUES(?,?,?,1)""",
                ("ad-contract", "uploads/ad-contract.webp", "Contract advertiser"),
            )

        handler = server.Handler.__new__(server.Handler)
        handler.require_admin = lambda _permission: admin_session
        handler.send_json = lambda payload, status=200: (status, payload)

        status, settings_response = handler.admin_post(
            "/api/admin/settings",
            {
                "monthlyGoal": 321,
                "supportWhatsapp": "96899000194",
                "adminCode": "4829",
            },
        )
        self.assertEqual(200, status, settings_response)
        self.assertTrue(settings_response["ok"])
        self.assertEqual(321, settings_response["settings"]["monthlyGoal"])
        self.assertEqual(
            "96899000194", settings_response["settings"]["supportWhatsapp"]
        )
        self.assertNotIn("adminCode", settings_response["settings"])
        self.assertNotIn("passwords", settings_response["settings"])
        with server.db() as con:
            stored_settings = json.loads(
                con.execute(
                    "SELECT value FROM settings WHERE key='platform'"
                ).fetchone()["value"]
            )
        self.assertEqual(stored_settings, settings_response["settings"])

        status, user_response = handler.admin_post(
            "/api/admin/app-user",
            {
                "action": "update", "id": "user-contract",
                "name": "After update", "phone": "96899000191",
                "gov": "مسقط", "wilayah": "بوشر", "status": "registered",
            },
        )
        self.assertEqual(200, status, user_response)
        self.assertTrue(user_response["ok"])
        self.assertEqual("user-contract", user_response["user"]["id"])
        self.assertEqual("After update", user_response["user"]["name"])
        self.assertEqual("بوشر", user_response["user"]["wilayah"])
        self.assertEqual("active", user_response["user"]["status"])
        self.assertTrue(user_response["user"]["pinConfigured"])
        self.assertNotIn("pin_hash", user_response["user"])

        status, review_response = handler.admin_post(
            "/api/admin/review-status",
            {
                "id": "review-contract", "action": "delete",
                "reason": "Contract moderation test",
            },
        )
        self.assertEqual(200, status, review_response)
        self.assertTrue(review_response["ok"])
        self.assertEqual("review-contract", review_response["review"]["id"])
        self.assertTrue(review_response["review"]["deleted"])
        self.assertFalse(review_response["review"]["approved"])
        self.assertEqual(
            "provider-contract", review_response["provider"]["id"]
        )

        status, ad_response = handler.admin_post(
            "/api/admin/ads", {"id": "ad-contract", "action": "delete"}
        )
        self.assertEqual(200, status, ad_response)
        self.assertEqual(
            {"ok": True, "archived": True, "id": "ad-contract"}, ad_response
        )

        status, delete_response = handler.admin_post(
            "/api/admin/app-user",
            {
                "action": "delete", "id": "user-contract",
                "reason": "Contract deletion test", "adminCode": "4829",
            },
        )
        self.assertEqual(200, status, delete_response)
        self.assertEqual(
            {"ok": True, "deleted": True, "id": "user-contract"},
            delete_response,
        )
        with server.db() as con:
            self.assertEqual(
                "deleted",
                con.execute(
                    "SELECT status FROM app_users WHERE id='user-contract'"
                ).fetchone()["status"],
            )
            archived_ad = con.execute(
                "SELECT active,deleted_at FROM advertisements WHERE id='ad-contract'"
            ).fetchone()
        self.assertEqual(0, archived_ad["active"])
        self.assertTrue(archived_ad["deleted_at"])

    def test_admin_cannot_verify_provider_without_identity_and_two_documents(self):
        admin_session = {
            "kind": "admin", "id": "admin-verification-gate",
            "name": "Verification Gate Admin", "role": "super_admin",
        }
        provider_id = "provider-verification-gate"
        with server.db() as con:
            con.execute(
                """INSERT OR REPLACE INTO admin_users(
                id,name,code_hash,role,permissions,active)
                VALUES(?,?,?,?,?,1)""",
                (
                    admin_session["id"], admin_session["name"],
                    server.hash_pin("7349"), "super_admin", "[]",
                ),
            )
            server.upsert_provider(
                con,
                {
                    "id": provider_id, "providerType": "individual",
                    "name": "Provider awaiting verification",
                    "phone": "96899000201", "pin": "7349",
                    "gov": "مسقط", "wilayah": "السيب", "areas": ["السيب"],
                    "bio": "مزود ينتظر اكتمال وثائق التحقق",
                    "status": "pending", "active": True, "verified": False,
                    "services": [{
                        "id": "verification-gate-service", "catId": "tech",
                        "serviceId": "networks", "active": True,
                        "areas": ["السيب"],
                    }],
                },
            )

        handler = server.Handler.__new__(server.Handler)
        handler.require_admin = lambda _permission: admin_session
        handler.send_json = lambda payload, status=200: (status, payload)
        verification_payload = {
            "id": provider_id, "status": "available", "active": True,
            "verified": True, "featured": False,
        }

        status, missing_identity = handler.admin_post(
            "/api/admin/provider-status", verification_payload
        )
        self.assertEqual(409, status, missing_identity)
        self.assertEqual(
            "commercial_number_required", missing_identity["error"]
        )

        with server.db() as con:
            still_unverified = con.execute(
                "SELECT verified FROM providers WHERE id=?", (provider_id,)
            ).fetchone()["verified"]
            self.assertEqual(0, still_unverified)
            con.execute(
                "UPDATE providers SET commercial_no=? WHERE id=?",
                (
                    "LIC-VERIFY-201",
                    provider_id,
                ),
            )

        status, missing_expiry = handler.admin_post(
            "/api/admin/provider-status", verification_payload
        )
        self.assertEqual(409, status, missing_expiry)
        self.assertEqual("credential_expiry_required", missing_expiry["error"])

        with server.db() as con:
            con.execute(
                "UPDATE providers SET license_expiry=?,documents=? WHERE id=?",
                (
                    "2028-12-31",
                    server.jdump(["uploads/private-documents/front.webp"]),
                    provider_id,
                ),
            )

        status, missing_document = handler.admin_post(
            "/api/admin/provider-status", verification_payload
        )
        self.assertEqual(409, status, missing_document)
        self.assertEqual("documents_required", missing_document["error"])
        self.assertEqual(2, missing_document["requiredDocuments"])
        self.assertEqual(1, missing_document["currentDocuments"])

        with server.db() as con:
            still_unverified = con.execute(
                "SELECT verified FROM providers WHERE id=?", (provider_id,)
            ).fetchone()["verified"]
            self.assertEqual(0, still_unverified)
            con.execute(
                "UPDATE providers SET documents=? WHERE id=?",
                (
                    server.jdump([
                        "uploads/private-documents/front.webp",
                        "uploads/private-documents/back.webp",
                    ]),
                    provider_id,
                ),
            )

        status, verified_response = handler.admin_post(
            "/api/admin/provider-status", verification_payload
        )
        self.assertEqual(200, status, verified_response)
        self.assertTrue(verified_response["ok"])
        self.assertTrue(verified_response["provider"]["verified"])
        with server.db() as con:
            saved = con.execute(
                "SELECT verified,status FROM providers WHERE id=?", (provider_id,)
            ).fetchone()
        self.assertEqual((1, "available"), tuple(saved))

    def test_admin_provider_upsert_cannot_bypass_verification_case(self):
        admin_session = {
            "kind": "admin", "id": "admin-upsert-verification",
            "name": "Provider Admin", "role": "super_admin",
        }
        handler = server.Handler.__new__(server.Handler)
        handler.require_admin = lambda _permission: admin_session
        handler.send_json = lambda payload, status=200: (status, payload)
        base = {
            "id": "provider-admin-upsert",
            "name": "Verified admin provider",
            "phone": "96899000211",
            "pin": "7349",
            "providerType": "individual",
            "commercialNo": "LIC-ADMIN-OLD",
            "licenseExpiry": "2028-12-31",
            "documents": ["uploads/front.webp", "uploads/back.webp"],
            "gov": "مسقط",
            "wilayah": "السيب",
            "areas": ["السيب"],
            "bio": "مزود موثوق لتدقيق مسار الإدارة",
            "hours": "Sunday 09:00 - 17:00",
            "status": "available",
            "active": True,
            "verified": True,
            "services": [{
                "id": "admin-upsert-network-service",
                "catId": "tech",
                "serviceId": "networks",
                "active": True,
                "areas": ["السيب"],
            }],
        }

        status, rejected_create = handler.admin_post(
            "/api/admin/providers", base
        )
        self.assertEqual(409, status, rejected_create)
        self.assertEqual(
            "verification_review_required", rejected_create["error"]
        )
        with server.db() as con:
            self.assertIsNone(
                con.execute(
                    "SELECT id FROM providers WHERE id=?", (base["id"],)
                ).fetchone()
            )
            server.upsert_provider(con, base)

        status, changed = handler.admin_post(
            "/api/admin/providers",
            {**base, "commercialNo": "LIC-ADMIN-NEW"},
        )
        self.assertEqual(200, status, changed)
        self.assertTrue(changed["ok"])
        self.assertTrue(changed["verificationInvalidated"])
        self.assertFalse(changed["provider"]["verified"])
        self.assertEqual("under_review", changed["provider"]["status"])
        self.assertFalse(changed["provider"]["listingEnabled"])
        self.assertFalse(changed["provider"]["requestEnabled"])
        with server.db() as con:
            case = ProviderVerificationService(con).get(
                base["id"], private=True
            )
        self.assertEqual("submitted", case["status"])

    def test_legal_review_uses_evidence_gate_and_controls_eligibility(self):
        admin_session = {
            "kind": "admin", "id": "admin-legal-verification",
            "name": "Legal Admin", "role": "super_admin",
        }
        provider_id = "provider-legal-verification"
        with server.db() as con:
            server.upsert_provider(
                con,
                {
                    "id": provider_id,
                    "name": "Legal verification provider",
                    "phone": "96899000212",
                    "pin": "7349",
                    "providerType": "individual",
                    "commercialNo": "LIC-LEGAL-212",
                    "licenseExpiry": "2028-12-31",
                    "documents": ["uploads/front.webp", "uploads/back.webp"],
                    "nationality": "عُماني",
                    "gov": "مسقط",
                    "wilayah": "السيب",
                    "areas": ["السيب"],
                    "bio": "مزود موثوق لتدقيق القرار القانوني",
                    "status": "available",
                    "active": True,
                    "verified": True,
                    "services": [{
                        "id": "legal-network-service",
                        "catId": "tech",
                        "serviceId": "networks",
                        "active": True,
                        "areas": ["السيب"],
                    }],
                },
            )
            server.ProviderLegalProfileService(con).save(
                provider_id,
                {"pathway": "individual_omani", "nationality": "عُماني"},
            )

        handler = server.Handler.__new__(server.Handler)
        handler.require_admin_platform_action = lambda _action: admin_session
        handler.send_json = lambda payload, status=200: (status, payload)

        status, rejected = handler.admin_platform_post({
            "action": "legal:review",
            "providerId": provider_id,
            "status": "rejected",
            "note": "Evidence rejected",
        })
        self.assertEqual(200, status, rejected)
        self.assertEqual("rejected", rejected["result"]["reviewStatus"])
        self.assertEqual("rejected", rejected["result"]["verification"]["status"])
        self.assertFalse(rejected["result"]["provider"]["verified"])
        self.assertFalse(rejected["result"]["provider"]["listingEnabled"])
        self.assertFalse(rejected["result"]["provider"]["requestEnabled"])

        status, approved = handler.admin_platform_post({
            "action": "legal:review",
            "providerId": provider_id,
            "status": "approved",
            "note": "Evidence approved",
        })
        self.assertEqual(200, status, approved)
        self.assertEqual("approved", approved["result"]["reviewStatus"])
        self.assertEqual("verified", approved["result"]["verification"]["status"])
        self.assertTrue(approved["result"]["provider"]["verified"])
        self.assertEqual(
            "2028-12-31T23:59:59.999999+00:00",
            approved["result"]["verification"]["expiresAt"],
        )

        status, expired = handler.admin_platform_post({
            "action": "legal:review",
            "providerId": provider_id,
            "status": "expired",
            "note": "Credential expired",
        })
        self.assertEqual(200, status, expired)
        self.assertEqual("expired", expired["result"]["verification"]["status"])
        self.assertFalse(expired["result"]["provider"]["verified"])
        self.assertFalse(expired["result"]["provider"]["listingEnabled"])
        self.assertFalse(expired["result"]["provider"]["requestEnabled"])

        with server.db() as con:
            con.execute(
                "UPDATE providers SET license_expiry='bad-date' WHERE id=?",
                (provider_id,),
            )
        status, malformed = self._domain_response(
            lambda: handler.admin_platform_post({
                "action": "legal:review",
                "providerId": provider_id,
                "status": "approved",
                "note": "Must fail closed",
            })
        )
        self.assertEqual(409, status, malformed)
        self.assertEqual("credential_expiry_invalid", malformed["error"])
        with server.db() as con:
            legal_state = con.execute(
                """SELECT review_status FROM provider_legal_profiles
                WHERE provider_id=?""",
                (provider_id,),
            ).fetchone()["review_status"]
            provider_state = con.execute(
                """SELECT verified,listing_enabled,request_enabled
                FROM providers WHERE id=?""",
                (provider_id,),
            ).fetchone()
        self.assertEqual("expired", legal_state)
        self.assertEqual((0, 0, 0), tuple(provider_state))

    def test_production_root_redirects_to_one_safe_public_url(self):
        old_env, old_url = server.APP_ENV, server.PUBLIC_APP_URL
        server.APP_ENV = "production"
        server.PUBLIC_APP_URL = "https://example.test/Khadamati/"
        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=5)
            connection.request("GET", "/")
            response = connection.getresponse()
            response.read()
            self.assertEqual(302, response.status)
            self.assertEqual("https://example.test/Khadamati/", response.getheader("Location"))
            connection.close()
            connection = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=5)
            connection.request("GET", "/index.html")
            response = connection.getresponse()
            response.read()
            self.assertEqual(302, response.status)
            self.assertEqual("https://example.test/Khadamati/", response.getheader("Location"))
            connection.close()
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=3)
            server.APP_ENV, server.PUBLIC_APP_URL = old_env, old_url

    def test_provider_review_reply_requires_ownership_and_commits(self):
        tokens = {"p-one": "provider-one-token", "p-two": "provider-two-token"}
        with server.db() as con:
            for index, provider_id in enumerate(tokens, start=1):
                server.upsert_provider(
                    con,
                    {
                        "id": provider_id, "name": f"Provider {index}",
                        "phone": f"9689900010{index}", "pin": "7349",
                        "gov": "مسقط", "wilayah": "السيب", "areas": ["السيب"],
                        "bio": "مزود معتمد للرد على التقييم", "status": "available",
                        "active": True, "verified": True,
                        "services": [{
                            "id": f"svc-{index}", "catId": "tech",
                            "serviceId": "networks", "active": True,
                            "areas": ["السيب"],
                        }],
                    },
                )
                con.execute(
                    """INSERT INTO auth_sessions(id,token_hash,session_json,expires_at)
                    VALUES(?,?,?,?)""",
                    (
                        f"session-{provider_id}",
                        server.hash_secret(tokens[provider_id]),
                        json.dumps({
                            "kind": "provider", "providerId": provider_id,
                            "role": "provider_owner",
                        }),
                        "2030-01-01T00:00:00+00:00",
                    ),
                )
            for review_id, provider_id in (("review-one", "p-one"), ("review-two", "p-two")):
                con.execute(
                    """INSERT INTO reviews(
                    id,provider_id,rating,customer_name,phone,comment,approved)
                    VALUES(?,?,?,?,?,?,1)""",
                    (
                        review_id, provider_id, 5, "Customer", "96890000000",
                        "Excellent service",
                    ),
                )

        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()

        def post(token, body):
            connection = http.client.HTTPConnection(
                "127.0.0.1", httpd.server_port, timeout=5
            )
            encoded = json.dumps(body).encode("utf-8")
            connection.request(
                "POST", "/api/provider/review-reply", body=encoded,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Content-Length": str(len(encoded)),
                },
            )
            response = connection.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
            status = response.status
            connection.close()
            return status, payload

        try:
            status, payload = post(
                tokens["p-one"],
                {"reviewId": "review-one", "text": "شكراً لثقتكم بخدمتنا."},
            )
            self.assertEqual(200, status, payload)
            self.assertEqual(
                "شكراً لثقتكم بخدمتنا.", payload["review"]["providerReply"]
            )
            self.assertTrue(payload["review"]["providerReplyAt"])
            self.assertNotIn("phone", payload["review"])
            status, _ = post(
                tokens["p-one"],
                {"reviewId": "review-two", "text": "Not my review"},
            )
            self.assertEqual(404, status)
            status, _ = post(
                tokens["p-one"], {"reviewId": "review-one", "text": ""}
            )
            self.assertEqual(400, status)
            with server.db() as con:
                stored = con.execute(
                    "SELECT provider_reply,provider_reply_at FROM reviews WHERE id='review-one'"
                ).fetchone()
                self.assertEqual("شكراً لثقتكم بخدمتنا.", stored["provider_reply"])
                self.assertTrue(stored["provider_reply_at"])
                self.assertEqual(
                    1,
                    con.execute(
                        """SELECT COUNT(*) FROM audit_logs
                        WHERE action='provider.review_reply.saved' AND target='review-one'"""
                    ).fetchone()[0],
                )
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=3)

    def test_admin_platform_dispatches_permissions_by_action(self):
        token = "settings-only-platform-token"
        admin_id = "admin-settings-only"
        with server.db() as con:
            con.execute(
                """INSERT INTO admin_users(
                id,name,code_hash,role,permissions,active)
                VALUES(?,?,?,?,?,1)""",
                (
                    admin_id,
                    "Settings only",
                    server.hash_pin("7349"),
                    "admin",
                    json.dumps(["manage_settings"]),
                ),
            )
            con.execute(
                """INSERT INTO auth_sessions(
                id,token_hash,session_json,expires_at)
                VALUES(?,?,?,?)""",
                (
                    "session-settings-only",
                    server.hash_secret(token),
                    json.dumps({"kind": "admin", "id": admin_id}),
                    "2030-01-01T00:00:00+00:00",
                ),
            )

        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()

        def post(body):
            connection = http.client.HTTPConnection(
                "127.0.0.1", httpd.server_port, timeout=5
            )
            encoded = json.dumps(body).encode("utf-8")
            connection.request(
                "POST",
                "/api/admin/platform",
                body=encoded,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Content-Length": str(len(encoded)),
                },
            )
            response = connection.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
            status = response.status
            connection.close()
            return status, payload

        try:
            cross_domain_actions = (
                {"action": "legal:review", "providerId": "provider-denied"},
                {
                    "action": "risk:record",
                    "subjectKind": "provider",
                    "subjectId": "provider-denied",
                    "signalType": "manual_review",
                    "signals": ["permission probe"],
                    "score": 50,
                },
                {"action": "risk:resolve", "id": "risk-denied"},
                {
                    "action": "scenario:save",
                    "name": "Denied scenario",
                    "assumptions": {"providerCount": 10, "paidRatio": 20},
                },
                {
                    "action": "enterprise:create",
                    "organizationId": "organization-denied",
                    "name": "Denied key",
                    "scopes": ["requests:read"],
                },
                {"action": "enterprise:revoke", "id": "client-denied"},
            )
            for body in cross_domain_actions:
                with self.subTest(action=body["action"]):
                    status, payload = post(body)
                    self.assertEqual(403, status, payload)
                    self.assertEqual("permission_denied", payload["error"])

            status, payload = post(
                {
                    "action": "feature:update",
                    "key": "settings_permission_probe",
                    "enabled": False,
                    "rolloutPercentage": 0,
                    "audiences": ["admin"],
                    "config": {},
                }
            )
            self.assertEqual(200, status, payload)
            self.assertTrue(payload["ok"])
            self.assertEqual("settings_permission_probe", payload["result"]["key"])
            with server.db() as con:
                saved = con.execute(
                    """SELECT updated_by FROM platform_feature_flags
                    WHERE key='settings_permission_probe'"""
                ).fetchone()
            self.assertIsNotNone(saved)
            self.assertEqual(admin_id, saved["updated_by"])
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=3)

    def test_commercial_endpoint_requires_permission_and_commits_before_success(self):
        tokens = {
            "finance": "finance-token", "support": "support-token",
            "backup": "backup-token",
        }
        with server.db() as con:
            for role, permissions in (
                ("finance", ["manage_finance"]),
                ("support", ["review_requests"]),
                ("backup", ["backup"]),
            ):
                con.execute(
                    """INSERT INTO admin_users(id,name,code_hash,role,permissions,active)
                    VALUES(?,?,?,?,?,1)""",
                    (
                        f"admin-{role}", role.title(), server.hash_pin("7349"), role,
                        json.dumps(permissions),
                    ),
                )
                con.execute(
                    """INSERT INTO auth_sessions(id,token_hash,session_json,expires_at)
                    VALUES(?,?,?,?)""",
                    (
                        f"session-{role}", server.hash_secret(tokens[role]),
                        json.dumps({"kind": "admin", "id": f"admin-{role}"}),
                        "2030-01-01T00:00:00+00:00",
                    ),
                )
        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()

        def post(token, body, path="/api/admin/finance"):
            connection = http.client.HTTPConnection(
                "127.0.0.1", httpd.server_port, timeout=5
            )
            encoded = json.dumps(body).encode("utf-8")
            connection.request(
                "POST", path, body=encoded,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Content-Length": str(len(encoded)),
                },
            )
            response = connection.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
            status = response.status
            connection.close()
            return status, payload

        def get(token, path):
            connection = http.client.HTTPConnection(
                "127.0.0.1", httpd.server_port, timeout=5
            )
            connection.request(
                "GET", path, headers={"Authorization": f"Bearer {token}"}
            )
            response = connection.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
            status = response.status
            connection.close()
            return status, payload

        try:
            status, _ = post(
                tokens["support"],
                {"id": "fin-denied", "kind": "revenue", "amount": 1},
            )
            self.assertEqual(403, status)
            status, payload = post(
                tokens["finance"],
                {"id": "fin-http", "kind": "revenue", "amount": "2.125"},
            )
            self.assertEqual(200, status, payload)
            self.assertEqual(2.125, payload["record"]["amount"])
            with server.db() as con:
                self.assertIsNotNone(
                    con.execute("SELECT id FROM finance_entries WHERE id='fin-http'").fetchone()
                )
                self.assertEqual(
                    1,
                    con.execute(
                        "SELECT COUNT(*) FROM finance_entry_events WHERE entry_id='fin-http'"
                    ).fetchone()[0],
                )
                self.assertEqual(
                    1,
                    con.execute(
                        "SELECT COUNT(*) FROM audit_logs WHERE target='fin-http'"
                    ).fetchone()[0],
                )
            status, _ = post(
                tokens["support"], {"action": "create"}, "/api/backup"
            )
            self.assertEqual(403, status)
            status, payload = post(
                tokens["backup"], {"action": "create", "label": "release"},
                "/api/backup",
            )
            self.assertEqual(201, status, payload)
            self.assertTrue(payload["backup"]["verified"])
            self.assertNotIn("path", payload["backup"])
            status, listing = get(tokens["backup"], "/api/backup")
            self.assertEqual(200, status, listing)
            self.assertIn(
                payload["backup"]["id"],
                {item["id"] for item in listing["backups"]},
            )
            with server.db() as con:
                self.assertEqual(
                    1,
                    con.execute(
                        "SELECT COUNT(*) FROM audit_logs WHERE action='backup.created' AND target=?",
                        (payload["backup"]["id"],),
                    ).fetchone()[0],
                )
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=3)

    def test_admin_email_login_still_requires_configured_totp(self):
        admin_id = "email-2fa-admin"
        email_code = "482731"
        with server.db() as con:
            con.execute(
                """INSERT INTO admin_users(
                id,name,code_hash,role,permissions,active)
                VALUES(?,?,?,?,?,1)""",
                (
                    admin_id,
                    "Email 2FA administrator",
                    server.hash_pin("7349"),
                    "super_admin",
                    "[]",
                ),
            )
            two_factor = server.AdminTwoFactorService(con, server.ADMIN_2FA_KEY)
            setup = two_factor.begin(admin_id, "Email 2FA administrator")
            two_factor.confirm(
                setup["challengeId"],
                two_factor._totp(setup["secret"], two_factor.now),
            )
            con.execute(
                """INSERT INTO admin_email_challenges(
                id,admin_id,code_hash,request_key,expires_at)
                VALUES(?,?,?,?,?)""",
                (
                    "email-2fa-challenge",
                    admin_id,
                    server.hash_pin(email_code),
                    "email-2fa-request",
                    server.iso_datetime(minutes=10),
                ),
            )

        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()

        def post(body):
            connection = http.client.HTTPConnection(
                "127.0.0.1", httpd.server_port, timeout=5
            )
            encoded = json.dumps(body).encode("utf-8")
            connection.request(
                "POST",
                "/api/admin/login",
                body=encoded,
                headers={
                    "Content-Type": "application/json",
                    "Content-Length": str(len(encoded)),
                },
            )
            response = connection.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
            status = response.status
            cookie = response.getheader("Set-Cookie")
            connection.close()
            return status, payload, cookie

        try:
            with mock.patch.object(server, "REQUIRE_ADMIN_2FA", True):
                status, required, cookie = post({
                    "emailChallengeId": "email-2fa-challenge",
                    "emailCode": email_code,
                    "deviceId": "email-2fa-device",
                })
                self.assertEqual(200, status, required)
                self.assertTrue(required["twoFactorRequired"])
                self.assertNotIn("token", required)
                self.assertIsNone(cookie)
                with server.db() as con:
                    challenge = con.execute(
                        "SELECT used_at FROM admin_email_challenges WHERE id=?",
                        ("email-2fa-challenge",),
                    ).fetchone()
                self.assertEqual("", challenge["used_at"])

                current_totp = server.AdminTwoFactorService._totp(
                    setup["secret"], server.datetime.now(server.UTC)
                )
                status, authenticated, cookie = post({
                    "emailChallengeId": "email-2fa-challenge",
                    "emailCode": email_code,
                    "twoFactorCode": current_totp,
                    "deviceId": "email-2fa-device",
                })
                self.assertEqual(200, status, authenticated)
                self.assertEqual("admin", authenticated["sessionKind"])
                self.assertTrue(authenticated.get("token"))
                self.assertIn("khadamati_admin_refresh=", cookie or "")
                with server.db() as con:
                    challenge = con.execute(
                        "SELECT used_at FROM admin_email_challenges WHERE id=?",
                        ("email-2fa-challenge",),
                    ).fetchone()
                self.assertTrue(challenge["used_at"])
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=3)

    def test_admin_email_login_without_2fa_keeps_non_required_behavior(self):
        admin_id = "email-no-2fa-admin"
        email_code = "592814"
        with server.db() as con:
            con.execute(
                """INSERT INTO admin_users(
                id,name,code_hash,role,permissions,active)
                VALUES(?,?,?,?,?,1)""",
                (
                    admin_id,
                    "Email administrator without 2FA",
                    server.hash_pin("7349"),
                    "admin",
                    "[]",
                ),
            )
            con.execute(
                """INSERT INTO admin_email_challenges(
                id,admin_id,code_hash,request_key,expires_at)
                VALUES(?,?,?,?,?)""",
                (
                    "email-no-2fa-challenge",
                    admin_id,
                    server.hash_pin(email_code),
                    "email-no-2fa-request",
                    server.iso_datetime(minutes=10),
                ),
            )

        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        connection = http.client.HTTPConnection(
            "127.0.0.1", httpd.server_port, timeout=5
        )
        body = json.dumps({
            "emailChallengeId": "email-no-2fa-challenge",
            "emailCode": email_code,
            "deviceId": "email-no-2fa-device",
        }).encode("utf-8")
        try:
            with mock.patch.object(server, "REQUIRE_ADMIN_2FA", False):
                connection.request(
                    "POST",
                    "/api/admin/login",
                    body=body,
                    headers={
                        "Content-Type": "application/json",
                        "Content-Length": str(len(body)),
                    },
                )
                response = connection.getresponse()
                payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(200, response.status, payload)
                self.assertEqual("admin", payload["sessionKind"])
                self.assertTrue(payload.get("token"))
                self.assertFalse(payload["user"]["twoFactorEnabled"])
        finally:
            connection.close()
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=3)

    def test_required_2fa_email_login_starts_setup_instead_of_session(self):
        admin_id = "email-required-setup-admin"
        email_code = "617203"
        with server.db() as con:
            con.execute(
                """INSERT INTO admin_users(
                id,name,code_hash,role,permissions,active)
                VALUES(?,?,?,?,?,1)""",
                (
                    admin_id,
                    "Email administrator requiring setup",
                    server.hash_pin("7349"),
                    "admin",
                    "[]",
                ),
            )
            con.execute(
                """INSERT INTO admin_email_challenges(
                id,admin_id,code_hash,request_key,expires_at)
                VALUES(?,?,?,?,?)""",
                (
                    "email-required-setup-challenge",
                    admin_id,
                    server.hash_pin(email_code),
                    "email-required-setup-request",
                    server.iso_datetime(minutes=10),
                ),
            )

        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        connection = http.client.HTTPConnection(
            "127.0.0.1", httpd.server_port, timeout=5
        )
        body = json.dumps({
            "emailChallengeId": "email-required-setup-challenge",
            "emailCode": email_code,
            "deviceId": "email-required-setup-device",
        }).encode("utf-8")
        try:
            with mock.patch.object(server, "REQUIRE_ADMIN_2FA", True):
                connection.request(
                    "POST",
                    "/api/admin/login",
                    body=body,
                    headers={
                        "Content-Type": "application/json",
                        "Content-Length": str(len(body)),
                    },
                )
                response = connection.getresponse()
                payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(200, response.status, payload)
                self.assertTrue(payload["twoFactorSetupRequired"])
                self.assertTrue(payload.get("challengeId"))
                self.assertNotIn("token", payload)
        finally:
            connection.close()
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
