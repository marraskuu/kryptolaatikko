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
    local = dt.astimezone()
    return local.strftime("%d.%m.%Y")


def _fmt_time(iso: str) -> str:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone()
    return local.strftime("%H:%M:%S")


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

    total_buy_eur = sum(t["eurTotal"] for t in buys)
    total_buy_fees = sum(t.get("fee", t["eurTotal"] * 0.001) for t in buys)
    total_sell_eur = sum(t["eurTotal"] for t in sells)
    total_profit = sum(t.get("profitLoss", t.get("profit", 0)) for t in sells)
    total_tax = portfolio.data["totalTaxPaid"]

    wb = Workbook()

    ws_summary = wb.active
    ws_summary.title = "Yhteenveto"
    summary_rows = [
        ("Raportin luontipäivä", _fmt_date(datetime.now(timezone.utc).isoformat())),
        ("Alkupääoma (EUR)", portfolio.data["initialCapital"]),
        ("Ostoja yhteensä (kpl)", len(buys)),
        ("Ostosumma yhteensä (EUR)", _round2(total_buy_eur)),
        ("Ostojen palkkiot (EUR)", _round2(total_buy_fees)),
        ("Myyntejä yhteensä (kpl)", len(sells)),
        ("Myyntisumma yhteensä (EUR)", _round2(total_sell_eur)),
        ("Voitot ja tappiot yhteensä (EUR)", _round2(total_profit)),
        ("Maksettu vero 30 % (EUR)", _round2(total_tax)),
        ("Nettovoitto verojen jälkeen (EUR)", _round2(total_profit - total_tax)),
        ("", ""),
        (
            "Huomautus",
            "Simulaatio Bitfinex-kursseilla. Tarkista tiedot ennen veroilmoitusta.",
        ),
    ]
    for row in summary_rows:
        ws_summary.append(list(row))

    ws_buys = wb.create_sheet("Ostot")
    buy_headers = [
        "Päivämäärä",
        "Aika",
        "Krypto",
        "Parit",
        "Määrä",
        "Hinta (EUR)",
        "Ostosumma (EUR)",
        "Palkkio (EUR)",
        "Yhteensä (EUR)",
        "Perustelu",
    ]
    ws_buys.append(buy_headers)
    for t in buys:
        fee = t.get("fee", t["eurTotal"] * 0.001)
        ws_buys.append(
            [
                _fmt_date(t["timestamp"]),
                _fmt_time(t["timestamp"]),
                get_crypto_label(t["symbol"]),
                t["symbol"].replace("t", "", 1),
                t["amount"],
                _round2(t["price"]),
                _round2(t["eurTotal"]),
                _round2(fee),
                _round2(t["eurTotal"] + fee),
                t["reason"],
            ]
        )

    ws_sells = wb.create_sheet("Myynnit")
    sell_headers = [
        "Päivämäärä",
        "Aika",
        "Krypto",
        "Parit",
        "Määrä",
        "Myyntihinta (EUR)",
        "Myyntisumma (EUR)",
        "Hankintamaksu (EUR)",
        "Voitto (+) / Tappio (-) (EUR)",
        "Palkkio (EUR)",
        "Vero 30 % (EUR)",
        "Netto käteiseen (EUR)",
        "Perustelu",
    ]
    ws_sells.append(sell_headers)
    for t in sells:
        cost_basis = t.get("costBasis", t["eurTotal"] - t.get("profitLoss", t.get("profit", 0)))
        profit_loss = t.get("profitLoss", t.get("profit", t["eurTotal"] - cost_basis))
        fee = t.get("fee", t["eurTotal"] * 0.001)
        tax = t.get("tax", 0)
        ws_sells.append(
            [
                _fmt_date(t["timestamp"]),
                _fmt_time(t["timestamp"]),
                get_crypto_label(t["symbol"]),
                t["symbol"].replace("t", "", 1),
                t["amount"],
                _round2(t["price"]),
                _round2(t["eurTotal"]),
                _round2(cost_basis),
                _round2(profit_loss),
                _round2(fee),
                _round2(tax),
                _round2(t["eurTotal"] - fee - tax),
                t["reason"],
            ]
        )

    for ws, widths in (
        (ws_summary, [32, 24]),
        (ws_buys, [12, 10, 10, 14, 14, 14, 16, 12, 14, 40]),
        (ws_sells, [12, 10, 10, 14, 14, 16, 18, 16, 22, 12, 14, 18, 40]),
    ):
        for idx, width in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(idx)].width = width

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    filename = f"krypto-veroraportti-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.xlsx"
    return buffer, filename
