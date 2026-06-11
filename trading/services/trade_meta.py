"""Kauppakirjauksen meta: sisäänostokonteksti oppimista varten."""

from __future__ import annotations

from typing import Any

from .market_learning import setup_key_for_analysis


def meta_from_analysis(
    analysis: dict[str, Any] | None,
    regime: str,
    *,
    for_sell: bool = False,
) -> dict[str, Any]:
    """Rakenna kauppakirjaukseen tallennettava meta analyysistä."""
    meta: dict[str, Any] = {"regime": regime}
    if not analysis:
        return meta

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
        meta["setup"] = setup_key_for_analysis(analysis, regime)
    else:
        sig = analysis.get("geminiSignal") or {}
        if sig.get("action") == "sell" and sig.get("confidence") is not None:
            meta["geminiConfidence"] = int(sig["confidence"])

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
    )
    return {k: trade[k] for k in keys if trade.get(k) is not None}
