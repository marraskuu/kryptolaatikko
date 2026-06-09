from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from django.contrib.sessions.backends.base import SessionBase

from .portfolio import Portfolio, default_portfolio

AI_EVENT_LIMIT = 20
PRICE_INTERVAL_MS = 15_000
TRADE_INTERVAL_MS = 60_000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_state() -> dict[str, Any]:
    return {
        "portfolio": default_portfolio(),
        "watches": {},
        "watchLogKeys": {},
        "aiEvents": [],
        "aiEventId": 0,
        "running": False,
        "lastPriceTick": 0,
        "lastTradeTick": 0,
        "tickers": {},
        "analyses": {},
        "profitWatch": {},
        "activeSymbols": [],
        "lastAIReport": None,
        "marketSearch": "",
    }


def load_state(session: SessionBase) -> dict[str, Any]:
    state = session.get("bot_state")
    if not state:
        state = default_state()
        save_state(session, state)
    return state


def save_state(session: SessionBase, state: dict[str, Any]) -> None:
    session["bot_state"] = state
    session.modified = True


def reset_state(session: SessionBase) -> dict[str, Any]:
    state = default_state()
    save_state(session, state)
    return state


def log_ai_event(
    state: dict[str, Any],
    event_type: str,
    label: str,
    reason: str,
    amount: float | None = None,
) -> None:
    state["aiEventId"] += 1
    state["aiEvents"].insert(
        0,
        {
            "id": state["aiEventId"],
            "timestamp": _now_iso(),
            "type": event_type,
            "label": label,
            "reason": reason,
            "amount": amount,
        },
    )
    state["aiEvents"] = state["aiEvents"][:AI_EVENT_LIMIT]


def log_watch_event(state: dict[str, Any], symbol: str, watch: dict[str, Any] | None) -> None:
    if not watch or watch.get("status") == "alle_3":
        return
    bucket = (
        "armed"
        if watch.get("secondsLeft", 0) <= 0
        else str(int((watch.get("secondsLeft", 0) + 29) / 30))
    )
    key = f"{symbol}:{watch['status']}:{bucket}"
    if state["watchLogKeys"].get(symbol) == key:
        return
    state["watchLogKeys"][symbol] = key
    from .bitfinex import get_crypto_label

    log_ai_event(state, "watch", get_crypto_label(symbol), watch["statusText"])


def build_api_payload(state: dict[str, Any]) -> dict[str, Any]:
    portfolio = Portfolio(state["portfolio"])
    tickers = state["tickers"]
    total_value = portfolio.get_total_value(tickers) if tickers else portfolio.cash
    pnl = portfolio.get_pnl(total_value)
    tax = portfolio.get_tax_summary(tickers) if tickers else {
        "totalTaxPaid": portfolio.data["totalTaxPaid"],
        "estimatedTax": 0,
        "totalTaxLiability": portfolio.data["totalTaxPaid"],
        "unrealizedProfit": 0,
    }

    return {
        "running": state["running"],
        "portfolio": portfolio.to_dict(),
        "tickers": tickers,
        "analyses": state["analyses"],
        "profitWatch": state["profitWatch"],
        "activeSymbols": state["activeSymbols"],
        "aiEvents": state["aiEvents"],
        "lastAIReport": state["lastAIReport"],
        "stats": {
            "totalValue": total_value,
            "pnl": pnl["pnl"],
            "pnlPct": pnl["pnlPct"],
            "cash": portfolio.cash,
            "tradeCount": len([t for t in portfolio.trades if t["type"] != "tax"]),
            "totalTaxPaid": tax["totalTaxPaid"],
            "estimatedTax": tax["estimatedTax"],
        },
        "marketCount": len(tickers),
        "tradeIntervalSec": TRADE_INTERVAL_MS // 1000,
    }
