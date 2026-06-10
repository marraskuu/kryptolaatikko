"""
D: Expectancy-pohjainen itsesäätö — oikea oppimissilmukka.

Mitataan toteutuneiden myyntien nettotuotto kategorioittain ja säädetään
kaupankäyntiä sen mukaan (esim. vähennetään tappiollista rotaatiota).
Kaikki perustuu omaan kauppahistoriaan, ei ulkoiseen API:in.
"""

from __future__ import annotations

from typing import Any

# Kuinka monta viimeisintä myyntiä otetaan mukaan oppimiseen
LEARNING_WINDOW = 40
# Vähimmäismäärä kategoriassa ennen kuin säätö aktivoituu
MIN_SAMPLES = 6


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
    note = "rotaatio normaali"

    if rot_n >= MIN_SAMPLES:
        if rot_exp < -0.15:
            rotation_enabled = False
            note = f"rotaatio pois päältä — tappiollinen (odotus {rot_exp:+.2f} €/kauppa)"
        elif rot_exp < 0:
            rotation_scale = 0.5
            note = f"rotaatiota vähennetty 50 % — heikko odotus ({rot_exp:+.2f} €/kauppa)"
        elif rot_exp > 0.2:
            rotation_scale = 1.0
            note = f"rotaatio toimii ({rot_exp:+.2f} €/kauppa)"

    return {
        "rotation_enabled": rotation_enabled,
        "rotation_scale": rotation_scale,
        "note": note,
        "stats": stats,
        "samples": len(sells),
    }
