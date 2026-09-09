import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.backup_sqlite import create_backup
from scripts.restore_sqlite_backup import restore_backup

ROOT = Path(__file__).resolve().parents[1]


class BackupRestoreTests(unittest.TestCase):
    def test_database_and_uploads_round_trip(self):
        with tempfile.TemporaryDirectory(prefix="khadamati-backup-test-") as temp:
            root = Path(temp)
            source_database = root / "source.sqlite3"
            source_uploads = root / "source-uploads"
            source_uploads.mkdir()
            (source_uploads / "sample.txt").write_text(
                "isolated test file", encoding="utf-8"
            )
            con = sqlite3.connect(source_database)
            try:
                con.execute("CREATE TABLE sample(id INTEGER PRIMARY KEY,value TEXT)")
                con.execute("INSERT INTO sample(value) VALUES(?)", ("safe-test",))
                con.commit()
            finally:
                con.close()

            archive = root / "backup.zip"
            backup = create_backup(source_database, archive, source_uploads)
            self.assertEqual("ok", backup["integrity"])
            self.assertEqual(1, backup["uploads"])

            restored_database = root / "restored.sqlite3"
            restored_uploads = root / "restored-uploads"
            restored = restore_backup(
                archive, restored_database, restored_uploads
            )
            self.assertEqual("ok", restored["integrity"])
            con = sqlite3.connect(restored_database)
            try:
                value = con.execute("SELECT value FROM sample").fetchone()[0]
            finally:
                con.close()
            self.assertEqual("safe-test", value)
            self.assertEqual(
                "isolated test file",
                (restored_uploads / "sample.txt").read_text(encoding="utf-8"),
            )

    def test_restore_refuses_existing_target(self):
        with tempfile.TemporaryDirectory(prefix="khadamati-restore-guard-") as temp:
            root = Path(temp)
            source = root / "source.sqlite3"
            con = sqlite3.connect(source)
            con.execute("CREATE TABLE sample(id INTEGER)")
            con.close()
            archive = root / "backup.zip"
            create_backup(source, archive)
            existing = root / "existing.sqlite3"
            existing.write_bytes(b"do-not-overwrite")
            with self.assertRaises(FileExistsError):
                restore_backup(archive, existing)
            self.assertEqual(b"do-not-overwrite", existing.read_bytes())

    def test_restore_rejects_incomplete_or_inconsistent_archives(self):
        with tempfile.TemporaryDirectory(prefix="khadamati-backup-integrity-") as temp:
            root = Path(temp)
            database = root / "source.sqlite3"
            with sqlite3.connect(database) as con:
                con.execute("CREATE TABLE sample(id INTEGER)")
            uploads = root / "source-uploads"
            uploads.mkdir()
            (uploads / "evidence.txt").write_bytes(b"important test attachment")
            archive = root / "good.zip"
            create_backup(database, archive, uploads)
            with zipfile.ZipFile(archive) as original:
                contents = {name: original.read(name) for name in original.namelist()}

            for scenario in ("missing_upload", "unlisted_upload", "wrong_size", "duplicate_entry"):
                with self.subTest(scenario=scenario):
                    altered = dict(contents)
                    manifest = json.loads(altered["manifest.json"])
                    if scenario == "missing_upload":
                        del altered["uploads/evidence.txt"]
                    elif scenario == "unlisted_upload":
                        del manifest["entries"]["uploads/evidence.txt"]
                    elif scenario == "wrong_size":
                        manifest["entries"]["uploads/evidence.txt"]["bytes"] += 1
                    altered["manifest.json"] = json.dumps(manifest).encode("utf-8")
                    broken = root / f"{scenario}.zip"
                    with zipfile.ZipFile(broken, "w") as target:
                        for name, data in altered.items():
                            target.writestr(name, data)
                        if scenario == "duplicate_entry":
                            with self.assertWarns(UserWarning):
                                target.writestr("uploads/evidence.txt", altered["uploads/evidence.txt"])
                    restored = root / f"{scenario}.sqlite3"
                    restored_uploads = root / f"{scenario}-uploads"
                    with self.assertRaises(ValueError):
                        restore_backup(broken, restored, restored_uploads)
                    self.assertFalse(restored.exists())
                    self.assertFalse(restored_uploads.exists())

    def test_restore_script_supports_direct_invocation(self):
        result = subprocess.run(
            [sys.executable, "scripts/restore_sqlite_backup.py", "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--database-target", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
