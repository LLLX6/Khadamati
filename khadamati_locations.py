"""Governate and wilayah catalog plus privacy-safe reverse area lookup."""

from __future__ import annotations

import json
import math
import re
import sqlite3
from typing import Any

from khadamati_domain import DomainError


OMAN_AREAS = [
    (
        "muscat",
        "مسقط",
        "Muscat",
        [
            ("muscat", "مسقط", "Muscat", 23.5880, 58.3829),
            ("muttrah", "مطرح", "Muttrah", 23.6165, 58.5659),
            ("bawshar", "بوشر", "Bawshar", 23.5653, 58.4202),
            ("seeb", "السيب", "Seeb", 23.6703, 58.1891),
            ("al-amerat", "العامرات", "Al Amerat", 23.4834, 58.4995),
            ("quriyat", "قريات", "Quriyat", 23.2620, 58.9160),
        ],
    ),
    (
        "al-dakhiliyah",
        "الداخلية",
        "Al Dakhiliyah",
        [
            ("nizwa", "نزوى", "Nizwa", 22.9333, 57.5333),
            ("bahla", "بهلاء", "Bahla", 22.9670, 57.2980),
            ("manah", "منح", "Manah", 22.7980, 57.5760),
            ("al-hamra", "الحمراء", "Al Hamra", 23.1159, 57.2850),
            ("adam", "أدم", "Adam", 22.3790, 57.5270),
            ("izki", "إزكي", "Izki", 22.9330, 57.7660),
            ("samail", "سمائل", "Samail", 23.3030, 57.9780),
            ("bidbid", "بدبد", "Bidbid", 23.4070, 58.1280),
        ],
    ),
    (
        "north-al-batinah",
        "شمال الباطنة",
        "North Al Batinah",
        [
            ("sohar", "صحار", "Sohar", 24.3470, 56.7070),
            ("shinas", "شناص", "Shinas", 24.7420, 56.4650),
            ("liwa", "لوى", "Liwa", 24.5310, 56.5650),
            ("saham", "صحم", "Saham", 24.1720, 56.8890),
            ("al-khaburah", "الخابورة", "Al Khaburah", 23.9710, 57.0930),
            ("suwaiq", "السويق", "Suwaiq", 23.8490, 57.4380),
        ],
    ),
    (
        "south-al-batinah",
        "جنوب الباطنة",
        "South Al Batinah",
        [
            ("rustaq", "الرستاق", "Rustaq", 23.3900, 57.4240),
            ("al-awabi", "العوابي", "Al Awabi", 23.3020, 57.5330),
            ("nakhal", "نخل", "Nakhal", 23.3950, 57.8290),
            (
                "wadi-al-maawil",
                "وادي المعاول",
                "Wadi Al Maawil",
                23.5320,
                57.8200,
            ),
            ("barka", "بركاء", "Barka", 23.7070, 57.8890),
            ("al-musannah", "المصنعة", "Al Musannah", 23.7890, 57.6340),
        ],
    ),
    (
        "dhofar",
        "ظفار",
        "Dhofar",
        [
            ("salalah", "صلالة", "Salalah", 17.0190, 54.0890),
            ("taqah", "طاقة", "Taqah", 17.0370, 54.4000),
            ("mirbat", "مرباط", "Mirbat", 16.9920, 54.6910),
            ("sadah", "سدح", "Sadah", 17.0370, 55.0710),
            ("thumrait", "ثمريت", "Thumrait", 17.6660, 54.0240),
            ("rakhyut", "رخيوت", "Rakhyut", 16.7460, 53.4170),
            ("dalkut", "ضلكوت", "Dalkut", 16.7060, 53.2500),
            ("muqshin", "مقشن", "Muqshin", 19.5530, 54.8840),
            ("al-mazyunah", "المزيونة", "Al Mazyunah", 17.8440, 52.6600),
        ],
    ),
    (
        "north-ash-sharqiyah",
        "شمال الشرقية",
        "North Ash Sharqiyah",
        [
            ("ibra", "إبراء", "Ibra", 22.6900, 58.5500),
            ("al-mudhaibi", "المضيبي", "Al Mudhaibi", 22.5720, 58.1260),
            ("bidiyah", "بدية", "Bidiyah", 22.4490, 58.8020),
            ("al-qabil", "القابل", "Al Qabil", 22.5710, 58.6900),
            (
                "wadi-bani-khalid",
                "وادي بني خالد",
                "Wadi Bani Khalid",
                22.6060,
                59.0860,
            ),
            (
                "dima-wa-al-tayeen",
                "دماء والطائيين",
                "Dima Wa Al Tayeen",
                23.0500,
                58.3780,
            ),
        ],
    ),
    (
        "south-ash-sharqiyah",
        "جنوب الشرقية",
        "South Ash Sharqiyah",
        [
            ("sur", "صور", "Sur", 22.5660, 59.5280),
            (
                "jalan-bani-bu-ali",
                "جعلان بني بو علي",
                "Jalan Bani Bu Ali",
                22.0110,
                59.3680,
            ),
            (
                "jalan-bani-bu-hassan",
                "جعلان بني بو حسن",
                "Jalan Bani Bu Hassan",
                22.0450,
                59.3100,
            ),
            (
                "al-kamil-wal-wafi",
                "الكامل والوافي",
                "Al Kamil Wal Wafi",
                22.2610,
                59.1940,
            ),
            ("masirah", "مصيرة", "Masirah", 20.6690, 58.8910),
        ],
    ),
    (
        "ad-dhahirah",
        "الظاهرة",
        "Ad Dhahirah",
        [
            ("ibri", "عبري", "Ibri", 23.2250, 56.5150),
            ("yanqul", "ينقل", "Yanqul", 23.5860, 56.5470),
            ("dhank", "ضنك", "Dhank", 23.4960, 56.2580),
        ],
    ),
    (
        "al-buraimi",
        "البريمي",
        "Al Buraimi",
        [
            ("al-buraimi", "البريمي", "Al Buraimi", 24.2500, 55.7930),
            ("mahdah", "محضة", "Mahdah", 24.4010, 55.9690),
            ("al-sunaynah", "السنينة", "Al Sunaynah", 23.8430, 55.8470),
        ],
    ),
    (
        "musandam",
        "مسندم",
        "Musandam",
        [
            ("khasab", "خصب", "Khasab", 26.1980, 56.2460),
            ("bukha", "بخاء", "Bukha", 26.1430, 56.1540),
            ("dibba", "دبا", "Dibba", 25.6150, 56.2470),
            ("madha", "مدحاء", "Madha", 25.2840, 56.3050),
        ],
    ),
    (
        "al-wusta",
        "الوسطى",
        "Al Wusta",
        [
            ("haima", "هيما", "Haima", 19.9590, 56.2900),
            ("mahout", "محوت", "Mahout", 20.7640, 58.0120),
            ("duqm", "الدقم", "Duqm", 19.6650, 57.7040),
            ("al-jazir", "الجازر", "Al Jazir", 18.9380, 56.6670),
        ],
    ),
]


def install_location_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS governorates(
          id TEXT PRIMARY KEY,
          name_ar TEXT NOT NULL,
          name_en TEXT NOT NULL,
          sort_order INTEGER NOT NULL DEFAULT 0,
          active INTEGER NOT NULL DEFAULT 1,
          deleted_at TEXT DEFAULT '',
          created_at TEXT DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS wilayats(
          id TEXT PRIMARY KEY,
          governorate_id TEXT NOT NULL,
          name_ar TEXT NOT NULL,
          name_en TEXT NOT NULL,
          sort_order INTEGER NOT NULL DEFAULT 0,
          active INTEGER NOT NULL DEFAULT 1,
          latitude REAL,
          longitude REAL,
          deleted_at TEXT DEFAULT '',
          created_at TEXT DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(governorate_id,name_ar),
          UNIQUE(governorate_id,name_en),
          FOREIGN KEY(governorate_id) REFERENCES governorates(id)
        );
        CREATE INDEX IF NOT EXISTS idx_governorates_active
          ON governorates(active,sort_order);
        CREATE INDEX IF NOT EXISTS idx_wilayats_governorate
          ON wilayats(governorate_id,active,sort_order);
        """
    )
    for gov_order, (gov_id, ar, en, wilayats) in enumerate(OMAN_AREAS, 1):
        con.execute(
            """
            INSERT OR IGNORE INTO governorates(
              id,name_ar,name_en,sort_order,active
            ) VALUES(?,?,?,?,1)
            """,
            (gov_id, ar, en, gov_order),
        )
        for wilayah_order, (wid, war, wen, lat, lng) in enumerate(wilayats, 1):
            con.execute(
                """
                INSERT OR IGNORE INTO wilayats(
                  id,governorate_id,name_ar,name_en,sort_order,active,
                  latitude,longitude
                ) VALUES(?,?,?,?,?,1,?,?)
                """,
                (f"{gov_id}-{wid}", gov_id, war, wen, wilayah_order, lat, lng),
            )


def location_snapshot(
    con: sqlite3.Connection, *, include_inactive: bool = False
) -> list[dict[str, Any]]:
    governorate_where = (
        "COALESCE(deleted_at,'')=''"
        if include_inactive
        else "active=1 AND COALESCE(deleted_at,'')=''"
    )
    wilayah_where = (
        "COALESCE(deleted_at,'')=''"
        if include_inactive
        else "active=1 AND COALESCE(deleted_at,'')=''"
    )
    result: list[dict[str, Any]] = []
    for gov in con.execute(
        f"""
        SELECT * FROM governorates WHERE {governorate_where}
        ORDER BY sort_order,name_ar
        """  # nosec B608 - fixed internal clauses only
    ):
        wilayahs = [
            {
                "id": row["id"],
                "ar": row["name_ar"],
                "en": row["name_en"],
                "sortOrder": int(row["sort_order"] or 0),
                "active": bool(row["active"]),
                "lat": row["latitude"],
                "lng": row["longitude"],
            }
            for row in con.execute(
                f"""
                SELECT * FROM wilayats
                WHERE governorate_id=? AND {wilayah_where}
                ORDER BY sort_order,name_ar
                """,  # nosec B608 - fixed internal clauses only
                (gov["id"],),
            )
        ]
        result.append(
            {
                "id": gov["id"],
                "ar": gov["name_ar"],
                "en": gov["name_en"],
                "sortOrder": int(gov["sort_order"] or 0),
                "active": bool(gov["active"]),
                "w": wilayahs,
            }
        )
    return result


def resolve_area(
    con: sqlite3.Connection,
    latitude: float,
    longitude: float,
    *,
    max_distance_km: float = 180,
) -> dict[str, Any] | None:
    try:
        lat = float(latitude)
        lng = float(longitude)
    except (TypeError, ValueError) as error:
        raise DomainError("invalid_location", 400) from error
    if (
        not math.isfinite(lat)
        or not math.isfinite(lng)
        or abs(lat) > 90
        or abs(lng) > 180
    ):
        raise DomainError("invalid_location", 400)
    best: tuple[float, sqlite3.Row] | None = None
    for row in con.execute(
        """
        SELECT w.*,g.name_ar governorate_ar,g.name_en governorate_en
        FROM wilayats w JOIN governorates g ON g.id=w.governorate_id
        WHERE w.active=1 AND g.active=1
          AND COALESCE(w.deleted_at,'')='' AND COALESCE(g.deleted_at,'')=''
          AND w.latitude IS NOT NULL AND w.longitude IS NOT NULL
        """
    ):
        distance = _haversine(lat, lng, row["latitude"], row["longitude"])
        if best is None or distance < best[0]:
            best = (distance, row)
    if best is None or best[0] > max_distance_km:
        return None
    distance, row = best
    return {
        "governorateId": row["governorate_id"],
        "governorateAr": row["governorate_ar"],
        "governorateEn": row["governorate_en"],
        "wilayahId": row["id"],
        "wilayahAr": row["name_ar"],
        "wilayahEn": row["name_en"],
        "distanceKm": round(distance, 2),
        "approximate": True,
    }


class LocationCatalogService:
    def __init__(self, con: sqlite3.Connection):
        self.con = con

    def apply(self, payload: dict[str, Any]) -> dict[str, Any]:
        action = _clean(payload.get("action"), 40)
        if action == "save_governorate":
            self._save_governorate(payload)
        elif action == "set_governorate_active":
            self._set_active("governorates", payload)
        elif action == "delete_governorate":
            self._delete_governorate(payload)
        elif action == "save_wilayat":
            self._save_wilayat(payload)
        elif action == "set_wilayat_active":
            self._set_active("wilayats", payload)
        elif action == "delete_wilayat":
            self._delete_wilayat(payload)
        else:
            raise DomainError("invalid_location_action", 400)
        return {"areas": location_snapshot(self.con, include_inactive=True)}

    def _save_governorate(self, payload: dict[str, Any]) -> None:
        gov_id = _identifier(payload.get("id") or payload.get("governorateId"))
        name_ar = _clean(payload.get("ar"), 100)
        name_en = _clean(payload.get("en"), 100)
        if not name_ar or not name_en:
            raise DomainError("location_name_required", 400)
        sort_order = _integer(payload.get("sortOrder"), 0, 10_000)
        duplicate = self.con.execute(
            """
            SELECT id FROM governorates
            WHERE id!=? AND COALESCE(deleted_at,'')=''
              AND (name_ar=? COLLATE NOCASE OR name_en=? COLLATE NOCASE)
            """,
            (gov_id, name_ar, name_en),
        ).fetchone()
        if duplicate:
            raise DomainError("governorate_already_exists", 409)
        self.con.execute(
            """
            INSERT INTO governorates(
              id,name_ar,name_en,sort_order,active,deleted_at
            ) VALUES(?,?,?,?,1,'')
            ON CONFLICT(id) DO UPDATE SET
              name_ar=excluded.name_ar,name_en=excluded.name_en,
              sort_order=excluded.sort_order,deleted_at='',
              updated_at=CURRENT_TIMESTAMP
            """,
            (gov_id, name_ar, name_en, sort_order),
        )

    def _save_wilayat(self, payload: dict[str, Any]) -> None:
        wilayah_id = _identifier(payload.get("id") or payload.get("wilayahId"))
        gov_id = _identifier(payload.get("governorateId"))
        if not self.con.execute(
            """
            SELECT id FROM governorates WHERE id=?
              AND COALESCE(deleted_at,'')=''
            """,
            (gov_id,),
        ).fetchone():
            raise DomainError("governorate_not_found", 404)
        name_ar = _clean(payload.get("ar"), 100)
        name_en = _clean(payload.get("en"), 100)
        if not name_ar or not name_en:
            raise DomainError("location_name_required", 400)
        duplicate = self.con.execute(
            """
            SELECT id FROM wilayats
            WHERE id!=? AND governorate_id=? AND COALESCE(deleted_at,'')=''
              AND (name_ar=? COLLATE NOCASE OR name_en=? COLLATE NOCASE)
            """,
            (wilayah_id, gov_id, name_ar, name_en),
        ).fetchone()
        if duplicate:
            raise DomainError("wilayat_already_exists", 409)
        latitude = _optional_coordinate(payload.get("lat"), 90)
        longitude = _optional_coordinate(payload.get("lng"), 180)
        self.con.execute(
            """
            INSERT INTO wilayats(
              id,governorate_id,name_ar,name_en,sort_order,active,
              latitude,longitude,deleted_at
            ) VALUES(?,?,?,?,?,1,?,?,'')
            ON CONFLICT(id) DO UPDATE SET
              governorate_id=excluded.governorate_id,
              name_ar=excluded.name_ar,name_en=excluded.name_en,
              sort_order=excluded.sort_order,latitude=excluded.latitude,
              longitude=excluded.longitude,deleted_at='',
              updated_at=CURRENT_TIMESTAMP
            """,
            (
                wilayah_id,
                gov_id,
                name_ar,
                name_en,
                _integer(payload.get("sortOrder"), 0, 10_000),
                latitude,
                longitude,
            ),
        )

    def _set_active(
        self, table: str, payload: dict[str, Any]
    ) -> None:
        item_id = _identifier(payload.get("id"))
        active = payload.get("active")
        if active not in {True, False, 0, 1}:
            raise DomainError("invalid_boolean", 400)
        result = self.con.execute(
            f"""
            UPDATE {table} SET active=?,updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND COALESCE(deleted_at,'')=''
            """,  # nosec B608 - table is selected from a fixed internal set
            (int(bool(active)), item_id),
        )
        if result.rowcount != 1:
            raise DomainError("location_not_found", 404)
        if table == "governorates" and not active:
            self.con.execute(
                """
                UPDATE wilayats SET active=0,updated_at=CURRENT_TIMESTAMP
                WHERE governorate_id=? AND COALESCE(deleted_at,'')=''
                """,
                (item_id,),
            )

    def _delete_governorate(self, payload: dict[str, Any]) -> None:
        gov_id = _identifier(payload.get("id"))
        row = self.con.execute(
            "SELECT name_ar FROM governorates WHERE id=?", (gov_id,)
        ).fetchone()
        if not row:
            raise DomainError("governorate_not_found", 404)
        if self._reference_count(governorate=row["name_ar"]):
            raise DomainError("location_in_use", 409)
        self.con.execute(
            """
            UPDATE governorates SET active=0,deleted_at=CURRENT_TIMESTAMP,
              updated_at=CURRENT_TIMESTAMP WHERE id=?
            """,
            (gov_id,),
        )
        self.con.execute(
            """
            UPDATE wilayats SET active=0,deleted_at=CURRENT_TIMESTAMP,
              updated_at=CURRENT_TIMESTAMP WHERE governorate_id=?
            """,
            (gov_id,),
        )

    def _delete_wilayat(self, payload: dict[str, Any]) -> None:
        wilayah_id = _identifier(payload.get("id"))
        row = self.con.execute(
            "SELECT name_ar FROM wilayats WHERE id=?", (wilayah_id,)
        ).fetchone()
        if not row:
            raise DomainError("wilayat_not_found", 404)
        if self._reference_count(wilayah=row["name_ar"]):
            raise DomainError("location_in_use", 409)
        self.con.execute(
            """
            UPDATE wilayats SET active=0,deleted_at=CURRENT_TIMESTAMP,
              updated_at=CURRENT_TIMESTAMP WHERE id=?
            """,
            (wilayah_id,),
        )

    def _reference_count(
        self, *, governorate: str = "", wilayah: str = ""
    ) -> int:
        value = governorate or wilayah
        column = "gov" if governorate else "wilayah"
        count = 0
        for table in (
            "app_users",
            "providers",
            "customer_requests",
            "provider_branches",
        ):
            row = self.con.execute(
                f"SELECT COUNT(*) n FROM {table} WHERE {column}=?",  # nosec B608
                (value,),
            ).fetchone()
            count += int(row["n"] or 0)
        if governorate:
            for row in self.con.execute("SELECT areas FROM providers"):
                try:
                    areas = json.loads(row["areas"] or "[]")
                except (TypeError, json.JSONDecodeError):
                    areas = []
                if value in areas:
                    count += 1
        return count


def _identifier(value: Any) -> str:
    result = _clean(value, 100)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", result):
        raise DomainError("invalid_location_id", 400)
    return result


def _clean(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _integer(value: Any, minimum: int, maximum: int) -> int:
    try:
        result = int(value or 0)
    except (TypeError, ValueError) as error:
        raise DomainError("invalid_sort_order", 400) from error
    if result < minimum or result > maximum:
        raise DomainError("invalid_sort_order", 400)
    return result


def _optional_coordinate(value: Any, maximum: float) -> float | None:
    if value in {"", None}:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise DomainError("invalid_location", 400) from error
    if not math.isfinite(result) or abs(result) > maximum:
        raise DomainError("invalid_location", 400)
    return result


def _haversine(lat_a: float, lng_a: float, lat_b: float, lng_b: float) -> float:
    lat_a_rad = math.radians(lat_a)
    lat_b_rad = math.radians(lat_b)
    d_lat = lat_b_rad - lat_a_rad
    d_lng = math.radians(lng_b - lng_a)
    value = (
        math.sin(d_lat / 2) ** 2
        + math.cos(lat_a_rad) * math.cos(lat_b_rad) * math.sin(d_lng / 2) ** 2
    )
    return 6371.0 * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))
