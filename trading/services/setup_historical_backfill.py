"""
Historiallinen setup-oppiminen: simuloi osto → voitto-myynti / stop-loss 1h-kynttilöillä.

Täydentää live-kauppoja (learning.setup_memory). Historia painotetaan kevyemmin
kuin omat kaupat (SETUP_HIST_WEIGHT).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

from .ai_trader import dynamic_stop_pct
from .bitfinex import fetch_all_markets, fetch_candle_history, is_stablecoin
from .market_learning import setup_key_for_analysis
from .market_learning_backfill import (
    BACKFILL_CANDLE_LIMIT,
    BACKFILL_MIN_WINDOW,
    BACKFILL_STEP_HOURS,
    BACKFILL_TOP_SYMBOLS,
    BITFINEX_REQ_PAUSE_SEC,
    _analysis_at,
    _find_btc_symbol,
    _historical_regime,
    _index_by_timestamp,
    _pause,
    _volume_eur_24h,
)
from .market_learning import MIN_VOLUME_EUR
from .sell_strategy import (
    PARTIAL_TAKE_FRACTION,
    PARTIAL_TAKE_TRIGGER_PCT,
    ROUND_TRIP_COST_PCT,
    _pullback_threshold_pct,
    _trigger_pct,
    default_profit_take_config,
)

logger = logging.getLogger(__name__)

_setup_lock = threading.Lock()
_setup_running = False

SETUP_HIST_WEIGHT = float(os.environ.get("SETUP_HIST_WEIGHT", "0.3"))
SETUP_BACKFILL_INTERVAL_SEC = int(
    os.environ.get("SETUP_BACKFILL_INTERVAL_SEC", str(7 * 24 * 3600))
)
SETUP_MIN_ENTRY_SCORE = int(os.environ.get("SETUP_MIN_ENTRY_SCORE", "2"))
SETUP_MAX_HOLD_HOURS = int(os.environ.get("SETUP_MAX_HOLD_HOURS", "72"))
SETUP_NOMINAL_EUR = float(os.environ.get("SETUP_NOMINAL_EUR", "100"))

_DEFAULT: dict[str, Any] = {"stats": {}, "lastBackfillAt": 0}


def _load() -> dict[str, Any]:
    from trading.models import BotState

    obj, _ = BotState.objects.get_or_create(pk=3, defaults={"data": dict(_DEFAULT)})
    data = obj.data or {}
    data.setdefault("stats", {})
    data.setdefault("lastBackfillAt", 0)
    return data


def _save(data: dict[str, Any]) -> None:
    from trading.models import BotState

    BotState.objects.update_or_create(pk=3, defaults={"data": data})


def load_setup_stats() -> dict[str, dict[str, float]]:
    """Setup-kohtaiset historiatilastot: {setup: {n, net, wins}}."""
    return dict(_load().get("stats") or {})


def _regime_str(regime: str) -> str:
    return regime if regime in ("bull", "bear", "neutral") else "neutral"


def simulate_round_trip_pct(
    candles: list[dict[str, Any]],
    entry_idx: int,
    analysis: dict[str, Any],
    regime: str,
) -> float | None:
    """
    Simuloi yhden position poistumisen tunnin kynttilöillä.
    Käyttää samaa voitto-myynti- ja stop-loss-logiikkaa kuin live (hourly approksimaatio).
    """
    if entry_idx + 1 >= len(candles):
        return None

    entry = float(candles[entry_idx]["close"])
    if entry <= 0:
        return None

    stop_pct = dynamic_stop_pct(analysis, _regime_str(regime))
    atr_pct = analysis.get("atrPct")
    cfg = default_profit_take_config()
    trigger_pct = _trigger_pct(float(atr_pct or 0), cfg)
    pullback_thresh = _pullback_threshold_pct(float(atr_pct or 0), cfg)

    peak = entry
    peak_idx = entry_idx
    armed = False
    tier1_taken = False
    tier1_profit_pct = 0.0
    remaining_fraction = 1.0

    for offset in range(1, SETUP_MAX_HOLD_HOURS + 1):
        idx = entry_idx + offset
        if idx >= len(candles):
            break

        price = float(candles[idx]["close"])
        profit_pct = (price - entry) / entry * 100.0

        if profit_pct <= stop_pct:
            blended = (
                tier1_profit_pct * (1.0 - remaining_fraction)
                + profit_pct * remaining_fraction
            )
            return blended - ROUND_TRIP_COST_PCT

        if (
            not tier1_taken
            and profit_pct >= PARTIAL_TAKE_TRIGGER_PCT
            and profit_pct > ROUND_TRIP_COST_PCT
        ):
            tier1_taken = True
            sold = PARTIAL_TAKE_FRACTION
            tier1_profit_pct += profit_pct * sold
            remaining_fraction -= sold
            peak = price
            peak_idx = idx
            armed = False

        if profit_pct >= trigger_pct and remaining_fraction > 0:
            if price > peak:
                peak = price
                peak_idx = idx
                armed = False
            elif idx - peak_idx >= 1:
                armed = True

            pullback = ((peak - price) / peak * 100.0) if peak else 0.0
            if (
                armed
                and pullback >= pullback_thresh
                and profit_pct > ROUND_TRIP_COST_PCT
            ):
                blended = (
                    tier1_profit_pct
                    + profit_pct * remaining_fraction
                )
                return blended - ROUND_TRIP_COST_PCT

    last_idx = min(entry_idx + SETUP_MAX_HOLD_HOURS, len(candles) - 1)
    final_pct = (float(candles[last_idx]["close"]) - entry) / entry * 100.0
    blended = tier1_profit_pct + final_pct * remaining_fraction
    return blended - ROUND_TRIP_COST_PCT


def _record_stat(stats: dict[str, Any], setup: str, ret_pct: float) -> None:
    b = stats.setdefault(setup, {"n": 0.0, "net": 0.0, "wins": 0.0})
    eur = SETUP_NOMINAL_EUR * ret_pct / 100.0
    b["n"] += 1.0
    b["net"] += eur
    if eur > 0.01:
        b["wins"] += 1.0


def backfill_setup_symbol(
    candles: list[dict[str, Any]],
    btc_candles: list[dict[str, Any]],
    stats: dict[str, Any],
) -> int:
    if len(candles) < BACKFILL_MIN_WINDOW + 4:
        return 0

    btc_idx = _index_by_timestamp(btc_candles)
    added = 0
    last_end = len(candles) - SETUP_MAX_HOLD_HOURS - 1

    for idx in range(BACKFILL_MIN_WINDOW, last_end, BACKFILL_STEP_HOURS):
        vol = _volume_eur_24h(candles, idx)
        if vol < MIN_VOLUME_EUR:
            continue
        analysis = _analysis_at(candles, idx, vol)
        if not analysis:
            continue
        if int(analysis.get("score") or 0) < SETUP_MIN_ENTRY_SCORE:
            continue

        btc_i = btc_idx.get(int(candles[idx]["timestamp"]))
        regime = (
            _historical_regime(btc_candles, btc_i)
            if btc_i is not None
            else "neutral"
        )
        ret = simulate_round_trip_pct(candles, idx, analysis, regime)
        if ret is None:
            continue

        setup = setup_key_for_analysis(analysis, regime)
        _record_stat(stats, setup, ret)
        added += 1

    return added


def run_setup_historical_backfill(
    symbols: list[str] | None = None,
    *,
    candle_limit: int | None = None,
) -> dict[str, Any]:
    limit = candle_limit or BACKFILL_CANDLE_LIMIT
    store = _load()
    stats: dict[str, Any] = dict(store.get("stats") or {})

    tickers, _ = fetch_all_markets()
    if not symbols:
        ranked = sorted(
            [s for s in tickers if not is_stablecoin(s)],
            key=lambda s: tickers[s].get("volumeEur", 0),
            reverse=True,
        )
        symbols = ranked[:BACKFILL_TOP_SYMBOLS]

    btc_sym = _find_btc_symbol(tickers)
    btc_candles: list[dict[str, Any]] = []
    if btc_sym:
        btc_candles = fetch_candle_history(btc_sym, "1h", limit=limit)
        _pause()

    per_symbol: dict[str, int] = {}
    errors: list[str] = []

    for sym in symbols:
        if is_stablecoin(sym):
            continue
        try:
            candles = fetch_candle_history(sym, "1h", limit=limit)
            _pause()
            if not candles:
                errors.append(f"{sym}: ei kynttilöitä")
                continue
            n = backfill_setup_symbol(candles, btc_candles, stats)
            per_symbol[sym] = n
            logger.info("Setup history backfill %s: %d round-trips", sym, n)
        except Exception as exc:
            logger.warning("Setup history backfill failed for %s: %s", sym, exc)
            errors.append(f"{sym}: {exc}")

    now_ms = int(time.time() * 1000)
    store["stats"] = stats
    store["lastBackfillAt"] = now_ms
    setups_ready = sum(1 for b in stats.values() if b.get("n", 0) >= 4)
    store["lastSummary"] = {
        "at": now_ms,
        "symbols": len(per_symbol),
        "roundTrips": sum(per_symbol.values()),
        "setupsTracked": len(stats),
        "setupsReady": setups_ready,
        "candleLimit": limit,
        "perSymbol": per_symbol,
        "errors": errors[:10],
    }
    _save(store)
    return store["lastSummary"]


def maybe_schedule_setup_historical_backfill(force: bool = False) -> bool:
    global _setup_running

    store = _load()
    last = int(store.get("lastBackfillAt") or 0)
    now_ms = int(time.time() * 1000)
    if not force and last and (now_ms - last) < SETUP_BACKFILL_INTERVAL_SEC * 1000:
        return False

    with _setup_lock:
        if _setup_running:
            return False
        _setup_running = True

    def _worker() -> None:
        global _setup_running
        try:
            result = run_setup_historical_backfill()
            logger.info(
                "Setup history backfill done: %d round-trips, %d setups",
                result.get("roundTrips", 0),
                result.get("setupsTracked", 0),
            )
        except Exception:
            logger.exception("Setup history backfill failed")
        finally:
            with _setup_lock:
                _setup_running = False

    threading.Thread(target=_worker, name="setup-history-backfill", daemon=True).start()
    return True


def get_setup_backfill_status() -> dict[str, Any]:
    store = _load()
    summary = store.get("lastSummary") or {}
    last = int(store.get("lastBackfillAt") or 0)
    now_ms = int(time.time() * 1000)
    return {
        "setupHistoryWeight": SETUP_HIST_WEIGHT,
        "lastSetupBackfillAt": last,
        "lastSetupBackfillAgeSec": int((now_ms - last) / 1000) if last else None,
        "setupBackfillRunning": _setup_running,
        "setupHistoryRoundTrips": summary.get("roundTrips"),
        "setupHistorySetupsReady": summary.get("setupsReady"),
        "setupHistorySetupsTracked": summary.get("setupsTracked"),
    }
