"""Julkiset FI/EN-sivut: /muutokset/, /changelog/, /eng/."""

from django.test import Client, TestCase

from trading.changelog import changelog_days


class MuutoksetPageTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_muutokset_returns_200(self):
        response = self.client.get("/muutokset/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Muutokset")
        self.assertContains(response, "Deploy A")

    def test_changelog_en_returns_200(self):
        response = self.client.get("/changelog/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Changelog")
        self.assertContains(response, "Deploy A")
        self.assertContains(response, "lang=\"en\"")

    def test_eng_home_returns_200(self):
        response = self.client.get("/eng/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Crypto Simulator")
        self.assertContains(response, "lang=\"en\"")
        self.assertContains(response, "i18n.js")
        self.assertContains(response, "lang-flags")

    def test_fi_home_still_finnish(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Krypto Simulaattori")
        self.assertContains(response, "lang=\"fi\"")
        self.assertContains(response, "lang-flags")
        self.assertContains(response, 'href="/eng/"')

    def test_sitemap_includes_en_pages(self):
        response = self.client.get("/sitemap.xml")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/muutokset/")
        self.assertContains(response, "/changelog/")
        self.assertContains(response, "/eng/")

    def test_sitemap_changelog_lastmod_matches_newest_entry(self):
        """Muutosloki-sivujen lastmod ei ole aina "tänään", vaan uusin julkaistu päivä."""
        newest_date = changelog_days()[0]["date"]
        response = self.client.get("/sitemap.xml")
        body = response.content.decode("utf-8")
        self.assertIn(f"<lastmod>{newest_date}</lastmod>", body)

    def test_pages_have_favicon_link(self):
        for path in ("/", "/eng/", "/muutokset/", "/changelog/"):
            response = self.client.get(path)
            self.assertContains(response, "favicon.svg", msg_prefix=f"path={path}")
