"""Regiimin ennakoinnin hyödyntäminen ja oppiminen — raportti + Gemini-konteksti."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .ai_trader import (
    REBALANCE_MIN_PROFIT_PCT,
    REGIME_PROFIT_TAKE_SCALES,
    anticipated_regime_key,
    risk_regime_key,
)
from .learning import LEARNING_WINDOW, _category, _net_eur, _parse_time

PHASE_LOG_MAX = 400
WIN_EPS = 0.01

PHASE_LABELS = {
    "bull_entering": "Nousevaan siirtymässä",
    "bull_emerging": "Nouseva muodostumassa",
    "bear_entering": "Laskevaan siirtymässä",
    "bear_emerging": "Laskeva muodostumassa",
    "neutral_entering": "Neutraaliin siirtymässä",
    "neutral_emerging": "Neutraali muodostumassa",
}


def _is_anticipated_phase(phase: str | None, regime: str | None) -> bool:
    if not phase:
        return False
    if regime and phase != regime:
        return True
    return phase.endswith("_entering") or phase.endswith("_emerging")


def record_regime_snapshot(state: dict[str, Any], regime_info: dict[str, Any]) -> None:
    """Tallenna regiimi-/vaihehistoriaa myyntien jälkikorjausta varten."""
    if not regime_info:
        return
    phase = str(regime_info.get("phase") or regime_info.get("regime") or "neutral")
    regime = str(regime_info.get("regime") or "neutral")
    anticipated = _is_anticipated_phase(phase, regime)
    strength = regime_info.get("shift_strength")
    transition = regime_info.get("transition")

    log: list[dict[str, Any]] = list(state.get("regimePhaseLog") or [])
    now_iso = datetime.now(timezone.utc).isoformat()
    last = log[-1] if log else {}
    if (
        last.get("phase") == phase
        and last.get("regime") == regime
        and last.get("shift_to") == regime_info.get("shift_to")
        and last.get("shift_strength") == strength
    ):
        return

    log.append(
        {
            "t": now_iso,
            "regime": regime,
            "phase": phase,
            "shift_to": regime_info.get("shift_to"),
            "shift_strength": strength,
            "transition": transition,
            "anticipated": anticipated,
            "signal_margin": regime_info.get("signal_margin"),
        }
    )
    if len(log) > PHASE_LOG_MAX:
        log = log[-PHASE_LOG_MAX:]
    state["regimePhaseLog"] = log


def _phase_at_time(log: list[dict[str, Any]], ts: datetime | None) -> dict[str, Any] | None:
    if not ts or not log:
        return None
    best = None
    for entry in log:
        parsed = _parse_time(entry.get("t"))
        if parsed and parsed <= ts:
            best = entry
        elif parsed and parsed > ts:
            break
    return best


def _resolve_trade_phase(trade: dict[str, Any], log: list[dict[str, Any]]) -> dict[str, Any]:
    if trade.get("regimePhase"):
        phase = str(trade["regimePhase"])
        regime = str(trade.get("regime") or "neutral")
        return {
            "phase": phase,
            "regime": regime,
            "anticipated": bool(trade.get("anticipated") or _is_anticipated_phase(phase, regime)),
            "shift_to": trade.get("shiftTo"),
            "shift_strength": trade.get("shiftStrength"),
            "source": "trade_meta",
        }
    ts = _parse_time(trade.get("timestamp"))
    snap = _phase_at_time(log, ts)
    if snap:
        return {
            "phase": snap.get("phase"),
            "regime": snap.get("regime"),
            "anticipated": bool(snap.get("anticipated")),
            "shift_to": snap.get("shift_to"),
            "shift_strength": snap.get("shift_strength"),
            "source": "phase_log",
        }
    regime = str(trade.get("regime") or "neutral")
    return {
        "phase": regime,
        "regime": regime,
        "anticipated": False,
        "shift_to": None,
        "shift_strength": None,
        "source": "regime_only",
    }


def _usage_summary(regime_info: dict[str, Any] | None) -> dict[str, Any]:
    regime_info = regime_info or {}
    phase = str(regime_info.get("phase") or regime_info.get("regime") or "neutral")
    regime = str(regime_info.get("regime") or "neutral")
    anticipated_key = anticipated_regime_key(regime_info) if regime_info else regime
    risk_key = risk_regime_key(regime_info) if regime_info else regime
    return {
        "currentRegime": regime,
        "currentPhase": phase,
        "shiftTo": regime_info.get("shift_to"),
        "shiftStrength": regime_info.get("shift_strength"),
        "transition": regime_info.get("transition"),
        "anticipatedActive": _is_anticipated_phase(phase, regime),
        "anticipatedRegimeKey": anticipated_key,
        "riskRegimeKey": risk_key,
        "rebalanceMinProfitPct": REBALANCE_MIN_PROFIT_PCT.get(phase)
        if phase in REBALANCE_MIN_PROFIT_PCT
        else REBALANCE_MIN_PROFIT_PCT.get(regime),
        "profitTakeScales": REGIME_PROFIT_TAKE_SCALES.get(phase)
        or REGIME_PROFIT_TAKE_SCALES.get(regime),
        "rulesInCode": [
            "Ostot/oppiminen: anticipated_regime_key() — entering/emerging → shift_to-säännöt",
            "Stop-loss: risk_regime_key() — bear-ennakointi kiristää aikaisin",
            "Tasapainotus: REBALANCE_MIN_PROFIT_PCT vaihekohtainen (bear_entering 0 %)",
            "Voitto-otto: REGIME_PROFIT_TAKE_SCALES vaihekohtainen trigger/partial",
        ],
    }


def _sell_outcomes_by_anticipation(
    portfolio: dict[str, Any],
    log: list[dict[str, Any]],
) -> dict[str, Any]:
    sells = [t for t in (portfolio.get("trades") or []) if t.get("type") == "sell"][:LEARNING_WINDOW]
    buckets: dict[str, dict[str, Any]] = {
        "anticipated": {"wins": 0, "losses": 0, "net": 0.0, "by_category": {}},
        "stable": {"wins": 0, "losses": 0, "net": 0.0, "by_category": {}},
    }
    by_phase: dict[str, dict[str, Any]] = {}

    for t in sells:
        resolved = _resolve_trade_phase(t, log)
        key = "anticipated" if resolved.get("anticipated") else "stable"
        net = _net_eur(t)
        b = buckets[key]
        b["net"] += net
        if net > WIN_EPS:
            b["wins"] += 1
        elif net < -WIN_EPS:
            b["losses"] += 1
        cat = _category(t.get("reason", ""))
        cat_b = b["by_category"].setdefault(cat, {"wins": 0, "losses": 0, "net": 0.0})
        cat_b["net"] += net
        if net > WIN_EPS:
            cat_b["wins"] += 1
        elif net < -WIN_EPS:
            cat_b["losses"] += 1

        phase = str(resolved.get("phase") or "unknown")
        ph = by_phase.setdefault(phase, {"wins": 0, "losses": 0, "net": 0.0, "n": 0})
        ph["n"] += 1
        ph["net"] += net
        if net > WIN_EPS:
            ph["wins"] += 1
        elif net < -WIN_EPS:
            ph["losses"] += 1

    for key, b in buckets.items():
        total = b["wins"] + b["losses"]
        b["net"] = round(b["net"], 2)
        b["win_rate"] = round(b["wins"] / total, 2) if total else None
        b["total"] = total

    phase_stats = []
    for phase, st in sorted(by_phase.items(), key=lambda x: -x[1]["n"]):
        total = st["wins"] + st["losses"]
        phase_stats.append(
            {
                "phase": phase,
                "label": PHASE_LABELS.get(phase, phase.replace("_", " ")),
                "samples": int(st["n"]),
                "wins": st["wins"],
                "losses": st["losses"],
                "net_eur": round(st["net"], 2),
                "win_rate": round(st["wins"] / total, 2) if total else None,
            }
        )

    tagged = sum(1 for t in sells if t.get("regimePhase"))
    return {
        "anticipated": buckets["anticipated"],
        "stable": buckets["stable"],
        "byPhase": phase_stats[:8],
        "taggedSellsWithPhase": tagged,
        "totalSells": len(sells),
    }


def _recommendations(
    usage: dict[str, Any],
    outcomes: dict[str, Any],
    learning: dict[str, Any],
) -> list[str]:
    recs: list[str] = []
    ant = outcomes.get("anticipated") or {}
    stable = outcomes.get("stable") or {}
    if usage.get("anticipatedActive"):
        phase = usage.get("currentPhase", "")
        label = PHASE_LABELS.get(phase, phase.replace("_", " "))
        recs.append(
            f"Ennakointi aktiivinen ({label}) — "
            f"tasapainotus ≥{usage.get('rebalanceMinProfitPct', 0):.2f} %, "
            f"riskiregiimi {usage.get('riskRegimeKey')}"
        )

    if ant.get("total", 0) >= 3 and stable.get("total", 0) >= 3:
        ant_wr = ant.get("win_rate")
        st_wr = stable.get("win_rate")
        if ant_wr is not None and st_wr is not None and ant_wr < st_wr - 0.15:
            recs.append(
                "Ennakointivaiheessa myynnit heikompia kuin vakaassa regiimissä — "
                "pidä kiinni tiukemmista tappiorajoista (rotaatio/aikastoppi)"
            )
        elif ant_wr is not None and st_wr is not None and ant_wr > st_wr + 0.1:
            recs.append(
                "Ennakointivaiheessa voitto-myynnit onnistuvat hyvin — "
                "hyödynnä bull_entering/emerging -kynnyksiä voitto-otossa"
            )

    phase_stats = outcomes.get("byPhase") or []
    for st in phase_stats:
        if st["samples"] >= 3 and st.get("net_eur", 0) < -1.0:
            recs.append(
                f"Vaihe {st['label']}: {st['losses']} tappiota — "
                f"tarkista regiimikohtainen viritys (learning.regime_tuning)"
            )

    overrides = (learning.get("regime_tuning") or {}).get(usage.get("currentRegime", ""))
    if overrides:
        parts = []
        if overrides.get("rotation_enabled") is False:
            parts.append("rotaatio pois")
        if overrides.get("profit_take_tuning"):
            parts.append("voitto-otto säätö")
        if parts:
            recs.append(f"Regiimi-{usage.get('currentRegime')}: {', '.join(parts)} käytössä")

    if not recs:
        if outcomes.get("taggedSellsWithPhase", 0) < 3:
            recs.append(
                "Ennakointidata kerääntyy — uudet myynnit tallentavat regimePhase-metan"
            )
        else:
            recs.append("Ennakointisäännöt käytössä — seurataan myyntituloksia vaiheittain")
    return recs[:5]


def build_gemini_context(
    portfolio: dict[str, Any],
    learning: dict[str, Any] | None = None,
    bot_state: dict[str, Any] | None = None,
    regime: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Konteksti Geminin oppimiskertomukseen — regiimin ennakointi."""
    learning = learning or {}
    bot_state = bot_state or {}
    regime = regime or bot_state.get("regime") or {}
    log = list(bot_state.get("regimePhaseLog") or [])
    usage = _usage_summary(regime)
    outcomes = _sell_outcomes_by_anticipation(portfolio, log)
    regime_stats = learning.get("regime_stats") or {}

    recent_log = [
        {
            "t": e.get("t"),
            "phase": e.get("phase"),
            "regime": e.get("regime"),
            "shift_to": e.get("shift_to"),
            "anticipated": e.get("anticipated"),
        }
        for e in log[-6:]
    ]

    return {
        "enabled": True,
        "usage": usage,
        "sellOutcomes": outcomes,
        "regimeCategoryStats": regime_stats,
        "recentPhaseLog": recent_log,
        "phaseLogEntries": len(log),
        "recommendations": _recommendations(usage, outcomes, learning),
    }


def learning_report_lines(context: dict[str, Any]) -> list[str]:
    """Rule-pohjaiset rivit oppimisraportin korttiin."""
    if not context.get("enabled"):
        return []

    lines: list[str] = []
    usage = context.get("usage") or {}
    phase = usage.get("currentPhase", "neutral")
    regime = usage.get("currentRegime", "neutral")
    if usage.get("anticipatedActive"):
        label = PHASE_LABELS.get(phase, phase.replace("_", " "))
        shift = usage.get("shiftTo")
        shift_txt = f" → {shift}" if shift and shift != regime else ""
        lines.append(f"Ennakointi: {label}{shift_txt} ({usage.get('shiftStrength') or '—'})")
    else:
        lines.append(f"Regiimi vakaa: {regime}")

    rules = []
    reb = usage.get("rebalanceMinProfitPct")
    if reb is not None:
        rules.append(f"tasapainotus ≥{reb:.2f} %")
    pt = usage.get("profitTakeScales") or {}
    if pt:
        rules.append(f"voitto-otto ×{pt.get('trigger_scale', 1):.2f}")
    if rules:
        lines.append("Käytössä: " + ", ".join(rules))

    outcomes = context.get("sellOutcomes") or {}
    ant = outcomes.get("anticipated") or {}
    stable = outcomes.get("stable") or {}
    if ant.get("total") or stable.get("total"):
        parts = []
        if ant.get("total"):
            wr = ant.get("win_rate")
            wr_t = f", {wr * 100:.0f} % WR" if wr is not None else ""
            parts.append(f"ennakoinnissa {ant['wins']}V/{ant['losses']}T{wr_t}")
        if stable.get("total"):
            wr = stable.get("win_rate")
            wr_t = f", {wr * 100:.0f} % WR" if wr is not None else ""
            parts.append(f"vakaassa {stable['wins']}V/{stable['losses']}T{wr_t}")
        lines.append("Myynnit: " + " · ".join(parts))

    tagged = int(outcomes.get("taggedSellsWithPhase") or 0)
    if tagged < 3:
        lines.append(f"Phase-meta myynneissä: {tagged}/3 (kerätään)")

    recs = context.get("recommendations") or []
    if recs:
        lines.append(recs[0])
    return lines
