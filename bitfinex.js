const BITFINEX_DIRECT = "https://api-pub.bitfinex.com/v2";
const CORS_PROXY = "https://api.allorigins.win/raw?url=";

const QUOTE_CURRENCIES = ["UST", "USD", "EUR"];
const EXCLUDED_BASES = new Set([
  "UST", "EUT", "EUR", "USD", "TEST", "TESTUSD", "USDT", "USDC",
  "DAI", "TUSD", "USDD", "USDR", "EURQ", "USDQ", "XAUT", "CNHT",
]);

const QUOTE_PRIORITY = { USD: 0, UST: 1, EUR: 2 };

async function bitfinexFetch(path) {
  const directUrl = `${BITFINEX_DIRECT}${path}`;

  if (typeof location !== "undefined" && location.protocol.startsWith("http")) {
    const localUrl = `${location.origin}/api/bitfinex/v2${path}`;
    try {
      const localRes = await fetch(localUrl);
      if (localRes.ok) return localRes;
    } catch {
      // fallback below
    }
  }

  return fetch(`${CORS_PROXY}${encodeURIComponent(directUrl)}`);
}

/** @typedef {{ symbol: string, id: string, quote: string, pairLabel: string }} CryptoMeta */
/** @typedef {{ symbol: string, bid: number, ask: number, last: number, change24h: number, changePct: number, high: number, low: number, volume: number, volumeEur: number }} Ticker */
/** @typedef {{ timestamp: number, open: number, close: number, high: number, low: number, volume: number }} Candle */

/** @type {Map<string, CryptoMeta>} */
export let cryptoMeta = new Map();

/**
 * @param {string} symbol
 */
export function parsePairSymbol(symbol) {
  if (!symbol.startsWith("t")) return null;
  const body = symbol.slice(1);
  for (const quote of QUOTE_CURRENCIES) {
    if (body.endsWith(quote) && body.length > quote.length) {
      const base = body.slice(0, -quote.length);
      if (!base || EXCLUDED_BASES.has(base)) return null;
      return { base, quote };
    }
  }
  return null;
}

/**
 * @param {string} symbol
 */
export function getCryptoLabel(symbol) {
  return cryptoMeta.get(symbol)?.id || symbol.replace(/^t/, "").replace(/(USD|UST|EUR)$/, "");
}

/**
 * @param {unknown[][]} rows
 */
function parseTickerRows(rows) {
  /** @type {Map<string, Ticker>} */
  const raw = new Map();

  for (const row of rows) {
    if (!Array.isArray(row) || row.length < 8) continue;
    const symbol = row[0];
    if (typeof symbol !== "string" || !symbol.startsWith("t")) continue;

    raw.set(symbol, {
      symbol,
      bid: row[1] ?? 0,
      ask: row[3] ?? 0,
      change24h: row[5] ?? 0,
      changePct: (row[6] ?? 0) * 100,
      last: row[7] ?? row[1] ?? 0,
      volume: row[8] ?? 0,
      high: row[9] ?? 0,
      low: row[10] ?? 0,
      volumeEur: 0,
    });
  }

  return raw;
}

/**
 * @param {number} price
 * @param {string} quote
 * @param {number} eurRate
 */
function toEur(price, quote, eurRate) {
  if (quote === "EUR") return price;
  if (quote === "USD" || quote === "UST") return price / eurRate;
  return price;
}

/**
 * @returns {Promise<{ tickers: Map<string, Ticker>, meta: Map<string, CryptoMeta> }>}
 */
export async function fetchAllMarkets() {
  const res = await bitfinexFetch("/tickers?symbols=ALL");
  if (!res.ok) {
    const err = await res.text().catch(() => "");
    throw new Error(`Bitfinex ticker error ${res.status}${err ? `: ${err}` : ""}`);
  }

  const data = await res.json();
  if (data.error) throw new Error(data.error);
  if (!Array.isArray(data)) throw new Error("Unexpected API response");

  const raw = parseTickerRows(data);
  const eurUsd = raw.get("tEURUSD");
  const eurRate = eurUsd?.last || eurUsd?.bid || 1.08;

  /** @type {Map<string, { symbol: string, meta: CryptoMeta, ticker: Ticker, score: number }>} */
  const bestByBase = new Map();

  for (const [symbol, ticker] of raw) {
    const parsed = parsePairSymbol(symbol);
    if (!parsed || ticker.last <= 0) continue;

    const lastEur = toEur(ticker.last, parsed.quote, eurRate);
    const volumeEur = toEur(ticker.volume * ticker.last, parsed.quote, eurRate);

    if (volumeEur < 500) continue;

    const enriched = {
      ...ticker,
      last: lastEur,
      bid: toEur(ticker.bid, parsed.quote, eurRate),
      ask: toEur(ticker.ask, parsed.quote, eurRate),
      high: toEur(ticker.high, parsed.quote, eurRate),
      low: toEur(ticker.low, parsed.quote, eurRate),
      change24h: toEur(ticker.change24h, parsed.quote, eurRate),
      volumeEur,
    };

    const meta = {
      symbol,
      id: parsed.base,
      quote: parsed.quote,
      pairLabel: `${parsed.base}/${parsed.quote}`,
    };

    const priority = QUOTE_PRIORITY[parsed.quote] ?? 9;
    const existing = bestByBase.get(parsed.base);

    if (
      !existing ||
      enriched.volumeEur > existing.ticker.volumeEur ||
      (enriched.volumeEur === existing.ticker.volumeEur && priority < (QUOTE_PRIORITY[existing.meta.quote] ?? 9))
    ) {
      bestByBase.set(parsed.base, { symbol, meta, ticker: enriched, score: enriched.volumeEur });
    }
  }

  /** @type {Map<string, Ticker>} */
  const tickers = new Map();
  /** @type {Map<string, CryptoMeta>} */
  const meta = new Map();

  for (const { symbol, meta: m, ticker } of bestByBase.values()) {
    tickers.set(symbol, ticker);
    meta.set(symbol, m);
  }

  cryptoMeta = meta;
  return { tickers, meta };
}

/** @deprecated use fetchAllMarkets */
export const CRYPTOS = [];

/**
 * @param {string} symbol
 * @param {string} timeframe
 * @param {number} limit
 */
export async function fetchCandles(symbol, timeframe = "1h", limit = 50) {
  const res = await bitfinexFetch(`/candle/trade:${timeframe}:${symbol}/hist?limit=${limit}`);
  if (!res.ok) throw new Error(`Bitfinex candles error: ${res.status}`);
  const data = await res.json();

  if (data.error) throw new Error(data.error);
  if (!Array.isArray(data)) throw new Error("Unexpected API response");

  const quote = cryptoMeta.get(symbol)?.quote ?? "USD";
  let eurRate = 1;

  if (quote === "USD" || quote === "UST") {
    const rateRes = await bitfinexFetch("/ticker/tEURUSD");
    if (rateRes.ok) {
      const rateData = await rateRes.json();
      eurRate = rateData[6] || rateData[0] || 1.08;
    } else {
      eurRate = 1.08;
    }
  }

  const factor = quote === "EUR" ? 1 : 1 / eurRate;

  return data
    .map((row) => ({
      timestamp: row[0],
      open: row[1] * factor,
      close: row[2] * factor,
      high: row[3] * factor,
      low: row[4] * factor,
      volume: row[5],
    }))
    .reverse();
}

export function formatEur(value) {
  if (!Number.isFinite(value)) return "—";
  return new Intl.NumberFormat("fi-FI", {
    style: "currency",
    currency: "EUR",
    minimumFractionDigits: 2,
    maximumFractionDigits: value < 1 ? 4 : 2,
  }).format(value);
}

export function formatCrypto(value, decimals = 6) {
  if (!Number.isFinite(value)) return "—";
  return value.toLocaleString("fi-FI", {
    minimumFractionDigits: 0,
    maximumFractionDigits: decimals,
  });
}

export function formatPct(value) {
  if (!Number.isFinite(value)) return "—";
  const sign = value >= 0 ? "+" : "";
  return `${sign}${value.toFixed(2)} %`;
}

export function formatTime(date) {
  return date.toLocaleTimeString("fi-FI", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function formatVolumeEur(value) {
  if (!Number.isFinite(value)) return "—";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)} M€`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(0)} k€`;
  return `${value.toFixed(0)} €`;
}
