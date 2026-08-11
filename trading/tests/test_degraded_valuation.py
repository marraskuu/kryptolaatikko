"""Portfolio valuation must not drop holdings to zero when a live ticker is missing."""

from django.test import SimpleTestCase

from trading.services.daily_policy_shadow import (
    day_key_utc,
    default_shadow_state,
    fork_shadow_portfolio,
    record_cycle,
)
from trading.services.portfolio import Portfolio, default_portfolio


def _portfolio_with_missing_ticker_holding() -> dict:
    portfolio = default_portfolio()
    portfolio["cash"] = 100.0
    portfolio["holdings"] = {
        "tBTCUSD": {
            "amount": 0.015,
            "avgPrice": 60_000.0,
        }
    }
    return portfolio


class DegradedPortfolioValuationTests(SimpleTestCase):
    def test_total_value_uses_avg_price_when_ticker_missing(self):
        portfolio = Portfolio(_portfolio_with_missing_ticker_holding())

        self.assertEqual(portfolio.get_total_value({}), 1_000.0)

    def test_missing_ticker_does_not_trigger_false_daily_stop(self):
        portfolio = _portfolio_with_missing_ticker_holding()
        total_value = Portfolio(portfolio).get_total_value({})
        shadow = default_shadow_state()
        shadow["dayKey"] = day_key_utc()
        shadow["dayStartValue"] = 1_000.0
        state = {
            "portfolio": portfolio,
            "tickers": {},
            "dailyPolicyShadow": shadow,
        }

        flags = record_cycle(state, total_value=total_value, regime="neutral", learning={})

        self.assertFalse(flags["dailyStopActive"])
        self.assertEqual(state["dailyPolicyShadow"]["today"]["realPnlPct"], 0.0)

    def test_shadow_portfolio_start_value_uses_degraded_holding_value(self):
        state = {
            "portfolio": _portfolio_with_missing_ticker_holding(),
            "tickers": {},
            "dailyPolicyShadow": default_shadow_state(),
        }

        fork_shadow_portfolio(state)

        self.assertEqual(state["dailyPolicyShadow"]["shadowDayStartValue"], 1_000.0)
