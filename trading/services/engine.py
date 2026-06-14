import logging
import os
import threading
import time
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from .ai_trader import (
    analyze_ticker_quick,
    apply_gemini_insights,
    build_decision_report,
    build_deep_analysis,
    compute_market_regime,
    DEEP_ANALYSIS_TIME_BUDGET_SEC,
    enrich_display_timeframes,
    enrich_analyses_for_gemini,
    format_initial_buy_reason,
    make_trading_decisions,
)
from .bitfinex import fetch_all_markets, fetch_candles, get_crypto_label, ensure_portfolio_tickers
from .bitfinex import CANDLE_DEEP_LIMIT
from .gemini import advise_portfolio, get_status as gemini_status_snapshot, is_configured as gemini_configured
from .learning import compute_tuning
from .daily_policy_shadow import record_cycle, record_executed_trade, record_profit_take_shadow, shadow_profit_take_config
from .trade_meta import meta_from_analysis
from . import exit_learning
from . import market_learning
from . import market_microstructure
from .portfolio import Portfolio
from .sell_strategy import update_profit_sell
from .session_state import (
    build_api_payload,
    log_ai_event,
    log_watch_event,
)
from .state_store import load_state, save_state

logger = logging.getLogger(__name__)

_cycle_running = False
_cycle_started_at = 0.0
_cycle_running_lock = threading.Lock()
CYCLE_LOCK_STALE_SEC = int(os.environ.get("CYCLE_LOCK_STALE_SEC", "180"))

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
    deadline = time.time() + DEEP_ANALYSIS_TIME_BUDGET_SEC
    for symbol in list(state["portfolio"].get("holdings", {}).keys()):
        if time.time() >= deadline:
            break
        ticker = state["tickers"].get(symbol)
        if not ticker:
            continue
        try:
            candles = fetch_candles(symbol, "1h", CANDLE_DEEP_LIMIT)
            deep = build_deep_analysis(ticker, candles)
            state["analyses"][symbol] = deep
        except Exception:
            logger.warning("Holding enrich failed for %s", symbol, exc_info=True)


def _profit_take_config(state: dict[str, Any], regime: str) -> dict[str, Any]:
    from .learning import merge_regime_tuning
    from .sell_strategy import default_profit_take_config

    learning = merge_regime_tuning(state.get("learning") or {}, regime)
    cfg = dict(learning.get("profit_take_tuning") or {})
    if cfg.get("level", "off") == "off":
        cfg = default_profit_take_config()
        if regime == "bear":
            cfg.update({"trigger_scale": 0.88, "partial_trigger_scale": 0.9})
        elif regime == "neutral":
            cfg.update({"trigger_scale": 0.92, "partial_trigger_scale": 0.92})
        else:
            cfg.update({"trigger_scale": 0.95, "partial_trigger_scale": 0.95})
    return cfg


def _log_shadow_trade(
    state: dict[str, Any],
    flags: dict[str, Any],
    *,
    trade_type: str,
    symbol: str,
    eur_amount: float,
    reason: str,
    portfolio: Portfolio,
    price: float | None = None,
    amount: float | None = None,
) -> None:
    profit_loss = None
    if trade_type == "sell" and portfolio.trades:
        profit_loss = portfolio.trades[0].get("profitLoss")
    record_executed_trade(
        state,
        trade_type=trade_type,
        symbol=symbol,
        eur_amount=eur_amount,
        reason=reason,
        flags=flags,
        profit_loss=profit_loss,
        price=price,
        amount=amount,
    )


def _check_profit_sells(
    state: dict[str, Any],
    portfolio: Portfolio,
    shadow_flags: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    executed: list[dict[str, Any]] = []
    regime = (state.get("regime") or {}).get("regime", "neutral")
    pt_cfg = _profit_take_config(state, regime)
    shadow_cfg = shadow_profit_take_config(pt_cfg, shadow_flags or {})
    for symbol, holding in list(portfolio.holdings.items()):
        ticker = state["tickers"].get(symbol)
        if not ticker:
            continue

        atr_pct = (state["analyses"].get(symbol) or {}).get("atrPct")
        analysis = state["analyses"].get(symbol) or {}
        profit_pct = (
            ((ticker["last"] - holding["avgPrice"]) / holding["avgPrice"]) * 100
            if holding["avgPrice"]
            else 0.0
        )
        exit_learned = exit_learning.adjustments_for_analysis(analysis, regime, profit_pct)
        result = update_profit_sell(
            state["watches"],
            symbol,
            ticker["last"],
            holding["avgPrice"],
            atr_pct=atr_pct,
            profit_take_config=pt_cfg,
            analysis=analysis,
            exit_learned=exit_learned,
        )
        if shadow_flags:
            shadow_watches = deepcopy(state["watches"])
            shadow_result = update_profit_sell(
                shadow_watches,
                symbol,
                ticker["last"],
                holding["avgPrice"],
                atr_pct=atr_pct,
                profit_take_config=shadow_cfg,
                analysis=analysis,
                exit_learned=exit_learned,
            )
            record_profit_take_shadow(
                state,
                symbol=symbol,
                actual=result,
                shadow=shadow_result,
                holding_amount=holding["amount"],
                price=float(ticker["last"]),
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
                    profit_pct=result.get("profitPct"),
                    peak_price=result.get("peakPrice"),
                    pullback_pct=result.get("pullbackPct"),
                ),
            )
            trade_id = portfolio.trades[0].get("id") if portfolio.trades else None
            exit_learning.record_profit_take_exit(
                symbol=symbol,
                sell_price=float(ticker["last"]),
                peak_price=float(result.get("peakPrice") or ticker["last"]),
                profit_pct=float(result.get("profitPct") or 0),
                pullback_pct=float(result.get("pullbackPct") or 0),
                exit_setup=str(result.get("exitSetup") or exit_learned.get("exit_setup") or ""),
                trade_id=trade_id,
            )
            log_ai_event(state, "sell", get_crypto_label(symbol), result["reason"], eur_total)
            if shadow_flags:
                _log_shadow_trade(
                    state,
                    shadow_flags,
                    trade_type="sell",
                    symbol=symbol,
                    eur_amount=eur_total,
                    reason=result["reason"],
                    portfolio=portfolio,
                    price=float(ticker["last"]),
                    amount=sell_amount,
                )
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

        state["tickers"] = ensure_portfolio_tickers(
            state.get("portfolio", {}).get("holdings") or {},
            tickers,
        )
        for symbol, ticker in tickers.items():
            existing = state["analyses"].get(symbol)
            if not existing or existing.get("quick"):
                state["analyses"][symbol] = analyze_ticker_quick(ticker)

        state["lastPriceTick"] = int(time.time() * 1000)
        state["error"] = None

        if state.get("running", True):
            portfolio = Portfolio(state["portfolio"])
            total_value = portfolio.get_total_value(state["tickers"])
            regime = (state.get("regime") or {}).get("regime", "neutral")
            learning = state.get("learning") or {}
            shadow_flags = record_cycle(
                state,
                total_value=total_value,
                regime=regime,
                learning=learning,
            )
            _check_profit_sells(state, portfolio, shadow_flags=shadow_flags)
            try:
                exit_summary = exit_learning.step(state["tickers"])
                state["exitLearning"] = exit_summary
            except Exception:
                logger.warning("Exit learning step failed", exc_info=True)

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
    global _cycle_running, _cycle_started_at

    with _cycle_running_lock:
        if _cycle_running:
            stale_for = time.time() - _cycle_started_at
            if stale_for < CYCLE_LOCK_STALE_SEC:
                logger.info("Kaupankäyntikierros ohitetaan — edellinen vielä käynnissä")
                return build_api_payload(load_state())
            logger.warning(
                "Kaupankäyntikierros jumissa %.0f s — nollataan lukitus",
                stale_for,
            )
            _cycle_running = False
        _cycle_running = True
        _cycle_started_at = time.time()

    try:
        state = load_state()
        if state.get("error"):
            return build_api_payload(state)

        _refresh_analyses(state)
        _enrich_holdings(state)
        enrich_display_timeframes(
            state["tickers"],
            state["analyses"],
            fetch_candles,
            skip_symbols=set(state["portfolio"].get("holdings", {}).keys()),
        )

        # B + D: markkinaregiimi ja oppiminen lasketaan joka kierros (ilmaiseksi)
        regime_info = compute_market_regime(state["tickers"], state["analyses"])
        regime = regime_info["regime"]
        state["regime"] = regime_info
        learning = compute_tuning(
            state["portfolio"],
            state.get("geminiPickStats"),
        )
        state["learning"] = learning

        regime_str = regime_info["regime"]
        try:
            micro_summary = market_microstructure.enrich_analyses(
                state["tickers"],
                state["analyses"],
                state["portfolio"],
                regime_str,
            )
            state["microstructure"] = micro_summary
        except Exception:
            logger.warning("Microstructure enrich failed", exc_info=True)

        # Koko markkinan varjo-oppiminen: signaalit → toteutunut 1h/4h tuotto kaikille
        ml_stats: dict[str, Any] = {}
        try:
            ml_stats, ml_summary = market_learning.step(
                state["tickers"], state["analyses"], regime
            )
            from .market_learning_backfill import get_backfill_status, maybe_schedule_historical_backfill

            ml_summary.update(get_backfill_status())
            maybe_schedule_historical_backfill()
            state["marketLearning"] = ml_summary
            learning["market_setups"] = ml_summary
        except Exception:
            logger.warning("Market learning step failed", exc_info=True)

        try:
            exit_summary = exit_learning.step(state["tickers"])
            state["exitLearning"] = {**exit_summary, **exit_learning.get_summary()}
            learning["exit_learning"] = state["exitLearning"]
        except Exception:
            logger.warning("Exit learning step failed", exc_info=True)

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
                from .gemini import build_gemini_snapshot
                from .gemini_pick_tracking import archive_previous_snapshot, compute_pick_tuning

                archive_previous_snapshot(
                    state,
                    state["tickers"],
                    snap_value,
                    get_crypto_label,
                )
                pick_tuning, pick_notes = compute_pick_tuning(state.get("geminiPickStats"))
                learning.update(
                    {
                        "gemini_buy_min_confidence": pick_tuning["gemini_buy_min_confidence"],
                        "gemini_pick_buy_scale": pick_tuning["gemini_pick_buy_scale"],
                        "gemini_pick_stats": pick_tuning.get("gemini_pick_stats"),
                    }
                )
                if pick_notes:
                    base_note = learning.get("note") or ""
                    extra = " · ".join(pick_notes)
                    learning["note"] = f"{base_note} · {extra}" if base_note else extra
                state["learning"] = learning
                apply_gemini_insights(
                    state["analyses"],
                    gemini_insights,
                    gemini_buy_min_confidence=learning.get("gemini_buy_min_confidence"),
                )
                state["lastGeminiSnapshot"] = build_gemini_snapshot(
                    gemini_insights,
                    state["tickers"],
                    state["analyses"],
                    snap_value,
                    regime_info,
                    get_crypto_label,
                )
            else:
                # Kutsu epäonnistui — käytä viimeisintä onnistunutta analyysiä
                gemini_insights = state.get("geminiInsights")
                if gemini_insights:
                    apply_gemini_insights(
                        state["analyses"],
                        gemini_insights,
                        gemini_buy_min_confidence=learning.get("gemini_buy_min_confidence"),
                    )
        elif gemini_configured():
            # Throttle: käytä välimuistissa olevaa analyysiä, ei uutta API-kutsua
            gemini_insights = state.get("geminiInsights")
            if gemini_insights:
                apply_gemini_insights(
                    state["analyses"],
                    gemini_insights,
                    gemini_buy_min_confidence=learning.get("gemini_buy_min_confidence"),
                )
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
        total_value = portfolio.get_total_value(state["tickers"])
        shadow_flags = record_cycle(
            state,
            total_value=total_value,
            regime=regime,
            learning=learning,
        )
        profit_sells = _check_profit_sells(state, portfolio, shadow_flags=shadow_flags)

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
                trade = next(
                    (t for t in portfolio.trades if t.get("type") == "buy" and t.get("symbol") == symbol),
                    None,
                )
                if trade:
                    _log_shadow_trade(
                        state,
                        shadow_flags,
                        trade_type="buy",
                        symbol=symbol,
                        eur_amount=float(trade.get("eurTotal") or eur_amount or 0),
                        reason=trade.get("reason") or "",
                        portfolio=portfolio,
                        price=float(trade.get("price") or 0),
                        amount=float(trade.get("amount") or 0),
                    )
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
            _log_shadow_trade(
                state,
                shadow_flags,
                trade_type="sell",
                symbol=d["symbol"],
                eur_amount=float(d.get("eurAmount") or 0),
                reason=d["reason"],
                portfolio=portfolio,
                price=float(analysis["currentPrice"]),
                amount=float(d.get("amount") or 0),
            )
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
                _log_shadow_trade(
                    state,
                    shadow_flags,
                    trade_type="buy",
                    symbol=d["symbol"],
                    eur_amount=float(d.get("eurAmount") or 0),
                    reason=d["reason"],
                    portfolio=portfolio,
                    price=float(analysis["currentPrice"]),
                    amount=float(d.get("amount") or 0),
                )
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
        save_state(state)

        payload = build_api_payload(state)
        payload["lastUpdate"] = _now_iso()
        return payload
    finally:
        with _cycle_running_lock:
            _cycle_running = False
