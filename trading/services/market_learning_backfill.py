"""
Historiallinen varjo-oppiminen Bitfinex-kynttilöistä.

Täyttää market_learning-ämpärit nopeasti ilman viikkoja live-näytteitä.
Ajetaan taustalla viikon välein (tai scripts/historical_learning_backfill.py).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

from .ai_trader import _mtf_alignment, analyze_market, calc_period_change_pct
from .bitfinex import fetch_all_markets, fetch_candle_history, is_stablecoin
from .market_learning import (
    HORIZONS,
    MIN_VOLUME_EUR,
    ROUND_TRIP_COST_PCT,
    _bucket_key,
    _load,
    _save,
    _summary,
    _update_stat,
)

logger = logging.getLogger(__name__)

_backfill_lock = threading.Lock()
_backfill_running = False

HISTORY_BACKFILL_INTERVAL_SEC = int(
    os.environ.get("HISTORY_BACKFILL_INTERVAL_SEC", str(7 * 24 * 3600))
)
BACKFILL_CANDLE_LIMIT = int(os.environ.get("BACKFILL_CANDLE_LIMIT", "3000"))
BACKFILL_TOP_SYMBOLS = int(os.environ.get("BACKFILL_TOP_SYMBOLS", "20"))
BACKFILL_STEP_HOURS = int(os.environ.get("BACKFILL_STEP_HOURS", "12"))
BACKFILL_MIN_WINDOW = 30
BITFINEX_REQ_PAUSE_SEC = float(os.environ.get("BITFINEX_REQ_PAUSE_SEC", "2.1"))
BTC_SYMBOL_CANDIDATES = ("tBTCUSD", "tBTCUST", "tBTCEUR")


def _pause() -> None:
    time.sleep(BITFINEX_REQ_PAUSE_SEC)


def _find_btc_symbol(tickers: dict[str, dict[str, Any]]) -> str | None:
    for sym in BTC_SYMBOL_CANDIDATES:
        if sym in tickers:
            return sym
    for sym in tickers:
        if sym.upper().startswith("TBTC"):
            return sym
    return None


def _volume_eur_24h(candles: list[dict[str, Any]], idx: int) -> float:
    start = max(0, idx - 23)
    window = candles[start : idx + 1]
    return sum(float(c.get("volume") or 0) * float(c.get("close") or 0) for c in window)


def _historical_regime(btc_candles: list[dict[str, Any]], idx: int) -> str:
    if idx < 24 or idx >= len(btc_candles):
        return "neutral"
    window = btc_candles[max(0, idx - 24) : idx + 1]
    closes = [float(c["close"]) for c in window]
    change_24h = calc_period_change_pct(closes, 24) or 0.0
    if change_24h < -1.5:
        return "bear"
    if change_24h > 1.0:
        return "bull"
    return "neutral"


def _analysis_at(
    candles: list[dict[str, Any]],
    idx: int,
    volume_eur: float,
) -> dict[str, Any] | None:
    start = max(0, idx - (BACKFILL_MIN_WINDOW - 1))
    window = candles[start : idx + 1]
    if len(window) < BACKFILL_MIN_WINDOW:
        return None

    closes = [float(c["close"]) for c in window]
    change_24h = calc_period_change_pct(closes, 24)
    if change_24h is None and len(closes) >= 2:
        change_24h = calc_period_change_pct(closes, len(closes) - 1)
    change_1h = calc_period_change_pct(closes, 1)
    change_4h = calc_period_change_pct(closes, 4)

    analysis = analyze_market(window)
    analysis["changePct"] = float(change_24h or 0)
    if change_1h is not None:
        analysis["change1hPct"] = change_1h
    if change_4h is not None:
        analysis["change4hPct"] = change_4h
    analysis["mtfAlign"] = _mtf_alignment(
        change_1h, change_4h, float(change_24h or 0)
    )
    analysis["volumeEur"] = volume_eur
    analysis["currentPrice"] = closes[-1]
    analysis["quick"] = False
    return analysis


def _forward_return(candles: list[dict[str, Any]], idx: int, hours: int) -> float | None:
    steps = hours  # 1h candles
    if idx + steps >= len(candles):
        return None
    p0 = float(candles[idx]["close"])
    p1 = float(candles[idx + steps]["close"])
    if p0 <= 0:
        return None
    return (p1 - p0) / p0 * 100.0 - ROUND_TRIP_COST_PCT


def _index_by_timestamp(candles: list[dict[str, Any]]) -> dict[int, int]:
    return {int(c["timestamp"]): i for i, c in enumerate(candles)}


def backfill_symbol(
    symbol: str,
    candles: list[dict[str, Any]],
    btc_candles: list[dict[str, Any]],
    stats: dict[str, Any],
) -> int:
    """Lisää historialliset havainnot stats-ämpäreihin. Palauttaa lisättyjen näytteiden määrän."""
    if len(candles) < BACKFILL_MIN_WINDOW + 4:
        return 0

    btc_idx = _index_by_timestamp(btc_candles)
    added = 0
    last_end = len(candles) - 4

    for idx in range(BACKFILL_MIN_WINDOW, last_end, BACKFILL_STEP_HOURS):
        vol = _volume_eur_24h(candles, idx)
        if vol < MIN_VOLUME_EUR:
            continue

        analysis = _analysis_at(candles, idx, vol)
        if not analysis:
            continue

        btc_i = btc_idx.get(int(candles[idx]["timestamp"]))
        regime = (
            _historical_regime(btc_candles, btc_i)
            if btc_i is not None
            else "neutral"
        )
        key = _bucket_key(analysis, regime)

        for hz, sec in HORIZONS.items():
            steps = sec // 3600
            ret = _forward_return(candles, idx, steps)
            if ret is not None:
                _update_stat(stats, key, hz, ret)

        added += 1

    return added


def run_historical_backfill(
    symbols: list[str] | None = None,
    *,
    candle_limit: int | None = None,
) -> dict[str, Any]:
    """Hae kynttilähistoria ja täytä varjo-oppimisen stats."""
    limit = candle_limit or BACKFILL_CANDLE_LIMIT
    store = _load()
    stats: dict[str, Any] = store.get("stats") or {}

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
            n = backfill_symbol(sym, candles, btc_candles, stats)
            per_symbol[sym] = n
            logger.info("History backfill %s: %d samples", sym, n)
        except Exception as exc:
            logger.warning("History backfill failed for %s: %s", sym, exc)
            errors.append(f"{sym}: {exc}")

    now_ms = int(time.time() * 1000)
    store["stats"] = stats
    store["lastHistoryBackfillAt"] = now_ms
    summary = _summary(stats)
    store["lastHistoryBackfillSummary"] = {
        "at": now_ms,
        "symbols": len(per_symbol),
        "samplesAdded": sum(per_symbol.values()),
        "candleLimit": limit,
        "perSymbol": per_symbol,
        "errors": errors[:10],
        **summary,
    }
    _save(store)

    return store["lastHistoryBackfillSummary"]


def maybe_schedule_historical_backfill(force: bool = False) -> bool:
    """Käynnistä taustalokiikka jos viikkobackfill erääntynyt."""
    global _backfill_running

    store = _load()
    last = int(store.get("lastHistoryBackfillAt") or 0)
    now_ms = int(time.time() * 1000)
    due = force or not last or (now_ms - last) >= HISTORY_BACKFILL_INTERVAL_SEC * 1000
    if not due:
        return False

    with _backfill_lock:
        if _backfill_running:
            return False
        _backfill_running = True

    def _worker() -> None:
        global _backfill_running
        try:
            result = run_historical_backfill()
            logger.info(
                "Scheduled history backfill done: %d samples, %d buckets learned",
                result.get("samplesAdded", 0),
                result.get("bucketsLearned", 0),
            )
        except Exception:
            logger.exception("Scheduled history backfill failed")
        finally:
            with _backfill_lock:
                _backfill_running = False

    threading.Thread(target=_worker, name="ml-history-backfill", daemon=True).start()
    return True


def get_backfill_status() -> dict[str, Any]:
    """Viimeisin historiabackfill-tila UI/state-yhteenvetoa varten."""
    store = _load()
    summary = store.get("lastHistoryBackfillSummary") or {}
    last = int(store.get("lastHistoryBackfillAt") or 0)
    now_ms = int(time.time() * 1000)
    return {
        "lastHistoryBackfillAt": last,
        "lastHistoryBackfillAgeSec": int((now_ms - last) / 1000) if last else None,
        "historyBackfillRunning": _backfill_running,
        "historySamplesAdded": summary.get("samplesAdded"),
        "historyBucketsLearned": summary.get("bucketsLearned"),
        "historyBucketsTracked": summary.get("bucketsTracked"),
    }
