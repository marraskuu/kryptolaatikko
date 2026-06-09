import time
from typing import Any

import requests

BITFINEX_DIRECT = "https://api-pub.bitfinex.com/v2"

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


def _bitfinex_fetch(path: str) -> list | dict:
    url = f"{BITFINEX_DIRECT}{path}"
    res = requests.get(url, timeout=30)
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
        raw[symbol] = {
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
    return raw


def _to_eur(price: float, quote: str, eur_rate: float) -> float:
    if quote == "EUR":
        return price
    if quote in ("USD", "UST"):
        return price / eur_rate
    return price


def fetch_all_markets() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, str]]]:
    global _crypto_meta
    data = _bitfinex_fetch("/tickers?symbols=ALL")
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


def fetch_candles(symbol: str, timeframe: str = "1h", limit: int = 50) -> list[dict[str, Any]]:
    symbol = normalize_symbol(symbol)
    if not is_valid_trading_symbol(symbol):
        return []

    path = f"/candles/trade:{timeframe}:{symbol}/hist"
    try:
        data = _bitfinex_fetch(f"{path}?limit={limit}&sort=1")
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code in (404, 429):
            return []
        raise
    if not isinstance(data, list):
        raise RuntimeError("Unexpected API response")

    quote = _crypto_meta.get(symbol, {}).get("quote", "USD")
    eur_rate = 1.0
    if quote in ("USD", "UST"):
        try:
            rate_data = _bitfinex_fetch("/ticker/tEURUSD")
            if isinstance(rate_data, list):
                eur_rate = rate_data[6] or rate_data[0] or 1.08
        except requests.RequestException:
            eur_rate = 1.08

    factor = 1.0 if quote == "EUR" else 1.0 / eur_rate
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
    ]
    candles.reverse()
    return candles
