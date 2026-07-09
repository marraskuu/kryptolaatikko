"""Kävijäseurannan suodattimet ja luotettava tallennus."""

from unittest.mock import patch

from django.test import RequestFactory, TestCase

from trading.models import PageVisit
from trading.services.visitor_analytics import (
    STATS_TRACKING_PAUSE_COOKIE,
    _normalize_client_ip,
    is_bot_user_agent,
    record_page_visit,
    should_record_page_visit,
)


class ShouldRecordPageVisitTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _get(self, path="/", **meta):
        request = self.factory.get(path, **meta)
        request.COOKIES[STATS_TRACKING_PAUSE_COOKIE] = "1"
        return request

    def test_direct_visit_counts_with_pause_cookie(self):
        request = self._get("/")
        self.assertTrue(should_record_page_visit(request, "/"))

    def test_prefetch_blocked_with_pause_cookie(self):
        request = self._get("/", HTTP_SEC_PURPOSE="prefetch")
        self.assertFalse(should_record_page_visit(request, "/"))

    def test_prerender_allowed(self):
        request = self.factory.get("/", HTTP_SEC_PURPOSE="prerender")
        self.assertTrue(should_record_page_visit(request, "/"))

    def test_stats_referrer_blocked_without_pause_cookie(self):
        request = self.factory.get("/", HTTP_REFERER="https://hiekkalaatikko.pro/stats/")
        self.assertFalse(should_record_page_visit(request, "/"))

    def test_direct_visit_without_pause_cookie(self):
        request = self.factory.get("/")
        self.assertTrue(should_record_page_visit(request, "/"))

    def test_client_fallback_allows_post(self):
        request = self.factory.post("/", HTTP_REFERER="https://hiekkalaatikko.pro/")
        self.assertTrue(
            should_record_page_visit(request, "/", client_fallback=True)
        )


class NormalizeClientIpTests(TestCase):
    def test_ipv4_with_port(self):
        self.assertEqual(_normalize_client_ip("203.0.113.10:443"), "203.0.113.10")

    def test_invalid_ip_returns_none(self):
        self.assertIsNone(_normalize_client_ip("not-an-ip"))


class RecordPageVisitTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_records_despite_geo_failure(self):
        request = self.factory.get(
            "/",
            HTTP_USER_AGENT="Mozilla/5.0 Chrome/120.0",
            REMOTE_ADDR="203.0.113.55",
        )
        with patch(
            "trading.services.visitor_analytics._country_code_for_request",
            side_effect=RuntimeError("geo down"),
        ):
            visit_id = record_page_visit(request, "/")
        self.assertIsNotNone(visit_id)
        visit = PageVisit.objects.get(pk=visit_id)
        self.assertEqual(visit.client_ip, "203.0.113.55")

    def test_invalid_ip_still_records_visit(self):
        request = self.factory.get(
            "/",
            HTTP_USER_AGENT="Mozilla/5.0 Chrome/120.0",
            HTTP_X_FORWARDED_FOR="definitely-not-valid",
        )
        visit_id = record_page_visit(request, "/")
        self.assertIsNotNone(visit_id)
        visit = PageVisit.objects.get(pk=visit_id)
        self.assertIsNone(visit.client_ip)


class BotUserAgentTests(TestCase):
    def test_normal_chrome_not_bot(self):
        ua = (
            "Mozilla/5.0 (Linux; Android 14; Pixel 8) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
        )
        self.assertFalse(is_bot_user_agent(ua))

    def test_googlebot_detected(self):
        self.assertTrue(is_bot_user_agent("Mozilla/5.0 (compatible; Googlebot/2.1)"))
