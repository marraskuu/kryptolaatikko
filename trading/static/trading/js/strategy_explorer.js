(function () {
  "use strict";

  const UI_LANG = document.documentElement.lang === "en" ? "en" : "fi";
  const LOCALE = UI_LANG === "en" ? "en-US" : "fi-FI";

  const STRINGS = {
    fi: {
      needSymbol: "Anna kryptosymboli, esim. BTC.",
      needDates: "Valitse alkamis- ja päättymispäivä.",
      running: "Ajetaan backtestiä — haetaan kynttilähistoriaa Bitfinexiltä…",
      failed: "Backtest epäonnistui — yritä hetken päästä uudelleen.",
      errInvalidSymbol: "Tuntematon symboli — kokeile esim. BTC, ETH tai SOL.",
      errNoCandles: "Kynttilähistoriaa ei löytynyt tälle symbolille/aikavälille.",
      errMissingParams: "Symboli ja aikaväli puuttuvat.",
      errBadDate: "Virheellinen päivämäärä.",
      errRangeOrder: "Alkamispäivän pitää olla ennen päättymispäivää.",
      errRangeTooLong: "Aikaväli liian pitkä (max {maxDays} päivää).",
      errRateLimit: "Liian monta hakua — odota hetki ja yritä uudelleen.",
      resultsTitle: "Tulos — {base} · {start}–{end}",
      summaryReturn: "Tuotto",
      summaryEndBalance: "Loppusaldo",
      summaryTradeCount: "Kauppoja",
      summaryWinRate: "Voitto-%",
      chartEmpty: "Ei kauppoja tällä aikavälillä.",
      tradesEmpty: "Ei kauppoja tällä aikavälillä — pisteytys ei ylittänyt osto-kynnystä.",
      reasonProfitTake: "Voitto-myynti",
      reasonStop: "Stop-loss",
      reasonTimeLimit: "Aikaraja",
      reasonDataEnd: "Data loppui",
    },
    en: {
      needSymbol: "Enter a crypto symbol, e.g. BTC.",
      needDates: "Pick a start and end date.",
      running: "Running backtest — fetching candle history from Bitfinex…",
      failed: "Backtest failed — try again in a moment.",
      errInvalidSymbol: "Unknown symbol — try e.g. BTC, ETH or SOL.",
      errNoCandles: "No candle history found for this symbol/date range.",
      errMissingParams: "Symbol and date range are missing.",
      errBadDate: "Invalid date.",
      errRangeOrder: "The start date must be before the end date.",
      errRangeTooLong: "Date range too long (max {maxDays} days).",
      errRateLimit: "Too many requests — wait a moment and try again.",
      resultsTitle: "Result — {base} · {start}–{end}",
      summaryReturn: "Return",
      summaryEndBalance: "Ending balance",
      summaryTradeCount: "Trades",
      summaryWinRate: "Win rate",
      chartEmpty: "No trades in this period.",
      tradesEmpty: "No trades in this period — score never crossed the buy threshold.",
      reasonProfitTake: "Profit take",
      reasonStop: "Stop-loss",
      reasonTimeLimit: "Time limit",
      reasonDataEnd: "Data ended",
    },
  };

  function t(key, vars) {
    let str = (STRINGS[UI_LANG] || STRINGS.fi)[key];
    if (str == null) return key;
    if (vars) {
      str = str.replace(/\{(\w+)\}/g, (_, name) => (vars[name] != null ? String(vars[name]) : ""));
    }
    return str;
  }

  const SVG_NS = "http://www.w3.org/2000/svg";
  const PLOT = { x0: 56, x1: 946, y0: 14, y1: 290, width: 960, height: 320 };

  const els = {
    symbol: document.getElementById("explorer-symbol"),
    start: document.getElementById("explorer-start"),
    end: document.getElementById("explorer-end"),
    run: document.getElementById("explorer-run"),
    presets: document.querySelectorAll(".explorer-preset-btn"),
    status: document.getElementById("explorer-status"),
    results: document.getElementById("explorer-results"),
    resultsTitle: document.getElementById("explorer-results-title"),
    summary: document.getElementById("explorer-summary"),
    chart: document.getElementById("explorer-chart"),
    tooltip: document.getElementById("explorer-tooltip"),
    tradesBody: document.getElementById("explorer-trades-body"),
  };

  function isoDate(d) {
    return d.toISOString().slice(0, 10);
  }

  function setDefaultRange(days) {
    const end = new Date();
    const start = new Date(end.getTime() - days * 86400000);
    els.start.value = isoDate(start);
    els.end.value = isoDate(end);
  }

  setDefaultRange(90);
  els.end.max = isoDate(new Date());
  loadSymbols();

  async function loadSymbols() {
    try {
      const res = await fetch("/api/strategy-explorer/symbols/");
      const data = await res.json();
      const symbols = data.symbols || [];
      if (!symbols.length) return;

      const previous = els.symbol.value || "BTC";
      els.symbol.innerHTML = "";
      for (const item of symbols) {
        const opt = document.createElement("option");
        opt.value = item.base;
        opt.textContent = item.base;
        els.symbol.appendChild(opt);
      }
      const hasPrevious = symbols.some((item) => item.base === previous);
      els.symbol.value = hasPrevious ? previous : symbols[0].base;
    } catch (err) {
      // Pidetään HTML:n BTC-oletusvaihtoehto, jos lista ei latautunut.
    }
  }

  els.presets.forEach((btn) => {
    btn.addEventListener("click", () => {
      setDefaultRange(parseInt(btn.dataset.days, 10));
      runBacktest();
    });
  });

  els.run.addEventListener("click", runBacktest);

  function formatEur(value) {
    return (
      Number(value).toLocaleString(LOCALE, {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      }) + " €"
    );
  }

  function formatPct(value) {
    const sign = value > 0 ? "+" : "";
    return sign + Number(value).toLocaleString(LOCALE, { maximumFractionDigits: 2 }) + " %";
  }

  function formatDateTime(ms) {
    return new Date(ms).toLocaleString(LOCALE, {
      day: "numeric",
      month: "numeric",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function formatDate(ms) {
    return new Date(ms).toLocaleDateString(LOCALE, {
      day: "numeric",
      month: "numeric",
      year: "numeric",
    });
  }

  const REASON_LABELS = {
    profit_take: t("reasonProfitTake"),
    stop: t("reasonStop"),
    time_limit: t("reasonTimeLimit"),
    data_end: t("reasonDataEnd"),
  };

  function setStatus(message, isError) {
    if (!message) {
      els.status.hidden = true;
      els.status.textContent = "";
      return;
    }
    els.status.hidden = false;
    els.status.textContent = message;
    els.status.classList.toggle("is-error", !!isError);
  }

  async function runBacktest() {
    const symbol = (els.symbol.value || "").trim();
    const start = els.start.value;
    const end = els.end.value;

    if (!symbol) {
      setStatus(t("needSymbol"), true);
      return;
    }
    if (!start || !end) {
      setStatus(t("needDates"), true);
      return;
    }

    els.run.disabled = true;
    els.results.hidden = true;
    setStatus(t("running"), false);

    try {
      const params = new URLSearchParams({ symbol, start, end });
      const res = await fetch(`/api/strategy-explorer/?${params.toString()}`);
      const data = await res.json();

      if (!res.ok || data.error) {
        setStatus(errorMessage(data.error, data), true);
        return;
      }

      setStatus("", false);
      renderResults(data);
    } catch (err) {
      setStatus(t("failed"), true);
    } finally {
      els.run.disabled = false;
    }
  }

  function errorMessage(code, data) {
    switch (code) {
      case "invalid_symbol":
        return t("errInvalidSymbol");
      case "no_candles":
        return t("errNoCandles");
      case "missing_params":
        return t("errMissingParams");
      case "bad_date":
        return t("errBadDate");
      case "range_order":
        return t("errRangeOrder");
      case "range_too_long":
        return t("errRangeTooLong", { maxDays: data.maxDays || 400 });
      case "rate_limit":
        return t("errRateLimit");
      default:
        return t("failed");
    }
  }

  function renderResults(data) {
    els.results.hidden = false;
    els.resultsTitle.textContent = t("resultsTitle", {
      base: data.base,
      start: formatDate(data.startMs),
      end: formatDate(data.endMs),
    });
    renderSummary(data);
    renderChart(data.equityCurve, data.returnPct >= 0);
    renderTrades(data.trades);
  }

  function renderSummary(data) {
    const positive = data.returnPct >= 0;
    const cards = [
      {
        label: t("summaryReturn"),
        value: formatPct(data.returnPct),
        cls: positive ? "positive" : "negative",
      },
      {
        label: t("summaryEndBalance"),
        value: formatEur(data.endBalance),
        cls: positive ? "positive" : "negative",
      },
      { label: t("summaryTradeCount"), value: String(data.tradeCount), cls: "" },
      {
        label: t("summaryWinRate"),
        value: data.winRate == null ? "—" : data.winRate.toLocaleString(LOCALE) + " %",
        cls: "",
      },
    ];

    els.summary.innerHTML = "";
    for (const card of cards) {
      const div = document.createElement("div");
      div.className = "stat-card";
      const label = document.createElement("span");
      label.className = "stat-label";
      label.textContent = card.label;
      const value = document.createElement("span");
      value.className = "stat-value" + (card.cls ? " " + card.cls : "");
      value.textContent = card.value;
      div.appendChild(label);
      div.appendChild(value);
      els.summary.appendChild(div);
    }
  }

  function niceNum(range, round) {
    const exponent = Math.floor(Math.log10(range || 1));
    const fraction = range / Math.pow(10, exponent);
    let niceFraction;
    if (round) {
      if (fraction < 1.5) niceFraction = 1;
      else if (fraction < 3) niceFraction = 2;
      else if (fraction < 7) niceFraction = 5;
      else niceFraction = 10;
    } else if (fraction <= 1) niceFraction = 1;
    else if (fraction <= 2) niceFraction = 2;
    else if (fraction <= 5) niceFraction = 5;
    else niceFraction = 10;
    return niceFraction * Math.pow(10, exponent);
  }

  function niceTicks(min, max, count) {
    if (min === max) {
      min -= 1;
      max += 1;
    }
    const range = niceNum(max - min, false);
    const step = niceNum(range / (count - 1), true);
    const niceMin = Math.floor(min / step) * step;
    const niceMax = Math.ceil(max / step) * step;
    const ticks = [];
    for (let v = niceMin; v <= niceMax + step * 0.5; v += step) ticks.push(v);
    return { min: niceMin, max: niceMax, ticks };
  }

  let chartState = null;

  function renderChart(equityCurve, positive) {
    const svg = els.chart;
    svg.innerHTML = "";
    els.tooltip.hidden = true;

    if (!equityCurve || equityCurve.length < 2) {
      chartState = null;
      const text = document.createElementNS(SVG_NS, "text");
      text.setAttribute("x", PLOT.width / 2);
      text.setAttribute("y", PLOT.height / 2);
      text.setAttribute("text-anchor", "middle");
      text.setAttribute("class", "explorer-axis-label");
      text.textContent = t("chartEmpty");
      svg.appendChild(text);
      return;
    }

    const values = equityCurve.map((p) => p.equity);
    const { min: yMin, max: yMax, ticks } = niceTicks(
      Math.min(...values),
      Math.max(...values),
      5
    );
    const t0 = equityCurve[0].t;
    const t1 = equityCurve[equityCurve.length - 1].t;
    const tSpan = Math.max(1, t1 - t0);
    const ySpan = Math.max(1e-6, yMax - yMin);

    const toX = (t) => PLOT.x0 + ((t - t0) / tSpan) * (PLOT.x1 - PLOT.x0);
    const toY = (v) => PLOT.y1 - ((v - yMin) / ySpan) * (PLOT.y1 - PLOT.y0);

    const gGrid = document.createElementNS(SVG_NS, "g");
    ticks.forEach((tick) => {
      if (tick < yMin - 1e-6 || tick > yMax + 1e-6) return;
      const y = toY(tick);
      const line = document.createElementNS(SVG_NS, "line");
      line.setAttribute("class", "explorer-gridline");
      line.setAttribute("x1", PLOT.x0);
      line.setAttribute("x2", PLOT.x1);
      line.setAttribute("y1", y);
      line.setAttribute("y2", y);
      gGrid.appendChild(line);

      const label = document.createElementNS(SVG_NS, "text");
      label.setAttribute("class", "explorer-axis-label");
      label.setAttribute("x", PLOT.x0 - 8);
      label.setAttribute("y", y + 4);
      label.setAttribute("text-anchor", "end");
      label.textContent = Math.round(tick).toLocaleString(LOCALE) + " €";
      gGrid.appendChild(label);
    });
    svg.appendChild(gGrid);

    const points = equityCurve.map((p) => [toX(p.t), toY(p.equity)]);
    const linePath = points.map((p, i) => (i === 0 ? "M" : "L") + p[0].toFixed(1) + "," + p[1].toFixed(1)).join(" ");
    const areaPath =
      linePath +
      ` L${points[points.length - 1][0].toFixed(1)},${PLOT.y1} L${points[0][0].toFixed(1)},${PLOT.y1} Z`;

    const sign = positive ? "positive" : "negative";

    const area = document.createElementNS(SVG_NS, "path");
    area.setAttribute("class", "explorer-area " + sign);
    area.setAttribute("d", areaPath);
    svg.appendChild(area);

    const line = document.createElementNS(SVG_NS, "path");
    line.setAttribute("class", "explorer-line " + sign);
    line.setAttribute("d", linePath);
    svg.appendChild(line);

    const last = points[points.length - 1];
    const endDot = document.createElementNS(SVG_NS, "circle");
    endDot.setAttribute("class", "explorer-end-dot " + sign);
    endDot.setAttribute("cx", last[0]);
    endDot.setAttribute("cy", last[1]);
    endDot.setAttribute("r", 5);
    endDot.setAttribute("fill", "currentColor");
    svg.appendChild(endDot);

    const endLabel = document.createElementNS(SVG_NS, "text");
    endLabel.setAttribute("class", "explorer-axis-label");
    endLabel.setAttribute("x", Math.min(last[0] + 8, PLOT.x1 - 70));
    endLabel.setAttribute("y", last[1] - 10);
    endLabel.textContent = formatEur(equityCurve[equityCurve.length - 1].equity);
    svg.appendChild(endLabel);

    const startLabel = document.createElementNS(SVG_NS, "text");
    startLabel.setAttribute("class", "explorer-axis-label");
    startLabel.setAttribute("x", PLOT.x0);
    startLabel.setAttribute("y", PLOT.height - 6);
    startLabel.textContent = formatDate(t0);
    svg.appendChild(startLabel);

    const endDateLabel = document.createElementNS(SVG_NS, "text");
    endDateLabel.setAttribute("class", "explorer-axis-label");
    endDateLabel.setAttribute("x", PLOT.x1);
    endDateLabel.setAttribute("y", PLOT.height - 6);
    endDateLabel.setAttribute("text-anchor", "end");
    endDateLabel.textContent = formatDate(t1);
    svg.appendChild(endDateLabel);

    const crosshair = document.createElementNS(SVG_NS, "line");
    crosshair.setAttribute("class", "explorer-crosshair");
    crosshair.setAttribute("y1", PLOT.y0);
    crosshair.setAttribute("y2", PLOT.y1);
    svg.appendChild(crosshair);

    const hoverDot = document.createElementNS(SVG_NS, "circle");
    hoverDot.setAttribute("class", "explorer-hover-dot");
    hoverDot.setAttribute("r", 4);
    svg.appendChild(hoverDot);

    const hit = document.createElementNS(SVG_NS, "rect");
    hit.setAttribute("class", "explorer-chart-hit");
    hit.setAttribute("x", PLOT.x0);
    hit.setAttribute("y", PLOT.y0);
    hit.setAttribute("width", PLOT.x1 - PLOT.x0);
    hit.setAttribute("height", PLOT.y1 - PLOT.y0);
    svg.appendChild(hit);

    chartState = { equityCurve, points, crosshair, hoverDot, hit };
    hit.addEventListener("pointermove", onChartHover);
    hit.addEventListener("pointerleave", onChartLeave);
  }

  function onChartHover(evt) {
    if (!chartState) return;
    const svg = els.chart;
    const rect = svg.getBoundingClientRect();
    const scaleX = PLOT.width / rect.width;
    const px = (evt.clientX - rect.left) * scaleX;

    let nearest = 0;
    let bestDist = Infinity;
    chartState.points.forEach((p, i) => {
      const dist = Math.abs(p[0] - px);
      if (dist < bestDist) {
        bestDist = dist;
        nearest = i;
      }
    });

    const point = chartState.points[nearest];
    const data = chartState.equityCurve[nearest];
    chartState.crosshair.style.display = "block";
    chartState.crosshair.setAttribute("x1", point[0]);
    chartState.crosshair.setAttribute("x2", point[0]);
    chartState.hoverDot.style.display = "block";
    chartState.hoverDot.setAttribute("cx", point[0]);
    chartState.hoverDot.setAttribute("cy", point[1]);

    const scaleXInv = rect.width / PLOT.width;
    els.tooltip.hidden = false;
    els.tooltip.innerHTML = "";
    const strong = document.createElement("strong");
    strong.textContent = formatEur(data.equity);
    const small = document.createElement("div");
    small.textContent = formatDateTime(data.t);
    els.tooltip.appendChild(strong);
    els.tooltip.appendChild(small);

    let left = point[0] * scaleXInv - els.tooltip.offsetWidth / 2;
    left = Math.max(4, Math.min(left, rect.width - els.tooltip.offsetWidth - 4));
    els.tooltip.style.left = left + "px";
  }

  function onChartLeave() {
    if (!chartState) return;
    chartState.crosshair.style.display = "none";
    chartState.hoverDot.style.display = "none";
    els.tooltip.hidden = true;
  }

  function renderTrades(trades) {
    els.tradesBody.innerHTML = "";
    if (!trades || !trades.length) {
      const tr = document.createElement("tr");
      tr.className = "explorer-empty-row";
      const td = document.createElement("td");
      td.colSpan = 7;
      td.textContent = t("tradesEmpty");
      tr.appendChild(td);
      els.tradesBody.appendChild(tr);
      return;
    }

    for (const trade of trades) {
      const tr = document.createElement("tr");

      const entryAt = document.createElement("td");
      entryAt.textContent = formatDateTime(trade.entryAt);
      tr.appendChild(entryAt);

      const entryPrice = document.createElement("td");
      entryPrice.textContent = formatEur(trade.entryPrice);
      tr.appendChild(entryPrice);

      const exitAt = document.createElement("td");
      exitAt.textContent = formatDateTime(trade.exitAt);
      tr.appendChild(exitAt);

      const exitPrice = document.createElement("td");
      exitPrice.textContent = formatEur(trade.exitPrice);
      tr.appendChild(exitPrice);

      const returnPct = document.createElement("td");
      returnPct.textContent = formatPct(trade.returnPct);
      returnPct.className = trade.returnPct >= 0 ? "positive" : "negative";
      tr.appendChild(returnPct);

      const pnlEur = document.createElement("td");
      pnlEur.textContent = formatEur(trade.pnlEur);
      pnlEur.className = trade.pnlEur >= 0 ? "positive" : "negative";
      tr.appendChild(pnlEur);

      const reason = document.createElement("td");
      reason.textContent = REASON_LABELS[trade.reason] || trade.reason;
      tr.appendChild(reason);

      els.tradesBody.appendChild(tr);
    }
  }
})();
