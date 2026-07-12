"""Deploy C: setup_memory promptiin + pick_scorecard micro-bucketit."""

from django.test import TestCase

from trading.services.gemini import (
    _compact_learning,
    _entry_eligible_for_picks,
    _filter_gemini_picks,
    _pick_entry_row,
    _scorecard_micro_from_pick,
)
from trading.services.gemini_pick_tracking import build_pick_scorecard
from trading.services.market_learning import setup_key_for_analysis

_MICRO_OK = {"microChecked": True, "microBlocked": False}


def _label(sym: str) -> str:
    return sym.replace("t", "").replace("USD", "")


class CompactLearningSetupTests(TestCase):
    def test_includes_blocked_setups_and_winners_losers(self):
        learning = {
            "setup_memory": {
                "bull|u2|mtf+|rsi_md|vol_lg|deep|bk+|cr0|fl+": {
                    "expectancy_eur": 0.8,
                    "trades": 6,
                    "win_rate": 0.67,
                    "blocked": False,
                },
                "bear|d2|mtf-|rsi_hi|vol_md|quick|bk-|cr+|fl-": {
                    "expectancy_eur": -0.5,
                    "trades": 5,
                    "win_rate": 0.2,
                    "blocked": True,
                },
            },
            "blocked_setups": ["bear|d2|mtf-|rsi_hi|vol_md|quick|bk-|cr+|fl-"],
        }
        compact = _compact_learning(learning)
        self.assertEqual(len(compact["blocked_setups"]), 1)
        self.assertEqual(compact["blocked_setups"][0]["expectancy_eur"], -0.5)
        self.assertEqual(len(compact["setup_winners"]), 1)
        self.assertEqual(len(compact["setup_losers"]), 1)


class PickMicroBucketTests(TestCase):
    def test_pick_entry_row_includes_buckets(self):
        tickers = {"tBTCUSD": {"last": 50000, "volumeEur": 5_000_000, "changePct": 1.0}}
        analyses = {
            "tBTCUSD": {
                "currentPrice": 50000,
                "volumeEur": 5_000_000,
                "score": 5,
                "microChecked": True,
                "bookBucket": "bk+",
                "flowBucket": "fl+",
                "crowdBucket": "cr0",
            }
        }
        row = _pick_entry_row("tBTCUSD", tickers, analyses, {"regime": "bull"}, _label)
        self.assertEqual(row["book_bucket"], "bk+")
        self.assertEqual(row["flow_bucket"], "fl+")

    def test_scorecard_maps_buckets(self):
        mapped = _scorecard_micro_from_pick(
            {"book_bucket": "bk+", "flow_bucket": "fl-", "crowd_bucket": "cr+"}
        )
        self.assertEqual(mapped["entry_book_bucket"], "bk+")
        self.assertEqual(mapped["entry_flow_bucket"], "fl-")


class BlockedSetupFilterTests(TestCase):
    def setUp(self):
        self.analyses = {
            "tOKUSD": {
                "currentPrice": 5.0,
                "volumeEur": 300_000,
                "changePct": -3.0,
                "mtfAlign": -1,
                "rsi": 72,
                "quick": True,
                "bookBucket": "bk0",
                "crowdBucket": "cr0",
                "flowBucket": "fl0",
                **_MICRO_OK,
            },
            "tGOODUSD": {
                "currentPrice": 6.0,
                "volumeEur": 400_000,
                "changePct": 2.0,
                "mtfAlign": 2,
                "rsi": 55,
                "quick": True,
                "bookBucket": "bk+",
                "crowdBucket": "cr0",
                "flowBucket": "fl+",
                **_MICRO_OK,
            },
        }
        self.tickers = {
            "tOKUSD": {"last": 5.0, "volumeEur": 300_000},
            "tGOODUSD": {"last": 6.0, "volumeEur": 400_000},
        }
        self.blocked = {
            setup_key_for_analysis(self.analyses["tOKUSD"], {"regime": "bear"})
        }

    def test_filters_blocked_setup(self):
        picks = _filter_gemini_picks(
            ["tOKUSD", "tGOODUSD"],
            self.analyses,
            self.tickers,
            blocked_setups=self.blocked,
            regime={"regime": "bear"},
        )
        self.assertEqual(picks, ["tGOODUSD"])

    def test_entry_eligible_rejects_blocked_setup(self):
        self.assertFalse(
            _entry_eligible_for_picks(
                "tOKUSD",
                self.analyses,
                self.tickers,
                blocked_setups=self.blocked,
                regime={"regime": "bear"},
            )
        )


class PickScorecardMicroEnrichmentTests(TestCase):
    def test_enriches_from_trade_meta(self):
        snapshot = {
            "timestamp": "2026-07-01T10:00:00+00:00",
            "total_value": 1000,
            "regime": "bull",
            "top_picks": ["tBTCUSD"],
            "picks": [
                {
                    "symbol": "tBTCUSD",
                    "label": "BTC",
                    "price_eur": 50000,
                    "setup": "bull|u2|mtf+|rsi_md|vol_lg|deep|bk+|cr0|fl+",
                }
            ],
        }
        trades = [
            {
                "type": "buy",
                "symbol": "tBTCUSD",
                "timestamp": "2026-07-01T10:05:00+00:00",
                "amount": 0.001,
                "price": 50000,
                "eurTotal": 50,
                "geminiPick": True,
                "bookBucket": "bk+",
                "flowBucket": "fl+",
                "crowdBucket": "cr0",
            }
        ]
        tickers = {"tBTCUSD": {"last": 51000}}
        scorecard = build_pick_scorecard(snapshot, tickers, 1010, _label, trades=trades)
        pick = scorecard["pick_outcomes"][0]
        self.assertEqual(pick["entry_book_bucket"], "bk+")
        self.assertEqual(pick["entry_flow_bucket"], "fl+")

    def test_snapshot_buckets_preferred_over_trade(self):
        snapshot = {
            "timestamp": "2026-07-01T10:00:00+00:00",
            "total_value": 1000,
            "regime": "bull",
            "top_picks": ["tBTCUSD"],
            "picks": [
                {
                    "symbol": "tBTCUSD",
                    "label": "BTC",
                    "price_eur": 50000,
                    "setup": "bull|u2|mtf+|rsi_md|vol_lg|deep|bk+|cr0|fl+",
                    "book_bucket": "bk+",
                    "flow_bucket": "fl0",
                }
            ],
        }
        trades = [
            {
                "type": "buy",
                "symbol": "tBTCUSD",
                "timestamp": "2026-07-01T10:05:00+00:00",
                "amount": 0.001,
                "price": 50000,
                "eurTotal": 50,
                "geminiPick": True,
                "bookBucket": "bk-",
                "flowBucket": "fl-",
            }
        ]
        tickers = {"tBTCUSD": {"last": 51000}}
        scorecard = build_pick_scorecard(snapshot, tickers, 1010, _label, trades=trades)
        pick = scorecard["pick_outcomes"][0]
        self.assertEqual(pick["entry_book_bucket"], "bk+")
        self.assertEqual(pick["entry_flow_bucket"], "fl0")
