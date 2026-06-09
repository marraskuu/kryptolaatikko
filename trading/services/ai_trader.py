from typing import Any, Callable


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

    if change_pct < -10:
        score += 3
        reasons.append(f"24h {change_pct:.1f} % — voimakas lasku, ostomahdollisuus")
    elif change_pct < -4:
        score += 2
        reasons.append(f"24h {change_pct:.1f} % — lasku, mahdollinen osto")
    elif change_pct > 10:
        score -= 2
        reasons.append(f"24h +{change_pct:.1f} % — voitto otettu")
    elif change_pct > 4:
        score += 1
        reasons.append(f"24h +{change_pct:.1f} % — nousussa")
    else:
        score += 1
        reasons.append(f"24h {change_pct:.1f} % — vakaa")

    if ticker["volumeEur"] > 500_000:
        score += 1
        reasons.append("Hyvä likviditeetti")

    action = "hold"
    if score >= 2:
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


def make_trading_decisions(
    analyses: dict[str, dict[str, Any]],
    portfolio_data: dict[str, Any],
    total_value: float,
    label_fn: Callable[[str], str],
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
        key=lambda x: (-x["rank"], -(x["analysis"].get("volumeEur") or 0))
    )

    target_count = (
        4
        if len(holdings) == 0
        else min(4, max(3, len(holdings)))
    )

    if gemini_picks:
        gemini_top = [
            {"symbol": s, "analysis": analyses[s], "rank": analyses[s].get("score", 0) + 10}
            for s in gemini_picks
            if s in analyses
        ]
        if len(gemini_top) >= 3:
            top_cryptos = gemini_top[:target_count]
        else:
            top_cryptos = ranked[:target_count]
    else:
        top_cryptos = ranked[:target_count]
    top_symbols = {c["symbol"] for c in top_cryptos}

    decisions: list[dict[str, Any]] = []

    if len(holdings) == 0 and cash > 100 and top_cryptos:
        return {
            "decisions": [],
            "targetCount": target_count,
            "topSymbols": list(top_symbols),
            "initialAllocation": top_cryptos[: min(target_count, len(top_cryptos))],
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

        if profit_pct >= 3:
            decisions.append(
                {
                    "type": "hold",
                    "symbol": symbol,
                    "reason": "Voitto-Myyntistrategia: +3 % saavutettu — odotetaan 180 s ja kurssin kääntymistä",
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
    available_cash = cash + sell_proceeds
    target_per_crypto = available_cash / target_count if target_count else 0

    for item in top_cryptos:
        symbol = item["symbol"]
        analysis = item["analysis"]
        holding = holdings.get(symbol)
        holding_value = holding["amount"] * analysis["currentPrice"] if holding else 0
        deficit = target_per_crypto - holding_value

        if not holding and available_cash > 30:
            buy_amount = min(target_per_crypto, available_cash * 0.95)
            if buy_amount >= 15:
                decisions.append(
                    {
                        "type": "buy",
                        "symbol": symbol,
                        "eurAmount": buy_amount,
                        "amount": buy_amount / analysis["currentPrice"],
                        "reason": f"Uusi positio top {target_count}:een — {analysis['reasons'][0]}",
                        "analysis": analysis,
                    }
                )
                available_cash -= buy_amount
        elif holding and deficit > 15 and available_cash > 30:
            buy_amount = min(deficit, available_cash * 0.4)
            if buy_amount >= 15:
                decisions.append(
                    {
                        "type": "buy",
                        "symbol": symbol,
                        "eurAmount": buy_amount,
                        "amount": buy_amount / analysis["currentPrice"],
                        "reason": f"Tasapainotus — lisätään {label_fn(symbol)}",
                        "analysis": analysis,
                    }
                )
                available_cash -= buy_amount
        elif holding and analysis["action"] == "buy" and deficit > 10 and available_cash > 20:
            buy_amount = min(deficit, available_cash * 0.3)
            if buy_amount >= 10:
                decisions.append(
                    {
                        "type": "buy",
                        "symbol": symbol,
                        "eurAmount": buy_amount,
                        "amount": buy_amount / analysis["currentPrice"],
                        "reason": f"Ostosignaali — {analysis['reasons'][0]}",
                        "analysis": analysis,
                    }
                )
                available_cash -= buy_amount

    if cash > 150 and not any(d["type"] == "buy" for d in decisions):
        best_not_held = next((r for r in ranked if r["symbol"] not in holdings), None)
        if best_not_held and best_not_held["symbol"] in top_symbols:
            buy_amount = min(cash * 0.25, target_per_crypto)
            if buy_amount >= 15:
                analysis = best_not_held["analysis"]
                decisions.append(
                    {
                        "type": "buy",
                        "symbol": best_not_held["symbol"],
                        "eurAmount": buy_amount,
                        "amount": buy_amount / analysis["currentPrice"],
                        "reason": f"Käteinen käytössä — {analysis['reasons'][0]}",
                        "analysis": analysis,
                    }
                )

    return {
        "decisions": decisions,
        "targetCount": target_count,
        "topSymbols": list(top_symbols),
    }


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

        analysis["score"] = analysis.get("score", 0) + (confidence - 5)

        if confidence >= 7:
            if action == "buy":
                analysis["action"] = "buy"
            elif action == "sell":
                analysis["action"] = "sell"

        if reason:
            analysis["reasons"] = [f"Gemini ({confidence}/10): {reason}"] + analysis.get(
                "reasons", []
            )
        analysis["gemini"] = True


def build_decision_report(
    decisions: list[dict[str, Any]],
    label_fn: Callable[[str], str],
) -> dict[str, Any]:
    buys = [d for d in decisions if d["type"] == "buy"]
    sells = [d for d in decisions if d["type"] == "sell"]
    holds = [d for d in decisions if d["type"] == "hold"]

    title = "AI-analyysi valmis"
    subtitle = f"{len(buys)} ostoa · {len(sells)} myyntiä · {len(holds)} pidossa"

    if buys and sells:
        title = "Ostoja ja myyntejä"
    elif buys:
        title = f"Ostetaan {len(buys)} kryptoa"
    elif sells:
        title = f"Myydään {len(sells)} kryptoa"
    elif holds:
        title = "Pidetään positioita"
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
