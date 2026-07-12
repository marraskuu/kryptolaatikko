import logging
import math
import os
import time
from datetime import datetime, timezone
from typing import Any, Callable

from .bitfinex import CANDLE_DEEP_LIMIT, is_stablecoin, normalize_symbol

logger = logging.getLogger(__name__)

STOP_LOSS_PCT = -2.0
ROTATE_LOSS_PCT = -1.25
PROFIT_TAKE_TRIGGER_PCT = 2.0
GEMINI_SELL_MIN_PROFIT_PCT = 0.5  # Gemini-myynti vain voitolla oleviin positioihin
GEMINI_BUY_MIN_CONFIDENCE = 5     # Gemini-ostot vain ≥ tämä (kun Gemini aktiivinen)
UPTREND_MIN_CHANGE_PCT = 0.3
MIN_TRADE_EUR = 10
CASH_BUFFER_EUR = 2
ROTATION_TRIM_FRACTION = 0.5
MIN_ROTATION_INTERVAL_SEC = 30 * 60
# Kun suurin osa pääomasta on käteisenä, sijoita aggressiivisesti (ei 30 min taukoa).
IDLE_CASH_DEPLOY_PCT = 0.35
IDLE_CASH_MIN_EUR = 150
# Bitfinex poisti kaupankäyntikulut kokonaan — 0 %.
FEE_RATE = 0.0
GEMINI_DEEP_ANALYSIS_LIMIT = int(os.environ.get("GEMINI_DEEP_ANALYSIS_LIMIT", "10"))
MARKET_DISPLAY_ENRICH_LIMIT = int(os.environ.get("MARKET_DISPLAY_ENRICH_LIMIT", "15"))
CANDLE_DISPLAY_LIMIT = 6
DEEP_ANALYSIS_TIME_BUDGET_SEC = int(os.environ.get("DEEP_ANALYSIS_TIME_BUDGET_SEC", "45"))

# A: ATR-pohjainen riski (tasapainoinen taso)
ATR_STOP_MULT = 1.5          # oletus neutraalissa (legacy-viite)
STOP_FLOOR_PCT = -1.5        # legacy-viite neutraalille
STOP_CAP_PCT = -8.0          # legacy-viite neutraalille
DEFAULT_ATR_PCT = 1.5        # jos ATR puuttuu, oletetaan ~1.5 %

# Regiimi + ATR stop-loss — bull: anna hengittää, bear: leikkaa nopeammin.
REGIME_STOP_PROFILES: dict[str, dict[str, float]] = {
    "bull": {"atr_mult": 1.75, "floor": -2.25, "cap": -9.0},
    "neutral": {"atr_mult": 1.5, "floor": -1.5, "cap": -8.0},
    "bear": {"atr_mult": 1.15, "floor": -1.15, "cap": -5.5},
}
# Tasapainotus (yli tavoitteen): regiimi + ennakointivaihe.
REBALANCE_MIN_PROFIT_PCT: dict[str, float] = {
    "bull": 0.5,
    "bull_entering": 0.35,
    "bull_emerging": 0.35,
    "neutral": 0.0,
    "neutral_entering": 0.15,
    "neutral_emerging": 0.15,
    "bear": 0.25,
    "bear_entering": 0.0,
    "bear_emerging": 0.1,
}
# Voitto-otto (oletus, kun ei oppimisdataa): phase → trigger/partial scale.
REGIME_PROFIT_TAKE_SCALES: dict[str, dict[str, float]] = {
    "bull_entering": {"trigger_scale": 0.86, "partial_trigger_scale": 0.84},
    "bull_emerging": {"trigger_scale": 0.9, "partial_trigger_scale": 0.88},
    "bull": {"trigger_scale": 0.95, "partial_trigger_scale": 0.95},
    "neutral_entering": {"trigger_scale": 0.9, "partial_trigger_scale": 0.9},
    "neutral_emerging": {"trigger_scale": 0.92, "partial_trigger_scale": 0.91},
    "neutral": {"trigger_scale": 0.92, "partial_trigger_scale": 0.92},
    "bear_entering": {"trigger_scale": 0.82, "partial_trigger_scale": 0.8},
    "bear_emerging": {"trigger_scale": 0.85, "partial_trigger_scale": 0.86},
    "bear": {"trigger_scale": 0.88, "partial_trigger_scale": 0.9},
}
ROUND_TRIP_COST_PCT = 0.0    # Bitfinex: ei kaupankäyntikuluja

# 1: Etuviisas rotaatio — kuluja ei enää ole, mutta vältetään silti turha
# noise-churn: vaihto vain jos uuden kohteen odotettu etu ylittää nykyisen
# selvällä marginaalilla (ja säästää 30 % voittoveron turhalta realisoinnilta).
ROTATION_EDGE_MARGIN_PCT = 0.3
ROTATION_MIN_EDGE_PCT = ROUND_TRIP_COST_PCT + ROTATION_EDGE_MARGIN_PCT

# 2: Aikastoppi — vapauta pääoma jämähtäneestä positiosta parempaan kohteeseen.
STUCK_POSITION_HOURS = 4.0       # positio ei liiku — myydään riippumatta markkinan 24h-noususta
STUCK_DEFER_1H_MIN = 0.25          # lykää jumitusta jos 1h ≥ tämä ja 4h ok
STUCK_DEFER_4H_MIN = 0.15
STUCK_MAX_DEFER_HOURS = 6.0        # pakko-vapautus — ei pidä yli 6 h vaikka lyhytaikainen pomppu
STUCK_FORCE_24H = -1.5             # heikko 24h → myy heti (ei lykätä)
STUCK_FORCE_LOSS_PCT = -1.1           # selvä tappio → myy heti (ei -0.8 % micro-churn)
STUCK_FORCE_4H = -0.5                # 4h yhä laskussa → ei dead-cat -poikkeusta
STAGNANT_HOURS_NEUTRAL = 4.0
STAGNANT_HOURS_BEAR = 5.0
STAGNANT_HOURS_BULL = 6.0
STAGNANT_MAX_PROFIT_PCT = 0.5
STAGNANT_MIN_LOSS_PCT = -0.25        # aikastoppi vain selvästä tappiosta (ei tasapaino/-0.1 %)
FAST_EXIT_LOSS_PCT = -1.5

# Bear-puolustus — vähemmän tappiollisia rotaatio-/aikastoppi-/ostoja laskevassa markkinassa.
BEAR_DEFENSE_ENABLED = os.environ.get("BEAR_DEFENSE_ENABLED", "1").lower() not in (
    "0",
    "false",
    "no",
    "off",
)
BEAR_MIN_CASH_SHARE = float(os.environ.get("BEAR_MIN_CASH_SHARE", "0.25"))
BEAR_CASH_TRIM_ENABLED = os.environ.get("BEAR_CASH_TRIM_ENABLED", "1").lower() not in (
    "0",
    "false",
    "no",
    "off",
)
BEAR_CASH_TRIM_MAX_FRACTION = float(os.environ.get("BEAR_CASH_TRIM_MAX_FRACTION", "0.5"))
BEAR_STAGNANT_MIN_LOSS_PCT = float(os.environ.get("BEAR_STAGNANT_MIN_LOSS_PCT", "-0.75"))
BEAR_STAGNANT_HOURS = float(os.environ.get("BEAR_STAGNANT_HOURS", "7.0"))
BEAR_STUCK_POSITION_HOURS = float(os.environ.get("BEAR_STUCK_POSITION_HOURS", "6.0"))

# 3: Hajautussuoja — rajoita korkeasti korreloivan klusterin yhteispaino.
CORR_THRESHOLD = 0.85
CORR_MIN_SAMPLES = 8
CLUSTER_WEIGHT_CAP = 0.6

# Likviditeetti — uudet ostot vain riittävän volyymin pariin (24 h Bitfinex).
MIN_ENTRY_VOLUME_EUR = 200_000
MIN_ENTRY_VOLUME_PREFERRED_EUR = 250_000
# Nimellishinta — vältä selvästi alle euron kolikoita (DOGE tms.) uusissa ostoissa.
MIN_ENTRY_PRICE_EUR = 1.0

# Keskittymistila — 1–2 vahvaa nostetta, isompi panos kun signaali selvä.
CONCENTRATION_MAX_POSITIONS = 2
CONCENTRATION_MIN_GEMINI_CONF = 8
CONCENTRATION_MIN_CHANGE_24H = 4.0
CONCENTRATION_MIN_CHANGE_4H = 2.5
CONCENTRATION_MIN_EDGE = 3.5
CONCENTRATION_MIN_CHANGE_24H_TECH = 6.0
CONCENTRATION_TRIM_FRACTION = 0.85
CONCENTRATION_MAX_RSI = 78
CONCENTRATION_OVERBOUGHT_CHANGE_24H = 12.0


def _edge_pct(analysis: dict[str, Any] | None) -> float:
    """Lyhyen aikavälin odotetun liikkeen estimaatti (%), rajattu — rotaation etuvertailuun."""
    if not analysis:
        return 0.0
    mom4 = analysis.get("change4hPct")
    if mom4 is None:
        mom4 = analysis.get("momentum")
    if mom4 is None:
        mom4 = analysis.get("changePct")
    edge = 0.5 * float(mom4 or 0)
    mom1 = analysis.get("change1hPct")
    if mom1 is not None:
        edge += 0.25 * float(mom1)
    edge += 0.4 * float(analysis.get("mtfAlign") or 0)
    sig = analysis.get("geminiSignal") or {}
    if sig.get("action") == "buy":
        edge += 0.3 * (float(sig.get("confidence", 5)) - 5.0)
    return max(-6.0, min(6.0, edge))


def _rotation_worthwhile(holding_analysis: dict[str, Any], best_target_edge: float) -> bool:
    """Kannattaako rotaatio: parhaan kohteen edun on ylitettävä nykyisen selvällä marginaalilla."""
    return (best_target_edge - _edge_pct(holding_analysis)) >= ROTATION_MIN_EDGE_PCT


def volume_eur(analysis: dict[str, Any] | None) -> float:
    if not analysis:
        return 0.0
    return float(analysis.get("volumeEur") or 0)


def entry_volume_ok(analysis: dict[str, Any] | None) -> bool:
    return volume_eur(analysis) >= MIN_ENTRY_VOLUME_EUR


def entry_price_ok(analysis: dict[str, Any] | None) -> bool:
    if not analysis:
        return False
    price = float(analysis.get("currentPrice") or 0)
    return price >= MIN_ENTRY_PRICE_EUR


def entry_eligible(analysis: dict[str, Any] | None) -> bool:
    return entry_volume_ok(analysis) and entry_price_ok(analysis)


def volume_rank_adjust(analysis: dict[str, Any]) -> float:
    v = volume_eur(analysis)
    if v >= 2_000_000:
        return 2.0
    if v >= 500_000:
        return 1.0
    if v >= MIN_ENTRY_VOLUME_PREFERRED_EUR:
        return 0.5
    if v >= MIN_ENTRY_VOLUME_EUR:
        return 0.0
    if v >= 50_000:
        return -3.0
    return -5.0


def _volume_k_label(analysis: dict[str, Any]) -> str:
    return f"{volume_eur(analysis) / 1000:.0f} k€"


def _liquid_crypto_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [c for c in items if entry_eligible(c.get("analysis"))]


def _low_volume_holding_release_ok(profit_pct: float, analysis: dict[str, Any]) -> bool:
    if entry_volume_ok(analysis):
        return False
    if profit_pct >= PROFIT_TAKE_TRIGGER_PCT and _in_uptrend(analysis):
        return False
    return True


def _holding_age_hours(opened_at: Any) -> float | None:
    if not opened_at:
        return None
    dt = _parse_time(opened_at)
    if dt is None:
        return None
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0


def _fifo_time_stop_sell_amount(
    symbol: str,
    holding_amount: float,
    price: float,
    trades: list[dict[str, Any]],
    fifo_lots: dict[str, list[dict[str, Any]]],
    min_age_hours: float,
) -> float | None:
    """Myy vain lotit, joiden ikä ≥ min_age_hours (ei koske tuoreita ostoja)."""
    from .fifo_lots import fifo_amount_older_than_hours

    stuck_amt = fifo_amount_older_than_hours(
        symbol, trades, min_age_hours, lots_cache=fifo_lots
    )
    if stuck_amt <= 1e-12:
        return None
    sell_amt = min(stuck_amt, holding_amount)
    if sell_amt * price < MIN_TRADE_EUR:
        return None
    return sell_amt


def _parse_time(iso: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _pearson(a: list[float], b: list[float]) -> float | None:
    n = min(len(a), len(b))
    if n < CORR_MIN_SAMPLES:
        return None
    a = a[-n:]
    b = b[-n:]
    ma = sum(a) / n
    mb = sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va <= 0 or vb <= 0:
        return None
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    return cov / math.sqrt(va * vb)


def _analysis_for(analyses: dict[str, dict[str, Any]], norm: str) -> dict[str, Any] | None:
    a = analyses.get(norm)
    if a is not None:
        return a
    for key, val in analyses.items():
        if normalize_symbol(key) == norm:
            return val
    return None


def _is_overheated_for_concentration(analysis: dict[str, Any]) -> bool:
    rsi = analysis.get("rsi")
    change = float(analysis.get("changePct") or analysis.get("momentum") or 0)
    return (
        rsi is not None
        and float(rsi) > CONCENTRATION_MAX_RSI
        and change > CONCENTRATION_OVERBOUGHT_CHANGE_24H
    )


def _strong_conviction_candidate(
    analysis: dict[str, Any],
    gemini_insights: dict[str, Any] | None,
    symbol: str,
) -> bool:
    if _is_overheated_for_concentration(analysis):
        return False
    if not entry_volume_ok(analysis):
        return False
    change_24h = float(analysis.get("changePct") or analysis.get("momentum") or 0)
    change_4h = float(analysis.get("change4hPct") or analysis.get("momentum") or 0)
    sig = _gemini_signal(gemini_insights, symbol) if gemini_insights else None
    if (
        sig
        and sig.get("action") == "buy"
        and int(sig.get("confidence", 0)) >= CONCENTRATION_MIN_GEMINI_CONF
        and (
            change_24h >= CONCENTRATION_MIN_CHANGE_24H
            or change_4h >= CONCENTRATION_MIN_CHANGE_4H
        )
    ):
        return True
    edge = _edge_pct(analysis)
    mtf = float(analysis.get("mtfAlign") or 0)
    if (
        edge >= CONCENTRATION_MIN_EDGE
        and change_24h >= CONCENTRATION_MIN_CHANGE_24H_TECH
        and mtf >= 1
    ):
        if analysis.get("emaBullish") or edge >= CONCENTRATION_MIN_EDGE + 1.0:
            return True
    return False


def _conviction_score(item: dict[str, Any], gemini_insights: dict[str, Any] | None) -> float:
    analysis = item.get("analysis") or {}
    sym = item.get("symbol", "")
    sig = _gemini_signal(gemini_insights, sym) if gemini_insights else None
    score = _edge_pct(analysis) * 2.0 + float(analysis.get("score", 0)) * 0.5
    change_24h = float(analysis.get("changePct") or analysis.get("momentum") or 0)
    score += min(change_24h, 15.0) * 0.3
    if sig and sig.get("action") == "buy":
        score += (int(sig.get("confidence", 5)) - 5) * 1.5
    return score


def _resolve_concentration(
    candidates: list[dict[str, Any]],
    gemini_insights: dict[str, Any] | None,
    regime: str,
    rotation_enabled: bool,
) -> tuple[bool, list[dict[str, Any]], str]:
    """Palauttaa (aktiivinen, 1–2 parasta kohdetta, syy)."""
    if regime == "bear" or not rotation_enabled or not candidates:
        return False, candidates, ""
    strong = [
        c
        for c in candidates
        if _strong_conviction_candidate(c.get("analysis") or {}, gemini_insights, c["symbol"])
    ]
    if not strong:
        return False, candidates, ""
    strong.sort(key=lambda c: -_conviction_score(c, gemini_insights))
    focused = strong[:CONCENTRATION_MAX_POSITIONS]
    labels = ", ".join(c["symbol"] for c in focused)
    best_a = focused[0].get("analysis") or {}
    ch = float(best_a.get("changePct") or best_a.get("momentum") or 0)
    reason = f"Keskittymistila — vahva noste ({ch:+.1f} % 24h), fokus: {labels}"
    return True, focused, reason


def diversify_weights(
    weights: dict[str, float],
    analyses: dict[str, dict[str, Any]],
    cluster_weight_cap: float = CLUSTER_WEIGHT_CAP,
) -> dict[str, float]:
    """3: Rajaa korkeasti korreloivan klusterin yhteispaino CLUSTER_WEIGHT_CAP:iin.

    Estää että koko salkku on tehollisesti yksi veto (esim. neljä samaan suuntaan
    liikkuvaa altcoinia). Jos kaikki valinnat korreloivat, ei voida hajauttaa →
    palautetaan painot ennallaan.
    """
    syms = [s for s, w in weights.items() if w > 0]
    if len(syms) < 2:
        return weights

    returns = {
        s: list(_analysis_for(analyses, s).get("recentReturns") or [])
        for s in syms
        if _analysis_for(analyses, s)
    }

    parent = {s: s for s in syms}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: str, y: str) -> None:
        parent[find(x)] = find(y)

    for i in range(len(syms)):
        for j in range(i + 1, len(syms)):
            ri = returns.get(syms[i])
            rj = returns.get(syms[j])
            if not ri or not rj:
                continue
            corr = _pearson(ri, rj)
            if corr is not None and corr >= CORR_THRESHOLD:
                union(syms[i], syms[j])

    clusters: dict[str, list[str]] = {}
    for s in syms:
        clusters.setdefault(find(s), []).append(s)
    if len(clusters) < 2:
        return weights  # kaikki samassa klusterissa — ei hajautettavaa

    adjusted = dict(weights)
    total = sum(adjusted[s] for s in syms) or 1.0
    cap = cluster_weight_cap * total

    freed = 0.0
    under: list[str] = []
    for members in clusters.values():
        cw = sum(adjusted[s] for s in members)
        if cw > cap and cw > 0:
            scale = cap / cw
            for s in members:
                freed += adjusted[s] * (1 - scale)
                adjusted[s] *= scale
        else:
            under.extend(members)

    if freed > 0 and under:
        base = sum(adjusted[s] for s in under) or float(len(under))
        for s in under:
            share = (adjusted[s] / base) if base > 0 else 1.0 / len(under)
            adjusted[s] += freed * share

    norm_total = sum(adjusted.values())
    if norm_total <= 0:
        return weights
    return {s: w / norm_total for s, w in adjusted.items()}


def _atr_pct(analysis: dict[str, Any]) -> float:
    val = analysis.get("atrPct")
    if val is None or val <= 0:
        return DEFAULT_ATR_PCT
    return float(val)


def default_stop_tuning() -> dict[str, float]:
    return {"atr_scale": 1.0, "floor_scale": 1.0, "cap_scale": 1.0}


def dynamic_stop_pct(
    analysis: dict[str, Any],
    regime: str = "neutral",
    stop_tuning: dict[str, Any] | None = None,
) -> float:
    """ATR- ja regiimipohjainen stop-loss (negatiivinen %), oppimisen hienosäätö."""
    profile = REGIME_STOP_PROFILES.get(regime, REGIME_STOP_PROFILES["neutral"])
    tuning = default_stop_tuning()
    if stop_tuning:
        for key in tuning:
            if key in stop_tuning:
                tuning[key] = float(stop_tuning[key])

    atr_mult = profile["atr_mult"] * tuning["atr_scale"]
    floor = profile["floor"] * tuning["floor_scale"]
    cap = profile["cap"] * tuning["cap_scale"]

    stop = -atr_mult * _atr_pct(analysis)
    return max(cap, min(floor, stop))


def format_stop_loss_reason(
    profit_pct: float,
    stop_pct: float,
    regime: str,
) -> str:
    regime_note = ""
    if regime in ("bull", "bear"):
        regime_note = f", {regime}-regiimi"
    return (
        f"Stop-loss {profit_pct:.1f} % (ATR-raja {stop_pct:.1f} %{regime_note}) — "
        f"rajataan tappio, pääoma parempaan"
    )


def _find_btc_symbol(tickers: dict[str, dict[str, Any]]) -> str | None:
    for sym in tickers:
        if normalize_symbol(sym).upper().startswith("TBTC"):
            return sym
    return None


def compute_market_regime(
    tickers: dict[str, dict[str, Any]],
    analyses: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """B: bull / neutral / bear BTC-trendin ja markkinaleveyden perusteella."""
    changes = [
        t.get("changePct", 0)
        for s, t in tickers.items()
        if not is_stablecoin(s) and t.get("last", 0) > 0
    ]
    breadth = (
        sum(1 for c in changes if c > 0) / len(changes) if changes else 0.5
    )

    btc_sym = _find_btc_symbol(tickers)
    btc_24h = 0.0
    btc_4h = None
    btc_ema_bull = None
    if btc_sym:
        btc_24h = tickers[btc_sym].get("changePct", 0)
        a = analyses.get(btc_sym, {})
        btc_4h = a.get("change4hPct")
        if a.get("ema9") is not None and a.get("ema21") is not None:
            btc_ema_bull = a["ema9"] > a["ema21"]

    bull_signals = 0
    bear_signals = 0
    if btc_24h > 1:
        bull_signals += 1
    elif btc_24h < -1.5:
        bear_signals += 1
    if btc_4h is not None:
        if btc_4h > 0.5:
            bull_signals += 1
        elif btc_4h < -0.5:
            bear_signals += 1
    if btc_ema_bull is True:
        bull_signals += 1
    elif btc_ema_bull is False:
        bear_signals += 1
    if breadth > 0.55:
        bull_signals += 1
    elif breadth < 0.4:
        bear_signals += 1

    if bear_signals >= 2 and bear_signals > bull_signals:
        regime = "bear"
    elif bull_signals >= 2 and bull_signals > bear_signals:
        regime = "bull"
    else:
        regime = "neutral"

    return {
        "regime": regime,
        "btc_change_24h_pct": round(btc_24h, 2),
        "btc_change_4h_pct": round(btc_4h, 2) if btc_4h is not None else None,
        "breadth_up_pct": round(breadth * 100, 1),
        "bull_signals": bull_signals,
        "bear_signals": bear_signals,
    }


def _shift_strength(
    margin: int,
    *,
    toward: str,
    prev_margin: int | None = None,
) -> str:
    """Heikkous/moderate/strong signaalimarginaalin ja momentin mukaan."""
    abs_m = abs(margin)
    if toward == "bull":
        strong = margin >= 2
        moderate = margin >= 1
    elif toward == "bear":
        strong = margin <= -2
        moderate = margin <= -1
    else:
        strong = abs_m <= 0
        moderate = abs_m <= 1

    if prev_margin is not None:
        delta = margin - prev_margin
        if toward == "bull" and delta >= 2:
            moderate = True
            strong = strong or delta >= 3
        elif toward == "bear" and delta <= -2:
            moderate = True
            strong = strong or delta <= -3

    if strong:
        return "strong"
    if moderate:
        return "moderate"
    return "weak"


def enrich_regime_phase(
    regime_info: dict[str, Any],
    prev_regime: str | None,
    prev_signal_margin: int | None = None,
) -> dict[str, Any]:
    """Ennakoi siirtymää bull/neutral/bear -suuntaan ennen virallista regiimivaihtoa."""
    regime = str(regime_info.get("regime", "neutral"))
    bull_s = int(regime_info.get("bull_signals") or 0)
    bear_s = int(regime_info.get("bear_signals") or 0)
    margin = bull_s - bear_s
    btc_24h = float(regime_info.get("btc_change_24h_pct") or 0)
    btc_4h = regime_info.get("btc_change_4h_pct")
    btc_4h_f = float(btc_4h) if btc_4h is not None else None

    phase = regime
    transition = None
    shift_to = regime
    shift_strength = "none"

    if prev_regime and prev_regime != regime:
        phase = f"{regime}_entering"
        transition = f"{prev_regime}_to_{regime}"
        shift_to = regime
        shift_strength = "strong"
    elif regime in ("bear", "neutral") and margin >= 1:
        if btc_24h > -0.5 and (btc_4h_f is None or btc_4h_f > 0):
            phase = "bull_emerging"
            shift_to = "bull"
            shift_strength = _shift_strength(
                margin, toward="bull", prev_margin=prev_signal_margin
            )
            if prev_regime == "bear":
                transition = "bear_toward_bull"
    elif regime in ("bull", "neutral") and margin <= -1:
        if btc_24h < 0.5 and (btc_4h_f is None or btc_4h_f < 0):
            phase = "bear_emerging"
            shift_to = "bear"
            shift_strength = _shift_strength(
                margin, toward="bear", prev_margin=prev_signal_margin
            )
            if prev_regime == "bull":
                transition = "bull_toward_bear"
    elif regime == "bull" and margin <= 0:
        if btc_4h_f is not None and btc_4h_f <= 0.25:
            phase = "neutral_emerging"
            shift_to = "neutral"
            shift_strength = _shift_strength(
                margin, toward="neutral", prev_margin=prev_signal_margin
            )
            transition = "bull_toward_neutral"
    elif regime == "bear" and margin >= 0:
        if btc_4h_f is not None and btc_4h_f >= -0.25:
            phase = "neutral_emerging"
            shift_to = "neutral"
            shift_strength = _shift_strength(
                margin, toward="neutral", prev_margin=prev_signal_margin
            )
            transition = "bear_toward_neutral"
    elif regime == "neutral" and abs(margin) <= 0 and bull_s == bear_s:
        shift_to = "neutral"
        shift_strength = "weak"

    regime_info["phase"] = phase
    regime_info["transition"] = transition
    regime_info["shift_to"] = shift_to
    regime_info["shift_strength"] = shift_strength
    regime_info["signal_margin"] = margin
    return regime_info


def anticipated_regime_key(regime_info: dict[str, Any] | str) -> str:
    """Ostot/oppiminen: ennakoidun shift_to-suunnan säännöt."""
    if isinstance(regime_info, str):
        return regime_info
    phase = str(regime_info.get("phase") or regime_info.get("regime", "neutral"))
    shift = regime_info.get("shift_to")
    strength = regime_info.get("shift_strength", "none")
    current = str(regime_info.get("regime", "neutral"))

    if phase.endswith("_entering") and shift:
        return str(shift)
    if phase.endswith("_emerging") and shift and strength in ("moderate", "strong", "weak"):
        return str(shift)
    return current


def risk_regime_key(regime_info: dict[str, Any] | str) -> str:
    """Stop-loss / defenssi: karhu-ennakko aktivoituu aikaisemmin kuin ostoaggressio."""
    if isinstance(regime_info, str):
        return regime_info
    phase = str(regime_info.get("phase") or "")
    shift = regime_info.get("shift_to")
    strength = regime_info.get("shift_strength", "none")
    current = str(regime_info.get("regime", "neutral"))

    if current == "bear" or phase == "bear_entering":
        return "bear"
    if phase == "bear_emerging" and shift == "bear":
        return "bear" if strength in ("moderate", "strong") else "neutral"
    if phase == "neutral_emerging" and current == "bull":
        return "neutral"
    return current


def _bear_defense_active(regime_info: dict[str, Any] | str | None) -> bool:
    if not BEAR_DEFENSE_ENABLED or not regime_info:
        return False
    return risk_regime_key(regime_info) == "bear"


def _stagnant_min_loss_pct(regime: str) -> float:
    if regime == "bear" and BEAR_DEFENSE_ENABLED:
        return BEAR_STAGNANT_MIN_LOSS_PCT
    return STAGNANT_MIN_LOSS_PCT


def _stagnant_hours(regime: str) -> float:
    if regime == "bull":
        return STAGNANT_HOURS_BULL
    if regime == "bear":
        return BEAR_STAGNANT_HOURS if BEAR_DEFENSE_ENABLED else STAGNANT_HOURS_BEAR
    return STAGNANT_HOURS_NEUTRAL


def _stuck_position_hours(regime: str) -> float:
    if regime == "bear" and BEAR_DEFENSE_ENABLED:
        return BEAR_STUCK_POSITION_HOURS
    return STUCK_POSITION_HOURS


def _bear_cash_deploy_ok(cash: float, total_value: float, regime_info: dict[str, Any] | str | None) -> bool:
    if not _bear_defense_active(regime_info) or total_value <= 0:
        return True
    return (cash / total_value) >= BEAR_MIN_CASH_SHARE


def _decision_cash_flow(decisions: list[dict[str, Any]]) -> tuple[float, float]:
    sell_proceeds = sum(d.get("eurAmount", 0) for d in decisions if d.get("type") == "sell")
    buy_spent = sum(d.get("eurAmount", 0) for d in decisions if d.get("type") == "buy")
    return sell_proceeds, buy_spent


def _projected_cash(cash: float, decisions: list[dict[str, Any]]) -> float:
    sell_proceeds, buy_spent = _decision_cash_flow(decisions)
    return cash + sell_proceeds - buy_spent


def _bear_cash_reserve_gap_eur(
    cash: float,
    total_value: float,
    regime_info: dict[str, Any] | str | None,
    decisions: list[dict[str, Any]],
) -> float:
    if not BEAR_CASH_TRIM_ENABLED or not _bear_defense_active(regime_info) or total_value <= 0:
        return 0.0
    target_cash = total_value * BEAR_MIN_CASH_SHARE
    return max(0.0, target_cash - _projected_cash(cash, decisions))


def _apply_bear_cash_reserve_trim(
    decisions: list[dict[str, Any]],
    holdings: dict[str, Any],
    analyses: dict[str, dict[str, Any]],
    cash: float,
    total_value: float,
    regime_info: dict[str, Any] | str | None,
    label_fn: Callable[[str], str],
    *,
    preferred_symbols: set[str] | None = None,
) -> None:
    """Pakota karhu-kassavara trimmaamalla heikoimmista positioista (ei rotaatiota)."""
    gap_eur = _bear_cash_reserve_gap_eur(cash, total_value, regime_info, decisions)
    if gap_eur < MIN_TRADE_EUR:
        return

    preferred = preferred_symbols or set()
    candidates: list[tuple[tuple[int, float, float], str, float, float, dict[str, Any]]] = []
    for symbol, holding in holdings.items():
        if is_stablecoin(symbol):
            continue
        analysis = analyses.get(symbol)
        if not analysis:
            continue
        price = float(analysis.get("currentPrice") or 0)
        if price <= 0:
            continue
        amount = _effective_holding_amount(symbol, holdings, decisions)
        if amount <= 0:
            continue
        value = amount * price
        if value < MIN_TRADE_EUR:
            continue
        norm = normalize_symbol(symbol)
        trim_priority = 1 if norm in preferred or symbol in preferred else 0
        score = float(analysis.get("score") or 0)
        candidates.append(
            ((trim_priority, score, -value), symbol, amount, price, analysis)
        )

    candidates.sort(key=lambda item: item[0])
    remaining = gap_eur
    target_pct = BEAR_MIN_CASH_SHARE * 100
    for _, symbol, amount, price, analysis in candidates:
        if remaining < MIN_TRADE_EUR:
            break
        max_sell_eur = amount * price * BEAR_CASH_TRIM_MAX_FRACTION
        sell_eur = min(remaining, max_sell_eur)
        if sell_eur < MIN_TRADE_EUR:
            continue
        sell_amount = min(amount, sell_eur / price)
        if sell_amount * price < MIN_TRADE_EUR:
            continue
        _append_sell_decision(
            decisions,
            symbol,
            sell_amount,
            price,
            (
                f"Karhu-kassavara — {label_fn(symbol)} trimmaus "
                f"{sell_eur:.0f} € kohti {target_pct:.0f} % käteistä"
            ),
            analysis,
        )
        remaining -= sell_amount * price


def _tier1_taken_for_symbol(
    symbol: str,
    profit_watches: dict[str, Any] | None,
) -> bool:
    if not profit_watches:
        return False
    watch = profit_watches.get(symbol) or profit_watches.get(normalize_symbol(symbol))
    if not watch:
        return False
    if watch.get("tier1Taken"):
        return True
    state = watch.get("state")
    return bool(isinstance(state, dict) and state.get("tier1Taken"))


def entry_regime_key(regime_info: dict[str, Any] | str) -> str:
    """Osto-/Gemini-sääntöihin (ennakoitu suunta)."""
    return anticipated_regime_key(regime_info)


def profit_take_phase_key(regime_info: dict[str, Any] | str) -> str:
    """Voitto-otto: phase ensin, sitten virallinen regiimi."""
    if isinstance(regime_info, str):
        return regime_info
    return str(regime_info.get("phase") or regime_info.get("regime", "neutral"))


def rebalance_phase_key(regime_info: dict[str, Any] | str | None) -> str:
    """Tasapainotuksen minimivoitto-kynnys."""
    if isinstance(regime_info, str):
        return regime_info
    if not regime_info:
        return "neutral"
    return str(regime_info.get("phase") or regime_info.get("regime", "neutral"))


def _entry_ok(analysis: dict[str, Any], regime: str) -> bool:
    """C + B: hyväksy tekninen sisäänosto vain kun aikajänteet linjassa ja regiimi sallii."""
    if analysis.get("action") == "sell" or analysis.get("condBlocked"):
        return False
    if not entry_price_ok(analysis):
        return False
    mtf = analysis.get("mtfAlign", 0)
    change_24h = analysis.get("changePct")
    if change_24h is None:
        change_24h = analysis.get("momentum") or 0
    change_4h = analysis.get("change4hPct")
    if regime == "bear":
        # Karhu: vain selvä moniaikainen nousu, ei putoavia veitsiä
        if change_4h is not None and change_4h <= 0:
            return False
        return mtf >= 2 and change_24h > -1
    if regime == "bull":
        return mtf >= 0
    # neutraali: vaadi selkeä linjaus ja positiivinen 24h
    return mtf >= 1 and change_24h > 0


def _gemini_conf_scale_for_analysis(
    analysis: dict[str, Any] | None,
    gemini_insights: dict[str, Any] | None,
    symbol: str,
    scales: dict[Any, float] | None,
) -> float:
    if not scales:
        return 1.0
    from .learning import _confidence_scale

    sym = normalize_symbol(symbol)
    sig = (analysis or {}).get("geminiSignal") or (
        _gemini_signal_for(gemini_insights, sym) if gemini_insights else None
    )
    if not sig:
        return 1.0
    try:
        conf = int(sig.get("confidence", 0))
    except (TypeError, ValueError):
        return 1.0
    if conf < 5:
        return 1.0
    return _confidence_scale(scales, conf)


def _is_buy_blocked(
    symbol: str,
    analysis: dict[str, Any] | None,
    *,
    blocked_buys: set[str],
    blocked_setups: set[str],
    regime: str,
    gemini_insights: dict[str, Any] | None = None,
    gemini_active: bool = False,
    gemini_conf_scales: dict[Any, float] | None = None,
    gemini_buy_min_confidence: int | None = None,
) -> bool:
    if not analysis:
        return True
    if normalize_symbol(symbol) in blocked_buys:
        return True
    if not entry_price_ok(analysis):
        return True
    if analysis.get("condBlocked"):
        return True
    from .market_learning import setup_matches_blocked
    from .market_microstructure import blocks_entry

    if blocks_entry(analysis):
        return True
    if not _gemini_buy_allowed(
        symbol,
        analysis,
        gemini_insights,
        gemini_active=gemini_active,
        gemini_conf_scales=gemini_conf_scales,
        gemini_buy_min_confidence=gemini_buy_min_confidence,
    ):
        return True

    return setup_matches_blocked(analysis, regime, blocked_setups)


def _gemini_signal_for(
    gemini_insights: dict[str, Any] | None,
    symbol: str,
) -> dict[str, Any] | None:
    if not gemini_insights:
        return None
    sym = normalize_symbol(symbol)
    signals = gemini_insights.get("signals") or {}
    if sym in signals:
        return signals[sym]
    for raw, sig in signals.items():
        if normalize_symbol(str(raw)) == sym:
            return sig
    return None


def _gemini_top_picks(gemini_insights: dict[str, Any] | None) -> set[str]:
    if not gemini_insights:
        return set()
    return {
        normalize_symbol(str(raw))
        for raw in (gemini_insights.get("top_picks") or [])
        if raw
    }


def _gemini_buy_allowed(
    symbol: str,
    analysis: dict[str, Any] | None,
    gemini_insights: dict[str, Any] | None,
    *,
    gemini_active: bool,
    gemini_conf_scales: dict[Any, float] | None = None,
    gemini_buy_min_confidence: int | None = None,
) -> bool:
    """Kun Gemini ohjaa salkkua: osta vain top-pickit confidence ≥ min."""
    min_conf = (
        GEMINI_BUY_MIN_CONFIDENCE
        if gemini_buy_min_confidence is None
        else int(gemini_buy_min_confidence)
    )
    if not gemini_active:
        return True
    sym = normalize_symbol(symbol)
    picks = _gemini_top_picks(gemini_insights)
    if sym not in picks:
        return False
    sig = (analysis or {}).get("geminiSignal") or _gemini_signal_for(gemini_insights, sym)
    if not sig:
        return False
    if sig.get("action") == "sell":
        return False
    conf = int(sig.get("confidence", 0))
    if conf < min_conf:
        return False
    if _gemini_conf_scale_for_analysis(analysis, gemini_insights, sym, gemini_conf_scales) <= 0:
        return False
    return True


def _market_stagnant_exit(
    profit_pct: float,
    age_h: float | None,
    regime: str,
    analysis: dict[str, Any],
    *,
    fifo_stuck_amount: float | None = None,
    oldest_stuck_age_h: float | None = None,
) -> bool:
    """Heikko markkina + pitkä pito ilman voittoa — ei bull-regiimissä."""
    change_24h = analysis.get("changePct") or analysis.get("momentum") or 0
    stagnant_h = _stagnant_hours(regime)
    aged = fifo_stuck_amount if fifo_stuck_amount is not None else 0.0
    if fifo_stuck_amount is None:
        aged_ok = age_h is not None and age_h >= stagnant_h
    else:
        aged_ok = aged > 1e-12
    stagnant_min_loss = _stagnant_min_loss_pct(regime)
    if not (
        aged_ok
        and profit_pct < STAGNANT_MAX_PROFIT_PCT
        and profit_pct <= stagnant_min_loss
        and regime != "bull"
        and change_24h <= 0
    ):
        return False
    if _stuck_release_forced(analysis, profit_pct, oldest_stuck_age_h):
        return True
    if _short_term_recovery_hold(analysis):
        return False
    return True


def _fast_loss_exit_reason(
    symbol: str,
    profit_pct: float,
    analysis: dict[str, Any],
    regime: str,
    symbol_memory: dict[str, dict[str, Any]],
    blocked_setups: set[str],
) -> str | None:
    """Täysi myynti lievässä tappiossa jos kohde/asetelma on jo merkitty huonoksi."""
    if profit_pct > FAST_EXIT_LOSS_PCT:
        return None
    norm = normalize_symbol(symbol)
    mem = symbol_memory.get(symbol) or symbol_memory.get(norm) or {}
    if mem.get("chronic"):
        return (
            f"Krooninen häviäjä — täysi myynti {profit_pct:.1f} % "
            f"(raja {FAST_EXIT_LOSS_PCT:.1f} %)"
        )
    if mem.get("blocked"):
        return (
            f"Symboli cooldownissa — täysi myynti {profit_pct:.1f} % "
            f"(raja {FAST_EXIT_LOSS_PCT:.1f} %)"
        )
    if (mem.get("score_adjust") or 0) <= -2.0:
        return (
            f"Tunnettu häviäjä (score {mem['score_adjust']:+.1f}) — "
            f"täysi myynti {profit_pct:.1f} %"
        )
    if _is_buy_blocked(
        symbol,
        analysis,
        blocked_buys=set(),
        blocked_setups=blocked_setups,
        regime=regime,
    ):
        if analysis.get("condBlocked"):
            return (
                f"Huono markkina-asetelma — täysi myynti {profit_pct:.1f} % "
                f"(raja {FAST_EXIT_LOSS_PCT:.1f} %)"
            )
        return (
            f"Huono oma asetelma — täysi myynti {profit_pct:.1f} % "
            f"(raja {FAST_EXIT_LOSS_PCT:.1f} %)"
        )
    return None


def _blocked_loser_release_reason(
    symbol: str,
    profit_pct: float,
    symbol_memory: dict[str, dict[str, Any]],
    blocked_buys: set[str],
) -> str | None:
    """Myy estetty häviäjä pienelläkin tappiolla — vapauttaa pääoman voittajiin."""
    norm = normalize_symbol(symbol)
    if norm not in blocked_buys:
        return None
    if profit_pct > 1.0:
        return None
    mem = symbol_memory.get(symbol) or symbol_memory.get(norm) or {}
    net = float(mem.get("net_eur") or 0)
    wins = int(mem.get("wins") or 0)
    losses = int(mem.get("losses") or 0)
    if mem.get("chronic") or net < -0.4 or (losses >= 3 and losses > wins):
        return (
            f"Estetty kohde — vapautetaan {profit_pct:+.1f} % "
            f"(historia {net:+.2f} €, {wins}V/{losses}T)"
        )
    return None


def _in_uptrend(analysis: dict[str, Any]) -> bool:
    """Position or market still rising — hold winners, don't sell early."""
    change = analysis.get("changePct") if analysis.get("changePct") is not None else analysis.get("momentum")
    if change is None:
        return False
    return change >= UPTREND_MIN_CHANGE_PCT


def _stuck_release_forced(
    analysis: dict[str, Any],
    profit_pct: float,
    oldest_stuck_age_h: float | None,
) -> bool:
    """Pakota vapautus kuolleesta kohteesta — ei lykätä lyhytaikaisen pompun takia."""
    change_24h = float(analysis.get("changePct") or analysis.get("momentum") or 0)
    if profit_pct <= STUCK_FORCE_LOSS_PCT:
        return True
    if change_24h <= STUCK_FORCE_24H:
        return True
    ch4 = analysis.get("change4hPct")
    if ch4 is not None and float(ch4) <= STUCK_FORCE_4H:
        return True
    if oldest_stuck_age_h is not None and oldest_stuck_age_h >= STUCK_MAX_DEFER_HOURS:
        return True
    mtf = analysis.get("mtfAlign")
    if mtf is not None and int(mtf) <= -1 and change_24h <= 0:
        return True
    return False


def _short_term_recovery_hold(analysis: dict[str, Any]) -> bool:
    """Lyhyen aikavälin elpyminen — lykää jumitusta / aikastoppia."""
    mtf = analysis.get("mtfAlign")
    if mtf is not None and int(mtf) >= 1:
        return True
    ch1 = analysis.get("change1hPct")
    ch4 = analysis.get("change4hPct")
    if ch1 is None or ch4 is None:
        return False
    return float(ch1) >= STUCK_DEFER_1H_MIN and float(ch4) >= STUCK_DEFER_4H_MIN


def _stuck_defer_reason(analysis: dict[str, Any], oldest_stuck_age_h: float | None) -> str:
    ch1 = analysis.get("change1hPct")
    ch4 = analysis.get("change4hPct")
    mtf = analysis.get("mtfAlign")
    parts: list[str] = []
    if ch1 is not None and ch4 is not None:
        parts.append(f"1h {float(ch1):+.1f} % · 4h {float(ch4):+.1f} %")
    elif mtf is not None and int(mtf) >= 1:
        parts.append("MTF ylös")
    age_note = ""
    if oldest_stuck_age_h is not None:
        age_note = f" (vanhin erä {oldest_stuck_age_h:.0f} h"
        if oldest_stuck_age_h < STUCK_MAX_DEFER_HOURS:
            age_note += f", pakko ≥{STUCK_MAX_DEFER_HOURS:.0f} h"
        age_note += ")"
    detail = " · ".join(parts) if parts else "lyhytaikainen nousu"
    return (
        f"Jumitus/aikastoppi lykätty — {detail}{age_note}. "
        f"Myydään jos tappio ≤{STUCK_FORCE_LOSS_PCT:.1f} %, 24h ≤{STUCK_FORCE_24H:.1f} % "
        f"tai pito ≥{STUCK_MAX_DEFER_HOURS:.0f} h"
    )


def calc_rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0

    gains = 0.0
    losses = 0.0
    for i in range(len(closes) - period, len(closes)):
        diff = closes[i] - closes[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses -= diff

    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def calc_ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    k = 2 / (period + 1)
    ema = values[0]
    for i in range(1, len(values)):
        ema = values[i] * k + ema * (1 - k)
    return ema


def calc_momentum(closes: list[float]) -> float:
    if len(closes) < 10:
        return 0.0
    recent = closes[-5:]
    older = closes[-10:-5]
    recent_avg = sum(recent) / len(recent)
    older_avg = sum(older) / len(older)
    if older_avg == 0:
        return 0.0
    return ((recent_avg - older_avg) / older_avg) * 100


def analyze_ticker_quick(ticker: dict[str, Any]) -> dict[str, Any]:
    change_pct = ticker["changePct"]
    score = 0
    reasons: list[str] = []

    # Voitto-orientoitunut: momentum ja nousu > laskuun ostaminen
    if 2 <= change_pct <= 8:
        score += 3
        reasons.append(f"24h +{change_pct:.1f} % — nousumomentum, voittopotentiaali")
    elif 0 <= change_pct < 2:
        score += 1
        reasons.append(f"24h +{change_pct:.1f} % — lievä nousu")
    elif -4 <= change_pct < 0:
        score += 0
        reasons.append(f"24h {change_pct:.1f} % — pieni dip, varovainen")
    elif change_pct < -6:
        score -= 3
        reasons.append(f"24h {change_pct:.1f} % — voimakas lasku, vältä")
    elif change_pct < -4:
        score -= 1
        reasons.append(f"24h {change_pct:.1f} % — laskussa")
    elif change_pct > 12:
        score -= 2
        reasons.append(f"24h +{change_pct:.1f} % — yliextended, voitto talteen")
    else:
        score += 1
        reasons.append(f"24h +{change_pct:.1f} % — vakaa nousu")

    if ticker["volumeEur"] > 500_000:
        score += 1
        reasons.append("Hyvä likviditeetti")
    if ticker["volumeEur"] > 2_000_000 and change_pct > 0:
        score += 1
        reasons.append("Vahva volyymi nousussa")
    vol = float(ticker.get("volumeEur") or 0)
    if vol < MIN_ENTRY_VOLUME_EUR:
        score -= 4
        reasons.append(
            f"Matala volyymi ({vol / 1000:.0f} k€) — ei kelpaa uusille ostoille"
        )
    elif vol < MIN_ENTRY_VOLUME_PREFERRED_EUR:
        score -= 1
        reasons.append(
            f"Volyymi alle {MIN_ENTRY_VOLUME_PREFERRED_EUR / 1000:.0f} k€ — varovainen"
        )

    last = float(ticker.get("last") or 0)
    if last > 0 and last < MIN_ENTRY_PRICE_EUR:
        score -= 4
        reasons.append(
            f"Hinta alle {MIN_ENTRY_PRICE_EUR:.0f} € ({last:.4f} €) — ei uusille ostoille"
        )

    action = "hold"
    if score >= 3:
        action = "buy"
    elif score <= -2:
        action = "sell"

    return {
        "action": action,
        "score": score,
        "rsi": 50,
        "ema9": ticker["last"],
        "ema21": ticker["last"],
        "momentum": change_pct,
        "changePct": change_pct,
        "currentPrice": ticker["last"],
        "volumeEur": ticker["volumeEur"],
        "reasons": reasons,
        "strength": min(abs(score) / 4, 1),
        "quick": True,
    }


def calc_period_change_pct(closes: list[float], periods: int) -> float | None:
    if len(closes) < periods + 1:
        return None
    old = closes[-(periods + 1)]
    new = closes[-1]
    if old <= 0:
        return None
    return ((new - old) / old) * 100


def calc_atr_pct(candles: list[dict[str, Any]], period: int = 14) -> float | None:
    """Average True Range prosentteina nykyhinnasta — volatiliteettimitta."""
    if len(candles) < period + 1:
        return None
    trs: list[float] = []
    for i in range(1, len(candles)):
        high = candles[i]["high"]
        low = candles[i]["low"]
        prev_close = candles[i - 1]["close"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    if len(trs) < period:
        return None
    atr = sum(trs[-period:]) / period
    last_close = candles[-1]["close"]
    if last_close <= 0:
        return None
    return (atr / last_close) * 100


def _mtf_alignment(change_1h: float | None, change_4h: float | None, change_24h: float) -> int:
    """+1 nouseva linjaus, -1 laskeva linjaus, 0 ristiriita (monen aikajänteen vahvistus)."""
    signs = []
    for v in (change_1h, change_4h, change_24h):
        if v is None:
            continue
        signs.append(1 if v > 0 else -1 if v < 0 else 0)
    if not signs:
        return 0
    if all(s > 0 for s in signs):
        return 1
    if all(s < 0 for s in signs):
        return -1
    return 0


def build_deep_analysis(ticker: dict[str, Any], candles: list[dict[str, Any]]) -> dict[str, Any]:
    if len(candles) >= 20:
        analysis = analyze_market(candles)
        closes = [c["close"] for c in candles]
        analysis["changePct"] = ticker.get("changePct", 0)
        change_1h = calc_period_change_pct(closes, 1)
        change_4h = calc_period_change_pct(closes, 4)
        if change_1h is not None:
            analysis["change1hPct"] = change_1h
        if change_4h is not None:
            analysis["change4hPct"] = change_4h
        atr_pct = calc_atr_pct(candles)
        if atr_pct is not None:
            analysis["atrPct"] = atr_pct
        rets = [
            (closes[i] - closes[i - 1]) / closes[i - 1]
            for i in range(1, len(closes))
            if closes[i - 1] > 0
        ]
        if rets:
            analysis["recentReturns"] = rets[-24:]
        analysis["mtfAlign"] = _mtf_alignment(
            change_1h, change_4h, ticker.get("changePct", 0)
        )
        analysis["volumeEur"] = ticker.get("volumeEur", 0)
        analysis["currentPrice"] = ticker.get("last", analysis["currentPrice"])
        analysis["emaBullish"] = analysis.get("ema9", 0) > analysis.get("ema21", 0)
        analysis["quick"] = False
        return analysis
    quick = analyze_ticker_quick(ticker)
    quick["change1hPct"] = None
    quick["change4hPct"] = None
    quick["atrPct"] = None
    quick["mtfAlign"] = 0
    return quick


def symbols_for_deep_analysis(
    tickers: dict[str, dict[str, Any]],
    portfolio: dict[str, Any],
    limit: int = GEMINI_DEEP_ANALYSIS_LIMIT,
) -> list[str]:
    holdings = list(portfolio.get("holdings", {}).keys())
    ranked = sorted(
        [s for s in tickers if not is_stablecoin(s)],
        key=lambda s: tickers[s].get("volumeEur", 0),
        reverse=True,
    )
    result: list[str] = []
    seen: set[str] = set()
    for sym in holdings + ranked:
        if sym in seen or sym not in tickers or is_stablecoin(sym):
            continue
        seen.add(sym)
        result.append(sym)
        if len(result) >= limit:
            break
    return result


def symbols_for_display_timeframes(
    tickers: dict[str, dict[str, Any]],
    limit: int = MARKET_DISPLAY_ENRICH_LIMIT,
) -> list[str]:
    """Markkinalistalle näkyvät top-volyymiparit (1h/4h UI:ta varten)."""
    return [
        s
        for s in sorted(
            [sym for sym in tickers if not is_stablecoin(sym)],
            key=lambda sym: tickers[sym].get("volumeEur", 0),
            reverse=True,
        )[:limit]
    ]


def enrich_display_timeframes(
    tickers: dict[str, dict[str, Any]],
    analyses: dict[str, dict[str, Any]],
    fetch_candles_fn: Callable[..., list[dict[str, Any]]],
    *,
    skip_symbols: set[str] | None = None,
    limit: int = MARKET_DISPLAY_ENRICH_LIMIT,
) -> None:
    """Kevyt 1h/4h-päivitys markkinalistalle — vain muutama kynttilä per symboli."""
    skip = skip_symbols or set()
    deadline = time.time() + min(20, DEEP_ANALYSIS_TIME_BUDGET_SEC)
    for symbol in symbols_for_display_timeframes(tickers, limit):
        if time.time() >= deadline:
            logger.warning(
                "Display timeframe enrich budget exhausted — remaining symbols skipped"
            )
            break
        if symbol in skip:
            continue
        ticker = tickers.get(symbol)
        if not ticker:
            continue
        try:
            candles = fetch_candles_fn(symbol, "1h", CANDLE_DISPLAY_LIMIT)
            if len(candles) < 2:
                continue
            closes = [c["close"] for c in candles]
            change_1h = calc_period_change_pct(closes, 1)
            change_4h = calc_period_change_pct(closes, 4)
            analysis = dict(analyses.get(symbol) or analyze_ticker_quick(ticker))
            if change_1h is not None:
                analysis["change1hPct"] = change_1h
            if change_4h is not None:
                analysis["change4hPct"] = change_4h
            analysis["mtfAlign"] = _mtf_alignment(
                change_1h, change_4h, ticker.get("changePct", 0)
            )
            analyses[symbol] = analysis
        except Exception:
            logger.warning("Display timeframe enrich failed for %s", symbol, exc_info=True)


def enrich_analyses_for_gemini(
    tickers: dict[str, dict[str, Any]],
    analyses: dict[str, dict[str, Any]],
    portfolio: dict[str, Any],
    fetch_candles_fn: Callable[..., list[dict[str, Any]]],
    limit: int = GEMINI_DEEP_ANALYSIS_LIMIT,
) -> None:
    """Päivittää top-symboleille kynttiläpohjaisen RSI/EMA/momentum-analyysin."""
    deadline = time.time() + DEEP_ANALYSIS_TIME_BUDGET_SEC
    for symbol in symbols_for_deep_analysis(tickers, portfolio, limit):
        if time.time() >= deadline:
            logger.warning(
                "Deep analysis time budget (%ss) exhausted — skipping remaining symbols",
                DEEP_ANALYSIS_TIME_BUDGET_SEC,
            )
            break
        ticker = tickers.get(symbol)
        if not ticker:
            continue
        prev = analyses.get(symbol) or {}
        try:
            candles = fetch_candles_fn(symbol, "1h", CANDLE_DEEP_LIMIT)
            fresh = build_deep_analysis(ticker, candles)
        except Exception:
            logger.warning("Deep analysis failed for %s", symbol, exc_info=True)
            fresh = analyze_ticker_quick(ticker)
        from .market_microstructure import carry_micro_fields

        carry_micro_fields(prev, fresh)
        analyses[symbol] = fresh


def _parse_trade_time(iso: str) -> datetime:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _is_emergency_trade_reason(reason: str) -> bool:
    keywords = ("Stop-loss", "Stablecoin", "realisoidaan voitto", "voitto +", "huipusta")
    lower = reason.lower()
    return any(k.lower() in lower for k in keywords)


def seconds_since_last_discretionary_trade(portfolio_data: dict[str, Any]) -> float | None:
    """Sekunteja viimeisestä rotaatio-/osto-myyntikaupasta (ei stop-loss / voitto-myynti)."""
    for trade in portfolio_data.get("trades", []):
        if trade.get("type") not in ("buy", "sell"):
            continue
        reason = trade.get("reason") or ""
        if _is_emergency_trade_reason(reason):
            continue
        try:
            last = _parse_trade_time(trade["timestamp"])
            return (datetime.now(timezone.utc) - last).total_seconds()
        except (ValueError, TypeError, KeyError):
            continue
    return None


def in_churn_cooldown(portfolio_data: dict[str, Any]) -> bool:
    elapsed = seconds_since_last_discretionary_trade(portfolio_data)
    if elapsed is None:
        return False
    return elapsed < MIN_ROTATION_INTERVAL_SEC


def _is_idle_cash(cash: float, total_value: float) -> bool:
    if cash < IDLE_CASH_MIN_EUR:
        return False
    if total_value <= 0:
        return True
    return cash / total_value >= IDLE_CASH_DEPLOY_PCT


def _symbols_for_idle_deploy(
    ranked: list[dict[str, Any]],
    limit: int,
    *,
    blocked_buys: set[str],
    blocked_setups: set[str],
    regime: str,
    gemini_insights: dict[str, Any] | None = None,
    gemini_active: bool = False,
    gemini_conf_scales: dict[Any, float] | None = None,
) -> list[str]:
    symbols: list[str] = []
    seen: set[str] = set()
    for r in ranked:
        sym = normalize_symbol(r["symbol"])
        if sym in seen:
            continue
        analysis = r.get("analysis") or {}
        if not entry_eligible(analysis):
            continue
        if _is_buy_blocked(
            sym,
            analysis,
            blocked_buys=blocked_buys,
            blocked_setups=blocked_setups,
            regime=regime,
            gemini_insights=gemini_insights,
            gemini_active=gemini_active,
            gemini_conf_scales=gemini_conf_scales,
        ):
            continue
        symbols.append(sym)
        seen.add(sym)
        if len(symbols) >= limit:
            break
    return symbols


def _release_idle_dust_holdings(
    decisions: list[dict[str, Any]],
    holdings: dict[str, Any],
    analyses: dict[str, dict[str, Any]],
    *,
    blocked_buys: set[str],
) -> None:
    """Myy alle 1 € / cooldown-kohteet — vapauttaa pääoman uudelleenallokaatioon."""
    for symbol, holding in list(holdings.items()):
        if is_stablecoin(symbol):
            continue
        analysis = analyses.get(symbol)
        if not analysis:
            continue
        norm = normalize_symbol(symbol)
        price = float(analysis.get("currentPrice") or 0)
        if price <= 0:
            continue
        cant_add = not entry_price_ok(analysis) or norm in blocked_buys
        if not cant_add:
            continue
        for d in decisions:
            if d.get("type") == "sell" and d.get("symbol") == symbol:
                break
        else:
            _append_sell_decision(
                decisions,
                symbol,
                holding["amount"],
                price,
                "Vapaa käteinen — myydään kohde johon ei voi lisätä (hinta/cooldown)",
                analysis,
            )


def analyze_market(candles: list[dict[str, Any]]) -> dict[str, Any]:
    closes = [c["close"] for c in candles]
    rsi = calc_rsi(closes)
    ema9 = calc_ema(closes[-20:], 9)
    ema21 = calc_ema(closes[-30:], 21)
    momentum = calc_momentum(closes)
    current_price = closes[-1]

    ema_bullish = ema9 > ema21
    ema_spread = ((ema9 - ema21) / ema21) * 100 if ema21 else 0

    score = 0
    reasons: list[str] = []

    if rsi < 30:
        score += 3
        reasons.append(f"RSI {rsi:.1f} — ylimyyty (ostosignaali)")
    elif rsi < 45:
        score += 1
        reasons.append(f"RSI {rsi:.1f} — lievä ostopaine")
    elif rsi > 70:
        score -= 3
        reasons.append(f"RSI {rsi:.1f} — yliostettu (myyntisignaali)")
    elif rsi > 55:
        score -= 1
        reasons.append(f"RSI {rsi:.1f} — lievä myyntipaine")
    else:
        reasons.append(f"RSI {rsi:.1f} — neutraali")

    if ema_bullish and ema_spread > 0.5:
        score += 2
        reasons.append(f"EMA9 > EMA21 (+{ema_spread:.2f} %) — nousutrendi")
    elif not ema_bullish and ema_spread < -0.5:
        score -= 2
        reasons.append(f"EMA9 < EMA21 ({ema_spread:.2f} %) — laskutrendi")
    else:
        reasons.append(f"EMA-risteys neutraali ({ema_spread:.2f} %)")

    if momentum > 2:
        score += 2
        reasons.append(f"Momentum +{momentum:.2f} % — vahva nousu")
    elif momentum < -2:
        score -= 2
        reasons.append(f"Momentum {momentum:.2f} % — vahva lasku")
    else:
        reasons.append(f"Momentum {momentum:.2f} % — maltillinen")

    action = "hold"
    if score >= 3:
        action = "buy"
    elif score <= -3:
        action = "sell"

    return {
        "action": action,
        "score": score,
        "rsi": rsi,
        "ema9": ema9,
        "ema21": ema21,
        "momentum": momentum,
        "currentPrice": current_price,
        "reasons": reasons,
        "strength": min(abs(score) / 5, 1),
        "quick": False,
    }


def _gemini_reason(analysis: dict[str, Any]) -> str | None:
    signal = analysis.get("geminiSignal")
    if signal and signal.get("reason"):
        conf = signal.get("confidence", 0)
        return f"Gemini ({conf}/10): {signal['reason']}"
    for reason in analysis.get("reasons", []):
        if reason.startswith("Gemini"):
            return reason
    return None


def _action_reason(analysis: dict[str, Any], fallback: str) -> str:
    return _gemini_reason(analysis) or fallback


def _market_change_summary(analysis: dict[str, Any]) -> str:
    parts: list[str] = []
    for key, label in (("change1hPct", "1h"), ("change4hPct", "4h"), ("changePct", "24h")):
        val = analysis.get(key)
        if val is not None:
            parts.append(f"{label} {val:+.1f} %")
    return ", ".join(parts)


def _format_trade_reason(
    analysis: dict[str, Any],
    *,
    gemini_active: bool,
    fallback: str,
    alloc_pct: float | None = None,
    eur_amount: float | None = None,
) -> str:
    """Perustelu selkeällä erottelulla: Gemini-teksti · hintamuutokset · salkun osuus · summa."""
    main = (_gemini_reason(analysis) if gemini_active else None) or fallback
    segments = [main]
    changes = _market_change_summary(analysis)
    if changes:
        segments.append(f"Hinta {changes}")
    if alloc_pct is not None:
        segments.append(f"salkun osuus {alloc_pct:.0f} %")
    if eur_amount is not None:
        segments.append(f"{eur_amount:.0f} €")
    return " · ".join(segments)


def _gemini_signal(
    gemini_insights: dict[str, Any] | None, symbol: str
) -> dict[str, Any] | None:
    if not gemini_insights:
        return None
    signals = gemini_insights.get("signals") or {}
    sym = normalize_symbol(symbol)
    return signals.get(sym) or signals.get(symbol)


def _compute_allocation_weights(
    gemini_insights: dict[str, Any] | None,
    symbols: list[str],
    analyses: dict[str, dict[str, Any]],
    gemini_active: bool,
) -> dict[str, float]:
    """Palauttaa symbol -> osuus (0–1), summa 1 valituille symboleille."""
    if not symbols:
        return {}

    raw: dict[str, float] = {}
    if gemini_insights:
        allocs = gemini_insights.get("allocations") or {}
        for sym in symbols:
            norm = normalize_symbol(sym)
            pct = allocs.get(sym) or allocs.get(norm)
            if pct is not None and pct > 0:
                raw[norm] = float(pct)
                continue
            sig = _gemini_signal(gemini_insights, sym)
            if sig and sig.get("alloc_pct") is not None and sig.get("alloc_pct") > 0:
                raw[norm] = float(sig["alloc_pct"])

    if not raw and gemini_active and gemini_insights:
        for sym in symbols:
            norm = normalize_symbol(sym)
            sig = _gemini_signal(gemini_insights, norm)
            picks = {normalize_symbol(s) for s in (gemini_insights.get("top_picks") or [])}
            if sig and sig.get("action") == "buy":
                raw[norm] = float(sig.get("confidence", 5))
            elif norm in picks:
                raw[norm] = float(sig.get("confidence", 6) if sig else 6)

    if not raw:
        ranked_scores = []
        for sym in symbols:
            norm = normalize_symbol(sym)
            analysis = analyses.get(norm) or analyses.get(sym) or {}
            ranked_scores.append((max(analysis.get("score", 1), 1), norm))
        total_score = sum(s for s, _ in ranked_scores)
        if total_score > 0:
            return {norm: score / total_score for score, norm in ranked_scores}
        equal = 1.0 / len(symbols)
        return {normalize_symbol(s): equal for s in symbols}

    normalized: dict[str, float] = {}
    for sym in symbols:
        norm = normalize_symbol(sym)
        normalized[norm] = raw.get(norm, 0.0)

    missing = [s for s, w in normalized.items() if w <= 0]
    if missing:
        for sym in missing:
            sig = _gemini_signal(gemini_insights, sym) if gemini_insights else None
            normalized[sym] = float(sig.get("confidence", 3)) if sig else 3.0

    total = sum(normalized.values())
    if total <= 0:
        equal = 1.0 / len(symbols)
        return {normalize_symbol(s): equal for s in symbols}
    return {sym: weight / total for sym, weight in normalized.items()}


def _target_holding_value(
    symbol: str,
    total_value: float,
    weights: dict[str, float],
) -> float:
    norm = normalize_symbol(symbol)
    return total_value * weights.get(norm, 0.0)


def _effective_holding_amount(
    symbol: str,
    holdings: dict[str, Any],
    decisions: list[dict[str, Any]],
) -> float:
    holding = holdings.get(symbol)
    if not holding:
        return 0.0
    amount = holding["amount"]
    for d in decisions:
        if d.get("type") == "sell" and d.get("symbol") == symbol:
            amount -= d.get("amount", 0)
    return max(0.0, amount)


def _holding_profit_pct(holding: dict[str, Any], analysis: dict[str, Any]) -> float:
    avg = float(holding.get("avgPrice") or 0)
    price = float(analysis.get("currentPrice") or 0)
    if avg <= 0 or price <= 0:
        return 0.0
    return ((price - avg) / avg) * 100


def _rotation_trim_allowed(profit_pct: float, regime: str = "neutral") -> bool:
    """Älä tee pieniä tappiomyyntejä rotaatiolla — odota stop tai selvä heikkous."""
    if profit_pct >= 0:
        return True
    return profit_pct <= _stagnant_min_loss_pct(regime)


def _rebalance_sell_allowed(
    profit_pct: float,
    regime: str,
    regime_info: dict[str, Any] | None = None,
) -> bool:
    """Tasapainotus vain kun voitto ≥ regiimin/vaiheen kynnys."""
    phase_key = rebalance_phase_key(regime_info if regime_info else regime)
    floor = REBALANCE_MIN_PROFIT_PCT.get(
        phase_key, REBALANCE_MIN_PROFIT_PCT.get(regime, 0.0)
    )
    return profit_pct >= floor


def _rotation_sell_amount(
    holding_amount: float,
    profit_pct: float,
    rotation_trim: float,
) -> float:
    """Tappiolla koko positio kerralla — osittaiset tappiomyynnit inflatoivat häviöitä."""
    if profit_pct < 0:
        return holding_amount
    return holding_amount * rotation_trim


def _gemini_sell_fraction(confidence: int) -> float:
    return {5: 0.35, 6: 0.45, 7: 0.55, 8: 0.65, 9: 0.80, 10: 1.0}.get(confidence, 0.50)


def _append_sell_decision(
    decisions: list[dict[str, Any]],
    symbol: str,
    crypto_amount: float,
    price: float,
    reason: str,
    analysis: dict[str, Any],
) -> None:
    if crypto_amount <= 0 or crypto_amount * price < MIN_TRADE_EUR:
        return
    for d in decisions:
        if d.get("type") == "sell" and d.get("symbol") == symbol:
            d["amount"] += crypto_amount
            d["eurAmount"] = d["amount"] * price
            return
    decisions.append(
        {
            "type": "sell",
            "symbol": symbol,
            "amount": crypto_amount,
            "eurAmount": crypto_amount * price,
            "reason": reason,
            "analysis": analysis,
        }
    )


def _deploy_cash_to_targets(
    decisions: list[dict[str, Any]],
    holdings: dict[str, Any],
    cash: float,
    total_value: float,
    weights: dict[str, float],
    target_symbols: list[str],
    analyses: dict[str, dict[str, Any]],
    label_fn: Callable[[str], str],
    gemini_active: bool,
    skip_sell_symbols: set[str],
    blocked_buys: set[str] | None = None,
    best_target_edge: float = 0.0,
    concentration_mode: bool = False,
    concentration_trim: float = CONCENTRATION_TRIM_FRACTION,
    *,
    blocked_setups: set[str] | None = None,
    regime: str = "neutral",
    regime_info: dict[str, Any] | None = None,
    buy_scale: float = 1.0,
    gemini_insights: dict[str, Any] | None = None,
    gemini_conf_scales: dict[Any, float] | None = None,
    gemini_buy_min_confidence: int | None = None,
    bull_satellite_split: dict[str, Any] | None = None,
) -> None:
    """Osittaiset myynnit ylipainoon / pois rotaatiosta; kaikki käteinen kohteisiin."""
    normalized_targets = {normalize_symbol(s) for s in target_symbols}
    blocked_buys = blocked_buys or set()
    blocked_setups = blocked_setups or set()
    # D: älä sijoita käteistä kolikkoon, joka on tappio-cooldownissa (vältä keskihinnan
    # alaspäin keskiarvoistamista häviäjään).
    buy_targets = [
        s
        for s in target_symbols
        if not _is_buy_blocked(
            s,
            analyses.get(s),
            blocked_buys=blocked_buys,
            blocked_setups=blocked_setups,
            regime=regime,
            gemini_insights=gemini_insights,
            gemini_active=gemini_active,
            gemini_conf_scales=gemini_conf_scales,
            gemini_buy_min_confidence=gemini_buy_min_confidence,
        )
        and entry_eligible(analyses.get(s))
    ]

    for symbol in list(holdings.keys()):
        if symbol in skip_sell_symbols or is_stablecoin(symbol):
            continue
        analysis = analyses.get(symbol)
        if not analysis:
            continue
        price = analysis["currentPrice"]
        if price <= 0:
            continue
        amount = _effective_holding_amount(symbol, holdings, decisions)
        if amount <= 0:
            continue
        current_value = amount * price
        norm = normalize_symbol(symbol)

        if norm in normalized_targets or symbol in target_symbols:
            profit_pct = _holding_profit_pct(holdings.get(symbol, {}), analysis)
            if not entry_volume_ok(analysis):
                if not _rebalance_sell_allowed(profit_pct, regime, regime_info):
                    continue
                sell_amount = amount * ROTATION_TRIM_FRACTION
                _append_sell_decision(
                    decisions,
                    symbol,
                    sell_amount,
                    price,
                    f"Matala volyymi ({_volume_k_label(analysis)}) — vapautetaan likvidimpiin",
                    analysis,
                )
                continue
            target = _target_holding_value(symbol, total_value, weights)
            excess = current_value - target
            if excess >= MIN_TRADE_EUR and _rebalance_sell_allowed(
                profit_pct, regime, regime_info
            ):
                sell_amount = min(amount, excess / price)
                phase = rebalance_phase_key(regime_info or regime)
                phase_note = ""
                if phase in ("bull_entering", "bull_emerging", "bull"):
                    phase_note = f" · {phase.replace('_', ' ')} ≥{REBALANCE_MIN_PROFIT_PCT.get(phase, 0):.2f} %"
                _append_sell_decision(
                    decisions,
                    symbol,
                    sell_amount,
                    price,
                    f"Tasapainotus — yli tavoitteen ({excess:.0f} €){phase_note}",
                    analysis,
                )
        elif target_symbols:
            if concentration_mode:
                sell_amount = amount * concentration_trim
                _append_sell_decision(
                    decisions,
                    symbol,
                    sell_amount,
                    price,
                    f"Keskittymistila — {label_fn(symbol)} ei fokuksessa, vapautetaan pääomaa",
                    analysis,
                )
            else:
                # 1: etuviisas — trimmaa pois valinnoista vain jos kohteella on selvä etu,
                # tai jos positio on selvästi heikkenevä (vältä turhaa noise-churnia).
                profit_pct = _holding_profit_pct(holdings.get(symbol, {}), analysis)
                if not _rotation_trim_allowed(profit_pct, regime):
                    continue
                weak = (analysis.get("changePct") or analysis.get("momentum") or 0) < -1
                if not (weak or _rotation_worthwhile(analysis, best_target_edge)):
                    continue
                sell_amount = _rotation_sell_amount(
                    amount, profit_pct, ROTATION_TRIM_FRACTION
                )
                _append_sell_decision(
                    decisions,
                    symbol,
                    sell_amount,
                    price,
                    f"{label_fn(symbol)} ei valinnoissa — myydään osa",
                    analysis,
                )

    sell_proceeds = sum(d.get("eurAmount", 0) for d in decisions if d["type"] == "sell")
    buy_spent = sum(d.get("eurAmount", 0) for d in decisions if d["type"] == "buy")
    available = cash + sell_proceeds - buy_spent - CASH_BUFFER_EUR
    buy_scale = max(0.0, min(1.0, float(buy_scale)))
    if buy_scale < 1.0:
        available *= buy_scale

    if available < MIN_TRADE_EUR or not buy_targets:
        return

    if not _bear_cash_deploy_ok(cash, total_value, regime_info):
        return

    if bull_satellite_split:
        from .bull_satellite import deploy_bull_satellite_cash

        if deploy_bull_satellite_cash(
            decisions,
            available_cash=available,
            split=bull_satellite_split,
            analyses=analyses,
            gemini_active=gemini_active,
            format_reason=_format_trade_reason,
        ):
            return

    deficits: list[tuple[float, str, dict[str, Any]]] = []
    for sym in buy_targets:
        analysis = analyses.get(sym)
        if not analysis or analysis["currentPrice"] <= 0:
            continue
        price = analysis["currentPrice"]
        amount = _effective_holding_amount(sym, holdings, decisions)
        current = amount * price
        target = _target_holding_value(sym, total_value, weights)
        deficit = target - current
        if deficit > 1:
            deficits.append((deficit, sym, analysis))

    if not deficits:
        best = max(
            buy_targets,
            key=lambda s: weights.get(normalize_symbol(s), 0),
        )
        analysis = analyses.get(best)
        if analysis and analysis["currentPrice"] > 0:
            deficits = [(available, best, analysis)]

    total_deficit = sum(d for d, _, _ in deficits)
    remaining = available

    for i, (deficit, sym, analysis) in enumerate(deficits):
        if remaining < MIN_TRADE_EUR:
            break
        price = analysis["currentPrice"]
        if i == len(deficits) - 1:
            buy_eur = remaining
        elif total_deficit > 0:
            buy_eur = min(remaining * (deficit / total_deficit), deficit, remaining)
        else:
            buy_eur = remaining / len(deficits)

        buy_eur = max(0.0, min(buy_eur, remaining))
        if gemini_active and gemini_conf_scales:
            conf_scale = _gemini_conf_scale_for_analysis(
                analysis, gemini_insights, sym, gemini_conf_scales
            )
            if conf_scale <= 0:
                continue
            buy_eur *= conf_scale
        buy_eur = max(0.0, min(buy_eur, remaining))
        if buy_eur < MIN_TRADE_EUR:
            continue

        alloc_pct = round(weights.get(normalize_symbol(sym), 0) * 100, 1)
        existing = next(
            (d for d in decisions if d["type"] == "buy" and d["symbol"] == sym),
            None,
        )
        if existing:
            existing["eurAmount"] += buy_eur
            existing["amount"] = existing["eurAmount"] / price
            remaining -= buy_eur
            continue

        reason = _format_trade_reason(
            analysis,
            gemini_active=gemini_active,
            fallback="Käteinen sijoitettu",
            alloc_pct=alloc_pct,
            eur_amount=buy_eur,
        )
        decisions.append(
            {
                "type": "buy",
                "symbol": sym,
                "eurAmount": buy_eur,
                "amount": buy_eur / price,
                "reason": reason,
                "analysis": analysis,
            }
        )
        remaining -= buy_eur


def _plan_initial_allocation(
    picks: list[dict[str, Any]],
    cash: float,
    gemini_insights: dict[str, Any] | None,
    gemini_active: bool,
    analyses: dict[str, dict[str, Any]],
    concentration_mode: bool = False,
) -> list[dict[str, Any]]:
    symbols = [item["symbol"] for item in picks]
    weights = _compute_allocation_weights(gemini_insights, symbols, analyses, gemini_active)
    if not (concentration_mode and len(symbols) <= CONCENTRATION_MAX_POSITIONS):
        weights = diversify_weights(weights, analyses)
    investable = cash / (1 + FEE_RATE)
    planned: list[dict[str, Any]] = []
    remaining = investable

    for i, item in enumerate(picks):
        sym = item["symbol"]
        w = weights.get(normalize_symbol(sym), 0.0)
        if i == len(picks) - 1:
            eur = round(remaining, 2)
        else:
            eur = round(investable * w, 2)
            remaining -= eur
        planned.append({**item, "eurAmount": max(eur, 0.0), "allocPct": round(w * 100, 1)})
    return planned


MAX_POSITIONS = 3

# Regiimikohtainen position yläraja — bear/neutral 1–2, bull 2–3 (absoluuttinen max = MAX_POSITIONS).
REGIME_MAX_POSITIONS: dict[str, int] = {
    "bear": 2,
    "bear_entering": 2,
    "bear_emerging": 2,
    "neutral": 2,
    "neutral_entering": 2,
    "neutral_emerging": 2,
    "bull": 3,
    "bull_entering": 3,
    "bull_emerging": 3,
}


def regime_max_positions(regime_info: dict[str, Any] | str | None) -> int:
    """Palauta regiimin/vaiheen sallima max-positionien määrä."""
    if isinstance(regime_info, dict):
        phase = str(regime_info.get("phase") or regime_info.get("regime") or "neutral")
        regime = str(regime_info.get("regime") or "neutral")
    else:
        regime = str(regime_info or "neutral")
        phase = regime
    if phase in REGIME_MAX_POSITIONS:
        return REGIME_MAX_POSITIONS[phase]
    return REGIME_MAX_POSITIONS.get(regime, 2)


def effective_max_positions(
    learning: dict[str, Any] | None,
    regime_info: dict[str, Any] | str | None,
) -> int:
    """Regiimikatto + oppimisen kiristys — ei koskaan yli MAX_POSITIONS."""
    cap = regime_max_positions(regime_info)
    learned = int((learning or {}).get("max_new_positions") or cap)
    return max(1, min(cap, learned, MAX_POSITIONS))


def _is_bull_regime_phase(regime: str, regime_info: dict[str, Any] | None) -> bool:
    phase = str((regime_info or {}).get("phase") or regime or "neutral")
    official = str((regime_info or {}).get("regime") or regime or "neutral")
    return phase.startswith("bull") or official == "bull"


def _technical_leader_symbols(
    analyses: dict[str, dict[str, Any]],
    limit: int = MAX_POSITIONS,
) -> list[str]:
    ranked = sorted(
        [
            (sym, a)
            for sym, a in analyses.items()
            if not is_stablecoin(sym)
            and a.get("currentPrice", 0) > 0
            and entry_volume_ok(a)
        ],
        key=lambda x: (
            -x[1].get("score", 0),
            -(x[1].get("changePct") or x[1].get("momentum") or 0),
            -(x[1].get("volumeEur") or 0),
        ),
    )
    return [normalize_symbol(sym) for sym, _ in ranked[:limit]]


def _gemini_desired_symbols(
    gemini_insights: dict[str, Any] | None,
    analyses: dict[str, dict[str, Any]] | None = None,
    gemini_conf_scales: dict[Any, float] | None = None,
    gemini_buy_min_confidence: int | None = None,
    limit: int = MAX_POSITIONS,
) -> list[str]:
    """Gemini valitsee 1–limit kohdetta — ei pakota täyteen."""
    min_conf = (
        GEMINI_BUY_MIN_CONFIDENCE
        if gemini_buy_min_confidence is None
        else int(gemini_buy_min_confidence)
    )
    if not gemini_insights:
        return []
    seen: set[str] = set()
    result: list[str] = []
    for raw in gemini_insights.get("top_picks") or []:
        sym = normalize_symbol(str(raw))
        if not sym or sym in seen or is_stablecoin(sym):
            continue
        sig = _gemini_signal_for(gemini_insights, sym)
        conf = int(sig.get("confidence", 0)) if sig else 0
        if conf < min_conf or (sig and sig.get("action") == "sell"):
            continue
        analysis = (analyses or {}).get(sym) or (analyses or {}).get(normalize_symbol(sym))
        if _gemini_conf_scale_for_analysis(analysis, gemini_insights, sym, gemini_conf_scales) <= 0:
            continue
        seen.add(sym)
        result.append(sym)
    if not result:
        for raw, signal in (gemini_insights.get("signals") or {}).items():
            if signal.get("action") != "buy" or signal.get("confidence", 0) < min_conf:
                continue
            sym = normalize_symbol(str(raw))
            if sym and sym not in seen and not is_stablecoin(sym):
                if _gemini_conf_scale_for_analysis(
                    (analyses or {}).get(sym),
                    gemini_insights,
                    sym,
                    gemini_conf_scales,
                ) <= 0:
                    continue
                seen.add(sym)
                result.append(sym)
    if analyses:
        result = [
            sym
            for sym in result
            if entry_volume_ok(analyses.get(sym) or analyses.get(normalize_symbol(sym)))
        ]
    return result[: max(1, limit)]


def _to_crypto_items(
    symbols: list[str],
    analyses: dict[str, dict[str, Any]],
    gemini_boost: bool = False,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for raw in symbols:
        sym = normalize_symbol(raw)
        if sym not in analyses or is_stablecoin(sym):
            continue
        analysis = analyses[sym]
        rank = analysis.get("score", 0) + (12 if gemini_boost else 0)
        items.append({"symbol": sym, "analysis": analysis, "rank": rank})
    return items


def _build_top_cryptos(
    ranked: list[dict[str, Any]],
    analyses: dict[str, dict[str, Any]],
    target_count: int,
    gemini_insights: dict[str, Any] | None,
    gemini_conf_scales: dict[Any, float] | None = None,
    gemini_buy_min_confidence: int | None = None,
) -> list[dict[str, Any]]:
    desired = _gemini_desired_symbols(
        gemini_insights,
        analyses,
        gemini_conf_scales,
        gemini_buy_min_confidence=gemini_buy_min_confidence,
        limit=target_count,
    )
    if desired:
        return _to_crypto_items(desired, analyses, gemini_boost=True)

    count = max(1, min(target_count, MAX_POSITIONS, len(ranked) or 1))
    return ranked[:count]


def make_trading_decisions(
    analyses: dict[str, dict[str, Any]],
    portfolio_data: dict[str, Any],
    total_value: float,
    label_fn: Callable[[str], str],
    gemini_insights: dict[str, Any] | None = None,
    gemini_picks: list[str] | None = None,
    regime: str = "neutral",
    learning: dict[str, Any] | None = None,
    regime_info: dict[str, Any] | None = None,
    profit_watches: dict[str, Any] | None = None,
) -> dict[str, Any]:
    holdings = portfolio_data["holdings"]
    cash = portfolio_data["cash"]
    learning = learning or {}
    from .learning import merge_regime_tuning
    from .market_learning import setup_key_for_analysis

    entry_regime = entry_regime_key(regime_info if regime_info else regime)
    risk_regime = risk_regime_key(regime_info if regime_info else regime)
    defense_regime = risk_regime
    learning = merge_regime_tuning(learning, entry_regime)
    rotation_scale = float(learning.get("rotation_scale", 1.0))
    rotation_enabled = bool(learning.get("rotation_enabled", True))
    rotation_trim = max(0.25, min(1.0, ROTATION_TRIM_FRACTION * rotation_scale))
    buy_scale = max(0.5, min(1.0, float(learning.get("buy_scale", 1.0))))
    gemini_buy_min_conf = int(
        learning.get("gemini_buy_min_confidence", GEMINI_BUY_MIN_CONFIDENCE)
    )
    gemini_pick_buy_scale = max(
        0.35, min(1.0, float(learning.get("gemini_pick_buy_scale", 1.0)))
    )
    # A: Geminin häviömyyntien hillintä (oppimisen säätämä)
    gemini_sell_min_conf = int(learning.get("gemini_sell_min_confidence", 0))
    gemini_sell_scale = max(0.2, min(1.0, float(learning.get("gemini_sell_scale", 1.0))))
    gemini_conf_scales = learning.get("gemini_confidence_scales") or {}

    # D: symbolimuisti — opi omista onnistumisista/epäonnistumisista per kolikko
    symbol_memory = learning.get("symbol_memory") or {}
    blocked_buys = {normalize_symbol(s) for s in (learning.get("blocked_buys") or [])}
    blocked_setups = set(learning.get("blocked_setups") or [])
    entry_score_min = int(learning.get("entry_score_min", 1))
    position_cap = effective_max_positions(learning, regime_info or regime)
    max_new_positions = position_cap
    setup_memory = learning.get("setup_memory") or {}
    gemini_active = bool(gemini_insights and gemini_insights.get("signals"))
    effective_buy_scale = buy_scale
    if gemini_active and gemini_pick_buy_scale < 1.0:
        effective_buy_scale = max(0.35, min(1.0, buy_scale * gemini_pick_buy_scale))

    def buy_blocked(sym: str, analysis: dict[str, Any] | None = None) -> bool:
        return _is_buy_blocked(
            sym,
            analysis if analysis is not None else analyses.get(sym),
            blocked_buys=blocked_buys,
            blocked_setups=blocked_setups,
            regime=entry_regime,
            gemini_insights=gemini_insights,
            gemini_active=gemini_active,
            gemini_conf_scales=gemini_conf_scales,
            gemini_buy_min_confidence=gemini_buy_min_conf,
        )

    def _mem_adjust(symbol: str) -> float:
        m = symbol_memory.get(symbol) or symbol_memory.get(normalize_symbol(symbol))
        return float(m.get("score_adjust", 0.0)) if m else 0.0

    def _setup_adjust(analysis: dict[str, Any]) -> float:
        key = setup_key_for_analysis(analysis, regime)
        m = setup_memory.get(key)
        return float(m.get("score_adjust", 0.0)) if m else 0.0

    ranked = [
        {
            "symbol": symbol,
            "analysis": analysis,
            "rank": analysis["score"]
            + _mem_adjust(symbol)
            + float(analysis.get("condAdjust") or 0)
            + float(analysis.get("microAdjust") or 0)
            + _setup_adjust(analysis)
            + volume_rank_adjust(analysis),
        }
        for symbol, analysis in analyses.items()
        if analysis.get("currentPrice", 0) > 0 and not is_stablecoin(symbol)
    ]
    ranked.sort(
        key=lambda x: (
            -x["rank"],
            -(x["analysis"].get("changePct") or x["analysis"].get("momentum") or 0),
            -(x["analysis"].get("volumeEur") or 0),
        )
    )
    # B + C + D: sisäänostoon vain regiimin/aikajänteiden sallimat, ei cooldownissa
    # olevia häviäjiä, ja vähintään valikoivuuskynnyksen score. Varalla kevyemmät suotimet.
    buyable = [
        r
        for r in ranked
        if _entry_ok(r["analysis"], entry_regime)
        and entry_eligible(r["analysis"])
        and normalize_symbol(r["symbol"]) not in blocked_buys
        and not buy_blocked(r["symbol"], r["analysis"])
        and r["rank"] >= entry_score_min
    ]
    ranked_liquid = [
        r
        for r in ranked
        if _entry_ok(r["analysis"], entry_regime)
        and entry_eligible(r["analysis"])
        and normalize_symbol(r["symbol"]) not in blocked_buys
        and not buy_blocked(r["symbol"], r["analysis"])
    ]
    ranked_buyable = buyable or ranked_liquid

    target_count = position_cap
    desired = (
        _gemini_desired_symbols(
            gemini_insights,
            analyses,
            gemini_conf_scales,
            gemini_buy_min_confidence=gemini_buy_min_conf,
            limit=position_cap,
        )
        if gemini_active
        else []
    )

    if gemini_active and desired:
        top_cryptos = _to_crypto_items(desired, analyses, gemini_boost=True)[:position_cap]
    elif gemini_active:
        leaders = _technical_leader_symbols(analyses, position_cap)
        liquid_held = [
            s for s in holdings if entry_volume_ok(analyses.get(s))
        ]
        symbols = list(dict.fromkeys(leaders + liquid_held))[:position_cap]
        top_cryptos = _to_crypto_items(symbols, analyses)[:position_cap]
    else:
        fallback_n = max(1, min(position_cap, len(holdings) or 2))
        top_cryptos = _build_top_cryptos(
            ranked_buyable,
            analyses,
            fallback_n,
            gemini_insights,
            gemini_conf_scales,
            gemini_buy_min_confidence=gemini_buy_min_conf,
        )

    top_cryptos = _liquid_crypto_items(top_cryptos)
    top_cryptos = [
        c
        for c in top_cryptos
        if not buy_blocked(c["symbol"], c.get("analysis"))
    ]

    if not top_cryptos and gemini_picks:
        gemini_top = _to_crypto_items(gemini_picks, analyses, gemini_boost=True)
        if gemini_top:
            top_cryptos = gemini_top

    concentration_mode, top_cryptos, concentration_reason = _resolve_concentration(
        top_cryptos, gemini_insights, regime, rotation_enabled
    )
    top_cryptos = [
        c
        for c in top_cryptos
        if not buy_blocked(c["symbol"], c.get("analysis"))
    ]
    if concentration_mode:
        logger.info("Concentration mode active: %s", concentration_reason)

    top_cryptos = top_cryptos[:position_cap]
    target_count = max(1, min(len(top_cryptos), position_cap)) if top_cryptos else 1

    top_symbols = {c["symbol"] for c in top_cryptos}
    top_norms = {normalize_symbol(s) for s in top_symbols}
    # 1: paras saatavilla oleva kohde-edge kuluviisasta rotaatiovertailua varten
    best_target_edge = max(
        (_edge_pct(c.get("analysis")) for c in top_cryptos if c.get("analysis")),
        default=0.0,
    )

    decisions: list[dict[str, Any]] = []
    churn_cooldown = in_churn_cooldown(portfolio_data)
    concentration_trim = (
        CONCENTRATION_TRIM_FRACTION if concentration_mode else rotation_trim
    )

    if len(holdings) == 0 and cash > 100:
        picks: list[dict[str, Any]] = []
        if desired:
            picks = _to_crypto_items(desired, analyses, gemini_boost=True)
        elif not gemini_active and ranked_buyable:
            picks = ranked_buyable[: min(position_cap, max_new_positions, len(ranked_buyable))]
        elif ranked_buyable:
            picks = ranked_buyable[:1]
        if picks:
            picks = _liquid_crypto_items(picks)
            picks = [
                c
                for c in picks
                if not buy_blocked(c["symbol"], c.get("analysis"))
            ]
        if picks:
            empty_conc, picks, empty_conc_reason = _resolve_concentration(
                picks, gemini_insights, regime, rotation_enabled
            )
            if empty_conc:
                logger.info("Concentration mode (empty portfolio): %s", empty_conc_reason)
            return {
                "decisions": [],
                "targetCount": len(picks),
                "topSymbols": [c["symbol"] for c in picks],
                "initialAllocation": _plan_initial_allocation(
                    picks, cash, gemini_insights, gemini_active, analyses, empty_conc
                ),
                "geminiActive": gemini_active,
                "concentrationMode": empty_conc,
            }

    portfolio_trades = portfolio_data.get("trades") or []
    from .fifo_lots import fifo_oldest_stuck_lot_age_hours, open_fifo_lots

    fifo_lots = open_fifo_lots(portfolio_trades)

    for symbol, holding in holdings.items():
        analysis = analyses.get(symbol)
        if not analysis:
            continue

        if is_stablecoin(symbol):
            decisions.append(
                {
                    "type": "sell",
                    "symbol": symbol,
                    "amount": holding["amount"],
                    "eurAmount": holding["amount"] * analysis["currentPrice"],
                    "reason": "Stablecoin — myydään, ei sijoituskohte",
                    "analysis": analysis,
                }
            )
            continue

        holding_value = holding["amount"] * analysis["currentPrice"]
        profit_pct = (
            ((analysis["currentPrice"] - holding["avgPrice"]) / holding["avgPrice"]) * 100
            if holding["avgPrice"]
            else 0
        )

        fast_exit = _fast_loss_exit_reason(
            symbol,
            profit_pct,
            analysis,
            regime,
            symbol_memory,
            blocked_setups,
        )
        if fast_exit:
            decisions.append(
                {
                    "type": "sell",
                    "symbol": symbol,
                    "amount": holding["amount"],
                    "eurAmount": holding_value,
                    "reason": fast_exit,
                    "analysis": analysis,
                }
            )
            continue

        blocked_release = _blocked_loser_release_reason(
            symbol, profit_pct, symbol_memory, blocked_buys
        )
        if blocked_release:
            decisions.append(
                {
                    "type": "sell",
                    "symbol": symbol,
                    "amount": holding["amount"],
                    "eurAmount": holding_value,
                    "reason": blocked_release,
                    "analysis": analysis,
                }
            )
            continue

        if not entry_volume_ok(analysis):
            if profit_pct <= -0.3:
                decisions.append(
                    {
                        "type": "sell",
                        "symbol": symbol,
                        "amount": holding["amount"],
                        "eurAmount": holding_value,
                        "reason": (
                            f"Matala volyymi ({_volume_k_label(analysis)}) + "
                            f"tappio {profit_pct:.1f} % — myydään"
                        ),
                        "analysis": analysis,
                    }
                )
                continue
            if _low_volume_holding_release_ok(profit_pct, analysis):
                sell_amount = _rotation_sell_amount(
                    holding["amount"], profit_pct, rotation_trim
                )
                if sell_amount * analysis["currentPrice"] >= MIN_TRADE_EUR:
                    _append_sell_decision(
                        decisions,
                        symbol,
                        sell_amount,
                        analysis["currentPrice"],
                        (
                            f"Matala volyymi ({_volume_k_label(analysis)}) — "
                            f"vapautetaan likvidimpiin kohteisiin"
                        ),
                        analysis,
                    )
                    continue

        from .market_microstructure import holding_illiquid_trap

        trapped, trap_reason = holding_illiquid_trap(analysis, holding_value)
        if trapped and trap_reason:
            if profit_pct <= -0.3:
                decisions.append(
                    {
                        "type": "sell",
                        "symbol": symbol,
                        "amount": holding["amount"],
                        "eurAmount": holding_value,
                        "reason": f"{trap_reason} — myydään tappiolla",
                        "analysis": analysis,
                    }
                )
                continue
            if _low_volume_holding_release_ok(profit_pct, analysis) or profit_pct <= 1.0:
                sell_amount = _rotation_sell_amount(
                    holding["amount"], profit_pct, rotation_trim
                )
                if sell_amount * analysis["currentPrice"] >= MIN_TRADE_EUR:
                    _append_sell_decision(
                        decisions,
                        symbol,
                        sell_amount,
                        analysis["currentPrice"],
                        f"{trap_reason} — vapautetaan pääomaa",
                        analysis,
                    )
                    continue

        norm_hold = normalize_symbol(symbol)
        if concentration_mode and norm_hold not in top_norms:
            edge_here = _edge_pct(analysis)
            gap = best_target_edge - edge_here
            if profit_pct <= -0.5 and not _bear_defense_active(regime_info):
                decisions.append(
                    {
                        "type": "sell",
                        "symbol": symbol,
                        "amount": holding["amount"],
                        "eurAmount": holding_value,
                        "reason": (
                            f"Keskittymistila — tappiolla {profit_pct:.1f} %, "
                            f"vapautetaan fokuksen kohteisiin"
                        ),
                        "analysis": analysis,
                    }
                )
                continue
            skip_consolidate = (
                profit_pct >= PROFIT_TAKE_TRIGGER_PCT
                and _in_uptrend(analysis)
                and gap < ROTATION_MIN_EDGE_PCT * 2
            ) or (
                profit_pct > 0
                and _in_uptrend(analysis)
                and gap < ROTATION_MIN_EDGE_PCT
            )
            if not skip_consolidate and _rotation_trim_allowed(profit_pct, defense_regime):
                sell_amount = _rotation_sell_amount(
                    holding["amount"], profit_pct, concentration_trim
                )
                if sell_amount * analysis["currentPrice"] >= MIN_TRADE_EUR:
                    _append_sell_decision(
                        decisions,
                        symbol,
                        sell_amount,
                        analysis["currentPrice"],
                        (
                            f"Keskittymistila — {profit_pct:+.1f} %, "
                            f"siirretään pääomaa vahvempaan nosteeseen"
                        ),
                        analysis,
                    )
                    continue

        if profit_pct >= PROFIT_TAKE_TRIGGER_PCT:
            decisions.append(
                {
                    "type": "hold",
                    "symbol": symbol,
                    "reason": (
                        f"Voitto +{profit_pct:.1f} % — pidetään nousussa, "
                        f"myydään vasta tasaantumisen tai pienen laskun jälkeen"
                    ),
                    "analysis": analysis,
                }
            )
            continue

        stop_pct = dynamic_stop_pct(
            analysis,
            risk_regime,
            learning.get("stop_tuning"),
        )
        if profit_pct <= stop_pct:
            decisions.append(
                {
                    "type": "sell",
                    "symbol": symbol,
                    "amount": holding["amount"],
                    "eurAmount": holding_value,
                    "reason": format_stop_loss_reason(profit_pct, stop_pct, defense_regime),
                    "analysis": analysis,
                }
            )
            continue

        age_h = _holding_age_hours(holding.get("openedAt"))
        if defense_regime != "bull" and profit_pct < STAGNANT_MAX_PROFIT_PCT:
            stuck_h = _stuck_position_hours(defense_regime)
            stagnant_min_loss = _stagnant_min_loss_pct(defense_regime)
            stuck_sell_amt = _fifo_time_stop_sell_amount(
                symbol,
                holding["amount"],
                analysis["currentPrice"],
                portfolio_trades,
                fifo_lots,
                stuck_h,
            )
            if stuck_sell_amt is not None:
                oldest_stuck_h = fifo_oldest_stuck_lot_age_hours(
                    symbol,
                    portfolio_trades,
                    stuck_h,
                    lots_cache=fifo_lots,
                )
                stuck_forced = _stuck_release_forced(
                    analysis, profit_pct, oldest_stuck_h
                )
                if (
                    not stuck_forced
                    and profit_pct > stagnant_min_loss
                ):
                    pass
                elif (
                    _short_term_recovery_hold(analysis)
                    and not stuck_forced
                ):
                    decisions.append(
                        {
                            "type": "hold",
                            "symbol": symbol,
                            "reason": _stuck_defer_reason(analysis, oldest_stuck_h),
                            "analysis": analysis,
                        }
                    )
                    continue
                elif stuck_forced or profit_pct <= stagnant_min_loss:
                    partial = stuck_sell_amt < holding["amount"] * 0.99
                    fifo_note = " (vain vanhat lotit)" if partial else ""
                    decisions.append(
                        {
                            "type": "sell",
                            "symbol": symbol,
                            "amount": stuck_sell_amt,
                            "eurAmount": stuck_sell_amt * analysis["currentPrice"],
                            "reason": (
                                f"Positio jämähtänyt ≥{stuck_h:.0f} h "
                                f"({profit_pct:+.1f} %){fifo_note} — "
                                f"myydään riippumatta markkinan noususta"
                            ),
                            "analysis": analysis,
                        }
                    )
                    continue

        if profit_pct > 0 and _in_uptrend(analysis):
            decisions.append(
                {
                    "type": "hold",
                    "symbol": symbol,
                    "reason": (
                        f"Nousuputki jatkuu (+{profit_pct:.1f} % voitolla) — "
                        f"pidetään kunnes tasaantuu tai tulee pieni lasku"
                    ),
                    "analysis": analysis,
                }
            )
            continue

        gemini_sig = _gemini_signal(gemini_insights, symbol) or analysis.get("geminiSignal")
        change_24h = analysis.get("changePct") or analysis.get("momentum") or 0

        stagnant_h = _stagnant_hours(defense_regime)
        stagnant_min_loss = _stagnant_min_loss_pct(defense_regime)
        sell_amt = _fifo_time_stop_sell_amount(
            symbol,
            holding["amount"],
            analysis["currentPrice"],
            portfolio_trades,
            fifo_lots,
            stagnant_h,
        )
        if sell_amt is not None:
            oldest_stuck_h = fifo_oldest_stuck_lot_age_hours(
                symbol,
                portfolio_trades,
                stagnant_h,
                lots_cache=fifo_lots,
            )
            aikastoppi_ready = (
                profit_pct < STAGNANT_MAX_PROFIT_PCT
                and profit_pct <= stagnant_min_loss
                and defense_regime != "bull"
                and change_24h <= 0
            )
            if aikastoppi_ready and _market_stagnant_exit(
                profit_pct,
                age_h,
                defense_regime,
                analysis,
                fifo_stuck_amount=sell_amt,
                oldest_stuck_age_h=oldest_stuck_h,
            ):
                partial = sell_amt < holding["amount"] * 0.99
                fifo_note = " (vain vanhat lotit)" if partial else ""
                decisions.append(
                    {
                        "type": "sell",
                        "symbol": symbol,
                        "amount": sell_amt,
                        "eurAmount": sell_amt * analysis["currentPrice"],
                        "reason": (
                            f"Aikastoppi ≥{stagnant_h:.0f} h — "
                            f"jämähtänyt ({profit_pct:+.1f} %){fifo_note}, "
                            f"vapautetaan pääoma vahvempaan kohteeseen"
                        ),
                        "analysis": analysis,
                    }
                )
                continue
            if (
                aikastoppi_ready
                and _short_term_recovery_hold(analysis)
                and not _stuck_release_forced(
                    analysis, profit_pct, oldest_stuck_h
                )
            ):
                decisions.append(
                    {
                        "type": "hold",
                        "symbol": symbol,
                        "reason": _stuck_defer_reason(analysis, oldest_stuck_h),
                        "analysis": analysis,
                    }
                )
                continue

        sell_conf = 5 if profit_pct < 0 else 6
        if churn_cooldown:
            decisions.append(
                {
                    "type": "hold",
                    "symbol": symbol,
                    "reason": "Churn-tauko (30 min) — ei rotaatiota vielä",
                    "analysis": analysis,
                }
            )
        elif (
            gemini_sig
            and gemini_sig.get("action") == "sell"
            and gemini_sig.get("confidence", 0) >= max(sell_conf, gemini_sell_min_conf)
        ):
            if profit_pct < GEMINI_SELL_MIN_PROFIT_PCT:
                decisions.append(
                    {
                        "type": "hold",
                        "symbol": symbol,
                        "reason": (
                            f"Gemini myynti estetty — positio {profit_pct:+.1f} % "
                            f"(vain voitolla ≥ {GEMINI_SELL_MIN_PROFIT_PCT:.1f} %)"
                        ),
                        "analysis": analysis,
                    }
                )
            else:
                conf = int(gemini_sig.get("confidence", 5))
                conf_scale = (
                    float(gemini_conf_scales.get(conf, gemini_conf_scales.get(str(conf), 1.0)))
                    if gemini_conf_scales
                    else 1.0
                )
                if conf_scale <= 0:
                    decisions.append(
                        {
                            "type": "hold",
                            "symbol": symbol,
                            "reason": (
                                f"Gemini myynti ({conf}/10) estetty — "
                                f"tappiollinen confidence-taso oppimisessa"
                            ),
                            "analysis": analysis,
                        }
                    )
                elif _tier1_taken_for_symbol(symbol, profit_watches):
                    decisions.append(
                        {
                            "type": "hold",
                            "symbol": symbol,
                            "reason": (
                                "Gemini osittainen myynti ohitettu — "
                                "voitto-otto porras 1 jo tehty (trailing jatkuu)"
                            ),
                            "analysis": analysis,
                        }
                    )
                else:
                    sell_amount = (
                        holding["amount"]
                        * _gemini_sell_fraction(conf)
                        * gemini_sell_scale
                        * conf_scale
                    )
                    _append_sell_decision(
                        decisions,
                        symbol,
                        sell_amount,
                        analysis["currentPrice"],
                        _action_reason(
                            analysis,
                            f"Gemini suosittelee osittaista myyntiä — {gemini_sig.get('reason', '')}",
                        ),
                        analysis,
                    )
        elif (
            gemini_active
            and gemini_sig
            and gemini_sig.get("action") == "hold"
            and gemini_sig.get("confidence", 0) >= 7
            and profit_pct >= ROTATE_LOSS_PCT
        ):
            decisions.append(
                {
                    "type": "hold",
                    "symbol": symbol,
                    "reason": _action_reason(analysis, "Gemini: pidä positio"),
                    "analysis": analysis,
                }
            )
        elif (
            _bear_defense_active(regime_info)
            and profit_pct < 0
        ):
            decisions.append(
                {
                    "type": "hold",
                    "symbol": symbol,
                    "reason": (
                        f"Karhu-puolustus — ei rotaatiota tappiolla ({profit_pct:+.1f} %), "
                        f"odotetaan stop-lossia"
                    ),
                    "analysis": analysis,
                }
            )
        elif (
            rotation_enabled
            and profit_pct < ROTATE_LOSS_PCT
            and (symbol not in top_symbols or change_24h < -2)
        ):
            # 1: etuviisas — älä rotatoi ilman selvää etua (vältä turha noise-churn ja
            # turha veron realisointi); poikkeus: selvästi heikkenevä positio < -2 %.
            if change_24h < -2 or _rotation_worthwhile(analysis, best_target_edge):
                sell_amount = _rotation_sell_amount(
                    holding["amount"], profit_pct, rotation_trim
                )
                _append_sell_decision(
                    decisions,
                    symbol,
                    sell_amount,
                    analysis["currentPrice"],
                    f"Tappiolla {profit_pct:.1f} % — myydään osa ja siirretään vahvempaan",
                    analysis,
                )
            else:
                decisions.append(
                    {
                        "type": "hold",
                        "symbol": symbol,
                        "reason": (
                            f"Ei rotaatiota ({profit_pct:.1f} %) — kohteella ei selvää "
                            f"etua, pidetään"
                        ),
                        "analysis": analysis,
                    }
                )
        elif rotation_enabled and analysis["action"] == "sell":
            if _bear_defense_active(regime_info) and profit_pct < 0:
                decisions.append(
                    {
                        "type": "hold",
                        "symbol": symbol,
                        "reason": (
                            f"Karhu-puolustus — ei teknistä rotaatiota tappiolla "
                            f"({profit_pct:+.1f} %)"
                        ),
                        "analysis": analysis,
                    }
                )
            elif not _rotation_trim_allowed(profit_pct, defense_regime):
                decisions.append(
                    {
                        "type": "hold",
                        "symbol": symbol,
                        "reason": (
                            f"Tekninen myynti ({profit_pct:+.1f} %) — lievä tappio, "
                            f"odotetaan stop-lossia (≥{stagnant_min_loss:.1f} %)"
                        ),
                        "analysis": analysis,
                    }
                )
            else:
                sell_amount = _rotation_sell_amount(
                    holding["amount"], profit_pct, rotation_trim
                )
                _append_sell_decision(
                    decisions,
                    symbol,
                    sell_amount,
                    analysis["currentPrice"],
                    "; ".join(analysis["reasons"]),
                    analysis,
                )
        elif not rotation_enabled and profit_pct < ROTATE_LOSS_PCT:
            decisions.append(
                {
                    "type": "hold",
                    "symbol": symbol,
                    "reason": (
                        "Oppiminen: rotaatio tuottanut tappiota — pidetään ja "
                        "annetaan teknisen stopin hoitaa"
                    ),
                    "analysis": analysis,
                }
            )
        elif analysis["action"] == "hold":
            decisions.append(
                {
                    "type": "hold",
                    "symbol": symbol,
                    "reason": "Pidetään — odotetaan parempaa signaalia",
                    "analysis": analysis,
                }
            )

    alloc_symbols = list(
        dict.fromkeys([c["symbol"] for c in top_cryptos] + desired)
    )[:position_cap]
    idle_cash = _is_idle_cash(cash, total_value)
    if idle_cash and not _bear_cash_deploy_ok(cash, total_value, regime_info):
        idle_cash = False
    if idle_cash:
        _release_idle_dust_holdings(
            decisions, holdings, analyses, blocked_buys=blocked_buys
        )
        idle_symbols = _symbols_for_idle_deploy(
            ranked_buyable,
            position_cap,
            blocked_buys=blocked_buys,
            blocked_setups=blocked_setups,
            regime=regime,
            gemini_insights=gemini_insights,
            gemini_active=gemini_active,
            gemini_conf_scales=gemini_conf_scales,
        )
        if idle_symbols:
            focus_buyable = [
                normalize_symbol(s)
                for s in alloc_symbols
                if entry_eligible(analyses.get(s))
                and not buy_blocked(s, analyses.get(s))
            ]
            if len(focus_buyable) < 1:
                concentration_mode = False
            alloc_symbols = idle_symbols
            top_symbols = set(alloc_symbols)
            top_norms = {normalize_symbol(s) for s in top_symbols}

    weights = _compute_allocation_weights(
        gemini_insights, alloc_symbols, analyses, gemini_active
    )
    if not (concentration_mode and len(alloc_symbols) <= CONCENTRATION_MAX_POSITIONS):
        weights = diversify_weights(weights, analyses)

    skip_sell_symbols = {d["symbol"] for d in decisions if d.get("type") == "hold"}

    bull_satellite_split = None
    if _is_bull_regime_phase(regime, regime_info):
        from .bull_satellite import evaluate_bull_satellite_split

        cash_estimate = max(0.0, cash - CASH_BUFFER_EUR) * effective_buy_scale
        bull_satellite_split = evaluate_bull_satellite_split(
            regime=regime,
            regime_info=regime_info,
            holdings=holdings,
            analyses=analyses,
            total_value=total_value,
            available_cash=cash_estimate,
            gemini_insights=gemini_insights,
            gemini_active=gemini_active,
            ranked_buyable=ranked_buyable,
            buy_blocked=buy_blocked,
            entry_score_min=entry_score_min,
        )
        if bull_satellite_split:
            alloc_symbols = list(
                dict.fromkeys([bull_satellite_split["primary"], bull_satellite_split["satellite"]])
            )
            top_symbols = set(alloc_symbols)
            top_norms = {normalize_symbol(s) for s in top_symbols}
            logger.info(
                "Bull-satelliitti: %s + %s (%s)",
                bull_satellite_split["primary"],
                bull_satellite_split["satellite"],
                bull_satellite_split.get("reason"),
            )

    _apply_bear_cash_reserve_trim(
        decisions,
        holdings,
        analyses,
        cash,
        total_value,
        regime_info,
        label_fn,
        preferred_symbols=top_norms,
    )

    if not churn_cooldown or concentration_mode or idle_cash:
        _deploy_cash_to_targets(
            decisions,
            holdings,
            cash,
            total_value,
            weights,
            alloc_symbols,
            analyses,
            label_fn,
            gemini_active,
            skip_sell_symbols,
            blocked_buys,
            best_target_edge,
            concentration_mode=concentration_mode,
            concentration_trim=concentration_trim,
            blocked_setups=blocked_setups,
            regime=regime,
            regime_info=regime_info,
            buy_scale=effective_buy_scale,
            gemini_insights=gemini_insights,
            gemini_conf_scales=gemini_conf_scales,
            gemini_buy_min_confidence=gemini_buy_min_conf,
            bull_satellite_split=bull_satellite_split,
        )

    for d in decisions:
        if d["type"] == "buy" and is_stablecoin(d["symbol"]):
            d["type"] = "hold"
            d["reason"] = "Stablecoin — ei osteta"

    return {
        "decisions": decisions,
        "targetCount": len(top_cryptos) or target_count,
        "topSymbols": list(top_symbols),
        "geminiActive": gemini_active,
        "concentrationMode": concentration_mode,
        "idleCashDeploy": idle_cash,
        "positionCap": position_cap,
        "bullSatellite": bull_satellite_split,
    }


def format_initial_buy_reason(
    analysis: dict[str, Any],
    label: str,
    index: int,
    total: int,
    gemini_active: bool,
    alloc_pct: float | None = None,
    eur_amount: float | None = None,
) -> str:
    if gemini_active and _gemini_reason(analysis):
        return _format_trade_reason(
            analysis,
            gemini_active=True,
            fallback=f"Gemini: avaa salkku — {label}",
            alloc_pct=alloc_pct,
            eur_amount=eur_amount,
        )
    fallback = (
        f"Gemini: avaa salkku — {label} ({index}/{total})"
        if gemini_active
        else f"Alkuallokaatio — {label} ({index}/{total})"
    )
    return _format_trade_reason(
        analysis,
        gemini_active=gemini_active,
        fallback=fallback,
        alloc_pct=alloc_pct,
        eur_amount=eur_amount,
    )


def apply_gemini_insights(
    analyses: dict[str, dict[str, Any]],
    insights: dict[str, Any] | None,
    *,
    gemini_buy_min_confidence: int | None = None,
) -> None:
    if not insights:
        return

    min_conf = (
        GEMINI_BUY_MIN_CONFIDENCE
        if gemini_buy_min_confidence is None
        else int(gemini_buy_min_confidence)
    )

    for symbol in insights.get("top_picks") or []:
        symbol = normalize_symbol(symbol)
        if symbol not in analyses or is_stablecoin(symbol):
            continue
        sig = _gemini_signal_for(insights, symbol)
        conf = int(sig.get("confidence", 0)) if sig else 0
        if conf < min_conf or (sig and sig.get("action") == "sell"):
            continue
        analyses[symbol]["score"] = analyses[symbol].get("score", 0) + 4
        analyses[symbol]["reasons"] = ["Gemini: top-valinta"] + analyses[symbol].get(
            "reasons", []
        )
        analyses[symbol]["geminiPick"] = True

    for symbol, signal in (insights.get("signals") or {}).items():
        symbol = normalize_symbol(symbol)
        if symbol not in analyses:
            continue
        analysis = analyses[symbol]
        action = signal.get("action", "hold")
        confidence = int(signal.get("confidence", 5))
        reason = signal.get("reason", "")

        if confidence >= min_conf:
            analysis["score"] = analysis.get("score", 0) + (confidence - 5) + (
                2 if action == "buy" else 0
            )
        elif action == "buy":
            analysis["score"] = analysis.get("score", 0) - 2

        if confidence >= min_conf:
            if action == "buy":
                analysis["action"] = "buy"
            elif confidence >= 6 and action == "sell":
                analysis["action"] = "sell"
        elif confidence >= 5 and action == "sell":
            analysis["action"] = "sell"

        if reason:
            analysis["reasons"] = [f"Gemini ({confidence}/10): {reason}"] + analysis.get(
                "reasons", []
            )
        analysis["gemini"] = True
        analysis["geminiSignal"] = {
            "action": action,
            "confidence": confidence,
            "reason": reason,
        }
        if signal.get("alloc_pct") is not None:
            analysis["geminiSignal"]["alloc_pct"] = signal["alloc_pct"]
            analysis["geminiAllocPct"] = signal["alloc_pct"]

    for symbol, pct in (insights.get("allocations") or {}).items():
        symbol = normalize_symbol(symbol)
        if symbol in analyses:
            analyses[symbol]["geminiAllocPct"] = pct


def build_decision_report(
    decisions: list[dict[str, Any]],
    label_fn: Callable[[str], str],
    gemini_active: bool = False,
) -> dict[str, Any]:
    buys = [d for d in decisions if d["type"] == "buy"]
    sells = [d for d in decisions if d["type"] == "sell"]
    holds = [d for d in decisions if d["type"] == "hold"]

    title = "Gemini-analyysi valmis" if gemini_active else "AI-analyysi valmis"
    subtitle = f"{len(buys)} ostoa · {len(sells)} myyntiä · {len(holds)} pidossa"

    if buys and sells:
        title = "Gemini: ostoja ja myyntejä" if gemini_active else "Ostoja ja myyntejä"
    elif buys:
        title = f"Gemini: {len(buys)} ostoa" if gemini_active else f"Ostetaan {len(buys)} kryptoa"
    elif sells:
        title = f"Gemini: {len(sells)} myyntiä" if gemini_active else f"Myydään {len(sells)} kryptoa"
    elif holds:
        title = "Gemini: pidetään positioita" if gemini_active else "Pidetään positioita"
        subtitle = "Ei uusia kauppoja tällä kierroksella"
    else:
        title = "Ei toimenpiteitä"
        subtitle = "Odotetaan parempaa signaalia"

    if buys and sells:
        action = "mixed"
    elif buys:
        action = "buy"
    elif sells:
        action = "sell"
    else:
        action = "hold"

    return {
        "action": action,
        "title": title,
        "subtitle": subtitle,
        "buys": [
            {
                "symbol": label_fn(b["symbol"]),
                "amount": b.get("eurAmount"),
                "reason": b["reason"],
                "analysis": b.get("analysis"),
            }
            for b in buys
        ],
        "sells": [
            {
                "symbol": label_fn(s["symbol"]),
                "amount": s.get("eurAmount"),
                "reason": s["reason"],
                "analysis": s.get("analysis"),
            }
            for s in sells
        ],
        "holds": [
            {
                "symbol": label_fn(h["symbol"]),
                "reason": h["reason"],
                "analysis": h.get("analysis"),
            }
            for h in holds
        ],
    }
