"""Django management — historiallinen market-learning backfill (Railway Console)."""

from __future__ import annotations

import json
import time

from django.core.management.base import BaseCommand

from trading.services.market_learning_backfill import (
    HISTORY_BACKFILL_INTERVAL_SEC,
    _load,
    maybe_schedule_historical_backfill,
    run_historical_backfill,
)


class Command(BaseCommand):
    help = "Historiallinen varjo-oppiminen Bitfinex-kynttilöistä (tai GET /api/admin/historical-backfill/)"

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--force",
            action="store_true",
            help="Aja heti (ohita viikkoväli)",
        )
        parser.add_argument(
            "--async",
            dest="run_async",
            action="store_true",
            help="Käynnistä taustasäie (kuten engine)",
        )
        parser.add_argument(
            "--symbols",
            type=str,
            default="",
            help="Pilkuilla erotetut symbolit (oletus: top volyymi)",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Kynttilöiden määrä per symboli",
        )

    def handle(self, *args, **options) -> None:
        symbols = [s.strip() for s in options["symbols"].split(",") if s.strip()] or None
        candle_limit = options["limit"] or None

        if options["run_async"]:
            started = maybe_schedule_historical_backfill(force=options["force"])
            self.stdout.write(json.dumps({"scheduled": started}, indent=2))
            return

        if not options["force"]:
            store = _load()
            last = int(store.get("lastHistoryBackfillAt") or 0)
            now_ms = int(time.time() * 1000)
            if last and (now_ms - last) < HISTORY_BACKFILL_INTERVAL_SEC * 1000:
                age_h = (now_ms - last) / 1000 / 3600
                self.stderr.write(
                    self.style.WARNING(
                        f"Backfill ajettu {age_h:.1f} h sitten — käytä --force pakottaaksesi."
                    )
                )
                raise SystemExit(1)

        result = run_historical_backfill(symbols, candle_limit=candle_limit)
        self.stdout.write(json.dumps(result, indent=2, default=str))
