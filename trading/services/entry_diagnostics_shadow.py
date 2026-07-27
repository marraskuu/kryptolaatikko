"""Ostohetken varjodiagnostiikka (ideat #1 korrelaatio, #2 ATR-koko, #4 Kelly-koko).

Kolme puhdasta funktiota, jotka lasketaan oikean ostopäätöksen yhteydessä ja
ratsastavat mukana kaupan meta-datassa (trading/services/trade_meta.py) —
EIVÄT KOSKAAN muuta eur_amount-arvoa tai mitään ai_trader.py:n valinta-/
pisteytyslogiikkaa. Bucketointi toteutunutta lopputulosta vasten tehdään
myyntihetkellä trading/services/learning.py:ssä (_compute_entry_diagnostics_
shadow_tuning), samaan tapaan kuin Gemini-confidence-kalibrointi.

Poikkeus: blended_entry_size_eur() ON tarkoitettu käytettäväksi oikean
eur_amount:n laskennassa (opt-in, ks. ENTRY_SIZE_*_BLEND_WEIGHT) — kaikki
muu tässä moduulissa pysyy puhtaana varjodiagnostiikkana.
"""

from __future__ import annotations

import os
from typing import Any

from .ai_trader import CORR_THRESHOLD, MIN_TRADE_EUR, _analysis_for, _atr_pct, _pearson

__all__ = [
    "max_correlation_vs_holdings",
    "atr_weighted_shadow_sizes",
    "kelly_expectancy_shadow_sizes",
    "blended_entry_size_eur",
    "build_gemini_context",
    "learning_report_lines",
]

MIN_SAMPLES_REPORT = 8

# Kelly/ATR-varjokoon sekoitusosuus oikeaan eur_amount:iin — 0.0 = ei vaikutusta
# (oletus, mekanismi laskeva mutta käytöksetön). Käyttäjä säätää myöhemmin
# manuaalisesti kun learning_report_lines() osoittaa riittävästi näytteitä.
ENTRY_SIZE_KELLY_BLEND_WEIGHT = float(os.environ.get("ENTRY_SIZE_KELLY_BLEND_WEIGHT", "0.0"))
ENTRY_SIZE_ATR_BLEND_WEIGHT = float(os.environ.get("ENTRY_SIZE_ATR_BLEND_WEIGHT", "0.0"))


def blended_entry_size_eur(
    symbol: str,
    original_eur: float,
    *,
    atr_shadow_map: dict[str, float],
    kelly_shadow_map: dict[str, float],
    kelly_weight: float,
    atr_weight: float,
    min_trade_eur: float = MIN_TRADE_EUR,
) -> float:
    """Sekoittaa jo lasketun eur_amount:n kohti Kelly- tai ATR-varjokokoa.

    Kelly ensisijainen (jos painotettu ja symboli mukana kartassa), muuten ATR,
    muuten alkuperäinen muuttumattomana. Molemmat varjokartat säilyttävät katetun
    joukon EUR-summan (ks. atr_weighted_shadow_sizes/kelly_expectancy_shadow_sizes),
    joten sekoitus voi vain siirtää pääomaa jo valittujen ehdokkaiden välillä —
    ei koskaan kokonaispanostusta eikä ohita mitään entry-porttia.
    Jos blendattu tulos alittaisi min_trade_eur:n, palautetaan alkuperäinen
    (ei koskaan luoda ali-minimi-tilausta äänettömästi).
    """
    original_eur = float(original_eur)
    if kelly_weight > 0 and symbol in kelly_shadow_map:
        target = float(kelly_shadow_map[symbol])
    elif atr_weight > 0 and symbol in atr_shadow_map:
        target = float(atr_shadow_map[symbol])
    else:
        return original_eur

    weight = kelly_weight if symbol in kelly_shadow_map and kelly_weight > 0 else atr_weight
    weight = max(0.0, min(1.0, weight))
    blended = original_eur + (target - original_eur) * weight
    if blended < min_trade_eur:
        return original_eur
    return round(blended, 2)


def max_correlation_vs_holdings(
    symbol: str,
    analyses: dict[str, dict[str, Any]],
    held_symbols: list[str],
) -> dict[str, Any] | None:
    """Korkein pareittainen korrelaatio ostoehdokkaan ja jo omistettujen positioiden välillä.

    Käyttää samaa Pearson-korrelaatiota ja kynnystä kuin ai_trader.diversify_weights(),
    mutta vertaa OLEMASSA OLEVIIN positioihin — tätä tarkistusta ei tänään tehdä
    missään (diversify_weights vertaa vain saman kierroksen ehdokkaita keskenään).
    """
    candidate = _analysis_for(analyses, symbol)
    if not candidate:
        return None
    candidate_returns = list(candidate.get("recentReturns") or [])
    if not candidate_returns:
        return None

    best_corr: float | None = None
    best_symbol: str | None = None
    for held in held_symbols:
        if held == symbol:
            continue
        held_analysis = _analysis_for(analyses, held)
        if not held_analysis:
            continue
        held_returns = list(held_analysis.get("recentReturns") or [])
        if not held_returns:
            continue
        corr = _pearson(candidate_returns, held_returns)
        if corr is None:
            continue
        if best_corr is None or corr > best_corr:
            best_corr = corr
            best_symbol = held

    if best_corr is None:
        return None
    return {
        "maxCorrSymbol": best_symbol,
        "maxCorrValue": round(best_corr, 4),
        "highCorrFlag": best_corr >= CORR_THRESHOLD,
    }


def atr_weighted_shadow_sizes(buy_batch: list[dict[str, Any]]) -> dict[str, float]:
    """Jakaisi saman kokonais-EUR:n uudelleen 1/ATR-painotuksella (matalampi vola = isompi paino).

    Ei muuta oikeaa eur_amount-arvoa — puhtaasti vertailuluku.
    """
    if not buy_batch:
        return {}
    total_eur = sum(float(item.get("eurAmount") or 0) for item in buy_batch)
    if total_eur <= 0:
        return {}

    inv_atr: dict[str, float] = {}
    for item in buy_batch:
        atr = _atr_pct(item.get("analysis") or {})
        if atr > 0:
            inv_atr[item["symbol"]] = 1.0 / atr
    weight_total = sum(inv_atr.values())
    if weight_total <= 0:
        return {}

    return {
        symbol: round(total_eur * (weight / weight_total), 2)
        for symbol, weight in inv_atr.items()
    }


def kelly_expectancy_shadow_sizes(
    buy_batch: list[dict[str, Any]],
    gemini_confidence_stats: dict[Any, dict[str, Any]] | None,
    *,
    min_samples: int = 8,
) -> dict[str, float]:
    """Jakaisi tunnisteellisen osuuden EUR:sta suhteessa opittuun confidence-bucketin
    expectancy_eur:iin (Kelly-tyylinen painotus) sen sijaan että käyttäisi nykyistä
    yksinkertaista scale-kerrointa. Tunnisteettomat symbolit säilyttävät oman
    eur_amount-arvonsa muuttumattomana (ei voida arvioida ilman bucketia).
    """
    if not buy_batch or not gemini_confidence_stats:
        return {}

    tagged: dict[str, float] = {}
    result: dict[str, float] = {}
    for item in buy_batch:
        symbol = item["symbol"]
        eur_amount = float(item.get("eurAmount") or 0)
        result[symbol] = eur_amount  # oletus tunnisteettomalle: ei muutosta

        analysis = item.get("analysis") or {}
        sig = analysis.get("geminiSignal") or {}
        conf = sig.get("confidence")
        if conf is None:
            continue
        stat = gemini_confidence_stats.get(conf) or gemini_confidence_stats.get(str(conf))
        if not stat or int(stat.get("trades") or 0) < min_samples:
            continue
        expectancy = max(0.0, float(stat.get("expectancy_eur") or 0))
        tagged[symbol] = expectancy

    if not tagged:
        return result

    tagged_total_eur = sum(
        float(item.get("eurAmount") or 0) for item in buy_batch if item["symbol"] in tagged
    )
    weight_total = sum(tagged.values())
    if weight_total <= 0 or tagged_total_eur <= 0:
        return result

    for symbol, expectancy in tagged.items():
        result[symbol] = round(tagged_total_eur * (expectancy / weight_total), 2)
    return result


def build_gemini_context(state: dict[str, Any]) -> dict[str, Any]:
    """Kolme itsenäistä varjolohkoa Gemini-oppimiskertomukselle. EI vaikuta oikeisiin kauppoihin."""
    shadow = (state.get("learning") or {}).get("entry_diagnostics_shadow") or {}
    return {
        "note": (
            "Ostohetken varjodiagnostiikka: korrelaatio olemassa oleviin positioihin, "
            "ATR-painotettu koko ja Gemini-kalibroinnin Kelly-tyylinen koko verrattuna "
            "toteutuneisiin kauppoihin. EI vaikuta oikeisiin kauppoihin."
        ),
        "correlationShadow": shadow.get("correlation_shadow"),
        "atrSizeShadow": shadow.get("atr_size_shadow"),
        "kellySizeShadow": shadow.get("kelly_size_shadow"),
    }


def _bucket_line(label: str, low: dict[str, Any] | None, high: dict[str, Any] | None) -> str | None:
    low = low or {}
    high = high or {}
    low_n = int(low.get("trades") or 0)
    high_n = int(high.get("trades") or 0)
    if low_n < MIN_SAMPLES_REPORT and high_n < MIN_SAMPLES_REPORT:
        return None
    parts = []
    if low_n:
        parts.append(f"{low.get('expectancy_eur', 0):+.2f} €/kauppa ({low_n} kpl)")
    if high_n:
        parts.append(f"{high.get('expectancy_eur', 0):+.2f} €/kauppa ({high_n} kpl)")
    return f"{label}: " + " vs. ".join(parts)


def learning_report_lines(state: dict[str, Any]) -> list[str]:
    shadow = (state.get("learning") or {}).get("entry_diagnostics_shadow") or {}
    lines: list[str] = []

    corr = shadow.get("correlation_shadow") or {}
    line = _bucket_line("Korrelaatio olemassa olevaan positioon (matala vs. korkea)",
                         corr.get("low_corr"), corr.get("high_corr"))
    if line:
        lines.append(line)

    atr = shadow.get("atr_size_shadow") or {}
    line = _bucket_line("ATR-koko vs. toteutunut (alimitoitettu vs. ylimitoitettu)",
                         atr.get("atr_undersized"), atr.get("atr_oversized"))
    if line:
        lines.append(line)

    kelly = shadow.get("kelly_size_shadow") or {}
    line = _bucket_line("Kelly-koko vs. toteutunut (alimitoitettu vs. ylimitoitettu)",
                         kelly.get("kelly_undersized"), kelly.get("kelly_oversized"))
    if line:
        lines.append(line)

    if not lines:
        return ["Ostohetken varjodiagnostiikka kerää dataa — liian vähän suljettuja kauppoja vertailuun"]
    return lines
