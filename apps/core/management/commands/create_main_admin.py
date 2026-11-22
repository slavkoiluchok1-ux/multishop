import os
from typing import cast

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.models import CustomUser


class Command(BaseCommand):
    help = "Створює головного адміністратора зі змінних середовища, якщо його ще немає."

    def handle(self, *args, **options):
        username = os.environ.get("MAIN_ADMIN_NICK", "").strip()
        email = os.environ.get("MAIN_ADMIN_EMAIL", "").strip()
        password = os.environ.get("MAIN_ADMIN_PASSWORD", "")
        if not username or not email or not password:
            raise CommandError(
                "Потрібно задати MAIN_ADMIN_NICK, MAIN_ADMIN_EMAIL і MAIN_ADMIN_PASSWORD."
            )

        User = cast(type[CustomUser], get_user_model())
        with transaction.atomic():
            existing = User.objects.select_for_update().filter(username__iexact=username).first()
            if existing is not None:
                if (
                    existing.role != User.ROLE_ADMIN_MAIN
                    or existing.email.casefold() != email.casefold()
                ):
                    raise CommandError(
                        "Нік головного адміністратора вже зайнятий іншим обліковим записом."
                    )
                self.stdout.write("Головний адміністратор уже існує; змін не внесено.")
                return

            if User.objects.filter(email__iexact=email).exists():
                raise CommandError("Email головного адміністратора вже використовується.")

            User.objects.create_user(
                username=username,
                email=email,
                password=password,
                role=User.ROLE_ADMIN_MAIN,
            )

        self.stdout.write(self.style.SUCCESS("Головного адміністратора створено."))
