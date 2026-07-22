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
    """Etusivun käynti — referer, selain, IP ja maa (stats-sivulle)."""

    visited_at = models.DateTimeField(auto_now_add=True, db_index=True)
    path = models.CharField(max_length=200, default="/")
    referer = models.CharField(max_length=512, blank=True)
    referer_source = models.CharField(max_length=64, db_index=True, default="direct")
    referer_host = models.CharField(max_length=128, blank=True)
    user_agent = models.CharField(max_length=256, blank=True)
    ip_hash = models.CharField(max_length=32, db_index=True)
    client_ip = models.GenericIPAddressField(null=True, blank=True, db_index=True)
    client_isp = models.CharField(max_length=128, blank=True, default="")
    country_code = models.CharField(max_length=2, blank=True, default="", db_index=True)
    duration_sec = models.PositiveIntegerField(null=True, blank=True)
    is_bot = models.BooleanField(default=False, db_index=True)

    class Meta:
        verbose_name = "Sivukäynti"
        verbose_name_plural = "Sivukäynnit"
        ordering = ["-visited_at"]
        indexes = [
            models.Index(
                fields=["-visited_at", "referer_source"],
                name="trading_pag_visited_6a0f0d_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.visited_at:%Y-%m-%d %H:%M} {self.referer_source}"


class ShareClick(models.Model):
    """Klikkaus footerin somejakoikonista (WhatsApp/Facebook/X/LinkedIn)."""

    PLATFORM_CHOICES = [
        ("whatsapp", "WhatsApp"),
        ("facebook", "Facebook"),
        ("x", "X"),
        ("linkedin", "LinkedIn"),
    ]

    clicked_at = models.DateTimeField(auto_now_add=True, db_index=True)
    platform = models.CharField(max_length=16, choices=PLATFORM_CHOICES, db_index=True)
    lang = models.CharField(max_length=2, blank=True, default="")

    class Meta:
        verbose_name = "Somejakoklikkaus"
        verbose_name_plural = "Somejakoklikkaukset"
        ordering = ["-clicked_at"]

    def __str__(self) -> str:
        return f"{self.platform} {self.clicked_at:%Y-%m-%d %H:%M}"
