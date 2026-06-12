"""Luo Django-superuser Railway-deployssa env-muuttujista."""

from __future__ import annotations

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Luo admin-käyttäjä jos DJANGO_SUPERUSER_* on asetettu (Railway)"

    def handle(self, *args, **options) -> None:
        username = os.environ.get("DJANGO_SUPERUSER_USERNAME", "").strip()
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD", "")
        email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "").strip()

        if not username or not password:
            self.stdout.write("ensure_superuser: ohitetaan (DJANGO_SUPERUSER_USERNAME/PASSWORD puuttuu)")
            return

        User = get_user_model()
        if User.objects.filter(username=username).exists():
            reset = os.environ.get("DJANGO_SUPERUSER_RESET", "").lower() in ("1", "true", "yes")
            if not reset:
                self.stdout.write(f"ensure_superuser: käyttäjä {username!r} on jo olemassa")
                return
            user = User.objects.get(username=username)
            user.set_password(password)
            if email:
                user.email = email
            user.is_staff = True
            user.is_superuser = True
            user.save()
            self.stdout.write(self.style.SUCCESS(f"ensure_superuser: salasana päivitetty ({username})"))
            return

        User.objects.create_superuser(
            username=username,
            email=email or f"{username}@localhost",
            password=password,
        )
        self.stdout.write(self.style.SUCCESS(f"ensure_superuser: luotu {username}"))
