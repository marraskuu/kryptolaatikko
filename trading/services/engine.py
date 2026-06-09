import logging
import time
from datetime import datetime, timezone
from typing import Any

from django.contrib.sessions.backends.base import SessionBase

from .ai_trader import (
    analyze_market,
    analyze_ticker_quick,
    build_decision_report,
    make_trading_decisions,
)
from .bitfinex import fetch_all_markets, fetch_candles, get_crypto_label
from .portfolio import Portfolio
from .sell_strategy import update_profit_sell
from .session_state import (
    build_api_payload,
    load_state,
    log_ai_event,
    log_watch_event,
    save_state,
)

logger = logging.getLogger(__name__)

DEEP_ANALYSIS_COUNT = 30


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _select_candidates_for_deep_analysis(state: dict[str, Any]) -> list[str]:
    candidates = set(state["portfolio"]["holdings"].keys())
    ranked = sorted(
        state["tickers"].items(),
        key=lambda x: x[1].get("volumeEur", 0),
        reverse=True,
    )
    for symbol, _ in ranked:
        if len(candidates) >= DEEP_ANALYSIS_COUNT:
            break
        candidates.add(symbol)
    return list(candidates)


def _refresh_analyses(state: dict[str, Any]) -> None:
    for symbol, ticker in state["tickers"].items():
        state["analyses"][symbol] = analyze_ticker_quick(ticker)

    for symbol in _select_candidates_for_deep_analysis(state):
        try:
            candles = fetch_candles(symbol, "1h", 50)
            if len(candles) >= 20:
                deep = analyze_market(candles)
                ticker = state["tickers"].get(symbol)
                if ticker:
                    deep["currentPrice"] = ticker["last"]
                    deep["volumeEur"] = ticker["volumeEur"]
                state["analyses"][symbol] = deep
        except Exception as exc:
            logger.warning("Deep analysis failed for %s: %s", symbol, exc)


def _check_profit_sells(state: dict[str, Any], portfolio: Portfolio) -> list[dict[str, Any]]:
    executed: list[dict[str, Any]] = []
    for symbol, holding in list(portfolio.holdings.items()):
        ticker = state["tickers"].get(symbol)
        if not ticker:
            continue

        result = update_profit_sell(
            state["watches"],
            symbol,
            ticker["last"],
            holding["avgPrice"],
        )
        state["profitWatch"][symbol] = result

        if result["shouldSell"]:
            eur_total = holding["amount"] * ticker["last"]
            portfolio.sell(symbol, holding["amount"], ticker["last"], result["reason"])
            log_ai_event(state, "sell", get_crypto_label(symbol), result["reason"], eur_total)
            executed.append(
                {
                    "type": "sell",
                    "symbol": symbol,
                    "label": get_crypto_label(symbol),
                    "amount": eur_total,
                    "reason": result["reason"],
                }
            )
            state["watches"].pop(symbol, None)
            state["profitWatch"].pop(symbol, None)

    state["portfolio"] = portfolio.to_dict()
    return executed


def refresh_prices(session: SessionBase) -> dict[str, Any]:
    state = load_state(session)
    try:
        tickers, _meta = fetch_all_markets()
        if not tickers:
            raise RuntimeError("Bitfinex ei palauttanut kursseja.")

        state["tickers"] = tickers
        for symbol, ticker in tickers.items():
            existing = state["analyses"].get(symbol)
            if not existing or existing.get("quick"):
                state["analyses"][symbol] = analyze_ticker_quick(ticker)

        state["lastPriceTick"] = int(time.time() * 1000)
        state["error"] = None

        if state["running"]:
            portfolio = Portfolio(state["portfolio"])
            _check_profit_sells(state, portfolio)

            report = state.get("lastAIReport")
            if report:
                watches = []
                for symbol in portfolio.holdings:
                    watch = state["profitWatch"].get(symbol)
                    if watch and watch.get("status") in ("waiting", "armed"):
                        watches.append(
                            {
                                "symbol": symbol,
                                "label": get_crypto_label(symbol),
                                "reason": watch["statusText"],
                                "profitPct": watch.get("profitPct"),
                            }
                        )
                        log_watch_event(state, symbol, watch)
                if watches:
                    report = {**report, "watches": watches, "timestamp": _now_iso()}
                    state["lastAIReport"] = report

    except Exception as exc:
        logger.exception("Price refresh failed")
        state["error"] = str(exc)

    save_state(session, state)
    payload = build_api_payload(state)
    payload["error"] = state.get("error")
    payload["lastUpdate"] = _now_iso()
    return payload


def execute_trading_cycle(session: SessionBase) -> dict[str, Any]:
    payload = refresh_prices(session)
    state = load_state(session)
    if state.get("error"):
        return payload

    _refresh_analyses(state)
    portfolio = Portfolio(state["portfolio"])
    profit_sells = _check_profit_sells(state, portfolio)

    total_value = portfolio.get_total_value(state["tickers"])
    decision_result = make_trading_decisions(
        state["analyses"],
        portfolio.to_dict(),
        total_value,
        get_crypto_label,
    )
    decisions = decision_result["decisions"]
    state["activeSymbols"] = decision_result.get("topSymbols", [])

    executed_buys: list[dict[str, Any]] = []
    executed_sells = [
        {**s, "analysis": state["analyses"].get(s["symbol"])}
        for s in profit_sells
    ]

    initial_allocation = decision_result.get("initialAllocation") or []
    if initial_allocation:
        slots = [
            {
                "symbol": item["symbol"],
                "price": item["analysis"]["currentPrice"],
                "reason": (
                    f"Alkuallokaatio — {get_crypto_label(item['symbol'])} "
                    f"({i + 1}/{len(initial_allocation)})"
                ),
            }
            for i, item in enumerate(initial_allocation)
        ]
        portfolio.allocate_initial(slots)
        for item in initial_allocation:
            symbol = item["symbol"]
            holding = portfolio.holdings.get(symbol)
            amount = holding["amount"] * item["analysis"]["currentPrice"] if holding else None
            log_ai_event(
                state,
                "buy",
                get_crypto_label(symbol),
                f"Alkuallokaatio — top {len(initial_allocation)} parasta signaalia",
                amount,
            )
            executed_buys.append(
                {
                    "symbol": symbol,
                    "label": get_crypto_label(symbol),
                    "amount": amount,
                    "reason": f"Top {len(initial_allocation)} parasta signaalia — jaetaan pääoma tasaisesti",
                    "analysis": item["analysis"],
                }
            )

    for d in [x for x in decisions if x["type"] == "sell"]:
        portfolio.sell(d["symbol"], d["amount"], d["analysis"]["currentPrice"], d["reason"])
        log_ai_event(state, "sell", get_crypto_label(d["symbol"]), d["reason"], d.get("eurAmount"))
        executed_sells.append(
            {
                "symbol": d["symbol"],
                "label": get_crypto_label(d["symbol"]),
                "amount": d.get("eurAmount"),
                "reason": d["reason"],
                "analysis": d["analysis"],
            }
        )

    for d in [x for x in decisions if x["type"] == "buy"]:
        ok = portfolio.buy(
            d["symbol"],
            d["eurAmount"],
            d["analysis"]["currentPrice"],
            d["reason"],
        )
        if ok:
            log_ai_event(state, "buy", get_crypto_label(d["symbol"]), d["reason"], d.get("eurAmount"))
            executed_buys.append(
                {
                    "symbol": d["symbol"],
                    "label": get_crypto_label(d["symbol"]),
                    "amount": d.get("eurAmount"),
                    "reason": d["reason"],
                    "analysis": d["analysis"],
                }
            )

    watches = []
    for symbol in portfolio.holdings:
        watch = state["profitWatch"].get(symbol)
        if watch and watch.get("status") in ("waiting", "armed"):
            watches.append(
                {
                    "symbol": symbol,
                    "label": get_crypto_label(symbol),
                    "reason": watch["statusText"],
                    "profitPct": watch.get("profitPct"),
                }
            )

    report = build_decision_report(decisions, get_crypto_label)
    report.update(
        {
            "executedBuys": executed_buys,
            "executedSells": executed_sells,
            "watches": watches,
            "timestamp": _now_iso(),
        }
    )
    state["lastAIReport"] = report

    for d in [x for x in decisions if x["type"] == "hold"]:
        log_ai_event(state, "hold", get_crypto_label(d["symbol"]), d["reason"])
    for w in watches:
        log_watch_event(state, w["symbol"], state["profitWatch"].get(w["symbol"]))

    state["portfolio"] = portfolio.to_dict()
    state["lastTradeTick"] = int(time.time() * 1000)
    save_state(session, state)

    payload = build_api_payload(state)
    payload["lastUpdate"] = _now_iso()
    return payload


def start_bot(session: SessionBase) -> dict[str, Any]:
    state = load_state(session)
    if not state["running"]:
        state["running"] = True
        log_ai_event(
            state,
            "info",
            "Botti",
            "Automaattinen kaupankäynti käynnistetty — analysoidaan kaikkia Bitfinex-markkinoita",
        )
        save_state(session, state)
    refresh_prices(session)
    return execute_trading_cycle(session)


def stop_bot(session: SessionBase) -> dict[str, Any]:
    state = load_state(session)
    state["running"] = False
    save_state(session, state)
    return build_api_payload(state)


def reset_bot(session: SessionBase) -> dict[str, Any]:
    from .session_state import reset_state

    state = reset_state(session)
    return build_api_payload(state)
