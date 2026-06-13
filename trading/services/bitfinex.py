import logging
import os
import time
from typing import Any

import requests

BITFINEX_DIRECT = "https://api-pub.bitfinex.com/v2"
BITFINEX_TIMEOUT = int(os.environ.get("BITFINEX_TIMEOUT", "12"))
BITFINEX_TICKER_TIMEOUT = int(os.environ.get("BITFINEX_TICKER_TIMEOUT", "25"))
CANDLES_MAX_LIMIT = 10_000
CANDLE_DEEP_LIMIT = int(os.environ.get("CANDLE_DEEP_LIMIT", "200"))

logger = logging.getLogger(__name__)

QUOTE_CURRENCIES = ["UST", "USD", "EUR"]
STABLECOIN_BASES = {
    "USDT", "USDC", "UDC", "DAI", "TUSD", "USDD", "USDR", "EURQ", "USDQ",
    "STABLE", "USAT", "PYUSD", "FRAX", "LUSD", "GUSD", "BUSD", "USDP",
    "CNHT", "XAUT", "EUT", "UST", "EUR", "USD",
}
EXCLUDED_BASES = STABLECOIN_BASES | {"TEST", "TESTUSD"}
QUOTE_PRIORITY = {"USD": 0, "UST": 1, "EUR": 2}

_crypto_meta: dict[str, dict[str, str]] = {}


def get_crypto_meta() -> dict[str, dict[str, str]]:
    return _crypto_meta


def get_crypto_label(symbol: str) -> str:
    meta = _crypto_meta.get(symbol)
    if meta:
        return meta["id"]
    body = symbol.replace("t", "", 1) if symbol.startswith("t") else symbol
    for quote in QUOTE_CURRENCIES:
        if body.endswith(quote):
            return body[: -len(quote)]
    return body


def normalize_symbol(symbol: str) -> str:
    """Bitfinex-symbolit ovat muotoa tBTCUSD — ei tBTC:USD."""
    if not symbol:
        return symbol
    s = symbol.strip()
    if s.startswith("t") and ":" in s:
        s = "t" + s[1:].replace(":", "")
    return s


def is_stablecoin(symbol: str) -> bool:
    """Stablecoinit ja fiat-pegatut tokenit — ei osteta voittoa varten."""
    symbol = normalize_symbol(symbol)
    parsed = parse_pair_symbol(symbol)
    if parsed:
        return parsed["base"] in STABLECOIN_BASES
    label = get_crypto_label(symbol).upper()
    return label in STABLECOIN_BASES


def is_valid_trading_symbol(symbol: str) -> bool:
    if not symbol or not symbol.startswith("t"):
        return False
    if "TEST" in symbol.upper():
        return False
    parsed = parse_pair_symbol(normalize_symbol(symbol))
    return parsed is not None


def parse_pair_symbol(symbol: str) -> dict[str, str] | None:
    symbol = normalize_symbol(symbol)
    if not symbol.startswith("t"):
        return None
    body = symbol[1:]
    if "TEST" in body.upper():
        return None
    for quote in QUOTE_CURRENCIES:
        if body.endswith(quote) and len(body) > len(quote):
            base = body[: -len(quote)]
            if not base or base in EXCLUDED_BASES:
                return None
            return {"base": base, "quote": quote}
    return None


def resolve_holding_ticker(
    symbol: str,
    tickers: dict[str, dict[str, Any]],
) -> tuple[str, dict[str, Any]] | tuple[None, None]:
    """Etsi kurssi omistukselle — kokeile vaihtoehtoisia quote-paria (USD↔UST)."""
    symbol = normalize_symbol(symbol)
    ticker = tickers.get(symbol)
    if ticker:
        return symbol, ticker

    parsed = parse_pair_symbol(symbol)
    if not parsed:
        return None, None

    base = parsed["base"]
    for quote in QUOTE_CURRENCIES:
        alt = f"t{base}{quote}"
        if alt != symbol and alt in tickers:
            return alt, tickers[alt]
    return None, None


def _bitfinex_fetch(path: str, *, timeout: int | None = None) -> list | dict:
    url = f"{BITFINEX_DIRECT}{path}"
    res = requests.get(url, timeout=timeout or BITFINEX_TIMEOUT)
    try:
        res.raise_for_status()
    except requests.HTTPError:
        raise
    data = res.json()
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(data["error"])
    return data


def _parse_ticker_rows(rows: list) -> dict[str, dict[str, Any]]:
    raw: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, list) or len(row) < 8:
            continue
        symbol = row[0]
        if not isinstance(symbol, str) or not symbol.startswith("t"):
            continue
        raw[symbol] = _raw_ticker_from_row(symbol, row)
    return raw


def _raw_ticker_from_row(symbol: str, row: list) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "bid": row[1] or 0,
        "ask": row[3] or 0,
        "change24h": row[5] or 0,
        "changePct": (row[6] or 0) * 100,
        "last": row[7] or row[1] or 0,
        "volume": row[8] or 0,
        "high": row[9] or 0,
        "low": row[10] or 0,
        "volumeEur": 0,
    }


def _api_ticker_path(symbol: str) -> str:
    """Bitfinex yksittäinen ticker vaatii usein kolonin (tLINK:USD)."""
    symbol = normalize_symbol(symbol)
    parsed = parse_pair_symbol(symbol)
    if parsed:
        return f"t{parsed['base']}:{parsed['quote']}"
    return symbol


def _fetch_eur_usd_rate() -> float:
    try:
        row = _bitfinex_fetch("/ticker/tEURUSD")
        if isinstance(row, list) and row:
            return float(row[6] or row[0] or 1.08)
    except (requests.RequestException, TypeError, ValueError):
        pass
    return 1.08


def _enrich_ticker(symbol: str, raw: dict[str, Any], eur_rate: float) -> dict[str, Any] | None:
    symbol = normalize_symbol(symbol)
    parsed = parse_pair_symbol(symbol)
    if not parsed or raw["last"] <= 0:
        return None
    last_eur = _to_eur(raw["last"], parsed["quote"], eur_rate)
    volume_eur = _to_eur(raw["volume"] * raw["last"], parsed["quote"], eur_rate)
    return {
        **raw,
        "symbol": symbol,
        "last": last_eur,
        "bid": _to_eur(raw["bid"], parsed["quote"], eur_rate),
        "ask": _to_eur(raw["ask"], parsed["quote"], eur_rate),
        "high": _to_eur(raw["high"], parsed["quote"], eur_rate),
        "low": _to_eur(raw["low"], parsed["quote"], eur_rate),
        "change24h": _to_eur(raw["change24h"], parsed["quote"], eur_rate),
        "volumeEur": volume_eur,
    }


def fetch_ticker(symbol: str) -> dict[str, Any] | None:
    """Hae yhden parin kurssi (ei volyymisuodatinta) — omistusten arvostus."""
    symbol = normalize_symbol(symbol)
    if not is_valid_trading_symbol(symbol):
        return None

    parsed = parse_pair_symbol(symbol)
    if not parsed:
        return None

    eur_rate = _fetch_eur_usd_rate()
    candidates = [symbol]
    for quote in QUOTE_CURRENCIES:
        alt = f"t{parsed['base']}{quote}"
        if alt not in candidates:
            candidates.append(alt)

    for candidate in candidates:
        try:
            row = _bitfinex_fetch(f"/ticker/{_api_ticker_path(candidate)}")
        except requests.RequestException:
            continue
        if not isinstance(row, list) or len(row) < 8:
            continue
        raw = {
            "symbol": candidate,
            "bid": row[0] or 0,
            "ask": row[2] or 0,
            "change24h": row[4] or 0,
            "changePct": (row[5] or 0) * 100,
            "last": row[6] or row[0] or 0,
            "volume": row[7] or 0,
            "high": row[8] if len(row) > 8 else 0,
            "low": row[9] if len(row) > 9 else 0,
            "volumeEur": 0,
        }
        enriched = _enrich_ticker(candidate, raw, eur_rate)
        if enriched:
            return enriched
    return None


def ensure_portfolio_tickers(
    holdings: dict[str, Any],
    tickers: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Varmista live-kurssi jokaiselle omistukselle (myös volyymisuodatuksen ulkopuolella)."""
    merged = dict(tickers)
    for raw_sym in holdings:
        sym = normalize_symbol(raw_sym)
        _resolved, existing = resolve_holding_ticker(sym, merged)
        if existing:
            if sym not in merged:
                merged[sym] = existing
            continue
        fetched = fetch_ticker(sym)
        if fetched:
            merged[sym] = fetched
            logger.info("Omistuksen kurssi haettu suoraan: %s", sym)
    return merged


def _to_eur(price: float, quote: str, eur_rate: float) -> float:
    if quote == "EUR":
        return price
    if quote in ("USD", "UST"):
        return price / eur_rate
    return price


def fetch_all_markets() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, str]]]:
    global _crypto_meta
    data = _bitfinex_fetch("/tickers?symbols=ALL", timeout=BITFINEX_TICKER_TIMEOUT)
    if not isinstance(data, list):
        raise RuntimeError("Unexpected API response")

    raw = _parse_ticker_rows(data)
    eur_usd = raw.get("tEURUSD")
    eur_rate = (eur_usd or {}).get("last") or (eur_usd or {}).get("bid") or 1.08

    best_by_base: dict[str, dict[str, Any]] = {}

    for raw_symbol, ticker in raw.items():
        symbol = normalize_symbol(raw_symbol)
        if not is_valid_trading_symbol(symbol):
            continue
        parsed = parse_pair_symbol(symbol)
        if not parsed or ticker["last"] <= 0:
            continue

        last_eur = _to_eur(ticker["last"], parsed["quote"], eur_rate)
        volume_eur = _to_eur(ticker["volume"] * ticker["last"], parsed["quote"], eur_rate)
        if volume_eur < 500:
            continue

        enriched = {
            **ticker,
            "last": last_eur,
            "bid": _to_eur(ticker["bid"], parsed["quote"], eur_rate),
            "ask": _to_eur(ticker["ask"], parsed["quote"], eur_rate),
            "high": _to_eur(ticker["high"], parsed["quote"], eur_rate),
            "low": _to_eur(ticker["low"], parsed["quote"], eur_rate),
            "change24h": _to_eur(ticker["change24h"], parsed["quote"], eur_rate),
            "volumeEur": volume_eur,
        }

        meta = {
            "symbol": symbol,
            "id": parsed["base"],
            "quote": parsed["quote"],
            "pairLabel": f"{parsed['base']}/{parsed['quote']}",
        }

        priority = QUOTE_PRIORITY.get(parsed["quote"], 9)
        existing = best_by_base.get(parsed["base"])
        if (
            not existing
            or enriched["volumeEur"] > existing["ticker"]["volumeEur"]
            or (
                enriched["volumeEur"] == existing["ticker"]["volumeEur"]
                and priority < QUOTE_PRIORITY.get(existing["meta"]["quote"], 9)
            )
        ):
            best_by_base[parsed["base"]] = {
                "symbol": symbol,
                "meta": meta,
                "ticker": enriched,
            }

    tickers: dict[str, dict[str, Any]] = {}
    meta_map: dict[str, dict[str, str]] = {}
    for item in best_by_base.values():
        tickers[item["symbol"]] = item["ticker"]
        meta_map[item["symbol"]] = item["meta"]

    _crypto_meta = meta_map
    return tickers, meta_map


def _eur_factor_for_symbol(symbol: str) -> float:
    quote = _crypto_meta.get(symbol, {}).get("quote", "USD")
    if quote == "EUR":
        return 1.0
    if quote in ("USD", "UST"):
        try:
            rate_data = _bitfinex_fetch("/ticker/tEURUSD")
            if isinstance(rate_data, list):
                eur_rate = rate_data[6] or rate_data[0] or 1.08
                return 1.0 / float(eur_rate)
        except requests.RequestException:
            return 1.0 / 1.08
    return 1.0


def _parse_candle_rows(data: list, factor: float) -> list[dict[str, Any]]:
    candles = [
        {
            "timestamp": row[0],
            "open": row[1] * factor,
            "close": row[2] * factor,
            "high": row[3] * factor,
            "low": row[4] * factor,
            "volume": row[5],
        }
        for row in data
        if isinstance(row, list) and len(row) >= 6
    ]
    candles.sort(key=lambda c: c["timestamp"])
    return candles


def fetch_candles(
    symbol: str,
    timeframe: str = "1h",
    limit: int = 50,
    *,
    start: int | None = None,
    end: int | None = None,
) -> list[dict[str, Any]]:
    symbol = normalize_symbol(symbol)
    if not is_valid_trading_symbol(symbol):
        return []

    capped = max(1, min(int(limit), CANDLES_MAX_LIMIT))
    path = f"/candles/trade:{timeframe}:{symbol}/hist"
    params = [f"limit={capped}"]
    if start is not None:
        params.append(f"start={int(start)}")
    if end is not None:
        params.append(f"end={int(end)}")
    query = "&".join(params)

    try:
        # HUOM: EI sort=1 — Bitfinexillä sort=1 + limit palauttaa VANHIMMAT kynttilät
        # (kolikon koko historian alusta, esim. 2016), ei tuoreimpia. Oletus (uusin
        # ensin) antaa viimeiset `limit` kynttilää; järjestetään alla vanhin→uusin.
        data = _bitfinex_fetch(f"{path}?{query}")
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code in (404, 429):
            return []
        raise
    except requests.RequestException as exc:
        logger.warning("Candles fetch failed for %s: %s", symbol, exc)
        return []
    if not isinstance(data, list):
        raise RuntimeError("Unexpected API response")

    return _parse_candle_rows(data, _eur_factor_for_symbol(symbol))


def fetch_candle_history(
    symbol: str,
    timeframe: str = "1h",
    *,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    """Hae viimeisimmät `limit` kynttilää (max 10 000 ≈ 416 pv @ 1h)."""
    return fetch_candles(symbol, timeframe, limit=limit)
