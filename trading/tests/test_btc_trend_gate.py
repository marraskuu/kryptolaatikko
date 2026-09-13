"""BTC trend gate + longer rebuy cooldown."""

from django.test import SimpleTestCase

from trading.services.btc_trend_gate import (
    btc_trend_blocks_buy,
    compute_btc_trend_pct,
)
from trading.services.ai_trader import (
    SYMBOL_REBUY_COOLDOWN_SEC,
    _is_buy_blocked,
)


class BtcTrendComputeTests(SimpleTestCase):
    def test_positive_trend(self):
        candles = [
            {"timestamp": i * 86_400_000, "close": 100.0 + i}
            for i in range(25)
        ]
        pct = compute_btc_trend_pct(candles, lookback_days=21)
        self.assertIsNotNone(pct)
        self.assertGreater(pct, 0)

    def test_negative_trend_blocks(self):
        self.assertTrue(
            btc_trend_blocks_buy({"ok": True, "changePct": -3.0, "blocksBuy": True})
        )
        self.assertFalse(
            btc_trend_blocks_buy({"ok": True, "changePct": 2.0, "blocksBuy": False})
        )


class BtcTrendBuyBlockTests(SimpleTestCase):
    def test_is_buy_blocked_when_btc_trend_flag_set(self):
        analysis = {
            "currentPrice": 60_000.0,
            "volumeEur": 5_000_000.0,
            "action": "buy",
            "score": 9,
            "mtfAlign": 1,
            "changePct": 1.0,
            "change4hPct": 0.5,
            "microChecked": True,
            "microBlocked": False,
        }
        blocked = _is_buy_blocked(
            "tBTCUSD",
            analysis,
            blocked_buys=set(),
            blocked_setups=set(),
            regime="bull",
            regime_info={
                "regime": "bull",
                "breadth_up_pct": 55.0,
                "btc_trend_blocks_buy": True,
                "btc_trend_pct": -4.0,
            },
            allow_non_gemini_pick=True,
        )
        self.assertTrue(blocked)

    def test_rebuy_cooldown_default_at_least_4h(self):
        self.assertGreaterEqual(SYMBOL_REBUY_COOLDOWN_SEC, 14_400)
