from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services.avito_secrets import AvitoKeyError, decrypt_secret, encrypt_secret, key_configured


class AvitoSecretsTestCase(unittest.TestCase):
    def test_web_session_secret_encrypts_and_decrypts(self) -> None:
        settings = SimpleNamespace(web_session_secret="panel-secret")
        with patch("app.services.avito_secrets.get_settings", return_value=settings):
            encrypted = encrypt_secret("avito-client-secret")
            self.assertNotEqual(encrypted, "avito-client-secret")
            self.assertEqual(decrypt_secret(encrypted), "avito-client-secret")
            self.assertTrue(key_configured())

    def test_missing_web_session_secret_is_rejected(self) -> None:
        settings = SimpleNamespace(web_session_secret="")
        with patch("app.services.avito_secrets.get_settings", return_value=settings):
            self.assertFalse(key_configured())
            with self.assertRaises(AvitoKeyError):
                encrypt_secret("secret")

    def test_changed_web_session_secret_is_rejected(self) -> None:
        with patch("app.services.avito_secrets.get_settings", return_value=SimpleNamespace(web_session_secret="first")):
            encrypted = encrypt_secret("secret")
        with patch("app.services.avito_secrets.get_settings", return_value=SimpleNamespace(web_session_secret="second")):
            with self.assertRaises(AvitoKeyError):
                decrypt_secret(encrypted)


if __name__ == "__main__":
    unittest.main()
