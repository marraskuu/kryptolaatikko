"""
scikit-learn-malli setup-backfill-datan (setup_historical_backfill.py) päälle.

Kouluttaa HistGradientBoostingClassifierin ennustamaan kaupan onnistumistodennäköisyyttä
rivikohtaisista feature-näytteistä (BotState pk=3 "samples"). Malli tallennetaan
BotState pk=5:een (joblib+base64, samaan tapaan kuin muutkin oppimismoduulit käyttävät
omaa BotState-riviään pk=1/2/3/4:n rinnalla).

Ei kutsuta live-kauppasyklistä (engine.py) tässä vaiheessa — puhtaasti offline-koulutus
ja valmius myöhempää shadow-integraatiota varten (predict_win_probability).
"""

from __future__ import annotations

import base64
import io
import logging
import math
import os
import time
from typing import Any

import joblib
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import accuracy_score, roc_auc_score
from sklearn.model_selection import train_test_split

from .setup_historical_backfill import load_setup_samples

logger = logging.getLogger(__name__)

FEATURE_NAMES: list[str] = [
    "rsi",
    "emaSpreadPct",
    "momentum",
    "score",
    "changePct",
    "change1hPct",
    "change4hPct",
    "mtfAlign",
    "atrPct",
    "volumeEurLog",
    "regime_bull",
    "regime_bear",
]

# Live-kytkentä pois päältä oletuksena — riskialtein neljästä 2026-07-27 muutoksesta
# (poikkeaa tietoisesti muiden lippujen oletus-päällä-käytännöstä), vaatii ensin
# manage.py train_setup_model:in ajamisen.
SETUP_MODEL_LIVE_ENABLED = os.environ.get("SETUP_MODEL_LIVE_ENABLED", "0").strip().lower() not in (
    "0", "false", "no",
)
SETUP_MODEL_ADJUST_WEIGHT = float(os.environ.get("SETUP_MODEL_ADJUST_WEIGHT", "6.0"))
# Neljäsosa market_learning.MAX_SCORE_ADJUST:sta (4.0) — tarkoituksella maltillinen
# kunnes mallin live-kalibrointi on vahvistettu.
SETUP_MODEL_MAX_ADJUST = float(os.environ.get("SETUP_MODEL_MAX_ADJUST", "1.0"))
SETUP_MODEL_MIN_AUC = float(os.environ.get("SETUP_MODEL_MIN_AUC", "0.58"))

_DEFAULT: dict[str, Any] = {"model": None, "meta": None}

# Module-level malli-välimuisti — vältetään joblib-deserialisointi joka kutsulla.
_cache: dict[str, Any] = {"trainedAt": None, "model": None, "meta": None}


def _load() -> dict[str, Any]:
    from trading.models import BotState

    obj, _ = BotState.objects.get_or_create(pk=5, defaults={"data": dict(_DEFAULT)})
    data = obj.data or {}
    data.setdefault("model", None)
    data.setdefault("meta", None)
    return data


def _save(data: dict[str, Any]) -> None:
    from trading.models import BotState

    BotState.objects.update_or_create(pk=5, defaults={"data": data})


def _to_float(value: Any) -> float:
    if value is None:
        return float("nan")
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def _ema_spread_pct(row: dict[str, Any]) -> float:
    """Sama kaava kuin setup_historical_backfill._sample_from_analysis().

    Koulutusnäytteillä on jo oma emaSpreadPct-avain (käytetään suoraan) — tämä
    fallback on live-analyysejä varten, joissa on vain ema9/ema21.
    """
    if "emaSpreadPct" in row:
        return _to_float(row.get("emaSpreadPct"))
    ema9 = row.get("ema9")
    ema21 = row.get("ema21")
    if ema9 is None or ema21 in (None, 0):
        return float("nan")
    try:
        return (float(ema9) - float(ema21)) / float(ema21) * 100.0
    except (TypeError, ValueError, ZeroDivisionError):
        return float("nan")


def _row_to_features(row: dict[str, Any]) -> dict[str, float]:
    volume_eur = _to_float(row.get("volumeEur"))
    volume_eur_log = math.log1p(volume_eur) if volume_eur > 0 else float("nan")
    regime = row.get("regime") or "neutral"
    return {
        "rsi": _to_float(row.get("rsi")),
        "emaSpreadPct": _ema_spread_pct(row),
        "momentum": _to_float(row.get("momentum")),
        "score": _to_float(row.get("score")),
        "changePct": _to_float(row.get("changePct")),
        "change1hPct": _to_float(row.get("change1hPct", 0.0)),
        "change4hPct": _to_float(row.get("change4hPct", 0.0)),
        "mtfAlign": _to_float(row.get("mtfAlign")),
        "atrPct": _to_float(row.get("atrPct")),
        "volumeEurLog": volume_eur_log,
        "regime_bull": 1.0 if regime == "bull" else 0.0,
        "regime_bear": 1.0 if regime == "bear" else 0.0,
    }


def build_training_frame() -> tuple[pd.DataFrame, pd.Series]:
    """Lataa raakanäytteet pk=3:sta ja muuntaa feature-DataFrameksi + label-Sarjaksi (win=1/0)."""
    samples = load_setup_samples()
    rows: list[dict[str, float]] = []
    labels: list[int] = []
    for s in samples:
        ret_pct = s.get("retPct")
        if ret_pct is None:
            continue
        rows.append(_row_to_features(s))
        labels.append(1 if float(ret_pct) > 0 else 0)
    X = pd.DataFrame(rows, columns=FEATURE_NAMES, dtype=float)
    y = pd.Series(labels, name="win", dtype=int)
    return X, y


def train_setup_model(min_samples: int = 200) -> dict[str, Any]:
    """Kouluttaa mallin ja tallentaa BotState pk=5:een. Palauttaa yhteenvedon (JSON-yhteensopiva)."""
    X, y = build_training_frame()
    n = len(X)
    if n < min_samples:
        return {
            "trained": False,
            "reason": f"liian vähän näytteitä ({n} < {min_samples})",
            "nSamples": n,
        }
    if y.nunique() < 2:
        return {
            "trained": False,
            "reason": "kaikki näytteet samassa luokassa (vain voittoja tai vain tappioita)",
            "nSamples": n,
        }

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    model = HistGradientBoostingClassifier(
        max_iter=150, max_leaf_nodes=15, learning_rate=0.08, random_state=42
    )
    model.fit(X_train, y_train)

    preds = model.predict(X_test)
    holdout_accuracy = float(accuracy_score(y_test, preds))
    holdout_auc: float | None = None
    if y_test.nunique() > 1:
        proba = model.predict_proba(X_test)[:, 1]
        holdout_auc = float(roc_auc_score(y_test, proba))

    buf = io.BytesIO()
    joblib.dump(model, buf)
    model_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    trained_at = int(time.time() * 1000)
    meta = {
        "trainedAt": trained_at,
        "nSamples": n,
        "nTrain": int(len(X_train)),
        "nTest": int(len(X_test)),
        "holdoutAccuracy": holdout_accuracy,
        "holdoutAuc": holdout_auc,
        "baselineWinRate": float(y.mean()),
        "featureNames": FEATURE_NAMES,
    }

    store = _load()
    store["model"] = model_b64
    store["meta"] = meta
    _save(store)

    _cache["trainedAt"] = trained_at
    _cache["model"] = model
    _cache["meta"] = meta

    return {"trained": True, **meta}


def load_model() -> tuple[Any, dict[str, Any]] | None:
    """Lataa+deserialisoi malli pk=5:stä. Välimuistitettu trainedAt-aikaleiman perusteella."""
    store = _load()
    meta = store.get("meta")
    model_b64 = store.get("model")
    if not meta or not model_b64:
        return None

    trained_at = meta.get("trainedAt")
    if _cache["model"] is not None and _cache["trainedAt"] == trained_at:
        return _cache["model"], _cache["meta"]

    try:
        raw = base64.b64decode(model_b64)
        model = joblib.load(io.BytesIO(raw))
    except Exception:
        logger.exception("Setup-mallin lataus epäonnistui")
        return None

    _cache["trainedAt"] = trained_at
    _cache["model"] = model
    _cache["meta"] = meta
    return model, meta


def predict_win_probability(analysis: dict[str, Any], regime: str) -> float | None:
    """Ennustaa voittotodennäköisyyden analyysille, tai None jos mallia ei ole/RSI puuttuu.

    Ei kutsuta live-koodipolusta tässä vaiheessa — valmiina myöhempää shadow-integraatiota
    varten (esim. engine.py:n build_deep_analysis-kutsun jälkeen, condBlocked/condAdjust-
    kenttien tapaan, ilman että se vaikuttaisi pisteytykseen).
    """
    loaded = load_model()
    if loaded is None:
        return None
    model, meta = loaded

    row = dict(analysis)
    row["regime"] = regime
    features = _row_to_features(row)
    if math.isnan(features["rsi"]):
        return None

    feature_names = meta.get("featureNames") or FEATURE_NAMES
    X = pd.DataFrame([[features[name] for name in feature_names]], columns=feature_names)
    try:
        return float(model.predict_proba(X)[0][1])
    except Exception:
        logger.exception("Setup-mallin ennuste epäonnistui")
        return None


def condition_adjust_from_model(
    analysis: dict[str, Any],
    regime: str,
    *,
    weight: float = SETUP_MODEL_ADJUST_WEIGHT,
    max_adjust: float = SETUP_MODEL_MAX_ADJUST,
    min_auc: float = SETUP_MODEL_MIN_AUC,
) -> tuple[float, float | None]:
    """Mallin score-säätö market_learning.condition_adjust():n rinnalle.

    Palauttaa (adjust, win_probability). adjust=0.0 jos malli puuttuu, sen
    holdout-AUC on alle min_auc:n (tuskin-parempi-kuin-arvaus-malli ei saa
    vaikuttaa pisteytykseen), tai ennuste ei onnistu (esim. RSI puuttuu).
    """
    loaded = load_model()
    if loaded is None:
        return 0.0, None
    _, meta = loaded
    holdout_auc = meta.get("holdoutAuc")
    if holdout_auc is not None and float(holdout_auc) < min_auc:
        return 0.0, None

    prob = predict_win_probability(analysis, regime)
    if prob is None:
        return 0.0, None

    adj = (prob - 0.5) * weight
    adj = max(-max_adjust, min(max_adjust, adj))
    return adj, prob


def apply(analyses: dict[str, dict[str, Any]], regime: Any) -> None:
    """Asettaa analysis['modelAdjust']/['modelWinProb'] jokaiselle symbolille.

    Peilaa market_learning.apply():n muotoa. Per-symboli-poikkeukset eivät
    kaada koko sykliä — kutsujan (engine.py) tulee silti kääriä oma
    try/except tämän kutsun ympärille samaan tapaan kuin market_learning.apply.
    Jos malli puuttuu tai holdout AUC < SETUP_MODEL_MIN_AUC, adjust on 0
    (condition_adjust_from_model) — live-kytkin ei pakota huonoa mallia.
    """
    loaded = load_model()
    if loaded is None:
        return
    _, meta = loaded
    holdout_auc = meta.get("holdoutAuc")
    if holdout_auc is None or float(holdout_auc) < SETUP_MODEL_MIN_AUC:
        logger.info(
            "Setup model live skip: AUC %s < min %s",
            holdout_auc,
            SETUP_MODEL_MIN_AUC,
        )
        return

    for analysis in analyses.values():
        try:
            adj, prob = condition_adjust_from_model(analysis, regime)
        except Exception:
            logger.warning("Setup model condition_adjust failed", exc_info=True)
            adj, prob = 0.0, None
        analysis["modelAdjust"] = round(adj, 2)
        if prob is not None:
            analysis["modelWinProb"] = round(prob, 3)


def get_setup_model_status() -> dict[str, Any]:
    """Yhteenveto mallin tilasta (admin-vastausta varten)."""
    store = _load()
    meta = store.get("meta") or {}
    return {
        "setupModelTrained": bool(meta),
        "setupModelTrainedAt": meta.get("trainedAt"),
        "setupModelHoldoutAccuracy": meta.get("holdoutAccuracy"),
        "setupModelHoldoutAuc": meta.get("holdoutAuc"),
        "setupModelBaselineWinRate": meta.get("baselineWinRate"),
        "setupModelNSamples": meta.get("nSamples"),
    }
