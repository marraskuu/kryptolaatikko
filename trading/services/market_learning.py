"""
Koko markkinan varjo-oppiminen (signaalit → toteutunut tuotto).

Idea: arvioi botin omat tekniset olosuhteet KAIKILLE kryptoille ja mittaa mitä
hinnalle oikeasti tapahtui 1 h ja 4 h kuluttua — vaikka kolikkoa ei ostettu.
Näin opitaan turvallisesti (ilman pääoman riskiä) mitkä asetelmat tuottavat,
ja vältetään systemaattisesti häviävät asetelmat.

Kevyt toteutus:
  - näyte joka 5 min per kierros (ei joka 60 s), vain riittävän likvideille
  - rikkaat olosuhdehaarukat (regiimi × 24h × MTF × RSI × volyymi × deep/quick)
  - fallback karkeampaan avaimeen jos otos pieni
  - rullaava unohdus (decay) sopeutuu markkinan muutokseen
  - tallennus erilliseen riviin (BotState pk=2), ettei 15 s hintapäivitys raskaudu
"""

from __future__ import annotations

import time
from typing import Any

from .bitfinex import is_stablecoin
from .market_microstructure import book_bucket, crowd_bucket, flow_bucket

HORIZONS = {"1h": 3600, "4h": 14400}
MAX_HORIZON_SEC = 14400
SAMPLE_INTERVAL_SEC = 300       # näyte per kolikko korkeintaan 5 min välein
EVAL_SLACK_SEC = 1800           # poista havainto viimeistään max-horisontti + 30 min
MIN_VOLUME_EUR = 200_000        # linjassa MIN_ENTRY_VOLUME_EUR (ai_trader)
MIN_SAMPLES = 40                # täysi paino rikkaimmalle avaimelle
MIN_SAMPLES_LIGHT = 15          # kevyt painotus karkeammalle avaimelle
MAX_OBS = 8000                  # kova katto odottaville havainnoille
BUCKET_CAP_N = 600              # ämpärin otoskatto → vanha unohtuu (recency)
DECAY = 0.8
ROUND_TRIP_COST_PCT = 0.0       # Bitfinex: ei kaupankäyntikuluja
MAX_SCORE_ADJUST = 4.0         # C: vahvempi vaikutus — nojaa voittaviin asetelmiin
W_1H = 0.6
W_4H = 0.4
EXP_TO_SCORE = 2.0             # C: +1 % opittu odotus → +2.0 score (clampattu)
BLOCK_EXP_PCT = -0.35            # varjo-oppiminen: alle tämän % → estä osto
BLOCK_EXP_PCT_LIGHT = -0.55      # tiukempi kynnys pienemmällä otoksella

_DEFAULT = {"obs": [], "stats": {}, "lastSample": 0}


def _load() -> dict[str, Any]:
    from trading.models import BotState

    obj, _ = BotState.objects.get_or_create(pk=2, defaults={"data": dict(_DEFAULT)})
    data = obj.data or {}
    data.setdefault("obs", [])
    data.setdefault("stats", {})
    data.setdefault("lastSample", 0)
    return data


def _save(data: dict[str, Any]) -> None:
    from trading.models import BotState

    BotState.objects.update_or_create(pk=2, defaults={"data": data})


def _change_bucket(c: float) -> str:
    if c < -5:
        return "d5"
    if c < -2:
        return "d2"
    if c < 0:
        return "d0"
    if c < 2:
        return "u0"
    if c < 5:
        return "u2"
    if c < 10:
        return "u5"
    return "u10"


def _rsi_bucket(rsi: float | None) -> str:
    if rsi is None:
        return "rsi_md"
    r = float(rsi)
    if r < 35:
        return "rsi_lo"
    if r < 50:
        return "rsi_md"
    if r < 65:
        return "rsi_hi"
    return "rsi_ob"


def _vol_bucket(volume_eur: float) -> str:
    if volume_eur >= 2_000_000:
        return "vol_xl"
    if volume_eur >= 500_000:
        return "vol_lg"
    if volume_eur >= 250_000:
        return "vol_md"
    if volume_eur >= MIN_VOLUME_EUR:
        return "vol_sm"
    return "vol_xs"


def _mtf_token(mtf: Any) -> str:
    if mtf is None:
        return "mtf0"
    m = int(mtf)
    if m > 0:
        return "mtf+"
    if m < 0:
        return "mtf-"
    return "mtf0"


def _regime_str(regime: Any) -> str:
    if isinstance(regime, str):
        return regime
    return (regime or {}).get("regime", "neutral")


def _change_for_analysis(analysis: dict[str, Any]) -> float:
    change = analysis.get("changePct")
    if change is None:
        change = analysis.get("momentum") or 0
    return float(change)


def _bucket_key_parts(analysis: dict[str, Any], regime: str) -> dict[str, str]:
    return {
        "regime": regime,
        "change": _change_bucket(_change_for_analysis(analysis)),
        "mtf": _mtf_token(analysis.get("mtfAlign")),
        "rsi": _rsi_bucket(analysis.get("rsi")),
        "vol": _vol_bucket(float(analysis.get("volumeEur") or 0)),
        "deep": "deep" if not analysis.get("quick", True) else "quick",
        "book": analysis.get("bookBucket") or book_bucket(analysis.get("bookImbalance")),
        "crowd": analysis.get("crowdBucket") or crowd_bucket(analysis.get("longShortRatio")),
        "flow": analysis.get("flowBucket") or flow_bucket(analysis.get("flowImbalance")),
    }


def _bucket_keys_fallback(analysis: dict[str, Any], regime: Any) -> list[str]:
    """Richest → coarsest — ensimmäinen riittävällä otoksella voittaa."""
    p = _bucket_key_parts(analysis, _regime_str(regime))
    return [
        f"{p['regime']}|{p['change']}|{p['mtf']}|{p['rsi']}|{p['vol']}|{p['deep']}|{p['book']}|{p['crowd']}|{p['flow']}",
        f"{p['regime']}|{p['change']}|{p['mtf']}|{p['rsi']}|{p['vol']}|{p['deep']}|{p['book']}|{p['crowd']}",
        f"{p['regime']}|{p['change']}|{p['mtf']}|{p['rsi']}|{p['vol']}|{p['deep']}|{p['book']}",
        f"{p['regime']}|{p['change']}|{p['mtf']}|{p['rsi']}|{p['vol']}|{p['deep']}",
        f"{p['regime']}|{p['change']}|{p['mtf']}|{p['rsi']}|{p['vol']}",
        f"{p['regime']}|{p['change']}|{p['mtf']}|{p['rsi']}",
        f"{p['regime']}|{p['change']}|{p['mtf']}",
        f"{p['regime']}|{p['change']}",
    ]


def setup_key_for_analysis(analysis: dict[str, Any], regime: Any) -> str:
    """Julkinen asetelma-avain — rikkain muoto (sama kuin varjo-oppimisessa)."""
    return _bucket_keys_fallback(analysis, regime)[0]


def _bucket_key(analysis: dict[str, Any], regime: str) -> str:
    """Näytteenotto käyttää rikkainta avainta (fallback apply-vaiheessa)."""
    return setup_key_for_analysis(analysis, regime)


def _update_stat(stats: dict[str, Any], key: str, horizon: str, ret: float) -> None:
    st = stats.setdefault(key, {})
    h = st.setdefault(horizon, {"n": 0.0, "sum": 0.0, "w": 0.0})
    h["n"] += 1.0
    h["sum"] += ret
    if ret > 0:
        h["w"] += 1.0
    if h["n"] > BUCKET_CAP_N:
        h["n"] *= DECAY
        h["sum"] *= DECAY
        h["w"] *= DECAY


def _horizon_expectancy(st: dict[str, Any]) -> float | None:
    num = 0.0
    den = 0.0
    for hz, w in (("1h", W_1H), ("4h", W_4H)):
        h = st.get(hz)
        if h and h.get("n", 0) >= MIN_SAMPLES_LIGHT:
            num += (h["sum"] / h["n"]) * w
            den += w
    if den <= 0:
        return None
    return num / den


def _bucket_sample_count(st: dict[str, Any]) -> float:
    return max(
        (st.get(hz, {}).get("n", 0) for hz in HORIZONS),
        default=0.0,
    )


def step(
    tickers: dict[str, dict[str, Any]],
    analyses: dict[str, dict[str, Any]],
    regime: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Arvioi odottavat havainnot, ota uusi näyte, palauta tilastot + yhteenveto."""
    store = _load()
    now = int(time.time() * 1000)
    obs: list[dict[str, Any]] = store["obs"]
    stats: dict[str, Any] = store["stats"]

    survivors: list[dict[str, Any]] = []
    for o in obs:
        tk = tickers.get(o.get("s"))
        price_now = tk.get("last") if tk else None
        age = (now - o.get("t", now)) / 1000.0
        done = o.get("d") or {}
        if price_now and o.get("p", 0) > 0:
            for hz, sec in HORIZONS.items():
                if not done.get(hz) and age >= sec:
                    ret = (price_now - o["p"]) / o["p"] * 100.0 - ROUND_TRIP_COST_PCT
                    _update_stat(stats, o["k"], hz, ret)
                    done[hz] = 1
            o["d"] = done
        if all(done.get(hz) for hz in HORIZONS) or age > (MAX_HORIZON_SEC + EVAL_SLACK_SEC):
            continue
        survivors.append(o)
    obs = survivors

    if now - int(store.get("lastSample", 0)) >= SAMPLE_INTERVAL_SEC * 1000:
        reg = _regime_str(regime)
        for sym, tk in tickers.items():
            if is_stablecoin(sym):
                continue
            last = tk.get("last", 0)
            if last <= 0 or (tk.get("volumeEur") or 0) < MIN_VOLUME_EUR:
                continue
            analysis = dict(analyses.get(sym) or {})
            if "volumeEur" not in analysis:
                analysis["volumeEur"] = tk.get("volumeEur", 0)
            if analysis.get("changePct") is None:
                analysis["changePct"] = tk.get("changePct")
            obs.append({"s": sym, "t": now, "p": last, "k": _bucket_key(analysis, reg), "d": {}})
        store["lastSample"] = now
        if len(obs) > MAX_OBS:
            obs = obs[-MAX_OBS:]

    store["obs"] = obs
    store["stats"] = stats
    _save(store)
    summary = _summary(stats)
    summary["obsPending"] = len(obs)
    summary["lastSampleAgeSec"] = int((now - int(store.get("lastSample", 0))) / 1000)
    return stats, summary


def condition_blocks_entry(analysis: dict[str, Any], regime: Any, stats: dict[str, Any]) -> bool:
    """Estä osto jos varjo-oppiminen näyttää selvästi tappiollisen asetelman."""
    for key in _bucket_keys_fallback(analysis, regime):
        st = stats.get(key)
        if not st:
            continue
        exp = _horizon_expectancy(st)
        if exp is None:
            continue
        n = _bucket_sample_count(st)
        if n >= MIN_SAMPLES and exp < BLOCK_EXP_PCT:
            return True
        if n >= MIN_SAMPLES_LIGHT and exp < BLOCK_EXP_PCT_LIGHT:
            return True
    return False


def condition_adjust(analysis: dict[str, Any], regime: Any, stats: dict[str, Any]) -> float:
    """Opittu score-säätö — fallback karkeampaan avaimeen jos otos pieni."""
    for key in _bucket_keys_fallback(analysis, regime):
        st = stats.get(key)
        if not st:
            continue
        exp = _horizon_expectancy(st)
        if exp is None:
            continue
        n = _bucket_sample_count(st)
        scale = 1.0 if n >= MIN_SAMPLES else max(0.35, n / MIN_SAMPLES)
        adj = exp * EXP_TO_SCORE * scale
        return max(-MAX_SCORE_ADJUST, min(MAX_SCORE_ADJUST, adj))
    return 0.0


def apply(analyses: dict[str, dict[str, Any]], regime: Any, stats: dict[str, Any]) -> None:
    """Liitä opittu olosuhdesäätö ja esto jokaiseen analyysiin (condAdjust / condBlocked)."""
    for sym, analysis in analyses.items():
        if is_stablecoin(sym):
            continue
        analysis["condBlocked"] = condition_blocks_entry(analysis, regime, stats)
        analysis["condAdjust"] = round(condition_adjust(analysis, regime, stats), 2)


def _summary(stats: dict[str, Any]) -> dict[str, Any]:
    ready: list[tuple[str, float, float]] = []
    for key, st in stats.items():
        exp = _horizon_expectancy(st)
        if exp is not None and _bucket_sample_count(st) >= MIN_SAMPLES_LIGHT:
            ready.append((key, exp, _bucket_sample_count(st)))
    ready.sort(key=lambda x: x[1])
    best = ready[-1] if ready else None
    worst = ready[0] if ready else None
    return {
        "bucketsLearned": len(ready),
        "bucketsTracked": len(stats),
        "best": {"setup": best[0], "exp1h": round(best[1], 3), "n": int(best[2])} if best else None,
        "worst": {"setup": worst[0], "exp1h": round(worst[1], 3), "n": int(worst[2])} if worst else None,
    }
