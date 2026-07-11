"""Deploy B: microstructure-kentät Geminin markkinadataan + suodatus."""

from django.test import TestCase

from trading.services.gemini import (
    _build_market_summary,
    _build_scan_leaders,
    _entry_eligible_for_picks,
    _filter_gemini_picks,
    _micro_fields_for_gemini,
)


def _label(sym: str) -> str:
    return sym.replace("t", "").replace("USD", "")


class MicroFieldsForGeminiTests(TestCase):
    def test_empty_when_not_checked(self):
        self.assertEqual(_micro_fields_for_gemini({"bookImbalance": 0.2}), {})

    def test_includes_book_flow_crowd(self):
        fields = _micro_fields_for_gemini(
            {
                "microChecked": True,
                "bookImbalance": 0.31,
                "bookSpreadPct": 0.08,
                "longShortRatio": 1.15,
                "flowImbalance5m": 0.42,
                "bookBucket": "bk+",
                "crowdBucket": "cr+",
                "flowBucket": "fl+",
                "microAdjust": 1.5,
            }
        )
        self.assertEqual(fields["book_imbalance"], 0.31)
        self.assertEqual(fields["book_spread_pct"], 0.08)
        self.assertEqual(fields["crowd_ls_ratio"], 1.15)
        self.assertEqual(fields["flow_imbalance_5m"], 0.42)
        self.assertEqual(fields["book_bucket"], "bk+")
        self.assertEqual(fields["micro_adjust"], 1.5)
        self.assertNotIn("micro_blocked", fields)

    def test_micro_blocked_flag(self):
        fields = _micro_fields_for_gemini({"microChecked": True, "microBlocked": True})
        self.assertTrue(fields["micro_blocked"])


class BuildMarketSummaryMicroTests(TestCase):
    def test_market_summary_includes_micro_fields(self):
        tickers = {"tBTCUSD": {"last": 50000, "volumeEur": 5_000_000, "changePct": 1.0}}
        analyses = {
            "tBTCUSD": {
                "currentPrice": 50000,
                "volumeEur": 5_000_000,
                "microChecked": True,
                "bookImbalance": 0.2,
                "flowImbalance5m": -0.1,
                "bookBucket": "bk+",
                "flowBucket": "fl-",
            }
        }
        rows = _build_market_summary(tickers, analyses, {"holdings": {}}, _label, limit=5)
        row = rows[0]
        self.assertEqual(row["book_imbalance"], 0.2)
        self.assertEqual(row["flow_imbalance_5m"], -0.1)
        self.assertEqual(row["book_bucket"], "bk+")


class ScanLeadersMicroTests(TestCase):
    def test_skips_micro_blocked(self):
        tickers = {
            "tOKUSD": {"last": 5.0, "volumeEur": 500_000, "changePct": 2.0},
            "tBADUSD": {"last": 6.0, "volumeEur": 600_000, "changePct": 3.0},
        }
        analyses = {
            "tOKUSD": {"currentPrice": 5.0, "volumeEur": 500_000, "score": 5},
            "tBADUSD": {
                "currentPrice": 6.0,
                "volumeEur": 600_000,
                "score": 8,
                "microChecked": True,
                "microBlocked": True,
            },
        }
        leaders = _build_scan_leaders(tickers, analyses, _label)
        symbols = [r["symbol"] for r in leaders]
        self.assertIn("tOKUSD", symbols)
        self.assertNotIn("tBADUSD", symbols)


class FilterGeminiPicksMicroTests(TestCase):
    def setUp(self):
        self.analyses = {
            "tOKUSD": {"currentPrice": 5.0, "volumeEur": 300_000},
            "tBADUSD": {
                "currentPrice": 6.0,
                "volumeEur": 400_000,
                "microChecked": True,
                "microBlocked": True,
            },
        }
        self.tickers = {
            "tOKUSD": {"last": 5.0, "volumeEur": 300_000},
            "tBADUSD": {"last": 6.0, "volumeEur": 400_000},
        }

    def test_filters_micro_blocked(self):
        picks = _filter_gemini_picks(["tBADUSD", "tOKUSD"], self.analyses, self.tickers)
        self.assertEqual(picks, ["tOKUSD"])

    def test_entry_eligible_rejects_micro_blocked(self):
        self.assertFalse(_entry_eligible_for_picks("tBADUSD", self.analyses, self.tickers))
        self.assertTrue(_entry_eligible_for_picks("tOKUSD", self.analyses, self.tickers))
