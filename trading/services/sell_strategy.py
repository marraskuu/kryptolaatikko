import time
from typing import Any

PROFIT_TRIGGER_PCT = 2.0
# Odotus huipun jälkeen ennen myyntivalmiutta (tasaantuminen)
STABILIZE_WAIT_MS = 180 * 1000
# Myy vasta kun hinta laskee tämän verran huipusta (pieni lasku, ei jokainen tick)
PULLBACK_FROM_PEAK_PCT = 0.35

# A + E: ATR-pohjainen trailing take-profit
PROFIT_TRIGGER_ATR_MULT = 1.2   # arming-kynnys = 1.2 x ATR%
PROFIT_TRIGGER_FLOOR_PCT = 1.5  # mutta vähintään tämä (kattaa kulut + veron)
PROFIT_TRIGGER_CAP_PCT = 5.0    # ja enintään tämä (lukitaan voitot aiemmin)
PULLBACK_ATR_MULT = 0.6         # trailing-stop = 0.6 x ATR% huipusta
PULLBACK_FLOOR_PCT = 0.35
PULLBACK_CAP_PCT = 3.0          # enimmäisanto huipusta ennen myyntiä
ROUND_TRIP_COST_PCT = 0.0       # Bitfinex: ei kaupankäyntikuluja (vain 30 % voittovero)

# 2: Porrastettu voiton kotiutus — lukitse osa voitosta ensimmäisessä portaassa,
# anna lopun ratsastaa trailing-stopilla (paras molemmista: turvattu voitto + nousuvara).
PARTIAL_TAKE_TRIGGER_PCT = 3.0  # ensimmäinen porras kun voitto ylittää tämän
PARTIAL_TAKE_FRACTION = 0.30    # kotiuta 30 % positiosta portaassa 1


def default_watch_state() -> dict[str, Any]:
    return {
        "active": False,
        "peakPrice": 0.0,
        "peakTime": 0,
        "prevPrice": 0.0,
        "armed": False,
        "tier1Taken": False,
    }


def _trigger_pct(atr_pct: float | None) -> float:
    if atr_pct and atr_pct > 0:
        return min(
            PROFIT_TRIGGER_CAP_PCT,
            max(PROFIT_TRIGGER_FLOOR_PCT, PROFIT_TRIGGER_ATR_MULT * atr_pct),
        )
    return PROFIT_TRIGGER_PCT


def _pullback_threshold_pct(atr_pct: float | None) -> float:
    if atr_pct and atr_pct > 0:
        return min(
            PULLBACK_CAP_PCT,
            max(PULLBACK_FLOOR_PCT, PULLBACK_ATR_MULT * atr_pct),
        )
    return PULLBACK_FROM_PEAK_PCT


def update_profit_sell(
    states: dict[str, dict[str, Any]],
    symbol: str,
    current_price: float,
    avg_price: float,
    now_ms: int | None = None,
    atr_pct: float | None = None,
) -> dict[str, Any]:
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    state = dict(states.get(symbol) or default_watch_state())
    profit_pct = ((current_price - avg_price) / avg_price) * 100 if avg_price else 0
    wait_sec = STABILIZE_WAIT_MS // 1000
    trigger_pct = _trigger_pct(atr_pct)
    pullback_threshold = _pullback_threshold_pct(atr_pct)
    covers_cost = profit_pct > ROUND_TRIP_COST_PCT

    # 2: Porras 1 — kotiuta osa heti kun voitto ylittää portaan ensimmäisen kerran,
    # ja jätä loppu trailing-stopille. Ei toistu samalla positiolla (tier1Taken).
    if not state.get("tier1Taken") and profit_pct >= PARTIAL_TAKE_TRIGGER_PCT and covers_cost:
        state["tier1Taken"] = True
        if not state["active"]:
            state["active"] = True
            state["peakPrice"] = current_price
            state["peakTime"] = now
            state["armed"] = False
        state["prevPrice"] = current_price
        states[symbol] = state
        return {
            "shouldSell": True,
            "sellFraction": PARTIAL_TAKE_FRACTION,
            "profitPct": profit_pct,
            "reason": (
                f"Voitto +{profit_pct:.1f} % — kotiutetaan {PARTIAL_TAKE_FRACTION * 100:.0f} % "
                f"(porras 1), loppu jää trailing-stopille nousua varten"
            ),
            "status": "tier1",
            "statusText": (
                f"+{profit_pct:.1f} % — kotiutettu {PARTIAL_TAKE_FRACTION * 100:.0f} %, "
                f"loppu trailaten"
            ),
            "state": state,
            "secondsLeft": 0,
        }

    if profit_pct < trigger_pct:
        if state["active"] and not state["armed"]:
            tier1_taken = state.get("tier1Taken", False)
            state = default_watch_state()
            state["tier1Taken"] = tier1_taken
        state["prevPrice"] = current_price
        states[symbol] = state
        return {
            "shouldSell": False,
            "sellFraction": 1.0,
            "profitPct": profit_pct,
            "status": "below_trigger",
            "statusText": f"Voitto {profit_pct:.1f} % — odotetaan +{trigger_pct:.1f} %",
            "state": state,
            "secondsLeft": 0,
        }

    if not state["active"]:
        state["active"] = True
        state["peakPrice"] = current_price
        state["peakTime"] = now
        state["armed"] = False
    elif current_price > state["peakPrice"]:
        # Uusi huippu — nousuputki jatkuu, ei myydä
        state["peakPrice"] = current_price
        state["peakTime"] = now
        state["armed"] = False

    elapsed = now - state["peakTime"]
    seconds_left = max(0, int((STABILIZE_WAIT_MS - elapsed + 999) / 1000))
    peak = state["peakPrice"]
    pullback_pct = ((peak - current_price) / peak) * 100 if peak else 0

    if elapsed >= STABILIZE_WAIT_MS:
        state["armed"] = True

    # E: realisoidaan vain jos voitto kattaa edestakaiset kulut
    should_sell = False
    reason = ""
    if state["armed"] and peak > 0 and pullback_pct >= pullback_threshold and covers_cost:
        should_sell = True
        reason = (
            f"Voitto +{profit_pct:.1f} % — nousu tasaantui (huippu {peak:.2f} €), "
            f"trailing-stop -{pullback_pct:.2f} % huipusta (raja {pullback_threshold:.2f} %) "
            f"→ realisoidaan voitto"
        )

    state["prevPrice"] = current_price
    states[symbol] = state

    if current_price >= peak and not state["armed"]:
        status_text = (
            f"+{profit_pct:.1f} % — nousuputki jatkuu (huippu {peak:.2f} €), pidetään"
        )
        status = "uptrend"
    elif not state["armed"]:
        status_text = (
            f"+{profit_pct:.1f} % — odotetaan tasaantumista {seconds_left}s "
            f"(huippu {peak:.2f} €)"
        )
        status = "waiting"
    elif pullback_pct < pullback_threshold:
        status_text = (
            f"+{profit_pct:.1f} % — tasaantunut, trailing-stop "
            f"-{pullback_threshold:.2f} % huipusta (nyt -{pullback_pct:.2f} %)"
        )
        status = "armed"
    else:
        status_text = f"+{profit_pct:.1f} % — valmis myyntiin"
        status = "ready"

    return {
        "shouldSell": should_sell,
        "sellFraction": 1.0,
        "profitPct": profit_pct,
        "reason": reason,
        "status": status,
        "statusText": status_text,
        "state": state,
        "secondsLeft": 0 if state["armed"] else seconds_left,
    }
