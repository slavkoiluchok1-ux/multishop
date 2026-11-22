import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class Command(BaseCommand):
    help = "Ідемпотентно створює або підвищує головного адміністратора за env-змінними."

    def handle(self, *args, **options):
        username = os.environ.get("BOOTSTRAP_ADMIN_USERNAME", "").strip()
        if not username:
            self.stdout.write("BOOTSTRAP_ADMIN_USERNAME не задано; bootstrap адміністратора пропущено.")
            return

        password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "")
        email = os.environ.get("BOOTSTRAP_ADMIN_EMAIL", "").strip()
        User = get_user_model()

        with transaction.atomic():
            user = User.objects.select_for_update().filter(username__iexact=username).first()
            if user is not None:
                if user.role != User.ROLE_ADMIN_MAIN:
                    user.role = User.ROLE_ADMIN_MAIN
                    user.save(update_fields=["role"])
                    self.stdout.write(self.style.SUCCESS("Користувача підвищено до головного адміна."))
                else:
                    self.stdout.write("Головний адміністратор уже налаштований.")
                return

            if not password:
                raise CommandError(
                    "Для нового bootstrap-користувача потрібна BOOTSTRAP_ADMIN_PASSWORD."
                )
            if not email:
                email = f"{username}@bootstrap.local"

            if User.objects.filter(email__iexact=email).exists():
                raise CommandError("BOOTSTRAP_ADMIN_EMAIL вже використовується.")

            User.objects.create_user(
                username=username,
                email=email,
                password=password,
                role=User.ROLE_ADMIN_MAIN,
            )

        self.stdout.write(self.style.SUCCESS("Головного адміністратора створено."))
