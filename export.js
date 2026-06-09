import * as XLSX from "https://cdn.sheetjs.com/xlsx-0.20.3/package/xlsx.mjs";

function fmtDate(d) {
  return d.toLocaleDateString("fi-FI");
}

function fmtTime(d) {
  return d.toLocaleTimeString("fi-FI", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function round2(n) {
  return Math.round(n * 100) / 100;
}

/**
 * @param {import('./portfolio.js').Portfolio} portfolio
 * @param {(symbol: string) => string} getCryptoLabel
 */
export function downloadTaxExcel(portfolio, getCryptoLabel) {
  const trades = [...portfolio.trades]
    .filter((t) => t.type === "buy" || t.type === "sell")
    .sort((a, b) => a.timestamp - b.timestamp);

  if (trades.length === 0) {
    alert("Ei kauppoja vientiin. Käynnistä botti ensin.");
    return;
  }

  const buys = trades.filter((t) => t.type === "buy");
  const sells = trades.filter((t) => t.type === "sell");

  const totalBuyEur = buys.reduce((s, t) => s + t.eurTotal, 0);
  const totalBuyFees = buys.reduce((s, t) => s + (t.fee ?? t.eurTotal * 0.001), 0);
  const totalSellEur = sells.reduce((s, t) => s + t.eurTotal, 0);
  const totalProfit = sells.reduce((s, t) => s + (t.profitLoss ?? t.profit ?? 0), 0);
  const totalTax = portfolio.totalTaxPaid;

  const buyRows = buys.map((t) => ({
    Päivämäärä: fmtDate(t.timestamp),
    Aika: fmtTime(t.timestamp),
    Krypto: getCryptoLabel(t.symbol),
    Parit: t.symbol.replace(/^t/, ""),
    Määrä: t.amount,
    "Hinta (EUR)": round2(t.price),
    "Ostosumma (EUR)": round2(t.eurTotal),
    "Palkkio (EUR)": round2(t.fee ?? t.eurTotal * 0.001),
    "Yhteensä (EUR)": round2(t.eurTotal + (t.fee ?? t.eurTotal * 0.001)),
    Perustelu: t.reason,
  }));

  const sellRows = sells.map((t) => {
    const costBasis = t.costBasis ?? t.eurTotal - (t.profitLoss ?? t.profit ?? 0);
    const profitLoss = t.profitLoss ?? t.profit ?? t.eurTotal - costBasis;
    const fee = t.fee ?? t.eurTotal * 0.001;
    const tax = t.tax ?? 0;

    return {
      Päivämäärä: fmtDate(t.timestamp),
      Aika: fmtTime(t.timestamp),
      Krypto: getCryptoLabel(t.symbol),
      Parit: t.symbol.replace(/^t/, ""),
      Määrä: t.amount,
      "Myyntihinta (EUR)": round2(t.price),
      "Myyntisumma (EUR)": round2(t.eurTotal),
      "Hankintamaksu (EUR)": round2(costBasis),
      "Voitto (+) / Tappio (-) (EUR)": round2(profitLoss),
      "Palkkio (EUR)": round2(fee),
      "Vero 30 % (EUR)": round2(tax),
      "Netto käteiseen (EUR)": round2(t.eurTotal - fee - tax),
      Perustelu: t.reason,
    };
  });

  const summaryRows = [
    { Erä: "Raportin luontipäivä", Arvo: fmtDate(new Date()) },
    { Erä: "Alkupääoma (EUR)", Arvo: portfolio.initialCapital },
    { Erä: "Ostoja yhteensä (kpl)", Arvo: buys.length },
    { Erä: "Ostosumma yhteensä (EUR)", Arvo: round2(totalBuyEur) },
    { Erä: "Ostojen palkkiot (EUR)", Arvo: round2(totalBuyFees) },
    { Erä: "Myyntejä yhteensä (kpl)", Arvo: sells.length },
    { Erä: "Myyntisumma yhteensä (EUR)", Arvo: round2(totalSellEur) },
    { Erä: "Voitot ja tappiot yhteensä (EUR)", Arvo: round2(totalProfit) },
    { Erä: "Maksettu vero 30 % (EUR)", Arvo: round2(totalTax) },
    { Erä: "Nettovoitto verojen jälkeen (EUR)", Arvo: round2(totalProfit - totalTax) },
    { Erä: "", Arvo: "" },
    { Erä: "Huomautus", Arvo: "Simulaatio Bitfinex-kursseilla. Tarkista tiedot ennen veroilmoitusta." },
  ];

  const wb = XLSX.utils.book_new();

  const wsSummary = XLSX.utils.json_to_sheet(summaryRows);
  const wsBuys = XLSX.utils.json_to_sheet(buyRows);
  const wsSells = XLSX.utils.json_to_sheet(sellRows);

  wsSummary["!cols"] = [{ wch: 32 }, { wch: 24 }];
  wsBuys["!cols"] = [
    { wch: 12 }, { wch: 10 }, { wch: 10 }, { wch: 14 }, { wch: 14 },
    { wch: 14 }, { wch: 16 }, { wch: 12 }, { wch: 14 }, { wch: 40 },
  ];
  wsSells["!cols"] = [
    { wch: 12 }, { wch: 10 }, { wch: 10 }, { wch: 14 }, { wch: 14 },
    { wch: 16 }, { wch: 18 }, { wch: 16 }, { wch: 22 }, { wch: 12 },
    { wch: 14 }, { wch: 18 }, { wch: 40 },
  ];

  XLSX.utils.book_append_sheet(wb, wsSummary, "Yhteenveto");
  XLSX.utils.book_append_sheet(wb, wsBuys, "Ostot");
  XLSX.utils.book_append_sheet(wb, wsSells, "Myynnit");

  const filename = `krypto-veroraportti-${new Date().toISOString().slice(0, 10)}.xlsx`;
  XLSX.writeFile(wb, filename);
}
