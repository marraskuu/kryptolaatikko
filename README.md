# Krypto Simulaattori — Bitfinex

Simulaattori hakee reaaliaikaiset kryptokurssit Bitfinexistä ja käyttää teknistä analyysiä (RSI, EMA, momentum) automaattisiin osto- ja myyntipäätöksiin.

## Ominaisuudet

- **1000 € alku** — paper trading, ei oikeaa rahaa
- **Kaikki Bitfinex-kryptot** — hakee kaikki kaupankäyntiparit, AI valitsee 3–4 parasta
- **Automaattinen botti** — ostaa halvalla, myy kalliilla, voitot sijoitetaan uudelleen
- **Bitfinex API** — live-kurssit ja tuntidata analyysiin

## Käynnistys

Sivu käyttää ES moduleja, joten tarvitset paikallisen HTTP-palvelimen.

### PowerShell (suositus Windowsilla)

```powershell
cd C:\Users\chris\crypto-trader-sim
.\start.ps1
```

Avaa selaimessa: **http://localhost:3000**

### Python (vaihtoehto)

```bash
python -m http.server 8080
```

### Node.js (vaihtoehto)

```bash
npx serve .
```

## Käyttö

1. Avaa sivu selaimessa
2. Paina **Käynnistä botti**
3. Botti päivittää kurssit 15 s välein ja tekee kauppapäätöksiä minuutin välein
4. Seuraa salkkua, AI-päätöksiä ja kauppahistoriaa

## GitHubiin siirtäminen

1. Asenna Git: [git-scm.com/download/win](https://git-scm.com/download/win) tai `winget install Git.Git`
2. Luo tyhjä repo GitHubissa: [github.com/new](https://github.com/new) (nimi esim. `crypto-trader-sim`)
3. Aja PowerShell projektikansiossa:

```powershell
cd C:\Users\chris\crypto-trader-sim
.\setup-github.ps1 -Username GITHUB-KAYTTAJANIMESI
```

Skripti tekee commitin ja ohjeistaa pushauksen.

## Huomio

Tämä on **simulaatio** opetus- ja kokeilutarkoituksiin. Tekninen analyysi ei takaa voittoa oikeilla markkinoilla.
