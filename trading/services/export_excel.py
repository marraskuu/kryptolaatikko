from datetime import datetime, timezone
from io import BytesIO

from openpyxl import Workbook
from openpyxl.utils import get_column_letter

from .bitfinex import get_crypto_label
from .portfolio import Portfolio


def _round2(value: float) -> float:
    return round(value, 2)


def _fmt_date(iso: str) -> str:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().strftime("%d.%m.%Y")


def _fmt_pnl(value: float | None) -> str:
    if value is None:
        return ""
    rounded = _round2(value)
    text = f"{abs(rounded):.2f}".replace(".", ",")
    if rounded > 0:
        return f"+{text}"
    if rounded < 0:
        return f"-{text}"
    return "0,00"


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
    ws_buys = wb.active
    ws_buys.title = "Ostot"
    ws_buys.append(["Päivämäärä", "Krypto", "Ostohinta (EUR)", "Voitto (+) / Tappio (-) (EUR)"])
    for t in buys:
        ws_buys.append(
            [
                _fmt_date(t["timestamp"]),
                get_crypto_label(t["symbol"]),
                _round2(t["price"]),
                "",
            ]
        )

    ws_sells = wb.create_sheet("Myynnit")
    ws_sells.append(["Päivämäärä", "Krypto", "Myyntihinta (EUR)", "Voitto (+) / Tappio (-) (EUR)"])
    for t in sells:
        profit_loss = t.get("profitLoss")
        if profit_loss is None:
            profit_loss = t.get("profit")
        if profit_loss is None:
            cost_basis = t.get("costBasis", 0)
            profit_loss = t["eurTotal"] - cost_basis
        ws_sells.append(
            [
                _fmt_date(t["timestamp"]),
                get_crypto_label(t["symbol"]),
                _round2(t["price"]),
                _fmt_pnl(profit_loss),
            ]
        )

    ws_summary = wb.create_sheet("Yhteenveto")
    total_profit = sum(
        t.get("profitLoss", t.get("profit", 0)) for t in sells
    )
    ws_summary.append(["Raportin luontipäivä", _fmt_date(datetime.now(timezone.utc).isoformat())])
    ws_summary.append(["Ostoja (kpl)", len(buys)])
    ws_summary.append(["Myyntejä (kpl)", len(sells)])
    ws_summary.append(["Voitot ja tappiot yhteensä (EUR)", _fmt_pnl(total_profit) or "0,00"])
    ws_summary.append(["Maksettu vero 30 % (EUR)", _round2(portfolio.data["totalTaxPaid"])])
    ws_summary.append(["", ""])
    ws_summary.append(
        [
            "Huomautus",
            "Simulaatio Bitfinex-kursseilla. Tarkista tiedot ennen veroilmoitusta.",
        ]
    )

    _autosize_columns(ws_buys, [14, 14, 18, 28])
    _autosize_columns(ws_sells, [14, 14, 18, 28])
    _autosize_columns(ws_summary, [32, 48])

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    filename = f"krypto-veroraportti-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.xlsx"
    return buffer, filename
