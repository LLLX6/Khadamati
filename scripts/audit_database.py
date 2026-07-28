"""Read-only SQLite integrity and relationship audit without exposing row data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3


ORPHAN_CHECKS = {
    "subscriptions_without_provider": """
        SELECT COUNT(*) FROM subscriptions s
        LEFT JOIN providers p ON p.id=s.provider_id WHERE p.id IS NULL
    """,
    "payments_without_provider": """
        SELECT COUNT(*) FROM payments x
        LEFT JOIN providers p ON p.id=x.provider_id
        WHERE COALESCE(x.provider_id,'')!='' AND p.id IS NULL
    """,
    "requests_without_user": """
        SELECT COUNT(*) FROM customer_requests r
        LEFT JOIN app_users u ON u.id=r.user_id
        WHERE COALESCE(r.user_id,'')!='' AND u.id IS NULL
    """,
    "dispatches_without_request": """
        SELECT COUNT(*) FROM request_dispatches d
        LEFT JOIN customer_requests r ON r.id=d.request_id WHERE r.id IS NULL
    """,
    "dispatches_without_provider": """
        SELECT COUNT(*) FROM request_dispatches d
        LEFT JOIN providers p ON p.id=d.provider_id WHERE p.id IS NULL
    """,
    "suggestions_without_request": """
        SELECT COUNT(*) FROM request_provider_suggestions s
        LEFT JOIN customer_requests r ON r.id=s.request_id WHERE r.id IS NULL
    """,
    "suggestions_without_provider": """
        SELECT COUNT(*) FROM request_provider_suggestions s
        LEFT JOIN providers p ON p.id=s.provider_id WHERE p.id IS NULL
    """,
    "consents_without_request": """
        SELECT COUNT(*) FROM contact_consents c
        LEFT JOIN customer_requests r ON r.id=c.request_id WHERE r.id IS NULL
    """,
    "consents_without_provider": """
        SELECT COUNT(*) FROM contact_consents c
        LEFT JOIN providers p ON p.id=c.provider_id WHERE p.id IS NULL
    """,
}


def scalar(con: sqlite3.Connection, query: str) -> int:
    return int(con.execute(query).fetchone()[0])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument("--allow-sample-data", action="store_true")
    args = parser.parse_args()
    database = args.database.resolve()
    if not database.is_file():
        parser.error(f"Database not found: {database}")

    con = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        integrity = str(con.execute("PRAGMA integrity_check").fetchone()[0])
        tables = scalar(
            con,
            """SELECT COUNT(*) FROM sqlite_master
            WHERE type='table' AND name NOT LIKE 'sqlite_%'""",
        )
        duplicates = {
            "userPhones": scalar(
                con,
                """SELECT COUNT(*) FROM (
                SELECT phone FROM app_users GROUP BY phone HAVING COUNT(*)>1)""",
            ),
            "providerPhones": scalar(
                con,
                """SELECT COUNT(*) FROM (
                SELECT phone FROM providers WHERE status!='deleted'
                GROUP BY phone HAVING COUNT(*)>1)""",
            ),
        }
        orphans = {name: scalar(con, query) for name, query in ORPHAN_CHECKS.items()}
        sample_rows = scalar(
            con,
            """SELECT COUNT(*) FROM providers
            WHERE id IN ('p1','p2','p3','p4','p5','p6',
                         'p7','p8','p9','p10','p11','p12')""",
        )
    finally:
        con.close()

    errors = []
    if integrity != "ok":
        errors.append("integrity_check_failed")
    if any(duplicates.values()):
        errors.append("duplicate_identity")
    if any(orphans.values()):
        errors.append("orphan_records")
    if sample_rows and not args.allow_sample_data:
        errors.append("sample_provider_rows_present")

    result = {
        "ok": not errors,
        "database": database.name,
        "bytes": database.stat().st_size,
        "integrity": integrity,
        "tables": tables,
        "duplicates": duplicates,
        "orphans": orphans,
        "sampleProviderRows": sample_rows,
        "errors": errors,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
