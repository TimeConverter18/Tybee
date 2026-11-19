from __future__ import annotations

import os
import tempfile
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.avito_webhook import _prune_rate_keys, _rate_key, _requests
from app.config import Settings
from app.covers.service import _atomic_write
from app.support.attachments import secure_storage, store_bytes


class RateLimitTestCase(unittest.TestCase):
    def setUp(self) -> None:
        _requests.clear()

    def tearDown(self) -> None:
        _requests.clear()

    def test_forwarded_ip_is_trusted_only_from_loopback(self) -> None:
        headers = {"X-Forwarded-For": "203.0.113.10, 127.0.0.1"}
        self.assertEqual(
            _rate_key(SimpleNamespace(remote="127.0.0.1", headers=headers)),
            "203.0.113.10",
        )
        self.assertEqual(
            _rate_key(SimpleNamespace(remote="198.51.100.20", headers=headers)),
            "198.51.100.20",
        )

    def test_configured_proxy_network_is_trusted(self) -> None:
        headers = {"X-Forwarded-For": "203.0.113.10"}
        settings = SimpleNamespace(webhook_trusted_proxy_ips="172.20.0.0/24")
        with patch("app.avito_webhook.get_settings", return_value=settings):
            self.assertEqual(
                _rate_key(SimpleNamespace(remote="172.20.0.6", headers=headers)),
                "203.0.113.10",
            )
            self.assertEqual(
                _rate_key(SimpleNamespace(remote="172.21.0.6", headers=headers)),
                "172.21.0.6",
            )

    def test_invalid_forwarded_ip_falls_back_to_loopback(self) -> None:
        request = SimpleNamespace(remote="127.0.0.1", headers={"X-Forwarded-For": "not-an-ip"})
        self.assertEqual(_rate_key(request), "127.0.0.1")

    def test_rate_keys_are_pruned_and_bounded(self) -> None:
        now = 1_000.0
        _requests["stale"] = deque([now - 120])
        with patch("app.avito_webhook._RATE_MAX_KEYS", 2):
            _requests["first"] = deque([now])
            _requests["second"] = deque([now])
            _prune_rate_keys(now)
            self.assertNotIn("stale", _requests)
            _requests["third"] = deque([now])
            _prune_rate_keys(now)
            self.assertLessEqual(len(_requests), 2)
            self.assertNotIn("first", _requests)


class FilePermissionTestCase(unittest.TestCase):
    def test_cover_config_is_private_but_banner_is_public(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.yaml"
            banner = Path(directory) / "welcome.png"
            with patch("app.covers.service.os.chmod", wraps=os.chmod) as chmod:
                _atomic_write(config, b"token: secret")
                _atomic_write(banner, b"image")
            self.assertEqual(chmod.call_args_list[0].args[1], 0o600)
            self.assertEqual(chmod.call_args_list[1].args[1], 0o644)

    def test_support_attachment_is_private(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch("app.support.attachments.get_settings", return_value=SimpleNamespace(data_dir=directory)):
                with patch("app.support.attachments.os.chmod", wraps=os.chmod) as chmod:
                    stored = store_bytes(b"private", kind="document", mime="application/pdf", original_name="file.pdf")
            self.assertEqual(chmod.call_args.args[1], 0o600)
            self.assertTrue(stored.path.startswith("attachments/"))

    def test_existing_attachments_are_secured_on_startup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            attachments = Path(directory) / "attachments"
            attachments.mkdir()
            existing = attachments / "existing.pdf"
            existing.write_bytes(b"private")
            with patch("app.support.attachments.get_settings", return_value=SimpleNamespace(data_dir=directory)):
                with patch("app.support.attachments.os.chmod", wraps=os.chmod) as chmod:
                    secure_storage()
            chmod.assert_called_once_with(existing, 0o600)


class DatabaseUrlTestCase(unittest.TestCase):
    def test_credentials_are_encoded_by_sqlalchemy_url(self) -> None:
        settings = Settings(
            _env_file=None,
            postgres_user="user@example",
            postgres_password="p@ss:/%",
            postgres_host="localhost",
        )
        url = settings.database_url
        self.assertEqual(url.username, "user@example")
        self.assertEqual(url.password, "p@ss:/%")
        rendered = url.render_as_string(hide_password=False)
        self.assertIn("user%40example", rendered)
        self.assertIn("p%40ss%3A%2F%25", rendered)


if __name__ == "__main__":
    unittest.main()
