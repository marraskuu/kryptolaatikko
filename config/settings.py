import os
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Kasvata joka deployssa — näkyy /api/state/ appBuild-kentässä.
APP_BUILD = "20250612b"

# Paikallinen .env (ei commitoida). Railway: aseta Variables-kohdassa.
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-change-in-production")

DEBUG = os.environ.get("DEBUG", "false").lower() in ("1", "true", "yes")

ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get("ALLOWED_HOSTS", "*").split(",")
    if h.strip()
]

RAILWAY_DOMAIN = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
if RAILWAY_DOMAIN:
    ALLOWED_HOSTS.append(RAILWAY_DOMAIN)

CUSTOM_DOMAIN = os.environ.get("CUSTOM_DOMAIN", "").strip()
if CUSTOM_DOMAIN:
    for host in (CUSTOM_DOMAIN, f"www.{CUSTOM_DOMAIN}"):
        if host not in ALLOWED_HOSTS:
            ALLOWED_HOSTS.append(host)

CSRF_TRUSTED_ORIGINS = [
    o.strip()
    for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",")
    if o.strip()
]
if RAILWAY_DOMAIN:
    CSRF_TRUSTED_ORIGINS.extend([f"https://{RAILWAY_DOMAIN}"])
if CUSTOM_DOMAIN:
    CSRF_TRUSTED_ORIGINS.extend(
        [f"https://{CUSTOM_DOMAIN}", f"https://www.{CUSTOM_DOMAIN}"]
    )

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "trading.apps.TradingConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

from urllib.parse import quote_plus, urlparse

WSGI_APPLICATION = "config.wsgi.application"


def _clean_db_url(url: str) -> str:
    """Siivoa Railwayn muuttuja: trimmaa ja poista mahdolliset lainausmerkit."""
    url = url.strip()
    if len(url) >= 2 and url[0] == url[-1] and url[0] in ("'", '"'):
        url = url[1:-1].strip()
    return url


def _is_valid_db_url(url: str) -> bool:
    url = _clean_db_url(url)
    if not url or url.startswith("${") or "${{" in url:
        return False
    scheme = urlparse(url).scheme.lower()
    base = scheme.split("+", 1)[0]
    return base in ("mysql", "mysql2", "mysqlconnector", "postgres", "postgresql", "postgis", "sqlite")


def _database_url() -> str:
    """Railway MySQL: MYSQL_URL, DATABASE_URL tai MYSQLHOST/MYSQLUSER/..."""
    for key in ("MYSQL_URL", "DATABASE_URL", "MYSQL_PUBLIC_URL"):
        value = _clean_db_url(os.environ.get(key, ""))
        if value and _is_valid_db_url(value):
            return value

    host = os.environ.get("MYSQLHOST") or os.environ.get("MYSQL_HOST")
    if host:
        user = os.environ.get("MYSQLUSER") or os.environ.get("MYSQL_USER", "root")
        password = os.environ.get("MYSQLPASSWORD") or os.environ.get("MYSQL_PASSWORD", "")
        database = os.environ.get("MYSQLDATABASE") or os.environ.get("MYSQL_DATABASE", "railway")
        port = os.environ.get("MYSQLPORT") or os.environ.get("MYSQL_PORT", "3306")
        user_q = quote_plus(user)
        pass_q = quote_plus(password)
        return f"mysql://{user_q}:{pass_q}@{host}:{port}/{database}"

    return f"sqlite:///{BASE_DIR / 'db.sqlite3'}"


_db_url = _database_url()
DATABASES = {
    "default": dj_database_url.config(
        default=_db_url,
        conn_max_age=600,
        conn_health_checks=True,
    )
}

if DATABASES["default"]["ENGINE"] == "django.db.backends.mysql":
    DATABASES["default"].setdefault("OPTIONS", {})
    DATABASES["default"]["OPTIONS"].update(
        {
            "charset": "utf8mb4",
            "init_command": "SET sql_mode='STRICT_TRANS_TABLES'",
        }
    )

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "fi"
TIME_ZONE = "Europe/Helsinki"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

if DEBUG:
    STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }
else:
    STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
        },
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

SESSION_COOKIE_AGE = 60 * 60 * 24 * 7

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
