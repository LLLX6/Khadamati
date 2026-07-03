import json
import os
import sys
import urllib.error
import urllib.request


BASE_URL = os.environ.get("KHADAMATI_TEST_URL", "http://127.0.0.1:8080").rstrip("/")


def request(path, payload=None, token=""):
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=body,
        headers=headers,
        method="POST" if payload is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8")
        return error.code, json.loads(raw or "{}")


def main():
    status, bootstrap = request("/api/bootstrap")
    assert status == 200 and bootstrap.get("categories"), "Bootstrap endpoint failed"

    status, provider = request("/api/provider/login", {"phone": "91234567", "pin": "1234"})
    assert status == 200 and provider.get("token"), "Provider login failed"

    status, admin = request("/api/admin/login", {"code": "0000"})
    assert status == 200 and admin.get("token"), "Admin login failed"

    status, invalid_user = request("/api/users/login", {"phone": "12", "name": "test"})
    assert status >= 400 and invalid_user.get("error"), "User validation did not reject an invalid phone"

    print(json.dumps({
        "ok": True,
        "bootstrap": True,
        "provider_login": True,
        "admin_login": True,
        "user_validation": True,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Smoke test failed: {error}", file=sys.stderr)
        raise
