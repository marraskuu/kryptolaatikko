from django.db import models


class BotState(models.Model):
    """Yksi globaali bottitila koko palvelulle (live-simulaatio)."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    data = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Bottitila"
        verbose_name_plural = "Bottitila"

    def __str__(self) -> str:
        return "Globaali bottitila"


class PageVisit(models.Model):
    """Etusivun käynti — referer, selain, hashattu IP (ei raaka-IP:tä)."""

    visited_at = models.DateTimeField(auto_now_add=True, db_index=True)
    path = models.CharField(max_length=200, default="/")
    referer = models.CharField(max_length=512, blank=True)
    referer_source = models.CharField(max_length=64, db_index=True, default="direct")
    referer_host = models.CharField(max_length=128, blank=True)
    user_agent = models.CharField(max_length=256, blank=True)
    ip_hash = models.CharField(max_length=32, db_index=True)
    is_bot = models.BooleanField(default=False, db_index=True)

    class Meta:
        verbose_name = "Sivukäynti"
        verbose_name_plural = "Sivukäynnit"
        ordering = ["-visited_at"]
        indexes = [
            models.Index(fields=["-visited_at", "referer_source"]),
        ]

    def __str__(self) -> str:
        return f"{self.visited_at:%Y-%m-%d %H:%M} {self.referer_source}"
