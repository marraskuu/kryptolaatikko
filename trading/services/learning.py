"""
D: Expectancy-pohjainen itsesäätö — oikea oppimissilmukka.

Mitataan toteutuneiden myyntien nettotuotto kategorioittain, regiimeittäin,
symboleittain ja sisäänostoasetelmittain (FIFO) ja säädetään kaupankäyntiä:
  - tappiollinen rotaatio / Gemini-myynti vähennetään tai poistetaan,
  - regiimikohtainen viritys kun tagattuja myyntejä riittää,
  - oma setup-oppiminen sisäänostoista (score, RSI, MTF, asetelma),
  - symbolimuisti, cooldownit ja valikoivuus kuten ennen.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from .trade_meta import entry_meta_from_trade

# Kuinka monta viimeisintä myyntiä otetaan mukaan oppimiseen
LEARNING_WINDOW = 40
# Vähimmäismäärä kategoriassa ennen kuin säätö aktivoituu
MIN_SAMPLES = 6
MIN_SAMPLES_REGIME = 4   # regiimikohtainen (vähemmän otosta per regiimi)
MIN_SAMPLES_SETUP = 4    # oma sisäänostoasetelma
SETUP_PENALTY_CAP = -3.0
SETUP_BONUS_CAP = 2.0

# Symbolimuisti
SYMBOL_MEMORY_WINDOW = 60        # montako viimeisintä myyntiä symbolimuistiin
LOSS_COOLDOWN_SEC = 2 * 3600     # älä osta uudelleen 2 h sisällä tappiosta
SYMBOL_MIN_TRADES = 2            # vähintään näin monta tulosta ennen säätöä
SYMBOL_PENALTY_CAP = -4.0        # rankingin maksimirangaistus
SYMBOL_BONUS_CAP = 3.0           # rankingin maksimibonus
CHRONIC_LOSER_LOSSES = 3         # 0 voittoa & näin monta tappiota → estä osto kokonaan


def _category(reason: str) -> str:
    r = (reason or "").lower()
    if "stop-loss" in r:
        return "stop_loss"
    if "aikastoppi" in r:
        return "time_stop"
    if "huipusta" in r or "realisoidaan voitto" in r or "valmis myyntiin" in r or "kotiut" in r:
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
    # Veroa ei vähennetä salkusta (käyttäjä maksaa sen itse), joten oppiminen
    # mittaa salkun todellista (veroa edeltävää) tuottoa. Kulut ovat 0.
    profit = float(trade.get("profitLoss") or 0)
    fee = float(trade.get("fee") or 0)
    return profit - fee


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

        # B: krooninen häviäjä — ei yhtään voittoa ja vähintään 3 tappiota.
        # Estetään osto kokonaan ja annetaan täysi ranking-rangaistus, riippumatta
        # nettotappion suuruudesta (pienetkin toistuvat tappiot kielivät huonosta
        # kohteesta). Esto purkautuu luonnostaan kun tappiot vanhenevat ikkunasta.
        chronic_loser = b["wins"] == 0 and b["losses"] >= CHRONIC_LOSER_LOSSES
        if chronic_loser:
            blocked = True
            score_adjust = SYMBOL_PENALTY_CAP

        memory[sym] = {
            "net_eur": round(net, 2),
            "wins": b["wins"],
            "losses": b["losses"],
            "score_adjust": round(score_adjust, 2),
            "blocked": blocked,
            "cooldown_min": cooldown_min,
            "chronic": chronic_loser,
        }
    return memory


def _sells_with_entry_context(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """FIFO: liitä myyntiin sisäänoston meta (setup, score, regiimi ostosta)."""
    chronological = sorted(
        [t for t in trades if t.get("type") in ("buy", "sell") and t.get("symbol")],
        key=lambda t: t.get("timestamp", ""),
    )
    lots: dict[str, list[dict[str, Any]]] = defaultdict(list)
    linked: list[dict[str, Any]] = []

    for trade in chronological:
        sym = trade["symbol"]
        if trade["type"] == "buy":
            lots[sym].append(
                {
                    "amount": float(trade.get("amount") or 0),
                    "meta": entry_meta_from_trade(trade),
                }
            )
            continue

        sell_amount = float(trade.get("amount") or 0)
        entry_meta: dict[str, Any] = {}
        while sell_amount > 1e-12 and lots[sym]:
            lot = lots[sym][0]
            take = min(sell_amount, lot["amount"])
            if lot["meta"] and not entry_meta:
                entry_meta = dict(lot["meta"])
            lot["amount"] -= take
            sell_amount -= take
            if lot["amount"] <= 1e-12:
                lots[sym].pop(0)

        linked.append(
            {
                "sell": trade,
                "entry_setup": entry_meta.get("setup"),
                "entry_regime": entry_meta.get("regime"),
                "entry_score": entry_meta.get("score"),
                "entry_mtf": entry_meta.get("mtfAlign"),
            }
        )
    return linked


def _stat_block(n: int, net: float, wins: int) -> dict[str, float]:
    return {
        "trades": n,
        "net_eur": round(net, 2),
        "expectancy_eur": round(net / n, 3) if n else 0.0,
        "win_rate": round(wins / n, 2) if n else 0.0,
    }


def _aggregate_category_stats(sells: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    cats: dict[str, dict[str, float]] = {}
    for t in sells:
        cat = _category(t.get("reason", ""))
        bucket = cats.setdefault(cat, {"n": 0, "net": 0.0, "wins": 0})
        net = _net_eur(t)
        bucket["n"] += 1
        bucket["net"] += net
        if net > 0.01:
            bucket["wins"] += 1
    return {
        cat: _stat_block(int(b["n"]), b["net"], int(b["wins"]))
        for cat, b in cats.items()
    }


def _apply_category_tuning(
    stats: dict[str, dict[str, float]],
    *,
    min_samples: int,
) -> tuple[dict[str, Any], list[str]]:
    """Sama rotaatio/Gemini/valikoivuus-logiikka annetulla tilastolla."""
    notes: list[str] = []
    rotation_enabled = True
    rotation_scale = 1.0
    gemini_sell_min_confidence = 0
    gemini_sell_scale = 1.0
    entry_score_min = 1
    max_new_positions = 4

    rot = stats.get("rotation", {})
    rot_n = int(rot.get("trades", 0))
    rot_exp = float(rot.get("expectancy_eur", 0.0))
    if rot_n >= min_samples:
        if rot_exp < -0.15:
            rotation_enabled = False
            notes.append(f"rotaatio pois ({rot_exp:+.2f} €/kauppa)")
        elif rot_exp < 0:
            rotation_scale = 0.5
            notes.append(f"rotaatio -50 % ({rot_exp:+.2f} €/kauppa)")
        elif rot_exp > 0.2:
            notes.append(f"rotaatio ok ({rot_exp:+.2f} €/kauppa)")

    gem = stats.get("gemini_sell", {})
    gem_n = int(gem.get("trades", 0))
    gem_exp = float(gem.get("expectancy_eur", 0.0))
    if gem_n >= min_samples:
        if gem_exp < -0.15:
            gemini_sell_min_confidence = 8
            gemini_sell_scale = 0.5
            notes.append(f"Gemini tiukemmin ({gem_exp:+.2f} €/kauppa)")
        elif gem_exp < 0:
            gemini_sell_min_confidence = 7
            gemini_sell_scale = 0.7
            notes.append(f"Gemini varovaisemmin ({gem_exp:+.2f} €/kauppa)")
        elif gem_exp > 0.2:
            notes.append(f"Gemini ok ({gem_exp:+.2f} €/kauppa)")

    total_n = sum(int(s.get("trades", 0)) for s in stats.values())
    total_net = sum(float(s.get("net_eur", 0.0)) for s in stats.values())
    overall_exp = total_net / total_n if total_n else 0.0
    if total_n >= min_samples:
        if overall_exp < -0.25:
            entry_score_min = 4
            max_new_positions = 2
            notes.append(f"valikoivampi ({overall_exp:+.2f} €/kauppa)")
        elif overall_exp < 0:
            entry_score_min = 3
            max_new_positions = 3
            notes.append(f"tarkempi ({overall_exp:+.2f} €/kauppa)")
        elif overall_exp > 0.3:
            notes.append(f"linja ok ({overall_exp:+.2f} €/kauppa)")

    return {
        "rotation_enabled": rotation_enabled,
        "rotation_scale": rotation_scale,
        "gemini_sell_min_confidence": gemini_sell_min_confidence,
        "gemini_sell_scale": gemini_sell_scale,
        "entry_score_min": entry_score_min,
        "max_new_positions": max_new_positions,
        "overall_expectancy_eur": round(overall_exp, 3),
        "samples": total_n,
    }, notes


def _compute_regime_tuning(
    sells: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, dict[str, float]]]]:
    """Regiimikohtainen säätö myyntien exit-regiimin mukaan."""
    by_regime: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in sells:
        reg = t.get("regime")
        if reg in ("bull", "neutral", "bear"):
            by_regime[reg].append(t)

    tuning: dict[str, dict[str, Any]] = {}
    regime_stats: dict[str, dict[str, dict[str, float]]] = {}
    for reg in ("bull", "neutral", "bear"):
        reg_sells = by_regime.get(reg, [])
        if not reg_sells:
            continue
        stats = _aggregate_category_stats(reg_sells)
        regime_stats[reg] = stats
        params, _ = _apply_category_tuning(stats, min_samples=MIN_SAMPLES_REGIME)
        overrides: dict[str, Any] = {}

        rot_n = int(stats.get("rotation", {}).get("trades", 0))
        if rot_n >= MIN_SAMPLES_REGIME:
            if not params["rotation_enabled"]:
                overrides["rotation_enabled"] = False
            elif params["rotation_scale"] != 1.0:
                overrides["rotation_scale"] = params["rotation_scale"]

        gem_n = int(stats.get("gemini_sell", {}).get("trades", 0))
        if gem_n >= MIN_SAMPLES_REGIME:
            if params["gemini_sell_min_confidence"]:
                overrides["gemini_sell_min_confidence"] = params["gemini_sell_min_confidence"]
            if params["gemini_sell_scale"] != 1.0:
                overrides["gemini_sell_scale"] = params["gemini_sell_scale"]

        if params["samples"] >= MIN_SAMPLES_REGIME:
            if params["entry_score_min"] != 1:
                overrides["entry_score_min"] = params["entry_score_min"]
            if params["max_new_positions"] != 4:
                overrides["max_new_positions"] = params["max_new_positions"]

        if overrides:
            tuning[reg] = overrides
    return tuning, regime_stats


def _compute_setup_memory(
    linked: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Oma kauppahistoria: mitkä sisäänostoasetelmat tuottavat."""
    agg: dict[str, dict[str, float]] = defaultdict(lambda: {"n": 0, "net": 0.0, "wins": 0})
    for item in linked:
        setup = item.get("entry_setup")
        if not setup:
            continue
        sell = item["sell"]
        net = _net_eur(sell)
        b = agg[setup]
        b["n"] += 1
        b["net"] += net
        if net > 0.01:
            b["wins"] += 1

    memory: dict[str, dict[str, Any]] = {}
    for setup, b in agg.items():
        n = int(b["n"])
        if n < MIN_SAMPLES_SETUP:
            continue
        net = b["net"]
        exp = net / n
        adjust = 0.0
        if exp < -0.2:
            adjust = max(SETUP_PENALTY_CAP, -1.0 + exp)
        elif exp > 0.3:
            adjust = min(SETUP_BONUS_CAP, 0.5 + exp)
        memory[setup] = {
            "trades": n,
            "net_eur": round(net, 2),
            "expectancy_eur": round(exp, 3),
            "win_rate": round(b["wins"] / n, 2),
            "score_adjust": round(adjust, 2),
        }
    return memory


def merge_regime_tuning(learning: dict[str, Any], regime: str) -> dict[str, Any]:
    """Yhdistä globaali oppiminen + aktiivisen regiimin ylikirjoitus."""
    merged = dict(learning)
    overrides = (learning.get("regime_tuning") or {}).get(regime) or {}
    if overrides:
        merged.update(overrides)
    merged["active_regime"] = regime
    return merged


def compute_tuning(portfolio: dict[str, Any]) -> dict[str, Any]:
    """Palauttaa säätöparametrit ja tilastot oppimista varten."""
    all_trades = portfolio.get("trades", [])
    sells = [t for t in all_trades if t.get("type") == "sell"][:LEARNING_WINDOW]

    stats = _aggregate_category_stats(sells)
    global_params, tune_notes = _apply_category_tuning(stats, min_samples=MIN_SAMPLES)

    rotation_enabled = global_params["rotation_enabled"]
    rotation_scale = global_params["rotation_scale"]
    gemini_sell_min_confidence = global_params["gemini_sell_min_confidence"]
    gemini_sell_scale = global_params["gemini_sell_scale"]
    entry_score_min = global_params["entry_score_min"]
    max_new_positions = global_params["max_new_positions"]
    overall_exp = global_params["overall_expectancy_eur"]
    total_n = global_params["samples"]

    linked = _sells_with_entry_context(all_trades)
    setup_memory = _compute_setup_memory(linked)
    regime_tuning, regime_stats = _compute_regime_tuning(sells)

    notes = list(tune_notes)
    tagged = sum(1 for t in sells if t.get("regime") in ("bull", "neutral", "bear"))
    if tagged < MIN_SAMPLES_REGIME:
        notes.append(f"regiimioppiminen {tagged}/{MIN_SAMPLES_REGIME} myyntiä")
    elif regime_tuning:
        active_regs = ", ".join(regime_tuning.keys())
        notes.append(f"regiimisäätö: {active_regs}")

    if setup_memory:
        good = sum(1 for m in setup_memory.values() if m.get("score_adjust", 0) > 0)
        bad = sum(1 for m in setup_memory.values() if m.get("score_adjust", 0) < 0)
        if good or bad:
            notes.append(f"asetelmat: {good} hyvää, {bad} huonoa")

    memory = compute_symbol_memory(portfolio)
    blocked = [s for s, m in memory.items() if m["blocked"]]
    chronic = [s for s, m in memory.items() if m.get("chronic")]
    cooldown = [s for s, m in memory.items() if m["blocked"] and not m.get("chronic")]
    losers = [s for s, m in memory.items() if m["score_adjust"] < 0]
    winners = [s for s, m in memory.items() if m["score_adjust"] > 0]
    if losers:
        notes.append(f"välttää {len(losers)} häviäjää")
    if chronic:
        notes.append(f"{len(chronic)} estetty (toistuva tappio)")
    if cooldown:
        notes.append(f"{len(cooldown)} cooldownissa")
    if winners:
        notes.append(f"suosii {len(winners)} voittajaa")

    note = " · ".join(notes) if notes else "oppiminen kerää dataa"

    return {
        "rotation_enabled": rotation_enabled,
        "rotation_scale": rotation_scale,
        "gemini_sell_min_confidence": gemini_sell_min_confidence,
        "gemini_sell_scale": gemini_sell_scale,
        "entry_score_min": entry_score_min,
        "max_new_positions": max_new_positions,
        "overall_expectancy_eur": round(overall_exp, 3),
        "symbol_memory": memory,
        "blocked_buys": blocked,
        "note": note,
        "stats": stats,
        "samples": total_n,
        "regime_tuning": regime_tuning,
        "regime_stats": regime_stats,
        "setup_memory": setup_memory,
        "regime_tagged_sells": tagged,
    }
