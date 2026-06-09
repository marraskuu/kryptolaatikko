/**
 * Tekninen analyysi ja AI-kaupankäyntipäätökset
 */

/**
 * @param {number[]} closes
 * @param {number} period
 */
export function calcRSI(closes, period = 14) {
  if (closes.length < period + 1) return 50;

  let gains = 0;
  let losses = 0;

  for (let i = closes.length - period; i < closes.length; i++) {
    const diff = closes[i] - closes[i - 1];
    if (diff >= 0) gains += diff;
    else losses -= diff;
  }

  const avgGain = gains / period;
  const avgLoss = losses / period;
  if (avgLoss === 0) return 100;
  const rs = avgGain / avgLoss;
  return 100 - 100 / (1 + rs);
}

/**
 * @param {number[]} values
 * @param {number} period
 */
export function calcEMA(values, period) {
  if (values.length === 0) return 0;
  const k = 2 / (period + 1);
  let ema = values[0];
  for (let i = 1; i < values.length; i++) {
    ema = values[i] * k + ema * (1 - k);
  }
  return ema;
}

/**
 * @param {number[]} closes
 */
export function calcMomentum(closes) {
  if (closes.length < 10) return 0;
  const recent = closes.slice(-5);
  const older = closes.slice(-10, -5);
  const recentAvg = recent.reduce((a, b) => a + b, 0) / recent.length;
  const olderAvg = older.reduce((a, b) => a + b, 0) / older.length;
  return ((recentAvg - olderAvg) / olderAvg) * 100;
}

/**
 * Nopea analyysi ticker-datasta (kaikille markkinoille)
 * @param {{ last: number, changePct: number, volumeEur: number }} ticker
 */
export function analyzeTickerQuick(ticker) {
  const changePct = ticker.changePct;
  let score = 0;
  const reasons = [];

  if (changePct < -10) {
    score += 3;
    reasons.push(`24h ${changePct.toFixed(1)} % — voimakas lasku, ostomahdollisuus`);
  } else if (changePct < -4) {
    score += 2;
    reasons.push(`24h ${changePct.toFixed(1)} % — lasku, mahdollinen osto`);
  } else if (changePct > 10) {
    score -= 2;
    reasons.push(`24h +${changePct.toFixed(1)} % — voitto otettu`);
  } else if (changePct > 4) {
    score += 1;
    reasons.push(`24h +${changePct.toFixed(1)} % — nousussa`);
  } else {
    score += 1;
    reasons.push(`24h ${changePct.toFixed(1)} % — vakaa`);
  }

  if (ticker.volumeEur > 500_000) {
    score += 1;
    reasons.push("Hyvä likviditeetti");
  }

  let action = "hold";
  if (score >= 2) action = "buy";
  else if (score <= -2) action = "sell";

  return {
    action,
    score,
    rsi: 50,
    ema9: ticker.last,
    ema21: ticker.last,
    momentum: changePct,
    currentPrice: ticker.last,
    volumeEur: ticker.volumeEur,
    reasons,
    strength: Math.min(Math.abs(score) / 4, 1),
    quick: true,
  };
}

/**
 * @param {{ close: number }[]} candles
 */
export function analyzeMarket(candles) {
  const closes = candles.map((c) => c.close);
  const rsi = calcRSI(closes);
  const ema9 = calcEMA(closes.slice(-20), 9);
  const ema21 = calcEMA(closes.slice(-30), 21);
  const momentum = calcMomentum(closes);
  const currentPrice = closes[closes.length - 1];

  const emaBullish = ema9 > ema21;
  const emaSpread = ((ema9 - ema21) / ema21) * 100;

  let score = 0;
  const reasons = [];

  if (rsi < 30) {
    score += 3;
    reasons.push(`RSI ${rsi.toFixed(1)} — ylimyyty (ostosignaali)`);
  } else if (rsi < 45) {
    score += 1;
    reasons.push(`RSI ${rsi.toFixed(1)} — lievä ostopaine`);
  } else if (rsi > 70) {
    score -= 3;
    reasons.push(`RSI ${rsi.toFixed(1)} — yliostettu (myyntisignaali)`);
  } else if (rsi > 55) {
    score -= 1;
    reasons.push(`RSI ${rsi.toFixed(1)} — lievä myyntipaine`);
  } else {
    reasons.push(`RSI ${rsi.toFixed(1)} — neutraali`);
  }

  if (emaBullish && emaSpread > 0.5) {
    score += 2;
    reasons.push(`EMA9 > EMA21 (+${emaSpread.toFixed(2)} %) — nousutrendi`);
  } else if (!emaBullish && emaSpread < -0.5) {
    score -= 2;
    reasons.push(`EMA9 < EMA21 (${emaSpread.toFixed(2)} %) — laskutrendi`);
  } else {
    reasons.push(`EMA-risteys neutraali (${emaSpread.toFixed(2)} %)`);
  }

  if (momentum > 2) {
    score += 2;
    reasons.push(`Momentum +${momentum.toFixed(2)} % — vahva nousu`);
  } else if (momentum < -2) {
    score -= 2;
    reasons.push(`Momentum ${momentum.toFixed(2)} % — vahva lasku`);
  } else {
    reasons.push(`Momentum ${momentum.toFixed(2)} % — maltillinen`);
  }

  let action = "hold";
  if (score >= 3) action = "buy";
  else if (score <= -3) action = "sell";

  return {
    action,
    score,
    rsi,
    ema9,
    ema21,
    momentum,
    currentPrice,
    reasons,
    strength: Math.min(Math.abs(score) / 5, 1),
    quick: false,
  };
}

/**
 * Valitsee 3–4 parasta kryptoa ja tekee kauppapäätökset
 * @param {Map<string, ReturnType<typeof analyzeMarket>>} analyses
 * @param {{ cash: number, holdings: Map<string, { amount: number, avgPrice: number }> }} portfolio
 * @param {number} totalValue
 * @param {(symbol: string) => string} labelFn
 */
export function makeTradingDecisions(analyses, portfolio, totalValue, labelFn = (s) => s) {
  /** @type {{ symbol: string, analysis: ReturnType<typeof analyzeMarket>, rank: number }[]} */
  const ranked = [];

  for (const [symbol, analysis] of analyses) {
    if (analysis.currentPrice > 0) {
      ranked.push({ symbol, analysis, rank: analysis.score });
    }
  }

  ranked.sort((a, b) => b.rank - a.rank || (b.analysis.volumeEur ?? 0) - (a.analysis.volumeEur ?? 0));

  const targetCount = portfolio.holdings.size === 0 ? 4 : Math.min(4, Math.max(3, portfolio.holdings.size));
  const topCryptos = ranked.slice(0, targetCount);
  const topSymbols = new Set(topCryptos.map((c) => c.symbol));

  /** @type {{ type: 'buy' | 'sell' | 'hold', symbol: string, amount?: number, eurAmount?: number, reason: string, analysis: ReturnType<typeof analyzeMarket> }[]} */
  const decisions = [];

  // Tyhjä salkku: merkitään alkuallokaatioksi (suoritetaan app.js:ssä)
  if (portfolio.holdings.size === 0 && portfolio.cash > 100 && topCryptos.length > 0) {
    return {
      decisions: [],
      targetCount,
      topSymbols,
      initialAllocation: topCryptos.slice(0, Math.min(targetCount, topCryptos.length)),
    };
  }

  for (const [symbol, holding] of portfolio.holdings) {
    const analysis = analyses.get(symbol);
    if (!analysis) continue;

    const holdingValue = holding.amount * analysis.currentPrice;
    const profitPct = ((analysis.currentPrice - holding.avgPrice) / holding.avgPrice) * 100;

    if (profitPct >= 3) {
      decisions.push({
        type: "hold",
        symbol,
        reason: "Voitto-Myyntistrategia: +3 % saavutettu — odotetaan 180 s ja kurssin kääntymistä",
        analysis,
      });
    } else if (!topSymbols.has(symbol) || analysis.action === "sell") {
      decisions.push({
        type: "sell",
        symbol,
        amount: holding.amount,
        eurAmount: holdingValue,
        reason: !topSymbols.has(symbol)
          ? `${labelFn(symbol)} putosi top ${targetCount}:sta — myydään ja siirretään parempiin`
          : analysis.reasons.join("; "),
        analysis,
      });
    } else if (analysis.action === "hold") {
      decisions.push({
        type: "hold",
        symbol,
        reason: "Pidetään — odotetaan parempaa signaalia",
        analysis,
      });
    }
  }

  const sellProceeds = decisions
    .filter((d) => d.type === "sell")
    .reduce((sum, d) => sum + (d.eurAmount || 0), 0);

  let availableCash = portfolio.cash + sellProceeds;
  const targetPerCrypto = availableCash / targetCount;

  for (const { symbol, analysis } of topCryptos) {
    const holding = portfolio.holdings.get(symbol);
    const holdingValue = holding ? holding.amount * analysis.currentPrice : 0;
    const deficit = targetPerCrypto - holdingValue;

    if (!holding && availableCash > 30) {
      const buyAmount = Math.min(targetPerCrypto, availableCash * 0.95);
      if (buyAmount >= 15) {
        decisions.push({
          type: "buy",
          symbol,
          eurAmount: buyAmount,
          amount: buyAmount / analysis.currentPrice,
          reason: `Uusi positio top ${targetCount}:een — ${analysis.reasons[0]}`,
          analysis,
        });
        availableCash -= buyAmount;
      }
    } else if (holding && deficit > 15 && availableCash > 30) {
      const buyAmount = Math.min(deficit, availableCash * 0.4);
      if (buyAmount >= 15) {
        decisions.push({
          type: "buy",
          symbol,
          eurAmount: buyAmount,
          amount: buyAmount / analysis.currentPrice,
          reason: `Tasapainotus — lisätään ${labelFn(symbol)}`,
          analysis,
        });
        availableCash -= buyAmount;
      }
    } else if (holding && analysis.action === "buy" && deficit > 10 && availableCash > 20) {
      const buyAmount = Math.min(deficit, availableCash * 0.3);
      if (buyAmount >= 10) {
        decisions.push({
          type: "buy",
          symbol,
          eurAmount: buyAmount,
          amount: buyAmount / analysis.currentPrice,
          reason: `Ostosignaali — ${analysis.reasons[0]}`,
          analysis,
        });
        availableCash -= buyAmount;
      }
    }
  }

  // Käteistä liikaa → osta paras puuttuva
  if (portfolio.cash > 150 && !decisions.some((d) => d.type === "buy")) {
    const bestNotHeld = ranked.find((r) => !portfolio.holdings.has(r.symbol));
    if (bestNotHeld && topSymbols.has(bestNotHeld.symbol)) {
      const buyAmount = Math.min(portfolio.cash * 0.25, targetPerCrypto);
      if (buyAmount >= 15) {
        decisions.push({
          type: "buy",
          symbol: bestNotHeld.symbol,
          eurAmount: buyAmount,
          amount: buyAmount / bestNotHeld.analysis.currentPrice,
          reason: `Käteinen käytössä — ${bestNotHeld.analysis.reasons[0]}`,
          analysis: bestNotHeld.analysis,
        });
      }
    }
  }

  return { decisions, targetCount, topSymbols };
}

/**
 * @param {ReturnType<typeof makeTradingDecisions>['decisions']} decisions
 * @param {(symbol: string) => string} labelFn
 */
export function buildDecisionReport(decisions, labelFn = (s) => s) {
  const label = labelFn;
  const buys = decisions.filter((d) => d.type === "buy");
  const sells = decisions.filter((d) => d.type === "sell");
  const holds = decisions.filter((d) => d.type === "hold");

  let title = "AI-analyysi valmis";
  let subtitle = `${buys.length} ostoa · ${sells.length} myyntiä · ${holds.length} pidossa`;

  if (buys.length && sells.length) {
    title = "Ostoja ja myyntejä";
  } else if (buys.length) {
    title = `Ostetaan ${buys.length} kryptoa`;
  } else if (sells.length) {
    title = `Myydään ${sells.length} kryptoa`;
  } else if (holds.length) {
    title = "Pidetään positioita";
    subtitle = "Ei uusia kauppoja tällä kierroksella";
  } else {
    title = "Ei toimenpiteitä";
    subtitle = "Odotetaan parempaa signaalia";
  }

  const action =
    buys.length && sells.length ? "mixed" : buys.length ? "buy" : sells.length ? "sell" : "hold";

  return {
    action,
    title,
    subtitle,
    buys: buys.map((b) => ({
      symbol: label(b.symbol),
      amount: b.eurAmount,
      reason: b.reason,
      analysis: b.analysis,
    })),
    sells: sells.map((s) => ({
      symbol: label(s.symbol),
      amount: s.eurAmount,
      reason: s.reason,
      analysis: s.analysis,
    })),
    holds: holds.map((h) => ({
      symbol: label(h.symbol),
      reason: h.reason,
      analysis: h.analysis,
    })),
  };
}
