"""Gemini-kertomuksen virheen jälkeinen uudelleenyritys."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from django.test import SimpleTestCase, TransactionTestCase

from trading.models import BotState
from trading.services.learning_report import (
    NARRATIVE_ERROR_RETRY_SEC,
    _merge_cached_learning_report,
    _narrative_error_retry_due,
    _next_narrative_error_retry_sec,
    _run_narrative_refresh,
    ensure_narrative_error_state,
    needs_narrative_refresh,
)
from trading.services.session_state import default_state


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
        at_ms = int(
            datetime.fromisoformat(state["learningNarrativeErrorAt"].replace("Z", "+00:00")).timestamp()
        )
        self.assertLess(at_ms, int(datetime.now(timezone.utc).timestamp()) - 3000)

    def test_ensure_inferred_timestamp_allows_immediate_retry(self):
        state = {
            "learningReport": {
                "narrativeError": "Gemini overload",
                "sections": [],
                "timestamp": _iso_ago(NARRATIVE_ERROR_RETRY_SEC + 60),
            }
        }
        ensure_narrative_error_state(state)
        self.assertTrue(_narrative_error_retry_due(state, state["learningReport"]))

    def test_infer_uses_report_timestamp_not_pending(self):
        state = {
            "learningNarrativePendingSince": _iso_ago(5),
            "learningReport": {
                "narrativeError": "fail",
                "sections": [],
                "timestamp": _iso_ago(NARRATIVE_ERROR_RETRY_SEC + 30),
            },
        }
        ensure_narrative_error_state(state)
        self.assertTrue(_narrative_error_retry_due(state, state["learningReport"]))

    def test_merge_clears_error_when_story_present(self):
        state = {
            "learningNarrative": {"story": "Tarina valmis"},
            "lastLearningNarrativeAt": _iso_ago(120),
            "learningNarrativeError": "old fail",
            "learningReport": {
                "narrativeError": "old fail",
                "sections": [],
                "timestamp": _iso_ago(120),
            },
        }
        report = _merge_cached_learning_report(state, dict(state["learningReport"]))
        self.assertNotIn("narrativeError", report)
        self.assertGreaterEqual(report["nextNarrativeInSec"], 0)

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


class LearningNarrativePersistenceTests(TransactionTestCase):
    def setUp(self):
        BotState.objects.update_or_create(pk=1, defaults={"data": default_state()})

    def tearDown(self):
        BotState.objects.filter(pk=1).update(data=default_state())

    def test_success_patches_narrative_without_full_state_save(self):
        state = default_state()
        state["lastPriceTick"] = 2_000
        state["tickers"] = {"tBTCUSD": {"last": 125.0}}
        state["analyses"] = {"tBTCUSD": {"price": 125.0}}
        state["learningNarrativePendingSince"] = _iso_ago(5)
        state["learningReport"] = {
            "sections": [{"title": "fresh rules"}],
            "timestamp": "fresh-ts",
            "changes": {"fresh": True},
            "narrativePending": True,
        }
        BotState.objects.update_or_create(pk=1, defaults={"data": state})

        fallback_report = {
            "sections": [{"title": "stale rules"}],
            "timestamp": "stale-ts",
            "changes": {"stale": True},
            "snapshot": {"stale": True},
            "narrativePending": True,
        }
        with (
            patch(
                "trading.services.gemini.generate_learning_narrative",
                return_value=({"story": "Kertomus valmis"}, {"ok": True}),
            ),
            patch("trading.services.state_store.save_state") as save_state_mock,
        ):
            _run_narrative_refresh(dict(state), fallback_report)

        save_state_mock.assert_not_called()
        final = BotState.objects.get(pk=1).data
        self.assertEqual(final["lastPriceTick"], 2_000)
        self.assertEqual(final["tickers"], {"tBTCUSD": {"last": 125.0}})
        self.assertEqual(final["analyses"], {"tBTCUSD": {"price": 125.0}})
        self.assertNotIn("learningNarrativePendingSince", final)
        self.assertEqual(final["learningNarrative"], {"story": "Kertomus valmis"})
        self.assertEqual(final["learningReportHistory"][0]["changes"], {"stale": True})
        report = final["learningReport"]
        self.assertEqual(report["sections"], [{"title": "fresh rules"}])
        self.assertEqual(report["timestamp"], "fresh-ts")
        self.assertEqual(report["changes"], {"fresh": True})
        self.assertEqual(report["narrative"], {"story": "Kertomus valmis"})
        self.assertFalse(report["narrativePending"])
        self.assertNotIn("narrativeError", report)

    def test_error_patches_narrative_error_without_full_state_save(self):
        state = default_state()
        state["lastPriceTick"] = 2_000
        state["tickers"] = {"tBTCUSD": {"last": 125.0}}
        state["learningNarrativePendingSince"] = _iso_ago(5)
        state["learningReport"] = {
            "sections": [{"title": "fresh rules"}],
            "timestamp": "fresh-ts",
            "changes": {"fresh": True},
            "narrativePending": True,
        }
        BotState.objects.update_or_create(pk=1, defaults={"data": state})

        with (
            patch(
                "trading.services.gemini.generate_learning_narrative",
                return_value=(None, {"ok": False, "message": "Gemini down"}),
            ),
            patch("trading.services.state_store.save_state") as save_state_mock,
        ):
            _run_narrative_refresh(dict(state), {"sections": [{"title": "stale rules"}]})

        save_state_mock.assert_not_called()
        final = BotState.objects.get(pk=1).data
        self.assertEqual(final["lastPriceTick"], 2_000)
        self.assertEqual(final["tickers"], {"tBTCUSD": {"last": 125.0}})
        self.assertNotIn("learningNarrativePendingSince", final)
        self.assertEqual(final["learningNarrativeError"], "Gemini down")
        report = final["learningReport"]
        self.assertEqual(report["sections"], [{"title": "fresh rules"}])
        self.assertEqual(report["timestamp"], "fresh-ts")
        self.assertEqual(report["changes"], {"fresh": True})
        self.assertEqual(report["narrativeError"], "Gemini down")
        self.assertFalse(report["narrativePending"])
