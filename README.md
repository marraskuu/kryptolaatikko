# Krypto Simulaattori — Bitfinex (Django)

Simulaattori hakee reaaliaikaiset kryptokurssit Bitfinexistä ja käyttää teknistä analyysiä (RSI, EMA, momentum) automaattisiin osto- ja myyntipäätöksiin. Backend on **Python/Django**, valmis deployattavaksi **Railway**-palveluun.

## Ominaisuudet

- **1000 € alku** — paper trading, ei oikeaa rahaa
- **Kaikki Bitfinex-kryptot** — AI valitsee 3–4 parasta likvidistä paria
- **30 % vero voitoista** — näkyy UI:ssa ja Excel-viennissä
- **Voitto-myynti** — +3 % → 180 s huipun jälkeen → myy laskussa
- **Excel-vienti** — ostot, myynnit ja veroyhteenveto verottajalle

## Paikallinen kehitys

```powershell
cd C:\Users\chris\crypto-trader-sim
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

Avaa: **http://127.0.0.1:8000**

## Railway-deploy

1. Pushaa repo GitHubiin: **https://github.com/marraskuu/kryptolaatikko**
2. [Railway](https://railway.app) → **New Project** → **Deploy from GitHub repo**
3. Valitse `kryptolaatikko`
4. Aseta ympäristömuuttujat (Variables):

| Muuttuja | Arvo |
|----------|------|
| `SECRET_KEY` | Satunnainen pitkä merkkijono |
| `DEBUG` | `false` |
| `ALLOWED_HOSTS` | `*` (tai Railway-domain) |

Railway asettaa automaattisesti `PORT` ja `RAILWAY_PUBLIC_DOMAIN`.

5. (Valinnainen) Lisää **PostgreSQL**-palvelu — Django käyttää `DATABASE_URL`-muuttujaa automaattisesti.

Deploy käynnistää: `migrate` + `gunicorn`.

## Käyttö

1. Avaa sovellus selaimessa
2. Paina **Käynnistä botti**
3. Kurssit päivittyvät 15 s välein, kauppapäätökset minuutin välein
4. **Lataa Excel** -nappi lataa veroraportin

## Projektirakenne

```
config/           # Django-asetukset
trading/          # Sovellus (API + UI)
  services/       # Bitfinex, portfolio, AI, myyntistrategia
  templates/
  static/
legacy/           # Alkuperäinen vanilla JS -versio (vain referenssi)
```

## GitHub

Repo: **https://github.com/marraskuu/kryptolaatikko**

## Huomio

Tämä on **simulaatio** opetus- ja kokeilutarkoituksiin. Tekninen analyysi ei takaa voittoa oikeilla markkinoilla.
