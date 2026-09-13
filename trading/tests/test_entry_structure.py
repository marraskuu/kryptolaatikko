"""Entry structure gates: 24h-high, volume spike, 15m/4h."""

from django.test import SimpleTestCase

from trading.services.entry_structure import (
    MAX_ENTRY_CHANGE_15M_PCT,
    MIN_DIST_FROM_24H_HIGH_PCT,
    MIN_ENTRY_CHANGE_4H_PCT,
    VOLUME_SPIKE_RATIO,
    apply_mtf_candles,
    apply_ticker_structure,
    apply_volume_structure,
    entry_structure_block_reason,
    entry_structure_blocks,
)
from trading.services.ai_trader import _is_buy_blocked


class TickerHighDistanceTests(SimpleTestCase):
    def test_near_high_blocks(self):
        analysis = {"currentPrice": 99.5}
        apply_ticker_structure(analysis, {"high": 100.0, "last": 99.5})
        self.assertTrue(analysis["near24hHigh"])
        self.assertLessEqual(analysis["distToHigh24hPct"], MIN_DIST_FROM_24H_HIGH_PCT)
        self.assertTrue(entry_structure_blocks(analysis))
        self.assertIn("near_24h_high", entry_structure_block_reason(analysis) or "")

    def test_far_from_high_allows(self):
        analysis = {"currentPrice": 90.0}
        apply_ticker_structure(analysis, {"high": 100.0, "last": 90.0})
        self.assertFalse(analysis["near24hHigh"])
        self.assertFalse(entry_structure_blocks(analysis))


class VolumeSpikeTests(SimpleTestCase):
    def test_spike_with_momentum_blocks(self):
        candles = [{"volume": 100.0, "close": 1.0} for _ in range(20)]
        candles.append({"volume": 100.0 * VOLUME_SPIKE_RATIO * 1.2, "close": 1.02})
        analysis = {"change1hPct": 1.5}
        apply_volume_structure(analysis, candles)
        self.assertTrue(analysis["volumeSpike"])
        self.assertTrue(entry_structure_blocks(analysis))

    def test_high_volume_without_momentum_does_not_flag_spike(self):
        candles = [{"volume": 100.0, "close": 1.0} for _ in range(20)]
        candles.append({"volume": 100.0 * VOLUME_SPIKE_RATIO * 1.2, "close": 1.0})
        analysis = {"change1hPct": 0.0}
        apply_volume_structure(analysis, candles)
        self.assertFalse(analysis.get("volumeSpike"))


class MtfCandleGateTests(SimpleTestCase):
    def test_15m_chase_blocks(self):
        # 4 closes: need bars+1 for period 1
        c15 = [{"close": 100.0}, {"close": 100.0 + MAX_ENTRY_CHANGE_15M_PCT + 0.5}]
        analysis: dict = {}
        apply_mtf_candles(analysis, c15, [{"close": 100.0}] * 5)
        self.assertGreaterEqual(analysis["change15mPct"], MAX_ENTRY_CHANGE_15M_PCT)
        self.assertTrue(entry_structure_blocks(analysis))

    def test_4h_downtrend_blocks(self):
        # 4 closes for period 3: indices -4 and -1
        base = 100.0
        c4h = [
            {"close": base},
            {"close": base * 0.99},
            {"close": base * 0.98},
            {"close": base * (1 + (MIN_ENTRY_CHANGE_4H_PCT - 1) / 100)},
        ]
        analysis: dict = {}
        apply_mtf_candles(analysis, [{"close": 100.0}, {"close": 100.1}], c4h)
        self.assertLess(analysis["change4hCandlePct"], MIN_ENTRY_CHANGE_4H_PCT)
        self.assertTrue(entry_structure_blocks(analysis))

    def test_calm_mtf_allows(self):
        c15 = [{"close": 100.0}, {"close": 100.5}]
        c4h = [{"close": 100.0}, {"close": 100.2}, {"close": 100.4}, {"close": 100.6}]
        analysis: dict = {}
        apply_mtf_candles(analysis, c15, c4h)
        self.assertFalse(entry_structure_blocks(analysis))


class BuyBlockedIntegrationTests(SimpleTestCase):
    def test_is_buy_blocked_respects_near_high(self):
        analysis = {
            "currentPrice": 60_000.0,
            "volumeEur": 5_000_000.0,
            "action": "buy",
            "score": 8,
            "mtfAlign": 1,
            "changePct": 2.0,
            "microChecked": True,
            "microBlocked": False,
            "near24hHigh": True,
            "distToHigh24hPct": 0.5,
        }
        blocked = _is_buy_blocked(
            "tBTCUSD",
            analysis,
            blocked_buys=set(),
            blocked_setups=set(),
            regime="bull",
            regime_info={"regime": "bull", "breadth_up_pct": 55.0},
        )
        self.assertTrue(blocked)
