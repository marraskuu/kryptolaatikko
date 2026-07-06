"""Kauppakirjauksen meta: sisäänostokonteksti oppimista varten."""

from __future__ import annotations

import re
from typing import Any

from .exit_learning import exit_setup_key_for_analysis
from .market_learning import setup_key_for_analysis

_GEMINI_CONF_RE = re.compile(r"Gemini\s*\((\d+)/10\)", re.I)


def _regime_phase_meta(regime: str, regime_info: dict[str, Any] | None) -> dict[str, Any]:
    """Tallenna regiimin vaihe myynti-/ostometadataan."""
    if not regime_info:
        return {}
    phase = str(regime_info.get("phase") or regime_info.get("regime") or regime)
    official = str(regime_info.get("regime") or regime)
    extra: dict[str, Any] = {}
    if phase:
        extra["regimePhase"] = phase
    shift = regime_info.get("shift_to")
    if shift:
        extra["shiftTo"] = shift
    strength = regime_info.get("shift_strength")
    if strength:
        extra["shiftStrength"] = strength
    if phase != official or phase.endswith("_entering") or phase.endswith("_emerging"):
        extra["anticipated"] = True
    return extra


def meta_from_analysis(
    analysis: dict[str, Any] | None,
    regime: str,
    *,
    regime_info: dict[str, Any] | None = None,
    for_sell: bool = False,
    profit_pct: float | None = None,
    peak_price: float | None = None,
    pullback_pct: float | None = None,
) -> dict[str, Any]:
    """Rakenna kauppakirjaukseen tallennettava meta analyysistä."""
    meta: dict[str, Any] = {"regime": regime}
    meta.update(_regime_phase_meta(regime, regime_info))
    if not analysis:
        if for_sell:
            if profit_pct is not None:
                meta["profitPctAtSell"] = round(float(profit_pct), 2)
            if peak_price is not None and peak_price > 0:
                meta["peakPriceAtSell"] = round(float(peak_price), 6)
            if pullback_pct is not None:
                meta["givebackPct"] = round(float(pullback_pct), 3)
            if profit_pct is not None:
                meta["exitSetup"] = exit_setup_key_for_analysis(None, regime, profit_pct)
        return {k: v for k, v in meta.items() if v is not None and v != ""}

    if analysis.get("atrPct") is not None:
        meta["atrPct"] = round(float(analysis["atrPct"]), 3)

    if not for_sell:
        if analysis.get("score") is not None:
            meta["score"] = int(analysis["score"])
        if analysis.get("rsi") is not None:
            meta["rsi"] = round(float(analysis["rsi"]), 1)
        if analysis.get("mtfAlign") is not None:
            meta["mtfAlign"] = int(analysis["mtfAlign"])
        if analysis.get("condAdjust") is not None:
            meta["condAdjust"] = round(float(analysis["condAdjust"]), 2)
        for key in ("change1hPct", "change4hPct", "changePct"):
            if analysis.get(key) is not None:
                meta[key] = round(float(analysis[key]), 2)
        for key in ("bookImbalance", "bookSpreadPct", "longShortRatio", "flowImbalance"):
            if analysis.get(key) is not None:
                meta[key] = round(float(analysis[key]), 4)
        if analysis.get("bookBucket"):
            meta["bookBucket"] = analysis["bookBucket"]
        if analysis.get("crowdBucket"):
            meta["crowdBucket"] = analysis["crowdBucket"]
        if analysis.get("flowBucket"):
            meta["flowBucket"] = analysis["flowBucket"]
        meta["setup"] = setup_key_for_analysis(analysis, regime)
        sig = analysis.get("geminiSignal") or {}
        if sig.get("confidence") is not None and sig.get("action") == "buy":
            meta["geminiConfidence"] = int(sig["confidence"])
        if analysis.get("geminiPick"):
            meta["geminiPick"] = True
        if "geminiConfidence" not in meta and (
            analysis.get("geminiPick") or analysis.get("gemini")
        ):
            if sig.get("confidence") is not None:
                meta["geminiConfidence"] = int(sig["confidence"])
        if "geminiConfidence" not in meta:
            for reason in analysis.get("reasons") or []:
                match = _GEMINI_CONF_RE.search(str(reason))
                if match and "gemini" in str(reason).lower():
                    meta["geminiConfidence"] = int(match.group(1))
                    break
    else:
        if profit_pct is not None:
            meta["profitPctAtSell"] = round(float(profit_pct), 2)
        if peak_price is not None and peak_price > 0:
            meta["peakPriceAtSell"] = round(float(peak_price), 6)
        if pullback_pct is not None:
            meta["givebackPct"] = round(float(pullback_pct), 3)
        if analysis:
            if analysis.get("rsi") is not None:
                meta["rsi"] = round(float(analysis["rsi"]), 1)
            if analysis.get("mtfAlign") is not None:
                meta["mtfAlign"] = int(analysis["mtfAlign"])
            for key in ("bookBucket", "crowdBucket", "flowBucket"):
                if analysis.get(key):
                    meta[key] = analysis[key]
            if profit_pct is not None:
                meta["exitSetup"] = exit_setup_key_for_analysis(analysis, regime, profit_pct)
        sig = (analysis.get("geminiSignal") or {}) if analysis else {}
        if sig.get("confidence") is not None and sig.get("action") == "sell":
            meta["geminiConfidence"] = int(sig["confidence"])
        if "geminiConfidence" not in meta:
            for reason in analysis.get("reasons") or []:
                match = _GEMINI_CONF_RE.search(str(reason))
                if match:
                    meta["geminiConfidence"] = int(match.group(1))
                    break

    return {k: v for k, v in meta.items() if v is not None and v != ""}


def entry_meta_from_trade(trade: dict[str, Any]) -> dict[str, Any]:
    """Palauta sisäänoston meta ostotapahtumasta."""
    keys = (
        "regime",
        "setup",
        "score",
        "rsi",
        "mtfAlign",
        "atrPct",
        "condAdjust",
        "change1hPct",
        "change4hPct",
        "bookImbalance",
        "bookSpreadPct",
        "longShortRatio",
        "flowImbalance",
        "bookBucket",
        "crowdBucket",
        "flowBucket",
        "geminiConfidence",
        "geminiPick",
    )
    meta = {k: trade[k] for k in keys if trade.get(k) is not None}
    if "geminiConfidence" not in meta and "gemini" in (trade.get("reason") or "").lower():
        match = _GEMINI_CONF_RE.search(trade.get("reason") or "")
        if match:
            meta["geminiConfidence"] = int(match.group(1))
    return meta
