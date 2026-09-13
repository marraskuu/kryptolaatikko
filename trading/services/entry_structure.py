"""Bitfinex-rakenneportit ostoille: 15m/4h, etäisyys 24h-huipusta, volume spike.

Public REST only — ei API-avainta. Tavoite: estää chase-huiput ja climax-volyymi
ennen kuin stop syö R:R:n.
"""

from __future__ import annotations

import logging
import os
import statistics
import time
from typing import Any, Callable

from .bitfinex import is_stablecoin, normalize_symbol

logger = logging.getLogger(__name__)

ENTRY_STRUCTURE_ENABLED = os.environ.get("ENTRY_STRUCTURE_ENABLED", "1").lower() not in (
    "0",
    "false",
    "no",
    "off",
)
# Estä osto jos hinta on lähempänä 24h-huippua kuin tämä (% huipusta alaspäin).
MIN_DIST_FROM_24H_HIGH_PCT = float(os.environ.get("MIN_DIST_FROM_24H_HIGH_PCT", "1.5"))
# 1h-kynttilän volyymi / mediaani → spike.
VOLUME_SPIKE_RATIO = float(os.environ.get("VOLUME_SPIKE_RATIO", "2.5"))
# 15m-muutos yli tämän = lyhyt chase.
MAX_ENTRY_CHANGE_15M_PCT = float(os.environ.get("MAX_ENTRY_CHANGE_15M_PCT", "2.5"))
# 4h-trendin (viimeiset ~3×4h) minimi ostoille — alle = laskeva TF.
MIN_ENTRY_CHANGE_4H_PCT = float(os.environ.get("MIN_ENTRY_CHANGE_4H_PCT", "-1.5"))
ENTRY_MTF_SYMBOL_LIMIT = int(os.environ.get("ENTRY_MTF_SYMBOL_LIMIT", "8"))
ENTRY_MTF_BUDGET_SEC = float(os.environ.get("ENTRY_MTF_BUDGET_SEC", "25"))
ENTRY_MTF_15M_LIMIT = int(os.environ.get("ENTRY_MTF_15M_LIMIT", "20"))
ENTRY_MTF_4H_LIMIT = int(os.environ.get("ENTRY_MTF_4H_LIMIT", "24"))
# Spike yksin ei riitä; vaadi myös nousumomentum (climax).
VOLUME_SPIKE_MIN_CHANGE_1H_PCT = float(
    os.environ.get("VOLUME_SPIKE_MIN_CHANGE_1H_PCT", "1.0")
)

CARRY_STRUCTURE_KEYS = (
    "distToHigh24hPct",
    "near24hHigh",
    "relVolume1h",
    "volumeSpike",
    "change15mPct",
    "change4hCandlePct",
    "entryMtfChecked",
)


def _period_change_pct(closes: list[float], bars: int) -> float | None:
    if bars <= 0 or len(closes) < bars + 1:
        return None
    older = closes[-(bars + 1)]
    newer = closes[-1]
    if older == 0:
        return None
    return ((newer - older) / older) * 100.0


def apply_ticker_structure(
    analysis: dict[str, Any],
    ticker: dict[str, Any] | None,
) -> None:
    """Etäisyys 24h-huipusta ticker.high/last -kentistä (ei lisäkutsua)."""
    if not ticker:
        return
    try:
        high = float(ticker.get("high") or 0)
        last = float(ticker.get("last") or analysis.get("currentPrice") or 0)
    except (TypeError, ValueError):
        return
    if high <= 0 or last <= 0:
        return
    dist = (high - last) / high * 100.0
    analysis["distToHigh24hPct"] = round(dist, 3)
    analysis["near24hHigh"] = dist <= MIN_DIST_FROM_24H_HIGH_PCT


def apply_volume_structure(
    analysis: dict[str, Any],
    candles: list[dict[str, Any]],
) -> None:
    """Suhteellinen 1h-volyymi viimeiseen mediaaniin."""
    vols = [float(c.get("volume") or 0) for c in candles if (c.get("volume") or 0) > 0]
    if len(vols) < 10:
        return
    last = vols[-1]
    baseline = vols[-21:-1] if len(vols) > 21 else vols[:-1]
    if not baseline:
        return
    try:
        med = statistics.median(baseline)
    except statistics.StatisticsError:
        return
    if med <= 0:
        return
    rel = last / med
    analysis["relVolume1h"] = round(rel, 3)
    ch1 = analysis.get("change1hPct")
    try:
        ch1_f = float(ch1) if ch1 is not None else 0.0
    except (TypeError, ValueError):
        ch1_f = 0.0
    analysis["volumeSpike"] = bool(
        rel >= VOLUME_SPIKE_RATIO and ch1_f >= VOLUME_SPIKE_MIN_CHANGE_1H_PCT
    )


def apply_mtf_candles(
    analysis: dict[str, Any],
    candles_15m: list[dict[str, Any]],
    candles_4h: list[dict[str, Any]],
) -> None:
    closes_15 = [float(c["close"]) for c in candles_15m if c.get("close")]
    closes_4h = [float(c["close"]) for c in candles_4h if c.get("close")]
    ch15 = _period_change_pct(closes_15, 1)
    ch4 = _period_change_pct(closes_4h, 3)
    if ch15 is not None:
        analysis["change15mPct"] = round(ch15, 3)
    if ch4 is not None:
        analysis["change4hCandlePct"] = round(ch4, 3)
    analysis["entryMtfChecked"] = True


def entry_structure_blocks(analysis: dict[str, Any] | None) -> bool:
    """True = osto estetty rakenneportilla."""
    if not ENTRY_STRUCTURE_ENABLED or not analysis:
        return False
    if analysis.get("near24hHigh"):
        return True
    if analysis.get("volumeSpike"):
        return True
    ch15 = analysis.get("change15mPct")
    if ch15 is not None:
        try:
            if float(ch15) >= MAX_ENTRY_CHANGE_15M_PCT:
                return True
        except (TypeError, ValueError):
            pass
    ch4 = analysis.get("change4hCandlePct")
    if ch4 is not None:
        try:
            if float(ch4) < MIN_ENTRY_CHANGE_4H_PCT:
                return True
        except (TypeError, ValueError):
            pass
    return False


def entry_structure_block_reason(analysis: dict[str, Any] | None) -> str | None:
    if not entry_structure_blocks(analysis):
        return None
    assert analysis is not None
    if analysis.get("near24hHigh"):
        dist = analysis.get("distToHigh24hPct")
        return f"near_24h_high({dist})"
    if analysis.get("volumeSpike"):
        return f"volume_spike({analysis.get('relVolume1h')})"
    ch15 = analysis.get("change15mPct")
    if ch15 is not None:
        try:
            if float(ch15) >= MAX_ENTRY_CHANGE_15M_PCT:
                return f"chase_15m({ch15})"
        except (TypeError, ValueError):
            pass
    ch4 = analysis.get("change4hCandlePct")
    if ch4 is not None:
        try:
            if float(ch4) < MIN_ENTRY_CHANGE_4H_PCT:
                return f"downtrend_4h({ch4})"
        except (TypeError, ValueError):
            pass
    return "entry_structure"


def _symbols_for_mtf(
    tickers: dict[str, dict[str, Any]],
    portfolio: dict[str, Any],
    limit: int,
) -> list[str]:
    from .ai_trader import BUY_MAJORS_ONLY, _is_buy_major

    holdings = [
        normalize_symbol(s)
        for s in (portfolio.get("holdings") or {})
        if not is_stablecoin(s)
    ]
    ranked = sorted(
        [s for s in tickers if not is_stablecoin(s)],
        key=lambda s: float((tickers[s] or {}).get("volumeEur") or 0),
        reverse=True,
    )
    if BUY_MAJORS_ONLY:
        ranked = [s for s in ranked if _is_buy_major(s)]
    out: list[str] = []
    seen: set[str] = set()
    for sym in holdings + ranked:
        norm = normalize_symbol(sym)
        if not norm or norm in seen or is_stablecoin(norm):
            continue
        if BUY_MAJORS_ONLY and not _is_buy_major(norm):
            continue
        seen.add(norm)
        out.append(norm)
        if len(out) >= limit:
            break
    return out


def enrich_mtf_entry_signals(
    tickers: dict[str, dict[str, Any]],
    analyses: dict[str, dict[str, Any]],
    portfolio: dict[str, Any],
    fetch_candles_fn: Callable[..., list[dict[str, Any]]],
    *,
    limit: int | None = None,
) -> dict[str, Any]:
    """Hae 15m + 4h top-symboleille (budjetoitu)."""
    if not ENTRY_STRUCTURE_ENABLED:
        return {"enabled": False, "enriched": 0}
    lim = limit if limit is not None else ENTRY_MTF_SYMBOL_LIMIT
    deadline = time.time() + ENTRY_MTF_BUDGET_SEC
    enriched = 0
    errors = 0
    for symbol in _symbols_for_mtf(tickers, portfolio, lim):
        if time.time() >= deadline:
            logger.warning("Entry MTF enrich budget exhausted")
            break
        ticker = tickers.get(symbol)
        analysis = analyses.get(symbol)
        if not ticker or not analysis:
            continue
        try:
            c15 = fetch_candles_fn(symbol, "15m", ENTRY_MTF_15M_LIMIT)
            c4h = fetch_candles_fn(symbol, "4h", ENTRY_MTF_4H_LIMIT)
            apply_mtf_candles(analysis, c15, c4h)
            apply_ticker_structure(analysis, ticker)
            enriched += 1
        except Exception:
            errors += 1
            logger.warning("Entry MTF enrich failed for %s", symbol, exc_info=True)
    return {"enabled": True, "enriched": enriched, "errors": errors, "limit": lim}
