"""
D: Expectancy-pohjainen itsesäätö — oikea oppimissilmukka.

Mitataan toteutuneiden myyntien nettotuotto kategorioittain, regiimeittäin,
symboleittain ja sisäänostoasetelmittain (FIFO) ja säädetään kaupankäyntiä:
  - tappiollinen rotaatio / Gemini-myynti vähennetään tai poistetaan,
  - regiimikohtainen viritys kun tagattuja myyntejä riittää,
  - oma setup-oppiminen sisäänostoista (score, RSI, MTF, asetelma),
  - symbolimuisti, cooldownit ja valikoivuus kuten ennen.
  - Gemini-confidence (5–10): estä tai hillitse tappiollisia luottamustasoja.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from .ai_trader import MAX_POSITIONS
from .bitfinex import normalize_symbol
from .trade_meta import entry_meta_from_trade

_GEMINI_CONF_RE = re.compile(r"Gemini\s*\((\d+)/10\)", re.I)

# Kuinka monta viimeisintä myyntiä otetaan mukaan oppimiseen
LEARNING_WINDOW = 40
# Vähimmäismäärä kategoriassa ennen kuin säätö aktivoituu
MIN_SAMPLES = 6
MIN_SAMPLES_REGIME = 4   # regiimikohtainen (vähemmän otosta per regiimi)
MIN_SAMPLES_SETUP = 4    # oma sisäänostoasetelma
SETUP_BLOCK_EXP = -0.2   # alle tämän €/kauppa → estä uudet ostot asetelmalla
SETUP_PENALTY_CAP = -3.0
SETUP_BONUS_CAP = 2.0
SETUP_HIST_WEIGHT = 0.3  # historiallinen backtest painotus vs live-kaupat

# Symbolimuisti
SYMBOL_MEMORY_WINDOW = 60        # montako viimeisintä myyntiä symbolimuistiin
LOSS_COOLDOWN_SEC = 2 * 3600     # älä osta uudelleen 2 h sisällä tappiosta
SYMBOL_MIN_TRADES = 2            # vähintään näin monta tulosta ennen säätöä
SYMBOL_PENALTY_CAP = -4.0        # rankingin maksimirangaistus
SYMBOL_BONUS_CAP = 3.0           # rankingin maksimibonus
SYMBOL_SCORE_BLOCK = -2.0        # score_adjust ≤ tämä → estä uudet ostot
CHRONIC_LOSER_LOSSES = 3         # 0 voittoa & näin monta tappiota → estä osto kokonaan
MIN_SAMPLES_GEMINI_CONF = 2      # per confidence-taso (5–10)
MIN_GEMINI_CONF_TAGGED = 6         # tagattuja Gemini-myyntejä ennen conf-oppimista
MIN_SAMPLES_PROFIT_TAKE_LIGHT = 6  # kevyt voitto-otto-viritys
MIN_SAMPLES_PROFIT_TAKE_FULL = 15  # täysi ATR/regiimi-viritys
MIN_SAMPLES_MICRO_PROFIT_TAKE = 3  # book/crowd-bucket voitto-otto
MIN_SAMPLES_STOP_LIGHT = 6         # kevyt stop-loss-viritys
MIN_SAMPLES_STOP_FULL = 15         # täysi stop-loss-viritys
BUY_SCALE_MIN = 0.5                # pienin ostokerroin tappioputkessa
BUY_SCALE_MAX = 1.0                # ei yli 100 % — vain hillitään huonoilla jaksoilla


def _category(reason: str) -> str:
    r = (reason or "").lower()
    if "stop-loss" in r:
        return "stop_loss"
    if "aikastoppi" in r or "positio jämähtänyt" in r:
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
                "entry_gemini_confidence": entry_meta.get("geminiConfidence"),
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
    max_new_positions = MAX_POSITIONS

    rot = stats.get("rotation", {})
    rot_n = int(rot.get("trades", 0))
    rot_exp = float(rot.get("expectancy_eur", 0.0))
    if rot_n >= min_samples:
        if rot_exp < 0:
            rotation_enabled = False
            notes.append(f"rotaatio pois ({rot_exp:+.2f} €/kauppa)")
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
            entry_score_min = max(entry_score_min, 2)
            max_new_positions = min(max_new_positions, 3)
            notes.append(f"tarkempi ({overall_exp:+.2f} €/kauppa)")
        elif overall_exp > 0.3:
            notes.append(f"linja ok ({overall_exp:+.2f} €/kauppa)")

    wins = 0
    wr_n = 0
    for block in stats.values():
        tn = int(block.get("trades", 0))
        wr_n += tn
        wins += int(round(tn * float(block.get("win_rate", 0))))
    win_rate = wins / wr_n if wr_n else 0.5
    if wr_n >= min_samples and win_rate < 0.40:
        entry_score_min = max(entry_score_min, 2)
        notes.append(f"valikoivampi win rate {win_rate * 100:.0f} %")
        if win_rate < 0.35:
            entry_score_min = max(entry_score_min, 3)

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


def _aggregate_win_rate(stats: dict[str, dict[str, float]]) -> tuple[int, float]:
    wins = 0
    n = 0
    for block in stats.values():
        tn = int(block.get("trades", 0))
        n += tn
        wins += int(round(tn * float(block.get("win_rate", 0))))
    return n, (wins / n if n else 0.5)


def _compute_buy_scale(
    stats: dict[str, dict[str, float]],
    overall_exp: float,
    total_n: int,
) -> tuple[float, str | None]:
    """Pienentää ostokokoja vain kun expectancy on negatiivinen (ei pidä käteistä hyvällä jaksolla)."""
    if total_n < MIN_SAMPLES:
        return 1.0, None

    n, win_rate = _aggregate_win_rate(stats)
    scale = 1.0
    if overall_exp < -0.25:
        scale = 0.55
    elif overall_exp < -0.12:
        scale = 0.70
    elif overall_exp < 0:
        scale = 0.85

    if overall_exp < 0 and n >= MIN_SAMPLES and win_rate < 0.38:
        scale *= 0.88

    scale = max(BUY_SCALE_MIN, min(BUY_SCALE_MAX, round(scale, 2)))
    if scale >= 0.99:
        return scale, None
    return scale, (
        f"ostokoot {scale * 100:.0f} % "
        f"(exp {overall_exp:+.2f} €/kauppa, win {win_rate * 100:.0f} %)"
    )


def _compute_stop_tuning(
    stats: dict[str, dict[str, float]],
) -> tuple[dict[str, Any], list[str]]:
    """Expectancy-pohjainen stop-loss: levennä/tiukenna ATR-rajoja datan perusteella."""
    sl = stats.get("stop_loss", {})
    n = int(sl.get("trades", 0))
    exp = float(sl.get("expectancy_eur", 0.0))
    config: dict[str, Any] = {
        "atr_scale": 1.0,
        "floor_scale": 1.0,
        "cap_scale": 1.0,
        "samples": n,
        "expectancy_eur": round(exp, 3),
        "level": "off",
    }
    notes: list[str] = []
    if n < MIN_SAMPLES_STOP_LIGHT:
        return config, notes

    config["level"] = "light"
    if exp > -0.35:
        config["atr_scale"] = 1.1
        config["floor_scale"] = 1.08
        notes.append(f"stop-loss löysempi ({exp:+.2f} €/kauppa)")
    elif exp < -1.0:
        config["atr_scale"] = 0.88
        config["floor_scale"] = 0.92
        config["cap_scale"] = 0.92
        notes.append(f"stop-loss tiukempi ({exp:+.2f} €/kauppa)")

    if n >= MIN_SAMPLES_STOP_FULL:
        config["level"] = "full"
        if exp > -0.25:
            config["atr_scale"] = max(float(config["atr_scale"]), 1.15)
            config["floor_scale"] = max(float(config["floor_scale"]), 1.12)
        elif exp < -1.25:
            config["atr_scale"] = min(float(config["atr_scale"]), 0.82)
            config["floor_scale"] = min(float(config["floor_scale"]), 0.88)
            config["cap_scale"] = min(float(config["cap_scale"]), 0.88)

    return config, notes


def _micro_profit_take_adjustments(
    trades: list[dict[str, Any]],
) -> tuple[dict[str, float], list[str]]:
    """Suljettujen voitto-ottotransaktioiden book/crowd-bucket → trailing-säätö."""
    from .market_microstructure import _linked_micro_outcomes

    linked = [
        item
        for item in _linked_micro_outcomes(trades)
        if _category(item.get("reason", "")) == "profit_take"
    ]
    if len(linked) < MIN_SAMPLES_MICRO_PROFIT_TAKE:
        return {"trigger_scale": 1.0, "pullback_scale": 1.0}, []

    by_book: dict[str, list[float]] = defaultdict(list)
    by_crowd: dict[str, list[float]] = defaultdict(list)
    for item in linked:
        entry = item.get("entry") or {}
        net = float(item.get("net_eur") or 0)
        bk = str(entry.get("bookBucket") or "bk0")
        cr = str(entry.get("crowdBucket") or "cr0")
        if bk != "bk0":
            by_book[bk].append(net)
        if cr != "cr0":
            by_crowd[cr].append(net)

    adj = {"trigger_scale": 1.0, "pullback_scale": 1.0}
    notes: list[str] = []

    def _apply_bucket(label: str, nets: list[float]) -> None:
        nonlocal adj, notes
        if len(nets) < MIN_SAMPLES_MICRO_PROFIT_TAKE:
            return
        exp = sum(nets) / len(nets)
        if exp < -0.1:
            adj["pullback_scale"] = min(adj["pullback_scale"], 0.88)
            adj["trigger_scale"] = min(adj["trigger_scale"], 0.92)
            notes.append(f"micro {label} voitto-otto {exp:+.2f} €/kauppa → tiukempi")
        elif exp > 0.2:
            adj["pullback_scale"] = max(adj["pullback_scale"], 1.08)
            notes.append(f"micro {label} voitto-otto {exp:+.2f} €/kauppa → löysempi")

    for bk, nets in by_book.items():
        _apply_bucket(bk, nets)
    for cr, nets in by_crowd.items():
        _apply_bucket(cr, nets)

    return adj, notes


def _compute_profit_take_tuning(
    stats: dict[str, dict[str, float]],
    trades: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Expectancy-pohjainen voitto-otto: kevyt ≥6 kauppaa, täysi ≥15."""
    pt = stats.get("profit_take", {})
    n = int(pt.get("trades", 0))
    exp = float(pt.get("expectancy_eur", 0.0))
    config: dict[str, Any] = {
        "trigger_scale": 1.0,
        "pullback_scale": 1.0,
        "partial_trigger_scale": 1.0,
        "partial_fraction_scale": 1.0,
        "partial_enabled": True,
        "samples": n,
        "expectancy_eur": round(exp, 3),
        "level": "off",
    }
    notes: list[str] = []
    if n < MIN_SAMPLES_PROFIT_TAKE_LIGHT:
        return config, notes

    config["level"] = "light"
    if exp < -0.15:
        config["trigger_scale"] = 0.82
        config["pullback_scale"] = 0.85
        notes.append(f"voitto-otto tiukempi ({exp:+.2f} €/kauppa)")
    elif exp < 0:
        config["trigger_scale"] = 0.92
        config["pullback_scale"] = 0.92
        notes.append(f"voitto-otto varovainen ({exp:+.2f} €/kauppa)")
    elif exp > 0.25:
        config["trigger_scale"] = 1.08
        config["pullback_scale"] = 1.12
        notes.append(f"voitto-otto löysempi ({exp:+.2f} €/kauppa)")

    if n >= MIN_SAMPLES_PROFIT_TAKE_FULL:
        config["level"] = "full"
        if exp < -0.1:
            config["trigger_scale"] = min(float(config["trigger_scale"]), 0.75)
            config["pullback_scale"] = min(float(config["pullback_scale"]), 0.78)
            config["partial_trigger_scale"] = 1.15
            if exp < -0.25:
                config["partial_enabled"] = False
            notes.append("voitto-otto täysi: lukitse voitto aiemmin")
        elif exp > 0.3:
            config["trigger_scale"] = max(float(config["trigger_scale"]), 1.12)
            config["pullback_scale"] = max(float(config["pullback_scale"]), 1.18)
            config["partial_trigger_scale"] = 0.9
            config["partial_fraction_scale"] = 0.85
            notes.append("voitto-otto täysi: anna voittojen juosta")

    if trades:
        micro_adj, micro_notes = _micro_profit_take_adjustments(trades)
        if micro_adj["trigger_scale"] != 1.0:
            config["trigger_scale"] = round(
                float(config["trigger_scale"]) * micro_adj["trigger_scale"],
                3,
            )
        if micro_adj["pullback_scale"] != 1.0:
            config["pullback_scale"] = round(
                float(config["pullback_scale"]) * micro_adj["pullback_scale"],
                3,
            )
        notes.extend(micro_notes)

    return config, notes


def _gemini_confidence_from_trade(trade: dict[str, Any]) -> int | None:
    raw = trade.get("geminiConfidence")
    if raw is not None:
        try:
            conf = int(raw)
            if 5 <= conf <= 10:
                return conf
        except (TypeError, ValueError):
            pass
    reason = trade.get("reason") or ""
    match = _GEMINI_CONF_RE.search(reason)
    if match:
        return int(match.group(1))
    return None


def _confidence_scale(scales: dict[Any, float], conf: int) -> float:
    if not scales:
        return 1.0
    if conf in scales:
        return float(scales[conf])
    return float(scales.get(str(conf), 1.0))


def _compute_gemini_confidence_tuning(
    sells: list[dict[str, Any]],
    linked: list[dict[str, Any]],
    *,
    base_min_conf: int,
) -> tuple[dict[str, Any], list[str]]:
    """Oppiminen Gemini-confidence-tasoittain: myynnit + ostosta sulkeutuneet."""
    buckets: dict[int, dict[str, float]] = {}
    tagged_sells = 0
    tagged_buys = 0
    sell_ids_counted: set[int | str] = set()

    for trade in sells:
        if _category(trade.get("reason", "")) != "gemini_sell":
            continue
        conf = _gemini_confidence_from_trade(trade)
        if conf is None:
            continue
        bucket = buckets.setdefault(conf, {"n": 0, "net": 0.0, "wins": 0})
        net = _net_eur(trade)
        bucket["n"] += 1
        bucket["net"] += net
        if net > 0.01:
            bucket["wins"] += 1
        tagged_sells += 1
        if trade.get("id") is not None:
            sell_ids_counted.add(trade["id"])

    for item in linked:
        sell = item.get("sell") or {}
        sell_id = sell.get("id")
        if sell_id is not None and sell_id in sell_ids_counted:
            continue
        if _category(sell.get("reason", "")) == "gemini_sell":
            continue
        conf_raw = item.get("entry_gemini_confidence")
        if conf_raw is None:
            continue
        try:
            conf = int(conf_raw)
        except (TypeError, ValueError):
            continue
        if not 5 <= conf <= 10:
            continue
        bucket = buckets.setdefault(conf, {"n": 0, "net": 0.0, "wins": 0})
        net = _net_eur(sell)
        bucket["n"] += 1
        bucket["net"] += net
        if net > 0.01:
            bucket["wins"] += 1
        tagged_buys += 1

    stats = {
        conf: _stat_block(int(b["n"]), b["net"], int(b["wins"]))
        for conf, b in buckets.items()
    }
    tagged = tagged_sells + tagged_buys

    notes: list[str] = []
    scales: dict[int, float] = {}
    blocked: list[int] = []

    for conf in range(5, 11):
        stat = stats.get(conf)
        if not stat or stat["trades"] < MIN_SAMPLES_GEMINI_CONF:
            continue
        exp = float(stat["expectancy_eur"])
        if exp < -0.15:
            scales[conf] = 0.0
            blocked.append(conf)
        elif exp < 0:
            scales[conf] = 0.5

    boosted_min = base_min_conf
    if blocked:
        boosted_min = max(boosted_min, min(10, max(blocked) + 1))

    if tagged >= MIN_GEMINI_CONF_TAGGED:
        if blocked:
            notes.append(f"Gemini estää conf {','.join(str(c) for c in sorted(blocked))}")
        good = [
            conf
            for conf, stat in stats.items()
            if stat["trades"] >= MIN_SAMPLES_GEMINI_CONF and stat["expectancy_eur"] > 0.2
        ]
        if good:
            best = max(good, key=lambda c: stats[c]["expectancy_eur"])
            notes.append(
                f"Gemini conf {best} ok ({stats[best]['expectancy_eur']:+.2f} €/kauppa)"
            )
        elif boosted_min > base_min_conf:
            notes.append(f"Gemini min conf {boosted_min}")
    elif tagged:
        notes.append(
            f"Gemini-conf {tagged}/{MIN_GEMINI_CONF_TAGGED} "
            f"(ostot {tagged_buys}, myynnit {tagged_sells})"
        )

    return {
        "gemini_confidence_stats": stats,
        "gemini_confidence_scales": scales,
        "gemini_confidence_tagged": tagged,
        "gemini_confidence_tagged_buys": tagged_buys,
        "gemini_confidence_tagged_sells": tagged_sells,
        "gemini_sell_min_confidence": boosted_min,
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
            if params["max_new_positions"] != MAX_POSITIONS:
                overrides["max_new_positions"] = params["max_new_positions"]

        pt_cfg, _ = _compute_profit_take_tuning(stats)
        if pt_cfg.get("level") in ("light", "full"):
            overrides["profit_take_tuning"] = pt_cfg

        stop_cfg, _ = _compute_stop_tuning(stats)
        if stop_cfg.get("level") in ("light", "full"):
            overrides["stop_tuning"] = stop_cfg

        if overrides:
            tuning[reg] = overrides
    return tuning, regime_stats


def _aggregate_setup_from_linked(
    linked: list[dict[str, Any]],
) -> dict[str, dict[str, float]]:
    agg: dict[str, dict[str, float]] = defaultdict(
        lambda: {"n": 0.0, "net": 0.0, "wins": 0.0}
    )
    for item in linked:
        setup = item.get("entry_setup")
        if not setup:
            continue
        sell = item["sell"]
        net = _net_eur(sell)
        b = agg[setup]
        b["n"] += 1.0
        b["net"] += net
        if net > 0.01:
            b["wins"] += 1.0
    return agg


def _merge_setup_aggregates(
    live: dict[str, dict[str, float]],
    historical: dict[str, dict[str, float]],
    *,
    hist_weight: float = SETUP_HIST_WEIGHT,
) -> dict[str, dict[str, float]]:
    merged: dict[str, dict[str, float]] = defaultdict(
        lambda: {"n": 0.0, "net": 0.0, "wins": 0.0}
    )
    for source, weight in ((live, 1.0), (historical, hist_weight)):
        for setup, b in source.items():
            m = merged[setup]
            m["n"] += float(b.get("n") or 0) * weight
            m["net"] += float(b.get("net") or 0) * weight
            m["wins"] += float(b.get("wins") or 0) * weight
    return merged


def _setup_memory_from_aggregate(
    agg: dict[str, dict[str, float]],
) -> dict[str, dict[str, Any]]:
    memory: dict[str, dict[str, Any]] = {}
    for setup, b in agg.items():
        n = float(b.get("n") or 0)
        if n < MIN_SAMPLES_SETUP:
            continue
        net = float(b.get("net") or 0)
        exp = net / n
        adjust = 0.0
        blocked = exp < SETUP_BLOCK_EXP
        if blocked:
            adjust = SETUP_PENALTY_CAP
        elif exp > 0.3:
            adjust = min(SETUP_BONUS_CAP, 0.5 + exp)
        memory[setup] = {
            "trades": round(n, 1),
            "net_eur": round(net, 2),
            "expectancy_eur": round(exp, 3),
            "win_rate": round(float(b.get("wins") or 0) / n, 2),
            "score_adjust": round(adjust, 2),
            "blocked": blocked,
        }
    return memory


def _compute_setup_memory(
    linked: list[dict[str, Any]],
    historical: dict[str, dict[str, float]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Oma kauppahistoria + historiallinen round-trip-backtest (painotettu)."""
    live_agg = _aggregate_setup_from_linked(linked)
    hist_agg = historical or {}
    merged = _merge_setup_aggregates(live_agg, hist_agg)
    return _setup_memory_from_aggregate(merged)


def merge_regime_tuning(learning: dict[str, Any], regime: str) -> dict[str, Any]:
    """Yhdistä globaali oppiminen + aktiivisen regiimin ylikirjoitus."""
    merged = dict(learning)
    overrides = (learning.get("regime_tuning") or {}).get(regime) or {}
    if overrides:
        merged.update(
            {
                k: v
                for k, v in overrides.items()
                if k not in ("profit_take_tuning", "stop_tuning")
            }
        )
        pt_override = overrides.get("profit_take_tuning")
        if pt_override:
            base_pt = dict(merged.get("profit_take_tuning") or {})
            base_pt.update(pt_override)
            merged["profit_take_tuning"] = base_pt
        st_override = overrides.get("stop_tuning")
        if st_override:
            base_st = dict(merged.get("stop_tuning") or {})
            base_st.update(st_override)
            merged["stop_tuning"] = base_st
    merged["active_regime"] = regime
    return merged


def compute_tuning(
    portfolio: dict[str, Any],
    gemini_pick_stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Palauttaa säätöparametrit ja tilastot oppimista varten."""
    all_trades = portfolio.get("trades", [])
    sells = [t for t in all_trades if t.get("type") == "sell"][:LEARNING_WINDOW]

    stats = _aggregate_category_stats(sells)
    global_params, tune_notes = _apply_category_tuning(stats, min_samples=MIN_SAMPLES)
    profit_take_tuning, pt_notes = _compute_profit_take_tuning(stats, all_trades)
    stop_tuning, stop_notes = _compute_stop_tuning(stats)

    rotation_enabled = global_params["rotation_enabled"]
    rotation_scale = global_params["rotation_scale"]
    gemini_sell_min_confidence = global_params["gemini_sell_min_confidence"]
    gemini_sell_scale = global_params["gemini_sell_scale"]
    entry_score_min = global_params["entry_score_min"]
    max_new_positions = global_params["max_new_positions"]
    overall_exp = global_params["overall_expectancy_eur"]
    total_n = global_params["samples"]
    buy_scale, buy_scale_note = _compute_buy_scale(stats, overall_exp, total_n)

    linked = _sells_with_entry_context(all_trades)
    conf_tuning, conf_notes = _compute_gemini_confidence_tuning(
        sells,
        linked,
        base_min_conf=gemini_sell_min_confidence,
    )
    gemini_confidence_scales = conf_tuning["gemini_confidence_scales"]
    if conf_tuning["gemini_confidence_tagged"] >= MIN_GEMINI_CONF_TAGGED:
        gemini_sell_min_confidence = conf_tuning["gemini_sell_min_confidence"]

    from .setup_historical_backfill import get_setup_backfill_status, load_setup_stats

    setup_history = load_setup_stats()
    setup_memory = _compute_setup_memory(linked, setup_history)
    setup_backfill_status = get_setup_backfill_status()
    blocked_setups: list[str] = []
    regime_tuning, regime_stats = _compute_regime_tuning(sells)

    notes = list(tune_notes) + pt_notes + stop_notes + conf_notes
    if buy_scale_note:
        notes.append(buy_scale_note)
    tagged = sum(1 for t in sells if t.get("regime") in ("bull", "neutral", "bear"))
    if tagged < MIN_SAMPLES_REGIME:
        notes.append(f"regiimioppiminen {tagged}/{MIN_SAMPLES_REGIME} myyntiä")
    elif regime_tuning:
        active_regs = ", ".join(regime_tuning.keys())
        notes.append(f"regiimisäätö: {active_regs}")

    if setup_memory:
        good = sum(1 for m in setup_memory.values() if m.get("score_adjust", 0) > 0)
        bad = sum(1 for m in setup_memory.values() if m.get("score_adjust", 0) < 0)
        blocked_setups = [k for k, m in setup_memory.items() if m.get("blocked")]
        if good or bad:
            notes.append(f"asetelmat: {good} hyvää, {bad} huonoa")
        if blocked_setups:
            notes.append(f"{len(blocked_setups)} asetelmaa estetty")
    hist_ready = setup_backfill_status.get("setupHistorySetupsReady")
    if hist_ready:
        notes.append(f"historia {hist_ready} setuppia (paino {SETUP_HIST_WEIGHT:.0%})")

    try:
        from .exit_learning import get_summary as exit_learning_summary

        ex = exit_learning_summary()
        ready = len(ex.get("topSetups") or [])
        if ready:
            notes.append(f"huippumyynti {ready} exit-setuppia opittu")
        elif ex.get("pending"):
            notes.append(f"huippumyynti {ex['pending']} odottaa arviointia")
    except Exception:
        pass

    from .gemini_pick_tracking import compute_pick_tuning

    pick_tuning, pick_notes = compute_pick_tuning(gemini_pick_stats)
    gemini_buy_min_confidence = int(pick_tuning.get("gemini_buy_min_confidence", 5))
    gemini_pick_buy_scale = float(pick_tuning.get("gemini_pick_buy_scale", 1.0))
    notes.extend(pick_notes)

    memory = compute_symbol_memory(portfolio)
    # Nettopositiivisia ei estetä score-/cooldown-listalla — crooniset (0 voittoa)
    # jäävät silti blocked=True ja net < 0.
    blocked = [
        s
        for s, m in memory.items()
        if m["blocked"] and float(m.get("net_eur") or 0) < 0
    ]
    score_blocked = [
        normalize_symbol(s)
        for s, m in memory.items()
        if (m.get("score_adjust") or 0) <= SYMBOL_SCORE_BLOCK
        and float(m.get("net_eur") or 0) < 0
    ]
    blocked = list(dict.fromkeys(normalize_symbol(s) for s in blocked + score_blocked))
    chronic = [s for s, m in memory.items() if m.get("chronic")]
    cooldown = [
        s
        for s, m in memory.items()
        if m["blocked"]
        and not m.get("chronic")
        and float(m.get("net_eur") or 0) < 0
    ]
    score_blocked_unique = [s for s in score_blocked if s in blocked]
    losers = [s for s, m in memory.items() if m["score_adjust"] < 0]
    winners = [s for s, m in memory.items() if m["score_adjust"] > 0]
    if losers:
        notes.append(f"välttää {len(losers)} häviäjää")
    if chronic:
        notes.append(f"{len(chronic)} estetty (toistuva tappio)")
    if score_blocked_unique:
        labels = ", ".join(
            s.replace("t", "").replace("USD", "").replace("UST", "")
            for s in score_blocked_unique[:4]
        )
        suffix = f" (+{len(score_blocked_unique) - 4})" if len(score_blocked_unique) > 4 else ""
        notes.append(f"{len(score_blocked_unique)} estetty score ≤ {SYMBOL_SCORE_BLOCK}: {labels}{suffix}")
    if cooldown:
        notes.append(f"{len(cooldown)} cooldownissa")
    if winners:
        notes.append(f"suosii {len(winners)} voittajaa")

    note = " · ".join(notes) if notes else "oppiminen kerää dataa"

    return {
        "rotation_enabled": rotation_enabled,
        "rotation_scale": rotation_scale,
        "buy_scale": buy_scale,
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
        "blocked_setups": blocked_setups,
        "setup_backfill": setup_backfill_status,
        "regime_tagged_sells": tagged,
        "gemini_confidence_stats": conf_tuning["gemini_confidence_stats"],
        "gemini_confidence_scales": gemini_confidence_scales,
        "gemini_confidence_tagged": conf_tuning["gemini_confidence_tagged"],
        "gemini_confidence_tagged_buys": conf_tuning["gemini_confidence_tagged_buys"],
        "gemini_confidence_tagged_sells": conf_tuning["gemini_confidence_tagged_sells"],
        "gemini_buy_min_confidence": gemini_buy_min_confidence,
        "gemini_pick_buy_scale": gemini_pick_buy_scale,
        "gemini_pick_stats": pick_tuning.get("gemini_pick_stats"),
        "profit_take_tuning": profit_take_tuning,
        "stop_tuning": stop_tuning,
    }
