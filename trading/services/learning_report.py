"""Oppimisraportti — rule-pohjainen yhteenveto + valinnainen Gemini-narratiivi (6 h)."""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from .bitfinex import get_crypto_label, normalize_symbol

LEARNING_REPORT_INTERVAL_SEC = int(os.environ.get("LEARNING_REPORT_INTERVAL_SEC", "21600"))
LEARNING_REPORT_HISTORY = 8
NARRATIVE_STALE_SEC = int(os.environ.get("NARRATIVE_STALE_SEC", "300"))

logger = logging.getLogger(__name__)
_narrative_refresh_lock = threading.Lock()
_narrative_refresh_running = False

ROADMAP_ITEMS = (
    {
        "key": "profit_take_light",
        "label": "Voitto-otto (kevyt viritys)",
        "metric": "profit_take_trades",
        "target": 6,
        "action": "Kevyt profit-take -viritys learning.py:ssä",
    },
    {
        "key": "profit_take_full",
        "label": "Voitto-otto (täysi optimointi)",
        "metric": "profit_take_trades",
        "target": 15,
        "action": "ATR/regiimi-pohjainen trailing sell_strategy.py",
    },
    {
        "key": "setup_learning",
        "label": "Setup-oppiminen (omat sisäänostot)",
        "metric": "regime_tagged_sells",
        "target": 4,
        "action": "Setup-muisti aktivoituu chipillä 📐",
    },
    {
        "key": "richer_buckets",
        "label": "Richer markkina-ämpärit",
        "metric": "buckets_learned",
        "target": 18,
        "action": "Laajempi setup-avain market_learning.py",
    },
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_time(iso: Any) -> datetime | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _fmt_exp(val: float | None) -> str:
    if val is None:
        return "—"
    return f"{val:+.2f} €/kauppa"


def _roadmap_metrics(learning: dict[str, Any], ml: dict[str, Any]) -> dict[str, float]:
    stats = learning.get("stats") or {}
    return {
        "profit_take_trades": float((stats.get("profit_take") or {}).get("trades") or 0),
        "regime_tagged_sells": float(learning.get("regime_tagged_sells") or 0),
        "setup_memory_keys": float(len(learning.get("setup_memory") or {})),
        "buckets_learned": float(ml.get("bucketsLearned") or 0),
        "buckets_tracked": float(ml.get("bucketsTracked") or 0),
        "gemini_confidence_tagged": float(learning.get("gemini_confidence_tagged") or 0),
    }


def _roadmap_progress(learning: dict[str, Any], ml: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = _roadmap_metrics(learning, ml)
    items: list[dict[str, Any]] = []
    for cfg in ROADMAP_ITEMS:
        current = int(metrics.get(cfg["metric"], 0))
        target = int(cfg["target"])
        if current >= target:
            status = "valmis"
        elif current >= target * 0.5:
            status = "tulossa"
        else:
            status = "kerätään"
        items.append(
            {
                "key": cfg["key"],
                "label": cfg["label"],
                "progress": f"{current}/{target}",
                "status": status,
                "action": cfg["action"],
            }
        )
    return items


def _sell_summary(portfolio: dict[str, Any], hours: int) -> dict[str, Any]:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    wins = losses = 0
    net = 0.0
    for t in portfolio.get("trades") or []:
        if t.get("type") != "sell":
            continue
        ts = _parse_time(t.get("timestamp"))
        if not ts or ts < since:
            continue
        pl = float(t.get("profitLoss") or t.get("profit") or 0)
        net += pl
        if pl > 0.01:
            wins += 1
        elif pl < -0.01:
            losses += 1
    total = wins + losses
    return {
        "wins": wins,
        "losses": losses,
        "net_eur": round(net, 2),
        "win_rate_pct": round(wins / total * 100, 1) if total else None,
    }


def _learning_snapshot(
    learning: dict[str, Any],
    ml: dict[str, Any],
    regime: dict[str, Any] | None,
) -> dict[str, Any]:
    scales = learning.get("gemini_confidence_scales") or {}
    blocked_conf = sorted(
        int(k) for k, v in scales.items() if float(v) <= 0
    )
    mem = learning.get("symbol_memory") or {}
    return {
        "bucketsLearned": ml.get("bucketsLearned"),
        "bucketsTracked": ml.get("bucketsTracked"),
        "blocked_buys": sorted(normalize_symbol(s) for s in (learning.get("blocked_buys") or [])),
        "rotation_enabled": learning.get("rotation_enabled"),
        "gemini_sell_min_confidence": learning.get("gemini_sell_min_confidence"),
        "entry_score_min": learning.get("entry_score_min"),
        "max_new_positions": learning.get("max_new_positions"),
        "blocked_conf": blocked_conf,
        "regime": (regime or {}).get("regime"),
        "note": learning.get("note"),
        "overall_expectancy_eur": learning.get("overall_expectancy_eur"),
    }


def _compute_changes(prev: dict[str, Any] | None, curr: dict[str, Any]) -> list[str]:
    if not prev:
        return ["Ensimmäinen raportti — vertailukohtaa ei vielä ole."]
    changes: list[str] = []

    prev_blocked = set(prev.get("blocked_buys") or [])
    curr_blocked = set(curr.get("blocked_buys") or [])
    new_blocked = curr_blocked - prev_blocked
    freed = prev_blocked - curr_blocked
    if new_blocked:
        labels = ", ".join(get_crypto_label(s) for s in sorted(new_blocked)[:5])
        changes.append(f"Estetty {len(new_blocked)} uutta ostokohdetta: {labels}")
    if freed:
        labels = ", ".join(get_crypto_label(s) for s in sorted(freed)[:5])
        changes.append(f"Ostokielto poistui: {labels}")

    prev_conf = set(prev.get("blocked_conf") or [])
    curr_conf = set(curr.get("blocked_conf") or [])
    if curr_conf - prev_conf:
        changes.append(f"Gemini estää nyt conf {','.join(str(c) for c in sorted(curr_conf))}")
    if prev_conf - curr_conf:
        changes.append("Gemini-confidence-esto keveni")

    if prev.get("rotation_enabled") and not curr.get("rotation_enabled"):
        changes.append("Rotaatio kytketty pois oppimisen perusteella")
    elif not prev.get("rotation_enabled") and curr.get("rotation_enabled"):
        changes.append("Rotaatio palautettu päälle")

    bl = int(prev.get("bucketsLearned") or 0)
    bc = int(curr.get("bucketsLearned") or 0)
    if bc > bl:
        changes.append(f"+{bc - bl} uutta markkina-asetelmaa opittu ({bc} yhteensä)")

    if prev.get("entry_score_min") != curr.get("entry_score_min"):
        changes.append(
            f"Sisäänostokynnys score {prev.get('entry_score_min')} → {curr.get('entry_score_min')}"
        )
    if prev.get("max_new_positions") != curr.get("max_new_positions"):
        changes.append(
            f"Max uudet positiot {prev.get('max_new_positions')} → {curr.get('max_new_positions')}"
        )
    if prev.get("gemini_sell_min_confidence") != curr.get("gemini_sell_min_confidence"):
        changes.append(
            f"Gemini min conf {prev.get('gemini_sell_min_confidence')} → "
            f"{curr.get('gemini_sell_min_confidence')}"
        )
    if prev.get("regime") != curr.get("regime"):
        changes.append(f"Markkinaregiimi: {prev.get('regime')} → {curr.get('regime')}")

    exp_p = prev.get("overall_expectancy_eur")
    exp_c = curr.get("overall_expectancy_eur")
    if exp_p is not None and exp_c is not None and abs(exp_c - exp_p) >= 0.05:
        changes.append(f"Kokonaisexpectancy {_fmt_exp(exp_p)} → {_fmt_exp(exp_c)}")

    if len(changes) == 0:
        changes.append("Ei merkittäviä säätömuutoksia edelliseen raporttiin.")
    return changes


def _gemini_conf_lines(learning: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    tagged = int(learning.get("gemini_confidence_tagged") or 0)
    stats = learning.get("gemini_confidence_stats") or {}
    scales = learning.get("gemini_confidence_scales") or {}
    min_conf = int(learning.get("gemini_sell_min_confidence") or 0)

    if tagged < 6:
        lines.append(f"Kerätään dataa ({tagged}/6 tagattua myyntiä)")
        return lines

    blocked = sorted(int(k) for k, v in scales.items() if float(v) <= 0)
    if blocked:
        lines.append(f"Estetyt confidence-tasot: {', '.join(str(c) for c in blocked)}")
    if min_conf:
        lines.append(f"Minimi confidence myynneille: {min_conf}/10")

    for conf in sorted(stats.keys(), key=lambda x: int(x)):
        s = stats[conf]
        exp = float(s.get("expectancy_eur") or 0)
        n = int(s.get("trades") or 0)
        scale = scales.get(conf, scales.get(str(conf), 1.0))
        suffix = ""
        if float(scale) <= 0:
            suffix = " · estetty"
        elif float(scale) < 1:
            suffix = f" · skaalattu ×{scale}"
        lines.append(f"{conf}/10: {n} kpl, {_fmt_exp(exp)}{suffix}")
    return lines


def build_learning_report(
    *,
    learning: dict[str, Any],
    market_learning: dict[str, Any] | None,
    regime: dict[str, Any] | None,
    portfolio: dict[str, Any],
    previous_snapshot: dict[str, Any] | None = None,
    narrative: dict[str, Any] | None = None,
    last_narrative_at: str | None = None,
) -> dict[str, Any]:
    """Rule-pohjainen oppimisraportti — rakennetaan 6 h välein."""
    ml = market_learning or {}
    stats = learning.get("stats") or {}
    mem = learning.get("symbol_memory") or {}
    regime_name = (regime or {}).get("regime", "neutral")

    chronic = [s for s, m in mem.items() if m.get("chronic")]
    cooldown = [s for s, m in mem.items() if m.get("blocked") and not m.get("chronic")]
    losers = sorted(
        (s for s, m in mem.items() if (m.get("score_adjust") or 0) < 0),
        key=lambda s: mem[s].get("net_eur", 0),
    )
    winners = sorted(
        (s for s, m in mem.items() if (m.get("score_adjust") or 0) > 0),
        key=lambda s: mem[s].get("net_eur", 0),
        reverse=True,
    )

    sections: list[dict[str, Any]] = []

    ml_lines = [
        f"{ml.get('bucketsLearned', 0)}/{ml.get('bucketsTracked', 0)} asetelmaa opittu",
    ]
    if ml.get("best"):
        b = ml["best"]
        ml_lines.append(f"Paras: {b.get('setup')} ({b.get('exp1h', 0):+.2f} % / 1h, n={b.get('n')})")
    if ml.get("worst"):
        w = ml["worst"]
        ml_lines.append(f"Huonoin: {w.get('setup')} ({w.get('exp1h', 0):+.2f} % / 1h, n={w.get('n')})")
    sections.append({"id": "market", "icon": "📊", "title": "Markkina-asetelmat", "lines": ml_lines})

    trade_lines: list[str] = []
    if learning.get("note"):
        trade_lines.append(str(learning["note"]))
    for key, label in (
        ("rotation", "Rotaatio"),
        ("gemini_sell", "Gemini-myynnit"),
        ("profit_take", "Voitto-otto"),
    ):
        cat = stats.get(key) or {}
        n = int(cat.get("trades") or 0)
        if n:
            trade_lines.append(f"{label}: {_fmt_exp(cat.get('expectancy_eur'))} ({n} kpl)")
    if not trade_lines:
        trade_lines.append("Oppiminen kerää vielä kauppadataa")
    sections.append({"id": "trades", "icon": "🧠", "title": "Kauppojen oppiminen", "lines": trade_lines})

    conf_lines = _gemini_conf_lines(learning)
    sections.append(
        {"id": "gemini_conf", "icon": "🔮", "title": "Gemini-confidence", "lines": conf_lines}
    )

    sym_lines: list[str] = []
    if chronic:
        sym_lines.append(
            "Estetty: "
            + ", ".join(get_crypto_label(s) for s in chronic[:6])
            + (f" (+{len(chronic) - 6})" if len(chronic) > 6 else "")
        )
    if cooldown:
        sym_lines.append(
            "Cooldown: "
            + ", ".join(
                f"{get_crypto_label(s)} ({mem[s].get('cooldown_min', 0)} min)"
                for s in cooldown[:4]
            )
        )
    if losers:
        sym_lines.append(
            "Vältetään: "
            + ", ".join(f"{get_crypto_label(s)}" for s in losers[:4])
        )
    if winners:
        sym_lines.append(
            "Suositaan: "
            + ", ".join(f"{get_crypto_label(s)}" for s in winners[:4])
        )
    if not sym_lines:
        sym_lines.append("Ei symbolikohtaisia estoja tai suosituksia vielä")
    sections.append({"id": "symbols", "icon": "🎯", "title": "Symbolimuisti", "lines": sym_lines})

    setup_mem = learning.get("setup_memory") or {}
    setup_lines: list[str] = []
    tagged = int(learning.get("regime_tagged_sells") or 0)
    if setup_mem:
        for key, m in sorted(
            setup_mem.items(),
            key=lambda x: x[1].get("expectancy_eur", 0),
            reverse=True,
        )[:4]:
            setup_lines.append(
                f"{key}: {_fmt_exp(m.get('expectancy_eur'))} ({m.get('trades', 0)} kpl)"
            )
    else:
        setup_lines.append(f"Setup-oppiminen: {tagged}/4 regiimitagattua myyntiä")
    sections.append({"id": "setup", "icon": "📐", "title": "Sisäänostoasetelmat", "lines": setup_lines})

    reg_lines = [f"Aktiivinen regiimi: {regime_name}"]
    overrides = (learning.get("regime_tuning") or {}).get(regime_name)
    if overrides:
        parts = []
        if overrides.get("rotation_enabled") is False:
            parts.append("rotaatio pois")
        if overrides.get("gemini_sell_min_confidence"):
            parts.append(f"Gemini min {overrides['gemini_sell_min_confidence']}")
        if overrides.get("entry_score_min", 1) > 1:
            parts.append(f"score ≥{overrides['entry_score_min']}")
        reg_lines.append("Regiimisäätö: " + ", ".join(parts) if parts else "Regiimisäätö aktiivinen")
    else:
        reg_lines.append(f"Regiimikohtainen viritys: {tagged}/4 tagattua myyntiä")
    sections.append({"id": "regime", "icon": "📈", "title": "Markkinaregiimi", "lines": reg_lines})

    day = _sell_summary(portfolio, 24)
    week = _sell_summary(portfolio, 24 * 7)
    perf_lines = [
        f"Viime 24 h: {day['wins']}V / {day['losses']}T · netto {day['net_eur']:+.2f} €",
        f"Viime 7 pv: {week['wins']}V / {week['losses']}T · netto {week['net_eur']:+.2f} €",
    ]
    if day["win_rate_pct"] is not None:
        perf_lines[0] += f" · win rate {day['win_rate_pct']:.0f} %"
    sections.append({"id": "performance", "icon": "💰", "title": "Tuotto", "lines": perf_lines})

    snapshot = _learning_snapshot(learning, ml, regime)
    changes = _compute_changes(previous_snapshot, snapshot)
    roadmap = _roadmap_progress(learning, ml)

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    last_ms = 0
    if last_narrative_at:
        parsed = _parse_time(last_narrative_at)
        if parsed:
            last_ms = int(parsed.timestamp() * 1000)
    elapsed_sec = (now_ms - last_ms) // 1000 if last_ms else LEARNING_REPORT_INTERVAL_SEC
    next_narrative = max(0, LEARNING_REPORT_INTERVAL_SEC - elapsed_sec)

    return {
        "timestamp": _now_iso(),
        "sections": sections,
        "changes": changes,
        "roadmap": roadmap,
        "snapshot": snapshot,
        "narrative": narrative,
        "lastNarrativeAt": last_narrative_at,
        "nextNarrativeInSec": next_narrative,
        "narrativeIntervalSec": LEARNING_REPORT_INTERVAL_SEC,
    }


def _has_narrative_story(state: dict[str, Any], report: dict[str, Any] | None = None) -> bool:
    for src in (
        state.get("learningNarrative"),
        (report or {}).get("narrative"),
    ):
        if isinstance(src, dict) and str(src.get("story") or "").strip():
            return True
    return False


def _narrative_pending_since(state: dict[str, Any], report: dict[str, Any] | None = None) -> datetime | None:
    for raw in (
        state.get("learningNarrativePendingSince"),
        (report or {}).get("timestamp"),
    ):
        parsed = _parse_time(raw)
        if parsed:
            return parsed
    return None


def _narrative_pending_stale(state: dict[str, Any], report: dict[str, Any]) -> bool:
    if _has_narrative_story(state, report):
        return False
    if not report.get("narrativePending") and not state.get("learningNarrativePendingSince"):
        return False
    since = _narrative_pending_since(state, report)
    if not since:
        return True
    age_sec = (datetime.now(timezone.utc) - since).total_seconds()
    return age_sec >= NARRATIVE_STALE_SEC


def _last_report_ms(state: dict[str, Any]) -> int:
    for key in ("lastLearningReportAt", "lastLearningNarrativeAt"):
        parsed = _parse_time(state.get(key))
        if parsed:
            return int(parsed.timestamp() * 1000)
    cached = state.get("learningReport") or {}
    parsed = _parse_time(cached.get("timestamp"))
    if parsed:
        return int(parsed.timestamp() * 1000)
    return 0


def _next_narrative_sec(last_ms: int, now_ms: int | None = None) -> int:
    now_ms = now_ms if now_ms is not None else int(datetime.now(timezone.utc).timestamp() * 1000)
    if not last_ms:
        return 0
    elapsed_sec = (now_ms - last_ms) // 1000
    return max(0, LEARNING_REPORT_INTERVAL_SEC - elapsed_sec)


def _merge_cached_learning_report(state: dict[str, Any], cached: dict[str, Any]) -> dict[str, Any]:
    """Palauta tallennettu raportti — päivitä vain laskurit ja narratiivi."""
    report = dict(cached)
    last_ms = _last_report_ms(state)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    report["nextNarrativeInSec"] = _next_narrative_sec(last_ms, now_ms)
    narrative = state.get("learningNarrative")
    if narrative:
        report["narrative"] = narrative
        report["narrativePending"] = False
    last_at = state.get("lastLearningNarrativeAt")
    if last_at:
        report["lastNarrativeAt"] = last_at
    err = state.get("learningNarrativeError")
    if err:
        report["narrativeError"] = err
    return report


def refresh_learning_report_if_due(state: dict[str, Any]) -> dict[str, Any]:
    """Päivitä koko oppimisraportti (kortit + Gemini) enintään 6 h välein."""
    cached = state.get("learningReport")
    last_ms = _last_report_ms(state)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    due = not cached or not last_ms or (now_ms - last_ms) >= LEARNING_REPORT_INTERVAL_SEC * 1000

    if not due and cached:
        report = _merge_cached_learning_report(state, cached)
    else:
        report = build_learning_report(
            learning=state.get("learning") or {},
            market_learning=state.get("marketLearning"),
            regime=state.get("regime"),
            portfolio=state.get("portfolio") or {},
            previous_snapshot=state.get("learningReportSnapshot"),
            narrative=state.get("learningNarrative"),
            last_narrative_at=state.get("lastLearningNarrativeAt"),
        )
        state["lastLearningReportAt"] = report["timestamp"]

    report = maybe_refresh_narrative(state, report)
    state["learningReport"] = report
    return report


def _apply_narrative_to_state(
    state: dict[str, Any],
    report: dict[str, Any],
    narrative: dict[str, Any],
) -> None:
    state["lastLearningNarrativeAt"] = _now_iso()
    state["learningNarrative"] = narrative
    state["learningReportSnapshot"] = report.get("snapshot")
    state.pop("learningNarrativeError", None)
    history = list(state.get("learningReportHistory") or [])
    history.insert(
        0,
        {
            "timestamp": state["lastLearningNarrativeAt"],
            "narrative": narrative,
            "changes": report.get("changes"),
        },
    )
    state["learningReportHistory"] = history[:LEARNING_REPORT_HISTORY]


def _run_narrative_refresh(state_data: dict[str, Any], report: dict[str, Any]) -> None:
    """Gemini-narratiivi taustalla — ei blokkaa kaupankäyntikierrosta."""
    global _narrative_refresh_running
    from .gemini import generate_learning_narrative
    from .state_store import load_state, save_state

    try:
        new_narrative, status = generate_learning_narrative(
            report,
            previous_narrative=state_data.get("learningNarrative"),
        )
        if not (new_narrative and status.get("ok")):
            logger.warning("Oppimisraportin Gemini epäonnistui: %s", status.get("message"))
            state = load_state()
            state["learningNarrativeError"] = status.get("message", "Gemini-kertomus epäonnistui")
            state.pop("learningNarrativePendingSince", None)
            merged = build_learning_report(
                learning=state.get("learning") or {},
                market_learning=state.get("marketLearning"),
                regime=state.get("regime"),
                portfolio=state.get("portfolio") or {},
                previous_snapshot=state.get("learningReportSnapshot"),
                narrative=state.get("learningNarrative"),
                last_narrative_at=state.get("lastLearningNarrativeAt"),
            )
            merged["narrativePending"] = False
            merged["narrativeError"] = state["learningNarrativeError"]
            state.pop("learningNarrativePendingSince", None)
            state["learningReport"] = merged
            save_state(state)
            return

        state = load_state()
        _apply_narrative_to_state(state, report, new_narrative)

        merged = dict(state.get("learningReport") or report)
        merged["narrative"] = new_narrative
        merged["narrativePending"] = False
        merged["lastNarrativeAt"] = state["lastLearningNarrativeAt"]
        merged["nextNarrativeInSec"] = LEARNING_REPORT_INTERVAL_SEC
        merged.pop("narrativeError", None)
        state.pop("learningNarrativePendingSince", None)
        state["learningReport"] = merged
        save_state(state)
    except Exception as exc:
        logger.exception("Oppimisraportin taustapäivitys epäonnistui")
        try:
            state = load_state()
            state["learningNarrativeError"] = str(exc) or "Oppimisraportin taustapäivitys epäonnistui"
            state.pop("learningNarrativePendingSince", None)
            merged = dict(state.get("learningReport") or report)
            merged["narrativePending"] = False
            merged["narrativeError"] = state["learningNarrativeError"]
            state["learningReport"] = merged
            save_state(state)
        except Exception:
            logger.exception("Oppimisraportin virhetilan tallennus epäonnistui")
    finally:
        with _narrative_refresh_lock:
            _narrative_refresh_running = False


def maybe_refresh_narrative(
    state: dict[str, Any],
    report: dict[str, Any],
) -> dict[str, Any]:
    """Päivitä Gemini-narratiivi 6 h välein taustalla."""
    global _narrative_refresh_running
    from .gemini import is_configured

    report["narrativePending"] = False

    if not is_configured():
        report["narrative"] = {
            "intro": "Gemini ei ole käytössä — alla rule-pohjainen oppimisraportti päivittyy 6 h välein.",
            "learned": "",
            "in_use": "",
            "next_steps": "",
            "ideas": "",
            "source": "local",
        }
        report["lastNarrativeAt"] = state.get("lastLearningNarrativeAt")
        return report

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    last_ms = 0
    last_at = state.get("lastLearningNarrativeAt")
    if last_at:
        parsed = _parse_time(last_at)
        if parsed:
            last_ms = int(parsed.timestamp() * 1000)

    due = (now_ms - last_ms) >= LEARNING_REPORT_INTERVAL_SEC * 1000 if last_ms else True
    narrative = state.get("learningNarrative")
    pending_stale = _narrative_pending_stale(state, report)
    narrative_error = state.get("learningNarrativeError") or report.get("narrativeError")
    retry_after_error = bool(narrative_error) and not _has_narrative_story(state, report)

    if not due and not pending_stale and not retry_after_error:
        report["narrative"] = narrative
        report["lastNarrativeAt"] = last_at
        report["narrativeError"] = state.get("learningNarrativeError")
        return report

    with _narrative_refresh_lock:
        already_running = _narrative_refresh_running
        if pending_stale and already_running:
            logger.warning("Gemini-kertomus näyttää jumittuneen — yritetään uudelleen")
            _narrative_refresh_running = False
            already_running = False
        if not already_running:
            _narrative_refresh_running = True
            state["learningNarrativePendingSince"] = _now_iso()

    if not already_running:
        report["narrativePending"] = True
        report.pop("narrativeError", None)
        state.pop("learningNarrativeError", None)
        threading.Thread(
            target=_run_narrative_refresh,
            args=(
                {
                    "learningNarrative": narrative,
                },
                report,
            ),
            name="learning-narrative",
            daemon=True,
        ).start()

    report["narrative"] = narrative
    report["lastNarrativeAt"] = last_at
    report["narrativeError"] = state.get("learningNarrativeError")
    return report
