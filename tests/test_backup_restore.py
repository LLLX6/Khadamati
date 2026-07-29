import sqlite3
import subprocess
import sys
import tempfile
import unittest
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
