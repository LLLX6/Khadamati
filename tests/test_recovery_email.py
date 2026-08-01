from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

import server


class RecoveryEmailTests(unittest.TestCase):
    def test_sends_temporary_code_through_configured_smtp(self):
        settings = {
            "KHADAMATI_SMTP_HOST": "smtp.example.test",
            "KHADAMATI_SMTP_PORT": "587",
            "KHADAMATI_SMTP_USER": "sender@example.test",
            "KHADAMATI_SMTP_PASSWORD": "test-only-password",
            "KHADAMATI_SMTP_FROM_EMAIL": "sender@example.test",
            "KHADAMATI_SMTP_FROM_NAME": "Khadamati Administration",
            "KHADAMATI_SMTP_USE_TLS": "1",
            "KHADAMATI_SMTP_USE_SSL": "0",
        }
        client = MagicMock()
        smtp = MagicMock()
        smtp.return_value.__enter__.return_value = client

        with (
            patch.dict(os.environ, settings, clear=False),
            patch.object(server.smtplib, "SMTP", smtp),
            patch.object(server, "log_event"),
        ):
            result = server.send_recovery_email(
                "customer@example.test", "Customer", "123456"
            )

        self.assertTrue(result["ok"])
        self.assertEqual("email", result["channel"])
        client.starttls.assert_called_once()
        client.login.assert_called_once_with(
            "sender@example.test", "test-only-password"
        )
        message = client.send_message.call_args.args[0]
        self.assertEqual("customer@example.test", message["To"])
        self.assertIn("123456", message.get_content())

    def test_does_not_send_without_credentials(self):
        settings = {
            "KHADAMATI_SMTP_HOST": "smtp.gmail.com",
            "KHADAMATI_SMTP_USER": "",
            "KHADAMATI_SMTP_PASSWORD": "",
        }
        with patch.dict(os.environ, settings, clear=False):
            result = server.send_recovery_email(
                "customer@example.test", "Customer", "123456"
            )
        self.assertFalse(result["ok"])
        self.assertFalse(result["configured"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
