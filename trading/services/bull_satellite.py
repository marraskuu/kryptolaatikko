"""
Bull-regiimin satelliitti — toinen positio ilman rotaatiota.

Käteinen (voitto-otto / idle) jaetaan 65 % ydin + 35 % paras Gemini-/momentum-kohde
vain jos odotettu hyöty ylittää pelkän ydinlisäyksen.
"""

from __future__ import annotations

from typing import Any, Callable

from .ai_trader import (
    MIN_TRADE_EUR,
    _edge_pct,
    _gemini_signal,
    _is_overheated_for_concentration,
    entry_eligible,
    normalize_symbol,
)
from .bitfinex import is_stablecoin

BULL_SATELLITE_PRIMARY_WEIGHT = 0.65
BULL_SATELLITE_MIN_EDGE = 1.25
BULL_SATELLITE_MIN_MOMENTUM_GAP = 2.5
BULL_SATELLITE_MIN_GEMINI_CONF = 7
BULL_SATELLITE_STRONG_GEMINI_CONF = 9
BULL_SATELLITE_MIN_CASH_EUR = 30.0
BULL_SATELLITE_MIN_HOLDING_SHARE = 0.55


def _is_bull_phase(regime: str, regime_info: dict[str, Any] | None) -> bool:
    phase = str((regime_info or {}).get("phase") or regime or "neutral")
    official = str((regime_info or {}).get("regime") or regime or "neutral")
    return phase.startswith("bull") or official == "bull"


def _momentum_pct(analysis: dict[str, Any]) -> float:
    for key in ("change4hPct", "change1hPct", "changePct", "momentum"):
        val = analysis.get(key)
        if val is not None:
            return float(val)
    return 0.0


def _holding_value_share(
    symbol: str,
    holding: dict[str, Any],
    analyses: dict[str, dict[str, Any]],
    total_value: float,
) -> float:
    if total_value <= 0:
        return 0.0
    analysis = analyses.get(symbol) or {}
    price = float(analysis.get("currentPrice") or 0)
    if price <= 0:
        return 0.0
    return (float(holding.get("amount") or 0) * price) / total_value


def _profit_pct(holding: dict[str, Any], analysis: dict[str, Any]) -> float:
    avg = float(holding.get("avgPrice") or 0)
    price = float(analysis.get("currentPrice") or 0)
    if avg <= 0 or price <= 0:
        return 0.0
    return ((price - avg) / avg) * 100.0


def _pick_satellite_candidates(
    primary_norm: str,
    *,
    gemini_insights: dict[str, Any] | None,
    ranked_buyable: list[dict[str, Any]],
    analyses: dict[str, dict[str, Any]],
) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()

    if gemini_insights:
        for raw in gemini_insights.get("top_picks") or []:
            norm = normalize_symbol(str(raw))
            if norm and norm != primary_norm and norm not in seen and not is_stablecoin(norm):
                seen.add(norm)
                ordered.append(norm)

    for item in ranked_buyable:
        norm = normalize_symbol(item.get("symbol", ""))
        if norm and norm != primary_norm and norm not in seen and not is_stablecoin(norm):
            seen.add(norm)
            ordered.append(norm)

    for norm, analysis in analyses.items():
        if is_stablecoin(norm) or norm == primary_norm or norm in seen:
            continue
        if float(analysis.get("score") or 0) >= 5:
            seen.add(norm)
            ordered.append(norm)

    return ordered


def evaluate_bull_satellite_split(
    *,
    regime: str,
    regime_info: dict[str, Any] | None,
    holdings: dict[str, Any],
    analyses: dict[str, dict[str, Any]],
    total_value: float,
    available_cash: float,
    gemini_insights: dict[str, Any] | None,
    gemini_active: bool,
    ranked_buyable: list[dict[str, Any]],
    buy_blocked: Callable[[str, dict[str, Any] | None], bool],
    entry_score_min: int = 1,
) -> dict[str, Any] | None:
    """
    Palauttaa split-suunnitelman tai None (pidä kaikki ydinposition lisäyksessä).

    Ei myy olemassa olevaa positiota — vain käteisen jako.
    """
    if not _is_bull_phase(regime, regime_info):
        return None
    if available_cash < BULL_SATELLITE_MIN_CASH_EUR:
        return None

    live_holdings = {
        sym: h
        for sym, h in holdings.items()
        if not is_stablecoin(sym) and float(h.get("amount") or 0) > 0
    }
    if len(live_holdings) != 1:
        return None

    primary_sym = next(iter(live_holdings))
    primary_norm = normalize_symbol(primary_sym)
    primary_holding = live_holdings[primary_sym]
    primary_analysis = analyses.get(primary_sym) or analyses.get(primary_norm) or {}
    if not primary_analysis:
        return None

    share = _holding_value_share(primary_sym, primary_holding, analyses, total_value)
    if share < BULL_SATELLITE_MIN_HOLDING_SHARE:
        return None

    primary_edge = _edge_pct(primary_analysis)
    primary_mom = _momentum_pct(primary_analysis)
    primary_profit = _profit_pct(primary_holding, primary_analysis)
    primary_sig = _gemini_signal(gemini_insights, primary_sym) if gemini_insights else None

    if primary_sig and primary_sig.get("action") == "sell":
        return None

    best: dict[str, Any] | None = None
    for cand_norm in _pick_satellite_candidates(
        primary_norm,
        gemini_insights=gemini_insights,
        ranked_buyable=ranked_buyable,
        analyses=analyses,
    ):
        cand_sym = cand_norm
        for key in analyses:
            if normalize_symbol(key) == cand_norm:
                cand_sym = key
                break

        sat_analysis = analyses.get(cand_sym) or analyses.get(cand_norm)
        if not sat_analysis or not entry_eligible(sat_analysis):
            continue
        if buy_blocked(cand_sym, sat_analysis):
            continue
        if _is_overheated_for_concentration(sat_analysis):
            continue
        if int(sat_analysis.get("score") or 0) < entry_score_min:
            continue

        sat_edge = _edge_pct(sat_analysis)
        sat_mom = _momentum_pct(sat_analysis)
        sat_sig = _gemini_signal(gemini_insights, cand_sym) if gemini_insights else None
        if sat_sig and sat_sig.get("action") == "sell":
            continue

        edge_delta = sat_edge - primary_edge
        mom_delta = sat_mom - primary_mom
        gemini_conf = int(sat_sig.get("confidence", 0)) if sat_sig else 0
        gemini_buy = bool(sat_sig and sat_sig.get("action") == "buy")

        edge_ok = edge_delta >= BULL_SATELLITE_MIN_EDGE
        mom_ok = mom_delta >= BULL_SATELLITE_MIN_MOMENTUM_GAP
        gemini_ok = gemini_buy and gemini_conf >= BULL_SATELLITE_MIN_GEMINI_CONF

        approved = (edge_ok and mom_ok) or (gemini_ok and mom_ok and edge_delta >= 0)
        if not approved:
            continue

        if primary_sig and int(primary_sig.get("confidence", 0)) >= BULL_SATELLITE_STRONG_GEMINI_CONF:
            if not (mom_ok and (edge_ok or gemini_conf >= BULL_SATELLITE_STRONG_GEMINI_CONF)):
                continue

        score = edge_delta * 2.0 + mom_delta + (gemini_conf * 0.3 if gemini_buy else 0)
        if best is None or score > float(best.get("_score", 0)):
            reasons = []
            if edge_ok:
                reasons.append(f"edge {sat_edge:+.1f} % vs ydin {primary_edge:+.1f} %")
            if mom_ok:
                reasons.append(f"momentum {sat_mom:+.1f} % vs {primary_mom:+.1f} %")
            if gemini_ok:
                reasons.append(f"Gemini {gemini_conf}/10")
            best = {
                "_score": score,
                "primary": primary_sym,
                "satellite": cand_sym,
                "weights": {
                    primary_norm: BULL_SATELLITE_PRIMARY_WEIGHT,
                    cand_norm: 1.0 - BULL_SATELLITE_PRIMARY_WEIGHT,
                },
                "edge_delta": round(edge_delta, 2),
                "momentum_gap": round(mom_delta, 2),
                "primary_profit_pct": round(primary_profit, 2),
                "reason": " · ".join(reasons),
            }

    return best


def deploy_bull_satellite_cash(
    decisions: list[dict[str, Any]],
    *,
    available_cash: float,
    split: dict[str, Any],
    analyses: dict[str, dict[str, Any]],
    gemini_active: bool,
    format_reason: Callable[..., str],
) -> bool:
    """Jaa käteinen 65/35 — ei koske olemassa olevaa positiota."""
    if available_cash < MIN_TRADE_EUR:
        return False

    primary = split["primary"]
    satellite = split["satellite"]
    primary_analysis = analyses.get(primary)
    satellite_analysis = analyses.get(satellite)
    if not primary_analysis or not satellite_analysis:
        return False

    primary_price = float(primary_analysis.get("currentPrice") or 0)
    satellite_price = float(satellite_analysis.get("currentPrice") or 0)
    if primary_price <= 0 or satellite_price <= 0:
        return False

    satellite_cash = round(available_cash * (1.0 - BULL_SATELLITE_PRIMARY_WEIGHT), 2)
    primary_cash = round(available_cash - satellite_cash, 2)

    if satellite_cash < MIN_TRADE_EUR:
        primary_cash = available_cash
        satellite_cash = 0.0

    if primary_cash < MIN_TRADE_EUR and satellite_cash >= MIN_TRADE_EUR:
        satellite_cash = available_cash
        primary_cash = 0.0

    split_note = (
        f"Bull-satelliitti ({int(BULL_SATELLITE_PRIMARY_WEIGHT * 100)}/"
        f"{int((1 - BULL_SATELLITE_PRIMARY_WEIGHT) * 100)}) — {split['reason']}"
    )

    planned: list[tuple[str, float, dict[str, Any], float]] = []
    if primary_cash >= MIN_TRADE_EUR:
        planned.append((primary, primary_cash, primary_analysis, BULL_SATELLITE_PRIMARY_WEIGHT))
    if satellite_cash >= MIN_TRADE_EUR:
        planned.append(
            (
                satellite,
                satellite_cash,
                satellite_analysis,
                1.0 - BULL_SATELLITE_PRIMARY_WEIGHT,
            )
        )

    if not planned:
        return False

    for sym, eur, analysis, weight in planned:
        price = float(analysis["currentPrice"])
        alloc_pct = round(weight * 100, 1)
        reason = format_reason(
            analysis,
            gemini_active=gemini_active,
            fallback=split_note,
            alloc_pct=alloc_pct,
            eur_amount=eur,
        )
        if sym == satellite:
            reason = f"{split_note} · {reason}" if gemini_active else split_note

        existing = next(
            (d for d in decisions if d.get("type") == "buy" and d.get("symbol") == sym),
            None,
        )
        if existing:
            existing["eurAmount"] = float(existing.get("eurAmount") or 0) + eur
            existing["amount"] = existing["eurAmount"] / price
            existing["reason"] = reason
            continue

        decisions.append(
            {
                "type": "buy",
                "symbol": sym,
                "eurAmount": eur,
                "amount": eur / price,
                "reason": reason,
                "analysis": analysis,
            }
        )

    return True
