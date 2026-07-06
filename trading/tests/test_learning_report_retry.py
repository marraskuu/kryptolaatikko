"""Gemini-kertomuksen virheen jälkeinen uudelleenyritys."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from django.test import SimpleTestCase

from trading.services.learning_report import (
    NARRATIVE_ERROR_RETRY_SEC,
    _merge_cached_learning_report,
    _narrative_error_retry_due,
    _next_narrative_error_retry_sec,
    ensure_narrative_error_state,
    needs_narrative_refresh,
)


def _iso_ago(seconds: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


class LearningReportRetryTests(SimpleTestCase):
    def test_ensure_syncs_error_from_report_and_sets_timestamp(self):
        state = {
            "learningReport": {
                "narrativeError": "Gemini overload",
                "sections": [],
                "timestamp": _iso_ago(3600),
            }
        }
        self.assertTrue(ensure_narrative_error_state(state))
        self.assertEqual(state["learningNarrativeError"], "Gemini overload")
        self.assertTrue(state.get("learningNarrativeErrorAt"))

    def test_retry_due_after_cooldown(self):
        state = {
            "learningNarrativeError": "fail",
            "learningNarrativeErrorAt": _iso_ago(NARRATIVE_ERROR_RETRY_SEC + 5),
            "learningReport": {"sections": [], "timestamp": _iso_ago(3600)},
        }
        self.assertTrue(_narrative_error_retry_due(state, state["learningReport"]))

    def test_retry_not_due_during_cooldown(self):
        state = {
            "learningNarrativeError": "fail",
            "learningNarrativeErrorAt": _iso_ago(120),
            "learningReport": {"sections": [], "timestamp": _iso_ago(3600)},
        }
        self.assertFalse(_narrative_error_retry_due(state, state["learningReport"]))

    def test_merge_recomputes_retry_countdown(self):
        state = {
            "learningNarrativeError": "fail",
            "learningNarrativeErrorAt": _iso_ago(120),
            "learningReport": {
                "narrativeError": "fail",
                "sections": [],
                "timestamp": _iso_ago(3600),
            },
        }
        report = _merge_cached_learning_report(state, dict(state["learningReport"]))
        remaining = _next_narrative_error_retry_sec(state)
        self.assertGreater(report["nextNarrativeInSec"], 0)
        self.assertLessEqual(report["nextNarrativeInSec"], remaining)
        self.assertGreater(report["nextNarrativeInSec"], NARRATIVE_ERROR_RETRY_SEC - 130)

    def test_needs_refresh_prioritizes_error_retry_over_young_pending(self):
        state = {
            "learningNarrativeError": "fail",
            "learningNarrativeErrorAt": _iso_ago(NARRATIVE_ERROR_RETRY_SEC + 1),
            "learningNarrativePendingSince": _iso_ago(30),
            "learningReport": {
                "narrativePending": True,
                "narrativeError": "fail",
                "sections": [],
                "timestamp": _iso_ago(3600),
            },
        }
        with patch("trading.services.gemini.is_configured", return_value=True):
            self.assertTrue(needs_narrative_refresh(state))
