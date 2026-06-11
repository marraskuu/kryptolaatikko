import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

from .ai_trader import (
    analyze_ticker_quick,
    apply_gemini_insights,
    build_decision_report,
    build_deep_analysis,
    compute_market_regime,
    enrich_analyses_for_gemini,
    format_initial_buy_reason,
    make_trading_decisions,
)
from .bitfinex import fetch_all_markets, fetch_candles, get_crypto_label
from .gemini import advise_portfolio, get_status as gemini_status_snapshot, is_configured as gemini_configured
from .learning import compute_tuning
from .learning_report import build_learning_report, maybe_refresh_narrative
from .trade_meta import meta_from_analysis
from . import market_learning
from .portfolio import Portfolio
from .sell_strategy import update_profit_sell
from .session_state import (
    build_api_payload,
    log_ai_event,
    log_watch_event,
)
from .state_store import load_state, save_state

logger = logging.getLogger(__name__)

# Gemini-kutsuväli sekunteina — kytketty irti 60 s kaupankäyntikierroksesta
# kustannusten hillitsemiseksi. Tekninen analyysi pyörii joka kierroksella.
GEMINI_INTERVAL_SEC = int(os.environ.get("GEMINI_INTERVAL_SEC", "600"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Hitaasti muuttuvat tekniset mittarit kannetaan eteenpäin Gemini-kutsujen välillä,
# mutta hinta ja 24h-muutos päivittyvät joka kierroksella tuoreesta tickeristä.
_CARRY_FORWARD_KEYS = (
    "atrPct",
    "mtfAlign",
    "ema9",
    "ema21",
    "rsi",
    "change1hPct",
    "change4hPct",
    "recentReturns",
)


def _refresh_analyses(state: dict[str, Any]) -> None:
    for symbol, ticker in state["tickers"].items():
        prev = state["analyses"].get(symbol) or {}
        fresh = analyze_ticker_quick(ticker)
        for key in _CARRY_FORWARD_KEYS:
            if prev.get(key) is not None:
                fresh[key] = prev[key]
        state["analyses"][symbol] = fresh


def _enrich_holdings(state: dict[str, Any]) -> None:
    """Päivitä omistettujen kolikoiden ATR/EMA/momentum tuoreesta candle-datasta joka kierros."""
    for symbol in list(state["portfolio"].get("holdings", {}).keys()):
        ticker = state["tickers"].get(symbol)
        if not ticker:
            continue
        try:
            candles = fetch_candles(symbol, "1h", 50)
            deep = build_deep_analysis(ticker, candles)
            state["analyses"][symbol] = deep
        except Exception:
            logger.warning("Holding enrich failed for %s", symbol, exc_info=True)


def _check_profit_sells(state: dict[str, Any], portfolio: Portfolio) -> list[dict[str, Any]]:
    executed: list[dict[str, Any]] = []
    regime = (state.get("regime") or {}).get("regime", "neutral")
    for symbol, holding in list(portfolio.holdings.items()):
        ticker = state["tickers"].get(symbol)
        if not ticker:
            continue

        atr_pct = (state["analyses"].get(symbol) or {}).get("atrPct")
        result = update_profit_sell(
            state["watches"],
            symbol,
            ticker["last"],
            holding["avgPrice"],
            atr_pct=atr_pct,
        )
        state["profitWatch"][symbol] = result

        if result["shouldSell"]:
            frac = max(0.0, min(1.0, result.get("sellFraction", 1.0)))
            sell_amount = holding["amount"] * frac
            if sell_amount <= 0:
                continue
            eur_total = sell_amount * ticker["last"]
            portfolio.sell(
                symbol,
                sell_amount,
                ticker["last"],
                result["reason"],
                meta=meta_from_analysis(
                    state["analyses"].get(symbol),
                    regime,
                    for_sell=True,
                ),
            )
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
            # Täysi myynti vapauttaa seurannan; osittainen (porras 1) jättää lopun
            # trailing-stopin seurattavaksi.
            if frac >= 0.999:
                state["watches"].pop(symbol, None)
                state["profitWatch"].pop(symbol, None)

    state["portfolio"] = portfolio.to_dict()
    return executed


def refresh_prices() -> dict[str, Any]:
    state = load_state()
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

        if state.get("running", True):
            portfolio = Portfolio(state["portfolio"])
            _check_profit_sells(state, portfolio)

            report = state.get("lastAIReport")
            if report:
                watches = []
                for symbol in portfolio.holdings:
                    watch = state["profitWatch"].get(symbol)
                    if watch and watch.get("status") in ("waiting", "armed", "uptrend"):
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

    save_state(state)
    payload = build_api_payload(state)
    payload["error"] = state.get("error")
    payload["lastUpdate"] = _now_iso()
    return payload


def execute_trading_cycle() -> dict[str, Any]:
    refresh_prices()
    state = load_state()
    if state.get("error"):
        return build_api_payload(state)

    _refresh_analyses(state)
    _enrich_holdings(state)

    # B + D: markkinaregiimi ja oppiminen lasketaan joka kierros (ilmaiseksi)
    regime_info = compute_market_regime(state["tickers"], state["analyses"])
    regime = regime_info["regime"]
    state["regime"] = regime_info
    learning = compute_tuning(state["portfolio"])
    state["learning"] = learning

    # Koko markkinan varjo-oppiminen: signaalit → toteutunut 1h/4h tuotto kaikille
    ml_stats: dict[str, Any] = {}
    try:
        ml_stats, ml_summary = market_learning.step(
            state["tickers"], state["analyses"], regime
        )
        state["marketLearning"] = ml_summary
        learning["market_setups"] = ml_summary
    except Exception:
        logger.warning("Market learning step failed", exc_info=True)

    gemini_insights = None
    now_ms = int(time.time() * 1000)
    last_gemini_ms = state.get("lastGeminiTick") or 0
    due_for_gemini = (now_ms - last_gemini_ms) >= GEMINI_INTERVAL_SEC * 1000

    if gemini_configured() and due_for_gemini:
        enrich_analyses_for_gemini(
            state["tickers"],
            state["analyses"],
            state["portfolio"],
            fetch_candles,
        )
        gemini_insights, gemini_status = advise_portfolio(
            state["tickers"],
            state["analyses"],
            state["portfolio"],
            get_crypto_label,
            last_gemini_snapshot=state.get("lastGeminiSnapshot"),
            regime=regime_info,
            learning=learning,
        )
        if gemini_insights and gemini_status.get("ok"):
            apply_gemini_insights(state["analyses"], gemini_insights)
            state["lastGeminiTick"] = now_ms
            state["geminiInsights"] = gemini_insights
            log_ai_event(
                state,
                "info",
                "Gemini",
                gemini_status.get("message", "Analyysi valmis"),
            )
            portfolio_for_snap = Portfolio(state["portfolio"])
            snap_value = portfolio_for_snap.get_total_value(state["tickers"])
            state["lastGeminiSnapshot"] = {
                "timestamp": _now_iso(),
                "top_picks": list(gemini_insights.get("top_picks") or []),
                "total_value": round(snap_value, 2),
            }
        else:
            # Kutsu epäonnistui — käytä viimeisintä onnistunutta analyysiä
            gemini_insights = state.get("geminiInsights")
            if gemini_insights:
                apply_gemini_insights(state["analyses"], gemini_insights)
    elif gemini_configured():
        # Throttle: käytä välimuistissa olevaa analyysiä, ei uutta API-kutsua
        gemini_insights = state.get("geminiInsights")
        if gemini_insights:
            apply_gemini_insights(state["analyses"], gemini_insights)
        gemini_status = state.get("geminiStatus") or gemini_status_snapshot()
    else:
        gemini_status = gemini_status_snapshot()
    state["geminiStatus"] = gemini_status

    # Liitä opittu olosuhdesäätö lopullisiin analyyseihin (myös Gemini-syväanalyysin jälkeen)
    try:
        market_learning.apply(state["analyses"], regime, ml_stats)
    except Exception:
        logger.warning("Market learning apply failed", exc_info=True)

    portfolio = Portfolio(state["portfolio"])
    profit_sells = _check_profit_sells(state, portfolio)

    total_value = portfolio.get_total_value(state["tickers"])
    gemini_picks = (gemini_insights or {}).get("top_picks") if gemini_insights else None
    decision_result = make_trading_decisions(
        state["analyses"],
        portfolio.to_dict(),
        total_value,
        get_crypto_label,
        gemini_insights=gemini_insights,
        gemini_picks=gemini_picks,
        regime=regime,
        learning=learning,
    )
    decisions = decision_result["decisions"]
    state["activeSymbols"] = decision_result.get("topSymbols", [])

    executed_buys: list[dict[str, Any]] = []
    executed_sells = [
        {**s, "analysis": state["analyses"].get(s["symbol"])}
        for s in profit_sells
    ]

    initial_allocation = decision_result.get("initialAllocation") or []
    gemini_active = decision_result.get("geminiActive", False)
    if initial_allocation:
        slots = [
            {
                "symbol": item["symbol"],
                "price": item["analysis"]["currentPrice"],
                "eur_amount": item.get("eurAmount"),
                "atrPct": (item.get("analysis") or {}).get("atrPct"),
                "tradeMeta": meta_from_analysis(item.get("analysis"), regime),
                "reason": format_initial_buy_reason(
                    item["analysis"],
                    get_crypto_label(item["symbol"]),
                    i + 1,
                    len(initial_allocation),
                    gemini_active,
                    alloc_pct=item.get("allocPct"),
                    eur_amount=item.get("eurAmount"),
                ),
            }
            for i, item in enumerate(initial_allocation)
        ]
        portfolio.allocate_initial(slots)
        for i, item in enumerate(initial_allocation):
            symbol = item["symbol"]
            label = get_crypto_label(symbol)
            eur_amount = item.get("eurAmount")
            reason = format_initial_buy_reason(
                item["analysis"],
                label,
                i + 1,
                len(initial_allocation),
                gemini_active,
                alloc_pct=item.get("allocPct"),
                eur_amount=eur_amount,
            )
            log_ai_event(state, "buy", label, reason, eur_amount)
            executed_buys.append(
                {
                    "symbol": symbol,
                    "label": label,
                    "amount": eur_amount,
                    "reason": reason,
                    "analysis": item["analysis"],
                }
            )

    for d in [x for x in decisions if x["type"] == "sell"]:
        analysis = d.get("analysis") or {}
        portfolio.sell(
            d["symbol"],
            d["amount"],
            analysis["currentPrice"],
            d["reason"],
            meta=meta_from_analysis(analysis, regime, for_sell=True),
        )
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
        analysis = d.get("analysis") or {}
        ok = portfolio.buy(
            d["symbol"],
            d["eurAmount"],
            analysis["currentPrice"],
            d["reason"],
            meta=meta_from_analysis(analysis, regime),
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
        if watch and watch.get("status") in ("waiting", "armed", "uptrend"):
            watches.append(
                {
                    "symbol": symbol,
                    "label": get_crypto_label(symbol),
                    "reason": watch.get("statusText"),
                    "profitPct": watch.get("profitPct"),
                }
            )

    report = build_decision_report(
        decisions,
        get_crypto_label,
        gemini_active=decision_result.get("geminiActive", False),
    )
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
    state["running"] = True

    learning_report = build_learning_report(
        learning=learning,
        market_learning=state.get("marketLearning"),
        regime=regime_info,
        portfolio=state["portfolio"],
        previous_snapshot=state.get("learningReportSnapshot"),
        narrative=state.get("learningNarrative"),
        last_narrative_at=state.get("lastLearningNarrativeAt"),
    )
    learning_report = maybe_refresh_narrative(state, learning_report)
    state["learningReport"] = learning_report

    save_state(state)

    payload = build_api_payload(state)
    payload["lastUpdate"] = _now_iso()
    return payload
