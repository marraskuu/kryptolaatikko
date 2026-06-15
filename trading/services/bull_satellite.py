"""
Bull-regiimin satelliitti — toinen positio ilman rotaatiota.

Käteinen (voitto-otto / idle) jaetaan 65 % ydin + 35 % paras Gemini-/momentum-kohde
vain jos odotettu hyöty ylittää pelkän ydinlisäyksen.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from .ai_trader import (
    MIN_TRADE_EUR,
    _edge_pct,
    _gemini_signal,
    _is_overheated_for_concentration,
    entry_eligible,
    normalize_symbol,
)
from .bitfinex import get_crypto_label, is_stablecoin

BULL_SATELLITE_PRIMARY_WEIGHT = 0.65
BULL_SATELLITE_MIN_EDGE = 1.25
BULL_SATELLITE_MIN_MOMENTUM_GAP = 2.5
BULL_SATELLITE_MIN_GEMINI_CONF = 7
BULL_SATELLITE_STRONG_GEMINI_CONF = 9
BULL_SATELLITE_MIN_CASH_EUR = 30.0
BULL_SATELLITE_MIN_HOLDING_SHARE = 0.55

EVENT_MAX = 80
WIN_EPS = 0.01


def _parse_time(iso: Any) -> datetime | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def bull_satellite_trade_meta(
    *,
    split: dict[str, Any],
    role: str,
    eur: float,
    entry_price: float,
    pair_id: str,
) -> dict[str, Any]:
    """Meta bull-satelliitti-ostoille — seurantaan ja oppimisraporttiin."""
    return {
        "bullSatellite": True,
        "bullSatellitePair": pair_id,
        "bullSatelliteRole": role,
        "bullSatellitePrimary": split.get("primary"),
        "bullSatelliteSatellite": split.get("satellite"),
        "bullSatelliteEdgeDelta": split.get("edge_delta"),
        "bullSatelliteMomentumGap": split.get("momentum_gap"),
        "bullSatelliteEur": round(float(eur), 2),
        "bullSatelliteEntryPrice": round(float(entry_price), 6),
    }


def _events(state: dict[str, Any]) -> list[dict[str, Any]]:
    return list(state.get("bullSatelliteEvents") or [])


def _save_events(state: dict[str, Any], events: list[dict[str, Any]]) -> None:
    state["bullSatelliteEvents"] = events[-EVENT_MAX:]


def sync_from_portfolio(
    state: dict[str, Any],
    portfolio: dict[str, Any],
    tickers: dict[str, dict[str, Any]] | None = None,
) -> None:
    """Päivitä split-tapahtumien tulokset kauppakirjauksesta ja mark-to-marketista."""
    trades = portfolio.get("trades") or []
    tickers = tickers or {}
    by_pair: dict[str, dict[str, Any]] = {}

    for trade in reversed(trades):
        if trade.get("type") != "buy" or not trade.get("bullSatellitePair"):
            continue
        pair = str(trade["bullSatellitePair"])
        bucket = by_pair.setdefault(
            pair,
            {
                "id": pair,
                "timestamp": trade.get("timestamp"),
                "primary": trade.get("bullSatellitePrimary"),
                "satellite": trade.get("bullSatelliteSatellite"),
                "edgeDelta": trade.get("bullSatelliteEdgeDelta"),
                "momentumGap": trade.get("bullSatelliteMomentumGap"),
                "primaryEur": 0.0,
                "satelliteEur": 0.0,
                "primaryEntryPrice": None,
                "satelliteEntryPrice": None,
                "primaryBuyId": None,
                "satelliteBuyId": None,
            },
        )
        role = trade.get("bullSatelliteRole")
        eur = float(trade.get("eurTotal") or 0)
        if role == "primary":
            bucket["primaryEur"] += eur
            bucket["primaryEntryPrice"] = float(trade.get("price") or 0)
            bucket["primaryBuyId"] = trade.get("id")
        elif role == "satellite":
            bucket["satelliteEur"] += eur
            bucket["satelliteEntryPrice"] = float(trade.get("price") or 0)
            bucket["satelliteBuyId"] = trade.get("id")
        if not bucket.get("timestamp"):
            bucket["timestamp"] = trade.get("timestamp")

    updated: list[dict[str, Any]] = []
    for pair_id, raw in by_pair.items():
        event = _compute_event_outcome(raw, trades, tickers)
        updated.append(event)

    updated.sort(key=lambda e: str(e.get("timestamp") or ""), reverse=True)
    _save_events(state, updated)


def _mark_price(symbol: str, tickers: dict[str, dict[str, Any]]) -> float | None:
    tk = tickers.get(symbol)
    if tk and tk.get("last"):
        return float(tk["last"])
    return None


def _leg_result(
    *,
    buy_trade: dict[str, Any] | None,
    symbol: str,
    sells: list[dict[str, Any]],
    current_price: float | None,
) -> dict[str, Any]:
    if not buy_trade:
        return {"invested": 0.0, "pl": 0.0, "closed": False}

    invested = float(buy_trade.get("eurTotal") or 0)
    amount = float(buy_trade.get("amount") or 0)
    entry = float(buy_trade.get("price") or 0)
    sold_amount = sum(float(s.get("amount") or 0) for s in sells)
    realized = sum(float(s.get("profitLoss") or 0) for s in sells)
    remaining = max(0.0, amount - sold_amount)

    if remaining > 0.001 and current_price and entry > 0:
        unrealized = remaining * (current_price - entry)
        pl = realized + unrealized
        closed = sold_amount >= amount * 0.999
    else:
        pl = realized
        closed = amount > 0 and sold_amount >= amount * 0.999

    return {
        "invested": round(invested, 2),
        "pl": round(pl, 2),
        "closed": closed,
        "remainingAmount": round(remaining, 8),
    }


def _compute_event_outcome(
    raw: dict[str, Any],
    trades: list[dict[str, Any]],
    tickers: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    pair_id = raw["id"]
    event_ts = _parse_time(raw.get("timestamp"))
    primary = raw.get("primary")
    satellite = raw.get("satellite")

    primary_buy = None
    satellite_buy = None
    for trade in trades:
        if trade.get("bullSatellitePair") != pair_id or trade.get("type") != "buy":
            continue
        if trade.get("bullSatelliteRole") == "primary":
            primary_buy = trade
        elif trade.get("bullSatelliteRole") == "satellite":
            satellite_buy = trade

    def sells_after(symbol: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if not event_ts or not symbol:
            return out
        for trade in trades:
            if trade.get("type") != "sell" or trade.get("symbol") != symbol:
                continue
            ts = _parse_time(trade.get("timestamp"))
            if ts and ts >= event_ts:
                out.append(trade)
        return out

    primary_leg = _leg_result(
        buy_trade=primary_buy,
        symbol=str(primary or ""),
        sells=sells_after(str(primary or "")),
        current_price=_mark_price(str(primary or ""), tickers),
    )
    satellite_leg = _leg_result(
        buy_trade=satellite_buy,
        symbol=str(satellite or ""),
        sells=sells_after(str(satellite or "")),
        current_price=_mark_price(str(satellite or ""), tickers),
    )

    total_invested = primary_leg["invested"] + satellite_leg["invested"]
    actual_pl = primary_leg["pl"] + satellite_leg["pl"]

    cf_pl = 0.0
    primary_entry = float(raw.get("primaryEntryPrice") or (primary_buy or {}).get("price") or 0)
    primary_now = _mark_price(str(primary or ""), tickers)
    if total_invested > 0 and primary_entry > 0 and primary_now:
        cf_pl = total_invested * (primary_now / primary_entry - 1.0)

    advantage = actual_pl - cf_pl
    closed = satellite_leg["invested"] > 0 and satellite_leg["closed"] and (
        primary_leg["invested"] <= 0 or primary_leg["closed"]
    )

    return {
        **raw,
        "totalEur": round(total_invested, 2),
        "actualPlEur": round(actual_pl, 2),
        "counterfactualPrimaryOnlyPlEur": round(cf_pl, 2),
        "advantageEur": round(advantage, 2),
        "primaryPlEur": primary_leg["pl"],
        "satellitePlEur": satellite_leg["pl"],
        "status": "closed" if closed else "open",
        "satelliteClosed": satellite_leg["closed"],
    }


def build_gemini_context(
    portfolio: dict[str, Any],
    bot_state: dict[str, Any] | None = None,
    tickers: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Konteksti Geminin kertomukseen — bull-satelliitin käytännön tulokset."""
    bot_state = bot_state or {}
    tickers = tickers or bot_state.get("tickers") or {}
    sync_from_portfolio(bot_state, portfolio, tickers)
    events = _events(bot_state)

    closed = [e for e in events if e.get("status") == "closed"]
    open_ev = [e for e in events if e.get("status") != "closed"]
    with_outcome = [e for e in events if e.get("totalEur", 0) > 0]

    advantages = [float(e.get("advantageEur") or 0) for e in with_outcome]
    wins = sum(1 for a in advantages if a > WIN_EPS)
    losses = sum(1 for a in advantages if a < -WIN_EPS)
    avg_adv = round(sum(advantages) / len(advantages), 2) if advantages else None
    total_adv = round(sum(advantages), 2) if advantages else 0.0

    examples = []
    for e in sorted(with_outcome, key=lambda x: float(x.get("advantageEur") or 0), reverse=True)[:3]:
        examples.append(
            {
                "timestamp": e.get("timestamp"),
                "primary": get_crypto_label(str(e.get("primary") or "")),
                "satellite": get_crypto_label(str(e.get("satellite") or "")),
                "totalEur": e.get("totalEur"),
                "actualPlEur": e.get("actualPlEur"),
                "counterfactualPrimaryOnlyPlEur": e.get("counterfactualPrimaryOnlyPlEur"),
                "advantageEur": e.get("advantageEur"),
                "edgeDelta": e.get("edgeDelta"),
                "momentumGap": e.get("momentumGap"),
                "status": e.get("status"),
            }
        )
    worst = sorted(with_outcome, key=lambda x: float(x.get("advantageEur") or 0))[:2]
    for e in worst:
        if e not in [x for x in with_outcome if x in examples]:
            examples.append(
                {
                    "timestamp": e.get("timestamp"),
                    "primary": get_crypto_label(str(e.get("primary") or "")),
                    "satellite": get_crypto_label(str(e.get("satellite") or "")),
                    "totalEur": e.get("totalEur"),
                    "actualPlEur": e.get("actualPlEur"),
                    "counterfactualPrimaryOnlyPlEur": e.get("counterfactualPrimaryOnlyPlEur"),
                    "advantageEur": e.get("advantageEur"),
                    "status": e.get("status"),
                    "type": "weak",
                }
            )

    return {
        "enabled": True,
        "splitCount": len(events),
        "closedCount": len(closed),
        "openCount": len(open_ev),
        "withOutcomeCount": len(with_outcome),
        "winsVsPrimaryOnly": wins,
        "lossesVsPrimaryOnly": losses,
        "avgAdvantageEur": avg_adv,
        "totalAdvantageEur": total_adv,
        "primaryWeightPct": int(BULL_SATELLITE_PRIMARY_WEIGHT * 100),
        "satelliteWeightPct": int((1 - BULL_SATELLITE_PRIMARY_WEIGHT) * 100),
        "rules": {
            "minEdgeDelta": BULL_SATELLITE_MIN_EDGE,
            "minMomentumGap": BULL_SATELLITE_MIN_MOMENTUM_GAP,
            "minGeminiConf": BULL_SATELLITE_MIN_GEMINI_CONF,
            "noRotation": True,
        },
        "examples": examples[:5],
        "recommendations": _recommendations_from_events(with_outcome, avg_adv, wins, losses),
    }


def _recommendations_from_events(
    events: list[dict[str, Any]],
    avg_adv: float | None,
    wins: int,
    losses: int,
) -> list[str]:
    recs: list[str] = []
    n = len(events)
    if n == 0:
        recs.append("Strategia käytössä — odottaa ensimmäistä 65/35-jakoa bull-regiimissä")
        return recs
    if n < 3:
        recs.append(f"Kerätään dataa ({n}/3 split-tapahtumaa) ennen vahvoja johtopäätöksiä")
    if avg_adv is not None and avg_adv > 0.5:
        recs.append(f"Jako on tuottanut keskimäärin {avg_adv:+.2f} € enemmän kuin pelkkä ydin")
    elif avg_adv is not None and avg_adv < -0.5:
        recs.append(f"Jako on jäänyt keskimäärin {avg_adv:+.2f} € alle pelkkä ydin - tiukenna kynnyksiä")
    if wins and losses and wins > losses:
        recs.append(f"Enemmän voitollisia ({wins}) kuin tappiollisia ({losses}) vs pelkkä ydin")
    elif losses > wins:
        recs.append(f"Tappiollisia jakoja ({losses}) enemmän kuin voitollisia ({wins})")
    if not recs:
        recs.append("Tulokset tasaiset — jatketaan seurantaa")
    return recs[:4]


def learning_report_lines(context: dict[str, Any]) -> list[str]:
    """Rule-pohjaiset rivit oppimisraportin korttiin."""
    if not context.get("enabled"):
        return []

    lines: list[str] = []
    n = int(context.get("splitCount") or 0)
    if n == 0:
        lines.append("Bull-satelliitti (65/35) — odottaa ensimmäistä jakotilannetta")
        return lines

    wins = int(context.get("winsVsPrimaryOnly") or 0)
    losses = int(context.get("lossesVsPrimaryOnly") or 0)
    total_adv = float(context.get("totalAdvantageEur") or 0)
    avg = context.get("avgAdvantageEur")
    open_n = int(context.get("openCount") or 0)

    lines.append(f"Split-jakoja: {n} ({open_n} auki)")
    if context.get("withOutcomeCount", 0) > 0:
        avg_txt = f", keskim. {avg:+.2f} €" if avg is not None else ""
        lines.append(
            f"Vs pelkkä ydin: {wins}V / {losses}T · yhteis etu {total_adv:+.2f} €{avg_txt}"
        )
    recs = context.get("recommendations") or []
    if recs:
        lines.append(recs[0])
    return lines


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

    pair_id = datetime.now(timezone.utc).strftime("bs-%Y%m%d%H%M%S")

    for sym, eur, analysis, weight in planned:
        price = float(analysis["currentPrice"])
        role = "primary" if sym == primary else "satellite"
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

        bs_meta = bull_satellite_trade_meta(
            split=split,
            role=role,
            eur=eur,
            entry_price=price,
            pair_id=pair_id,
        )

        existing = next(
            (d for d in decisions if d.get("type") == "buy" and d.get("symbol") == sym),
            None,
        )
        if existing:
            existing["eurAmount"] = float(existing.get("eurAmount") or 0) + eur
            existing["amount"] = existing["eurAmount"] / price
            existing["reason"] = reason
            existing["bullSatelliteMeta"] = bs_meta
            continue

        decisions.append(
            {
                "type": "buy",
                "symbol": sym,
                "eurAmount": eur,
                "amount": eur / price,
                "reason": reason,
                "analysis": analysis,
                "bullSatelliteMeta": bs_meta,
            }
        )

    return True
