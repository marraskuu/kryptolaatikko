from typing import Any, Callable

from .bitfinex import normalize_symbol

STOP_LOSS_PCT = -2.0
ROTATE_LOSS_PCT = -1.0
PROFIT_TAKE_TRIGGER_PCT = 2.0


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


def _gemini_signal(
    gemini_insights: dict[str, Any] | None, symbol: str
) -> dict[str, Any] | None:
    if not gemini_insights:
        return None
    signals = gemini_insights.get("signals") or {}
    sym = normalize_symbol(symbol)
    return signals.get(sym) or signals.get(symbol)


def _build_top_cryptos(
    ranked: list[dict[str, Any]],
    analyses: dict[str, dict[str, Any]],
    target_count: int,
    gemini_insights: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    gemini_picks = (gemini_insights or {}).get("top_picks") or []
    if gemini_picks:
        top: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw_sym in gemini_picks:
            sym = normalize_symbol(raw_sym)
            if sym in analyses and sym not in seen:
                analysis = analyses[sym]
                top.append(
                    {
                        "symbol": sym,
                        "analysis": analysis,
                        "rank": analysis.get("score", 0) + 12,
                    }
                )
                seen.add(sym)
        for item in ranked:
            if len(top) >= target_count:
                break
            if item["symbol"] not in seen:
                top.append(item)
                seen.add(item["symbol"])
        if top:
            return top[:target_count]

    return ranked[:target_count]


def make_trading_decisions(
    analyses: dict[str, dict[str, Any]],
    portfolio_data: dict[str, Any],
    total_value: float,
    label_fn: Callable[[str], str],
    gemini_insights: dict[str, Any] | None = None,
    gemini_picks: list[str] | None = None,
) -> dict[str, Any]:
    holdings = portfolio_data["holdings"]
    cash = portfolio_data["cash"]

    ranked = [
        {"symbol": symbol, "analysis": analysis, "rank": analysis["score"]}
        for symbol, analysis in analyses.items()
        if analysis.get("currentPrice", 0) > 0
    ]
    ranked.sort(
        key=lambda x: (
            -x["rank"],
            -(x["analysis"].get("changePct") or x["analysis"].get("momentum") or 0),
            -(x["analysis"].get("volumeEur") or 0),
        )
    )

    target_count = 4 if len(holdings) < 4 else min(4, len(holdings))
    gemini_active = bool(gemini_insights and gemini_insights.get("signals"))

    top_cryptos = _build_top_cryptos(ranked, analyses, target_count, gemini_insights)
    if not top_cryptos and gemini_picks:
        gemini_top = [
            {"symbol": normalize_symbol(s), "analysis": analyses[normalize_symbol(s)], "rank": 10}
            for s in gemini_picks
            if normalize_symbol(s) in analyses
        ]
        if gemini_top:
            top_cryptos = gemini_top[:target_count]

    top_symbols = {c["symbol"] for c in top_cryptos}

    decisions: list[dict[str, Any]] = []

    if len(holdings) == 0 and cash > 100 and top_cryptos:
        return {
            "decisions": [],
            "targetCount": target_count,
            "topSymbols": list(top_symbols),
            "initialAllocation": top_cryptos[: min(target_count, len(top_cryptos))],
            "geminiActive": gemini_active,
        }

    for symbol, holding in holdings.items():
        analysis = analyses.get(symbol)
        if not analysis:
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
                        f"Voitto-Myyntistrategia: +{PROFIT_TAKE_TRIGGER_PCT:.0f} % saavutettu "
                        f"— odotetaan huippua ja myydään nousussa"
                    ),
                    "analysis": analysis,
                }
            )
            continue

        if profit_pct <= STOP_LOSS_PCT:
            decisions.append(
                {
                    "type": "sell",
                    "symbol": symbol,
                    "amount": holding["amount"],
                    "eurAmount": holding_value,
                    "reason": f"Stop-loss {profit_pct:.1f} % — rajataan tappio, pääoma parempaan",
                    "analysis": analysis,
                }
            )
            continue

        gemini_sig = _gemini_signal(gemini_insights, symbol) or analysis.get("geminiSignal")
        change_24h = analysis.get("changePct") or analysis.get("momentum") or 0

        sell_conf = 5 if profit_pct < 0 else 6
        if gemini_sig and gemini_sig.get("action") == "sell" and gemini_sig.get("confidence", 0) >= sell_conf:
            decisions.append(
                {
                    "type": "sell",
                    "symbol": symbol,
                    "amount": holding["amount"],
                    "eurAmount": holding_value,
                    "reason": _action_reason(
                        analysis,
                        f"Gemini suosittelee myyntiä — {gemini_sig.get('reason', '')}",
                    ),
                    "analysis": analysis,
                }
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
            profit_pct < ROTATE_LOSS_PCT
            and (symbol not in top_symbols or change_24h < -2)
        ):
            decisions.append(
                {
                    "type": "sell",
                    "symbol": symbol,
                    "amount": holding["amount"],
                    "eurAmount": holding_value,
                    "reason": (
                        f"Tappiolla {profit_pct:.1f} % — myydään ja siirretään vahvempaan kohteeseen"
                    ),
                    "analysis": analysis,
                }
            )
        elif symbol not in top_symbols or analysis["action"] == "sell":
            decisions.append(
                {
                    "type": "sell",
                    "symbol": symbol,
                    "amount": holding["amount"],
                    "eurAmount": holding_value,
                    "reason": (
                        f"{label_fn(symbol)} putosi top {target_count}:sta — myydään ja siirretään parempiin"
                        if symbol not in top_symbols
                        else "; ".join(analysis["reasons"])
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

    sell_proceeds = sum(d.get("eurAmount", 0) for d in decisions if d["type"] == "sell")
    symbols_to_sell = {d["symbol"] for d in decisions if d["type"] == "sell"}
    available_cash = cash + sell_proceeds
    # Kohde-omistus perustuu koko salkun arvoon — ei pelkkään käteiseen (muuten käteinen jää roikkumaan).
    target_per_crypto = total_value / target_count if target_count else 0

    for item in top_cryptos:
        symbol = item["symbol"]
        analysis = item["analysis"]
        holding = holdings.get(symbol)
        if symbol in symbols_to_sell:
            holding_value = 0.0
        else:
            holding_value = holding["amount"] * analysis["currentPrice"] if holding else 0.0
        deficit = target_per_crypto - holding_value

        if not holding or symbol in symbols_to_sell:
            if available_cash > 15 and deficit > 10:
                buy_amount = min(deficit, available_cash - 2)
                if buy_amount >= 10:
                    gemini_sig = _gemini_signal(gemini_insights, symbol)
                    default = (
                        f"Gemini valitsee salkkuun — {analysis['reasons'][0]}"
                        if gemini_active
                        else f"Uusi positio top {target_count}:een — {analysis['reasons'][0]}"
                    )
                    if gemini_sig and gemini_sig.get("action") == "buy":
                        default = f"Gemini ({gemini_sig.get('confidence', 0)}/10): {gemini_sig.get('reason', default)}"
                    decisions.append(
                        {
                            "type": "buy",
                            "symbol": symbol,
                            "eurAmount": buy_amount,
                            "amount": buy_amount / analysis["currentPrice"],
                            "reason": _action_reason(analysis, default),
                            "analysis": analysis,
                        }
                    )
                    available_cash -= buy_amount
        elif deficit > 10 and available_cash > 15:
            buy_amount = min(deficit, available_cash - 2)
            if buy_amount >= 10:
                decisions.append(
                    {
                        "type": "buy",
                        "symbol": symbol,
                        "eurAmount": buy_amount,
                        "amount": buy_amount / analysis["currentPrice"],
                        "reason": _action_reason(
                            analysis,
                            f"Tasapainotus — lisätään {label_fn(symbol)}",
                        ),
                        "analysis": analysis,
                    }
                )
                available_cash -= buy_amount
        elif holding and analysis["action"] == "buy" and deficit > 5 and available_cash > 10:
            buy_amount = min(deficit, available_cash - 2)
            if buy_amount >= 5:
                decisions.append(
                    {
                        "type": "buy",
                        "symbol": symbol,
                        "eurAmount": buy_amount,
                        "amount": buy_amount / analysis["currentPrice"],
                        "reason": _action_reason(
                            analysis,
                            f"Ostosignaali — {analysis['reasons'][0]}",
                        ),
                        "analysis": analysis,
                    }
                )
                available_cash -= buy_amount

    if gemini_insights and available_cash > 20 and len(holdings) < target_count:
        for sym, signal in (gemini_insights.get("signals") or {}).items():
            sym = normalize_symbol(sym)
            if sym in holdings or sym in symbols_to_sell or sym not in analyses:
                continue
            if signal.get("action") != "buy" or signal.get("confidence", 0) < 7:
                continue
            analysis = analyses[sym]
            buy_amount = min(target_per_crypto, available_cash - 2)
            if buy_amount < 10:
                continue
            if any(d["type"] == "buy" and d["symbol"] == sym for d in decisions):
                continue
            decisions.append(
                {
                    "type": "buy",
                    "symbol": sym,
                    "eurAmount": buy_amount,
                    "amount": buy_amount / analysis["currentPrice"],
                    "reason": f"Gemini ({signal.get('confidence', 0)}/10): {signal.get('reason', 'Ostosuositus')}",
                    "analysis": analysis,
                }
            )
            available_cash -= buy_amount
            top_symbols.add(sym)
            if len(holdings) + len([d for d in decisions if d["type"] == "buy"]) >= target_count:
                break

    if available_cash > 15:
        underweight = []
        for item in top_cryptos:
            symbol = item["symbol"]
            analysis = item["analysis"]
            holding = holdings.get(symbol)
            if symbol in symbols_to_sell:
                hv = 0.0
            else:
                hv = holding["amount"] * analysis["currentPrice"] if holding else 0.0
            gap = target_per_crypto - hv
            if gap > 5:
                underweight.append((gap, symbol, analysis))
        underweight.sort(reverse=True)
        for gap, symbol, analysis in underweight:
            if available_cash <= 15:
                break
            if any(d["type"] == "buy" and d["symbol"] == symbol for d in decisions):
                continue
            buy_amount = min(gap, available_cash - 2)
            if buy_amount >= 10:
                decisions.append(
                    {
                        "type": "buy",
                        "symbol": symbol,
                        "eurAmount": buy_amount,
                        "reason": _action_reason(
                            analysis,
                            f"Käteinen sijoitetaan — {analysis['reasons'][0]}",
                        ),
                        "analysis": analysis,
                        "amount": buy_amount / analysis["currentPrice"],
                    }
                )
                available_cash -= buy_amount

    return {
        "decisions": decisions,
        "targetCount": target_count,
        "topSymbols": list(top_symbols),
        "geminiActive": gemini_active,
    }


def format_initial_buy_reason(
    analysis: dict[str, Any],
    label: str,
    index: int,
    total: int,
    gemini_active: bool,
) -> str:
    if gemini_active:
        gemini = _gemini_reason(analysis)
        if gemini:
            return gemini
        return f"Gemini: avaa salkku — {label} ({index}/{total})"
    return f"Alkuallokaatio — {label} ({index}/{total})"


def apply_gemini_insights(
    analyses: dict[str, dict[str, Any]],
    insights: dict[str, Any] | None,
) -> None:
    if not insights:
        return

    for symbol in insights.get("top_picks") or []:
        symbol = normalize_symbol(symbol)
        if symbol in analyses:
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
