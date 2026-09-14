"""
BTC N-day trend gate — block new buys when BTC momentum is not positive.

Live 94d evidence: bot −€154 while BTC HODL +24%; bear sells −€423.
External long-only playbooks (cash when BTC momentum ≤ 0) match that split.
Cache on bot state to avoid candle rate limits.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

BTC_TREND_GATE_ENABLED = os.environ.get("BTC_TREND_GATE_ENABLED", "1").lower() not in (
    "0",
    "false",
    "no",
    "off",
)
BTC_TREND_LOOKBACK_DAYS = int(os.environ.get("BTC_TREND_LOOKBACK_DAYS", "21"))
BTC_TREND_MIN_PCT = float(os.environ.get("BTC_TREND_MIN_PCT", "0.0"))
BTC_TREND_SYMBOL = os.environ.get("BTC_TREND_SYMBOL", "tBTCUSD")
BTC_TREND_CACHE_SEC = int(os.environ.get("BTC_TREND_CACHE_SEC", "3600"))


def compute_btc_trend_pct(
    candles: list[dict[str, Any]],
    lookback_days: int = BTC_TREND_LOOKBACK_DAYS,
) -> float | None:
    """Return % change from close lookback_days ago to latest close."""
    if not candles or lookback_days < 1:
        return None
    ordered = sorted(candles, key=lambda c: int(c.get("timestamp") or 0))
    if len(ordered) < lookback_days + 1:
        # allow slightly short history: use oldest vs newest
        if len(ordered) < 2:
            return None
        p0 = float(ordered[0].get("close") or 0)
        p1 = float(ordered[-1].get("close") or 0)
    else:
        p0 = float(ordered[-(lookback_days + 1)].get("close") or 0)
        p1 = float(ordered[-1].get("close") or 0)
    if p0 <= 0 or p1 <= 0:
        return None
    return ((p1 - p0) / p0) * 100.0


def btc_trend_blocks_buy(trend: dict[str, Any] | None) -> bool:
    if not BTC_TREND_GATE_ENABLED:
        return False
    if not isinstance(trend, dict):
        return False
    # Fail-open only when never measured — once we have a reading, enforce it.
    if not trend.get("ok"):
        return False
    pct = trend.get("changePct")
    if pct is None:
        return False
    try:
        return float(pct) <= BTC_TREND_MIN_PCT
    except (TypeError, ValueError):
        return False


def _cached_trend_on_refresh_error(
    cached: dict[str, Any] | None,
    now: float,
) -> dict[str, Any] | None:
    """Use the last valid BTC trend as the live gate when refresh fails."""
    if not isinstance(cached, dict) or not cached.get("ok"):
        return None
    if cached.get("changePct") is None:
        return None

    trend = dict(cached)
    trend["blocksBuy"] = btc_trend_blocks_buy(trend)
    trend["stale"] = True
    trend["error"] = True
    trend["lastRefreshErrorAt"] = now
    return trend


def refresh_btc_trend(state: dict[str, Any]) -> dict[str, Any]:
    """Update state['btcTrend'] at most once per BTC_TREND_CACHE_SEC."""
    now = time.time()
    cached = state.get("btcTrend")
    if (
        isinstance(cached, dict)
        and cached.get("ok")
        and (now - float(cached.get("fetchedAt") or 0)) < BTC_TREND_CACHE_SEC
    ):
        return cached

    from .bitfinex import fetch_candles

    lookback = max(5, BTC_TREND_LOOKBACK_DAYS)
    try:
        candles = fetch_candles(
            BTC_TREND_SYMBOL,
            timeframe="1D",
            limit=lookback + 5,
        )
        pct = compute_btc_trend_pct(candles, BTC_TREND_LOOKBACK_DAYS)
        trend = {
            "ok": pct is not None,
            "changePct": round(pct, 3) if pct is not None else None,
            "lookbackDays": BTC_TREND_LOOKBACK_DAYS,
            "minPct": BTC_TREND_MIN_PCT,
            "symbol": BTC_TREND_SYMBOL,
            "blocksBuy": bool(
                pct is not None and float(pct) <= BTC_TREND_MIN_PCT and BTC_TREND_GATE_ENABLED
            ),
            "fetchedAt": now,
            "nCandles": len(candles or []),
        }
    except Exception:
        logger.warning("BTC trend refresh failed", exc_info=True)
        trend = _cached_trend_on_refresh_error(cached, now)
        if trend is None:
            trend = {
                "ok": False,
                "changePct": None,
                "lookbackDays": BTC_TREND_LOOKBACK_DAYS,
                "minPct": BTC_TREND_MIN_PCT,
                "symbol": BTC_TREND_SYMBOL,
                "blocksBuy": False,
                "fetchedAt": now,
                "error": True,
            }

    state["btcTrend"] = trend
    return trend


def attach_btc_trend_to_regime(
    regime_info: dict[str, Any],
    trend: dict[str, Any] | None,
) -> dict[str, Any]:
    """Copy gate fields onto regime dict for buy blockers / UI."""
    out = dict(regime_info or {})
    if not isinstance(trend, dict):
        return out
    out["btc_trend_pct"] = trend.get("changePct")
    out["btc_trend_blocks_buy"] = bool(trend.get("blocksBuy"))
    out["btc_trend_lookback_days"] = trend.get("lookbackDays")
    return out
