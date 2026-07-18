from pathlib import Path
import json
import os
import sqlite3

BASE_DIR = Path(__file__).resolve().parent
_legacy_db = BASE_DIR / "foran.sqlite3"
DB_PATH = Path(os.environ.get("KHADAMATI_DB_PATH") or os.environ.get("FORAN_DB_PATH") or (_legacy_db if _legacy_db.exists() else BASE_DIR / "khadamati.sqlite3"))
OUT_PATH = BASE_DIR / "khadamati-export.json"
TABLE_QUERIES = {
    "settings": "SELECT * FROM settings",
    "admin_users": "SELECT * FROM admin_users",
    "categories": "SELECT * FROM categories",
    "services": "SELECT * FROM services",
    "providers": "SELECT * FROM providers",
    "provider_requests": "SELECT * FROM provider_requests",
    "leads": "SELECT * FROM leads",
    "finance": "SELECT * FROM finance",
    "whatsapp_logs": "SELECT * FROM whatsapp_logs",
    "reviews": "SELECT * FROM reviews",
    "complaints": "SELECT * FROM complaints",
    "packages": "SELECT * FROM packages",
    "subscriptions": "SELECT * FROM subscriptions",
    "payments": "SELECT * FROM payments",
    "audit_logs": "SELECT * FROM audit_logs",
}


def rows(con, table):
    query = TABLE_QUERIES.get(table)
    if not query:
        raise ValueError("unsupported_export_table")
    return [dict(r) for r in con.execute(query)]


def main():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    data = {
        "settings": rows(con, "settings"),
        "admin_users": rows(con, "admin_users"),
        "categories": rows(con, "categories"),
        "services": rows(con, "services"),
        "providers": rows(con, "providers"),
        "provider_requests": rows(con, "provider_requests"),
        "leads": rows(con, "leads"),
        "finance": rows(con, "finance"),
        "whatsapp_logs": rows(con, "whatsapp_logs"),
        "reviews": rows(con, "reviews"),
        "complaints": rows(con, "complaints"),
        "packages": rows(con, "packages"),
        "subscriptions": rows(con, "subscriptions"),
        "payments": rows(con, "payments"),
        "audit_logs": rows(con, "audit_logs"),
    }
    OUT_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(OUT_PATH)


if __name__ == "__main__":
    main()
