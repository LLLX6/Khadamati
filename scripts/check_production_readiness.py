"""Validate production environment names and storage without printing values."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
from urllib.parse import urlparse


PATH_VARIABLES = (
    "KHADAMATI_DB_PATH",
    "KHADAMATI_UPLOAD_DIR",
    "KHADAMATI_BACKUP_DIR",
)
SECRET_MINIMUMS = {
    "KHADAMATI_OTP_PEPPER": 32,
    "KHADAMATI_MEDIA_SIGNING_KEY": 32,
}


def truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def check_environment(environment: dict[str, str], *, check_paths: bool = True) -> dict:
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    def error(code: str, variable: str = "") -> None:
        errors.append({"code": code, **({"variable": variable} if variable else {})})

    def warning(code: str, variable: str = "") -> None:
        warnings.append({"code": code, **({"variable": variable} if variable else {})})

    if environment.get("KHADAMATI_ENV", "").strip().lower() != "production":
        error("environment_not_production", "KHADAMATI_ENV")
    if truthy(environment.get("KHADAMATI_SEED_SAMPLE_DATA")) or truthy(
        environment.get("KHADAMATI_SEED_DEMO_DATA")
    ):
        error("sample_seed_must_be_disabled", "KHADAMATI_SEED_SAMPLE_DATA")
    if environment.get("KHADAMATI_DEV_OTP_CODE", "").strip():
        error("development_otp_must_be_removed", "KHADAMATI_DEV_OTP_CODE")

    public_url = environment.get("KHADAMATI_PUBLIC_URL", "").strip()
    parsed_public = urlparse(public_url)
    if parsed_public.scheme != "https" or not parsed_public.netloc:
        error("public_url_must_use_https", "KHADAMATI_PUBLIC_URL")

    origins = [
        item.strip().rstrip("/")
        for item in environment.get("KHADAMATI_ALLOWED_ORIGINS", "").split(",")
        if item.strip()
    ]
    if not origins:
        error("allowed_origins_required", "KHADAMATI_ALLOWED_ORIGINS")
    for origin in origins:
        parsed = urlparse(origin)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.hostname in {"localhost", "127.0.0.1"}
        ):
            error("production_origin_invalid", "KHADAMATI_ALLOWED_ORIGINS")
            break

    for variable, minimum in SECRET_MINIMUMS.items():
        value = environment.get(variable, "")
        if len(value) < minimum:
            error("secret_missing_or_too_short", variable)

    for variable in PATH_VARIABLES:
        raw = environment.get(variable, "").strip()
        if not raw:
            error("persistent_path_required", variable)
            continue
        path = Path(raw)
        if not path.is_absolute():
            error("persistent_path_must_be_absolute", variable)
            continue
        if not check_paths:
            continue
        target = path.parent if variable == "KHADAMATI_DB_PATH" else path
        if not target.exists():
            error("persistent_path_missing", variable)
        elif not os.access(target, os.W_OK):
            error("persistent_path_not_writable", variable)

    admin_code = environment.get("KHADAMATI_ADMIN_CODE", "").strip()
    if admin_code and not re.fullmatch(r"\d{6,10}", admin_code):
        warning("initial_admin_code_should_be_6_to_10_digits", "KHADAMATI_ADMIN_CODE")
    if not admin_code:
        warning("initial_admin_code_absent_verify_admin_exists", "KHADAMATI_ADMIN_CODE")

    gateway = environment.get("KHADAMATI_PAYMENT_GATEWAY", "manual").strip().lower()
    if gateway and gateway != "manual":
        for variable in (
            "KHADAMATI_PAYMENT_CHECKOUT_URL",
            "KHADAMATI_PAYMENT_WEBHOOK_SECRET",
        ):
            if not environment.get(variable, "").strip():
                error("payment_configuration_incomplete", variable)

    whatsapp = [
        environment.get("WHATSAPP_PHONE_NUMBER_ID", "").strip(),
        environment.get("WHATSAPP_ACCESS_TOKEN", "").strip(),
    ]
    if any(whatsapp) and not all(whatsapp):
        error("whatsapp_configuration_incomplete", "WHATSAPP_*")

    vapid = [
        environment.get("VAPID_PUBLIC_KEY", "").strip(),
        environment.get("VAPID_PRIVATE_KEY", "").strip(),
        environment.get("VAPID_SUBJECT", "").strip(),
    ]
    if any(vapid) and not all(vapid):
        error("web_push_configuration_incomplete", "VAPID_*")

    return {
        "ok": not errors,
        "mode": "production_preflight",
        "errors": errors,
        "warnings": warnings,
        "valuesExposed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--skip-path-check",
        action="store_true",
        help="Validate declarations without requiring mounted paths to exist.",
    )
    args = parser.parse_args()
    result = check_environment(dict(os.environ), check_paths=not args.skip_path_check)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
