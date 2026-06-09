import os
import sys

from django.apps import AppConfig


class TradingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "trading"

    def ready(self) -> None:
        if os.environ.get("DISABLE_BOT_WORKER") == "1":
            return
        if "migrate" in sys.argv or "collectstatic" in sys.argv:
            return
        is_web = "runserver" in sys.argv or "gunicorn" in " ".join(sys.argv)
        if not is_web:
            return

        from .services.bot_worker import start_bot_worker

        start_bot_worker()
