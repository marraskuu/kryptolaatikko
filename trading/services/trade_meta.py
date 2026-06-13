"""Kauppakirjauksen meta: sisäänostokonteksti oppimista varten."""

from __future__ import annotations

import re
from typing import Any

from .market_learning import setup_key_for_analysis

_GEMINI_CONF_RE = re.compile(r"Gemini\s*\((\d+)/10\)", re.I)


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
        for key in ("bookImbalance", "bookSpreadPct", "longShortRatio"):
            if analysis.get(key) is not None:
                meta[key] = round(float(analysis[key]), 4)
        if analysis.get("bookBucket"):
            meta["bookBucket"] = analysis["bookBucket"]
        if analysis.get("crowdBucket"):
            meta["crowdBucket"] = analysis["crowdBucket"]
        meta["setup"] = setup_key_for_analysis(analysis, regime)
    else:
        sig = analysis.get("geminiSignal") or {}
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
        "bookBucket",
        "crowdBucket",
    )
    return {k: trade[k] for k in keys if trade.get(k) is not None}
