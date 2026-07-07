"""Turvallisuusapujen yksikkötestit."""

import hashlib
import os
from unittest.mock import patch

from django.test import RequestFactory, TestCase, override_settings

from trading.security_utils import (
    admin_task_key,
    rate_limit_exceeded,
    read_admin_key_from_request,
    safe_next_path,
)


class SafeNextPathTests(TestCase):
    def test_relative_path_ok(self):
        self.assertEqual(safe_next_path("/stats/"), "/stats/")

    def test_external_url_blocked(self):
        self.assertEqual(safe_next_path("https://evil.com"), "/stats/")

    def test_protocol_relative_blocked(self):
        self.assertEqual(safe_next_path("//evil.com"), "/stats/")

    def test_empty_defaults(self):
        self.assertEqual(safe_next_path(None), "/stats/")


class AdminTaskKeyTests(TestCase):
    @override_settings(DEBUG=True, SECRET_KEY="dev-only-change-in-production")
    def test_debug_uses_secret(self):
        self.assertEqual(admin_task_key(), "dev-only-change-in-production")

    @override_settings(DEBUG=False, SECRET_KEY="prod-secret-value")
    def test_prod_derives_from_secret(self):
        expected = hashlib.sha256(b"admin-task:prod-secret-value").hexdigest()
        self.assertEqual(admin_task_key(), expected)

    @override_settings(DEBUG=False, SECRET_KEY="prod-secret-value")
    @patch.dict(os.environ, {"ADMIN_TASK_KEY": "custom-key"}, clear=False)
    def test_explicit_env_overrides(self):
        self.assertEqual(admin_task_key(), "custom-key")


class ReadAdminKeyTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_header_preferred(self):
        req = self.factory.get("/", HTTP_X_ADMIN_TASK_KEY="hdr-key")
        self.assertEqual(read_admin_key_from_request(req), "hdr-key")

    def test_bearer_auth(self):
        req = self.factory.get("/", HTTP_AUTHORIZATION="Bearer token-123")
        self.assertEqual(read_admin_key_from_request(req), "token-123")

    def test_query_fallback(self):
        req = self.factory.get("/?key=query-key")
        self.assertEqual(read_admin_key_from_request(req), "query-key")


class RateLimitTests(TestCase):
    def test_allows_under_limit(self):
        self.assertFalse(rate_limit_exceeded("test-scope", "1.2.3.4", limit=3, window_sec=60))
        self.assertFalse(rate_limit_exceeded("test-scope", "1.2.3.4", limit=3, window_sec=60))

    def test_blocks_over_limit(self):
        for _ in range(3):
            rate_limit_exceeded("block-scope", "9.9.9.9", limit=3, window_sec=60)
        self.assertTrue(rate_limit_exceeded("block-scope", "9.9.9.9", limit=3, window_sec=60))
