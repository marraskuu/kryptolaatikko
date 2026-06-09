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
