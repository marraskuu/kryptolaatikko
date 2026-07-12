"""Deploy A: varjo-oppiminen ennen Geminia + pick-suodatus."""

from django.test import TestCase

from trading.services.gemini import (
    _effective_technical_score,
    _entry_eligible_for_picks,
    _filter_gemini_picks,
)

_MICRO_OK = {"microChecked": True, "microBlocked": False}


class EffectiveTechnicalScoreTests(TestCase):
    def test_includes_cond_adjust(self):
        self.assertEqual(_effective_technical_score({"score": 5, "condAdjust": 2.5}), 7.5)


class FilterGeminiPicksTests(TestCase):
    def setUp(self):
        self.analyses = {
            "tBTCUSD": {"currentPrice": 50000, "volumeEur": 5_000_000, **_MICRO_OK},
            "tLOWUSD": {"currentPrice": 2.0, "volumeEur": 100_000, **_MICRO_OK},
            "tOKUSD": {"currentPrice": 5.0, "volumeEur": 300_000, **_MICRO_OK},
        }
        self.tickers = {
            "tBTCUSD": {"last": 50000, "volumeEur": 5_000_000},
            "tLOWUSD": {"last": 2.0, "volumeEur": 100_000},
            "tOKUSD": {"last": 5.0, "volumeEur": 300_000},
        }

    def test_filters_low_volume(self):
        picks = _filter_gemini_picks(["tLOWUSD", "tOKUSD"], self.analyses, self.tickers)
        self.assertEqual(picks, ["tOKUSD"])

    def test_entry_eligible_requires_min_volume(self):
        self.assertFalse(_entry_eligible_for_picks("tLOWUSD", self.analyses, self.tickers))
        self.assertTrue(_entry_eligible_for_picks("tOKUSD", self.analyses, self.tickers))
