"""HTTP integration checks for the staged marketplace platform layer."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ADMIN_CODE = "Platform-Admin-4826"
TEST_PNG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9W"
    "lqAAAAAASUVORK5CYII="
)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def http(base: str, path: str, payload=None, token: str = "", headers=None):
    body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    request_headers = {"Content-Type": "application/json", "Origin": "http://127.0.0.1:8080"}
    if token:
        request_headers["Authorization"] = f"Bearer {token}"
    request_headers.update(headers or {})
    request = urllib.request.Request(
        f"{base}{path}", data=body, headers=request_headers,
        method="POST" if payload is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode() or "{}")


def expect(result, statuses, label):
    status, data = result
    assert status in statuses, f"{label}: HTTP {status} {data}"
    return data


def ready(base: str) -> None:
    for _ in range(100):
        try:
            if http(base, "/readyz")[0] == 200:
                return
        except urllib.error.URLError:
            pass
        time.sleep(0.1)
    raise AssertionError("isolated platform server did not start")


def register_user(base: str, phone: str, name: str):
    result = expect(
        http(base, "/api/users/register", {
            "phone": phone, "name": name, "pin": "2468", "age": 30,
            "nationality": "عُماني", "gov": "مسقط", "wilayah": "السيب",
        }), {200}, f"register {name}",
    )
    return result["user"], result["token"]


def register_provider(base: str, admin_token: str):
    phone, pin = "96895550881", "8819"
    registration = expect(
        http(base, "/api/provider-requests", {
            "name": "مزود منصة الاختبار", "phone": phone, "pin": pin,
            "providerType": "individual", "legalPath": "individual_omani",
            "age": 34, "nationality": "عُماني", "businessRole": "فني كهرباء",
            "gov": "مسقط", "wilayah": "السيب",
            "service": "homecare|electrician",
            "services": [{"catId": "homecare", "serviceId": "electrician", "priceFrom": 8}],
            "note": "خدمة كهرباء منزلية موثوقة وآمنة",
            "hours": "الأحد: 8:00 ص - 8:00 م",
            "documentsData": [TEST_PNG, TEST_PNG],
        }), {201}, "provider registration without false licence requirement",
    )
    expect(
        http(base, "/api/admin/request-decision", {
            "id": registration["request"]["id"], "decision": "accept",
        }, admin_token), {200}, "provider approval",
    )
    admin_state = expect(http(base, "/api/admin/session", token=admin_token), {200}, "admin state")
    provider = next(item for item in admin_state["providers"] if item.get("phone") == phone)
    login = expect(http(base, "/api/provider/login", {"phone": phone, "pin": pin}), {200}, "provider login")
    return provider, login["token"]


def create_selected_request(base: str, user_token: str, provider_token: str):
    created = expect(
        http(base, "/api/user/requests", {
            "serviceValue": "homecare|electrician", "serviceName": "كهربائي",
            "customerName": "عميل المنصة", "gov": "مسقط", "wilayah": "السيب",
            "location": {"lat": 23.621, "lng": 58.221}, "urgency": "normal",
            "scheduleType": "flexible", "note": "طلب لاختبار العقود والمحادثة",
            "idempotencyKey": "platform-request-1",
        }, user_token), {201}, "request creation",
    )["request"]
    offered = expect(
        http(base, "/api/request/collaboration", {
            "id": created["id"], "action": "offer", "laborAmount": 10,
            "materialsAmount": 2, "duration": "ساعتان", "scope": "فحص وإصلاح واختبار",
            "warrantyDays": 30, "validityDays": 5, "note": "عرض منصة الاختبار",
        }, provider_token), {200}, "structured offer",
    )["request"]
    offer_id = offered["offers"][0]["id"]
    expect(
        http(base, "/api/request/collaboration", {
            "id": created["id"], "action": "choose_offer", "offerId": offer_id,
        }, user_token), {200}, "offer selection",
    )
    return created["id"]


def run() -> None:
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="khadamati-platform-api-") as temp:
        env = os.environ.copy()
        env.update({
            "HOST": "127.0.0.1", "PORT": str(port), "KHADAMATI_ENV": "test",
            "KHADAMATI_ADMIN_CODE": ADMIN_CODE,
            "KHADAMATI_DB_PATH": str(Path(temp) / "platform.sqlite3"),
            "KHADAMATI_UPLOAD_DIR": str(Path(temp) / "uploads"),
            "KHADAMATI_BACKUP_DIR": str(Path(temp) / "backups"),
            "KHADAMATI_MEDIA_SIGNING_KEY": "platform-media-key-4826",
        })
        process = subprocess.Popen(
            [sys.executable, "server.py"], cwd=ROOT, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            ready(base)
            admin_token = expect(
                http(base, "/api/admin/login", {"code": ADMIN_CODE}), {200}, "admin login"
            )["token"]
            provider, provider_token = register_provider(base, admin_token)
            user_a, user_token = register_user(base, "96895550882", "عميل مؤسسة")
            _, other_token = register_user(base, "96895550883", "عميل آخر")

            legal = expect(
                http(base, "/api/platform", {
                    "action": "legal:save", "pathway": "individual_omani",
                    "nationality": "عُماني",
                }, provider_token), {200}, "provider legal pathway",
            )["result"]
            assert legal["reviewStatus"] == "pending"
            reviewed = expect(
                http(base, "/api/admin/platform", {
                    "action": "legal:review", "providerId": provider["id"],
                    "status": "approved", "note": "مراجعة اختبارية معزولة",
                }, admin_token), {200}, "legal pathway review",
            )["result"]
            assert reviewed["reviewStatus"] == "approved"

            organization = expect(
                http(base, "/api/platform", {
                    "action": "organization:save", "name": "مؤسسة العميل للاختبار",
                    "organizationType": "business", "approvalMode": "two_step",
                }, user_token), {200}, "organization creation",
            )["result"]
            organization_id = organization["id"]
            organization = expect(
                http(base, "/api/platform", {
                    "action": "organization:add_location", "organizationId": organization_id,
                    "name": "الفرع الرئيسي", "gov": "مسقط", "wilayah": "السيب",
                }, user_token), {200}, "organization location",
            )["result"]
            assert organization["locations"][0]["name"] == "الفرع الرئيسي"
            denied = http(base, "/api/platform", {
                "action": "organization:add_location", "organizationId": organization_id,
                "name": "محاولة عابرة", "gov": "مسقط", "wilayah": "بوشر",
            }, other_token)
            assert denied[0] == 403, f"organization ownership leaked: {denied}"

            request_id = create_selected_request(base, user_token, provider_token)
            conversation = expect(
                http(base, "/api/platform", {
                    "action": "conversation:mute", "requestId": request_id, "muted": True,
                }, user_token), {200}, "conversation mute",
            )["result"]
            assert conversation["muted"] is True
            ended = expect(
                http(base, "/api/platform", {
                    "action": "conversation:end", "requestId": request_id,
                    "reason": "إنهاء اختباري",
                }, user_token), {200}, "conversation end",
            )["result"]
            assert ended["status"] == "ended" and ended["canReopen"] is True
            rejected_message = http(base, "/api/request/collaboration", {
                "id": request_id, "action": "message", "text": "يجب رفضها بعد الإنهاء",
            }, provider_token)
            assert rejected_message[0] == 409 and rejected_message[1].get("error") == "conversation_ended"
            provider_reopen = http(base, "/api/platform", {
                "action": "conversation:reopen", "requestId": request_id,
            }, provider_token)
            assert provider_reopen[0] == 403, f"non-owner reopened conversation: {provider_reopen}"
            reopened = expect(
                http(base, "/api/platform", {
                    "action": "conversation:reopen", "requestId": request_id,
                }, user_token), {200}, "conversation owner reopen",
            )["result"]
            assert reopened["status"] == "open"

            contract = expect(
                http(base, "/api/platform", {
                    "action": "contract:create", "providerId": provider["id"],
                    "requestId": request_id, "serviceValue": "homecare|electrician",
                    "title": "صيانة كهربائية دورية", "frequencyDays": 30, "amount": 12,
                    "autoRenew": False,
                }, user_token), {200}, "maintenance contract",
            )["result"]
            assert contract["providerId"] == provider["id"]
            provider_platform = expect(
                http(base, "/api/bootstrap", token=provider_token), {200}, "provider platform snapshot"
            )["platform"]
            assert any(item["id"] == contract["id"] for item in provider_platform["maintenanceContracts"])
            assert any(item["requestId"] == request_id for item in provider_platform["crm"])

            referral = expect(
                http(base, "/api/platform", {"action": "referral:create"}, user_token),
                {200}, "referral code",
            )["result"]
            claim = expect(
                http(base, "/api/platform", {"action": "referral:claim", "code": referral["code"]}, other_token),
                {200}, "referral claim",
            )["result"]
            assert claim["riskStatus"] == "pending_review"
            duplicate_claim = http(
                base, "/api/platform", {"action": "referral:claim", "code": referral["code"]}, other_token
            )
            assert duplicate_claim[0] == 409

            module = provider_platform["training"][0]
            training = expect(
                http(base, "/api/platform", {
                    "action": "training:complete", "moduleId": module["id"], "score": 100,
                }, provider_token), {200}, "training completion",
            )["result"]
            assert training["status"] == "passed"
            alert = expect(
                http(base, "/api/platform", {
                    "action": "demand:save", "serviceValue": "homecare|electrician",
                    "gov": "مسقط", "wilayah": "السيب",
                }, user_token), {200}, "demand alert",
            )["result"]
            expect(
                http(base, "/api/platform", {"action": "demand:cancel", "id": alert["id"]}, user_token),
                {200}, "demand alert cancel",
            )

            disabled_api = http(base, "/api/enterprise/v1/summary", headers={"X-Khadamati-API-Key": "invalid"})
            assert disabled_api[0] == 401
            expect(
                http(base, "/api/admin/platform", {
                    "action": "feature:update", "key": "enterprise_api", "enabled": True,
                    "rolloutPercentage": 100, "audiences": ["organization"], "config": {},
                }, admin_token), {200}, "enterprise feature enable",
            )
            client = expect(
                http(base, "/api/admin/platform", {
                    "action": "enterprise:create", "organizationId": organization_id,
                    "name": "عميل API اختباري", "scopes": ["reports:read"], "rateLimit": 10,
                }, admin_token), {200}, "enterprise client",
            )["result"]
            assert client.get("apiKey") and client["apiKey"].startswith("khd_")
            summary = expect(
                http(base, "/api/enterprise/v1/summary", headers={"X-Khadamati-API-Key": client["apiKey"]}),
                {200}, "enterprise aggregate summary",
            )
            assert summary["organization"]["id"] == organization_id
            assert "phone" not in json.dumps(summary).lower()
            adapter_denied = http(base, "/api/admin/platform", {
                "action": "adapter:update", "key": "insurance", "enabled": True,
                "legalStatus": "pending", "mode": "sandbox", "config": {},
            }, admin_token)
            assert adapter_denied[0] == 409

            print("Platform API integration: PASS")
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    run()
