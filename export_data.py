from pathlib import Path
import json
import sqlite3

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "foran.sqlite3"
OUT_PATH = BASE_DIR / "foran-export.json"


def rows(con, table):
    return [dict(r) for r in con.execute(f"SELECT * FROM {table}")]


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
    }
    OUT_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(OUT_PATH)


if __name__ == "__main__":
    main()
