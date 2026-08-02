from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import http.client
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, quote, urlparse
from pathlib import Path
from datetime import datetime, timedelta, UTC
from contextlib import contextmanager
import base64
import csv
import hashlib
import hmac
import html
import io
import ipaddress
import json
import math
import mimetypes
import os
import re
import secrets
import smtplib
import sqlite3
import ssl
import threading
import time
import zipfile
from email.message import EmailMessage
from email.utils import formataddr

from khadamati_domain import (
    MIGRATION_KEY,
    OMR,
    PLAN_IDS,
    POLICY_VERSION,
    SUPPORT_EMAIL,
    ContactConsentService,
    DomainError,
    EntitlementService,
    OTPService,
    PaymentAdapter,
    PlanCatalog,
    RankingService,
    RequestMarketplace,
    SubscriptionService,
    run_subscription_migration_v1,
)
from khadamati_workflow import (
    CompletionEvidenceService,
    RequestAgreementService,
    RequestIdempotencyService,
    RequestLifecycleService,
    ServiceAssetService,
    attach_workflow_data,
    install_workflow_schema,
)
from khadamati_locations import (
    LocationCatalogService,
    install_location_schema,
    location_snapshot,
    resolve_area,
)
from khadamati_rewards import (
    RewardCampaignService,
    install_reward_schema,
    loyalty_summary,
    record_loyalty_transaction,
)
from khadamati_community import (
    CommunityService,
    community_settings,
    install_community_schema,
    run_community_maintenance,
    save_community_settings,
)
from khadamati_trust import (
    ComplaintCaseService,
    InteractionBlockService,
    ProviderVerificationService,
    install_trust_schema,
    trust_statistics,
)
from khadamati_security import AdminTwoFactorService, install_security_schema
from khadamati_growth import KnownProviderInvitationService, install_growth_schema
from khadamati_platform import (
    ConversationControlService,
    DemandAlertService,
    EnterpriseAPIService,
    FeatureFlagService,
    FinancialScenarioService,
    MaintenanceContractService,
    OrganizationService,
    ProviderCRMService,
    ProviderLegalProfileService,
    ReferralService,
    RiskReviewService,
    TrainingAchievementService,
    adapter_snapshot,
    install_platform_schema,
    platform_snapshot,
)

try:
    from pywebpush import WebPushException, webpush
except ImportError:
    WebPushException = Exception
    webpush = None

BASE_DIR = Path(__file__).resolve().parent
PUBLIC_DIR = BASE_DIR / "public"
UPLOAD_DIR = Path(os.environ.get("KHADAMATI_UPLOAD_DIR") or os.environ.get("FORAN_UPLOAD_DIR") or (PUBLIC_DIR / "uploads"))
_legacy_db = BASE_DIR / "foran.sqlite3"
DB_PATH = Path(os.environ.get("KHADAMATI_DB_PATH") or os.environ.get("FORAN_DB_PATH") or (_legacy_db if _legacy_db.exists() else BASE_DIR / "khadamati.sqlite3"))
APP_ENV = os.environ.get("KHADAMATI_ENV", "development").strip().lower() or "development"


def environment_flag(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return bool(default)
    return value.strip().lower() in {"1", "true", "yes", "on"}


APP_RELEASE = os.environ.get("KHADAMATI_RELEASE", "v1.1.3").strip() or "v1.1.3"
TRUST_MIGRATION_KEY = "TRUST_SCHEMA_V1"
QUALITY_MIGRATION_KEY = "QUALITY_SCHEMA_V1"
PLATFORM_MIGRATION_KEY = "PLATFORM_SCHEMA_V1"
SAMPLE_DATA_ENABLED = environment_flag(
    "KHADAMATI_SEED_SAMPLE_DATA",
    environment_flag("KHADAMATI_SEED_DEMO_DATA", APP_ENV in {"development", "test"}),
)
INITIAL_ADMIN_CODE = (
    os.environ.get("KHADAMATI_ADMIN_CODE")
    or os.environ.get("FORAN_ADMIN_CODE")
    or (os.environ.get("KHADAMATI_DEV_ADMIN_CODE") if APP_ENV != "production" else "")
    or ""
)
DEFAULT_ALLOWED_ORIGINS = {
    "https://lllx6.github.io",
    "https://khadamati-app-api.onrender.com",
    "http://127.0.0.1:8080",
    "http://localhost:8080",
}
ALLOWED_ORIGINS = {
    item.strip().rstrip("/")
    for item in os.environ.get("KHADAMATI_ALLOWED_ORIGINS", ",".join(sorted(DEFAULT_ALLOWED_ORIGINS))).split(",")
    if item.strip()
}
SESSION_DAYS = int(os.environ.get("KHADAMATI_SESSION_DAYS", "30"))
ACCESS_TOKEN_MINUTES = max(
    5, int(os.environ.get("KHADAMATI_ACCESS_TOKEN_MINUTES", "480"))
)
PUBLIC_APP_URL = os.environ.get("KHADAMATI_PUBLIC_URL", "https://lllx6.github.io/Khadamati/").rstrip("/") + "/"
LOGIN_MAX_ATTEMPTS = max(3, int(os.environ.get("KHADAMATI_LOGIN_MAX_ATTEMPTS", "5")))
LOGIN_LOCK_MINUTES = max(1, int(os.environ.get("KHADAMATI_LOGIN_LOCK_MINUTES", "15")))
MEDIA_URL_TTL_SECONDS = max(60, int(os.environ.get("KHADAMATI_MEDIA_URL_TTL_SECONDS", "21600")))
MEDIA_SIGNING_KEY = (
    os.environ.get("KHADAMATI_MEDIA_SIGNING_KEY")
    or os.environ.get("KHADAMATI_OTP_PEPPER")
    or secrets.token_urlsafe(32)
)
ADMIN_2FA_KEY = os.environ.get("KHADAMATI_ADMIN_2FA_KEY") or MEDIA_SIGNING_KEY
REQUIRE_ADMIN_2FA = environment_flag(
    "KHADAMATI_REQUIRE_ADMIN_2FA", False
)
ADMIN_EMAIL = (
    os.environ.get("KHADAMATI_ADMIN_EMAIL") or SUPPORT_EMAIL
).strip().lower()
DEFAULT_JSON_LIMIT = max(65_536, int(os.environ.get("KHADAMATI_MAX_JSON_BYTES", "1000000")))
JSON_LIMITS = {
    "/api/provider-requests": 60_000_000,
    "/api/provider/profile": 60_000_000,
    "/api/provider/work-images": 50_000_000,
    "/api/provider/documents": 22_000_000,
    "/api/provider/image": 4_000_000,
    "/api/users/register": 4_000_000,
    "/api/user/profile": 4_000_000,
    "/api/user/requests": 20_000_000,
    "/api/request/collaboration": 10_000_000,
    "/api/request/workflow": 14_000_000,
    "/api/service-assets": 4_000_000,
    "/api/community": 8_000_000,
    "/api/trust/complaint": 18_000_000,
    "/api/trust/verification": 8_000_000,
    "/api/trust/block": 200_000,
    "/api/platform": 2_000_000,
    "/api/admin/platform": 2_000_000,
    "/api/admin/ads": 6_000_000,
    "/api/admin/complaint-case": 2_000_000,
    "/api/admin/verification": 2_000_000,
}

ALL_PERMISSIONS = [
    "view_reports",
    "manage_providers",
    "review_requests",
    "manage_quality",
    "manage_subscriptions",
    "manage_finance",
    "manage_settings",
    "manage_admins",
    "manage_team",
    "manage_consent",
    "manage_campaigns",
    "manage_community",
    "manage_audit",
    "backup",
]
ROLE_PERMISSIONS = {
    "super_admin": ALL_PERMISSIONS,
    "admin": [
        "view_reports", "manage_providers", "review_requests", "manage_quality",
        "manage_subscriptions", "manage_finance", "manage_settings", "manage_team",
        "manage_consent", "manage_campaigns", "manage_community", "manage_audit", "backup",
    ],
    "owner": ALL_PERMISSIONS,
    "manager": ["view_reports", "manage_providers", "review_requests", "manage_quality", "manage_subscriptions", "manage_finance", "manage_team", "manage_consent", "manage_community", "backup"],
    "support": ["view_reports", "review_requests", "manage_quality", "manage_community"],
    "finance": ["view_reports", "manage_subscriptions", "manage_finance", "backup"],
    "user": [],
    "provider": [],
    "provider_owner": [],
    "provider_manager": [],
    "provider_staff": [],
}
PROVIDER_ROLE_PERMISSIONS = {
    "provider_owner": {"profile", "media", "documents", "subscription", "team", "branches", "requests"},
    "provider_manager": {"profile", "media", "documents", "team", "branches", "requests"},
    "provider_staff": {"requests"},
}

IMAGE_MIMES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
DOCUMENT_MIMES = {"application/pdf": "pdf", "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
CHAT_MIMES = {
    **IMAGE_MIMES,
    "audio/webm": "webm",
    "audio/mp4": "m4a",
    "audio/x-m4a": "m4a",
    "audio/aac": "aac",
    "audio/mpeg": "mp3",
    "audio/ogg": "ogg",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
}
VIDEO_MIMES = {"video/mp4": "mp4", "video/webm": "webm", "video/quicktime": "mov"}


@contextmanager
def db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=12)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=12000")
    con.execute("PRAGMA foreign_keys=ON")
    try:
        with con:
            yield con
    finally:
        con.close()


def slug(prefix):
    return f"{prefix}_{secrets.token_hex(16)}"


def hash_secret(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def hash_pin(value):
    salt = secrets.token_hex(16)
    rounds = 160_000
    digest = hashlib.pbkdf2_hmac("sha256", str(value).encode("utf-8"), salt.encode("ascii"), rounds).hex()
    return f"pbkdf2_sha256${rounds}${salt}${digest}"


def verify_secret(value, encoded):
    encoded = str(encoded or "")
    if encoded.startswith("pbkdf2_sha256$"):
        try:
            _, rounds, salt, digest = encoded.split("$", 3)
            actual = hashlib.pbkdf2_hmac(
                "sha256", str(value).encode("utf-8"), salt.encode("ascii"), int(rounds)
            ).hex()
            return hmac.compare_digest(actual, digest)
        except (TypeError, ValueError):
            return False
    return hmac.compare_digest(hash_secret(value), encoded)


def jdump(value):
    return json.dumps(value, ensure_ascii=False)


def jload(value, fallback=None):
    if value in (None, ""):
        return fallback
    return json.loads(value)


def normalize_phone(raw):
    phone = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if phone.startswith("0"):
        phone = "968" + phone[1:]
    if len(phone) == 8:
        phone = "968" + phone
    return phone


def safe_text(value, limit=240):
    return str(value or "").strip()[:limit]


def log_event(event, level="info", **fields):
    """Write one structured event without request bodies, query strings, or secrets."""
    record = {
        "timestamp": datetime.now(UTC).isoformat(),
        "level": safe_text(level, 20) or "info",
        "event": safe_text(event, 120) or "application.event",
        "release": APP_RELEASE,
        "environment": APP_ENV,
    }
    for key, value in fields.items():
        if value is None:
            continue
        record[key] = (
            value
            if isinstance(value, (str, int, float, bool, dict, list))
            else str(value)
        )
    print(jdump(record), flush=True)


def finite_number(value, default=0.0, *, minimum=None, maximum=None):
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    if not math.isfinite(number):
        raise DomainError("invalid_number", 400)
    if minimum is not None and number < minimum:
        raise DomainError("number_out_of_range", 400)
    if maximum is not None and number > maximum:
        raise DomainError("number_out_of_range", 400)
    return number


def bounded_int(value, default=0, *, minimum=None, maximum=None):
    number = finite_number(value, default, minimum=minimum, maximum=maximum)
    if not number.is_integer():
        raise DomainError("invalid_integer", 400)
    return int(number)


def strict_bool(value, default=False):
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    raise DomainError("invalid_boolean", 400)


def normalized_location(value):
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise DomainError("invalid_location", 400)
    if value.get("lat") in (None, "") or value.get("lng") in (None, ""):
        return {}
    lat = finite_number(value.get("lat"), minimum=-90, maximum=90)
    lng = finite_number(value.get("lng"), minimum=-180, maximum=180)
    result = {"lat": lat, "lng": lng}
    if value.get("accuracy") not in (None, ""):
        result["accuracy"] = finite_number(value.get("accuracy"), minimum=0, maximum=100_000)
    if value.get("updatedAt"):
        result["updatedAt"] = safe_text(value.get("updatedAt"), 50)
    if value.get("label"):
        result["label"] = safe_text(value.get("label"), 80)
    return result


def normalized_availability(value, fallback=None):
    if value in (None, ""):
        return dict(fallback or {})
    if not isinstance(value, dict):
        raise DomainError("invalid_availability", 400)
    days = value.get("days", [])
    if not isinstance(days, list):
        raise DomainError("invalid_availability_days", 400)
    normalized_days = []
    for day in days:
        try:
            day_number = int(day)
        except (TypeError, ValueError) as exc:
            raise DomainError("invalid_availability_days", 400) from exc
        if day_number < 0 or day_number > 6:
            raise DomainError("invalid_availability_days", 400)
        if str(day_number) not in normalized_days:
            normalized_days.append(str(day_number))
    start = safe_text(value.get("start"), 5)
    end = safe_text(value.get("end"), 5)
    for clock in (start, end):
        if clock and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", clock):
            raise DomainError("invalid_availability_time", 400)
    if bool(start) != bool(end):
        raise DomainError("availability_time_range_required", 400)
    daily_capacity = bounded_int(
        value.get("dailyCapacity", 0), 0, minimum=0, maximum=100
    )
    return {
        "days": normalized_days,
        "start": start,
        "end": end,
        "dailyCapacity": daily_capacity,
    }


def normalized_provider_services(
    con, value, *, limit, category_limit=1, fallback_price=0, default_areas=None
):
    if not isinstance(value, list):
        raise DomainError("services_must_be_list", 400)
    services = []
    seen = set()
    categories = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        cat_id = safe_text(item.get("catId"), 80)
        service_id = safe_text(item.get("serviceId"), 80)
        key = (cat_id, service_id)
        if not cat_id or not service_id or key in seen:
            continue
        exists = con.execute(
            """SELECT s.id FROM services s JOIN categories c ON c.id=s.category_id
            WHERE s.id=? AND s.category_id=? AND s.active=1 AND c.active=1
            AND COALESCE(s.deleted_at,'')='' AND COALESCE(c.deleted_at,'')=''""",
            (service_id, cat_id),
        ).fetchone()
        if not exists:
            raise DomainError("service_not_found", 400, f"{cat_id}|{service_id}")
        item_areas = item.get("areas", default_areas or [])
        if not isinstance(item_areas, list):
            item_areas = default_areas or []
        areas = list(dict.fromkeys(safe_text(area, 80) for area in item_areas if safe_text(area, 80)))[:50]
        services.append(
            {
                "id": safe_text(item.get("id"), 100) or slug("ps"),
                "catId": cat_id,
                "serviceId": service_id,
                "priceFrom": finite_number(
                    item.get("priceFrom", fallback_price), minimum=0, maximum=1_000_000
                ),
                "active": bool(item.get("active", True)),
                "areas": areas,
            }
        )
        seen.add(key)
        categories.add(cat_id)
    if len(services) > max(1, int(limit)):
        raise DomainError("service_limit_exceeded", 409)
    if len(categories) > max(1, int(category_limit)):
        raise DomainError("provider_category_limit", 409)
    return services


def phone_matches(stored, normalized):
    """Compare current and legacy phone formats without forcing a data reset."""
    return bool(normalized) and normalize_phone(stored) == normalized


def iso_date(days=0):
    return (datetime.now(UTC) + timedelta(days=days)).strftime("%Y-%m-%d")


def iso_datetime(minutes=0, days=0):
    return (datetime.now(UTC) + timedelta(minutes=minutes, days=days)).isoformat()


def parse_iso(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    except (TypeError, ValueError):
        return None


def seed_service(service_id, icon, ar, en):
    return {"id": service_id, "icon": icon, "ar": ar, "en": en, "active": 1}


def seed_category(cat_id, icon, ar, en, services):
    return {"id": cat_id, "icon": icon, "ar": ar, "en": en, "active": 1, "services": services}


SEED_CATEGORIES = [
    seed_category("homecare", "🏠", "صيانة المنزل", "Home maintenance", [
        seed_service("electrician", "⚡", "كهربائي", "Electrician"), seed_service("plumber", "🚿", "سباك", "Plumber"),
        seed_service("ac", "❄️", "صيانة مكيفات", "AC maintenance"), seed_service("appliances", "🔧", "صيانة أجهزة منزلية", "Home appliances"),
        seed_service("curtains", "🪟", "تركيب ستائر", "Curtain installation"), seed_service("furniture", "🪑", "تركيب أثاث", "Furniture assembly"),
        seed_service("paint", "🎨", "دهان", "Painting"), seed_service("gypsum", "◻️", "جبس وديكور", "Gypsum and decor"),
        seed_service("pest", "🐜", "مكافحة حشرات", "Pest control"), seed_service("tanks", "💧", "تنظيف خزانات", "Tank cleaning"),
        seed_service("doors", "🚪", "تصليح أبواب وأقفال", "Doors and locks"), seed_service("gardens", "🌿", "تنسيق حدائق", "Garden care"),
        seed_service("pools", "🏊", "صيانة مسابح", "Pool maintenance"), seed_service("satellite", "📡", "تركيب دش وستلايت", "Satellite installation"),
        seed_service("smart_home", "🏡", "أنظمة منزل ذكي", "Smart home systems"), seed_service("water_heater", "♨️", "صيانة سخانات", "Water heater repair"),
    ]),
    seed_category("cleaning", "🧼", "التنظيف", "Cleaning", [
        seed_service("home_clean", "🏡", "تنظيف منازل", "Home cleaning"), seed_service("apt_clean", "🏢", "تنظيف شقق", "Apartment cleaning"),
        seed_service("majlis", "🛋️", "تنظيف مجالس", "Majlis cleaning"), seed_service("sofa", "🛋️", "تنظيف كنب", "Sofa cleaning"),
        seed_service("carpet", "🧽", "تنظيف سجاد", "Carpet cleaning"), seed_service("post_build", "🏗️", "تنظيف بعد البناء", "Post-construction cleaning"),
        seed_service("office_clean", "🏬", "تنظيف مكاتب", "Office cleaning"), seed_service("facade", "🪟", "تنظيف واجهات", "Facade cleaning"),
        seed_service("deep_clean", "🧴", "تنظيف عميق", "Deep cleaning"), seed_service("kitchen_clean", "🍽️", "تنظيف مطابخ", "Kitchen cleaning"),
        seed_service("bath_clean", "🚿", "تنظيف دورات مياه", "Bathroom cleaning"), seed_service("mattress", "🛏️", "تنظيف مراتب", "Mattress cleaning"),
        seed_service("sterilize", "🛡️", "تعقيم", "Sanitization"), seed_service("maid_hourly", "⏱️", "عاملة بالساعة", "Hourly cleaner"),
    ]),
    seed_category("transport", "🚚", "النقل والتوصيل", "Moving and delivery", [
        seed_service("furniture_move", "🚚", "نقل أثاث", "Furniture moving"), seed_service("items_delivery", "📦", "توصيل أغراض", "Item delivery"),
        seed_service("within_wilayah", "🛻", "نقل داخل الولاية", "Within-wilayah moving"), seed_service("between_gov", "🛣️", "نقل بين المحافظات", "Inter-governorate moving"),
        seed_service("loading", "📦", "تحميل وتنزيل", "Loading and unloading"), seed_service("private_driver", "🚗", "سائق خاص", "Private driver"),
        seed_service("small_truck", "🚛", "شاحنة صغيرة", "Small truck"), seed_service("large_truck", "🚛", "شاحنة كبيرة", "Large truck"),
        seed_service("cold_delivery", "❄️", "توصيل مبرد", "Cold delivery"), seed_service("airport", "✈️", "توصيل مطار", "Airport transfer"),
        seed_service("school_bus", "🚌", "نقل مدارس", "School transport"), seed_service("heavy_equipment", "🏗️", "نقل معدات", "Equipment transport"),
        seed_service("parcel", "📮", "طرود ومستندات", "Parcels and documents"),
    ]),
    seed_category("construction", "🏗️", "البناء والمقاولات", "Construction", [
        seed_service("building", "🧱", "بناء", "Building"), seed_service("renovation", "🛠️", "ترميم", "Renovation"),
        seed_service("tiles", "⬜", "بلاط", "Tiles"), seed_service("marble", "▫️", "رخام", "Marble"),
        seed_service("aluminium", "🪟", "ألمنيوم", "Aluminium"), seed_service("metal", "⚒️", "حدادة", "Metalwork"),
        seed_service("carpentry", "🪚", "نجارة", "Carpentry"), seed_service("insulation", "🧱", "عزل", "Insulation"),
        seed_service("roof", "🏠", "صيانة أسطح", "Roof maintenance"), seed_service("glass", "🪟", "زجاج ومرايا", "Glass and mirrors"),
        seed_service("plaster", "📐", "لياسة", "Plastering"), seed_service("blocks", "🧱", "طابوق", "Block work"),
        seed_service("survey", "📏", "مساحة وتخطيط", "Surveying"), seed_service("engineering", "📋", "استشارة هندسية", "Engineering consultation"),
        seed_service("demolition", "🚧", "إزالة وهدم", "Demolition"),
    ]),
    seed_category("tech", "💻", "التقنية", "Technology", [
        seed_service("pc", "💻", "صيانة كمبيوتر", "Computer repair"), seed_service("phone_repair", "📱", "صيانة هواتف", "Phone repair"),
        seed_service("cameras", "📹", "كاميرات مراقبة", "Security cameras"), seed_service("networks", "🌐", "شبكات", "Networks"),
        seed_service("websites", "🧩", "برمجة مواقع", "Web development"), seed_service("design", "✏️", "تصميم", "Design"),
        seed_service("tech_support", "🧑‍💻", "دعم تقني", "Technical support"), seed_service("pos", "🧾", "أنظمة نقاط بيع", "Point-of-sale systems"),
        seed_service("printer", "🖨️", "طابعات", "Printers"), seed_service("data_recovery", "💾", "استرجاع بيانات", "Data recovery"),
        seed_service("apps", "📲", "تطبيقات", "Mobile apps"), seed_service("marketing", "📣", "تسويق رقمي", "Digital marketing"),
        seed_service("cyber", "🔐", "أمن معلومات", "Cybersecurity"), seed_service("apple", "🍏", "أجهزة أبل", "Apple devices"),
    ]),
    seed_category("cars", "🚘", "السيارات", "Cars", [
        seed_service("car_electric", "🔌", "كهرباء سيارات", "Car electrical"), seed_service("mechanic", "🔧", "ميكانيكي", "Mechanic"),
        seed_service("car_wash", "🧽", "غسيل سيارات", "Car wash"), seed_service("battery", "🔋", "تبديل بطارية", "Battery replacement"),
        seed_service("tires", "🛞", "تبديل إطارات", "Tire replacement"), seed_service("inspection", "🔍", "فحص سيارة", "Car inspection"),
        seed_service("tow", "🚨", "ونش", "Tow truck"), seed_service("polish", "✨", "تلميع", "Polishing"),
        seed_service("oil", "🛢️", "تبديل زيت", "Oil change"), seed_service("ac_car", "❄️", "مكيف سيارات", "Car AC"),
        seed_service("keys", "🗝️", "مفاتيح سيارات", "Car keys"), seed_service("tint", "🌗", "تظليل", "Window tinting"),
        seed_service("paintless", "🧲", "شفط صدمات", "Dent repair"), seed_service("diagnostics", "🧪", "فحص كمبيوتر", "Diagnostics"),
        seed_service("detailing", "🧼", "تنظيف داخلي", "Interior detailing"),
    ]),
    seed_category("events", "🎉", "المناسبات", "Events", [
        seed_service("photo", "📸", "تصوير", "Photography"), seed_service("party", "🎈", "تنسيق حفلات", "Event coordination"),
        seed_service("hospitality", "☕", "ضيافة", "Hospitality"), seed_service("coffee", "☕", "قهوة ومشروبات", "Coffee and drinks"),
        seed_service("wedding", "💐", "كوش أفراح", "Wedding stage"), seed_service("dj", "🎧", "دي جي", "DJ"),
        seed_service("flowers", "🌹", "ورود", "Flowers"), seed_service("equip", "🎪", "تجهيزات", "Event equipment"),
        seed_service("video", "🎥", "تصوير فيديو", "Videography"), seed_service("sound", "🔊", "صوتيات وإضاءة", "Sound and lighting"),
        seed_service("catering", "🍽️", "بوفيه وضيافة", "Catering"), seed_service("kids_party", "🎁", "حفلات أطفال", "Kids parties"),
        seed_service("chairs", "🪑", "كراسي وطاولات", "Chairs and tables"), seed_service("makeup", "💄", "مكياج مناسبات", "Event makeup"),
    ]),
    seed_category("education", "📚", "التعليم", "Education", [
        seed_service("english", "🇬🇧", "مدرس لغة إنجليزية", "English tutor"), seed_service("math", "➗", "مدرس رياضيات", "Math tutor"),
        seed_service("arabic", "✍️", "مدرس عربي", "Arabic tutor"), seed_service("private_tutor", "👨‍🏫", "مدرس خصوصي", "Private tutor"),
        seed_service("quran", "📖", "تحفيظ قرآن", "Quran memorization"), seed_service("computer_train", "💻", "تدريب حاسوب", "Computer training"),
        seed_service("vocational", "🧰", "تدريب مهني", "Vocational training"), seed_service("physics", "🧲", "مدرس فيزياء", "Physics tutor"),
        seed_service("chemistry", "⚗️", "مدرس كيمياء", "Chemistry tutor"), seed_service("ielts", "📝", "IELTS وTOEFL", "IELTS and TOEFL"),
        seed_service("kids_learning", "🧒", "تأسيس أطفال", "Kids foundation"), seed_service("university", "🎓", "دروس جامعية", "University tutoring"),
    ]),
    seed_category("personal", "🧍", "خدمات شخصية", "Personal services", [
        seed_service("barber", "💈", "حلاقة", "Barber"), seed_service("men_care", "🧴", "عناية رجالية", "Men care"),
        seed_service("tailor", "🧵", "خياطة", "Tailoring"), seed_service("ironing", "👔", "كوي", "Ironing"),
        seed_service("laundry", "🧺", "غسيل ملابس", "Laundry"), seed_service("perfume", "🪔", "عطور وبخور", "Perfume and bukhoor"),
        seed_service("home_help", "🤝", "مساعدة منزلية", "Home assistance"), seed_service("beauty", "💅", "تجميل منزلي", "Home beauty"),
        seed_service("massage", "🧘", "مساج واسترخاء", "Massage"), seed_service("elder_care", "🧓", "رعاية كبار السن", "Elder care"),
        seed_service("pet_care", "🐾", "رعاية حيوانات أليفة", "Pet care"), seed_service("documents", "📄", "تخليص معاملات", "Document services"),
    ]),
]

SEED_PROVIDERS = [
    {
        "id": "p1",
        "name": "سالم البلوشي",
        "phone": "91234567",
        "gov": "مسقط",
        "wilayah": "السيب",
        "areas": ["السيب", "بوشر"],
        "bio": "تنفيذ أعمال الكهرباء المنزلية والصيانة الطارئة بدقة وتنظيم.",
        "hours": "8:00 ص - 9:00 م",
        "status": "available",
        "active": 1,
        "verified": 1,
        "featured": 1,
        "package_id": "plus",
        "rating": 4.8,
        "reviews": 38,
        "services": [{"catId": "homecare", "serviceId": "electrician", "priceFrom": 5, "active": True, "areas": ["السيب", "بوشر"]}],
    },
    {
        "id": "p2",
        "name": "النخبة للتنظيف",
        "phone": "92345678",
        "gov": "مسقط",
        "wilayah": "بوشر",
        "areas": ["بوشر", "مطرح", "السيب"],
        "bio": "تنظيف منازل ومجالس وكنب بفرق منظمة ومواعيد واضحة.",
        "hours": "7:00 ص - 10:00 م",
        "status": "busy",
        "active": 1,
        "verified": 1,
        "featured": 1,
        "package_id": "growth",
        "rating": 4.7,
        "reviews": 51,
        "services": [{"catId": "cleaning", "serviceId": "home_clean", "priceFrom": 12, "active": True, "areas": ["بوشر", "السيب"]}],
    },
    {
        "id": "p3", "name": "ناصر للمكيفات", "phone": "93456789", "gov": "الداخلية", "wilayah": "نزوى",
        "areas": ["نزوى", "بهلاء", "منح"], "bio": "صيانة مكيفات وتنظيف وفحص أعطال وتركيب.",
        "hours": "9:00 ص - 8:00 م", "status": "available", "active": 1, "verified": 0, "featured": 0,
        "package_id": "individual_6m", "rating": 4.4, "reviews": 22,
        "services": [{"catId": "homecare", "serviceId": "ac", "priceFrom": 6, "active": True, "areas": ["نزوى", "بهلاء"]}],
    },
    {
        "id": "p4", "name": "بركاء للنقل", "phone": "94567890", "gov": "جنوب الباطنة", "wilayah": "بركاء",
        "areas": ["بركاء", "المصنعة", "مسقط"], "bio": "نقل أثاث وتحميل وتنزيل داخل الولاية وبين المحافظات.",
        "hours": "6:00 ص - 11:00 م", "status": "available", "active": 1, "verified": 1, "featured": 0,
        "package_id": "individual_6m", "rating": 4.6, "reviews": 18,
        "services": [{"catId": "transport", "serviceId": "furniture_move", "priceFrom": 18, "active": True, "areas": ["بركاء", "مسقط"]}, {"catId": "transport", "serviceId": "loading", "priceFrom": 10, "active": True, "areas": ["بركاء"]}],
    },
    {
        "id": "p5", "name": "تقنية الوادي", "phone": "95678901", "gov": "مسقط", "wilayah": "مطرح",
        "areas": ["مطرح", "بوشر", "السيب"], "bio": "صيانة كمبيوتر وشبكات وكاميرات وأنظمة نقاط بيع.",
        "hours": "10:00 ص - 9:00 م", "status": "unavailable", "active": 1, "verified": 1, "featured": 0,
        "package_id": "individual_year", "rating": 4.9, "reviews": 14,
        "services": [{"catId": "tech", "serviceId": "pc", "priceFrom": 8, "active": True, "areas": ["مسقط"]}, {"catId": "tech", "serviceId": "cameras", "priceFrom": 25, "active": True, "areas": ["مسقط"]}],
    },
    {
        "id": "p6", "name": "ظفار للمناسبات", "phone": "96789012", "gov": "ظفار", "wilayah": "صلالة",
        "areas": ["صلالة", "طاقة"], "bio": "تصوير وتنسيق مناسبات وضيافة بتجهيزات مرتبة.",
        "hours": "حسب الموعد", "status": "available", "active": 1, "verified": 0, "featured": 0,
        "package_id": "intro", "rating": 4.3, "reviews": 11,
        "services": [{"catId": "events", "serviceId": "photo", "priceFrom": 35, "active": True, "areas": ["صلالة"]}, {"catId": "events", "serviceId": "hospitality", "priceFrom": 20, "active": True, "areas": ["صلالة"]}],
    },
    {
        "id": "p7", "name": "عُمان للمقاولات الخفيفة", "phone": "97890123", "provider_type": "company",
        "company_name": "عُمان للمقاولات الخفيفة", "gov": "مسقط", "wilayah": "العامرات",
        "areas": ["العامرات", "بوشر", "قريات"], "bio": "شركة صغيرة لأعمال الترميم والبلاط والألمنيوم مع فريق عمل منظم.",
        "hours": "كل أيام الأسبوع 8:00 - 18:00", "status": "available", "active": 1, "verified": 1, "featured": 1,
        "package_id": "company_year", "rating": 4.8, "reviews": 27, "subscription_start": "2026-06-01", "subscription_until": "2027-06-01",
        "services": [{"catId": "construction", "serviceId": "renovation", "priceFrom": 25, "active": True, "areas": ["مسقط"]}, {"catId": "construction", "serviceId": "tiles", "priceFrom": 18, "active": True, "areas": ["مسقط"]}, {"catId": "construction", "serviceId": "aluminium", "priceFrom": 30, "active": True, "areas": ["مسقط"]}],
    },
    {
        "id": "p8", "name": "مركز الطريق للسيارات", "phone": "98901234", "provider_type": "company",
        "company_name": "مركز الطريق للسيارات", "gov": "شمال الباطنة", "wilayah": "صحار",
        "areas": ["صحار", "صحم", "السويق"], "bio": "خدمات فحص وميكانيكا وكهرباء سيارات مع مواعيد واضحة وتواصل سريع.",
        "hours": "السبت - الخميس 8:00 - 20:00", "status": "available", "active": 1, "verified": 1, "featured": 1,
        "package_id": "company_year", "rating": 4.7, "reviews": 34, "subscription_start": "2026-06-10", "subscription_until": "2027-06-10",
        "services": [{"catId": "cars", "serviceId": "mechanic", "priceFrom": 10, "active": True, "areas": ["صحار"]}, {"catId": "cars", "serviceId": "inspection", "priceFrom": 8, "active": True, "areas": ["صحار", "صحم"]}, {"catId": "cars", "serviceId": "battery", "priceFrom": 12, "active": True, "areas": ["شمال الباطنة"]}],
    },
    {
        "id": "p9", "name": "أسماء للتعليم المنزلي", "phone": "99012345", "gov": "الداخلية", "wilayah": "بهلاء",
        "areas": ["بهلاء", "نزوى", "منح"], "bio": "دروس تأسيس ورياضيات وإنجليزي للطلاب مع متابعة أسبوعية مختصرة.",
        "hours": "أيام محددة 16:00 - 21:00", "status": "busy", "active": 1, "verified": 1, "featured": 0,
        "package_id": "individual_year", "rating": 4.9, "reviews": 19, "subscription_start": "2026-05-20", "subscription_until": "2027-05-20",
        "services": [{"catId": "education", "serviceId": "math", "priceFrom": 6, "active": True, "areas": ["بهلاء", "نزوى"]}, {"catId": "education", "serviceId": "english", "priceFrom": 7, "active": True, "areas": ["الداخلية"]}, {"catId": "education", "serviceId": "kids_learning", "priceFrom": 5, "active": True, "areas": ["بهلاء"]}],
    },
    {
        "id": "p10", "name": "دار العناية المنزلية", "phone": "90123456", "provider_type": "company",
        "company_name": "دار العناية المنزلية", "gov": "مسقط", "wilayah": "مطرح",
        "areas": ["مطرح", "بوشر", "السيب"], "bio": "شركة خدمات شخصية منزلية تشمل رعاية كبار السن والمساعدة المنزلية والغسيل.",
        "hours": "كل أيام الأسبوع 7:00 - 22:00", "status": "available", "active": 1, "verified": 1, "featured": 0,
        "package_id": "company_year", "rating": 4.6, "reviews": 23, "subscription_start": "2026-06-15", "subscription_until": "2027-06-15",
        "services": [{"catId": "personal", "serviceId": "elder_care", "priceFrom": 15, "active": True, "areas": ["مسقط"]}, {"catId": "personal", "serviceId": "home_help", "priceFrom": 8, "active": True, "areas": ["مسقط"]}, {"catId": "personal", "serviceId": "laundry", "priceFrom": 4, "active": True, "areas": ["مطرح", "بوشر"]}],
    },
    {
        "id": "p11", "name": "مريم للخياطة والتجهيز", "phone": "91230001", "gov": "جنوب الباطنة", "wilayah": "الرستاق",
        "areas": ["الرستاق", "بركاء"], "bio": "خياطة وتعديل ملابس وكوي وتجهيز بسيط للمناسبات.",
        "hours": "نهاية الأسبوع 10:00 - 20:00", "status": "available", "active": 1, "verified": 0, "featured": 0,
        "package_id": "individual_6m", "rating": 4.5, "reviews": 12, "subscription_start": "2026-06-01", "subscription_until": "2026-12-01",
        "services": [{"catId": "personal", "serviceId": "tailor", "priceFrom": 3, "active": True, "areas": ["الرستاق"]}, {"catId": "personal", "serviceId": "ironing", "priceFrom": 2, "active": True, "areas": ["جنوب الباطنة"]}],
    },
    {
        "id": "p12", "name": "المهارة للتقنية والتصميم", "phone": "92340002", "provider_type": "company",
        "company_name": "المهارة للتقنية والتصميم", "gov": "مسقط", "wilayah": "بوشر",
        "areas": ["بوشر", "السيب", "مطرح"], "bio": "شركة تقنية لتصميم المواقع والدعم التقني والشبكات للمحلات والشركات.",
        "hours": "الأحد - الخميس 9:00 - 18:00", "status": "available", "active": 1, "verified": 1, "featured": 1,
        "package_id": "company_year", "rating": 4.8, "reviews": 29, "subscription_start": "2026-06-05", "subscription_until": "2027-06-05",
        "services": [{"catId": "tech", "serviceId": "websites", "priceFrom": 80, "active": True, "areas": ["مسقط"]}, {"catId": "tech", "serviceId": "design", "priceFrom": 20, "active": True, "areas": ["مسقط"]}, {"catId": "tech", "serviceId": "networks", "priceFrom": 25, "active": True, "areas": ["مسقط"]}],
    },
]


SQL_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SQL_COLUMN_DEFINITION_RE = re.compile(
    r"^(?:TEXT|INTEGER|REAL)(?:\s+NOT\s+NULL)?(?:\s+DEFAULT\s+(?:CURRENT_TIMESTAMP|'[^']*'|-?\d+(?:\.\d+)?))?$",
    re.IGNORECASE,
)


def trusted_sql_identifier(value):
    value = str(value or "")
    if not SQL_IDENTIFIER_RE.fullmatch(value):
        raise ValueError("invalid_sql_identifier")
    return f'"{value}"'


def ensure_column(con, table, column, definition):
    table_sql = trusted_sql_identifier(table)
    column_sql = trusted_sql_identifier(column)
    definition = str(definition or "TEXT").strip()
    if not SQL_COLUMN_DEFINITION_RE.fullmatch(definition):
        raise ValueError("invalid_sql_column_definition")
    # SQLite does not parameterize identifiers; both identifiers are regex-validated above.
    columns = [r["name"] for r in con.execute(f"PRAGMA table_info({table_sql})")]  # nosec B608
    if column not in columns:
        original_definition = definition or "TEXT"
        effective_definition = original_definition
        if "current_timestamp" in original_definition.lower() or "datetime('now')" in original_definition.lower():
            effective_definition = re.sub(
                r"\bDEFAULT\s+CURRENT_TIMESTAMP\b|\bDEFAULT\s+DATETIME\('now'\)",
                "DEFAULT ''",
                original_definition,
                flags=re.IGNORECASE,
            )
            if "DEFAULT" not in effective_definition.upper():
                effective_definition = effective_definition.strip() + " DEFAULT ''"
        # The identifiers and the complete column definition use strict allowlists.
        con.execute(f"ALTER TABLE {table_sql} ADD COLUMN {column_sql} {effective_definition}")  # nosec B608
        if "current_timestamp" in original_definition.lower() or "datetime('now')" in original_definition.lower():
            con.execute(
                f"UPDATE {table_sql} SET {column_sql}=CURRENT_TIMESTAMP WHERE {column_sql} IS NULL OR {column_sql}=''"  # nosec B608
            )


def create_pre_migration_backup(migration_key=MIGRATION_KEY):
    """Create one SQLite snapshot immediately before an additive migration."""
    if not DB_PATH.exists() or DB_PATH.stat().st_size == 0:
        return None
    source = sqlite3.connect(DB_PATH, timeout=12)
    source.row_factory = sqlite3.Row
    try:
        try:
            migrated = source.execute(
                "SELECT 1 FROM settings WHERE key=? LIMIT 1", (migration_key,)
            ).fetchone()
        except sqlite3.OperationalError:
            migrated = None
        if migrated:
            return None
        backup_dir = Path(os.environ.get("KHADAMATI_BACKUP_DIR") or (DB_PATH.parent / "backups"))
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        safe_key = re.sub(r"[^a-z0-9_-]+", "-", migration_key.lower()).strip("-")
        target = backup_dir / f"khadamati-pre-{safe_key}-{stamp}.sqlite3"
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            destination.close()
        return target
    finally:
        source.close()


def init_db():
    backup_paths = [
        create_pre_migration_backup(MIGRATION_KEY),
        create_pre_migration_backup(TRUST_MIGRATION_KEY),
        create_pre_migration_backup(QUALITY_MIGRATION_KEY),
        create_pre_migration_backup(PLATFORM_MIGRATION_KEY),
    ]
    for backup_path in dict.fromkeys(path for path in backup_paths if path):
        log_event("database.pre_migration_backup", file=backup_path.name)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    with db() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS admin_users(
              id TEXT PRIMARY KEY, name TEXT NOT NULL, code_hash TEXT NOT NULL, role TEXT NOT NULL,
              permissions TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS categories(
              id TEXT PRIMARY KEY, icon TEXT, ar TEXT NOT NULL, en TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS services(
              id TEXT NOT NULL, category_id TEXT NOT NULL, icon TEXT, ar TEXT NOT NULL, en TEXT NOT NULL,
              active INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(id, category_id)
            );
            CREATE TABLE IF NOT EXISTS providers(
              id TEXT PRIMARY KEY, name TEXT NOT NULL, phone TEXT NOT NULL,
              email TEXT DEFAULT '', age INTEGER NOT NULL DEFAULT 0, nationality TEXT DEFAULT '',
              gov TEXT, wilayah TEXT, governorates TEXT DEFAULT '[]',
              areas TEXT, bio TEXT, hours TEXT, status TEXT, active INTEGER, verified INTEGER, featured INTEGER,
              package_id TEXT, rating REAL, reviews INTEGER, admin_note TEXT DEFAULT '', image_path TEXT DEFAULT '', card_image TEXT DEFAULT '',
              pin_hash TEXT DEFAULT '', services TEXT NOT NULL, work_images TEXT DEFAULT '[]', documents TEXT DEFAULT '[]',
              quality_score INTEGER DEFAULT 60, response_score INTEGER DEFAULT 70,
              quality_breakdown TEXT DEFAULT '{}', subscription_until TEXT DEFAULT '',
              subscription_start TEXT DEFAULT '', provider_type TEXT DEFAULT 'individual', company_name TEXT DEFAULT '', company_id TEXT DEFAULT '',
              commercial_no TEXT DEFAULT '', verification_expiry TEXT DEFAULT '', commercial_expiry TEXT DEFAULT '', license_expiry TEXT DEFAULT '',
              latitude REAL, longitude REAL, location_updated_at TEXT DEFAULT '',
              map_visible INTEGER NOT NULL DEFAULT 1, primary_service_id TEXT DEFAULT '',
              before_after TEXT DEFAULT '[]', intro_video_url TEXT DEFAULT '',
              stats TEXT NOT NULL DEFAULT '{"views":0,"whatsapp":0,"calls":0}', created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS provider_requests(
              id TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS leads(
              id TEXT PRIMARY KEY, provider_id TEXT, kind TEXT, customer_name TEXT, phone TEXT, note TEXT,
              service_value TEXT DEFAULT '', service_name TEXT DEFAULT '', gov TEXT DEFAULT '', status TEXT DEFAULT 'open',
              created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS finance(
              id TEXT PRIMARY KEY, kind TEXT, amount REAL, source TEXT, note TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS whatsapp_logs(
              id TEXT PRIMARY KEY, target TEXT, status TEXT, detail TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS reviews(
              id TEXT PRIMARY KEY, provider_id TEXT NOT NULL, rating INTEGER NOT NULL, customer_name TEXT,
              phone TEXT, comment TEXT, dimensions TEXT DEFAULT '{}',
              approved INTEGER NOT NULL DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS complaints(
              id TEXT PRIMARY KEY, provider_id TEXT, customer_name TEXT, phone TEXT, reason TEXT, detail TEXT,
              status TEXT NOT NULL DEFAULT 'open', priority TEXT NOT NULL DEFAULT 'normal',
              resolution TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS packages(
              id TEXT PRIMARY KEY, ar TEXT NOT NULL, en TEXT NOT NULL, price REAL NOT NULL DEFAULT 0,
              duration_days INTEGER NOT NULL DEFAULT 30, featured_boost INTEGER NOT NULL DEFAULT 0,
              max_services INTEGER NOT NULL DEFAULT 3, max_categories INTEGER NOT NULL DEFAULT 1,
              max_images INTEGER NOT NULL DEFAULT 5, active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS subscriptions(
              id TEXT PRIMARY KEY, provider_id TEXT NOT NULL, package_id TEXT NOT NULL, amount REAL NOT NULL DEFAULT 0,
              status TEXT NOT NULL DEFAULT 'pending', start_date TEXT, end_date TEXT, note TEXT,
              created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS payments(
              id TEXT PRIMARY KEY, provider_id TEXT, subscription_id TEXT, kind TEXT NOT NULL DEFAULT 'revenue',
              amount REAL NOT NULL DEFAULT 0, method TEXT DEFAULT 'manual', status TEXT NOT NULL DEFAULT 'paid',
              note TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS audit_logs(
              id TEXT PRIMARY KEY, actor_kind TEXT, actor_id TEXT, action TEXT NOT NULL, target TEXT,
              detail TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS app_users(
              id TEXT PRIMARY KEY, phone TEXT NOT NULL UNIQUE, name TEXT DEFAULT '', pin_hash TEXT DEFAULT '',
              email TEXT DEFAULT '', age INTEGER NOT NULL DEFAULT 0, nationality TEXT DEFAULT '',
              gov TEXT DEFAULT '', wilayah TEXT DEFAULT '', avatar TEXT DEFAULT '', latitude REAL, longitude REAL,
              status TEXT NOT NULL DEFAULT 'active', failed_attempts INTEGER NOT NULL DEFAULT 0,
              locked_until TEXT DEFAULT '', first_login TEXT DEFAULT CURRENT_TIMESTAMP,
              last_login TEXT DEFAULT CURRENT_TIMESTAMP, login_count INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS auth_sessions(
              id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE, session_json TEXT NOT NULL,
              expires_at TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS customer_requests(
              id TEXT PRIMARY KEY, user_id TEXT DEFAULT '', customer_name TEXT DEFAULT '', phone TEXT DEFAULT '',
              service_value TEXT NOT NULL, service_name TEXT DEFAULT '', gov TEXT DEFAULT '', wilayah TEXT DEFAULT '',
              latitude REAL, longitude REAL, urgency TEXT DEFAULT 'normal', schedule_type TEXT DEFAULT 'flexible',
              requested_at TEXT DEFAULT '', budget_min REAL DEFAULT 0, budget_max REAL DEFAULT 0,
              location_text TEXT DEFAULT '', note TEXT DEFAULT '', images TEXT DEFAULT '[]',
              status TEXT NOT NULL DEFAULT 'matching', accepted_provider_id TEXT DEFAULT '',
              matching_provider_ids TEXT DEFAULT '[]', declined_provider_ids TEXT DEFAULT '[]',
              offers TEXT DEFAULT '[]', messages TEXT DEFAULT '[]', arrival TEXT DEFAULT '{}',
              contact_consent TEXT DEFAULT '{}',
              waitlisted INTEGER NOT NULL DEFAULT 0,
              offers_open INTEGER NOT NULL DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS request_provider_suggestions(
              id TEXT PRIMARY KEY, request_id TEXT NOT NULL, provider_id TEXT NOT NULL,
              suggested_by_user_id TEXT NOT NULL, preset_key TEXT NOT NULL DEFAULT '',
              comment TEXT DEFAULT '', status TEXT NOT NULL DEFAULT 'active',
              report_reason TEXT DEFAULT '', selected_at TEXT DEFAULT '', reported_at TEXT DEFAULT '',
              deleted_at TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
              UNIQUE(request_id,provider_id)
            );
            CREATE TABLE IF NOT EXISTS app_notifications(
              id TEXT PRIMARY KEY, target_kind TEXT NOT NULL, target_id TEXT DEFAULT '', type TEXT DEFAULT 'general',
              title TEXT NOT NULL, message TEXT DEFAULT '', related_id TEXT DEFAULT '',
              priority TEXT DEFAULT 'normal', action_text TEXT DEFAULT '', action_route TEXT DEFAULT '',
              is_read INTEGER NOT NULL DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS advertisements(
              id TEXT PRIMARY KEY, image_path TEXT NOT NULL, advertiser TEXT DEFAULT '', phone TEXT DEFAULT '',
              amount REAL DEFAULT 0, title TEXT DEFAULT '', body TEXT DEFAULT '', starts_at TEXT DEFAULT '',
              ends_at TEXT DEFAULT '', active INTEGER NOT NULL DEFAULT 1, deleted_at TEXT DEFAULT '',
              created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS password_recoveries(
              id TEXT PRIMARY KEY, account_kind TEXT NOT NULL, account_id TEXT DEFAULT '', phone TEXT NOT NULL,
              code_hash TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, expires_at TEXT NOT NULL,
              verified_at TEXT DEFAULT '', reset_token_hash TEXT DEFAULT '',
              used_at TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS admin_email_challenges(
              id TEXT PRIMARY KEY, admin_id TEXT NOT NULL, code_hash TEXT NOT NULL,
              request_key TEXT DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0,
              expires_at TEXT NOT NULL, used_at TEXT DEFAULT '',
              created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS push_subscriptions(
              id TEXT PRIMARY KEY, target_kind TEXT NOT NULL, target_id TEXT DEFAULT '', endpoint TEXT NOT NULL UNIQUE,
              subscription_json TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
              last_success_at TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS policy_acceptances(
              id TEXT PRIMARY KEY, user_id TEXT DEFAULT '', phone TEXT DEFAULT '', policy_version TEXT NOT NULL,
              accepted_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS login_failures(
              account_kind TEXT NOT NULL, account_id TEXT NOT NULL, phone TEXT DEFAULT '',
              attempts INTEGER NOT NULL DEFAULT 0, last_attempt TEXT DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY(account_kind, account_id)
            );
            CREATE TABLE IF NOT EXISTS subscription_events(
              id TEXT PRIMARY KEY, subscription_id TEXT NOT NULL, event_type TEXT NOT NULL,
              from_state TEXT DEFAULT '', to_state TEXT DEFAULT '', actor TEXT DEFAULT 'system',
              detail TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS foundation_claims(
              id TEXT PRIMARY KEY, provider_id TEXT NOT NULL UNIQUE, phone TEXT DEFAULT '',
              commercial_no TEXT DEFAULT '', fingerprint TEXT NOT NULL UNIQUE,
              subscription_id TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS contact_consents(
              id TEXT PRIMARY KEY, request_id TEXT NOT NULL, user_id TEXT NOT NULL,
              provider_id TEXT NOT NULL, channel TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'revoked',
              granted_at TEXT DEFAULT '', expires_at TEXT DEFAULT '', revoked_at TEXT DEFAULT '',
              created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
              UNIQUE(request_id,provider_id,channel)
            );
            CREATE TABLE IF NOT EXISTS request_dispatches(
              id TEXT PRIMARY KEY, request_id TEXT NOT NULL, provider_id TEXT NOT NULL,
              rank INTEGER NOT NULL DEFAULT 0, score REAL NOT NULL DEFAULT 0,
              score_breakdown TEXT DEFAULT '{}', wave INTEGER NOT NULL DEFAULT 1,
              release_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'scheduled',
              notified_at TEXT DEFAULT '', opened_at TEXT DEFAULT '', offered_at TEXT DEFAULT '',
              accepted_at TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(request_id,provider_id)
            );
            CREATE TABLE IF NOT EXISTS invoices(
              id TEXT PRIMARY KEY, payment_id TEXT NOT NULL UNIQUE, subscription_id TEXT NOT NULL,
              provider_id TEXT NOT NULL, number TEXT NOT NULL UNIQUE, currency TEXT NOT NULL DEFAULT 'OMR',
              subtotal REAL NOT NULL DEFAULT 0, total REAL NOT NULL DEFAULT 0,
              status TEXT NOT NULL DEFAULT 'issued', issued_at TEXT NOT NULL, paid_at TEXT DEFAULT '',
              metadata TEXT DEFAULT '{}', created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS coupons(
              id TEXT PRIMARY KEY, code TEXT NOT NULL UNIQUE, name_ar TEXT DEFAULT '', name_en TEXT DEFAULT '',
              discount_type TEXT NOT NULL DEFAULT 'fixed', discount_value REAL NOT NULL DEFAULT 0,
              applies_to TEXT DEFAULT '[]', starts_at TEXT DEFAULT '', ends_at TEXT DEFAULT '',
              max_uses INTEGER NOT NULL DEFAULT 0, uses_count INTEGER NOT NULL DEFAULT 0,
              active INTEGER NOT NULL DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS coupon_redemptions(
              id TEXT PRIMARY KEY, coupon_id TEXT NOT NULL, provider_id TEXT NOT NULL,
              subscription_id TEXT DEFAULT '', amount REAL NOT NULL DEFAULT 0,
              created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(coupon_id,provider_id,subscription_id)
            );
            CREATE TABLE IF NOT EXISTS campaigns(
              id TEXT PRIMARY KEY, name_ar TEXT NOT NULL, name_en TEXT DEFAULT '',
              kind TEXT NOT NULL DEFAULT 'subscription', starts_at TEXT DEFAULT '', ends_at TEXT DEFAULT '',
              budget REAL NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'draft',
              rules TEXT DEFAULT '{}', created_at TEXT DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS provider_promotions(
              id TEXT PRIMARY KEY, provider_id TEXT NOT NULL, campaign_id TEXT DEFAULT '',
              kind TEXT NOT NULL DEFAULT 'featured', area TEXT DEFAULT '', service_value TEXT DEFAULT '',
              starts_at TEXT DEFAULT '', ends_at TEXT DEFAULT '', amount REAL NOT NULL DEFAULT 0,
              status TEXT NOT NULL DEFAULT 'pending_payment', created_at TEXT DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS provider_team_members(
              id TEXT PRIMARY KEY, provider_id TEXT NOT NULL, name TEXT NOT NULL, phone TEXT NOT NULL,
              role TEXT NOT NULL DEFAULT 'provider_staff', pin_hash TEXT NOT NULL DEFAULT '',
              permissions TEXT DEFAULT '[]', active INTEGER NOT NULL DEFAULT 1,
              created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
              UNIQUE(provider_id,phone)
            );
            CREATE TABLE IF NOT EXISTS provider_branches(
              id TEXT PRIMARY KEY, provider_id TEXT NOT NULL, name TEXT NOT NULL,
              gov TEXT DEFAULT '', wilayah TEXT DEFAULT '', address TEXT DEFAULT '',
              latitude REAL, longitude REAL, phone TEXT DEFAULT '', active INTEGER NOT NULL DEFAULT 1,
              created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS otp_challenges(
              id TEXT PRIMARY KEY, phone TEXT NOT NULL, purpose TEXT NOT NULL,
              target_kind TEXT NOT NULL DEFAULT 'user', code_hash TEXT NOT NULL,
              attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 5,
              expires_at TEXT NOT NULL, verified_at TEXT DEFAULT '', delivery_status TEXT DEFAULT 'pending',
              created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS webhook_events(
              id TEXT PRIMARY KEY, provider TEXT NOT NULL, event_id TEXT NOT NULL UNIQUE,
              signature_valid INTEGER NOT NULL DEFAULT 0, payload_hash TEXT NOT NULL,
              processed INTEGER NOT NULL DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_requests_status ON customer_requests(status, created_at);
            CREATE INDEX IF NOT EXISTS idx_request_suggestions_request ON request_provider_suggestions(request_id,status,created_at);
            CREATE INDEX IF NOT EXISTS idx_request_suggestions_user ON request_provider_suggestions(suggested_by_user_id,created_at);
            CREATE INDEX IF NOT EXISTS idx_notifications_target ON app_notifications(target_kind, target_id, is_read);
            CREATE INDEX IF NOT EXISTS idx_sessions_hash ON auth_sessions(token_hash, expires_at);
            CREATE INDEX IF NOT EXISTS idx_dispatch_release ON request_dispatches(status,release_at,wave);
            CREATE INDEX IF NOT EXISTS idx_dispatch_provider ON request_dispatches(provider_id,status,notified_at);
            CREATE INDEX IF NOT EXISTS idx_consent_lookup ON contact_consents(request_id,provider_id,channel,status);
            CREATE INDEX IF NOT EXISTS idx_subscription_provider ON subscriptions(provider_id,status,end_date);
            CREATE INDEX IF NOT EXISTS idx_payment_subscription ON payments(subscription_id,status);
            CREATE INDEX IF NOT EXISTS idx_otp_phone ON otp_challenges(phone,purpose,created_at);
            CREATE INDEX IF NOT EXISTS idx_admin_email_challenge
              ON admin_email_challenges(admin_id,expires_at,used_at);
            """
        )
        ensure_column(con, "providers", "image_path", "TEXT DEFAULT ''")
        ensure_column(con, "providers", "card_image", "TEXT DEFAULT ''")
        ensure_column(con, "providers", "pin_hash", "TEXT DEFAULT ''")
        ensure_column(con, "providers", "work_images", "TEXT DEFAULT '[]'")
        ensure_column(con, "providers", "documents", "TEXT DEFAULT '[]'")
        ensure_column(con, "providers", "quality_score", "INTEGER DEFAULT 60")
        ensure_column(con, "providers", "response_score", "INTEGER DEFAULT 70")
        ensure_column(con, "providers", "quality_breakdown", "TEXT DEFAULT '{}'")
        ensure_column(con, "providers", "subscription_until", "TEXT DEFAULT ''")
        ensure_column(con, "providers", "subscription_start", "TEXT DEFAULT ''")
        ensure_column(con, "providers", "provider_type", "TEXT DEFAULT 'individual'")
        ensure_column(con, "providers", "company_name", "TEXT DEFAULT ''")
        ensure_column(con, "providers", "company_id", "TEXT DEFAULT ''")
        ensure_column(con, "providers", "commercial_no", "TEXT DEFAULT ''")
        ensure_column(con, "providers", "verification_expiry", "TEXT DEFAULT ''")
        ensure_column(con, "providers", "commercial_expiry", "TEXT DEFAULT ''")
        ensure_column(con, "providers", "license_expiry", "TEXT DEFAULT ''")
        ensure_column(con, "providers", "latitude", "REAL")
        ensure_column(con, "providers", "longitude", "REAL")
        ensure_column(con, "providers", "location_updated_at", "TEXT DEFAULT ''")
        ensure_column(con, "providers", "map_visible", "INTEGER NOT NULL DEFAULT 1")
        ensure_column(con, "providers", "primary_service_id", "TEXT DEFAULT ''")
        ensure_column(con, "providers", "before_after", "TEXT DEFAULT '[]'")
        ensure_column(con, "providers", "intro_video_url", "TEXT DEFAULT ''")
        ensure_column(con, "providers", "listing_enabled", "INTEGER NOT NULL DEFAULT 1")
        ensure_column(con, "providers", "request_enabled", "INTEGER NOT NULL DEFAULT 1")
        ensure_column(con, "providers", "subscription_state", "TEXT DEFAULT 'active'")
        ensure_column(con, "providers", "availability", "TEXT DEFAULT '{}'")
        ensure_column(con, "providers", "response_minutes", "INTEGER NOT NULL DEFAULT 30")
        ensure_column(con, "providers", "completed_jobs", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(con, "providers", "quote_templates", "TEXT DEFAULT '[]'")
        ensure_column(con, "providers", "updated_at", "TEXT DEFAULT ''")
        ensure_column(con, "providers", "gender", "TEXT DEFAULT 'not_specified'")
        ensure_column(con, "providers", "email", "TEXT DEFAULT ''")
        ensure_column(con, "providers", "age", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(con, "providers", "nationality", "TEXT DEFAULT ''")
        ensure_column(con, "providers", "governorates", "TEXT DEFAULT '[]'")
        ensure_column(con, "providers", "location_sharing_expires_at", "TEXT DEFAULT ''")
        ensure_column(con, "providers", "deleted_at", "TEXT DEFAULT ''")
        ensure_column(con, "providers", "delete_reason", "TEXT DEFAULT ''")
        ensure_column(con, "providers", "hidden_history", "TEXT DEFAULT '[]'")
        ensure_column(con, "app_users", "location_updated_at", "TEXT DEFAULT ''")
        ensure_column(con, "app_users", "updated_at", "TEXT DEFAULT ''")
        ensure_column(con, "app_users", "gender", "TEXT DEFAULT 'not_specified'")
        ensure_column(con, "app_users", "email", "TEXT DEFAULT ''")
        ensure_column(con, "app_users", "age", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(con, "app_users", "nationality", "TEXT DEFAULT ''")
        ensure_column(con, "auth_sessions", "refresh_hash", "TEXT DEFAULT ''")
        ensure_column(con, "auth_sessions", "access_expires_at", "TEXT DEFAULT ''")
        ensure_column(con, "auth_sessions", "device_id", "TEXT DEFAULT ''")
        ensure_column(con, "auth_sessions", "last_used_at", "TEXT DEFAULT ''")
        ensure_column(con, "auth_sessions", "refreshed_at", "TEXT DEFAULT ''")
        ensure_column(con, "password_recoveries", "verified_at", "TEXT DEFAULT ''")
        ensure_column(con, "password_recoveries", "reset_token_hash", "TEXT DEFAULT ''")
        con.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_auth_refresh_hash
            ON auth_sessions(refresh_hash) WHERE refresh_hash!=''"""
        )
        ensure_column(con, "customer_requests", "offers", "TEXT DEFAULT '[]'")
        ensure_column(con, "customer_requests", "messages", "TEXT DEFAULT '[]'")
        ensure_column(con, "customer_requests", "arrival", "TEXT DEFAULT '{}'")
        ensure_column(con, "customer_requests", "contact_consent", "TEXT DEFAULT '{}'")
        ensure_column(con, "customer_requests", "waitlisted", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(con, "customer_requests", "marketplace_status", "TEXT DEFAULT 'pending'")
        ensure_column(con, "customer_requests", "dispatch_started_at", "TEXT DEFAULT ''")
        ensure_column(con, "customer_requests", "expansion_at", "TEXT DEFAULT ''")
        ensure_column(con, "customer_requests", "ranking_version", "TEXT DEFAULT ''")
        install_workflow_schema(con)
        ensure_column(con, "packages", "currency", "TEXT NOT NULL DEFAULT 'OMR'")
        ensure_column(con, "packages", "max_categories", "INTEGER NOT NULL DEFAULT 1")
        ensure_column(con, "packages", "max_wilayats", "INTEGER NOT NULL DEFAULT 5")
        ensure_column(con, "packages", "max_governorates", "INTEGER NOT NULL DEFAULT 1")
        ensure_column(con, "packages", "monthly_response_limit", "INTEGER NOT NULL DEFAULT 30")
        ensure_column(con, "packages", "lead_delay_minutes", "INTEGER NOT NULL DEFAULT 15")
        ensure_column(con, "packages", "lead_delay_seconds", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(con, "packages", "max_team_members", "INTEGER NOT NULL DEFAULT 1")
        ensure_column(con, "packages", "max_branches", "INTEGER NOT NULL DEFAULT 1")
        ensure_column(con, "packages", "shared_inbox", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(con, "packages", "advanced_reports", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(con, "packages", "badge_ar", "TEXT DEFAULT ''")
        ensure_column(con, "packages", "badge_en", "TEXT DEFAULT ''")
        ensure_column(con, "packages", "foundation_once", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(con, "packages", "verified_required", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(con, "packages", "legacy", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(con, "packages", "account_scope", "TEXT NOT NULL DEFAULT 'all'")
        ensure_column(con, "packages", "community_package_quota", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(con, "packages", "community_package_days", "INTEGER NOT NULL DEFAULT 30")
        ensure_column(con, "packages", "entitlements", "TEXT DEFAULT '{}'")
        ensure_column(con, "subscriptions", "currency", "TEXT NOT NULL DEFAULT 'OMR'")
        ensure_column(con, "subscriptions", "grace_days", "INTEGER NOT NULL DEFAULT 14")
        ensure_column(con, "subscriptions", "renewal_package_id", "TEXT DEFAULT ''")
        ensure_column(con, "subscriptions", "previous_package_id", "TEXT DEFAULT ''")
        ensure_column(con, "subscriptions", "proration_amount", "REAL NOT NULL DEFAULT 0")
        ensure_column(con, "subscriptions", "credit_amount", "REAL NOT NULL DEFAULT 0")
        ensure_column(con, "subscriptions", "activated_at", "TEXT DEFAULT ''")
        ensure_column(con, "subscriptions", "grace_until", "TEXT DEFAULT ''")
        ensure_column(con, "subscriptions", "cancelled_at", "TEXT DEFAULT ''")
        ensure_column(con, "subscriptions", "refunded_at", "TEXT DEFAULT ''")
        ensure_column(con, "subscriptions", "payment_id", "TEXT DEFAULT ''")
        ensure_column(con, "subscriptions", "auto_renew", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(con, "subscriptions", "legacy_package_id", "TEXT DEFAULT ''")
        ensure_column(con, "subscriptions", "metadata", "TEXT DEFAULT '{}'")
        ensure_column(con, "subscriptions", "updated_at", "TEXT DEFAULT CURRENT_TIMESTAMP")
        ensure_column(con, "payments", "currency", "TEXT NOT NULL DEFAULT 'OMR'")
        ensure_column(con, "payments", "external_id", "TEXT DEFAULT ''")
        ensure_column(con, "payments", "gateway", "TEXT DEFAULT 'manual'")
        ensure_column(con, "payments", "failure_code", "TEXT DEFAULT ''")
        ensure_column(con, "payments", "verified_at", "TEXT DEFAULT ''")
        ensure_column(con, "payments", "refunded_at", "TEXT DEFAULT ''")
        ensure_column(con, "payments", "metadata", "TEXT DEFAULT '{}'")
        ensure_column(con, "payments", "updated_at", "TEXT DEFAULT CURRENT_TIMESTAMP")
        ensure_column(con, "policy_acceptances", "document_types", "TEXT DEFAULT '[]'")
        ensure_column(con, "policy_acceptances", "language", "TEXT DEFAULT 'ar'")
        ensure_column(con, "policy_acceptances", "withdrawn_at", "TEXT DEFAULT ''")
        ensure_column(con, "policy_acceptances", "metadata", "TEXT DEFAULT '{}'")
        ensure_column(con, "leads", "service_value", "TEXT DEFAULT ''")
        ensure_column(con, "leads", "service_name", "TEXT DEFAULT ''")
        ensure_column(con, "leads", "gov", "TEXT DEFAULT ''")
        ensure_column(con, "leads", "status", "TEXT DEFAULT 'open'")
        ensure_column(con, "reviews", "request_id", "TEXT DEFAULT ''")
        ensure_column(con, "reviews", "user_id", "TEXT DEFAULT ''")
        ensure_column(con, "reviews", "dimensions", "TEXT DEFAULT '{}'")
        ensure_column(con, "reviews", "deleted_at", "TEXT DEFAULT ''")
        ensure_column(con, "reviews", "moderation_reason", "TEXT DEFAULT ''")
        ensure_column(con, "complaints", "request_id", "TEXT DEFAULT ''")
        ensure_column(con, "complaints", "user_id", "TEXT DEFAULT ''")
        ensure_column(con, "categories", "deleted_at", "TEXT DEFAULT ''")
        ensure_column(con, "services", "deleted_at", "TEXT DEFAULT ''")
        install_reward_schema(con)
        install_location_schema(con)
        install_community_schema(con)
        install_trust_schema(con)
        install_security_schema(con)
        install_growth_schema(con)
        install_platform_schema(con)
        ensure_column(con, "customer_requests", "organization_id", "TEXT DEFAULT ''")
        ensure_column(con, "customer_requests", "organization_location_id", "TEXT DEFAULT ''")
        ensure_column(con, "customer_requests", "requested_by_member_id", "TEXT DEFAULT ''")
        for c in SEED_CATEGORIES:
            con.execute(
                "INSERT OR IGNORE INTO categories(id,icon,ar,en,active) VALUES(?,?,?,?,?)",
                (c["id"], c["icon"], c["ar"], c["en"], c["active"]),
            )
            con.execute(
                "UPDATE categories SET icon=?, ar=?, en=? WHERE id=?",
                (c["icon"], c["ar"], c["en"], c["id"]),
            )
            for s in c["services"]:
                con.execute(
                    "INSERT OR IGNORE INTO services(id,category_id,icon,ar,en,active) VALUES(?,?,?,?,?,?)",
                    (s["id"], c["id"], s["icon"], s["ar"], s["en"], s["active"]),
                )
                con.execute(
                    "UPDATE services SET icon=?, ar=?, en=? WHERE id=? AND category_id=?",
                    (s["icon"], s["ar"], s["en"], s["id"], c["id"]),
                )
        if SAMPLE_DATA_ENABLED:
            for p in SEED_PROVIDERS:
                con.execute(
                    """INSERT OR IGNORE INTO providers(id,name,phone,gov,wilayah,areas,bio,hours,status,active,verified,featured,
                    package_id,rating,reviews,services,subscription_until,subscription_start,provider_type,company_name,stats,pin_hash)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        p["id"], p["name"], p["phone"], p["gov"], p["wilayah"], jdump(p["areas"]), p["bio"], p["hours"],
                        p.get("status", "available"), p.get("active", 1), p.get("verified", 0), p.get("featured", 0),
                        p.get("package_id", "intro"), p.get("rating", 0), p.get("reviews", 0), jdump(p.get("services", [])),
                        p.get("subscription_until", ""), p.get("subscription_start", ""),
                        p.get("provider_type", "individual"), p.get("company_name", ""),
                        jdump(p.get("stats", {"views": 0, "whatsapp": 0,"calls": 0})),
                        "",
                    ),
                )
        for p in SEED_PROVIDERS:
            con.execute(
                "UPDATE providers SET pin_hash='' WHERE id=? AND pin_hash IN (?,?)",
                (p["id"], hash_secret("1234"), hash_secret(str(p.get("phone", ""))[-4:])),
            )
        verification_backfill = ProviderVerificationService(con).backfill()
        con.execute(
            """INSERT INTO settings(key,value) VALUES(?,?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            (
                TRUST_MIGRATION_KEY,
                jdump(
                    {
                        "installedAt": datetime.now(UTC).isoformat(),
                        "verificationCasesCreated": verification_backfill,
                    }
                ),
            ),
        )
        con.execute(
            """INSERT INTO settings(key,value) VALUES(?,?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            (
                PLATFORM_MIGRATION_KEY,
                jdump(
                    {
                        "installedAt": datetime.now(UTC).isoformat(),
                        "version": 1,
                        "rollback": "schema additions are additive; disable feature flags before code rollback",
                    }
                ),
            ),
        )
        for pkg in [
            ("intro", "مجانية", "Free launch", 0, 365, 0, 1, 5, 1),
            ("individual_6m", "مزود 6 أشهر", "Provider 6 months", 10, 183, 10, 4, 5, 1),
            ("individual_year", "مزود سنة", "Provider yearly", 15, 365, 20, 7, 5, 1),
            ("company_year", "شركة سنوية", "Company yearly", 50, 365, 45, 5, 15, 1),
            ("intro_90", "تعريفية", "Introductory", 0, 90, 0, 1, 5, 1),
            ("basic_90", "أساسية", "Basic", 6, 90, 0, 1, 5, 1),
            ("active_90", "نشطة", "Active", 12, 90, 12, 1, 5, 1),
            ("featured_90", "بارزة", "Featured", 20, 90, 40, 1, 10, 1),
            ("company_90", "شركة", "Company", 30, 90, 25, 5, 15, 1),
        ]:
            con.execute(
                """INSERT OR IGNORE INTO packages(
                id,ar,en,price,duration_days,featured_boost,max_services,max_images,active
                ) VALUES(?,?,?,?,?,?,?,?,?)""",
                pkg,
            )
        con.execute("UPDATE packages SET max_services=5,max_images=15 WHERE id='company_year' AND max_services>5")
        migration_summary = run_subscription_migration_v1(con)
        log_event("database.subscription_migration", summary=migration_summary)
        if SAMPLE_DATA_ENABLED and con.execute("SELECT COUNT(*) n FROM reviews").fetchone()["n"] == 0:
            con.execute(
                """INSERT INTO reviews(
                id,provider_id,rating,customer_name,phone,comment,approved,created_at)
                VALUES(?,?,?,?,?,?,1,CURRENT_TIMESTAMP)""",
                ("rev_seed_1", "p1", 5, "عميل موثق", "", "خدمة سريعة ومرتبة"),
            )
            con.execute(
                """INSERT INTO reviews(
                id,provider_id,rating,customer_name,phone,comment,approved,created_at)
                VALUES(?,?,?,?,?,?,1,CURRENT_TIMESTAMP)""",
                ("rev_seed_2", "p2", 5, "عميلة", "", "التنظيف ممتاز والموعد واضح"),
            )
        if not con.execute(
            "SELECT 1 FROM settings WHERE key=?",
            (QUALITY_MIGRATION_KEY,),
        ).fetchone():
            for provider_row in con.execute("SELECT id FROM providers"):
                recompute_provider_quality(con, provider_row["id"])
            con.execute(
                "INSERT INTO settings(key,value) VALUES(?,?)",
                (
                    QUALITY_MIGRATION_KEY,
                    jdump(
                        {
                            "installedAt": datetime.now(UTC).isoformat(),
                            "version": 1,
                        }
                    ),
                ),
            )
        con.execute(
            "INSERT OR IGNORE INTO settings VALUES('platform', ?)",
            (jdump({
                "nameAr": "خدماتي",
                "nameEn": "Khadamati App",
                "supportEmail": SUPPORT_EMAIL,
                "policyVersion": POLICY_VERSION,
                "currency": OMR,
                "adminWhatsapp": "",
                "monthlyGoal": 500,
                "acceptProviders": True,
                "subscriptionsEnabled": False,
                "paymentGatewayEnabled": False,
                "uiMode": "simple",
                "showHeroImage": True,
                "showQuickActions": True,
                "showCategories": True,
                "showPopularServices": True,
                "showTopProviders": True,
                "showProviderShortcut": True,
                "showAdminShortcut": False,
                "showQualityBadge": True,
                "maxHomeCategories": 6,
                "maxPopularServices": 4,
                "maxHomeProviders": 2,
                "loyaltyEnabled": True,
                "loyaltyCampaignActive": False,
                "loyaltyTargetPoints": 100,
                "loyaltyTargetRequests": 8,
                "loyaltyCycleMode": "cap",
                "loyaltyCampaignAr": "مكافأة خدماتي",
                "loyaltyCampaignEn": "Khadamati reward",
                "loyaltyCampaignNoteAr": "تحدد الإدارة تفاصيل المكافأة عند تفعيل الحملة.",
                "loyaltyCampaignNoteEn": "Reward details are set by management when the campaign is active.",
                "communityEnabled": True,
                "communityPackagesEnabled": True,
                "communityBoardEnabled": True,
                "communityProviderOffersEnabled": True,
                "communityUserRecommendationsEnabled": True,
                "communityModerationRequired": False,
                "communityWantedExpiryDays": 30,
                "communityPackageExpiryDays": 30,
                "communityFirstPackageFreeDays": 30,
                "communityRenewalFee": 2,
            }),),
        )
        platform_row = con.execute("SELECT value FROM settings WHERE key='platform'").fetchone()
        platform_settings = jload(platform_row["value"], {}) if platform_row else {}
        platform_settings["nameAr"] = "خدماتي"
        platform_settings["nameEn"] = "Khadamati App"
        platform_settings["supportEmail"] = SUPPORT_EMAIL
        platform_settings["policyVersion"] = POLICY_VERSION
        platform_settings["currency"] = OMR
        platform_settings.setdefault("subscriptionGraceDays", 14)
        platform_settings.setdefault("expiryThresholds", [30, 14, 7, 1, 0])
        platform_settings.setdefault("loyaltyEnabled", True)
        platform_settings.setdefault("loyaltyCampaignActive", False)
        platform_settings.setdefault("loyaltyTargetPoints", 100)
        platform_settings.setdefault("loyaltyTargetRequests", 8)
        platform_settings.setdefault("loyaltyCycleMode", "cap")
        platform_settings.setdefault("loyaltyCampaignAr", "مكافأة خدماتي")
        platform_settings.setdefault("loyaltyCampaignEn", "Khadamati reward")
        platform_settings.setdefault("loyaltyCampaignNoteAr", "تحدد الإدارة تفاصيل المكافأة عند تفعيل الحملة.")
        platform_settings.setdefault("loyaltyCampaignNoteEn", "Reward details are set by management when the campaign is active.")
        for key, value in community_settings(con).items():
            platform_settings.setdefault(key, value)
        con.execute("UPDATE settings SET value=? WHERE key='platform'", (jdump(platform_settings),))
        if con.execute("SELECT COUNT(*) n FROM admin_users").fetchone()["n"] == 0 and INITIAL_ADMIN_CODE:
            con.execute(
                """INSERT INTO admin_users(
                id,name,code_hash,role,permissions,active,created_at)
                VALUES(?,?,?,?,?,1,CURRENT_TIMESTAMP)""",
                ("admin_owner", "المالك", hash_pin(INITIAL_ADMIN_CODE), "super_admin", jdump(ALL_PERMISSIONS)),
            )
        elif con.execute("SELECT COUNT(*) n FROM admin_users").fetchone()["n"] == 0:
            log_event(
                "security.admin_seed_skipped",
                level="warning",
                reason="admin_code_not_configured",
            )
        con.execute(
            "UPDATE admin_users SET role='super_admin',permissions=? WHERE role='owner'",
            (jdump(ALL_PERMISSIONS),),
        )


def image_url(path):
    value = str(path or "")
    if not value or value.startswith(("data:", "http://", "https://", "/")):
        return value
    return f"/{value.replace(os.sep, '/')}"


def upload_filename(path):
    value = urlparse(str(path or "")).path
    if value.startswith("/uploads/") or value.startswith("/media/"):
        return value.rsplit("/", 1)[-1]
    if value.startswith("uploads/"):
        return value.split("/", 1)[-1]
    return ""


def is_private_upload(path):
    filename = upload_filename(path)
    if not filename:
        return False
    lowered = filename.lower()
    return bool(
        "-doc" in lowered
        or "-problem" in lowered
        or ("-msg_" in lowered and ("-image" in lowered or "-audio" in lowered))
        or (lowered.startswith("usr") and "-avatar" in lowered)
    )


def secure_media_url(path, ttl_seconds=MEDIA_URL_TTL_SECONDS):
    value = image_url(path)
    if not value or not is_private_upload(value):
        return value
    filename = upload_filename(value)
    expires = int(time.time()) + int(ttl_seconds)
    payload = f"{filename}:{expires}".encode("utf-8")
    signature = hmac.new(MEDIA_SIGNING_KEY.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return f"/media/{filename}?exp={expires}&sig={signature}"


def valid_media_signature(filename, expires, signature):
    if not filename or not re.fullmatch(r"[A-Za-z0-9_.-]{1,220}", filename):
        return False
    try:
        expires_at = int(expires)
    except (TypeError, ValueError):
        return False
    if expires_at < int(time.time()) or expires_at > int(time.time()) + 86_400:
        return False
    expected = hmac.new(
        MEDIA_SIGNING_KEY.encode("utf-8"),
        f"{filename}:{expires_at}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, str(signature or ""))


def upload_signature_matches(mime, blob):
    """Validate the declared data-URL type against a small, deterministic magic-byte set."""
    if not blob:
        return False
    if mime == "image/jpeg":
        return blob.startswith(b"\xff\xd8\xff")
    if mime == "image/png":
        return blob.startswith(b"\x89PNG\r\n\x1a\n")
    if mime == "image/webp":
        return len(blob) >= 12 and blob[:4] == b"RIFF" and blob[8:12] == b"WEBP"
    if mime == "application/pdf":
        return blob.startswith(b"%PDF-")
    if mime in {"audio/webm", "video/webm"}:
        return blob.startswith(b"\x1aE\xdf\xa3")
    if mime in {"audio/mp4", "audio/x-m4a", "video/mp4", "video/quicktime"}:
        return len(blob) >= 12 and blob[4:8] == b"ftyp"
    if mime == "audio/aac":
        return len(blob) > 1 and blob[0] == 0xFF and blob[1] & 0xF0 == 0xF0
    if mime == "audio/mpeg":
        return blob.startswith(b"ID3") or (len(blob) > 1 and blob[0] == 0xFF and blob[1] & 0xE0 == 0xE0)
    if mime == "audio/ogg":
        return blob.startswith(b"OggS")
    if mime in {"audio/wav", "audio/x-wav"}:
        return len(blob) >= 12 and blob[:4] == b"RIFF" and blob[8:12] == b"WAVE"
    return False


def urls(paths):
    return [image_url(p) for p in paths if p]


def row_provider(r, private=False, sign_private=False):
    d = dict(r)
    d["areas"] = jload(d["areas"], [])
    d["governorates"] = jload(d.pop("governorates", "[]"), [])
    if not d["governorates"] and d.get("gov"):
        d["governorates"] = [d["gov"]]
    d["services"] = jload(d["services"], [])
    d["stats"] = jload(d["stats"], {"views": 0, "whatsapp": 0, "calls": 0})
    d["workImages"] = jload(d.pop("work_images", "[]"), [])
    d["workImageUrls"] = urls(d["workImages"])
    d["documents"] = jload(d.pop("documents", "[]"), [])
    if private and sign_private:
        d["documents"] = [secure_media_url(item) for item in d["documents"] if item]
    before_after = jload(d.pop("before_after", "[]"), [])
    d["beforeAfter"] = [
        {
            **item,
            "before": image_url(item.get("before", "")),
            "after": image_url(item.get("after", "")),
        }
        for item in before_after
        if isinstance(item, dict)
    ]
    d["introVideoUrl"] = image_url(d.pop("intro_video_url", ""))
    for k in ("active", "verified", "featured"):
        d[k] = bool(d[k])
    d["listingEnabled"] = bool(d.pop("listing_enabled", True))
    d["requestEnabled"] = bool(d.pop("request_enabled", True))
    d["mapVisible"] = bool(d.pop("map_visible", True))
    d["primaryServiceId"] = d.pop("primary_service_id", "")
    d["subscriptionState"] = d.pop("subscription_state", "active") or "active"
    d["availability"] = jload(d.pop("availability", "{}"), {})
    d["responseMinutes"] = int(d.pop("response_minutes", 30) or 30)
    d["completedJobs"] = int(d.pop("completed_jobs", 0) or 0)
    d["age"] = int(d.get("age", 0) or 0)
    quote_templates = jload(d.pop("quote_templates", "[]"), [])
    if private:
        d["quoteTemplates"] = quote_templates if isinstance(quote_templates, list) else []
    d["packageId"] = d.pop("package_id", "")
    d["adminNote"] = d.pop("admin_note", "")
    d["imagePath"] = d.pop("image_path", "")
    d["imageUrl"] = image_url(d["imagePath"])
    d["cardImage"] = d.pop("card_image", "") or d["imageUrl"]
    d["qualityScore"] = int(d.pop("quality_score", 0) or 0)
    d["responseScore"] = int(d.pop("response_score", 0) or 0)
    d["qualityBreakdown"] = jload(d.pop("quality_breakdown", "{}"), {})
    d["subscriptionUntil"] = d.pop("subscription_until", "")
    d["subscriptionStart"] = d.pop("subscription_start", "")
    d["providerType"] = d.pop("provider_type", "individual") or "individual"
    d["companyName"] = d.pop("company_name", "")
    d["companyId"] = d.pop("company_id", "")
    d["commercialNo"] = d.pop("commercial_no", "")
    d["verificationExpiry"] = d.pop("verification_expiry", "")
    d["commercialExpiry"] = d.pop("commercial_expiry", "")
    d["licenseExpiry"] = d.pop("license_expiry", "")
    d["locationSharingExpiresAt"] = d.pop("location_sharing_expires_at", "")
    d["deletedAt"] = d.pop("deleted_at", "")
    d["deleteReason"] = d.pop("delete_reason", "")
    hidden_history = jload(d.pop("hidden_history", "[]"), [])
    if private:
        d["hiddenHistoryIds"] = hidden_history if isinstance(hidden_history, list) else []
    latitude = d.pop("latitude", None)
    longitude = d.pop("longitude", None)
    location_updated_at = d.pop("location_updated_at", "")
    sharing_expires = parse_iso(d.get("locationSharingExpiresAt"))
    if not private and (
        not d["mapVisible"]
        or (sharing_expires is not None and sharing_expires <= datetime.now(UTC))
    ):
        latitude, longitude = None, None
    d["location"] = (
        {"lat": latitude, "lng": longitude, "updatedAt": location_updated_at}
        if latitude is not None and longitude is not None
        else None
    )
    d["pinConfigured"] = bool(d.pop("pin_hash", ""))
    if not private:
        for key in (
            "phone", "email", "adminNote", "documents", "commercialNo", "companyId",
            "verificationExpiry", "commercialExpiry", "licenseExpiry", "pinConfigured",
            "locationSharingExpiresAt", "deletedAt", "deleteReason",
            "hiddenHistoryIds",
        ):
            d.pop(key, None)
    return d


def provider_request_view(payload, created_at=""):
    """Return one pending provider request with stable frontend field names."""
    item = dict(payload or {})
    description = safe_text(item.get("bio") or item.get("note"), 600)
    item["bio"] = description
    item["note"] = description
    item["pending"] = True
    item["active"] = False
    item["status"] = "pending"
    item["services"] = item.get("services") if isinstance(item.get("services"), list) else []
    item["documents"] = item.get("documents") if isinstance(item.get("documents"), list) else []
    item["workImages"] = item.get("workImages") if isinstance(item.get("workImages"), list) else []
    if created_at:
        item["createdAt"] = created_at
    item.pop("pinHash", None)
    return item


REVIEW_DIMENSION_KEYS = ("quality", "punctuality", "communication", "value")


def normalize_review_dimensions(value, fallback_rating=0):
    source = jload(value, {}) if isinstance(value, str) else value
    source = source if isinstance(source, dict) else {}
    try:
        fallback = max(1, min(5, int(round(float(fallback_rating or 0)))))
    except (TypeError, ValueError):
        fallback = 0
    result = {}
    for key in REVIEW_DIMENSION_KEYS:
        raw = source.get(key, fallback)
        try:
            score = int(round(float(raw)))
        except (TypeError, ValueError):
            score = fallback
        if score:
            result[key] = max(1, min(5, score))
    return result


def row_review(r, private=False):
    d = dict(r)
    d["approved"] = bool(d["approved"])
    d["deleted"] = bool(d.get("deleted_at"))
    d["dimensions"] = normalize_review_dimensions(
        d.get("dimensions"), d.get("rating", 0)
    )
    if not private:
        for key in ("phone", "user_id", "request_id", "deleted_at", "moderation_reason"):
            d.pop(key, None)
    return d


def row_complaint(r, private=False):
    d = dict(r)
    if not private:
        for key in ("phone", "user_id"):
            d.pop(key, None)
    return d


def secure_complaint_view(item):
    result = dict(item or {})
    result["evidence"] = [dict(evidence) for evidence in result.get("evidence", [])]
    for evidence in result["evidence"]:
        media_path = evidence.pop("mediaPath", "")
        if media_path:
            evidence["mediaUrl"] = secure_media_url(media_path)
    return result


def row_package(r):
    d = dict(r)
    d["active"] = bool(d["active"])
    d["legacy"] = bool(d.get("legacy", 0))
    d["durationDays"] = d.pop("duration_days")
    d["featuredBoost"] = d.pop("featured_boost")
    d["maxServices"] = d.pop("max_services")
    d["maxCategories"] = d.pop("max_categories", 1)
    d["maxImages"] = d.pop("max_images")
    d["maxWilayats"] = d.pop("max_wilayats", 0)
    d["maxGovernorates"] = d.pop("max_governorates", 0)
    d["monthlyResponses"] = d.pop("monthly_response_limit", 0)
    d["leadDelayMinutes"] = d.pop("lead_delay_minutes", 0)
    d["leadDelaySeconds"] = d.pop("lead_delay_seconds", d["leadDelayMinutes"] * 60)
    d["teamMembers"] = d.pop("max_team_members", 1)
    d["branches"] = d.pop("max_branches", 1)
    d["sharedInbox"] = bool(d.pop("shared_inbox", 0))
    d["advancedReports"] = bool(d.pop("advanced_reports", 0))
    d["badgeAr"] = d.pop("badge_ar", "")
    d["badgeEn"] = d.pop("badge_en", "")
    d["foundationOnce"] = bool(d.pop("foundation_once", 0))
    d["verifiedRequired"] = bool(d.pop("verified_required", 0))
    d["accountScope"] = d.pop("account_scope", "all")
    d["communityPackageQuota"] = d.pop("community_package_quota", 0)
    d["communityPackageDays"] = d.pop("community_package_days", 30)
    d["entitlements"] = jload(d.get("entitlements"), {})
    account_limits = (
        d["entitlements"].get("accountLimits", {})
        if isinstance(d["entitlements"], dict)
        else {}
    )
    d["individualLimits"] = account_limits.get("individual", {})
    d["companyLimits"] = account_limits.get("company", {})
    return d


def row_subscription(r):
    d = dict(r)
    d["packageId"] = d.pop("package_id")
    d["providerId"] = d.pop("provider_id")
    d["startDate"] = d.pop("start_date")
    d["endDate"] = d.pop("end_date")
    d["renewalPackageId"] = d.pop("renewal_package_id", "")
    d["previousPackageId"] = d.pop("previous_package_id", "")
    d["prorationAmount"] = d.pop("proration_amount", 0)
    d["creditAmount"] = d.pop("credit_amount", 0)
    d["graceDays"] = d.pop("grace_days", 14)
    d["graceUntil"] = d.pop("grace_until", "")
    d["activatedAt"] = d.pop("activated_at", "")
    d["cancelledAt"] = d.pop("cancelled_at", "")
    d["refundedAt"] = d.pop("refunded_at", "")
    d["paymentId"] = d.pop("payment_id", "")
    d["legacyPackageId"] = d.pop("legacy_package_id", "")
    d["autoRenew"] = bool(d.pop("auto_renew", 0))
    d["metadata"] = jload(d.get("metadata"), {})
    return d


def row_payment(r):
    d = dict(r)
    d["providerId"] = d.pop("provider_id")
    d["subscriptionId"] = d.pop("subscription_id")
    d["externalId"] = d.pop("external_id", "")
    d["failureCode"] = d.pop("failure_code", "")
    d["verifiedAt"] = d.pop("verified_at", "")
    d["refundedAt"] = d.pop("refunded_at", "")
    d["metadata"] = jload(d.get("metadata"), {})
    return d


def row_audit(r):
    return dict(r)


def row_lead(r):
    return dict(r)


def lead_matches_provider(lead, provider):
    if lead.get("kind") != "request" or lead.get("status") in ("cancelled", "deleted", "closed"):
        return False
    service_value = (lead.get("service_value") or "").strip()
    requested_cat = ""
    requested_service = ""
    if "|" in service_value:
        requested_cat, requested_service = service_value.split("|", 1)
    elif service_value:
        requested_service = service_value
    provider_services = provider.get("services") or []
    service_ok = not requested_service or any(
        svc.get("active", True)
        and svc.get("serviceId") == requested_service
        and (not requested_cat or svc.get("catId") == requested_cat)
        for svc in provider_services
    )
    gov = (lead.get("gov") or "").strip()
    areas = set(provider.get("areas") or [])
    areas.update([provider.get("gov"), provider.get("wilayah")])
    area_ok = not gov or gov in areas
    return bool(service_ok and area_ok)


def log_audit(con, session, action, target="", detail=""):
    actor_kind = (session or {}).get("kind", "system")
    actor_id = (session or {}).get("id") or (session or {}).get("providerId") or "system"
    con.execute("INSERT INTO audit_logs VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)", (slug("audit"), actor_kind, actor_id, action, target, detail[:900]))


def recompute_provider_quality(con, provider_id):
    r = con.execute("SELECT * FROM providers WHERE id=?", (provider_id,)).fetchone()
    if not r:
        return
    provider = row_provider(r, private=True)
    review_rows = list(
        con.execute(
            """SELECT rating,dimensions FROM reviews WHERE provider_id=? AND approved=1
            AND COALESCE(deleted_at,'')=''""",
            (provider_id,),
        )
    )
    review_count = len(review_rows)
    average_rating = (
        sum(float(row["rating"] or 0) for row in review_rows) / review_count
        if review_count
        else 0
    )
    dimension_values = {key: [] for key in REVIEW_DIMENSION_KEYS}
    for review in review_rows:
        dimensions = normalize_review_dimensions(
            review["dimensions"], review["rating"]
        )
        for key in REVIEW_DIMENSION_KEYS:
            if key in dimensions:
                dimension_values[key].append(dimensions[key])
    dimension_averages = {
        key: round(sum(values) / len(values), 2) if values else 0
        for key, values in dimension_values.items()
    }

    response_minutes = []
    for dispatch in con.execute(
        """SELECT created_at,offered_at FROM request_dispatches
        WHERE provider_id=? AND COALESCE(offered_at,'')!=''""",
        (provider_id,),
    ):
        started = parse_iso(dispatch["created_at"])
        offered = parse_iso(dispatch["offered_at"])
        if started and offered and offered >= started:
            response_minutes.append(
                max(0, (offered - started).total_seconds() / 60)
            )
    measured_response = (
        round(sum(response_minutes) / len(response_minutes))
        if response_minutes
        else max(1, int(provider.get("responseMinutes") or 30))
    )
    if measured_response <= 15:
        response_score = 100
    elif measured_response <= 30:
        response_score = 90
    elif measured_response <= 60:
        response_score = 78
    elif measured_response <= 180:
        response_score = 58
    else:
        response_score = 38

    job_rows = list(
        con.execute(
            """SELECT user_id,status,offers FROM customer_requests
            WHERE accepted_provider_id=?""",
            (provider_id,),
        )
    )
    completed_states = {"closed", "completed", "archived"}
    completed_jobs = sum(
        1 for row in job_rows if row["status"] in completed_states
    )
    cancelled_jobs = sum(1 for row in job_rows if row["status"] == "cancelled")
    decided_jobs = completed_jobs + cancelled_jobs
    completion_score = (
        round(completed_jobs / decided_jobs * 100)
        if decided_jobs
        else 70
    )
    completed_by_user = {}
    warranty_offers = 0
    for job in job_rows:
        if job["status"] in completed_states and job["user_id"]:
            completed_by_user[job["user_id"]] = (
                completed_by_user.get(job["user_id"], 0) + 1
            )
        accepted_offer = next(
            (
                offer
                for offer in jload(job["offers"], [])
                if isinstance(offer, dict)
                and offer.get("providerId") == provider_id
                and offer.get("status") == "accepted"
            ),
            None,
        )
        if accepted_offer and int(accepted_offer.get("warrantyDays") or 0) > 0:
            warranty_offers += 1
    repeat_customers = sum(
        1 for count in completed_by_user.values() if count >= 2
    )

    open_complaints = int(
        con.execute(
            """SELECT COUNT(*) n FROM complaints WHERE provider_id=?
            AND status NOT IN ('resolved','closed','rejected')""",
            (provider_id,),
        ).fetchone()["n"]
        or 0
    )
    complaint_score = max(40, 100 - open_complaints * 12)

    verification = con.execute(
        """SELECT status FROM provider_verification_cases
        WHERE provider_id=?""",
        (provider_id,),
    ).fetchone()
    verification_status = (
        verification["status"]
        if verification
        else ("verified" if provider.get("verified") else "unverified")
    )
    trust_score = {
        "verified": 100,
        "submitted": 72,
        "under_review": 72,
        "changes_required": 50,
        "unverified": 42,
        "rejected": 20,
        "expired": 20,
        "suspended": 10,
    }.get(verification_status, 42)

    profile_score = 0
    profile_score += 15 if provider.get("imagePath") else 0
    profile_score += 15 if provider.get("bio") else 0
    profile_score += 10 if provider.get("hours") else 0
    profile_score += 10 if provider.get("areas") else 0
    profile_score += 10 if provider.get("services") else 0
    profile_score += min(len(provider.get("workImages") or []) * 5, 20)
    profile_score += 10 if provider.get("documents") else 0
    profile_score += 10 if provider.get("commercialNo") else 0
    profile_score = min(100, profile_score)

    review_quality = (
        round(
            sum(dimension_averages.values())
            / len(REVIEW_DIMENSION_KEYS)
            * 20
        )
        if review_count
        else 65
    )
    quality = round(
        profile_score * 0.25
        + review_quality * 0.25
        + response_score * 0.12
        + completion_score * 0.15
        + trust_score * 0.18
        + complaint_score * 0.05
    )
    quality = max(0, min(100, quality))
    breakdown = {
        "version": 1,
        "profile": profile_score,
        "customerRating": round(average_rating, 2),
        "quality": dimension_averages["quality"],
        "punctuality": dimension_averages["punctuality"],
        "communication": dimension_averages["communication"],
        "value": dimension_averages["value"],
        "response": response_score,
        "responseMinutes": measured_response,
        "completion": completion_score,
        "trust": trust_score,
        "complaintRecord": complaint_score,
        "reviewCount": review_count,
        "responseSamples": len(response_minutes),
        "acceptedJobs": len(job_rows),
        "completedJobs": completed_jobs,
        "repeatCustomers": repeat_customers,
        "warrantyOffers": warranty_offers,
        "openComplaints": open_complaints,
    }
    con.execute(
        """UPDATE providers SET quality_score=?,response_score=?,
        response_minutes=?,completed_jobs=?,quality_breakdown=?,rating=?,reviews=?
        WHERE id=?""",
        (
            quality,
            response_score,
            measured_response,
            completed_jobs,
            jdump(breakdown),
            round(average_rating, 2),
            review_count,
            provider_id,
        ),
    )


def admin_public(r):
    d = dict(r)
    d.pop("code_hash", None)
    d.pop("two_factor_secret", None)
    d.pop("recovery_codes", None)
    d["permissions"] = jload(d["permissions"], [])
    d["active"] = bool(d["active"])
    d["twoFactorEnabled"] = bool(d.pop("two_factor_enabled", 0))
    return d


REFRESH_COOKIE_NAMES = {
    "user": "khadamati_user_refresh",
    "provider": "khadamati_provider_refresh",
    "provider_pending": "khadamati_provider_refresh",
    "admin": "khadamati_admin_refresh",
}


def issue_session_tokens(session, *, device_id=""):
    access_token = secrets.token_urlsafe(32)
    refresh_token = secrets.token_urlsafe(48)
    session_id = slug("ses")
    with db() as con:
        con.execute(
            """INSERT INTO auth_sessions(
            id,token_hash,refresh_hash,session_json,expires_at,
            access_expires_at,device_id,last_used_at,refreshed_at)
            VALUES(?,?,?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
            (
                session_id,
                hash_secret(access_token),
                hash_secret(refresh_token),
                jdump(session),
                iso_datetime(days=SESSION_DAYS),
                iso_datetime(minutes=ACCESS_TOKEN_MINUTES),
                safe_text(device_id, 120),
            ),
        )
    return {
        "token": access_token,
        "refreshToken": refresh_token,
        "sessionId": session_id,
        "kind": session.get("kind", ""),
        "accessExpiresAt": iso_datetime(minutes=ACCESS_TOKEN_MINUTES),
        "refreshExpiresAt": iso_datetime(days=SESSION_DAYS),
    }


def issue_token(session):
    """Compatibility wrapper for callers that only consume an access token."""
    return issue_session_tokens(session)["token"]


def _cookie_values(headers):
    cookie = SimpleCookie()
    try:
        cookie.load(str(headers.get("Cookie", "") or ""))
    except Exception:
        return {}
    return {key: morsel.value for key, morsel in cookie.items()}


def _session_not_expired(value):
    try:
        expires_at = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at > datetime.now(UTC)


def refresh_session(headers, requested_kind):
    requested_kind = safe_text(requested_kind, 40)
    cookie_kind = "provider" if requested_kind == "provider_pending" else requested_kind
    cookie_name = REFRESH_COOKIE_NAMES.get(cookie_kind)
    refresh_token = _cookie_values(headers).get(cookie_name, "") if cookie_name else ""
    if not refresh_token:
        raise DomainError("refresh_session_required", 401)
    with db() as con:
        row = con.execute(
            """SELECT * FROM auth_sessions
            WHERE refresh_hash=? AND revoked=0""",
            (hash_secret(refresh_token),),
        ).fetchone()
        if not row or not _session_not_expired(row["expires_at"]):
            if row:
                con.execute(
                    "UPDATE auth_sessions SET revoked=1 WHERE id=?", (row["id"],)
                )
            raise DomainError("session_expired", 401)
        stored = jload(row["session_json"], None)
        stored_kind = stored.get("kind") if isinstance(stored, dict) else ""
        if requested_kind == "provider":
            kind_matches = stored_kind in {"provider", "provider_pending"}
        else:
            kind_matches = stored_kind == requested_kind
        if not kind_matches:
            raise DomainError("session_kind_mismatch", 403)
        session = validated_session(con, stored)
        if not session:
            con.execute(
                "UPDATE auth_sessions SET revoked=1 WHERE id=?", (row["id"],)
            )
            raise DomainError("session_expired", 401)
        access_token = secrets.token_urlsafe(32)
        next_refresh = secrets.token_urlsafe(48)
        access_expires_at = iso_datetime(minutes=ACCESS_TOKEN_MINUTES)
        refresh_expires_at = iso_datetime(days=SESSION_DAYS)
        con.execute(
            """UPDATE auth_sessions
            SET token_hash=?,refresh_hash=?,session_json=?,expires_at=?,
                access_expires_at=?,last_used_at=CURRENT_TIMESTAMP,
                refreshed_at=CURRENT_TIMESTAMP
            WHERE id=?""",
            (
                hash_secret(access_token),
                hash_secret(next_refresh),
                jdump(session),
                refresh_expires_at,
                access_expires_at,
                row["id"],
            ),
        )
    return {
        "token": access_token,
        "refreshToken": next_refresh,
        "session": session,
        "kind": session["kind"],
        "accessExpiresAt": access_expires_at,
        "refreshExpiresAt": refresh_expires_at,
    }


def persist_access_session(headers, requested_kind=""):
    authorization = str(headers.get("Authorization", "") or "")
    if not authorization.startswith("Bearer "):
        raise DomainError("authentication_required", 401)
    token = authorization[7:].strip()
    with db() as con:
        row = con.execute(
            """SELECT * FROM auth_sessions
            WHERE token_hash=? AND revoked=0""",
            (hash_secret(token),),
        ).fetchone()
        if not row or not _session_not_expired(row["expires_at"]):
            raise DomainError("session_expired", 401)
        session = validated_session(con, jload(row["session_json"], None))
        if not session:
            raise DomainError("session_expired", 401)
        expected = "provider" if session["kind"] == "provider_pending" else session["kind"]
        if requested_kind and requested_kind != expected:
            raise DomainError("session_kind_mismatch", 403)
        refresh_token = secrets.token_urlsafe(48)
        access_expires_at = iso_datetime(minutes=ACCESS_TOKEN_MINUTES)
        refresh_expires_at = iso_datetime(days=SESSION_DAYS)
        con.execute(
            """UPDATE auth_sessions SET refresh_hash=?,expires_at=?,
            access_expires_at=?,last_used_at=CURRENT_TIMESTAMP,
            refreshed_at=CURRENT_TIMESTAMP WHERE id=?""",
            (
                hash_secret(refresh_token),
                refresh_expires_at,
                access_expires_at,
                row["id"],
            ),
        )
    return {
        "token": token,
        "refreshToken": refresh_token,
        "session": session,
        "kind": session["kind"],
        "accessExpiresAt": access_expires_at,
        "refreshExpiresAt": refresh_expires_at,
    }


def validated_session(con, session):
    if not isinstance(session, dict):
        return None
    kind = session.get("kind")
    if kind == "admin":
        row = con.execute(
            "SELECT * FROM admin_users WHERE id=? AND active=1", (session.get("id", ""),)
        ).fetchone()
        return {"kind": "admin", **admin_public(row)} if row else None
    if kind == "user":
        row = con.execute(
            "SELECT id,name,phone,status FROM app_users WHERE id=? AND status='active'",
            (session.get("userId", ""),),
        ).fetchone()
        if not row:
            return None
        return {
            "kind": "user", "userId": row["id"], "name": row["name"], "phone": row["phone"]
        }
    if kind == "provider":
        provider = con.execute(
            "SELECT id,name,active,status FROM providers WHERE id=?",
            (session.get("providerId", ""),),
        ).fetchone()
        if not provider or not bool(provider["active"]) or provider["status"] == "deleted":
            return None
        member_id = safe_text(session.get("memberId", ""), 100)
        if member_id:
            member = con.execute(
                """SELECT id,role,permissions FROM provider_team_members
                WHERE id=? AND provider_id=? AND active=1""",
                (member_id, provider["id"]),
            ).fetchone()
            if not member:
                return None
            role = member["role"]
            if role not in {"provider_manager", "provider_staff"}:
                return None
            provider_permissions = [
                permission for permission in jload(member["permissions"], [])
                if permission in PROVIDER_ROLE_PERMISSIONS.get(role, set())
            ]
        else:
            role = "provider_owner"
            provider_permissions = list(PROVIDER_ROLE_PERMISSIONS["provider_owner"])
        return {
            "kind": "provider", "providerId": provider["id"], "name": provider["name"],
            "role": role, "memberId": member_id, "providerPermissions": provider_permissions,
        }
    if kind == "provider_pending":
        request_id = safe_text(session.get("requestId", ""), 120)
        row = con.execute(
            "SELECT payload FROM provider_requests WHERE id=?", (request_id,)
        ).fetchone()
        if not row:
            return None
        payload = jload(row["payload"], {})
        phone = normalize_phone(payload.get("phone", ""))
        if not phone or not phone_matches(session.get("phone", ""), phone):
            return None
        return {
            "kind": "provider_pending", "requestId": request_id,
            "name": payload.get("name", ""), "phone": phone,
        }
    return None


def token_session(headers):
    authorization = str(headers.get("Authorization", "") or "")
    if not authorization.startswith("Bearer "):
        return None
    token = authorization[7:].strip()
    if not token:
        return None
    with db() as con:
        row = con.execute(
            """SELECT id,session_json,expires_at,access_expires_at
            FROM auth_sessions WHERE token_hash=? AND revoked=0""",
            (hash_secret(token),),
        ).fetchone()
        if not row:
            return None
        access_expiry = row["access_expires_at"] or row["expires_at"]
        if not _session_not_expired(access_expiry):
            if not row["access_expires_at"]:
                con.execute(
                    "UPDATE auth_sessions SET revoked=1 WHERE id=?", (row["id"],)
                )
            return None
        if not _session_not_expired(row["expires_at"]):
            con.execute("UPDATE auth_sessions SET revoked=1 WHERE id=?", (row["id"],))
            return None
        session = validated_session(con, jload(row["session_json"], None))
        if not session:
            con.execute("UPDATE auth_sessions SET revoked=1 WHERE id=?", (row["id"],))
            return None
        con.execute(
            "UPDATE auth_sessions SET last_used_at=CURRENT_TIMESTAMP WHERE id=?",
            (row["id"],),
        )
        return session


def revoke_session(headers):
    authorization = str(headers.get("Authorization", "") or "")
    token = authorization[7:].strip() if authorization.startswith("Bearer ") else ""
    cookie_hashes = [
        hash_secret(value)
        for name, value in _cookie_values(headers).items()
        if name in set(REFRESH_COOKIE_NAMES.values()) and value
    ]
    with db() as con:
        changed = 0
        if token:
            changed += con.execute(
                "UPDATE auth_sessions SET revoked=1 WHERE token_hash=?",
                (hash_secret(token),),
            ).rowcount
        for cookie_hash in cookie_hashes:
            changed += con.execute(
                "UPDATE auth_sessions SET revoked=1 WHERE refresh_hash=?",
                (cookie_hash,),
            ).rowcount
    return changed > 0


def revoke_account_sessions(con, kind, account_id, exclude_token_hash=None):
    """Revoke sessions for one exact account without relying on JSON substring matching."""
    id_key = {"user": "userId", "provider": "providerId", "admin": "id"}.get(kind)
    if not id_key or not account_id:
        return 0
    revoked = 0
    rows = con.execute(
        "SELECT id,token_hash,session_json FROM auth_sessions WHERE revoked=0"
    ).fetchall()
    for row in rows:
        session = jload(row["session_json"], {})
        if (
            isinstance(session, dict)
            and session.get("kind") == kind
            and str(session.get(id_key, "")) == str(account_id)
            and row["token_hash"] != (exclude_token_hash or "")
        ):
            con.execute("UPDATE auth_sessions SET revoked=1 WHERE id=?", (row["id"],))
            revoked += 1
    return revoked


def row_app_user(r, private=False, sign_private=False):
    d = dict(r)
    d["firstLogin"] = d.pop("first_login", "")
    d["lastLogin"] = d.pop("last_login", "")
    d["loginCount"] = int(d.pop("login_count", 0) or 0)
    d["failedAttempts"] = int(d.pop("failed_attempts", 0) or 0)
    d["lockedUntil"] = d.pop("locked_until", "")
    d["pinConfigured"] = bool(d.pop("pin_hash", ""))
    if private and sign_private and d.get("avatar"):
        d["avatar"] = secure_media_url(d["avatar"])
    if not private:
        d.pop("phone", None)
    return d


def row_customer_request(r, sign_private=False):
    d = dict(r)
    d["userId"] = d.pop("user_id", "")
    d["customerName"] = d.pop("customer_name", "")
    d["serviceValue"] = d.pop("service_value", "")
    d["serviceName"] = d.pop("service_name", "")
    d["scheduleType"] = d.pop("schedule_type", "")
    d["requestedAt"] = d.pop("requested_at", "")
    d["budgetMin"] = d.pop("budget_min", 0)
    d["budgetMax"] = d.pop("budget_max", 0)
    d["locationText"] = d.pop("location_text", "")
    latitude = d.pop("latitude", None)
    longitude = d.pop("longitude", None)
    d["location"] = (
        {"lat": latitude, "lng": longitude}
        if latitude is not None and longitude is not None
        else None
    )
    d["images"] = jload(d["images"], [])
    if sign_private:
        d["images"] = [secure_media_url(item) for item in d["images"] if item]
    d["acceptedProviderId"] = d.pop("accepted_provider_id", "")
    d["matchingProviderIds"] = jload(d.pop("matching_provider_ids", "[]"), [])
    d["declinedProviderIds"] = jload(d.pop("declined_provider_ids", "[]"), [])
    d["offers"] = jload(d.pop("offers", "[]"), [])
    messages = jload(d.pop("messages", "[]"), [])
    d["messages"] = [
        {
            **message,
            "image": secure_media_url(message.get("image", "")) if sign_private else image_url(message.get("image", "")),
            "audio": secure_media_url(message.get("audio", "")) if sign_private else image_url(message.get("audio", "")),
        }
        for message in messages
        if isinstance(message, dict)
    ]
    d["arrival"] = jload(d.pop("arrival", "{}"), {})
    d["contactConsent"] = jload(d.pop("contact_consent", "{}"), {})
    d["waitlisted"] = bool(d.pop("waitlisted", 0))
    d["offersOpen"] = bool(d.pop("offers_open", 0))
    d["marketplaceStatus"] = d.pop("marketplace_status", "pending")
    d["dispatchStartedAt"] = d.pop("dispatch_started_at", "")
    d["expansionAt"] = d.pop("expansion_at", "")
    d["rankingVersion"] = d.pop("ranking_version", "")
    d["assetId"] = d.pop("asset_id", "")
    d["organizationId"] = d.pop("organization_id", "")
    d["organizationLocationId"] = d.pop("organization_location_id", "")
    d["requestedByMemberId"] = d.pop("requested_by_member_id", "")
    d["createdAt"] = d.pop("created_at", "")
    d["updatedAt"] = d.pop("updated_at", "")
    return d


def community_snapshot_view(snapshot):
    """Expose community media through the same safe URL rules as provider cards."""
    result = dict(snapshot or {})
    listings = []
    for raw in result.get("listings", []):
        item = dict(raw)
        item["imageUrl"] = image_url(item.pop("imagePath", ""))
        owner = dict(item.get("owner") or {})
        owner["imageUrl"] = image_url(owner.pop("imagePath", ""))
        item["owner"] = owner
        offers = []
        for raw_offer in item.get("offers", []):
            offer = dict(raw_offer)
            provider = dict(offer.get("provider") or {})
            provider["imageUrl"] = image_url(provider.pop("imagePath", ""))
            offer["provider"] = provider
            offers.append(offer)
        item["offers"] = offers
        listings.append(item)
    result["listings"] = listings
    return result


def request_with_workflow(con, item, *, asset_visible=False):
    """Attach private workflow details after request-level authorization."""
    result = attach_workflow_data(con, item, asset_visible=asset_visible)
    evidence = result.get("completionEvidence")
    if evidence:
        evidence["beforeImages"] = [
            secure_media_url(path) for path in evidence.get("beforeImages", []) if path
        ]
        evidence["afterImages"] = [
            secure_media_url(path) for path in evidence.get("afterImages", []) if path
        ]
    asset = result.get("serviceAsset")
    if asset and asset.get("imagePath"):
        asset["imageUrl"] = secure_media_url(asset["imagePath"])
        asset.pop("imagePath", None)
    return result


SUGGESTION_PRESET_KEYS = {
    "excellent_work",
    "fast_execution",
    "fair_price",
    "worked_before",
    "recommended_contact",
}
ACTIVE_REQUEST_STATES = {"matching", "viewed", "unavailable", "paused", "open"}


def row_request_suggestion(r):
    d = dict(r)
    d["requestId"] = d.pop("request_id", "")
    d["providerId"] = d.pop("provider_id", "")
    d["suggestedByUserId"] = d.pop("suggested_by_user_id", "")
    d["presetKey"] = d.pop("preset_key", "")
    d["reportReason"] = d.pop("report_reason", "")
    d["selectedAt"] = d.pop("selected_at", "")
    d["reportedAt"] = d.pop("reported_at", "")
    d["deletedAt"] = d.pop("deleted_at", "")
    d["createdAt"] = d.pop("created_at", "")
    d["updatedAt"] = d.pop("updated_at", "")
    if "provider_name" in d:
        d["providerName"] = d.pop("provider_name", "")
    return d


def request_suggestions(con, request_id, *, include_hidden=False):
    if include_hidden:
        rows = con.execute(
            """SELECT s.*,p.name provider_name FROM request_provider_suggestions s
            LEFT JOIN providers p ON p.id=s.provider_id WHERE s.request_id=?
            ORDER BY s.created_at DESC""",
            (request_id,),
        )
    else:
        rows = con.execute(
            """SELECT s.*,p.name provider_name FROM request_provider_suggestions s
            LEFT JOIN providers p ON p.id=s.provider_id
            WHERE s.request_id=? AND s.status IN ('active','selected')
            ORDER BY s.created_at DESC""",
            (request_id,),
        )
    return [
        row_request_suggestion(row)
        for row in rows
    ]


def request_suggestion_by_id(con, suggestion_id):
    row = con.execute(
        """SELECT s.*,p.name provider_name FROM request_provider_suggestions s
        LEFT JOIN providers p ON p.id=s.provider_id WHERE s.id=?""",
        (suggestion_id,),
    ).fetchone()
    return row_request_suggestion(row) if row else None


def marketplace_request(item, include_note=False):
    """Return only the fields needed by the public request board."""
    allowed = {
        "id", "serviceValue", "serviceName", "gov", "wilayah", "urgency",
        "scheduleType", "requestedAt", "note", "status", "offers",
        "acceptedProviderId", "offersOpen", "createdAt", "updatedAt",
    }
    public_item = {key: value for key, value in item.items() if key in allowed}
    public_item["requesterLabel"] = "مستخدم خدماتي"
    public_item["offerCount"] = len(public_item.get("offers") or [])
    public_item["locationPrecision"] = "area"
    public_item["offers"] = []
    public_item["acceptedProviderId"] = ""
    public_item["note"] = safe_text(public_item.get("note", ""), 280) if include_note else ""
    return public_item


def distance_km(request_row, provider_row):
    values = (
        request_row.get("latitude"), request_row.get("longitude"),
        provider_row.get("latitude"), provider_row.get("longitude"),
    )
    if any(value is None for value in values):
        return None
    lat1, lng1, lat2, lng2 = map(math.radians, map(float, values))
    dlat, dlng = lat2 - lat1, lng2 - lng1
    value = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return round(6371 * 2 * math.atan2(math.sqrt(value), math.sqrt(max(0, 1 - value))), 2)


def provider_profile_complete(provider):
    services = jload(provider.get("services"), []) if isinstance(provider.get("services"), str) else provider.get("services", [])
    documents = jload(provider.get("documents"), []) if isinstance(provider.get("documents"), str) else provider.get("documents", [])
    return bool(
        provider.get("verified")
        and provider.get("commercial_no")
        and documents
        and services
        and len(str(provider.get("bio") or "").split()) >= 3
        and provider.get("hours")
    )


def ranked_suggestion_candidates(con, request_row, *, limit=10):
    request = dict(request_row)
    entitlements = EntitlementService(con)
    blocks = InteractionBlockService(con)
    request_user_id = str(request.get("user_id") or request.get("userId") or "")
    candidates = []
    for row in con.execute(
        """SELECT * FROM providers WHERE active=1 AND verified=1 AND status='available'
        AND COALESCE(listing_enabled,1)=1 AND COALESCE(request_enabled,1)=1"""
    ):
        provider = dict(row)
        if request_user_id and blocks.is_blocked(request_user_id, provider["id"]):
            continue
        if not provider_profile_complete(provider):
            continue
        try:
            allowed, _, grants = entitlements.can_receive(provider["id"])
        except DomainError:
            allowed, grants = False, {}
        if not allowed:
            continue
        score, breakdown = RankingService.score(request, provider, grants.get("planId", provider.get("package_id", "")), datetime.now(UTC))
        if score <= 0 or not RankingService.exact_service_match(request, provider):
            continue
        distance = distance_km(request, provider)
        area_priority = 0 if request.get("wilayah") and request.get("wilayah") == provider.get("wilayah") else 1
        candidates.append((area_priority, distance if distance is not None else 9999, -score, provider, breakdown))
    candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3]["id"]))
    result = []
    for _, distance, negative_score, provider, breakdown in candidates[: max(1, min(int(limit), 20))]:
        public_provider = row_provider(provider, private=False)
        public_provider["suggestionScore"] = round(-negative_score, 1)
        public_provider["distanceKm"] = None if distance == 9999 else distance
        public_provider["matchBreakdown"] = breakdown
        result.append(public_provider)
    return result


def row_notification(r):
    d = dict(r)
    d["targetKind"] = d.pop("target_kind")
    d["targetId"] = d.pop("target_id")
    d["relatedId"] = d.pop("related_id")
    d["actionText"] = d.pop("action_text")
    d["actionRoute"] = d.pop("action_route")
    d["read"] = bool(d.pop("is_read"))
    d["createdAt"] = d.pop("created_at")
    return d


def row_advertisement(r):
    d = dict(r)
    d["imageUrl"] = image_url(d.pop("image_path", ""))
    d["startsAt"] = d.pop("starts_at", "")
    d["endsAt"] = d.pop("ends_at", "")
    d["deletedAt"] = d.pop("deleted_at", "")
    d["createdAt"] = d.pop("created_at", "")
    d["updatedAt"] = d.pop("updated_at", "")
    d["active"] = bool(d["active"])
    return d


def push_ready():
    return bool(webpush and os.environ.get("VAPID_PRIVATE_KEY") and os.environ.get("VAPID_PUBLIC_KEY"))


def deliver_push(target_kind, target_id, payload):
    if not push_ready():
        return
    time.sleep(0.15)
    with db() as con:
        subscriptions = list(
            con.execute(
                """SELECT id,subscription_json FROM push_subscriptions
                WHERE target_kind=? AND target_id=? AND active=1""",
                (target_kind, target_id or ""),
            )
        )
    for subscription in subscriptions:
        try:
            webpush(
                subscription_info=jload(subscription["subscription_json"], {}),
                data=jdump(payload),
                vapid_private_key=os.environ["VAPID_PRIVATE_KEY"],
                vapid_claims={"sub": os.environ.get("VAPID_SUBJECT", f"mailto:{SUPPORT_EMAIL}")},
                ttl=300,
            )
            with db() as con:
                con.execute(
                    "UPDATE push_subscriptions SET last_success_at=CURRENT_TIMESTAMP WHERE id=?",
                    (subscription["id"],),
                )
        except WebPushException as err:
            status = getattr(getattr(err, "response", None), "status_code", 0)
            if status in (404, 410):
                with db() as con:
                    con.execute("UPDATE push_subscriptions SET active=0 WHERE id=?", (subscription["id"],))
        except Exception as err:
            log_event(
                "push.delivery_skipped",
                level="warning",
                errorType=type(err).__name__,
            )


def create_notification(con, target_kind, target_id, title, message="", *, type_="general",
                        related_id="", priority="normal", action_text="", action_route=""):
    notification_id = slug("ntf")
    con.execute(
        """INSERT INTO app_notifications(
        id,target_kind,target_id,type,title,message,related_id,priority,action_text,action_route)
        VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (
            notification_id, target_kind, target_id or "", type_, title[:160], message[:1200],
            related_id or "", priority, action_text[:80], action_route[:240],
        ),
    )
    if push_ready():
        is_chat = type_ == "chat" and bool(related_id)
        push_tag = (
            f"khadamati-chat-{target_kind}-{target_id or 'account'}-{related_id}"
            if is_chat else f"khadamati-{notification_id}"
        )
        push_route = PUBLIC_APP_URL
        if is_chat:
            push_route += f"#chat={related_id}&target={target_kind}"
        threading.Thread(
            target=deliver_push,
            args=(
                target_kind,
                target_id or "",
                {
                    "id": notification_id,
                    "title": title[:160],
                    "body": message[:1200],
                    "tag": push_tag,
                    "route": push_route,
                },
            ),
            daemon=True,
        ).start()
    return notification_id


def request_matches_provider(request_item, provider):
    service_value = str(request_item.get("serviceValue") or "")
    requested_cat, requested_service = ("", "")
    if "|" in service_value:
        requested_cat, requested_service = service_value.split("|", 1)
    service_ok = any(
        svc.get("active", True)
        and requested_service
        and svc.get("serviceId") == requested_service
        and (not requested_cat or svc.get("catId") == requested_cat)
        for svc in provider.get("services") or []
    )
    if not service_ok:
        return False
    request_area = {str(request_item.get("gov") or ""), str(request_item.get("wilayah") or "")} - {""}
    provider_area = {
        str(provider.get("gov") or ""),
        str(provider.get("wilayah") or ""),
        *(str(item) for item in provider.get("governorates") or []),
        *(str(a) for a in provider.get("areas") or []),
    } - {""}
    return not request_area or bool(request_area & provider_area)


def provider_eligibility(con, provider, *, receive_requests=False, map_only=False):
    """Single source of truth for public listing, request intake, and map markers."""
    item = dict(provider) if not isinstance(provider, dict) else provider
    if not int(item.get("active") or 0) or not int(item.get("verified") or 0):
        return False, "provider_inactive"
    if item.get("status") in {"unavailable", "under_review", "pending", "suspended", "deleted"}:
        return False, "provider_unavailable"
    if not int(item.get("listing_enabled", item.get("listingEnabled", 1)) or 0):
        return False, "listing_disabled"
    if receive_requests:
        if item.get("status") != "available":
            return False, "provider_not_available"
        if not int(item.get("request_enabled", item.get("requestEnabled", 1)) or 0):
            return False, "requests_disabled"
    if map_only:
        if not int(item.get("map_visible", item.get("mapVisible", 1)) or 0):
            return False, "map_hidden"
        if item.get("latitude") is None or item.get("longitude") is None:
            return False, "location_missing"
    allowed, reason, _ = EntitlementService(con).can_receive(item.get("id", ""))
    if receive_requests and not allowed:
        return False, reason or "subscription_inactive"
    if not receive_requests:
        grants = EntitlementService(con).for_provider(item.get("id", ""))
        if not grants.get("allowed"):
            return False, "subscription_inactive"
    return True, ""


def service_availability_snapshot(con):
    """Return privacy-safe provider counts used by the direct-request UI."""
    services = {}
    categories = {}
    for row in con.execute("SELECT * FROM providers"):
        eligible, _ = provider_eligibility(con, row, receive_requests=True)
        if not eligible:
            continue
        provider_services = jload(row["services"], [])
        provider_categories = set()
        for service in provider_services:
            if not isinstance(service, dict) or service.get("active") is False:
                continue
            cat_id = safe_text(service.get("catId"), 80)
            service_id = safe_text(service.get("serviceId"), 80)
            if not cat_id or not service_id:
                continue
            key = f"{cat_id}|{service_id}"
            services[key] = int(services.get(key, 0)) + 1
            provider_categories.add(cat_id)
        for cat_id in provider_categories:
            categories[cat_id] = int(categories.get(cat_id, 0)) + 1
    return {
        "services": services,
        "categories": categories,
        "generatedAt": datetime.now(UTC).isoformat(),
    }


def provider_operational_insights(con, provider_id):
    """Return measured funnel counters for one provider workspace."""
    dispatch = con.execute(
        """SELECT COUNT(*) dispatched,
        SUM(CASE WHEN COALESCE(opened_at,'')!='' THEN 1 ELSE 0 END) opened,
        SUM(CASE WHEN COALESCE(offered_at,'')!='' THEN 1 ELSE 0 END) offered,
        SUM(CASE WHEN COALESCE(accepted_at,'')!='' THEN 1 ELSE 0 END) accepted,
        COALESCE(AVG(score),0) average_score
        FROM request_dispatches WHERE provider_id=?""",
        (provider_id,),
    ).fetchone()
    jobs = con.execute(
        """SELECT
        SUM(CASE WHEN status IN ('accepted','appointmentConfirmed','inProgress',
        'awaitingConfirmation','qualityReview') THEN 1 ELSE 0 END) active_jobs,
        SUM(CASE WHEN status IN ('closed','archived') THEN 1 ELSE 0 END) completed_jobs
        FROM customer_requests WHERE accepted_provider_id=?""",
        (provider_id,),
    ).fetchone()
    dispatched = int(dispatch["dispatched"] or 0)
    opened = int(dispatch["opened"] or 0)
    offered = int(dispatch["offered"] or 0)
    accepted = int(dispatch["accepted"] or 0)
    return {
        "dispatched": dispatched,
        "opened": opened,
        "offered": offered,
        "accepted": accepted,
        "activeJobs": int(jobs["active_jobs"] or 0),
        "completedJobs": int(jobs["completed_jobs"] or 0),
        "openRate": round(100 * opened / max(1, dispatched), 1),
        "offerRate": round(100 * offered / max(1, dispatched), 1),
        "winRate": round(100 * accepted / max(1, offered), 1),
        "averageMatchScore": round(float(dispatch["average_score"] or 0), 1),
    }


def admin_demand_gaps(con):
    """Summarize repeatedly unserved demand without exposing customer details."""
    rows = con.execute(
        """SELECT service_value,service_name,gov,wilayah,COUNT(*) request_count,
        MAX(created_at) last_requested_at
        FROM customer_requests
        WHERE status='unavailable'
           OR (status IN ('matching','viewed') AND COALESCE(accepted_provider_id,'')=''
               AND datetime(created_at)<=datetime('now','-24 hours'))
        GROUP BY service_value,service_name,gov,wilayah
        ORDER BY request_count DESC,last_requested_at DESC
        LIMIT 16"""
    )
    return [
        {
            "serviceValue": row["service_value"],
            "serviceName": row["service_name"],
            "gov": row["gov"],
            "wilayah": row["wilayah"],
            "requestCount": int(row["request_count"] or 0),
            "lastRequestedAt": row["last_requested_at"],
        }
        for row in rows
    ]


def login_failure_state(con, account_kind, account_id):
    key = safe_text(account_id, 160) or "unknown"
    row = con.execute(
        "SELECT attempts,last_attempt FROM login_failures WHERE account_kind=? AND account_id=?",
        (account_kind, key),
    ).fetchone()
    if not row:
        return {"locked": False, "attempts": 0, "retryAfter": 0}
    try:
        last_attempt = datetime.fromisoformat(str(row["last_attempt"]).replace("Z", "+00:00"))
        if last_attempt.tzinfo is None:
            last_attempt = last_attempt.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        last_attempt = datetime.now(UTC) - timedelta(minutes=LOGIN_LOCK_MINUTES + 1)
    unlock_at = last_attempt + timedelta(minutes=LOGIN_LOCK_MINUTES)
    attempts = int(row["attempts"] or 0)
    if datetime.now(UTC) >= unlock_at:
        con.execute(
            "DELETE FROM login_failures WHERE account_kind=? AND account_id=?",
            (account_kind, key),
        )
        return {"locked": False, "attempts": 0, "retryAfter": 0}
    retry_after = max(1, math.ceil((unlock_at - datetime.now(UTC)).total_seconds()))
    return {
        "locked": attempts >= LOGIN_MAX_ATTEMPTS,
        "attempts": attempts,
        "retryAfter": retry_after if attempts >= LOGIN_MAX_ATTEMPTS else 0,
    }


def record_login_failure(con, account_kind, account_id, phone=""):
    key = safe_text(account_id or phone, 160) or "unknown"
    login_failure_state(con, account_kind, key)
    con.execute(
        """INSERT INTO login_failures(account_kind,account_id,phone,attempts,last_attempt)
        VALUES(?,?,?,1,CURRENT_TIMESTAMP)
        ON CONFLICT(account_kind,account_id) DO UPDATE SET
        attempts=login_failures.attempts+1,last_attempt=CURRENT_TIMESTAMP""",
        (account_kind, key, safe_text(phone, 32)),
    )
    row = con.execute(
        "SELECT attempts FROM login_failures WHERE account_kind=? AND account_id=?",
        (account_kind, key),
    ).fetchone()
    attempts = int(row["attempts"] or 0)
    if attempts in {3, LOGIN_MAX_ATTEMPTS}:
        create_notification(
            con, "admin", "", "محاولات دخول غير ناجحة",
            f"{account_kind}: {phone or key} - عدد المحاولات {attempts}",
            type_="security", related_id=key, priority="urgent",
            action_text="مراجعة الحساب", action_route=f"admin:{account_kind}:{key}",
        )
    return attempts


def clear_login_failures(con, account_kind, account_id):
    con.execute(
        "DELETE FROM login_failures WHERE account_kind=? AND account_id=?",
        (account_kind, account_id),
    )


def permissions_for(role, selected=None):
    if selected:
        return [p for p in selected if p in ALL_PERMISSIONS]
    return ROLE_PERMISSIONS.get(role, [])


def has_permission(session, permission):
    if not session or session.get("kind") != "admin":
        return False
    role = str(session.get("role") or "")
    raw_permissions = session.get("permissions")
    if isinstance(raw_permissions, str):
        raw_permissions = jload(raw_permissions, [])
    if not isinstance(raw_permissions, list):
        raw_permissions = []
    permissions = raw_permissions if raw_permissions else permissions_for(role)
    return role in {"owner", "super_admin"} or permission in permissions


def scan_expirations(con):
    settings_row = con.execute("SELECT value FROM settings WHERE key='platform'").fetchone()
    settings = jload(settings_row["value"], {}) if settings_row else {}
    thresholds = settings.get("expiryThresholds", [30, 14, 7, 1, 0])
    thresholds = sorted({int(x) for x in thresholds if str(x).lstrip("-").isdigit()}) or [0, 1, 7, 14, 30]
    checks = []
    for row in con.execute(
        """SELECT id,name,subscription_until,verification_expiry,commercial_expiry,license_expiry
        FROM providers"""
    ):
        for field, label in (
            ("subscription_until", "الاشتراك"),
            ("verification_expiry", "التوثيق"),
            ("commercial_expiry", "السجل التجاري"),
            ("license_expiry", "الرخصة"),
        ):
            if row[field]:
                checks.append(("provider", row["id"], row["name"], label, row[field]))
    for row in con.execute("SELECT id,advertiser,ends_at FROM advertisements WHERE active=1"):
        if row["ends_at"]:
            checks.append(("advertisement", row["id"], row["advertiser"] or "إعلان", "الإعلان", row["ends_at"]))
    today = datetime.now(UTC).date()
    for kind, item_id, name, label, raw_date in checks:
        try:
            expiry = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00")).date()
        except ValueError:
            try:
                expiry = datetime.strptime(str(raw_date)[:10], "%Y-%m-%d").date()
            except ValueError:
                continue
        days = (expiry - today).days
        if label == "الاشتراك" and days < 0:
            stage = "expired"
        elif days < -14:
            stage = "expired"
        elif days < 0:
            stage = "grace"
        else:
            stage = next((str(t) for t in thresholds if days <= t), None)
        if stage is None:
            continue
        dedupe = f"expiry:{kind}:{item_id}:{label}:{stage}"
        if con.execute(
            "SELECT id FROM app_notifications WHERE type='expiry' AND related_id=?",
            (dedupe,),
        ).fetchone():
            continue
        title = (
            f"انتهت صلاحية {label}"
            if stage == "expired"
            else f"{label} في فترة السماح"
            if stage == "grace"
            else f"{label} قريب الانتهاء"
        )
        message = f"{name} - {raw_date}" + (
            f" - متبقٍ {days} يوم" if days >= 0 else f" - مضى {abs(days)} يوم"
        )
        create_notification(
            con, "admin", "", title, message, type_="expiry", related_id=dedupe,
            priority="urgent" if days <= 3 else "high",
            action_text="فتح الملف", action_route=f"admin:{kind}:{item_id}",
        )
        if kind == "provider" and label == "الاشتراك":
            create_notification(
                con, "provider", item_id, title, message, type_="expiry", related_id=dedupe,
                priority="urgent" if days <= 3 else "high",
                action_text="تجديد الباقة", action_route="provider:subscription",
            )


def create_marketplace_notifications(con, released):
    for item in released:
        create_notification(
            con, "provider", item["providerId"], "طلب مناسب لخدمتك",
            f"{item['serviceName']} - {item['area'] or 'الموقع داخل الطلب'}",
            type_="request", related_id=item["requestId"], priority="high",
            action_text="فتح الطلب", action_route=f"provider:request:{item['requestId']}",
        )


def run_domain_maintenance(con):
    """Synchronize access and release due request waves without a separate queue service."""
    state_changes = SubscriptionService(con).synchronize_all()
    for change in state_changes:
        subscription = change.get("subscription") or {}
        provider_id = change.get("providerId", "")
        state = change.get("state", "")
        related = f"subscription-state:{subscription.get('id', provider_id)}:{state}"
        exists = con.execute(
            """SELECT id FROM app_notifications WHERE target_kind='provider'
            AND target_id=? AND type='subscription' AND related_id=? LIMIT 1""",
            (provider_id, related),
        ).fetchone()
        if not exists:
            title = "تحديث حالة الاشتراك"
            message = {
                "expiring": "اشتراكك قريب الانتهاء. راجع التجديد للحفاظ على ظهور بطاقتك.",
                "grace": "اشتراكك في فترة السماح. بياناتك محفوظة ويمكنك التجديد الآن.",
                "expired": "انتهى الاشتراك وتوقف الظهور واستقبال الطلبات فقط. بيانات الحساب محفوظة.",
                "active": "اشتراكك نشط وعاد ظهور البطاقة واستقبال الطلبات.",
                "foundation": "فترة التأسيس نشطة.",
            }.get(state, "تم تحديث حالة اشتراكك.")
            create_notification(
                con, "provider", provider_id, title, message,
                type_="subscription", related_id=related,
                priority="high" if state in {"grace", "expired"} else "normal",
                action_text="إدارة الاشتراك", action_route="provider:subscription",
            )
            create_notification(
                con, "admin", "", title, f"{provider_id}: {message}",
                type_="subscription", related_id=related,
                priority="high" if state in {"grace", "expired"} else "normal",
                action_text="فتح الاشتراك", action_route=f"admin:subscription:{subscription.get('id', '')}",
            )
    released = RequestMarketplace(con).release_due()
    create_marketplace_notifications(con, released)
    scan_expirations(con)
    return {"stateChanges": len(state_changes), "releasedRequests": len(released)}


def catalog_snapshot(con):
    categories = []
    for category in con.execute(
        """SELECT id,icon,ar,en,active FROM categories
        WHERE COALESCE(deleted_at,'')='' ORDER BY rowid"""
    ):
        item = dict(category)
        item["active"] = bool(item["active"])
        item["services"] = [
            dict(service) | {"active": bool(service["active"])}
            for service in con.execute(
                """SELECT id,icon,ar,en,active FROM services
                WHERE category_id=? AND COALESCE(deleted_at,'')='' ORDER BY rowid""",
                (category["id"],),
            )
        ]
        categories.append(item)
    return categories


def catalog_reference_count(con, category_id, service_id=""):
    service_value = f"{category_id}|{service_id}" if service_id else ""
    request_sql = (
        "SELECT COUNT(*) n FROM customer_requests "
        "WHERE status!='deleted' AND service_value=?"
        if service_id
        else "SELECT COUNT(*) n FROM customer_requests "
        "WHERE status!='deleted' AND service_value LIKE ?"
    )
    request_value = service_value if service_id else f"{category_id}|%"
    count = int(con.execute(request_sql, (request_value,)).fetchone()["n"] or 0)
    lead_sql = (
        "SELECT COUNT(*) n FROM leads WHERE status!='deleted' AND service_value=?"
        if service_id
        else "SELECT COUNT(*) n FROM leads WHERE status!='deleted' AND service_value LIKE ?"
    )
    count += int(con.execute(lead_sql, (request_value,)).fetchone()["n"] or 0)
    for row in con.execute("SELECT services FROM providers"):
        for item in jload(row["services"], []):
            if not isinstance(item, dict) or item.get("catId") != category_id:
                continue
            if not service_id or item.get("serviceId") == service_id:
                count += 1
    for row in con.execute("SELECT payload FROM provider_requests"):
        payload = jload(row["payload"], {})
        for item in payload.get("services", []) if isinstance(payload, dict) else []:
            if not isinstance(item, dict) or item.get("catId") != category_id:
                continue
            if not service_id or item.get("serviceId") == service_id:
                count += 1
    return count


def get_bootstrap(session=None):
    with db() as con:
        maintenance = run_domain_maintenance(con)
        verification_service = ProviderVerificationService(con)
        expired_verifications = verification_service.expire_managed_cases()
        for provider_id in expired_verifications:
            create_notification(
                con,
                "provider",
                provider_id,
                "انتهت صلاحية التحقق",
                "تم إيقاف الظهور واستقبال الطلبات الجديدة حتى تحديث وثائق التحقق.",
                type_="verification",
                related_id=provider_id,
                priority="high",
                action_text="تحديث التحقق",
                action_route="provider:account:verification",
            )
            create_notification(
                con,
                "admin",
                "",
                "انتهت صلاحية تحقق مزود",
                f"يحتاج المزود {provider_id} إلى مراجعة وثائقه قبل إعادة تفعيله.",
                type_="verification",
                related_id=provider_id,
                priority="high",
                action_text="فتح مركز الثقة",
                action_route=f"admin:trust:{provider_id}",
            )
        community_maintenance = run_community_maintenance(con)
        for item in community_maintenance["warnings"]:
            target_kind = "user" if item["owner_kind"] == "user" else "provider"
            create_notification(
                con,
                target_kind,
                item["owner_id"],
                "إعلانك في المجتمع أوشك على الانتهاء",
                f"{item['title']} • بقي أقل من 3 أيام",
                type_="community",
                related_id=item["id"],
                action_text="فتح الإعلان",
                action_route=f"{target_kind}:community:{item['id']}",
            )
        for item in community_maintenance["expired"]:
            target_kind = "user" if item["owner_kind"] == "user" else "provider"
            create_notification(
                con,
                target_kind,
                item["owner_id"],
                "انتهى إعلانك في المجتمع",
                item["title"],
                type_="community",
                related_id=item["id"],
                action_text="مراجعة الإعلان",
                action_route=f"{target_kind}:community:{item['id']}",
            )
        categories = catalog_snapshot(con)
        is_admin = bool(session and session.get("kind") == "admin")
        is_provider = bool(session and session.get("kind") == "provider")
        is_pending_provider = bool(session and session.get("kind") == "provider_pending")
        is_user = bool(session and session.get("kind") == "user")
        if is_admin:
            provider_rows = con.execute(
                "SELECT * FROM providers ORDER BY featured DESC,quality_score DESC,rating DESC"
            )
        elif is_provider:
            provider_rows = con.execute(
                """SELECT * FROM providers WHERE id=? OR (
                active=1 AND verified=1 AND status!='unavailable' AND COALESCE(listing_enabled,1)=1)
                ORDER BY featured DESC,quality_score DESC,rating DESC""",
                (session["providerId"],),
            )
        else:
            provider_rows = con.execute(
                """SELECT * FROM providers WHERE active=1 AND verified=1 AND status!='unavailable'
                AND COALESCE(listing_enabled,1)=1
                ORDER BY featured DESC,quality_score DESC,rating DESC"""
            )
        providers = [
            row_provider(
                r,
                private=is_admin or bool(is_provider and r["id"] == session.get("providerId")),
                sign_private=is_admin or bool(is_provider and r["id"] == session.get("providerId")),
            )
            for r in provider_rows
        ]
        company_ids = [
            provider["id"]
            for provider in providers
            if provider.get("providerType") == "company"
        ]
        if company_ids:
            placeholders = ",".join("?" for _ in company_ids)
            team_counts = {
                row["provider_id"]: int(row["n"] or 0)
                for row in con.execute(
                    f"""SELECT provider_id,COUNT(*) n FROM provider_team_members
                    WHERE active=1 AND provider_id IN ({placeholders})
                    GROUP BY provider_id""",  # nosec B608 - placeholders are generated, not user input
                    company_ids,
                )
            }
            branch_counts = {
                row["provider_id"]: int(row["n"] or 0)
                for row in con.execute(
                    f"""SELECT provider_id,COUNT(*) n FROM provider_branches
                    WHERE active=1 AND provider_id IN ({placeholders})
                    GROUP BY provider_id""",  # nosec B608 - placeholders are generated, not user input
                    company_ids,
                )
            }
            for provider in providers:
                if provider.get("providerType") != "company":
                    continue
                provider["companySummary"] = {
                    "teams": team_counts.get(provider["id"], 0),
                    "branches": branch_counts.get(provider["id"], 0),
                    "serviceCount": len(provider.get("services") or []),
                    "coverageCount": len(provider.get("areas") or []),
                    "responseMinutes": int(provider.get("responseMinutes") or 30),
                }
        for provider in providers:
            verification = verification_service.get(
                provider["id"],
                private=bool(
                    is_admin
                    or (
                        is_provider
                        and provider["id"] == session.get("providerId")
                    )
                ),
            )
            if verification:
                provider["verification"] = verification
                provider["verified"] = bool(verification.get("verified"))
        requests = []
        if has_permission(session, "review_requests") or is_pending_provider:
            if is_pending_provider:
                request_rows = con.execute(
                    "SELECT * FROM provider_requests WHERE id=?",
                    (session.get("requestId", ""),),
                )
            else:
                request_rows = con.execute(
                    "SELECT * FROM provider_requests ORDER BY created_at DESC"
                )
            for r in request_rows:
                payload = jload(r["payload"], {})
                payload["services"] = payload.get("services", [])
                if not payload["services"] and "|" in payload.get("service", ""):
                    cat_id, service_id = payload["service"].split("|", 1)
                    payload["services"] = [{"id": f"pending-{payload.get('id','')}", "catId": cat_id, "serviceId": service_id, "priceFrom": payload.get("priceFrom", 0), "active": True, "areas": [payload.get("wilayah", "")]}]
                requests.append(provider_request_view(payload, r["created_at"]))
        platform_row = con.execute("SELECT value FROM settings WHERE key='platform'").fetchone()
        platform_settings = jload(platform_row["value"], {}) if platform_row else {}
        public_setting_keys = {
            "nameAr", "nameEn", "defaultGov", "adIntervalSeconds", "displayScale",
            "uiMode", "maxHomeProviders", "maxPopularServices", "maxRequestMatches",
            "loyaltyEnabled", "requestBoardEnabled", "contactApprovalRequired",
            "loyaltyCampaignActive", "loyaltyTargetPoints", "loyaltyCampaignAr",
            "loyaltyCampaignEn", "loyaltyCampaignNoteAr", "loyaltyCampaignNoteEn",
            "loyaltyTargetRequests", "loyaltyCycleMode",
            "subscriptionsEnabled", "paymentGatewayEnabled", "serviceAreas",
            "deviceNotifications", "mergeNotifications",
            "communityEnabled", "communityPackagesEnabled", "communityBoardEnabled",
            "communityProviderOffersEnabled", "communityUserRecommendationsEnabled",
            "communityModerationRequired",
            "communityWantedExpiryDays", "communityPackageExpiryDays",
            "communityFirstPackageFreeDays", "communityRenewalFee",
        }
        settings = platform_settings if is_admin else {
            key: value for key, value in platform_settings.items() if key in public_setting_keys
        }
        location_catalog = location_snapshot(con, include_inactive=is_admin)
        settings = dict(settings)
        settings["serviceAreas"] = [
            {
                "id": area["id"],
                "ar": area["ar"],
                "en": area["en"],
                "active": area["active"],
                "w": [
                    [wilayah["ar"], wilayah["en"], wilayah["id"]]
                    for wilayah in area["w"]
                    if is_admin or wilayah["active"]
                ],
            }
            for area in location_catalog
            if is_admin or area["active"]
        ]
        packages = [
            row_package(r) for r in con.execute(
                "SELECT * FROM packages WHERE active=1 AND COALESCE(legacy,0)=0 ORDER BY price,duration_days"
            )
        ]
        complaint_service = ComplaintCaseService(con)
        if is_admin:
            reviews = [row_review(r, private=True) for r in con.execute("SELECT * FROM reviews ORDER BY created_at DESC")]
            complaints = complaint_service.list_admin()
            subscriptions = [row_subscription(r) for r in con.execute("SELECT * FROM subscriptions ORDER BY created_at DESC")]
            payments = [row_payment(r) for r in con.execute("SELECT * FROM payments ORDER BY created_at DESC")]
            audits = [row_audit(r) for r in con.execute("SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT 80")]
            leads = [row_lead(r) for r in con.execute("SELECT * FROM leads ORDER BY created_at DESC LIMIT 120")]
        elif is_provider:
            pid = session["providerId"]
            reviews = [
                row_review(r)
                for r in con.execute(
                    """SELECT * FROM reviews WHERE provider_id=? AND approved=1
                    AND COALESCE(deleted_at,'')='' ORDER BY created_at DESC""",
                    (pid,),
                )
            ]
            complaints = complaint_service.list_for_provider(pid)
            subscriptions = [row_subscription(r) for r in con.execute("SELECT * FROM subscriptions WHERE provider_id=? ORDER BY created_at DESC", (pid,))]
            payments = [row_payment(r) for r in con.execute("SELECT * FROM payments WHERE provider_id=? ORDER BY created_at DESC", (pid,))]
            leads = [row_lead(r) for r in con.execute("SELECT * FROM leads WHERE provider_id=? ORDER BY created_at DESC LIMIT 80", (pid,))]
            current_provider = next((p for p in providers if p["id"] == pid), None)
            if current_provider:
                open_requests = [row_lead(r) for r in con.execute("SELECT * FROM leads WHERE kind='request' AND COALESCE(provider_id,'')='' AND status NOT IN ('cancelled','deleted','closed') ORDER BY created_at DESC LIMIT 120")]
                matched = [lead for lead in open_requests if lead_matches_provider(lead, current_provider)]
                leads = leads + matched[:40]
            audits = []
        else:
            reviews = [
                row_review(r)
                for r in con.execute(
                    """SELECT * FROM reviews WHERE approved=1
                    AND COALESCE(deleted_at,'')='' ORDER BY created_at DESC"""
                )
            ]
            complaints, subscriptions, payments, audits, leads = [], [], [], [], []
        all_customer_requests = [
            row_customer_request(r, sign_private=bool(is_admin or is_provider or is_user))
            for r in con.execute(
                """SELECT * FROM customer_requests
                WHERE status!='deleted' ORDER BY created_at DESC LIMIT 300"""
            )
        ]
        marketplace_requests = [
            marketplace_request(item, include_note=is_user)
            for item in all_customer_requests
            if item.get("status") in ACTIVE_REQUEST_STATES and item.get("offersOpen", True)
        ]
        if is_admin or is_provider:
            marketplace_requests = []
        if is_admin:
            customer_requests = [
                request_with_workflow(con, item, asset_visible=True)
                for item in all_customer_requests
            ]
            for item in customer_requests:
                item["providerSuggestions"] = request_suggestions(con, item["id"], include_hidden=True)
            notifications = [
                row_notification(r)
                for r in con.execute(
                    """SELECT * FROM app_notifications
                    WHERE target_kind='admin' ORDER BY created_at DESC LIMIT 300"""
                )
            ]
            users = [
                row_app_user(r, private=True, sign_private=True)
                for r in con.execute("SELECT * FROM app_users ORDER BY last_login DESC LIMIT 300")
            ]
            advertisements = [
                row_advertisement(r)
                for r in con.execute("SELECT * FROM advertisements ORDER BY created_at DESC")
            ]
        elif is_provider:
            pid = session["providerId"]
            customer_requests = [
                item for item in all_customer_requests
                if pid in item["matchingProviderIds"] or item["acceptedProviderId"] == pid
            ]
            customer_requests = [
                request_with_workflow(
                    con,
                    item,
                    asset_visible=item.get("acceptedProviderId") == pid,
                )
                for item in customer_requests
            ]
            consent_service = ContactConsentService(con)
            for item in customer_requests:
                accepted_for_provider = item.get("acceptedProviderId") == pid
                item["locationPrecision"] = (
                    "exact" if accepted_for_provider else "area"
                )
                if not accepted_for_provider:
                    item["location"] = None
                    item["locationText"] = ""
                consent = consent_service.summary(item["id"], pid)
                item["contactConsent"] = consent
                if not accepted_for_provider or not (
                    consent.get("whatsapp") or consent.get("call")
                ):
                    item["phone"] = ""
            request_lookup = {item.get("id"): item for item in customer_requests}
            for lead in leads:
                linked = request_lookup.get(lead.get("request_id") or lead.get("requestId") or lead.get("id"))
                consent = (linked or {}).get("contactConsent") or {}
                if not linked or linked.get("acceptedProviderId") != pid or not (
                    consent.get("whatsapp") or consent.get("call")
                ):
                    lead["phone"] = ""
            notifications = [
                row_notification(r)
                for r in con.execute(
                    """SELECT * FROM app_notifications
                    WHERE target_kind='provider' AND target_id=? ORDER BY created_at DESC LIMIT 160""",
                    (pid,),
                )
            ]
            users = []
            advertisements = [
                row_advertisement(r)
                for r in con.execute(
                    "SELECT * FROM advertisements WHERE active=1 AND COALESCE(deleted_at,'')='' ORDER BY created_at DESC"
                )
            ]
        elif is_user:
            uid = session["userId"]
            complaints = complaint_service.list_for_user(uid)
            customer_requests = [
                request_with_workflow(con, item, asset_visible=True)
                for item in all_customer_requests
                if item["userId"] == uid
            ]
            for item in customer_requests:
                item["providerSuggestions"] = request_suggestions(con, item["id"])
            marketplace_requests = [item for item in marketplace_requests if item["id"] not in {request["id"] for request in customer_requests}]
            for item in marketplace_requests:
                item["mySuggestedProviderIds"] = [
                    row["provider_id"]
                    for row in con.execute(
                        """SELECT provider_id FROM request_provider_suggestions
                        WHERE request_id=? AND suggested_by_user_id=? AND status IN ('active','selected')""",
                        (item["id"], uid),
                    )
                ]
            consent_service = ContactConsentService(con)
            for item in customer_requests:
                if item.get("acceptedProviderId"):
                    item["contactConsent"] = consent_service.summary(
                        item["id"], item["acceptedProviderId"]
                    )
                    if item["contactConsent"].get("whatsapp") or item["contactConsent"].get("call"):
                        contact_row = con.execute(
                            "SELECT phone FROM providers WHERE id=?",
                            (item["acceptedProviderId"],),
                        ).fetchone()
                        if contact_row:
                            item["providerContact"] = {"phone": contact_row["phone"]}
            notifications = [
                row_notification(r)
                for r in con.execute(
                    """SELECT * FROM app_notifications
                    WHERE target_kind='user' AND target_id=? ORDER BY created_at DESC LIMIT 160""",
                    (uid,),
                )
            ]
            user_row = con.execute("SELECT * FROM app_users WHERE id=?", (uid,)).fetchone()
            users = [row_app_user(user_row, private=True, sign_private=True)] if user_row else []
            service_assets = ServiceAssetService(con).list_for_user(uid)
            for asset in service_assets:
                if asset.get("imagePath"):
                    asset["imageUrl"] = secure_media_url(asset["imagePath"])
                    asset.pop("imagePath", None)
            advertisements = [
                row_advertisement(r)
                for r in con.execute(
                    "SELECT * FROM advertisements WHERE active=1 AND COALESCE(deleted_at,'')='' ORDER BY created_at DESC"
                )
            ]
        else:
            customer_requests, notifications, users = [], [], []
            service_assets = []
            advertisements = [
                row_advertisement(r)
                for r in con.execute(
                    "SELECT * FROM advertisements WHERE active=1 AND COALESCE(deleted_at,'')='' ORDER BY created_at DESC"
                )
            ]
        payment_revenue = con.execute(
            """SELECT COALESCE(SUM(amount),0) n FROM payments
            WHERE kind IN ('revenue','subscription','promotion') AND status='paid'"""
        ).fetchone()["n"]
        finance_revenue = con.execute("SELECT COALESCE(SUM(amount),0) n FROM finance WHERE kind='revenue'").fetchone()["n"]
        stats = {
            "providers": len(providers),
            "activeProviders": len([p for p in providers if p["active"]]),
            "requests": con.execute("SELECT COUNT(*) n FROM provider_requests").fetchone()["n"],
            "leads": con.execute("SELECT COUNT(*) n FROM leads").fetchone()["n"],
            "revenue": payment_revenue + finance_revenue,
            "reviews": con.execute(
                "SELECT COUNT(*) n FROM reviews WHERE approved=1 AND COALESCE(deleted_at,'')=''"
            ).fetchone()["n"],
            "openComplaints": con.execute(
                """SELECT COUNT(*) n FROM complaints
                WHERE status NOT IN ('resolved','closed','rejected')"""
            ).fetchone()["n"],
            "activeSubscriptions": con.execute("SELECT COUNT(*) n FROM subscriptions WHERE status='active'").fetchone()["n"],
            "qualityAverage": round(con.execute("SELECT COALESCE(AVG(quality_score),0) n FROM providers").fetchone()["n"], 1),
            "whatsappLogs": con.execute("SELECT COUNT(*) n FROM whatsapp_logs").fetchone()["n"],
            "users": con.execute("SELECT COUNT(*) n FROM app_users WHERE status='active'").fetchone()["n"],
            "customerRequests": con.execute(
                "SELECT COUNT(*) n FROM customer_requests WHERE status!='deleted'"
            ).fetchone()["n"],
            "unavailableRequests": con.execute(
                "SELECT COUNT(*) n FROM customer_requests WHERE status='unavailable'"
            ).fetchone()["n"],
            "unreadNotifications": con.execute(
                """SELECT COUNT(*) n FROM app_notifications
                WHERE target_kind='admin' AND is_read=0"""
            ).fetchone()["n"] if is_admin else len([n for n in notifications if not n["read"]]),
        }
        if not is_admin:
            stats = {
                key: stats[key]
                for key in ("providers", "activeProviders", "reviews", "qualityAverage", "unreadNotifications")
            }
        reports = {
            "topProviders": sorted(
                [{"id": p["id"], "name": p["name"], "rating": p["rating"], "qualityScore": p["qualityScore"], "stats": p["stats"]} for p in providers],
                key=lambda p: (p["qualityScore"], p["rating"], p["stats"].get("whatsapp", 0)),
                reverse=True,
            )[:8],
            "qualityQueue": [
                {"id": p["id"], "name": p["name"], "qualityScore": p["qualityScore"], "rating": p["rating"], "reviews": p["reviews"]}
                for p in providers if p["qualityScore"] < 65 or p["reviews"] == 0
            ][:12],
            "subscriptionRevenue": payment_revenue,
            "complaintsByStatus": {
                row["status"]: row["n"] for row in con.execute("SELECT status, COUNT(*) n FROM complaints GROUP BY status")
            },
        }
        if not is_admin:
            reports = {}
        admin_entities = {}
        financial_metrics = {}
        if is_admin:
            if has_permission(session, "manage_providers"):
                admin_entities["verificationCases"] = (
                    verification_service.list_admin()
                )
            if has_permission(session, "manage_quality"):
                admin_entities["trustStatistics"] = trust_statistics(con)
            state_counts = {
                row["status"]: int(row["n"])
                for row in con.execute("SELECT status,COUNT(*) n FROM subscriptions GROUP BY status")
            }
            recurring = con.execute(
                """SELECT COALESCE(SUM(p.price * 30.4375 / NULLIF(p.duration_days,0)),0) mrr
                FROM subscriptions s JOIN packages p ON p.id=s.package_id
                WHERE s.status IN ('foundation','active','expiring','grace') AND p.price>0"""
            ).fetchone()["mrr"]
            subscriber_count = con.execute(
                """SELECT COUNT(DISTINCT provider_id) n FROM payments
                WHERE status='paid' AND amount>0"""
            ).fetchone()["n"]
            failed_payments = con.execute(
                "SELECT COUNT(*) n FROM payments WHERE status IN ('failed','cancelled')"
            ).fetchone()["n"]
            paid_requests = con.execute(
                "SELECT COUNT(*) n FROM subscriptions WHERE status='pending_payment'"
            ).fetchone()["n"]
            paid_activated = con.execute(
                """SELECT COUNT(*) n FROM subscriptions WHERE amount>0
                AND status IN ('active','expiring','grace','expired','cancelled','refunded')"""
            ).fetchone()["n"]
            churned = sum(state_counts.get(key, 0) for key in ("expired", "cancelled", "refunded"))
            subscription_total = sum(state_counts.values()) or 1
            financial_metrics = {
                "currency": OMR,
                "mrr": round(float(recurring or 0), 3),
                "arr": round(float(recurring or 0) * 12, 3),
                "averageRevenuePerProvider": round(float(payment_revenue or 0) / max(1, int(subscriber_count or 0)), 3),
                "failedPayments": int(failed_payments or 0),
                "conversionRate": round(100 * int(paid_activated or 0) / max(1, int(paid_activated or 0) + int(paid_requests or 0)), 1),
                "churnRate": round(100 * churned / subscription_total, 1),
                "subscriptionStates": state_counts,
            }
            if has_permission(session, "manage_admins"):
                recovery_items = []
                for recovery in con.execute(
                    """SELECT id,account_kind,account_id,phone,attempts,expires_at,used_at,created_at
                    FROM password_recoveries ORDER BY created_at DESC LIMIT 120"""
                ):
                    if recovery["account_kind"] == "provider":
                        account = con.execute(
                            "SELECT name FROM providers WHERE id=?",
                            (recovery["account_id"],),
                        ).fetchone()
                    else:
                        account = con.execute(
                            "SELECT name FROM app_users WHERE id=?",
                            (recovery["account_id"],),
                        ).fetchone()
                    recovery_items.append({
                        "id": recovery["id"],
                        "accountKind": recovery["account_kind"],
                        "accountId": recovery["account_id"],
                        "name": account["name"] if account else "",
                        "phone": recovery["phone"],
                        "attempts": int(recovery["attempts"] or 0),
                        "expiresAt": recovery["expires_at"],
                        "usedAt": recovery["used_at"],
                        "createdAt": recovery["created_at"],
                    })
                admin_entities["passwordRecoveries"] = recovery_items
            if has_permission(session, "manage_subscriptions"):
                admin_entities.update({
                    "subscriptionEvents": [dict(r) for r in con.execute(
                        "SELECT * FROM subscription_events ORDER BY created_at DESC LIMIT 300"
                    )],
                    "legacyPackages": [row_package(r) for r in con.execute(
                        "SELECT * FROM packages WHERE COALESCE(legacy,0)=1 ORDER BY rowid DESC"
                    )],
                    "coupons": [dict(r) | {"active": bool(r["active"]), "appliesTo": jload(r["applies_to"], [])} for r in con.execute(
                        "SELECT * FROM coupons ORDER BY created_at DESC"
                    )],
                })
            if has_permission(session, "manage_finance"):
                admin_entities["invoices"] = [dict(r) for r in con.execute(
                    "SELECT * FROM invoices ORDER BY issued_at DESC LIMIT 300"
                )]
            if has_permission(session, "manage_campaigns"):
                admin_entities.update({
                    "campaigns": [dict(r) | {"rules": jload(r["rules"], {})} for r in con.execute(
                        "SELECT * FROM campaigns ORDER BY created_at DESC"
                    )],
                    "rewardCampaigns": RewardCampaignService(con).list_admin(),
                    "campaignEligibility": RewardCampaignService(con).eligibility_queue(),
                    "promotions": [dict(r) for r in con.execute(
                        "SELECT * FROM provider_promotions ORDER BY created_at DESC"
                    )],
                })
            if has_permission(session, "manage_team"):
                admin_entities.update({
                    "teamMembers": [
                        {k: v for k, v in dict(r).items() if k != "pin_hash"}
                        for r in con.execute("SELECT * FROM provider_team_members ORDER BY created_at DESC")
                    ],
                    "branches": [dict(r) for r in con.execute(
                        "SELECT * FROM provider_branches ORDER BY created_at DESC"
                    )],
                })
            if has_permission(session, "manage_consent"):
                admin_entities["contactConsents"] = [dict(r) for r in con.execute(
                    "SELECT * FROM contact_consents ORDER BY updated_at DESC LIMIT 300"
                )]
        for complaint in complaints:
            for evidence in complaint.get("evidence", []):
                media_path = evidence.pop("mediaPath", "")
                evidence["mediaUrl"] = secure_media_url(media_path)
        reward_campaigns = []
        user_loyalty = {}
        interaction_blocks = []
        if is_user:
            interaction_blocks = InteractionBlockService(con).list_for(
                "user", session["userId"]
            )
        elif is_provider:
            interaction_blocks = InteractionBlockService(con).list_for(
                "provider", session["providerId"]
            )
        reward_service = RewardCampaignService(con)
        if is_user:
            reward_campaigns = reward_service.for_subject("user", session["userId"])
            try:
                loyalty_target = int(platform_settings.get("loyaltyTargetRequests", 8) or 8)
            except (TypeError, ValueError):
                loyalty_target = 8
            user_loyalty = loyalty_summary(
                con,
                session["userId"],
                target=max(1, loyalty_target),
                cycle_mode=(
                    "repeat"
                    if platform_settings.get("loyaltyCycleMode") == "repeat"
                    else "cap"
                ),
            )
        elif is_provider:
            current_provider = next(
                (item for item in providers if item["id"] == session["providerId"]),
                {},
            )
            subject_kind = (
                "company"
                if current_provider.get("providerType") == "company"
                else "provider"
            )
            reward_campaigns = reward_service.for_subject(
                subject_kind, session["providerId"]
            )
        conversation_actor_kind = (
            "admin" if is_admin else "user" if is_user else "provider" if is_provider else ""
        )
        conversation_actor_id = (
            session.get("id", "") if is_admin else
            session.get("userId", "") if is_user else
            session.get("providerId", "") if is_provider else ""
        )
        if conversation_actor_kind:
            conversation_service = ConversationControlService(con)
            for request_item in customer_requests:
                if not request_item.get("acceptedProviderId"):
                    continue
                try:
                    request_item["conversationControl"] = conversation_service.summary(
                        request_item["id"],
                        conversation_actor_kind,
                        conversation_actor_id,
                    )
                except DomainError:
                    request_item["conversationControl"] = {
                        "requestId": request_item["id"],
                        "status": "open",
                        "muted": False,
                    }
        community = community_snapshot_view(CommunityService(con).snapshot(session))
        platform = platform_snapshot(con, session)
        data = {
            "categories": categories,
            "providers": providers,
            "requests": requests,
            "reviews": reviews,
            "complaints": complaints,
            "packages": packages,
            "subscriptions": subscriptions,
            "payments": payments,
            "leads": leads,
            "auditLogs": audits,
            "customerRequests": customer_requests,
            "marketplaceRequests": marketplace_requests,
            "notifications": notifications,
            "users": users,
            "advertisements": advertisements,
            "serviceAssets": service_assets if is_user else [],
            "locationCatalog": location_catalog,
            "rewardCampaigns": reward_campaigns,
            "loyaltySummary": user_loyalty,
            "communityListings": community.get("listings", []),
            "communitySettings": community.get("settings", {}),
            "communityFavorites": community.get("favorites", []),
            "communityStats": community.get("stats", {}),
            "communityReports": community.get("reports", []) if is_admin else [],
            "interactionBlocks": interaction_blocks,
            "platform": platform,
            "serverTime": datetime.now(UTC).isoformat(),
            "serviceAvailability": service_availability_snapshot(con),
            "providerInsights": provider_operational_insights(
                con, session["providerId"]
            ) if is_provider else {},
            "demandGaps": admin_demand_gaps(con) if is_admin else [],
            "settings": settings,
            "appConfig": {
                "nameAr": "خدماتي",
                "nameEn": "Khadamati App",
                "supportEmail": SUPPORT_EMAIL,
                "policyVersion": POLICY_VERSION,
                "currency": OMR,
            },
            "stats": stats,
            "reports": reports,
            "financialMetrics": financial_metrics,
            "adminEntities": admin_entities,
            "maintenance": {
                **maintenance,
                "community": community_maintenance,
            } if is_admin else {},
            "integrations": {
                "whatsappConfigured": whatsapp_configured(),
                "paymentConfigured": PaymentAdapter(con).configured,
                "otpDeliveryConfigured": whatsapp_configured() or (
                    APP_ENV != "production" and bool(os.environ.get("KHADAMATI_DEV_OTP_CODE"))
                ),
                "postgresReady": False,
                "databaseEngine": "sqlite",
            },
            "permissions": ALL_PERMISSIONS if is_admin else [],
        }
        if session and session.get("kind") == "admin":
            data["adminUser"] = {k: session[k] for k in ("id", "name", "role", "permissions")}
            if has_permission(session, "manage_admins"):
                data["adminUsers"] = [admin_public(r) for r in con.execute("SELECT * FROM admin_users ORDER BY created_at")]
        elif is_user and users:
            data["currentUser"] = users[0]
        if is_provider:
            provider_id = session["providerId"]
            data["providerEntitlements"] = EntitlementService(con).for_provider(provider_id)
            data["currentProvider"] = next((p for p in providers if p["id"] == provider_id), None)
            data["providerSession"] = {
                "role": session.get("role", "provider_owner"),
                "memberId": session.get("memberId", ""),
                "permissions": session.get("providerPermissions", []),
            }
            data["providerTeam"] = [
                {k: v for k, v in dict(r).items() if k != "pin_hash"}
                for r in con.execute(
                    "SELECT * FROM provider_team_members WHERE provider_id=? ORDER BY created_at",
                    (provider_id,),
                )
            ]
            data["providerBranches"] = [
                dict(r) for r in con.execute(
                    "SELECT * FROM provider_branches WHERE provider_id=? ORDER BY created_at",
                    (provider_id,),
                )
            ]
        return data


def get_classic_state():
    with db() as con:
        row = con.execute("SELECT value FROM settings WHERE key='classicState'").fetchone()
        if not row:
            return None
        return jload(row["value"], None)


def save_classic_state(data):
    if not isinstance(data, dict):
        raise ValueError("state_must_be_object")
    data["serverSavedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with db() as con:
        con.execute(
            "INSERT INTO settings(key,value) VALUES('classicState', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (jdump(data),),
        )
    return data["serverSavedAt"]


def whatsapp_configured():
    return bool(os.environ.get("WHATSAPP_ACCESS_TOKEN") and os.environ.get("WHATSAPP_PHONE_NUMBER_ID"))


def smtp_configured():
    return all(
        str(os.environ.get(key, "")).strip()
        for key in ("KHADAMATI_SMTP_HOST", "KHADAMATI_SMTP_USER", "KHADAMATI_SMTP_PASSWORD")
    )


def send_recovery_email(to, account_name, code):
    address = safe_text(to, 254).strip().lower()
    if not smtp_configured() or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", address):
        return {"ok": False, "configured": smtp_configured(), "channel": "email"}
    host = str(os.environ.get("KHADAMATI_SMTP_HOST", "")).strip()
    user = str(os.environ.get("KHADAMATI_SMTP_USER", "")).strip()
    password = str(os.environ.get("KHADAMATI_SMTP_PASSWORD", "")).strip()
    sender = str(os.environ.get("KHADAMATI_SMTP_FROM_EMAIL", user or SUPPORT_EMAIL)).strip()
    sender_name = str(os.environ.get("KHADAMATI_SMTP_FROM_NAME", "إدارة خدماتي | Khadamati")).strip()
    try:
        port = int(os.environ.get("KHADAMATI_SMTP_PORT", "587"))
    except ValueError:
        port = 587
    use_ssl = environment_flag("KHADAMATI_SMTP_USE_SSL", port == 465)
    use_tls = environment_flag("KHADAMATI_SMTP_USE_TLS", not use_ssl)
    message = EmailMessage()
    message["Subject"] = "رمز استعادة حساب خدماتي | Khadamati recovery code"
    message["From"] = formataddr((sender_name, sender))
    message["To"] = address
    display_name = safe_text(account_name, 100) or "مستخدم خدماتي"
    message.set_content(
        f"مرحباً {display_name}،\n\n"
        f"رمز التحقق المؤقت لاستعادة حساب خدماتي هو: {code}\n"
        "صالح لمدة 10 دقائق. لا تشارك هذا الرمز مع أي شخص.\n\n"
        "Hello,\n\n"
        f"Your temporary Khadamati account recovery code is: {code}\n"
        "It expires in 10 minutes. Do not share this code with anyone.\n"
    )
    try:
        client_class = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
        kwargs = {"host": host, "port": port, "timeout": 12}
        if use_ssl:
            kwargs["context"] = ssl.create_default_context()
        with client_class(**kwargs) as client:
            client.ehlo()
            if use_tls:
                client.starttls(context=ssl.create_default_context())
                client.ehlo()
            client.login(user, password)
            client.send_message(message)
        log_event("recovery.email_sent", destination=f"***@{address.rsplit('@', 1)[-1]}")
        return {"ok": True, "configured": True, "channel": "email"}
    except (OSError, smtplib.SMTPException) as err:
        log_event("recovery.email_failed", level="warning", errorType=type(err).__name__)
        return {"ok": False, "configured": True, "channel": "email"}


def send_admin_login_email(code):
    """Send a short-lived administrator login code without exposing it elsewhere."""
    address = safe_text(ADMIN_EMAIL, 254).strip().lower()
    if not smtp_configured() or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", address):
        return {"ok": False, "configured": smtp_configured(), "channel": "email"}
    host = str(os.environ.get("KHADAMATI_SMTP_HOST", "")).strip()
    user = str(os.environ.get("KHADAMATI_SMTP_USER", "")).strip()
    password = str(os.environ.get("KHADAMATI_SMTP_PASSWORD", "")).strip()
    sender = str(os.environ.get("KHADAMATI_SMTP_FROM_EMAIL", user or SUPPORT_EMAIL)).strip()
    sender_name = str(os.environ.get("KHADAMATI_SMTP_FROM_NAME", "إدارة خدماتي | Khadamati")).strip()
    try:
        port = int(os.environ.get("KHADAMATI_SMTP_PORT", "587"))
    except ValueError:
        port = 587
    use_ssl = environment_flag("KHADAMATI_SMTP_USE_SSL", port == 465)
    use_tls = environment_flag("KHADAMATI_SMTP_USE_TLS", not use_ssl)
    message = EmailMessage()
    message["Subject"] = "رمز دخول إدارة خدماتي | Khadamati admin code"
    message["From"] = formataddr((sender_name, sender))
    message["To"] = address
    message.set_content(
        "رمز الدخول المؤقت إلى إدارة خدماتي هو: " + code + "\n"
        "صالح لمدة 10 دقائق ولمحاولة دخول واحدة. لا تشاركه مع أي شخص.\n\n"
        "Your temporary Khadamati admin sign-in code is: " + code + "\n"
        "It expires in 10 minutes and can be used once. Do not share it.\n"
    )
    try:
        client_class = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
        kwargs = {"host": host, "port": port, "timeout": 12}
        if use_ssl:
            kwargs["context"] = ssl.create_default_context()
        with client_class(**kwargs) as client:
            client.ehlo()
            if use_tls:
                client.starttls(context=ssl.create_default_context())
                client.ehlo()
            client.login(user, password)
            client.send_message(message)
        log_event("admin.email_code_sent", destination=f"***@{address.rsplit('@', 1)[-1]}")
        return {"ok": True, "configured": True, "channel": "email"}
    except (OSError, smtplib.SMTPException) as err:
        log_event("admin.email_code_failed", level="warning", errorType=type(err).__name__)
        return {"ok": False, "configured": True, "channel": "email"}


def mask_email(address):
    value = safe_text(address, 254).strip().lower()
    if "@" not in value:
        return ""
    local, domain = value.rsplit("@", 1)
    visible = local[:2] if len(local) > 2 else local[:1]
    return f"{visible}***@{domain}"


def log_whatsapp(target, status, detail):
    digits = normalize_phone(target)
    masked_target = f"***{digits[-4:]}" if len(digits) >= 4 else "***"
    try:
        with db() as con:
            con.execute(
                "INSERT INTO whatsapp_logs VALUES(?,?,?,?,CURRENT_TIMESTAMP)",
                (slug("wa"), masked_target, status, safe_text(detail, 900)),
            )
    except sqlite3.OperationalError as err:
        log_event(
            "whatsapp.audit_log_skipped",
            level="warning",
            errorType=type(err).__name__,
        )


def send_whatsapp(to, text):
    target = normalize_phone(to)
    token = os.environ.get("WHATSAPP_ACCESS_TOKEN")
    phone_id = os.environ.get("WHATSAPP_PHONE_NUMBER_ID")
    version = os.environ.get("WHATSAPP_API_VERSION", "v20.0")
    if not token or not phone_id or not target:
        return {"ok": False, "configured": False}
    if not re.fullmatch(r"v\d{1,2}\.\d{1,2}", version) or not str(phone_id).isdigit():
        return {"ok": False, "configured": False, "error": "invalid_whatsapp_configuration"}
    payload = {
        "messaging_product": "whatsapp",
        "to": target,
        "type": "text",
        "text": {"preview_url": False, "body": text[:3500]},
    }
    connection = http.client.HTTPSConnection("graph.facebook.com", 443, timeout=12)
    try:
        connection.request(
            "POST", f"/{version}/{phone_id}/messages", body=jdump(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        response = connection.getresponse()
        body = response.read().decode("utf-8", errors="replace")
        try:
            response_data = jload(body, {})
        except json.JSONDecodeError:
            response_data = {}
        if 200 <= response.status < 300:
            messages = response_data.get("messages", []) if isinstance(response_data, dict) else []
            message_id = (
                safe_text(messages[0].get("id"), 160)
                if messages and isinstance(messages[0], dict)
                else ""
            )
            log_whatsapp(
                target,
                "sent",
                jdump({"status": response.status, "messageId": message_id}),
            )
            return {"ok": True, "configured": True, "channel": "whatsapp", "messageId": message_id}
        error_data = response_data.get("error", {}) if isinstance(response_data, dict) else {}
        log_whatsapp(
            target,
            "failed",
            jdump(
                {
                    "status": response.status,
                    "code": error_data.get("code", ""),
                    "type": safe_text(error_data.get("type"), 120),
                }
            ),
        )
        return {"ok": False, "configured": True, "error": "gateway_rejected"}
    except Exception as err:
        log_whatsapp(target, "failed", str(err))
        return {"ok": False, "configured": True, "error": "delivery_failed"}
    finally:
        connection.close()


def save_upload_data(owner_id, data_url, slot, allowed_mimes, max_bytes):
    if not data_url:
        return ""
    if not data_url.startswith("data:") or ";base64," not in data_url:
        raise ValueError("invalid_upload")
    head, raw = data_url.split(";base64,", 1)
    # Safari may include codec parameters in MediaRecorder data URLs.
    mime = head.replace("data:", "").split(";", 1)[0].strip().lower()
    ext = allowed_mimes.get(mime)
    if not ext:
        raise ValueError("unsupported_upload_type")
    blob = base64.b64decode(raw, validate=True)
    if len(blob) > max_bytes:
        raise ValueError("upload_too_large")
    if not upload_signature_matches(mime, blob):
        raise ValueError("upload_content_mismatch")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_owner = "".join(ch for ch in str(owner_id) if ch.isalnum() or ch in ("_", "-"))[:60] or "file"
    safe_slot = "".join(ch for ch in str(slot) if ch.isalnum() or ch in ("_", "-"))[:40] or secrets.token_hex(4)
    filename = f"{safe_owner}-{safe_slot}-{secrets.token_hex(12)}.{ext}"
    rel = f"uploads/{filename}"
    (UPLOAD_DIR / filename).write_bytes(blob)
    return rel


def save_data_url(provider_id, image_data):
    return save_upload_data(provider_id, image_data, "avatar", IMAGE_MIMES, 2_500_000)


def save_many_images(owner_id, images, prefix="work", limit=5):
    paths = []
    for i, image_data in enumerate((images or [])[:limit], 1):
        if image_data:
            paths.append(save_upload_data(owner_id, image_data, f"{prefix}{i}", IMAGE_MIMES, 2_500_000))
    return paths


def save_many_documents(owner_id, docs, prefix="doc", limit=3):
    paths = []
    for i, doc_data in enumerate((docs or [])[:limit], 1):
        if doc_data:
            paths.append(save_upload_data(owner_id, doc_data, f"{prefix}{i}", DOCUMENT_MIMES, 5_000_000))
    return paths


def upsert_provider(con, data):
    p = data | {"id": data.get("id") or slug("p")}
    p["id"] = safe_text(p["id"], 120)
    p["name"] = safe_text(data.get("name", ""), 120)
    p["phone"] = normalize_phone(data.get("phone", ""))
    existing = con.execute("SELECT * FROM providers WHERE id=?", (p["id"],)).fetchone()
    existing_provider = row_provider(existing, private=True) if existing else {}
    if not p["id"] or not p["name"] or len(p["phone"]) < 8:
        raise DomainError("provider_identity_required", 400)
    p["status"] = safe_text(data.get("status", existing_provider.get("status", "available")), 30)
    if p["status"] not in {"available", "busy", "unavailable", "under_review", "pending", "suspended", "deleted"}:
        raise DomainError("invalid_provider_status", 400)
    p["providerType"] = safe_text(
        data.get("providerType", existing_provider.get("providerType", "individual")), 30
    )
    if p["providerType"] not in {"individual", "company"}:
        raise DomainError("invalid_provider_type", 400)
    image_path = data.get("imagePath") or ""
    if data.get("imageData"):
        image_path = save_data_url(p["id"], data["imageData"])
    elif not image_path:
        image_path = existing_provider.get("imagePath", "")
    pin_hash = data.get("pinHash") or ""
    if data.get("pin"):
        pin_hash = hash_pin(data["pin"])
    if not pin_hash:
        pin_hash = existing["pin_hash"] if existing else ""
    default_package_id = PlanCatalog.foundation_for(p["providerType"])
    package_id = data.get(
        "packageId", existing_provider.get("packageId", default_package_id)
    )
    package = PlanCatalog.get(con, package_id, False) or PlanCatalog.get(
        con, default_package_id, False
    )
    account_limits = PlanCatalog.account_limits(package, p["providerType"])
    raw_governorates = data.get(
        "governorates",
        existing_provider.get("governorates")
        or [data.get("gov") or existing_provider.get("gov", "")],
    )
    if not isinstance(raw_governorates, list):
        raise DomainError("governorates_must_be_list", 400)
    p["governorates"] = list(
        dict.fromkeys(
            safe_text(item, 80) for item in raw_governorates if safe_text(item, 80)
        )
    )
    primary_governorate = safe_text(
        data.get("gov", existing_provider.get("gov", "")), 80
    )
    if primary_governorate and primary_governorate not in p["governorates"]:
        p["governorates"].insert(0, primary_governorate)
    existing_governorate_count = len(existing_provider.get("governorates") or [])
    governorate_limit = max(
        1,
        int(account_limits.get("maxGovernorates") or 1),
        existing_governorate_count if existing else 0,
    )
    if len(p["governorates"]) > governorate_limit:
        raise DomainError("governorate_limit_exceeded", 409)
    existing_services = existing_provider.get("services", [])
    existing_categories = {
        item.get("catId")
        for item in existing_services
        if isinstance(item, dict) and item.get("catId")
    }
    service_limit = max(
        1,
        int(account_limits.get("maxServices") or 1),
        len(existing_services) if existing else 0,
    )
    category_limit = max(
        1,
        int(account_limits.get("maxCategories") or 1),
        len(existing_categories) if existing else 0,
    )
    raw_services = data.get("services", existing_services)
    p["services"] = normalized_provider_services(
        con,
        raw_services,
        limit=service_limit,
        category_limit=category_limit,
        fallback_price=data.get("priceFrom", existing_provider.get("priceFrom", 0)),
        default_areas=data.get("areas")
        or existing_provider.get("areas")
        or [data.get("wilayah") or existing_provider.get("wilayah", "")],
    )
    image_limit = max(1, int(account_limits.get("maxImages") or 5))
    work_images = data.get("workImages") or existing_provider.get("workImages", [])
    if data.get("workImagesData"):
        new_images = save_many_images(
            p["id"], data.get("workImagesData"),
            f"work{int(time.time())}-", max(0, image_limit - len(work_images)),
        )
        work_images = list(dict.fromkeys([*work_images, *new_images]))
    card_image = data.get("cardImage") or existing_provider.get("cardImage", "") or image_url(image_path)
    if isinstance(card_image, str) and card_image.startswith("data:"):
        if card_image == data.get("imageData"):
            card_image = image_url(image_path)
        else:
            source_images = data.get("workImagesData") or []
            try:
                card_image = image_url(work_images[source_images.index(card_image)])
            except (ValueError, IndexError):
                card_image = image_url(image_path)
    documents = data.get("documents") or existing_provider.get("documents", [])
    if data.get("documentsData"):
        new_documents = save_many_documents(
            p["id"], data.get("documentsData"),
            f"doc{int(time.time())}-", max(0, 6 - len(documents)),
        )
        documents = list(dict.fromkeys([*documents, *new_documents]))[:6]
    before_after = data.get("beforeAfter")
    if before_after is None:
        before_after = existing_provider.get("beforeAfter", [])
    before_after = [
        {
            **item,
            "before": str(item.get("before", "")).lstrip("/")
            if str(item.get("before", "")).startswith("/uploads/")
            else item.get("before", ""),
            "after": str(item.get("after", "")).lstrip("/")
            if str(item.get("after", "")).startswith("/uploads/")
            else item.get("after", ""),
        }
        for item in before_after
        if isinstance(item, dict) and item.get("before") and item.get("after")
    ][:8]
    pair_data = data.get("beforeAfterData") or {}
    if pair_data.get("before") and pair_data.get("after"):
        pair_id = slug("compare")
        pair_paths = save_many_images(
            p["id"], [pair_data["before"], pair_data["after"]], pair_id, 2
        )
        if len(pair_paths) == 2:
            before_after.append(
                {
                    "id": pair_id,
                    "before": pair_paths[0],
                    "after": pair_paths[1],
                    "caption": str(pair_data.get("caption", "") or "")[:120],
                    "createdAt": datetime.now(UTC).isoformat(),
                }
            )
            before_after = before_after[-8:]
    intro_video_url = data.get(
        "introVideoUrl", existing_provider.get("introVideoUrl", "")
    )
    if data.get("introVideoData"):
        intro_video_url = save_upload_data(
            p["id"], data["introVideoData"], "intro", VIDEO_MIMES, 12_000_000
        )
    if isinstance(intro_video_url, str) and intro_video_url.startswith("/uploads/"):
        intro_video_url = intro_video_url.lstrip("/")
    location = normalized_location(data.get("location") or existing_provider.get("location") or {})
    con.execute(
        """INSERT INTO providers(id,name,phone,gov,wilayah,areas,bio,hours,status,active,verified,featured,
        package_id,rating,reviews,admin_note,image_path,card_image,pin_hash,services,work_images,documents,quality_score,response_score,
        subscription_until,subscription_start,provider_type,company_name,company_id,commercial_no,
        verification_expiry,commercial_expiry,license_expiry,latitude,longitude,location_updated_at,
        map_visible,primary_service_id,stats)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET name=excluded.name,phone=excluded.phone,gov=excluded.gov,
        wilayah=excluded.wilayah,areas=excluded.areas,bio=excluded.bio,hours=excluded.hours,status=excluded.status,
        active=excluded.active,verified=excluded.verified,featured=excluded.featured,package_id=excluded.package_id,
        rating=excluded.rating,reviews=excluded.reviews,admin_note=excluded.admin_note,image_path=excluded.image_path,card_image=excluded.card_image,
        pin_hash=excluded.pin_hash,services=excluded.services,work_images=excluded.work_images,documents=excluded.documents,
        quality_score=excluded.quality_score,response_score=excluded.response_score,subscription_until=excluded.subscription_until,
        subscription_start=excluded.subscription_start,provider_type=excluded.provider_type,
        company_name=excluded.company_name,company_id=excluded.company_id,commercial_no=excluded.commercial_no,
        verification_expiry=excluded.verification_expiry,commercial_expiry=excluded.commercial_expiry,
        license_expiry=excluded.license_expiry,latitude=excluded.latitude,longitude=excluded.longitude,
        location_updated_at=excluded.location_updated_at,map_visible=excluded.map_visible,
        primary_service_id=excluded.primary_service_id""",
        (
            p["id"], p.get("name", ""), p.get("phone", ""), p.get("gov", ""), p.get("wilayah", ""),
            jdump(p.get("areas", [])), p.get("bio", ""), p.get("hours", ""), p.get("status", "available"),
            int(bool(p.get("active", True))), int(bool(p.get("verified", False))), int(bool(p.get("featured", False))),
            package_id,
            finite_number(p.get("rating", existing_provider.get("rating", 0)), minimum=0, maximum=5),
            int(finite_number(p.get("reviews", existing_provider.get("reviews", 0)), minimum=0, maximum=10_000_000)),
            p.get("adminNote", ""), image_path, card_image, pin_hash, jdump(p.get("services", [])), jdump(work_images), jdump(documents),
            int(finite_number(p.get("qualityScore", existing_provider.get("qualityScore", 60)), default=60, minimum=0, maximum=100)),
            int(finite_number(p.get("responseScore", existing_provider.get("responseScore", 70)), default=70, minimum=0, maximum=100)),
            p.get("subscriptionUntil", existing_provider.get("subscriptionUntil", "")),
            p.get("subscriptionStart", existing_provider.get("subscriptionStart", "")),
            p.get("providerType", existing_provider.get("providerType", "individual")),
            p.get("companyName", existing_provider.get("companyName", "")),
            p.get("companyId", existing_provider.get("companyId", "")),
            p.get("commercialNo", existing_provider.get("commercialNo", "")),
            p.get("verificationExpiry", existing_provider.get("verificationExpiry", "")),
            p.get("commercialExpiry", existing_provider.get("commercialExpiry", "")),
            p.get("licenseExpiry", existing_provider.get("licenseExpiry", "")),
            location.get("lat"),
            location.get("lng"),
            location.get("updatedAt", ""),
            int(bool(p.get("mapVisible", existing_provider.get("mapVisible", True)))),
            safe_text(
                p.get("primaryServiceId", existing_provider.get("primaryServiceId", "")), 80
            ),
            jdump(p.get("stats", existing_provider.get("stats", {"views": 0, "whatsapp": 0, "calls": 0}))),
        ),
    )
    con.execute(
        """UPDATE providers SET before_after=?,intro_video_url=?,availability=?,governorates=?,
        response_minutes=?,completed_jobs=?,gender=?,email=?,age=?,nationality=?,location_sharing_expires_at=?
        WHERE id=?""",
        (
            jdump(before_after), intro_video_url,
            jdump(p.get("availability", existing_provider.get("availability", {}))),
            jdump(p.get("governorates", existing_provider.get("governorates", []))),
            int(finite_number(p.get("responseMinutes", existing_provider.get("responseMinutes", 30)), default=30, minimum=0, maximum=100_000)),
            int(finite_number(p.get("completedJobs", existing_provider.get("completedJobs", 0)), minimum=0, maximum=100_000_000)),
            p.get("gender", existing_provider.get("gender", "not_specified"))
            if p.get("gender", existing_provider.get("gender", "not_specified"))
            in {"male", "female", "not_specified"}
            else "not_specified",
            safe_text(p.get("email", existing_provider.get("email", "")), 160).strip().lower(),
            int(finite_number(p.get("age", existing_provider.get("age", 0)), minimum=0, maximum=120)),
            safe_text(p.get("nationality", existing_provider.get("nationality", "")), 80),
            safe_text(
                p.get(
                    "locationSharingExpiresAt",
                    existing_provider.get("locationSharingExpiresAt", ""),
                ),
                80,
            ),
            p["id"],
        ),
    )
    p["imagePath"] = image_path
    p["cardImage"] = card_image
    p["workImages"] = work_images
    p["documents"] = documents
    p["beforeAfter"] = before_after
    p["introVideoUrl"] = image_url(intro_video_url)
    p["governorates"] = p.get("governorates", [])
    recompute_provider_quality(con, p["id"])
    return p


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        self.request_id = secrets.token_hex(12)
        self.request_started = time.monotonic()
        super().__init__(*args, directory=str(PUBLIC_DIR), **kwargs)

    def handle_one_request(self):
        self.request_id = secrets.token_hex(12)
        self.request_started = time.monotonic()
        return super().handle_one_request()

    def log_request(self, code="-", size="-"):
        try:
            status = int(code)
        except (TypeError, ValueError):
            status = code
        try:
            response_bytes = int(size)
        except (TypeError, ValueError):
            response_bytes = None
        log_event(
            "http.request",
            requestId=self.request_id,
            method=safe_text(getattr(self, "command", ""), 16),
            path=safe_text(urlparse(getattr(self, "path", "")).path, 500),
            status=status,
            responseBytes=response_bytes,
            durationMs=round((time.monotonic() - self.request_started) * 1000, 2),
        )

    def log_message(self, format_, *args):
        log_event(
            "http.message",
            requestId=self.request_id,
            message=safe_text(format_ % args, 400),
        )

    def log_error(self, format_, *args):
        log_event(
            "http.error",
            requestId=self.request_id,
            level="warning",
            message=safe_text(format_ % args, 400),
        )

    def end_headers(self):
        path = urlparse(self.path).path
        self.send_header("X-Request-ID", self.request_id)
        if path.startswith(("/api/", "/media/", "/uploads/")):
            self.send_header("Cache-Control", "no-store")
        elif path.endswith((".css", ".js", ".webp", ".png", ".svg", ".woff", ".woff2")):
            self.send_header("Cache-Control", "public, max-age=86400")
        else:
            self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Permissions-Policy", "camera=(), geolocation=(self), microphone=(self)")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'self'; "
            "script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https:; "
            "img-src 'self' data: blob: https:; media-src 'self' data: blob: https:; "
            "font-src 'self' data: https:; connect-src 'self' https://khadamati-app-api.onrender.com https:; "
            "frame-src https://www.openstreetmap.org; worker-src 'self' blob:; manifest-src 'self'",
        )
        if self.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip() == "https":
            self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        origin = self.headers.get("Origin", "").rstrip("/")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header(
                "Access-Control-Allow-Headers",
                "Authorization, Content-Type, X-Khadamati-API-Key",
            )
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Expose-Headers", "X-Request-ID")
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Vary", "Origin")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def send_json(self, data, status=200, extra_headers=None):
        raw = jdump(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        for name, value in extra_headers or []:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(raw)

    def request_origin_allowed(self):
        origin = str(self.headers.get("Origin", "") or "").rstrip("/")
        return not origin or origin in ALLOWED_ORIGINS

    def refresh_cookie_header(self, kind, value, *, clear=False):
        kind = "provider" if kind == "provider_pending" else kind
        name = REFRESH_COOKIE_NAMES.get(kind)
        if not name:
            return ""
        secure = (
            APP_ENV == "production"
            or self.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip()
            == "https"
        )
        parts = [
            f"{name}={'' if clear else value}",
            "Path=/api/",
            "HttpOnly",
            f"Max-Age={0 if clear else SESSION_DAYS * 86400}",
            "SameSite=None" if secure else "SameSite=Lax",
        ]
        if secure:
            parts.append("Secure")
        return "; ".join(parts)

    def send_session_json(self, payload, session_bundle, status=200):
        kind = session_bundle.get("kind", "")
        cookie = self.refresh_cookie_header(
            kind, session_bundle.pop("refreshToken", "")
        )
        response = {
            **payload,
            "token": session_bundle["token"],
            "accessExpiresAt": session_bundle["accessExpiresAt"],
            "sessionKind": kind,
        }
        headers = [("Set-Cookie", cookie)] if cookie else []
        return self.send_json(response, status, headers)

    def send_bytes(self, raw, content_type, filename=None, status=200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(raw)

    def read_json(self):
        path = urlparse(self.path).path
        raw = self.read_raw(JSON_LIMITS.get(path, DEFAULT_JSON_LIMIT))
        if not raw:
            return {}
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise DomainError("json_object_required", 400)
        return value

    def read_raw(self, max_bytes=1_000_000):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except (TypeError, ValueError) as error:
            raise DomainError("invalid_content_length", 400) from error
        if length < 0 or length > max_bytes:
            raise DomainError("request_too_large", 413)
        return self.rfile.read(length) if length else b""

    def client_key(self):
        forwarded = self.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
        remote = forwarded or (self.client_address[0] if self.client_address else "unknown")
        return hashlib.sha256(remote.encode("utf-8")).hexdigest()[:32]

    def send_domain_error(self, error):
        return self.send_json(
            {"error": error.code, "detail": error.detail or ""}, error.status
        )

    def session(self):
        return token_session(self.headers)

    def require_admin(self, permission="view_reports"):
        session = self.session()
        if not has_permission(session, permission):
            self.send_json({"error": "permission_denied", "permission": permission}, 403)
            return None
        return session

    def require_provider(self, permission=""):
        session = self.session()
        if not session or session.get("kind") != "provider":
            self.send_json({"error": "provider_auth_required"}, 401)
            return None
        role = session.get("role", "provider_owner")
        selected = set(session.get("providerPermissions") or [])
        allowed = PROVIDER_ROLE_PERMISSIONS.get(role, set()) | selected
        if permission and permission not in allowed:
            self.send_json({"error": "provider_permission_denied", "permission": permission}, 403)
            return None
        return session

    def require_user(self):
        session = self.session()
        if not session or session.get("kind") != "user":
            self.send_json({"error": "user_auth_required"}, 401)
            return None
        return session

    def send_upload(self, path):
        filename = upload_filename(path)
        if not filename or "/" in filename or "\\" in filename:
            return self.send_error(404)
        target = (UPLOAD_DIR / filename).resolve()
        try:
            target.relative_to(UPLOAD_DIR.resolve())
        except ValueError:
            return self.send_error(404)
        if not target.is_file():
            return self.send_error(404)
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(str(target))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(target.stat().st_size))
        self.end_headers()
        with target.open("rb") as f:
            self.copyfile(f, self.wfile)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/healthz":
            return self.send_json(
                {"ok": True, "service": "khadamati-api", "release": APP_RELEASE}
            )
        if path == "/readyz":
            issues = []
            if APP_ENV == "production":
                if not os.environ.get("KHADAMATI_DB_PATH"):
                    issues.append("database_path_not_configured")
                if not os.environ.get("KHADAMATI_UPLOAD_DIR"):
                    issues.append("upload_path_not_configured")
                if not os.environ.get("KHADAMATI_BACKUP_DIR"):
                    issues.append("backup_path_not_configured")
                if not (
                    os.environ.get("KHADAMATI_MEDIA_SIGNING_KEY")
                    or os.environ.get("KHADAMATI_OTP_PEPPER")
                ):
                    issues.append("media_signing_key_not_configured")
                if REQUIRE_ADMIN_2FA and not os.environ.get("KHADAMATI_ADMIN_2FA_KEY"):
                    issues.append("admin_2fa_key_not_configured")
            try:
                with db() as con:
                    con.execute("SELECT 1").fetchone()
                    if REQUIRE_ADMIN_2FA:
                        enabled_admins = con.execute(
                            """SELECT COUNT(*) n FROM admin_users
                            WHERE active=1 AND two_factor_enabled=1"""
                        ).fetchone()["n"]
                        if not int(enabled_admins or 0):
                            issues.append("admin_2fa_not_configured")
            except sqlite3.Error:
                issues.append("database_unavailable")
            storage_writable = (
                os.access(DB_PATH.parent, os.W_OK)
                and os.access(UPLOAD_DIR, os.W_OK)
            )
            if not storage_writable:
                issues.append("storage_not_writable")
            ready = not issues
            return self.send_json(
                {
                    "ok": ready,
                    "service": "khadamati-api",
                    "release": APP_RELEASE,
                    "database": "sqlite",
                    "issues": issues,
                },
                200 if ready else 503,
            )
        if path.startswith("/media/"):
            filename = path.removeprefix("/media/")
            query = parse_qs(parsed.query)
            if not valid_media_signature(
                filename,
                (query.get("exp") or [""])[0],
                (query.get("sig") or [""])[0],
            ):
                return self.send_json({"error": "media_link_invalid_or_expired"}, 403)
            return self.send_upload(path)
        if path.startswith("/uploads/"):
            if is_private_upload(path):
                session = self.session()
                if not (
                    has_permission(session, "review_requests")
                    or has_permission(session, "manage_providers")
                ):
                    return self.send_json({"error": "private_media_requires_signed_url"}, 403)
            return self.send_upload(path)
        if path.startswith("/share/provider/"):
            provider_id = path.rsplit("/", 1)[-1]
            with db() as con:
                row = con.execute("SELECT * FROM providers WHERE id=? AND active=1", (provider_id,)).fetchone()
            if not row:
                return self.send_error(404)
            provider = row_provider(row)
            host = self.headers.get("Host", "localhost")
            scheme = self.headers.get("X-Forwarded-Proto", "http").split(",", 1)[0]
            image_path = provider.get("cardImage") or provider.get("imageUrl") or "/app-icon-512.png"
            image = image_path if str(image_path).startswith("http") else f"{scheme}://{host}{image_path}"
            target = f"{PUBLIC_APP_URL}#provider={provider_id}"
            page = f"""<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
            <meta name="viewport" content="width=device-width,initial-scale=1">
            <title>{html.escape(provider['name'])} | خدماتي</title>
            <meta property="og:type" content="profile"><meta property="og:site_name" content="خدماتي">
            <meta property="og:title" content="{html.escape(provider['name'])}">
            <meta property="og:description" content="{html.escape(provider.get('bio') or 'مزود خدمة على منصة خدماتي')}">
            <meta property="og:image" content="{html.escape(image)}">
            <meta property="og:url" content="{html.escape(f'{scheme}://{host}{path}')}">
            <style>body{{font-family:Arial;background:#f4f6fb;color:#102a43;display:grid;place-items:center;min-height:100vh}}
            a{{background:#168f7a;color:white;padding:14px 22px;border-radius:12px;text-decoration:none;font-weight:bold}}</style>
            <meta http-equiv="refresh" content="1;url={html.escape(target)}"></head>
            <body><a href="{html.escape(target)}">فتح بطاقة {html.escape(provider['name'])} في خدماتي</a></body></html>"""
            return self.send_bytes(page.encode("utf-8"), "text/html; charset=utf-8")
        if path == "/api/classic-state":
            session = self.require_admin("manage_settings")
            if not session:
                return
            state = get_classic_state()
            return self.send_json({"ok": True, "state": state})
        if path == "/api/bootstrap":
            return self.send_json(get_bootstrap(self.session()))
        if path == "/api/config":
            return self.send_json({
                "nameAr": "خدماتي",
                "nameEn": "Khadamati App",
                "supportEmail": SUPPORT_EMAIL,
                "policyVersion": POLICY_VERSION,
                "currency": OMR,
                "publicUrl": PUBLIC_APP_URL,
            })
        if path == "/api/push/public-key":
            return self.send_json({"publicKey": os.environ.get("VAPID_PUBLIC_KEY", "")})
        if path == "/api/admin/session":
            session = self.require_admin()
            if not session:
                return
            return self.send_json(get_bootstrap(session))
        if path == "/api/provider/me":
            session = self.require_provider()
            if not session:
                return
            with db() as con:
                row = con.execute("SELECT * FROM providers WHERE id=?", (session["providerId"],)).fetchone()
                if not row:
                    return self.send_json({"error": "not_found"}, 404)
                return self.send_json({"provider": row_provider(row, private=True, sign_private=True)})
        if path == "/api/backup":
            session = self.require_admin("backup")
            if not session:
                return
            return self.send_json(get_bootstrap(session))
        if path == "/api/enterprise/v1/summary":
            api_key = self.headers.get("X-Khadamati-API-Key", "")
            try:
                with db() as con:
                    client_service = EnterpriseAPIService(con)
                    client = client_service.authenticate(api_key, "reports:read")
                    if not FeatureFlagService(con).is_enabled(
                        "enterprise_api", "organization", client["organizationId"]
                    ):
                        return self.send_json({"error": "enterprise_api_disabled"}, 403)
                    organization = con.execute(
                        """SELECT id,name,organization_type,status FROM customer_organizations
                        WHERE id=?""",
                        (client["organizationId"],),
                    ).fetchone()
                    locations = [
                        {
                            "id": row["id"], "name": row["name"],
                            "gov": row["gov"], "wilayah": row["wilayah"],
                        }
                        for row in con.execute(
                            """SELECT id,name,gov,wilayah FROM organization_locations
                            WHERE organization_id=? AND active=1 ORDER BY name""",
                            (client["organizationId"],),
                        )
                    ]
                    statuses = {
                        row["status"]: int(row["n"])
                        for row in con.execute(
                            """SELECT status,COUNT(*) n FROM customer_requests
                            WHERE organization_id=? AND status!='deleted' GROUP BY status""",
                            (client["organizationId"],),
                        )
                    }
                    return self.send_json(
                        {
                            "organization": dict(organization) if organization else {},
                            "locations": locations,
                            "requestStatusCounts": statuses,
                            "generatedAt": datetime.now(UTC).isoformat(),
                        }
                    )
            except DomainError as err:
                return self.send_domain_error(err)
        if path.startswith("/api/reports/"):
            session = self.require_admin("view_reports")
            if not session:
                return
            return self.download_report(path)
        return super().do_GET()

    def download_report(self, path):
        lang = parse_qs(urlparse(self.path).query).get("lang", ["ar"])[0]
        lang = "en" if lang == "en" else "ar"
        is_ar = lang == "ar"
        labels = {
            "title": "تقرير خدماتي التشغيلي" if is_ar else "Khadamati operational report",
            "section": "القسم" if is_ar else "Section",
            "metric": "المؤشر" if is_ar else "Metric",
            "value": "القيمة" if is_ar else "Value",
            "note": "ملاحظة" if is_ar else "Note",
            "operations": "التشغيل" if is_ar else "Operations",
            "audience": "الجمهور" if is_ar else "Audience",
            "subscriptions": "الاشتراكات" if is_ar else "Subscriptions",
            "finance": "المال" if is_ar else "Finance",
            "quality": "الجودة" if is_ar else "Quality",
            "demand": "الطلب حسب المحافظة" if is_ar else "Demand by governorate",
            "status": "الطلبات حسب الحالة" if is_ar else "Requests by status",
            "providers": "أداء المزودين" if is_ar else "Provider performance",
            "generated": "تاريخ الإنشاء" if is_ar else "Generated",
            "privacy": (
                "هذا التقرير تشغيلي ولا يتضمن كلمات مرور أو وثائق سرية أو مواقع دقيقة."
                if is_ar else
                "This operational report excludes passwords, confidential documents, and precise locations."
            ),
        }
        with db() as con:
            scalar = lambda query, params=(): con.execute(query, params).fetchone()["n"]
            rows = [[labels["section"], labels["metric"], labels["value"], labels["note"]]]
            metrics = [
                (labels["audience"], "المستخدمون المسجلون" if is_ar else "Registered users", scalar("SELECT COUNT(*) n FROM app_users WHERE status='active'"), ""),
                (labels["audience"], "كل المزودين" if is_ar else "All providers", scalar("SELECT COUNT(*) n FROM providers"), ""),
                (labels["audience"], "المزودون الظاهرون" if is_ar else "Visible providers", scalar("SELECT COUNT(*) n FROM providers WHERE active=1 AND status!='unavailable'"), ""),
                (labels["audience"], "الشركات" if is_ar else "Companies", scalar("SELECT COUNT(*) n FROM providers WHERE provider_type='company'"), ""),
                (labels["operations"], "طلبات العملاء" if is_ar else "Customer requests", scalar("SELECT COUNT(*) n FROM customer_requests"), ""),
                (labels["operations"], "الطلبات المكتملة" if is_ar else "Completed requests", scalar("SELECT COUNT(*) n FROM customer_requests WHERE status IN ('closed','archived','completed')"), ""),
                (labels["operations"], "طلبات غير متاحة" if is_ar else "Unavailable requests", scalar("SELECT COUNT(*) n FROM customer_requests WHERE status='unavailable' OR waitlisted=1"), ""),
                (labels["operations"], "طلبات مزودين للمراجعة" if is_ar else "Provider applications pending", scalar("SELECT COUNT(*) n FROM provider_requests"), ""),
                (labels["subscriptions"], "اشتراكات نشطة" if is_ar else "Active subscriptions", scalar("SELECT COUNT(*) n FROM subscriptions WHERE status='active'"), ""),
                (labels["subscriptions"], "اشتراكات بانتظار الإجراء" if is_ar else "Subscriptions pending action", scalar("SELECT COUNT(*) n FROM subscriptions WHERE status IN ('pending','pending_payment','pending_approval')"), ""),
                (labels["subscriptions"], "اشتراكات منتهية" if is_ar else "Expired subscriptions", scalar("SELECT COUNT(*) n FROM subscriptions WHERE status='expired'"), ""),
                (labels["finance"], "الإيرادات المؤكدة" if is_ar else "Confirmed revenue", scalar("SELECT COALESCE(SUM(amount),0) n FROM payments WHERE kind IN ('revenue','subscription','promotion') AND status='paid'"), "OMR"),
                (labels["quality"], "متوسط تقييم المزودين" if is_ar else "Average provider rating", round(float(scalar("SELECT COALESCE(AVG(rating),0) n FROM providers")), 2), "5"),
                (labels["quality"], "شكاوى مفتوحة" if is_ar else "Open complaints", scalar("SELECT COUNT(*) n FROM complaints WHERE status!='closed'"), ""),
            ]
            rows.extend([list(item) for item in metrics])
            for item in con.execute(
                "SELECT COALESCE(NULLIF(gov,''), ?) name, COUNT(*) n FROM customer_requests GROUP BY COALESCE(NULLIF(gov,''), ?) ORDER BY n DESC LIMIT 20",
                (("غير محدد" if is_ar else "Not specified"),) * 2,
            ).fetchall():
                rows.append([labels["demand"], item["name"], item["n"], ""])
            for item in con.execute("SELECT status name, COUNT(*) n FROM customer_requests GROUP BY status ORDER BY n DESC").fetchall():
                rows.append([labels["status"], item["name"] or ("غير محدد" if is_ar else "Not specified"), item["n"], ""])
            for item in con.execute(
                "SELECT name, rating, reviews, quality_score FROM providers ORDER BY rating DESC, reviews DESC LIMIT 15"
            ).fetchall():
                detail = (
                    f"التقييم {float(item['rating'] or 0):.1f}/5 | المراجعات {item['reviews'] or 0} | الجودة {item['quality_score'] or 0}%"
                    if is_ar else
                    f"Rating {float(item['rating'] or 0):.1f}/5 | Reviews {item['reviews'] or 0} | Quality {item['quality_score'] or 0}%"
                )
                rows.append([labels["providers"], item["name"], item["reviews"] or 0, detail])
        stamp = datetime.now().strftime("%Y-%m-%d")
        if path.endswith(".csv"):
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerows(rows)
            raw = ("\ufeff" + output.getvalue()).encode("utf-8")
            return self.send_bytes(raw, "text/csv; charset=utf-8", f"khadamati-report-{stamp}.csv")
        if path.endswith(".docx"):
            rtl_props = "<w:rtl/>" if is_ar else ""
            bidi_props = "<w:bidi/>" if is_ar else ""
            table_rows = "".join(
                "<w:tr>" + "".join(
                    f'<w:tc><w:tcPr><w:tcW w:w="2400" w:type="dxa"/></w:tcPr><w:p><w:pPr>{bidi_props}</w:pPr><w:r><w:rPr>{rtl_props}</w:rPr><w:t>{html.escape(str(cell))}</w:t></w:r></w:p></w:tc>'
                    for cell in row
                ) + "</w:tr>"
                for row in rows
            )
            document = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                f'<w:body><w:p><w:pPr>{bidi_props}</w:pPr><w:r><w:rPr><w:b/><w:sz w:val="34"/>{rtl_props}</w:rPr><w:t>{html.escape(labels["title"])}</w:t></w:r></w:p>'
                f'<w:p><w:pPr>{bidi_props}</w:pPr><w:r><w:rPr>{rtl_props}</w:rPr><w:t>{html.escape(labels["generated"])}: {stamp}</w:t></w:r></w:p>'
                '<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/><w:tblBorders><w:top w:val="single" w:sz="4" w:color="B8C7D1"/><w:left w:val="single" w:sz="4" w:color="B8C7D1"/><w:bottom w:val="single" w:sz="4" w:color="B8C7D1"/><w:right w:val="single" w:sz="4" w:color="B8C7D1"/><w:insideH w:val="single" w:sz="3" w:color="D9E2EC"/><w:insideV w:val="single" w:sz="3" w:color="D9E2EC"/></w:tblBorders></w:tblPr>'
                f'{table_rows}</w:tbl><w:p><w:pPr>{bidi_props}</w:pPr><w:r><w:rPr>{rtl_props}</w:rPr><w:t>{html.escape(labels["privacy"])}</w:t></w:r></w:p><w:sectPr/></w:body></w:document>'
            )
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(
                    "[Content_Types].xml",
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                    '<Default Extension="xml" ContentType="application/xml"/>'
                    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                    "</Types>",
                )
                archive.writestr(
                    "_rels/.rels",
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                    "</Relationships>",
                )
                archive.writestr("word/document.xml", document)
            return self.send_bytes(
                stream.getvalue(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                f"khadamati-report-{stamp}.docx",
            )
        align = "right" if is_ar else "left"
        table_rows = "".join(
            "<tr>" + "".join(f"<td>{html.escape(str(cell))}</td>" for cell in row) + "</tr>"
            for row in rows[1:]
        )
        table_head = "".join(f"<th>{html.escape(str(cell))}</th>" for cell in rows[0])
        print_label = "طباعة أو حفظ PDF" if is_ar else "Print or save PDF"
        page = f"""<!doctype html><html lang="{lang}" dir="{'rtl' if is_ar else 'ltr'}"><head><meta charset="utf-8">
        <meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(labels['title'])}</title><style>
        @page{{size:A4;margin:16mm}}*{{box-sizing:border-box}}body{{font-family:Tahoma,Arial,sans-serif;max-width:960px;margin:32px auto;color:#102a43;padding:0 14px;line-height:1.55}}
        header{{display:flex;justify-content:space-between;gap:20px;align-items:center;border-bottom:3px solid #087f78;padding-bottom:16px;margin-bottom:18px}}h1{{margin:0;color:#075b58;font-size:25px}}header p{{color:#627d98;margin:4px 0 0}}
        table{{width:100%;border-collapse:collapse;font-size:12px}}th,td{{padding:9px;border:1px solid #d9e2ec;text-align:{align};vertical-align:top}}th{{background:#e7f5f2;color:#075b58}}tbody tr:nth-child(even){{background:#f8fafc}}
        button{{padding:10px 18px;border:0;border-radius:8px;background:#087f78;color:white;font:inherit;font-weight:700}}footer{{margin-top:16px;color:#829ab1;font-size:11px}}
        @media(max-width:640px){{body{{margin:12px auto}}table{{font-size:10px}}th,td{{padding:6px}}}}@media print{{button{{display:none}}body{{margin:0;padding:0}}}}
        </style></head><body><header><div><h1>{html.escape(labels['title'])}</h1><p>{html.escape(labels['generated'])}: {stamp}</p></div><button onclick="print()">{print_label}</button></header>
        <table><thead><tr>{table_head}</tr></thead><tbody>{table_rows}</tbody></table><footer>{html.escape(labels['privacy'])} • Khadamati App</footer></body></html>"""
        return self.send_bytes(page.encode("utf-8"), "text/html; charset=utf-8")

    def do_POST(self):
        try:
            return self._do_POST()
        except DomainError as err:
            return self.send_domain_error(err)
        except ValueError as err:
            code = str(err)
            if code not in {
                "state_must_be_object", "invalid_upload", "unsupported_upload_type",
                "upload_too_large", "upload_content_mismatch",
            }:
                code = "invalid_request_data"
            return self.send_json({"error": code}, 400)
        except (BrokenPipeError, ConnectionResetError):
            return None
        except Exception:
            return self.send_json({"error": "server_error"}, 500)

    def _do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/payments/webhook":
            try:
                raw = self.read_raw()
                signature = self.headers.get("X-Khadamati-Signature") or self.headers.get("X-Signature", "")
                with db() as con:
                    result = PaymentAdapter(con).verify_webhook(raw, signature)
                return self.send_json(result)
            except DomainError as err:
                return self.send_domain_error(err)
            except Exception:
                return self.send_json({"error": "invalid_webhook_payload"}, 400)
        try:
            data = self.read_json()
        except DomainError as err:
            return self.send_domain_error(err)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            return self.send_json({"error": "invalid_request_data"}, 400)
        if path == "/api/location/reverse":
            try:
                latitude = finite_number(
                    data.get("lat"), minimum=-90, maximum=90
                )
                longitude = finite_number(
                    data.get("lng"), minimum=-180, maximum=180
                )
            except DomainError as err:
                return self.send_domain_error(err)
            with db() as con:
                area = resolve_area(con, latitude, longitude)
            return self.send_json(
                {
                    "ok": True,
                    "area": area,
                    "location": {"lat": latitude, "lng": longitude},
                    "serverTime": datetime.now(UTC).isoformat(),
                }
            )
        if path in {"/api/auth/refresh", "/api/auth/persist", "/api/auth/logout"}:
            if not self.request_origin_allowed():
                return self.send_json({"error": "origin_not_allowed"}, 403)
        if path == "/api/auth/refresh":
            requested_kind = safe_text(data.get("kind"), 40)
            if requested_kind not in {"user", "provider", "admin"}:
                return self.send_json({"error": "invalid_session_kind"}, 400)
            bundle = refresh_session(self.headers, requested_kind)
            return self.send_session_json(
                {"ok": True, "session": bundle["session"]}, bundle
            )
        if path == "/api/auth/persist":
            requested_kind = safe_text(data.get("kind"), 40)
            if requested_kind not in {"", "user", "provider", "admin"}:
                return self.send_json({"error": "invalid_session_kind"}, 400)
            bundle = persist_access_session(self.headers, requested_kind)
            return self.send_session_json(
                {"ok": True, "session": bundle["session"]}, bundle
            )
        if path == "/api/auth/logout":
            requested_kind = safe_text(data.get("kind"), 40)
            revoked = revoke_session(self.headers)
            clear_kinds = (
                [requested_kind]
                if requested_kind in {"user", "provider", "admin"}
                else ["user", "provider", "admin"]
            )
            headers = [
                ("Set-Cookie", self.refresh_cookie_header(kind, "", clear=True))
                for kind in clear_kinds
            ]
            return self.send_json(
                {"ok": True, "revoked": revoked}, extra_headers=headers
            )
        if path == "/api/otp/request":
            purpose = str(data.get("purpose", "login") or "login")[:80]
            target_kind = str(data.get("targetKind", "user") or "user")[:40]
            if purpose not in {"login", "recovery", "delete_account", "change_pin"}:
                return self.send_json({"error": "invalid_otp_purpose"}, 400)
            if target_kind not in {"user", "provider", "company"}:
                return self.send_json({"error": "invalid_otp_target"}, 400)

            def deliver(phone, code):
                result = send_whatsapp(
                    phone,
                    f"رمز التحقق لخدماتي: {code}. لا تشارك الرمز مع أي شخص.",
                )
                return bool(result.get("ok"))

            try:
                with db() as con:
                    result = OTPService(
                        con, deliver=deliver if whatsapp_configured() else None
                    ).request(
                        data.get("phone", ""), purpose, target_kind
                    )
                return self.send_json({"ok": True, **result}, 201)
            except DomainError as err:
                return self.send_domain_error(err)
        if path == "/api/otp/verify":
            try:
                with db() as con:
                    result = OTPService(con).verify(
                        str(data.get("challengeId", "") or ""),
                        str(data.get("code", "") or ""),
                    )
                return self.send_json(result)
            except DomainError as err:
                return self.send_domain_error(err)
        if path == "/api/classic-state":
            session = self.require_admin("manage_settings")
            if not session:
                return
            try:
                saved_at = save_classic_state(data.get("state", data))
                return self.send_json({"ok": True, "savedAt": saved_at})
            except ValueError as err:
                return self.send_json({"error": str(err)}, 400)
        if path == "/api/admin/2fa/setup":
            try:
                with db() as con:
                    result = AdminTwoFactorService(con, ADMIN_2FA_KEY).confirm(
                        safe_text(data.get("challengeId"), 160),
                        data.get("code"),
                    )
                    row = con.execute(
                        "SELECT * FROM admin_users WHERE id=? AND active=1",
                        (result["adminId"],),
                    ).fetchone()
                    if not row:
                        return self.send_json({"error": "admin_account_not_found"}, 404)
                    user = admin_public(row)
                    log_audit(
                        con,
                        {"kind": "admin", "id": row["id"]},
                        "admin.two_factor.enabled",
                        row["id"],
                        "totp",
                    )
                bundle = issue_session_tokens(
                    {"kind": "admin", **user}, device_id=data.get("deviceId", "")
                )
                return self.send_session_json(
                    {"user": user, "recoveryCodes": result["recoveryCodes"]},
                    bundle,
                )
            except DomainError as err:
                return self.send_domain_error(err)
        if path == "/api/admin/email-code/request":
            return self.admin_email_code_request(data)
        if path == "/api/admin/login":
            challenge_id = safe_text(data.get("emailChallengeId", ""), 160)
            email_code = safe_text(data.get("emailCode", ""), 12)
            supplied_code = safe_text(data.get("code", ""), 128)
            lock_key = f"ip:{self.client_key()}"
            with db() as con:
                lock_state = login_failure_state(con, "admin", lock_key)
                if lock_state["locked"]:
                    return self.send_json(
                        {"error": "login_temporarily_locked", "retryAfter": lock_state["retryAfter"]},
                        429,
                    )
                if challenge_id or email_code:
                    challenge = con.execute(
                        """SELECT * FROM admin_email_challenges
                        WHERE id=? AND COALESCE(used_at,'')=''""",
                        (challenge_id,),
                    ).fetchone()
                    if not challenge:
                        return self.send_json({"error": "admin_email_code_not_found"}, 404)
                    try:
                        expires = datetime.fromisoformat(str(challenge["expires_at"]).replace("Z", "+00:00"))
                        if expires.tzinfo is None:
                            expires = expires.replace(tzinfo=UTC)
                    except ValueError:
                        expires = datetime.now(UTC) - timedelta(seconds=1)
                    if expires <= datetime.now(UTC):
                        return self.send_json({"error": "admin_email_code_expired"}, 410)
                    if int(challenge["attempts"] or 0) >= 5 or not verify_secret(email_code, challenge["code_hash"]):
                        con.execute(
                            "UPDATE admin_email_challenges SET attempts=attempts+1 WHERE id=?",
                            (challenge_id,),
                        )
                        attempts = record_login_failure(con, "admin", lock_key)
                        return self.send_json(
                            {"error": "admin_email_code_invalid", "attempts": attempts}, 403
                        )
                    row = con.execute(
                        "SELECT * FROM admin_users WHERE id=? AND active=1",
                        (challenge["admin_id"],),
                    ).fetchone()
                    if not row:
                        return self.send_json({"error": "admin_account_not_found"}, 404)
                    con.execute(
                        "UPDATE admin_email_challenges SET used_at=CURRENT_TIMESTAMP WHERE id=?",
                        (challenge_id,),
                    )
                else:
                    row = next(
                        (
                            candidate for candidate in con.execute("SELECT * FROM admin_users WHERE active=1")
                            if verify_secret(supplied_code, candidate["code_hash"])
                        ),
                        None,
                    )
                    if row and not str(row["code_hash"] or "").startswith("pbkdf2_sha256$"):
                        con.execute(
                            "UPDATE admin_users SET code_hash=? WHERE id=?",
                            (hash_pin(supplied_code), row["id"]),
                        )
                    if not row:
                        attempts = record_login_failure(con, "admin", lock_key)
                        return self.send_json({"error": "invalid_code", "attempts": attempts}, 403)
                    two_factor = AdminTwoFactorService(con, ADMIN_2FA_KEY)
                    if bool(row["two_factor_enabled"]):
                        if not data.get("twoFactorCode"):
                            return self.send_json(
                                {"ok": True, "twoFactorRequired": True, "message": "admin_2fa_required"}
                            )
                        if not two_factor.verify_admin(row, data.get("twoFactorCode")):
                            attempts = record_login_failure(con, "admin", lock_key)
                            return self.send_json({"error": "admin_2fa_invalid", "attempts": attempts}, 403)
                    elif REQUIRE_ADMIN_2FA:
                        setup = two_factor.begin(row["id"], row["name"])
                        return self.send_json({"ok": True, "twoFactorSetupRequired": True, **setup})
                clear_login_failures(con, "admin", lock_key)
            user = admin_public(row)
            bundle = issue_session_tokens(
                {"kind": "admin", **user}, device_id=data.get("deviceId", "")
            )
            return self.send_session_json({"user": user}, bundle)
        if path == "/api/provider/login":
            phone = normalize_phone(data.get("phone", ""))
            account_id = safe_text(data.get("accountId"), 120)
            if len(phone) < 11 and not account_id:
                return self.send_json({"error": "valid_phone_required"}, 400)
            pending_request = None
            provider_row = None
            pin = safe_text(data.get("pin", ""), 8)
            if not pin:
                pin = safe_text(data.get("code", ""), 8)
            with db() as con:
                lock_key = account_id or phone
                lock_state = login_failure_state(con, "provider", lock_key)
                if lock_state["locked"]:
                    return self.send_json(
                        {"error": "login_temporarily_locked", "retryAfter": lock_state["retryAfter"]},
                        429,
                    )
                if account_id:
                    provider_candidates = list(
                        con.execute(
                            """SELECT * FROM providers
                            WHERE id=? AND active=1 AND status!='deleted'""",
                            (account_id,),
                        )
                    )
                    if provider_candidates:
                        phone = normalize_phone(provider_candidates[0]["phone"])
                else:
                    provider_candidates = list(con.execute(
                        """SELECT * FROM providers WHERE active=1 AND status!='deleted'
                        AND (phone=? OR phone=?) ORDER BY created_at DESC""",
                        (phone, phone.replace("968", "", 1)),
                    ))
                if not provider_candidates and phone:
                    provider_candidates = [
                        candidate for candidate in con.execute(
                            "SELECT * FROM providers WHERE active=1 AND status!='deleted' ORDER BY created_at DESC"
                        )
                        if phone_matches(candidate["phone"], phone)
                    ]
                # A phone can legitimately own both a user account and a provider account, and
                # historical provider rows may share a normalized number. Select the row whose
                # hash verifies instead of failing against whichever row SQLite returned first.
                row = next(
                    (
                        candidate for candidate in provider_candidates
                        if candidate["pin_hash"] and verify_secret(pin, candidate["pin_hash"])
                    ),
                    provider_candidates[0] if provider_candidates else None,
                )
                team_row = None if account_id else con.execute(
                    """SELECT tm.*,p.name provider_name FROM provider_team_members tm
                    JOIN providers p ON p.id=tm.provider_id
                    WHERE tm.active=1 AND p.active=1 AND p.status!='deleted'
                    AND (tm.phone=? OR tm.phone=?) LIMIT 1""",
                    (phone, phone.replace("968", "", 1)),
                ).fetchone()
                if not team_row and not account_id:
                    team_row = next(
                        (
                            candidate
                            for candidate in con.execute(
                                """SELECT tm.*,p.name provider_name FROM provider_team_members tm
                                JOIN providers p ON p.id=tm.provider_id
                                WHERE tm.active=1 AND p.active=1 AND p.status!='deleted'"""
                            )
                            if phone_matches(candidate["phone"], phone)
                        ),
                        None,
                    )
                otp_ok = False
                if data.get("challengeId") and data.get("otpCode"):
                    try:
                        proof = OTPService(con).verify(
                            str(data.get("challengeId")), str(data.get("otpCode"))
                        )
                        otp_ok = (
                            proof["phone"] == phone
                            and proof["purpose"] == "login"
                            and proof["targetKind"] in {"provider", "company"}
                        )
                    except DomainError:
                        otp_ok = False
                owner_pin_ok = bool(row and row["pin_hash"] and verify_secret(pin, row["pin_hash"]))
                team_pin_ok = bool(
                    team_row and team_row["pin_hash"] and verify_secret(pin, team_row["pin_hash"])
                )
                if not (owner_pin_ok or team_pin_ok or otp_ok):
                    pending_rows = (
                        con.execute(
                            """SELECT id,payload,created_at FROM provider_requests
                            WHERE id=?""",
                            (account_id,),
                        )
                        if account_id
                        else con.execute(
                            """SELECT id,payload,created_at FROM provider_requests
                            ORDER BY created_at DESC"""
                        )
                    )
                    for request_row in pending_rows:
                        request_payload = jload(request_row["payload"], {})
                        if account_id and request_row["id"] == account_id:
                            phone = normalize_phone(request_payload.get("phone", ""))
                        if (
                            phone_matches(request_payload.get("phone", ""), phone)
                            and request_payload.get("pinHash")
                            and verify_secret(pin, request_payload["pinHash"])
                        ):
                            pending_request = provider_request_view(
                                request_payload, request_row["created_at"]
                            )
                            break
                if not (owner_pin_ok or team_pin_ok or otp_ok or pending_request):
                    attempts = record_login_failure(
                        con, "provider", lock_key, phone
                    )
                    return self.send_json(
                        {"error": "invalid_provider_login", "attempts": attempts},
                        403,
                    )
                if pending_request:
                    clear_login_failures(con, "provider", lock_key)
                elif row and (owner_pin_ok or otp_ok):
                    provider_row = row
                    provider_id = row["id"]
                    clear_login_failures(con, "provider", lock_key)
                    provider_role = "provider_owner"
                    provider_permissions = list(PROVIDER_ROLE_PERMISSIONS["provider_owner"])
                    member_id = ""
                elif team_row and (team_pin_ok or otp_ok):
                    provider_id = team_row["provider_id"]
                    provider_row = con.execute(
                        "SELECT * FROM providers WHERE id=? AND active=1 AND status!='deleted'",
                        (provider_id,),
                    ).fetchone()
                    clear_login_failures(con, "provider", lock_key)
                    if not str(team_row["pin_hash"] or "").startswith("pbkdf2_sha256$"):
                        con.execute(
                            "UPDATE provider_team_members SET pin_hash=? WHERE id=?",
                            (hash_pin(pin), team_row["id"]),
                        )
                    provider_role = team_row["role"]
                    provider_permissions = jload(team_row["permissions"], [])
                    member_id = team_row["id"]
                if owner_pin_ok and row and not str(row["pin_hash"] or "").startswith("pbkdf2_sha256$"):
                    con.execute(
                        "UPDATE providers SET pin_hash=? WHERE id=?",
                        (hash_pin(pin), row["id"]),
                    )
            if pending_request:
                bundle = issue_session_tokens({
                    "kind": "provider_pending", "requestId": pending_request["id"],
                    "name": pending_request.get("name", ""), "phone": phone,
                }, device_id=data.get("deviceId", ""))
                return self.send_session_json(
                    {"pending": True, "request": pending_request}, bundle
                )
            if not provider_row:
                return self.send_json({"error": "invalid_provider_login"}, 403)
            provider = row_provider(provider_row, private=True, sign_private=True)
            bundle = issue_session_tokens({
                "kind": "provider", "providerId": provider["id"], "name": provider["name"],
                "role": provider_role, "memberId": member_id,
                "providerPermissions": provider_permissions,
            }, device_id=data.get("deviceId", ""))
            return self.send_session_json({"provider": provider}, bundle)
        if path == "/api/users/register":
            phone = normalize_phone(data.get("phone", ""))
            name = safe_text(data.get("name"), 80).strip()
            email = safe_text(data.get("email"), 160).strip().lower()
            nationality = safe_text(data.get("nationality"), 80).strip()
            pin = str(data.get("pin", "") or "")
            try:
                age = int(data.get("age", 0) or 0)
            except (TypeError, ValueError):
                age = 0
            gender = (
                data.get("gender")
                if data.get("gender") in {"male", "female", "not_specified"}
                else "not_specified"
            )
            if len(phone) != 11 or not phone.startswith("968"):
                return self.send_json({"error": "valid_phone_required"}, 400)
            if len(name) < 2:
                return self.send_json({"error": "registration_name_required"}, 400)
            if not re.fullmatch(r"\d{4,8}", pin):
                return self.send_json({"error": "pin_required"}, 400)
            if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                return self.send_json({"error": "invalid_email"}, 400)
            if age < 1 or age > 120:
                return self.send_json({"error": "invalid_age"}, 400)
            if len(nationality) < 2:
                return self.send_json({"error": "nationality_required"}, 400)
            try:
                location = normalized_location(data.get("location"))
            except DomainError as err:
                return self.send_domain_error(err)
            user_id = slug("usr")
            avatar = ""
            if data.get("avatarData"):
                avatar = save_upload_data(
                    user_id, data["avatarData"], "avatar", IMAGE_MIMES, 2_500_000
                )
            with db() as con:
                if con.execute(
                    "SELECT id FROM app_users WHERE phone=?", (phone,)
                ).fetchone():
                    return self.send_json({"error": "phone_already_registered"}, 409)
                con.execute(
                    """INSERT INTO app_users(
                    id,phone,name,pin_hash,email,age,nationality,gov,wilayah,avatar,
                    latitude,longitude,gender)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        user_id, phone, name, hash_pin(pin), email, age, nationality,
                        safe_text(data.get("gov"), 80),
                        safe_text(data.get("wilayah"), 80),
                        avatar, location.get("lat"), location.get("lng"), gender,
                    ),
                )
                create_notification(
                    con, "admin", "", "مستخدم جديد",
                    f"تم تسجيل {name}", type_="user", related_id=user_id,
                    action_text="فتح المستخدم", action_route=f"admin:user:{user_id}",
                )
                user_row = con.execute(
                    "SELECT * FROM app_users WHERE id=?", (user_id,)
                ).fetchone()
            user = row_app_user(user_row, private=True, sign_private=True)
            bundle = issue_session_tokens(
                {"kind": "user", "userId": user_id, "name": name, "phone": phone},
                device_id=data.get("deviceId", ""),
            )
            return self.send_session_json({"user": user}, bundle)
        if path == "/api/users/login":
            phone = normalize_phone(data.get("phone", ""))
            account_id = safe_text(data.get("accountId"), 120)
            pin = str(data.get("pin", "") or "")
            if len(phone) < 11 and not account_id:
                return self.send_json({"error": "valid_phone_required"}, 400)
            with db() as con:
                row = (
                    con.execute(
                        "SELECT * FROM app_users WHERE id=?", (account_id,)
                    ).fetchone()
                    if account_id
                    else con.execute(
                        "SELECT * FROM app_users WHERE phone=?", (phone,)
                    ).fetchone()
                )
                if row:
                    phone = normalize_phone(row["phone"])
                elif account_id:
                    return self.send_json({"error": "account_not_found"}, 404)
                if not row:
                    return self.send_json({"error": "account_not_found"}, 404)
                if row["status"] != "active":
                    return self.send_json({"error": "account_inactive"}, 403)
                lock_key = row["id"] if row else (account_id or phone)
                lock_state = login_failure_state(con, "user", lock_key)
                if lock_state["locked"]:
                    return self.send_json(
                        {"error": "login_temporarily_locked", "retryAfter": lock_state["retryAfter"]},
                        429,
                    )
                otp_ok = False
                if data.get("challengeId") and data.get("otpCode"):
                    try:
                        proof = OTPService(con).verify(
                            str(data.get("challengeId")), str(data.get("otpCode"))
                        )
                        otp_ok = (
                            proof["phone"] == phone
                            and proof["purpose"] == "login"
                            and proof["targetKind"] == "user"
                        )
                    except DomainError:
                        otp_ok = False
                pin_ok = bool(row["pin_hash"] and verify_secret(pin, row["pin_hash"]))
                if row["pin_hash"] and not (pin_ok or otp_ok):
                    attempts = record_login_failure(con, "user", lock_key, phone)
                    con.execute("UPDATE app_users SET failed_attempts=? WHERE id=?", (attempts, row["id"]))
                    return self.send_json({"error": "invalid_user_pin", "attempts": attempts}, 403)
                if not row["pin_hash"] and len(pin) < 4 and not otp_ok:
                    return self.send_json({"error": "otp_or_new_pin_required"}, 403)
                con.execute(
                    """UPDATE app_users SET last_login=CURRENT_TIMESTAMP,
                    login_count=login_count+1,failed_attempts=0 WHERE id=?""",
                    (row["id"],),
                )
                user_id = row["id"]
                clear_login_failures(con, "user", lock_key)
                if not row["pin_hash"] and len(pin) >= 4:
                    con.execute(
                        "UPDATE app_users SET pin_hash=? WHERE id=?", (hash_pin(pin), user_id)
                    )
                if pin_ok and not str(row["pin_hash"] or "").startswith("pbkdf2_sha256$"):
                    con.execute(
                        "UPDATE app_users SET pin_hash=? WHERE id=?", (hash_pin(pin), user_id)
                    )
                user_row = con.execute("SELECT * FROM app_users WHERE id=?", (user_id,)).fetchone()
            user = row_app_user(user_row, private=True, sign_private=True)
            bundle = issue_session_tokens(
                {
                    "kind": "user",
                    "userId": user_id,
                    "name": user["name"],
                    "phone": phone,
                },
                device_id=data.get("deviceId", ""),
            )
            return self.send_session_json({"user": user}, bundle)
        if path == "/api/provider-requests":
            pin = str(data.get("pin", "")).strip()[:128]
            req_id = slug("req")
            try:
                location = normalized_location(data.get("location"))
                base_price = finite_number(
                    data.get("priceFrom", 0), minimum=0, maximum=1_000_000
                )
                provider_age = int(
                    finite_number(data.get("age", 0), minimum=0, maximum=120)
                )
            except DomainError as err:
                return self.send_domain_error(err)
            item = {
                "id": req_id,
                "name": safe_text(data.get("name"), 120),
                "phone": normalize_phone(data.get("phone", "")),
                "email": safe_text(data.get("email"), 160).strip().lower(),
                "age": provider_age,
                "nationality": safe_text(data.get("nationality"), 80).strip(),
                "providerType": data.get("providerType", "individual") if data.get("providerType") in ("individual", "company") else "individual",
                "companyName": safe_text(data.get("companyName"), 160),
                "commercialNo": safe_text(data.get("commercialNo"), 120),
                "commercialExpiry": safe_text(data.get("commercialExpiry"), 40),
                "licenseExpiry": safe_text(data.get("licenseExpiry"), 40),
                "registrationVersion": 58,
                "companySize": safe_text(data.get("companySize"), 80),
                "businessRole": safe_text(data.get("businessRole"), 80),
                "legalPath": safe_text(data.get("legalPath"), 40),
                "residencyStatus": safe_text(data.get("residencyStatus"), 40),
                "employerName": safe_text(data.get("employerName"), 160),
                "employerAuthorizationStatus": safe_text(
                    data.get("employerAuthorizationStatus"), 40
                ),
                "workPermitExpiry": safe_text(data.get("workPermitExpiry"), 40),
                "residencyExpiry": safe_text(data.get("residencyExpiry"), 40),
                "gender": data.get("gender")
                if data.get("gender") in {"male", "female", "not_specified"}
                else "not_specified",
                "gov": safe_text(data.get("gov", "مسقط"), 80),
                "wilayah": safe_text(data.get("wilayah"), 80),
                "location": location,
                "service": safe_text(data.get("service"), 180),
                "services": [],
                "priceFrom": base_price,
                "note": safe_text(data.get("note"), 600),
                "bio": safe_text(data.get("bio") or data.get("note"), 600),
                "hours": safe_text(data.get("hours"), 240),
                "imagePath": "",
                "workImages": [],
                "documents": [],
                "pinHash": hash_pin(pin) if len(pin) >= 4 else "",
            }
            item["note"] = item["bio"]
            raw_services = data.get("services") if isinstance(data.get("services"), list) else []
            if not item["services"] and "|" in item["service"]:
                cat_id, service_id = item["service"].split("|", 1)
                raw_services = [{
                    "catId": cat_id, "serviceId": service_id,
                    "priceFrom": item["priceFrom"], "active": True,
                    "areas": [item["wilayah"]],
                }]
            try:
                with db() as con:
                    is_company = item["providerType"] == "company"
                    foundation_id = PlanCatalog.foundation_for("company" if is_company else "individual")
                    foundation = PlanCatalog.get(con, foundation_id, False) or {}
                    limits = PlanCatalog.account_limits(
                        foundation, "company" if is_company else "individual"
                    )
                    try:
                        requested_team_size = int(item["companySize"] or 1)
                    except (TypeError, ValueError):
                        requested_team_size = 1
                    team_limit = max(1, int(limits.get("maxTeamMembers") or 1))
                    if not is_company:
                        requested_team_size = 1
                    if requested_team_size < 1 or requested_team_size > team_limit:
                        raise DomainError("team_size_exceeds_plan", 409)
                    item["companySize"] = str(requested_team_size)
                    item["services"] = normalized_provider_services(
                        con, raw_services,
                        limit=max(1, int(limits.get("maxServices") or 1)),
                        category_limit=max(1, int(limits.get("maxCategories") or 1)),
                        fallback_price=item["priceFrom"], default_areas=[item["wilayah"]],
                    )
            except DomainError as err:
                return self.send_domain_error(err)
            if item["services"]:
                first = item["services"][0]
                item["service"] = f"{first['catId']}|{first['serviceId']}"
            if not item["name"] or len(item["phone"]) < 11 or not item["pinHash"]:
                return self.send_json({"error": "name_phone_pin_required"}, 400)
            if len(pin) < 4:
                return self.send_json({"error": "pin_too_short"}, 400)
            if item["providerType"] == "company" and not item["companyName"]:
                return self.send_json({"error": "company_name_required"}, 400)
            if item["email"] and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", item["email"]):
                return self.send_json({"error": "invalid_email"}, 400)
            if item["providerType"] == "individual" and not 18 <= item["age"] <= 120:
                return self.send_json({"error": "invalid_age"}, 400)
            if item["providerType"] == "individual" and len(item["nationality"]) < 2:
                return self.send_json({"error": "nationality_required"}, 400)
            nationality_key = re.sub(
                r"[\u064b-\u065f\u0670]", "", item["nationality"].strip().lower()
            )
            inferred_path = (
                "company"
                if item["providerType"] == "company"
                else "individual_omani"
                if nationality_key in {"omani", "oman", "عماني", "عمانية", "سلطنة عمان", "عمان"}
                else "individual_foreign"
            )
            item["legalPath"] = item["legalPath"] or inferred_path
            if item["legalPath"] not in {
                "individual_omani", "individual_foreign", "company"
            }:
                return self.send_json({"error": "invalid_legal_pathway"}, 400)
            if item["providerType"] == "company" and item["legalPath"] != "company":
                return self.send_json({"error": "company_pathway_required"}, 400)
            if item["providerType"] == "individual" and item["legalPath"] == "company":
                return self.send_json({"error": "individual_pathway_required"}, 400)
            if item["providerType"] == "company" and not item["commercialNo"]:
                return self.send_json({"error": "commercial_number_required"}, 400)
            credential_expiry = item["commercialExpiry"] if item["providerType"] == "company" else item["licenseExpiry"]
            if item["providerType"] == "company" and not credential_expiry:
                return self.send_json({"error": "credential_expiry_required"}, 400)
            if item["providerType"] == "individual" and item["commercialNo"] and not credential_expiry:
                return self.send_json({"error": "credential_expiry_required"}, 400)
            if item["legalPath"] == "individual_foreign" and (
                not item["employerName"] or not item["workPermitExpiry"]
            ):
                return self.send_json(
                    {"error": "foreign_worker_details_required"}, 400
                )
            note_words = len(str(item["note"]).split())
            if note_words < 3 or note_words > 20:
                return self.send_json({"error": "description_word_limit"}, 400)
            if not item["services"]:
                return self.send_json({"error": "service_required"}, 400)
            if not item["hours"]:
                return self.send_json({"error": "availability_required"}, 400)
            documents_data = (
                data.get("documentsData")
                if isinstance(data.get("documentsData"), list)
                else []
            )
            if len([doc for doc in documents_data if doc]) < 2:
                return self.send_json({"error": "documents_required"}, 400)
            try:
                if data.get("imageData"):
                    item["imagePath"] = save_data_url(req_id, data.get("imageData"))
                if data.get("workImagesData"):
                    item["workImages"] = save_many_images(
                        req_id,
                        data.get("workImagesData"),
                        "work",
                        max(1, int(limits.get("maxImages") or 2)),
                    )
                if data.get("documentsData"):
                    item["documents"] = save_many_documents(
                        req_id, data.get("documentsData"), "doc", 4
                    )
            except ValueError as err:
                return self.send_json({"error": str(err)}, 400)
            with db() as con:
                invitation_id = ""
                invite_token = safe_text(data.get("inviteToken"), 200)
                if invite_token:
                    try:
                        invitation = KnownProviderInvitationService(
                            con
                        ).resolve_for_registration(invite_token, item["phone"])
                    except DomainError as err:
                        return self.send_domain_error(err)
                    invitation_id = invitation["id"]
                    item["invitationId"] = invitation_id
                con.execute("INSERT INTO provider_requests(id,payload) VALUES(?,?)", (item["id"], jdump(item)))
                if invitation_id:
                    KnownProviderInvitationService(con).mark_registration(
                        invitation_id, item["id"]
                    )
                settings = jload(con.execute("SELECT value FROM settings WHERE key='platform'").fetchone()["value"], {})
                create_notification(
                    con,
                    "admin",
                    "",
                    "طلب تسجيل شركة جديد" if item["providerType"] == "company" else "طلب تسجيل مزود جديد",
                    f"{item['name']} • {item['phone']}",
                    type_="provider_request",
                    related_id=item["id"],
                    priority="high",
                    action_text="مراجعة الطلب",
                    action_route=f"admin:providerRequest:{item['id']}",
                )
            send_whatsapp(settings.get("adminWhatsapp"), f"طلب مزود جديد في خدماتي: {item['name']} - {item['phone']} - {len(item['services'])} خدمات")
            safe_item = provider_request_view(item)
            bundle = issue_session_tokens({
                "kind": "provider_pending",
                "requestId": item["id"],
                "name": item["name"],
                "phone": item["phone"],
            }, device_id=data.get("deviceId", ""))
            return self.send_session_json(
                {"ok": True, "request": safe_item}, bundle, 201
            )
        if path == "/api/reviews":
            return self.save_review(data)
        if path == "/api/complaints":
            return self.save_complaint(data)
        if path == "/api/leads":
            return self.save_lead(data)
        if path.startswith("/api/user/"):
            return self.user_post(path, data)
        if path == "/api/requests/action":
            return self.request_action(data)
        if path == "/api/request-suggestions":
            return self.request_suggestion(data)
        if path == "/api/request/collaboration":
            return self.request_collaboration(data)
        if path == "/api/request/workflow":
            return self.request_workflow(data)
        if path == "/api/service-assets":
            return self.service_assets(data)
        if path == "/api/community":
            return self.community_post(data)
        if path == "/api/trust/verification":
            return self.trust_verification(data)
        if path == "/api/trust/complaint":
            return self.trust_complaint(data)
        if path == "/api/trust/block":
            return self.trust_block(data)
        if path == "/api/platform":
            return self.platform_post(data)
        if path == "/api/admin/platform":
            return self.admin_platform_post(data)
        if path == "/api/notifications/action":
            return self.notification_action(data)
        if path == "/api/recovery/request":
            return self.recovery_request(data)
        if path == "/api/recovery/verify":
            return self.recovery_verify(data)
        if path == "/api/recovery/complete":
            return self.recovery_complete(data)
        if path == "/api/account/delete":
            return self.delete_account(data)
        if path == "/api/push/subscribe":
            return self.push_subscribe(data)
        if path == "/api/policy/accept":
            return self.policy_accept(data)
        if path.startswith("/api/provider/"):
            return self.provider_post(path, data)
        if path.startswith("/api/admin/"):
            return self.admin_post(path, data)
        self.send_json({"error": "not_found"}, 404)

    def save_review(self, data):
        session = self.require_user()
        if not session:
            return
        provider_id = data.get("providerId")
        request_id = str(data.get("requestId", "") or "")
        rating = int(data.get("rating", 0) or 0)
        if not provider_id or not request_id or rating < 1 or rating > 5:
            return self.send_json({"error": "invalid_review"}, 400)
        dimensions = normalize_review_dimensions(data.get("dimensions"), rating)
        if set(dimensions) != set(REVIEW_DIMENSION_KEYS):
            return self.send_json({"error": "invalid_review_dimensions"}, 400)
        with db() as con:
            user = con.execute("SELECT * FROM app_users WHERE id=?", (session["userId"],)).fetchone()
            request_row = con.execute(
                """SELECT id FROM customer_requests WHERE id=? AND user_id=?
                AND accepted_provider_id=? AND status IN ('closed','completed','archived')""",
                (request_id, session["userId"], provider_id),
            ).fetchone()
            if not user or not request_row:
                return self.send_json({"error": "completed_request_required"}, 403)
            if con.execute("SELECT id FROM reviews WHERE request_id=? AND user_id=?", (request_id, session["userId"])).fetchone():
                return self.send_json({"error": "request_already_reviewed"}, 409)
            if not con.execute("SELECT id FROM providers WHERE id=?", (provider_id,)).fetchone():
                return self.send_json({"error": "provider_not_found"}, 404)
            item = {
                "id": slug("rev"),
                "provider_id": provider_id,
                "request_id": request_id,
                "user_id": session["userId"],
                "rating": rating,
                "customer_name": user["name"],
                "phone": user["phone"],
                "comment": str(data.get("comment", "") or "").strip()[:900],
                "dimensions": dimensions,
            }
            con.execute(
                """INSERT INTO reviews(
                id,provider_id,rating,customer_name,phone,comment,dimensions,
                approved,request_id,user_id)
                VALUES(?,?,?,?,?,?,?,1,?,?)""",
                (
                    item["id"], item["provider_id"], item["rating"], item["customer_name"],
                    item["phone"], item["comment"], jdump(item["dimensions"]),
                    item["request_id"], item["user_id"],
                ),
            )
            record_loyalty_transaction(
                con,
                session["userId"],
                5,
                "verified_review",
                f"review:{request_id}",
            )
            recompute_provider_quality(con, provider_id)
            log_audit(con, session, "review.created", provider_id, request_id)
            platform_row = con.execute(
                "SELECT value FROM settings WHERE key='platform'"
            ).fetchone()
            platform = jload(platform_row["value"], {}) if platform_row else {}
            summary = loyalty_summary(
                con,
                session["userId"],
                target=max(1, int(platform.get("loyaltyTargetRequests", 8) or 8)),
                cycle_mode=(
                    "repeat"
                    if platform.get("loyaltyCycleMode") == "repeat"
                    else "cap"
                ),
            )
        return self.send_json(
            {"ok": True, "review": item, "loyaltySummary": summary}, 201
        )

    def save_complaint(self, data):
        session = self.require_user()
        if not session:
            return
        provider_id = safe_text(data.get("providerId"), 120)
        request_id = str(data.get("requestId", "") or "")
        complaint_id = slug("cmp")
        with db() as con:
            user = con.execute("SELECT * FROM app_users WHERE id=?", (session["userId"],)).fetchone()
            if not user:
                return self.send_json({"error": "user_not_found"}, 404)
            if request_id:
                request_row = con.execute(
                    """SELECT id,accepted_provider_id FROM customer_requests
                    WHERE id=? AND user_id=?""",
                    (request_id, session["userId"]),
                ).fetchone()
                if not request_row:
                    return self.send_json({"error": "request_not_found"}, 404)
                selected_provider = request_row["accepted_provider_id"] or ""
                if selected_provider:
                    if provider_id and provider_id != selected_provider:
                        return self.send_json(
                            {"error": "complaint_provider_mismatch"}, 403
                        )
                    provider_id = selected_provider
            if provider_id and not con.execute(
                "SELECT 1 FROM providers WHERE id=? AND COALESCE(status,'')!='deleted'",
                (provider_id,),
            ).fetchone():
                return self.send_json({"error": "provider_not_found"}, 404)
            try:
                evidence_paths = save_many_documents(
                    complaint_id,
                    data.get("evidenceData", []),
                    "problem",
                    5,
                )
            except ValueError as err:
                return self.send_json({"error": str(err)}, 400)
            item = {
                "id": complaint_id,
                "provider_id": provider_id,
                "request_id": request_id,
                "user_id": session["userId"],
                "customer_name": user["name"],
                "phone": user["phone"],
                "reason": str(data.get("reason", "quality") or "quality").strip()[:80],
                "detail": str(data.get("detail", "") or "").strip()[:1400],
                "priority": data.get("priority", "normal") if data.get("priority") in ("low", "normal", "high") else "normal",
            }
            if not item["detail"]:
                return self.send_json({"error": "complaint_required_fields"}, 400)
            con.execute(
                """INSERT INTO complaints(
                id,provider_id,customer_name,phone,reason,detail,status,priority,resolution,request_id,user_id)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    item["id"], item["provider_id"], item["customer_name"], item["phone"],
                    item["reason"], item["detail"], "open", item["priority"], "",
                    item["request_id"], item["user_id"],
                ),
            )
            complaint_service = ComplaintCaseService(con)
            complaint_service.open_existing(
                item["id"],
                actor_kind="user",
                actor_id=session["userId"],
                category=item["reason"],
                source="request" if request_id else "provider_profile",
            )
            if evidence_paths:
                complaint_service.add_evidence(
                    item["id"],
                    evidence_paths,
                    uploader_kind="user",
                    uploader_id=session["userId"],
                    labels=data.get("evidenceLabels", []),
                )
            if provider_id:
                recompute_provider_quality(con, provider_id)
            log_audit(con, session, "complaint.created", provider_id or "", request_id or item["reason"])
            settings = jload(con.execute("SELECT value FROM settings WHERE key='platform'").fetchone()["value"], {})
            create_notification(
                con,
                "admin",
                "",
                "شكوى جديدة تحتاج فرزًا",
                f"{item['customer_name']} • {item['reason']}",
                type_="complaint",
                related_id=item["id"],
                priority="high" if item["priority"] == "high" else "normal",
                action_text="فتح ملف الشكوى",
                action_route=f"admin:complaint:{item['id']}",
            )
            if provider_id:
                create_notification(
                    con,
                    "provider",
                    provider_id,
                    "وردت ملاحظة جودة مرتبطة بخدمة",
                    "سيظهر لك ما يلزم الرد عليه بعد فرز الإدارة للبلاغ.",
                    type_="complaint",
                    related_id=item["id"],
                    priority="normal",
                    action_text="متابعة الحالة",
                    action_route=f"provider:complaint:{item['id']}",
                )
            response_item = complaint_service.get(item["id"])
            for evidence in response_item.get("evidence", []):
                evidence["mediaUrl"] = secure_media_url(
                    evidence.pop("mediaPath", "")
                )
        send_whatsapp(settings.get("adminWhatsapp"), f"شكوى جديدة في خدماتي: {item['customer_name']} - {item['phone']} - {item['reason']}")
        return self.send_json({"ok": True, "complaint": response_item}, 201)

    def save_lead(self, data):
        kind = data.get("kind", "whatsapp")
        if kind not in ("request", "views", "whatsapp", "calls", "booking", "quote"):
            kind = "request"
        session = self.session()
        session_kind = (session or {}).get("kind", "")

        # This endpoint remains for backwards compatibility. Identity and phone data
        # are always sourced from the authenticated session, never from the browser.
        if kind == "quote" and session_kind != "provider":
            return self.send_json({"error": "provider_auth_required"}, 401)
        if kind in ("request", "views", "whatsapp", "calls") and session_kind != "user":
            return self.send_json({"error": "user_auth_required"}, 401)
        if kind == "booking" and session_kind != "admin":
            return self.send_json({"error": "permission_denied"}, 403)

        supplied_id = str(data.get("id") or "").strip()[:80]
        lead_id = supplied_id if session_kind == "admin" and supplied_id else slug("lead")
        provider_id = str(data.get("providerId") or "").strip()[:80]
        if kind == "quote":
            provider_id = str(session.get("providerId") or "").strip()[:80]
        item = {
            "id": lead_id,
            "provider_id": provider_id,
            "kind": kind,
            "customer_name": "",
            "phone": "",
            "note": (data.get("note", "") or "").strip()[:1200],
            "service_value": (data.get("serviceValue", "") or "").strip()[:120],
            "service_name": (data.get("serviceName", "") or "").strip()[:120],
            "gov": (data.get("gov", "") or "").strip()[:80],
            "status": (data.get("status", "open") or "open").strip()[:40],
        }
        with db() as con:
            if item["provider_id"]:
                provider_row = con.execute(
                    "SELECT id FROM providers WHERE id=? AND active=1", (item["provider_id"],)
                ).fetchone()
                if not provider_row:
                    return self.send_json({"error": "provider_not_found"}, 404)
            if session_kind == "user":
                user_row = con.execute(
                    "SELECT name,phone FROM app_users WHERE id=? AND status='active'",
                    (session.get("userId"),),
                ).fetchone()
                if not user_row:
                    return self.send_json({"error": "user_not_found"}, 404)
                item["customer_name"] = user_row["name"] or ""
                item["phone"] = user_row["phone"] or ""
            elif session_kind == "admin":
                item["customer_name"] = "إدارة خدماتي"
            elif kind == "quote":
                # A quote never needs a copied customer phone number.
                item["customer_name"] = str(data.get("customerName") or "").strip()[:80]

            exists = con.execute("SELECT id FROM leads WHERE id=?", (item["id"],)).fetchone()
            if exists:
                con.execute(
                    """UPDATE leads
                    SET provider_id=?, kind=?, customer_name=?, phone=?, note=?, service_value=?, service_name=?, gov=?, status=?
                    WHERE id=?""",
                    (
                        item["provider_id"], item["kind"], item["customer_name"], item["phone"], item["note"],
                        item["service_value"], item["service_name"], item["gov"], item["status"], item["id"],
                    ),
                )
            else:
                con.execute(
                    """INSERT INTO leads(id,provider_id,kind,customer_name,phone,note,service_value,service_name,gov,status,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
                    (
                        item["id"], item["provider_id"], item["kind"], item["customer_name"], item["phone"], item["note"],
                        item["service_value"], item["service_name"], item["gov"], item["status"],
                    ),
                )
            provider = None
            if item["kind"] in ("views", "whatsapp", "calls") and item["provider_id"]:
                r = con.execute("SELECT * FROM providers WHERE id=?", (item["provider_id"],)).fetchone()
                if r:
                    provider = row_provider(r, private=True)
                    stats = provider["stats"]
                    stats[item["kind"]] = int(stats.get(item["kind"], 0)) + 1
                    con.execute("UPDATE providers SET stats=? WHERE id=?", (jdump(stats), item["provider_id"]))
            if (
                item["provider_id"]
                and session
                and session.get("kind") == "admin"
                and item["kind"] in ("booking", "quote", "request")
            ):
                create_notification(
                    con, "provider", item["provider_id"], "ملاحظة من الإدارة",
                    item["note"], type_="admin", related_id=item["id"], priority="high",
                    action_text="فتح الرسالة", action_route="provider:support",
                )
        if data.get("notifyProvider") and provider and session_kind == "admin":
            send_whatsapp(provider["phone"], f"تنبيه من خدماتي: لديك تواصل جديد. {item['note']}".strip())
        safe_item = dict(item)
        if session_kind != "admin":
            safe_item.pop("phone", None)
        return self.send_json({"ok": True, "lead": safe_item}, 200 if exists else 201)

    def create_community_request(
        self,
        con,
        session,
        listing,
        provider_id,
        *,
        amount,
        duration_text,
        offer_note="",
        source_kind,
        source_id,
        whatsapp=False,
        language="ar",
    ):
        user = con.execute(
            "SELECT * FROM app_users WHERE id=? AND status='active'",
            (session["userId"],),
        ).fetchone()
        provider = con.execute(
            """SELECT * FROM providers WHERE id=? AND active=1 AND verified=1
            AND status NOT IN ('deleted','unavailable')
            AND COALESCE(request_enabled,1)=1""",
            (provider_id,),
        ).fetchone()
        if not user:
            raise DomainError("user_not_found", 404)
        if not provider:
            raise DomainError("community_provider_not_eligible", 403)
        service_value = listing["service_value"]
        category_id, service_id = service_value.split("|", 1)
        service = con.execute(
            """SELECT s.ar,s.en FROM services s
            WHERE s.id=? AND s.category_id=?""",
            (service_id, category_id),
        ).fetchone()
        language = "en" if safe_text(language, 5).lower() == "en" else "ar"
        service_name = (
            (service[language] if service else "")
            or (service["ar"] if service else "")
            or listing["title"]
            or service_value
        )
        request_id = slug("req")
        now = datetime.now(UTC).isoformat()
        snapshot = {
            "source": "community",
            "kind": source_kind,
            "listingId": listing["id"],
            "sourceId": source_id,
            "title": listing["title"],
            "description": listing["description"],
            "serviceValue": service_value,
            "priceAmount": float(amount or 0),
            "durationText": duration_text,
            "billingPeriod": listing["billing_period"],
        }
        offer = {
            "id": source_id,
            "providerId": provider_id,
            "amount": float(amount or 0),
            "duration": duration_text,
            "durationText": duration_text,
            "note": offer_note,
            "status": "accepted",
            "source": "community",
            "createdAt": now,
        }
        if language == "en":
            welcome_text = (
                f"Hello {user['name'] or 'Khadamati customer'}, this is "
                f"{provider['name']}. Your chat about “{listing['title']}” is now open. "
                "We can confirm the details and schedule here."
            )
        else:
            welcome_text = (
                f"مرحباً {user['name'] or 'عميل خدماتي'}، معك {provider['name']}. "
                f"تم فتح المحادثة بخصوص «{listing['title']}». "
                "يمكننا تأكيد التفاصيل والموعد هنا."
            )
        message = {
            "id": slug("msg"),
            "sender": "provider",
            "senderId": provider_id,
            "text": welcome_text,
            "image": "",
            "audio": "",
            "location": None,
            "systemGenerated": True,
            "communitySnapshot": snapshot,
            "createdAt": now,
        }
        channels = jload(listing["contact_channels"], ["app"])
        whatsapp_enabled = bool(whatsapp and "whatsapp" in channels)
        consent = {
            "chat": True,
            "whatsapp": whatsapp_enabled,
            "call": False,
        }
        image_paths = [listing["image_path"]] if listing["image_path"] else []
        con.execute(
            """
            INSERT INTO customer_requests(
              id,user_id,customer_name,phone,service_value,service_name,gov,wilayah,
              latitude,longitude,urgency,schedule_type,requested_at,budget_min,budget_max,
              location_text,note,images,status,accepted_provider_id,matching_provider_ids,
              declined_provider_ids,offers,messages,contact_consent,waitlisted,offers_open,
              updated_at
            ) VALUES(
              :id,:user_id,:customer_name,:phone,:service_value,:service_name,:gov,:wilayah,
              :latitude,:longitude,'normal','agreement',:requested_at,:budget_min,:budget_max,
              :location_text,:note,:images,'accepted',:provider_id,:matching_provider_ids,
              '[]',:offers,:messages,:contact_consent,0,0,CURRENT_TIMESTAMP
            )
            """,
            {
                "id": request_id,
                "user_id": user["id"],
                "customer_name": user["name"] or "",
                "phone": user["phone"] or "",
                "service_value": service_value,
                "service_name": service_name,
                "gov": listing["gov"] or user["gov"] or "",
                "wilayah": listing["wilayah"] or user["wilayah"] or "",
                "latitude": listing["latitude"],
                "longitude": listing["longitude"],
                "requested_at": now,
                "budget_min": float(amount or 0),
                "budget_max": float(amount or 0),
                "location_text": listing["location_text"] or "",
                "note": listing["description"] or "",
                "images": jdump(image_paths),
                "provider_id": provider_id,
                "matching_provider_ids": jdump([provider_id]),
                "offers": jdump([offer]),
                "messages": jdump([message]),
                "contact_consent": jdump(consent),
            },
        )
        RequestLifecycleService(con).record(
            request_id,
            "community_request_created",
            actor_kind="user",
            actor_id=user["id"],
            to_status="accepted",
            detail=snapshot,
        )
        consent_service = ContactConsentService(con)
        consent_service.set_channel(
            request_id, user["id"], provider_id, "chat", True
        )
        consent_service.set_channel(
            request_id, user["id"], provider_id, "whatsapp", whatsapp_enabled
        )
        consent_service.set_channel(
            request_id, user["id"], provider_id, "call", False
        )
        create_notification(
            con,
            "provider",
            provider_id,
            "طلب جديد من المجتمع",
            f"{service_name} • {listing['title']}",
            type_="community",
            related_id=request_id,
            priority="high",
            action_text="فتح المهمة",
            action_route=f"provider:tasks:{request_id}",
        )
        create_notification(
            con,
            "user",
            user["id"],
            f"بدأت المحادثة مع {provider['name']}",
            f"{service_name} • {listing['title']}",
            type_="chat",
            related_id=request_id,
            priority="high",
            action_text="فتح المحادثة",
            action_route=f"user:chat:{request_id}",
        )
        create_notification(
            con,
            "admin",
            "",
            "طلب جديد من المجتمع",
            f"{service_name} • {provider['name']}",
            type_="community",
            related_id=request_id,
            action_text="فتح الطلب",
            action_route=f"admin:request:{request_id}",
        )
        whatsapp_url = ""
        if whatsapp_enabled and provider["phone"]:
            whatsapp_message = (
                f"مرحباً {provider['name']}، أرسلت طلب «{listing['title']}» "
                f"عبر مجتمع خدماتي. رقم الطلب: {request_id}"
            )
            whatsapp_url = (
                f"https://wa.me/{normalize_phone(provider['phone'])}"
                f"?text={quote(whatsapp_message)}"
            )
        return {
            "requestId": request_id,
            "route": f"user:chat:{request_id}",
            "whatsappUrl": whatsapp_url,
        }

    def community_post(self, data):
        session = self.session()
        if not session or session.get("kind") not in {"user", "provider", "admin"}:
            return self.send_json({"error": "auth_required"}, 401)
        action = safe_text(data.get("action"), 50)
        with db() as con:
            service = CommunityService(con)
            if action == "save":
                if session["kind"] not in {"user", "provider"}:
                    return self.send_json({"error": "account_auth_required"}, 403)
                owner_id = session.get("userId") or session.get("providerId")
                listing_id = safe_text(data.get("id"), 120) or slug("community")
                image_path = ""
                if data.get("imageData"):
                    image_path = save_upload_data(
                        owner_id,
                        data["imageData"],
                        f"{listing_id}-cover",
                        IMAGE_MIMES,
                        3_000_000,
                    )
                item = service.save(
                    session,
                    data,
                    listing_id=listing_id,
                    image_path=image_path,
                )
                if item["status"] == "pending_review":
                    create_notification(
                        con,
                        "admin",
                        "",
                        "إعلان مجتمع يحتاج مراجعة",
                        item["title"],
                        type_="community",
                        related_id=item["id"],
                        priority="high",
                        action_text="مراجعة الإعلان",
                        action_route=f"admin:community:{item['id']}",
                    )
                return self.send_json(
                    {
                        "ok": True,
                        "listing": community_snapshot_view(
                            {"listings": [item]}
                        )["listings"][0],
                    },
                    200 if data.get("id") else 201,
                )
            if action == "owner_action":
                if session["kind"] not in {"user", "provider"}:
                    return self.send_json({"error": "account_auth_required"}, 403)
                item = service.owner_action(
                    session,
                    safe_text(data.get("listingId"), 120),
                    safe_text(data.get("ownerAction"), 30),
                )
                if item["status"] == "pending_payment":
                    create_notification(
                        con,
                        "admin",
                        "",
                        "طلب تجديد إعلان باقة",
                        item["title"],
                        type_="community",
                        related_id=item["id"],
                        action_text="مراجعة التجديد",
                        action_route=f"admin:community:{item['id']}",
                    )
                return self.send_json(
                    {
                        "ok": True,
                        "listing": community_snapshot_view(
                            {"listings": [item]}
                        )["listings"][0],
                    }
                )
            if action == "offer":
                if session["kind"] != "provider":
                    return self.send_json(
                        {"error": "provider_auth_required"}, 401
                    )
                offer = service.offer(
                    session,
                    safe_text(data.get("listingId"), 120),
                    data,
                    offer_id=slug("community_offer"),
                )
                listing = service._get(offer["listingId"])
                create_notification(
                    con,
                    "user",
                    listing["owner_id"],
                    "وصلك عرض جديد",
                    f"{listing['title']} • {offer['amount']:.3f} ر.ع",
                    type_="community",
                    related_id=listing["id"],
                    priority="high",
                    action_text="عرض التفاصيل",
                    action_route=f"user:community:{listing['id']}",
                )
                return self.send_json({"ok": True, "offer": offer}, 201)
            if action == "accept_offer":
                if session["kind"] != "user":
                    return self.send_json({"error": "user_auth_required"}, 401)
                con.execute("BEGIN IMMEDIATE")
                result = service.accept_offer(
                    session,
                    safe_text(data.get("listingId"), 120),
                    safe_text(data.get("offerId"), 120),
                )
                if result["duplicate"]:
                    return self.send_json(
                        {
                            "ok": True,
                            "duplicate": True,
                            "requestId": result["requestId"],
                            "route": f"user:chat:{result['requestId']}",
                        }
                    )
                listing = result["listing"]
                offer = result["offer"]
                created = self.create_community_request(
                    con,
                    session,
                    listing,
                    offer["provider_id"],
                    amount=offer["amount"],
                    duration_text=offer["duration_text"],
                    offer_note=offer["note"],
                    source_kind="wanted",
                    source_id=offer["id"],
                    whatsapp=bool(data.get("useWhatsapp")),
                    language=data.get("language", "ar"),
                )
                service.complete_offer_acceptance(
                    listing["id"], offer["id"], created["requestId"]
                )
                return self.send_json({"ok": True, **created}, 201)
            if action == "request_package":
                if session["kind"] != "user":
                    return self.send_json({"error": "user_auth_required"}, 401)
                con.execute("BEGIN IMMEDIATE")
                listing_id = safe_text(data.get("listingId"), 120)
                started = service.begin_package_order(
                    session,
                    listing_id,
                    safe_text(data.get("idempotencyKey"), 160),
                    order_id=slug("community_order"),
                )
                existing_request = (
                    started["order"]["request_id"]
                    if isinstance(started["order"], sqlite3.Row)
                    else started["order"].get("request_id", "")
                )
                if started["duplicate"] and existing_request:
                    return self.send_json(
                        {
                            "ok": True,
                            "duplicate": True,
                            "requestId": existing_request,
                            "route": f"user:chat:{existing_request}",
                        }
                    )
                listing = started["listing"]
                order_id = (
                    started["order"]["id"]
                    if isinstance(started["order"], sqlite3.Row)
                    else started["order"].get("id")
                )
                created = self.create_community_request(
                    con,
                    session,
                    listing,
                    listing["owner_id"],
                    amount=listing["price_amount"],
                    duration_text=listing["duration_text"],
                    source_kind="package",
                    source_id=order_id,
                    whatsapp=bool(data.get("useWhatsapp")),
                    language=data.get("language", "ar"),
                )
                service.complete_package_order(
                    order_id, created["requestId"], listing_id
                )
                return self.send_json({"ok": True, **created}, 201)
            if action == "favorite":
                enabled = service.favorite(
                    session,
                    safe_text(data.get("listingId"), 120),
                    bool(data.get("enabled")),
                    row_id=slug("community_favorite"),
                )
                return self.send_json({"ok": True, "enabled": enabled})
            if action == "report":
                report = service.report(
                    session,
                    safe_text(data.get("listingId"), 120),
                    safe_text(data.get("reason"), 500),
                    report_id=slug("community_report"),
                )
                create_notification(
                    con,
                    "admin",
                    "",
                    "بلاغ جديد في المجتمع",
                    report["reason"],
                    type_="community",
                    related_id=report["listingId"],
                    priority="high",
                    action_text="فتح البلاغ",
                    action_route=f"admin:community:{report['listingId']}",
                )
                return self.send_json({"ok": True, "report": report}, 201)
            if action in {"settings", "moderate", "resolve_report"}:
                if not has_permission(session, "manage_community"):
                    return self.send_json(
                        {
                            "error": "permission_denied",
                            "permission": "manage_community",
                        },
                        403,
                    )
                if action == "settings":
                    settings = save_community_settings(con, data)
                    log_audit(
                        con, session, "community.settings.updated", "community", ""
                    )
                    return self.send_json({"ok": True, "settings": settings})
                if action == "moderate":
                    item = service.moderate(
                        session,
                        safe_text(data.get("listingId"), 120),
                        safe_text(data.get("moderationAction"), 30),
                        safe_text(data.get("note"), 500),
                    )
                    target_kind = (
                        "user" if item["ownerKind"] == "user" else "provider"
                    )
                    create_notification(
                        con,
                        target_kind,
                        item["ownerId"],
                        "تم تحديث إعلانك في المجتمع",
                        item["title"],
                        type_="community",
                        related_id=item["id"],
                        action_text="فتح الإعلان",
                        action_route=f"{target_kind}:community:{item['id']}",
                    )
                    log_audit(
                        con,
                        session,
                        f"community.{safe_text(data.get('moderationAction'), 30)}",
                        "community_listing",
                        item["id"],
                    )
                    return self.send_json(
                        {
                            "ok": True,
                            "listing": community_snapshot_view(
                                {"listings": [item]}
                            )["listings"][0],
                        }
                    )
                service.resolve_report(
                    session,
                    safe_text(data.get("reportId"), 120),
                    safe_text(data.get("resolution"), 500),
                )
                log_audit(
                    con,
                    session,
                    "community.report.resolved",
                    "community_report",
                    safe_text(data.get("reportId"), 120),
                )
                return self.send_json({"ok": True})
            return self.send_json({"error": "invalid_community_action"}, 400)

    def user_post(self, path, data):
        session = self.require_user()
        if not session:
            return
        user_id = session["userId"]
        with db() as con:
            user_row = con.execute("SELECT * FROM app_users WHERE id=? AND status='active'", (user_id,)).fetchone()
            if not user_row:
                return self.send_json({"error": "user_not_found"}, 404)
            if path == "/api/user/profile":
                avatar = user_row["avatar"] or ""
                if data.get("avatarData"):
                    avatar = save_upload_data(user_id, data["avatarData"], "avatar", IMAGE_MIMES, 2_500_000)
                age_was_supplied = "age" in data
                nationality_was_supplied = "nationality" in data
                phone = normalize_phone(data.get("phone", user_row["phone"]))
                if len(phone) != 11 or not phone.startswith("968"):
                    return self.send_json({"error": "valid_phone_required"}, 400)
                duplicate = con.execute(
                    "SELECT id FROM app_users WHERE phone=? AND id<>?",
                    (phone, user_id),
                ).fetchone()
                if duplicate:
                    return self.send_json({"error": "phone_already_registered"}, 409)
                if (
                    phone != user_row["phone"]
                    and user_row["pin_hash"]
                    and not verify_secret(data.get("currentPin", ""), user_row["pin_hash"])
                ):
                    return self.send_json({"error": "current_pin_incorrect"}, 403)
                email = safe_text(data.get("email", user_row["email"]), 160).strip().lower()
                nationality = safe_text(
                    data.get("nationality", user_row["nationality"]), 80
                ).strip()
                if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                    return self.send_json({"error": "invalid_email"}, 400)
                try:
                    age = int(data.get("age", user_row["age"]) or 0)
                except (TypeError, ValueError):
                    return self.send_json({"error": "invalid_age"}, 400)
                if age_was_supplied and (age < 0 or age > 120):
                    return self.send_json({"error": "invalid_age"}, 400)
                if nationality_was_supplied and len(nationality) < 2:
                    return self.send_json({"error": "nationality_required"}, 400)
                try:
                    location = normalized_location(data.get("location"))
                except DomainError as err:
                    return self.send_domain_error(err)
                con.execute(
                    """UPDATE app_users SET name=?,phone=?,email=?,age=?,nationality=?,gov=?,wilayah=?,avatar=?,
                    latitude=COALESCE(?,latitude),longitude=COALESCE(?,longitude),gender=?
                    WHERE id=?""",
                    (
                        str(data.get("name", user_row["name"]) or "").strip()[:80],
                        phone, email, age, nationality,
                        str(data.get("gov", user_row["gov"]) or "").strip()[:80],
                        str(data.get("wilayah", user_row["wilayah"]) or "").strip()[:80],
                        avatar, location.get("lat"), location.get("lng"),
                        data.get("gender")
                        if data.get("gender") in {"male", "female", "not_specified"}
                        else user_row["gender"],
                        user_id,
                    ),
                )
                updated = con.execute("SELECT * FROM app_users WHERE id=?", (user_id,)).fetchone()
                return self.send_json({"ok": True, "user": row_app_user(updated, private=True, sign_private=True)})
            if path == "/api/user/pin":
                pin = str(data.get("pin", ""))
                if not re.fullmatch(r"\d{4,8}", pin):
                    return self.send_json({"error": "pin_too_short"}, 400)
                if user_row["pin_hash"] and not verify_secret(data.get("currentPin", ""), user_row["pin_hash"]):
                    return self.send_json({"error": "current_pin_incorrect"}, 403)
                con.execute("UPDATE app_users SET pin_hash=? WHERE id=?", (hash_pin(pin), user_id))
                authorization = str(self.headers.get("Authorization", "") or "")
                current_hash = hash_secret(authorization[7:].strip()) if authorization.startswith("Bearer ") else ""
                revoke_account_sessions(con, "user", user_id, current_hash)
                return self.send_json({"ok": True})
            if path == "/api/user/provider-invitations":
                service = KnownProviderInvitationService(con)
                action = safe_text(data.get("action", "list"), 20)
                try:
                    if action == "list":
                        return self.send_json(
                            {"ok": True, "invitations": service.list_for_user(user_id)}
                        )
                    if action == "cancel":
                        cancelled = service.cancel(
                            user_id, safe_text(data.get("id"), 160)
                        )
                        if not cancelled:
                            return self.send_json(
                                {"error": "provider_invitation_not_found"}, 404
                            )
                        return self.send_json({"ok": True})
                    if action != "create":
                        return self.send_json(
                            {"error": "invalid_provider_invitation_action"}, 400
                        )
                    phone = normalize_phone(data.get("phone", ""))
                    invitation = service.create(
                        user_id,
                        safe_text(data.get("requestId"), 160),
                        phone,
                    )
                    raw_token = invitation.pop("token")
                    invite_url = (
                        f"{PUBLIC_APP_URL}#provider-invite={quote(raw_token)}"
                    )
                    if invitation.get("providerId"):
                        create_notification(
                            con,
                            "provider",
                            invitation["providerId"],
                            "طلب مباشر من عميل يعرفك",
                            "أرسل لك عميل طلبًا مطابقًا لخدمتك.",
                            type_="request",
                            related_id=invitation["requestId"],
                            priority="high",
                            action_text="فتح الطلب",
                            action_route=(
                                f"provider:request:{invitation['requestId']}"
                            ),
                        )
                    return self.send_json(
                        {
                            "ok": True,
                            "invitation": invitation,
                            "inviteUrl": invite_url,
                            "shareText": (
                                "لدي طلب خدمة لك عبر خدماتي. افتح الرابط وسجل "
                                f"حساب المزود: {invite_url}"
                            ),
                        },
                        201,
                    )
                except DomainError as err:
                    return self.send_domain_error(err)
            if path == "/api/user/requests":
                request_id = str(data.get("id", "") or "")
                action = data.get("action", "save")
                if request_id:
                    current = con.execute(
                        "SELECT * FROM customer_requests WHERE id=? AND user_id=?",
                        (request_id, user_id),
                    ).fetchone()
                    if not current:
                        return self.send_json({"error": "request_not_found"}, 404)
                    if action == "complete":
                        if not current["accepted_provider_id"]:
                            return self.send_json({"error": "accepted_provider_required"}, 409)
                        try:
                            CompletionEvidenceService(con).decide(
                                request_id, user_id, "resolved"
                            )
                        except DomainError as err:
                            return self.send_domain_error(err)
                        create_notification(
                            con,
                            "provider",
                            current["accepted_provider_id"],
                            "أكد العميل اكتمال الخدمة",
                            current["service_name"] or current["service_value"],
                            type_="request",
                            related_id=request_id,
                            action_text="فتح المهمة",
                            action_route=f"provider:tasks:{request_id}",
                        )
                        record_loyalty_transaction(
                            con,
                            user_id,
                            10,
                            "completed_request",
                            f"completed:{request_id}",
                        )
                        ProviderCRMService(con).sync(
                            current["accepted_provider_id"]
                        )
                        TrainingAchievementService(con).recompute_achievements(
                            current["accepted_provider_id"]
                        )
                        ReferralService(con).qualify("user", user_id)
                        ReferralService(con).qualify(
                            "provider", current["accepted_provider_id"]
                        )
                        updated = con.execute(
                            "SELECT * FROM customer_requests WHERE id=?",
                            (request_id,),
                        ).fetchone()
                        platform_row = con.execute(
                            "SELECT value FROM settings WHERE key='platform'"
                        ).fetchone()
                        platform = (
                            jload(platform_row["value"], {})
                            if platform_row
                            else {}
                        )
                        return self.send_json(
                            {
                                "ok": True,
                                "status": "closed",
                                "request": request_with_workflow(
                                    con,
                                    row_customer_request(updated, sign_private=True),
                                    asset_visible=True,
                                ),
                                "loyaltySummary": loyalty_summary(
                                    con,
                                    user_id,
                                    target=max(
                                        1,
                                        int(
                                            platform.get(
                                                "loyaltyTargetRequests", 8
                                            )
                                            or 8
                                        ),
                                    ),
                                    cycle_mode=(
                                        "repeat"
                                        if platform.get("loyaltyCycleMode")
                                        == "repeat"
                                        else "cap"
                                    ),
                                ),
                            }
                        )
                    if action in ("cancel", "delete", "pause", "archive"):
                        if action == "archive" and not current["accepted_provider_id"]:
                            return self.send_json({"error": "accepted_provider_required"}, 409)
                        if action == "delete" and current["accepted_provider_id"]:
                            return self.send_json(
                                {"error": "accepted_request_cannot_be_deleted"}, 409
                            )
                        next_status = {
                            "cancel": "cancelled", "delete": "deleted", "pause": "paused",
                            "archive": "archived",
                        }[action]
                        con.execute(
                            """UPDATE customer_requests SET status=?,offers_open=0,
                            updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                            (next_status, request_id),
                        )
                        RequestLifecycleService(con).record(
                            request_id,
                            f"request_{action}",
                            actor_kind="user",
                            actor_id=user_id,
                            from_status=current["status"],
                            to_status=next_status,
                        )
                        if action == "delete":
                            con.execute(
                                "DELETE FROM request_dispatches WHERE request_id=?",
                                (request_id,),
                            )
                            con.execute(
                                """UPDATE request_provider_suggestions
                                SET status='deleted',deleted_at=CURRENT_TIMESTAMP,
                                updated_at=CURRENT_TIMESTAMP WHERE request_id=?""",
                                (request_id,),
                            )
                            con.execute(
                                """UPDATE contact_consents SET status='revoked',
                                revoked_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
                                WHERE request_id=?""",
                                (request_id,),
                            )
                            con.execute(
                                "DELETE FROM app_notifications WHERE related_id=?",
                                (request_id,),
                            )
                            log_audit(
                                con, session, "request.deleted", request_id,
                                current["service_value"],
                            )
                            return self.send_json(
                                {"ok": True, "status": next_status, "id": request_id}
                            )
                        create_notification(
                            con, "admin", "", "تم تحديث طلب",
                            f"الطلب {request_id}", type_="request", related_id=request_id,
                            action_text="فتح الطلب", action_route=f"admin:request:{request_id}",
                        )
                        return self.send_json({"ok": True, "status": next_status})
                    if current["accepted_provider_id"]:
                        return self.send_json({"error": "accepted_request_cannot_be_edited"}, 409)
                else:
                    idempotency_key = safe_text(
                        data.get("idempotencyKey")
                        or self.headers.get("Idempotency-Key", ""),
                        128,
                    )
                    existing_request_id = RequestIdempotencyService(con).find(
                        user_id, idempotency_key, data
                    )
                    if existing_request_id:
                        existing_request = con.execute(
                            """SELECT * FROM customer_requests
                            WHERE id=? AND user_id=?""",
                            (existing_request_id, user_id),
                        ).fetchone()
                        if existing_request:
                            return self.send_json(
                                {
                                    "ok": True,
                                    "duplicate": True,
                                    "request": request_with_workflow(
                                        con,
                                        row_customer_request(
                                            existing_request, sign_private=True
                                        ),
                                        asset_visible=True,
                                    ),
                                }
                            )
                    request_id = slug("ord")
                service_value = str(data.get("serviceValue", "") or "").strip()[:120]
                service_name = str(data.get("serviceName", "") or "").strip()[:120]
                if not service_value or "|" not in service_value:
                    return self.send_json({"error": "service_required"}, 400)
                cat_id, service_id = service_value.split("|", 1)
                service_row = con.execute(
                    """SELECT s.ar,s.en FROM services s JOIN categories c ON c.id=s.category_id
                    WHERE s.id=? AND s.category_id=? AND s.active=1 AND c.active=1
                    AND COALESCE(s.deleted_at,'')='' AND COALESCE(c.deleted_at,'')=''""",
                    (safe_text(service_id, 80), safe_text(cat_id, 80)),
                ).fetchone()
                if not service_row:
                    return self.send_json({"error": "service_not_found"}, 400)
                service_value = f"{safe_text(cat_id, 80)}|{safe_text(service_id, 80)}"
                service_name = service_name or service_row["ar"]
                preferred_provider_id = safe_text(
                    data.get("preferredProviderId"), 160
                )
                preferred_provider_row = None
                if preferred_provider_id:
                    preferred_provider_row = con.execute(
                        """SELECT * FROM providers WHERE id=? AND active=1 AND verified=1
                        AND status NOT IN ('unavailable','deleted')
                        AND COALESCE(listing_enabled,1)=1
                        AND COALESCE(request_enabled,1)=1""",
                        (preferred_provider_id,),
                    ).fetchone()
                    if not preferred_provider_row:
                        return self.send_json(
                            {"error": "provider_no_longer_available"}, 409
                        )
                    if InteractionBlockService(con).is_blocked(
                        user_id, preferred_provider_id
                    ):
                        return self.send_json({"error": "interaction_blocked"}, 403)
                    request_probe = {
                        "service_value": service_value,
                        "serviceValue": service_value,
                        "gov": data.get("gov", user_row["gov"]),
                        "wilayah": data.get("wilayah", user_row["wilayah"]),
                    }
                    if not RankingService.exact_service_match(
                        request_probe, dict(preferred_provider_row)
                    ):
                        return self.send_json(
                            {"error": "provider_not_eligible_for_request"}, 409
                        )
                    allowed, _, _ = EntitlementService(con).can_receive(
                        preferred_provider_id
                    )
                    if not allowed:
                        return self.send_json(
                            {"error": "provider_no_longer_available"}, 409
                        )
                images = jload(current["images"], []) if request_id and 'current' in locals() else []
                if data.get("imagesData"):
                    images = save_many_images(request_id, data["imagesData"], "problem", 5)
                request_item = {
                    "id": request_id,
                    "userId": user_id,
                    "customerName": str(data.get("customerName", user_row["name"]) or "")[:80],
                    "phone": user_row["phone"],
                    "serviceValue": service_value,
                    "serviceName": service_name,
                    "gov": str(data.get("gov", user_row["gov"]) or "")[:80],
                    "wilayah": str(data.get("wilayah", user_row["wilayah"]) or "")[:80],
                }
                try:
                    location = normalized_location(data.get("location"))
                    budget_min = finite_number(
                        data.get("budgetMin", 0), minimum=0, maximum=1_000_000
                    )
                    budget_max = finite_number(
                        data.get("budgetMax", 0), minimum=0, maximum=1_000_000
                    )
                except DomainError as err:
                    return self.send_domain_error(err)
                if budget_max and budget_min > budget_max:
                    return self.send_json({"error": "invalid_budget_range"}, 400)
                urgency = data.get("urgency", "normal")
                if urgency not in {"normal", "urgent", "emergency"}:
                    urgency = "normal"
                schedule_type = data.get("scheduleType", "flexible")
                if schedule_type not in {
                    "flexible", "scheduled", "specific", "agreement"
                }:
                    schedule_type = "flexible"
                asset_id = safe_text(data.get("assetId"), 120)
                if asset_id:
                    ServiceAssetService(con).get_for_user(asset_id, user_id)
                organization_id = safe_text(data.get("organizationId"), 120)
                organization_location_id = safe_text(
                    data.get("organizationLocationId"), 120
                )
                requested_by_member_id = safe_text(
                    data.get("requestedByMemberId"), 120
                )
                if organization_id:
                    organization = con.execute(
                        """SELECT id FROM customer_organizations
                        WHERE id=? AND owner_user_id=? AND status='active'""",
                        (organization_id, user_id),
                    ).fetchone()
                    if not organization:
                        return self.send_json(
                            {"error": "organization_access_denied"}, 403
                        )
                    if organization_location_id:
                        location_row = con.execute(
                            """SELECT id FROM organization_locations
                            WHERE id=? AND organization_id=? AND active=1""",
                            (organization_location_id, organization_id),
                        ).fetchone()
                        if not location_row:
                            return self.send_json(
                                {"error": "organization_location_not_found"}, 404
                            )
                    if requested_by_member_id:
                        member_row = con.execute(
                            """SELECT id FROM organization_members
                            WHERE id=? AND organization_id=? AND active=1""",
                            (requested_by_member_id, organization_id),
                        ).fetchone()
                        if not member_row:
                            return self.send_json(
                                {"error": "organization_member_not_found"}, 404
                            )
                else:
                    organization_location_id = ""
                    requested_by_member_id = ""
                if data.get("id"):
                    con.execute("DELETE FROM request_dispatches WHERE request_id=?", (request_id,))
                con.execute(
                    """INSERT INTO customer_requests(
                    id,user_id,customer_name,phone,service_value,service_name,gov,wilayah,
                    latitude,longitude,urgency,schedule_type,requested_at,budget_min,budget_max,
                    location_text,note,images,status,accepted_provider_id,matching_provider_ids,
                    declined_provider_ids,offers_open,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                    ON CONFLICT(id) DO UPDATE SET
                    customer_name=excluded.customer_name,service_value=excluded.service_value,
                    service_name=excluded.service_name,gov=excluded.gov,wilayah=excluded.wilayah,
                    latitude=excluded.latitude,longitude=excluded.longitude,urgency=excluded.urgency,
                    schedule_type=excluded.schedule_type,requested_at=excluded.requested_at,
                    budget_min=excluded.budget_min,budget_max=excluded.budget_max,
                    location_text=excluded.location_text,note=excluded.note,images=excluded.images,
                    status=excluded.status,matching_provider_ids=excluded.matching_provider_ids,
                    offers_open=1,updated_at=CURRENT_TIMESTAMP""",
                    (
                        request_id, user_id, request_item["customerName"], user_row["phone"],
                        service_value, service_name, request_item["gov"], request_item["wilayah"],
                        location.get("lat"), location.get("lng"), urgency,
                        schedule_type, safe_text(data.get("requestedAt"), 80),
                        budget_min, budget_max,
                        str(data.get("locationText", "") or "")[:240],
                        str(data.get("note", "") or "")[:1200], jdump(images), "matching", "",
                        "[]", "[]", 1,
                    ),
                )
                con.execute(
                    """UPDATE customer_requests SET asset_id=?,organization_id=?,
                    organization_location_id=?,requested_by_member_id=? WHERE id=?""",
                    (
                        asset_id,
                        organization_id,
                        organization_location_id,
                        requested_by_member_id,
                        request_id,
                    ),
                )
                if not data.get("id"):
                    RequestIdempotencyService(con).remember(
                        user_id, idempotency_key, request_id, data
                    )
                    RequestLifecycleService(con).record(
                        request_id,
                        "request_created",
                        actor_kind="user",
                        actor_id=user_id,
                        to_status="matching",
                        detail={
                            "serviceValue": service_value,
                            "gov": request_item["gov"],
                            "wilayah": request_item["wilayah"],
                        },
                    )
                else:
                    RequestLifecycleService(con).record(
                        request_id,
                        "request_updated",
                        actor_kind="user",
                        actor_id=user_id,
                        from_status=current["status"],
                        to_status="matching",
                    )
                if preferred_provider_row:
                    direct_request = con.execute(
                        "SELECT * FROM customer_requests WHERE id=?", (request_id,)
                    ).fetchone()
                    KnownProviderInvitationService(con).attach(
                        direct_request, preferred_provider_row
                    )
                    ranked = [{"providerId": preferred_provider_id, "score": 100}]
                    released = [{
                        "requestId": request_id,
                        "providerId": preferred_provider_id,
                        "serviceName": service_name,
                        "area": request_item["wilayah"] or request_item["gov"],
                    }]
                else:
                    marketplace = RequestMarketplace(con)
                    ranked = marketplace.schedule(request_id)
                    released = marketplace.release_due(request_id)
                create_marketplace_notifications(con, released)
                status = "matching" if ranked else "unavailable"
                create_notification(
                    con, "admin", "", "طلب خدمة جديد" if ranked else "خدمة غير متاحة",
                    f"{service_name or service_value} - {request_item['wilayah'] or request_item['gov']}",
                    type_="request", related_id=request_id,
                    priority="normal" if ranked else "high",
                    action_text="فتح الطلب", action_route=f"admin:request:{request_id}",
                )
                saved = con.execute("SELECT * FROM customer_requests WHERE id=?", (request_id,)).fetchone()
                return self.send_json(
                    {
                        "ok": True,
                        "request": request_with_workflow(
                            con,
                            row_customer_request(saved, sign_private=True),
                            asset_visible=True,
                        ),
                        "matchedProviders": len(ranked),
                        "notifiedProviders": len(released),
                    },
                    200 if data.get("id") else 201,
                )
        return self.send_json({"error": "not_found"}, 404)

    def request_suggestion(self, data):
        session = self.require_user()
        if not session:
            return
        user_id = session["userId"]
        action = str(data.get("action") or "candidates")
        suggestion_id = str(data.get("suggestionId") or "")
        request_id = str(data.get("requestId") or "")
        with db() as con:
            suggestion_row = None
            if suggestion_id:
                suggestion_row = con.execute(
                    """SELECT s.*,r.user_id request_owner_id FROM request_provider_suggestions s
                    JOIN customer_requests r ON r.id=s.request_id WHERE s.id=?""",
                    (suggestion_id,),
                ).fetchone()
                if not suggestion_row:
                    return self.send_json({"error": "suggestion_not_found"}, 404)
                request_id = suggestion_row["request_id"]
            request_row = con.execute(
                "SELECT * FROM customer_requests WHERE id=?", (request_id,)
            ).fetchone()
            if not request_row:
                return self.send_json({"error": "request_not_found"}, 404)
            if request_row["status"] not in ACTIVE_REQUEST_STATES or not bool(request_row["offers_open"]):
                return self.send_json({"error": "request_not_open"}, 409)

            if action == "candidates":
                if request_row["user_id"] == user_id:
                    return self.send_json({"error": "request_owner_cannot_suggest"}, 403)
                existing = {
                    row["provider_id"]
                    for row in con.execute(
                        """SELECT provider_id FROM request_provider_suggestions
                        WHERE request_id=? AND status IN ('active','selected')""",
                        (request_id,),
                    )
                }
                providers = [
                    provider for provider in ranked_suggestion_candidates(con, request_row)
                    if provider["id"] not in existing
                ]
                return self.send_json({"ok": True, "providers": providers})

            if action == "create":
                if request_row["user_id"] == user_id:
                    return self.send_json({"error": "request_owner_cannot_suggest"}, 403)
                provider_id = str(data.get("providerId") or "")
                preset_key = str(data.get("presetKey") or "")
                comment = re.sub(r"\s+", " ", str(data.get("comment") or "")).strip()[:160]
                if preset_key not in SUGGESTION_PRESET_KEYS:
                    return self.send_json({"error": "suggestion_comment_required"}, 400)
                candidates = {provider["id"]: provider for provider in ranked_suggestion_candidates(con, request_row, limit=20)}
                provider = candidates.get(provider_id)
                if not provider:
                    return self.send_json({"error": "provider_not_eligible_for_request"}, 409)
                duplicate = con.execute(
                    "SELECT id FROM request_provider_suggestions WHERE request_id=? AND provider_id=?",
                    (request_id, provider_id),
                ).fetchone()
                if duplicate:
                    return self.send_json({"error": "suggestion_already_exists"}, 409)
                per_request = con.execute(
                    """SELECT COUNT(*) n FROM request_provider_suggestions
                    WHERE request_id=? AND suggested_by_user_id=? AND status IN ('active','selected')""",
                    (request_id, user_id),
                ).fetchone()["n"]
                daily = con.execute(
                    """SELECT COUNT(*) n FROM request_provider_suggestions
                    WHERE suggested_by_user_id=? AND created_at>=datetime('now','-1 day')""",
                    (user_id,),
                ).fetchone()["n"]
                if int(per_request) >= 3 or int(daily) >= 10:
                    return self.send_json({"error": "suggestion_rate_limited"}, 429)
                suggestion_id = slug("suggestion")
                con.execute(
                    """INSERT INTO request_provider_suggestions(
                    id,request_id,provider_id,suggested_by_user_id,preset_key,comment)
                    VALUES(?,?,?,?,?,?)""",
                    (suggestion_id, request_id, provider_id, user_id, preset_key, comment),
                )
                create_notification(
                    con, "user", request_row["user_id"], "ترشيح مزود جديد",
                    f"تم ترشيح {provider['name']} لطلب {request_row['service_name'] or request_row['service_value']}",
                    type_="provider_suggestion", related_id=suggestion_id, priority="normal",
                    action_text="عرض الترشيح",
                    action_route=f"user:suggestion:{suggestion_id}:provider:{provider_id}:request:{request_id}",
                )
                item = request_suggestion_by_id(con, suggestion_id)
                return self.send_json({"ok": True, "suggestion": item}, 201)

            if action == "delete":
                if suggestion_row["status"] not in ("active", "selected"):
                    return self.send_json({"error": "suggestion_not_active"}, 409)
                if user_id not in {suggestion_row["suggested_by_user_id"], suggestion_row["request_owner_id"]}:
                    return self.send_json({"error": "suggestion_action_denied"}, 403)
                con.execute(
                    """UPDATE request_provider_suggestions SET status='deleted',deleted_at=CURRENT_TIMESTAMP,
                    updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (suggestion_id,),
                )
                con.execute(
                    "DELETE FROM app_notifications WHERE type='provider_suggestion' AND related_id=?",
                    (suggestion_id,),
                )
                return self.send_json({"ok": True})

            if action == "report":
                if suggestion_row["status"] not in ("active", "selected"):
                    return self.send_json({"error": "suggestion_not_active"}, 409)
                if user_id not in {suggestion_row["suggested_by_user_id"], suggestion_row["request_owner_id"]}:
                    return self.send_json({"error": "suggestion_action_denied"}, 403)
                reason = re.sub(r"\s+", " ", str(data.get("reason") or "")).strip()[:240]
                if not reason:
                    return self.send_json({"error": "report_reason_required"}, 400)
                con.execute(
                    """UPDATE request_provider_suggestions SET status='reported',report_reason=?,
                    reported_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (reason, suggestion_id),
                )
                con.execute(
                    "DELETE FROM app_notifications WHERE type='provider_suggestion' AND related_id=?",
                    (suggestion_id,),
                )
                create_notification(
                    con, "admin", "", "بلاغ عن ترشيح مزود", reason,
                    type_="provider_suggestion", related_id=suggestion_id, priority="high",
                    action_text="فتح الطلب", action_route=f"admin:request:{request_id}",
                )
                return self.send_json({"ok": True})

            if action == "select":
                if suggestion_row["status"] != "active":
                    return self.send_json({"error": "suggestion_not_active"}, 409)
                if user_id != suggestion_row["request_owner_id"]:
                    return self.send_json({"error": "suggestion_action_denied"}, 403)
                provider_id = suggestion_row["provider_id"]
                candidates = {provider["id"]: provider for provider in ranked_suggestion_candidates(con, request_row, limit=20)}
                provider = candidates.get(provider_id)
                if not provider:
                    return self.send_json({"error": "provider_no_longer_available"}, 409)
                matching = jload(request_row["matching_provider_ids"], [])
                matching = list(dict.fromkeys([*matching, provider_id]))
                con.execute(
                    """UPDATE request_provider_suggestions SET status='selected',selected_at=CURRENT_TIMESTAMP,
                    updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (suggestion_id,),
                )
                con.execute(
                    """UPDATE customer_requests SET matching_provider_ids=?,status='matching',waitlisted=0,
                    updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (jdump(matching), request_id),
                )
                con.execute(
                    """INSERT INTO request_dispatches(
                    id,request_id,provider_id,rank,score,score_breakdown,wave,release_at,status,notified_at)
                    VALUES(?,?,?,?,?,'{}',1,CURRENT_TIMESTAMP,'notified',CURRENT_TIMESTAMP)
                    ON CONFLICT(request_id,provider_id) DO UPDATE SET status='notified',
                    release_at=CURRENT_TIMESTAMP,notified_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP""",
                    (slug("dispatch"), request_id, provider_id, 1, float(provider.get("suggestionScore") or 0)),
                )
                create_notification(
                    con, "provider", provider_id, "طلب خدمة اختارك صاحبه",
                    f"أرسل لك صاحب الطلب خدمة {request_row['service_name'] or request_row['service_value']}",
                    type_="request", related_id=request_id, priority="high",
                    action_text="فتح الطلب", action_route=f"provider:request:{request_id}",
                )
                updated = con.execute("SELECT * FROM customer_requests WHERE id=?", (request_id,)).fetchone()
                return self.send_json({
                    "ok": True,
                    "suggestion": request_suggestion_by_id(con, suggestion_id),
                    "request": row_customer_request(updated, sign_private=True),
                })

        return self.send_json({"error": "invalid_suggestion_action"}, 400)

    def request_action(self, data):
        session = self.require_provider("requests")
        if not session:
            return
        request_id = str(data.get("id", ""))
        action = data.get("action")
        if action not in ("accept", "decline"):
            return self.send_json({"error": "invalid_request_action"}, 400)
        provider_id = session["providerId"]
        with db() as con:
            row = con.execute("SELECT * FROM customer_requests WHERE id=?", (request_id,)).fetchone()
            if not row:
                return self.send_json({"error": "request_not_found"}, 404)
            item = row_customer_request(row)
            if provider_id not in item["matchingProviderIds"]:
                return self.send_json({"error": "request_not_assigned_to_provider"}, 403)
            if action == "accept":
                result = con.execute(
                    """UPDATE customer_requests SET accepted_provider_id=?,status='accepted',
                    offers_open=0,contact_consent=?,updated_at=CURRENT_TIMESTAMP
                    WHERE id=? AND offers_open=1 AND COALESCE(accepted_provider_id,'')=''""",
                    (provider_id, jdump({"chat": False, "whatsapp": False, "call": False}), request_id),
                )
                if result.rowcount != 1:
                    return self.send_json({"error": "request_already_accepted"}, 409)
                RequestLifecycleService(con).record(
                    request_id,
                    "provider_accepted",
                    actor_kind="provider",
                    actor_id=provider_id,
                    from_status=item["status"],
                    to_status="accepted",
                )
                consent_service = ContactConsentService(con)
                for channel in ("chat", "whatsapp", "call"):
                    consent_service.set_channel(request_id, item["userId"], provider_id, channel, False)
                con.execute(
                    """UPDATE request_dispatches SET status='accepted',accepted_at=CURRENT_TIMESTAMP,
                    updated_at=CURRENT_TIMESTAMP WHERE request_id=? AND provider_id=?""",
                    (request_id, provider_id),
                )
                create_notification(
                    con, "user", item["userId"], "تم قبول طلبك",
                    f"وافق مزود على طلب {item['serviceName'] or item['serviceValue']}",
                    type_="request", related_id=request_id, priority="high",
                    action_text="عرض الطلب", action_route=f"user:request:{request_id}",
                )
                create_notification(
                    con, "admin", "", "تم قبول طلب",
                    f"{request_id} بواسطة {session.get('name', provider_id)}",
                    type_="request", related_id=request_id,
                    action_text="فتح الطلب", action_route=f"admin:request:{request_id}",
                )
            else:
                declined = list(item["declinedProviderIds"])
                if provider_id not in declined:
                    declined.append(provider_id)
                remaining = [pid for pid in item["matchingProviderIds"] if pid not in declined]
                status = "matching" if remaining else "unavailable"
                con.execute(
                    """UPDATE customer_requests SET declined_provider_ids=?,status=?,
                    offers_open=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (jdump(declined), status, int(bool(remaining)), request_id),
                )
                if not remaining:
                    create_notification(
                        con, "user", item["userId"], "لم يتوفر مزود بعد",
                        "سنحتفظ بطلبك لإيجاد مزود مناسب.", type_="request",
                        related_id=request_id, action_text="عرض الطلب",
                        action_route=f"user:request:{request_id}",
                    )
            return self.send_json({"ok": True, "status": action})

    def request_collaboration(self, data):
        session = self.session()
        if not session:
            return self.send_json({"error": "auth_required"}, 401)
        request_id = str(data.get("id", "") or "")
        action = str(data.get("action", "") or "")
        if not request_id or action not in (
            "offer", "choose_offer", "contact_consent", "message", "arrival",
            "waitlist", "start_work"
        ):
            return self.send_json({"error": "invalid_request_action"}, 400)
        with db() as con:
            row = con.execute(
                "SELECT * FROM customer_requests WHERE id=?", (request_id,)
            ).fetchone()
            if not row:
                return self.send_json({"error": "request_not_found"}, 404)
            item = row_customer_request(row)
            provider_id = session.get("providerId", "")
            user_id = session.get("userId", "")
            is_user = bool(user_id and user_id == item["userId"])
            is_provider = bool(
                provider_id
                and (
                    provider_id in item["matchingProviderIds"]
                    or provider_id == item["acceptedProviderId"]
                )
            )
            blocks = InteractionBlockService(con)

            if action == "offer":
                if not is_provider or not item["offersOpen"] or item["acceptedProviderId"]:
                    return self.send_json({"error": "offer_not_allowed"}, 403)
                blocks.assert_allowed(item["userId"], provider_id)
                try:
                    price = finite_number(
                        data.get("price", 0), minimum=0, maximum=1_000_000
                    )
                    labor_amount = finite_number(
                        data.get("laborAmount", 0),
                        minimum=0,
                        maximum=1_000_000,
                    )
                    materials_amount = finite_number(
                        data.get("materialsAmount", 0),
                        minimum=0,
                        maximum=1_000_000,
                    )
                except DomainError:
                    return self.send_json({"error": "invalid_offer_price"}, 400)
                if labor_amount or materials_amount:
                    price = round(labor_amount + materials_amount, 3)
                duration = str(data.get("duration", "") or "").strip()[:100]
                if not duration:
                    return self.send_json({"error": "offer_duration_required"}, 400)
                try:
                    warranty_days = bounded_int(
                        data.get("warrantyDays", 0),
                        0,
                        minimum=0,
                        maximum=3650,
                    )
                    validity_days = bounded_int(
                        data.get("validityDays", 7),
                        7,
                        minimum=1,
                        maximum=90,
                    )
                except DomainError:
                    return self.send_json({"error": "invalid_offer_terms"}, 400)
                offers = list(item.get("offers") or [])
                existing = next(
                    (offer for offer in offers if offer.get("providerId") == provider_id),
                    None,
                )
                offer = {
                    "id": existing.get("id") if existing else slug("offer"),
                    "providerId": provider_id,
                    "price": price,
                    "laborAmount": labor_amount,
                    "materialsAmount": materials_amount,
                    "duration": duration,
                    "scope": safe_text(data.get("scope"), 600),
                    "warrantyDays": warranty_days,
                    "validUntil": (
                        datetime.now(UTC) + timedelta(days=validity_days)
                    ).isoformat(),
                    "note": str(data.get("note", "") or "").strip()[:500],
                    "status": "pending",
                    "createdAt": existing.get("createdAt") if existing else datetime.now(UTC).isoformat(),
                    "updatedAt": datetime.now(UTC).isoformat(),
                }
                if existing:
                    offers = [offer if row_offer is existing else row_offer for row_offer in offers]
                else:
                    offers.append(offer)
                con.execute(
                    "UPDATE customer_requests SET offers=?,status='viewed',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (jdump(offers[-12:]), request_id),
                )
                con.execute(
                    """UPDATE request_dispatches SET status='offered',
                    offered_at=COALESCE(NULLIF(offered_at,''),CURRENT_TIMESTAMP),
                    updated_at=CURRENT_TIMESTAMP WHERE request_id=? AND provider_id=?""",
                    (request_id, provider_id),
                )
                RequestLifecycleService(con).record(
                    request_id,
                    "offer_submitted",
                    actor_kind="provider",
                    actor_id=provider_id,
                    from_status=item["status"],
                    to_status="viewed",
                    detail={"offerId": offer["id"]},
                )
                recompute_provider_quality(con, provider_id)
                create_notification(
                    con, "user", item["userId"], "وصل عرض جديد لطلبك",
                    f"{session.get('name', 'مزود')} أرسل سعراً ومدة لخدمة {item['serviceName'] or item['serviceValue']}.",
                    type_="request", related_id=request_id, priority="high",
                    action_text="مقارنة العروض", action_route=f"user:request:{request_id}",
                )

            elif action == "choose_offer":
                if not is_user or item["acceptedProviderId"]:
                    return self.send_json({"error": "offer_selection_not_allowed"}, 403)
                offer_id = str(data.get("offerId", "") or "")
                offers = list(item.get("offers") or [])
                selected = next((offer for offer in offers if offer.get("id") == offer_id), None)
                if not selected:
                    return self.send_json({"error": "offer_not_found"}, 404)
                valid_until = parse_iso(selected.get("validUntil"))
                if valid_until and valid_until <= datetime.now(UTC):
                    return self.send_json({"error": "offer_expired"}, 409)
                selected_provider = selected.get("providerId", "")
                blocks.assert_allowed(item["userId"], selected_provider)
                # In-app chat opens after the customer deliberately selects an offer.
                # Phone and WhatsApp remain separately consent-gated.
                chat_granted = True
                for offer in offers:
                    offer["status"] = "accepted" if offer.get("id") == offer_id else "declined"
                provider_row = con.execute(
                    "SELECT name FROM providers WHERE id=?", (selected_provider,)
                ).fetchone()
                provider_name = provider_row["name"] if provider_row else "مزود الخدمة"
                service_name = item["serviceName"] or item["serviceValue"] or "الخدمة"
                customer_name = item.get("customerName") or "عميل خدماتي"
                if str(data.get("language", "ar")).lower() == "en":
                    welcome_text = (
                        f"Hello {customer_name}, this is {provider_name}. "
                        f"Thank you for choosing my offer for {service_name}. "
                        "We can confirm the details and appointment here."
                    )
                else:
                    welcome_text = (
                        f"مرحباً {customer_name}، معك {provider_name}. "
                        f"شكراً لاختيار عرضي لخدمة {service_name}. "
                        "يمكننا الآن تأكيد التفاصيل والموعد هنا."
                    )
                messages = list(item.get("messages") or [])
                welcome_message = {
                    "id": slug("msg"),
                    "sender": "provider",
                    "senderId": selected_provider,
                    "text": welcome_text,
                    "image": "",
                    "audio": "",
                    "location": None,
                    "systemGenerated": True,
                    "createdAt": datetime.now(UTC).isoformat(),
                }
                messages.append(welcome_message)
                con.execute(
                    """UPDATE customer_requests SET offers=?,accepted_provider_id=?,
                    status='accepted',offers_open=0,waitlisted=0,contact_consent=?,messages=?,
                    updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (
                        jdump(offers), selected_provider,
                        jdump({"chat": chat_granted, "whatsapp": False, "call": False}),
                        jdump(messages[-120:]),
                        request_id,
                    ),
                )
                con.execute(
                    """INSERT INTO conversation_threads(request_id,status,updated_at)
                    VALUES(?,'open',CURRENT_TIMESTAMP)
                    ON CONFLICT(request_id) DO UPDATE SET status='open',
                    ended_by_kind='',ended_by_id='',end_reason='',ended_at='',
                    reopened_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP""",
                    (request_id,),
                )
                RequestLifecycleService(con).record(
                    request_id,
                    "offer_selected",
                    actor_kind="user",
                    actor_id=item["userId"],
                    from_status=item["status"],
                    to_status="accepted",
                    detail={"offerId": offer_id, "providerId": selected_provider},
                )
                consent_service = ContactConsentService(con)
                consent_service.set_channel(
                    request_id, item["userId"], selected_provider, "chat", chat_granted
                )
                consent_service.set_channel(
                    request_id, item["userId"], selected_provider, "whatsapp", False
                )
                consent_service.set_channel(
                    request_id, item["userId"], selected_provider, "call", False
                )
                con.execute(
                    """UPDATE request_dispatches SET status=CASE WHEN provider_id=? THEN 'accepted'
                    ELSE 'closed' END,accepted_at=CASE WHEN provider_id=? THEN CURRENT_TIMESTAMP
                    ELSE accepted_at END,updated_at=CURRENT_TIMESTAMP WHERE request_id=?""",
                    (selected_provider, selected_provider, request_id),
                )
                con.execute(
                    "DELETE FROM app_notifications WHERE related_id=? AND target_kind='provider' AND type='request'",
                    (request_id,),
                )
                con.execute(
                    "DELETE FROM app_notifications WHERE related_id=? AND target_kind='user' AND target_id=? AND type='request'",
                    (request_id, item["userId"]),
                )
                create_notification(
                    con, "provider", selected_provider, "اختار العميل عرضك",
                    f"تم اختيار عرضك لخدمة {item['serviceName'] or item['serviceValue']}.",
                    type_="request", related_id=request_id, priority="high",
                    action_text="فتح المهمة", action_route=f"provider:tasks:{request_id}",
                )
                create_notification(
                    con, "user", item["userId"], f"رسالة جديدة من {provider_name}",
                    f"{service_name} • {welcome_text[:105]}",
                    type_="chat", related_id=request_id, priority="high",
                    action_text="فتح المحادثة", action_route=f"user:chat:{request_id}",
                )
                create_notification(
                    con, "admin", "", "تم اختيار عرض",
                    f"{request_id} - المزود {selected_provider}",
                    type_="request", related_id=request_id,
                    action_text="فتح الطلب", action_route=f"admin:request:{request_id}",
                )

            elif action == "start_work":
                if not is_user or not item["acceptedProviderId"]:
                    return self.send_json({"error": "start_work_not_allowed"}, 403)
                blocks.assert_allowed(
                    item["userId"], item["acceptedProviderId"]
                )
                if item["status"] not in ("accepted", "appointmentConfirmed", "inProgress"):
                    return self.send_json({"error": "request_stage_not_allowed"}, 409)
                agreement = RequestAgreementService(con).get(request_id)
                if agreement and agreement.get("status") != "confirmed":
                    return self.send_json({"error": "agreement_confirmation_required"}, 409)
                RequestLifecycleService(con).transition(
                    request_id,
                    "inProgress",
                    actor_kind="user",
                    actor_id=item["userId"],
                    event_type="work_started",
                    allowed_from={"accepted", "appointmentConfirmed", "inProgress"},
                )
                con.execute(
                    "UPDATE app_notifications SET is_read=1 WHERE related_id=? AND type='request'",
                    (request_id,),
                )
                create_notification(
                    con, "provider", item["acceptedProviderId"], "بدأ تنفيذ الطلب",
                    f"أكد العميل بدء تنفيذ {item['serviceName'] or item['serviceValue']}.",
                    type_="request", related_id=request_id, priority="high",
                    action_text="فتح المهمة", action_route=f"provider:tasks:{request_id}",
                )

            elif action == "contact_consent":
                if not is_user or not item["acceptedProviderId"]:
                    return self.send_json({"error": "contact_consent_not_allowed"}, 403)
                blocks.assert_allowed(
                    item["userId"], item["acceptedProviderId"]
                )
                consent_service = ContactConsentService(con)
                existing_consent = consent_service.summary(request_id, item["acceptedProviderId"])
                consent = {
                    "chat": bool(data.get("chat", existing_consent.get("chat", False))),
                    "whatsapp": bool(data.get("whatsapp", existing_consent.get("whatsapp", False))),
                    "call": bool(data.get("call", existing_consent.get("call", False))),
                }
                for channel, granted in consent.items():
                    consent_service.set_channel(
                        request_id, item["userId"], item["acceptedProviderId"], channel, granted
                    )
                consent = consent_service.summary(request_id, item["acceptedProviderId"])
                con.execute(
                    "UPDATE customer_requests SET contact_consent=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (jdump(consent), request_id),
                )
                enabled = [
                    label for key, label in (("chat", "المحادثة"), ("whatsapp", "واتساب"), ("call", "الاتصال"))
                    if consent[key]
                ]
                create_notification(
                    con, "provider", item["acceptedProviderId"], "حدّث العميل خيارات التواصل",
                    "سمح العميل بـ " + (" و".join(enabled) if enabled else "لا توجد قناة تواصل مفعلة بعد"),
                    type_="request", related_id=request_id, priority="normal",
                    action_text="فتح الطلب", action_route=f"provider:request:{request_id}",
                )

            elif action == "message":
                accepted_provider = item["acceptedProviderId"]
                if accepted_provider:
                    blocks.assert_allowed(item["userId"], accepted_provider)
                consent_service = ContactConsentService(con)
                chat_allowed = bool(
                    accepted_provider
                    and consent_service.allowed(request_id, accepted_provider, "chat")
                )
                if not (is_user or (is_provider and provider_id == accepted_provider)):
                    return self.send_json({"error": "chat_not_allowed"}, 403)
                if not chat_allowed:
                    return self.send_json({"error": "chat_consent_required"}, 403)
                conversation_service = ConversationControlService(con)
                conversation_service.assert_open(
                    request_id,
                    "user" if is_user else "provider",
                    user_id if is_user else provider_id,
                )
                text = str(data.get("text", "") or "").strip()[:1000]
                try:
                    location = normalized_location(data.get("location"))
                except (DomainError, ValueError) as err:
                    code = err.code if isinstance(err, DomainError) else str(err)
                    status = err.status if isinstance(err, DomainError) else 400
                    return self.send_json({"error": code}, status)
                image_path = ""
                audio_path = ""
                message_id = slug("msg")
                try:
                    if data.get("imageData"):
                        image_path = save_upload_data(
                            request_id, data["imageData"], f"{message_id}-image",
                            IMAGE_MIMES, 2_500_000,
                        )
                    if data.get("audioData"):
                        audio_path = save_upload_data(
                            request_id, data["audioData"], f"{message_id}-audio",
                            CHAT_MIMES, 4_000_000,
                        )
                except ValueError as err:
                    return self.send_json({"error": str(err)}, 400)
                if not text and not image_path and not audio_path and not location:
                    return self.send_json({"error": "empty_message"}, 400)
                messages = list(item.get("messages") or [])
                message = {
                    "id": message_id,
                    "sender": "user" if is_user else "provider",
                    "senderId": user_id if is_user else provider_id,
                    "text": text,
                    "image": image_path,
                    "audio": audio_path,
                    "location": location,
                    "createdAt": datetime.now(UTC).isoformat(),
                }
                messages.append(message)
                con.execute(
                    "UPDATE customer_requests SET messages=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (jdump(messages[-120:]), request_id),
                )
                target_kind = "provider" if is_user else "user"
                target_id = item["acceptedProviderId"] if is_user else item["userId"]
                if is_user:
                    sender_name = item.get("customerName") or "عميل خدماتي"
                else:
                    provider_row = con.execute(
                        "SELECT name FROM providers WHERE id=?", (provider_id,)
                    ).fetchone()
                    sender_name = provider_row["name"] if provider_row else "مزود الخدمة"
                preview = text[:105] or ("صورة جديدة" if image_path else "رسالة صوتية جديدة" if audio_path else "موقع جديد")
                if not conversation_service.notifications_muted(
                    request_id, target_kind, target_id
                ):
                    create_notification(
                        con, target_kind, target_id, f"رسالة جديدة من {sender_name}",
                        f"{item.get('serviceName') or 'طلب خدمة'} • {preview}",
                        type_="chat", related_id=request_id, priority="normal",
                        action_text="فتح المحادثة",
                        action_route=f"{target_kind}:chat:{request_id}",
                    )

            elif action == "arrival":
                if not is_provider or provider_id != item["acceptedProviderId"]:
                    return self.send_json({"error": "arrival_not_allowed"}, 403)
                blocks.assert_allowed(item["userId"], provider_id)
                status = str(data.get("status", "onTheWay") or "onTheWay")
                if status not in ("onTheWay", "near", "arrived"):
                    return self.send_json({"error": "invalid_arrival_status"}, 400)
                try:
                    location = normalized_location(data.get("location"))
                except DomainError as err:
                    return self.send_domain_error(err)
                arrival = {
                    **(item.get("arrival") or {}),
                    "status": status,
                    "providerLocation": {
                        "lat": location.get("lat"),
                        "lng": location.get("lng"),
                        "accuracy": location.get("accuracy", 0),
                        "updatedAt": location.get("updatedAt", datetime.now(UTC).isoformat()),
                    },
                    "etaMinutes": max(0, int(data.get("etaMinutes", 0) or 0)),
                    "startedAt": (item.get("arrival") or {}).get("startedAt")
                    or datetime.now(UTC).isoformat(),
                    "updatedAt": datetime.now(UTC).isoformat(),
                }
                con.execute(
                    "UPDATE customer_requests SET arrival=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (jdump(arrival), request_id),
                )
                if status in ("onTheWay", "arrived"):
                    create_notification(
                        con, "user", item["userId"],
                        "المزود في الطريق" if status == "onTheWay" else "وصل المزود",
                        f"{session.get('name', 'المزود')} "
                        + (
                            f"سيصل خلال نحو {arrival['etaMinutes']} دقيقة."
                            if status == "onTheWay"
                            else "وصل إلى موقع تنفيذ الخدمة."
                        ),
                        type_="request", related_id=request_id, priority="high",
                        action_text="متابعة الوصول", action_route=f"user:request:{request_id}",
                    )

            elif action == "waitlist":
                if not is_user:
                    return self.send_json({"error": "waitlist_not_allowed"}, 403)
                enabled = bool(data.get("enabled", True))
                con.execute(
                    "UPDATE customer_requests SET waitlisted=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (int(enabled), request_id),
                )

            updated = con.execute(
                "SELECT * FROM customer_requests WHERE id=?", (request_id,)
            ).fetchone()
            response_request = row_customer_request(updated, sign_private=True)
            if provider_id:
                consent = response_request.get("contactConsent") or {}
                if response_request.get("acceptedProviderId") != provider_id or not (
                    consent.get("whatsapp") or consent.get("call")
                ):
                    response_request["phone"] = ""
            return self.send_json({"ok": True, "request": response_request})

    def request_workflow(self, data):
        """Handle agreement, execution, and completion on one guarded workflow."""
        session = self.session()
        if not session or session.get("kind") not in {"user", "provider"}:
            return self.send_json({"error": "auth_required"}, 401)
        request_id = safe_text(data.get("id"), 120)
        action = safe_text(data.get("action"), 80)
        if not request_id or action not in {
            "agreement_save",
            "agreement_confirm",
            "agreement_reject",
            "start_work",
            "completion_submit",
            "completion_decide",
            "asset_attach",
        }:
            return self.send_json({"error": "invalid_workflow_action"}, 400)
        actor_kind = session["kind"]
        actor_id = session.get("userId") or session.get("providerId") or ""
        with db() as con:
            request_row = con.execute(
                "SELECT * FROM customer_requests WHERE id=?", (request_id,)
            ).fetchone()
            if not request_row:
                return self.send_json({"error": "request_not_found"}, 404)
            request = row_customer_request(request_row)
            is_owner = actor_kind == "user" and request["userId"] == actor_id
            is_selected_provider = (
                actor_kind == "provider"
                and request["acceptedProviderId"] == actor_id
            )
            if not (is_owner or is_selected_provider):
                return self.send_json({"error": "request_access_denied"}, 403)

            if action == "agreement_save":
                agreement = RequestAgreementService(con).save(
                    request_id, actor_kind, actor_id, data
                )
                target_kind = "provider" if is_owner else "user"
                target_id = request["acceptedProviderId"] if is_owner else request["userId"]
                create_notification(
                    con,
                    target_kind,
                    target_id,
                    "وصلك اتفاق تنفيذ",
                    "راجع الموعد والمدة والسعر ثم أكّد الاتفاق.",
                    type_="request",
                    related_id=request_id,
                    priority="high",
                    action_text="عرض الاتفاق",
                    action_route=f"{target_kind}:chat:{request_id}",
                )
                create_notification(
                    con, "admin", "", "اتفاق تنفيذ جديد",
                    f"الطلب {request_id} • النسخة {agreement.get('version', 1)}",
                    type_="request", related_id=request_id, priority="high",
                    action_text="مراجعة الاتفاق", action_route=f"admin:request:{request_id}",
                )
            elif action == "agreement_confirm":
                try:
                    version = int(data.get("version") or 0)
                except (TypeError, ValueError):
                    return self.send_json({"error": "invalid_agreement_version"}, 400)
                agreement = RequestAgreementService(con).confirm(
                    request_id, actor_kind, actor_id, version
                )
                target_kind = "provider" if is_owner else "user"
                target_id = request["acceptedProviderId"] if is_owner else request["userId"]
                title = (
                    "تم تأكيد الموعد"
                    if agreement.get("status") == "confirmed"
                    else "الطرف الآخر أكّد الاتفاق"
                )
                create_notification(
                    con,
                    target_kind,
                    target_id,
                    title,
                    "افتح الطلب لمراجعة الموعد الحالي.",
                    type_="request",
                    related_id=request_id,
                    action_text="فتح الطلب",
                    action_route=f"{target_kind}:chat:{request_id}",
                )
                create_notification(
                    con, "admin", "", "تحديث تأكيد اتفاق",
                    f"الطلب {request_id} • {title}", type_="request",
                    related_id=request_id, action_text="عرض الاتفاق",
                    action_route=f"admin:request:{request_id}",
                )
            elif action == "agreement_reject":
                try:
                    version = int(data.get("version") or 0)
                except (TypeError, ValueError):
                    return self.send_json({"error": "invalid_agreement_version"}, 400)
                agreement = RequestAgreementService(con).reject(
                    request_id,
                    actor_kind,
                    actor_id,
                    version,
                    safe_text(data.get("reason"), 240),
                )
                target_kind = "provider" if is_owner else "user"
                target_id = request["acceptedProviderId"] if is_owner else request["userId"]
                create_notification(
                    con,
                    target_kind,
                    target_id,
                    "طُلب تعديل الاتفاق",
                    "راجع بطاقة الاتفاق داخل المحادثة وأرسل نسخة معدلة.",
                    type_="request",
                    related_id=request_id,
                    priority="high",
                    action_text="فتح المحادثة",
                    action_route=f"{target_kind}:chat:{request_id}",
                )
                create_notification(
                    con, "admin", "", "طُلب تعديل اتفاق تنفيذ",
                    f"الطلب {request_id} • النسخة {agreement.get('version', 1)}",
                    type_="request", related_id=request_id, priority="high",
                    action_text="مراجعة الاتفاق", action_route=f"admin:request:{request_id}",
                )
            elif action == "start_work":
                agreement = RequestAgreementService(con).get(request_id)
                if agreement and agreement.get("status") != "confirmed":
                    return self.send_json({"error": "agreement_confirmation_required"}, 409)
                RequestLifecycleService(con).transition(
                    request_id,
                    "inProgress",
                    actor_kind=actor_kind,
                    actor_id=actor_id,
                    event_type="work_started",
                    allowed_from={"accepted", "appointmentConfirmed"},
                )
                target_kind = "provider" if is_owner else "user"
                target_id = request["acceptedProviderId"] if is_owner else request["userId"]
                create_notification(
                    con,
                    target_kind,
                    target_id,
                    "بدأ تنفيذ الطلب",
                    request["serviceName"] or request["serviceValue"],
                    type_="request",
                    related_id=request_id,
                    priority="high",
                    action_text="فتح المهمة",
                    action_route=f"{target_kind}:{'tasks' if target_kind == 'provider' else 'request'}:{request_id}",
                )
            elif action == "completion_submit":
                if not is_selected_provider:
                    return self.send_json({"error": "provider_required"}, 403)
                evidence_service = CompletionEvidenceService(con)
                existing = evidence_service.get(request_id) or {}
                before_images = existing.get("beforeImages", [])
                after_images = existing.get("afterImages", [])
                if data.get("beforeImagesData"):
                    before_images = save_many_images(
                        request_id, data["beforeImagesData"], "completion-before", 5
                    )
                if data.get("afterImagesData"):
                    after_images = save_many_images(
                        request_id, data["afterImagesData"], "completion-after", 5
                    )
                evidence = evidence_service.submit(
                    request_id,
                    actor_id,
                    before_images=before_images,
                    after_images=after_images,
                    note=safe_text(data.get("note"), 600),
                )
                create_notification(
                    con,
                    "user",
                    request["userId"],
                    "اكتمل العمل بانتظار تأكيدك",
                    "راجع صور الإنجاز ثم أكّد حل المشكلة أو أرسلها لمراجعة الجودة.",
                    type_="request",
                    related_id=request_id,
                    priority="high",
                    action_text="مراجعة الإنجاز",
                    action_route=f"user:request:{request_id}",
                )
            elif action == "completion_decide":
                if not is_owner:
                    return self.send_json({"error": "request_owner_required"}, 403)
                decision = safe_text(data.get("decision"), 40)
                evidence = CompletionEvidenceService(con).decide(
                    request_id,
                    actor_id,
                    decision,
                    safe_text(data.get("note"), 600),
                )
                title = (
                    "أكد العميل اكتمال الخدمة"
                    if decision == "resolved"
                    else "أُحيلت الخدمة لمراجعة الجودة"
                )
                create_notification(
                    con,
                    "provider",
                    request["acceptedProviderId"],
                    title,
                    request["serviceName"] or request["serviceValue"],
                    type_="request",
                    related_id=request_id,
                    priority="high" if decision == "issue" else "normal",
                    action_text="فتح المهمة",
                    action_route=f"provider:tasks:{request_id}",
                )
                if decision == "issue":
                    create_notification(
                        con,
                        "admin",
                        "",
                        "طلب يحتاج مراجعة جودة",
                        request["serviceName"] or request["serviceValue"],
                        type_="quality",
                        related_id=request_id,
                        priority="high",
                        action_text="فتح الطلب",
                        action_route=f"admin:request:{request_id}",
                    )
            elif action == "asset_attach":
                if not is_owner:
                    return self.send_json({"error": "request_owner_required"}, 403)
                ServiceAssetService(con).attach(
                    request_id, safe_text(data.get("assetId"), 120), actor_id
                )

            updated_row = con.execute(
                "SELECT * FROM customer_requests WHERE id=?", (request_id,)
            ).fetchone()
            response_request = request_with_workflow(
                con,
                row_customer_request(updated_row, sign_private=True),
                asset_visible=True,
            )
            return self.send_json({"ok": True, "request": response_request})

    def service_assets(self, data):
        session = self.require_user()
        if not session:
            return
        user_id = session["userId"]
        action = safe_text(data.get("action"), 40) or "list"
        with db() as con:
            service = ServiceAssetService(con)
            if action == "list":
                assets = service.list_for_user(
                    user_id, include_archived=bool(data.get("includeArchived"))
                )
            elif action == "save":
                image_path = None
                if data.get("imageData"):
                    asset_ref = safe_text(data.get("id"), 80) or user_id
                    image_path = save_upload_data(
                        asset_ref,
                        data["imageData"],
                        "service-asset",
                        IMAGE_MIMES,
                        2_500_000,
                    )
                asset = service.save(user_id, data, image_path=image_path)
                assets = [asset]
            elif action == "archive":
                service.archive(safe_text(data.get("id"), 120), user_id)
                assets = service.list_for_user(user_id, include_archived=True)
            elif action == "history":
                history = service.history(safe_text(data.get("id"), 120), user_id)
                return self.send_json({"ok": True, "history": history})
            else:
                return self.send_json({"error": "invalid_service_asset_action"}, 400)
            for asset in assets:
                if asset.get("imagePath"):
                    asset["imageUrl"] = secure_media_url(asset["imagePath"])
                    asset.pop("imagePath", None)
            return self.send_json({"ok": True, "serviceAssets": assets})

    def trust_verification(self, data):
        session = self.require_provider("documents")
        if not session:
            return
        action = safe_text(data.get("action"), 40) or "get"
        provider_id = session["providerId"]
        with db() as con:
            service = ProviderVerificationService(con)
            provider = con.execute(
                """SELECT id,provider_type,verified,verification_expiry
                FROM providers WHERE id=? AND COALESCE(status,'')!='deleted'""",
                (provider_id,),
            ).fetchone()
            if not provider:
                return self.send_json({"error": "provider_not_found"}, 404)
            if action == "submit":
                case = service.submit(
                    provider_id,
                    requirements=data.get("requirements", []),
                    evidence=data.get("evidence", []),
                    actor_id=provider_id,
                )
                create_notification(
                    con,
                    "admin",
                    "",
                    "ملف تحقق يحتاج مراجعة",
                    session.get("name") or provider_id,
                    type_="verification",
                    related_id=provider_id,
                    priority="high",
                    action_text="فتح مركز الثقة",
                    action_route=f"admin:trust:{provider_id}",
                )
                log_audit(
                    con,
                    session,
                    "verification.submitted",
                    provider_id,
                    case["id"],
                )
            elif action == "get":
                case = service.ensure_case(provider)
            else:
                return self.send_json(
                    {"error": "invalid_verification_action"}, 400
                )
            case["timeline"] = service.timeline(case["id"], subject_view=True)
            case["evidence"] = [
                secure_media_url(path)
                for path in case.get("evidence", [])
                if path
            ]
            return self.send_json({"ok": True, "verification": case})

    def trust_complaint(self, data):
        session = self.session()
        if not session or session.get("kind") not in {"user", "provider"}:
            return self.send_json({"error": "auth_required"}, 401)
        complaint_id = safe_text(data.get("id"), 120)
        action = safe_text(data.get("action"), 40) or "get"
        if not complaint_id:
            return self.send_json({"error": "complaint_id_required"}, 400)
        actor_kind = session["kind"]
        actor_id = session.get("userId") or session.get("providerId") or ""
        with db() as con:
            row = con.execute(
                "SELECT * FROM complaints WHERE id=?", (complaint_id,)
            ).fetchone()
            if not row:
                return self.send_json({"error": "complaint_not_found"}, 404)
            owns_case = (
                actor_kind == "user" and row["user_id"] == actor_id
            ) or (
                actor_kind == "provider" and row["provider_id"] == actor_id
            )
            if not owns_case:
                return self.send_json(
                    {"error": "complaint_access_denied"}, 403
                )
            service = ComplaintCaseService(con)
            if action == "get":
                complaint = service.get(complaint_id)
            elif action == "message":
                service.add_message(
                    complaint_id,
                    data.get("message", ""),
                    actor_kind=actor_kind,
                    actor_id=actor_id,
                )
                complaint = service.get(complaint_id)
                create_notification(
                    con,
                    "admin",
                    "",
                    "تحديث جديد في ملف شكوى",
                    complaint.get("reason") or complaint_id,
                    type_="complaint",
                    related_id=complaint_id,
                    priority="normal",
                    action_text="فتح ملف الشكوى",
                    action_route=f"admin:complaint:{complaint_id}",
                )
            elif action == "evidence":
                try:
                    evidence_paths = save_many_documents(
                        complaint_id,
                        data.get("evidenceData", []),
                        "problem",
                        5,
                    )
                except ValueError as err:
                    return self.send_json({"error": str(err)}, 400)
                if not evidence_paths:
                    return self.send_json(
                        {"error": "complaint_evidence_required"}, 400
                    )
                service.add_evidence(
                    complaint_id,
                    evidence_paths,
                    uploader_kind=actor_kind,
                    uploader_id=actor_id,
                    labels=data.get("evidenceLabels", []),
                )
                complaint = service.get(complaint_id)
                create_notification(
                    con,
                    "admin",
                    "",
                    "أضيف دليل إلى شكوى",
                    complaint.get("reason") or complaint_id,
                    type_="complaint",
                    related_id=complaint_id,
                    priority="high",
                    action_text="مراجعة الدليل",
                    action_route=f"admin:complaint:{complaint_id}",
                )
            elif action == "reopen" and actor_kind == "user":
                complaint = service.reopen(
                    complaint_id,
                    data.get("message", ""),
                    actor_kind=actor_kind,
                    actor_id=actor_id,
                )
                create_notification(
                    con,
                    "admin",
                    "",
                    "أعاد المستخدم فتح شكوى",
                    complaint.get("reason") or complaint_id,
                    type_="complaint",
                    related_id=complaint_id,
                    priority="high",
                    action_text="فتح ملف الشكوى",
                    action_route=f"admin:complaint:{complaint_id}",
                )
            else:
                return self.send_json(
                    {"error": "invalid_complaint_action"}, 400
                )
            log_audit(
                con,
                session,
                f"complaint.{action}",
                complaint_id,
                complaint.get("status", ""),
            )
            return self.send_json(
                {"ok": True, "complaint": secure_complaint_view(complaint)}
            )

    def trust_block(self, data):
        session = self.session()
        if not session or session.get("kind") not in {"user", "provider"}:
            return self.send_json({"error": "auth_required"}, 401)
        actor_kind = session["kind"]
        actor_id = session.get("userId") or session.get("providerId") or ""
        target_kind = "provider" if actor_kind == "user" else "user"
        target_id = safe_text(data.get("targetId"), 120)
        request_id = safe_text(data.get("requestId"), 120)
        action = safe_text(data.get("action"), 20) or "block"
        with db() as con:
            service = InteractionBlockService(con)
            if action == "block":
                request = None
                if request_id:
                    request = con.execute(
                        """SELECT user_id,accepted_provider_id,matching_provider_ids
                        FROM customer_requests WHERE id=?""",
                        (request_id,),
                    ).fetchone()
                    if not request:
                        return self.send_json(
                            {"error": "request_not_found"}, 404
                        )
                    valid_pair = (
                        request["user_id"]
                        == (actor_id if actor_kind == "user" else target_id)
                        and request["accepted_provider_id"]
                        == (target_id if actor_kind == "user" else actor_id)
                    )
                    if not valid_pair:
                        return self.send_json(
                            {"error": "block_request_relationship_invalid"},
                            403,
                        )
                block = service.block(
                    actor_kind,
                    actor_id,
                    target_kind,
                    target_id,
                    reason=data.get("reason", ""),
                    request_id=request_id,
                )
                if request:
                    con.execute(
                        """UPDATE contact_consents SET status='revoked',
                        revoked_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
                        WHERE request_id=? AND user_id=? AND provider_id=?""",
                        (
                            request_id,
                            request["user_id"],
                            request["accepted_provider_id"],
                        ),
                    )
                    con.execute(
                        """UPDATE customer_requests SET contact_consent=?,
                        updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                        (
                            jdump(
                                {
                                    "chat": False,
                                    "whatsapp": False,
                                    "call": False,
                                }
                            ),
                            request_id,
                        ),
                    )
                log_audit(
                    con,
                    session,
                    "interaction.blocked",
                    target_id,
                    request_id,
                )
            elif action == "unblock":
                service.unblock(
                    actor_kind, actor_id, target_kind, target_id
                )
                block = {}
                log_audit(
                    con,
                    session,
                    "interaction.unblocked",
                    target_id,
                    request_id,
                )
            else:
                return self.send_json({"error": "invalid_block_action"}, 400)
            return self.send_json(
                {
                    "ok": True,
                    "block": block,
                    "interactionBlocks": service.list_for(
                        actor_kind, actor_id
                    ),
                }
            )

    def platform_post(self, data):
        session = self.session()
        if not session or session.get("kind") not in {"user", "provider"}:
            return self.send_json({"error": "auth_required"}, 401)
        kind = session["kind"]
        actor_id = session.get("userId") or session.get("providerId") or ""
        action = safe_text(data.get("action"), 80)
        with db() as con:
            result = None
            if action.startswith("conversation:"):
                request_id = safe_text(data.get("requestId"), 120)
                conversation_action = action.split(":", 1)[1]
                service = ConversationControlService(con)
                if conversation_action == "get":
                    result = service.summary(request_id, kind, actor_id)
                else:
                    result = service.update(
                        request_id, kind, actor_id, conversation_action, data
                    )
                    if conversation_action in {"end", "reopen"}:
                        row = con.execute(
                            "SELECT messages,user_id,accepted_provider_id,service_name FROM customer_requests WHERE id=?",
                            (request_id,),
                        ).fetchone()
                        if row:
                            messages = jload(row["messages"], [])
                            ended = conversation_action == "end"
                            messages.append(
                                {
                                    "id": slug("msg"),
                                    "sender": "system",
                                    "senderId": actor_id,
                                    "text": (
                                        "تم إنهاء المحادثة. يبقى السجل محفوظًا ويمكن للطرف الذي أنهى المحادثة إعادة فتحها."
                                        if ended
                                        else "تمت إعادة فتح المحادثة."
                                    ),
                                    "image": "",
                                    "audio": "",
                                    "location": None,
                                    "systemGenerated": True,
                                    "createdAt": datetime.now(UTC).isoformat(),
                                }
                            )
                            con.execute(
                                "UPDATE customer_requests SET messages=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                                (jdump(messages[-120:]), request_id),
                            )
                            target_kind = "provider" if kind == "user" else "user"
                            target_id = row["accepted_provider_id"] if kind == "user" else row["user_id"]
                            create_notification(
                                con,
                                target_kind,
                                target_id,
                                "تم إنهاء المحادثة" if ended else "تمت إعادة فتح المحادثة",
                                row["service_name"] or "طلب خدمة",
                                type_="chat",
                                related_id=request_id,
                                action_text="عرض المحادثة",
                                action_route=f"{target_kind}:chat:{request_id}",
                            )
                    log_audit(
                        con,
                        session,
                        f"conversation.{conversation_action}",
                        request_id,
                        safe_text(data.get("reason"), 300),
                    )
            elif action == "organization:save" and kind == "user":
                result = OrganizationService(con).save(actor_id, data)
            elif action == "organization:add_member" and kind == "user":
                result = OrganizationService(con).add_member(
                    safe_text(data.get("organizationId"), 120), actor_id, data
                )
            elif action == "organization:add_location" and kind == "user":
                result = OrganizationService(con).add_location(
                    safe_text(data.get("organizationId"), 120), actor_id, data
                )
            elif action == "contract:create" and kind == "user":
                result = MaintenanceContractService(con).create(actor_id, data)
                create_notification(
                    con,
                    "provider",
                    result["providerId"],
                    "عقد صيانة دوري جديد",
                    result["title"],
                    type_="contract",
                    related_id=result["id"],
                    priority="high",
                    action_text="فتح العقود",
                    action_route="provider:growth:contracts",
                )
            elif action == "contract:status":
                result = MaintenanceContractService(con).update_status(
                    safe_text(data.get("id"), 120),
                    kind,
                    actor_id,
                    safe_text(data.get("status"), 40),
                )
            elif action == "crm:update" and kind == "provider":
                result = ProviderCRMService(con).update(
                    actor_id, safe_text(data.get("id"), 120), data
                )
            elif action == "referral:create":
                result = ReferralService(con).create_code(kind, actor_id)
            elif action == "referral:claim":
                result = ReferralService(con).claim(
                    safe_text(data.get("code"), 30), kind, actor_id
                )
                RiskReviewService(con).record(
                    kind,
                    actor_id,
                    "referral_claim",
                    ["new_referral_claim", "human_review_before_reward"],
                    20,
                )
            elif action == "training:complete" and kind == "provider":
                result = TrainingAchievementService(con).complete(
                    actor_id,
                    safe_text(data.get("moduleId"), 120),
                    bounded_int(data.get("score"), 0, minimum=0, maximum=100),
                )
            elif action == "demand:save":
                result = DemandAlertService(con).save(kind, actor_id, data)
            elif action == "demand:cancel":
                DemandAlertService(con).cancel(
                    kind, actor_id, safe_text(data.get("id"), 120)
                )
                result = {"cancelled": True}
            elif action == "legal:save" and kind == "provider":
                result = ProviderLegalProfileService(con).save(actor_id, data)
                create_notification(
                    con,
                    "admin",
                    "",
                    "مسار قانوني لمزود يحتاج مراجعة",
                    session.get("name") or actor_id,
                    type_="verification",
                    related_id=actor_id,
                    priority="high",
                    action_text="فتح مركز التحقق",
                    action_route=f"admin:trust:{actor_id}",
                )
            elif action == "snapshot":
                result = platform_snapshot(con, session)
            else:
                return self.send_json({"error": "invalid_platform_action"}, 400)
            return self.send_json(
                {
                    "ok": True,
                    "result": result,
                    "platform": platform_snapshot(con, session),
                    "serverTime": datetime.now(UTC).isoformat(),
                }
            )

    def admin_platform_post(self, data):
        session = self.require_admin("manage_settings")
        if not session:
            return
        action = safe_text(data.get("action"), 80)
        with db() as con:
            result = None
            if action == "feature:update":
                result = FeatureFlagService(con).update(
                    safe_text(data.get("key"), 80), data, session["id"]
                )
            elif action == "legal:review":
                result = ProviderLegalProfileService(con).review(
                    safe_text(data.get("providerId"), 120),
                    safe_text(data.get("status"), 40),
                    session["id"],
                    safe_text(data.get("note"), 600),
                )
                create_notification(
                    con,
                    "provider",
                    result["providerId"],
                    "تحديث مراجعة المسار القانوني",
                    result["reviewStatus"],
                    type_="verification",
                    related_id=result["providerId"],
                    action_text="فتح مركز التحقق",
                    action_route="provider:account:verification",
                )
            elif action == "risk:record":
                signals = data.get("signals", [])
                if not isinstance(signals, list):
                    return self.send_json({"error": "invalid_risk_signals"}, 400)
                result = RiskReviewService(con).record(
                    safe_text(data.get("subjectKind"), 40),
                    safe_text(data.get("subjectId"), 160),
                    safe_text(data.get("signalType"), 80),
                    signals,
                    bounded_int(data.get("score"), 0, minimum=0, maximum=100),
                )
            elif action == "risk:resolve":
                result = RiskReviewService(con).resolve(
                    safe_text(data.get("id"), 120),
                    session["id"],
                    safe_text(data.get("decision"), 40),
                    safe_text(data.get("note"), 600),
                )
            elif action == "scenario:calculate":
                assumptions = data.get("assumptions", {})
                if not isinstance(assumptions, dict):
                    return self.send_json({"error": "invalid_scenario_assumptions"}, 400)
                result = FinancialScenarioService(con).calculate(assumptions)
            elif action == "scenario:save":
                assumptions = data.get("assumptions", {})
                if not isinstance(assumptions, dict):
                    return self.send_json({"error": "invalid_scenario_assumptions"}, 400)
                result = FinancialScenarioService(con).save(
                    safe_text(data.get("name"), 160), assumptions, session["id"]
                )
            elif action == "enterprise:create":
                scopes = data.get("scopes", [])
                if not isinstance(scopes, list):
                    return self.send_json({"error": "invalid_enterprise_scopes"}, 400)
                result = EnterpriseAPIService(con).create_client(
                    safe_text(data.get("organizationId"), 120),
                    safe_text(data.get("name"), 120),
                    scopes,
                    bounded_int(data.get("rateLimit"), 60, minimum=1, maximum=600),
                )
            elif action == "enterprise:revoke":
                EnterpriseAPIService(con).revoke(safe_text(data.get("id"), 120))
                result = {"revoked": True}
            elif action == "adapter:update":
                key = safe_text(data.get("key"), 80)
                row = con.execute(
                    "SELECT * FROM integration_adapters WHERE key=?", (key,)
                ).fetchone()
                if not row:
                    return self.send_json({"error": "integration_adapter_not_found"}, 404)
                legal_status = safe_text(data.get("legalStatus"), 40) or row["legal_status"]
                mode = safe_text(data.get("mode"), 40) or row["mode"]
                enabled = strict_bool(data.get("enabled"), False)
                config = data.get("config", {})
                if not isinstance(config, dict):
                    return self.send_json({"error": "invalid_adapter_config"}, 400)
                if enabled and (legal_status != "approved" or not config):
                    return self.send_json(
                        {"error": "adapter_contract_and_configuration_required"}, 409
                    )
                safe_config = {
                    key_: safe_text(value, 240)
                    for key_, value in config.items()
                    if key_ in {"providerName", "endpoint", "agreementReference", "environment"}
                }
                con.execute(
                    """UPDATE integration_adapters SET enabled=?,mode=?,legal_status=?,
                    config=?,updated_at=CURRENT_TIMESTAMP WHERE key=?""",
                    (int(enabled), mode, legal_status, jdump(safe_config), key),
                )
                result = next(
                    item for item in adapter_snapshot(con) if item["key"] == key
                )
            elif action == "snapshot":
                result = platform_snapshot(con, session)
            else:
                return self.send_json({"error": "invalid_admin_platform_action"}, 400)
            log_audit(
                con,
                session,
                f"platform.{action}",
                safe_text(data.get("id") or data.get("key") or data.get("providerId"), 160),
                "",
            )
            return self.send_json(
                {
                    "ok": True,
                    "result": result,
                    "platform": platform_snapshot(con, session),
                    "serverTime": datetime.now(UTC).isoformat(),
                }
            )

    def notification_action(self, data):
        session = self.session()
        if not session:
            return self.send_json({"error": "auth_required"}, 401)
        notification_id = str(data.get("id", ""))
        action = data.get("action", "read")
        target_kind = session.get("kind")
        target_id = session.get("providerId") or session.get("userId") or ""
        with db() as con:
            row = con.execute("SELECT * FROM app_notifications WHERE id=?", (notification_id,)).fetchone()
            if not row:
                return self.send_json({"error": "notification_not_found"}, 404)
            if target_kind != "admin" and (
                row["target_kind"] != target_kind or row["target_id"] != target_id
            ):
                return self.send_json({"error": "notification_access_denied"}, 403)
            if action == "delete":
                con.execute("DELETE FROM app_notifications WHERE id=?", (notification_id,))
            else:
                con.execute("UPDATE app_notifications SET is_read=1 WHERE id=?", (notification_id,))
            return self.send_json({"ok": True})

    def recovery_request(self, data):
        phone = normalize_phone(data.get("phone", ""))
        kind = data.get("kind", "user")
        if kind not in ("user", "provider"):
            return self.send_json({"error": "invalid_account_kind"}, 400)
        with db() as con:
            recent = con.execute(
                """SELECT COUNT(*) n FROM password_recoveries
                WHERE phone=? AND created_at>=datetime('now','-1 hour')""", (phone,)
            ).fetchone()["n"]
            if int(recent or 0) >= 5:
                return self.send_json({"error": "recovery_rate_limited"}, 429)
            if kind == "user":
                row = con.execute("SELECT id,name,email FROM app_users WHERE phone=? AND status='active'", (phone,)).fetchone()
            else:
                row = con.execute(
                    """SELECT id,name,email FROM providers WHERE active=1 AND status!='deleted'
                    AND (phone=? OR phone=?)""",
                    (phone, phone.replace("968", "", 1)),
                ).fetchone()
            if not row:
                # Keep the response indistinguishable from an existing account.
                return self.send_json(
                    {
                        "ok": True,
                        "recoveryId": slug("rcv"),
                        "deliveryConfigured": smtp_configured() or whatsapp_configured(),
                        "deliveryChannel": "pending",
                    },
                    202,
                )
            development_code = os.environ.get("KHADAMATI_DEV_OTP_CODE", "").strip()
            code = (
                development_code
                if APP_ENV != "production" and development_code
                else f"{secrets.randbelow(1_000_000):06d}"
            )
            recovery_id = slug("rcv")
            con.execute(
                """INSERT INTO password_recoveries(
                id,account_kind,account_id,phone,code_hash,expires_at)
                VALUES(?,?,?,?,?,?)""",
                (recovery_id, kind, row["id"], phone, hash_pin(code), iso_datetime(minutes=10)),
            )
            create_notification(
                con, "admin", "", "طلب استعادة رمز",
                f"{row['name']} - {phone}", type_="security", related_id=row["id"],
                priority="high", action_text="مراجعة الاسترجاع",
                action_route=f"admin:recovery:{recovery_id}",
            )
        delivery = send_recovery_email(row["email"], row["name"], code)
        if not delivery.get("ok"):
            delivery = send_whatsapp(phone, f"رمز استعادة حساب خدماتي هو: {code}. صالح لمدة 10 دقائق.")
        # Keep the recovery request for secure manual handling when the messaging
        # integration is unavailable. The code remains hashed and is never exposed.
        response = {
            "ok": True,
            "recoveryId": recovery_id,
            "deliveryConfigured": bool(delivery.get("ok")),
            "deliveryChannel": delivery.get("channel") if delivery.get("ok") else "manual",
            "manualReview": not bool(delivery.get("ok")),
        }
        if APP_ENV != "production" and development_code:
            response["debugCode"] = code
        return self.send_json(response)

    def admin_email_code_request(self, data):
        if not smtp_configured():
            return self.send_json(
                {"error": "email_delivery_unavailable", "fallback": "admin_code"}, 503
            )
        request_key = hashlib.sha256(self.client_key().encode("utf-8")).hexdigest()[:32]
        with db() as con:
            recent = con.execute(
                """SELECT COUNT(*) n FROM admin_email_challenges
                WHERE request_key=? AND created_at>=datetime('now','-1 hour')""",
                (request_key,),
            ).fetchone()["n"]
            if int(recent or 0) >= 5:
                return self.send_json({"error": "admin_email_rate_limited"}, 429)
            row = con.execute(
                "SELECT * FROM admin_users WHERE active=1 ORDER BY created_at LIMIT 1"
            ).fetchone()
            if not row:
                return self.send_json({"error": "admin_account_not_found"}, 404)
            development_code = os.environ.get("KHADAMATI_DEV_OTP_CODE", "").strip()
            code = (
                development_code
                if APP_ENV != "production" and re.fullmatch(r"\d{6}", development_code)
                else f"{secrets.randbelow(1_000_000):06d}"
            )
            challenge_id = slug("adm-email")
            con.execute(
                """INSERT INTO admin_email_challenges(
                id,admin_id,code_hash,request_key,expires_at) VALUES(?,?,?,?,?)""",
                (challenge_id, row["id"], hash_pin(code), request_key, iso_datetime(minutes=10)),
            )
        delivery = send_admin_login_email(code)
        if not delivery.get("ok"):
            with db() as con:
                con.execute("DELETE FROM admin_email_challenges WHERE id=?", (challenge_id,))
            return self.send_json(
                {"error": "email_delivery_unavailable", "fallback": "admin_code"}, 503
            )
        response = {
            "ok": True,
            "challengeId": challenge_id,
            "maskedEmail": mask_email(ADMIN_EMAIL),
            "expiresIn": 600,
        }
        if APP_ENV != "production" and development_code:
            response["debugCode"] = code
        return self.send_json(response)

    def recovery_verify(self, data):
        recovery_id = safe_text(data.get("recoveryId", ""), 160)
        code = safe_text(data.get("code", ""), 12)
        with db() as con:
            row = con.execute(
                "SELECT * FROM password_recoveries WHERE id=? AND COALESCE(used_at,'')=''",
                (recovery_id,),
            ).fetchone()
            if not row:
                return self.send_json({"error": "recovery_not_found"}, 404)
            try:
                expires = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00"))
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=UTC)
            except ValueError:
                expires = datetime.now(UTC) - timedelta(seconds=1)
            if expires <= datetime.now(UTC):
                return self.send_json({"error": "recovery_expired"}, 410)
            if int(row["attempts"] or 0) >= 5 or not verify_secret(code, row["code_hash"]):
                con.execute(
                    "UPDATE password_recoveries SET attempts=attempts+1 WHERE id=?", (recovery_id,)
                )
                return self.send_json({"error": "invalid_recovery_code"}, 403)
            reset_token = secrets.token_urlsafe(32)
            con.execute(
                """UPDATE password_recoveries
                SET verified_at=CURRENT_TIMESTAMP,reset_token_hash=? WHERE id=?""",
                (hash_pin(reset_token), recovery_id),
            )
        return self.send_json(
            {"ok": True, "resetToken": reset_token, "accountKind": row["account_kind"]}
        )

    def recovery_complete(self, data):
        recovery_id = str(data.get("recoveryId", ""))
        code = str(data.get("code", ""))
        reset_token = str(data.get("resetToken", ""))
        pin = str(data.get("pin", ""))
        if not re.fullmatch(r"\d{4,8}", pin):
            return self.send_json({"error": "pin_too_short"}, 400)
        with db() as con:
            row = con.execute(
                "SELECT * FROM password_recoveries WHERE id=? AND COALESCE(used_at,'')=''",
                (recovery_id,),
            ).fetchone()
            if not row:
                return self.send_json({"error": "recovery_not_found"}, 404)
            try:
                expires = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00"))
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=UTC)
            except ValueError:
                expires = datetime.now(UTC) - timedelta(seconds=1)
            if expires <= datetime.now(UTC):
                return self.send_json({"error": "recovery_expired"}, 410)
            token_valid = bool(
                reset_token
                and row["verified_at"]
                and row["reset_token_hash"]
                and verify_secret(reset_token, row["reset_token_hash"])
            )
            if not token_valid:
                if int(row["attempts"] or 0) >= 5 or not verify_secret(code, row["code_hash"]):
                    con.execute("UPDATE password_recoveries SET attempts=attempts+1 WHERE id=?", (recovery_id,))
                    return self.send_json({"error": "invalid_recovery_code"}, 403)
            if row["account_kind"] == "user":
                con.execute(
                    "UPDATE app_users SET pin_hash=? WHERE id=?", (hash_pin(pin), row["account_id"])
                )
            else:
                con.execute(
                    "UPDATE providers SET pin_hash=? WHERE id=?", (hash_pin(pin), row["account_id"])
                )
            con.execute("UPDATE password_recoveries SET used_at=CURRENT_TIMESTAMP WHERE id=?", (recovery_id,))
            clear_login_failures(con, row["account_kind"], row["account_id"])
            revoke_account_sessions(con, row["account_kind"], row["account_id"])
        return self.send_json({"ok": True})

    def delete_account(self, data):
        session = self.session()
        if not session or session.get("kind") not in ("user", "provider"):
            return self.send_json({"error": "auth_required"}, 401)
        pin = str(data.get("pin", ""))
        with db() as con:
            if session["kind"] == "user":
                account_id = session["userId"]
                row = con.execute("SELECT pin_hash FROM app_users WHERE id=?", (account_id,)).fetchone()
                if not row or not row["pin_hash"]:
                    return self.send_json({"error": "pin_not_configured"}, 409)
                if row and row["pin_hash"] and not verify_secret(pin, row["pin_hash"]):
                    return self.send_json({"error": "invalid_user_pin"}, 403)
                anonymous_phone = f"deleted-{hashlib.sha256(account_id.encode('utf-8')).hexdigest()[:16]}"
                con.execute(
                    """UPDATE app_users SET status='deleted',name='حساب محذوف',phone=?,pin_hash='',
                    avatar='',latitude=NULL,longitude=NULL,location_updated_at='',updated_at=CURRENT_TIMESTAMP
                    WHERE id=?""",
                    (anonymous_phone, account_id),
                )
                con.execute(
                    """UPDATE contact_consents SET status='revoked',revoked_at=CURRENT_TIMESTAMP,
                    updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND status='granted'""",
                    (account_id,),
                )
                con.execute(
                    "UPDATE push_subscriptions SET active=0 WHERE target_kind='user' AND target_id=?",
                    (account_id,),
                )
            else:
                account_id = session["providerId"]
                row = con.execute("SELECT pin_hash FROM providers WHERE id=?", (account_id,)).fetchone()
                if not row or not verify_secret(pin, row["pin_hash"]):
                    return self.send_json({"error": "invalid_provider_login"}, 403)
                anonymous_phone = f"deleted-{hashlib.sha256(account_id.encode('utf-8')).hexdigest()[:16]}"
                con.execute(
                    """UPDATE providers SET active=0,status='deleted',listing_enabled=0,request_enabled=0,
                    phone=?,pin_hash='',latitude=NULL,longitude=NULL,location_updated_at='',updated_at=CURRENT_TIMESTAMP
                    WHERE id=?""",
                    (anonymous_phone, account_id),
                )
                con.execute(
                    "UPDATE push_subscriptions SET active=0 WHERE target_kind='provider' AND target_id=?",
                    (account_id,),
                )
            revoke_account_sessions(con, session["kind"], account_id)
            create_notification(
                con, "admin", "", "تم حذف حساب",
                f"{session['kind']} - {account_id}", type_="security",
                related_id=account_id, priority="high",
            )
            log_audit(con, session, "account.deleted", account_id, "personal_data_anonymized")
        return self.send_json({"ok": True})

    def push_subscribe(self, data):
        session = self.session()
        if not session:
            return self.send_json({"error": "auth_required"}, 401)
        subscription = data.get("subscription") or {}
        endpoint = safe_text(subscription.get("endpoint"), 2048)
        endpoint_url = urlparse(endpoint)
        keys = subscription.get("keys") if isinstance(subscription.get("keys"), dict) else {}
        if (
            endpoint_url.scheme != "https"
            or not endpoint_url.netloc
            or not safe_text(keys.get("p256dh"), 512)
            or not safe_text(keys.get("auth"), 512)
        ):
            return self.send_json({"error": "push_endpoint_required"}, 400)
        subscription = {
            "endpoint": endpoint,
            "expirationTime": subscription.get("expirationTime"),
            "keys": {
                "p256dh": safe_text(keys.get("p256dh"), 512),
                "auth": safe_text(keys.get("auth"), 512),
            },
        }
        target_id = session.get("providerId") or session.get("userId") or session.get("id") or ""
        with db() as con:
            con.execute(
                """INSERT INTO push_subscriptions(
                id,target_kind,target_id,endpoint,subscription_json)
                VALUES(?,?,?,?,?)
                ON CONFLICT(endpoint) DO UPDATE SET target_kind=excluded.target_kind,
                target_id=excluded.target_id,subscription_json=excluded.subscription_json,active=1""",
                (slug("push"), session["kind"], target_id, endpoint, jdump(subscription)),
            )
        return self.send_json({"ok": True, "deliveryReady": bool(os.environ.get("VAPID_PRIVATE_KEY"))})

    def policy_accept(self, data):
        session = self.session()
        if not session:
            return self.send_json({"error": "auth_required"}, 401)
        policy_version = str(data.get("version", POLICY_VERSION) or POLICY_VERSION)[:40]
        if policy_version != POLICY_VERSION:
            return self.send_json({"error": "policy_version_outdated", "currentVersion": POLICY_VERSION}, 409)
        allowed_documents = {"privacy", "quality", "terms", "cancellation"}
        documents = [
            item for item in data.get("documents", list(allowed_documents))
            if item in allowed_documents
        ]
        if not documents:
            return self.send_json({"error": "policy_documents_required"}, 400)
        user_id = session.get("userId") or session.get("providerId") or session.get("id") or ""
        phone = session.get("phone", "")
        with db() as con:
            if data.get("action") == "withdraw":
                con.execute(
                    """UPDATE policy_acceptances SET withdrawn_at=CURRENT_TIMESTAMP
                    WHERE user_id=? AND policy_version=? AND COALESCE(withdrawn_at,'')=''""",
                    (user_id, policy_version),
                )
                log_audit(con, session, "policy.withdrawn", user_id, policy_version)
                return self.send_json({"ok": True, "withdrawn": True})
            existing = con.execute(
                """SELECT id FROM policy_acceptances WHERE user_id=? AND policy_version=?
                AND COALESCE(withdrawn_at,'')='' LIMIT 1""", (user_id, policy_version)
            ).fetchone()
            if not existing:
                con.execute(
                    """INSERT INTO policy_acceptances(
                    id,user_id,phone,policy_version,document_types,language,metadata)
                    VALUES(?,?,?,?,?,?,?)""",
                    (
                        slug("pol"), user_id, phone, policy_version, jdump(documents),
                        "en" if data.get("language") == "en" else "ar",
                        jdump({"source": "in_app", "consent": True}),
                    ),
                )
                log_audit(con, session, "policy.accepted", user_id, policy_version)
        return self.send_json({"ok": True, "version": policy_version, "documents": documents})

    def provider_post(self, path, data):
        permission = {
            "/api/provider/profile": "profile",
            "/api/provider/image": "media",
            "/api/provider/work-images": "media",
            "/api/provider/media": "media",
            "/api/provider/documents": "documents",
            "/api/provider/pin": "profile",
            "/api/provider/quote-templates": "profile",
            "/api/provider/support": "requests",
            "/api/provider/history": "requests",
            "/api/provider/subscription-request": "subscription",
            "/api/provider/payment-intent": "subscription",
            "/api/provider/team": "team",
            "/api/provider/branches": "branches",
        }.get(path, "requests")
        session = self.require_provider(permission)
        if not session:
            return
        with db() as con:
            row = con.execute("SELECT * FROM providers WHERE id=?", (session["providerId"],)).fetchone()
            if not row:
                return self.send_json({"error": "not_found"}, 404)
            provider = row_provider(row, private=True)
            if path == "/api/provider/history":
                request_id = safe_text(data.get("requestId"), 120)
                action = safe_text(data.get("action", "hide"), 20)
                request_row = con.execute(
                    """SELECT id,status FROM customer_requests
                    WHERE id=? AND accepted_provider_id=?""",
                    (request_id, provider["id"]),
                ).fetchone()
                if not request_row:
                    return self.send_json({"error": "request_not_found"}, 404)
                if request_row["status"] not in {"closed", "archived", "cancelled"}:
                    return self.send_json({"error": "request_not_terminal"}, 409)
                hidden_ids = list(dict.fromkeys(provider.get("hiddenHistoryIds") or []))
                if action == "restore":
                    hidden_ids = [item for item in hidden_ids if item != request_id]
                elif action == "hide":
                    if request_id not in hidden_ids:
                        hidden_ids.append(request_id)
                else:
                    return self.send_json({"error": "invalid_history_action"}, 400)
                con.execute(
                    "UPDATE providers SET hidden_history=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (jdump(hidden_ids[-500:]), provider["id"]),
                )
                log_audit(con, session, f"provider.history.{action}", request_id, provider["id"])
                return self.send_json({"ok": True, "hiddenHistoryIds": hidden_ids[-500:]})
            if path == "/api/provider/quote-templates":
                raw_templates = data.get("templates")
                if not isinstance(raw_templates, list) or not 1 <= len(raw_templates) <= 10:
                    return self.send_json({"error": "invalid_quote_templates"}, 400)
                templates = []
                for raw in raw_templates:
                    if not isinstance(raw, dict):
                        return self.send_json({"error": "invalid_quote_template"}, 400)
                    title_ar = safe_text(raw.get("ar"), 120)
                    title_en = safe_text(raw.get("en"), 120)
                    if not title_ar or not title_en:
                        return self.send_json({"error": "quote_template_title_required"}, 400)
                    try:
                        price = finite_number(raw.get("price", 0) or 0, minimum=0, maximum=1_000_000)
                    except DomainError:
                        return self.send_json({"error": "invalid_offer_price"}, 400)
                    templates.append({
                        "id": safe_text(raw.get("id"), 80) or slug("quote"),
                        "ar": title_ar,
                        "en": title_en,
                        "durationAr": safe_text(raw.get("durationAr"), 100),
                        "durationEn": safe_text(raw.get("durationEn"), 100),
                        "price": price,
                    })
                con.execute(
                    "UPDATE providers SET quote_templates=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (jdump(templates), provider["id"]),
                )
                log_audit(con, session, "provider.quote_templates.updated", provider["id"], str(len(templates)))
                return self.send_json({"ok": True, "templates": templates})
            if path == "/api/provider/support":
                note = safe_text(data.get("note"), 1200)
                if len(note) < 3:
                    return self.send_json({"error": "support_note_required"}, 400)
                notification_id = create_notification(
                    con, "admin", "", "رسالة دعم من مزود",
                    f"{provider['name']}: {note}", type_="provider",
                    related_id=provider["id"], priority="high",
                    action_text="فتح المزود", action_route=f"admin:provider:{provider['id']}",
                )
                log_audit(con, session, "provider.support.sent", provider["id"], notification_id)
                return self.send_json({"ok": True, "notificationId": notification_id}, 201)
            if path == "/api/provider/profile":
                entitlements = EntitlementService(con).profile_limits(
                    provider["id"], preserve_existing=True
                )
                age_was_supplied = "age" in data
                nationality_was_supplied = "nationality" in data
                name = safe_text(data.get("name", provider["name"]), 120)
                phone = normalize_phone(data.get("phone", provider["phone"]))
                email = safe_text(
                    data.get("email", provider.get("email", "")), 160
                ).strip().lower()
                nationality = safe_text(
                    data.get("nationality", provider.get("nationality", "")), 80
                ).strip()
                try:
                    age = int(
                        finite_number(
                            data.get("age", provider.get("age", 0)),
                            minimum=0,
                            maximum=120,
                        )
                    )
                except DomainError as err:
                    return self.send_domain_error(err)
                bio = safe_text(data.get("bio", provider["bio"]), 900)
                commercial_no = safe_text(
                    data.get("commercialNo", provider.get("commercialNo", "")), 120
                )
                status = data.get("status", provider["status"])
                if status not in {"available", "busy", "unavailable", "under_review"}:
                    return self.send_json({"error": "invalid_provider_status"}, 400)
                areas_value = data.get("areas", provider["areas"])
                if not isinstance(areas_value, list):
                    return self.send_json({"error": "areas_must_be_list"}, 400)
                areas = list(
                    dict.fromkeys(safe_text(area, 80) for area in areas_value if safe_text(area, 80))
                )
                governorates_value = data.get(
                    "governorates",
                    provider.get("governorates") or [provider.get("gov", "")],
                )
                if not isinstance(governorates_value, list):
                    return self.send_json({"error": "governorates_must_be_list"}, 400)
                governorates = list(
                    dict.fromkeys(
                        safe_text(item, 80)
                        for item in governorates_value
                        if safe_text(item, 80)
                    )
                )
                try:
                    location = normalized_location(
                        data.get("location", provider.get("location"))
                    )
                    services = normalized_provider_services(
                        con,
                        data.get("services", provider["services"]),
                        limit=max(1, int(entitlements.get("maxServices") or 1)),
                        category_limit=max(1, int(entitlements.get("maxCategories") or 1)),
                        default_areas=areas,
                    )
                    availability = normalized_availability(
                        data.get("availability"),
                        provider.get("availability", {}),
                    )
                except DomainError as err:
                    return self.send_domain_error(err)
                if not name or len(phone) < 11:
                    return self.send_json({"error": "name_and_valid_phone_required"}, 400)
                if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                    return self.send_json({"error": "invalid_email"}, 400)
                if (
                    provider.get("providerType") == "individual"
                    and age_was_supplied
                    and not 18 <= age <= 120
                ):
                    return self.send_json({"error": "invalid_age"}, 400)
                if (
                    provider.get("providerType") == "individual"
                    and nationality_was_supplied
                    and len(nationality) < 2
                ):
                    return self.send_json({"error": "nationality_required"}, 400)
                if not commercial_no:
                    return self.send_json({"error": "commercial_number_required"}, 400)
                word_count = len(bio.split())
                if word_count < 3 or word_count > 20:
                    return self.send_json({"error": "description_word_limit"}, 400)
                if not services:
                    return self.send_json({"error": "service_required"}, 400)
                primary_service_id = safe_text(
                    data.get("primaryServiceId", provider.get("primaryServiceId", "")), 80
                )
                service_ids = {service.get("serviceId") for service in services}
                if primary_service_id not in service_ids:
                    primary_service_id = services[0].get("serviceId", "")
                if len(areas) > int(entitlements.get("maxWilayats") or max(1, len(areas))):
                    return self.send_json({"error": "area_limit_exceeded"}, 409)
                provider.update({
                    "name": name,
                    "phone": phone,
                    "email": email,
                    "age": age,
                    "nationality": nationality,
                    "commercialNo": commercial_no,
                    "verificationExpiry": safe_text(data.get("verificationExpiry", provider.get("verificationExpiry", "")), 40),
                    "commercialExpiry": safe_text(data.get("commercialExpiry", provider.get("commercialExpiry", "")), 40),
                    "licenseExpiry": safe_text(data.get("licenseExpiry", provider.get("licenseExpiry", "")), 40),
                    "gov": safe_text(data.get("gov", provider["gov"]), 80),
                    "governorates": governorates,
                    "wilayah": safe_text(data.get("wilayah", provider["wilayah"]), 80),
                    "location": location,
                    "areas": areas,
                    "bio": bio,
                    "hours": safe_text(data.get("hours", provider["hours"]), 240),
                    "status": status,
                    "services": services,
                    "availability": availability,
                    "primaryServiceId": primary_service_id,
                    "mapVisible": strict_bool(
                        data.get("mapVisible"), provider.get("mapVisible", True)
                    ),
                    "locationSharingExpiresAt": safe_text(
                        data.get(
                            "locationSharingExpiresAt",
                            provider.get("locationSharingExpiresAt", ""),
                        ),
                        80,
                    ),
                    "gender": data.get("gender", provider.get("gender", "not_specified"))
                    if data.get("gender", provider.get("gender", "not_specified"))
                    in {"male", "female", "not_specified"}
                    else "not_specified",
                    "cardImage": data.get("cardImage", provider.get("cardImage", "")),
                    "beforeAfter": data.get("beforeAfter", provider.get("beforeAfter", [])),
                    "beforeAfterData": data.get("beforeAfterData", {}),
                    "introVideoUrl": data.get("introVideoUrl", provider.get("introVideoUrl", "")),
                    "introVideoData": data.get("introVideoData", ""),
                    "active": provider["active"],
                    "verified": provider["verified"],
                    "featured": provider["featured"],
                })
                if data.get("imageData"):
                    provider["imageData"] = data["imageData"]
                if data.get("workImagesData"):
                    provider["workImagesData"] = data["workImagesData"]
                if data.get("documentsData"):
                    provider["documentsData"] = data["documentsData"]
                try:
                    EntitlementService(con).validate_profile(
                        provider["id"],
                        services=provider.get("services", []),
                        areas=provider.get("areas", []),
                        governorates=provider.get("governorates", []),
                    )
                except DomainError as err:
                    return self.send_domain_error(err)
                saved_provider = upsert_provider(con, provider)
                if saved_provider.get("status") == "available":
                    waiting_rows = con.execute(
                        "SELECT * FROM customer_requests WHERE waitlisted=1 AND status='unavailable'"
                    ).fetchall()
                    for waiting_row in waiting_rows:
                        waiting_request = row_customer_request(waiting_row)
                        if not request_matches_provider(waiting_request, saved_provider):
                            continue
                        marketplace = RequestMarketplace(con)
                        ranked = marketplace.schedule(waiting_request["id"])
                        if not ranked:
                            continue
                        released = marketplace.release_due(waiting_request["id"])
                        create_marketplace_notifications(con, released)
                        create_notification(
                            con, "user", waiting_request["userId"], "توفر مزود لخدمتك",
                            f"أصبح هناك مزود مناسب لطلب {waiting_request['serviceName'] or waiting_request['serviceValue']}.",
                            type_="request", related_id=waiting_request["id"], priority="high",
                            action_text="فتح الطلب", action_route=f"user:request:{waiting_request['id']}",
                        )
                log_audit(con, session, "provider.profile.updated", provider["id"], provider["name"])
                updated = con.execute("SELECT * FROM providers WHERE id=?", (provider["id"],)).fetchone()
                return self.send_json({"ok": True, "provider": row_provider(updated, private=True, sign_private=True)})
            if path == "/api/provider/image":
                image_path = save_data_url(provider["id"], data.get("imageData", ""))
                con.execute("UPDATE providers SET image_path=? WHERE id=?", (image_path, provider["id"]))
                recompute_provider_quality(con, provider["id"])
                return self.send_json({"ok": True, "imageUrl": image_url(image_path)})
            if path == "/api/provider/work-images":
                entitlements = EntitlementService(con).for_provider(provider["id"])
                images = save_many_images(
                    provider["id"], data.get("workImagesData", []), "work",
                    max(1, int(entitlements.get("maxImages") or 5)),
                )
                if images:
                    con.execute("UPDATE providers SET work_images=? WHERE id=?", (jdump(images), provider["id"]))
                    recompute_provider_quality(con, provider["id"])
                return self.send_json({"ok": True, "workImageUrls": urls(images)})
            if path == "/api/provider/media":
                action = data.get("action")
                raw_path = str(data.get("path", "") or "")
                selected_path = raw_path.lstrip("/")
                allowed = {provider.get("imagePath", ""), *(provider.get("workImages") or [])}
                if selected_path not in allowed:
                    return self.send_json({"error": "media_not_found"}, 404)
                if action == "set-card":
                    con.execute("UPDATE providers SET card_image=? WHERE id=?", (image_url(selected_path), provider["id"]))
                    return self.send_json({"ok": True, "cardImage": image_url(selected_path)})
                if action == "delete":
                    work_images = [p for p in provider.get("workImages", []) if p != selected_path]
                    avatar = "" if provider.get("imagePath") == selected_path else provider.get("imagePath", "")
                    card_image = provider.get("cardImage", "")
                    if card_image.lstrip("/") == selected_path:
                        card_image = image_url(avatar) if avatar else (image_url(work_images[0]) if work_images else "")
                    con.execute(
                        "UPDATE providers SET image_path=?,work_images=?,card_image=? WHERE id=?",
                        (avatar, jdump(work_images), card_image, provider["id"]),
                    )
                    target = (UPLOAD_DIR / Path(selected_path).name).resolve()
                    try:
                        target.relative_to(UPLOAD_DIR.resolve())
                        if target.is_file():
                            target.unlink()
                    except ValueError:
                        pass
                    recompute_provider_quality(con, provider["id"])
                    return self.send_json({"ok": True, "cardImage": card_image})
                return self.send_json({"error": "invalid_media_action"}, 400)
            if path == "/api/provider/documents":
                docs = save_many_documents(provider["id"], data.get("documentsData", []), "doc", 3)
                if docs:
                    con.execute("UPDATE providers SET documents=? WHERE id=?", (jdump(docs), provider["id"]))
                return self.send_json({
                    "ok": True,
                    "documents": [secure_media_url(path) for path in docs],
                })
            if path == "/api/provider/pin":
                if session.get("role") != "provider_owner" or session.get("memberId"):
                    return self.send_json({"error": "provider_owner_required"}, 403)
                pin = str(data.get("pin", ""))
                if not re.fullmatch(r"\d{4,8}", pin):
                    return self.send_json({"error": "pin_too_short"}, 400)
                if not verify_secret(data.get("currentPin", ""), row["pin_hash"]):
                    return self.send_json({"error": "current_pin_incorrect"}, 403)
                con.execute("UPDATE providers SET pin_hash=? WHERE id=?", (hash_pin(pin), provider["id"]))
                authorization = str(self.headers.get("Authorization", "") or "")
                current_hash = hash_secret(authorization[7:].strip()) if authorization.startswith("Bearer ") else ""
                revoke_account_sessions(con, "provider", provider["id"], current_hash)
                return self.send_json({"ok": True})
            if path == "/api/provider/subscription-request":
                package_id = str(data.get("packageId", "") or "")
                requested_plan = PlanCatalog.get(con, package_id)
                try:
                    result = SubscriptionService(con).request_plan(
                        provider["id"], package_id,
                        coupon_code=str(data.get("couponCode", "") or ""),
                        payment_required=not bool((requested_plan or {}).get("foundation_once")),
                        actor=f"provider:{provider['id']}",
                    )
                except DomainError as err:
                    return self.send_domain_error(err)
                sub_id = result["subscriptionId"]
                pkg = PlanCatalog.get(con, package_id, False)
                create_notification(
                    con, "admin", "", "طلب ترقية باقة",
                    f"{provider['name']} - {pkg['ar']} - {result['amount']} ر.ع",
                    type_="subscription", related_id=sub_id, priority="high",
                    action_text="مراجعة الطلب", action_route=f"admin:subscription:{sub_id}",
                )
                log_audit(con, session, "subscription.requested", provider["id"], package_id)
                return self.send_json({"ok": True, **result})
            if path == "/api/provider/payment-intent":
                try:
                    result = PaymentAdapter(con).create_intent(
                        str(data.get("subscriptionId", "") or ""), provider["id"],
                        client_amount=data.get("amount"),
                    )
                except DomainError as err:
                    return self.send_domain_error(err)
                log_audit(con, session, "payment.intent.created", result["paymentId"], result["reference"])
                return self.send_json({"ok": True, **result}, 201)
            if path == "/api/provider/team":
                entitlements = EntitlementService(con).for_provider(provider["id"])
                requested_id = safe_text(data.get("id"), 100)
                member_id = requested_id or slug("member")
                owned_member = None
                if requested_id:
                    owned_member = con.execute(
                        "SELECT * FROM provider_team_members WHERE id=? AND provider_id=?",
                        (member_id, provider["id"]),
                    ).fetchone()
                    if not owned_member:
                        return self.send_json({"error": "team_member_not_found"}, 404)
                if session.get("role") == "provider_manager" and owned_member and owned_member["role"] != "provider_staff":
                    return self.send_json({"error": "provider_permission_denied"}, 403)
                if data.get("action") == "delete":
                    result = con.execute(
                        "UPDATE provider_team_members SET active=0,updated_at=CURRENT_TIMESTAMP WHERE id=? AND provider_id=?",
                        (member_id, provider["id"]),
                    )
                    if result.rowcount != 1:
                        return self.send_json({"error": "team_member_not_found"}, 404)
                    log_audit(con, session, "provider.team.disabled", member_id, provider["id"])
                    return self.send_json({"ok": True})
                existing_count = con.execute(
                    """SELECT COUNT(*) n FROM provider_team_members
                    WHERE provider_id=? AND active=1 AND id!=?""",
                    (provider["id"], member_id),
                ).fetchone()["n"]
                if int(existing_count or 0) >= int(entitlements.get("teamMembers") or 1) - 1:
                    return self.send_json({"error": "team_member_limit_exceeded"}, 409)
                role = str(data.get("role", "provider_staff") or "provider_staff")
                if role not in {"provider_manager", "provider_staff"}:
                    return self.send_json({"error": "invalid_provider_role"}, 400)
                if session.get("role") == "provider_manager" and role != "provider_staff":
                    return self.send_json({"error": "provider_permission_denied"}, 403)
                name = safe_text(data.get("name"), 120)
                phone = normalize_phone(data.get("phone", ""))
                if not name or len(phone) < 11:
                    return self.send_json({"error": "name_and_valid_phone_required"}, 400)
                pin_hash = owned_member["pin_hash"] if owned_member else ""
                if data.get("pin"):
                    if len(str(data["pin"])) < 4 or len(str(data["pin"])) > 128:
                        return self.send_json({"error": "invalid_pin_length"}, 400)
                    pin_hash = hash_pin(str(data["pin"]))
                if not pin_hash:
                    return self.send_json({"error": "pin_required"}, 400)
                selected_permissions = [
                    item for item in data.get("permissions", [])
                    if item in PROVIDER_ROLE_PERMISSIONS[role]
                ] if isinstance(data.get("permissions"), list) else []
                try:
                    con.execute(
                        """INSERT INTO provider_team_members(
                        id,provider_id,name,phone,role,pin_hash,permissions,active)
                        VALUES(?,?,?,?,?,?,?,1) ON CONFLICT(id) DO UPDATE SET
                        name=excluded.name,phone=excluded.phone,role=excluded.role,
                        pin_hash=excluded.pin_hash,permissions=excluded.permissions,active=1,
                        updated_at=CURRENT_TIMESTAMP
                        WHERE provider_team_members.provider_id=excluded.provider_id""",
                        (
                            member_id, provider["id"], name, phone, role, pin_hash,
                            jdump(selected_permissions),
                        ),
                    )
                except sqlite3.IntegrityError:
                    return self.send_json({"error": "team_phone_already_used"}, 409)
                log_audit(con, session, "provider.team.upserted", member_id, role)
                return self.send_json({"ok": True, "id": member_id})
            if path == "/api/provider/branches":
                entitlements = EntitlementService(con).for_provider(provider["id"])
                requested_id = safe_text(data.get("id"), 100)
                branch_id = requested_id or slug("branch")
                if requested_id and not con.execute(
                    "SELECT id FROM provider_branches WHERE id=? AND provider_id=?",
                    (branch_id, provider["id"]),
                ).fetchone():
                    return self.send_json({"error": "branch_not_found"}, 404)
                if data.get("action") == "delete":
                    result = con.execute(
                        "UPDATE provider_branches SET active=0,updated_at=CURRENT_TIMESTAMP WHERE id=? AND provider_id=?",
                        (branch_id, provider["id"]),
                    )
                    if result.rowcount != 1:
                        return self.send_json({"error": "branch_not_found"}, 404)
                    log_audit(con, session, "provider.branch.disabled", branch_id, provider["id"])
                    return self.send_json({"ok": True})
                existing_count = con.execute(
                    """SELECT COUNT(*) n FROM provider_branches
                    WHERE provider_id=? AND active=1 AND id!=?""",
                    (provider["id"], branch_id),
                ).fetchone()["n"]
                if int(existing_count or 0) >= int(entitlements.get("branches") or 1):
                    return self.send_json({"error": "branch_limit_exceeded"}, 409)
                name = safe_text(data.get("name"), 120)
                if not name:
                    return self.send_json({"error": "branch_name_required"}, 400)
                try:
                    location = normalized_location(data.get("location"))
                except DomainError as err:
                    return self.send_domain_error(err)
                con.execute(
                    """INSERT INTO provider_branches(
                    id,provider_id,name,gov,wilayah,address,latitude,longitude,phone,active)
                    VALUES(?,?,?,?,?,?,?,?,?,1) ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,gov=excluded.gov,wilayah=excluded.wilayah,
                    address=excluded.address,latitude=excluded.latitude,longitude=excluded.longitude,
                    phone=excluded.phone,active=1,updated_at=CURRENT_TIMESTAMP
                    WHERE provider_branches.provider_id=excluded.provider_id""",
                    (
                        branch_id, provider["id"], name,
                        safe_text(data.get("gov"), 80), safe_text(data.get("wilayah"), 80),
                        safe_text(data.get("address"), 240), location.get("lat"), location.get("lng"),
                        normalize_phone(data.get("phone", provider.get("phone", ""))),
                    ),
                )
                log_audit(con, session, "provider.branch.upserted", branch_id, provider["id"])
                return self.send_json({"ok": True, "id": branch_id})
        return self.send_json({"error": "not_found"}, 404)

    def admin_post(self, path, data):
        permission = {
            "/api/admin/providers": "manage_providers",
            "/api/admin/provider-status": "manage_providers",
            "/api/admin/provider-delete": "manage_providers",
            "/api/admin/request-decision": "review_requests",
            "/api/admin/customer-request-action": "review_requests",
            "/api/admin/review-status": "manage_quality",
            "/api/admin/complaint-status": "manage_quality",
            "/api/admin/complaint-case": "manage_quality",
            "/api/admin/verification": "manage_providers",
            "/api/admin/packages": "manage_subscriptions",
            "/api/admin/subscriptions": "manage_subscriptions",
            "/api/admin/payments": "manage_finance",
            "/api/admin/coupons": "manage_subscriptions",
            "/api/admin/campaigns": "manage_campaigns",
            "/api/admin/team": "manage_team",
            "/api/admin/branches": "manage_team",
            "/api/admin/contact-consents": "manage_consent",
            "/api/admin/recovery-code": "manage_admins",
            "/api/admin/settings": "manage_settings",
            "/api/admin/catalog": "manage_settings",
            "/api/admin/locations": "manage_settings",
            "/api/admin/users": "manage_admins",
            "/api/admin/test-whatsapp": "manage_settings",
            "/api/admin/ads": "manage_settings",
        }.get(path, "view_reports")
        session = self.require_admin(permission)
        if not session:
            return
        with db() as con:
            if path == "/api/admin/verification":
                provider_id = safe_text(data.get("providerId"), 120)
                action = safe_text(data.get("action"), 40) or "get"
                service = ProviderVerificationService(con)
                if action == "review":
                    try:
                        case = service.review(
                            provider_id,
                            data,
                            reviewer_id=session["id"],
                        )
                    except DomainError as err:
                        return self.send_domain_error(err)
                    create_notification(
                        con,
                        "provider",
                        provider_id,
                        "تم تحديث حالة التحقق",
                        case["badge"]["ar"],
                        type_="verification",
                        related_id=provider_id,
                        priority=(
                            "high"
                            if case["status"]
                            in {"changes_required", "rejected", "suspended", "expired"}
                            else "normal"
                        ),
                        action_text="فتح حالة التحقق",
                        action_route="provider:account:verification",
                    )
                    log_audit(
                        con,
                        session,
                        "verification.reviewed",
                        provider_id,
                        case["status"],
                    )
                elif action == "get":
                    provider = con.execute(
                        """SELECT id,provider_type,verified,verification_expiry
                        FROM providers WHERE id=? AND COALESCE(status,'')!='deleted'""",
                        (provider_id,),
                    ).fetchone()
                    if not provider:
                        return self.send_json(
                            {"error": "provider_not_found"}, 404
                        )
                    case = service.ensure_case(provider)
                else:
                    return self.send_json(
                        {"error": "invalid_verification_action"}, 400
                    )
                case["timeline"] = service.timeline(case["id"])
                case["evidence"] = [
                    secure_media_url(path)
                    for path in case.get("evidence", [])
                    if path
                ]
                return self.send_json(
                    {"ok": True, "verification": case}
                )
            if path == "/api/admin/complaint-case":
                complaint_id = safe_text(data.get("id"), 120)
                action = safe_text(data.get("action"), 40) or "get"
                service = ComplaintCaseService(con)
                existing = con.execute(
                    "SELECT * FROM complaints WHERE id=?", (complaint_id,)
                ).fetchone()
                if not existing:
                    return self.send_json(
                        {"error": "complaint_not_found"}, 404
                    )
                try:
                    if action == "update":
                        complaint = service.update(
                            complaint_id, data, admin_id=session["id"]
                        )
                    elif action == "message":
                        service.add_message(
                            complaint_id,
                            data.get("message", ""),
                            actor_kind="admin",
                            actor_id=session["id"],
                            visible=bool(data.get("visibleToSubject", True)),
                        )
                        complaint = service.get(
                            complaint_id, private=True
                        )
                    elif action == "evidence":
                        evidence_paths = save_many_documents(
                            complaint_id,
                            data.get("evidenceData", []),
                            "problem",
                            5,
                        )
                        if not evidence_paths:
                            return self.send_json(
                                {"error": "complaint_evidence_required"}, 400
                            )
                        service.add_evidence(
                            complaint_id,
                            evidence_paths,
                            uploader_kind="admin",
                            uploader_id=session["id"],
                            labels=data.get("evidenceLabels", []),
                        )
                        complaint = service.get(
                            complaint_id, private=True
                        )
                    elif action == "get":
                        complaint = service.get(
                            complaint_id, private=True
                        )
                    else:
                        return self.send_json(
                            {"error": "invalid_complaint_action"}, 400
                        )
                except DomainError as err:
                    return self.send_domain_error(err)
                except ValueError as err:
                    return self.send_json({"error": str(err)}, 400)
                if action != "get" and bool(
                    data.get("visibleToSubject", True)
                ):
                    status_label = complaint.get("status", "")
                    if complaint.get("userId"):
                        create_notification(
                            con,
                            "user",
                            complaint["userId"],
                            "تحديث في ملف الشكوى",
                            status_label,
                            type_="complaint",
                            related_id=complaint_id,
                            priority="normal",
                            action_text="متابعة الشكوى",
                            action_route=f"user:complaint:{complaint_id}",
                        )
                    if complaint.get("providerId"):
                        create_notification(
                            con,
                            "provider",
                            complaint["providerId"],
                            "تحديث في ملف الجودة",
                            status_label,
                            type_="complaint",
                            related_id=complaint_id,
                            priority="normal",
                            action_text="متابعة الحالة",
                            action_route=f"provider:complaint:{complaint_id}",
                        )
                if complaint.get("providerId"):
                    recompute_provider_quality(
                        con, complaint["providerId"]
                    )
                log_audit(
                    con,
                    session,
                    f"complaint.{action}",
                    complaint_id,
                    complaint.get("status", ""),
                )
                return self.send_json(
                    {
                        "ok": True,
                        "complaint": secure_complaint_view(complaint),
                    }
                )
            if path == "/api/admin/customer-request-action":
                request_id = safe_text(data.get("id"), 120)
                action = safe_text(data.get("action"), 24)
                if action not in {"pause", "resume", "close", "urgent", "normal"}:
                    return self.send_json({"error": "invalid_request_action"}, 400)
                request_row = con.execute(
                    "SELECT * FROM customer_requests WHERE id=?", (request_id,)
                ).fetchone()
                if not request_row:
                    return self.send_json({"error": "not_found"}, 404)
                current_status = request_row["status"] or "matching"
                terminal = {"closed", "archived", "cancelled", "deleted"}
                offer_states = {"received", "matching", "viewed", "unavailable", "paused"}
                if action in {"pause", "resume"} and current_status not in offer_states:
                    return self.send_json({"error": "request_action_not_allowed"}, 409)
                if action == "pause":
                    con.execute(
                        """UPDATE customer_requests SET status='paused',offers_open=0,
                        updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                        (request_id,),
                    )
                elif action == "resume":
                    con.execute(
                        """UPDATE customer_requests SET status='matching',offers_open=1,waitlisted=0,
                        updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                        (request_id,),
                    )
                elif action == "close":
                    if current_status in terminal:
                        return self.send_json({"error": "request_already_closed"}, 409)
                    con.execute(
                        """UPDATE customer_requests SET status='closed',offers_open=0,
                        updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                        (request_id,),
                    )
                else:
                    con.execute(
                        """UPDATE customer_requests SET urgency=?,updated_at=CURRENT_TIMESTAMP
                        WHERE id=?""",
                        ("urgent" if action == "urgent" else "normal", request_id),
                    )
                user_id = request_row["user_id"] or ""
                if user_id and action in {"pause", "resume", "close"}:
                    title = {
                        "pause": "تم إيقاف استقبال العروض مؤقتاً",
                        "resume": "تم استئناف استقبال العروض",
                        "close": "تم إغلاق الطلب",
                    }[action]
                    create_notification(
                        con,
                        "user",
                        user_id,
                        title,
                        request_row["service_name"] or "طلب خدمة",
                        type_="request",
                        related_id=request_id,
                        priority="normal",
                        action_text="فتح الطلب",
                        action_route=f"user:request:{request_id}",
                    )
                log_audit(
                    con,
                    session,
                    f"customer_request.{action}",
                    request_id,
                    current_status,
                )
                updated = con.execute(
                    "SELECT * FROM customer_requests WHERE id=?", (request_id,)
                ).fetchone()
                return self.send_json(
                    {"ok": True, "request": row_customer_request(updated, True)}
                )
            if path == "/api/admin/locations":
                try:
                    result = LocationCatalogService(con).apply(data)
                except DomainError as err:
                    return self.send_domain_error(err)
                log_audit(
                    con,
                    session,
                    f"location.{safe_text(data.get('action'), 40) or 'updated'}",
                    safe_text(
                        data.get("id")
                        or data.get("governorateId")
                        or data.get("wilayahId"),
                        120,
                    ),
                    "",
                )
                return self.send_json({"ok": True, **result})
            if path == "/api/admin/catalog":
                action = safe_text(data.get("action"), 40)
                category_id = safe_text(data.get("categoryId") or data.get("id"), 80)
                service_id = safe_text(data.get("serviceId"), 80)
                valid_id = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
                if not category_id or not valid_id.fullmatch(category_id):
                    return self.send_json({"error": "invalid_catalog_id"}, 400)
                if action in {
                    "save_service", "set_service_active", "delete_service"
                } and (not service_id or not valid_id.fullmatch(service_id)):
                    return self.send_json({"error": "invalid_catalog_id"}, 400)
                if action == "save_category":
                    name_ar = safe_text(data.get("ar"), 100)
                    name_en = safe_text(data.get("en"), 100)
                    if not name_ar or not name_en:
                        return self.send_json({"error": "catalog_name_required"}, 400)
                    con.execute(
                        """INSERT INTO categories(id,icon,ar,en,active,deleted_at)
                        VALUES(?,?,?,?,1,'') ON CONFLICT(id) DO UPDATE SET
                        icon=excluded.icon,ar=excluded.ar,en=excluded.en,
                        deleted_at=''""",
                        (
                            category_id, safe_text(data.get("icon"), 80),
                            name_ar, name_en,
                        ),
                    )
                    log_audit(
                        con, session, "catalog.category.saved", category_id, name_ar
                    )
                elif action == "set_category_active":
                    active = data.get("active")
                    if active not in (True, False, 0, 1):
                        return self.send_json({"error": "invalid_boolean"}, 400)
                    result = con.execute(
                        """UPDATE categories SET active=? WHERE id=?
                        AND COALESCE(deleted_at,'')=''""",
                        (int(bool(active)), category_id),
                    )
                    if result.rowcount != 1:
                        return self.send_json({"error": "category_not_found"}, 404)
                    log_audit(
                        con, session, "catalog.category.active", category_id,
                        "1" if active else "0",
                    )
                elif action == "delete_category":
                    if catalog_reference_count(con, category_id):
                        return self.send_json({"error": "catalog_item_in_use"}, 409)
                    result = con.execute(
                        """UPDATE categories SET active=0,
                        deleted_at=CURRENT_TIMESTAMP WHERE id=?
                        AND COALESCE(deleted_at,'')=''""",
                        (category_id,),
                    )
                    if result.rowcount != 1:
                        return self.send_json({"error": "category_not_found"}, 404)
                    con.execute(
                        """UPDATE services SET active=0,
                        deleted_at=CURRENT_TIMESTAMP WHERE category_id=?""",
                        (category_id,),
                    )
                    log_audit(
                        con, session, "catalog.category.deleted", category_id, ""
                    )
                elif action == "save_service":
                    category = con.execute(
                        """SELECT id FROM categories WHERE id=?
                        AND COALESCE(deleted_at,'')=''""",
                        (category_id,),
                    ).fetchone()
                    if not category:
                        return self.send_json({"error": "category_not_found"}, 404)
                    name_ar = safe_text(data.get("ar"), 100)
                    name_en = safe_text(data.get("en"), 100)
                    if not name_ar or not name_en:
                        return self.send_json({"error": "catalog_name_required"}, 400)
                    con.execute(
                        """INSERT INTO services(
                        id,category_id,icon,ar,en,active,deleted_at)
                        VALUES(?,?,?,?,?,1,'') ON CONFLICT(id,category_id)
                        DO UPDATE SET icon=excluded.icon,ar=excluded.ar,
                        en=excluded.en,deleted_at=''""",
                        (
                            service_id, category_id,
                            safe_text(data.get("icon"), 80), name_ar, name_en,
                        ),
                    )
                    log_audit(
                        con, session, "catalog.service.saved",
                        f"{category_id}|{service_id}", name_ar,
                    )
                elif action == "set_service_active":
                    active = data.get("active")
                    if active not in (True, False, 0, 1):
                        return self.send_json({"error": "invalid_boolean"}, 400)
                    result = con.execute(
                        """UPDATE services SET active=? WHERE id=? AND category_id=?
                        AND COALESCE(deleted_at,'')=''""",
                        (int(bool(active)), service_id, category_id),
                    )
                    if result.rowcount != 1:
                        return self.send_json({"error": "service_not_found"}, 404)
                    log_audit(
                        con, session, "catalog.service.active",
                        f"{category_id}|{service_id}", "1" if active else "0",
                    )
                elif action == "delete_service":
                    if catalog_reference_count(con, category_id, service_id):
                        return self.send_json({"error": "catalog_item_in_use"}, 409)
                    result = con.execute(
                        """UPDATE services SET active=0,
                        deleted_at=CURRENT_TIMESTAMP WHERE id=? AND category_id=?
                        AND COALESCE(deleted_at,'')=''""",
                        (service_id, category_id),
                    )
                    if result.rowcount != 1:
                        return self.send_json({"error": "service_not_found"}, 404)
                    log_audit(
                        con, session, "catalog.service.deleted",
                        f"{category_id}|{service_id}", "",
                    )
                else:
                    return self.send_json({"error": "invalid_catalog_action"}, 400)
                return self.send_json(
                    {"ok": True, "categories": catalog_snapshot(con)}
                )
            if path == "/api/admin/providers":
                p = upsert_provider(con, data)
                log_audit(con, session, "provider.upserted", p["id"], p.get("name", ""))
                return self.send_json({"ok": True, "provider": p})
            if path == "/api/admin/provider-status":
                provider_id = safe_text(data.get("id"), 120)
                status = safe_text(data.get("status", "available"), 30)
                if status not in {"available", "busy", "unavailable", "under_review", "pending", "suspended", "deleted"}:
                    return self.send_json({"error": "invalid_provider_status"}, 400)
                flags = []
                for key, default in (("active", 1), ("verified", 0), ("featured", 0)):
                    value = data.get(key, default)
                    if value not in (True, False, 0, 1):
                        return self.send_json({"error": "invalid_boolean", "field": key}, 400)
                    flags.append(int(bool(value)))
                if not con.execute("SELECT id FROM providers WHERE id=?", (provider_id,)).fetchone():
                    return self.send_json({"error": "provider_not_found"}, 404)
                con.execute(
                    "UPDATE providers SET active=?, verified=?, featured=?, status=? WHERE id=?",
                    (*flags, status, provider_id),
                )
                provider_row = con.execute(
                    """SELECT id,provider_type,verified,verification_expiry,status
                    FROM providers WHERE id=?""",
                    (provider_id,),
                ).fetchone()
                verification_service = ProviderVerificationService(con)
                verification_case = verification_service.ensure_case(provider_row)
                if flags[1] and verification_case["status"] != "verified":
                    verification_service.review(
                        provider_id,
                        {
                            "status": "verified",
                            "identityStatus": "verified",
                            "entityStatus": (
                                "verified"
                                if provider_row["provider_type"] == "company"
                                else "not_applicable"
                            ),
                            "activityStatus": "verified",
                            "decisionNote": "اعتماد إداري من شاشة المزود.",
                        },
                        reviewer_id=session["id"],
                    )
                elif not flags[1] and verification_case["status"] == "verified":
                    verification_service.review(
                        provider_id,
                        {
                            "status": "changes_required",
                            "identityStatus": verification_case["identityStatus"],
                            "entityStatus": verification_case["entityStatus"],
                            "activityStatus": verification_case["activityStatus"],
                            "decisionNote": "أوقفت الإدارة الاعتماد لحين المراجعة.",
                        },
                        reviewer_id=session["id"],
                    )
                recompute_provider_quality(con, provider_id)
                log_audit(con, session, "provider.status.updated", provider_id, status)
                return self.send_json({"ok": True})
            if path == "/api/admin/provider-delete":
                provider_id = str(data.get("id", "") or "")
                admin_code = safe_text(data.get("adminCode", ""), 128)
                delete_reason = safe_text(data.get("reason", ""), 500)
                admin_row = con.execute(
                    "SELECT code_hash FROM admin_users WHERE id=? AND active=1",
                    (session.get("id", ""),),
                ).fetchone()
                if not admin_row or not verify_secret(admin_code, admin_row["code_hash"]):
                    return self.send_json({"error": "invalid_code"}, 403)
                if len(delete_reason) < 3:
                    return self.send_json({"error": "delete_reason_required"}, 400)
                provider_row = con.execute(
                    "SELECT id,name,phone,active,status FROM providers WHERE id=?", (provider_id,)
                ).fetchone()
                if not provider_row:
                    return self.send_json({"error": "provider_not_found"}, 404)
                if int(provider_row["active"] or 0) or provider_row["status"] not in {
                    "unavailable", "suspended", "deleted"
                }:
                    return self.send_json({"error": "provider_must_be_stopped_before_delete"}, 409)
                anonymous_phone = f"deleted-{hashlib.sha256(provider_id.encode('utf-8')).hexdigest()[:16]}"
                con.execute(
                    """UPDATE providers SET active=0,status='deleted',listing_enabled=0,
                    request_enabled=0,name='حساب مزود محذوف',phone=?,pin_hash='',image_path='',
                    card_image='',work_images='[]',documents='[]',latitude=NULL,longitude=NULL,
                    location_updated_at='',deleted_at=CURRENT_TIMESTAMP,delete_reason=?,
                    updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (anonymous_phone, delete_reason, provider_id),
                )
                con.execute(
                    "UPDATE push_subscriptions SET active=0 WHERE target_kind='provider' AND target_id=?",
                    (provider_id,),
                )
                revoke_account_sessions(con, "provider", provider_id)
                log_audit(
                    con,
                    session,
                    "provider.deleted",
                    provider_id,
                    f"{provider_row['name']} | {delete_reason}",
                )
                return self.send_json({"ok": True})
            if path == "/api/admin/request-decision":
                decision = safe_text(data.get("decision"), 20)
                if decision not in {"accept", "reject"}:
                    return self.send_json({"error": "invalid_request_decision"}, 400)
                row = con.execute("SELECT payload FROM provider_requests WHERE id=?", (data.get("id"),)).fetchone()
                if not row:
                    return self.send_json({"error": "not_found"}, 404)
                payload = jload(row["payload"], {})
                description = safe_text(payload.get("bio") or payload.get("note"), 600)
                if decision == "accept":
                    note_words = len(description.split())
                    if (
                        payload.get("providerType") == "company"
                        and not payload.get("commercialNo")
                    ):
                        return self.send_json({"error": "commercial_number_required"}, 400)
                    credential_expiry = payload.get("commercialExpiry") if payload.get("providerType") == "company" else payload.get("licenseExpiry")
                    requires_credential_expiry = payload.get("providerType") == "company" or bool(payload.get("commercialNo"))
                    if int(payload.get("registrationVersion") or 0) >= 57 and requires_credential_expiry and not credential_expiry:
                        return self.send_json({"error": "credential_expiry_required"}, 400)
                    if payload.get("legalPath") == "individual_foreign" and (
                        not payload.get("employerName") or not payload.get("workPermitExpiry")
                    ):
                        return self.send_json({"error": "foreign_worker_details_required"}, 400)
                    if note_words < 3 or note_words > 20:
                        return self.send_json({"error": "description_word_limit"}, 400)
                    if len(payload.get("documents") or []) < 2:
                        return self.send_json({"error": "documents_required"}, 400)
                    if not payload.get("pinHash"):
                        return self.send_json({"error": "pin_not_configured"}, 400)
                con.execute("DELETE FROM provider_requests WHERE id=?", (data.get("id"),))
                if decision == "accept":
                    provider = {
                        "id": slug("p"),
                        "name": payload.get("name", ""),
                        "phone": payload.get("phone", ""),
                        "providerType": payload.get("providerType", "individual"),
                        "companyName": payload.get("companyName", ""),
                        "companyId": payload.get("companyName", "") if payload.get("providerType") == "company" else "",
                        "commercialNo": payload.get("commercialNo", ""),
                        "commercialExpiry": payload.get("commercialExpiry", ""),
                        "licenseExpiry": payload.get("licenseExpiry", ""),
                        "companySize": payload.get("companySize", ""),
                        "businessRole": payload.get("businessRole", ""),
                        "email": payload.get("email", ""),
                        "age": payload.get("age", 0),
                        "nationality": payload.get("nationality", ""),
                        "gender": payload.get("gender", "not_specified"),
                        "gov": payload.get("gov", ""),
                        "wilayah": payload.get("wilayah", ""),
                        "location": payload.get("location"),
                        "areas": [payload.get("wilayah", "")],
                        "bio": description,
                        "hours": payload.get("hours", ""),
                        "status": "available",
                        "active": True,
                        "verified": True,
                        "featured": False,
                        "packageId": PlanCatalog.foundation_for(
                            "company" if payload.get("providerType") == "company" else "individual"
                        ),
                        "rating": 0,
                        "reviews": 0,
                        "imagePath": payload.get("imagePath", ""),
                        "workImages": payload.get("workImages", []),
                        "documents": payload.get("documents", []),
                        "services": [],
                        "stats": {"views": 0, "whatsapp": 0, "calls": 0},
                        "adminNote": "تم قبوله من الطلبات" + (f" | سجل: {payload.get('commercialNo', '')} | فريق: {payload.get('companySize', '')}" if payload.get("providerType") == "company" else f" | مهنة: {payload.get('businessRole', '')}"),
                        "pinHash": payload.get("pinHash") or "",
                    }
                    services = payload.get("services") if isinstance(payload.get("services"), list) else []
                    foundation_id = PlanCatalog.foundation_for(
                        "company" if payload.get("providerType") == "company" else "individual"
                    )
                    foundation = PlanCatalog.get(con, foundation_id, False) or {}
                    limits = PlanCatalog.account_limits(
                        foundation,
                        "company" if payload.get("providerType") == "company" else "individual",
                    )
                    provider["services"] = normalized_provider_services(
                        con,
                        services,
                        limit=max(1, int(limits.get("maxServices") or 1)),
                        category_limit=max(1, int(limits.get("maxCategories") or 1)),
                        fallback_price=payload.get("priceFrom") or 0,
                        default_areas=[payload.get("wilayah", "")],
                    )
                    service = payload.get("service", "")
                    if not provider["services"] and "|" in service:
                        cat_id, service_id = service.split("|", 1)
                        provider["services"] = [{"id": slug("ps"), "catId": cat_id, "serviceId": service_id, "priceFrom": float(payload.get("priceFrom") or 0), "active": True, "areas": [payload.get("wilayah", "")]}]
                    upsert_provider(con, provider)
                    legal_profile_service = ProviderLegalProfileService(con)
                    legal_profile_service.save(
                        provider["id"],
                        {
                            "pathway": payload.get("legalPath") or (
                                "company"
                                if provider["providerType"] == "company"
                                else "individual_omani"
                            ),
                            "nationality": payload.get("nationality", ""),
                            "residencyStatus": payload.get("residencyStatus", ""),
                            "employerName": payload.get("employerName", ""),
                            "employerAuthorizationStatus": payload.get(
                                "employerAuthorizationStatus", ""
                            ),
                            "workPermitExpiry": payload.get("workPermitExpiry", ""),
                            "residencyExpiry": payload.get("residencyExpiry", ""),
                            "commercialExpiry": payload.get("commercialExpiry", ""),
                            "activityLicenseExpiry": payload.get("licenseExpiry", ""),
                        },
                    )
                    legal_profile_service.review(
                        provider["id"],
                        "approved",
                        session["id"],
                        "تمت مراجعة المسار والوثائق عند قبول التسجيل.",
                    )
                    provider_row_for_verification = con.execute(
                        """SELECT id,provider_type,verified,verification_expiry,status
                        FROM providers WHERE id=?""",
                        (provider["id"],),
                    ).fetchone()
                    verification_service = ProviderVerificationService(con)
                    verification_service.ensure_case(
                        provider_row_for_verification
                    )
                    verification_service.review(
                        provider["id"],
                        {
                            "status": "verified",
                            "identityStatus": "verified",
                            "entityStatus": (
                                "verified"
                                if provider["providerType"] == "company"
                                else "not_applicable"
                            ),
                            "activityStatus": "verified",
                            "expiresAt": credential_expiry,
                            "decisionNote": (
                                "تمت مراجعة الهوية والوثائق والنشاط عند قبول التسجيل."
                            ),
                        },
                        reviewer_id=session["id"],
                    )
                    promoted_session = {
                        "kind": "provider", "providerId": provider["id"],
                        "name": provider["name"], "role": "provider_owner", "memberId": "",
                        "providerPermissions": list(PROVIDER_ROLE_PERMISSIONS["provider_owner"]),
                    }
                    for auth_row in con.execute(
                        "SELECT id,session_json FROM auth_sessions WHERE revoked=0"
                    ):
                        auth_data = jload(auth_row["session_json"], {})
                        if (
                            auth_data.get("kind") == "provider_pending"
                            and auth_data.get("requestId") == data.get("id")
                        ):
                            con.execute(
                                "UPDATE auth_sessions SET session_json=? WHERE id=?",
                                (jdump(promoted_session), auth_row["id"]),
                            )
                    create_notification(
                        con, "provider", provider["id"], "تم اعتماد حسابك",
                        "أصبح حسابك جاهزًا للدخول واستقبال الطلبات المطابقة لخدماتك.",
                        type_="provider", related_id=provider["id"], priority="high",
                        action_text="فتح الحساب", action_route="home",
                    )
                    try:
                        SubscriptionService(con).request_plan(
                            provider["id"], foundation_id, payment_required=False,
                            actor=f"admin:{session['id']}",
                        )
                    except DomainError as err:
                        con.execute(
                            """UPDATE providers SET listing_enabled=0,request_enabled=0,
                            subscription_state='pending_payment' WHERE id=?""",
                            (provider["id"],),
                        )
                        create_notification(
                            con, "admin", "", "تعذر منح فترة التأسيس",
                            f"{provider['name']}: {err.code}", type_="subscription",
                            related_id=provider["id"], priority="high",
                            action_text="فتح المزود", action_route=f"admin:provider:{provider['id']}",
                        )
                    linked_invitations = KnownProviderInvitationService(
                        con
                    ).match_approved_provider(provider["id"], provider["phone"])
                    for linked in linked_invitations:
                        request_owner = con.execute(
                            "SELECT user_id,service_name,service_value FROM customer_requests WHERE id=?",
                            (linked["requestId"],),
                        ).fetchone()
                        if not request_owner:
                            continue
                        create_notification(
                            con,
                            "provider",
                            provider["id"],
                            "طلب العميل الذي دعاك أصبح متاحًا",
                            request_owner["service_name"] or request_owner["service_value"],
                            type_="request",
                            related_id=linked["requestId"],
                            priority="high",
                            action_text="فتح الطلب",
                            action_route=f"provider:request:{linked['requestId']}",
                        )
                        create_notification(
                            con,
                            "user",
                            request_owner["user_id"],
                            "انضم مزودك إلى خدماتي",
                            f"تم ربط {provider['name']} بطلبك ويمكنه الآن إرسال عرضه.",
                            type_="request",
                            related_id=linked["requestId"],
                            priority="high",
                            action_text="فتح الطلب",
                            action_route=f"user:request:{linked['requestId']}",
                        )
                    log_audit(con, session, "provider.request.accepted", provider["id"], provider["name"])
                    send_whatsapp(provider["phone"], "تم قبول حسابك كمزود في خدماتي. يمكنك الدخول من بوابة المزودين.")
                    approved_row = con.execute(
                        "SELECT * FROM providers WHERE id=?", (provider["id"],)
                    ).fetchone()
                else:
                    log_audit(con, session, "provider.request.rejected", data.get("id", ""), payload.get("name", ""))
                return self.send_json({
                    "ok": True,
                    "provider": row_provider(approved_row, private=True, sign_private=True)
                    if decision == "accept" and approved_row else None,
                })
            if path == "/api/admin/review-status":
                review_id = safe_text(data.get("id"), 120)
                approved = strict_bool(data.get("approved"), True)
                action = safe_text(data.get("action", "status"), 30)
                row = con.execute(
                    "SELECT provider_id FROM reviews WHERE id=?", (review_id,)
                ).fetchone()
                if not row:
                    return self.send_json({"error": "review_not_found"}, 404)
                if action == "delete":
                    reason = safe_text(data.get("reason", ""), 500)
                    if len(reason) < 3:
                        return self.send_json({"error": "delete_reason_required"}, 400)
                    con.execute(
                        """UPDATE reviews SET approved=0,deleted_at=CURRENT_TIMESTAMP,
                        moderation_reason=? WHERE id=?""",
                        (reason, review_id),
                    )
                    audit_action = "review.deleted"
                    audit_detail = reason
                else:
                    con.execute(
                        """UPDATE reviews SET approved=?,deleted_at='',
                        moderation_reason=? WHERE id=?""",
                        (
                            int(approved),
                            safe_text(data.get("reason", ""), 500),
                            review_id,
                        ),
                    )
                    audit_action = "review.status.updated"
                    audit_detail = str(approved)
                recompute_provider_quality(con, row["provider_id"])
                log_audit(con, session, audit_action, review_id, audit_detail)
                return self.send_json({"ok": True})
            if path == "/api/admin/complaint-status":
                complaint_id = data.get("id")
                row = con.execute(
                    "SELECT provider_id FROM complaints WHERE id=?",
                    (complaint_id,),
                ).fetchone()
                if not row:
                    return self.send_json({"error": "not_found"}, 404)
                status = safe_text(data.get("status", "open"), 30)
                priority = safe_text(data.get("priority", "normal"), 30)
                status = (
                    "investigating" if status == "reviewing" else status
                )
                try:
                    complaint = ComplaintCaseService(con).update(
                        complaint_id,
                        {
                            "status": status,
                            "priority": priority,
                            "resolution": data.get("resolution", ""),
                            "visibleToSubject": True,
                        },
                        admin_id=session["id"],
                    )
                except DomainError as err:
                    return self.send_domain_error(err)
                if row["provider_id"]:
                    recompute_provider_quality(con, row["provider_id"])
                log_audit(con, session, "complaint.status.updated", complaint_id, status)
                return self.send_json(
                    {
                        "ok": True,
                        "complaint": secure_complaint_view(complaint),
                    }
                )
            if path == "/api/admin/recovery-code":
                recovery_id = safe_text(data.get("id"), 120)
                account_id = safe_text(data.get("accountId"), 120)
                account_kind = safe_text(data.get("accountKind"), 24)
                recovery = None
                if recovery_id:
                    recovery = con.execute(
                        """SELECT * FROM password_recoveries
                        WHERE id=? AND COALESCE(used_at,'')=''""",
                        (recovery_id,),
                    ).fetchone()
                    if not recovery:
                        return self.send_json({"error": "recovery_not_found"}, 404)
                    account_id = recovery["account_id"]
                    account_kind = recovery["account_kind"]
                if account_kind not in {"user", "provider"} or not account_id:
                    return self.send_json({"error": "invalid_recovery_account"}, 400)
                if account_kind == "provider":
                    account = con.execute(
                        "SELECT id,name,phone,email FROM providers WHERE id=?",
                        (account_id,),
                    ).fetchone()
                else:
                    account = con.execute(
                        "SELECT id,name,phone,email FROM app_users WHERE id=?",
                        (account_id,),
                    ).fetchone()
                if not account:
                    return self.send_json({"error": "account_not_found"}, 404)
                temporary_code = f"{secrets.randbelow(1_000_000):06d}"
                expires_at = iso_datetime(minutes=10)
                if recovery:
                    con.execute(
                        """UPDATE password_recoveries SET code_hash=?,attempts=0,expires_at=?
                        WHERE id=?""",
                        (hash_pin(temporary_code), expires_at, recovery_id),
                    )
                    phone = recovery["phone"]
                else:
                    recovery_id = slug("rcv")
                    phone = account["phone"]
                    con.execute(
                        """INSERT INTO password_recoveries(
                        id,account_kind,account_id,phone,code_hash,expires_at)
                        VALUES(?,?,?,?,?,?)""",
                        (
                            recovery_id,
                            account_kind,
                            account_id,
                            phone,
                            hash_pin(temporary_code),
                            expires_at,
                        ),
                    )
                message = (
                    f"مرحباً {account['name']}، رمز التحقق المؤقت لاستعادة حساب خدماتي هو: "
                    f"{temporary_code}. صالح لمدة 10 دقائق. لا تشاركه مع أي شخص آخر."
                )
                email_delivery = send_recovery_email(
                    account["email"], account["name"], temporary_code
                )
                log_audit(con, session, "recovery.code.issued", recovery_id, account_id)
                return self.send_json({
                    "ok": True,
                    "recoveryId": recovery_id,
                    "temporaryCode": temporary_code,
                    "expiresAt": expires_at,
                    "phone": phone,
                    "name": account["name"],
                    "accountKind": account_kind,
                    "whatsappMessage": message,
                    "emailDelivered": bool(email_delivery.get("ok")),
                    "deliveryChannel": "email" if email_delivery.get("ok") else "manual",
                })
            if path == "/api/admin/packages":
                action = safe_text(data.get("action") or "save", 20)
                package_id = safe_text(data.get("id"), 120)
                if action in {"delete", "toggle"} and not package_id:
                    return self.send_json({"error": "package_id_required"}, 400)
                if action == "delete":
                    referenced = int(con.execute(
                        "SELECT COUNT(*) n FROM subscriptions WHERE package_id=?",
                        (package_id,),
                    ).fetchone()["n"]) + int(con.execute(
                        "SELECT COUNT(*) n FROM providers WHERE package_id=?",
                        (package_id,),
                    ).fetchone()["n"])
                    if referenced or package_id in PLAN_IDS:
                        con.execute(
                            "UPDATE packages SET active=0,legacy=1 WHERE id=?",
                            (package_id,),
                        )
                        deleted = False
                    else:
                        con.execute("DELETE FROM packages WHERE id=?", (package_id,))
                        deleted = True
                    log_audit(con, session, "package.deleted", package_id, f"referenced={referenced}")
                    return self.send_json({"ok": True, "deleted": deleted, "archived": not deleted})
                if action == "toggle":
                    current = con.execute("SELECT active FROM packages WHERE id=?", (package_id,)).fetchone()
                    if not current:
                        return self.send_json({"error": "package_not_found"}, 404)
                    active = strict_bool(data.get("active"), not bool(current["active"]))
                    con.execute(
                        "UPDATE packages SET active=?,legacy=CASE WHEN ?=1 THEN 0 ELSE legacy END WHERE id=?",
                        (int(active), int(active), package_id),
                    )
                    saved = con.execute("SELECT * FROM packages WHERE id=?", (package_id,)).fetchone()
                    log_audit(con, session, "package.toggled", package_id, str(active))
                    return self.send_json({"ok": True, "package": row_package(saved)})
                account_scope = safe_text(data.get("accountScope") or "individual", 20)
                if account_scope not in {"individual", "company", "all"}:
                    return self.send_json({"error": "invalid_package_scope"}, 400)
                if not package_id:
                    package_id = slug(f"pkg_{account_scope}")
                current_plan = PlanCatalog.get(con, package_id, False) or {}
                current_individual = PlanCatalog.account_limits(current_plan, "individual")
                current_company = PlanCatalog.account_limits(current_plan, "company")
                individual_limits = {
                    "maxServices": bounded_int(
                        data.get("individualMaxServices", current_individual.get("maxServices", 3)),
                        current_individual.get("maxServices", 3),
                        minimum=1,
                        maximum=100,
                    ),
                    "maxCategories": bounded_int(
                        data.get("individualMaxCategories", current_individual.get("maxCategories", 1)),
                        current_individual.get("maxCategories", 1),
                        minimum=1,
                        maximum=20,
                    ),
                    "maxImages": bounded_int(
                        data.get("individualMaxImages", current_individual.get("maxImages", 5)),
                        current_individual.get("maxImages", 5),
                        minimum=1,
                        maximum=50,
                    ),
                    "maxWilayats": bounded_int(
                        data.get("individualMaxWilayats", current_individual.get("maxWilayats", 1)),
                        current_individual.get("maxWilayats", 1),
                        minimum=0,
                        maximum=100,
                    ),
                }
                company_limits = {
                    "maxServices": bounded_int(
                        data.get("companyMaxServices", current_company.get("maxServices", 6)),
                        current_company.get("maxServices", 6),
                        minimum=1,
                        maximum=100,
                    ),
                    "maxCategories": bounded_int(
                        data.get("companyMaxCategories", current_company.get("maxCategories", 3)),
                        current_company.get("maxCategories", 3),
                        minimum=1,
                        maximum=20,
                    ),
                    "maxImages": bounded_int(
                        data.get("companyMaxImages", current_company.get("maxImages", 10)),
                        current_company.get("maxImages", 10),
                        minimum=1,
                        maximum=50,
                    ),
                    "maxWilayats": bounded_int(
                        data.get("companyMaxWilayats", current_company.get("maxWilayats", 0)),
                        current_company.get("maxWilayats", 0),
                        minimum=0,
                        maximum=100,
                    ),
                }
                selected_limits = company_limits if account_scope == "company" else individual_limits
                if account_scope == "all":
                    selected_limits = {
                        key: max(individual_limits[key], company_limits[key])
                        for key in individual_limits
                    }
                lead_delay_seconds = bounded_int(
                    data.get(
                        "leadDelaySeconds",
                        current_plan.get("lead_delay_seconds")
                        or bounded_int(data.get("leadDelayMinutes", 0), 0, minimum=0, maximum=1440) * 60,
                    ),
                    0,
                    minimum=0,
                    maximum=86400,
                )
                entitlements = {
                    "maxServices": selected_limits["maxServices"],
                    "maxCategories": selected_limits["maxCategories"],
                    "maxImages": selected_limits["maxImages"],
                    "maxWilayats": selected_limits["maxWilayats"],
                    "maxGovernorates": bounded_int(data.get("maxGovernorates", 1), 1, minimum=1, maximum=20),
                    "monthlyResponses": bounded_int(data.get("monthlyResponses", 0), 0, minimum=0, maximum=100000),
                    "leadDelaySeconds": lead_delay_seconds,
                    "leadDelayMinutes": (lead_delay_seconds + 59) // 60,
                    "teamMembers": bounded_int(data.get("teamMembers", 1), 1, minimum=1, maximum=100),
                    "branches": bounded_int(data.get("branches", 1), 1, minimum=1, maximum=100),
                    "sharedInbox": strict_bool(data.get("sharedInbox"), False),
                    "advancedReports": strict_bool(data.get("advancedReports"), False),
                    "accountScope": account_scope,
                    "communityPackageQuota": bounded_int(data.get("communityPackageQuota", 0), 0, minimum=0, maximum=100),
                    "communityPackageDays": bounded_int(data.get("communityPackageDays", 30), 30, minimum=1, maximum=365),
                    "accountLimits": {
                        **({"individual": individual_limits} if account_scope in {"individual", "all"} else {}),
                        **({"company": company_limits} if account_scope in {"company", "all"} else {}),
                    },
                }
                con.execute(
                    """INSERT INTO packages(
                    id,ar,en,price,currency,duration_days,featured_boost,
                    max_services,max_categories,max_images,max_wilayats,max_governorates,
                    monthly_response_limit,lead_delay_minutes,lead_delay_seconds,max_team_members,max_branches,
                    shared_inbox,advanced_reports,badge_ar,badge_en,foundation_once,verified_required,
                    entitlements,active,legacy,account_scope,community_package_quota,community_package_days)
                    VALUES(?,?,?,?,?,?,0,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET ar=excluded.ar,en=excluded.en,price=excluded.price,
                    currency=excluded.currency,duration_days=excluded.duration_days,
                    max_services=excluded.max_services,max_categories=excluded.max_categories,
                    max_images=excluded.max_images,max_wilayats=excluded.max_wilayats,
                    max_governorates=excluded.max_governorates,
                    monthly_response_limit=excluded.monthly_response_limit,
                    lead_delay_minutes=excluded.lead_delay_minutes,
                    lead_delay_seconds=excluded.lead_delay_seconds,
                    max_team_members=excluded.max_team_members,max_branches=excluded.max_branches,
                    shared_inbox=excluded.shared_inbox,advanced_reports=excluded.advanced_reports,
                    badge_ar=excluded.badge_ar,badge_en=excluded.badge_en,
                    foundation_once=excluded.foundation_once,verified_required=excluded.verified_required,
                    entitlements=excluded.entitlements,active=excluded.active,legacy=0,
                    account_scope=excluded.account_scope,
                    community_package_quota=excluded.community_package_quota,
                    community_package_days=excluded.community_package_days""",
                    (
                        package_id,
                        data.get("ar", "باقة"),
                        data.get("en", "Package"),
                        finite_number(data.get("price", 0), 0, minimum=0, maximum=1_000_000),
                        "OMR",
                        bounded_int(data.get("durationDays", 30), 30, minimum=1, maximum=3650),
                        entitlements["maxServices"], entitlements["maxCategories"], entitlements["maxImages"],
                        entitlements["maxWilayats"], entitlements["maxGovernorates"],
                        entitlements["monthlyResponses"], entitlements["leadDelayMinutes"],
                        entitlements["leadDelaySeconds"],
                        entitlements["teamMembers"], entitlements["branches"],
                        int(entitlements["sharedInbox"]), int(entitlements["advancedReports"]),
                        str(data.get("badgeAr", "") or "")[:80],
                        str(data.get("badgeEn", "") or "")[:80],
                        int(strict_bool(data.get("foundationOnce"), bool(current_plan.get("foundation_once")))),
                        int(strict_bool(data.get("verifiedRequired"), bool(current_plan.get("verified_required")))),
                        jdump(entitlements), int(strict_bool(data.get("active"), True)),
                        account_scope, entitlements["communityPackageQuota"], entitlements["communityPackageDays"],
                    ),
                )
                log_audit(con, session, "package.upserted", package_id, data.get("ar", ""))
                saved = con.execute("SELECT * FROM packages WHERE id=?", (package_id,)).fetchone()
                return self.send_json({"ok": True, "package": row_package(saved)})
            if path == "/api/admin/subscriptions":
                action = str(data.get("action", "request") or "request")
                service = SubscriptionService(con)
                try:
                    if action == "activate":
                        result = service.activate(
                            str(data.get("id", "") or ""), actor=f"admin:{session['id']}"
                        )
                    elif action == "extend":
                        result = service.extend(
                            str(data.get("id", "") or ""),
                            days=int(data.get("days", 0) or 0) or None,
                            actor=f"admin:{session['id']}",
                        )
                    elif action == "suspend":
                        service.suspend(
                            str(data.get("id", "") or ""), actor=f"admin:{session['id']}",
                            reason=str(data.get("note", "") or ""),
                        )
                        result = {"id": data.get("id"), "status": "suspended"}
                    elif action == "cancel":
                        service.cancel(
                            str(data.get("id", "") or ""), actor=f"admin:{session['id']}",
                            reason=str(data.get("note", "") or ""),
                        )
                        result = {"id": data.get("id"), "status": "cancelled"}
                    elif action == "refund":
                        service.refund(
                            str(data.get("id", "") or ""), actor=f"admin:{session['id']}",
                            reason=str(data.get("note", "") or ""),
                        )
                        result = {"id": data.get("id"), "status": "refunded"}
                    else:
                        provider_id = str(data.get("providerId", "") or "")
                        package_id = str(data.get("packageId", "") or "")
                        result = service.request_plan(
                            provider_id, package_id,
                            coupon_code=str(data.get("couponCode", "") or ""),
                            payment_required=not bool(data.get("approveWithoutPayment", False)),
                            actor=f"admin:{session['id']}",
                        )
                except DomainError as err:
                    return self.send_domain_error(err)
                sub_id = result.get("id") or result.get("subscriptionId") or data.get("id", "")
                if sub_id and data.get("note"):
                    con.execute(
                        "UPDATE subscriptions SET note=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (str(data.get("note", ""))[:500], sub_id),
                    )
                subscription_row = con.execute(
                    """SELECT s.provider_id,s.package_id,s.status,p.ar package_ar
                    FROM subscriptions s LEFT JOIN packages p ON p.id=s.package_id WHERE s.id=?""",
                    (sub_id,),
                ).fetchone() if sub_id else None
                if subscription_row:
                    notification_copy = {
                        "activate": ("تم تفعيل الاشتراك", "أصبحت صلاحيات الباقة متاحة للحساب."),
                        "extend": ("تم تمديد الاشتراك", "تم تحديث تاريخ انتهاء الاشتراك."),
                        "suspend": ("تم إيقاف الاشتراك", "توقف الظهور واستقبال الطلبات حتى إعادة التفعيل."),
                        "cancel": ("تم إلغاء الاشتراك", "تم إلغاء طلب الاشتراك مع الاحتفاظ بسجل الحساب."),
                        "refund": ("تم استرداد الاشتراك", "تم تحديث حالة الاشتراك والمدفوعات."),
                    }
                    if action in notification_copy:
                        title, message = notification_copy[action]
                        create_notification(
                            con, "provider", subscription_row["provider_id"], title, message,
                            type_="subscription", related_id=sub_id, priority="high" if action in {"suspend", "cancel", "refund"} else "normal",
                            action_text="فتح الاشتراك", action_route="subscription",
                        )
                    elif action == "request" and subscription_row["status"] == "pending_payment":
                        create_notification(
                            con, "admin", "", "طلب اشتراك ينتظر الإجراء",
                            f"طلب جديد لباقـة {subscription_row['package_ar'] or subscription_row['package_id']}.",
                            type_="subscription", related_id=sub_id, priority="normal",
                            action_text="فتح الاشتراكات", action_route=f"admin:subscription:{sub_id}",
                        )
                log_audit(con, session, f"subscription.{action}", sub_id, jdump(result))
                return self.send_json({"ok": True, "subscription": result})
            if path == "/api/admin/payments":
                action = str(data.get("action", "confirm") or "confirm")
                payment_id = str(data.get("id", "") or "")
                try:
                    if action == "record":
                        subscription_id = str(data.get("subscriptionId", "") or "")
                        subscription = con.execute(
                            "SELECT * FROM subscriptions WHERE id=?", (subscription_id,)
                        ).fetchone()
                        if not subscription:
                            raise DomainError("subscription_not_found", 404)
                        adapter = PaymentAdapter(con)
                        intent = adapter.create_intent(
                            subscription_id,
                            subscription["provider_id"],
                            client_amount=data.get("amount"),
                        )
                        payment_id = intent["paymentId"]
                        method = str(data.get("method", "manual") or "manual")
                        if method not in {"manual", "cash", "bank"}:
                            raise DomainError("invalid_payment_method")
                        con.execute(
                            "UPDATE payments SET method=?,note=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                            (method, str(data.get("note", ""))[:500], payment_id),
                        )
                        result = adapter.confirm_manual(payment_id, actor=f"admin:{session['id']}")
                    elif action == "confirm":
                        result = PaymentAdapter(con).confirm_manual(
                            payment_id, actor=f"admin:{session['id']}"
                        )
                    elif action == "refund":
                        payment = con.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()
                        if not payment:
                            raise DomainError("payment_not_found", 404)
                        if payment["status"] != "paid":
                            raise DomainError("invalid_payment_transition", 409)
                        con.execute(
                            """UPDATE payments SET status='refunded',refunded_at=CURRENT_TIMESTAMP,
                            updated_at=CURRENT_TIMESTAMP WHERE id=?""", (payment_id,)
                        )
                        if payment["subscription_id"]:
                            SubscriptionService(con).refund(
                                payment["subscription_id"], actor=f"admin:{session['id']}",
                                reason=str(data.get("note", "") or ""),
                            )
                        result = {"id": payment_id, "status": "refunded"}
                    else:
                        raise DomainError("invalid_payment_action")
                except DomainError as err:
                    return self.send_domain_error(err)
                if action in {"record", "confirm"}:
                    paid = con.execute(
                        """SELECT p.provider_id,p.subscription_id,s.package_id
                        FROM payments p LEFT JOIN subscriptions s ON s.id=p.subscription_id WHERE p.id=?""",
                        (payment_id,),
                    ).fetchone()
                    if paid:
                        create_notification(
                            con, "provider", paid["provider_id"], "تم تأكيد الدفعة وتفعيل الاشتراك",
                            "تم التحقق من المبلغ وتحديث صلاحيات الحساب وإنشاء سجل الفاتورة.",
                            type_="subscription", related_id=paid["subscription_id"], priority="normal",
                            action_text="فتح الاشتراك", action_route="subscription",
                        )
                log_audit(con, session, f"payment.{action}", payment_id, jdump(result))
                return self.send_json({"ok": True, "payment": result})
            if path == "/api/admin/coupons":
                coupon_id = str(data.get("id") or slug("coupon"))
                if data.get("action") == "disable":
                    con.execute(
                        "UPDATE coupons SET active=0,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (coupon_id,),
                    )
                    log_audit(con, session, "coupon.disabled", coupon_id, "")
                    return self.send_json({"ok": True})
                code = re.sub(r"[^A-Z0-9_-]", "", str(data.get("code", "") or "").upper())[:32]
                discount_type = str(data.get("discountType", "fixed") or "fixed")
                if not code or discount_type not in {"fixed", "percent"}:
                    return self.send_json({"error": "invalid_coupon"}, 400)
                value = finite_number(data.get("discountValue", 0), minimum=0, maximum=1_000_000)
                if discount_type == "percent" and value > 100:
                    return self.send_json({"error": "invalid_coupon_value"}, 400)
                applies_to = [plan for plan in data.get("appliesTo", []) if plan in PLAN_IDS]
                con.execute(
                    """INSERT INTO coupons(
                    id,code,name_ar,name_en,discount_type,discount_value,applies_to,
                    starts_at,ends_at,max_uses,active)
                    VALUES(?,?,?,?,?,?,?,?,?,?,1)
                    ON CONFLICT(id) DO UPDATE SET code=excluded.code,name_ar=excluded.name_ar,
                    name_en=excluded.name_en,discount_type=excluded.discount_type,
                    discount_value=excluded.discount_value,applies_to=excluded.applies_to,
                    starts_at=excluded.starts_at,ends_at=excluded.ends_at,
                    max_uses=excluded.max_uses,active=1,updated_at=CURRENT_TIMESTAMP""",
                    (
                        coupon_id, code, str(data.get("nameAr", "") or "")[:120],
                        str(data.get("nameEn", "") or "")[:120], discount_type, value,
                        jdump(applies_to), str(data.get("startsAt", "") or "")[:40],
                        str(data.get("endsAt", "") or "")[:40],
                        max(0, int(data.get("maxUses", 0) or 0)),
                    ),
                )
                log_audit(con, session, "coupon.upserted", coupon_id, code)
                return self.send_json({"ok": True, "id": coupon_id})
            if path == "/api/admin/campaigns":
                service = RewardCampaignService(con)
                action = safe_text(data.get("action"), 40)
                if action == "review_eligibility":
                    eligibility = service.review_eligibility(
                        safe_text(data.get("eligibilityId"), 180),
                        status=safe_text(data.get("status"), 40),
                        reviewed_by=session.get("id", ""),
                        note=safe_text(data.get("note"), 500),
                    )
                    log_audit(
                        con,
                        session,
                        "campaign.eligibility_reviewed",
                        eligibility["id"],
                        eligibility["status"],
                    )
                    return self.send_json(
                        {"ok": True, "eligibility": eligibility}
                    )
                campaign_id = safe_text(data.get("id"), 120) or slug("campaign")
                is_reward = (
                    data.get("kind") == "reward"
                    or any(
                        key in data
                        for key in (
                            "audience",
                            "rewardType",
                            "metric",
                            "target",
                            "descriptionAr",
                        )
                    )
                )
                if action == "set_status":
                    campaign = service.update_status(
                        campaign_id, safe_text(data.get("status"), 40)
                    )
                    log_audit(
                        con,
                        session,
                        "campaign.status_updated",
                        campaign_id,
                        campaign["status"],
                    )
                    return self.send_json({"ok": True, "campaign": campaign})
                if is_reward:
                    campaign = service.save(campaign_id, data)
                    log_audit(
                        con,
                        session,
                        "campaign.upserted",
                        campaign_id,
                        campaign["status"],
                    )
                    return self.send_json({"ok": True, "campaign": campaign})
                status = str(data.get("status", "draft") or "draft")
                if status not in {
                    "draft", "scheduled", "active", "paused", "completed", "cancelled"
                }:
                    return self.send_json(
                        {"error": "invalid_campaign_status"}, 400
                    )
                con.execute(
                    """INSERT INTO campaigns(
                    id,name_ar,name_en,kind,starts_at,ends_at,budget,status,rules)
                    VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                    name_ar=excluded.name_ar,name_en=excluded.name_en,kind=excluded.kind,
                    starts_at=excluded.starts_at,ends_at=excluded.ends_at,budget=excluded.budget,
                    status=excluded.status,rules=excluded.rules,updated_at=CURRENT_TIMESTAMP""",
                    (
                        campaign_id, str(data.get("nameAr", "") or "")[:160],
                        str(data.get("nameEn", "") or "")[:160],
                        str(data.get("kind", "subscription") or "subscription")[:40],
                        str(data.get("startsAt", "") or "")[:40],
                        str(data.get("endsAt", "") or "")[:40],
                        finite_number(
                            data.get("budget", 0),
                            minimum=0,
                            maximum=1_000_000_000,
                        ),
                        status,
                        jdump(
                            data.get("rules", {})
                            if isinstance(data.get("rules"), dict)
                            else {}
                        ),
                    ),
                )
                log_audit(con, session, "campaign.upserted", campaign_id, status)
                return self.send_json({"ok": True, "id": campaign_id})
            if path == "/api/admin/team":
                member_id = safe_text(data.get("id"), 100) or slug("member")
                if data.get("action") == "delete":
                    result = con.execute("UPDATE provider_team_members SET active=0 WHERE id=?", (member_id,))
                    if result.rowcount != 1:
                        return self.send_json({"error": "team_member_not_found"}, 404)
                    log_audit(con, session, "team.disabled", member_id, "")
                    return self.send_json({"ok": True})
                provider_id = safe_text(data.get("providerId"), 120)
                if not con.execute("SELECT id FROM providers WHERE id=?", (provider_id,)).fetchone():
                    return self.send_json({"error": "provider_not_found"}, 404)
                role = str(data.get("role", "provider_staff") or "provider_staff")
                if role not in {"provider_owner", "provider_manager", "provider_staff"}:
                    return self.send_json({"error": "invalid_provider_role"}, 400)
                name = safe_text(data.get("name"), 120)
                phone = normalize_phone(data.get("phone", ""))
                if not name or len(phone) < 11:
                    return self.send_json({"error": "name_and_valid_phone_required"}, 400)
                existing = con.execute(
                    "SELECT pin_hash,provider_id FROM provider_team_members WHERE id=?", (member_id,)
                ).fetchone()
                if existing and existing["provider_id"] != provider_id:
                    return self.send_json({"error": "team_member_provider_mismatch"}, 409)
                pin_hash = existing["pin_hash"] if existing else ""
                if data.get("pin"):
                    pin = str(data["pin"])
                    if not re.fullmatch(r"\d{4,10}", pin):
                        return self.send_json({"error": "invalid_pin"}, 400)
                    pin_hash = hash_pin(pin)
                if not pin_hash:
                    return self.send_json({"error": "pin_required"}, 400)
                selected_permissions = [
                    item for item in data.get("permissions", [])
                    if item in PROVIDER_ROLE_PERMISSIONS[role]
                ] if isinstance(data.get("permissions"), list) else []
                active = strict_bool(data.get("active"), True)
                con.execute(
                    """INSERT INTO provider_team_members(
                    id,provider_id,name,phone,role,pin_hash,permissions,active)
                    VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                    provider_id=excluded.provider_id,name=excluded.name,phone=excluded.phone,
                    role=excluded.role,pin_hash=excluded.pin_hash,permissions=excluded.permissions,
                    active=excluded.active,updated_at=CURRENT_TIMESTAMP""",
                    (
                        member_id, provider_id, name, phone, role, pin_hash,
                        jdump(selected_permissions), int(active),
                    ),
                )
                log_audit(con, session, "team.upserted", member_id, role)
                return self.send_json({"ok": True, "id": member_id})
            if path == "/api/admin/branches":
                branch_id = safe_text(data.get("id"), 100) or slug("branch")
                if data.get("action") == "delete":
                    result = con.execute("UPDATE provider_branches SET active=0 WHERE id=?", (branch_id,))
                    if result.rowcount != 1:
                        return self.send_json({"error": "branch_not_found"}, 404)
                    log_audit(con, session, "branch.disabled", branch_id, "")
                    return self.send_json({"ok": True})
                provider_id = safe_text(data.get("providerId"), 120)
                if not con.execute("SELECT id FROM providers WHERE id=?", (provider_id,)).fetchone():
                    return self.send_json({"error": "provider_not_found"}, 404)
                existing = con.execute(
                    "SELECT provider_id FROM provider_branches WHERE id=?", (branch_id,)
                ).fetchone()
                if existing and existing["provider_id"] != provider_id:
                    return self.send_json({"error": "branch_provider_mismatch"}, 409)
                name = safe_text(data.get("name"), 120)
                if not name:
                    return self.send_json({"error": "branch_name_required"}, 400)
                location = normalized_location(data.get("location"))
                active = strict_bool(data.get("active"), True)
                con.execute(
                    """INSERT INTO provider_branches(
                    id,provider_id,name,gov,wilayah,address,latitude,longitude,phone,active)
                    VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                    provider_id=excluded.provider_id,name=excluded.name,gov=excluded.gov,
                    wilayah=excluded.wilayah,address=excluded.address,latitude=excluded.latitude,
                    longitude=excluded.longitude,phone=excluded.phone,active=excluded.active,
                    updated_at=CURRENT_TIMESTAMP""",
                    (
                        branch_id, provider_id, name,
                        str(data.get("gov", "") or "")[:80], str(data.get("wilayah", "") or "")[:80],
                        str(data.get("address", "") or "")[:240], location.get("lat"), location.get("lng"),
                        normalize_phone(data.get("phone", "")), int(active),
                    ),
                )
                log_audit(con, session, "branch.upserted", branch_id, provider_id)
                return self.send_json({"ok": True, "id": branch_id})
            if path == "/api/admin/contact-consents":
                if data.get("action") != "revoke":
                    return self.send_json({"error": "invalid_consent_action"}, 400)
                request_id = str(data.get("requestId", "") or "")
                provider_id = str(data.get("providerId", "") or "")
                channel = str(data.get("channel", "") or "")
                row = con.execute(
                    "SELECT user_id FROM customer_requests WHERE id=?", (request_id,)
                ).fetchone()
                if not row:
                    return self.send_json({"error": "request_not_found"}, 404)
                try:
                    consent = ContactConsentService(con).set_channel(
                        request_id, row["user_id"], provider_id, channel, False
                    )
                except DomainError as err:
                    return self.send_domain_error(err)
                log_audit(con, session, "consent.revoked", request_id, f"{provider_id}:{channel}")
                return self.send_json({"ok": True, "consent": consent})
            if path == "/api/admin/settings":
                current_settings_row = con.execute(
                    "SELECT value FROM settings WHERE key='platform'"
                ).fetchone()
                settings_data = jload(current_settings_row["value"], {}) if current_settings_row else {}
                settings_data.update(dict(data))
                new_admin_code = str(settings_data.pop("adminCode", "") or "")
                settings_data.pop("passwords", None)
                settings_data.pop("otpCode", None)
                if new_admin_code:
                    if not re.fullmatch(r"\d{4,10}", new_admin_code):
                        return self.send_json({"error": "invalid_admin_code"}, 400)
                    con.execute(
                        "UPDATE admin_users SET code_hash=? WHERE id=?",
                        (hash_pin(new_admin_code), session["id"]),
                    )
                settings_data["nameAr"] = "خدماتي"
                settings_data["nameEn"] = "Khadamati App"
                settings_data["supportEmail"] = SUPPORT_EMAIL
                settings_data["policyVersion"] = POLICY_VERSION
                settings_data["currency"] = OMR
                try:
                    loyalty_target = int(
                        settings_data.get("loyaltyTargetRequests", 8)
                    )
                except (TypeError, ValueError):
                    return self.send_json(
                        {"error": "invalid_loyalty_target"}, 400
                    )
                if loyalty_target < 1 or loyalty_target > 100_000:
                    return self.send_json(
                        {"error": "invalid_loyalty_target"}, 400
                    )
                settings_data["loyaltyTargetRequests"] = loyalty_target
                settings_data["loyaltyCycleMode"] = (
                    "repeat"
                    if settings_data.get("loyaltyCycleMode") == "repeat"
                    else "cap"
                )
                settings_data.pop("serviceAreas", None)
                con.execute("UPDATE settings SET value=? WHERE key='platform'", (jdump(settings_data),))
                log_audit(con, session, "settings.updated", "platform", "")
                return self.send_json({"ok": True})
            if path == "/api/admin/ads":
                ad_id = str(data.get("id") or slug("ad"))
                existing = con.execute("SELECT * FROM advertisements WHERE id=?", (ad_id,)).fetchone()
                if data.get("action") == "delete":
                    if not existing:
                        return self.send_json({"error": "advertisement_not_found"}, 404)
                    con.execute(
                        "UPDATE advertisements SET active=0,deleted_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (ad_id,),
                    )
                    create_notification(
                        con, "admin", "", "تمت أرشفة إعلان",
                        existing["advertiser"] or ad_id, type_="advertisement", related_id=ad_id,
                    )
                    return self.send_json({"ok": True, "archived": True})
                image_path = existing["image_path"] if existing else ""
                if data.get("imageData"):
                    image_path = save_upload_data(ad_id, data["imageData"], "banner", IMAGE_MIMES, 4_000_000)
                if not image_path:
                    return self.send_json({"error": "advertisement_image_required"}, 400)
                con.execute(
                    """INSERT INTO advertisements(
                    id,image_path,advertiser,phone,amount,title,body,starts_at,ends_at,active,deleted_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET image_path=excluded.image_path,
                    advertiser=excluded.advertiser,phone=excluded.phone,amount=excluded.amount,
                    title=excluded.title,body=excluded.body,starts_at=excluded.starts_at,
                    ends_at=excluded.ends_at,active=excluded.active,deleted_at=excluded.deleted_at,
                    updated_at=CURRENT_TIMESTAMP""",
                    (
                        ad_id, image_path, str(data.get("advertiser", "") or "")[:120],
                        normalize_phone(data.get("phone", "")),
                        finite_number(data.get("amount", 0), minimum=0, maximum=1_000_000_000),
                        str(data.get("title", "") or "")[:160],
                        str(data.get("body", "") or "")[:500],
                        str(data.get("startsAt", "") or "")[:40],
                        str(data.get("endsAt", "") or "")[:40],
                        int(bool(data.get("active", True))), "",
                    ),
                )
                create_notification(
                    con, "admin", "", "تم حفظ إعلان",
                    str(data.get("advertiser", "") or ad_id), type_="advertisement",
                    related_id=ad_id, action_text="فتح الإعلان",
                    action_route=f"admin:advertisement:{ad_id}",
                )
                saved = con.execute("SELECT * FROM advertisements WHERE id=?", (ad_id,)).fetchone()
                return self.send_json({"ok": True, "advertisement": row_advertisement(saved)})
            if path == "/api/admin/users":
                role = data.get("role", "support")
                if role not in {"super_admin", "admin", "manager", "support", "finance"}:
                    return self.send_json({"error": "invalid_admin_role"}, 400)
                perms = permissions_for(role, data.get("permissions"))
                user_id = safe_text(data.get("id"), 100) or slug("admin")
                name = safe_text(data.get("name", "مشرف"), 120)
                if not name:
                    return self.send_json({"error": "admin_name_required"}, 400)
                existing = con.execute("SELECT code_hash FROM admin_users WHERE id=?", (user_id,)).fetchone()
                code_hash = existing["code_hash"] if existing else ""
                if data.get("code"):
                    if not re.fullmatch(r"\d{4,10}", str(data["code"])):
                        return self.send_json({"error": "invalid_admin_code"}, 400)
                    code_hash = hash_pin(data["code"])
                if not code_hash:
                    return self.send_json({"error": "code_required"}, 400)
                con.execute(
                    """INSERT INTO admin_users(id,name,code_hash,role,permissions,active) VALUES(?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET name=excluded.name,code_hash=excluded.code_hash,
                    role=excluded.role,permissions=excluded.permissions,active=excluded.active""",
                    (user_id, name, code_hash, role, jdump(perms), int(strict_bool(data.get("active"), True))),
                )
                log_audit(con, session, "admin_user.upserted", user_id, role)
                return self.send_json({"ok": True})
            if path == "/api/admin/test-whatsapp":
                return self.send_json(send_whatsapp(data.get("to"), data.get("message", "اختبار من منصة خدماتي")))
        self.send_json({"error": "not_found"}, 404)


if __name__ == "__main__":
    init_db()
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8080"))
    try:
        display_host = "127.0.0.1" if ipaddress.ip_address(host).is_unspecified else host
    except ValueError:
        display_host = host
    log_event(
        "service.started",
        host=display_host,
        port=port,
        database="sqlite",
    )
    ThreadingHTTPServer((host, port), Handler).serve_forever()
