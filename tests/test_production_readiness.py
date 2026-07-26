from pathlib import Path
import tempfile
import unittest

from scripts.check_production_readiness import check_environment


class ProductionReadinessTests(unittest.TestCase):
    def valid_environment(self, root: Path) -> dict[str, str]:
        uploads = root / "uploads"
        backups = root / "backups"
        uploads.mkdir()
        backups.mkdir()
        return {
            "KHADAMATI_ENV": "production",
            "KHADAMATI_SEED_DEMO_DATA": "false",
            "KHADAMATI_PUBLIC_URL": "https://example.test/khadamati/",
            "KHADAMATI_ALLOWED_ORIGINS": "https://example.test",
            "KHADAMATI_DB_PATH": str(root / "khadamati.sqlite3"),
            "KHADAMATI_UPLOAD_DIR": str(uploads),
            "KHADAMATI_BACKUP_DIR": str(backups),
            "KHADAMATI_OTP_PEPPER": "o" * 40,
            "KHADAMATI_MEDIA_SIGNING_KEY": "m" * 40,
            "KHADAMATI_ADMIN_CODE": "839174",
            "KHADAMATI_PAYMENT_GATEWAY": "manual",
        }

    def test_valid_production_environment_passes_without_exposing_values(self):
        with tempfile.TemporaryDirectory(prefix="khadamati-preflight-") as temp:
            environment = self.valid_environment(Path(temp))
            result = check_environment(environment)
        self.assertTrue(result["ok"])
        self.assertFalse(result["valuesExposed"])
        rendered = str(result)
        self.assertNotIn(environment["KHADAMATI_OTP_PEPPER"], rendered)
        self.assertNotIn(environment["KHADAMATI_MEDIA_SIGNING_KEY"], rendered)

    def test_ephemeral_or_development_configuration_is_rejected(self):
        result = check_environment(
            {
                "KHADAMATI_ENV": "development",
                "KHADAMATI_SEED_DEMO_DATA": "true",
                "KHADAMATI_DEV_OTP_CODE": "123456",
                "KHADAMATI_PUBLIC_URL": "http://localhost:8080",
                "KHADAMATI_ALLOWED_ORIGINS": "http://localhost:8080",
            },
            check_paths=False,
        )
        codes = {item["code"] for item in result["errors"]}
        self.assertFalse(result["ok"])
        self.assertIn("environment_not_production", codes)
        self.assertIn("demo_seed_must_be_disabled", codes)
        self.assertIn("development_otp_must_be_removed", codes)
        self.assertIn("persistent_path_required", codes)


if __name__ == "__main__":
    unittest.main(verbosity=2)
