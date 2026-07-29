"""Bear-kassavaratrimmaus valitsee ensin voitolla/tasan olevat positiot.

sellOutcomeLearning-data (2026-07-29) näytti bear_cash_trim win raten 8 % (12
tappiota / 13 kauppaa), koska valinta oli P/L-sokea — se trimmasi aina heikoimman
scoren position riippumatta siitä oliko se juuri tappiolla. Tämä testi lukitsee
korjauksen: kun sekä voitollinen että tappiollinen kandidaatti riittävät
kassatavoitteeseen, tappiollinen jätetään rauhaan.
"""

from __future__ import annotations

from django.test import SimpleTestCase

from trading.services import ai_trader


def _label(symbol: str) -> str:
    return symbol


class BearCashTrimPrefersNonLosingPositionsTests(SimpleTestCase):
    def setUp(self):
        self.regime_info = {"regime": "bear", "phase": "bear"}
        # total_value 1000, käteistä 100 (10 %) -> tavoite 25 % = 250 € -> gap 150 €
        self.total_value = 1000.0
        self.cash = 100.0
        self.holdings = {
            "tLOSSUSD": {"amount": 10.0, "avgPrice": 20.0},  # nyt 15 -> -25 % tappiolla
            # Riittävän iso, jotta BEAR_CASH_TRIM_MAX_FRACTION (50 %) kattaa koko
            # 150 € kassavajeen yhdellä kaupalla eikä silmukka etene LOSSUSD:iin.
            "tWINUSD": {"amount": 30.0, "avgPrice": 10.0},  # nyt 15 -> +50 % voitolla
        }
        self.analyses = {
            "tLOSSUSD": {"currentPrice": 15.0, "score": 1.0},
            "tWINUSD": {"currentPrice": 15.0, "score": 1.0},
        }

    def test_trims_winning_position_before_losing_one(self):
        decisions: list[dict] = []
        ai_trader._apply_bear_cash_reserve_trim(
            decisions,
            self.holdings,
            self.analyses,
            self.cash,
            self.total_value,
            self.regime_info,
            _label,
        )
        sold_symbols = {d["symbol"] for d in decisions if d.get("type") == "sell"}
        self.assertIn("tWINUSD", sold_symbols)
        self.assertNotIn("tLOSSUSD", sold_symbols)

    def test_falls_back_to_losing_position_when_no_alternative(self):
        decisions: list[dict] = []
        holdings = {"tLOSSUSD": self.holdings["tLOSSUSD"]}
        analyses = {"tLOSSUSD": self.analyses["tLOSSUSD"]}
        ai_trader._apply_bear_cash_reserve_trim(
            decisions,
            holdings,
            analyses,
            self.cash,
            self.total_value,
            self.regime_info,
            _label,
        )
        sold_symbols = {d["symbol"] for d in decisions if d.get("type") == "sell"}
        self.assertIn("tLOSSUSD", sold_symbols)
