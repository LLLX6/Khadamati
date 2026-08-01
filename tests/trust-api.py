"""Focused HTTP integration checks for verification, complaints, and blocking."""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ADMIN_CODE = "Trust-Admin-6732"
TEST_PNG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9W"
    "lqAAAAAASUVORK5CYII="
)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def http(base: str, path: str, payload=None, token: str = ""):
    trace = os.environ.get("KHADAMATI_TEST_TRACE") == "1"
    if trace:
        print(f"HTTP {('POST' if payload is not None else 'GET'):4} {path}", flush=True)
    body = (
        json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if payload is not None
        else None
    )
    headers = {
        "Content-Type": "application/json",
        "Origin": "http://127.0.0.1:8080",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"{base}{path}",
        data=body,
        headers=headers,
        method="POST" if payload is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = response.status, json.loads(response.read().decode("utf-8"))
            if trace:
                print(f" -> {result[0]}", flush=True)
            return result
    except urllib.error.HTTPError as error:
        result = error.code, json.loads(error.read().decode("utf-8") or "{}")
        if trace:
            print(f" -> {result[0]} {result[1].get('error', '')}", flush=True)
        return result


def expect(result, statuses, label):
    status, data = result
    assert status in statuses, f"{label}: HTTP {status} {data}"
    return data


def wait_until_ready(base: str) -> None:
    for _ in range(100):
        try:
            if http(base, "/readyz")[0] == 200:
                return
        except urllib.error.URLError:
            pass
        time.sleep(0.1)
    raise AssertionError("isolated trust server did not start")


def register_provider(base: str, admin_token: str):
    phone, pin = "96895550731", "7319"
    registration = expect(
        http(
            base,
            "/api/provider-requests",
            {
                "name": "مزود اختبار الثقة",
                "phone": phone,
                "pin": pin,
                "providerType": "individual",
                "age": 31,
                "nationality": "عُماني",
                "commercialNo": "TRUST-LIC-731",
                "licenseExpiry": "2028-12-31",
                "businessRole": "كهربائي منازل",
                "gov": "مسقط",
                "wilayah": "السيب",
                "location": {"lat": 23.621234, "lng": 58.221234},
                "service": "homecare|electrician",
                "services": [
                    {
                        "catId": "homecare",
                        "serviceId": "electrician",
                        "priceFrom": 8,
                        "areas": ["السيب"],
                    }
                ],
                "priceFrom": 8,
                "note": "خدمة كهرباء منزلية لاختبار الثقة",
                "hours": "الأحد: 8:00 ص - 8:00 م",
                "documentsData": [TEST_PNG, TEST_PNG],
            },
        ),
        {201},
        "provider registration",
    )
    expect(
        http(
            base,
            "/api/admin/request-decision",
            {"id": registration["request"]["id"], "decision": "accept"},
            admin_token,
        ),
        {200},
        "provider approval",
    )
    state = expect(
        http(base, "/api/admin/session", token=admin_token),
        {200},
        "admin provider state",
    )
    provider = next(item for item in state["providers"] if item.get("phone") == phone)
    login = expect(
        http(base, "/api/provider/login", {"phone": phone, "pin": pin}),
        {200},
        "provider login",
    )
    return provider, login["token"]


def register_user(base: str, suffix: str):
    result = expect(
        http(
            base,
            "/api/users/register",
            {
                "phone": f"96895550{suffix}",
                "name": f"مستخدم ثقة {suffix}",
                "pin": "2468",
                "age": 30,
                "nationality": "عُماني",
                "gov": "مسقط",
                "wilayah": "السيب",
            },
        ),
        {200},
        f"user {suffix} registration",
    )
    return result["user"], result["token"]


def run() -> None:
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="khadamati-trust-api-") as temp:
        db_path = Path(temp) / "trust.sqlite3"
        env = os.environ.copy()
        env.update(
            {
                "HOST": "127.0.0.1",
                "PORT": str(port),
                "KHADAMATI_ENV": "test",
                "KHADAMATI_ADMIN_CODE": ADMIN_CODE,
                "KHADAMATI_DB_PATH": str(db_path),
                "KHADAMATI_UPLOAD_DIR": str(Path(temp) / "uploads"),
                "KHADAMATI_BACKUP_DIR": str(Path(temp) / "backups"),
                "KHADAMATI_MEDIA_SIGNING_KEY": "trust-media-signing-key-6732",
            }
        )
        process = subprocess.Popen(
            [sys.executable, "server.py"],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            wait_until_ready(base)
            admin = expect(
                http(base, "/api/admin/login", {"code": ADMIN_CODE}),
                {200},
                "admin login",
            )
            admin_token = admin["token"]
            provider, provider_token = register_provider(base, admin_token)
            provider_id = provider["id"]

            submitted = expect(
                http(
                    base,
                    "/api/trust/verification",
                    {
                        "action": "submit",
                        "requirements": ["identity", "activity_licence"],
                    },
                    provider_token,
                ),
                {200},
                "verification submission",
            )["verification"]
            assert submitted["status"] == "submitted"

            admin_state = expect(
                http(base, "/api/admin/session", token=admin_token),
                {200},
                "verification queue",
            )
            assert any(
                item.get("providerId") == provider_id
                and item.get("status") == "submitted"
                for item in admin_state.get("adminEntities", {}).get(
                    "verificationCases", []
                )
            ), "submitted verification did not reach management"

            user_a, user_token = register_user(base, "741")
            _, other_user_token = register_user(base, "742")
            denied = http(
                base,
                "/api/admin/verification",
                {"providerId": provider_id, "action": "get"},
                user_token,
            )
            assert denied[0] in {401, 403}, "user reached admin verification API"

            reviewed = expect(
                http(
                    base,
                    "/api/admin/verification",
                    {
                        "providerId": provider_id,
                        "action": "review",
                        "status": "verified",
                        "identityStatus": "verified",
                        "entityStatus": "not_applicable",
                        "activityStatus": "verified",
                        "decisionNote": "تمت مراجعة الهوية والنشاط كلٌ على حدة.",
                    },
                    admin_token,
                ),
                {200},
                "verification review",
            )["verification"]
            assert reviewed["status"] == "verified"
            assert reviewed["identityStatus"] == reviewed["activityStatus"] == "verified"
            assert reviewed["entityStatus"] == "not_applicable"

            created = expect(
                http(
                    base,
                    "/api/user/requests",
                    {
                        "serviceValue": "homecare|electrician",
                        "serviceName": "كهربائي",
                        "customerName": user_a["name"],
                        "gov": "مسقط",
                        "wilayah": "السيب",
                        "location": {"lat": 23.621, "lng": 58.221},
                        "urgency": "normal",
                        "scheduleType": "flexible",
                        "note": "طلب مرتبط باختبار ملف الشكوى والحظر",
                        "idempotencyKey": "trust-api-request-1",
                    },
                    user_token,
                ),
                {201},
                "request creation",
            )
            request_id = created["request"]["id"]
            provider_before_selection = expect(
                http(base, "/api/bootstrap", token=provider_token),
                {200},
                "provider request before selection",
            )
            private_request = next(
                item
                for item in provider_before_selection["customerRequests"]
                if item["id"] == request_id
            )
            assert private_request.get("locationPrecision") == "area"
            assert private_request.get("location") is None
            assert not private_request.get("locationText"), (
                "exact request location leaked before quote selection"
            )
            offered = expect(
                http(
                    base,
                    "/api/request/collaboration",
                    {
                        "id": request_id,
                        "action": "offer",
                        "price": 999,
                        "laborAmount": 9,
                        "materialsAmount": 3,
                        "duration": "ساعتان",
                        "scope": "يشمل الفحص والعمل والاختبار",
                        "warrantyDays": 30,
                        "validityDays": 7,
                        "note": "يشمل الفحص",
                    },
                    provider_token,
                ),
                {200},
                "provider offer",
            )
            quote = offered["request"]["offers"][0]
            offer_id = quote["id"]
            assert quote["price"] == 12
            assert quote["laborAmount"] == 9
            assert quote["materialsAmount"] == 3
            assert quote["warrantyDays"] == 30
            assert quote["scope"] == "يشمل الفحص والعمل والاختبار"

            connection = sqlite3.connect(db_path)
            try:
                row = connection.execute(
                    "SELECT offers FROM customer_requests WHERE id=?", (request_id,)
                ).fetchone()
                expired_offers = json.loads(row[0])
                expired_offers[0]["validUntil"] = "2000-01-01T00:00:00+00:00"
                connection.execute(
                    "UPDATE customer_requests SET offers=? WHERE id=?",
                    (
                        json.dumps(expired_offers, ensure_ascii=False),
                        request_id,
                    ),
                )
                connection.commit()
            finally:
                connection.close()
            expired_selection = http(
                base,
                "/api/request/collaboration",
                {
                    "id": request_id,
                    "action": "choose_offer",
                    "offerId": offer_id,
                    "language": "ar",
                },
                user_token,
            )
            assert expired_selection[0] == 409
            assert expired_selection[1].get("error") == "offer_expired"

            refreshed_quote = expect(
                http(
                    base,
                    "/api/request/collaboration",
                    {
                        "id": request_id,
                        "action": "offer",
                        "laborAmount": 9,
                        "materialsAmount": 3,
                        "duration": "ساعتان",
                        "scope": "يشمل الفحص والعمل والاختبار",
                        "warrantyDays": 30,
                        "validityDays": 7,
                        "note": "يشمل الفحص",
                    },
                    provider_token,
                ),
                {200},
                "quote refresh after expiry",
            )["request"]["offers"][0]
            assert refreshed_quote["id"] == offer_id
            expect(
                http(
                    base,
                    "/api/request/collaboration",
                    {
                        "id": request_id,
                        "action": "choose_offer",
                        "offerId": offer_id,
                        "language": "ar",
                    },
                    user_token,
                ),
                {200},
                "offer selection",
            )
            provider_after_selection = expect(
                http(base, "/api/bootstrap", token=provider_token),
                {200},
                "provider request after selection",
            )
            selected_request = next(
                item
                for item in provider_after_selection["customerRequests"]
                if item["id"] == request_id
            )
            assert selected_request.get("locationPrecision") == "exact"
            assert selected_request.get("location"), (
                "accepted provider did not receive exact request location"
            )

            connection = sqlite3.connect(db_path)
            try:
                connection.execute(
                    "UPDATE customer_requests SET status='closed' WHERE id=?",
                    (request_id,),
                )
                connection.commit()
            finally:
                connection.close()
            review = expect(
                http(
                    base,
                    "/api/reviews",
                    {
                        "providerId": provider_id,
                        "requestId": request_id,
                        "rating": 5,
                        "dimensions": {
                            "quality": 5,
                            "punctuality": 4,
                            "communication": 5,
                            "value": 4,
                        },
                        "comment": "تقييم موثق متعدد الأبعاد",
                    },
                    user_token,
                ),
                {201},
                "multidimensional review",
            )["review"]
            assert review["dimensions"]["quality"] == 5
            assert review["dimensions"]["punctuality"] == 4
            quality_state = expect(
                http(base, "/api/bootstrap", token=user_token),
                {200},
                "quality breakdown bootstrap",
            )
            quality_provider = next(
                item for item in quality_state["providers"] if item["id"] == provider_id
            )
            assert quality_provider["qualityBreakdown"]["reviewCount"] == 1
            assert quality_provider["qualityBreakdown"]["quality"] == 5

            complaint = expect(
                http(
                    base,
                    "/api/complaints",
                    {
                        "providerId": provider_id,
                        "requestId": request_id,
                        "reason": "quality",
                        "detail": "وصف واضح لمشكلة مرتبطة بالطلب.",
                        "priority": "high",
                        "evidenceData": [TEST_PNG],
                    },
                    user_token,
                ),
                {201},
                "complaint creation",
            )["complaint"]
            complaint_id = complaint["id"]
            assert complaint.get("timeline") and complaint.get("evidence")

            forbidden_case = http(
                base,
                "/api/trust/complaint",
                {"id": complaint_id, "action": "get"},
                other_user_token,
            )
            assert forbidden_case[0] == 403, "complaint leaked to another user"
            provider_case = expect(
                http(
                    base,
                    "/api/trust/complaint",
                    {"id": complaint_id, "action": "get"},
                    provider_token,
                ),
                {200},
                "provider complaint view",
            )["complaint"]
            assert provider_case["id"] == complaint_id
            updated_case = expect(
                http(
                    base,
                    "/api/admin/complaint-case",
                    {
                        "id": complaint_id,
                        "action": "update",
                        "status": "triaged",
                        "priority": "high",
                        "outcome": "تحتاج متابعة",
                        "visibleToSubject": True,
                    },
                    admin_token,
                ),
                {200},
                "admin complaint triage",
            )["complaint"]
            assert updated_case["status"] == "triaged"

            blocked = expect(
                http(
                    base,
                    "/api/trust/block",
                    {
                        "action": "block",
                        "targetId": provider_id,
                        "requestId": request_id,
                        "reason": "chat_safety",
                    },
                    user_token,
                ),
                {200},
                "interaction block",
            )
            assert blocked["interactionBlocks"][0]["blockedId"] == provider_id
            blocked_message = http(
                base,
                "/api/request/collaboration",
                {"id": request_id, "action": "message", "text": "رسالة يجب رفضها"},
                provider_token,
            )
            assert blocked_message[0] == 403
            assert blocked_message[1].get("error") == "interaction_blocked"

            expect(
                http(
                    base,
                    "/api/trust/block",
                    {
                        "action": "unblock",
                        "targetId": provider_id,
                        "requestId": request_id,
                    },
                    user_token,
                ),
                {200},
                "interaction unblock",
            )
            state_after_unblock = expect(
                http(base, "/api/bootstrap", token=user_token),
                {200},
                "user state after unblock",
            )
            request_after_unblock = next(
                item
                for item in state_after_unblock["customerRequests"]
                if item["id"] == request_id
            )
            assert request_after_unblock["contactConsent"]["chat"] is False, (
                "unblocking silently restored contact consent"
            )
            expect(
                http(
                    base,
                    "/api/request/collaboration",
                    {
                        "id": request_id,
                        "action": "contact_consent",
                        "chat": True,
                        "whatsapp": False,
                        "call": False,
                    },
                    user_token,
                ),
                {200},
                "contact re-consent",
            )
            expect(
                http(
                    base,
                    "/api/request/collaboration",
                    {"id": request_id, "action": "message", "text": "رسالة بعد الموافقة"},
                    provider_token,
                ),
                {200},
                "message after re-consent",
            )
            print("Trust API integration: PASS")
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    run()
