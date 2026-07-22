"""Oppimisraportti — rule-pohjainen yhteenveto + valinnainen Gemini-narratiivi (6 h)."""

from __future__ import annotations

import logging
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

_GREETING_LINE_RE = re.compile(r"^\s*Hei\s+sijoittaja,?\s*$", re.I)
_GREETING_PREFIX_RE = re.compile(r"^\s*Hei\s+sijoittaja,?\s*", re.I)
_NAME_FIXES = (
    (re.compile(r"Kryptosimuattori", re.I), "Krypto Simulaattori"),
    (re.compile(r"krypto-simulaattori", re.I), "Krypto Simulaattori"),
)


def _sanitize_narrative_text(text: str) -> str:
    if not text:
        return text
    for pattern, replacement in _NAME_FIXES:
        text = pattern.sub(replacement, text)
    text = _GREETING_PREFIX_RE.sub("", text)
    lines = text.split("\n")
    while lines and _GREETING_LINE_RE.match(lines[0]):
        lines.pop(0)
        while lines and not lines[0].strip():
            lines.pop(0)
    return "\n".join(lines).strip()


def sanitize_learning_narrative(narrative: dict[str, Any] | None) -> dict[str, Any] | None:
    """Poista tervehdykset ja korjaa sovelluksen nimi kertomuksessa."""
    if not narrative:
        return narrative
    cleaned = dict(narrative)
    keys = (
        "story",
        "intro",
        "learned",
        "in_use",
        "next_steps",
        "ideas",
        "shadow_learned",
        "shadow_ideas",
        "micro_learned",
        "micro_ideas",
        "exit_learned",
        "exit_ideas",
        "sell_learned",
        "sell_ideas",
        "anticipation_learned",
        "anticipation_ideas",
        "satellite_learned",
        "satellite_ideas",
    )
    for key in keys:
        if cleaned.get(key):
            cleaned[key] = _sanitize_narrative_text(str(cleaned[key]))
        en_key = f"{key}_en"
        if cleaned.get(en_key):
            cleaned[en_key] = _sanitize_narrative_text(str(cleaned[en_key]))
    return cleaned

from .bitfinex import get_crypto_label, normalize_symbol

LEARNING_REPORT_INTERVAL_SEC = int(os.environ.get("LEARNING_REPORT_INTERVAL_SEC", "21600"))
NARRATIVE_ERROR_RETRY_SEC = int(os.environ.get("NARRATIVE_ERROR_RETRY_SEC", "600"))
GEMINI_NARRATIVE_HISTORY = int(os.environ.get("GEMINI_NARRATIVE_HISTORY", "40"))
NARRATIVE_STALE_SEC = int(os.environ.get("NARRATIVE_STALE_SEC", "300"))

logger = logging.getLogger(__name__)
_narrative_refresh_lock = threading.Lock()
_narrative_refresh_running = False
_narrative_kick_lock = threading.Lock()
_last_narrative_kick_ms = 0

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
        "key": "trade_flow_learning",
        "label": "Trade flow (fl+/fl−)",
        "metric": "closed_trades_with_flow",
        "target": 8,
        "action": "Arvioidaan ostoalotteisen flow'n vaikutus entryihin",
    },
    {
        "key": "shadow_portfolio",
        "label": "Varjosalkku vs. live",
        "metric": "shadow_mirror_trades",
        "target": 3,
        "action": "Luotettava varjo-live-vertailu ennen sääntöjen siirtoa",
    },
    {
        "key": "setup_learning",
        "label": "Setup-oppiminen (omat sisäänostot)",
        "metric": "regime_tagged_sells",
        "target": 4,
        "mode": "active",
        "action": "Setup-muisti chipillä 📐 — käytössä",
    },
    {
        "key": "richer_buckets",
        "label": "Richer markkina-ämpärit",
        "metric": "buckets_learned",
        "target": 18,
        "mode": "active",
        "action": "Regiimi×24h×MTF×RSI×vol×deep + fallback — käytössä",
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


def _roadmap_metrics(
    learning: dict[str, Any],
    ml: dict[str, Any],
    *,
    portfolio: dict[str, Any] | None = None,
    bot_state: dict[str, Any] | None = None,
) -> dict[str, float]:
    stats = learning.get("stats") or {}
    closed_with_flow = 0
    if portfolio:
        from .market_microstructure import _linked_micro_outcomes

        linked = _linked_micro_outcomes(portfolio.get("trades") or [])
        closed_with_flow = sum(1 for item in linked if item["entry"].get("flowBucket"))

    shadow = (bot_state or {}).get("dailyPolicyShadow") or {}
    shadow_metrics = shadow.get("portfolioMetrics") or {}
    shadow_mirror = int(shadow_metrics.get("tradesMirrored") or 0) + int(
        shadow_metrics.get("tradesSkipped") or 0
    )

    return {
        "profit_take_trades": float((stats.get("profit_take") or {}).get("trades") or 0),
        "regime_tagged_sells": float(learning.get("regime_tagged_sells") or 0),
        "setup_memory_keys": float(len(learning.get("setup_memory") or {})),
        "buckets_learned": float(ml.get("bucketsLearned") or 0),
        "buckets_tracked": float(ml.get("bucketsTracked") or 0),
        "gemini_confidence_tagged": float(learning.get("gemini_confidence_tagged") or 0),
        "closed_trades_with_flow": float(closed_with_flow),
        "shadow_mirror_trades": float(shadow_mirror),
    }


def _roadmap_progress(
    learning: dict[str, Any],
    ml: dict[str, Any],
    *,
    portfolio: dict[str, Any] | None = None,
    bot_state: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    metrics = _roadmap_metrics(learning, ml, portfolio=portfolio, bot_state=bot_state)
    items: list[dict[str, Any]] = []
    for cfg in ROADMAP_ITEMS:
        current = int(metrics.get(cfg["metric"], 0))
        target = int(cfg["target"])
        mode = cfg.get("mode", "collect")
        if mode == "active":
            status = "aktiivinen"
            progress = "käytössä"
        elif current >= target:
            status = "aktiivinen"
            progress = "käytössä"
        elif current >= target * 0.5:
            status = "tulossa"
            progress = f"{current}/{target}"
        else:
            status = "kerätään"
            progress = f"{current}/{target}"
        items.append(
            {
                "key": cfg["key"],
                "label": cfg["label"],
                "progress": progress,
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
        "buy_scale": learning.get("buy_scale"),
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
    prev_buy = prev.get("buy_scale")
    curr_buy = curr.get("buy_scale")
    if prev_buy is not None and curr_buy is not None and prev_buy != curr_buy:
        changes.append(f"Ostokertoimen skaalaus {prev_buy} → {curr_buy}")
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
    tagged_buys = int(learning.get("gemini_confidence_tagged_buys") or 0)
    tagged_sells = int(learning.get("gemini_confidence_tagged_sells") or 0)
    stats = learning.get("gemini_confidence_stats") or {}
    scales = learning.get("gemini_confidence_scales") or {}
    min_conf = int(learning.get("gemini_sell_min_confidence") or 0)

    if tagged < 6:
        lines.append(
            f"Kerätään dataa ({tagged}/6) — Gemini-ostot {tagged_buys}, "
            f"Gemini-myynnit {tagged_sells}"
        )
        lines.append("Oppiminen: sulkeutuneet ostot + Gemini-aloittamat myynnit")
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
    bot_state: dict[str, Any] | None = None,
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
        ("time_stop", "Aikastoppi/jumitus"),
        ("gemini_sell", "Gemini-myynnit"),
        ("profit_take", "Voitto-otto"),
        ("stop_loss", "Stop-loss"),
        ("loser_release", "Häviäjän vapautus"),
        ("setup_exit", "Huono asetelma"),
        ("bear_cash_trim", "Karhu-kassavara"),
    ):
        cat = stats.get(key) or {}
        n = int(cat.get("trades") or 0)
        if n:
            trade_lines.append(f"{label}: {_fmt_exp(cat.get('expectancy_eur'))} ({n} kpl)")
    if not trade_lines:
        trade_lines.append("Oppiminen kerää vielä kauppadataa")
    sections.append({"id": "trades", "icon": "🧠", "title": "Kauppojen oppiminen", "lines": trade_lines})

    if bot_state:
        from .daily_policy_shadow import learning_report_lines

        shadow_lines = learning_report_lines(bot_state)
        if shadow_lines:
            sections.append(
                {
                    "id": "shadow_policy",
                    "icon": "🧪",
                    "title": "Varjopolitiikka (testidata)",
                    "lines": shadow_lines,
                }
            )

        from .price_spike_shadow import learning_report_lines as spike_lines

        spike_report_lines = spike_lines(bot_state)
        if spike_report_lines:
            sections.append(
                {
                    "id": "price_spike_shadow",
                    "icon": "⚡",
                    "title": "Hintapiikin järkevyystarkistus (testidata)",
                    "lines": spike_report_lines,
                }
            )

        from .entry_diagnostics_shadow import learning_report_lines as entry_diag_lines

        entry_diag_report_lines = entry_diag_lines(bot_state)
        if entry_diag_report_lines:
            sections.append(
                {
                    "id": "entry_diagnostics_shadow",
                    "icon": "🧭",
                    "title": "Ostohetken varjodiagnostiikka (testidata)",
                    "lines": entry_diag_report_lines,
                }
            )

        from .market_microstructure import build_gemini_context, learning_report_lines as micro_lines

        micro_ctx = build_gemini_context(
            portfolio,
            learning=learning,
            bot_state=bot_state,
        )
        micro_report_lines = micro_lines(micro_ctx)
        if micro_report_lines:
            sections.append(
                {
                    "id": "microstructure",
                    "icon": "📖",
                    "title": "Order book, flow & crowd",
                    "lines": micro_report_lines,
                }
            )

        from .exit_learning import build_gemini_context as build_exit_context, learning_report_lines as exit_lines

        exit_ctx = build_exit_context(
            portfolio,
            learning=learning,
            bot_state=bot_state,
        )
        exit_report_lines = exit_lines(exit_ctx)
        if exit_report_lines:
            sections.append(
                {
                    "id": "exit_peak",
                    "icon": "⛰️",
                    "title": "Huippumyynti",
                    "lines": exit_report_lines,
                }
            )

        from .sell_outcome_learning import build_gemini_context as build_sell_context
        from .sell_outcome_learning import learning_report_lines as sell_lines

        sell_ctx = build_sell_context(
            portfolio,
            learning=learning,
            bot_state=bot_state,
        )
        sell_report_lines = sell_lines(sell_ctx)
        if sell_report_lines:
            sections.append(
                {
                    "id": "sell_outcomes",
                    "icon": "💹",
                    "title": "Voitto- vs tappiomyynnit",
                    "lines": sell_report_lines,
                }
            )

        from .regime_anticipation_learning import (
            build_gemini_context as build_anticipation_context,
        )
        from .regime_anticipation_learning import (
            learning_report_lines as anticipation_lines,
        )

        anticipation_ctx = build_anticipation_context(
            portfolio,
            learning=learning,
            bot_state=bot_state,
            regime=regime,
        )
        anticipation_report_lines = anticipation_lines(anticipation_ctx)
        if anticipation_report_lines:
            sections.append(
                {
                    "id": "regime_anticipation",
                    "icon": "↻",
                    "title": "Regiimin ennakointi",
                    "lines": anticipation_report_lines,
                }
            )

        from .bull_satellite import build_gemini_context as build_satellite_context
        from .bull_satellite import learning_report_lines as satellite_lines

        satellite_ctx = build_satellite_context(
            portfolio,
            bot_state=bot_state,
            tickers=bot_state.get("tickers") if bot_state else None,
        )
        satellite_report_lines = satellite_lines(satellite_ctx)
        if satellite_report_lines:
            sections.append(
                {
                    "id": "bull_satellite",
                    "icon": "🛰️",
                    "title": "Bull-satelliitti (65/35)",
                    "lines": satellite_report_lines,
                }
            )

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
        st = overrides.get("stop_tuning") or {}
        if st.get("level") in ("light", "full"):
            parts.append(f"stop-loss {st.get('level')}")
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
    roadmap = _roadmap_progress(learning, ml, portfolio=portfolio, bot_state=bot_state)

    shadow_policy = None
    price_spike_learning = None
    entry_diagnostics_learning = None
    microstructure_learning = None
    exit_peak_learning = None
    sell_outcome_learning = None
    regime_anticipation_learning = None
    bull_satellite_learning = None
    if bot_state:
        from .daily_policy_shadow import build_gemini_context

        shadow_policy = build_gemini_context(bot_state)
        from .price_spike_shadow import build_gemini_context as build_spike_context

        price_spike_learning = build_spike_context(bot_state)
        from .entry_diagnostics_shadow import build_gemini_context as build_entry_diag_context

        entry_diagnostics_learning = build_entry_diag_context(bot_state)
        from .market_microstructure import build_gemini_context as build_micro_context

        microstructure_learning = build_micro_context(
            portfolio,
            learning=learning,
            bot_state=bot_state,
        )
        from .exit_learning import build_gemini_context as build_exit_context

        exit_peak_learning = build_exit_context(
            portfolio,
            learning=learning,
            bot_state=bot_state,
        )
        from .sell_outcome_learning import build_gemini_context as build_sell_context

        sell_outcome_learning = build_sell_context(
            portfolio,
            learning=learning,
            bot_state=bot_state,
        )
        from .regime_anticipation_learning import build_gemini_context as build_anticipation_context

        regime_anticipation_learning = build_anticipation_context(
            portfolio,
            learning=learning,
            bot_state=bot_state,
            regime=regime,
        )
        from .bull_satellite import build_gemini_context as build_satellite_context

        bull_satellite_learning = build_satellite_context(
            portfolio,
            bot_state=bot_state,
            tickers=bot_state.get("tickers"),
        )

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
        "shadowPolicy": shadow_policy,
        "priceSpikeLearning": price_spike_learning,
        "entryDiagnosticsLearning": entry_diagnostics_learning,
        "microstructureLearning": microstructure_learning,
        "exitPeakLearning": exit_peak_learning,
        "sellOutcomeLearning": sell_outcome_learning,
        "regimeAnticipationLearning": regime_anticipation_learning,
        "bullSatelliteLearning": bull_satellite_learning,
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
    """Milloin rule-pohjainen raportti viimeksi rakennettu."""
    parsed = _parse_time(state.get("lastLearningReportAt"))
    if parsed:
        return int(parsed.timestamp() * 1000)
    cached = state.get("learningReport") or {}
    parsed = _parse_time(cached.get("timestamp"))
    if parsed:
        return int(parsed.timestamp() * 1000)
    return 0


def _last_narrative_ms(state: dict[str, Any]) -> int:
    """Milloin Gemini-kertomus viimeksi onnistui."""
    parsed = _parse_time(state.get("lastLearningNarrativeAt"))
    if parsed:
        return int(parsed.timestamp() * 1000)
    return 0


def _last_narrative_error_ms(state: dict[str, Any]) -> int:
    """Milloin Gemini-kertomus viimeksi epäonnistui."""
    parsed = _parse_time(state.get("learningNarrativeErrorAt"))
    if parsed:
        return int(parsed.timestamp() * 1000)
    return 0


def _infer_narrative_error_at(state: dict[str, Any], report: dict[str, Any]) -> str:
    """Arvioi virheen aika — älä käytä uutta pendingSince-arvoa cooldownin nollaukseen."""
    for raw in (
        report.get("timestamp"),
        state.get("lastLearningReportAt"),
    ):
        parsed = _parse_time(raw)
        if parsed:
            return parsed.isoformat()
    return _now_iso()


def ensure_narrative_error_state(state: dict[str, Any]) -> bool:
    """Synkronoi virheilmoitus ja aikaleima — estää jumittuneen countdownin ja uudelleenyrityksen."""
    report = state.get("learningReport") or {}
    if _has_narrative_story(state, report):
        return _clear_orphan_narrative_errors(state, report)
    err = state.get("learningNarrativeError") or report.get("narrativeError")
    if not err:
        return False
    changed = False
    if not state.get("learningNarrativeError"):
        state["learningNarrativeError"] = str(err)
        changed = True
    if not _last_narrative_error_ms(state):
        state["learningNarrativeErrorAt"] = _infer_narrative_error_at(state, report)
        changed = True
    return changed


def _clear_orphan_narrative_errors(state: dict[str, Any], report: dict[str, Any]) -> bool:
    """Poista virheilmoitus kun kertomus on jo olemassa."""
    changed = False
    from .state_store import mark_state_keys_deleted

    if state.get("learningNarrativeError") or state.get("learningNarrativeErrorAt"):
        mark_state_keys_deleted(state, "learningNarrativeError", "learningNarrativeErrorAt")
        changed = True
    if report.get("narrativeError"):
        cleaned = dict(report)
        cleaned.pop("narrativeError", None)
        state["learningReport"] = cleaned
        changed = True
    return changed


def persist_ensure_narrative_error_state(state: dict[str, Any]) -> bool:
    """Tallenna ensure-muutokset — älä ylikirjoita käynnissä olevaa Gemini-säiettä."""
    if not ensure_narrative_error_state(state):
        return False
    from .state_store import STATE_DELETED_KEYS, patch_narrative_error_state, save_state

    if narrative_refresh_in_progress():
        patch_narrative_error_state(state)
        return True
    save_state(state)
    return True


def narrative_refresh_in_progress() -> bool:
    return _narrative_refresh_running


def _persist_narrative_pending_since(state: dict[str, Any]) -> None:
    """Tallenna vain pending-lippu — ei koske learningReport/Gemini-tuloksia."""
    since = state.get("learningNarrativePendingSince")
    if not since:
        return
    from .state_store import patch_state_keys

    patch_state_keys({"learningNarrativePendingSince": since})


def _persist_learning_report_rule_cards(state: dict[str, Any]) -> None:
    """Tallenna rule-kortit ilman narratiivi-/virhekenttien ylikirjoitusta."""
    from .state_store import patch_learning_report_rule_cards

    report = state.get("learningReport") or {}
    patch_learning_report_rule_cards(
        last_learning_report_at=state.get("lastLearningReportAt"),
        snapshot=state.get("learningReportSnapshot"),
        sections=report.get("sections"),
        timestamp=report.get("timestamp"),
        changes=report.get("changes"),
    )


def _next_narrative_error_retry_sec(state: dict[str, Any], now_ms: int | None = None) -> int:
    """Sekunteja seuraavaan uudelleenyritykseen virheen jälkeen."""
    now_ms = now_ms if now_ms is not None else int(datetime.now(timezone.utc).timestamp() * 1000)
    err_ms = _last_narrative_error_ms(state)
    if not err_ms:
        return NARRATIVE_ERROR_RETRY_SEC
    elapsed_sec = (now_ms - err_ms) // 1000
    return max(0, NARRATIVE_ERROR_RETRY_SEC - elapsed_sec)


def _narrative_error_retry_due(
    state: dict[str, Any],
    report: dict[str, Any] | None = None,
) -> bool:
    """Onko virheen jälkeen kulunut tarpeeksi aikaa uudelleenyritykseen."""
    report = report if report is not None else (state.get("learningReport") or {})
    if not (state.get("learningNarrativeError") or report.get("narrativeError")):
        return False
    if _has_narrative_story(state, report):
        return False
    err_ms = _last_narrative_error_ms(state)
    if not err_ms:
        return False
    age_sec = (datetime.now(timezone.utc).timestamp() * 1000 - err_ms) / 1000
    return age_sec >= NARRATIVE_ERROR_RETRY_SEC


def _next_narrative_sec(last_narrative_ms: int, now_ms: int | None = None) -> int:
    now_ms = now_ms if now_ms is not None else int(datetime.now(timezone.utc).timestamp() * 1000)
    if not last_narrative_ms:
        return 0
    elapsed_sec = (now_ms - last_narrative_ms) // 1000
    return max(0, LEARNING_REPORT_INTERVAL_SEC - elapsed_sec)


def _merge_cached_learning_report(state: dict[str, Any], cached: dict[str, Any]) -> dict[str, Any]:
    """Palauta tallennettu raportti — päivitä vain laskurit ja narratiivi."""
    report = dict(cached)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    report["nextNarrativeInSec"] = _next_narrative_sec(_last_narrative_ms(state), now_ms)
    narrative = sanitize_learning_narrative(state.get("learningNarrative"))
    if narrative:
        report["narrative"] = narrative
        report["narrativePending"] = False
    last_at = state.get("lastLearningNarrativeAt")
    if last_at:
        report["lastNarrativeAt"] = last_at
    if _has_narrative_story(state, report):
        report.pop("narrativeError", None)
        report["nextNarrativeInSec"] = _next_narrative_sec(_last_narrative_ms(state), now_ms)
        return report
    err = state.get("learningNarrativeError") or report.get("narrativeError")
    if err:
        report["narrativeError"] = err
        report["nextNarrativeInSec"] = _next_narrative_error_retry_sec(state, now_ms)
    elif report.get("narrativePending"):
        report.pop("narrativeError", None)
    return report


def clear_stale_narrative_error(state: dict[str, Any]) -> bool:
    """Poista tunnettu vanhentunut virhe (korjattu bugi) — sallii uudelleenyrityksen."""
    if _has_narrative_story(state, state.get("learningReport")):
        return False
    err = state.get("learningNarrativeError") or (state.get("learningReport") or {}).get(
        "narrativeError"
    )
    if not err or "_model_candidates" not in str(err):
        return False
    from .state_store import mark_state_keys_deleted

    mark_state_keys_deleted(state, "learningNarrativeError", "learningNarrativeErrorAt")
    cached = state.get("learningReport")
    if isinstance(cached, dict):
        report = dict(cached)
        report.pop("narrativeError", None)
        state["learningReport"] = report
    return True


def needs_narrative_refresh(state: dict[str, Any]) -> bool:
    """Tarvitaanko Gemini-kertomus (6 h välein, uudelleenyritys jos puuttuu tai epäonnistui)."""
    from .gemini import is_configured

    if not is_configured():
        return False
    cached = state.get("learningReport")
    if not cached:
        return False
    report = _merge_cached_learning_report(state, cached)
    narrative_error = state.get("learningNarrativeError") or report.get("narrativeError")
    if narrative_error:
        if persist_ensure_narrative_error_state(state):
            return False
        if _narrative_error_retry_due(state, report):
            return True
    if report.get("narrativePending") or state.get("learningNarrativePendingSince"):
        return _narrative_pending_stale(state, report)
    if narrative_error:
        return False
    last_ms = _last_narrative_ms(state)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    if last_ms and (now_ms - last_ms) >= LEARNING_REPORT_INTERVAL_SEC * 1000:
        return True
    if not last_ms:
        return True
    return not _has_narrative_story(state, report)


def kick_narrative_refresh_if_due(min_interval_sec: int = 90) -> None:
    """Herätä Gemini-kertomus taustalla (API-poll / wake) ilman että blokataan pyyntöä."""
    global _last_narrative_kick_ms

    from .gemini import is_configured

    if not is_configured():
        return

    state = None
    try:
        from .state_store import load_state

        state = load_state()
    except Exception:
        logger.warning("Gemini-kertomuksen kick: tilan luku epäonnistui", exc_info=True)
        return

    if not needs_narrative_refresh(state):
        return

    cached = state.get("learningReport") or {}
    report = _merge_cached_learning_report(state, cached)
    retry_due = _narrative_error_retry_due(state, report)

    pending_since = _parse_time(state.get("learningNarrativePendingSince"))
    if pending_since and not retry_due:
        age = (datetime.now(timezone.utc) - pending_since).total_seconds()
        if age < NARRATIVE_STALE_SEC:
            return

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    with _narrative_kick_lock:
        if now_ms - _last_narrative_kick_ms < min_interval_sec * 1000:
            return
        _last_narrative_kick_ms = now_ms

    def _run() -> None:
        try:
            from .state_store import load_state

            s = load_state()
            refresh_narrative_if_due(s)
        except Exception:
            logger.exception("Gemini-kertomuksen taustakick epäonnistui")

    threading.Thread(target=_run, name="learning-narrative-kick", daemon=True).start()


_narrative_en_kick_lock = threading.Lock()
_last_narrative_en_kick_ms = 0
_narrative_en_running = False


def kick_narrative_en_backfill_if_needed(min_interval_sec: int = 120) -> None:
    """Fill missing *_en fields on the current Finnish Gemini narrative (for /eng/)."""
    global _last_narrative_en_kick_ms, _narrative_en_running

    from .gemini import is_configured, narrative_needs_en

    if not is_configured():
        return

    try:
        from .state_store import load_state, patch_state_keys

        state = load_state()
    except Exception:
        logger.warning("Narrative EN backfill: state load failed", exc_info=True)
        return

    narrative = sanitize_learning_narrative(state.get("learningNarrative"))
    if not narrative_needs_en(narrative):
        return

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    with _narrative_en_kick_lock:
        if _narrative_en_running:
            return
        if now_ms - _last_narrative_en_kick_ms < min_interval_sec * 1000:
            return
        _last_narrative_en_kick_ms = now_ms
        _narrative_en_running = True

    def _run() -> None:
        global _narrative_en_running
        try:
            from .gemini import translate_learning_narrative_en
            from .state_store import load_state, patch_state_keys

            s = load_state()
            current = sanitize_learning_narrative(s.get("learningNarrative")) or {}
            if not narrative_needs_en(current):
                return
            translated, status = translate_learning_narrative_en(current)
            if not status.get("ok") or not translated:
                logger.warning("Narrative EN backfill failed: %s", status.get("message"))
                return
            # Preserve Finnish primary fields; merge *_en only.
            merged = dict(current)
            for key, val in translated.items():
                if key.endswith("_en") and isinstance(val, str) and val.strip():
                    merged[key] = val
            patch_state_keys({"learningNarrative": merged})
            # Also update learningReport.narrative + newest history entry.
            report = dict(s.get("learningReport") or {})
            if report:
                report_narr = dict(report.get("narrative") or current)
                for key, val in merged.items():
                    if key.endswith("_en") and isinstance(val, str) and val.strip():
                        report_narr[key] = val
                report["narrative"] = report_narr
                patch_state_keys({"learningReport": report})
            history = list(s.get("learningReportHistory") or [])
            if history and isinstance(history[0], dict):
                entry = dict(history[0])
                entry_narr = dict(entry.get("narrative") or current)
                for key, val in merged.items():
                    if key.endswith("_en") and isinstance(val, str) and val.strip():
                        entry_narr[key] = val
                entry["narrative"] = entry_narr
                history[0] = entry
                patch_state_keys({"learningReportHistory": history})
            logger.info("Narrative EN backfill stored")
        except Exception:
            logger.exception("Narrative EN backfill crashed")
        finally:
            with _narrative_en_kick_lock:
                _narrative_en_running = False

    threading.Thread(target=_run, name="learning-narrative-en", daemon=True).start()


def refresh_narrative_if_due(state: dict[str, Any]) -> dict[str, Any]:
    """Päivitä vain Gemini-kertomus (rule-kortit pysyvät) kun 6 h täyttyy."""
    cached = state.get("learningReport")
    if not cached:
        return refresh_learning_report_if_due(state)
    report = _merge_cached_learning_report(state, cached)
    report = maybe_refresh_narrative(state, report)
    state["learningReport"] = report
    if _narrative_refresh_running:
        _persist_narrative_pending_since(state)
    return report


def needs_learning_report_refresh(state: dict[str, Any]) -> bool:
    """Tarvitaanko oppimisraporttiin kirjoitus/Gemini-kutsu (ei joka minuutti)."""
    cached = state.get("learningReport")
    last_ms = _last_report_ms(state)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    due = not cached or not last_ms or (now_ms - last_ms) >= LEARNING_REPORT_INTERVAL_SEC * 1000
    if due:
        return True
    return needs_narrative_refresh(state)


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
            bot_state=state,
        )
        state["lastLearningReportAt"] = report["timestamp"]

    report = maybe_refresh_narrative(state, report)
    state["learningReport"] = report
    if _narrative_refresh_running:
        _persist_narrative_pending_since(state)
        if due:
            _persist_learning_report_rule_cards(state)
    return report


def _apply_narrative_to_state(
    state: dict[str, Any],
    report: dict[str, Any],
    narrative: dict[str, Any],
) -> None:
    narrative = sanitize_learning_narrative(narrative) or narrative
    state["lastLearningNarrativeAt"] = _now_iso()
    state["learningNarrative"] = narrative
    state["learningReportSnapshot"] = report.get("snapshot")
    from .state_store import mark_state_keys_deleted

    mark_state_keys_deleted(state, "learningNarrativeError", "learningNarrativeErrorAt")
    history = list(state.get("learningReportHistory") or [])
    history.insert(
        0,
        {
            "timestamp": state["lastLearningNarrativeAt"],
            "narrative": narrative,
            "changes": report.get("changes"),
        },
    )
    state["learningReportHistory"] = history[:GEMINI_NARRATIVE_HISTORY]


def _narrative_has_content(narrative: dict[str, Any] | None) -> bool:
    if not narrative:
        return False
    return bool(narrative.get("story") or narrative.get("intro"))


def build_gemini_narrative_history(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Gemini-kertomusten historia UI-modaa varten (uusin ensin)."""
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()

    current = sanitize_learning_narrative(state.get("learningNarrative"))
    current_ts = str(state.get("lastLearningNarrativeAt") or "")
    if _narrative_has_content(current):
        entries.append(
            {
                "timestamp": current_ts,
                "narrative": current,
                "current": True,
            }
        )
        if current_ts:
            seen.add(current_ts)

    for item in state.get("learningReportHistory") or []:
        ts = str(item.get("timestamp") or "")
        if ts and ts in seen:
            continue
        narrative = sanitize_learning_narrative(item.get("narrative"))
        if not _narrative_has_content(narrative):
            continue
        entries.append(
            {
                "timestamp": ts,
                "narrative": narrative,
                "current": False,
            }
        )
        if ts:
            seen.add(ts)

    return entries[:GEMINI_NARRATIVE_HISTORY]


def _run_narrative_refresh(state_data: dict[str, Any], report: dict[str, Any]) -> None:
    """Gemini-narratiivi taustalla — ei blokkaa kaupankäyntikierrosta."""
    global _narrative_refresh_running
    from .gemini import generate_learning_narrative
    from .state_store import load_state, mark_state_keys_deleted, save_state

    try:
        new_narrative, status = generate_learning_narrative(
            report,
            previous_narrative=state_data.get("learningNarrative"),
        )
        if not (new_narrative and status.get("ok")):
            logger.warning("Oppimisraportin Gemini epäonnistui: %s", status.get("message"))
            state = load_state()
            state["learningNarrativeError"] = status.get("message", "Gemini-kertomus epäonnistui")
            state["learningNarrativeErrorAt"] = _now_iso()
            mark_state_keys_deleted(state, "learningNarrativePendingSince")
            merged = _merge_cached_learning_report(
                state,
                dict(state.get("learningReport") or report),
            )
            merged["narrativePending"] = False
            merged["narrativeError"] = state["learningNarrativeError"]
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
        mark_state_keys_deleted(state, "learningNarrativePendingSince")
        state["learningReport"] = merged
        save_state(state)
    except Exception as exc:
        logger.exception("Oppimisraportin taustapäivitys epäonnistui")
        try:
            state = load_state()
            state["learningNarrativeError"] = str(exc) or "Oppimisraportin taustapäivitys epäonnistui"
            state["learningNarrativeErrorAt"] = _now_iso()
            mark_state_keys_deleted(state, "learningNarrativePendingSince")
            merged = _merge_cached_learning_report(
                state,
                dict(state.get("learningReport") or report),
            )
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

    ensure_narrative_error_state(state)

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    last_ms = 0
    last_at = state.get("lastLearningNarrativeAt")
    if last_at:
        parsed = _parse_time(last_at)
        if parsed:
            last_ms = int(parsed.timestamp() * 1000)

    due = (now_ms - last_ms) >= LEARNING_REPORT_INTERVAL_SEC * 1000 if last_ms else True
    narrative = sanitize_learning_narrative(state.get("learningNarrative"))
    pending_stale = _narrative_pending_stale(state, report)
    narrative_error = state.get("learningNarrativeError") or report.get("narrativeError")
    retry_after_error = _narrative_error_retry_due(state, report)

    if not due and not pending_stale and not retry_after_error:
        report["narrative"] = narrative
        report["lastNarrativeAt"] = last_at
        report["narrativeError"] = state.get("learningNarrativeError")
        if narrative_error:
            report["nextNarrativeInSec"] = _next_narrative_error_retry_sec(state, now_ms)
        else:
            report["nextNarrativeInSec"] = _next_narrative_sec(last_ms, now_ms)
        return report

    with _narrative_refresh_lock:
        already_running = _narrative_refresh_running
        if already_running and due and not state.get("learningNarrativePendingSince"):
            logger.warning("Gemini-kertomus: jumittunut lukko — nollataan")
            _narrative_refresh_running = False
            already_running = False
        if pending_stale and already_running:
            logger.warning("Gemini-kertomus näyttää jumittuneen — odotetaan käynnissä olevaa säiettä")
        elif not already_running:
            _narrative_refresh_running = True
            state["learningNarrativePendingSince"] = _now_iso()

    if not already_running:
        report["narrativePending"] = True
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
    elif retry_after_error or pending_stale:
        report["narrativePending"] = True
    elif already_running and due:
        report["narrativePending"] = True

    report["narrative"] = narrative
    report["lastNarrativeAt"] = last_at
    if narrative_error:
        report["nextNarrativeInSec"] = _next_narrative_error_retry_sec(state, now_ms)
    else:
        report["nextNarrativeInSec"] = _next_narrative_sec(last_ms, now_ms)
    if state.get("learningNarrativeError"):
        report["narrativeError"] = state["learningNarrativeError"]
    elif report.get("narrativePending"):
        report.pop("narrativeError", None)
    return report
