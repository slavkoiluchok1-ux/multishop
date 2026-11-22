"""Одноразова синхронізація is_staff/is_superuser із роллю користувача.

Потрібна тому, що масові .update() та дані, створені до появи правила,
обходять CustomUser.save(): прапорці Django Admin виставляються лише
для головного адміна (role == admin_main).
"""
from django.core.management.base import BaseCommand

from apps.accounts.models import CustomUser


class Command(BaseCommand):
    help = "Виставляє is_staff/is_superuser за роллю (лише головний адмін)."

    def handle(self, *args, **options):
        mains = CustomUser.objects.filter(role=CustomUser.ROLE_ADMIN_MAIN)
        others = CustomUser.objects.exclude(role=CustomUser.ROLE_ADMIN_MAIN)
        promoted = mains.update(is_staff=True, is_superuser=True)
        demoted = others.update(is_staff=False, is_superuser=False)
        self.stdout.write(
            self.style.SUCCESS(
                f"Головних адмінів: {promoted}. Решта користувачів: {demoted}."
            )
        )
