"""FIFO-lot-seuranta kauppahistoriasta (osittaiset myynnit / ikä)."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from .bitfinex import normalize_symbol


def _parse_time(iso: Any) -> datetime | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def open_fifo_lots(trades: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Palauttaa symbolikohtaiset avoimet lotit kronologisessa FIFO-järjestyksessä."""
    chronological = sorted(
        [t for t in trades if t.get("type") in ("buy", "sell") and t.get("symbol")],
        key=lambda t: t.get("timestamp", ""),
    )
    lots: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in chronological:
        sym = normalize_symbol(str(trade["symbol"]))
        if trade["type"] == "buy":
            lots[sym].append(
                {
                    "amount": float(trade.get("amount") or 0),
                    "opened_at": _parse_time(trade.get("timestamp")),
                }
            )
            continue

        sell_amount = float(trade.get("amount") or 0)
        while sell_amount > 1e-12 and lots[sym]:
            lot = lots[sym][0]
            take = min(sell_amount, lot["amount"])
            lot["amount"] -= take
            sell_amount -= take
            if lot["amount"] <= 1e-12:
                lots[sym].pop(0)
    return lots


def fifo_amount_older_than_hours(
    symbol: str,
    trades: list[dict[str, Any]],
    min_age_hours: float,
    *,
    lots_cache: dict[str, list[dict[str, Any]]] | None = None,
    now: datetime | None = None,
) -> float:
    """Summa avoimista loteista, joiden ikä ≥ min_age_hours."""
    now = now or datetime.now(timezone.utc)
    sym = normalize_symbol(symbol)
    all_lots = lots_cache if lots_cache is not None else open_fifo_lots(trades)
    total = 0.0
    for lot in all_lots.get(sym, []):
        opened = lot.get("opened_at")
        if opened is None:
            continue
        age_h = (now - opened).total_seconds() / 3600.0
        if age_h >= min_age_hours:
            total += float(lot.get("amount") or 0)
    return total
