"""Run a low-load API benchmark against an isolated local Khadamati server."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import math
import os
import platform
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
REQUESTS = max(20, int(os.environ.get("KHADAMATI_PERF_REQUESTS", "120")))
CONCURRENCY = max(1, min(20, int(os.environ.get("KHADAMATI_PERF_CONCURRENCY", "5"))))
P95_LIMIT_MS = max(100, int(os.environ.get("KHADAMATI_PERF_P95_LIMIT_MS", "2500")))


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_until_ready(base_url: str) -> None:
    for _ in range(150):
        try:
            with urllib.request.urlopen(f"{base_url}/readyz", timeout=2) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError):
            pass
        time.sleep(0.1)
    raise RuntimeError("isolated_server_not_ready")


def request_once(base_url: str, index: int) -> tuple[float, int]:
    path = "/healthz" if index % 4 == 0 else "/api/bootstrap"
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(f"{base_url}{path}", timeout=10) as response:
            response.read()
            status = int(response.status)
    except urllib.error.HTTPError as error:
        error.read()
        status = int(error.code)
    except (urllib.error.URLError, TimeoutError):
        status = 0
    return (time.perf_counter() - started) * 1000, status


def read_linux_rss_kib(pid: int) -> int | None:
    status_path = Path(f"/proc/{pid}/status")
    if not status_path.is_file():
        return None
    try:
        for line in status_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


def percentile(values: list[float], ratio: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * ratio) - 1))
    return ordered[index]


def main() -> int:
    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="khadamati-api-performance-") as temp:
        env = os.environ.copy()
        env.update(
            {
                "HOST": "127.0.0.1",
                "PORT": str(port),
                "KHADAMATI_ENV": "test",
                "KHADAMATI_ADMIN_CODE": "Perf-Admin-6200",
                "KHADAMATI_DB_PATH": str(Path(temp) / "performance.sqlite3"),
                "KHADAMATI_UPLOAD_DIR": str(Path(temp) / "uploads"),
                "KHADAMATI_BACKUP_DIR": str(Path(temp) / "backups"),
                "KHADAMATI_MEDIA_SIGNING_KEY": "performance-media-key-6200",
                "KHADAMATI_SEED_DEMO_DATA": "false",
            }
        )
        process = subprocess.Popen(
            [sys.executable, "server.py"],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        stop_monitor = threading.Event()
        max_rss_kib = 0

        def monitor_memory() -> None:
            nonlocal max_rss_kib
            while not stop_monitor.wait(0.05):
                value = read_linux_rss_kib(process.pid)
                if value is not None:
                    max_rss_kib = max(max_rss_kib, value)

        monitor = threading.Thread(target=monitor_memory, daemon=True)
        try:
            wait_until_ready(base_url)
            for index in range(8):
                request_once(base_url, index)
            monitor.start()
            started = time.perf_counter()
            results = []
            with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
                futures = [
                    pool.submit(request_once, base_url, index)
                    for index in range(REQUESTS)
                ]
                for future in as_completed(futures):
                    results.append(future.result())
            duration = time.perf_counter() - started
        finally:
            stop_monitor.set()
            if monitor.is_alive():
                monitor.join(timeout=1)
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    latencies = [latency for latency, _ in results]
    errors = sum(1 for _, status in results if status != 200)
    summary = {
        "environment": {
            "os": platform.platform(),
            "python": platform.python_version(),
            "cpuCount": os.cpu_count(),
            "scope": "isolated_local_low_load",
        },
        "requests": REQUESTS,
        "concurrency": CONCURRENCY,
        "durationSeconds": round(duration, 3),
        "requestsPerSecond": round(REQUESTS / duration, 2),
        "averageMs": round(sum(latencies) / len(latencies), 2),
        "p95Ms": round(percentile(latencies, 0.95), 2),
        "maxMs": round(max(latencies), 2),
        "errors": errors,
        "errorRatePercent": round(errors / REQUESTS * 100, 3),
        "maxRssMiB": round(max_rss_kib / 1024, 2) if max_rss_kib else None,
        "p95LimitMs": P95_LIMIT_MS,
        "withinLocalTarget": errors == 0 and percentile(latencies, 0.95) <= P95_LIMIT_MS,
        "productionCapacityClaimed": False,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["withinLocalTarget"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
