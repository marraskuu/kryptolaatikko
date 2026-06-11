from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

INITIAL_CAPITAL = 1000.0
TAX_RATE = 0.30
# Bitfinex poisti kaupankäyntikulut kokonaan (spot + margin, kaikki asiakkaat,
# pysyvästi). Simulaattori heijastaa tätä: 0 % maker/taker. Voittovero (30 %)
# ei ole pörssin kulu, joten se säilyy.
FEE_RATE = 0.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _year_of(iso: Any) -> int | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return dt.year
    except (ValueError, TypeError):
        return None


def _dt_of(iso: Any) -> datetime | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


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

    def buy(
        self,
        symbol: str,
        eur_amount: float,
        price: float,
        reason: str,
        meta: dict[str, Any] | None = None,
    ) -> bool:
        from .bitfinex import is_stablecoin

        if is_stablecoin(symbol):
            return False
        if eur_amount < 1 or self.cash < 1:
            return False

        fee = eur_amount * FEE_RATE
        total_cost = eur_amount + fee
        if total_cost > self.cash:
            eur_amount = self.cash / (1 + FEE_RATE)
            if eur_amount < 1:
                return False

        final_fee = eur_amount * FEE_RATE
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
        trade = {
            "id": self.data["tradeId"],
            "type": "buy",
            "symbol": symbol,
            "amount": amount,
            "price": price,
            "eurTotal": eur_amount,
            "fee": final_fee,
            "timestamp": _now_iso(),
            "reason": reason,
        }
        if meta:
            trade.update(meta)
        self.trades.insert(0, trade)
        return True

    def allocate_initial(
        self,
        slots: list[dict[str, Any]],
        meta: dict[str, Any] | None = None,
    ) -> None:
        """Sijoittaa käteisen slotteihin. Jos eur_amount on annettu, käytetään sitä — muuten tasajaot."""
        if not slots:
            return

        def _slot_meta(slot: dict[str, Any]) -> dict[str, Any] | None:
            merged = dict(meta or {})
            if slot.get("atrPct") is not None:
                merged["atrPct"] = slot["atrPct"]
            return merged or None

        has_amounts = all(
            (slot.get("eur_amount") or slot.get("eurAmount") or 0) >= 1 for slot in slots
        )
        if has_amounts:
            for slot in slots:
                if self.cash < 5:
                    break
                amount = slot.get("eur_amount") or slot.get("eurAmount") or 0
                self.buy(
                    slot["symbol"],
                    min(amount, self.cash / (1 + FEE_RATE)),
                    slot["price"],
                    slot["reason"],
                    meta=_slot_meta(slot),
                )
            return

        count = min(4, len(slots))
        for i in range(count):
            if self.cash < 5:
                break
            slots_left = count - i
            slot = slots[i]
            buy_amount = self.cash / (slots_left * (1 + FEE_RATE))
            self.buy(slot["symbol"], buy_amount, slot["price"], slot["reason"], meta=_slot_meta(slot))

    def sell(
        self,
        symbol: str,
        amount: float,
        price: float,
        reason: str,
        meta: dict[str, Any] | None = None,
    ) -> bool:
        holding = self.holdings.get(symbol)
        if not holding or amount > holding["amount"]:
            return False

        eur_total = amount * price
        fee = eur_total * FEE_RATE
        cost_basis = amount * holding["avgPrice"]
        profit = eur_total - cost_basis
        tax = 0.0

        if profit > 0:
            tax = profit * TAX_RATE
            # Veroa EI vähennetä salkusta — käyttäjä maksaa pääomatuloveron itse
            # jälkikäteen. Tax kirjataan vain raportointia varten (vuosittainen
            # arvio). Salkku kasvaa täydellä myyntisummalla.
            self.data["totalTaxPaid"] += tax
            self.data["totalRealizedProfit"] += profit

        self.data["cash"] += eur_total - fee
        holding["amount"] -= amount
        if holding["amount"] < 0.00000001:
            del self.holdings[symbol]

        self.data["tradeId"] += 1
        trade = {
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
        }
        if meta:
            trade.update(meta)
        self.trades.insert(0, trade)
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

    def realized_profit_by_year(self) -> dict[int, float]:
        """Toteutunut nettomyyntivoitto (voitot - tappiot) kalenterivuosittain."""
        by_year: dict[int, float] = {}
        for t in self.trades:
            if t.get("type") != "sell":
                continue
            year = _year_of(t.get("timestamp"))
            if year is None:
                continue
            by_year[year] = by_year.get(year, 0.0) + float(t.get("profitLoss") or 0.0)
        return by_year

    def get_realized_breakdown(self) -> dict[str, dict[str, Any]]:
        """Voitto-/tappiomyyntien lukumäärät ja eurot kolmelta jaksolta.

        Jaksot: kuluva kalenterivuosi, kuluva kalenterikuukausi ja viimeiset 24 h.
        Palauttaa kullekin: voittojen lkm + yhteen­laskettu voitto (€), tappioiden
        lkm + yhteenlaskettu tappio (€, positiivisena lukuna).
        """
        now = datetime.now(timezone.utc)
        day_cutoff = now.timestamp() - 24 * 3600

        def _blank() -> dict[str, Any]:
            return {"winCount": 0, "winEur": 0.0, "lossCount": 0, "lossEur": 0.0}

        periods = {"year": _blank(), "month": _blank(), "day": _blank()}

        for t in self.trades:
            if t.get("type") != "sell":
                continue
            dt = _dt_of(t.get("timestamp"))
            if dt is None:
                continue
            pl = float(t.get("profitLoss") or 0.0)
            in_year = dt.year == now.year
            in_month = in_year and dt.month == now.month
            in_day = dt.timestamp() >= day_cutoff
            for key, included in (("year", in_year), ("month", in_month), ("day", in_day)):
                if not included:
                    continue
                bucket = periods[key]
                if pl > 0.01:
                    bucket["winCount"] += 1
                    bucket["winEur"] += pl
                elif pl < -0.01:
                    bucket["lossCount"] += 1
                    bucket["lossEur"] += -pl

        for bucket in periods.values():
            bucket["winEur"] = round(bucket["winEur"], 2)
            bucket["lossEur"] = round(bucket["lossEur"], 2)
        return periods

    def get_tax_summary(self, tickers: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """Verot myyntivoitoista kalenterivuosittain (1.1.–31.12.).

        Veroa ei vähennetä salkusta — tämä on arvio siitä, paljonko pääomatuloveroa
        (30 %) on tulossa maksettavaksi kultakin vuodelta toteutuneista myyntivoitoista
        (tappiot vähentävät saman vuoden voittoja).
        """
        by_year = self.realized_profit_by_year()
        current_year = datetime.now(timezone.utc).year
        previous_year = current_year - 1

        def tax_for(year: int) -> float:
            return max(0.0, by_year.get(year, 0.0)) * TAX_RATE

        unrealized = self.get_unrealized_profit(tickers)
        estimated_tax = unrealized * TAX_RATE

        return {
            "currentYear": current_year,
            "currentYearRealized": round(by_year.get(current_year, 0.0), 2),
            "currentYearTax": round(tax_for(current_year), 2),
            "previousYear": previous_year,
            "previousYearRealized": (
                round(by_year[previous_year], 2) if previous_year in by_year else None
            ),
            "previousYearTax": (
                round(tax_for(previous_year), 2) if previous_year in by_year else None
            ),
            "estimatedTax": round(estimated_tax, 2),
            "unrealizedProfit": round(unrealized, 2),
            "totalTaxOwed": round(self.data.get("totalTaxPaid", 0.0), 2),
        }
