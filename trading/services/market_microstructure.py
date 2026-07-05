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
BOOK_SYMBOL_LIMIT = int(os.environ.get("MICROSTRUCTURE_BOOK_LIMIT", "12"))
STATS_SYMBOL_LIMIT = int(os.environ.get("MICROSTRUCTURE_STATS_LIMIT", "8"))
STATS_CACHE_TTL_SEC = int(os.environ.get("MICROSTRUCTURE_STATS_TTL_SEC", "300"))
STATS_FETCH_PER_CYCLE = int(os.environ.get("MICROSTRUCTURE_STATS_PER_CYCLE", "3"))
BOOK_REQ_PAUSE_SEC = float(os.environ.get("MICROSTRUCTURE_BOOK_PAUSE_SEC", "0.15"))
HOLDINGS_EXIT_BOOK_ENABLED = os.environ.get("MICROSTRUCTURE_HOLDINGS_EXIT_BOOK", "1").lower() not in (
    "0",
    "false",
    "no",
)

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


def _holding_symbols(portfolio: dict[str, Any], tickers: dict[str, dict[str, Any]]) -> list[str]:
    return [
        normalize_symbol(s)
        for s in portfolio.get("holdings", {})
        if not is_stablecoin(s) and normalize_symbol(s) in tickers
    ]


def _stats_pool(candidates: list[str], holdings: list[str] | None = None) -> list[str]:
    """Kaikki avoimet positiot aina poolissa; loput top-kandidaatit."""
    held = list(dict.fromkeys(holdings or []))
    pool = list(held)
    room = max(0, STATS_SYMBOL_LIMIT - len(pool))
    for sym in candidates:
        if room <= 0:
            break
        if sym in pool:
            continue
        pool.append(sym)
        room -= 1
    return pool if pool else candidates[: max(1, STATS_SYMBOL_LIMIT)]


def _stats_refresh_targets(
    pool: list[str],
    *,
    priority: list[str] | None = None,
) -> list[str]:
    """Hae korkeintaan STATS_FETCH_PER_CYCLE symbolia; holdings ensin."""
    global _stats_rotation_idx
    if not pool:
        return []

    priority_set = set(priority or [])
    stale = [
        sym
        for sym in pool
        if sym not in _stats_cache
        or time.time() - _stats_cache[sym][0] >= STATS_CACHE_TTL_SEC
    ]
    if not stale:
        return []

    stale.sort(
        key=lambda sym: (
            0 if sym in priority_set else 1,
            pool.index(sym) if sym in pool else 999,
        )
    )

    fetch_n = min(max(1, STATS_FETCH_PER_CYCLE), len(stale))
    targets: list[str] = []
    for i in range(fetch_n):
        sym = stale[(_stats_rotation_idx + i) % len(stale)]
        if sym not in targets:
            targets.append(sym)
    _stats_rotation_idx += fetch_n
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
    holdings = _holding_symbols(portfolio, tickers)
    stats_pool = _stats_pool(candidates, holdings)
    stats_fetch_targets = _stats_refresh_targets(stats_pool, priority=holdings)

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


def enrich_holdings_for_exits(
    tickers: dict[str, dict[str, Any]],
    analyses: dict[str, dict[str, Any]],
    portfolio: dict[str, Any],
    regime: str,
) -> dict[str, Any]:
    """
    Kevyt microstructure vain avoimille positioille (15 s voitto-polku).
    Order book aina tuore; crowd käytetään cachesta (60 s sykli päivittää).
    """
    summary = {
        "enabled": ENABLED and HOLDINGS_EXIT_BOOK_ENABLED,
        "bookFetched": 0,
        "statsCached": 0,
        "symbols": [],
    }
    if not ENABLED or not HOLDINGS_EXIT_BOOK_ENABLED:
        return summary

    holdings = _holding_symbols(portfolio, tickers)
    if not holdings:
        return summary

    for sym in holdings:
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

    for sym in holdings:
        stats = _cached_position_stats(sym)
        if not stats:
            continue
        analysis = analyses.setdefault(sym, {})
        _apply_micro_fields(analysis, stats)
        summary["statsCached"] += 1
        if sym not in summary["symbols"]:
            summary["symbols"].append(sym)

    for sym in holdings:
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


def _net_eur(trade: dict[str, Any]) -> float:
    return float(trade.get("profitLoss") or trade.get("profit") or 0)


def _linked_micro_outcomes(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """FIFO: myynnit + sisäänoston order book / crowd -meta."""
    from collections import defaultdict

    from .trade_meta import entry_meta_from_trade

    chronological = sorted(
        [t for t in trades if t.get("type") in ("buy", "sell") and t.get("symbol")],
        key=lambda t: t.get("timestamp", ""),
    )
    lots: dict[str, list[dict[str, Any]]] = defaultdict(list)
    linked: list[dict[str, Any]] = []

    for trade in chronological:
        sym = trade["symbol"]
        if trade["type"] == "buy":
            lots[sym].append(
                {
                    "amount": float(trade.get("amount") or 0),
                    "meta": entry_meta_from_trade(trade),
                }
            )
            continue

        sell_amount = float(trade.get("amount") or 0)
        entry_meta: dict[str, Any] = {}
        while sell_amount > 1e-12 and lots[sym]:
            lot = lots[sym][0]
            take = min(sell_amount, lot["amount"])
            if lot["meta"] and not entry_meta:
                entry_meta = dict(lot["meta"])
            lot["amount"] -= take
            sell_amount -= take
            if lot["amount"] <= 1e-12:
                lots[sym].pop(0)

        has_micro = any(
            entry_meta.get(k) is not None
            for k in ("bookBucket", "crowdBucket", "bookImbalance", "longShortRatio")
        )
        if not has_micro:
            continue

        linked.append(
            {
                "symbol": sym,
                "net_eur": round(_net_eur(trade), 2),
                "reason": trade.get("reason") or "",
                "entry": entry_meta,
            }
        )
    return linked


def _aggregate_micro_bucket(
    linked: list[dict[str, Any]],
    field: str,
) -> dict[str, dict[str, Any]]:
    buckets: dict[str, dict[str, float]] = {}
    for item in linked:
        key = item["entry"].get(field) or "?"
        if key == "?":
            continue
        b = buckets.setdefault(str(key), {"n": 0.0, "net": 0.0, "wins": 0.0})
        net = float(item["net_eur"])
        b["n"] += 1.0
        b["net"] += net
        if net > 0.01:
            b["wins"] += 1.0
    return {
        k: {
            "trades": int(v["n"]),
            "net_eur": round(v["net"], 2),
            "expectancy_eur": round(v["net"] / v["n"], 3) if v["n"] else 0.0,
            "win_rate": round(v["wins"] / v["n"], 2) if v["n"] else 0.0,
        }
        for k, v in buckets.items()
    }


def _setup_memory_by_micro(setup_memory: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Jaa setup-oppiminen book/crowd -segmenteihin avaimen perusteella."""
    book: dict[str, dict[str, float]] = {}
    crowd: dict[str, dict[str, float]] = {}

    for setup, m in setup_memory.items():
        parts = setup.split("|")
        bk = parts[6] if len(parts) > 6 else None
        cr = parts[7] if len(parts) > 7 else None
        n = float(m.get("trades") or 0)
        net = float(m.get("net_eur") or 0)
        wins = n * float(m.get("win_rate") or 0)
        if bk and bk != "bk0":
            b = book.setdefault(bk, {"n": 0.0, "net": 0.0, "wins": 0.0})
            b["n"] += n
            b["net"] += net
            b["wins"] += wins
        if cr and cr != "cr0":
            c = crowd.setdefault(cr, {"n": 0.0, "net": 0.0, "wins": 0.0})
            c["n"] += n
            c["net"] += net
            c["wins"] += wins

    def _fmt(d: dict[str, dict[str, float]]) -> dict[str, dict[str, Any]]:
        return {
            k: {
                "trades": round(v["n"], 1),
                "net_eur": round(v["net"], 2),
                "expectancy_eur": round(v["net"] / v["n"], 3) if v["n"] else 0.0,
                "win_rate": round(v["wins"] / v["n"], 2) if v["n"] else 0.0,
            }
            for k, v in d.items()
        }

    return {"book": _fmt(book), "crowd": _fmt(crowd)}


def build_gemini_context(
    portfolio: dict[str, Any],
    learning: dict[str, Any] | None = None,
    bot_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Konteksti Geminin oppimiskertomukseen — miten microstructure-dataa hyödynnetään."""
    learning = learning or {}
    trades = portfolio.get("trades") or []
    linked = _linked_micro_outcomes(trades)
    setup_memory = learning.get("setup_memory") or {}
    micro_state = (bot_state or {}).get("microstructure") or {}

    total_net = round(sum(x["net_eur"] for x in linked), 2)
    wins = sum(1 for x in linked if x["net_eur"] > 0.01)
    losses = sum(1 for x in linked if x["net_eur"] < -0.01)

    by_book = _aggregate_micro_bucket(linked, "bookBucket")
    by_crowd = _aggregate_micro_bucket(linked, "crowdBucket")
    setup_by_micro = _setup_memory_by_micro(setup_memory)

    examples: list[dict[str, Any]] = []
    for item in sorted(linked, key=lambda x: x["net_eur"], reverse=True)[:3]:
        e = item["entry"]
        examples.append(
            {
                "type": "win",
                "symbol": item["symbol"],
                "net_eur": item["net_eur"],
                "book": e.get("bookBucket"),
                "crowd": e.get("crowdBucket"),
                "imbalance_pct": round(float(e["bookImbalance"]) * 100, 1)
                if e.get("bookImbalance") is not None
                else None,
                "long_pct": round(float(e["longShortRatio"]) * 100, 1)
                if e.get("longShortRatio") is not None
                else None,
            }
        )
    for item in sorted(linked, key=lambda x: x["net_eur"])[:3]:
        if item["net_eur"] >= -0.01:
            continue
        e = item["entry"]
        examples.append(
            {
                "type": "loss",
                "symbol": item["symbol"],
                "net_eur": item["net_eur"],
                "book": e.get("bookBucket"),
                "crowd": e.get("crowdBucket"),
                "imbalance_pct": round(float(e["bookImbalance"]) * 100, 1)
                if e.get("bookImbalance") is not None
                else None,
                "long_pct": round(float(e["longShortRatio"]) * 100, 1)
                if e.get("longShortRatio") is not None
                else None,
            }
        )

    return {
        "enabled": ENABLED,
        "operational": {
            "lastBookFetched": micro_state.get("bookFetched", 0),
            "lastStatsFetched": micro_state.get("statsFetched", 0),
            "symbolsTracked": micro_state.get("symbols") or [],
        },
        "usage": {
            "scoreAdjustField": "microAdjust",
            "blocksField": "microBlocked",
            "bookBonusThresholdPct": round(BOOK_IMBALANCE_BONUS * 100, 0),
            "bookPenaltyThresholdPct": round(BOOK_IMBALANCE_PENALTY * 100, 0),
            "crowdLongBlockPct": round(CROWD_LONG_RATIO * 100, 0),
            "spreadBlockPct": SPREAD_BLOCK_PCT,
        },
        "closedTradesWithMicro": len(linked),
        "closedTradesNetEur": total_net,
        "closedTradesWinRate": round(wins / len(linked), 2) if linked else None,
        "closedTradesWins": wins,
        "closedTradesLosses": losses,
        "outcomesByBookBucket": by_book,
        "outcomesByCrowdBucket": by_crowd,
        "setupMemoryByMicro": setup_by_micro,
        "examples": examples,
    }


def learning_report_lines(context: dict[str, Any]) -> list[str]:
    """Rule-pohjaiset rivit oppimisraportin korttiin."""
    if not context.get("enabled"):
        return ["Microstructure pois päältä (MICROSTRUCTURE_ENABLED=0)"]

    lines: list[str] = []
    op = context.get("operational") or {}
    if op.get("lastBookFetched"):
        lines.append(
            f"Viime kierros: order book {op['lastBookFetched']} · positioning {op.get('lastStatsFetched', 0)}"
        )
    else:
        lines.append("Order book + crowd -data kerätään kierroksittain")

    n = int(context.get("closedTradesWithMicro") or 0)
    if n == 0:
        lines.append("Ei vielä suljettuja kauppoja micro-meta-datalla — keruu alkaa uusista ostoista")
        return lines

    net = context.get("closedTradesNetEur")
    wr = context.get("closedTradesWinRate")
    lines.append(f"Suljetut kaupat micro-datalla: {n} kpl · netto {net:+.2f} €" + (f" · win rate {wr * 100:.0f} %" if wr is not None else ""))

    by_book = context.get("outcomesByBookBucket") or {}
    if by_book.get("bk+"):
        b = by_book["bk+"]
        lines.append(f"Ostopaine (bk+): {b['expectancy_eur']:+.2f} €/kauppa ({b['trades']} kpl)")
    if by_book.get("bk-"):
        b = by_book["bk-"]
        lines.append(f"Myyntipaine (bk-): {b['expectancy_eur']:+.2f} €/kauppa ({b['trades']} kpl)")

    by_crowd = context.get("outcomesByCrowdBucket") or {}
    if by_crowd.get("crL"):
        c = by_crowd["crL"]
        lines.append(f"Crowd long (crL): {c['expectancy_eur']:+.2f} €/kauppa ({c['trades']} kpl)")
    if by_crowd.get("crS"):
        c = by_crowd["crS"]
        lines.append(f"Crowd short (crS): {c['expectancy_eur']:+.2f} €/kauppa ({c['trades']} kpl)")

    return lines

