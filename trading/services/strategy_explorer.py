"""
Strategy Explorer — vierailijalle näkyvä backtest-työkalu.

Valitse krypto ja aikaväli, ja aja botin OIKEA osto-pisteytys (ai_trader.analyze_market)
sekä voitto-myynti/stop-loss-logiikka (sell_strategy + setup_historical_backfill:n
simulointi) kynttilähistoriaan. Tulos: kauppalista + equity-käyrä.

Yksinkertaistus: yksi krypto kerrallaan, yksi positio kerrallaan (ei rinnakkaisia
positioita, ei Gemini-tarkistuksia, ei koko markkinan rotaatiota) — oikea botti
hajauttaa kymmeniin pareihin ja käyttää myös Geminiä. Käyttää silti samaa
pisteytystä ja poistumissääntöjä kuin live-botti ja taustalla ajettava
setup_historical_backfill-oppiminen.
"""

from __future__ import annotations

import time
from typing import Any

from .ai_trader import dynamic_stop_pct
from .bitfinex import (
    CANDLES_MAX_LIMIT,
    fetch_candles,
    is_stablecoin,
    is_valid_trading_symbol,
)
from .market_learning import MIN_VOLUME_EUR, setup_key_for_analysis
from .market_learning_backfill import (
    BACKFILL_MIN_WINDOW,
    _analysis_at,
    _historical_regime,
    _index_by_timestamp,
    _volume_eur_24h,
)
from .sell_strategy import (
    PARTIAL_TAKE_FRACTION,
    PARTIAL_TAKE_TRIGGER_PCT,
    ROUND_TRIP_COST_PCT,
    _pullback_threshold_pct,
    _trigger_pct,
    default_profit_take_config,
)
from .setup_historical_backfill import (
    SETUP_MAX_HOLD_HOURS,
    SETUP_MIN_ENTRY_SCORE,
)

EXPLORER_START_BALANCE_EUR = 1000.0
EXPLORER_MAX_DAYS = 400
BTC_REFERENCE_SYMBOL = "tBTCUSD"
_QUOTE_CANDIDATES = ("USD", "UST", "EUR")


def normalize_base_symbol(raw: str) -> str:
    """"BTC", "tBTCUSD" tai "btcusd" -> "BTC"."""
    s = (raw or "").strip().upper()
    if s.startswith("T") and len(s) > 1:
        s = s[1:]
    for quote in _QUOTE_CANDIDATES:
        if s.endswith(quote) and len(s) > len(quote):
            s = s[: -len(quote)]
            break
    return s


def _regime_str(regime: str) -> str:
    return regime if regime in ("bull", "bear", "neutral") else "neutral"


def _resolve_symbol_candles(
    base: str, *, limit: int, start_ms: int, end_ms: int
) -> tuple[str | None, list[dict[str, Any]]]:
    for quote in _QUOTE_CANDIDATES:
        symbol = f"t{base}{quote}"
        if not is_valid_trading_symbol(symbol) or is_stablecoin(symbol):
            continue
        candles = fetch_candles(symbol, "1h", limit=limit, start=start_ms, end=end_ms)
        if candles:
            return symbol, candles
    return None, []


def _simulate_trade(
    candles: list[dict[str, Any]],
    entry_idx: int,
    analysis: dict[str, Any],
    regime: str,
) -> dict[str, Any] | None:
    """Sama logiikka kuin setup_historical_backfill.simulate_round_trip_pct,
    mutta palauttaa myös poistumisindeksin, jotta kutsuja voi rakentaa
    kronologisen equity-käyrän eikä vain kerätä tilastoja."""
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
            return {
                "exitIdx": idx,
                "returnPct": blended - ROUND_TRIP_COST_PCT,
                "reason": "stop",
            }

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
                blended = tier1_profit_pct + profit_pct * remaining_fraction
                return {
                    "exitIdx": idx,
                    "returnPct": blended - ROUND_TRIP_COST_PCT,
                    "reason": "profit_take",
                }

    last_idx = min(entry_idx + SETUP_MAX_HOLD_HOURS, len(candles) - 1)
    final_pct = (float(candles[last_idx]["close"]) - entry) / entry * 100.0
    blended = tier1_profit_pct + final_pct * remaining_fraction
    reason = "time_limit" if last_idx == entry_idx + SETUP_MAX_HOLD_HOURS else "data_end"
    return {"exitIdx": last_idx, "returnPct": blended - ROUND_TRIP_COST_PCT, "reason": reason}


def run_explorer_backtest(symbol_input: str, start_ms: int, end_ms: int) -> dict[str, Any]:
    base = normalize_base_symbol(symbol_input)
    if not base:
        return {"error": "invalid_symbol"}

    pad_before_ms = (BACKFILL_MIN_WINDOW + 2) * 3_600_000
    pad_after_ms = (SETUP_MAX_HOLD_HOURS + 4) * 3_600_000
    now_ms = int(time.time() * 1000)
    fetch_start = start_ms - pad_before_ms
    fetch_end = min(end_ms + pad_after_ms, now_ms)
    hours_span = int((fetch_end - fetch_start) / 3_600_000) + 4
    limit = min(CANDLES_MAX_LIMIT, max(hours_span, BACKFILL_MIN_WINDOW + 8))

    symbol, candles = _resolve_symbol_candles(
        base, limit=limit, start_ms=fetch_start, end_ms=fetch_end
    )
    if not symbol or len(candles) < BACKFILL_MIN_WINDOW + 8:
        return {"error": "no_candles"}

    if symbol == BTC_REFERENCE_SYMBOL:
        btc_candles = candles
    else:
        _, btc_candles = _resolve_symbol_candles(
            "BTC", limit=limit, start_ms=fetch_start, end_ms=fetch_end
        )
    btc_idx_by_ts = _index_by_timestamp(btc_candles) if btc_candles else {}

    balance = EXPLORER_START_BALANCE_EUR
    n = len(candles)
    equity_curve: list[dict[str, Any]] = [
        {"t": int(candles[0]["timestamp"]), "equity": round(balance, 2)}
    ]
    trades: list[dict[str, Any]] = []

    idx = BACKFILL_MIN_WINDOW
    while idx < n:
        ts = int(candles[idx]["timestamp"])
        if ts < start_ms:
            idx += 1
            continue
        if ts > end_ms:
            break

        vol = _volume_eur_24h(candles, idx)
        if vol < MIN_VOLUME_EUR:
            idx += 1
            continue

        analysis = _analysis_at(candles, idx, vol)
        if not analysis or int(analysis.get("score") or 0) < SETUP_MIN_ENTRY_SCORE:
            idx += 1
            continue

        btc_i = btc_idx_by_ts.get(ts)
        regime = _historical_regime(btc_candles, btc_i) if btc_i is not None else "neutral"

        result = _simulate_trade(candles, idx, analysis, regime)
        if not result:
            idx += 1
            continue

        pnl_eur = balance * (result["returnPct"] / 100.0)
        balance += pnl_eur
        exit_idx = result["exitIdx"]
        exit_ts = int(candles[exit_idx]["timestamp"])
        trades.append(
            {
                "entryAt": ts,
                "exitAt": exit_ts,
                "entryPrice": round(float(candles[idx]["close"]), 6),
                "exitPrice": round(float(candles[exit_idx]["close"]), 6),
                "returnPct": round(result["returnPct"], 2),
                "pnlEur": round(pnl_eur, 2),
                "reason": result["reason"],
                "setup": setup_key_for_analysis(analysis, regime),
                "balanceAfter": round(balance, 2),
            }
        )
        equity_curve.append({"t": exit_ts, "equity": round(balance, 2)})
        idx = exit_idx + 1

    wins = sum(1 for tr in trades if tr["pnlEur"] > 0)
    return {
        "symbol": symbol,
        "base": base,
        "startMs": start_ms,
        "endMs": end_ms,
        "startBalance": EXPLORER_START_BALANCE_EUR,
        "endBalance": round(balance, 2),
        "returnPct": round((balance / EXPLORER_START_BALANCE_EUR - 1) * 100.0, 2),
        "trades": trades,
        "equityCurve": equity_curve,
        "winRate": round(wins / len(trades) * 100.0, 1) if trades else None,
        "tradeCount": len(trades),
    }
