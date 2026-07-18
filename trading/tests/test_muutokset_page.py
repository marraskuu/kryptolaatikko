"""Julkiset FI/EN-sivut: /muutokset/, /changelog/, /eng/."""

from django.test import Client, TestCase


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
