"""Julkinen muutosloki /muutokset/."""

from django.test import Client, TestCase


class MuutoksetPageTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_muutokset_returns_200(self):
        response = self.client.get("/muutokset/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Muutokset")
        self.assertContains(response, "Deploy A")

    def test_sitemap_includes_muutokset(self):
        response = self.client.get("/sitemap.xml")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/muutokset/")
