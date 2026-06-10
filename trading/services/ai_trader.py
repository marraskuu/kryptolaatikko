import logging
from datetime import datetime, timezone
from typing import Any, Callable

from .bitfinex import is_stablecoin, normalize_symbol

logger = logging.getLogger(__name__)

STOP_LOSS_PCT = -2.0
ROTATE_LOSS_PCT = -1.0
PROFIT_TAKE_TRIGGER_PCT = 2.0
UPTREND_MIN_CHANGE_PCT = 0.3
MIN_TRADE_EUR = 10
CASH_BUFFER_EUR = 2
ROTATION_TRIM_FRACTION = 0.5
MIN_ROTATION_INTERVAL_SEC = 30 * 60
FEE_RATE = 0.001
GEMINI_DEEP_ANALYSIS_LIMIT = 25

# A: ATR-pohjainen riski (tasapainoinen taso)
ATR_STOP_MULT = 1.5          # stop = entry - 1.5 * ATR%
STOP_FLOOR_PCT = -1.5        # stop ei tiukempi kuin -1.5 %
STOP_CAP_PCT = -8.0          # stop ei löysempi kuin -8 %
DEFAULT_ATR_PCT = 1.5        # jos ATR puuttuu, oletetaan ~1.5 %
ROUND_TRIP_COST_PCT = 0.2    # 2 x 0.1 % kaupankäyntikulu

# E: rotaation pitää tuottaa vähintään tämän verran odotusarvoa kuluja vastaan
ROTATION_EDGE_PCT = 1.0


def _atr_pct(analysis: dict[str, Any]) -> float:
    val = analysis.get("atrPct")
    if val is None or val <= 0:
        return DEFAULT_ATR_PCT
    return float(val)


def dynamic_stop_pct(analysis: dict[str, Any]) -> float:
    """ATR-pohjainen stop-loss-prosentti (negatiivinen), tasapainoisin rajoin."""
    stop = -ATR_STOP_MULT * _atr_pct(analysis)
    return max(STOP_CAP_PCT, min(STOP_FLOOR_PCT, stop))


def _find_btc_symbol(tickers: dict[str, dict[str, Any]]) -> str | None:
    for sym in tickers:
        if normalize_symbol(sym).upper().startswith("TBTC"):
            return sym
    return None


def compute_market_regime(
    tickers: dict[str, dict[str, Any]],
    analyses: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """B: bull / neutral / bear BTC-trendin ja markkinaleveyden perusteella."""
    changes = [
        t.get("changePct", 0)
        for s, t in tickers.items()
        if not is_stablecoin(s) and t.get("last", 0) > 0
    ]
    breadth = (
        sum(1 for c in changes if c > 0) / len(changes) if changes else 0.5
    )

    btc_sym = _find_btc_symbol(tickers)
    btc_24h = 0.0
    btc_4h = None
    btc_ema_bull = None
    if btc_sym:
        btc_24h = tickers[btc_sym].get("changePct", 0)
        a = analyses.get(btc_sym, {})
        btc_4h = a.get("change4hPct")
        if a.get("ema9") is not None and a.get("ema21") is not None:
            btc_ema_bull = a["ema9"] > a["ema21"]

    bull_signals = 0
    bear_signals = 0
    if btc_24h > 1:
        bull_signals += 1
    elif btc_24h < -1.5:
        bear_signals += 1
    if btc_4h is not None:
        if btc_4h > 0.5:
            bull_signals += 1
        elif btc_4h < -0.5:
            bear_signals += 1
    if btc_ema_bull is True:
        bull_signals += 1
    elif btc_ema_bull is False:
        bear_signals += 1
    if breadth > 0.55:
        bull_signals += 1
    elif breadth < 0.4:
        bear_signals += 1

    if bear_signals >= 2 and bear_signals > bull_signals:
        regime = "bear"
    elif bull_signals >= 2 and bull_signals > bear_signals:
        regime = "bull"
    else:
        regime = "neutral"

    return {
        "regime": regime,
        "btc_change_24h_pct": round(btc_24h, 2),
        "btc_change_4h_pct": round(btc_4h, 2) if btc_4h is not None else None,
        "breadth_up_pct": round(breadth * 100, 1),
    }


def _entry_ok(analysis: dict[str, Any], regime: str) -> bool:
    """C + B: hyväksy tekninen sisäänosto vain kun aikajänteet linjassa ja regiimi sallii."""
    if analysis.get("action") == "sell":
        return False
    mtf = analysis.get("mtfAlign", 0)
    change_24h = analysis.get("changePct")
    if change_24h is None:
        change_24h = analysis.get("momentum") or 0
    if regime == "bear":
        # Karhumarkkinassa vain selvästi linjassa nousevat, ei putoavia veitsiä
        return mtf >= 1 and change_24h > -1
    if regime == "bull":
        return mtf >= 0
    # neutraali: ei täysin laskevaa linjausta
    return mtf >= 0 and change_24h > -3


def _in_uptrend(analysis: dict[str, Any]) -> bool:
    """Position or market still rising — hold winners, don't sell early."""
    change = analysis.get("changePct") if analysis.get("changePct") is not None else analysis.get("momentum")
    if change is None:
        return False
    return change >= UPTREND_MIN_CHANGE_PCT


def calc_rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0

    gains = 0.0
    losses = 0.0
    for i in range(len(closes) - period, len(closes)):
        diff = closes[i] - closes[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses -= diff

    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def calc_ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    k = 2 / (period + 1)
    ema = values[0]
    for i in range(1, len(values)):
        ema = values[i] * k + ema * (1 - k)
    return ema


def calc_momentum(closes: list[float]) -> float:
    if len(closes) < 10:
        return 0.0
    recent = closes[-5:]
    older = closes[-10:-5]
    recent_avg = sum(recent) / len(recent)
    older_avg = sum(older) / len(older)
    if older_avg == 0:
        return 0.0
    return ((recent_avg - older_avg) / older_avg) * 100


def analyze_ticker_quick(ticker: dict[str, Any]) -> dict[str, Any]:
    change_pct = ticker["changePct"]
    score = 0
    reasons: list[str] = []

    # Voitto-orientoitunut: momentum ja nousu > laskuun ostaminen
    if 2 <= change_pct <= 8:
        score += 3
        reasons.append(f"24h +{change_pct:.1f} % — nousumomentum, voittopotentiaali")
    elif 0 <= change_pct < 2:
        score += 1
        reasons.append(f"24h +{change_pct:.1f} % — lievä nousu")
    elif -4 <= change_pct < 0:
        score += 0
        reasons.append(f"24h {change_pct:.1f} % — pieni dip, varovainen")
    elif change_pct < -6:
        score -= 3
        reasons.append(f"24h {change_pct:.1f} % — voimakas lasku, vältä")
    elif change_pct < -4:
        score -= 1
        reasons.append(f"24h {change_pct:.1f} % — laskussa")
    elif change_pct > 12:
        score -= 2
        reasons.append(f"24h +{change_pct:.1f} % — yliextended, voitto talteen")
    else:
        score += 1
        reasons.append(f"24h +{change_pct:.1f} % — vakaa nousu")

    if ticker["volumeEur"] > 500_000:
        score += 1
        reasons.append("Hyvä likviditeetti")
    if ticker["volumeEur"] > 2_000_000 and change_pct > 0:
        score += 1
        reasons.append("Vahva volyymi nousussa")

    action = "hold"
    if score >= 3:
        action = "buy"
    elif score <= -2:
        action = "sell"

    return {
        "action": action,
        "score": score,
        "rsi": 50,
        "ema9": ticker["last"],
        "ema21": ticker["last"],
        "momentum": change_pct,
        "changePct": change_pct,
        "currentPrice": ticker["last"],
        "volumeEur": ticker["volumeEur"],
        "reasons": reasons,
        "strength": min(abs(score) / 4, 1),
        "quick": True,
    }


def calc_period_change_pct(closes: list[float], periods: int) -> float | None:
    if len(closes) < periods + 1:
        return None
    old = closes[-(periods + 1)]
    new = closes[-1]
    if old <= 0:
        return None
    return ((new - old) / old) * 100


def calc_atr_pct(candles: list[dict[str, Any]], period: int = 14) -> float | None:
    """Average True Range prosentteina nykyhinnasta — volatiliteettimitta."""
    if len(candles) < period + 1:
        return None
    trs: list[float] = []
    for i in range(1, len(candles)):
        high = candles[i]["high"]
        low = candles[i]["low"]
        prev_close = candles[i - 1]["close"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    if len(trs) < period:
        return None
    atr = sum(trs[-period:]) / period
    last_close = candles[-1]["close"]
    if last_close <= 0:
        return None
    return (atr / last_close) * 100


def _mtf_alignment(change_1h: float | None, change_4h: float | None, change_24h: float) -> int:
    """+1 nouseva linjaus, -1 laskeva linjaus, 0 ristiriita (monen aikajänteen vahvistus)."""
    signs = []
    for v in (change_1h, change_4h, change_24h):
        if v is None:
            continue
        signs.append(1 if v > 0 else -1 if v < 0 else 0)
    if not signs:
        return 0
    if all(s > 0 for s in signs):
        return 1
    if all(s < 0 for s in signs):
        return -1
    return 0


def build_deep_analysis(ticker: dict[str, Any], candles: list[dict[str, Any]]) -> dict[str, Any]:
    if len(candles) >= 20:
        analysis = analyze_market(candles)
        closes = [c["close"] for c in candles]
        analysis["changePct"] = ticker.get("changePct", 0)
        change_1h = calc_period_change_pct(closes, 1)
        change_4h = calc_period_change_pct(closes, 4)
        if change_1h is not None:
            analysis["change1hPct"] = change_1h
        if change_4h is not None:
            analysis["change4hPct"] = change_4h
        atr_pct = calc_atr_pct(candles)
        if atr_pct is not None:
            analysis["atrPct"] = atr_pct
        analysis["mtfAlign"] = _mtf_alignment(
            change_1h, change_4h, ticker.get("changePct", 0)
        )
        analysis["volumeEur"] = ticker.get("volumeEur", 0)
        analysis["currentPrice"] = ticker.get("last", analysis["currentPrice"])
        analysis["emaBullish"] = analysis.get("ema9", 0) > analysis.get("ema21", 0)
        analysis["quick"] = False
        return analysis
    quick = analyze_ticker_quick(ticker)
    quick["change1hPct"] = None
    quick["change4hPct"] = None
    quick["atrPct"] = None
    quick["mtfAlign"] = 0
    return quick


def symbols_for_deep_analysis(
    tickers: dict[str, dict[str, Any]],
    portfolio: dict[str, Any],
    limit: int = GEMINI_DEEP_ANALYSIS_LIMIT,
) -> list[str]:
    holdings = list(portfolio.get("holdings", {}).keys())
    ranked = sorted(
        [s for s in tickers if not is_stablecoin(s)],
        key=lambda s: tickers[s].get("volumeEur", 0),
        reverse=True,
    )
    result: list[str] = []
    seen: set[str] = set()
    for sym in holdings + ranked:
        if sym in seen or sym not in tickers or is_stablecoin(sym):
            continue
        seen.add(sym)
        result.append(sym)
        if len(result) >= limit:
            break
    return result


def enrich_analyses_for_gemini(
    tickers: dict[str, dict[str, Any]],
    analyses: dict[str, dict[str, Any]],
    portfolio: dict[str, Any],
    fetch_candles_fn: Callable[..., list[dict[str, Any]]],
    limit: int = GEMINI_DEEP_ANALYSIS_LIMIT,
) -> None:
    """Päivittää top-symboleille kynttiläpohjaisen RSI/EMA/momentum-analyysin."""
    for symbol in symbols_for_deep_analysis(tickers, portfolio, limit):
        ticker = tickers.get(symbol)
        if not ticker:
            continue
        try:
            candles = fetch_candles_fn(symbol, "1h", 50)
            analyses[symbol] = build_deep_analysis(ticker, candles)
        except Exception:
            logger.warning("Deep analysis failed for %s", symbol, exc_info=True)
            analyses[symbol] = analyze_ticker_quick(ticker)


def _parse_trade_time(iso: str) -> datetime:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _is_emergency_trade_reason(reason: str) -> bool:
    keywords = ("Stop-loss", "Stablecoin", "realisoidaan voitto", "voitto +", "huipusta")
    lower = reason.lower()
    return any(k.lower() in lower for k in keywords)


def seconds_since_last_discretionary_trade(portfolio_data: dict[str, Any]) -> float | None:
    """Sekunteja viimeisestä rotaatio-/osto-myyntikaupasta (ei stop-loss / voitto-myynti)."""
    for trade in portfolio_data.get("trades", []):
        if trade.get("type") not in ("buy", "sell"):
            continue
        reason = trade.get("reason") or ""
        if _is_emergency_trade_reason(reason):
            continue
        try:
            last = _parse_trade_time(trade["timestamp"])
            return (datetime.now(timezone.utc) - last).total_seconds()
        except (ValueError, TypeError, KeyError):
            continue
    return None


def in_churn_cooldown(portfolio_data: dict[str, Any]) -> bool:
    elapsed = seconds_since_last_discretionary_trade(portfolio_data)
    if elapsed is None:
        return False
    return elapsed < MIN_ROTATION_INTERVAL_SEC


def analyze_market(candles: list[dict[str, Any]]) -> dict[str, Any]:
    closes = [c["close"] for c in candles]
    rsi = calc_rsi(closes)
    ema9 = calc_ema(closes[-20:], 9)
    ema21 = calc_ema(closes[-30:], 21)
    momentum = calc_momentum(closes)
    current_price = closes[-1]

    ema_bullish = ema9 > ema21
    ema_spread = ((ema9 - ema21) / ema21) * 100 if ema21 else 0

    score = 0
    reasons: list[str] = []

    if rsi < 30:
        score += 3
        reasons.append(f"RSI {rsi:.1f} — ylimyyty (ostosignaali)")
    elif rsi < 45:
        score += 1
        reasons.append(f"RSI {rsi:.1f} — lievä ostopaine")
    elif rsi > 70:
        score -= 3
        reasons.append(f"RSI {rsi:.1f} — yliostettu (myyntisignaali)")
    elif rsi > 55:
        score -= 1
        reasons.append(f"RSI {rsi:.1f} — lievä myyntipaine")
    else:
        reasons.append(f"RSI {rsi:.1f} — neutraali")

    if ema_bullish and ema_spread > 0.5:
        score += 2
        reasons.append(f"EMA9 > EMA21 (+{ema_spread:.2f} %) — nousutrendi")
    elif not ema_bullish and ema_spread < -0.5:
        score -= 2
        reasons.append(f"EMA9 < EMA21 ({ema_spread:.2f} %) — laskutrendi")
    else:
        reasons.append(f"EMA-risteys neutraali ({ema_spread:.2f} %)")

    if momentum > 2:
        score += 2
        reasons.append(f"Momentum +{momentum:.2f} % — vahva nousu")
    elif momentum < -2:
        score -= 2
        reasons.append(f"Momentum {momentum:.2f} % — vahva lasku")
    else:
        reasons.append(f"Momentum {momentum:.2f} % — maltillinen")

    action = "hold"
    if score >= 3:
        action = "buy"
    elif score <= -3:
        action = "sell"

    return {
        "action": action,
        "score": score,
        "rsi": rsi,
        "ema9": ema9,
        "ema21": ema21,
        "momentum": momentum,
        "currentPrice": current_price,
        "reasons": reasons,
        "strength": min(abs(score) / 5, 1),
        "quick": False,
    }


def _gemini_reason(analysis: dict[str, Any]) -> str | None:
    signal = analysis.get("geminiSignal")
    if signal and signal.get("reason"):
        conf = signal.get("confidence", 0)
        return f"Gemini ({conf}/10): {signal['reason']}"
    for reason in analysis.get("reasons", []):
        if reason.startswith("Gemini"):
            return reason
    return None


def _action_reason(analysis: dict[str, Any], fallback: str) -> str:
    return _gemini_reason(analysis) or fallback


def _market_change_summary(analysis: dict[str, Any]) -> str:
    parts: list[str] = []
    for key, label in (("change1hPct", "1h"), ("change4hPct", "4h"), ("changePct", "24h")):
        val = analysis.get(key)
        if val is not None:
            parts.append(f"{label} {val:+.1f} %")
    return ", ".join(parts)


def _format_trade_reason(
    analysis: dict[str, Any],
    *,
    gemini_active: bool,
    fallback: str,
    alloc_pct: float | None = None,
    eur_amount: float | None = None,
) -> str:
    """Perustelu selkeällä erottelulla: Gemini-teksti · hintamuutokset · salkun osuus · summa."""
    main = (_gemini_reason(analysis) if gemini_active else None) or fallback
    segments = [main]
    changes = _market_change_summary(analysis)
    if changes:
        segments.append(f"Hinta {changes}")
    if alloc_pct is not None:
        segments.append(f"salkun osuus {alloc_pct:.0f} %")
    if eur_amount is not None:
        segments.append(f"{eur_amount:.0f} €")
    return " · ".join(segments)


def _gemini_signal(
    gemini_insights: dict[str, Any] | None, symbol: str
) -> dict[str, Any] | None:
    if not gemini_insights:
        return None
    signals = gemini_insights.get("signals") or {}
    sym = normalize_symbol(symbol)
    return signals.get(sym) or signals.get(symbol)


def _compute_allocation_weights(
    gemini_insights: dict[str, Any] | None,
    symbols: list[str],
    analyses: dict[str, dict[str, Any]],
    gemini_active: bool,
) -> dict[str, float]:
    """Palauttaa symbol -> osuus (0–1), summa 1 valituille symboleille."""
    if not symbols:
        return {}

    raw: dict[str, float] = {}
    if gemini_insights:
        allocs = gemini_insights.get("allocations") or {}
        for sym in symbols:
            norm = normalize_symbol(sym)
            pct = allocs.get(sym) or allocs.get(norm)
            if pct is not None and pct > 0:
                raw[norm] = float(pct)
                continue
            sig = _gemini_signal(gemini_insights, sym)
            if sig and sig.get("alloc_pct") is not None and sig.get("alloc_pct") > 0:
                raw[norm] = float(sig["alloc_pct"])

    if not raw and gemini_active and gemini_insights:
        for sym in symbols:
            norm = normalize_symbol(sym)
            sig = _gemini_signal(gemini_insights, norm)
            picks = {normalize_symbol(s) for s in (gemini_insights.get("top_picks") or [])}
            if sig and sig.get("action") == "buy":
                raw[norm] = float(sig.get("confidence", 5))
            elif norm in picks:
                raw[norm] = float(sig.get("confidence", 6) if sig else 6)

    if not raw:
        ranked_scores = []
        for sym in symbols:
            norm = normalize_symbol(sym)
            analysis = analyses.get(norm) or analyses.get(sym) or {}
            ranked_scores.append((max(analysis.get("score", 1), 1), norm))
        total_score = sum(s for s, _ in ranked_scores)
        if total_score > 0:
            return {norm: score / total_score for score, norm in ranked_scores}
        equal = 1.0 / len(symbols)
        return {normalize_symbol(s): equal for s in symbols}

    normalized: dict[str, float] = {}
    for sym in symbols:
        norm = normalize_symbol(sym)
        normalized[norm] = raw.get(norm, 0.0)

    missing = [s for s, w in normalized.items() if w <= 0]
    if missing:
        for sym in missing:
            sig = _gemini_signal(gemini_insights, sym) if gemini_insights else None
            normalized[sym] = float(sig.get("confidence", 3)) if sig else 3.0

    total = sum(normalized.values())
    if total <= 0:
        equal = 1.0 / len(symbols)
        return {normalize_symbol(s): equal for s in symbols}
    return {sym: weight / total for sym, weight in normalized.items()}


def _target_holding_value(
    symbol: str,
    total_value: float,
    weights: dict[str, float],
) -> float:
    norm = normalize_symbol(symbol)
    return total_value * weights.get(norm, 0.0)


def _effective_holding_amount(
    symbol: str,
    holdings: dict[str, Any],
    decisions: list[dict[str, Any]],
) -> float:
    holding = holdings.get(symbol)
    if not holding:
        return 0.0
    amount = holding["amount"]
    for d in decisions:
        if d.get("type") == "sell" and d.get("symbol") == symbol:
            amount -= d.get("amount", 0)
    return max(0.0, amount)


def _gemini_sell_fraction(confidence: int) -> float:
    return {5: 0.35, 6: 0.45, 7: 0.55, 8: 0.65, 9: 0.80, 10: 1.0}.get(confidence, 0.50)


def _append_sell_decision(
    decisions: list[dict[str, Any]],
    symbol: str,
    crypto_amount: float,
    price: float,
    reason: str,
    analysis: dict[str, Any],
) -> None:
    if crypto_amount <= 0 or crypto_amount * price < MIN_TRADE_EUR:
        return
    for d in decisions:
        if d.get("type") == "sell" and d.get("symbol") == symbol:
            d["amount"] += crypto_amount
            d["eurAmount"] = d["amount"] * price
            return
    decisions.append(
        {
            "type": "sell",
            "symbol": symbol,
            "amount": crypto_amount,
            "eurAmount": crypto_amount * price,
            "reason": reason,
            "analysis": analysis,
        }
    )


def _deploy_cash_to_targets(
    decisions: list[dict[str, Any]],
    holdings: dict[str, Any],
    cash: float,
    total_value: float,
    weights: dict[str, float],
    target_symbols: list[str],
    analyses: dict[str, dict[str, Any]],
    label_fn: Callable[[str], str],
    gemini_active: bool,
    skip_sell_symbols: set[str],
) -> None:
    """Osittaiset myynnit ylipainoon / pois rotaatiosta; kaikki käteinen kohteisiin."""
    normalized_targets = {normalize_symbol(s) for s in target_symbols}

    for symbol in list(holdings.keys()):
        if symbol in skip_sell_symbols or is_stablecoin(symbol):
            continue
        analysis = analyses.get(symbol)
        if not analysis:
            continue
        price = analysis["currentPrice"]
        if price <= 0:
            continue
        amount = _effective_holding_amount(symbol, holdings, decisions)
        if amount <= 0:
            continue
        current_value = amount * price
        norm = normalize_symbol(symbol)

        if norm in normalized_targets or symbol in target_symbols:
            target = _target_holding_value(symbol, total_value, weights)
            excess = current_value - target
            if excess >= MIN_TRADE_EUR:
                sell_amount = min(amount, excess / price)
                _append_sell_decision(
                    decisions,
                    symbol,
                    sell_amount,
                    price,
                    f"Tasapainotus — yli tavoitteen ({excess:.0f} €)",
                    analysis,
                )
        elif target_symbols:
            sell_amount = amount * ROTATION_TRIM_FRACTION
            _append_sell_decision(
                decisions,
                symbol,
                sell_amount,
                price,
                f"{label_fn(symbol)} ei valinnoissa — myydään osa",
                analysis,
            )

    sell_proceeds = sum(d.get("eurAmount", 0) for d in decisions if d["type"] == "sell")
    buy_spent = sum(d.get("eurAmount", 0) for d in decisions if d["type"] == "buy")
    available = cash + sell_proceeds - buy_spent - CASH_BUFFER_EUR

    if available < MIN_TRADE_EUR or not target_symbols:
        return

    deficits: list[tuple[float, str, dict[str, Any]]] = []
    for sym in target_symbols:
        analysis = analyses.get(sym)
        if not analysis or analysis["currentPrice"] <= 0:
            continue
        price = analysis["currentPrice"]
        amount = _effective_holding_amount(sym, holdings, decisions)
        current = amount * price
        target = _target_holding_value(sym, total_value, weights)
        deficit = target - current
        if deficit > 1:
            deficits.append((deficit, sym, analysis))

    if not deficits:
        best = max(
            target_symbols,
            key=lambda s: weights.get(normalize_symbol(s), 0),
        )
        analysis = analyses.get(best)
        if analysis and analysis["currentPrice"] > 0:
            deficits = [(available, best, analysis)]

    total_deficit = sum(d for d, _, _ in deficits)
    remaining = available

    for i, (deficit, sym, analysis) in enumerate(deficits):
        if remaining < MIN_TRADE_EUR:
            break
        price = analysis["currentPrice"]
        if i == len(deficits) - 1:
            buy_eur = remaining
        elif total_deficit > 0:
            buy_eur = min(remaining * (deficit / total_deficit), deficit, remaining)
        else:
            buy_eur = remaining / len(deficits)

        buy_eur = max(0.0, min(buy_eur, remaining))
        if buy_eur < MIN_TRADE_EUR:
            continue

        alloc_pct = round(weights.get(normalize_symbol(sym), 0) * 100, 1)
        existing = next(
            (d for d in decisions if d["type"] == "buy" and d["symbol"] == sym),
            None,
        )
        if existing:
            existing["eurAmount"] += buy_eur
            existing["amount"] = existing["eurAmount"] / price
            remaining -= buy_eur
            continue

        reason = _format_trade_reason(
            analysis,
            gemini_active=gemini_active,
            fallback="Käteinen sijoitettu",
            alloc_pct=alloc_pct,
            eur_amount=buy_eur,
        )
        decisions.append(
            {
                "type": "buy",
                "symbol": sym,
                "eurAmount": buy_eur,
                "amount": buy_eur / price,
                "reason": reason,
                "analysis": analysis,
            }
        )
        remaining -= buy_eur


def _plan_initial_allocation(
    picks: list[dict[str, Any]],
    cash: float,
    gemini_insights: dict[str, Any] | None,
    gemini_active: bool,
    analyses: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    symbols = [item["symbol"] for item in picks]
    weights = _compute_allocation_weights(gemini_insights, symbols, analyses, gemini_active)
    investable = cash / 1.001
    planned: list[dict[str, Any]] = []
    remaining = investable

    for i, item in enumerate(picks):
        sym = item["symbol"]
        w = weights.get(normalize_symbol(sym), 0.0)
        if i == len(picks) - 1:
            eur = round(remaining, 2)
        else:
            eur = round(investable * w, 2)
            remaining -= eur
        planned.append({**item, "eurAmount": max(eur, 0.0), "allocPct": round(w * 100, 1)})
    return planned


MAX_POSITIONS = 4


def _technical_leader_symbols(
    analyses: dict[str, dict[str, Any]],
    limit: int = MAX_POSITIONS,
) -> list[str]:
    ranked = sorted(
        [
            (sym, a)
            for sym, a in analyses.items()
            if not is_stablecoin(sym) and a.get("currentPrice", 0) > 0
        ],
        key=lambda x: (
            -x[1].get("score", 0),
            -(x[1].get("changePct") or x[1].get("momentum") or 0),
        ),
    )
    return [normalize_symbol(sym) for sym, _ in ranked[:limit]]


def _gemini_desired_symbols(gemini_insights: dict[str, Any] | None) -> list[str]:
    """Gemini valitsee 1–4 kohdetta — ei pakota neljää."""
    if not gemini_insights:
        return []
    seen: set[str] = set()
    result: list[str] = []
    for raw in gemini_insights.get("top_picks") or []:
        sym = normalize_symbol(str(raw))
        if sym and sym not in seen and not is_stablecoin(sym):
            seen.add(sym)
            result.append(sym)
    if result:
        return result[:MAX_POSITIONS]
    for raw, signal in (gemini_insights.get("signals") or {}).items():
        if signal.get("action") != "buy" or signal.get("confidence", 0) < 6:
            continue
        sym = normalize_symbol(str(raw))
        if sym and sym not in seen and not is_stablecoin(sym):
            seen.add(sym)
            result.append(sym)
    return result[:MAX_POSITIONS]


def _to_crypto_items(
    symbols: list[str],
    analyses: dict[str, dict[str, Any]],
    gemini_boost: bool = False,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for raw in symbols:
        sym = normalize_symbol(raw)
        if sym not in analyses or is_stablecoin(sym):
            continue
        analysis = analyses[sym]
        rank = analysis.get("score", 0) + (12 if gemini_boost else 0)
        items.append({"symbol": sym, "analysis": analysis, "rank": rank})
    return items


def _build_top_cryptos(
    ranked: list[dict[str, Any]],
    analyses: dict[str, dict[str, Any]],
    target_count: int,
    gemini_insights: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    desired = _gemini_desired_symbols(gemini_insights)
    if desired:
        return _to_crypto_items(desired, analyses, gemini_boost=True)

    count = max(1, min(target_count, MAX_POSITIONS, len(ranked) or 1))
    return ranked[:count]


def make_trading_decisions(
    analyses: dict[str, dict[str, Any]],
    portfolio_data: dict[str, Any],
    total_value: float,
    label_fn: Callable[[str], str],
    gemini_insights: dict[str, Any] | None = None,
    gemini_picks: list[str] | None = None,
    regime: str = "neutral",
    learning: dict[str, Any] | None = None,
) -> dict[str, Any]:
    holdings = portfolio_data["holdings"]
    cash = portfolio_data["cash"]
    learning = learning or {}
    rotation_scale = float(learning.get("rotation_scale", 1.0))
    rotation_enabled = bool(learning.get("rotation_enabled", True))
    rotation_trim = max(0.25, min(1.0, ROTATION_TRIM_FRACTION * rotation_scale))

    ranked = [
        {"symbol": symbol, "analysis": analysis, "rank": analysis["score"]}
        for symbol, analysis in analyses.items()
        if analysis.get("currentPrice", 0) > 0 and not is_stablecoin(symbol)
    ]
    ranked.sort(
        key=lambda x: (
            -x["rank"],
            -(x["analysis"].get("changePct") or x["analysis"].get("momentum") or 0),
            -(x["analysis"].get("volumeEur") or 0),
        )
    )
    # B + C: tekniseen sisäänostoon vain regiimin/aikajänteiden sallimat — varalla koko lista
    ranked_buyable = [r for r in ranked if _entry_ok(r["analysis"], regime)] or ranked

    target_count = MAX_POSITIONS
    gemini_active = bool(gemini_insights and gemini_insights.get("signals"))
    desired = _gemini_desired_symbols(gemini_insights) if gemini_active else []

    if gemini_active and desired:
        top_cryptos = _to_crypto_items(desired, analyses, gemini_boost=True)
    elif gemini_active:
        leaders = _technical_leader_symbols(analyses, MAX_POSITIONS)
        symbols = list(dict.fromkeys(leaders + list(holdings.keys())))
        top_cryptos = _to_crypto_items(symbols, analyses)
    else:
        fallback_n = max(1, min(MAX_POSITIONS, len(holdings) or 2))
        top_cryptos = _build_top_cryptos(ranked_buyable, analyses, fallback_n, gemini_insights)

    if not top_cryptos and gemini_picks:
        gemini_top = _to_crypto_items(gemini_picks, analyses, gemini_boost=True)
        if gemini_top:
            top_cryptos = gemini_top

    target_count = max(1, len(top_cryptos)) if top_cryptos else 1

    top_symbols = {c["symbol"] for c in top_cryptos}

    decisions: list[dict[str, Any]] = []
    churn_cooldown = in_churn_cooldown(portfolio_data)

    if len(holdings) == 0 and cash > 100:
        picks: list[dict[str, Any]] = []
        if desired:
            picks = _to_crypto_items(desired, analyses, gemini_boost=True)
        elif not gemini_active and ranked_buyable:
            picks = ranked_buyable[: min(2, len(ranked_buyable))]
        elif ranked_buyable:
            picks = ranked_buyable[:1]
        if picks:
            return {
                "decisions": [],
                "targetCount": len(picks),
                "topSymbols": [c["symbol"] for c in picks],
                "initialAllocation": _plan_initial_allocation(
                    picks, cash, gemini_insights, gemini_active, analyses
                ),
                "geminiActive": gemini_active,
            }

    for symbol, holding in holdings.items():
        analysis = analyses.get(symbol)
        if not analysis:
            continue

        if is_stablecoin(symbol):
            decisions.append(
                {
                    "type": "sell",
                    "symbol": symbol,
                    "amount": holding["amount"],
                    "eurAmount": holding["amount"] * analysis["currentPrice"],
                    "reason": "Stablecoin — myydään, ei sijoituskohte",
                    "analysis": analysis,
                }
            )
            continue

        holding_value = holding["amount"] * analysis["currentPrice"]
        profit_pct = (
            ((analysis["currentPrice"] - holding["avgPrice"]) / holding["avgPrice"]) * 100
            if holding["avgPrice"]
            else 0
        )

        if profit_pct >= PROFIT_TAKE_TRIGGER_PCT:
            decisions.append(
                {
                    "type": "hold",
                    "symbol": symbol,
                    "reason": (
                        f"Voitto +{profit_pct:.1f} % — pidetään nousussa, "
                        f"myydään vasta tasaantumisen tai pienen laskun jälkeen"
                    ),
                    "analysis": analysis,
                }
            )
            continue

        stop_pct = dynamic_stop_pct(analysis)
        if profit_pct <= stop_pct:
            decisions.append(
                {
                    "type": "sell",
                    "symbol": symbol,
                    "amount": holding["amount"],
                    "eurAmount": holding_value,
                    "reason": (
                        f"Stop-loss {profit_pct:.1f} % (ATR-raja {stop_pct:.1f} %) — "
                        f"rajataan tappio, pääoma parempaan"
                    ),
                    "analysis": analysis,
                }
            )
            continue

        if profit_pct > 0 and _in_uptrend(analysis):
            decisions.append(
                {
                    "type": "hold",
                    "symbol": symbol,
                    "reason": (
                        f"Nousuputki jatkuu (+{profit_pct:.1f} % voitolla) — "
                        f"pidetään kunnes tasaantuu tai tulee pieni lasku"
                    ),
                    "analysis": analysis,
                }
            )
            continue

        gemini_sig = _gemini_signal(gemini_insights, symbol) or analysis.get("geminiSignal")
        change_24h = analysis.get("changePct") or analysis.get("momentum") or 0

        sell_conf = 5 if profit_pct < 0 else 6
        if churn_cooldown:
            decisions.append(
                {
                    "type": "hold",
                    "symbol": symbol,
                    "reason": "Churn-tauko (30 min) — ei rotaatiota vielä",
                    "analysis": analysis,
                }
            )
        elif gemini_sig and gemini_sig.get("action") == "sell" and gemini_sig.get("confidence", 0) >= sell_conf:
            sell_amount = holding["amount"] * _gemini_sell_fraction(
                gemini_sig.get("confidence", 5)
            )
            _append_sell_decision(
                decisions,
                symbol,
                sell_amount,
                analysis["currentPrice"],
                _action_reason(
                    analysis,
                    f"Gemini suosittelee osittaista myyntiä — {gemini_sig.get('reason', '')}",
                ),
                analysis,
            )
        elif (
            gemini_active
            and gemini_sig
            and gemini_sig.get("action") == "hold"
            and gemini_sig.get("confidence", 0) >= 7
            and profit_pct >= ROTATE_LOSS_PCT
        ):
            decisions.append(
                {
                    "type": "hold",
                    "symbol": symbol,
                    "reason": _action_reason(analysis, "Gemini: pidä positio"),
                    "analysis": analysis,
                }
            )
        elif (
            rotation_enabled
            and profit_pct < ROTATE_LOSS_PCT
            and (symbol not in top_symbols or change_24h < -2)
        ):
            sell_amount = holding["amount"] * rotation_trim
            _append_sell_decision(
                decisions,
                symbol,
                sell_amount,
                analysis["currentPrice"],
                f"Tappiolla {profit_pct:.1f} % — myydään osa ja siirretään vahvempaan",
                analysis,
            )
        elif rotation_enabled and analysis["action"] == "sell":
            sell_amount = holding["amount"] * rotation_trim
            _append_sell_decision(
                decisions,
                symbol,
                sell_amount,
                analysis["currentPrice"],
                "; ".join(analysis["reasons"]),
                analysis,
            )
        elif not rotation_enabled and profit_pct < ROTATE_LOSS_PCT:
            decisions.append(
                {
                    "type": "hold",
                    "symbol": symbol,
                    "reason": (
                        "Oppiminen: rotaatio tuottanut tappiota — pidetään ja "
                        "annetaan teknisen stopin hoitaa"
                    ),
                    "analysis": analysis,
                }
            )
        elif analysis["action"] == "hold":
            decisions.append(
                {
                    "type": "hold",
                    "symbol": symbol,
                    "reason": "Pidetään — odotetaan parempaa signaalia",
                    "analysis": analysis,
                }
            )

    alloc_symbols = list(
        dict.fromkeys([c["symbol"] for c in top_cryptos] + desired)
    )[:MAX_POSITIONS]
    weights = _compute_allocation_weights(
        gemini_insights, alloc_symbols, analyses, gemini_active
    )

    skip_sell_symbols = {d["symbol"] for d in decisions if d.get("type") == "hold"}
    if not churn_cooldown:
        _deploy_cash_to_targets(
            decisions,
            holdings,
            cash,
            total_value,
            weights,
            alloc_symbols,
            analyses,
            label_fn,
            gemini_active,
            skip_sell_symbols,
        )

    for d in decisions:
        if d["type"] == "buy" and is_stablecoin(d["symbol"]):
            d["type"] = "hold"
            d["reason"] = "Stablecoin — ei osteta"

    return {
        "decisions": decisions,
        "targetCount": len(top_cryptos) or target_count,
        "topSymbols": list(top_symbols),
        "geminiActive": gemini_active,
    }


def format_initial_buy_reason(
    analysis: dict[str, Any],
    label: str,
    index: int,
    total: int,
    gemini_active: bool,
    alloc_pct: float | None = None,
    eur_amount: float | None = None,
) -> str:
    if gemini_active and _gemini_reason(analysis):
        return _format_trade_reason(
            analysis,
            gemini_active=True,
            fallback=f"Gemini: avaa salkku — {label}",
            alloc_pct=alloc_pct,
            eur_amount=eur_amount,
        )
    fallback = (
        f"Gemini: avaa salkku — {label} ({index}/{total})"
        if gemini_active
        else f"Alkuallokaatio — {label} ({index}/{total})"
    )
    return _format_trade_reason(
        analysis,
        gemini_active=gemini_active,
        fallback=fallback,
        alloc_pct=alloc_pct,
        eur_amount=eur_amount,
    )


def apply_gemini_insights(
    analyses: dict[str, dict[str, Any]],
    insights: dict[str, Any] | None,
) -> None:
    if not insights:
        return

    for symbol in insights.get("top_picks") or []:
        symbol = normalize_symbol(symbol)
        if symbol in analyses and not is_stablecoin(symbol):
            analyses[symbol]["score"] = analyses[symbol].get("score", 0) + 4
            analyses[symbol]["reasons"] = ["Gemini: top-valinta"] + analyses[symbol].get(
                "reasons", []
            )
            analyses[symbol]["geminiPick"] = True

    for symbol, signal in (insights.get("signals") or {}).items():
        symbol = normalize_symbol(symbol)
        if symbol not in analyses:
            continue
        analysis = analyses[symbol]
        action = signal.get("action", "hold")
        confidence = int(signal.get("confidence", 5))
        reason = signal.get("reason", "")

        analysis["score"] = analysis.get("score", 0) + (confidence - 5) + (2 if action == "buy" else 0)

        if confidence >= 6:
            if action == "buy":
                analysis["action"] = "buy"
            elif action == "sell":
                analysis["action"] = "sell"
        elif confidence >= 5 and action == "sell":
            analysis["action"] = "sell"

        if reason:
            analysis["reasons"] = [f"Gemini ({confidence}/10): {reason}"] + analysis.get(
                "reasons", []
            )
        analysis["gemini"] = True
        analysis["geminiSignal"] = {
            "action": action,
            "confidence": confidence,
            "reason": reason,
        }
        if signal.get("alloc_pct") is not None:
            analysis["geminiSignal"]["alloc_pct"] = signal["alloc_pct"]
            analysis["geminiAllocPct"] = signal["alloc_pct"]

    for symbol, pct in (insights.get("allocations") or {}).items():
        symbol = normalize_symbol(symbol)
        if symbol in analyses:
            analyses[symbol]["geminiAllocPct"] = pct


def build_decision_report(
    decisions: list[dict[str, Any]],
    label_fn: Callable[[str], str],
    gemini_active: bool = False,
) -> dict[str, Any]:
    buys = [d for d in decisions if d["type"] == "buy"]
    sells = [d for d in decisions if d["type"] == "sell"]
    holds = [d for d in decisions if d["type"] == "hold"]

    title = "Gemini-analyysi valmis" if gemini_active else "AI-analyysi valmis"
    subtitle = f"{len(buys)} ostoa · {len(sells)} myyntiä · {len(holds)} pidossa"

    if buys and sells:
        title = "Gemini: ostoja ja myyntejä" if gemini_active else "Ostoja ja myyntejä"
    elif buys:
        title = f"Gemini: {len(buys)} ostoa" if gemini_active else f"Ostetaan {len(buys)} kryptoa"
    elif sells:
        title = f"Gemini: {len(sells)} myyntiä" if gemini_active else f"Myydään {len(sells)} kryptoa"
    elif holds:
        title = "Gemini: pidetään positioita" if gemini_active else "Pidetään positioita"
        subtitle = "Ei uusia kauppoja tällä kierroksella"
    else:
        title = "Ei toimenpiteitä"
        subtitle = "Odotetaan parempaa signaalia"

    if buys and sells:
        action = "mixed"
    elif buys:
        action = "buy"
    elif sells:
        action = "sell"
    else:
        action = "hold"

    return {
        "action": action,
        "title": title,
        "subtitle": subtitle,
        "buys": [
            {
                "symbol": label_fn(b["symbol"]),
                "amount": b.get("eurAmount"),
                "reason": b["reason"],
                "analysis": b.get("analysis"),
            }
            for b in buys
        ],
        "sells": [
            {
                "symbol": label_fn(s["symbol"]),
                "amount": s.get("eurAmount"),
                "reason": s["reason"],
                "analysis": s.get("analysis"),
            }
            for s in sells
        ],
        "holds": [
            {
                "symbol": label_fn(h["symbol"]),
                "reason": h["reason"],
                "analysis": h.get("analysis"),
            }
            for h in holds
        ],
    }
