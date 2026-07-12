"""Microstructure fail-closed — ostoja ei sallita kun enrich puuttuu."""

from unittest.mock import patch

from django.test import SimpleTestCase

from trading.services.ai_trader import _is_buy_blocked, enrich_analyses_for_gemini
from trading.services.engine import _refresh_analyses
from trading.services.market_microstructure import blocks_entry, carry_micro_fields, enrich_analyses
from trading.services.session_state import default_state


def _buy_analysis(**overrides):
    base = {
        "currentPrice": 50_000.0,
        "volumeEur": 500_000.0,
        "action": "buy",
        "score": 5,
        "mtfAlign": 2,
        "changePct": 2.0,
        "change4hPct": 1.0,
    }
    base.update(overrides)
    return base


class MicrostructureGateTests(SimpleTestCase):
    @patch("trading.services.market_microstructure.ENABLED", True)
    def test_blocks_entry_without_micro_checked(self):
        """Kun enrich ei aja, microChecked puuttuu → osto estettävä."""
        analysis = _buy_analysis()
        self.assertNotIn("microChecked", analysis)
        self.assertTrue(blocks_entry(analysis))

    @patch("trading.services.market_microstructure.ENABLED", True)
    def test_blocks_entry_allows_checked_unblocked(self):
        analysis = _buy_analysis(microChecked=True, microBlocked=False)
        self.assertFalse(blocks_entry(analysis))

    @patch("trading.services.market_microstructure.ENABLED", True)
    def test_refresh_analyses_leaves_buy_blocked_until_enrich(self):
        """_refresh_analyses tyhjentää micro-kentät — _is_buy_blocked estää ostot."""
        state = default_state()
        state["tickers"] = {
            "tBTCUSD": {
                "last": 50_000.0,
                "volumeEur": 1_000_000.0,
                "changePct": 1.5,
            }
        }
        _refresh_analyses(state)
        analysis = state["analyses"]["tBTCUSD"]

        blocked = _is_buy_blocked(
            "tBTCUSD",
            analysis,
            blocked_buys=set(),
            blocked_setups=set(),
            regime="neutral",
        )
        self.assertTrue(blocked)

    @patch("trading.services.market_microstructure.ENABLED", False)
    def test_blocks_entry_disabled_when_microstructure_off(self):
        analysis = _buy_analysis()
        self.assertFalse(blocks_entry(analysis))

    @patch("trading.services.market_microstructure.ENABLED", True)
    def test_carry_micro_fields_preserves_gate_after_deep_replace(self):
        prev = _buy_analysis(
            microChecked=True,
            microBlocked=True,
            bookBidDepthEur=5_000.0,
            microAdjust=-2.0,
        )
        fresh = {"currentPrice": 50_000.0, "score": 3, "action": "buy"}
        carry_micro_fields(prev, fresh)
        self.assertTrue(fresh["microChecked"])
        self.assertTrue(fresh["microBlocked"])
        self.assertEqual(fresh["bookBidDepthEur"], 5_000.0)
        self.assertTrue(blocks_entry(fresh))

    @patch("trading.services.ai_trader.build_deep_analysis")
    def test_gemini_deep_analysis_keeps_micro_checked(self, mock_deep):
        mock_deep.return_value = {"currentPrice": 100.0, "score": 4, "action": "buy"}
        tickers = {"tBTCUSD": {"last": 100.0, "volumeEur": 1_000_000.0, "changePct": 1.0}}
        analyses = {
            "tBTCUSD": _buy_analysis(
                microChecked=True,
                microBlocked=False,
                bookBidDepthEur=80_000.0,
            )
        }

        enrich_analyses_for_gemini(
            tickers,
            analyses,
            {"holdings": {}, "cash": 1000, "trades": []},
            lambda sym, interval, limit: [],
        )

        self.assertTrue(analyses["tBTCUSD"]["microChecked"])
        self.assertFalse(analyses["tBTCUSD"]["microBlocked"])
        self.assertEqual(analyses["tBTCUSD"]["bookBidDepthEur"], 80_000.0)

    @patch("trading.services.market_microstructure.BOOK_REQ_PAUSE_SEC", 0)
    @patch("trading.services.market_microstructure._cached_position_stats", return_value=None)
    @patch("trading.services.market_microstructure.fetch_trades_hist", return_value=[])
    @patch(
        "trading.services.market_microstructure.parse_order_book",
        return_value={
            "bookImbalance": 0.1,
            "bookSpreadPct": 0.01,
            "bookBidDepthEur": 90_000.0,
            "bookAskDepthEur": 95_000.0,
        },
    )
    @patch("trading.services.market_microstructure.fetch_order_book", return_value=[["raw"]])
    @patch("trading.services.market_microstructure.ENABLED", True)
    def test_enrich_marks_checked_after_micro_observation(
        self,
        _mock_book,
        _mock_parse_book,
        _mock_trades,
        _mock_stats,
    ):
        tickers = {"tBTCUSD": {"last": 100.0, "volumeEur": 1_000_000.0, "changePct": 1.0}}
        analyses = {"tBTCUSD": {"score": 5, "currentPrice": 100.0}}

        enrich_analyses(tickers, analyses, {"holdings": {}, "cash": 1000, "trades": []}, "neutral")

        self.assertTrue(analyses["tBTCUSD"]["microChecked"])
        self.assertFalse(analyses["tBTCUSD"]["microBlocked"])
        self.assertFalse(blocks_entry(analyses["tBTCUSD"]))

    @patch("trading.services.market_microstructure.BOOK_REQ_PAUSE_SEC", 0)
    @patch("trading.services.market_microstructure._cached_position_stats", return_value=None)
    @patch("trading.services.market_microstructure.fetch_trades_hist", return_value=[])
    @patch("trading.services.market_microstructure.fetch_order_book", return_value=[])
    @patch("trading.services.market_microstructure.ENABLED", True)
    def test_enrich_leaves_empty_micro_fetches_unchecked(
        self,
        _mock_book,
        _mock_trades,
        _mock_stats,
    ):
        """Tyhjät/parseamattomat micro-vastaukset eivät saa avata ostoporttia."""
        symbols = [f"tSYM{i}USD" for i in range(15)]
        tickers = {
            sym: {"last": 100.0 + i, "volumeEur": 1_000_000.0 - i * 1000, "changePct": 1.0}
            for i, sym in enumerate(symbols)
        }
        analyses = {sym: {"score": 15 - i, "currentPrice": tickers[sym]["last"]} for i, sym in enumerate(symbols)}

        enrich_analyses(tickers, analyses, {"holdings": {}, "cash": 1000, "trades": []}, "neutral")

        for sym in symbols:
            self.assertFalse(
                analyses[sym].get("microChecked"),
                f"{sym} unexpectedly marked microChecked",
            )
            self.assertTrue(blocks_entry(analyses[sym]), f"{sym} entry gate opened")
