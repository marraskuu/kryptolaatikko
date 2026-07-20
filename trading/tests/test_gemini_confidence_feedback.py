"""Gemini näkee oman confidence- ja pick-valinnan track recordin promptissa."""

from django.test import TestCase

from trading.services.gemini import _compact_learning


class CompactLearningConfidenceFeedbackTests(TestCase):
    def test_includes_confidence_and_pick_track_record(self):
        learning = {
            "gemini_confidence_stats": {
                7: {"trades": 464, "net_eur": 82.95, "win_rate": 0.45, "expectancy_eur": 0.179},
                9: {"trades": 65, "net_eur": -71.34, "win_rate": 0.35, "expectancy_eur": -1.098},
            },
            "gemini_pick_stats": {
                "rounds": 40,
                "win_rate_pct": 25.8,
                "pick_beats_skipped_pct": 12.5,
            },
        }
        compact = _compact_learning(learning)
        self.assertEqual(compact["confidence_track_record"], learning["gemini_confidence_stats"])
        self.assertEqual(compact["pick_track_record"], learning["gemini_pick_stats"])

    def test_missing_stats_do_not_crash(self):
        compact = _compact_learning({"note": "test"})
        self.assertIsNone(compact["confidence_track_record"])
        self.assertIsNone(compact["pick_track_record"])

    def test_empty_learning_returns_empty_dict(self):
        self.assertEqual(_compact_learning(None), {})
        self.assertEqual(_compact_learning({}), {})
