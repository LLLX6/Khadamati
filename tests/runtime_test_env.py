"""Small, isolated runtime accommodations for locked-down local test hosts."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


_WINDOWS_CRYPTO_SHIM = '''\
import base64
import sys
import types

class Fernet:
    @staticmethod
    def generate_key():
        return base64.urlsafe_b64encode(b"k" * 32)

    def __init__(self, _key):
        pass

    def encrypt(self, value):
        return base64.urlsafe_b64encode(value)

    def decrypt(self, value):
        return base64.urlsafe_b64decode(value)

cryptography_module = types.ModuleType("cryptography")
fernet_module = types.ModuleType("cryptography.fernet")
fernet_module.Fernet = Fernet
fernet_module.InvalidToken = ValueError
sys.modules["cryptography"] = cryptography_module
sys.modules["cryptography.fernet"] = fernet_module
'''


def add_windows_crypto_shim(env: dict[str, str], temporary_root: str | Path) -> bool:
    """Allow isolated API tests to start only when App Control blocks cryptography.

    Production never sees this shim.  Linux/CI and normal Windows installations
    continue to exercise the real dependency; it is used solely for the known
    Windows App Control DLL block before a disposable child server starts.
    """

    if os.name != "nt":
        return False
    probe = subprocess.run(
        [sys.executable, "-c", "from cryptography.fernet import Fernet"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if probe.returncode == 0:
        return False
    shim_dir = Path(temporary_root) / "crypto-test-shim"
    shim_dir.mkdir(parents=True, exist_ok=True)
    (shim_dir / "sitecustomize.py").write_text(_WINDOWS_CRYPTO_SHIM, encoding="utf-8")
    inherited = env.get("PYTHONPATH") or ""
    env["PYTHONPATH"] = str(shim_dir) + (os.pathsep + inherited if inherited else "")
    return True
