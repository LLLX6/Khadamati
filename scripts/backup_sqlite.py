"""Create a consistent SQLite snapshot and optional uploads archive."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import zipfile


FORMAT = "khadamati-backup-v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def upload_files(root: Path | None) -> list[tuple[Path, str]]:
    if root is None or not root.exists():
        return []
    if not root.is_dir():
        raise ValueError("uploads_path_is_not_directory")
    result = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("uploads_symlink_not_allowed")
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            result.append((path, f"uploads/{relative}"))
    return result


def create_backup(
    database: Path,
    output: Path,
    uploads: Path | None = None,
) -> dict[str, object]:
    database = database.resolve()
    output = output.resolve()
    uploads = uploads.resolve() if uploads is not None else None
    if not database.is_file():
        raise FileNotFoundError(database)
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    files = upload_files(uploads)
    with tempfile.TemporaryDirectory(
        prefix="khadamati-backup-", dir=output.parent
    ) as temp:
        temp_root = Path(temp)
        snapshot = temp_root / "database.sqlite3"
        source = sqlite3.connect(
            f"file:{database.as_posix()}?mode=ro", uri=True, timeout=30
        )
        destination = sqlite3.connect(snapshot)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()

        verify = sqlite3.connect(f"file:{snapshot.as_posix()}?mode=ro", uri=True)
        try:
            integrity = str(verify.execute("PRAGMA integrity_check").fetchone()[0])
        finally:
            verify.close()
        if integrity != "ok":
            raise RuntimeError("backup_integrity_check_failed")

        entries = {
            "database.sqlite3": {
                "bytes": snapshot.stat().st_size,
                "sha256": sha256_file(snapshot),
            }
        }
        for path, archive_name in files:
            entries[archive_name] = {
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        manifest = {
            "format": FORMAT,
            "createdAt": datetime.now(UTC).isoformat(),
            "databaseEngine": "sqlite",
            "entries": entries,
        }

        pending = temp_root / "backup.zip"
        with zipfile.ZipFile(
            pending, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as archive:
            archive.write(snapshot, "database.sqlite3")
            for path, archive_name in files:
                archive.write(path, archive_name)
            archive.writestr(
                "manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
            )
        os.replace(pending, output)

    archive_hash = sha256_file(output)
    checksum = output.with_suffix(output.suffix + ".sha256")
    checksum.write_text(f"{archive_hash}  {output.name}\n", encoding="ascii")
    return {
        "archive": output.name,
        "bytes": output.stat().st_size,
        "sha256": archive_hash,
        "uploads": len(files),
        "integrity": integrity,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--uploads", type=Path)
    args = parser.parse_args()
    result = create_backup(args.database, args.output, args.uploads)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
