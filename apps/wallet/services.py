"""Транзакційна логіка гаманця, застави та відкладених виплат."""
from decimal import Decimal, ROUND_HALF_UP
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.catalog.models import ProductVariant
from apps.core.models import SiteSettings
from apps.orders.models import Order
from apps.complaints.models import Complaint

from .models import FrozenPayout, SellerDeposit, Wallet, WalletTransaction

QUANTUM = Decimal("0.000001")
# Decimal(15,6) уміщає лише 9 цілих цифр — більше ламає читання гаманця з SQLite.
MAX_BALANCE_MC = Decimal("999999999")
BALANCE_LIMIT_MESSAGE = "Баланс не може перевищувати 999 999 999 MC"


def money(value: Decimal) -> Decimal:
    """Округлення до точності гаманця без переходу через float."""
    return Decimal(value).quantize(QUANTUM, rounding=ROUND_HALF_UP)


def balance_over_limit(value: Decimal) -> bool:
    """True, якщо сума виходить за межі Decimal(15,6)."""
    return money(value) > MAX_BALANCE_MC


def commission_for(amount: Decimal) -> Decimal:
    settings = SiteSettings.get_solo()
    return money(amount * settings.commission_percent / Decimal("100"))


def seller_deposit_required(seller_id: int) -> Decimal:
    """Застава: відсоток від ціни, помноженої на залишок активних і прихованих товарів.

    Заблоковані й видалені адміном товари в заставу не входять.
    """
    subtotal = Decimal("0")
    for price, stock in ProductVariant.objects.filter(
        product__seller_id=seller_id,
        product__status__in=["active", "hidden"],
        product__moderation="ok",
    ).values_list("price_mc", "stock"):
        subtotal += price * stock
    percent = SiteSettings.get_solo().deposit_percent
    return money(subtotal * percent / Decimal("100"))


@transaction.atomic
def recalculate_seller_deposit(
    seller, order: Order | None = None, allow_increase: bool = True
) -> Decimal:
    """Змінює заставу атомарно; її повернена частина заморожується на 14 днів.

    allow_increase=False — покупка не залежить від застави продавця: якщо
    потрібна застава більша за заблоковану, нічого не блокуємо і не кидаємо
    помилку, лише оновлюємо required_mc. Повернення застави працює як завжди.
    """
    required = seller_deposit_required(seller.pk)
    deposit, _ = SellerDeposit.objects.select_for_update().get_or_create(
        seller=seller, defaults={"required_mc": Decimal("0")}
    )
    wallet, _ = Wallet.objects.select_for_update().get_or_create(user=seller)
    difference = money(required - wallet.deposit_locked_mc)

    if difference > 0:
        if not allow_increase:
            # Не блокуємо додаткову заставу в потоках замовлення
            deposit.required_mc = required
            deposit.save(update_fields=["required_mc", "updated_at"])
            return required
        if wallet.debt_mc > 0:
            raise ValidationError("Поки є борг, публікувати товари не можна.")
        if wallet.free_balance_mc < difference:
            raise ValidationError(
                f"Недостатньо вільних MC для застави. Потрібно ще {difference} MC."
            )
        wallet.free_balance_mc -= difference
        wallet.deposit_locked_mc += difference
        WalletTransaction.objects.create(
            wallet=wallet,
            amount_mc=-difference,
            kind="deposit_lock",
            order=order,
            note="Блокування застави для активних товарів",
        )
    elif difference < 0:
        returned = -difference
        settings = SiteSettings.get_solo()
        wallet.deposit_locked_mc -= returned
        wallet.frozen_mc += returned
        if balance_over_limit(wallet.frozen_mc):
            raise ValidationError(BALANCE_LIMIT_MESSAGE)
        FrozenPayout.objects.create(
            seller=seller,
            order=order,
            amount_mc=returned,
            release_at=timezone.now() + timedelta(days=settings.payout_freeze_days),
        )
        WalletTransaction.objects.create(
            wallet=wallet,
            amount_mc=returned,
            kind="deposit_return",
            order=order,
            note="Повернення частини застави; заморожено на 14 днів",
        )

    if difference:
        wallet.save(
            update_fields=[
                "free_balance_mc",
                "deposit_locked_mc",
                "frozen_mc",
                "updated_at",
            ]
        )
    deposit.required_mc = required
    deposit.save(update_fields=["required_mc", "updated_at"])
    return required


@transaction.atomic
def forfeit_deposit(user, actor) -> Decimal:
    """Безстроковий бан: застава втрачається остаточно (розбан її не повертає).

    SellerDeposit.required_mc не чіпаємо — це лише розрахункова потреба.
    """
    wallet, _ = Wallet.objects.select_for_update().get_or_create(user=user)
    amount = money(wallet.deposit_locked_mc)
    if amount <= 0:
        return Decimal("0")
    wallet.deposit_locked_mc = Decimal("0")
    wallet.save(update_fields=["deposit_locked_mc", "updated_at"])
    WalletTransaction.objects.create(
        wallet=wallet,
        amount_mc=-amount,
        kind="deposit_forfeit",
        note=f"Втрата застави (безстроковий бан, адмін {getattr(actor, 'username', '—')})",
    )
    return amount


@transaction.atomic
def recover_seller_refund(order: Order, amount: Decimal, note: str) -> tuple[Decimal, str]:
    """Покриває повернення покупцю виплатою, балансом і заставою продавця.

    Непокрита доступними коштами сума стає боргом продавця.
    """
    remaining = money(amount)
    if remaining <= 0:
        return Decimal("0"), "seller_balance"

    wallet, _ = Wallet.objects.select_for_update().get_or_create(user_id=order.seller_id)
    now = timezone.now()
    sources: set[str] = set()

    payouts = list(
        FrozenPayout.objects.select_for_update()
        .filter(seller_id=order.seller_id, order=order, status="frozen")
        .order_by("created_at", "id")
    )
    payout_total = money(sum((row.amount_mc for row in payouts), Decimal("0")))
    frozen_amount = min(remaining, payout_total, wallet.frozen_mc)
    if frozen_amount:
        to_take = frozen_amount
        for payout in payouts:
            if to_take <= 0:
                break
            take = min(payout.amount_mc, to_take)
            payout.amount_mc -= take
            to_take -= take
            if payout.amount_mc == 0:
                payout.status = "used_for_refund"
                payout.resolved_at = now
                payout.save(update_fields=["amount_mc", "status", "resolved_at"])
            else:
                payout.save(update_fields=["amount_mc"])
        wallet.frozen_mc -= frozen_amount
        remaining -= frozen_amount
        sources.add("frozen")
        WalletTransaction.objects.create(
            wallet=wallet,
            amount_mc=-frozen_amount,
            kind="refund",
            order=order,
            note=f"{note} (із замороженої виплати)",
        )

    balance_amount = min(remaining, wallet.free_balance_mc)
    if balance_amount:
        wallet.free_balance_mc -= balance_amount
        remaining -= balance_amount
        sources.add("seller_balance")
        WalletTransaction.objects.create(
            wallet=wallet,
            amount_mc=-balance_amount,
            kind="refund",
            order=order,
            note=f"{note} (із доступного балансу продавця)",
        )

    deposit_amount = min(remaining, wallet.deposit_locked_mc)
    if deposit_amount:
        wallet.deposit_locked_mc -= deposit_amount
        remaining -= deposit_amount
        sources.add("deposit")
        WalletTransaction.objects.create(
            wallet=wallet,
            amount_mc=-deposit_amount,
            kind="deposit_forfeit",
            order=order,
            note=f"{note} (заставу використано для повернення)",
        )

    if remaining:
        new_debt = money(wallet.debt_mc + remaining)
        if balance_over_limit(new_debt):
            raise ValidationError(BALANCE_LIMIT_MESSAGE)
        wallet.debt_mc = new_debt
        sources.add("seller_balance")
        WalletTransaction.objects.create(
            wallet=wallet,
            amount_mc=-remaining,
            kind="debt_add",
            order=order,
            note=f"{note} (непокрита частина повернення — борг продавця)",
        )

    wallet.save(
        update_fields=[
            "frozen_mc",
            "free_balance_mc",
            "deposit_locked_mc",
            "debt_mc",
            "updated_at",
        ]
    )

    if "deposit" in sources:
        source = "deposit"
    elif "seller_balance" in sources:
        source = "seller_balance"
    else:
        source = "frozen"
    return money(remaining), source


@transaction.atomic
def release_due_frozen(user=None) -> int:
    """Ліниво звільняє виплати, для яких минув строк і немає відкритої скарги."""
    now = timezone.now()
    payouts = FrozenPayout.objects.select_for_update().filter(
        status="frozen", release_at__lte=now
    )
    if user is not None:
        payouts = payouts.filter(seller=user)

    released = 0
    for payout in payouts.select_related("seller"):
        if payout.order_id and Complaint.objects.filter(
            order_id=payout.order_id, status__in=("new", "in_review")
        ).exists():
            continue

        wallet, _ = Wallet.objects.select_for_update().get_or_create(user=payout.seller)
        # Не перевищуємо ліміт Decimal(15,6): таку виплату лишаємо замороженою.
        if balance_over_limit(wallet.free_balance_mc + payout.amount_mc):
            continue
        wallet.frozen_mc = F("frozen_mc") - payout.amount_mc
        wallet.free_balance_mc = F("free_balance_mc") + payout.amount_mc
        wallet.save(update_fields=["frozen_mc", "free_balance_mc", "updated_at"])
        payout.status = "released"
        payout.resolved_at = now
        payout.save(update_fields=["status", "resolved_at"])
        WalletTransaction.objects.create(
            wallet=wallet,
            amount_mc=payout.amount_mc,
            kind="frozen_release",
            order=payout.order,
            note="Заморожену виплату звільнено",
        )
        released += 1
    return released
