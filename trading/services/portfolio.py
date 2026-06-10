from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

INITIAL_CAPITAL = 1000.0
TAX_RATE = 0.30


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_portfolio() -> dict[str, Any]:
    return {
        "initialCapital": INITIAL_CAPITAL,
        "cash": INITIAL_CAPITAL,
        "totalTaxPaid": 0.0,
        "totalRealizedProfit": 0.0,
        "holdings": {},
        "trades": [],
        "tradeId": 0,
    }


class Portfolio:
    def __init__(self, data: dict[str, Any] | None = None):
        self.data = deepcopy(data) if data else default_portfolio()

    @property
    def cash(self) -> float:
        return self.data["cash"]

    @property
    def holdings(self) -> dict[str, dict[str, float]]:
        return self.data["holdings"]

    @property
    def trades(self) -> list[dict[str, Any]]:
        return self.data["trades"]

    def to_dict(self) -> dict[str, Any]:
        return deepcopy(self.data)

    def reset(self) -> None:
        self.data = default_portfolio()

    def buy(self, symbol: str, eur_amount: float, price: float, reason: str) -> bool:
        from .bitfinex import is_stablecoin

        if is_stablecoin(symbol):
            return False
        if eur_amount < 1 or self.cash < 1:
            return False

        fee = eur_amount * 0.001
        total_cost = eur_amount + fee
        if total_cost > self.cash:
            eur_amount = self.cash / 1.001
            if eur_amount < 1:
                return False

        final_fee = eur_amount * 0.001
        final_cost = eur_amount + final_fee
        amount = eur_amount / price
        self.data["cash"] -= final_cost

        existing = self.holdings.get(symbol)
        if existing:
            total_amount = existing["amount"] + amount
            total_cost_basis = existing["amount"] * existing["avgPrice"] + eur_amount
            existing["amount"] = total_amount
            existing["avgPrice"] = total_cost_basis / total_amount
            existing.setdefault("openedAt", _now_iso())
        else:
            self.holdings[symbol] = {
                "amount": amount,
                "avgPrice": price,
                "openedAt": _now_iso(),
            }

        self.data["tradeId"] += 1
        self.trades.insert(
            0,
            {
                "id": self.data["tradeId"],
                "type": "buy",
                "symbol": symbol,
                "amount": amount,
                "price": price,
                "eurTotal": eur_amount,
                "fee": final_fee,
                "timestamp": _now_iso(),
                "reason": reason,
            },
        )
        return True

    def allocate_initial(self, slots: list[dict[str, Any]]) -> None:
        """Sijoittaa käteisen slotteihin. Jos eur_amount on annettu, käytetään sitä — muuten tasajaot."""
        if not slots:
            return

        has_amounts = all(
            (slot.get("eur_amount") or slot.get("eurAmount") or 0) >= 1 for slot in slots
        )
        if has_amounts:
            for slot in slots:
                if self.cash < 5:
                    break
                amount = slot.get("eur_amount") or slot.get("eurAmount") or 0
                self.buy(slot["symbol"], min(amount, self.cash / 1.001), slot["price"], slot["reason"])
            return

        count = min(4, len(slots))
        for i in range(count):
            if self.cash < 5:
                break
            slots_left = count - i
            slot = slots[i]
            buy_amount = self.cash / (slots_left * 1.001)
            self.buy(slot["symbol"], buy_amount, slot["price"], slot["reason"])

    def sell(self, symbol: str, amount: float, price: float, reason: str) -> bool:
        holding = self.holdings.get(symbol)
        if not holding or amount > holding["amount"]:
            return False

        eur_total = amount * price
        fee = eur_total * 0.001
        cost_basis = amount * holding["avgPrice"]
        profit = eur_total - cost_basis
        tax = 0.0

        if profit > 0:
            tax = profit * TAX_RATE
            self.data["totalTaxPaid"] += tax
            self.data["totalRealizedProfit"] += profit

        self.data["cash"] += eur_total - fee - tax
        holding["amount"] -= amount
        if holding["amount"] < 0.00000001:
            del self.holdings[symbol]

        self.data["tradeId"] += 1
        self.trades.insert(
            0,
            {
                "id": self.data["tradeId"],
                "type": "sell",
                "symbol": symbol,
                "amount": amount,
                "price": price,
                "eurTotal": eur_total,
                "costBasis": cost_basis,
                "fee": fee,
                "profitLoss": profit,
                "profit": profit if profit > 0 else 0,
                "tax": tax,
                "timestamp": _now_iso(),
                "reason": reason,
            },
        )

        if tax > 0:
            self.data["tradeId"] += 1
            self.trades.insert(
                0,
                {
                    "id": self.data["tradeId"],
                    "type": "tax",
                    "symbol": symbol,
                    "eurTotal": tax,
                    "profit": profit,
                    "tax": tax,
                    "timestamp": _now_iso(),
                    "reason": f"30 % vero voitosta ({profit:.2f} €)",
                },
            )
        return True

    def get_total_value(self, tickers: dict[str, dict[str, Any]]) -> float:
        holdings_value = 0.0
        for symbol, holding in self.holdings.items():
            ticker = tickers.get(symbol)
            if ticker:
                holdings_value += holding["amount"] * ticker["last"]
        return self.cash + holdings_value

    def get_pnl(self, total_value: float) -> dict[str, float]:
        pnl = total_value - self.data["initialCapital"]
        pnl_pct = (pnl / self.data["initialCapital"]) * 100
        return {"pnl": pnl, "pnlPct": pnl_pct}

    def get_unrealized_profit(self, tickers: dict[str, dict[str, Any]]) -> float:
        unrealized = 0.0
        for symbol, holding in self.holdings.items():
            ticker = tickers.get(symbol)
            if not ticker:
                continue
            gain = (ticker["last"] - holding["avgPrice"]) * holding["amount"]
            if gain > 0:
                unrealized += gain
        return unrealized

    def get_tax_summary(self, tickers: dict[str, dict[str, Any]]) -> dict[str, float]:
        unrealized = self.get_unrealized_profit(tickers)
        estimated_tax = unrealized * TAX_RATE
        return {
            "totalTaxPaid": self.data["totalTaxPaid"],
            "estimatedTax": estimated_tax,
            "totalTaxLiability": self.data["totalTaxPaid"] + estimated_tax,
            "unrealizedProfit": unrealized,
        }
