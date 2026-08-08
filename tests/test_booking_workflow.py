import json
import sqlite3
import unittest
from datetime import UTC, datetime, timedelta

from khadamati_domain import DomainError
from khadamati_workflow import (
    BookingPolicyService,
    CompletionEvidenceService,
    InstantBookingService,
    NotificationActionService,
    RequestChangeOrderService,
    RequestLifecycleService,
    RequestWorkOrderService,
    StartVerificationService,
    install_workflow_schema,
    notification_request_state,
    request_workflow_view,
)


NOW = datetime(2026, 8, 8, 12, tzinfo=UTC)


class BookingWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        self.con.executescript(
            """
            CREATE TABLE app_users(id TEXT PRIMARY KEY);
            CREATE TABLE providers(
              id TEXT PRIMARY KEY,
              active INTEGER DEFAULT 1,
              verified INTEGER DEFAULT 1,
              status TEXT DEFAULT 'available',
              request_enabled INTEGER DEFAULT 1,
              deleted_at TEXT DEFAULT '',
              services TEXT DEFAULT '[]',
              areas TEXT DEFAULT '[]',
              availability TEXT DEFAULT '{}',
              gov TEXT DEFAULT '',
              wilayah TEXT DEFAULT '',
              completed_jobs INTEGER DEFAULT 0,
              updated_at TEXT DEFAULT ''
            );
            CREATE TABLE customer_requests(
              id TEXT PRIMARY KEY,
              user_id TEXT DEFAULT '',
              customer_name TEXT DEFAULT '',
              phone TEXT DEFAULT '',
              service_value TEXT DEFAULT '',
              service_name TEXT DEFAULT '',
              gov TEXT DEFAULT '',
              wilayah TEXT DEFAULT '',
              location_text TEXT DEFAULT '',
              requested_at TEXT DEFAULT '',
              status TEXT DEFAULT 'matching',
              accepted_provider_id TEXT DEFAULT '',
              offers TEXT DEFAULT '[]',
              messages TEXT DEFAULT '[]',
              waitlisted INTEGER DEFAULT 0,
              offers_open INTEGER DEFAULT 1,
              contact_consent TEXT DEFAULT '{}',
              latitude REAL,
              longitude REAL,
              created_at TEXT DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE app_notifications(
              id TEXT PRIMARY KEY,
              target_kind TEXT NOT NULL,
              target_id TEXT DEFAULT '',
              type TEXT DEFAULT 'general',
              title TEXT NOT NULL,
              message TEXT DEFAULT '',
              related_id TEXT DEFAULT '',
              priority TEXT DEFAULT 'normal',
              action_text TEXT DEFAULT '',
              action_route TEXT DEFAULT '',
              is_read INTEGER DEFAULT 0,
              created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE complaints(
              id TEXT PRIMARY KEY,
              provider_id TEXT,
              customer_name TEXT,
              phone TEXT,
              reason TEXT,
              detail TEXT,
              status TEXT,
              priority TEXT,
              resolution TEXT,
              request_id TEXT,
              user_id TEXT,
              created_at TEXT DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        install_workflow_schema(self.con)
        self.con.execute("INSERT INTO app_users(id) VALUES('user-1')")
        self.con.execute(
            """INSERT INTO providers(
            id,active,verified,status,request_enabled,services,areas,availability,gov,wilayah)
            VALUES('provider-1',1,1,'available',1,?,?,?,?,?)""",
            (
                json.dumps(
                    [{"catId": "homecare", "serviceId": "electrician", "active": True}]
                ),
                json.dumps(["Muscat", "Seeb"]),
                json.dumps(
                    {
                        "days": ["0"],
                        "start": "08:00",
                        "end": "18:00",
                        "dailyCapacity": 2,
                    }
                ),
                "Muscat",
                "Seeb",
            ),
        )

    def tearDown(self):
        self.con.close()

    @staticmethod
    def offer(offer_id="offer-1", price=12.5):
        return {
            "id": offer_id,
            "providerId": "provider-1",
            "price": price,
            "laborAmount": 10,
            "materialsAmount": price - 10,
            "duration": "2 hours",
            "scope": "Inspect and repair",
            "warrantyDays": 30,
            "validUntil": "2027-01-01T00:00:00+00:00",
        }

    def booking_request(self, request_id="request-1", *, evidence="optional"):
        offer = self.offer()
        self.con.execute(
            """INSERT INTO customer_requests(
            id,user_id,customer_name,phone,service_value,service_name,gov,wilayah,
            requested_at,status,offers,workflow_version,evidence_policy)
            VALUES(?,?,?,?,?,?,?,?,?,'viewed',?,'booking_v2',?)""",
            (
                request_id,
                "user-1",
                "Test user",
                "96890000000",
                "homecare|electrician",
                "Electrician",
                "Muscat",
                "Seeb",
                "2026-08-10T10:00:00+00:00",
                json.dumps([offer]),
                evidence,
            ),
        )
        return offer

    def accept(self, request_id="request-1", *, evidence="optional"):
        offer = self.booking_request(request_id, evidence=evidence)
        work_order, duplicate = RequestWorkOrderService(
            self.con, now=NOW
        ).accept_offer(
            request_id,
            "user-1",
            offer["id"],
            offers=[offer],
        )
        self.assertFalse(duplicate)
        return work_order, offer

    def instant_policy(self, *, start_verification="none"):
        return BookingPolicyService(self.con).save(
            "homecare|electrician",
            {
                "fulfillmentMode": "instant",
                "pricingMode": "fixed",
                "fixedPriceAmount": 18.5,
                "defaultDurationMinutes": 60,
                "evidencePolicy": "optional",
                "startVerificationMode": start_verification,
                "autoCloseEnabled": True,
                "completionWindowHours": 24,
            },
            "admin-1",
        )

    def instant_request(self, request_id, *, user_id="user-1", start_verification="none"):
        self.con.execute(
            """INSERT INTO customer_requests(
            id,user_id,customer_name,phone,service_value,service_name,gov,wilayah,
            location_text,status,offers,offers_open,workflow_version,fulfillment_mode,
            pricing_mode,default_duration_minutes,evidence_policy,start_verification_mode)
            VALUES(?,?,?,?,?,?,?,?,?,'matching','[]',1,'booking_v2','instant','fixed',
            60,'optional',?)""",
            (
                request_id,
                user_id,
                "Test user",
                "96890000000",
                "homecare|electrician",
                "Electrician",
                "Muscat",
                "Seeb",
                "Seeb",
                start_verification,
            ),
        )

    def test_policy_is_server_owned_and_validated(self):
        service = BookingPolicyService(self.con)
        defaults = service.get("education|math")
        self.assertEqual("quoted", defaults["fulfillmentMode"])
        self.assertEqual("none", defaults["evidencePolicy"])
        saved = service.save(
            "cleaning|home_clean",
            {
                "fulfillmentMode": "instant",
                "pricingMode": "fixed",
                "fixedPriceAmount": 18.5,
                "defaultDurationMinutes": 120,
                "evidencePolicy": "optional",
                "startVerificationMode": "none",
                "autoCloseEnabled": True,
                "completionWindowHours": 24,
            },
            "admin-1",
        )
        self.assertEqual("instant", saved["fulfillmentMode"])
        self.assertEqual(24, saved["completionWindowHours"])
        with self.assertRaises(DomainError):
            service.save(
                "cleaning|home_clean",
                {**saved, "evidencePolicy": "always_upload_everything"},
                "admin-1",
            )

    def test_offer_acceptance_is_one_work_order_and_keeps_snapshot(self):
        work_order, offer = self.accept()
        self.assertEqual(1, work_order["version"])
        self.assertEqual(12.5, work_order["priceAmount"])
        self.assertEqual(120, work_order["durationMinutes"])
        duplicate, replayed = RequestWorkOrderService(
            self.con, now=NOW
        ).accept_offer(
            "request-1", "user-1", offer["id"], offers=[offer]
        )
        self.assertTrue(replayed)
        self.assertEqual(work_order["id"], duplicate["id"])
        with self.assertRaises(DomainError) as caught:
            RequestWorkOrderService(self.con, now=NOW).accept_offer(
                "request-1",
                "user-1",
                "offer-other",
                offers=[{**offer, "id": "offer-other"}],
            )
        self.assertEqual("offer_selection_conflict", caught.exception.code)
        versions = RequestWorkOrderService(self.con).versions("request-1")
        self.assertEqual([1], [item["version"] for item in versions])
        self.assertEqual(12.5, versions[0]["snapshot"]["priceAmount"])
        analytics = self.con.execute(
            """SELECT detail FROM request_events
            WHERE request_id='request-1' AND event_type='offer_accepted'"""
        ).fetchone()
        self.assertEqual({"fulfillmentMode": "quoted"}, json.loads(analytics["detail"]))

    def test_change_order_requires_other_party_and_current_version(self):
        self.accept()
        service = RequestChangeOrderService(self.con, now=NOW)
        change = service.propose(
            "request-1",
            "provider",
            "provider-1",
            expected_version=1,
            changes={"priceAmount": 15, "scope": "Repair and replace part"},
            reason="Part replacement",
            idempotency_key="change:test:one",
        )
        replay = service.propose(
            "request-1",
            "provider",
            "provider-1",
            expected_version=1,
            changes={"priceAmount": 15, "scope": "Repair and replace part"},
            reason="Part replacement",
            idempotency_key="change:test:one",
        )
        self.assertEqual(change["id"], replay["id"])
        with self.assertRaises(DomainError) as sender:
            service.decide(
                change["id"],
                "provider",
                "provider-1",
                decision="accepted",
                expected_version=1,
                idempotency_key="decision:test:self",
            )
        self.assertEqual("change_order_sender_cannot_decide", sender.exception.code)
        decided, work_order = service.decide(
            change["id"],
            "user",
            "user-1",
            decision="accepted",
            expected_version=1,
            idempotency_key="decision:test:one",
        )
        self.assertEqual("accepted", decided["status"])
        self.assertEqual(2, work_order["version"])
        self.assertEqual(15, work_order["priceAmount"])
        with self.assertRaises(DomainError) as stale:
            service.propose(
                "request-1",
                "user",
                "user-1",
                expected_version=1,
                changes={"durationMinutes": 180},
                reason="More time",
                idempotency_key="change:test:stale",
            )
        self.assertEqual("work_order_version_changed", stale.exception.code)
        self.assertEqual(
            [1, 2],
            [
                item["version"]
                for item in RequestWorkOrderService(self.con).versions("request-1")
            ],
        )

    def test_server_view_never_allows_customer_to_start_booking_v2(self):
        self.accept()
        row = dict(
            self.con.execute(
                "SELECT * FROM customer_requests WHERE id='request-1'"
            ).fetchone()
        )
        user_view = request_workflow_view(
            self.con, row, actor_kind="user", actor_id="user-1"
        )
        provider_view = request_workflow_view(
            self.con, row, actor_kind="provider", actor_id="provider-1"
        )
        self.assertEqual("booked", user_view["visibleState"])
        self.assertNotIn("start_work", user_view["allowedActions"])
        self.assertEqual("start_work", provider_view["nextAction"]["type"])
        self.assertIn("start_work", provider_view["allowedActions"])

    def test_evidence_policy_and_completion_deadline_are_enforced(self):
        self.accept(evidence="none")
        self.con.execute(
            "UPDATE customer_requests SET status='inProgress' WHERE id='request-1'"
        )
        evidence = CompletionEvidenceService(self.con, now=NOW).submit(
            "request-1",
            "provider-1",
            before_images=[],
            after_images=[],
            checklist=[],
            note="Completed",
            idempotency_key="completion-submit-none-0001",
        )
        self.assertEqual([], evidence["afterImages"])
        row = self.con.execute(
            "SELECT status,completion_due_at FROM customer_requests WHERE id='request-1'"
        ).fetchone()
        self.assertEqual("awaitingConfirmation", row["status"])
        self.assertEqual("2026-08-10T12:00:00+00:00", row["completion_due_at"])

        self.booking_request("request-photo", evidence="required_photo")
        offer = self.offer()
        RequestWorkOrderService(self.con, now=NOW).accept_offer(
            "request-photo", "user-1", offer["id"], offers=[offer]
        )
        self.con.execute(
            "UPDATE customer_requests SET status='inProgress' WHERE id='request-photo'"
        )
        with self.assertRaises(DomainError) as required:
            CompletionEvidenceService(self.con, now=NOW).submit(
                "request-photo",
                "provider-1",
                before_images=[],
                after_images=[],
                checklist=[],
            )
        self.assertEqual("completion_after_image_required", required.exception.code)

    def test_notification_states_are_idempotent_and_action_is_separate(self):
        self.con.execute(
            """INSERT INTO app_notifications(
            id,target_kind,target_id,title,entity_kind,entity_id,action_kind,
            requires_action,dedupe_key,state_version)
            VALUES('notification-1','user','user-1','Review completion','request',
            'request-1','review_completion',1,
            'request:request-1:review_completion:v1:user:user-1',1)"""
        )
        service = NotificationActionService(self.con, now=NOW)
        service.update("notification-1", "user", "user-1", "seen")
        service.update("notification-1", "user", "user-1", "read")
        service.update("notification-1", "user", "user-1", "read")
        row = self.con.execute(
            "SELECT * FROM app_notifications WHERE id='notification-1'"
        ).fetchone()
        self.assertTrue(row["seen_at"])
        self.assertTrue(row["read_at"])
        self.assertEqual("", row["acted_at"])
        self.assertEqual(1, service.pending_count("user", "user-1"))
        self.assertEqual(
            1,
            service.resolve(
                entity_kind="request",
                entity_id="request-1",
                action_kind="review_completion",
                target_kind="user",
                target_id="user-1",
            ),
        )
        self.assertEqual(0, service.pending_count("user", "user-1"))

    def test_instant_booking_reserves_one_real_slot_and_replays_idempotently(self):
        self.instant_policy()
        booking = InstantBookingService(self.con, now=NOW)
        slot = booking.upsert_slot(
            "provider-1",
            "homecare|electrician",
            "2026-08-09T04:00:00+00:00",
        )
        self.assertEqual("2026-08-09T05:00:00+00:00", slot["endsAt"])
        self.instant_request("instant-request-1")
        work_order, reserved_slot, duplicate = booking.book(
            "instant-request-1",
            "user-1",
            slot["id"],
            idempotency_key="instant:test:booking:one",
        )
        self.assertFalse(duplicate)
        self.assertEqual("instant", work_order["fulfillmentMode"])
        self.assertEqual(18.5, work_order["priceAmount"])
        self.assertEqual(slot["id"], reserved_slot["id"])
        analytics = self.con.execute(
            """SELECT detail FROM request_events
            WHERE request_id='instant-request-1' AND event_type='booking_confirmed'"""
        ).fetchone()
        self.assertEqual({"fulfillmentMode": "instant"}, json.loads(analytics["detail"]))
        replayed_order, _, replayed = booking.book(
            "instant-request-1",
            "user-1",
            slot["id"],
            idempotency_key="instant:test:booking:one",
        )
        self.assertTrue(replayed)
        self.assertEqual(work_order["id"], replayed_order["id"])
        self.assertEqual(
            1,
            self.con.execute(
                "SELECT COUNT(*) n FROM request_slot_reservations WHERE slot_id=?",
                (slot["id"],),
            ).fetchone()["n"],
        )

        self.instant_request("instant-request-2")
        with self.assertRaises(DomainError) as conflict:
            booking.book(
                "instant-request-2",
                "user-1",
                slot["id"],
                idempotency_key="instant:test:booking:two",
            )
        self.assertEqual("instant_slot_reserved", conflict.exception.code)

    def test_instant_slots_use_oman_sunday_and_reject_overlap_or_invalid_provider(self):
        self.instant_policy()
        booking = InstantBookingService(self.con, now=NOW)
        booking.upsert_slot(
            "provider-1",
            "homecare|electrician",
            "2026-08-09T04:00:00+00:00",
        )
        with self.assertRaises(DomainError) as overlap:
            booking.upsert_slot(
                "provider-1",
                "homecare|electrician",
                "2026-08-09T04:30:00+00:00",
            )
        self.assertEqual("instant_slot_overlap", overlap.exception.code)
        self.con.execute("UPDATE providers SET verified=0 WHERE id='provider-1'")
        with self.assertRaises(DomainError) as inactive:
            booking.upsert_slot(
                "provider-1",
                "homecare|electrician",
                "2026-08-09T06:00:00+00:00",
            )
        self.assertEqual("provider_no_longer_available", inactive.exception.code)

    def test_completed_instant_reservation_permanently_consumes_slot(self):
        self.instant_policy()
        booking = InstantBookingService(self.con, now=NOW)
        slot = booking.upsert_slot(
            "provider-1",
            "homecare|electrician",
            "2026-08-09T04:00:00+00:00",
        )
        self.instant_request("instant-completed-1")
        booking.book(
            "instant-completed-1",
            "user-1",
            slot["id"],
            idempotency_key="instant:completed:first",
        )
        booking.complete_request("instant-completed-1")
        self.assertEqual(
            [],
            booking.available_slots(
                "homecare|electrician",
                starts_after="2026-08-08T12:00:00+00:00",
            ),
        )
        inventory = booking.provider_slots("provider-1")
        self.assertEqual("completed", inventory[0]["reservationStatus"])
        self.assertTrue(inventory[0]["reserved"])
        self.assertFalse(inventory[0]["available"])
        self.instant_request("instant-completed-2")
        with self.assertRaises(DomainError) as conflict:
            booking.book(
                "instant-completed-2",
                "user-1",
                slot["id"],
                idempotency_key="instant:completed:second",
            )
        self.assertEqual("instant_slot_reserved", conflict.exception.code)

    def test_instant_schedule_change_requires_cancel_and_rebooking(self):
        self.instant_policy()
        booking = InstantBookingService(self.con, now=NOW)
        slot = booking.upsert_slot(
            "provider-1",
            "homecare|electrician",
            "2026-08-09T04:00:00+00:00",
        )
        self.instant_request("instant-change")
        booking.book(
            "instant-change",
            "user-1",
            slot["id"],
            idempotency_key="instant:change:booking",
        )
        with self.assertRaises(DomainError) as rejected:
            RequestChangeOrderService(self.con, now=NOW).propose(
                "instant-change",
                "provider",
                "provider-1",
                expected_version=1,
                changes={"appointmentAt": "2026-08-09T06:00:00+00:00"},
                reason="Move appointment",
                idempotency_key="instant:change:schedule",
            )
        self.assertEqual(
            "instant_schedule_change_requires_rebooking", rejected.exception.code
        )
        reservation = self.con.execute(
            """SELECT starts_at,status FROM request_slot_reservations
            WHERE request_id='instant-change'"""
        ).fetchone()
        self.assertEqual(slot["startsAt"], reservation["starts_at"])
        self.assertEqual("active", reservation["status"])

    def test_completion_submit_and_decide_are_single_use_and_idempotent(self):
        self.accept(evidence="none")
        self.con.execute(
            "UPDATE customer_requests SET status='inProgress' WHERE id='request-1'"
        )
        service = CompletionEvidenceService(self.con, now=NOW)
        first = service.submit(
            "request-1",
            "provider-1",
            before_images=[],
            after_images=[],
            checklist=[],
            note="Completed as agreed",
            idempotency_key="completion:submit:single",
        )
        replay = service.submit(
            "request-1",
            "provider-1",
            before_images=[],
            after_images=[],
            checklist=[],
            note="Completed as agreed",
            idempotency_key="completion:submit:single",
        )
        self.assertTrue(replay.pop("_duplicate"))
        self.assertEqual(first["submittedAt"], replay["submittedAt"])
        due_at = self.con.execute(
            "SELECT completion_due_at FROM customer_requests WHERE id='request-1'"
        ).fetchone()["completion_due_at"]
        with self.assertRaises(DomainError) as second_submission:
            service.submit(
                "request-1",
                "provider-1",
                before_images=[],
                after_images=[],
                checklist=[],
                note="Different submission",
                idempotency_key="completion:submit:different",
            )
        self.assertEqual("completion_already_submitted", second_submission.exception.code)
        self.assertEqual(
            due_at,
            self.con.execute(
                "SELECT completion_due_at FROM customer_requests WHERE id='request-1'"
            ).fetchone()["completion_due_at"],
        )
        service.decide(
            "request-1",
            "user-1",
            "resolved",
            idempotency_key="completion:decision:single",
        )
        decision_replay = service.decide(
            "request-1",
            "user-1",
            "resolved",
            idempotency_key="completion:decision:single",
        )
        self.assertTrue(decision_replay.pop("_duplicate"))
        self.assertEqual(
            1,
            self.con.execute(
                "SELECT completed_jobs FROM providers WHERE id='provider-1'"
            ).fetchone()["completed_jobs"],
        )

    def test_start_otp_is_hashed_authorized_expiring_and_single_use(self):
        self.booking_request("otp-request")
        self.con.execute(
            """UPDATE customer_requests SET start_verification_mode='otp'
            WHERE id='otp-request'"""
        )
        offer = self.offer()
        RequestWorkOrderService(self.con, now=NOW).accept_offer(
            "otp-request", "user-1", offer["id"], offers=[offer]
        )
        verification = StartVerificationService(self.con, now=NOW)
        issued = verification.issue("otp-request", "user-1")
        self.assertRegex(issued["code"], r"^\d{6}$")
        stored = self.con.execute(
            "SELECT * FROM request_start_verifications WHERE request_id='otp-request'"
        ).fetchone()
        self.assertNotEqual(issued["code"], stored["code_hash"])
        self.assertNotIn(
            issued["code"],
            " ".join(
                str(row[0])
                for row in self.con.execute(
                    "SELECT detail FROM request_events WHERE request_id='otp-request'"
                )
            ),
        )
        with self.assertRaises(DomainError) as wrong_provider:
            verification.consume("otp-request", "provider-other", issued["code"])
        self.assertEqual("start_verification_access_denied", wrong_provider.exception.code)
        with self.assertRaises(DomainError) as wrong_code:
            verification.consume("otp-request", "provider-1", "000000")
        self.assertEqual("invalid_start_verification_code", wrong_code.exception.code)
        self.assertEqual(
            1,
            self.con.execute(
                "SELECT attempts FROM request_start_verifications WHERE request_id='otp-request'"
            ).fetchone()["attempts"],
        )
        verification.consume("otp-request", "provider-1", issued["code"])
        with self.assertRaises(DomainError) as replay:
            verification.consume("otp-request", "provider-1", issued["code"])
        self.assertEqual("start_verification_used", replay.exception.code)

        self.booking_request("otp-expired")
        self.con.execute(
            "UPDATE customer_requests SET start_verification_mode='otp' WHERE id='otp-expired'"
        )
        RequestWorkOrderService(self.con, now=NOW).accept_offer(
            "otp-expired", "user-1", offer["id"], offers=[offer]
        )
        expired = verification.issue("otp-expired", "user-1")
        self.con.execute(
            """UPDATE request_start_verifications SET expires_at=?
            WHERE request_id='otp-expired'""",
            ((NOW - timedelta(seconds=1)).isoformat(),),
        )
        with self.assertRaises(DomainError) as expiry:
            verification.consume("otp-expired", "provider-1", expired["code"])
        self.assertEqual("start_verification_expired", expiry.exception.code)

    def test_notification_deep_link_supersedes_stale_state_version(self):
        self.accept()
        self.con.execute(
            """INSERT INTO app_notifications(
            id,target_kind,target_id,title,entity_kind,entity_id,action_kind,
            requires_action,dedupe_key,state_version)
            VALUES('notification-stale','provider','provider-1','Open booking','request',
            'request-1','open_booking',1,
            'request:request-1:open_booking:v99:provider:provider-1',99)"""
        )
        row = self.con.execute(
            "SELECT * FROM app_notifications WHERE id='notification-stale'"
        ).fetchone()
        state = notification_request_state(
            self.con,
            row,
            actor_kind="provider",
            actor_id="provider-1",
            now=NOW,
        )
        self.assertTrue(state["stale"])
        self.assertEqual("state_version_changed", state["staleReason"])
        self.assertEqual(1, state["currentRequest"]["stateVersion"])
        refreshed = self.con.execute(
            "SELECT superseded_at,dismissed_at FROM app_notifications WHERE id='notification-stale'"
        ).fetchone()
        self.assertTrue(refreshed["superseded_at"])
        self.assertEqual("", refreshed["dismissed_at"])

    def test_snoozed_action_stays_pending_but_is_not_prompted_or_dismissible(self):
        self.con.execute(
            """INSERT INTO app_notifications(
            id,target_kind,target_id,title,entity_kind,entity_id,action_kind,
            requires_action,dedupe_key,state_version)
            VALUES('notification-pending','user','user-1','Action','request',
            'request-1','review_completion',1,
            'request:request-1:review_completion:v1:user:user-1',1)"""
        )
        service = NotificationActionService(self.con, now=NOW)
        self.assertEqual(1, service.pending_count("user", "user-1"))
        service.update(
            "notification-pending",
            "user",
            "user-1",
            "snooze",
            snooze_minutes=30,
        )
        self.assertEqual(1, service.pending_count("user", "user-1"))
        self.assertEqual([], service.prompt_due("user", "user-1"))
        with self.assertRaises(DomainError) as dismiss:
            service.update("notification-pending", "user", "user-1", "dismiss")
        self.assertEqual("required_action_cannot_be_dismissed", dismiss.exception.code)
        service.resolve(
            entity_kind="request",
            entity_id="request-1",
            action_kind="review_completion",
            target_kind="user",
            target_id="user-1",
        )
        service.update("notification-pending", "user", "user-1", "dismiss")
        self.assertEqual(0, service.pending_count("user", "user-1"))

    def test_provider_revalidated_at_offer_acceptance(self):
        offer = self.booking_request("provider-revalidation")
        self.con.execute("UPDATE providers SET verified=0 WHERE id='provider-1'")
        with self.assertRaises(DomainError) as unavailable:
            RequestWorkOrderService(self.con, now=NOW).accept_offer(
                "provider-revalidation", "user-1", offer["id"], offers=[offer]
            )
        self.assertEqual("provider_no_longer_available", unavailable.exception.code)

    def test_repeated_start_transition_is_idempotent(self):
        self.accept()
        lifecycle = RequestLifecycleService(self.con, now=NOW)
        lifecycle.transition(
            "request-1",
            "inProgress",
            actor_kind="provider",
            actor_id="provider-1",
            event_type="work_started",
            allowed_from={"accepted", "appointmentConfirmed"},
        )
        lifecycle.transition(
            "request-1",
            "inProgress",
            actor_kind="provider",
            actor_id="provider-1",
            event_type="work_started",
            allowed_from={"accepted", "appointmentConfirmed"},
        )
        self.assertEqual(
            1,
            self.con.execute(
                """SELECT COUNT(*) n FROM request_events
                WHERE request_id='request-1' AND event_type='work_started'"""
            ).fetchone()["n"],
        )

    def test_auto_close_only_runs_for_explicitly_eligible_request(self):
        self.accept(evidence="none")
        self.con.execute(
            """UPDATE customer_requests SET status='inProgress',auto_close_enabled=1,
            completion_window_hours=1 WHERE id='request-1'"""
        )
        CompletionEvidenceService(self.con, now=NOW).submit(
            "request-1",
            "provider-1",
            before_images=[],
            after_images=[],
            checklist=[],
            note="Finished safely",
            idempotency_key="completion-auto-close-0001",
        )
        closed = CompletionEvidenceService(
            self.con, now=NOW + timedelta(hours=2)
        ).auto_close_due()
        self.assertEqual(["request-1"], [item["requestId"] for item in closed])
        row = self.con.execute(
            "SELECT status FROM customer_requests WHERE id='request-1'"
        ).fetchone()
        self.assertEqual("closed", row["status"])


if __name__ == "__main__":
    unittest.main()
