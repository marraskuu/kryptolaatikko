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
let tradeLogFilter = "all";
let pollTimer = null;
let countdownTimer = null;
let lastLearningReportBodyKey = "";
let nextTradeDeadlineMs = null;

async function fetchState() {
  const res = await fetch("/api/state/", {
    credentials: "same-origin",
    cache: "no-store",
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

function formatMarketTimeframeChanges(analysis, ticker) {
  const change24 = ticker?.changePct ?? 0;
  const change1h = analysis?.change1hPct;
  const has1h = Number.isFinite(change1h);
  const change24Class = change24 >= 0 ? "up" : "down";
  const change1hClass = has1h && change1h >= 0 ? "up" : "down";

  const parts = [];
  if (has1h) {
    parts.push(
      `<span class="${change1hClass}" title="1 h markkinamuutos (kynttilädata)">1h ${formatPct(change1h)}</span>`
    );
  }
  parts.push(
    `<span class="${change24Class}" title="24 h markkinamuutos (Bitfinex)">24h ${formatPct(change24)}</span>`
  );

  return {
    change24Class,
    change24Label: formatPct(change24),
    subHtml: parts.join('<span class="market-pct-sep"> · </span>'),
    has1h,
  };
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

function parsePairBase(symbol) {
  const key = normalizeSymbol(symbol);
  const body = key.startsWith("t") ? key.slice(1) : key;
  for (const quote of ["UST", "USD", "EUR"]) {
    if (body.endsWith(quote) && body.length > quote.length) {
      return body.slice(0, -quote.length);
    }
  }
  return null;
}

function resolveHoldingTicker(symbol) {
  const key = normalizeSymbol(symbol);
  if (state.tickers[key]?.last) return state.tickers[key];
  const base = parsePairBase(key);
  if (!base) return null;
  for (const quote of ["UST", "USD", "EUR"]) {
    const alt = `t${base}${quote}`;
    if (alt !== key && state.tickers[alt]?.last) return state.tickers[alt];
  }
  return null;
}

function getPositionPnl(symbol) {
  const key = normalizeSymbol(symbol);
  const holding = state.portfolio.holdings?.[key] || state.portfolio.holdings?.[symbol];
  const ticker = resolveHoldingTicker(key) || resolveHoldingTicker(symbol);
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
    const ticker = resolveHoldingTicker(symbol);
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
    learningReport: data.learningReport ?? state.learningReport,
    learning: data.learning ?? state.learning,
    marketLearning: data.marketLearning ?? state.marketLearning,
    geminiPickTracking: data.geminiPickTracking ?? state.geminiPickTracking,
    geminiNarrativeHistory: data.geminiNarrativeHistory ?? state.geminiNarrativeHistory ?? [],
    dailyPolicyShadow: data.dailyPolicyShadow ?? state.dailyPolicyShadow,
    botStartedAt: data.botStartedAt ?? state.botStartedAt,
    lastTradeAt: data.lastTradeAt ?? state.lastTradeAt,
    tradeIntervalSec: data.tradeIntervalSec ?? state.tradeIntervalSec,
    nextTradeInSec: data.nextTradeInSec ?? state.nextTradeInSec,
    botStale: data.botStale ?? state.botStale,
    botStaleSec: data.botStaleSec ?? state.botStaleSec,
  };

  syncTradeCountdownFromServer(data);

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
  statTaxLabel: document.getElementById("stat-tax-label"),
  statTaxYear: document.getElementById("stat-tax-year"),
  statTaxPrevious: document.getElementById("stat-tax-previous"),
  statTaxEstimate: document.getElementById("stat-tax-estimate"),
  statTrades: document.getElementById("stat-trades"),
  statTradesMonth: document.getElementById("stat-trades-month"),
  statTrades24h: document.getElementById("stat-trades-24h"),
  wlYearLabel: document.getElementById("wl-year-label"),
  wlYearWin: document.getElementById("wl-year-win"),
  wlYearLoss: document.getElementById("wl-year-loss"),
  wlYearNet: document.getElementById("wl-year-net"),
  wlPnlSplit: document.getElementById("wl-pnl-split"),
  wlMonthWin: document.getElementById("wl-month-win"),
  wlMonthLoss: document.getElementById("wl-month-loss"),
  wlMonthNet: document.getElementById("wl-month-net"),
  wlDayWin: document.getElementById("wl-day-win"),
  wlDayLoss: document.getElementById("wl-day-loss"),
  wlDayNet: document.getElementById("wl-day-net"),
  statNext: document.getElementById("stat-next"),
  statUptime: document.getElementById("stat-uptime"),
  lastUpdate: document.getElementById("last-update"),
  marketList: document.getElementById("market-list"),
  marketCount: document.getElementById("market-count"),
  marketSearch: document.getElementById("market-search"),
  aiDecision: document.getElementById("ai-decision"),
  headerRegime: document.getElementById("header-regime"),
  headerRegimeInline: document.getElementById("header-regime-inline"),
  headerMarketLearningInline: document.getElementById("header-market-learning-inline"),
  portfolioBody: document.getElementById("portfolio-body"),
  portfolioLivePnl: document.getElementById("portfolio-live-pnl"),
  tradeLog: document.getElementById("trade-log"),
  learningReport: document.getElementById("learning-report"),
  learningReportMeta: document.getElementById("learning-report-meta"),
  learningReportTitle: document.getElementById("learning-report-title"),
  geminiNarrativeModal: document.getElementById("gemini-narrative-modal"),
  geminiNarrativeClose: document.getElementById("gemini-narrative-close"),
  geminiNarrativeSearch: document.getElementById("gemini-narrative-search"),
  geminiNarrativeCount: document.getElementById("gemini-narrative-count"),
  geminiNarrativeList: document.getElementById("gemini-narrative-list"),
  geminiNarrativeDetail: document.getElementById("gemini-narrative-detail"),
  shadowTodayPnl: document.getElementById("shadow-today-pnl"),
  shadowDayStart: document.getElementById("shadow-day-start"),
  shadowYearLabel: document.getElementById("shadow-year-label"),
  shadowYearPnl: document.getElementById("shadow-year-pnl"),
  shadowYearStart: document.getElementById("shadow-year-start"),
  shadowPolicyFlags: document.getElementById("shadow-policy-flags"),
  shadowThresholds: document.getElementById("shadow-thresholds"),
  shadowCounterfactual: document.getElementById("shadow-counterfactual"),
  shadowCounterfactualDetail: document.getElementById("shadow-counterfactual-detail"),
  shadowBlockedTrades: document.getElementById("shadow-blocked-trades"),
  shadowBlockedDetail: document.getElementById("shadow-blocked-detail"),
  shadowDataMeta: document.getElementById("shadow-data-meta"),
  shadowProfitTake: document.getElementById("shadow-profit-take"),
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

function formatUptime(startedAtIso) {
  if (!startedAtIso) return "—";
  const start = new Date(startedAtIso);
  if (Number.isNaN(start.getTime())) return "—";
  let diffMs = Date.now() - start.getTime();
  if (diffMs < 0) diffMs = 0;
  const totalMin = Math.floor(diffMs / 60000);
  const days = Math.floor(totalMin / (60 * 24));
  const hours = Math.floor((totalMin % (60 * 24)) / 60);
  const mins = totalMin % 60;
  const parts = [];
  if (days > 0) parts.push(`${days} pv`);
  parts.push(`${hours} t`);
  parts.push(`${mins} min`);
  return `Pyörinyt ${parts.join(" ")}`;
}

function renderUptime() {
  if (!els.statUptime) return;
  els.statUptime.textContent = formatUptime(state.botStartedAt);
}

function syncTradeCountdownFromServer(data) {
  const interval = data.tradeIntervalSec ?? state.tradeIntervalSec ?? 60;
  // Palvelimen nextTradeInSec on auktoritatiivinen (ei riipu selaimen kellosta).
  if (typeof data.nextTradeInSec === "number") {
    const targetDeadline =
      data.nextTradeInSec <= 0
        ? Date.now()
        : Date.now() + data.nextTradeInSec * 1000;
    if (
      nextTradeDeadlineMs == null ||
      Math.abs(targetDeadline - nextTradeDeadlineMs) > 1500
    ) {
      nextTradeDeadlineMs = targetDeadline;
    }
    return;
  }
  if (data.lastTradeAt) {
    const lastMs = new Date(data.lastTradeAt).getTime();
    if (Number.isFinite(lastMs)) {
      nextTradeDeadlineMs = lastMs + interval * 1000;
    }
  }
}

function computeNextTradeSec() {
  const interval = state.tradeIntervalSec || 60;
  if (nextTradeDeadlineMs != null) {
    const remaining = Math.ceil((nextTradeDeadlineMs - Date.now()) / 1000);
    return Math.min(interval, Math.max(0, remaining));
  }
  if (typeof state.nextTradeInSec === "number") {
    return Math.min(interval, Math.max(0, state.nextTradeInSec));
  }
  return interval;
}

function computeTradeOverdueSec() {
  if (nextTradeDeadlineMs != null) {
    return Math.max(0, Math.ceil((Date.now() - nextTradeDeadlineMs) / 1000));
  }
  const interval = state.tradeIntervalSec || 60;
  if (!state.lastTradeAt) return 0;
  const lastMs = new Date(state.lastTradeAt).getTime();
  if (!Number.isFinite(lastMs)) return 0;
  const elapsed = Math.max(0, Math.floor((Date.now() - lastMs) / 1000));
  return Math.max(0, elapsed - interval);
}

function renderNextCountdown() {
  if (!els.statNext) return;
  els.statNext.classList.remove("status-due", "status-overdue");
  const remaining = computeNextTradeSec();
  if (remaining > 0) {
    els.statNext.textContent = `${remaining}s`;
    return;
  }
  const overdue = computeTradeOverdueSec();
  if (state.botStale || overdue > 90) {
    els.statNext.textContent = "Odottaa…";
    els.statNext.classList.add("status-overdue");
  } else {
    els.statNext.textContent = "Ajetaan…";
    els.statNext.classList.add("status-due");
  }
}

function renderAll(lastUpdate) {
  if (lastUpdate) {
    els.lastUpdate.textContent = `Päivitetty ${formatTime(lastUpdate)}`;
  }
  renderNextCountdown();
  renderUptime();
  renderStats();
  renderShadowPolicy();
  renderMarketList();
  renderPortfolio();
  renderTradeLog();
  renderLearningReport();
  if (narrativeModalOpen) renderGeminiNarrativeModal();
  renderAIDecision(state.lastAIReport);
  if (els.headerMarketLearningInline) {
    els.headerMarketLearningInline.innerHTML = renderMarketLearningChip();
  }
  if (els.headerRegimeInline) {
    els.headerRegimeInline.innerHTML = renderRegimeChip();
  }
  if (els.headerRegime) {
    els.headerRegime.innerHTML = renderLearningChips();
  }
}

function setShadowMetricValue(el, text, tone) {
  if (!el) return;
  const isSm =
    el.id === "shadow-policy-flags" ||
    el.id === "shadow-blocked-trades" ||
    el.id === "shadow-data-meta";
  el.textContent = text;
  el.className = `shadow-metric-value${isSm ? " shadow-metric-sm" : ""}${tone ? ` ${tone}` : ""}`;
}

function renderShadowPolicy() {
  if (!els.shadowTodayPnl) return;
  const shadow = state.dailyPolicyShadow;
  const empty = () => {
    setShadowMetricValue(els.shadowTodayPnl, "—");
    if (els.shadowDayStart) els.shadowDayStart.textContent = "Live tänään —";
    if (els.shadowYearLabel) els.shadowYearLabel.textContent = "Varjosalkku · vuosi";
    setShadowMetricValue(els.shadowYearPnl, "—");
    if (els.shadowYearStart) els.shadowYearStart.textContent = "Live vuosi —";
    setShadowMetricValue(els.shadowPolicyFlags, "—", null);
    if (els.shadowPolicyFlags) els.shadowPolicyFlags.className = "shadow-metric-value shadow-metric-sm";
    if (els.shadowThresholds) els.shadowThresholds.textContent = "Testidata kerääntyy";
    setShadowMetricValue(els.shadowCounterfactual, "—");
    if (els.shadowCounterfactualDetail) els.shadowCounterfactualDetail.textContent = "Ei vertailua";
    setShadowMetricValue(els.shadowBlockedTrades, "—", null);
    if (els.shadowBlockedTrades) els.shadowBlockedTrades.className = "shadow-metric-value shadow-metric-sm";
    if (els.shadowBlockedDetail) els.shadowBlockedDetail.textContent = "—";
    setShadowMetricValue(els.shadowDataMeta, "—", null);
    if (els.shadowDataMeta) els.shadowDataMeta.className = "shadow-metric-value shadow-metric-sm";
    if (els.shadowProfitTake) els.shadowProfitTake.textContent = "—";
  };

  if (!shadow?.enabled) {
    empty();
    return;
  }

  const thresholds = shadow.thresholds || {};
  const comparison = shadow.portfolioComparison || {};
  if (els.shadowThresholds) {
    els.shadowThresholds.textContent = `Stop ${thresholds.dailyStopPct ?? -1} % · lock +${thresholds.profitLockSoftPct ?? 0.5} / +${thresholds.profitLockFirmPct ?? 1} %`;
  }

  if (els.shadowDayStart) {
    const liveTodayEur = comparison.liveTodayPnlEur ?? shadow.liveTodayPnlEur ?? shadow.todayPnlEur;
    if (liveTodayEur != null) {
      const sign = liveTodayEur >= 0 ? "+" : "";
      els.shadowDayStart.textContent = `Live tänään ${sign}${Number(liveTodayEur).toFixed(2)} €`;
    } else {
      els.shadowDayStart.textContent = "Live tänään —";
    }
  }

  const shadowTodayEur = comparison.shadowTodayPnlEur;
  const shadowTodayPct = comparison.shadowTodayPnlPct;
  if (shadowTodayEur != null && shadowTodayPct != null) {
    const sign = shadowTodayEur >= 0 ? "+" : "";
    const tone = shadowTodayEur > 0.005 ? "positive" : shadowTodayEur < -0.005 ? "negative" : null;
    setShadowMetricValue(
      els.shadowTodayPnl,
      `${sign}${shadowTodayEur.toFixed(2)} € (${formatPct(shadowTodayPct)})`,
      tone
    );
  } else if (shadowTodayEur != null) {
    const sign = shadowTodayEur >= 0 ? "+" : "";
    const tone = shadowTodayEur > 0.005 ? "positive" : shadowTodayEur < -0.005 ? "negative" : null;
    setShadowMetricValue(els.shadowTodayPnl, `${sign}${shadowTodayEur.toFixed(2)} €`, tone);
  } else {
    setShadowMetricValue(els.shadowTodayPnl, "Tänään —");
  }

  const shadowYear = shadow.shadowYearPnl || {};
  const liveYear = shadow.yearPnl || {};
  const yearNum = shadowYear.year ?? liveYear.year ?? state.stats?.taxCurrentYearLabel ?? new Date().getFullYear();
  if (els.shadowYearLabel) {
    els.shadowYearLabel.textContent = `Varjosalkku · ${yearNum}`;
  }
  const yearEur = shadowYear.pnlEur;
  const yearPct = shadowYear.pnlPct;
  if (yearEur != null && yearPct != null) {
    const sign = yearEur >= 0 ? "+" : "";
    const tone = yearEur > 0.005 ? "positive" : yearEur < -0.005 ? "negative" : null;
    setShadowMetricValue(
      els.shadowYearPnl,
      `${sign}${yearEur.toFixed(2)} € (${formatPct(yearPct)})`,
      tone
    );
  } else if (yearEur != null) {
    const sign = yearEur >= 0 ? "+" : "";
    const tone = yearEur > 0.005 ? "positive" : yearEur < -0.005 ? "negative" : null;
    setShadowMetricValue(els.shadowYearPnl, `${sign}${yearEur.toFixed(2)} €`, tone);
  } else {
    setShadowMetricValue(els.shadowYearPnl, `${yearNum} —`);
  }
  if (els.shadowYearStart) {
    const liveYearEur = liveYear.pnlEur;
    const daysInYear = shadowYear.daysInYear ?? liveYear.daysInYear ?? 0;
    if (liveYearEur != null) {
      const sign = liveYearEur >= 0 ? "+" : "";
      els.shadowYearStart.textContent = `Live vuosi ${sign}${liveYearEur.toFixed(2)} € · ${daysInYear} pv`;
    } else if (daysInYear) {
      els.shadowYearStart.textContent = `Live vuosi — · ${daysInYear} pv`;
    } else {
      els.shadowYearStart.textContent = "Live vuosi —";
    }
  }

  const policy = shadow.policy || {};
  let policyText = "Normaali — ei rajoituksia";
  let policyTone = null;
  if (policy.dailyStopActive) {
    policyText = "Päivästop −1 %";
    policyTone = "negative";
  } else if (policy.profitLockTier === "firm") {
    policyText = "Profit lock +1 %";
    policyTone = "warning";
  } else if (policy.profitLockTier === "soft") {
    policyText = "Profit lock +0,5 %";
    policyTone = "warning";
  } else if (policy.aggressiveEligible) {
    policyText = "Aggressiivinen sallittu";
    policyTone = "positive";
  }
  setShadowMetricValue(els.shadowPolicyFlags, policyText, policyTone);

  const summary = shadow.summary || {};
  const hints = shadow.hints || [];
  const trades = summary.tradesLogged ?? 0;
  const days = summary.daysTracked ?? 0;
  const mirrored = comparison.tradesMirrored ?? 0;
  const skipped = comparison.tradesSkipped ?? 0;
  const advantage = comparison.advantageEur;
  const reliable = comparison.reliable;

  if (!reliable && mirrored + skipped < 3) {
    setShadowMetricValue(els.shadowCounterfactual, "Kerätään…");
    if (els.shadowCounterfactualDetail) {
      els.shadowCounterfactualDetail.textContent = `${mirrored + skipped} peilattua/ohitettua kauppaa`;
    }
  } else if (advantage != null) {
    const sign = advantage >= 0 ? "+" : "";
    const tone = advantage > 0.05 ? "positive" : advantage < -0.05 ? "negative" : null;
    setShadowMetricValue(els.shadowCounterfactual, `${sign}${Number(advantage).toFixed(2)} €`, tone);
    if (els.shadowCounterfactualDetail) {
      const liveVal = comparison.liveTotalValue;
      const shadowVal = comparison.shadowTotalValue;
      if (liveVal != null && shadowVal != null) {
        els.shadowCounterfactualDetail.textContent = `Varjo ${Number(shadowVal).toFixed(2)} € · live ${Number(liveVal).toFixed(2)} €`;
      } else {
        els.shadowCounterfactualDetail.textContent = `Peilattu ${mirrored} · ohitettu ${skipped}`;
      }
      els.shadowCounterfactualDetail.title = hints[0] || els.shadowCounterfactualDetail.textContent;
    }
  } else {
    setShadowMetricValue(els.shadowCounterfactual, "—");
    if (els.shadowCounterfactualDetail) els.shadowCounterfactualDetail.textContent = "Rinnakkaisvarjosalkku";
  }

  const buysBlock = summary.buysWouldBlock ?? 0;
  const sellsBlock = summary.sellsWouldBlock ?? 0;
  if (buysBlock || sellsBlock) {
    setShadowMetricValue(
      els.shadowBlockedTrades,
      `${buysBlock} osto · ${sellsBlock} myynti`,
      "accent"
    );
    const parts = [];
    const buyCf = summary.blockedBuyCounterfactualEur ?? summary.buyBlockEur;
    if (buyCf) {
      const b = Number(buyCf);
      parts.push(`ostot ${b >= 0 ? "+" : ""}${b.toFixed(2)} €`);
    }
    if (summary.sellBlockCounterfactualEur) {
      const s = Number(summary.sellBlockCounterfactualEur);
      parts.push(`myynnit ${s >= 0 ? "+" : ""}${s.toFixed(2)} €`);
    }
    if (els.shadowBlockedDetail) {
      els.shadowBlockedDetail.textContent = parts.length ? parts.join(" · ") : "Counterfactual laskettu";
    }
  } else if (trades > 0) {
    setShadowMetricValue(els.shadowBlockedTrades, "Ei estoja vielä");
    if (els.shadowBlockedDetail) els.shadowBlockedDetail.textContent = "Kaikki kaupat sallittu simulaatiossa";
  } else {
    setShadowMetricValue(els.shadowBlockedTrades, "0 osto · 0 myynti");
    if (els.shadowBlockedDetail) els.shadowBlockedDetail.textContent = "Odotetaan kauppoja";
  }

  const ptSignals = summary.profitTakeShadowSignals ?? 0;
  const ptEst = summary.profitTakeShadowEurEst;
  setShadowMetricValue(
    els.shadowDataMeta,
    `${trades} kauppaa · ${days} pv`,
    trades >= 8 ? "accent" : null
  );
  if (els.shadowProfitTake) {
    if (mirrored || skipped) {
      els.shadowProfitTake.textContent = `Peilattu ${mirrored} · ohitettu ${skipped}`;
    } else if (ptSignals > 0) {
      els.shadowProfitTake.textContent = `Aikaisempi voitto-otto: ${ptSignals}× (~${Number(ptEst || 0).toFixed(2)} €)`;
    } else {
      els.shadowProfitTake.textContent = "Peilatut / ohitetut kaupat —";
    }
  }

  if (hints.length && els.shadowCounterfactualDetail && reliable) {
    els.shadowCounterfactualDetail.title = hints[0];
  }
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
  els.statBreakdown.textContent =
    cash <= 5 && holdings > 0
      ? `${formatEur(holdings)} kryptot + ${formatEur(cash)} käteistä (lähes kaikki sijoitettu) = ${formatEur(total)}`
      : `${formatEur(holdings)} kryptot + ${formatEur(cash)} käteistä = ${formatEur(total)}`;
  const tradeCounts = getTradeCounts();
  els.statTrades.textContent = String(s.tradeCount ?? tradeCounts.total);
  if (els.statTradesMonth) {
    els.statTradesMonth.textContent = `Tässä kuussa: ${tradeCounts.month}`;
  }
  if (els.statTrades24h) {
    els.statTrades24h.textContent = `Viime 24 h: ${tradeCounts.last24h}`;
  }
  const taxYear = s.taxCurrentYearLabel;
  if (els.statTaxLabel && taxYear) {
    els.statTaxLabel.textContent = `Vero myyntivoitoista ${taxYear} (30 %)`;
  }
  els.statTaxYear.textContent = formatEur(s.taxCurrentYear ?? 0);
  if (els.statTaxPrevious) {
    if (s.taxPreviousYear != null) {
      els.statTaxPrevious.textContent = `${s.taxPreviousYearLabel}: ${formatEur(s.taxPreviousYear)}`;
    } else {
      els.statTaxPrevious.textContent = "";
    }
  }
  const grossWins = s.taxCurrentYearGrossWins;
  const taxBasis =
    grossWins != null && grossWins > 0
      ? `Voitoilliset myynnit ${formatEur(grossWins)} · `
      : "";
  els.statTaxEstimate.textContent = `${taxBasis}Arvio avoimista (jos myyt nyt): ${formatEur(s.estimatedTax ?? 0)}`;

  renderWinLoss(s.realizedBreakdown);

  if (els.wlPnlSplit) {
    const unreal = s.unrealizedPnl ?? 0;
    const real = s.realizedPnl ?? 0;
    const uSign = unreal >= 0 ? "+" : "−";
    const rSign = real >= 0 ? "+" : "−";
    els.wlPnlSplit.textContent =
      `Avoimet positiot: ${uSign}${formatEur(Math.abs(unreal)).replace("€", "").trim()} € · ` +
      `Kaikki myynnit: ${rSign}${formatEur(Math.abs(real)).replace("€", "").trim()} €`;
  }

  const pnl = s.pnl ?? 0;
  const pnlPct = s.pnlPct ?? 0;
  const pnlClass = pnl > 0 ? "positive" : pnl < 0 ? "negative" : "neutral";
  const sign = pnl >= 0 ? "+" : "";
  els.statPnl.textContent = `${sign}${formatEur(pnl).replace("€", "").trim()} € (${formatPct(pnlPct)})`;
  els.statPnl.className = `stat-change ${pnlClass}`;
}

function renderWinLoss(breakdown) {
  const empty = { winCount: 0, winEur: 0, lossCount: 0, lossEur: 0 };
  const data = breakdown || {};
  const winText = (p) => `${p.winCount} kpl · +${formatEur(p.winEur || 0)}`;
  const lossText = (p) =>
    `${p.lossCount} kpl · ${p.lossEur > 0 ? "−" : ""}${formatEur(p.lossEur || 0)}`;
  const netText = (p) => {
    const net = (p.winEur || 0) - (p.lossEur || 0);
    const sign = net > 0.005 ? "+" : net < -0.005 ? "−" : "";
    const abs = Math.abs(net);
    return `${sign}${formatEur(abs)}`;
  };
  const netClass = (p) => {
    const net = (p.winEur || 0) - (p.lossEur || 0);
    if (net > 0.005) return "up";
    if (net < -0.005) return "down";
    return "even";
  };
  const rows = [
    ["year", els.wlYearWin, els.wlYearLoss, els.wlYearNet],
    ["month", els.wlMonthWin, els.wlMonthLoss, els.wlMonthNet],
    ["day", els.wlDayWin, els.wlDayLoss, els.wlDayNet],
  ];
  rows.forEach(([key, winEl, lossEl, netEl]) => {
    const p = data[key] || empty;
    if (winEl) winEl.textContent = winText(p);
    if (lossEl) lossEl.textContent = lossText(p);
    if (netEl) {
      netEl.textContent = netText(p);
      netEl.className = `winloss-net ${netClass(p)}`;
    }
  });
  if (els.wlYearLabel) {
    const year = state.stats?.taxCurrentYearLabel;
    els.wlYearLabel.textContent = year ? `Vuonna ${year}` : "Tänä vuonna";
  }
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

  const maxPos = state.maxPositions ?? 3;
  els.marketCount.textContent = `${Object.keys(state.tickers).length} kryptoparia Bitfinexissä · salkussa ${heldSet.size} (max ${maxPos})`;

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
      const marketChanges = formatMarketTimeframeChanges(analysis, ticker);
      const change24Class = marketChanges.change24Class;
      const isHeld = heldSet.has(sym);
      const isTarget = !isHeld && targetSet.has(sym);
      const watch = state.profitWatch[sym] || state.profitWatch[symbol];
      const signal = analysis?.action === "buy" ? "▲" : analysis?.action === "sell" ? "▼" : "●";
      const positionPct = isHeld ? getPositionPct(sym) : null;
      const change24Label = marketChanges.change24Label;
      const holdingDuration = isHeld ? formatHoldingDuration(sym) : "";
      const holdingDurationSuffix = holdingDuration ? ` — ${holdingDuration}` : "";

      let changeHtml;
      if (isHeld && positionPct != null) {
        const pnlClass = positionPct >= 0 ? "up" : "down";
        changeHtml = `
          <div class="market-change-stack">
            <span class="market-pct-pill ${pnlClass}" title="Voitto/tappio ostohintaan">P/L ${formatPct(positionPct)}</span>
            <span class="market-pct-sub market-pct-times">${marketChanges.subHtml}</span>
          </div>`;
      } else {
        changeHtml = `
          <div class="market-change-stack">
            <span class="market-pct-pill ${change24Class}" title="24 h markkinamuutos">${change24Label}</span>
            <span class="market-pct-sub market-pct-times">${marketChanges.subHtml}</span>
          </div>`;
      }

      let badge = "";
      if (isHeld && watch) {
        let watchText = watch.statusText;
        const pnl = getPositionPnl(sym);
        let holdingPrefix = "";
        if (pnl && Number.isFinite(pnl.pnlEur)) {
          const sign = pnl.pnlEur >= 0 ? "+" : "";
          const eur = `${sign}${formatEur(pnl.pnlEur).replace("€", "").trim()} €`;
          const eurCls =
            pnl.pnlEur > 0.005 ? "up" : pnl.pnlEur < -0.005 ? "down" : "even";
          const parts = watchText.split(" — ");
          parts[0] = `${parts[0]} <span class="holding-pnl-eur ${eurCls}">(${eur})</span>`;
          watchText = parts.join(" — ");

          // Omistuksen nykyarvo "Voitto"-sanan eteen: vihreä voitolla, punainen
          // tappiolla, keltainen jos sama kuin ostohinta.
          const valueCls =
            pnl.pnlEur > 0.005 ? "up" : pnl.pnlEur < -0.005 ? "down" : "even";
          const valueStr = `${formatEur(pnl.currentValue).replace("€", "").trim()} €`;
          holdingPrefix = `<span class="holding-value ${valueCls}">${valueStr}</span> `;
        }
        watchText = `${watchText}${holdingDurationSuffix}`;
        badge = `<span class="market-row-badge">${holdingPrefix}${watchText}</span>`;
      } else if (isHeld) {
        const pnl = getPositionPnl(sym);
        let holdingPrefix = "";
        if (pnl && Number.isFinite(pnl.pnlEur)) {
          const valueCls =
            pnl.pnlEur > 0.005 ? "up" : pnl.pnlEur < -0.005 ? "down" : "even";
          const valueStr = `${formatEur(pnl.currentValue).replace("€", "").trim()} €`;
          holdingPrefix = `<span class="holding-value ${valueCls}">${valueStr}</span> `;
        }
        badge = `<span class="market-row-badge">${holdingPrefix}${signal} Salkussa${holdingDurationSuffix}</span>`;
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
    const ticker = resolveHoldingTicker(symbol);
    if (!ticker?.last) {
      rows.push(`
      <tr class="portfolio-stale-row">
        <td><strong>${getCryptoLabel(symbol)}</strong><br><span style="font-size:0.75rem;color:var(--muted)">Kurssi päivittyy…</span></td>
        <td>${formatCrypto(holding.amount, 6)}</td>
        <td>—</td>
        <td>${formatEur(holding.amount * (holding.avgPrice || 0))} <span style="font-size:0.75rem;color:var(--muted)">(hankinta)</span></td>
        <td>—</td>
        <td>—</td>
        <td>—</td>
      </tr>`);
      continue;
    }

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

function renderRegimeChip() {
  const regime = state.regime;
  if (!regime?.regime) return "";

  const regimeMap = {
    bull: { label: "Nouseva markkina", cls: "up" },
    bear: { label: "Laskeva markkina", cls: "down" },
    neutral: { label: "Neutraali markkina", cls: "neutral" },
  };
  const shiftLabels = {
    bull: "nousevaan",
    bear: "laskevaan",
    neutral: "neutraaliin",
  };
  const phaseLabels = {
    bull_entering: " · käännös nousevaan",
    bull_emerging: " · kääntymässä nousevaan",
    bear_entering: " · käännös laskevaan",
    bear_emerging: " · kääntymässä laskevaan",
    neutral_entering: " · tasaantumassa",
    neutral_emerging: " · kääntymässä neutraaliin",
  };

  const r = regimeMap[regime.regime] || regimeMap.neutral;
  let phase = "";
  if (regime.phase && regime.phase !== regime.regime) {
    phase =
      phaseLabels[regime.phase] ||
      (regime.shift_to && regime.shift_to !== regime.regime
        ? ` · → ${shiftLabels[regime.shift_to] || regime.shift_to}`
        : "");
  }
  const strength =
    regime.shift_strength && regime.shift_strength !== "none"
      ? ` (${regime.shift_strength})`
      : "";
  const btc =
    regime.btc_change_24h_pct != null ? ` · BTC ${formatPct(regime.btc_change_24h_pct)}` : "";
  const breadth =
    regime.breadth_up_pct != null
      ? ` · ${regime.breadth_up_pct}% kryptoista nousussa (24 h)`
      : "";
  const title = [
    regime.transition ? `Siirtymä: ${regime.transition}` : "",
    regime.signal_margin != null ? `Signaalimarginaali: ${regime.signal_margin > 0 ? "+" : ""}${regime.signal_margin}` : "",
    regime.shift_to ? `Ennakoidaan: ${shiftLabels[regime.shift_to] || regime.shift_to}` : "",
  ]
    .filter(Boolean)
    .join("\n");

  return `<span class="metric-chip regime-chip ${r.cls}" title="${escapeHtml(title)}">${r.label}${phase}${strength}${btc}${breadth}</span>`;
}

function renderRegimeAnticipationChip() {
  const regime = state.regime;
  if (!regime?.regime) return "";

  const shiftLabels = {
    bull: "nousevaan",
    bear: "laskevaan",
    neutral: "neutraaliin",
  };
  const phaseLabels = {
    bull_entering: "Käännös nousevaan",
    bull_emerging: "Kääntymässä nousevaan",
    bear_entering: "Käännös laskevaan",
    bear_emerging: "Kääntymässä laskevaan",
    neutral_entering: "Tasaantumassa",
    neutral_emerging: "Kääntymässä neutraaliin",
  };

  const phase = regime.phase;
  const current = regime.regime;
  const shift = regime.shift_to;
  const strength = regime.shift_strength;
  const emerging =
    phase && phase !== current
      ? phaseLabels[phase]
      : shift && shift !== current
        ? `→ ${shiftLabels[shift] || shift}`
        : "";

  if (!emerging && (!strength || strength === "none")) return "";

  const strengthFi =
    strength === "strong" ? "vahva" : strength === "moderate" ? "kohtalainen" : strength === "weak" ? "heikko" : "";
  const label = emerging + (strengthFi ? ` (${strengthFi})` : "");
  const cls =
    shift === "bull" || phase?.startsWith("bull")
      ? "up"
      : shift === "bear" || phase?.startsWith("bear")
        ? "down"
        : "neutral";
  const title = [
    `Nyt: ${current}`,
    regime.transition ? `Siirtymä: ${regime.transition}` : "",
    regime.signal_margin != null
      ? `Signaalimarginaali: ${regime.signal_margin > 0 ? "+" : ""}${regime.signal_margin}`
      : "",
    shift ? `Ennakoidaan: ${shiftLabels[shift] || shift}` : "",
  ]
    .filter(Boolean)
    .join("\n");

  return `<span class="metric-chip ${cls}" title="${escapeHtml(title)}">↻ ${escapeHtml(label)}</span>`;
}

function renderMarketLearningChip() {
  const ml = state.marketLearning;
  if (!ml || (!ml.bucketsLearned && !ml.bucketsTracked)) return "";
  let title = "Koko markkinan varjo-oppiminen (signaalit → toteutunut 1h/4h tuotto)";
  if (ml.best?.setup) {
    title += `\nParas: ${ml.best.setup} (${ml.best.exp1h > 0 ? "+" : ""}${ml.best.exp1h} % / 1h)`;
  }
  if (ml.worst?.setup) {
    title += `\nHuonoin: ${ml.worst.setup} (${ml.worst.exp1h > 0 ? "+" : ""}${ml.worst.exp1h} % / 1h)`;
  }
  return `<span class="metric-chip" title="${title}">📊 ${ml.bucketsLearned} asetelmaa opittu</span>`;
}

function renderLearningChips() {
  const regime = state.regime;
  const learning = state.learning;
  if (!learning && !state.marketLearning && !regime?.regime) return "";

  let noteHtml = "";
  if (learning?.note) {
    noteHtml = `<span class="metric-chip learning-note-chip" title="Oppiminen omasta kauppahistoriasta">🧠 ${learning.note}</span>`;
  }

  let chips = "";
  const gemTagged = learning?.gemini_confidence_tagged || 0;
  const gemTaggedBuys = learning?.gemini_confidence_tagged_buys || 0;
  const gemTaggedSells = learning?.gemini_confidence_tagged_sells || 0;
  const gemConfStats = learning?.gemini_confidence_stats;
  if (gemTagged >= 6 && gemConfStats && Object.keys(gemConfStats).length) {
    const lines = Object.entries(gemConfStats)
      .sort(([a], [b]) => Number(a) - Number(b))
      .map(
        ([conf, s]) =>
          `${conf}/10: ${s.trades} kpl, ${s.expectancy_eur >= 0 ? "+" : ""}${s.expectancy_eur} €/kauppa`
      );
    chips += `<span class="metric-chip" title="${lines.join("\n")}">🔮 Gemini-conf</span>`;
  } else if (gemTagged > 0 || gemTaggedBuys > 0 || gemTaggedSells > 0) {
    chips += `<span class="metric-chip" title="Gemini-confidence-oppiminen">🔮 Conf ${gemTagged}/6 (O${gemTaggedBuys}/M${gemTaggedSells})</span>`;
  }
  const activeRegime = regime?.regime;
  if (activeRegime && learning?.regime_tuning?.[activeRegime]) {
    chips += `<span class="metric-chip" title="Regiimikohtainen säätö aktiivisessa markkinassa">🎯 ${activeRegime}</span>`;
  }
  const ownSetups = learning?.setup_memory ? Object.keys(learning.setup_memory).length : 0;
  if (ownSetups > 0) {
    chips += `<span class="metric-chip" title="Omat sisäänostoasetelmat kauppahistoriasta">📐 ${ownSetups} setuppia</span>`;
  }
  const gpt = state.geminiPickTracking;
  const gpStats = gpt?.stats;
  if (gpStats?.picks_tracked >= 3 && gpStats.win_rate_pct != null) {
    const title = [
      `${gpStats.rounds} Gemini-kierrosta arkistoitu`,
      `${gpStats.picks_tracked} pickiä seurattu`,
      `Keskituotto ${gpStats.avg_return_pct >= 0 ? "+" : ""}${gpStats.avg_return_pct} %`,
      gpStats.pick_beats_skipped_pct != null
        ? `Pickit voittivat ohitetun ${gpStats.pick_beats_skipped_pct} %`
        : "",
    ]
      .filter(Boolean)
      .join("\n");
    chips += `<span class="metric-chip" title="${escapeHtml(title)}">🎯 Gemini ${gpStats.win_rate_pct}%</span>`;
  } else if (gpt?.current?.pick_outcomes?.length) {
    chips += `<span class="metric-chip" title="Seurataan edellisen Geminin pickien tuottoa">🎯 Gemini seuraa</span>`;
  }
  chips += renderRegimeAnticipationChip();

  const rowHtml = chips ? `<div class="learning-chip-row">${chips}</div>` : "";
  return noteHtml + rowHtml;
}

function formatDurationSec(sec) {
  if (sec == null || sec < 0) return "—";
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  if (h > 0) return `${h} t ${m} min`;
  return `${m} min`;
}

function formatHoldingDuration(symbol) {
  const key = normalizeSymbol(symbol);
  const holding = state.portfolio.holdings?.[key] || state.portfolio.holdings?.[symbol];
  const openedAt = holding?.openedAt;
  if (!openedAt) return "";
  const start = Date.parse(String(openedAt).replace("Z", "+00:00"));
  if (!Number.isFinite(start)) return "";
  const sec = Math.max(0, Math.floor((Date.now() - start) / 1000));
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  if (h > 0) return `${h}h ${m}min`;
  if (m > 0) return `${m}min`;
  return "<1min";
}

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function resolveLearningReport() {
  if (state.learningReport?.sections?.length) return state.learningReport;

  const learning = state.learning;
  const ml = state.marketLearning;
  if (!learning && !ml) return null;

  const sections = [];
  if (ml) {
    const lines = [`${ml.bucketsLearned || 0}/${ml.bucketsTracked || 0} asetelmaa opittu`];
    if (ml.best?.setup) lines.push(`Paras: ${ml.best.setup} (${ml.best.exp1h > 0 ? "+" : ""}${ml.best.exp1h} % / 1h)`);
    if (ml.worst?.setup) lines.push(`Huonoin: ${ml.worst.setup} (${ml.worst.exp1h > 0 ? "+" : ""}${ml.worst.exp1h} % / 1h)`);
    sections.push({ icon: "📊", title: "Markkina-asetelmat", lines });
  }
  if (learning?.note) {
    sections.push({ icon: "🧠", title: "Kauppojen oppiminen", lines: [learning.note] });
  }
  return { sections, changes: [], roadmap: [], narrative: null };
}

function renderGeminiPickTrackingHtml() {
  const gpt = state.geminiPickTracking;
  if (!gpt) return "";

  const lines = [];
  const current = gpt.current;
  if (current?.pick_outcomes?.length) {
    const mins = current.minutes_since_snapshot;
    lines.push(
      `<strong>Odottaa arkistointia</strong>${mins != null ? ` (${mins} min sitten)` : ""}:`
    );
    for (const p of current.pick_outcomes) {
      const ret = p.return_since_pct;
      if (ret == null) continue;
      const tag = p.executed ? " [kauppa]" : "";
      lines.push(`${escapeHtml(p.label)}: ${ret >= 0 ? "+" : ""}${ret.toFixed(1)} %${tag}`);
    }
    for (const lesson of current.lessons || []) {
      lines.push(escapeHtml(lesson));
    }
  }

  const stats = gpt.stats;
  if (stats?.picks_tracked >= 1) {
    lines.push(
      `<strong>Historia:</strong> ${stats.rounds} kierrosta · ${stats.picks_tracked} pickiä · osuu ${stats.win_rate_pct} % · keski ${stats.avg_return_pct >= 0 ? "+" : ""}${stats.avg_return_pct} %`
    );
  }

  for (const rnd of gpt.recent || []) {
    const ts = (rnd.timestamp || "").slice(0, 16).replace("T", " ");
    const pickStr = (rnd.picks || [])
      .filter((p) => p.return_pct != null)
      .map((p) => {
        const tag = p.executed ? "*" : "";
        return `${p.label}${tag} ${p.return_pct >= 0 ? "+" : ""}${p.return_pct.toFixed(1)}%`;
      })
      .join(", ");
    if (pickStr) lines.push(`${ts} (${rnd.regime || "?"}): ${escapeHtml(pickStr)}`);
  }

  if (!lines.length) return "";

  return `
    <div class="learning-section">
      <h4>🎯 Gemini-pick-seuranta</h4>
      <ul>${lines.map((line) => `<li>${line}</li>`).join("")}</ul>
    </div>`;
}

function buildNarrativeContentHtml(narrative) {
  if (!narrative) return "";
  if (narrative.story) {
    return `
      <div class="learning-narrative">
        <h4 class="learning-story-title">Geminin kertomus</h4>
        ${narrative.intro ? `<p class="learning-narrative-intro">${escapeHtml(narrative.intro)}</p>` : ""}
        <div class="learning-story-body">${escapeHtml(narrative.story)}</div>
        ${
          narrative.ideas
            ? `<div class="learning-narrative-block ideas">
            <h4>Ideat (ei vielä käytössä bottiin)</h4>
            <p>${escapeHtml(narrative.ideas)}</p>
          </div>`
            : ""
        }
        ${
          narrative.shadow_learned
            ? `<div class="learning-narrative-block shadow-policy">
            <h4>Varjopolitiikka — mitä testidata opettaa</h4>
            <p>${escapeHtml(narrative.shadow_learned)}</p>
          </div>`
            : ""
        }
        ${
          narrative.shadow_ideas
            ? `<div class="learning-narrative-block shadow-policy ideas">
            <h4>Varjopolitiikka — hyödyntämisehdotukset (ei vielä käytössä)</h4>
            <p>${escapeHtml(narrative.shadow_ideas)}</p>
          </div>`
            : ""
        }
        ${
          narrative.micro_learned
            ? `<div class="learning-narrative-block microstructure">
            <h4>Order book & crowd — mitä data opettaa</h4>
            <p>${escapeHtml(narrative.micro_learned)}</p>
          </div>`
            : ""
        }
        ${
          narrative.micro_ideas
            ? `<div class="learning-narrative-block microstructure ideas">
            <h4>Order book & crowd — hyödyntämisehdotukset (ei vielä käytössä)</h4>
            <p>${escapeHtml(narrative.micro_ideas)}</p>
          </div>`
            : ""
        }
        ${
          narrative.exit_learned
            ? `<div class="learning-narrative-block exit-peak">
            <h4>Huippumyynti — mitä data opettaa</h4>
            <p>${escapeHtml(narrative.exit_learned)}</p>
          </div>`
            : ""
        }
        ${
          narrative.exit_ideas
            ? `<div class="learning-narrative-block exit-peak ideas">
            <h4>Huippumyynti — hyödyntämisehdotukset (ei vielä käytössä)</h4>
            <p>${escapeHtml(narrative.exit_ideas)}</p>
          </div>`
            : ""
        }
        ${
          narrative.sell_learned
            ? `<div class="learning-narrative-block sell-outcomes">
            <h4>Voitto- vs tappiomyynnit — mitä data opettaa</h4>
            <p>${escapeHtml(narrative.sell_learned)}</p>
          </div>`
            : ""
        }
        ${
          narrative.sell_ideas
            ? `<div class="learning-narrative-block sell-outcomes ideas">
            <h4>Myyntisuositukset — enemmän voitolla (ei vielä automaattisesti käytössä)</h4>
            <p>${escapeHtml(narrative.sell_ideas)}</p>
          </div>`
            : ""
        }
        ${
          narrative.anticipation_learned
            ? `<div class="learning-narrative-block regime-anticipation">
            <h4>Regiimin ennakointi — hyödyntäminen ja oppiminen</h4>
            <p>${escapeHtml(narrative.anticipation_learned)}</p>
          </div>`
            : ""
        }
        ${
          narrative.anticipation_ideas
            ? `<div class="learning-narrative-block regime-anticipation ideas">
            <h4>Ennakoinnin hyödyntämisehdotukset (ei vielä käytössä)</h4>
            <p>${escapeHtml(narrative.anticipation_ideas)}</p>
          </div>`
            : ""
        }
        ${
          narrative.satellite_learned
            ? `<div class="learning-narrative-block bull-satellite">
            <h4>Bull-satelliitti (65/35) — käytännön tulokset</h4>
            <p>${escapeHtml(narrative.satellite_learned)}</p>
          </div>`
            : ""
        }
        ${
          narrative.satellite_ideas
            ? `<div class="learning-narrative-block bull-satellite ideas">
            <h4>Satelliittijaon hienosäätö (ei vielä automaattisesti käytössä)</h4>
            <p>${escapeHtml(narrative.satellite_ideas)}</p>
          </div>`
            : ""
        }
      </div>`;
  }
  if (narrative.intro || narrative.learned || narrative.in_use) {
    const blocks = [
      ["learned", "Mitä opittiin"],
      ["in_use", "Käytössä nyt"],
      ["next_steps", "Seuraavaksi"],
      ["shadow_learned", "Varjopolitiikka — mitä testidata opettaa"],
      ["shadow_ideas", "Varjopolitiikka — hyödyntämisehdotukset (ei vielä käytössä)"],
      ["micro_learned", "Order book & crowd — mitä data opettaa"],
      ["micro_ideas", "Order book & crowd — hyödyntämisehdotukset (ei vielä käytössä)"],
      ["exit_learned", "Huippumyynti — mitä data opettaa"],
      ["exit_ideas", "Huippumyynti — hyödyntämisehdotukset (ei vielä käytössä)"],
      ["sell_learned", "Voitto- vs tappiomyynnit — mitä data opettaa"],
      ["sell_ideas", "Myyntisuositukset — enemmän voitolla (ei vielä automaattisesti käytössä)"],
      ["anticipation_learned", "Regiimin ennakointi — hyödyntäminen ja oppiminen"],
      ["anticipation_ideas", "Ennakoinnin hyödyntämisehdotukset (ei vielä käytössä)"],
      ["satellite_learned", "Bull-satelliitti (65/35) — käytännön tulokset"],
      ["satellite_ideas", "Satelliittijaon hienosäätö (ei vielä automaattisesti käytössä)"],
      ["ideas", "Ideat (ei vielä käytössä)"],
    ];
    return `
      <div class="learning-narrative">
        ${narrative.intro ? `<p class="learning-narrative-intro">${escapeHtml(narrative.intro)}</p>` : ""}
        ${blocks
          .filter(([key]) => narrative[key])
          .map(
            ([key, title]) => `
          <div class="learning-narrative-block${
            key === "ideas" || key === "shadow_ideas" || key === "micro_ideas" || key === "exit_ideas" || key === "sell_ideas" || key === "anticipation_ideas" || key === "satellite_ideas"
              ? " ideas"
              : key === "shadow_learned"
                ? " shadow-policy"
                : key === "micro_learned"
                  ? " microstructure"
                  : key === "exit_learned"
                    ? " exit-peak"
                    : key === "sell_learned"
                      ? " sell-outcomes"
                      : key === "anticipation_learned"
                        ? " regime-anticipation"
                        : key === "satellite_learned"
                          ? " bull-satellite"
                          : ""
          }">
            <h4>${title}</h4>
            <p>${escapeHtml(narrative[key])}</p>
          </div>`
          )
          .join("")}
      </div>`;
  }
  return "";
}

let narrativeModalOpen = false;
let narrativeModalSearch = "";
let narrativeModalSelectedIdx = 0;
let narrativeModalFiltered = [];

function narrativePreviewText(narrative) {
  const text = narrative?.intro || narrative?.story || narrative?.learned || "";
  const oneLine = text.replace(/\s+/g, " ").trim();
  return oneLine.length > 100 ? `${oneLine.slice(0, 97)}…` : oneLine;
}

function narrativeSearchBlob(entry) {
  const n = entry.narrative || {};
  const ts = entry.timestamp || "";
  let formatted = "";
  if (ts) {
    try {
      formatted = formatDateTime(ts);
    } catch {
      formatted = ts;
    }
  }
  return [ts, formatted, n.story, n.intro, n.ideas, n.shadow_learned, n.shadow_ideas, n.micro_learned, n.micro_ideas, n.exit_learned, n.exit_ideas, n.sell_learned, n.sell_ideas, n.anticipation_learned, n.anticipation_ideas, n.satellite_learned, n.satellite_ideas, n.learned, n.in_use, n.next_steps]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

function filterGeminiNarratives(entries, query) {
  const q = query.trim().toLowerCase();
  if (!q) return entries;
  return entries.filter((entry) => narrativeSearchBlob(entry).includes(q));
}

function openGeminiNarrativeModal() {
  narrativeModalSearch = "";
  narrativeModalSelectedIdx = 0;
  if (els.geminiNarrativeSearch) els.geminiNarrativeSearch.value = "";
  renderGeminiNarrativeModal();
  if (!els.geminiNarrativeModal) return;
  els.geminiNarrativeModal.classList.remove("hidden");
  els.geminiNarrativeModal.removeAttribute("hidden");
  document.body.classList.add("modal-open");
  narrativeModalOpen = true;
  els.geminiNarrativeSearch?.focus();
}

function closeGeminiNarrativeModal() {
  if (!els.geminiNarrativeModal) return;
  els.geminiNarrativeModal.classList.add("hidden");
  els.geminiNarrativeModal.setAttribute("hidden", "");
  document.body.classList.remove("modal-open");
  narrativeModalOpen = false;
  els.learningReportTitle?.focus();
}

function renderGeminiNarrativeModal() {
  const all = state.geminiNarrativeHistory || [];
  narrativeModalFiltered = filterGeminiNarratives(all, narrativeModalSearch);
  if (narrativeModalSelectedIdx >= narrativeModalFiltered.length) {
    narrativeModalSelectedIdx = Math.max(0, narrativeModalFiltered.length - 1);
  }

  if (els.geminiNarrativeCount) {
    if (!all.length) {
      els.geminiNarrativeCount.textContent = "Ei tallennettuja kertomuksia";
    } else if (narrativeModalSearch.trim()) {
      els.geminiNarrativeCount.textContent = `${narrativeModalFiltered.length} / ${all.length} kertomusta`;
    } else {
      els.geminiNarrativeCount.textContent = `${all.length} kertomusta`;
    }
  }

  if (els.geminiNarrativeList) {
    if (!narrativeModalFiltered.length) {
      els.geminiNarrativeList.innerHTML = `<p class="gemini-narrative-list-empty">${
        all.length ? "Ei hakutuloksia." : "Gemini-kertomuksia ei vielä tallennettu."
      }</p>`;
    } else {
      els.geminiNarrativeList.innerHTML = narrativeModalFiltered
        .map((entry, idx) => {
          const active = idx === narrativeModalSelectedIdx ? " active" : "";
          const current = entry.current ? '<span class="gemini-narrative-badge">Nykyinen</span>' : "";
          const ts = entry.timestamp ? formatDateTime(entry.timestamp) : "Ei aikaleimaa";
          const preview = escapeHtml(narrativePreviewText(entry.narrative));
          return `<button type="button" class="gemini-narrative-list-item${active}" data-idx="${idx}">
          <span class="gemini-narrative-list-date">${ts}${current}</span>
          <span class="gemini-narrative-list-preview">${preview}</span>
        </button>`;
        })
        .join("");
    }
  }

  if (els.geminiNarrativeDetail) {
    const entry = narrativeModalFiltered[narrativeModalSelectedIdx];
    if (!entry) {
      els.geminiNarrativeDetail.innerHTML = "";
    } else {
      const ts = entry.timestamp ? formatDateTime(entry.timestamp) : "";
      els.geminiNarrativeDetail.innerHTML = `
        <div class="gemini-narrative-detail-header">
          ${ts ? `<time datetime="${escapeHtml(entry.timestamp)}">${ts}</time>` : ""}
          ${entry.current ? '<span class="gemini-narrative-badge">Nykyinen</span>' : ""}
        </div>
        ${buildNarrativeContentHtml(entry.narrative)}
      `;
    }
  }
}

function renderLearningReportMeta(report) {
  if (!els.learningReportMeta) return;
  const next = report.nextNarrativeInSec;
  const last = report.lastNarrativeAt;
  const parts = [`Päivitetty ${report.timestamp ? formatTime(report.timestamp) : "juuri nyt"}`];
  if (report.narrativePending) {
    parts.push("Gemini kirjoittaa kertomusta…");
  } else if (last) {
    parts.push(`Gemini ${formatTime(last)}`);
  } else if (next === 0) {
    parts.push("Gemini-kertomus tulossa");
  } else {
    parts.push("Gemini odottaa seuraavaa kierrosta");
  }
  if (report.narrativePending) {
    // countdown hidden while writing
  } else if (report.narrativeError) {
    if (next != null && next > 0) {
      parts.push(`uudelleenyritys ${formatDurationSec(next)} kuluttua`);
    } else {
      parts.push("uusi kertomus epäonnistui — yritetään uudelleen");
    }
  } else if (next != null && next > 0) {
    parts.push(`seuraava kertomus ${formatDurationSec(next)} kuluttua`);
  } else if (next === 0 && last) {
    parts.push("seuraava kertomus nyt");
  }
  if (report.narrativeError) {
    const errShort = String(report.narrativeError).slice(0, 80);
    parts.push(errShort);
  }
  els.learningReportMeta.textContent = parts.join(" · ");
}

function renderLearningReport() {
  if (!els.learningReport) return;
  const report = resolveLearningReport();
  if (!report) {
    els.learningReport.innerHTML = '<p class="empty-log">Oppimisraportti latautuu…</p>';
    if (els.learningReportMeta) els.learningReportMeta.textContent = "Odotetaan dataa…";
    lastLearningReportBodyKey = "";
    return;
  }

  renderLearningReportMeta(report);

  const narrative = report.narrative;
  const retryMinBucket =
    report.narrativeError && report.nextNarrativeInSec > 0
      ? Math.floor(report.nextNarrativeInSec / 60)
      : "";
  const bodyKey = [
    report.timestamp,
    report.narrativePending,
    narrative?.story || "",
    narrative?.intro || "",
    report.narrativeError || "",
    retryMinBucket,
    (report.sections || []).length,
  ].join("|");
  if (bodyKey === lastLearningReportBodyKey) return;
  lastLearningReportBodyKey = bodyKey;
  let narrativeHtml = "";
  if (narrative?.story || (narrative && (narrative.intro || narrative.learned || narrative.in_use))) {
    narrativeHtml = buildNarrativeContentHtml(narrative);
  } else if (report.narrativePending) {
    narrativeHtml =
      '<div class="learning-narrative learning-narrative-pending"><p>Gemini kirjoittaa kertomusta… (päivittyy automaattisesti)</p></div>';
  } else if (report.narrativeError) {
    const retryNote =
      report.nextNarrativeInSec > 0
        ? `<p class="learning-narrative-retry">Uudelleenyritys ${formatDurationSec(report.nextNarrativeInSec)} kuluttua.</p>`
        : "";
    narrativeHtml = `<div class="learning-narrative learning-narrative-error"><p>${escapeHtml(report.narrativeError)}</p>${retryNote}</div>`;
  } else if (!report.lastNarrativeAt && report.nextNarrativeInSec > 0) {
    narrativeHtml = `<div class="learning-narrative learning-narrative-pending"><p>Seuraava Gemini-kertomus ${formatDurationSec(report.nextNarrativeInSec)} kuluttua (6 h välein).</p></div>`;
  } else if (!report.lastNarrativeAt) {
    narrativeHtml =
      '<div class="learning-narrative learning-narrative-pending"><p>Gemini kirjoittaa ensimmäistä kertomusta… (päivittyy automaattisesti)</p></div>';
  }

  const sectionsHtml = (report.sections || [])
    .map(
      (sec) => `
      <div class="learning-section">
        <h4>${sec.icon || ""} ${escapeHtml(sec.title || "")}</h4>
        <ul>${(sec.lines || []).map((line) => `<li>${escapeHtml(line)}</li>`).join("")}</ul>
      </div>`
    )
    .join("");

  const changesHtml = (report.changes || []).length
    ? `<div class="learning-changes">
        <h4>Muuttunut edelliseen raporttiin</h4>
        <ul>${report.changes.map((c) => `<li>${escapeHtml(c)}</li>`).join("")}</ul>
      </div>`
    : "";

  const roadmapHtml = (report.roadmap || []).length
    ? `<div class="learning-roadmap">
        <h4>Roadmap</h4>
        <ul>${report.roadmap
          .map((r) => {
            const cls =
              r.status === "aktiivinen" || r.status === "valmis"
                ? "roadmap-status-ready"
                : r.status === "tulossa"
                  ? "roadmap-status-soon"
                  : "";
            const progress = r.progress ? ` · ${r.progress}` : "";
            return `<li><span class="${cls}">${escapeHtml(r.label)}${escapeHtml(progress)}</span><span>${escapeHtml(r.action)}</span></li>`;
          })
          .join("")}</ul>
      </div>`
    : "";

  els.learningReport.innerHTML = `
    ${narrativeHtml}
    ${renderGeminiPickTrackingHtml()}
    <div class="learning-sections">${sectionsHtml}</div>
    ${changesHtml}
    ${roadmapHtml}
  `;
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

  function signedEurSuffix(value) {
    if (!Number.isFinite(value)) return "";
    const sign = value >= 0 ? "+" : "−";
    const abs = Math.abs(value);
    const formatted = abs.toLocaleString("fi-FI", {
      minimumFractionDigits: 2,
      maximumFractionDigits: abs < 1 ? 4 : 2,
    });
    return ` (${sign}${formatted} €)`;
  }

  if (trade.type === "sell") {
    const costBasis = trade.costBasis ?? trade.eurTotal - (trade.profitLoss ?? trade.profit ?? 0);
    const profitLoss = trade.profitLoss ?? trade.profit ?? trade.eurTotal - costBasis;
    if (!costBasis) return "";
    const pct = (profitLoss / costBasis) * 100;
    const cls = pct >= 0 ? "up" : "down";
    const sign = pct >= 0 ? "+" : "";
    return `<span class="trade-pnl ${cls}">Myynti ${sign}${pct.toFixed(2)} %${signedEurSuffix(profitLoss)}</span>`;
  }
  const ticker = state.tickers[trade.symbol];
  if (!ticker || !trade.price) return "";
  const stillHeld = Object.prototype.hasOwnProperty.call(state.portfolio.holdings || {}, trade.symbol);
  const pct = ((ticker.last - trade.price) / trade.price) * 100;
  const unrealizedEur = (ticker.last - trade.price) * (trade.amount || 0);
  const cls = pct >= 0 ? "up" : "down";
  const sign = pct >= 0 ? "+" : "";
  return `<span class="trade-pnl ${cls}">Nyt ${sign}${pct.toFixed(2)} %${signedEurSuffix(unrealizedEur)}${stillHeld ? "" : " · myyty"}</span>`;
}

function renderTradeLog() {
  const trades = state.portfolio.trades || [];
  if (!trades.length) {
    els.tradeLog.innerHTML = '<p class="empty-log">Ei kauppoja vielä.</p>';
    return;
  }

  const filtered =
    tradeLogFilter === "all"
      ? trades
      : trades.filter((t) => t.type === tradeLogFilter);

  if (!filtered.length) {
    const emptyMsg =
      tradeLogFilter === "buy"
        ? "Ei ostoja."
        : tradeLogFilter === "sell"
          ? "Ei myyntejä."
          : "Ei kauppoja vielä.";
    els.tradeLog.innerHTML = `<p class="empty-log">${emptyMsg}</p>`;
    return;
  }

  els.tradeLog.innerHTML = filtered
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
    renderNextCountdown();
    renderUptime();
    const report = resolveLearningReport();
    if (report) renderLearningReportMeta(report);
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

els.learningReportTitle?.addEventListener("click", openGeminiNarrativeModal);

els.geminiNarrativeClose?.addEventListener("click", closeGeminiNarrativeModal);

els.geminiNarrativeModal?.addEventListener("click", (e) => {
  if (e.target === els.geminiNarrativeModal) closeGeminiNarrativeModal();
});

els.geminiNarrativeSearch?.addEventListener("input", (e) => {
  narrativeModalSearch = e.target.value;
  narrativeModalSelectedIdx = 0;
  renderGeminiNarrativeModal();
});

els.geminiNarrativeList?.addEventListener("click", (e) => {
  const btn = e.target.closest(".gemini-narrative-list-item");
  if (!btn) return;
  narrativeModalSelectedIdx = Number(btn.dataset.idx) || 0;
  renderGeminiNarrativeModal();
});

document.addEventListener("keydown", (e) => {
  if (!narrativeModalOpen) return;
  if (e.key === "Escape") {
    e.preventDefault();
    closeGeminiNarrativeModal();
    return;
  }
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    if (!narrativeModalFiltered.length) return;
    e.preventDefault();
    if (e.key === "ArrowDown") {
      narrativeModalSelectedIdx = Math.min(
        narrativeModalSelectedIdx + 1,
        narrativeModalFiltered.length - 1
      );
    } else {
      narrativeModalSelectedIdx = Math.max(narrativeModalSelectedIdx - 1, 0);
    }
    renderGeminiNarrativeModal();
    els.geminiNarrativeList
      ?.querySelector(`.gemini-narrative-list-item[data-idx="${narrativeModalSelectedIdx}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }
});

els.marketSearch.addEventListener("input", (e) => {
  marketSearch = e.target.value;
  renderMarketList();
});

document.querySelectorAll("[data-trade-filter]").forEach((btn) => {
  btn.addEventListener("click", () => {
    tradeLogFilter = btn.dataset.tradeFilter || "all";
    document.querySelectorAll("[data-trade-filter]").forEach((b) => {
      b.classList.toggle("active", b === btn);
    });
    renderTradeLog();
  });
});

const botUrlEl = document.getElementById("bot-url");
if (botUrlEl) {
  botUrlEl.href = location.origin;
  botUrlEl.textContent = location.origin;
}

poll();
startCountdown();
pollTimer = setInterval(poll, POLL_INTERVAL);
