"""Fail fast on release repository drift and obvious secret/file mistakes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
MIRRORED_FILES = (
    "index.html",
    "service-worker.js",
    "manifest.webmanifest",
    "app-icon-192.png",
    "app-icon-512.png",
)
PROHIBITED_TRACKED = (
    re.compile(r"(^|/)\.env($|\.)", re.IGNORECASE),
    re.compile(r"\.(sqlite3?|db|log)$", re.IGNORECASE),
    re.compile(r"(^|/)(uploads|backups|node_modules)/", re.IGNORECASE),
)
SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}"),
    re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(rb"AKIA[0-9A-Z]{16}"),
)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [
        item.decode("utf-8", errors="strict")
        for item in result.stdout.split(b"\0")
        if item
    ]


def main() -> int:
    errors: list[dict[str, object]] = []
    warnings: list[dict[str, object]] = []
    files = tracked_files()

    for relative in MIRRORED_FILES:
        source = ROOT / relative
        public = ROOT / "public" / relative
        if not source.is_file() or not public.is_file():
            errors.append({"type": "missing_mirror", "file": relative})
        elif digest(source) != digest(public):
            errors.append({"type": "mirror_mismatch", "file": relative})

    for relative in files:
        normalized = relative.replace("\\", "/")
        allowed_placeholder = normalized == ".env.example" or normalized.endswith(
            "/uploads/.gitkeep"
        )
        if not allowed_placeholder and any(
            pattern.search(normalized) for pattern in PROHIBITED_TRACKED
        ):
            errors.append({"type": "prohibited_tracked_file", "file": normalized})
            continue
        path = ROOT / relative
        if not path.is_file() or path.stat().st_size > 5_000_000:
            continue
        raw = path.read_bytes()
        for pattern in SECRET_PATTERNS:
            if pattern.search(raw):
                errors.append({"type": "possible_secret", "file": normalized})
                break

    index_text = (ROOT / "index.html").read_text(encoding="utf-8")
    worker_text = (ROOT / "service-worker.js").read_text(encoding="utf-8")
    app_version = re.search(r"const APP_VERSION\s*=\s*(\d+)", index_text)
    cache_version = re.search(r"khadamati-app-shell-v(\d+)", worker_text)
    if not app_version or not cache_version:
        errors.append({"type": "version_marker_missing"})
    elif app_version.group(1) != cache_version.group(1):
        errors.append(
            {
                "type": "version_mismatch",
                "app": app_version.group(1),
                "cache": cache_version.group(1),
            }
        )

    if (ROOT / "README.md").is_file():
        readme_text = (ROOT / "README.md").read_text(encoding="utf-8")
        expected_heading = f"## الإصدار v{app_version.group(1)}"
        if expected_heading not in readme_text:
            warnings.append(
                {"type": "readme_version_stale", "expected": app_version.group(1)}
            )

    result = {
        "ok": not errors,
        "trackedFiles": len(files),
        "errors": errors,
        "warnings": warnings,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
