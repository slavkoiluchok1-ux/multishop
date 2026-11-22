"""check_wallets — огляд гаманців і виправлення переповнених балансів.

Читаємо сирим SQL, щоб обійти конвертер Decimal у Django: він падає на
значеннях, які не вміщаються в Decimal(15,6) (понад 9 цілих цифр).
"""
from decimal import Decimal, DecimalException

from django.core.management.base import BaseCommand
from django.db import connection

# Поля-суми в wallet_wallet.
COLUMNS = ("free_balance_mc", "held_mc", "frozen_mc", "deposit_locked_mc", "debt_mc")
# Максимум, який уміщає Decimal(15,6).
MAX_ABS = Decimal("999999999.999999")


def as_text(value) -> str:
    if isinstance(value, float):
        return format(value, "f")
    return str(value)


def problem(value) -> str | None:
    """Причина проблеми або None, якщо значення коректне."""
    text = as_text(value).strip()
    try:
        number = Decimal(text)
    except (DecimalException, ValueError):
        return "не парситься в Decimal"
    if not number.is_finite():
        return "не є скінченним числом"
    if abs(number) > MAX_ABS:
        return f"за модулем більше за {MAX_ABS}"
    return None


class Command(BaseCommand):
    help = "Показує всі гаманці й позначає значення поза Decimal(15,6)."

    def add_arguments(self, parser):
        parser.add_argument("--fix", action="store_true", help="Замінити проблемні значення на '0'.")

    def handle(self, *args, **options):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, user_id, " + ", ".join(COLUMNS) + " FROM wallet_wallet ORDER BY id"
            )
            rows = cursor.fetchall()

        if not rows:
            self.stdout.write("Гаманців не знайдено.")
            return

        self.stdout.write(f"Усього гаманців: {len(rows)}")
        broken = []
        for row in rows:
            wallet_id, user_id = row[0], row[1]
            bad = {}
            self.stdout.write(f"Гаманець #{wallet_id} (користувач #{user_id}):")
            for name, value in zip(COLUMNS, row[2:]):
                reason = problem(value)
                suffix = f"  ← {reason}" if reason else ""
                self.stdout.write(f"  {name} = {as_text(value)}{suffix}")
                if reason:
                    bad[name] = reason
            if bad:
                broken.append((wallet_id, bad))

        if not broken:
            self.stdout.write("Проблемних значень не знайдено.")
            return

        self.stdout.write(f"Гаманців із проблемами: {len(broken)}")

        if not options["fix"]:
            self.stdout.write("Запустіть із --fix, щоб замінити проблемні значення на '0'.")
            return

        answer = input("Замінити проблемні значення на '0'? (y/n): ").strip().lower()
        if answer != "y":
            self.stdout.write("Скасовано.")
            return

        with connection.cursor() as cursor:
            for wallet_id, bad in broken:
                sets = ", ".join(f"{name} = '0'" for name in bad)
                cursor.execute(f"UPDATE wallet_wallet SET {sets} WHERE id = %s", [wallet_id])
        self.stdout.write(f"Оновлено гаманців: {len(broken)}")
