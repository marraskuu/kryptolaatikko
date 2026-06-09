import time
from typing import Any

PROFIT_TRIGGER_PCT = 2.0
# Odotus huipun jälkeen ennen myyntivalmiutta (tasaantuminen)
STABILIZE_WAIT_MS = 180 * 1000
# Myy vasta kun hinta laskee tämän verran huipusta (pieni lasku, ei jokainen tick)
PULLBACK_FROM_PEAK_PCT = 0.35


def default_watch_state() -> dict[str, Any]:
    return {
        "active": False,
        "peakPrice": 0.0,
        "peakTime": 0,
        "prevPrice": 0.0,
        "armed": False,
    }


def update_profit_sell(
    states: dict[str, dict[str, Any]],
    symbol: str,
    current_price: float,
    avg_price: float,
    now_ms: int | None = None,
) -> dict[str, Any]:
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    state = dict(states.get(symbol) or default_watch_state())
    profit_pct = ((current_price - avg_price) / avg_price) * 100 if avg_price else 0
    wait_sec = STABILIZE_WAIT_MS // 1000

    if profit_pct < PROFIT_TRIGGER_PCT:
        if state["active"] and not state["armed"]:
            state = default_watch_state()
        state["prevPrice"] = current_price
        states[symbol] = state
        return {
            "shouldSell": False,
            "profitPct": profit_pct,
            "status": "below_trigger",
            "statusText": f"Voitto {profit_pct:.1f} % — odotetaan +{PROFIT_TRIGGER_PCT:.0f} %",
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

    should_sell = False
    reason = ""
    if state["armed"] and peak > 0 and pullback_pct >= PULLBACK_FROM_PEAK_PCT:
        should_sell = True
        reason = (
            f"Voitto +{profit_pct:.1f} % — nousu tasaantui (huippu {peak:.2f} €), "
            f"pieni lasku -{pullback_pct:.2f} % huipusta → realisoidaan voitto"
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
    elif pullback_pct < PULLBACK_FROM_PEAK_PCT:
        status_text = (
            f"+{profit_pct:.1f} % — tasaantunut, odotetaan pientä laskua "
            f"(-{PULLBACK_FROM_PEAK_PCT:.1f} % huipusta, nyt -{pullback_pct:.2f} %)"
        )
        status = "armed"
    else:
        status_text = f"+{profit_pct:.1f} % — valmis myyntiin"

    return {
        "shouldSell": should_sell,
        "profitPct": profit_pct,
        "reason": reason,
        "status": status,
        "statusText": status_text,
        "state": state,
        "secondsLeft": 0 if state["armed"] else seconds_left,
    }
