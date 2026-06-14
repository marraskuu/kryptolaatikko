import time
from datetime import datetime, timezone
from typing import Any

from .ai_trader import MAX_POSITIONS
from .gemini import get_status as gemini_status_snapshot
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
        "running": True,
        "lastPriceTick": 0,
        "lastTradeTick": 0,
        "lastGeminiTick": 0,
        "geminiInsights": None,
        "regime": None,
        "learning": None,
        "marketLearning": None,
        "exitLearning": None,
        "microstructure": None,
        "tickers": {},
        "analyses": {},
        "profitWatch": {},
        "activeSymbols": [],
        "lastAIReport": None,
        "marketSearch": "",
    }


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


def _resolve_gemini_status(state: dict[str, Any]) -> dict[str, Any]:
    """Yhdistä live-ympäristötarkistus + viimeisin Gemini-yritys."""
    from .gemini import _read_model

    live = gemini_status_snapshot()
    configured_model = _read_model()
    saved = state.get("geminiStatus") or {}
    base = {"configuredModel": configured_model}
    if not live.get("configured"):
        return {**live, **base}
    if saved.get("ok"):
        merged = {**live, **saved, **base, "provider": "gemini", "status": "ok"}
        if saved.get("model") and saved["model"] != configured_model:
            merged["lastUsedModel"] = saved["model"]
        return merged
    if saved.get("status") == "error" or (
        saved.get("message") and "epäonnistui" in saved["message"]
    ):
        return {**live, **saved, **base, "status": "error"}
    return {**live, **base}


def _ms_to_iso(ms: int | float | None) -> str | None:
    if not ms:
        return None
    # Millisekuntitarkkuus — osa selaimista hylkää 6 desimaalin ISO-aikaleiman.
    return datetime.fromtimestamp(float(ms) / 1000, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%f"
    )[:-3] + "Z"


def build_api_payload(state: dict[str, Any]) -> dict[str, Any]:
    portfolio = Portfolio(state["portfolio"])
    tickers = state["tickers"]
    holdings_value = 0.0
    if tickers:
        for symbol, holding in portfolio.holdings.items():
            price = portfolio._holding_mark_price(symbol, holding, tickers)
            if price is not None:
                holdings_value += holding["amount"] * price
        total_value = portfolio.cash + holdings_value
    else:
        total_value = portfolio.cash
    pnl = portfolio.get_pnl(total_value)
    tax = portfolio.get_tax_summary(tickers or {})
    realized = portfolio.get_realized_breakdown()
    unrealized_pnl = portfolio.get_unrealized_pnl(tickers or {})
    realized_pnl = sum(
        float(t.get("profitLoss") or 0.0)
        for t in portfolio.trades
        if t.get("type") == "sell"
    )

    trade_interval = TRADE_INTERVAL_MS // 1000
    last_trade_ms = state.get("lastTradeTick") or 0
    last_price_ms = state.get("lastPriceTick") or 0
    if last_trade_ms:
        elapsed = int(time.time() * 1000 - last_trade_ms) // 1000
        next_trade_in = max(0, trade_interval - elapsed)
    else:
        next_trade_in = trade_interval

    last_activity_ms = max(last_trade_ms, last_price_ms)

    gemini_status = _resolve_gemini_status(state)

    from .learning_report import _merge_cached_learning_report, build_gemini_narrative_history

    learning_report = state.get("learningReport")
    if learning_report:
        learning_report = _merge_cached_learning_report(state, learning_report)

    gemini_pick_tracking = None
    if tickers:
        from .bitfinex import get_crypto_label
        from .gemini_pick_tracking import build_pick_tracking

        gemini_pick_tracking = build_pick_tracking(
            state, tickers, total_value, get_crypto_label
        )

    return {
        "running": state.get("running", True),
        "portfolio": portfolio.to_dict(),
        "tickers": tickers,
        "analyses": state["analyses"],
        "profitWatch": state["profitWatch"],
        "activeSymbols": state["activeSymbols"],
        "aiEvents": state["aiEvents"],
        "lastAIReport": state["lastAIReport"],
        "stats": {
            "totalValue": total_value,
            "holdingsValue": round(holdings_value, 2),
            "pnl": pnl["pnl"],
            "pnlPct": pnl["pnlPct"],
            "unrealizedPnl": round(unrealized_pnl, 2),
            "realizedPnl": round(realized_pnl, 2),
            "cash": portfolio.cash,
            "tradeCount": len([t for t in portfolio.trades if t["type"] != "tax"]),
            "taxCurrentYear": tax["currentYearTax"],
            "taxCurrentYearLabel": tax["currentYear"],
            "taxCurrentYearRealized": tax["currentYearRealized"],
            "taxCurrentYearGrossWins": tax["currentYearGrossWins"],
            "taxPreviousYear": tax["previousYearTax"],
            "taxPreviousYearLabel": tax["previousYear"],
            "taxPreviousYearRealized": tax["previousYearRealized"],
            "estimatedTax": tax["estimatedTax"],
            "realizedBreakdown": realized,
        },
        "marketCount": len(tickers),
        "maxPositions": MAX_POSITIONS,
        "tradeIntervalSec": trade_interval,
        "nextTradeInSec": next_trade_in,
        "lastTradeAt": _ms_to_iso(last_trade_ms),
        "lastUpdate": _ms_to_iso(last_activity_ms),
        "aiProvider": gemini_status.get("provider", "technical"),
        "geminiStatus": gemini_status,
        "regime": state.get("regime"),
        "learning": state.get("learning"),
        "marketLearning": state.get("marketLearning"),
        "exitLearning": state.get("exitLearning"),
        "microstructure": state.get("microstructure"),
        "learningReport": learning_report,
        "geminiNarrativeHistory": build_gemini_narrative_history(state),
        "geminiPickTracking": gemini_pick_tracking,
        "botStartedAt": state.get("botStartedAt"),
        "dailyPolicyShadow": _build_daily_policy_shadow(state),
    }


def _build_daily_policy_shadow(state: dict[str, Any]) -> dict[str, Any]:
    from .daily_policy_shadow import build_api_summary

    return build_api_summary(state)
