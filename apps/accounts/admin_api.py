"""API адмінських сторінок: кабінет, права, бани, видача MC.

Права (PROJECT_STRUCTURE розділ 4):
- admin_2 (за правами): перегляд профілів і бани, не розбанює.
- admin_1: налаштовує права admin_2, розбанює, не банить головного й інших admin_1.
- admin_main: банить будь-кого, видає рівні 1 і 2, єдиний видає MC.
"""
import calendar
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, cast

from django.db import transaction
from django.db.models import Prefetch, Q
from django.utils.dateparse import parse_date
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalog.models import Product
from apps.notifications.services import notify_admin_rights_changed
from apps.wallet.models import CoinIssue, Wallet, WalletTransaction
from apps.wallet.services import (
    BALANCE_LIMIT_MESSAGE,
    balance_over_limit,
    forfeit_deposit,
    money,
)

from .models import AdminLog, AdminRight, Ban, CustomUser, SellerVerification

# Тривалості бану (PROJECT_STRUCTURE розділ 8)
BAN_DURATIONS = [
    ("1d", "1 день", 1, "days"),
    ("3d", "3 дні", 3, "days"),
    ("7d", "7 днів", 7, "days"),
    ("30d", "30 днів", 30, "days"),
    ("1m", "1 місяць", 1, "months"),
    ("2m", "2 місяці", 2, "months"),
    ("4m", "4 місяці", 4, "months"),
    ("6m", "6 місяців", 6, "months"),
    ("12m", "12 місяців", 12, "months"),
    ("1y", "1 рік", 12, "months"),
    ("5y", "5 років", 60, "months"),
    ("10y", "10 років", 120, "months"),
    ("forever", "Назавжди", None, None),
    ("custom", "Своя дата", None, "custom"),
]

ADMIN_ROLES = (
    CustomUser.ROLE_ADMIN_2,
    CustomUser.ROLE_ADMIN_1,
    CustomUser.ROLE_ADMIN_MAIN,
)

# Ранги ролей: більший ранг — вищий рівень. Видно тих, чий ранг не більший.
ROLE_RANK = {
    CustomUser.ROLE_ADMIN_2: 1,
    CustomUser.ROLE_ADMIN_1: 2,
    CustomUser.ROLE_ADMIN_MAIN: 3,
}


# --- Допоміжні перевірки прав ---

def is_main(user: CustomUser) -> bool:
    return bool(getattr(user, "is_admin_main", False))


def is_admin1(user: CustomUser) -> bool:
    """Адмін 1-го рівня або головний: має всі права й розбанює."""
    return bool(getattr(user, "is_admin_1", False) or getattr(user, "is_admin_main", False))


def require_admin(user: CustomUser) -> None:
    if not getattr(user, "is_staff_admin", False):
        raise PermissionDenied("Доступ лише для адміністраторів.")


def require_right(user: CustomUser, right: str) -> None:
    if not user.has_right(right):
        raise PermissionDenied("Недостатньо прав.")


def rights_list(user: CustomUser) -> list[str]:
    if is_admin1(user):
        return [key for key, _ in AdminRight.RIGHTS]
    prefetched = getattr(user, "prefetched_admin_rights", None)
    if prefetched is not None:
        return [row.right for row in prefetched]
    return list(AdminRight.objects.filter(user=user).values_list("right", flat=True))


def _log(actor: CustomUser, action: str, target: CustomUser | None = None) -> None:
    AdminLog.objects.create(actor=actor, action=action, target=target)


# --- Тривалість бану ---

def _shift_months(value: datetime, months: int) -> datetime:
    """Додає місяці, зберігаючи кінець місця в межах кількості днів."""
    year = value.year + (value.month - 1 + months) // 12
    month = (value.month - 1 + months) % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def ban_ends_at(code: str, custom_date: str | None):
    """Обчислює дату завершення бану. None — назавжди."""
    table = {key: (label, amount, unit) for key, label, amount, unit in BAN_DURATIONS}
    if code not in table:
        raise ValidationError({"duration": "Невідома тривалість бану."})
    now = timezone.now()
    label, amount, unit = table[code]
    if code == "forever":
        return None
    if unit == "custom":
        value = str(custom_date or "").strip()
        if not value:
            raise ValidationError({"ends_at": "Укажіть дату завершення бану."})
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValidationError({"ends_at": "Формат дати: РРРР-ММ-ДД."}) from exc
        end = timezone.make_aware(
            datetime(parsed.year, parsed.month, parsed.day, 23, 59, 59),
            timezone.get_current_timezone(),
        )
        if end <= now:
            raise ValidationError({"ends_at": "Дата має бути в майбутньому."})
        return end
    if unit == "days":
        return now + timedelta(days=int(amount))
    return _shift_months(now, int(amount))


def duration_label(code: str) -> str:
    for key, label, _, _ in BAN_DURATIONS:
        if key == code:
            return label
    return code


# --- Хто кого може банити / змінювати ---

def can_ban(actor: CustomUser, target: CustomUser) -> tuple[bool, str]:
    if actor.pk == target.pk:
        return False, "Не можна забанити себе."
    if getattr(target, "is_admin_main", False):
        return False, "Головного адміна забанити неможливо."
    if is_main(actor):
        return True, ""
    if is_admin1(actor):
        if getattr(target, "is_admin_1", False):
            return False, "Не можна забанити іншого адміна 1-го рівня."
        return True, ""
    # admin_2 з правом «bans» — лише звичайних користувачів
    if getattr(target, "is_staff_admin", False):
        return False, "Адміністратора може забанити лише адмін 1-го рівня або головний."
    return True, ""


def can_grant(actor: CustomUser, target: CustomUser, level: str) -> tuple[bool, str]:
    if actor.pk == target.pk:
        return False, "Не можна змінити власний рівень."
    if getattr(target, "is_admin_main", False):
        return False, "Головного адміна змінити неможливо."
    if is_main(actor):
        return True, ""
    if is_admin1(actor):
        if target.is_admin_1:
            return False, "Змінити іншого адміна 1-го рівня може лише головний."
        if level != CustomUser.ROLE_ADMIN_2 and level != "2":
            return False, "Лише головний адмін видає рівень 1."
        return True, ""
    return False, "Недостатньо прав."


def can_revoke(actor: CustomUser, target: CustomUser) -> tuple[bool, str]:
    if actor.pk == target.pk:
        return False, "Не можна змінити власний рівень."
    if getattr(target, "is_admin_main", False):
        return False, "Головного адміна змінити неможливо."
    if is_main(actor):
        return True, ""
    if is_admin1(actor):
        if getattr(target, "is_admin_1", False):
            return False, "Змінити іншого адміна 1-го рівня може лише головний."
        return True, ""
    return False, "Недостатньо прав."


def can_set_rights(actor: CustomUser, target: CustomUser) -> tuple[bool, str]:
    if not is_admin1(actor):
        return False, "Налаштовувати права може адмін 1-го рівня або головний."
    if target.role != CustomUser.ROLE_ADMIN_2:
        return False, "Права можна налаштовувати лише адміну 2-го рівня."
    return True, ""


# --- Серіалізація ---

def _ban_payload(ban: Ban) -> dict[str, Any]:
    return {
        "id": ban.pk,
        "user_id": ban.user_id,
        "username": ban.user.username,
        "reason": ban.reason,
        "source": ban.source,
        "source_display": ban.get_source_display(),  # pyright: ignore[reportAttributeAccessIssue]
        "issued_by": ban.issued_by.username if ban.issued_by else "—",
        "issued_by_role": ban.issued_by_role,
        "issued_by_role_display": ban.issued_by_role_display,
        "starts_at": ban.starts_at,
        "ends_at": ban.ends_at,
        "forever": ban.ends_at is None,
        "is_active": ban.is_active,
        "lifted_by": ban.lifted_by.username if ban.lifted_by else None,
        "lifted_at": ban.lifted_at,
    }


def _admin_payload(user: CustomUser) -> dict[str, Any]:
    return {
        "id": user.pk,
        "username": user.username,
        "role": user.role,
        "role_display": user.get_role_display(),  # pyright: ignore[reportAttributeAccessIssue]
        "rights": rights_list(user),
        "created_at": user.created_at,
    }


def _log_payload(row: AdminLog, my_rank: int | None = None) -> dict[str, Any]:
    """Запис журналу. Нік target вищого рангу, ніж у глядача, показуємо як «—»."""
    target = row.target
    target_name = target.username if target else None
    if target_name and my_rank is not None:
        if target is not None and ROLE_RANK.get(target.role, 0) > my_rank:
            target_name = None
    return {
        "id": row.pk,
        "actor": row.actor.username if row.actor else "—",
        "action": row.action,
        "target": target_name,
        "created_at": row.created_at,
    }


class AdminCabinetView(APIView):
    """GET/POST /api/admin/cabinet — права поточного адміна й керування рівнями."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = cast(CustomUser, request.user)
        require_admin(actor)
        my_rank = ROLE_RANK.get(actor.role, 0)

        # Список адмінів: бачимо лише тих, чий ранг не більший за наш.
        if is_main(actor):
            admins_qs = CustomUser.objects.filter(role__in=ADMIN_ROLES)
        elif getattr(actor, "is_admin_1", False):
            admins_qs = CustomUser.objects.filter(
                role__in=(CustomUser.ROLE_ADMIN_2, CustomUser.ROLE_ADMIN_1)
            )
        else:  # admin_2 бачить лише себе
            admins_qs = CustomUser.objects.filter(pk=actor.pk)
        admin_query = str(request.query_params.get("admin_q") or "").strip()
        admin_role = str(request.query_params.get("admin_role") or "").strip()
        if admin_query:
            admins_qs = admins_qs.filter(username__icontains=admin_query)
        if admin_role in ADMIN_ROLES:
            admins_qs = admins_qs.filter(role=admin_role)
        for param, lookup in (
            ("admin_from", "created_at__date__gte"),
            ("admin_to", "created_at__date__lte"),
        ):
            raw_date = str(request.query_params.get(param) or "").strip()
            if raw_date:
                parsed_date = parse_date(raw_date)
                if parsed_date is None:
                    raise ValidationError({param: "Вкажіть дату у форматі РРРР-ММ-ДД."})
                admins_qs = admins_qs.filter(**{lookup: parsed_date})
        admins_qs = admins_qs.prefetch_related(
            Prefetch("admin_rights", to_attr="prefetched_admin_rights")
        )
        admins = [_admin_payload(user) for user in admins_qs.order_by("username")]

        # Журнал: лише дії тих, чий ранг не більший за наш.
        log_qs = AdminLog.objects.select_related("actor", "target")
        if is_main(actor):
            pass  # головний бачить усе
        elif getattr(actor, "is_admin_1", False):
            log_qs = log_qs.filter(
                actor__role__in=(CustomUser.ROLE_ADMIN_2, CustomUser.ROLE_ADMIN_1)
            )
        else:  # admin_2 бачить лише власні дії
            log_qs = log_qs.filter(actor=actor)
        log_query = str(request.query_params.get("log_q") or "").strip()
        log_from = str(request.query_params.get("log_from") or "").strip()
        log_to = str(request.query_params.get("log_to") or "").strip()
        if log_query:
            log_qs = log_qs.filter(
                Q(actor__username__icontains=log_query)
                | Q(target__username__icontains=log_query)
                | Q(action__icontains=log_query)
            )
        for raw_date, lookup, param in (
            (log_from, "created_at__date__gte", "log_from"),
            (log_to, "created_at__date__lte", "log_to"),
        ):
            if raw_date:
                parsed_date = parse_date(raw_date)
                if parsed_date is None:
                    raise ValidationError({param: "Вкажіть дату у форматі РРРР-ММ-ДД."})
                log_qs = log_qs.filter(**{lookup: parsed_date})
        log = log_qs.order_by("-created_at", "-pk")[:100]

        return Response(
            {
                "me": _admin_payload(actor),
                "is_admin_main": is_main(actor),
                "is_admin_1": is_admin1(actor),
                "can_ban_admins": is_main(actor),
                "admins": admins,
                "rights_catalog": [{"key": key, "label": label} for key, label in AdminRight.RIGHTS],
                "log": [_log_payload(row, my_rank) for row in log],
            }
        )

    def post(self, request):
        actor = cast(CustomUser, request.user)
        require_admin(actor)
        action = str(request.data.get("action") or "").strip()
        login = str(request.data.get("login") or "").strip()
        target = CustomUser.find_by_login(login) if login else None
        if action not in ("grant_level", "set_rights", "revoke_level"):
            raise ValidationError({"action": "Невідома дія."})
        if target is None:
            raise ValidationError({"login": "Користувача з таким ніком не знайдено."})

        if action == "grant_level":
            level = str(request.data.get("level") or "").strip()
            if level not in ("1", "2"):
                raise ValidationError({"level": "Оберіть рівень 1 або 2."})
            allowed, message = can_grant(actor, target, level)
            if not allowed:
                raise PermissionDenied(message)
            new_role = (
                CustomUser.ROLE_ADMIN_1 if level == "1" else CustomUser.ROLE_ADMIN_2
            )
            # Для рівня 2 у тому ж запиті можна передати права.
            chosen: set[str] = set()
            if new_role == CustomUser.ROLE_ADMIN_2:
                requested = request.data.get("rights") or []
                if not isinstance(requested, list):
                    raise ValidationError({"rights": "Очікується список прав."})
                valid = {key for key, _ in AdminRight.RIGHTS}
                chosen = {str(item) for item in requested} & valid
            old_role = target.role
            role_changed = old_role != new_role
            old_rights = set(
                AdminRight.objects.filter(user=target).values_list("right", flat=True)
            )
            with transaction.atomic():
                target.role = new_role
                target.save(update_fields=["role"])
                if new_role == CustomUser.ROLE_ADMIN_1:
                    # Рівень 1 має всі права — залишкові записи не потрібні.
                    AdminRight.objects.filter(user=target).delete()
                elif role_changed or chosen:
                    # Зміна ролі або передані права: перезаписуємо набір.
                    # Повторна видача рівня 2 без прав нічого не стирає.
                    AdminRight.objects.filter(
                        user=target, right__in=old_rights - chosen
                    ).delete()
                    AdminRight.objects.bulk_create(
                        [
                            AdminRight(user=target, right=right)
                            for right in sorted(chosen - old_rights)
                        ]
                    )
            new_rights = set(
                AdminRight.objects.filter(user=target).values_list("right", flat=True)
            )
            notify_admin_rights_changed(target, old_rights, new_rights)
            _log(actor, f"Видав рівень {level} користувачу {target.username}", target)

        elif action == "set_rights":
            allowed, message = can_set_rights(actor, target)
            if not allowed:
                raise PermissionDenied(message)
            valid = {key for key, _ in AdminRight.RIGHTS}
            requested = request.data.get("rights") or []
            if not isinstance(requested, list):
                raise ValidationError({"rights": "Очікується список прав."})
            chosen = {str(item) for item in requested} & valid
            old_rights = set(
                AdminRight.objects.filter(user=target).values_list("right", flat=True)
            )
            AdminRight.objects.filter(
                user=target, right__in=old_rights - chosen
            ).delete()
            AdminRight.objects.bulk_create(
                [
                    AdminRight(user=target, right=right)
                    for right in sorted(chosen - old_rights)
                ]
            )
            notify_admin_rights_changed(target, old_rights, chosen)
            _log(actor, f"Оновив права користувача {target.username}", target)

        else:  # revoke_level
            allowed, message = can_revoke(actor, target)
            if not allowed:
                raise PermissionDenied(message)
            if target.role == CustomUser.ROLE_USER:
                raise ValidationError({"login": "Користувач не є адміністратором."})
            old_rights = set(
                AdminRight.objects.filter(user=target).values_list("right", flat=True)
            )
            target.role = CustomUser.ROLE_USER
            target.save(update_fields=["role"])
            AdminRight.objects.filter(user=target).delete()
            notify_admin_rights_changed(target, old_rights, set())
            _log(actor, f"Зняв права адміністратора з {target.username}", target)

        return Response({"ok": True})


class AdminUserView(APIView):
    """GET /api/admin/users/{id} — профіль користувача з товарами (право «bans»)."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        actor = cast(CustomUser, request.user)
        require_admin(actor)
        require_right(actor, "bans")
        target = CustomUser.objects.filter(pk=pk).select_related("profile").first()
        if target is None:
            return Response({"error": "Користувача не знайдено."}, status=404)
        wallet, _ = Wallet.objects.get_or_create(user=target)
        products = list(
            Product.objects.filter(seller=target)
            .values("id", "name", "status")
            .order_by("-created_at")[:100]
        )
        bans = [
            _ban_payload(ban)
            for ban in Ban.objects.filter(user=target).select_related("issued_by", "user", "lifted_by")[:50]
        ]
        verification = SellerVerification.objects.filter(user=target).first()
        profile = target.profile
        return Response(
            {
                "id": target.pk,
                "username": target.username,
                "email": target.email,
                "role": target.role,
                "role_display": target.get_role_display(),  # pyright: ignore[reportAttributeAccessIssue]
                "is_banned": target.is_banned(),
                "created_at": target.created_at,
                "contacts": {
                    "phone": profile.phone,
                    "telegram": profile.telegram,
                    "instagram": profile.instagram,
                    "viber": profile.viber,
                    "facebook": profile.facebook,
                    "whatsapp": profile.whatsapp,
                },
                "verification": (
                    {"age": verification.age, "created_at": verification.created_at}
                    if verification
                    else None
                ),
                "balance": {
                    "free_balance_mc": str(wallet.free_balance_mc),
                    "held_mc": str(wallet.held_mc),
                    "frozen_mc": str(wallet.frozen_mc),
                    "deposit_locked_mc": str(wallet.deposit_locked_mc),
                    "debt_mc": str(wallet.debt_mc),
                },
                "products": products,
                "bans": bans,
            }
        )


class AdminUserSearchView(APIView):
    """GET /api/admin/users/search?q=... — пошук користувачів за ніком або email.

    Доступний будь-якому адміну. Email у відповідь не потрапляє.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = cast(CustomUser, request.user)
        require_admin(actor)
        query = str(request.query_params.get("q") or "").strip()
        if len(query) < 2:
            return Response([])
        users = CustomUser.objects.filter(
            Q(username__icontains=query) | Q(email__icontains=query)
        ).order_by("username")[:10]
        return Response([_admin_payload(user) for user in users])


class AdminBansView(APIView):
    """GET/POST /api/admin/bans — список банів і створення бану (право «bans»)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = cast(CustomUser, request.user)
        require_admin(actor)
        require_right(actor, "bans")
        bans = (
            Ban.objects.select_related("user", "issued_by", "lifted_by")
            .order_by("-starts_at")[:200]
        )
        return Response(
            {
                "bans": [_ban_payload(ban) for ban in bans],
                "durations": [{"code": key, "label": label} for key, label, _, _ in BAN_DURATIONS],
                "can_unban": is_admin1(actor),
                "can_custom_date": True,
            }
        )

    def post(self, request):
        actor = cast(CustomUser, request.user)
        require_admin(actor)
        require_right(actor, "bans")
        login = str(request.data.get("login") or "").strip()
        target = CustomUser.find_by_login(login) if login else None
        if target is None:
            raise ValidationError({"login": "Користувача з таким ніком не знайдено."})
        allowed, message = can_ban(actor, target)
        if not allowed:
            raise PermissionDenied(message)
        if target.is_banned():
            raise ValidationError({"login": "Користувач уже забанений."})
        reason = str(request.data.get("reason") or "").strip()
        if len(reason) < 5:
            raise ValidationError({"reason": "Причина обов'язкова (мінімум 5 символів)."})
        code = str(request.data.get("duration") or "").strip()
        ends_at = ban_ends_at(code, request.data.get("ends_at"))
        with transaction.atomic():
            ban = Ban.objects.create(
                user=target,
                issued_by=actor,
                reason=reason,
                source="admin",
                ends_at=ends_at,
            )
            _log(actor, f"Забанив {target.username} ({duration_label(code)})", target)
            if code == "forever":
                # Безстроковий бан: застава втрачається остаточно (в одній транзакції).
                lost = forfeit_deposit(target, actor)
                if lost > 0:
                    _log(actor, f"Застава {lost} MC втрачена остаточно", target)
        return Response({"ban": _ban_payload(ban)}, status=201)


class AdminBanLiftView(APIView):
    """POST /api/admin/bans/{id}/lift — зняти бан (адмін 1-го рівня або головний)."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        actor = cast(CustomUser, request.user)
        require_admin(actor)
        require_right(actor, "bans")
        if not is_admin1(actor):
            raise PermissionDenied("Розбанювати може лише адмін 1-го рівня або головний.")
        ban = (
            Ban.objects.select_related("user")
            .filter(pk=pk, lifted_at__isnull=True)
            .first()
        )
        if ban is None:
            return Response({"error": "Активний бан не знайдено."}, status=404)
        ban.lifted_by = actor
        ban.lifted_at = timezone.now()
        ban.save(update_fields=["lifted_by", "lifted_at"])
        _log(actor, f"Зняв бан з {ban.user.username}", ban.user)
        return Response({"ok": True})


class AdminCoinIssueView(APIView):
    """GET/POST /api/admin/coins/issue — видача MC (лише головний адмін)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = cast(CustomUser, request.user)
        require_admin(actor)
        if not is_main(actor):
            raise PermissionDenied("Видавати MC може лише головний адмін.")
        issues = CoinIssue.objects.select_related("to_user", "issued_by")[:50]
        return Response(
            {
                "issues": [
                    {
                        "id": row.pk,
                        "to_user": row.to_user.username,
                        "amount_mc": str(row.amount_mc),
                        "issued_by": row.issued_by.username,
                        "created_at": row.created_at,
                    }
                    for row in issues
                ]
            }
        )

    def post(self, request):
        actor = cast(CustomUser, request.user)
        require_admin(actor)
        if not is_main(actor):
            raise PermissionDenied("Видавати MC може лише головний адмін.")
        login = str(request.data.get("login") or "").strip()
        target = CustomUser.find_by_login(login) if login else None
        if target is None:
            raise ValidationError({"login": "Користувача з таким ніком не знайдено."})
        raw_amount = request.data.get("amount_mc")
        try:
            amount = money(Decimal(str(raw_amount)))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValidationError({"amount_mc": "Укажіть коректну суму."}) from exc
        if amount <= 0:
            raise ValidationError({"amount_mc": "Сума має бути більшою за нуль."})
        if amount > Decimal("999999999"):
            raise ValidationError({"amount_mc": "Сума завелика."})

        with transaction.atomic():
            wallet, _ = Wallet.objects.select_for_update().get_or_create(user=target)
            if balance_over_limit(wallet.free_balance_mc + amount):
                raise ValidationError(BALANCE_LIMIT_MESSAGE)
            wallet.free_balance_mc += amount
            wallet.save(update_fields=["free_balance_mc", "updated_at"])
            WalletTransaction.objects.create(
                wallet=wallet,
                amount_mc=amount,
                kind="coin_issue",
                note=f"Видача MC адміном {actor.username}",
            )
            CoinIssue.objects.create(issued_by=actor, to_user=target, amount_mc=amount)
        _log(actor, f"Видав {amount} MC користувачу {target.username}", target)
        return Response({"ok": True, "amount_mc": str(amount)}, status=201)
