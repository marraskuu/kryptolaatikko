"""Sentry — valinnainen virhe- ja suorituskykyseuranta."""

from __future__ import annotations

import logging
import os

import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration
from sentry_sdk.integrations.logging import LoggingIntegration


def init_sentry(*, debug: bool, release: str) -> None:
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return

    traces_rate = float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0") or "0")
    profiles_rate = float(os.environ.get("SENTRY_PROFILES_SAMPLE_RATE", "0") or "0")
    environment = os.environ.get("SENTRY_ENVIRONMENT", "").strip() or (
        "development" if debug else "production"
    )

    sentry_sdk.init(
        dsn=dsn,
        integrations=[
            DjangoIntegration(),
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        ],
        environment=environment,
        release=os.environ.get("SENTRY_RELEASE", "").strip() or release,
        traces_sample_rate=max(0.0, min(1.0, traces_rate)),
        profiles_sample_rate=max(0.0, min(1.0, profiles_rate)),
        send_default_pii=False,
    )
