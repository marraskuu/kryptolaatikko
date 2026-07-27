"""BEAR_FREEZE_EXCEPTION_MIN_ADJUST: env-säädettävyys ja tunnetun rajoituksen dokumentointi."""

from __future__ import annotations

import importlib
from unittest import mock

from django.test import SimpleTestCase

from trading.services import ai_trader, market_learning


def _bear_analysis(cond_adjust: float) -> dict:
    return {
        "action": "buy",
        "condBlocked": False,
        "condAdjust": cond_adjust,
        "currentPrice": 100.0,
        "mtfAlign": 1,
        "changePct": 5.0,
        "change4hPct": 1.0,
        "microChecked": True,
        "microBlocked": False,
    }


class IsBuyBlockedThresholdTests(SimpleTestCase):
    """_is_buy_blocked (Gemini-active path) — kynnyksen molemmin puolin."""

    def test_below_threshold_is_blocked(self):
        analysis = _bear_analysis(ai_trader.BEAR_FREEZE_EXCEPTION_MIN_ADJUST - 0.1)
        blocked = ai_trader._is_buy_blocked(
            "tBTCUSD",
            analysis,
            blocked_buys=set(),
            blocked_setups=set(),
            regime="bear",
        )
        self.assertTrue(blocked)

    def test_at_or_above_threshold_is_not_blocked(self):
        analysis = _bear_analysis(ai_trader.BEAR_FREEZE_EXCEPTION_MIN_ADJUST)
        blocked = ai_trader._is_buy_blocked(
            "tBTCUSD",
            analysis,
            blocked_buys=set(),
            blocked_setups=set(),
            regime="bear",
        )
        self.assertFalse(blocked)


class EntryOkBearStructuralGateRegressionTests(SimpleTestCase):
    """Dokumentoi tunnettu rajoitus: _entry_ok() blokkaa bear-regiimin AINA
    teknisellä (ei-Gemini) polulla riippumatta condAdjustista, koska mtf>=2
    on saavuttamaton (_mtf_alignment palauttaa vain -1/0/1). Tämä testi on
    tarkoituksella regressio nykyiselle käytökselle, ei toivotulle."""

    def test_entry_ok_blocks_bear_even_with_maximal_favorable_setup(self):
        analysis = _bear_analysis(4.0)  # max condAdjust, ei silti riitä
        self.assertFalse(ai_trader._entry_ok(analysis, "bear"))


class BearFreezeExceptionEnvOverrideTests(SimpleTestCase):
    @mock.patch.dict("os.environ", {"BEAR_FREEZE_EXCEPTION_MIN_ADJUST": "3.5"})
    def test_env_override_changes_effective_threshold(self):
        reloaded = importlib.reload(ai_trader)
        try:
            self.assertEqual(reloaded.BEAR_FREEZE_EXCEPTION_MIN_ADJUST, 3.5)
            analysis = _bear_analysis(3.0)
            blocked = reloaded._is_buy_blocked(
                "tBTCUSD",
                analysis,
                blocked_buys=set(),
                blocked_setups=set(),
                regime="bear",
            )
            self.assertTrue(blocked)  # 3.0 < 3.5 override
        finally:
            importlib.reload(ai_trader)


class ConditionAdjustDerivationSanityTest(SimpleTestCase):
    """Toistaa käsinlasketun johdon: exp1h=3.19 %, n=33 -> clamppautuu MAX_SCORE_ADJUST=4.0:aan."""

    def test_cited_bear_setup_clamps_at_max_score_adjust(self):
        analysis = {
            "changePct": -6.0,       # d5-bucket
            "mtfAlign": -1,          # mtf-
            "rsi": 60,               # rsi_hi
            "volumeEur": 300_000,    # vol_sm
            "quick": True,
            "bookImbalance": -0.5,
            "longShortRatio": 1.0,
            "flowImbalance": 0.0,
        }
        key = market_learning.setup_key_for_analysis(analysis, "bear")
        stats = {key: {"1h": {"n": 33.0, "sum": 33.0 * 3.19}}}

        adj = market_learning.condition_adjust(analysis, "bear", stats)

        self.assertEqual(adj, market_learning.MAX_SCORE_ADJUST)
        self.assertGreater(market_learning.MAX_SCORE_ADJUST, ai_trader.BEAR_FREEZE_EXCEPTION_MIN_ADJUST)
