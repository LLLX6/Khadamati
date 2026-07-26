"""Run the production API smoke flow against an isolated temporary server."""

from __future__ import annotations

import os
import runpy
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ADMIN_CODE = "Smoke-Admin-6200"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_until_ready(base_url: str) -> None:
    for _ in range(100):
        try:
            with urllib.request.urlopen(f"{base_url}/api/bootstrap", timeout=2) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError):
            pass
        time.sleep(0.1)
    raise RuntimeError("The isolated Khadamati server did not start.")


def main() -> None:
    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="khadamati-smoke-") as temp:
        env = os.environ.copy()
        env.update(
            {
                "HOST": "127.0.0.1",
                "PORT": str(port),
                "KHADAMATI_ENV": "test",
                "KHADAMATI_ADMIN_CODE": ADMIN_CODE,
                "KHADAMATI_DB_PATH": str(Path(temp) / "smoke.sqlite3"),
                "KHADAMATI_UPLOAD_DIR": str(Path(temp) / "uploads"),
                "KHADAMATI_BACKUP_DIR": str(Path(temp) / "backups"),
                "KHADAMATI_MEDIA_SIGNING_KEY": "smoke-media-signing-key-6200",
            }
        )
        process = subprocess.Popen(
            [sys.executable, "server.py"],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            wait_until_ready(base_url)
            os.environ["KHADAMATI_TEST_URL"] = base_url
            os.environ["KHADAMATI_TEST_ADMIN_CODE"] = ADMIN_CODE
            runpy.run_path(str(ROOT / "tests" / "smoke-api.py"), run_name="__main__")
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    main()
