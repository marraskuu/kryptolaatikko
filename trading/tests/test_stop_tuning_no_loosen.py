"""Stop-tuning ei saa löysentää stoppeja (bleed-pack 2026-08-05)."""

from django.test import SimpleTestCase

from trading.services.learning import (
    MIN_SAMPLES_STOP_FULL,
    MIN_SAMPLES_STOP_LIGHT,
    _compute_stop_tuning,
)


class StopTuningNoLoosenTests(SimpleTestCase):
    def test_mild_negative_expectancy_does_not_loosen(self):
        stats = {
            "stop_loss": {
                "trades": MIN_SAMPLES_STOP_LIGHT,
                "expectancy_eur": -0.10,
            }
        }
        cfg, notes = _compute_stop_tuning(stats)
        self.assertEqual(cfg["atr_scale"], 1.0)
        self.assertEqual(cfg["floor_scale"], 1.0)
        self.assertEqual(cfg["cap_scale"], 1.0)
        self.assertFalse(any("löysempi" in n for n in notes))

    def test_bad_expectancy_still_tightens(self):
        stats = {
            "stop_loss": {
                "trades": MIN_SAMPLES_STOP_LIGHT,
                "expectancy_eur": -1.5,
            }
        }
        cfg, notes = _compute_stop_tuning(stats)
        self.assertLess(cfg["atr_scale"], 1.0)
        self.assertTrue(any("tiukempi" in n for n in notes))

    def test_full_sample_mild_still_no_loosen(self):
        stats = {
            "stop_loss": {
                "trades": MIN_SAMPLES_STOP_FULL,
                "expectancy_eur": -0.10,
            }
        }
        cfg, _ = _compute_stop_tuning(stats)
        self.assertEqual(cfg["atr_scale"], 1.0)
        self.assertEqual(cfg["level"], "full")
