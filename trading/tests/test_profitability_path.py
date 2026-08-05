"""Päiväpolitiikan ostojen esto liveen + Gemini conf-lattia."""

from __future__ import annotations

from django.test import SimpleTestCase

from trading.services import ai_trader
from trading.services.daily_policy_shadow import would_block_buy
from trading.services.engine import _apply_daily_policy_buy_block
from trading.services.gemini_pick_tracking import compute_pick_tuning


def _buy(symbol: str) -> dict:
    return {"type": "buy", "symbol": symbol, "amount": 1.0, "eurAmount": 100.0, "reason": "osto"}


def _sell(symbol: str, reason: str) -> dict:
    return {"type": "sell", "symbol": symbol, "amount": 1.0, "eurAmount": 100.0, "reason": reason}


class WouldBlockBuyTests(SimpleTestCase):
    def test_daily_stop_blocks(self):
        blocked, reason = would_block_buy({"dailyStopActive": True})
        self.assertTrue(blocked)
        self.assertEqual(reason, "daily_stop")

    def test_firm_lock_blocks(self):
        blocked, reason = would_block_buy({"profitLockTier": "firm"})
        self.assertTrue(blocked)
        self.assertEqual(reason, "profit_lock_firm")

    def test_rolling_dd_optional(self):
        flags = {"rollingDrawdownActive": True, "dailyStopActive": False, "profitLockTier": "none"}
        blocked, _ = would_block_buy(flags)
        self.assertFalse(blocked)
        blocked, reason = would_block_buy(flags, include_rolling_dd=True)
        self.assertTrue(blocked)
        self.assertEqual(reason, "rolling_drawdown")


class DailyPolicyBuyBlockTests(SimpleTestCase):
    def test_converts_buys_to_hold_on_daily_stop(self):
        decisions = [_buy("tBTCUSD"), _sell("tETHUSD", "Stop-loss -1.2 %")]
        flags = {"dailyStopActive": True, "profitLockTier": "none"}
        result = _apply_daily_policy_buy_block(decisions, flags)
        buys = [d for d in result if d["type"] == "buy"]
        holds = [d for d in result if d["type"] == "hold"]
        sells = [d for d in result if d["type"] == "sell"]
        self.assertEqual(buys, [])
        self.assertEqual(len(holds), 1)
        self.assertIn("Päivästoppi", holds[0]["reason"])
        self.assertEqual(len(sells), 1)

    def test_firm_lock_blocks_buys(self):
        decisions = [_buy("tBTCUSD")]
        flags = {"dailyStopActive": False, "profitLockTier": "firm"}
        result = _apply_daily_policy_buy_block(decisions, flags)
        self.assertEqual(result[0]["type"], "hold")
        self.assertIn("Voittolukko", result[0]["reason"])

    def test_rolling_dd_blocks_when_enabled(self):
        decisions = [_buy("tBTCUSD")]
        flags = {
            "dailyStopActive": False,
            "profitLockTier": "none",
            "rollingDrawdownActive": True,
        }
        result = _apply_daily_policy_buy_block(
            decisions, flags, include_rolling_dd=True
        )
        self.assertEqual(result[0]["type"], "hold")
        self.assertIn("drawdown", result[0]["reason"].lower())


class GeminiSellDisabledTests(SimpleTestCase):
    def test_gemini_sell_disabled_by_default(self):
        self.assertFalse(ai_trader.GEMINI_SELL_ENABLED)


class GeminiConfFloorTests(SimpleTestCase):
    def test_pick_tuning_floor_at_least_buy_min(self):
        tuning, _ = compute_pick_tuning(None)
        self.assertGreaterEqual(
            tuning["gemini_buy_min_confidence"], ai_trader.GEMINI_BUY_MIN_CONFIDENCE
        )

    def test_weak_picks_still_at_least_floor(self):
        tuning, _ = compute_pick_tuning(
            {
                "rounds": 10,
                "picks_tracked": 20,
                "executed_picks_tracked": 5,
                "win_rate_pct": 10,
                "executed_win_rate_pct": 10,
                "avg_return_pct": -1.0,
                "executed_avg_return_pct": -1.0,
                "pick_beats_skipped_pct": 10,
            }
        )
        self.assertGreaterEqual(
            tuning["gemini_buy_min_confidence"], ai_trader.GEMINI_BUY_MIN_CONFIDENCE
        )


class RotationGateTests(SimpleTestCase):
    def test_rotation_off_until_positive_with_samples(self):
        from trading.services.learning import _apply_category_tuning

        params, notes = _apply_category_tuning(
            {"rotation": {"trades": 3, "expectancy_eur": 0.5, "win_rate": 1.0, "net_eur": 1.5}},
            min_samples=4,
        )
        self.assertFalse(params["rotation_enabled"])
        self.assertTrue(any("n=3" in n for n in notes))

        params2, _ = _apply_category_tuning(
            {"rotation": {"trades": 8, "expectancy_eur": -0.2, "win_rate": 0.3, "net_eur": -1.6}},
            min_samples=4,
        )
        self.assertFalse(params2["rotation_enabled"])

        params3, notes3 = _apply_category_tuning(
            {"rotation": {"trades": 8, "expectancy_eur": 0.5, "win_rate": 0.6, "net_eur": 4.0}},
            min_samples=4,
        )
        self.assertTrue(params3["rotation_enabled"])
        self.assertTrue(any("ok" in n for n in notes3))
