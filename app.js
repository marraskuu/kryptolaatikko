import {
  fetchAllMarkets,
  fetchCandles,
  getCryptoLabel,
  formatEur,
  formatCrypto,
  formatPct,
  formatTime,
  formatVolumeEur,
} from "./bitfinex.js";
import {
  analyzeMarket,
  analyzeTickerQuick,
  makeTradingDecisions,
  summarizeDecision,
} from "./ai-trader.js";
import { portfolio } from "./portfolio.js";
import { downloadTaxExcel } from "./export.js";
import { updateProfitSell, resetAllWatches, resetWatch } from "./sell-strategy.js";

const INITIAL_CAPITAL = 1000;
const PRICE_INTERVAL = 15000;
const TRADE_INTERVAL = 60000;
const DEEP_ANALYSIS_COUNT = 30;

let running = false;
let priceTimer = null;
let tradeTimer = null;
let countdown = TRADE_INTERVAL / 1000;
let countdownTimer = null;
let marketSearch = "";

/** @type {Map<string, import('./bitfinex.js').Ticker>} */
let tickers = new Map();
/** @type {Map<string, ReturnType<typeof analyzeMarket>>} */
let analyses = new Map();
/** @type {Map<string, ReturnType<typeof updateProfitSell>>} */
let profitWatch = new Map();
/** @type {Set<string>} */
let activeSymbols = new Set();

function checkProfitSells() {
  let sold = false;

  for (const [symbol, holding] of portfolio.holdings) {
    const ticker = tickers.get(symbol);
    if (!ticker) continue;

    const result = updateProfitSell(symbol, ticker.last, holding.avgPrice);
    profitWatch.set(symbol, result);

    if (result.shouldSell) {
      portfolio.sell(symbol, holding.amount, ticker.last, result.reason);
      resetWatch(symbol);
      profitWatch.delete(symbol);
      sold = true;
    }
  }

  if (sold) {
    renderTradeLog();
    renderPortfolio();
    renderStats();
    renderMarketList();
  }
}

const els = {
  btnStart: document.getElementById("btn-start"),
  btnStop: document.getElementById("btn-stop"),
  btnReset: document.getElementById("btn-reset"),
  statPortfolio: document.getElementById("stat-portfolio"),
  statPnl: document.getElementById("stat-pnl"),
  statCash: document.getElementById("stat-cash"),
  statTaxPaid: document.getElementById("stat-tax-paid"),
  statTaxEstimate: document.getElementById("stat-tax-estimate"),
  statTrades: document.getElementById("stat-trades"),
  statNext: document.getElementById("stat-next"),
  lastUpdate: document.getElementById("last-update"),
  marketList: document.getElementById("market-list"),
  marketCount: document.getElementById("market-count"),
  marketSearch: document.getElementById("market-search"),
  aiDecision: document.getElementById("ai-decision"),
  portfolioBody: document.getElementById("portfolio-body"),
  tradeLog: document.getElementById("trade-log"),
  btnExport: document.getElementById("btn-export"),
  errorBanner: document.getElementById("error-banner"),
};

function showError(message) {
  els.errorBanner.textContent = message;
  els.errorBanner.classList.remove("hidden");
}

function clearError() {
  els.errorBanner.classList.add("hidden");
  els.errorBanner.textContent = "";
}

async function refreshPrices() {
  try {
    if (location.protocol === "file:") {
      throw new Error(
        "Sivu on avattu tiedostosta. Kaynnista palvelin: .\\start.ps1 ja avaa http://localhost:3000"
      );
    }

    const { tickers: allTickers } = await fetchAllMarkets();
    tickers = allTickers;

    if (tickers.size === 0) {
      throw new Error("Bitfinex ei palauttanut kursseja. Tarkista internet-yhteys.");
    }

    clearError();
    els.lastUpdate.textContent = `Paivitetty ${formatTime(new Date())}`;
    els.marketCount.textContent = `${tickers.size} kryptoparia Bitfinexissä · salkussa ${activeSymbols.size}/4`;

    for (const [symbol, ticker] of tickers) {
      if (!analyses.has(symbol) || analyses.get(symbol)?.quick) {
        analyses.set(symbol, analyzeTickerQuick(ticker));
      }
    }

    renderMarketList();
    renderPortfolio();
    renderStats();

    if (running) {
      checkProfitSells();
    }
  } catch (err) {
    console.error("Hintojen haku epaonnistui:", err);
    const msg =
      err.message === "Failed to fetch"
        ? "Yhteys Bitfinexiin epaonnistui. Kaynnista: .\\start.ps1 ja avaa http://localhost:3000"
        : err.message;
    showError(msg);
    els.lastUpdate.textContent = "Virhe kurssien haussa";
  }
}

function selectCandidatesForDeepAnalysis() {
  const candidates = new Set();

  for (const symbol of portfolio.holdings.keys()) {
    candidates.add(symbol);
  }

  const ranked = [...tickers.entries()]
    .sort((a, b) => b[1].volumeEur - a[1].volumeEur)
    .map(([symbol]) => symbol);

  for (const symbol of ranked) {
    if (candidates.size >= DEEP_ANALYSIS_COUNT) break;
    candidates.add(symbol);
  }

  return [...candidates];
}

async function refreshAnalyses() {
  for (const [symbol, ticker] of tickers) {
    analyses.set(symbol, analyzeTickerQuick(ticker));
  }

  const candidates = selectCandidatesForDeepAnalysis();

  for (const symbol of candidates) {
    try {
      const candles = await fetchCandles(symbol, "1h", 50);
      if (candles.length >= 20) {
        const deep = analyzeMarket(candles);
        const ticker = tickers.get(symbol);
        if (ticker) {
          deep.currentPrice = ticker.last;
          deep.volumeEur = ticker.volumeEur;
        }
        analyses.set(symbol, deep);
      }
    } catch (err) {
      console.warn(`Syva analyysi epaonnistui ${symbol}:`, err);
    }
  }
}

async function executeTradingCycle() {
  await refreshPrices();
  await refreshAnalyses();

  checkProfitSells();

  const totalValue = portfolio.getTotalValue(tickers);
  const { decisions, topSymbols, initialAllocation } = makeTradingDecisions(
    analyses,
    portfolio,
    totalValue,
    getCryptoLabel
  );
  activeSymbols = topSymbols;

  if (initialAllocation?.length) {
    portfolio.allocateInitial(
      initialAllocation.map(({ symbol, analysis }, i) => ({
        symbol,
        price: analysis.currentPrice,
        reason: `Alkuallokaatio — ${getCryptoLabel(symbol)} (${i + 1}/${initialAllocation.length})`,
      }))
    );
  }

  const sells = decisions.filter((d) => d.type === "sell");
  for (const d of sells) {
    portfolio.sell(d.symbol, d.amount, d.analysis.currentPrice, d.reason);
  }

  const buys = decisions.filter((d) => d.type === "buy");
  for (const d of buys) {
    const ok = portfolio.buy(d.symbol, d.eurAmount, d.analysis.currentPrice, d.reason);
    if (!ok) {
      console.warn(`Osto epaonnistui ${d.symbol}`, d.eurAmount);
    }
  }

  els.marketCount.textContent = `${tickers.size} kryptoparia Bitfinexissä · salkussa ${activeSymbols.size}/4`;

  renderAIDecision(decisions);
  renderMarketList();
  renderPortfolio();
  renderStats();
  renderTradeLog();
}

function renderStats() {
  const totalValue = portfolio.getTotalValue(tickers);
  const { pnl, pnlPct } = portfolio.getPnL(totalValue);
  const { totalTaxPaid, estimatedTax } = portfolio.getTaxSummary(tickers);

  els.statPortfolio.textContent = formatEur(totalValue);
  els.statCash.textContent = formatEur(portfolio.cash);
  els.statTrades.textContent = String(portfolio.trades.filter((t) => t.type !== "tax").length);
  els.statTaxPaid.textContent = formatEur(totalTaxPaid);
  els.statTaxEstimate.textContent = `Arvio avoimista: ${formatEur(estimatedTax)}`;

  const pnlClass = pnl > 0 ? "positive" : pnl < 0 ? "negative" : "neutral";
  const sign = pnl >= 0 ? "+" : "";
  const taxNote = totalTaxPaid > 0 ? ` · vero ${formatEur(totalTaxPaid)}` : "";
  els.statPnl.textContent = `${sign}${formatEur(pnl).replace("€", "").trim()} € (${formatPct(pnlPct)})${taxNote}`;
  els.statPnl.className = `stat-change ${pnlClass}`;
}

function renderMarketList() {
  const query = marketSearch.trim().toLowerCase();
  let entries = [...tickers.entries()];

  if (query) {
    entries = entries.filter(([symbol]) => {
      const label = getCryptoLabel(symbol).toLowerCase();
      return label.includes(query) || symbol.toLowerCase().includes(query);
    });
  }

  entries.sort((a, b) => {
    const aHeld = activeSymbols.has(a[0]) ? 1 : 0;
    const bHeld = activeSymbols.has(b[0]) ? 1 : 0;
    if (aHeld !== bHeld) return bHeld - aHeld;
    return b[1].volumeEur - a[1].volumeEur;
  });

  if (entries.length === 0) {
    els.marketList.innerHTML = '<p class="empty-log">Ei hakutuloksia.</p>';
    return;
  }

  els.marketList.innerHTML = entries
    .map(([symbol, ticker]) => {
      const label = getCryptoLabel(symbol);
      const analysis = analyses.get(symbol);
      const changeClass = ticker.changePct >= 0 ? "up" : "down";
      const isHeld = activeSymbols.has(symbol);
      const watch = profitWatch.get(symbol);
      const signal =
        analysis?.action === "buy" ? "▲" : analysis?.action === "sell" ? "▼" : "●";

      let badge = "";
      if (isHeld && watch) {
        badge = `<span class="market-row-badge">${watch.statusText}</span>`;
      } else if (isHeld) {
        badge = `<span class="market-row-badge">${signal} Salkussa</span>`;
      }

      return `
        <div class="market-row ${isHeld ? "selected" : ""}">
          <div>
            <div class="market-row-id">${label}</div>
            <div class="market-row-pair">${symbol.replace(/^t/, "")} · vol ${formatVolumeEur(ticker.volumeEur)}</div>
          </div>
          <div class="market-row-price">${formatEur(ticker.last)}</div>
          <div class="market-row-change ${changeClass}">${formatPct(ticker.changePct)}</div>
          ${badge}
        </div>`;
    })
    .join("");
}

function renderPortfolio() {
  const totalValue = portfolio.getTotalValue(tickers);

  if (portfolio.holdings.size === 0 && portfolio.cash === INITIAL_CAPITAL) {
    els.portfolioBody.innerHTML = `
      <tr><td colspan="6" style="color:var(--muted);padding:20px 8px">
        Kaynnista botti — AI valitsee 3-4 parasta kryptoa kaikista Bitfinex-markkinoista.
      </td></tr>`;
    return;
  }

  const rows = [];

  for (const [symbol, holding] of portfolio.holdings) {
    const ticker = tickers.get(symbol);
    if (!ticker) continue;

    const value = holding.amount * ticker.last;
    const share = totalValue > 0 ? (value / totalValue) * 100 : 0;
    const changeClass = ticker.changePct >= 0 ? "up" : "down";
    const profitPct = ((ticker.last - holding.avgPrice) / holding.avgPrice) * 100;
    const watch = profitWatch.get(symbol);
    const watchNote = watch ? `<br><span style="font-size:0.75rem;color:var(--muted)">${watch.statusText}</span>` : "";

    rows.push(`
      <tr>
        <td><strong>${getCryptoLabel(symbol)}</strong>${watchNote}</td>
        <td>${formatCrypto(holding.amount, 6)}</td>
        <td>${formatEur(ticker.last)}</td>
        <td>${formatEur(value)}</td>
        <td>${share.toFixed(1)} %</td>
        <td class="crypto-change ${changeClass}">${formatPct(ticker.changePct)}</td>
      </tr>
    `);
  }

  if (portfolio.cash > 0.01) {
    const share = totalValue > 0 ? (portfolio.cash / totalValue) * 100 : 0;
    rows.push(`
      <tr>
        <td><strong>EUR</strong></td>
        <td>—</td>
        <td>—</td>
        <td>${formatEur(portfolio.cash)}</td>
        <td>${share.toFixed(1)} %</td>
        <td>—</td>
      </tr>
    `);
  }

  els.portfolioBody.innerHTML = rows.join("");
}

function renderAIDecision(decisions) {
  const summary = summarizeDecision(decisions, getCryptoLabel);
  const iconMap = { buy: "📈", sell: "📉", hold: "⏳" };

  const detailsHtml = summary.details
    .map(
      (d) => `
      <div style="margin-top:8px;padding-top:8px;border-top:1px solid var(--border)">
        <strong>${d.symbol}</strong>${d.amount ? ` · ${formatEur(d.amount)}` : ""}
        <br><span style="font-size:0.8rem">${d.reason}</span>
      </div>`
    )
    .join("");

  const metrics = decisions.find((d) => d.analysis && !d.analysis.quick)?.analysis;
  const metricsHtml = metrics
    ? `
    <div class="ai-metrics">
      <span class="metric-chip">RSI ${metrics.rsi.toFixed(1)}</span>
      <span class="metric-chip">Momentum ${metrics.momentum.toFixed(2)} %</span>
      <span class="metric-chip">EMA9 ${formatEur(metrics.ema9)}</span>
      <span class="metric-chip">EMA21 ${formatEur(metrics.ema21)}</span>
    </div>`
    : `<p style="font-size:0.8rem;color:var(--muted);margin-top:8px">Analysoitu ${tickers.size} kryptoparia · syva analyysi ${DEEP_ANALYSIS_COUNT} likvideimmalle</p>`;

  els.aiDecision.innerHTML = `
    <div class="ai-action">
      <div class="ai-action-icon ${summary.action}">${iconMap[summary.action]}</div>
      <div class="ai-action-text">
        <h3>${summary.title}</h3>
        <p>${summary.subtitle}</p>
      </div>
    </div>
    <div class="ai-reasoning">
      <strong>AI:n perustelut:</strong>
      ${detailsHtml || "<p>Ei toimenpiteita talla kierroksella.</p>"}
      ${metricsHtml}
    </div>
  `;
}

function renderTradeLog() {
  if (portfolio.trades.length === 0) {
    els.tradeLog.innerHTML = '<p class="empty-log">Ei kauppoja viela.</p>';
    return;
  }

  els.tradeLog.innerHTML = portfolio.trades
    .slice(0, 50)
    .map((trade) => {
      const label = getCryptoLabel(trade.symbol);
      if (trade.type === "tax") {
        return `
          <div class="trade-item">
            <span class="trade-type tax">VERO</span>
            <div class="trade-details">
              <div class="main">${label} · ${formatEur(trade.eurTotal)}</div>
              <div class="sub">30 % voittovero · voitto ${formatEur(trade.profit)}</div>
            </div>
            <span class="trade-time">${formatTime(trade.timestamp)}</span>
          </div>`;
      }
      const typeLabel = trade.type === "buy" ? "OSTO" : "MYYNTI";
      const taxNote = trade.tax > 0 ? ` · vero ${formatEur(trade.tax)}` : "";
      return `
        <div class="trade-item">
          <span class="trade-type ${trade.type}">${typeLabel}</span>
          <div class="trade-details">
            <div class="main">${label} · ${formatEur(trade.eurTotal)}${taxNote}</div>
            <div class="sub">${formatCrypto(trade.amount, 6)} @ ${formatEur(trade.price)} — ${trade.reason}</div>
          </div>
          <span class="trade-time">${formatTime(trade.timestamp)}</span>
        </div>`;
    })
    .join("");
}

function startCountdown() {
  countdown = TRADE_INTERVAL / 1000;
  els.statNext.textContent = `${countdown}s`;
  els.statNext.classList.add("status-running");

  if (countdownTimer) clearInterval(countdownTimer);
  countdownTimer = setInterval(() => {
    countdown--;
    if (countdown <= 0) countdown = TRADE_INTERVAL / 1000;
    els.statNext.textContent = `${countdown}s`;
  }, 1000);
}

function startBot() {
  if (running) return;
  running = true;
  els.btnStart.disabled = true;
  els.btnStop.disabled = false;

  refreshPrices().then(() => executeTradingCycle());
  startCountdown();

  priceTimer = setInterval(refreshPrices, PRICE_INTERVAL);
  tradeTimer = setInterval(() => {
    executeTradingCycle();
    countdown = TRADE_INTERVAL / 1000;
  }, TRADE_INTERVAL);
}

function stopBot() {
  running = false;
  els.btnStart.disabled = false;
  els.btnStop.disabled = true;
  els.statNext.textContent = "—";
  els.statNext.classList.remove("status-running");

  if (priceTimer) clearInterval(priceTimer);
  if (tradeTimer) clearInterval(tradeTimer);
  if (countdownTimer) clearInterval(countdownTimer);
}

function resetBot() {
  stopBot();
  portfolio.reset();
  resetAllWatches();
  activeSymbols.clear();
  profitWatch.clear();
  analyses.clear();
  tickers.clear();
    '<p class="ai-placeholder">Kaynnista botti aloittaaksesi automaattisen kaupankaynnin.</p>';
  els.tradeLog.innerHTML = '<p class="empty-log">Ei kauppoja viela.</p>';
  els.lastUpdate.textContent = "Paivitetaan…";
  els.marketCount.textContent = "0 kryptoparia";

  renderStats();
  renderMarketList();
  renderPortfolio();
}

els.btnStart.addEventListener("click", startBot);
els.btnStop.addEventListener("click", stopBot);
els.btnReset.addEventListener("click", resetBot);
els.btnExport.addEventListener("click", () => downloadTaxExcel(portfolio, getCryptoLabel));
els.marketSearch.addEventListener("input", (e) => {
  marketSearch = e.target.value;
  renderMarketList();
});

renderStats();
renderMarketList();
renderPortfolio();

const botUrlEl = document.getElementById("bot-url");
if (botUrlEl && location.protocol.startsWith("http")) {
  botUrlEl.href = location.origin;
  botUrlEl.textContent = location.origin;
}

refreshPrices();
