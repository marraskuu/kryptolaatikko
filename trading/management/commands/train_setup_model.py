"""Django management — kouluttaa scikit-learn-mallin setup-backfill-datasta (Railway Console)."""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from trading.services.setup_model import train_setup_model


class Command(BaseCommand):
    help = (
        "Kouluttaa HistGradientBoostingClassifierin setup_historical_backfill-näytteistä "
        "(tai GET /api/admin/train-model/). Aja historical_backfill ensin datan keräämiseksi."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--min-samples",
            type=int,
            default=200,
            help="Vähimmäismäärä näytteitä ennen koulutusta (oletus 200)",
        )

    def handle(self, *args, **options) -> None:
        result = train_setup_model(min_samples=options["min_samples"])
        self.stdout.write(json.dumps(result, indent=2, default=str))
