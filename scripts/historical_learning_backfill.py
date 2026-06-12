#!/usr/bin/env python3
"""
Historiallinen varjo-oppiminen Bitfinex-kynttilöistä (manuaali / cron).

Käyttö (paikallinen):
  python manage.py historical_backfill --force
  python scripts/historical_learning_backfill.py --force

Railway Console (jos python manage.py ei toimi — konsolissa ei aina Djangoa):
  Avaa selaimessa (SECRET_KEY Railway Variables -kohdasta):
  https://hiekkalaatikko.pro/api/admin/historical-backfill/?key=SALAINEN&force=1

Paikallinen / konsoli (kun Django asennettu):
  python manage.py historical_backfill --force
"""

from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from trading.services.market_learning_backfill import (  # noqa: E402
    maybe_schedule_historical_backfill,
    run_historical_backfill,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Historiallinen market-learning backfill")
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
        help="Kynttilöiden määrä per symboli (oletus: BACKFILL_CANDLE_LIMIT)",
    )
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()] or None
    candle_limit = args.limit or None

    if args.run_async:
        started = maybe_schedule_historical_backfill(force=args.force)
        print(json.dumps({"scheduled": started}, indent=2))
        return 0 if started else 1

    if not args.force:
        from trading.services.market_learning_backfill import _load, HISTORY_BACKFILL_INTERVAL_SEC
        import time

        store = _load()
        last = int(store.get("lastHistoryBackfillAt") or 0)
        now_ms = int(time.time() * 1000)
        if last and (now_ms - last) < HISTORY_BACKFILL_INTERVAL_SEC * 1000:
            age_h = (now_ms - last) / 1000 / 3600
            print(
                f"Backfill ajettu {age_h:.1f} h sitten — käytä --force pakottaaksesi.",
                file=sys.stderr,
            )
            return 1

    result = run_historical_backfill(symbols, candle_limit=candle_limit)
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
