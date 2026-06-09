import logging
import os
import sys
import threading
import time

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

        def _delayed_start() -> None:
            time.sleep(3)
            from .services.bot_worker import start_bot_worker
            from .services.gemini import log_startup_status

            log_startup_status()
            start_bot_worker()

        threading.Thread(target=_delayed_start, name="bot-worker-init", daemon=True).start()
