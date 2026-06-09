const PRICE_INTERVAL = 15000;
const AI_EVENT_LIMIT = 20;
const INITIAL_CAPITAL = 1000;

let running = false;
let priceTimer = null;
let tradeTimer = null;
let countdownTimer = null;
let countdown = 60;
let tradeIntervalSec = 60;

let state = {
  tickers: {},
  analyses: {},
  profitWatch: {},
  activeSymbols: [],
  portfolio: { holdings: {}, cash: INITIAL_CAPITAL, trades: [] },
  aiEvents: [],
  lastAIReport: null,
  stats: {},
};

let marketSearch = "";
const cryptoLabels = {};

function getCsrfToken() {
  const match = document.cookie.match(/csrftoken=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : "";
}

async function apiPost(url) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "X-CSRFToken": getCsrfToken() },
    credentials: "same-origin",
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || `Virhe ${res.status}`);
  return data;
}

function formatEur(value) {
  if (!Number.isFinite(value)) return "—";
  return new Intl.NumberFormat("fi-FI", {
    style: "currency",
    currency: "EUR",
    minimumFractionDigits: 2,
    maximumFractionDigits: value < 1 ? 4 : 2,
  }).format(value);
}

function formatCrypto(value, decimals = 6) {
  if (!Number.isFinite(value)) return "—";
  return value.toLocaleString("fi-FI", {
    minimumFractionDigits: 0,
    maximumFractionDigits: decimals,
  });
}

function formatPct(value) {
  if (!Number.isFinite(value)) return "—";
  const sign = value >= 0 ? "+" : "";
  return `${sign}${value.toFixed(2)} %`;
}

function formatTime(isoOrDate) {
  const date = typeof isoOrDate === "string" ? new Date(isoOrDate) : isoOrDate;
  return date.toLocaleTimeString("fi-FI", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function formatDateTime(iso) {
  return new Date(iso).toLocaleString("fi-FI", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function formatVolumeEur(value) {
  if (!Number.isFinite(value)) return "—";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)} M€`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(0)} k€`;
  return `${value.toFixed(0)} €`;
}

function getCryptoLabel(symbol) {
  if (cryptoLabels[symbol]) return cryptoLabels[symbol];
  const body = symbol.replace(/^t/, "");
  for (const quote of ["USD", "UST", "EUR"]) {
    if (body.endsWith(quote)) return body.slice(0, -quote.length);
  }
  return body;
}

function applyPayload(data) {
  state = {
    ...state,
    ...data,
    tickers: data.tickers || state.tickers,
    analyses: data.analyses || state.analyses,
    profitWatch: data.profitWatch || state.profitWatch,
    activeSymbols: data.activeSymbols || state.activeSymbols,
    portfolio: data.portfolio || state.portfolio,
    aiEvents: data.aiEvents || state.aiEvents,
    lastAIReport: data.lastAIReport ?? state.lastAIReport,
    stats: data.stats || state.stats,
  };
  running = !!data.running;
  if (data.tradeIntervalSec) tradeIntervalSec = data.tradeIntervalSec;

  if (data.error) showError(data.error);
  else clearError();

  renderAll(data.lastUpdate);
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

function renderAll(lastUpdate) {
  els.btnStart.disabled = running;
  els.btnStop.disabled = !running;
  if (lastUpdate) {
    els.lastUpdate.textContent = `Päivitetty ${formatTime(lastUpdate)}`;
  }
  renderStats();
  renderMarketList();
  renderPortfolio();
  renderTradeLog();
  renderAIDecision(state.lastAIReport);
}

function renderStats() {
  const s = state.stats;
  els.statPortfolio.textContent = formatEur(s.totalValue ?? INITIAL_CAPITAL);
  els.statCash.textContent = formatEur(s.cash ?? INITIAL_CAPITAL);
  els.statTrades.textContent = String(s.tradeCount ?? 0);
  els.statTaxPaid.textContent = formatEur(s.totalTaxPaid ?? 0);
  els.statTaxEstimate.textContent = `Arvio avoimista: ${formatEur(s.estimatedTax ?? 0)}`;

  const pnl = s.pnl ?? 0;
  const pnlPct = s.pnlPct ?? 0;
  const pnlClass = pnl > 0 ? "positive" : pnl < 0 ? "negative" : "neutral";
  const sign = pnl >= 0 ? "+" : "";
  const taxNote = (s.totalTaxPaid ?? 0) > 0 ? ` · vero ${formatEur(s.totalTaxPaid)}` : "";
  els.statPnl.textContent = `${sign}${formatEur(pnl).replace("€", "").trim()} € (${formatPct(pnlPct)})${taxNote}`;
  els.statPnl.className = `stat-change ${pnlClass}`;
}

function renderMarketList() {
  const query = marketSearch.trim().toLowerCase();
  let entries = Object.entries(state.tickers);

  if (query) {
    entries = entries.filter(([symbol]) => {
      const label = getCryptoLabel(symbol).toLowerCase();
      return label.includes(query) || symbol.toLowerCase().includes(query);
    });
  }

  const activeSet = new Set(state.activeSymbols);
  entries.sort((a, b) => {
    const aHeld = activeSet.has(a[0]) ? 1 : 0;
    const bHeld = activeSet.has(b[0]) ? 1 : 0;
    if (aHeld !== bHeld) return bHeld - aHeld;
    return (b[1].volumeEur || 0) - (a[1].volumeEur || 0);
  });

  els.marketCount.textContent = `${Object.keys(state.tickers).length} kryptoparia Bitfinexissä · salkussa ${activeSet.size}/4`;

  if (entries.length === 0) {
    els.marketList.innerHTML = '<p class="empty-log">Ei hakutuloksia.</p>';
    return;
  }

  els.marketList.innerHTML = entries
    .map(([symbol, ticker]) => {
      const label = getCryptoLabel(symbol);
      const analysis = state.analyses[symbol];
      const changeClass = ticker.changePct >= 0 ? "up" : "down";
      const isHeld = activeSet.has(symbol);
      const watch = state.profitWatch[symbol];
      const signal = analysis?.action === "buy" ? "▲" : analysis?.action === "sell" ? "▼" : "●";

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
  const portfolio = state.portfolio;
  const tickers = state.tickers;
  const totalValue = state.stats.totalValue ?? portfolio.cash;

  if (Object.keys(portfolio.holdings || {}).length === 0 && portfolio.cash === INITIAL_CAPITAL) {
    els.portfolioBody.innerHTML = `
      <tr><td colspan="6" style="color:var(--muted);padding:20px 8px">
        Käynnistä botti — AI valitsee 3–4 parasta kryptoa kaikista Bitfinex-markkinoista.
      </td></tr>`;
    return;
  }

  const rows = [];
  for (const [symbol, holding] of Object.entries(portfolio.holdings || {})) {
    const ticker = tickers[symbol];
    if (!ticker) continue;

    const value = holding.amount * ticker.last;
    const share = totalValue > 0 ? (value / totalValue) * 100 : 0;
    const changeClass = ticker.changePct >= 0 ? "up" : "down";
    const watch = state.profitWatch[symbol];
    const watchNote = watch
      ? `<br><span style="font-size:0.75rem;color:var(--muted)">${watch.statusText}</span>`
      : "";

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

function renderAIEventLog() {
  if (!state.aiEvents.length) {
    return `<p class="ai-placeholder">Ei tapahtumia vielä.</p>`;
  }

  const typeLabels = {
    buy: "OSTO",
    sell: "MYYNTI",
    hold: "PIDÄ",
    watch: "SEURANTA",
    info: "INFO",
  };

  return state.aiEvents
    .slice(0, AI_EVENT_LIMIT)
    .map(
      (ev) => `
    <div class="ai-event-item ${ev.type}">
      <div class="ai-event-head">
        <span class="ai-decision-type ${ev.type === "info" ? "hold" : ev.type}">${typeLabels[ev.type] || ev.type.toUpperCase()}</span>
        <strong>${ev.label}</strong>
        ${ev.amount != null ? `<span class="ai-decision-amount">${formatEur(ev.amount)}</span>` : ""}
        <span class="ai-event-time">${formatDateTime(ev.timestamp)}</span>
      </div>
      <p class="ai-decision-reason">${ev.reason}</p>
    </div>`
    )
    .join("");
}

function renderAIDecision(report) {
  if (!report && !state.aiEvents.length) {
    els.aiDecision.innerHTML =
      '<p class="ai-placeholder">Käynnistä botti näyttääksesi AI:n osto- ja myyntipäätökset.</p>';
    return;
  }

  const iconMap = { buy: "📈", sell: "📉", hold: "⏳", mixed: "⚖️" };
  const action = report?.action || "hold";
  const icon = iconMap[action] || "⏳";

  const headerHtml = report
    ? `
    <div class="ai-action">
      <div class="ai-action-icon ${action === "mixed" ? "hold" : action}">${icon}</div>
      <div class="ai-action-text">
        <h3>${report.title}</h3>
        <p>${report.subtitle}</p>
      </div>
    </div>`
    : "";

  els.aiDecision.innerHTML = `
    ${headerHtml}
    <div class="ai-reasoning">
      <div class="ai-section ai-event-section">
        <h4 class="ai-section-title">Viimeiset ${AI_EVENT_LIMIT} tapahtumaa</h4>
        <div class="ai-event-log">${renderAIEventLog()}</div>
      </div>
      ${
        report
          ? `<p class="ai-decision-meta">Analysoitu ${Object.keys(state.tickers).length} kryptoparia · ${report.timestamp ? `Päivitetty ${formatTime(report.timestamp)}` : ""}</p>`
          : ""
      }
    </div>
  `;
}

function getTradePnlBadge(trade) {
  if (trade.type === "tax") return "";

  if (trade.type === "sell") {
    const costBasis = trade.costBasis ?? trade.eurTotal - (trade.profitLoss ?? trade.profit ?? 0);
    const profitLoss = trade.profitLoss ?? trade.profit ?? trade.eurTotal - costBasis;
    if (!costBasis) return "";
    const pct = (profitLoss / costBasis) * 100;
    const cls = pct >= 0 ? "up" : "down";
    const sign = pct >= 0 ? "+" : "";
    return `<span class="trade-pnl ${cls}" title="Myyntihetkellä">Myynti ${sign}${pct.toFixed(2)} %</span>`;
  }

  const ticker = state.tickers[trade.symbol];
  if (!ticker || !trade.price) return "";

  const stillHeld = Object.prototype.hasOwnProperty.call(state.portfolio.holdings || {}, trade.symbol);
  const pct = ((ticker.last - trade.price) / trade.price) * 100;
  const cls = pct >= 0 ? "up" : "down";
  const sign = pct >= 0 ? "+" : "";
  const note = stillHeld ? "" : " · myyty";
  return `<span class="trade-pnl ${cls}" title="Ostohintaan verrattuna">Nyt ${sign}${pct.toFixed(2)} %${note}</span>`;
}

function renderTradeLog() {
  const trades = state.portfolio.trades || [];
  if (!trades.length) {
    els.tradeLog.innerHTML = '<p class="empty-log">Ei kauppoja vielä.</p>';
    return;
  }

  els.tradeLog.innerHTML = trades
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
      const pnlBadge = getTradePnlBadge(trade);
      let pnlSub = "";
      if (trade.type === "sell") {
        const pl = trade.profitLoss ?? trade.profit ?? 0;
        if (pl !== 0) {
          const sign = pl >= 0 ? "+" : "";
          pnlSub = ` · ${sign}${formatEur(pl)}`;
        }
      }
      return `
        <div class="trade-item">
          <span class="trade-type ${trade.type}">${typeLabel}</span>
          <div class="trade-details">
            <div class="main">${label} · ${formatEur(trade.eurTotal)}${taxNote}${pnlBadge ? ` ${pnlBadge}` : ""}</div>
            <div class="sub">${formatCrypto(trade.amount, 6)} @ ${formatEur(trade.price)}${pnlSub} — ${trade.reason}</div>
          </div>
          <span class="trade-time">${formatTime(trade.timestamp)}</span>
        </div>`;
    })
    .join("");
}

function startCountdown() {
  countdown = tradeIntervalSec;
  els.statNext.textContent = `${countdown}s`;
  els.statNext.classList.add("status-running");

  if (countdownTimer) clearInterval(countdownTimer);
  countdownTimer = setInterval(() => {
    countdown--;
    if (countdown <= 0) countdown = tradeIntervalSec;
    els.statNext.textContent = `${countdown}s`;
  }, 1000);
}

async function refreshPrices() {
  try {
    const data = await apiPost("/api/prices/");
    applyPayload(data);
  } catch (err) {
    showError(err.message);
    els.lastUpdate.textContent = "Virhe kurssien haussa";
  }
}

async function executeTradingCycle() {
  try {
    const data = await apiPost("/api/trade/");
    applyPayload(data);
    countdown = tradeIntervalSec;
  } catch (err) {
    showError(err.message);
  }
}

async function startBot() {
  if (running) return;
  try {
    const data = await apiPost("/api/bot/start/");
    applyPayload(data);
    running = true;
    els.btnStart.disabled = true;
    els.btnStop.disabled = false;
    startCountdown();
    priceTimer = setInterval(refreshPrices, PRICE_INTERVAL);
    tradeTimer = setInterval(executeTradingCycle, tradeIntervalSec * 1000);
  } catch (err) {
    showError(err.message);
  }
}

async function stopBot() {
  try {
    const data = await apiPost("/api/bot/stop/");
    applyPayload(data);
  } catch (err) {
    showError(err.message);
  }
  running = false;
  els.btnStart.disabled = false;
  els.btnStop.disabled = true;
  els.statNext.textContent = "—";
  els.statNext.classList.remove("status-running");
  if (priceTimer) clearInterval(priceTimer);
  if (tradeTimer) clearInterval(tradeTimer);
  if (countdownTimer) clearInterval(countdownTimer);
}

async function resetBot() {
  await stopBot();
  try {
    const data = await apiPost("/api/bot/reset/");
    applyPayload(data);
    els.tradeLog.innerHTML = '<p class="empty-log">Ei kauppoja vielä.</p>';
    els.lastUpdate.textContent = "Päivitetään…";
  } catch (err) {
    showError(err.message);
  }
}

els.btnStart.addEventListener("click", startBot);
els.btnStop.addEventListener("click", stopBot);
els.btnReset.addEventListener("click", resetBot);
els.marketSearch.addEventListener("input", (e) => {
  marketSearch = e.target.value;
  renderMarketList();
});

const botUrlEl = document.getElementById("bot-url");
if (botUrlEl) {
  botUrlEl.href = location.origin;
  botUrlEl.textContent = location.origin;
}

fetch("/api/state/")
  .then((r) => r.json())
  .then((data) => {
    applyPayload(data);
    refreshPrices();
  })
  .catch((err) => showError(err.message));
