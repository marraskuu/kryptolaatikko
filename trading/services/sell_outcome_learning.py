"""Voitto- vs tappiomyyntien oppiminen — raportti + Gemini-konteksti."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .bitfinex import get_crypto_label
from .learning import LEARNING_WINDOW, _category, _net_eur

MIN_SAMPLES = 3
WIN_EPS = 0.01


def _sell_trades(portfolio: dict[str, Any], limit: int = LEARNING_WINDOW) -> list[dict[str, Any]]:
    return [t for t in (portfolio.get("trades") or []) if t.get("type") == "sell"][:limit]


def _split_outcomes(trades: list[dict[str, Any]]) -> tuple[list[dict], list[dict], list[dict]]:
    wins: list[dict] = []
    losses: list[dict] = []
    flat: list[dict] = []
    for t in trades:
        net = _net_eur(t)
        if net > WIN_EPS:
            wins.append({**t, "_net": net})
        elif net < -WIN_EPS:
            losses.append({**t, "_net": net})
        else:
            flat.append({**t, "_net": net})
    return wins, losses, flat


def _category_stats(trades: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    agg: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"wins": 0, "losses": 0, "net": 0.0, "profit_sum": 0.0, "loss_sum": 0.0}
    )
    for t in trades:
        cat = _category(t.get("reason", ""))
        net = float(t.get("_net", _net_eur(t)))
        b = agg[cat]
        b["net"] += net
        if net > WIN_EPS:
            b["wins"] += 1
            b["profit_sum"] += net
        elif net < -WIN_EPS:
            b["losses"] += 1
            b["loss_sum"] += net
    out: dict[str, dict[str, Any]] = {}
    for cat, b in agg.items():
        total = b["wins"] + b["losses"]
        if total == 0:
            continue
        out[cat] = {
            "wins": b["wins"],
            "losses": b["losses"],
            "net_eur": round(b["net"], 2),
            "win_rate": round(b["wins"] / total, 2),
            "avg_win_eur": round(b["profit_sum"] / b["wins"], 2) if b["wins"] else None,
            "avg_loss_eur": round(b["loss_sum"] / b["losses"], 2) if b["losses"] else None,
        }
    return out


def _avg(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def _recommendations(
    wins: list[dict],
    losses: list[dict],
    by_cat: dict[str, dict[str, Any]],
    learning: dict[str, Any],
) -> list[str]:
    recs: list[str] = []
    total = len(wins) + len(losses)
    if total < MIN_SAMPLES:
        recs.append("Kerätään lisää myyntidataa ennen vahvoja suosituksia")
        return recs

    win_rate = len(wins) / total if total else 0
    if win_rate < 0.45 and len(losses) > len(wins):
        recs.append(
            f"Voitto-myyntien osuus {win_rate * 100:.0f} % — keskity vähentämään "
            f"tappiollisia pakko-/rotaatiomyyntejä"
        )

    loss_cats = sorted(
        ((cat, st) for cat, st in by_cat.items() if st["losses"] > 0),
        key=lambda x: x[1]["net_eur"],
    )
    for cat, st in loss_cats[:3]:
        if st["losses"] >= 2 and st["net_eur"] < -0.5:
            label = {
                "rotation": "Rotaatio/tasapainotus",
                "time_stop": "Aikastoppi",
                "gemini_sell": "Gemini-myynnit",
                "profit_take": "Voitto-otto",
                "stop_loss": "Stop-loss",
                "other": "Muut myynnit",
            }.get(cat, cat)
            recs.append(
                f"{label}: {st['losses']} tappiota, netto {st['net_eur']:+.2f} € — "
                f"hillitse tätä myyntityyppiä tappiossa"
            )

    win_cats = sorted(
        ((cat, st) for cat, st in by_cat.items() if st["wins"] >= 2),
        key=lambda x: x[1]["net_eur"],
        reverse=True,
    )
    for cat, st in win_cats[:2]:
        avg = st.get("avg_win_eur")
        if avg and avg > 0.3:
            label = {
                "profit_take": "Voitto-otto",
                "gemini_sell": "Gemini-myynnit",
                "rotation": "Rotaatio",
            }.get(cat, cat)
            recs.append(
                f"{label} tuottaa keskimäärin {avg:+.2f} €/voitto — "
                f"suosi tätä polkua kun positio on plussalla"
            )

    givebacks = [float(t["givebackPct"]) for t in wins if t.get("givebackPct") is not None]
    profits = [float(t["profitPctAtSell"]) for t in wins if t.get("profitPctAtSell") is not None]
    avg_gb = _avg(givebacks)
    avg_profit = _avg(profits)
    if avg_gb is not None and avg_gb > 1.2:
        recs.append(
            f"Voitoissa annettiin keskimäärin {avg_gb:.1f} % takaisin huipusta — "
            f"tiukempi trailing voi parantaa nettotuottoa"
        )
    elif avg_profit is not None and avg_profit < 1.5 and len(wins) >= 4:
        recs.append(
            f"Voittojen keskikoko {avg_profit:.1f} % on pieni — "
            f"harkitse pidempää pitoa vahvoissa trendeissä (bull/regiimi)"
        )

    if not learning.get("rotation_enabled"):
        recs.append("Rotaatio on jo hillitty oppimisen perusteella — jatka seurantaa")
    min_conf = learning.get("gemini_sell_min_confidence")
    if min_conf and int(min_conf) > 5:
        recs.append(f"Gemini-myynnit vaativat vähintään conf {min_conf}/10 — säilytä korkea kynnys")

    if not recs:
        recs.append("Myyntijakauma tasapainoinen — jatka nykyistä linjaa ja kerää lisää näytteitä")
    return recs[:6]


def build_gemini_context(
    portfolio: dict[str, Any],
    learning: dict[str, Any] | None = None,
    bot_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Konteksti Geminin oppimiskertomukseen — voitto- vs tappiomyynnit."""
    learning = learning or {}
    trades = _sell_trades(portfolio)
    wins, losses, flat = _split_outcomes(trades)
    by_cat = _category_stats([*wins, *losses])

    win_profits = [float(t["profitPctAtSell"]) for t in wins if t.get("profitPctAtSell") is not None]
    loss_profits = [float(t["profitPctAtSell"]) for t in losses if t.get("profitPctAtSell") is not None]
    win_givebacks = [float(t["givebackPct"]) for t in wins if t.get("givebackPct") is not None]

    win_examples = [
        {
            "symbol": get_crypto_label(t.get("symbol", "")),
            "net_eur": round(float(t["_net"]), 2),
            "reason": (t.get("reason") or "")[:120],
            "category": _category(t.get("reason", "")),
            "profit_pct": t.get("profitPctAtSell"),
            "giveback_pct": t.get("givebackPct"),
        }
        for t in sorted(wins, key=lambda x: float(x["_net"]), reverse=True)[:4]
    ]
    loss_examples = [
        {
            "symbol": get_crypto_label(t.get("symbol", "")),
            "net_eur": round(float(t["_net"]), 2),
            "reason": (t.get("reason") or "")[:120],
            "category": _category(t.get("reason", "")),
            "profit_pct": t.get("profitPctAtSell"),
        }
        for t in sorted(losses, key=lambda x: float(x["_net"]))[:4]
    ]

    stats = learning.get("stats") or {}
    tuning_in_use = {
        "rotation_enabled": learning.get("rotation_enabled"),
        "rotation_scale": learning.get("rotation_scale"),
        "gemini_sell_min_confidence": learning.get("gemini_sell_min_confidence"),
        "profit_take_tuning": learning.get("profit_take_tuning"),
        "stop_tuning": learning.get("stop_tuning"),
    }

    return {
        "enabled": True,
        "window": LEARNING_WINDOW,
        "totalSells": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "flat": len(flat),
        "netEur": round(sum(float(t["_net"]) for t in wins + losses), 2),
        "winRate": round(len(wins) / (len(wins) + len(losses)), 2) if wins or losses else None,
        "avgProfitPctAtWin": _avg(win_profits),
        "avgProfitPctAtLoss": _avg(loss_profits),
        "avgGivebackPctOnWins": _avg(win_givebacks),
        "byCategory": by_cat,
        "learningStats": stats,
        "tuningInUse": tuning_in_use,
        "topWinExamples": win_examples,
        "topLossExamples": loss_examples,
        "recommendations": _recommendations(wins, losses, by_cat, learning),
    }


def learning_report_lines(context: dict[str, Any]) -> list[str]:
    """Rule-pohjaiset rivit oppimisraportin korttiin."""
    if not context.get("enabled"):
        return []

    lines: list[str] = []
    wins = int(context.get("wins") or 0)
    losses = int(context.get("losses") or 0)
    total = int(context.get("totalSells") or 0)
    if total == 0:
        lines.append("Ei vielä myyntejä oppimiseen")
        return lines

    wr = context.get("winRate")
    wr_txt = f" · win rate {wr * 100:.0f} %" if wr is not None else ""
    lines.append(
        f"Viime {context.get('window', LEARNING_WINDOW)} myyntiä: "
        f"{wins}V / {losses}T · netto {context.get('netEur', 0):+.2f} €{wr_txt}"
    )

    by_cat = context.get("byCategory") or {}
    win_cats = sorted(
        ((c, s) for c, s in by_cat.items() if s.get("wins", 0) > 0),
        key=lambda x: x[1].get("net_eur", 0),
        reverse=True,
    )
    loss_cats = sorted(
        ((c, s) for c, s in by_cat.items() if s.get("losses", 0) > 0),
        key=lambda x: x[1].get("net_eur", 0),
    )
    labels = {
        "rotation": "Rotaatio",
        "time_stop": "Aikastoppi",
        "gemini_sell": "Gemini",
        "profit_take": "Voitto-otto",
        "stop_loss": "Stop-loss",
        "other": "Muu",
    }
    if win_cats:
        parts = [
            f"{labels.get(c, c)} {s['wins']}V ({s.get('avg_win_eur', 0):+.2f} €)"
            for c, s in win_cats[:3]
        ]
        lines.append("Voitoissa: " + ", ".join(parts))
    if loss_cats:
        parts = [
            f"{labels.get(c, c)} {s['losses']}T ({s.get('avg_loss_eur', 0):+.2f} €)"
            for c, s in loss_cats[:3]
        ]
        lines.append("Tappioissa: " + ", ".join(parts))

    recs = context.get("recommendations") or []
    if recs:
        lines.append(f"Suositus: {recs[0]}")
    return lines
