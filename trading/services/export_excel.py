from datetime import datetime, timezone
from io import BytesIO

from openpyxl import Workbook
from openpyxl.utils import get_column_letter

from .bitfinex import get_crypto_label
from .portfolio import Portfolio


def _fmt_date(iso: str) -> str:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().strftime("%d.%m.%Y")


def _fmt_pnl(value: float | None) -> str:
    if value is None:
        return ""
    rounded = round(value, 2)
    text = f"{abs(rounded):.2f}".replace(".", ",")
    if rounded > 0:
        return f"+{text}"
    if rounded < 0:
        return f"-{text}"
    return "0,00"


def _unit_price(trade: dict) -> float:
    amount = trade.get("amount") or 0
    if amount > 0 and trade.get("eurTotal") is not None:
        return trade["eurTotal"] / amount
    return float(trade.get("price") or 0)


def _fmt_unit_price(price: float) -> float:
    if price >= 1000:
        return round(price, 2)
    if price >= 1:
        return round(price, 4)
    if price >= 0.01:
        return round(price, 6)
    return round(price, 8)


def _fmt_quantity(amount: float) -> float:
    if amount >= 1:
        return round(amount, 6)
    if amount >= 0.0001:
        return round(amount, 8)
    return round(amount, 12)


def _sell_profit_loss(trade: dict) -> float:
    profit_loss = trade.get("profitLoss")
    if profit_loss is not None:
        return float(profit_loss)
    if trade.get("profit") is not None:
        return float(trade["profit"])
    cost_basis = trade.get("costBasis", 0)
    return float(trade["eurTotal"]) - float(cost_basis)


def _autosize_columns(ws, widths: list[int]) -> None:
    for idx, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width


def build_tax_excel(portfolio_data: dict) -> tuple[BytesIO, str]:
    portfolio = Portfolio(portfolio_data)
    trades = sorted(
        [t for t in portfolio.trades if t["type"] in ("buy", "sell")],
        key=lambda t: t["timestamp"],
    )
    if not trades:
        raise ValueError("Ei kauppoja vientiin.")

    buys = [t for t in trades if t["type"] == "buy"]
    sells = [t for t in trades if t["type"] == "sell"]

    wb = Workbook()
    ws = wb.active
    ws.title = "Kaupat"
    ws.append(
        [
            "Päivämäärä",
            "Krypto",
            "Kappalemäärä",
            "Ostohinta (EUR/kpl)",
            "Myyntihinta (EUR/kpl)",
            "Voitto (+) / Tappio (-) (EUR)",
        ]
    )

    for t in trades:
        label = get_crypto_label(t["symbol"])
        date = _fmt_date(t["timestamp"])
        qty = _fmt_quantity(float(t.get("amount") or 0))
        unit = _fmt_unit_price(_unit_price(t))

        if t["type"] == "buy":
            ws.append([date, label, qty, unit, "", ""])
        else:
            ws.append(
                [
                    date,
                    label,
                    qty,
                    "",
                    unit,
                    _fmt_pnl(_sell_profit_loss(t)),
                ]
            )

    ws_summary = wb.create_sheet("Yhteenveto")
    total_profit = sum(_sell_profit_loss(t) for t in sells)
    ws_summary.append(["Raportin luontipäivä", _fmt_date(datetime.now(timezone.utc).isoformat())])
    ws_summary.append(["Ostoja (kpl)", len(buys)])
    ws_summary.append(["Myyntejä (kpl)", len(sells)])
    ws_summary.append(["Voitot ja tappiot yhteensä (EUR)", _fmt_pnl(total_profit) or "0,00"])
    ws_summary.append(
        ["Maksettu vero 30 % (EUR)", round(portfolio.data["totalTaxPaid"], 2)]
    )
    ws_summary.append(["", ""])
    ws_summary.append(
        [
            "Huomautus",
            "Simulaatio Bitfinex-kursseilla. Hinta = EUR/kpl (eurTotal ÷ kappalemäärä).",
        ]
    )

    _autosize_columns(ws, [14, 12, 18, 18, 18, 28])
    _autosize_columns(ws_summary, [32, 48])

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    filename = f"krypto-veroraportti-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.xlsx"
    return buffer, filename
