"""
Koko markkinan varjo-oppiminen (signaalit → toteutunut tuotto).

Idea: arvioi botin omat tekniset olosuhteet KAIKILLE kryptoille ja mittaa mitä
hinnalle oikeasti tapahtui 1 h ja 4 h kuluttua — vaikka kolikkoa ei ostettu.
Näin opitaan turvallisesti (ilman pääoman riskiä) mitkä asetelmat tuottavat,
ja vältetään systemaattisesti häviävät asetelmat.

Kevyt toteutus:
  - näyte joka 5 min per kierros (ei joka 60 s), vain riittävän likvideille
  - karkeat olosuhdehaarukat (regiimi × 24h-muutoshaarukka × score) → ~63 ämpäriä
  - rullaava unohdus (decay) sopeutuu markkinan muutokseen
  - tallennus erilliseen riviin (BotState pk=2), ettei 15 s hintapäivitys raskaudu
"""

from __future__ import annotations

import time
from typing import Any

from .bitfinex import is_stablecoin

HORIZONS = {"1h": 3600, "4h": 14400}
MAX_HORIZON_SEC = 14400
SAMPLE_INTERVAL_SEC = 300       # näyte per kolikko korkeintaan 5 min välein
EVAL_SLACK_SEC = 1800           # poista havainto viimeistään max-horisontti + 30 min
MIN_VOLUME_EUR = 50_000         # vain likvidit mukaan
MIN_SAMPLES = 40                # ämpäri vaikuttaa vasta kun otoskoko riittää
MAX_OBS = 8000                  # kova katto odottaville havainnoille
BUCKET_CAP_N = 600              # ämpärin otoskatto → vanha unohtuu (recency)
DECAY = 0.8
ROUND_TRIP_COST_PCT = 0.2       # 2 x 0.1 % — arvioidaan netto, ei brutto
MAX_SCORE_ADJUST = 3.0
W_1H = 0.6
W_4H = 0.4
EXP_TO_SCORE = 1.5              # +1 % opittu odotus → +1.5 score (clampattu)

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


def _bucket_key(analysis: dict[str, Any], regime: str) -> str:
    # Vakaat piirteet: regiimi (kiinteä kierroksella) + 24h-muutoshaarukka (ei muutu
    # Geminin/syväanalyysin myötä) → sama ämpäri näytteenotossa ja soveltamisessa.
    change = analysis.get("changePct")
    if change is None:
        change = analysis.get("momentum") or 0
    return f"{regime}|{_change_bucket(float(change))}"


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


def _regime_str(regime: Any) -> str:
    if isinstance(regime, str):
        return regime
    return (regime or {}).get("regime", "neutral")


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
            analysis = analyses.get(sym) or {"changePct": tk.get("changePct"), "score": 0}
            obs.append({"s": sym, "t": now, "p": last, "k": _bucket_key(analysis, reg), "d": {}})
        store["lastSample"] = now
        if len(obs) > MAX_OBS:
            obs = obs[-MAX_OBS:]

    store["obs"] = obs
    store["stats"] = stats
    _save(store)
    return stats, _summary(stats)


def condition_adjust(analysis: dict[str, Any], regime: Any, stats: dict[str, Any]) -> float:
    """Opittu score-säätö nykyiselle olosuhteelle (clampattu, vain riittävällä otoksella)."""
    key = _bucket_key(analysis, _regime_str(regime))
    st = stats.get(key)
    if not st:
        return 0.0
    num = 0.0
    den = 0.0
    for hz, w in (("1h", W_1H), ("4h", W_4H)):
        h = st.get(hz)
        if h and h.get("n", 0) >= MIN_SAMPLES:
            num += (h["sum"] / h["n"]) * w
            den += w
    if den <= 0:
        return 0.0
    exp = num / den
    return max(-MAX_SCORE_ADJUST, min(MAX_SCORE_ADJUST, exp * EXP_TO_SCORE))


def apply(analyses: dict[str, dict[str, Any]], regime: Any, stats: dict[str, Any]) -> None:
    """Liitä opittu olosuhdesäätö jokaiseen analyysiin (condAdjust)."""
    for sym, analysis in analyses.items():
        if is_stablecoin(sym):
            continue
        analysis["condAdjust"] = round(condition_adjust(analysis, regime, stats), 2)


def _summary(stats: dict[str, Any]) -> dict[str, Any]:
    ready: list[tuple[str, float, float]] = []
    for key, st in stats.items():
        h = st.get("1h")
        if h and h.get("n", 0) >= MIN_SAMPLES:
            ready.append((key, h["sum"] / h["n"], h["n"]))
    ready.sort(key=lambda x: x[1])
    best = ready[-1] if ready else None
    worst = ready[0] if ready else None
    return {
        "bucketsLearned": len(ready),
        "bucketsTracked": len(stats),
        "best": {"setup": best[0], "exp1h": round(best[1], 3), "n": int(best[2])} if best else None,
        "worst": {"setup": worst[0], "exp1h": round(worst[1], 3), "n": int(worst[2])} if worst else None,
    }
