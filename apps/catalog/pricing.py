"""Форматування цін для API. Гроші — лише Decimal і рядки, ніколи float."""
from decimal import Decimal

# Від 10 000 скорочуємо до 10k / 123k / 2m / 293m
CARD_THRESHOLD = Decimal("10000")


def _trim(value, places=6):
    """Прибирає зайві нулі після коми."""
    text = f"{Decimal(value):.{places}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def format_card(value):
    """Ціна на картці: до 9999 повністю, далі скорочення (не більше 3 цифр)."""
    if value is None:
        return ""
    v = Decimal(value)
    if v < CARD_THRESHOLD:
        return _trim(v, 2)
    if v >= Decimal("1000000000"):
        return f"{int(v // Decimal('1000000000'))}b"
    if v >= Decimal("1000000"):
        return f"{int(v // Decimal('1000000'))}m"
    if v >= Decimal("1000"):
        return f"{int(v // Decimal('1000'))}k"
    return str(int(v))


def format_full(value):
    """Повна ціна: групування тисяч, дробова частина без зайвих нулів."""
    if value is None:
        return ""
    v = Decimal(value)
    int_part = int(v)
    frac = _trim(v - int_part, 6)
    grouped = f"{int_part:,}".replace(",", " ")
    return f"{grouped}.{frac}" if frac != "0" else grouped


def dec(value):
    """Decimal → рядок (для JSON), None лишається None."""
    return None if value is None else str(value)
