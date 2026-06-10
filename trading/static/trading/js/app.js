const POLL_INTERVAL = 5000;
const AI_EVENT_LIMIT = 20;
const INITIAL_CAPITAL = 1000;

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
let pollTimer = null;
let countdownTimer = null;
let countdown = 60;

async function fetchState() {
  const res = await fetch("/api/state/", {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  });
  const contentType = res.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) {
    throw new Error(`Palvelinvirhe ${res.status}`);
  }
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
    year: "numeric",
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
  const normalized = normalizeSymbol(symbol);
  const body = normalized.replace(/^t/, "");
  for (const quote of ["USD", "UST", "EUR"]) {
    if (body.endsWith(quote)) return body.slice(0, -quote.length);
  }
  return body.replace(/:/g, "");
}

function normalizeSymbol(symbol) {
  if (!symbol) return symbol;
  if (symbol.startsWith("t") && symbol.includes(":")) {
    return "t" + symbol.slice(1).replace(/:/g, "");
  }
  return symbol;
}

function getPositionPct(symbol) {
  const pnl = getPositionPnl(symbol);
  return pnl ? pnl.pnlPct : null;
}

function getPositionPnl(symbol) {
  const key = normalizeSymbol(symbol);
  const holding = state.portfolio.holdings?.[key] || state.portfolio.holdings?.[symbol];
  const ticker = state.tickers[key] || state.tickers[symbol];
  if (!holding || !ticker?.last || !holding.avgPrice) return null;
  const costBasis = holding.amount * holding.avgPrice;
  const currentValue = holding.amount * ticker.last;
  const pnlEur = currentValue - costBasis;
  const pnlPct = ((ticker.last - holding.avgPrice) / holding.avgPrice) * 100;
  return { pnlEur, pnlPct, costBasis, currentValue };
}

function getPortfolioUnrealizedPnl() {
  let costBasis = 0;
  let holdingsValue = 0;
  for (const [symbol, holding] of Object.entries(state.portfolio.holdings || {})) {
    const key = normalizeSymbol(symbol);
    const ticker = state.tickers[key] || state.tickers[symbol];
    if (!ticker?.last) continue;
    costBasis += holding.amount * holding.avgPrice;
    holdingsValue += holding.amount * ticker.last;
  }
  if (costBasis <= 0) return null;
  const pnlEur = holdingsValue - costBasis;
  const pnlPct = (pnlEur / costBasis) * 100;
  return { pnlEur, pnlPct, costBasis, holdingsValue };
}

function formatPnlBadge(pnlEur, pnlPct) {
  const cls = pnlEur >= 0 ? "up" : "down";
  const sign = pnlEur >= 0 ? "+" : "";
  return {
    cls,
    pct: formatPct(pnlPct),
    eur: `${sign}${formatEur(pnlEur).replace("€", "").trim()} €`,
  };
}

function countTradesSince(trades, sinceMs) {
  return trades.filter((t) => {
    if (t.type === "tax") return false;
    const ts = new Date(t.timestamp).getTime();
    return Number.isFinite(ts) && ts >= sinceMs;
  }).length;
}

function getTradeCounts() {
  const trades = state.portfolio?.trades || [];
  const active = trades.filter((t) => t.type !== "tax");
  const now = Date.now();
  const monthStart = new Date(new Date().getFullYear(), new Date().getMonth(), 1).getTime();
  const dayAgo = now - 24 * 60 * 60 * 1000;
  return {
    total: active.length,
    month: countTradesSince(active, monthStart),
    last24h: countTradesSince(active, dayAgo),
  };
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

  if (data.nextTradeInSec != null) countdown = data.nextTradeInSec;

  const providerBadge = document.getElementById("ai-provider-badge");
  const geminiBadge = document.getElementById("gemini-badge");
  let geminiNotice = "";

  const gs = data.geminiStatus;
  const geminiWaiting = gs?.status === "waiting";
  const geminiError = gs?.status === "error";

  if (gs?.ok || gs?.status === "ok") {
    if (providerBadge) {
      providerBadge.textContent = "Gemini AI";
      providerBadge.classList.add("ai-badge-active");
    }
    if (geminiBadge) geminiBadge.classList.add("hidden");
  } else if (gs?.configured && geminiWaiting) {
    if (providerBadge) {
      providerBadge.textContent = "Gemini AI";
      providerBadge.classList.add("ai-badge-active");
    }
  } else if (gs?.configured && geminiError) {
    if (providerBadge) {
      providerBadge.textContent = "Gemini (virhe)";
      providerBadge.classList.remove("ai-badge-active");
    }
    geminiNotice = gs.message || "";
  } else if (gs?.configured) {
    if (providerBadge) {
      providerBadge.textContent = "Gemini AI";
      providerBadge.classList.add("ai-badge-active");
    }
  } else {
    if (providerBadge) {
      providerBadge.textContent = "Tekninen AI";
      providerBadge.classList.remove("ai-badge-active");
    }
    if (data.geminiStatus?.message) {
      geminiNotice = `Gemini: ${data.geminiStatus.message}`;
    }
  }

  if (data.error) showError(data.error);
  else if (geminiNotice) showError(geminiNotice);
  else clearError();
  renderAll(data.lastUpdate);
}

const els = {
  statPortfolio: document.getElementById("stat-portfolio"),
  statPnl: document.getElementById("stat-pnl"),
  statBreakdown: document.getElementById("stat-breakdown"),
  statCash: document.getElementById("stat-cash"),
  statCryptoHoldings: document.getElementById("stat-crypto-holdings"),
  statTaxPaid: document.getElementById("stat-tax-paid"),
  statTaxEstimate: document.getElementById("stat-tax-estimate"),
  statTrades: document.getElementById("stat-trades"),
  statTradesMonth: document.getElementById("stat-trades-month"),
  statTrades24h: document.getElementById("stat-trades-24h"),
  statNext: document.getElementById("stat-next"),
  lastUpdate: document.getElementById("last-update"),
  marketList: document.getElementById("market-list"),
  marketCount: document.getElementById("market-count"),
  marketSearch: document.getElementById("market-search"),
  aiDecision: document.getElementById("ai-decision"),
  portfolioBody: document.getElementById("portfolio-body"),
  portfolioLivePnl: document.getElementById("portfolio-live-pnl"),
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
  if (lastUpdate) {
    els.lastUpdate.textContent = `Päivitetty ${formatTime(lastUpdate)}`;
  }
  els.statNext.textContent = `${countdown}s`;
  els.statNext.classList.add("status-running");
  renderStats();
  renderMarketList();
  renderPortfolio();
  renderTradeLog();
  renderAIDecision(state.lastAIReport);
}

function renderStats() {
  const s = state.stats;
  const total = s.totalValue ?? INITIAL_CAPITAL;
  const cash = Math.max(0, s.cash ?? 0);
  const holdings =
    s.holdingsValue != null ? s.holdingsValue : Math.max(0, total - cash);

  els.statPortfolio.textContent = formatEur(total);
  els.statCryptoHoldings.textContent = formatEur(holdings);
  els.statCash.textContent =
    cash > 1 ? `Vapaa käteinen: ${formatEur(cash)}` : "Kaikki sijoitettu";
  els.statBreakdown.textContent = `${formatEur(holdings)} kryptot + ${formatEur(cash)} käteistä = ${formatEur(total)}`;
  const tradeCounts = getTradeCounts();
  els.statTrades.textContent = String(s.tradeCount ?? tradeCounts.total);
  if (els.statTradesMonth) {
    els.statTradesMonth.textContent = `Tässä kuussa: ${tradeCounts.month}`;
  }
  if (els.statTrades24h) {
    els.statTrades24h.textContent = `Viime 24 h: ${tradeCounts.last24h}`;
  }
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

function getHeldSymbolsSet() {
  return new Set(
    Object.keys(state.portfolio.holdings || {}).map((symbol) => normalizeSymbol(symbol))
  );
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

  const heldSet = getHeldSymbolsSet();
  const targetSet = new Set((state.activeSymbols || []).map((s) => normalizeSymbol(s)));
  entries.sort((a, b) => {
    const aSym = normalizeSymbol(a[0]);
    const bSym = normalizeSymbol(b[0]);
    const aHeld = heldSet.has(aSym) ? 1 : 0;
    const bHeld = heldSet.has(bSym) ? 1 : 0;
    if (aHeld !== bHeld) return bHeld - aHeld;
    return (b[1].volumeEur || 0) - (a[1].volumeEur || 0);
  });

  els.marketCount.textContent = `${Object.keys(state.tickers).length} kryptoparia Bitfinexissä · salkussa ${heldSet.size} (max 4)`;

  if (entries.length === 0) {
    els.marketList.innerHTML = '<p class="empty-log">Ladataan markkinoita…</p>';
    return;
  }

  els.marketList.innerHTML = `
    <div class="market-row market-row-head">
      <div>Krypto</div>
      <div class="market-head-price">Kurssi</div>
      <div class="market-head-change">Muutos</div>
    </div>
  ${entries
    .map(([symbol, ticker]) => {
      const sym = normalizeSymbol(symbol);
      const label = getCryptoLabel(sym);
      const analysis = state.analyses[sym] || state.analyses[symbol];
      const change24Class = (ticker.changePct ?? 0) >= 0 ? "up" : "down";
      const isHeld = heldSet.has(sym);
      const isTarget = !isHeld && targetSet.has(sym);
      const watch = state.profitWatch[sym] || state.profitWatch[symbol];
      const signal = analysis?.action === "buy" ? "▲" : analysis?.action === "sell" ? "▼" : "●";
      const positionPct = isHeld ? getPositionPct(sym) : null;
      const change24Label = formatPct(ticker.changePct ?? 0);

      let changeHtml;
      if (isHeld && positionPct != null) {
        const pnlClass = positionPct >= 0 ? "up" : "down";
        changeHtml = `
          <div class="market-change-stack">
            <span class="market-pct-pill ${pnlClass}" title="Voitto/tappio ostohintaan">P/L ${formatPct(positionPct)}</span>
            <span class="market-pct-sub ${change24Class}" title="24 h markkinamuutos">24h ${change24Label}</span>
          </div>`;
      } else {
        changeHtml = `
          <div class="market-change-stack">
            <span class="market-pct-pill ${change24Class}" title="24 h markkinamuutos">${change24Label}</span>
            <span class="market-pct-sub ${change24Class}">24h</span>
          </div>`;
      }

      let badge = "";
      if (isHeld && watch) {
        badge = `<span class="market-row-badge">${watch.statusText}</span>`;
      } else if (isHeld) {
        badge = `<span class="market-row-badge">${signal} Salkussa</span>`;
      } else if (isTarget) {
        badge = `<span class="market-row-badge market-row-badge-target">◎ Gemini-valinta</span>`;
      }

      return `
        <div class="market-row ${isHeld ? "selected" : isTarget ? "target" : ""}">
          <div>
            <div class="market-row-id">${label}</div>
            <div class="market-row-pair">${sym.replace(/^t/, "")} · vol ${formatVolumeEur(ticker.volumeEur)}</div>
          </div>
          <div class="market-row-price">${formatEur(ticker.last)}</div>
          ${changeHtml}
          ${badge}
        </div>`;
    })
    .join("")}`;
}

function renderPortfolio() {
  const portfolio = state.portfolio;
  const tickers = state.tickers;
  const totalValue = state.stats.totalValue ?? portfolio.cash;
  const hasHoldings = Object.keys(portfolio.holdings || {}).length > 0;

  const unrealized = getPortfolioUnrealizedPnl();
  if (els.portfolioLivePnl) {
    if (unrealized) {
      const badge = formatPnlBadge(unrealized.pnlEur, unrealized.pnlPct);
      els.portfolioLivePnl.textContent = `Avoin P/L: ${badge.pct} (${badge.eur})`;
      els.portfolioLivePnl.className = `portfolio-live-pnl ${badge.cls === "up" ? "positive" : "negative"}`;
    } else if (state.stats.pnlPct != null) {
      const pnl = state.stats.pnl ?? 0;
      const sign = pnl >= 0 ? "+" : "";
      els.portfolioLivePnl.textContent = `Salkku: ${formatPct(state.stats.pnlPct)} (${sign}${formatEur(pnl).replace("€", "").trim()} €)`;
      els.portfolioLivePnl.className = `portfolio-live-pnl ${pnl >= 0 ? "positive" : pnl < 0 ? "negative" : "neutral"}`;
    } else {
      els.portfolioLivePnl.textContent = "—";
      els.portfolioLivePnl.className = "portfolio-live-pnl neutral";
    }
  }

  if (!hasHoldings && (portfolio.cash ?? INITIAL_CAPITAL) >= INITIAL_CAPITAL - 1) {
    els.portfolioBody.innerHTML = `
      <tr><td colspan="7" style="color:var(--muted);padding:20px 8px">
        Botti valitsee parhaat kryptot automaattisesti — odota seuraavaa kaupankäyntikierrosta.
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
    const position = getPositionPnl(symbol);
    const pnlClass = position && position.pnlEur >= 0 ? "up" : "down";
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
        <td>
          ${
            position
              ? (() => {
                  const badge = formatPnlBadge(position.pnlEur, position.pnlPct);
                  return `<div class="portfolio-pnl-stack">
            <span class="market-pct-pill ${badge.cls}">${badge.pct}</span>
            <span class="market-pct-sub ${badge.cls}">${badge.eur}</span>
          </div>`;
                })()
              : "—"
          }
        </td>
        <td class="crypto-change ${changeClass}">${formatPct(ticker.changePct)}</td>
      </tr>
    `);
  }

  if (unrealized && rows.length > 0) {
    const badge = formatPnlBadge(unrealized.pnlEur, unrealized.pnlPct);
    rows.push(`
      <tr class="portfolio-summary-row">
        <td><strong>Yhteensä (kryptot)</strong></td>
        <td>—</td>
        <td>—</td>
        <td>${formatEur(unrealized.holdingsValue)}</td>
        <td>—</td>
        <td>
          <div class="portfolio-pnl-stack">
            <span class="market-pct-pill ${badge.cls}">${badge.pct}</span>
            <span class="market-pct-sub ${badge.cls}">${badge.eur}</span>
          </div>
        </td>
        <td>—</td>
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
        <td>—</td>
      </tr>
    `);
  }

  els.portfolioBody.innerHTML = rows.join("");
}

function renderAIEventLog() {
  if (!state.aiEvents.length) {
    return `<p class="ai-placeholder">Ei tapahtumia vielä — botti aloittaa pian.</p>`;
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

function renderRegimeLearning() {
  const regime = state.regime;
  const learning = state.learning;
  if (!regime && !learning) return "";
  const regimeMap = {
    bull: { label: "Nouseva markkina", cls: "up" },
    bear: { label: "Laskeva markkina", cls: "down" },
    neutral: { label: "Neutraali markkina", cls: "neutral" },
  };
  let html = '<div class="ai-metrics">';
  if (regime?.regime) {
    const r = regimeMap[regime.regime] || regimeMap.neutral;
    const btc = regime.btc_change_24h_pct != null ? ` · BTC ${formatPct(regime.btc_change_24h_pct)}` : "";
    const breadth = regime.breadth_up_pct != null ? ` · ${regime.breadth_up_pct}% nousussa` : "";
    html += `<span class="metric-chip ${r.cls}">${r.label}${btc}${breadth}</span>`;
  }
  if (learning?.note) {
    html += `<span class="metric-chip" title="Oppiminen omasta kauppahistoriasta">🧠 ${learning.note}</span>`;
  }
  html += "</div>";
  return html;
}

function renderAIDecision(report) {
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
      ${renderRegimeLearning()}
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
    return `<span class="trade-pnl ${cls}">Myynti ${sign}${pct.toFixed(2)} %</span>`;
  }
  const ticker = state.tickers[trade.symbol];
  if (!ticker || !trade.price) return "";
  const stillHeld = Object.prototype.hasOwnProperty.call(state.portfolio.holdings || {}, trade.symbol);
  const pct = ((ticker.last - trade.price) / trade.price) * 100;
  const cls = pct >= 0 ? "up" : "down";
  const sign = pct >= 0 ? "+" : "";
  return `<span class="trade-pnl ${cls}">Nyt ${sign}${pct.toFixed(2)} %${stillHeld ? "" : " · myyty"}</span>`;
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
            <span class="trade-time">${formatDateTime(trade.timestamp)}</span>
          </div>`;
      }
      const typeLabel = trade.type === "buy" ? "OSTO" : "MYYNTI";
      const taxNote = trade.tax > 0 ? ` · vero ${formatEur(trade.tax)}` : "";
      const pnlBadge = getTradePnlBadge(trade);
      return `
        <div class="trade-item">
          <span class="trade-type ${trade.type}">${typeLabel}</span>
          <div class="trade-details">
            <div class="main">${label} · ${formatEur(trade.eurTotal)}${taxNote}${pnlBadge ? ` ${pnlBadge}` : ""}</div>
            <div class="sub">${formatCrypto(trade.amount, 6)} @ ${formatEur(trade.price)} — ${trade.reason}</div>
          </div>
          <span class="trade-time">${formatDateTime(trade.timestamp)}</span>
        </div>`;
    })
    .join("");
}

function startCountdown() {
  if (countdownTimer) clearInterval(countdownTimer);
  countdownTimer = setInterval(() => {
    if (countdown > 0) countdown--;
    els.statNext.textContent = `${countdown}s`;
  }, 1000);
}

async function poll() {
  try {
    const data = await fetchState();
    applyPayload(data);
  } catch (err) {
    showError(err.message);
  }
}

els.marketSearch.addEventListener("input", (e) => {
  marketSearch = e.target.value;
  renderMarketList();
});

const botUrlEl = document.getElementById("bot-url");
if (botUrlEl) {
  botUrlEl.href = location.origin;
  botUrlEl.textContent = location.origin;
}

poll();
startCountdown();
pollTimer = setInterval(poll, POLL_INTERVAL);
