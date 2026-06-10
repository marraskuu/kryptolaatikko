"""
D: Expectancy-pohjainen itsesäätö — oikea oppimissilmukka.

Mitataan toteutuneiden myyntien nettotuotto kategorioittain JA symboleittain ja
säädetään kaupankäyntiä sen mukaan:
  - tappiollinen rotaatio vähennetään/poistetaan,
  - toistuvasti häviävät kolikot saavat osto-rankingissa miinusta + tappio-cooldownin,
  - voittavat kolikot saavat lievän bonuksen,
  - jos kokonaisodotusarvo on negatiivinen, botti muuttuu valikoivammaksi (vähemmän,
    vain vahvempia sisäänostoja).
Kaikki perustuu omaan kauppahistoriaan, ei ulkoiseen API:in.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# Kuinka monta viimeisintä myyntiä otetaan mukaan oppimiseen
LEARNING_WINDOW = 40
# Vähimmäismäärä kategoriassa ennen kuin säätö aktivoituu
MIN_SAMPLES = 6

# Symbolimuisti
SYMBOL_MEMORY_WINDOW = 60        # montako viimeisintä myyntiä symbolimuistiin
LOSS_COOLDOWN_SEC = 2 * 3600     # älä osta uudelleen 2 h sisällä tappiosta
SYMBOL_MIN_TRADES = 2            # vähintään näin monta tulosta ennen säätöä
SYMBOL_PENALTY_CAP = -4.0        # rankingin maksimirangaistus
SYMBOL_BONUS_CAP = 3.0           # rankingin maksimibonus


def _category(reason: str) -> str:
    r = (reason or "").lower()
    if "stop-loss" in r:
        return "stop_loss"
    if "huipusta" in r or "realisoidaan voitto" in r or "valmis myyntiin" in r:
        return "profit_take"
    if "gemini" in r:
        return "gemini_sell"
    if (
        "siirret" in r
        or "ei valinnoissa" in r
        or "tasapainotus" in r
        or "myydään osa" in r
        or "rotaatio" in r
    ):
        return "rotation"
    return "other"


def _net_eur(trade: dict[str, Any]) -> float:
    profit = float(trade.get("profitLoss") or 0)
    fee = float(trade.get("fee") or 0)
    tax = float(trade.get("tax") or 0)
    return profit - fee - tax


def _parse_time(iso: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def compute_symbol_memory(
    portfolio: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, dict[str, Any]]:
    """Per-symboli: nettotulos, voitot/tappiot, ranking-säätö ja tappio-cooldown."""
    now = now or datetime.now(timezone.utc)
    sells = [
        t for t in portfolio.get("trades", []) if t.get("type") == "sell"
    ][:SYMBOL_MEMORY_WINDOW]

    agg: dict[str, dict[str, Any]] = {}
    for t in sells:
        sym = t.get("symbol")
        if not sym:
            continue
        net = _net_eur(t)
        b = agg.setdefault(
            sym, {"net": 0.0, "wins": 0, "losses": 0, "last_loss_time": None}
        )
        b["net"] += net
        if net > 0.01:
            b["wins"] += 1
        elif net < -0.01:
            b["losses"] += 1
            ts = _parse_time(t.get("timestamp"))
            if ts and (b["last_loss_time"] is None or ts > b["last_loss_time"]):
                b["last_loss_time"] = ts

    memory: dict[str, dict[str, Any]] = {}
    for sym, b in agg.items():
        trades = b["wins"] + b["losses"]
        net = b["net"]
        score_adjust = 0.0
        if trades >= SYMBOL_MIN_TRADES:
            if net < -2 and b["losses"] >= 2:
                score_adjust = max(SYMBOL_PENALTY_CAP, -2.0 + net / 10.0)
            elif net > 2 and b["wins"] >= 2:
                score_adjust = min(SYMBOL_BONUS_CAP, 1.0 + net / 10.0)

        blocked = False
        cooldown_min = 0
        last_loss = b["last_loss_time"]
        if last_loss is not None:
            elapsed = (now - last_loss).total_seconds()
            if elapsed < LOSS_COOLDOWN_SEC:
                blocked = True
                cooldown_min = int((LOSS_COOLDOWN_SEC - elapsed) / 60)

        memory[sym] = {
            "net_eur": round(net, 2),
            "wins": b["wins"],
            "losses": b["losses"],
            "score_adjust": round(score_adjust, 2),
            "blocked": blocked,
            "cooldown_min": cooldown_min,
        }
    return memory


def compute_tuning(portfolio: dict[str, Any]) -> dict[str, Any]:
    """Palauttaa säätöparametrit ja tilastot oppimista varten."""
    sells = [t for t in portfolio.get("trades", []) if t.get("type") == "sell"][:LEARNING_WINDOW]

    cats: dict[str, dict[str, float]] = {}
    for t in sells:
        cat = _category(t.get("reason", ""))
        bucket = cats.setdefault(cat, {"n": 0, "net": 0.0, "wins": 0})
        net = _net_eur(t)
        bucket["n"] += 1
        bucket["net"] += net
        if net > 0.01:
            bucket["wins"] += 1

    stats: dict[str, dict[str, float]] = {}
    for cat, b in cats.items():
        n = int(b["n"])
        stats[cat] = {
            "trades": n,
            "net_eur": round(b["net"], 2),
            "expectancy_eur": round(b["net"] / n, 3) if n else 0.0,
            "win_rate": round(b["wins"] / n, 2) if n else 0.0,
        }

    rot = stats.get("rotation", {})
    rot_n = int(rot.get("trades", 0))
    rot_exp = float(rot.get("expectancy_eur", 0.0))

    rotation_enabled = True
    rotation_scale = 1.0
    notes: list[str] = []

    if rot_n >= MIN_SAMPLES:
        if rot_exp < -0.15:
            rotation_enabled = False
            notes.append(f"rotaatio pois — tappiollinen ({rot_exp:+.2f} €/kauppa)")
        elif rot_exp < 0:
            rotation_scale = 0.5
            notes.append(f"rotaatiota -50 % — heikko ({rot_exp:+.2f} €/kauppa)")
        elif rot_exp > 0.2:
            notes.append(f"rotaatio toimii ({rot_exp:+.2f} €/kauppa)")

    # Globaali valikoivuus: jos oma kokonaisodotusarvo on negatiivinen, ole tarkempi
    total_n = len(sells)
    total_net = sum(_net_eur(t) for t in sells)
    overall_exp = total_net / total_n if total_n else 0.0
    entry_score_min = 1
    max_new_positions = 4
    if total_n >= MIN_SAMPLES:
        if overall_exp < -0.25:
            entry_score_min = 4
            max_new_positions = 2
            notes.append(f"valikoivampi — kokonaisodotus {overall_exp:+.2f} €/kauppa")
        elif overall_exp < 0:
            entry_score_min = 3
            max_new_positions = 3
            notes.append(f"hieman tarkempi — odotus {overall_exp:+.2f} €/kauppa")
        elif overall_exp > 0.3:
            notes.append(f"linja toimii — odotus {overall_exp:+.2f} €/kauppa")

    memory = compute_symbol_memory(portfolio)
    blocked = [s for s, m in memory.items() if m["blocked"]]
    losers = [s for s, m in memory.items() if m["score_adjust"] < 0]
    winners = [s for s, m in memory.items() if m["score_adjust"] > 0]
    if losers:
        notes.append(f"välttää {len(losers)} häviäjää")
    if blocked:
        notes.append(f"{len(blocked)} cooldownissa")
    if winners:
        notes.append(f"suosii {len(winners)} voittajaa")

    note = " · ".join(notes) if notes else "oppiminen kerää dataa"

    return {
        "rotation_enabled": rotation_enabled,
        "rotation_scale": rotation_scale,
        "entry_score_min": entry_score_min,
        "max_new_positions": max_new_positions,
        "overall_expectancy_eur": round(overall_exp, 3),
        "symbol_memory": memory,
        "blocked_buys": blocked,
        "note": note,
        "stats": stats,
        "samples": total_n,
    }
