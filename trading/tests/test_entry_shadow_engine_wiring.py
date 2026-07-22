"""Ostohetken varjodiagnostiikan kytkentä engine.py:ssä.

Erityisesti: kokodiagnostiikka (idea #2/#4) lasketaan kaupan TODELLISTA
eurTotal-arvoa vasten, ei alkuperäistä pyydettyä eurAmount-arvoa — jos
Portfolio.buy() joutuu pienentämään ostoa käteisen riittämättömyyden vuoksi,
delta ei saa vääristyä.
"""

from django.test import SimpleTestCase

from trading.services.engine import (
    _attach_entry_correlation_shadow,
    _attach_entry_size_shadow,
)


class AttachEntryCorrelationShadowTests(SimpleTestCase):
    def test_sets_high_corr_flag_when_correlated_holding_exists(self):
        series = [1.0, 2.0, -1.0, 3.0, 0.0, 2.0, -2.0, 1.0]
        analyses = {
            "tBTCUSD": {"recentReturns": series},
            "tETHUSD": {"recentReturns": list(series)},
        }
        meta: dict = {}
        _attach_entry_correlation_shadow(
            meta, symbol="tETHUSD", analyses=analyses, held_symbols=["tBTCUSD"]
        )
        self.assertTrue(meta.get("shadowHighCorrFlag"))
        self.assertIn("shadowMaxHoldingCorr", meta)

    def test_no_keys_added_without_holdings(self):
        meta: dict = {}
        _attach_entry_correlation_shadow(
            meta, symbol="tETHUSD", analyses={"tETHUSD": {"recentReturns": [1.0] * 8}}, held_symbols=[]
        )
        self.assertNotIn("shadowHighCorrFlag", meta)
        self.assertNotIn("shadowMaxHoldingCorr", meta)


class AttachEntrySizeShadowTests(SimpleTestCase):
    def test_uses_actual_trade_eur_total_not_requested_amount(self):
        # Varjoehdotus (esim. ATR-painotettu) olisi ollut 100 €, mutta
        # Portfolio.buy() pienensi oikean kaupan 60 €:oon käteisen puutteen
        # vuoksi -> delta pitää laskea 60 €:a vasten, ei alkuperäistä pyyntöä.
        trade = {"symbol": "tETHUSD", "eurTotal": 60.0}
        _attach_entry_size_shadow(
            trade,
            symbol="tETHUSD",
            atr_shadow_map={"tETHUSD": 100.0},
            kelly_shadow_map={"tETHUSD": 80.0},
        )
        self.assertAlmostEqual(trade["shadowAtrSizeDeltaEur"], 40.0, places=2)
        self.assertAlmostEqual(trade["shadowKellySizeDeltaEur"], 20.0, places=2)

    def test_missing_shadow_entry_adds_no_key(self):
        trade = {"symbol": "tETHUSD", "eurTotal": 60.0}
        _attach_entry_size_shadow(
            trade, symbol="tETHUSD", atr_shadow_map={}, kelly_shadow_map={}
        )
        self.assertNotIn("shadowAtrSizeDeltaEur", trade)
        self.assertNotIn("shadowKellySizeDeltaEur", trade)

    def test_zero_eur_total_does_not_raise(self):
        trade = {"symbol": "tETHUSD", "eurTotal": 0.0}
        _attach_entry_size_shadow(
            trade,
            symbol="tETHUSD",
            atr_shadow_map={"tETHUSD": 50.0},
            kelly_shadow_map={},
        )
        self.assertAlmostEqual(trade["shadowAtrSizeDeltaEur"], 50.0, places=2)
