import time
from typing import Any

from .market_microstructure import SPREAD_BLOCK_PCT

PROFIT_TRIGGER_PCT = 2.0
# Odotus huipun jälkeen ennen myyntivalmiutta (tasaantuminen)
STABILIZE_WAIT_MS = 180 * 1000
STABILIZE_WAIT_MIN_MS = 45 * 1000
FAST_DUMP_PULLBACK_PCT = 0.5
FAST_DUMP_WINDOW_MS = 15 * 60 * 1000
RSI_OVERBOUGHT = 72.0
RSI_ELEVATED = 65.0
# Myy vasta kun hinta laskee tämän verran huipusta (pieni lasku, ei jokainen tick)
PULLBACK_FROM_PEAK_PCT = 0.35

# A + E: ATR-pohjainen trailing take-profit
PROFIT_TRIGGER_ATR_MULT = 1.2   # arming-kynnys = 1.2 x ATR%
PROFIT_TRIGGER_FLOOR_PCT = 1.2  # mutta vähintään tämä (kattaa kulut + veron)
PROFIT_TRIGGER_CAP_PCT = 5.0    # ja enintään tämä (lukitaan voitot aiemmin)
PULLBACK_ATR_MULT = 0.6         # trailing-stop = 0.6 x ATR% huipusta
PULLBACK_FLOOR_PCT = 0.35
PULLBACK_CAP_PCT = 3.0          # enimmäisanto huipusta ennen myyntiä
ROUND_TRIP_COST_PCT = 0.0       # Bitfinex: ei kaupankäyntikuluja (vain 30 % voittovero)

# 2: Porrastettu voiton kotiutus — lukitse osa voitosta ensimmäisessä portaassa,
# anna lopun ratsastaa trailing-stopilla (paras molemmista: turvattu voitto + nousuvara).
PARTIAL_TAKE_TRIGGER_PCT = 2.5  # ensimmäinen porras kun voitto ylittää tämän
PARTIAL_TAKE_FRACTION = 0.30    # kotiuta 30 % positiosta portaassa 1

# Pitkä pito + hiipuva 1h/flow → aiempi arm + tiukempi trailing
LONG_HOLD_EARLY_HOURS = 2.0
LONG_HOLD_STRICT_HOURS = 4.0
LONG_HOLD_EARLY_TRIGGER_MULT = 0.75   # ≥2 h + fade → arm 75 % normaalikynnyksestä
LONG_HOLD_STRICT_TRIGGER_MULT = 0.65  # ≥4 h + fade → arm 65 %
LONG_HOLD_1H_FADE_PCT = 0.0           # 1h-muutos ≤ tämä = hiipuminen
LONG_HOLD_MIN_PROFIT_PCT = 0.8        # alle tämän ei pakoteta aikaisempaa armia


def default_profit_take_config() -> dict[str, Any]:
    return {
        "trigger_scale": 1.0,
        "pullback_scale": 1.0,
        "partial_trigger_scale": 1.0,
        "partial_fraction_scale": 1.0,
        "partial_enabled": True,
    }


def _scaled_config(config: dict[str, Any] | None) -> dict[str, Any]:
    merged = default_profit_take_config()
    if config:
        merged.update(config)
    return merged


def default_watch_state() -> dict[str, Any]:
    return {
        "active": False,
        "peakPrice": 0.0,
        "peakTime": 0,
        "prevPrice": 0.0,
        "armed": False,
        "tier1Taken": False,
    }


def _trigger_pct(atr_pct: float | None, config: dict[str, Any]) -> float:
    scale = float(config.get("trigger_scale", 1.0))
    if atr_pct and atr_pct > 0:
        return min(
            PROFIT_TRIGGER_CAP_PCT * scale,
            max(
                PROFIT_TRIGGER_FLOOR_PCT * scale,
                PROFIT_TRIGGER_ATR_MULT * scale * atr_pct,
            ),
        )
    return PROFIT_TRIGGER_PCT * scale


def _pullback_threshold_pct(atr_pct: float | None, config: dict[str, Any]) -> float:
    scale = float(config.get("pullback_scale", 1.0))
    if atr_pct and atr_pct > 0:
        return min(
            PULLBACK_CAP_PCT * scale,
            max(PULLBACK_FLOOR_PCT * scale, PULLBACK_ATR_MULT * scale * atr_pct),
        )
    return PULLBACK_FROM_PEAK_PCT * scale


def _momentum_fading(analysis: dict[str, Any] | None) -> bool:
    """1h heikko tai myyntialotteinen flow → momentum hiipuu."""
    if not analysis:
        return False
    ch1 = analysis.get("change1hPct")
    if ch1 is not None and float(ch1) <= LONG_HOLD_1H_FADE_PCT:
        return True
    flow = str(analysis.get("flowBucket") or "")
    return flow == "fl-"


def long_hold_trigger_mult(
    analysis: dict[str, Any] | None,
    hold_age_hours: float | None,
    profit_pct: float,
) -> tuple[float, list[str]]:
    """Aiempi voitto-oton arm pitkälle pidolle kun 1h/flow heikkenee."""
    if hold_age_hours is None or hold_age_hours < LONG_HOLD_EARLY_HOURS:
        return 1.0, []
    if profit_pct < LONG_HOLD_MIN_PROFIT_PCT:
        return 1.0, []
    if not _momentum_fading(analysis):
        return 1.0, []
    signals: list[str] = []
    ch1 = analysis.get("change1hPct") if analysis else None
    flow = (analysis or {}).get("flowBucket") or ""
    if ch1 is not None and float(ch1) <= LONG_HOLD_1H_FADE_PCT:
        signals.append(f"pito {hold_age_hours:.1f} h + 1h {float(ch1):+.1f} %")
    if flow == "fl-":
        signals.append(f"pito {hold_age_hours:.1f} h + myyntiflow")
    if hold_age_hours >= LONG_HOLD_STRICT_HOURS:
        return LONG_HOLD_STRICT_TRIGGER_MULT, signals or [
            f"pito {hold_age_hours:.1f} h + hiipuva momentum"
        ]
    return LONG_HOLD_EARLY_TRIGGER_MULT, signals or [
        f"pito {hold_age_hours:.1f} h + hiipuva momentum"
    ]


def compute_peak_exit_adjustments(
    analysis: dict[str, Any] | None,
    *,
    profit_pct: float,
    elapsed_ms: int,
    pullback_pct: float,
    learned: dict[str, Any] | None = None,
    hold_age_hours: float | None = None,
) -> dict[str, Any]:
    """
    Dynaaminen huippumyynti: RSI/MTF/book + opittu exit-setup + pitkä pito/1h/flow
    säätää tasaantumisodotusta ja trailing-rajaa.
    """
    signals: list[str] = []
    stabilize_mult = 1.0
    pullback_mult = 1.0
    force_arm = False
    force_sell = False

    if learned:
        stabilize_mult *= float(learned.get("stabilize_mult") or 1.0)
        pullback_mult *= float(learned.get("pullback_mult") or 1.0)
        if learned.get("learned"):
            signals.append("exit-oppiminen")

    rsi = float(analysis.get("rsi")) if analysis and analysis.get("rsi") is not None else None
    mtf = int(analysis.get("mtfAlign")) if analysis and analysis.get("mtfAlign") is not None else None
    book = (analysis or {}).get("bookBucket") or ""
    crowd = (analysis or {}).get("crowdBucket") or ""
    flow = (analysis or {}).get("flowBucket") or ""
    spread = float(analysis["bookSpreadPct"]) if analysis and analysis.get("bookSpreadPct") is not None else None

    if analysis and analysis.get("condBlocked") and profit_pct > 0:
        force_arm = True
        stabilize_mult *= 0.5
        pullback_mult *= 0.85
        signals.append("shadow-oppiminen: huono setup voitolla")

    if rsi is not None and rsi >= RSI_OVERBOUGHT and profit_pct >= PROFIT_TRIGGER_PCT:
        stabilize_mult *= 0.33
        pullback_mult *= 0.75
        signals.append(f"RSI {rsi:.0f} yliostettu")
    elif rsi is not None and rsi >= RSI_ELEVATED and profit_pct >= PROFIT_TRIGGER_PCT:
        stabilize_mult = min(stabilize_mult, stabilize_mult * 0.5)
        pullback_mult *= 0.85
        signals.append(f"RSI {rsi:.0f} korkea")

    if mtf is not None and mtf < 0:
        force_arm = True
        pullback_mult *= 0.88
        signals.append("MTF kääntynyt alas")

    if book == "bk-":
        pullback_mult *= 0.8
        signals.append("myyntipaine order bookissa")
    elif book == "bk+":
        pullback_mult = min(pullback_mult * 1.08, 1.25)

    if flow == "fl-":
        pullback_mult *= 0.88
        stabilize_mult *= 0.85
        signals.append("myyntialotteinen flow")
    elif flow == "fl+":
        pullback_mult = min(pullback_mult * 1.05, 1.25)

    if crowd == "crL" and profit_pct >= PARTIAL_TAKE_TRIGGER_PCT:
        stabilize_mult *= 0.7
        pullback_mult *= 0.85
        signals.append("crowd extreme long")

    if (
        spread is not None
        and spread >= SPREAD_BLOCK_PCT
        and profit_pct >= PROFIT_TRIGGER_PCT
    ):
        force_arm = True
        pullback_mult *= 0.75
        if pullback_pct > 0:
            force_sell = True
            signals.append(f"leveä spread {spread:.2f} % + lasku huipusta")

    if (
        elapsed_ms <= FAST_DUMP_WINDOW_MS
        and pullback_pct >= FAST_DUMP_PULLBACK_PCT
        and profit_pct >= PROFIT_TRIGGER_PCT
    ):
        force_arm = True
        force_sell = True
        signals.append(f"nopea lasku -{pullback_pct:.2f} % huipusta")

    # Pitkä pito + hiipuva momentum → tiukempi trailing ja nopeampi arm
    if (
        hold_age_hours is not None
        and hold_age_hours >= LONG_HOLD_EARLY_HOURS
        and profit_pct >= LONG_HOLD_MIN_PROFIT_PCT
        and _momentum_fading(analysis)
    ):
        if hold_age_hours >= LONG_HOLD_STRICT_HOURS:
            stabilize_mult *= 0.45
            pullback_mult *= 0.72
            force_arm = True
            signals.append(f"pitkä pito {hold_age_hours:.1f} h + hiipuva 1h/flow")
        else:
            stabilize_mult *= 0.65
            pullback_mult *= 0.82
            force_arm = True
            signals.append(f"pito {hold_age_hours:.1f} h + hiipuva 1h/flow")

    stabilize_ms = max(
        STABILIZE_WAIT_MIN_MS,
        int(STABILIZE_WAIT_MS * max(0.25, min(1.5, stabilize_mult))),
    )
    pullback_mult = max(0.55, min(1.35, pullback_mult))

    return {
        "stabilize_ms": stabilize_ms,
        "pullback_mult": pullback_mult,
        "force_arm": force_arm,
        "force_sell": force_sell,
        "signals": signals,
    }


def update_profit_sell(
    states: dict[str, dict[str, Any]],
    symbol: str,
    current_price: float,
    avg_price: float,
    now_ms: int | None = None,
    atr_pct: float | None = None,
    profit_take_config: dict[str, Any] | None = None,
    analysis: dict[str, Any] | None = None,
    exit_learned: dict[str, Any] | None = None,
    hold_age_hours: float | None = None,
) -> dict[str, Any]:
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    cfg = _scaled_config(profit_take_config)
    state = dict(states.get(symbol) or default_watch_state())
    profit_pct = ((current_price - avg_price) / avg_price) * 100 if avg_price else 0
    trigger_pct = _trigger_pct(atr_pct, cfg)
    early_mult, early_signals = long_hold_trigger_mult(
        analysis, hold_age_hours, profit_pct
    )
    if early_mult < 1.0:
        trigger_pct *= early_mult
    pullback_threshold = _pullback_threshold_pct(atr_pct, cfg)
    covers_cost = profit_pct > ROUND_TRIP_COST_PCT
    partial_trigger = PARTIAL_TAKE_TRIGGER_PCT * float(cfg.get("partial_trigger_scale", 1.0))
    partial_fraction = min(
        0.9,
        max(0.1, PARTIAL_TAKE_FRACTION * float(cfg.get("partial_fraction_scale", 1.0))),
    )

    if (
        cfg.get("partial_enabled", True)
        and not state.get("tier1Taken")
        and profit_pct >= partial_trigger
        and covers_cost
    ):
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
            "sellFraction": partial_fraction,
            "profitPct": profit_pct,
            "reason": (
                f"Voitto +{profit_pct:.1f} % — kotiutetaan {partial_fraction * 100:.0f} % "
                f"(porras 1), loppu jää trailing-stopille nousua varten"
            ),
            "status": "tier1",
            "statusText": (
                f"+{profit_pct:.1f} % — kotiutettu {partial_fraction * 100:.0f} %, "
                f"loppu trailaten"
            ),
            "state": state,
            "secondsLeft": 0,
            "peakPrice": state["peakPrice"],
            "pullbackPct": 0.0,
            "exitSetup": (exit_learned or {}).get("exit_setup"),
            "exitSignals": [],
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
        state["peakPrice"] = current_price
        state["peakTime"] = now
        state["armed"] = False

    elapsed = now - state["peakTime"]
    peak = state["peakPrice"]
    pullback_pct = ((peak - current_price) / peak) * 100 if peak else 0

    peak_adj = compute_peak_exit_adjustments(
        analysis,
        profit_pct=profit_pct,
        elapsed_ms=elapsed,
        pullback_pct=pullback_pct,
        learned=exit_learned,
        hold_age_hours=hold_age_hours,
    )
    stabilize_ms = int(peak_adj["stabilize_ms"])
    pullback_threshold *= float(peak_adj["pullback_mult"])
    seconds_left = max(0, int((stabilize_ms - elapsed + 999) / 1000))

    if peak_adj.get("force_arm") or elapsed >= stabilize_ms:
        state["armed"] = True

    should_sell = False
    reason = ""
    exit_signals = list(peak_adj.get("signals") or [])
    for sig in early_signals:
        if sig not in exit_signals:
            exit_signals.append(sig)
    if (
        peak_adj.get("force_sell")
        and peak > 0
        and covers_cost
        and (
            pullback_pct >= FAST_DUMP_PULLBACK_PCT
            or any("leveä spread" in s for s in exit_signals)
        )
    ):
        should_sell = True
        reason = (
            f"Voitto +{profit_pct:.1f} % — nopea lasku huipusta {peak:.2f} € "
            f"(-{pullback_pct:.2f} %) → realisoidaan voitto"
        )
    elif state["armed"] and peak > 0 and pullback_pct >= pullback_threshold and covers_cost:
        should_sell = True
        signal_note = f" ({', '.join(exit_signals)})" if exit_signals else ""
        reason = (
            f"Voitto +{profit_pct:.1f} % — nousu tasaantui (huippu {peak:.2f} €), "
            f"trailing-stop -{pullback_pct:.2f} % huipusta (raja {pullback_threshold:.2f} %) "
            f"→ realisoidaan voitto{signal_note}"
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
        "peakPrice": peak,
        "pullbackPct": round(pullback_pct, 3),
        "exitSetup": (exit_learned or {}).get("exit_setup"),
        "exitSignals": exit_signals,
    }
