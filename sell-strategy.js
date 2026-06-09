const PROFIT_TRIGGER_PCT = 3;
const WAIT_MS = 180 * 1000;

/** @typedef {{ active: boolean, peakPrice: number, peakTime: number, prevPrice: number, armed: boolean }} WatchState */

/** @type {Map<string, WatchState>} */
const states = new Map();

function defaultState() {
  return { active: false, peakPrice: 0, peakTime: 0, prevPrice: 0, armed: false };
}

export function resetWatch(symbol) {
  states.delete(symbol);
}

export function resetAllWatches() {
  states.clear();
}

/**
 * @param {string} symbol
 * @param {number} currentPrice
 * @param {number} avgPrice
 * @param {number} [now]
 */
export function updateProfitSell(symbol, currentPrice, avgPrice, now = Date.now()) {
  let state = states.get(symbol) ?? defaultState();
  const profitPct = ((currentPrice - avgPrice) / avgPrice) * 100;

  if (profitPct < PROFIT_TRIGGER_PCT) {
    if (state.active && !state.armed) {
      state = defaultState();
    }
    state.prevPrice = currentPrice;
    states.set(symbol, state);
    return {
      shouldSell: false,
      profitPct,
      status: "alle_3",
      statusText: `Voitto ${profitPct.toFixed(1)} % — odotetaan +3 %`,
      state,
      secondsLeft: 0,
    };
  }

  if (!state.active) {
    state.active = true;
    state.peakPrice = currentPrice;
    state.peakTime = now;
    state.armed = false;
  } else if (currentPrice >= state.peakPrice) {
    if (currentPrice > state.peakPrice) {
      state.peakPrice = currentPrice;
      state.peakTime = now;
      state.armed = false;
    }
  }

  const elapsed = now - state.peakTime;
  const secondsLeft = Math.max(0, Math.ceil((WAIT_MS - elapsed) / 1000));

  if (elapsed >= WAIT_MS) {
    state.armed = true;
  }

  let shouldSell = false;
  let reason = "";

  if (state.armed && state.prevPrice > 0 && currentPrice < state.prevPrice) {
    shouldSell = true;
    reason =
      `Voitto +${profitPct.toFixed(1)} % — 180 s huipun (${state.peakPrice.toFixed(2)} €) jälkeen, ` +
      `kurssi kääntyi laskuun (${state.prevPrice.toFixed(2)} → ${currentPrice.toFixed(2)} €)`;
  }

  state.prevPrice = currentPrice;
  states.set(symbol, state);

  let statusText;
  if (!state.armed) {
    statusText = `+${profitPct.toFixed(1)} % — odotetaan ${secondsLeft}s huipun jälkeen (huippu ${state.peakPrice.toFixed(2)} €)`;
  } else {
    statusText = `+${profitPct.toFixed(1)} % — valmis myyntiin, odotetaan kurssin kääntymistä`;
  }

  return {
    shouldSell,
    profitPct,
    reason,
    status: state.armed ? "armed" : "waiting",
    statusText,
    state,
    secondsLeft: state.armed ? 0 : secondsLeft,
  };
}

/**
 * @param {string} symbol
 */
export function getWatchStatus(symbol) {
  return states.get(symbol);
}

export { PROFIT_TRIGGER_PCT, WAIT_MS };
