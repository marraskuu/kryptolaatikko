"""Varjopolitiikka — kerää counterfactual-dataa ilman live-kaupankäynnin muutosta.

Simuloi sääntöjä:
  - päivästop −1 %
  - profit lock +0,5 % / +1 % (tiukempi voitto-otto)
  - aggressiivinen tila vain regiimi + setup -datalla

Data tallentuu state["dailyPolicyShadow"] ja API:in kautta UI:hin.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from .ai_trader import _is_emergency_trade_reason
from .bitfinex import normalize_symbol
from .portfolio import Portfolio
from .sell_strategy import default_profit_take_config, update_profit_sell

DAILY_STOP_PCT = -1.0
PROFIT_LOCK_SOFT_PCT = 0.5
PROFIT_LOCK_FIRM_PCT = 1.0
MIN_WIN_RATE_AGGRESSIVE = 0.40
MIN_SAMPLES_AGGRESSIVE = 6
EVENT_LIMIT = 60
DAY_LIMIT = 45
OPEN_BLOCKED_BUY_LIMIT = 40


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def day_key_utc(dt: datetime | None = None) -> str:
    dt = dt or datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%d")


def default_shadow_state() -> dict[str, Any]:
    return {
        "version": 3,
        "dayKey": None,
        "dayStartValue": None,
        "dayStartAt": None,
        "cyclesToday": 0,
        "today": {},
        "summary": {
            "cyclesTotal": 0,
            "tradesLogged": 0,
            "buysWouldBlock": 0,
            "sellsWouldBlock": 0,
            "buyBlockEur": 0.0,
            "sellBlockCounterfactualEur": 0.0,
            "blockedBuyOpenEur": 0.0,
            "blockedBuyCounterfactualEur": 0.0,
            "profitTakeShadowSignals": 0,
            "profitTakeShadowEurEst": 0.0,
            "netCounterfactualEur": 0.0,
            "daysTracked": 0,
        },
        "openBlockedBuys": [],
        "events": [],
        "days": [],
    }


def _get_shadow(state: dict[str, Any]) -> dict[str, Any]:
    shadow = state.get("dailyPolicyShadow")
    if not shadow:
        shadow = default_shadow_state()
        state["dailyPolicyShadow"] = shadow
        return shadow

    version = shadow.get("version", 1)
    if version < 2:
        # v1 laski voitto-ottosignaalin joka minuutti uudelleen → inflatoitunut summa.
        summary = shadow.setdefault("summary", {})
        summary["profitTakeShadowSignals"] = 0
        summary["profitTakeShadowEurEst"] = 0.0
        shadow["_prevShadowSell"] = {}
        shadow["version"] = 2
        _recompute_net(summary)
        state["dailyPolicyShadow"] = shadow
        version = 2
    if version < 3:
        shadow["version"] = 3
        shadow.pop("shadowPortfolio", None)
        shadow.pop("portfolioComparison", None)
        state["dailyPolicyShadow"] = shadow
    return shadow


def _aggregate_win_rate(learning: dict[str, Any]) -> tuple[int, float]:
    stats = learning.get("stats") or {}
    wins = 0
    n = 0
    for block in stats.values():
        tn = int(block.get("trades") or 0)
        n += tn
        wins += int(round(tn * float(block.get("win_rate") or 0)))
    return n, (wins / n if n else 0.5)


def evaluate_policy(
    day_pnl_pct: float,
    regime: str,
    learning: dict[str, Any] | None,
) -> dict[str, Any]:
    learning = learning or {}
    n, win_rate = _aggregate_win_rate(learning)
    overall_exp = float(learning.get("overall_expectancy_eur") or 0)
    samples = int(learning.get("samples") or 0)

    daily_stop = day_pnl_pct <= DAILY_STOP_PCT
    if day_pnl_pct >= PROFIT_LOCK_FIRM_PCT:
        profit_tier = "firm"
    elif day_pnl_pct >= PROFIT_LOCK_SOFT_PCT:
        profit_tier = "soft"
    else:
        profit_tier = "none"

    aggressive = (
        regime in ("bull", "neutral")
        and not daily_stop
        and profit_tier != "firm"
        and samples >= MIN_SAMPLES_AGGRESSIVE
        and overall_exp >= 0
        and win_rate >= MIN_WIN_RATE_AGGRESSIVE
        and day_pnl_pct >= -0.3
    )

    return {
        "dayPnlPct": round(day_pnl_pct, 3),
        "dailyStopActive": daily_stop,
        "profitLockTier": profit_tier,
        "aggressiveEligible": aggressive,
        "winRate": round(win_rate, 3),
        "overallExp": round(overall_exp, 3),
    }


def shadow_profit_take_config(
    actual_cfg: dict[str, Any] | None,
    flags: dict[str, Any],
) -> dict[str, Any]:
    cfg = dict(actual_cfg or default_profit_take_config())
    tier = flags.get("profitLockTier", "none")
    if tier == "soft":
        cfg["trigger_scale"] = float(cfg.get("trigger_scale", 1.0)) * 0.85
        cfg["partial_trigger_scale"] = float(cfg.get("partial_trigger_scale", 1.0)) * 0.9
    elif tier == "firm":
        cfg["trigger_scale"] = float(cfg.get("trigger_scale", 1.0)) * 0.75
        cfg["partial_trigger_scale"] = float(cfg.get("partial_trigger_scale", 1.0)) * 0.85
    return cfg


def is_discretionary_sell(reason: str) -> bool:
    if not reason:
        return False
    if _is_emergency_trade_reason(reason):
        return False
    lower = reason.lower()
    skip = (
        "stop-loss",
        "aikastoppi",
        "krooninen",
        "cooldown",
        "stablecoin",
        "matala volyymi",
        "estetty kohde",
        "kotiut",
        "realisoidaan voitto",
        "huipusta",
        "trailing-stop",
        "porras",
        "tasaantui",
    )
    return not any(k in lower for k in skip)


def would_block_buy(flags: dict[str, Any]) -> tuple[bool, str | None]:
    if flags.get("dailyStopActive"):
        return True, "daily_stop"
    if flags.get("profitLockTier") == "firm":
        return True, "profit_lock_firm"
    return False, None


def would_block_discretionary_sell(flags: dict[str, Any]) -> tuple[bool, str | None]:
    if flags.get("dailyStopActive"):
        return True, "daily_stop"
    if flags.get("profitLockTier") == "firm":
        return True, "profit_lock_firm"
    return False, None


def _day_pnl(total_value: float, day_start: float | None) -> tuple[float, float]:
    if not day_start or day_start <= 0:
        return 0.0, 0.0
    eur = total_value - day_start
    pct = (eur / day_start) * 100
    return round(eur, 2), round(pct, 3)


def _append_event(shadow: dict[str, Any], event: dict[str, Any]) -> None:
    events = shadow.setdefault("events", [])
    events.insert(0, event)
    shadow["events"] = events[:EVENT_LIMIT]


def _recompute_net(summary: dict[str, Any]) -> None:
    summary["netCounterfactualEur"] = round(
        float(summary.get("sellBlockCounterfactualEur") or 0)
        + float(summary.get("blockedBuyCounterfactualEur") or 0)
        + float(summary.get("profitTakeShadowEurEst") or 0),
        2,
    )


def fork_shadow_portfolio(state: dict[str, Any]) -> None:
    """Kopioi live-salkku varjopolitiikan lähtökohdaksi (päivän alussa tai ensimmäisellä kierroksella)."""
    shadow = _get_shadow(state)
    live = state.get("portfolio") or {}
    shadow["shadowPortfolio"] = deepcopy(live)
    tickers = state.get("tickers") or {}
    sp = Portfolio(shadow["shadowPortfolio"])
    start_value = sp.get_total_value(tickers) if tickers else float(live.get("cash") or 0)
    shadow["shadowDayStartValue"] = round(start_value, 2)
    shadow["portfolioMetrics"] = {"tradesMirrored": 0, "tradesSkipped": 0}


def should_mirror_trade(
    trade_type: str,
    reason: str,
    flags: dict[str, Any],
) -> tuple[bool, str | None]:
    """Päätä peilataanko live-kauppa varjosalkkuun (päivästop / profit lock)."""
    if trade_type == "buy":
        blocked, block_reason = would_block_buy(flags)
        return (not blocked, block_reason)
    if trade_type == "sell":
        if not is_discretionary_sell(reason):
            return True, None
        blocked, block_reason = would_block_discretionary_sell(flags)
        return (not blocked, block_reason)
    return True, None


def mirror_live_trade(
    state: dict[str, Any],
    *,
    trade_type: str,
    symbol: str,
    eur_amount: float,
    reason: str,
    flags: dict[str, Any],
    price: float,
    amount: float,
    meta: dict[str, Any] | None = None,
) -> bool:
    """Peilaa live-kaupan varjosalkkuun jos säännöt sallivat."""
    shadow = _get_shadow(state)
    if not shadow.get("shadowPortfolio"):
        fork_shadow_portfolio(state)

    mirror, _skip = should_mirror_trade(trade_type, reason, flags)
    metrics = shadow.setdefault("portfolioMetrics", {"tradesMirrored": 0, "tradesSkipped": 0})
    today = shadow.setdefault("today", {})

    if not mirror:
        metrics["tradesSkipped"] = int(metrics.get("tradesSkipped") or 0) + 1
        today["tradesSkippedToday"] = int(today.get("tradesSkippedToday") or 0) + 1
        return False

    sp = Portfolio(shadow["shadowPortfolio"])
    if trade_type == "buy":
        ok = sp.buy(symbol, eur_amount, price, reason, meta=meta)
    else:
        ok = sp.sell(symbol, amount, price, reason, meta=meta)

    if ok:
        shadow["shadowPortfolio"] = sp.to_dict()
        metrics["tradesMirrored"] = int(metrics.get("tradesMirrored") or 0) + 1
        today["tradesMirroredToday"] = int(today.get("tradesMirroredToday") or 0) + 1
    return ok


def sync_shadow_valuation(state: dict[str, Any], live_total_value: float) -> None:
    """Päivitä varjo vs. live -vertailu markkinahintojen mukaan."""
    shadow = _get_shadow(state)
    if not shadow.get("shadowPortfolio"):
        fork_shadow_portfolio(state)

    tickers = state.get("tickers") or {}
    sp = Portfolio(shadow.get("shadowPortfolio") or {})
    shadow_value = (
        sp.get_total_value(tickers)
        if tickers
        else float(shadow.get("shadowDayStartValue") or live_total_value)
    )

    day_start_live = float(shadow.get("dayStartValue") or live_total_value)
    day_start_shadow = float(shadow.get("shadowDayStartValue") or shadow_value)
    shadow_today_pnl = round(shadow_value - day_start_shadow, 2)
    shadow_today_pct = (
        round((shadow_today_pnl / day_start_shadow) * 100, 3) if day_start_shadow > 0 else 0.0
    )

    mirrored = int(shadow.get("portfolioMetrics", {}).get("tradesMirrored") or 0)
    skipped = int(shadow.get("portfolioMetrics", {}).get("tradesSkipped") or 0)
    shadow["portfolioComparison"] = {
        "liveTotalValue": round(live_total_value, 2),
        "shadowTotalValue": round(shadow_value, 2),
        "advantageEur": round(shadow_value - live_total_value, 2),
        "shadowTodayPnlEur": shadow_today_pnl,
        "shadowTodayPnlPct": shadow_today_pct,
        "liveTodayPnlEur": round(live_total_value - day_start_live, 2),
        "tradesMirrored": mirrored,
        "tradesSkipped": skipped,
        "reliable": mirrored + skipped >= 3,
    }


def _roll_day_if_needed(shadow: dict[str, Any], state: dict[str, Any], total_value: float) -> None:
    key = day_key_utc()
    if shadow.get("dayKey") == key:
        return

    prev_today = shadow.get("today") or {}
    if shadow.get("dayKey") and prev_today:
        pc = shadow.get("portfolioComparison") or {}
        days = shadow.setdefault("days", [])
        days.insert(
            0,
            {
                "dayKey": shadow["dayKey"],
                "startValue": shadow.get("dayStartValue"),
                "endValue": round(total_value, 2),
                "shadowEndValue": pc.get("shadowTotalValue"),
                "shadowDayPnlEur": pc.get("shadowTodayPnlEur"),
                "advantageEur": pc.get("advantageEur"),
                **prev_today,
            },
        )
        shadow["days"] = days[:DAY_LIMIT]
        summary = shadow["summary"]
        summary["daysTracked"] = len(shadow["days"]) + (1 if shadow.get("dayKey") else 0)

    shadow["dayKey"] = key
    shadow["dayStartValue"] = round(total_value, 2)
    shadow["dayStartAt"] = _now_iso()
    shadow["cyclesToday"] = 0
    shadow["today"] = {
        "realPnlEur": 0.0,
        "realPnlPct": 0.0,
        "cyclesDailyStop": 0,
        "cyclesProfitSoft": 0,
        "cyclesProfitFirm": 0,
        "cyclesAggressive": 0,
        "buysWouldBlockToday": 0,
        "sellsWouldBlockToday": 0,
        "tradesMirroredToday": 0,
        "tradesSkippedToday": 0,
    }
    fork_shadow_portfolio(state)


def record_cycle(
    state: dict[str, Any],
    *,
    total_value: float,
    regime: str,
    learning: dict[str, Any] | None,
) -> dict[str, Any]:
    """Päivitä päivämittarit ja avoimien estettyjen ostojen counterfactual."""
    shadow = _get_shadow(state)
    _roll_day_if_needed(shadow, state, total_value)

    day_start = float(shadow.get("dayStartValue") or total_value)
    pnl_eur, pnl_pct = _day_pnl(total_value, day_start)
    flags = evaluate_policy(pnl_pct, regime, learning)

    today = shadow["today"]
    today["realPnlEur"] = pnl_eur
    today["realPnlPct"] = pnl_pct
    today["policy"] = flags
    shadow["cyclesToday"] = int(shadow.get("cyclesToday") or 0) + 1
    summary = shadow["summary"]
    summary["cyclesTotal"] = int(summary.get("cyclesTotal") or 0) + 1

    if flags["dailyStopActive"]:
        today["cyclesDailyStop"] = int(today.get("cyclesDailyStop") or 0) + 1
    if flags["profitLockTier"] == "soft":
        today["cyclesProfitSoft"] = int(today.get("cyclesProfitSoft") or 0) + 1
    elif flags["profitLockTier"] == "firm":
        today["cyclesProfitFirm"] = int(today.get("cyclesProfitFirm") or 0) + 1
    if flags["aggressiveEligible"]:
        today["cyclesAggressive"] = int(today.get("cyclesAggressive") or 0) + 1

    _update_blocked_buy_mtm(shadow, state, total_value)
    sync_shadow_valuation(state, total_value)
    return flags


def _update_blocked_buy_mtm(
    shadow: dict[str, Any],
    state: dict[str, Any],
    total_value: float,
) -> None:
    portfolio = Portfolio(state.get("portfolio") or {})
    tickers = state.get("tickers") or {}
    open_buys = shadow.get("openBlockedBuys") or []
    if not open_buys:
        shadow["summary"]["blockedBuyOpenEur"] = 0.0
        return

    counterfactual = 0.0
    open_eur = 0.0
    for item in open_buys:
        sym = item.get("symbol")
        norm = normalize_symbol(sym or "")
        holding = portfolio.holdings.get(sym) or portfolio.holdings.get(norm)
        eur_amount = float(item.get("eurAmount") or 0)
        open_eur += eur_amount
        if not holding:
            continue
        ticker = tickers.get(sym) or tickers.get(norm)
        if not ticker or not ticker.get("last"):
            continue
        buy_price = float(item.get("buyPrice") or 0)
        amount = float(item.get("amount") or 0)
        if buy_price <= 0 or amount <= 0:
            continue
        current = amount * float(ticker["last"])
        cost = amount * buy_price
        unrealized = current - cost
        item["unrealizedPnl"] = round(unrealized, 2)
        counterfactual += -unrealized

    summary = shadow["summary"]
    summary["blockedBuyOpenEur"] = round(open_eur, 2)
    summary["blockedBuyCounterfactualEur"] = round(counterfactual, 2)
    _recompute_net(summary)


def record_executed_trade(
    state: dict[str, Any],
    *,
    trade_type: str,
    symbol: str,
    eur_amount: float,
    reason: str,
    flags: dict[str, Any],
    profit_loss: float | None = None,
    price: float | None = None,
    amount: float | None = None,
) -> None:
    """Kirjaa kauppa ja counterfactual-arvio."""
    shadow = _get_shadow(state)
    summary = shadow["summary"]
    summary["tradesLogged"] = int(summary.get("tradesLogged") or 0) + 1
    today = shadow.setdefault("today", {})

    event: dict[str, Any] = {
        "timestamp": _now_iso(),
        "type": trade_type,
        "symbol": symbol,
        "eurAmount": round(float(eur_amount or 0), 2),
        "reason": (reason or "")[:120],
        "policy": flags,
        "profitLoss": round(float(profit_loss), 2) if profit_loss is not None else None,
    }

    if trade_type == "buy":
        blocked, block_reason = would_block_buy(flags)
        event["wouldBlock"] = blocked
        event["blockReason"] = block_reason
        if blocked:
            summary["buysWouldBlock"] = int(summary.get("buysWouldBlock") or 0) + 1
            summary["buyBlockEur"] = round(
                float(summary.get("buyBlockEur") or 0) + float(eur_amount or 0),
                2,
            )
            today["buysWouldBlockToday"] = int(today.get("buysWouldBlockToday") or 0) + 1
            open_buys = shadow.setdefault("openBlockedBuys", [])
            open_buys.append(
                {
                    "symbol": symbol,
                    "eurAmount": round(float(eur_amount or 0), 2),
                    "buyPrice": float(price or 0),
                    "amount": float(amount or 0),
                    "timestamp": event["timestamp"],
                }
            )
            shadow["openBlockedBuys"] = open_buys[-OPEN_BLOCKED_BUY_LIMIT:]

    elif trade_type == "sell" and is_discretionary_sell(reason):
        blocked, block_reason = would_block_discretionary_sell(flags)
        event["wouldBlock"] = blocked
        event["blockReason"] = block_reason
        if blocked and profit_loss is not None:
            summary["sellsWouldBlock"] = int(summary.get("sellsWouldBlock") or 0) + 1
            today["sellsWouldBlockToday"] = int(today.get("sellsWouldBlockToday") or 0) + 1
            delta = -float(profit_loss)
            summary["sellBlockCounterfactualEur"] = round(
                float(summary.get("sellBlockCounterfactualEur") or 0) + delta,
                2,
            )
            event["counterfactualEur"] = round(delta, 2)

    if trade_type == "sell":
        shadow.setdefault("_prevShadowSell", {}).pop(normalize_symbol(symbol), None)

    _append_event(shadow, event)
    _recompute_net(summary)


def record_profit_take_shadow(
    state: dict[str, Any],
    *,
    symbol: str,
    actual: dict[str, Any],
    shadow: dict[str, Any],
    holding_amount: float,
    price: float,
) -> None:
    """Kirjaa kun varjopolitiikka olisi myynyt voitto-oton aiemmin (kerran per signaali)."""
    if not shadow.get("shouldSell") or actual.get("shouldSell"):
        policy_shadow = _get_shadow(state)
        prev = policy_shadow.setdefault("_prevShadowSell", {})
        prev.pop(normalize_symbol(symbol), None)
        return

    norm = normalize_symbol(symbol)
    policy_shadow = _get_shadow(state)
    prev = policy_shadow.setdefault("_prevShadowSell", {})
    if prev.get(norm):
        return
    prev[norm] = True

    profit_pct = float(shadow.get("profitPct") or 0)
    if profit_pct <= 0:
        return
    frac = float(shadow.get("sellFraction") or 1.0)
    est_eur = holding_amount * frac * price * (profit_pct / 100.0)
    if est_eur <= 0.05:
        return

    summary = policy_shadow["summary"]
    summary["profitTakeShadowSignals"] = int(summary.get("profitTakeShadowSignals") or 0) + 1
    summary["profitTakeShadowEurEst"] = round(
        float(summary.get("profitTakeShadowEurEst") or 0) + est_eur,
        2,
    )
    _recompute_net(summary)
    _append_event(
        policy_shadow,
        {
            "timestamp": _now_iso(),
            "type": "profit_take_shadow",
            "symbol": symbol,
            "profitPct": round(profit_pct, 2),
            "estEur": round(est_eur, 2),
            "actualStatus": actual.get("status"),
            "shadowStatus": shadow.get("status"),
            "policy": (policy_shadow.get("today") or {}).get("policy"),
        },
    )


def _compute_year_pnl(shadow: dict[str, Any], year: int | None = None) -> dict[str, Any]:
    """Kumulatiivinen varjopolitiikan päivä-P/L valitulle vuodelle."""
    year = year or datetime.now(timezone.utc).year
    prefix = f"{year}-"

    pnl_eur = 0.0
    year_start: float | None = None
    days_count = 0
    earliest_key: str | None = None

    for day in shadow.get("days") or []:
        key = day.get("dayKey") or ""
        if not key.startswith(prefix):
            continue
        pnl_eur += float(day.get("realPnlEur") or 0)
        days_count += 1
        if earliest_key is None or key < earliest_key:
            earliest_key = key
            start = day.get("startValue")
            if start is not None:
                year_start = float(start)

    current_key = shadow.get("dayKey") or ""
    today = shadow.get("today") or {}
    if current_key.startswith(prefix):
        pnl_eur += float(today.get("realPnlEur") or 0)
        days_count += 1
        if earliest_key is None or current_key < earliest_key:
            year_start = float(shadow.get("dayStartValue") or 0) or year_start

    pnl_eur = round(pnl_eur, 2)
    pnl_pct = round((pnl_eur / year_start) * 100, 3) if year_start and year_start > 0 else None

    return {
        "year": year,
        "pnlEur": pnl_eur,
        "pnlPct": pnl_pct,
        "yearStartValue": round(year_start, 2) if year_start else None,
        "daysInYear": days_count,
    }


def _compute_shadow_year_pnl(shadow: dict[str, Any], year: int | None = None) -> dict[str, Any]:
    """Kumulatiivinen varjosalkun päivä-P/L valitulle vuodelle."""
    year = year or datetime.now(timezone.utc).year
    prefix = f"{year}-"

    pnl_eur = 0.0
    year_start: float | None = None
    days_count = 0
    earliest_key: str | None = None

    for day in shadow.get("days") or []:
        key = day.get("dayKey") or ""
        if not key.startswith(prefix):
            continue
        shadow_day = day.get("shadowDayPnlEur")
        if shadow_day is not None:
            pnl_eur += float(shadow_day)
        days_count += 1
        if earliest_key is None or key < earliest_key:
            earliest_key = key
            start = day.get("shadowEndValue") or day.get("startValue")
            if start is not None and year_start is None:
                year_start = float(day.get("startValue") or start)

    pc = shadow.get("portfolioComparison") or {}
    current_key = shadow.get("dayKey") or ""
    if current_key.startswith(prefix):
        if pc.get("shadowTodayPnlEur") is not None:
            pnl_eur += float(pc["shadowTodayPnlEur"])
        days_count += 1
        if year_start is None:
            year_start = float(shadow.get("shadowDayStartValue") or shadow.get("dayStartValue") or 0) or None

    pnl_eur = round(pnl_eur, 2)
    pnl_pct = round((pnl_eur / year_start) * 100, 3) if year_start and year_start > 0 else None

    return {
        "year": year,
        "pnlEur": pnl_eur,
        "pnlPct": pnl_pct,
        "yearStartValue": round(year_start, 2) if year_start else None,
        "daysInYear": days_count,
    }


def build_api_summary(state: dict[str, Any]) -> dict[str, Any]:
    """API/UI-yhteenveto."""
    shadow = state.get("dailyPolicyShadow") or default_shadow_state()
    summary = dict(shadow.get("summary") or {})
    today = shadow.get("today") or {}
    policy = today.get("policy") or {}
    latest_day = (shadow.get("days") or [{}])[0] if shadow.get("days") else None

    hints: list[str] = []
    net = float(summary.get("netCounterfactualEur") or 0)
    trades = int(summary.get("tradesLogged") or 0)
    pt_signals = int(summary.get("profitTakeShadowSignals") or 0)
    pt_est = float(summary.get("profitTakeShadowEurEst") or 0)
    if pt_signals >= 2 and pt_est > 0:
        hints.append(
            f"Aikaisempi voitto-otto: {pt_signals} signaalia (~{pt_est:.2f} € arvio, ei takaa voittoa)"
        )
    if trades >= 8 and net >= 2 and net != pt_est:
        hints.append(f"Estettyjen kauppojen counterfactual-yhteenveto {net:+.2f} € ({trades} kauppaa)")
    elif trades >= 8 and net >= 2 and pt_signals == 0:
        hints.append(f"Varjopolitiikan counterfactual-arvio {net:+.2f} € ({trades} kauppaa)")
    if int(summary.get("sellsWouldBlock") or 0) >= 3 and float(
        summary.get("sellBlockCounterfactualEur") or 0
    ) > 0:
        hints.append("Päivästop/profit-lock olisi välttänyt tappiollisia myyntejä")
    if int(summary.get("buysWouldBlock") or 0) >= 3 and float(
        summary.get("blockedBuyCounterfactualEur") or 0
    ) > 0:
        hints.append("Ostojen rajoitus olisi säästänyt tappioita")

    year_pnl = _compute_year_pnl(shadow)
    shadow_year_pnl = _compute_shadow_year_pnl(shadow)
    comparison = dict(shadow.get("portfolioComparison") or {})
    portfolio_metrics = shadow.get("portfolioMetrics") or {}

    if comparison.get("reliable"):
        adv = float(comparison.get("advantageEur") or 0)
        hints.insert(
            0,
            f"Varjosalkku vs. live: {adv:+.2f} € (peilattu {comparison.get('tradesMirrored', 0)} kauppaa, "
            f"ohitettu {comparison.get('tradesSkipped', 0)})",
        )

    return {
        "enabled": True,
        "dayKey": shadow.get("dayKey"),
        "dayStartValue": shadow.get("dayStartValue"),
        "liveTodayPnlEur": today.get("realPnlEur"),
        "liveTodayPnlPct": today.get("realPnlPct"),
        "todayPnlEur": today.get("realPnlEur"),
        "todayPnlPct": today.get("realPnlPct"),
        "yearPnl": year_pnl,
        "shadowYearPnl": shadow_year_pnl,
        "portfolioComparison": comparison,
        "portfolioMetrics": portfolio_metrics,
        "policy": policy,
        "summary": summary,
        "recentEvents": (shadow.get("events") or [])[:8],
        "recentDays": (shadow.get("days") or [])[:7],
        "latestClosedDay": latest_day,
        "hints": hints,
        "thresholds": {
            "dailyStopPct": DAILY_STOP_PCT,
            "profitLockSoftPct": PROFIT_LOCK_SOFT_PCT,
            "profitLockFirmPct": PROFIT_LOCK_FIRM_PCT,
        },
    }


def build_gemini_context(state: dict[str, Any]) -> dict[str, Any]:
    """Rikas konteksti Gemini-kertomusta varten."""
    api = build_api_summary(state)
    summary = api.get("summary") or {}
    events = []
    for ev in (api.get("recentEvents") or [])[:6]:
        events.append(
            {
                "type": ev.get("type"),
                "symbol": ev.get("symbol"),
                "wouldBlock": ev.get("wouldBlock"),
                "blockReason": ev.get("blockReason"),
                "counterfactualEur": ev.get("counterfactualEur"),
                "profitLoss": ev.get("profitLoss"),
                "estEur": ev.get("estEur"),
                "reason": (ev.get("reason") or "")[:80],
            }
        )
    return {
        "note": (
            "Varjopolitiikka pyörii rinnalla live-bottia — simuloi päivästopia, "
            "profit lockia ja aggressiivista tilaa. EI vaikuta oikeisiin kauppoihin."
        ),
        "todayPnlPct": api.get("todayPnlPct"),
        "todayPnlEur": api.get("todayPnlEur"),
        "policy": api.get("policy"),
        "portfolioComparison": api.get("portfolioComparison"),
        "shadowYearPnl": api.get("shadowYearPnl"),
        "summary": {
            "tradesLogged": summary.get("tradesLogged"),
            "netCounterfactualEur": summary.get("netCounterfactualEur"),
            "buysWouldBlock": summary.get("buysWouldBlock"),
            "sellsWouldBlock": summary.get("sellsWouldBlock"),
            "buyBlockEur": summary.get("buyBlockEur"),
            "sellBlockCounterfactualEur": summary.get("sellBlockCounterfactualEur"),
            "blockedBuyCounterfactualEur": summary.get("blockedBuyCounterfactualEur"),
            "profitTakeShadowSignals": summary.get("profitTakeShadowSignals"),
            "profitTakeShadowEurEst": summary.get("profitTakeShadowEurEst"),
            "daysTracked": summary.get("daysTracked"),
        },
        "thresholds": api.get("thresholds"),
        "hints": api.get("hints"),
        "recentDays": api.get("recentDays"),
        "recentEvents": events,
    }


def learning_report_lines(state: dict[str, Any]) -> list[str]:
    api = build_api_summary(state)
    summary = api.get("summary") or {}
    comparison = api.get("portfolioComparison") or {}
    lines: list[str] = []
    if comparison.get("shadowTotalValue") is not None:
        adv = float(comparison.get("advantageEur") or 0)
        lines.append(
            f"Varjosalkku {comparison.get('shadowTotalValue')} € vs. live "
            f"{comparison.get('liveTotalValue')} € (ero {adv:+.2f} €)"
        )
    if int(summary.get("tradesLogged") or 0) < 3 and not comparison.get("reliable"):
        lines.append("Varjopolitiikka kerää dataa — liian vähän kauppoja vertailuun")
        return lines
    net = float(summary.get("netCounterfactualEur") or 0)
    lines.append(
        f"Varjopolitiikka: {summary.get('tradesLogged')} kauppaa, "
        f"arvioitu ero {net:+.2f} €"
    )
    if int(summary.get("buysWouldBlock") or 0):
        lines.append(
            f"Estetyt ostot: {summary.get('buysWouldBlock')} "
            f"({summary.get('buyBlockEur')} €)"
        )
    if int(summary.get("sellsWouldBlock") or 0):
        lines.append(
            f"Estetyt myynnit: {summary.get('sellsWouldBlock')} "
            f"(counterfactual {summary.get('sellBlockCounterfactualEur'):+.2f} €)"
        )
    if int(summary.get("profitTakeShadowSignals") or 0):
        lines.append(
            f"Aikaisempi voitto-otto: {summary.get('profitTakeShadowSignals')} signaalia "
            f"(~{summary.get('profitTakeShadowEurEst'):+.2f} €)"
        )
    for hint in api.get("hints") or []:
        lines.append(hint)
    return lines
