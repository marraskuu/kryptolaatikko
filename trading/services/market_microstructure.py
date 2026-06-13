"""
Bitfinex order book + long/short positioning — rikastaa analyysejä ennen ostoja.

Order book (240 req/min): spread, imbalance (osto vs myyntipaine).
Position stats (15 req/min): long/short crowd — contrarian-signaali oppimiseen.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from .bitfinex import (
    fetch_order_book,
    fetch_position_sizes,
    is_stablecoin,
    normalize_symbol,
    parse_order_book,
)

logger = logging.getLogger(__name__)

ENABLED = os.environ.get("MICROSTRUCTURE_ENABLED", "1").strip().lower() not in (
    "0",
    "false",
    "no",
)
BOOK_SYMBOL_LIMIT = int(os.environ.get("MICROSTRUCTURE_BOOK_LIMIT", "8"))
STATS_SYMBOL_LIMIT = int(os.environ.get("MICROSTRUCTURE_STATS_LIMIT", "6"))
STATS_CACHE_TTL_SEC = int(os.environ.get("MICROSTRUCTURE_STATS_TTL_SEC", "300"))
STATS_FETCH_PER_CYCLE = int(os.environ.get("MICROSTRUCTURE_STATS_PER_CYCLE", "1"))
BOOK_REQ_PAUSE_SEC = float(os.environ.get("MICROSTRUCTURE_BOOK_PAUSE_SEC", "0.15"))

# Score-säätö ja estot
BOOK_IMBALANCE_BONUS = 0.25
BOOK_IMBALANCE_PENALTY = -0.25
BOOK_BONUS_SCORE = 1.0
BOOK_PENALTY_SCORE = -2.0
SPREAD_WARN_PCT = 0.15
SPREAD_BLOCK_PCT = 0.35
CROWD_LONG_RATIO = 0.85
CROWD_SHORT_RATIO = 0.35
CROWD_EXTREME_LONG = 0.92
CROWD_LONG_PENALTY = -1.5
CROWD_SHORT_BONUS_BULL = 0.75

_stats_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_stats_rotation_idx = 0


def crowd_bucket(long_ratio: float | None) -> str:
    if long_ratio is None:
        return "cr0"
    if long_ratio >= CROWD_LONG_RATIO:
        return "crL"
    if long_ratio <= CROWD_SHORT_RATIO:
        return "crS"
    return "cr0"


def book_bucket(imbalance: float | None) -> str:
    if imbalance is None:
        return "bk0"
    if imbalance >= BOOK_IMBALANCE_BONUS:
        return "bk+"
    if imbalance <= BOOK_IMBALANCE_PENALTY:
        return "bk-"
    return "bk0"


def _candidate_symbols(
    tickers: dict[str, dict[str, Any]],
    analyses: dict[str, dict[str, Any]],
    portfolio: dict[str, Any],
) -> list[str]:
    holdings = [normalize_symbol(s) for s in portfolio.get("holdings", {}).keys()]
    ranked = sorted(
        [normalize_symbol(s) for s in tickers if not is_stablecoin(s)],
        key=lambda s: (
            -(analyses.get(s, {}).get("score", 0) or 0),
            -(tickers.get(s, {}).get("volumeEur", 0) or 0),
        ),
    )
    result: list[str] = []
    seen: set[str] = set()
    for sym in holdings + ranked:
        if sym in seen or sym not in tickers or is_stablecoin(sym):
            continue
        seen.add(sym)
        result.append(sym)
    return result


def _cached_position_stats(symbol: str, *, force: bool = False) -> dict[str, Any] | None:
    now = time.time()
    cached = _stats_cache.get(symbol)
    if not force and cached and now - cached[0] < STATS_CACHE_TTL_SEC:
        return cached[1]

    stats = fetch_position_sizes(symbol)
    if stats:
        _stats_cache[symbol] = (now, stats)
    return stats


def _stats_pool(candidates: list[str]) -> list[str]:
    return candidates[: max(1, STATS_SYMBOL_LIMIT)]


def _stats_refresh_targets(pool: list[str]) -> list[str]:
    """Hae korkeintaan STATS_FETCH_PER_CYCLE symbolia, joiden cache on vanhentunut."""
    global _stats_rotation_idx
    if not pool:
        return []

    stale = [
        sym
        for sym in pool
        if sym not in _stats_cache
        or time.time() - _stats_cache[sym][0] >= STATS_CACHE_TTL_SEC
    ]
    if not stale:
        return []

    targets: list[str] = []
    for _ in range(min(max(1, STATS_FETCH_PER_CYCLE), len(stale))):
        sym = stale[_stats_rotation_idx % len(stale)]
        _stats_rotation_idx += 1
        if sym not in targets:
            targets.append(sym)
    return targets


def _apply_micro_fields(analysis: dict[str, Any], fields: dict[str, Any]) -> None:
    for key, value in fields.items():
        if value is not None:
            analysis[key] = value
    analysis["bookBucket"] = book_bucket(analysis.get("bookImbalance"))
    analysis["crowdBucket"] = crowd_bucket(analysis.get("longShortRatio"))


_MICRO_REASON_PREFIXES = (
    "Order book:",
    "Spread ",
    "Leveä spread",
    "Crowd ",
    "Bear + crowd",
)


def _strip_micro_reasons(reasons: list[str]) -> list[str]:
    return [
        r
        for r in reasons
        if not any(str(r).startswith(prefix) for prefix in _MICRO_REASON_PREFIXES)
    ]


def _score_and_block(analysis: dict[str, Any], regime: str) -> None:
    adjust = 0.0
    blocked = False
    reasons: list[str] = _strip_micro_reasons(list(analysis.get("reasons") or []))

    imbalance = analysis.get("bookImbalance")
    if imbalance is not None:
        if imbalance >= BOOK_IMBALANCE_BONUS:
            adjust += BOOK_BONUS_SCORE
            reasons.append(f"Order book: ostopaine +{imbalance * 100:.0f} %")
        elif imbalance <= BOOK_IMBALANCE_PENALTY:
            adjust += BOOK_PENALTY_SCORE
            reasons.append(f"Order book: myyntipaine {imbalance * 100:.0f} %")

    spread = analysis.get("bookSpreadPct")
    if spread is not None:
        if spread >= SPREAD_BLOCK_PCT:
            blocked = True
            reasons.append(f"Leveä spread {spread:.2f} % — ei uusia ostoja")
        elif spread >= SPREAD_WARN_PCT:
            adjust -= 1.0
            reasons.append(f"Spread {spread:.2f} % — varovainen")

    long_ratio = analysis.get("longShortRatio")
    if long_ratio is not None:
        pct = long_ratio * 100.0
        if long_ratio >= CROWD_EXTREME_LONG:
            adjust += CROWD_LONG_PENALTY
            reasons.append(f"Crowd long {pct:.0f} % — ylikuormitus")
        elif long_ratio >= CROWD_LONG_RATIO:
            adjust -= 0.75
            reasons.append(f"Crowd long {pct:.0f} %")
        elif long_ratio <= CROWD_SHORT_RATIO:
            if regime == "bull":
                adjust += CROWD_SHORT_BONUS_BULL
                reasons.append(f"Crowd short {pct:.0f} % — bull contrarian")
            else:
                reasons.append(f"Crowd short {pct:.0f} %")

        if regime == "bear" and long_ratio >= CROWD_LONG_RATIO:
            blocked = True
            reasons.append(f"Bear + crowd long {pct:.0f} % — osto estetty")

    analysis["microAdjust"] = round(adjust, 2)
    analysis["microBlocked"] = blocked
    analysis["reasons"] = reasons


def enrich_analyses(
    tickers: dict[str, dict[str, Any]],
    analyses: dict[str, dict[str, Any]],
    portfolio: dict[str, Any],
    regime: str,
) -> dict[str, Any]:
    """Hae order book + positioning top-kandidaateille ja päivitä analyysit."""
    summary = {
        "enabled": ENABLED,
        "bookFetched": 0,
        "statsFetched": 0,
        "symbols": [],
    }
    if not ENABLED:
        return summary

    candidates = _candidate_symbols(tickers, analyses, portfolio)
    if not candidates:
        return summary

    book_targets = candidates[: max(1, BOOK_SYMBOL_LIMIT)]
    stats_pool = _stats_pool(candidates)
    stats_fetch_targets = _stats_refresh_targets(stats_pool)

    for sym in book_targets:
        rows = fetch_order_book(sym)
        parsed = parse_order_book(rows)
        if not parsed:
            continue
        analysis = analyses.setdefault(sym, {})
        _apply_micro_fields(analysis, parsed)
        summary["bookFetched"] += 1
        summary["symbols"].append(sym)
        if BOOK_REQ_PAUSE_SEC > 0:
            time.sleep(BOOK_REQ_PAUSE_SEC)

    for sym in stats_fetch_targets:
        stats = _cached_position_stats(sym, force=True)
        if not stats:
            continue
        analysis = analyses.setdefault(sym, {})
        _apply_micro_fields(analysis, stats)
        summary["statsFetched"] += 1
        if sym not in summary["symbols"]:
            summary["symbols"].append(sym)

    for sym in stats_pool:
        stats = _cached_position_stats(sym)
        if not stats:
            continue
        analysis = analyses.setdefault(sym, {})
        _apply_micro_fields(analysis, stats)

    for sym in set(book_targets + stats_pool):
        analysis = analyses.get(sym)
        if analysis and (
            analysis.get("bookImbalance") is not None
            or analysis.get("longShortRatio") is not None
        ):
            _score_and_block(analysis, regime)

    return summary


def blocks_entry(analysis: dict[str, Any]) -> bool:
    return bool(analysis.get("microBlocked"))


def score_adjust(analysis: dict[str, Any]) -> float:
    return float(analysis.get("microAdjust") or 0.0)
