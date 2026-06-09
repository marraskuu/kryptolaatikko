const INITIAL_CAPITAL = 1000;
export const TAX_RATE = 0.30;

export class Portfolio {
  constructor() {
    this.initialCapital = INITIAL_CAPITAL;
    this.cash = INITIAL_CAPITAL;
    this.totalTaxPaid = 0;
    this.totalRealizedProfit = 0;
    /** @type {Map<string, { amount: number, avgPrice: number }>} */
    this.holdings = new Map();
    /** @type {{ id: number, type: 'buy' | 'sell' | 'tax', symbol: string, amount?: number, price?: number, eurTotal: number, timestamp: Date, reason: string, profit?: number, tax?: number }[]} */
    this.trades = [];
    this.tradeId = 0;
  }

  reset() {
    this.cash = INITIAL_CAPITAL;
    this.totalTaxPaid = 0;
    this.totalRealizedProfit = 0;
    this.holdings.clear();
    this.trades = [];
    this.tradeId = 0;
  }

  /**
   * @param {string} symbol
   * @param {number} eurAmount
   * @param {number} price
   * @param {string} reason
   */
  buy(symbol, eurAmount, price, reason) {
    if (eurAmount < 1 || this.cash < 1) return false;

    const fee = eurAmount * 0.001;
    const totalCost = eurAmount + fee;

    if (totalCost > this.cash) {
      eurAmount = this.cash / 1.001;
      if (eurAmount < 1) return false;
    }

    const finalFee = eurAmount * 0.001;
    const finalCost = eurAmount + finalFee;
    const amount = eurAmount / price;

    this.cash -= finalCost;

    const existing = this.holdings.get(symbol);
    if (existing) {
      const totalAmount = existing.amount + amount;
      const totalCostBasis = existing.amount * existing.avgPrice + eurAmount;
      existing.amount = totalAmount;
      existing.avgPrice = totalCostBasis / totalAmount;
    } else {
      this.holdings.set(symbol, { amount, avgPrice: price });
    }

    this.trades.unshift({
      id: ++this.tradeId,
      type: "buy",
      symbol,
      amount,
      price,
      eurTotal: eurAmount,
      fee: finalFee,
      timestamp: new Date(),
      reason,
    });

    return true;
  }

  /**
   * Jakaa kaiken käteisen tasaisesti useaan ostoon (palkkiot huomioiden)
   * @param {{ symbol: string, price: number, reason: string }[]} slots
   */
  allocateInitial(slots) {
    const count = Math.min(4, slots.length);
    for (let i = 0; i < count; i++) {
      if (this.cash < 5) break;
      const slotsLeft = count - i;
      const { symbol, price, reason } = slots[i];
      const buyAmount = this.cash / (slotsLeft * 1.001);
      this.buy(symbol, buyAmount, price, reason);
    }
  }

  /**
   * @param {string} symbol
   * @param {number} amount
   * @param {number} price
   * @param {string} reason
   */
  sell(symbol, amount, price, reason) {
    const holding = this.holdings.get(symbol);
    if (!holding || amount > holding.amount) return false;

    const eurTotal = amount * price;
    const fee = eurTotal * 0.001;
    const costBasis = amount * holding.avgPrice;
    const profit = eurTotal - costBasis;
    let tax = 0;

    if (profit > 0) {
      tax = profit * TAX_RATE;
      this.totalTaxPaid += tax;
      this.totalRealizedProfit += profit;
    }

    this.cash += eurTotal - fee - tax;

    holding.amount -= amount;
    if (holding.amount < 0.00000001) {
      this.holdings.delete(symbol);
    }

    this.trades.unshift({
      id: ++this.tradeId,
      type: "sell",
      symbol,
      amount,
      price,
      eurTotal,
      costBasis,
      fee,
      profitLoss: profit,
      profit: profit > 0 ? profit : 0,
      tax,
      timestamp: new Date(),
      reason,
    });

    if (tax > 0) {
      this.trades.unshift({
        id: ++this.tradeId,
        type: "tax",
        symbol,
        eurTotal: tax,
        profit,
        tax,
        timestamp: new Date(),
        reason: `30 % vero voitosta (${profit.toFixed(2)} €)`,
      });
    }

    return true;
  }

  /**
   * @param {Map<string, { last: number }>} tickers
   */
  getTotalValue(tickers) {
    let holdingsValue = 0;
    for (const [symbol, holding] of this.holdings) {
      const ticker = tickers.get(symbol);
      if (ticker) {
        holdingsValue += holding.amount * ticker.last;
      }
    }
    return this.cash + holdingsValue;
  }

  getPnL(totalValue) {
    const pnl = totalValue - this.initialCapital;
    const pnlPct = (pnl / this.initialCapital) * 100;
    return { pnl, pnlPct };
  }

  /**
   * @param {Map<string, { last: number }>} tickers
   */
  getUnrealizedProfit(tickers) {
    let unrealized = 0;
    for (const [symbol, holding] of this.holdings) {
      const ticker = tickers.get(symbol);
      if (!ticker) continue;
      const gain = (ticker.last - holding.avgPrice) * holding.amount;
      if (gain > 0) unrealized += gain;
    }
    return unrealized;
  }

  /**
   * @param {Map<string, { last: number }>} tickers
   */
  getTaxSummary(tickers) {
    const unrealizedProfit = this.getUnrealizedProfit(tickers);
    const estimatedTax = unrealizedProfit * TAX_RATE;
    return {
      totalTaxPaid: this.totalTaxPaid,
      estimatedTax,
      totalTaxLiability: this.totalTaxPaid + estimatedTax,
      unrealizedProfit,
    };
  }
}

export const portfolio = new Portfolio();
