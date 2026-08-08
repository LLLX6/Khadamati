import json
import os
import sqlite3
import sys
import tempfile
import threading
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Keep importing ``server`` safe even when this module is the first test loaded
# by unittest discovery. Every test then switches the globals to its own fresh
# database and restores them in tearDown.
IMPORT_TEMP = tempfile.TemporaryDirectory(prefix="khadamati-booking-api-import-")
os.environ["KHADAMATI_DB_PATH"] = str(Path(IMPORT_TEMP.name) / "import.sqlite3")
os.environ["KHADAMATI_UPLOAD_DIR"] = str(Path(IMPORT_TEMP.name) / "uploads")
os.environ["KHADAMATI_ENV"] = "test"
os.environ["KHADAMATI_SEED_SAMPLE_DATA"] = "0"
os.environ.setdefault("KHADAMATI_ADMIN_CODE", "839174")

import server  # noqa: E402
from khadamati_domain import DomainError  # noqa: E402
from khadamati_workflow import (  # noqa: E402
    BookingPolicyService,
    InstantBookingService,
    NotificationActionService,
    RequestChangeOrderService,
    RequestWorkOrderService,
)


NOW = datetime(2026, 8, 8, 12, tzinfo=UTC)
SERVICE_VALUE = "homecare|electrician"


class BookingApiTests(unittest.TestCase):
    def setUp(self):
        self.case_temp = tempfile.TemporaryDirectory(prefix="khadamati-booking-api-")
        self.previous_globals = (
            server.DB_PATH,
            server.UPLOAD_DIR,
            server.SAMPLE_DATA_ENABLED,
        )
        server.DB_PATH = Path(self.case_temp.name) / "booking-api.sqlite3"
        server.UPLOAD_DIR = Path(self.case_temp.name) / "uploads"
        server.SAMPLE_DATA_ENABLED = False
        server.init_db()

    def tearDown(self):
        server.DB_PATH, server.UPLOAD_DIR, server.SAMPLE_DATA_ENABLED = (
            self.previous_globals
        )
        self.case_temp.cleanup()

    @staticmethod
    def _connect():
        con = sqlite3.connect(server.DB_PATH, timeout=15)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=15000")
        con.execute("PRAGMA foreign_keys=ON")
        return con

    @staticmethod
    @contextmanager
    def _db():
        con = BookingApiTests._connect()
        try:
            with con:
                yield con
        finally:
            con.close()

    @staticmethod
    def _offer(offer_id, provider_id, price):
        return {
            "id": offer_id,
            "providerId": provider_id,
            "price": price,
            "laborAmount": price,
            "materialsAmount": 0,
            "durationMinutes": 60,
            "scope": f"Private scope for {offer_id}",
            "validUntil": "2027-01-01T00:00:00+00:00",
        }

    @staticmethod
    def _insert_user(con, user_id, phone_suffix):
        con.execute(
            """INSERT INTO app_users(id,phone,name,pin_hash,status,gov,wilayah)
            VALUES(?,?,?,?, 'active','Muscat','Seeb')""",
            (
                user_id,
                f"96895{phone_suffix:06d}",
                f"User {user_id}",
                server.hash_pin("7349"),
            ),
        )

    @staticmethod
    def _insert_provider(con, provider_id, phone_suffix):
        con.execute(
            """INSERT INTO providers(
            id,name,phone,gov,wilayah,areas,bio,hours,status,active,verified,
            featured,package_id,rating,reviews,services,request_enabled,deleted_at,
            availability)
            VALUES(?,?,?,?,?,?,?,?, 'available',1,1,0,'',0,0,?,1,'',?)""",
            (
                provider_id,
                f"Provider {provider_id}",
                f"96891{phone_suffix:06d}",
                "Muscat",
                "Seeb",
                json.dumps(["Muscat", "Seeb"]),
                "Professional test provider",
                "Sunday 08:00-18:00",
                json.dumps(
                    [
                        {
                            "catId": "homecare",
                            "serviceId": "electrician",
                            "active": True,
                        }
                    ]
                ),
                json.dumps(
                    {
                        "days": ["0"],
                        "start": "08:00",
                        "end": "18:00",
                        "dailyCapacity": 2,
                    }
                ),
            ),
        )

    @staticmethod
    def _insert_request(
        con,
        request_id,
        user_id,
        offers,
        *,
        fulfillment_mode="quoted",
        matching_provider_ids=None,
    ):
        con.execute(
            """INSERT INTO customer_requests(
            id,user_id,customer_name,phone,service_value,service_name,gov,wilayah,
            location_text,requested_at,status,accepted_provider_id,
            matching_provider_ids,offers,offers_open,workflow_version,
            fulfillment_mode,pricing_mode,default_duration_minutes,evidence_policy,
            start_verification_mode)
            VALUES(?,?,?,?,?,?,?,?,?,?,'viewed','',?,?,1,'booking_v2',?, ?,60,
            'optional','none')""",
            (
                request_id,
                user_id,
                f"Customer {user_id}",
                "96895000000",
                SERVICE_VALUE,
                "Electrician",
                "Muscat",
                "Seeb",
                "Seeb",
                "2026-08-09T04:00:00+00:00",
                json.dumps(matching_provider_ids or []),
                json.dumps(offers),
                fulfillment_mode,
                "fixed" if fulfillment_mode == "instant" else "quote",
            ),
        )

    @staticmethod
    def _http_call(path, data, session, *, method="POST"):
        handler = object.__new__(server.Handler)
        handler.path = path
        handler.headers = {}
        handler.session = types.MethodType(lambda _self: session, handler)
        handler.read_json = types.MethodType(lambda _self: data, handler)
        handler.send_json = types.MethodType(
            lambda _self, payload, status=200, extra_headers=None: (status, payload),
            handler,
        )
        if method == "GET":
            return server.Handler.do_GET(handler)
        return server.Handler.do_POST(handler)

    def _seed_quoted_booking(self, suffix="auth"):
        user_id = f"user-{suffix}"
        winner_id = f"provider-{suffix}-winner"
        loser_id = f"provider-{suffix}-loser"
        request_id = f"request-{suffix}"
        offers = [
            self._offer(f"offer-{suffix}-winner", winner_id, 20),
            self._offer(f"offer-{suffix}-loser", loser_id, 22),
        ]
        with self._db() as con:
            self._insert_user(con, user_id, 100001)
            self._insert_provider(con, winner_id, 200001)
            self._insert_provider(con, loser_id, 200002)
            self._insert_request(
                con,
                request_id,
                user_id,
                offers,
                matching_provider_ids=[winner_id, loser_id],
            )
            work_order, duplicate = RequestWorkOrderService(con, now=NOW).accept_offer(
                request_id,
                user_id,
                offers[0]["id"],
                offers=offers,
            )
            self.assertFalse(duplicate)
        return user_id, winner_id, loser_id, request_id, offers, work_order

    def test_two_concurrent_offer_acceptances_create_only_one_work_order(self):
        user_id = "user-offer-race"
        request_id = "request-offer-race"
        providers = ["provider-offer-race-a", "provider-offer-race-b"]
        offers = [
            self._offer("offer-race-a", providers[0], 18),
            self._offer("offer-race-b", providers[1], 21),
        ]
        with self._db() as con:
            self._insert_user(con, user_id, 110001)
            self._insert_provider(con, providers[0], 210001)
            self._insert_provider(con, providers[1], 210002)
            self._insert_request(
                con,
                request_id,
                user_id,
                offers,
                matching_provider_ids=providers,
            )

        barrier = threading.Barrier(3)

        def accept(offer):
            con = self._connect()
            try:
                barrier.wait(timeout=5)
                con.execute("BEGIN IMMEDIATE")
                order, duplicate = RequestWorkOrderService(con, now=NOW).accept_offer(
                    request_id,
                    user_id,
                    offer["id"],
                    offers=offers,
                )
                con.commit()
                return "accepted", order["acceptedOfferId"], duplicate
            except DomainError as error:
                con.rollback()
                return "rejected", error.code, error.status
            finally:
                con.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(accept, offer) for offer in offers]
            barrier.wait(timeout=5)
            results = [future.result(timeout=20) for future in futures]

        self.assertEqual(1, sum(result[0] == "accepted" for result in results))
        self.assertEqual(
            [("rejected", "offer_selection_conflict", 409)],
            [result for result in results if result[0] == "rejected"],
        )
        with self._db() as con:
            self.assertEqual(
                1,
                con.execute(
                    "SELECT COUNT(*) n FROM request_work_orders WHERE request_id=?",
                    (request_id,),
                ).fetchone()["n"],
            )
            row = con.execute(
                """SELECT status,accepted_provider_id,offers_open
                FROM customer_requests WHERE id=?""",
                (request_id,),
            ).fetchone()
            self.assertEqual("accepted", row["status"])
            self.assertIn(row["accepted_provider_id"], providers)
            self.assertEqual(0, row["offers_open"])

    def test_two_users_cannot_reserve_the_same_instant_slot(self):
        provider_id = "provider-slot-race"
        users = ["user-slot-race-a", "user-slot-race-b"]
        requests = ["request-slot-race-a", "request-slot-race-b"]
        with self._db() as con:
            self._insert_provider(con, provider_id, 220001)
            self._insert_user(con, users[0], 120001)
            self._insert_user(con, users[1], 120002)
            BookingPolicyService(con).save(
                SERVICE_VALUE,
                {
                    "fulfillmentMode": "instant",
                    "pricingMode": "fixed",
                    "fixedPriceAmount": 18.5,
                    "defaultDurationMinutes": 60,
                    "evidencePolicy": "optional",
                    "startVerificationMode": "none",
                    "autoCloseEnabled": True,
                    "completionWindowHours": 24,
                },
                "test-admin",
            )
            slot = InstantBookingService(con, now=NOW).upsert_slot(
                provider_id,
                SERVICE_VALUE,
                "2026-08-09T04:00:00+00:00",
            )
            for request_id, user_id in zip(requests, users):
                self._insert_request(
                    con,
                    request_id,
                    user_id,
                    [],
                    fulfillment_mode="instant",
                    matching_provider_ids=[],
                )

        barrier = threading.Barrier(3)

        def reserve(request_id, user_id, key):
            con = self._connect()
            try:
                barrier.wait(timeout=5)
                order, _, duplicate = InstantBookingService(con, now=NOW).book(
                    request_id,
                    user_id,
                    slot["id"],
                    idempotency_key=key,
                )
                con.commit()
                return "booked", order["requestId"], duplicate
            except DomainError as error:
                con.rollback()
                return "rejected", error.code, error.status
            finally:
                con.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(reserve, requests[0], users[0], "slot-race-key-a"),
                pool.submit(reserve, requests[1], users[1], "slot-race-key-b"),
            ]
            barrier.wait(timeout=5)
            results = [future.result(timeout=20) for future in futures]

        self.assertEqual(1, sum(result[0] == "booked" for result in results))
        rejected = [result for result in results if result[0] == "rejected"]
        self.assertEqual(1, len(rejected))
        self.assertIn(rejected[0][1], {"instant_slot_reserved", "instant_booking_conflict"})
        self.assertEqual(409, rejected[0][2])
        with self._db() as con:
            self.assertEqual(
                1,
                con.execute(
                    "SELECT COUNT(*) n FROM request_slot_reservations WHERE slot_id=?",
                    (slot["id"],),
                ).fetchone()["n"],
            )
            self.assertEqual(
                1,
                con.execute(
                    """SELECT COUNT(*) n FROM request_work_orders
                    WHERE request_id IN (?,?)""",
                    requests,
                ).fetchone()["n"],
            )
            statuses = {
                row["status"]
                for row in con.execute(
                    "SELECT status FROM customer_requests WHERE id IN (?,?)", requests
                )
            }
            self.assertEqual({"accepted", "viewed"}, statuses)

    def test_provider_slot_inventory_remains_visible_without_active_entitlement(self):
        provider_id = "provider-slot-entitlement"
        with self._db() as con:
            self._insert_provider(con, provider_id, 220011)
            BookingPolicyService(con).save(
                SERVICE_VALUE,
                {
                    "fulfillmentMode": "instant",
                    "pricingMode": "fixed",
                    "fixedPriceAmount": 18.5,
                    "defaultDurationMinutes": 60,
                    "evidencePolicy": "optional",
                    "startVerificationMode": "none",
                    "autoCloseEnabled": True,
                    "completionWindowHours": 24,
                },
                "test-admin",
            )
            InstantBookingService(con, now=NOW).upsert_slot(
                provider_id,
                SERVICE_VALUE,
                "2026-08-09T04:00:00+00:00",
            )
        provider_session = {"kind": "provider", "providerId": provider_id}
        status, payload = self._http_call(
            "/api/instant-booking",
            {
                "action": "list",
                "serviceValue": SERVICE_VALUE,
                "providerId": provider_id,
                "startsAfter": "2026-08-08T12:00:00+00:00",
            },
            provider_session,
        )
        self.assertEqual(200, status)
        self.assertEqual(1, len(payload["slots"]))
        self.assertTrue(payload["slots"][0]["available"])
        self.assertFalse(payload["slots"][0]["reserved"])

        with self._db() as con:
            server.SubscriptionService(con, now=NOW).request_plan(
                provider_id,
                "individual_free_3m",
                payment_required=False,
                actor="test",
            )
        status, payload = self._http_call(
            "/api/instant-booking",
            {
                "action": "list",
                "serviceValue": SERVICE_VALUE,
                "providerId": provider_id,
                "startsAfter": "2026-08-08T12:00:00+00:00",
            },
            provider_session,
        )
        self.assertEqual(200, status)
        self.assertEqual(1, len(payload["slots"]))

    def test_workflow_api_denies_customer_and_unassigned_provider_and_hides_private_data(self):
        user_id, winner_id, loser_id, request_id, _, _ = self._seed_quoted_booking()

        user_session = {"kind": "user", "userId": user_id, "id": user_id}
        loser_session = {
            "kind": "provider",
            "providerId": loser_id,
            "id": loser_id,
            "role": "provider_owner",
            "permissions": ["requests"],
        }
        status, payload = self._http_call(
            "/api/request/workflow",
            {"id": request_id, "action": "start_work"},
            user_session,
        )
        self.assertEqual(403, status)
        self.assertEqual("provider_required", payload["error"])

        for action in ("start_work", "completion_submit"):
            status, payload = self._http_call(
                "/api/request/workflow",
                {"id": request_id, "action": action, "note": "must not persist"},
                loser_session,
            )
            self.assertEqual(403, status)
            self.assertEqual("request_access_denied", payload["error"])

        bootstrap = server.get_bootstrap(loser_session)
        visible = next(
            item for item in bootstrap["customerRequests"] if item["id"] == request_id
        )
        self.assertIsNone(visible["workOrder"])
        self.assertEqual([], visible["workOrderVersions"])
        self.assertEqual([], visible["changeOrders"])
        self.assertEqual([], visible["timeline"])
        self.assertIsNone(visible["completionEvidence"])
        self.assertEqual([], visible["messages"])
        self.assertEqual("", visible["phone"])
        self.assertEqual("", visible["locationText"])
        self.assertNotIn("start_work", visible["allowedActions"])

        with self._db() as con:
            row = con.execute(
                "SELECT status,accepted_provider_id FROM customer_requests WHERE id=?",
                (request_id,),
            ).fetchone()
            self.assertEqual("accepted", row["status"])
            self.assertEqual(winner_id, row["accepted_provider_id"])
            self.assertIsNone(
                con.execute(
                    "SELECT * FROM request_completion_evidence WHERE request_id=?",
                    (request_id,),
                ).fetchone()
            )

    def test_stale_change_order_decision_returns_http_409_and_preserves_version(self):
        user_id, winner_id, _, request_id, _, _ = self._seed_quoted_booking("change")
        with self._db() as con:
            change = RequestChangeOrderService(con, now=NOW).propose(
                request_id,
                "provider",
                winner_id,
                expected_version=1,
                changes={"priceAmount": 24, "scope": "Updated private scope"},
                reason="Part price changed",
                idempotency_key="change-api-stale-0001",
            )

        status, payload = self._http_call(
            "/api/request/workflow",
            {
                "id": request_id,
                "action": "change_decide",
                "changeOrderId": change["id"],
                "decision": "accepted",
                "expectedVersion": 99,
                "idempotencyKey": "decision-api-stale-0001",
            },
            {"kind": "user", "userId": user_id, "id": user_id},
        )
        self.assertEqual(409, status)
        self.assertEqual("work_order_version_changed", payload["error"])
        with self._db() as con:
            work_order = con.execute(
                "SELECT version,price_total FROM request_work_orders WHERE request_id=?",
                (request_id,),
            ).fetchone()
            pending = con.execute(
                "SELECT status FROM request_change_orders WHERE id=?", (change["id"],)
            ).fetchone()
            self.assertEqual(1, work_order["version"])
            self.assertEqual(20, work_order["price_total"])
            self.assertEqual("pending", pending["status"])

    def test_notification_api_keeps_snoozed_action_pending_and_blocks_dismiss(self):
        owner_id = "user-notification-owner"
        other_id = "user-notification-other"
        request_id = "request-notification"
        with self._db() as con:
            self._insert_user(con, owner_id, 130001)
            self._insert_user(con, other_id, 130002)
            notification_id = server.create_notification(
                con,
                "user",
                owner_id,
                "Action required",
                "Open the request to continue.",
                type_="request",
                related_id=request_id,
                action_route=f"user:request:{request_id}",
                entity_kind="request",
                entity_id=request_id,
                action_kind="review_completion",
                requires_action=True,
                state_version=1,
            )

        other_session = {"kind": "user", "userId": other_id, "id": other_id}
        status, payload = self._http_call(
            f"/api/notifications/{notification_id}",
            {},
            other_session,
            method="GET",
        )
        self.assertEqual(403, status)
        self.assertEqual("notification_access_denied", payload["error"])

        status, payload = self._http_call(
            "/api/notifications/action",
            {"id": notification_id, "action": "snooze", "snoozeMinutes": 20},
            other_session,
        )
        self.assertEqual(403, status)
        self.assertEqual("notification_access_denied", payload["error"])

        owner_session = {"kind": "user", "userId": owner_id, "id": owner_id}
        status, payload = self._http_call(
            "/api/notifications/action",
            {"id": notification_id, "action": "snooze", "snoozeMinutes": 20},
            owner_session,
        )
        self.assertEqual(200, status)
        self.assertTrue(payload["notification"]["snoozedUntil"])
        self.assertFalse(payload["notification"]["actedAt"])

        status, dismiss = self._http_call(
            "/api/notifications/action",
            {"id": notification_id, "action": "dismiss"},
            owner_session,
        )
        self.assertEqual(409, status)
        self.assertEqual("required_action_cannot_be_dismissed", dismiss["error"])

        with self._db() as con:
            row = con.execute(
                "SELECT * FROM app_notifications WHERE id=?", (notification_id,)
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertTrue(row["snoozed_until"])
            self.assertEqual("", row["dismissed_at"])
            self.assertEqual("", row["acted_at"])
            self.assertEqual(
                1,
                NotificationActionService(con).pending_count("user", owner_id),
            )
            self.assertEqual(
                [],
                NotificationActionService(con).prompt_due("user", owner_id),
            )

    def test_booking_v2_legacy_actions_cannot_cancel_active_or_finished_work(self):
        user_id, _, _, request_id, _, _ = self._seed_quoted_booking("cancel-guard")
        for state in ("inProgress", "awaitingConfirmation", "closed"):
            with self._db() as con:
                con.execute(
                    "UPDATE customer_requests SET status=? WHERE id=?",
                    (state, request_id),
                )
            status, payload = self._http_call(
                "/api/user/requests",
                {"id": request_id, "action": "cancel"},
                {"kind": "user", "userId": user_id, "id": user_id},
            )
            self.assertEqual(409, status)
            self.assertEqual("invalid_request_transition", payload["error"])
            with self._db() as con:
                self.assertEqual(
                    state,
                    con.execute(
                        "SELECT status FROM customer_requests WHERE id=?", (request_id,)
                    ).fetchone()["status"],
                )

    def test_archiving_closed_instant_booking_consumes_active_reservation(self):
        user_id = "user-archive-instant"
        provider_id = "provider-archive-instant"
        request_id = "request-archive-instant"
        with self._db() as con:
            self._insert_user(con, user_id, 140001)
            self._insert_provider(con, provider_id, 240001)
            BookingPolicyService(con).save(
                SERVICE_VALUE,
                {
                    "fulfillmentMode": "instant",
                    "pricingMode": "fixed",
                    "fixedPriceAmount": 18.5,
                    "defaultDurationMinutes": 60,
                    "evidencePolicy": "optional",
                    "startVerificationMode": "none",
                    "autoCloseEnabled": True,
                    "completionWindowHours": 24,
                },
                "test-admin",
            )
            slot = InstantBookingService(con, now=NOW).upsert_slot(
                provider_id, SERVICE_VALUE, "2026-08-09T04:00:00+00:00"
            )
            self._insert_request(
                con, request_id, user_id, [], fulfillment_mode="instant"
            )
            InstantBookingService(con, now=NOW).book(
                request_id,
                user_id,
                slot["id"],
                idempotency_key="instant:archive:booking",
            )
            con.execute(
                "UPDATE customer_requests SET status='closed' WHERE id=?", (request_id,)
            )
        inventory_status, inventory_payload = self._http_call(
            "/api/instant-booking",
            {"action": "list", "providerId": provider_id},
            {
                "kind": "provider",
                "providerId": provider_id,
                "id": provider_id,
                "role": "provider_owner",
            },
        )
        self.assertEqual(200, inventory_status)
        self.assertEqual("active", inventory_payload["slots"][0]["reservationStatus"])
        self.assertTrue(inventory_payload["slots"][0]["reserved"])
        self.assertNotIn("userId", inventory_payload["slots"][0])
        self.assertNotIn("requestId", inventory_payload["slots"][0])
        status, payload = self._http_call(
            "/api/user/requests",
            {"id": request_id, "action": "archive"},
            {"kind": "user", "userId": user_id, "id": user_id},
        )
        self.assertEqual(200, status)
        self.assertEqual("archived", payload["status"])
        with self._db() as con:
            reservation = con.execute(
                "SELECT status FROM request_slot_reservations WHERE request_id=?",
                (request_id,),
            ).fetchone()
            self.assertEqual("completed", reservation["status"])

    def test_kill_switch_allows_only_idempotent_instant_booking_replay(self):
        user_id = "user-kill-switch"
        provider_id = "provider-kill-switch"
        request_id = "request-kill-switch"
        with self._db() as con:
            self._insert_user(con, user_id, 140011)
            self._insert_provider(con, provider_id, 240011)
            BookingPolicyService(con).save(
                SERVICE_VALUE,
                {
                    "fulfillmentMode": "instant",
                    "pricingMode": "fixed",
                    "fixedPriceAmount": 18.5,
                    "defaultDurationMinutes": 60,
                    "evidencePolicy": "optional",
                    "startVerificationMode": "none",
                    "autoCloseEnabled": True,
                    "completionWindowHours": 24,
                },
                "test-admin",
            )
            slot = InstantBookingService(con, now=NOW).upsert_slot(
                provider_id, SERVICE_VALUE, "2026-08-09T04:00:00+00:00"
            )
            self._insert_request(
                con, request_id, user_id, [], fulfillment_mode="instant"
            )
            InstantBookingService(con, now=NOW).book(
                request_id,
                user_id,
                slot["id"],
                idempotency_key="instant:kill-switch:replay",
            )
        session = {"kind": "user", "userId": user_id, "id": user_id}
        status, payload = self._http_call(
            "/api/instant-booking",
            {
                "action": "book",
                "requestId": request_id,
                "slotId": slot["id"],
                "idempotencyKey": "instant:kill-switch:replay",
            },
            session,
        )
        self.assertEqual(200, status)
        self.assertTrue(payload["duplicate"])
        status, payload = self._http_call(
            "/api/instant-booking",
            {"action": "list", "serviceValue": SERVICE_VALUE},
            session,
        )
        self.assertEqual(403, status)
        self.assertEqual("booking_v2_disabled", payload["error"])

    def test_offer_updates_use_monotonic_revision_and_canonical_route(self):
        user_id = "user-offer-revision"
        provider_id = "provider-offer-revision"
        request_id = "request-offer-revision"
        with self._db() as con:
            self._insert_user(con, user_id, 150001)
            self._insert_provider(con, provider_id, 250001)
            self._insert_request(
                con,
                request_id,
                user_id,
                [self._offer("offer-revision", provider_id, 20)],
                matching_provider_ids=[provider_id],
            )
        provider_session = {
            "kind": "provider",
            "providerId": provider_id,
            "id": provider_id,
            "role": "provider_owner",
        }
        for price in (21, 22):
            status, _ = self._http_call(
                "/api/request/collaboration",
                {
                    "id": request_id,
                    "action": "offer",
                    "price": price,
                    "duration": "60 minutes",
                    "scope": "Updated offer",
                },
                provider_session,
            )
            self.assertEqual(200, status)
        with self._db() as con:
            rows = list(
                con.execute(
                    """SELECT id,state_version,action_route,superseded_at
                    FROM app_notifications WHERE entity_kind='request'
                    AND entity_id=? AND action_kind='compare_offers'
                    ORDER BY state_version""",
                    (request_id,),
                )
            )
            self.assertEqual([1, 2], [row["state_version"] for row in rows])
            self.assertTrue(rows[0]["superseded_at"])
            self.assertEqual("", rows[1]["superseded_at"])
            self.assertEqual(f"user:offers:{request_id}", rows[1]["action_route"])

    def test_rejected_change_order_next_prompt_gets_new_revision(self):
        user_id, provider_id, _, request_id, _, _ = self._seed_quoted_booking(
            "change-revision"
        )
        provider_session = {
            "kind": "provider",
            "providerId": provider_id,
            "id": provider_id,
            "role": "provider_owner",
        }
        user_session = {"kind": "user", "userId": user_id, "id": user_id}
        status, first = self._http_call(
            "/api/request/workflow",
            {
                "id": request_id,
                "action": "change_propose",
                "expectedVersion": 1,
                "changes": {"scope": "First scope"},
                "reason": "First revision",
                "idempotencyKey": "change:revision:first",
            },
            provider_session,
        )
        self.assertEqual(200, status)
        first_change = first["request"]["changeOrders"][0]
        status, _ = self._http_call(
            "/api/request/workflow",
            {
                "id": request_id,
                "action": "change_decide",
                "changeOrderId": first_change["id"],
                "decision": "rejected",
                "expectedVersion": 1,
                "idempotencyKey": "change:revision:reject",
            },
            user_session,
        )
        self.assertEqual(200, status)
        status, _ = self._http_call(
            "/api/request/workflow",
            {
                "id": request_id,
                "action": "change_propose",
                "expectedVersion": 1,
                "changes": {"scope": "Second scope"},
                "reason": "Second revision",
                "idempotencyKey": "change:revision:second",
            },
            provider_session,
        )
        self.assertEqual(200, status)
        with self._db() as con:
            rows = list(
                con.execute(
                    """SELECT state_version,acted_at,superseded_at
                    FROM app_notifications WHERE entity_kind='request'
                    AND entity_id=? AND action_kind='review_change_order'
                    AND target_kind='user' ORDER BY state_version""",
                    (request_id,),
                )
            )
            self.assertEqual([1, 2], [row["state_version"] for row in rows])
            self.assertTrue(rows[0]["acted_at"])
            self.assertFalse(rows[1]["acted_at"])
            self.assertFalse(rows[1]["superseded_at"])

    def test_admin_notifications_and_bootstrap_follow_permissions(self):
        user_id, provider_id, _, request_id, _, _ = self._seed_quoted_booking("admin-scope")
        with self._db() as con:
            con.execute(
                """INSERT INTO provider_verification_cases(
                id,provider_id,status,evidence,reviewer_id,decision_note,managed)
                VALUES('verification-admin-scope',?,'verified',?,'private-reviewer',
                'private decision',1)""",
                (provider_id, json.dumps([{"path": "private-document.pdf"}])),
            )
            con.execute(
                """INSERT INTO reviews(
                id,provider_id,rating,customer_name,phone,comment,approved,
                request_id,user_id) VALUES(
                'review-admin-scope',?,5,'Private customer','96899999999',
                'Private review',1,?,?)""",
                (provider_id, request_id, user_id),
            )
            con.execute(
                """INSERT INTO complaints(
                id,provider_id,customer_name,phone,reason,detail,status,request_id,user_id)
                VALUES('complaint-admin-scope',?,'Private customer','96899999999',
                'quality','Private complaint','open',?,?)""",
                (provider_id, request_id, user_id),
            )
            con.execute(
                """INSERT INTO subscriptions(
                id,provider_id,package_id,amount,status)
                VALUES('subscription-admin-scope',?,'starter',12,'active')""",
                (provider_id,),
            )
            con.execute(
                """INSERT INTO payments(
                id,provider_id,subscription_id,kind,amount,status,note)
                VALUES('payment-admin-scope',?,'subscription-admin-scope',
                'subscription',12,'paid','Private payment')""",
                (provider_id,),
            )
            con.execute(
                """INSERT INTO audit_logs(
                id,actor_kind,actor_id,action,target,detail)
                VALUES('audit-admin-scope','admin','private-admin','private_action',
                'private-target','Private audit detail')"""
            )
            con.execute(
                """INSERT INTO leads(
                id,provider_id,kind,customer_name,phone,note,status)
                VALUES('lead-admin-scope',?,'request','Private lead','96899999999',
                'Private lead note','open')""",
                (provider_id,),
            )
            admin_notification = server.create_notification(
                con,
                "admin",
                "",
                "Review request",
                type_="request",
                related_id=request_id,
            )
            user_notification = server.create_notification(
                con, "user", user_id, "Private user notice", type_="request"
            )
        settings_admin = {
            "kind": "admin",
            "id": "admin-settings",
            "name": "Settings admin",
            "role": "admin",
            "permissions": ["manage_settings"],
        }
        review_admin = {
            "kind": "admin",
            "id": "admin-review",
            "name": "Review admin",
            "role": "admin",
            "permissions": ["review_requests"],
        }
        status, _ = self._http_call(
            f"/api/notifications/{admin_notification}", {}, settings_admin, method="GET"
        )
        self.assertEqual(403, status)
        status, _ = self._http_call(
            f"/api/notifications/{admin_notification}", {}, review_admin, method="GET"
        )
        self.assertEqual(200, status)
        status, _ = self._http_call(
            "/api/notifications/action",
            {"id": user_notification, "action": "read"},
            review_admin,
        )
        self.assertEqual(403, status)
        settings_bootstrap = server.get_bootstrap(settings_admin)
        self.assertEqual([], settings_bootstrap["customerRequests"])
        self.assertEqual(["manage_settings"], settings_bootstrap["permissions"])
        redacted_provider = next(
            item for item in settings_bootstrap["providers"]
            if item["id"] == provider_id
        )
        for private_key in (
            "phone", "email", "documents", "adminNote", "commercialNo",
            "companyId", "verification", "pinConfigured",
        ):
            self.assertNotIn(private_key, redacted_provider)
        for collection in (
            "users", "reviews", "complaints", "subscriptions", "payments",
            "auditLogs", "leads",
        ):
            self.assertEqual([], settings_bootstrap[collection], collection)
        self.assertEqual({}, settings_bootstrap["reports"])
        self.assertEqual({}, settings_bootstrap["financialMetrics"])
        self.assertEqual([], settings_bootstrap["demandGaps"])
        self.assertNotIn("revenue", settings_bootstrap["stats"])
        review_bootstrap = server.get_bootstrap(review_admin)
        request = next(
            item for item in review_bootstrap["customerRequests"]
            if item["id"] == request_id
        )
        self.assertIsNotNone(request["workOrder"])
        self.assertIsNone(request["completionEvidence"])
        self.assertEqual(["review_requests"], review_bootstrap["permissions"])

    def test_outbox_claim_token_blocks_stale_worker_overwrite(self):
        with patch.object(server, "push_ready", return_value=True):
            with self._db() as con:
                notification_id = server.create_notification(
                    con, "user", "outbox-user", "Outbox test"
                )
                con.execute(
                    """UPDATE push_delivery_outbox SET status='processing',
                    locked_at=?,claim_token='stale-worker' WHERE notification_id=?""",
                    ((datetime.now(UTC) - timedelta(minutes=10)).isoformat(), notification_id),
                )
            with patch.object(server, "deliver_push", return_value=True):
                result = server.process_push_outbox()
        self.assertEqual(1, result["delivered"])
        with self._db() as con:
            stale = con.execute(
                """UPDATE push_delivery_outbox SET status='pending'
                WHERE notification_id=? AND status='processing'
                AND claim_token='stale-worker'""",
                (notification_id,),
            )
            self.assertEqual(0, stale.rowcount)
            row = con.execute(
                """SELECT status,claim_token FROM push_delivery_outbox
                WHERE notification_id=?""",
                (notification_id,),
            ).fetchone()
            self.assertEqual("delivered", row["status"])
            self.assertEqual("", row["claim_token"])

    def test_push_endpoint_validation_rejects_private_and_mapped_addresses(self):
        def resolved(ip):
            family = server.socket.AF_INET6 if ":" in ip else server.socket.AF_INET
            return [(family, server.socket.SOCK_STREAM, 6, "", (ip, 443))]

        for address in ("127.0.0.1", "10.0.0.4", "::1", "::ffff:127.0.0.1"):
            with self.assertRaises(DomainError) as blocked:
                server.validate_push_endpoint(
                    "https://fcm.googleapis.com/fcm/send/test",
                    resolver=lambda *_args, ip=address, **_kwargs: resolved(ip),
                )
            self.assertEqual("push_endpoint_not_public", blocked.exception.code)
        with self.assertRaises(DomainError) as untrusted:
            server.validate_push_endpoint(
                "https://push.example.test/send",
                resolver=lambda *_args, **_kwargs: resolved("8.8.8.8"),
            )
        self.assertEqual("push_endpoint_host_not_allowed", untrusted.exception.code)
        self.assertEqual(
            "https://fcm.googleapis.com/fcm/send/test",
            server.validate_push_endpoint(
                "https://fcm.googleapis.com/fcm/send/test",
                resolver=lambda *_args, **_kwargs: resolved("8.8.8.8"),
            ),
        )

    def test_push_subscribe_and_delivery_both_reject_private_dns(self):
        user_id = "user-push-ssrf"
        endpoint = "https://fcm.googleapis.com/fcm/send/rebinding-test"
        with self._db() as con:
            self._insert_user(con, user_id, 160001)
        private_resolution = [
            (
                server.socket.AF_INET,
                server.socket.SOCK_STREAM,
                6,
                "",
                ("127.0.0.1", 443),
            )
        ]
        with patch.object(
            server.socket, "getaddrinfo", return_value=private_resolution
        ):
            status, payload = self._http_call(
                "/api/push/subscribe",
                {
                    "action": "subscribe",
                    "subscription": {
                        "endpoint": endpoint,
                        "keys": {"p256dh": "public-key", "auth": "auth-key"},
                    },
                },
                {"kind": "user", "userId": user_id, "id": user_id},
            )
        self.assertEqual(400, status)
        self.assertEqual("push_endpoint_not_public", payload["error"])

        public_resolution = [
            (
                server.socket.AF_INET,
                server.socket.SOCK_STREAM,
                6,
                "",
                ("8.8.8.8", 443),
            )
        ]
        with patch.object(
            server.socket, "getaddrinfo", return_value=public_resolution
        ):
            status, payload = self._http_call(
                "/api/push/subscribe",
                {
                    "action": "subscribe",
                    "subscription": {
                        "endpoint": "https://attacker.example/push",
                        "keys": {"p256dh": "public-key", "auth": "auth-key"},
                    },
                },
                {"kind": "user", "userId": user_id, "id": user_id},
            )
        self.assertEqual(400, status)
        self.assertEqual("push_endpoint_host_not_allowed", payload["error"])
        with patch.object(
            server.socket, "getaddrinfo", return_value=public_resolution
        ):
            status, _ = self._http_call(
                "/api/push/subscribe",
                {
                    "action": "subscribe",
                    "subscription": {
                        "endpoint": endpoint,
                        "keys": {"p256dh": "public-key", "auth": "auth-key"},
                    },
                },
                {"kind": "user", "userId": user_id, "id": user_id},
            )
        self.assertEqual(200, status)
        with patch.object(server, "push_ready", return_value=True), patch.object(
            server.socket, "getaddrinfo", return_value=private_resolution
        ), patch.object(server, "webpush") as send:
            self.assertTrue(server.deliver_push("user", user_id, {"ttl": 300}))
        send.assert_not_called()
        with self._db() as con:
            self.assertEqual(
                0,
                con.execute(
                    """SELECT active FROM push_subscription_bindings
                    WHERE target_kind='user' AND target_id=? AND endpoint=?""",
                    (user_id, endpoint),
                ).fetchone()["active"],
            )

    def test_push_delivery_uses_bounded_no_redirect_transport(self):
        user_id = "user-push-redirect"
        endpoint = "https://updates.push.services.mozilla.com/wpush/test"
        with self._db() as con:
            self._insert_user(con, user_id, 160002)
            con.execute(
                """INSERT INTO push_subscription_bindings(
                id,target_kind,target_id,endpoint,subscription_json,active)
                VALUES('redirect-binding','user',?,?,?,1)""",
                (user_id, endpoint, json.dumps({"endpoint": endpoint, "keys": {}})),
            )
        public_resolution = [
            (
                server.socket.AF_INET,
                server.socket.SOCK_STREAM,
                6,
                "",
                ("8.8.8.8", 443),
            )
        ]
        response = types.SimpleNamespace(
            status_code=302, reason="Found", text="", headers={}
        )
        observed = {}

        def redirecting_webpush(**kwargs):
            observed["timeout"] = kwargs.get("timeout")
            result = kwargs["requests_session"].post(endpoint, timeout=kwargs["timeout"])
            if result.status_code > 202:
                raise server.WebPushException("redirect refused", response=result)
            return result

        with patch.dict(
            os.environ,
            {"VAPID_PRIVATE_KEY": "test-private", "VAPID_PUBLIC_KEY": "test-public"},
        ), patch.object(server, "push_ready", return_value=True), patch.object(
            server.socket, "getaddrinfo", return_value=public_resolution
        ), patch.object(server, "webpush", side_effect=redirecting_webpush), patch.object(
            server.requests.Session, "post", return_value=response
        ) as post:
            self.assertTrue(server.deliver_push("user", user_id, {"ttl": 300}))
        self.assertEqual(server.PUSH_DELIVERY_TIMEOUT_SECONDS, observed["timeout"])
        self.assertFalse(post.call_args.kwargs["allow_redirects"])
        with self._db() as con:
            self.assertEqual(
                0,
                con.execute(
                    "SELECT active FROM push_subscription_bindings WHERE id='redirect-binding'"
                ).fetchone()["active"],
            )

    def test_push_outbox_waits_without_keys_then_delivers_when_enabled(self):
        with patch.object(server, "push_ready", return_value=False):
            with self._db() as con:
                notification_id = server.create_notification(
                    con, "user", "enable-later-user", "Enable later"
                )
                row = con.execute(
                    """SELECT status,attempts FROM push_delivery_outbox
                    WHERE notification_id=?""",
                    (notification_id,),
                ).fetchone()
                self.assertEqual("pending", row["status"])
                self.assertEqual(0, row["attempts"])
            self.assertEqual(
                {"claimed": 0, "delivered": 0, "retried": 0},
                server.process_push_outbox(),
            )
        with patch.object(server, "push_ready", return_value=True), patch.object(
            server, "deliver_push", return_value=True
        ) as deliver:
            result = server.process_push_outbox()
        self.assertEqual(1, result["claimed"])
        self.assertEqual(1, result["delivered"])
        deliver.assert_called_once()
        with self._db() as con:
            self.assertEqual(
                "delivered",
                con.execute(
                    """SELECT status FROM push_delivery_outbox
                    WHERE notification_id=?""",
                    (notification_id,),
                ).fetchone()["status"],
            )


if __name__ == "__main__":
    unittest.main()
