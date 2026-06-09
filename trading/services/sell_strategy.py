import time
from typing import Any

PROFIT_TRIGGER_PCT = 3
WAIT_MS = 180 * 1000


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

    if profit_pct < PROFIT_TRIGGER_PCT:
        if state["active"] and not state["armed"]:
            state = default_watch_state()
        state["prevPrice"] = current_price
        states[symbol] = state
        return {
            "shouldSell": False,
            "profitPct": profit_pct,
            "status": "alle_3",
            "statusText": f"Voitto {profit_pct:.1f} % — odotetaan +3 %",
            "state": state,
            "secondsLeft": 0,
        }

    if not state["active"]:
        state["active"] = True
        state["peakPrice"] = current_price
        state["peakTime"] = now
        state["armed"] = False
    elif current_price >= state["peakPrice"]:
        if current_price > state["peakPrice"]:
            state["peakPrice"] = current_price
            state["peakTime"] = now
            state["armed"] = False

    elapsed = now - state["peakTime"]
    seconds_left = max(0, int((WAIT_MS - elapsed + 999) / 1000))

    if elapsed >= WAIT_MS:
        state["armed"] = True

    should_sell = False
    reason = ""
    if state["armed"] and state["prevPrice"] > 0 and current_price < state["prevPrice"]:
        should_sell = True
        reason = (
            f"Voitto +{profit_pct:.1f} % — 180 s huipun ({state['peakPrice']:.2f} €) jälkeen, "
            f"kurssi kääntyi laskuun ({state['prevPrice']:.2f} → {current_price:.2f} €)"
        )

    state["prevPrice"] = current_price
    states[symbol] = state

    if not state["armed"]:
        status_text = (
            f"+{profit_pct:.1f} % — odotetaan {seconds_left}s huipun jälkeen "
            f"(huippu {state['peakPrice']:.2f} €)"
        )
    else:
        status_text = f"+{profit_pct:.1f} % — valmis myyntiin, odotetaan kurssin kääntymistä"

    return {
        "shouldSell": should_sell,
        "profitPct": profit_pct,
        "reason": reason,
        "status": "armed" if state["armed"] else "waiting",
        "statusText": status_text,
        "state": state,
        "secondsLeft": 0 if state["armed"] else seconds_left,
    }
