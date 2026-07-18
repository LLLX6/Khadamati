import json
import os
import sys
import urllib.error
import urllib.request


BASE_URL = os.environ.get("KHADAMATI_TEST_URL", "http://127.0.0.1:8080").rstrip("/")
ADMIN_CODE = os.environ.get("KHADAMATI_TEST_ADMIN_CODE", "")
TEST_PNG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9W"
    "lqAAAAAASUVORK5CYII="
)


def request(path, payload=None, token=""):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json", "Origin": "http://127.0.0.1:8080"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=body,
        headers=headers,
        method="POST" if payload is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8")
        return error.code, json.loads(raw or "{}")


def expect(status, data, expected, message):
    assert status in expected, f"{message}: HTTP {status} {data}"
    return data


def main():
    assert ADMIN_CODE, "Set KHADAMATI_TEST_ADMIN_CODE for the isolated test server."

    status, public = request("/api/bootstrap")
    expect(status, public, {200}, "Public bootstrap failed")
    assert public.get("categories"), "Public categories are missing"
    for provider in public.get("providers", []):
        assert not provider.get("phone"), "Public provider phone leaked"
        assert not provider.get("documents"), "Public provider documents leaked"
        assert not provider.get("commercialNo"), "Public provider registration leaked"
    assert not public.get("notifications") and not public.get("customerRequests"), "Visitor received private session data"

    status, admin = request("/api/admin/login", {"code": ADMIN_CODE})
    expect(status, admin, {200}, "Admin login failed")
    admin_token = admin["token"]

    provider_phone = "96895550991"
    provider_pin = "7319"
    status, registration = request(
        "/api/provider-requests",
        {
            "name": "مزود اختبار الإنتاج",
            "phone": provider_phone,
            "pin": provider_pin,
            "providerType": "individual",
            "commercialNo": "TEST-LIC-991",
            "businessRole": "كهربائي منازل",
            "gov": "مسقط",
            "wilayah": "السيب",
            "location": {"lat": 23.62, "lng": 58.22},
            "service": "homecare|electrician",
            "services": [
                {"catId": "homecare", "serviceId": "electrician", "priceFrom": 8, "areas": ["السيب"]},
                {"catId": "homecare", "serviceId": "locks", "priceFrom": 6, "areas": ["السيب"]},
            ],
            "priceFrom": 8,
            "note": "خدمة كهرباء منزلية دقيقة وموثوقة",
            "hours": "الأحد، الاثنين: 8:00 ص - 8:00 م",
            "documentsData": [TEST_PNG],
        },
    )
    expect(status, registration, {201}, "Provider registration failed")
    registration_id = registration["request"]["id"]

    status, decision = request(
        "/api/admin/request-decision",
        {"id": registration_id, "decision": "accept"},
        admin_token,
    )
    expect(status, decision, {200}, "Provider approval failed")

    status, admin_state = request("/api/admin/session", token=admin_token)
    expect(status, admin_state, {200}, "Admin state failed")
    provider = next(item for item in admin_state["providers"] if item.get("phone") == provider_phone)
    provider_id = provider["id"]

    status, pending_subscription = request(
        "/api/admin/subscriptions",
        {
            "action": "request",
            "providerId": provider_id,
            "packageId": "basic_6m",
        },
        admin_token,
    )
    expect(status, pending_subscription, {200}, "Pending subscription request failed")
    pending_id = pending_subscription["subscription"]["subscriptionId"]
    assert pending_subscription["subscription"]["status"] == "pending_payment"

    status, tampered_payment = request(
        "/api/admin/payments",
        {
            "action": "record",
            "subscriptionId": pending_id,
            "amount": 5,
            "method": "bank",
        },
        admin_token,
    )
    expect(status, tampered_payment, {409}, "Tampered subscription amount was accepted")

    status, recorded_payment = request(
        "/api/admin/payments",
        {
            "action": "record",
            "subscriptionId": pending_id,
            "amount": 6,
            "method": "bank",
            "note": "Verified smoke-test transfer",
        },
        admin_token,
    )
    expect(status, recorded_payment, {200}, "Manual payment recording failed")
    assert recorded_payment["payment"]["status"] == "paid"

    status, subscription = request(
        "/api/admin/subscriptions",
        {
            "action": "request",
            "providerId": provider_id,
            "packageId": "professional_12m",
            "approveWithoutPayment": True,
        },
        admin_token,
    )
    expect(status, subscription, {200}, "Professional subscription activation failed")

    status, provider_login = request(
        "/api/provider/login", {"phone": provider_phone, "pin": provider_pin}
    )
    expect(status, provider_login, {200}, "Provider login failed")
    provider_token = provider_login["token"]

    status, unauthenticated_lead = request(
        "/api/leads",
        {"kind": "request", "providerId": provider_id, "phone": "96899999999"},
    )
    expect(status, unauthenticated_lead, {401}, "Legacy lead endpoint accepted an anonymous request")

    status, provider_quote = request(
        "/api/leads",
        {
            "kind": "quote",
            "providerId": "forged-provider",
            "customerName": "عميل",
            "phone": "96899999999",
            "serviceValue": "homecare|electrician",
            "note": "عرض آمن داخل التطبيق",
        },
        provider_token,
    )
    expect(status, provider_quote, {201}, "Authenticated provider quote failed")
    assert provider_quote["lead"]["provider_id"] == provider_id, "Provider identity was not session-bound"
    assert "phone" not in provider_quote["lead"], "Provider quote response exposed a customer phone"

    status, user = request(
        "/api/users/login",
        {
            "phone": "96895550992",
            "name": "مستخدم اختبار الإنتاج",
            "pin": "2468",
            "gov": "مسقط",
            "wilayah": "السيب",
            "location": {"lat": 23.621, "lng": 58.221},
        },
    )
    expect(status, user, {200}, "User login failed")
    user_token = user["token"]

    status, created = request(
        "/api/user/requests",
        {
            "serviceValue": "homecare|electrician",
            "serviceName": "كهربائي",
            "customerName": "مستخدم اختبار الإنتاج",
            "gov": "مسقط",
            "wilayah": "السيب",
            "location": {"lat": 23.621, "lng": 58.221},
            "urgency": "normal",
            "scheduleType": "specific",
            "requestedAt": "2026-07-20T09:00",
            "note": "فحص انقطاع الكهرباء في المنزل",
        },
        user_token,
    )
    expect(status, created, {201}, "Request creation failed")
    request_id = created["request"]["id"]
    assert created.get("matchedProviders", 0) >= 1, "Exact matching returned no providers"

    status, provider_state = request("/api/bootstrap", token=provider_token)
    expect(status, provider_state, {200}, "Provider bootstrap failed")
    assigned = next(item for item in provider_state["customerRequests"] if item["id"] == request_id)
    assert not assigned.get("phone"), "Customer phone leaked before contact consent"

    status, offered = request(
        "/api/request/collaboration",
        {
            "id": request_id,
            "action": "offer",
            "price": 12,
            "duration": "خلال ساعتين",
            "note": "يشمل الفحص والتنفيذ بعد الموافقة",
        },
        provider_token,
    )
    expect(status, offered, {200}, "Provider offer failed")
    offer = offered["request"]["offers"][0]

    status, selected = request(
        "/api/request/collaboration",
        {"id": request_id, "action": "choose_offer", "offerId": offer["id"]},
        user_token,
    )
    expect(status, selected, {200}, "Offer selection failed")
    assert selected["request"].get("acceptedProviderId") == provider_id, (
        f"Selected the wrong provider: {selected['request'].get('acceptedProviderId')} != {provider_id}"
    )
    consent = selected["request"].get("contactConsent", {})
    assert not any(consent.get(channel) for channel in ("chat", "whatsapp", "call")), "Contact consent must start disabled"

    status, blocked_message = request(
        "/api/request/collaboration",
        {"id": request_id, "action": "message", "text": "رسالة قبل الموافقة"},
        provider_token,
    )
    assert status == 403 and blocked_message.get("error") == "chat_consent_required", (
        f"Chat opened before consent: HTTP {status} {blocked_message}"
    )

    status, chat_consent = request(
        "/api/request/collaboration",
        {"id": request_id, "action": "contact_consent", "chat": True, "whatsapp": False, "call": False},
        user_token,
    )
    expect(status, chat_consent, {200}, "Chat consent failed")

    status, message = request(
        "/api/request/collaboration",
        {"id": request_id, "action": "message", "text": "تم الاتفاق داخل المحادثة"},
        provider_token,
    )
    expect(status, message, {200}, "Request chat failed")
    assert not message["request"].get("phone"), "Chat consent exposed the phone"

    status, contact = request(
        "/api/request/collaboration",
        {"id": request_id, "action": "contact_consent", "chat": True, "whatsapp": True, "call": False},
        user_token,
    )
    expect(status, contact, {200}, "WhatsApp consent failed")

    status, provider_allowed = request("/api/bootstrap", token=provider_token)
    allowed = next(item for item in provider_allowed["customerRequests"] if item["id"] == request_id)
    assert allowed.get("phone") == "96895550992", "Approved customer contact was not exposed"

    status, user_allowed = request("/api/bootstrap", token=user_token)
    own_request = next(item for item in user_allowed["customerRequests"] if item["id"] == request_id)
    assert own_request.get("providerContact", {}).get("phone") == provider_phone, "Approved provider contact was not exposed"

    status, completed = request(
        "/api/user/requests", {"id": request_id, "action": "complete"}, user_token
    )
    expect(status, completed, {200}, "Request completion failed")

    status, review = request(
        "/api/reviews",
        {"providerId": provider_id, "requestId": request_id, "rating": 5, "comment": "خدمة ممتازة"},
        user_token,
    )
    expect(status, review, {201}, "Verified review failed")
    status, duplicate = request(
        "/api/reviews",
        {"providerId": provider_id, "requestId": request_id, "rating": 5, "comment": "مكرر"},
        user_token,
    )
    assert status == 409 and duplicate.get("error") == "request_already_reviewed", "Duplicate review was accepted"

    print(
        json.dumps(
            {
                "ok": True,
                "public_privacy": True,
                "provider_registration": True,
                "exact_matching": True,
                "subscription_entitlements": True,
                "payment_integrity": True,
                "contact_consent": True,
                "request_chat": True,
                "verified_review": True,
                "visitor_isolation": True,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Smoke test failed: {error}", file=sys.stderr)
        raise
