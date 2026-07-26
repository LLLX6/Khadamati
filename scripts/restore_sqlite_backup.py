"""Restore a Khadamati backup only into new, empty target paths."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sqlite3
import tempfile
import zipfile

from scripts.backup_sqlite import FORMAT, sha256_file


def safe_archive_name(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(name) and not path.is_absolute() and ".." not in path.parts


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def restore_backup(
    archive_path: Path,
    database_target: Path,
    uploads_target: Path | None = None,
) -> dict[str, object]:
    archive_path = archive_path.resolve()
    database_target = database_target.resolve()
    uploads_target = uploads_target.resolve() if uploads_target is not None else None
    if not archive_path.is_file():
        raise FileNotFoundError(archive_path)
    if database_target.exists():
        raise FileExistsError(database_target)
    if uploads_target is not None and uploads_target.exists():
        raise FileExistsError(uploads_target)
    database_target.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        if any(not safe_archive_name(name) for name in names):
            raise ValueError("unsafe_archive_path")
        if "manifest.json" not in names or "database.sqlite3" not in names:
            raise ValueError("backup_required_entry_missing")
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        if manifest.get("format") != FORMAT:
            raise ValueError("unsupported_backup_format")
        entries = manifest.get("entries")
        if not isinstance(entries, dict):
            raise ValueError("backup_manifest_invalid")

        with tempfile.TemporaryDirectory(
            prefix="khadamati-restore-", dir=database_target.parent
        ) as temp:
            stage = Path(temp)
            database_bytes = archive.read("database.sqlite3")
            expected = entries.get("database.sqlite3", {})
            if sha256_bytes(database_bytes) != expected.get("sha256"):
                raise ValueError("database_checksum_mismatch")
            staged_database = stage / "database.sqlite3"
            staged_database.write_bytes(database_bytes)
            check = sqlite3.connect(
                f"file:{staged_database.as_posix()}?mode=ro", uri=True
            )
            try:
                integrity = str(check.execute("PRAGMA integrity_check").fetchone()[0])
            finally:
                check.close()
            if integrity != "ok":
                raise ValueError("restored_database_integrity_failed")

            staged_uploads = stage / "uploads"
            upload_count = 0
            for name in names:
                if not name.startswith("uploads/") or name.endswith("/"):
                    continue
                if uploads_target is None:
                    raise ValueError("uploads_target_required")
                data = archive.read(name)
                expected = entries.get(name, {})
                if sha256_bytes(data) != expected.get("sha256"):
                    raise ValueError("upload_checksum_mismatch")
                relative = PurePosixPath(name).relative_to("uploads")
                target = staged_uploads.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                upload_count += 1

            os.replace(staged_database, database_target)
            if uploads_target is not None and staged_uploads.exists():
                uploads_target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staged_uploads, uploads_target)

    return {
        "database": database_target.name,
        "uploads": upload_count,
        "integrity": integrity,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--database-target", required=True, type=Path)
    parser.add_argument("--uploads-target", type=Path)
    parser.add_argument(
        "--confirm",
        required=True,
        choices=["RESTORE_TO_EMPTY_TARGET"],
        help="Restoration never overwrites an existing database or uploads directory.",
    )
    args = parser.parse_args()
    result = restore_backup(
        args.archive, args.database_target, args.uploads_target
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
