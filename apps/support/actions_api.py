"""Категорійні дії підтримки з окремою перевіркою прав і журналюванням."""
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import smtplib
from typing import Any, cast
from urllib.parse import quote

from django.core.mail.message import BadHeaderError
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.admin_api import _log, is_admin1, require_right
from apps.accounts.models import Ban, CustomUser
from apps.catalog.models import Product
from apps.complaints.models import Complaint
from apps.core.models import SiteSettings
from apps.notifications.services import notify
from apps.orders.models import Order, Refund
from apps.support.admin_api import (
    ACTION_LABELS,
    _admin_detail,
    _ban_access,
    _email_guest_reply,
    _require_support,
)
from apps.support.models import SupportAction, SupportMessage, SupportTicket
from apps.wallet.models import (
    CoinIssue,
    EscrowHold,
    FrozenPayout,
    Wallet,
    WalletTransaction,
)
from apps.wallet.services import BALANCE_LIMIT_MESSAGE, balance_over_limit, money


BUG_TEMPLATES = {
    "received": "Дякуємо за повідомлення. Передали опис проблеми розробникам.",
    "fixed": "Проблему виправлено. Перевірте, будь ласка, ще раз.",
    "not_reproduced": "Не вдалося відтворити проблему. Будь ласка, опишіть точні кроки.",
}
IDEA_TEMPLATES = {
    "thanks": "Дякуємо за пропозицію! Ми її розглянемо.",
    "accepted": "Дякуємо за ідею! Ми прийняли її до реалізації.",
    "declined": "Дякуємо за пропозицію. Наразі ми не плануємо її реалізовувати.",
}
POLICY_SECTIONS = {
    "1": "Загальні положення",
    "2": "Валюта та розрахунки",
    "3": "Гаманець",
    "4": "Комісія",
    "5": "Замороження виплат",
    "6": "Застава продавця",
    "7": "Види товарів",
    "8": "Статуси товару",
    "9": "Замовлення",
    "10": "Повернення коштів",
    "11": "Верифікація продавця",
    "12": "Правила поведінки та скарги",
    "13": "Підтримка",
    "14": "Прикінцеві положення",
}


def _ticket(pk: int, actor: CustomUser) -> SupportTicket:
    ticket = (
        SupportTicket.objects.filter(pk=pk)
        .select_related("user", "parent_ticket", "appeal_ban")
        .first()
    )
    if ticket is None or (ticket.request_type == "appeal" and not is_admin1(actor)):
        raise NotFound("Звернення не знайдено.")
    return ticket


def _require_action_right(actor: CustomUser, right: str) -> None:
    _require_support(actor)
    require_right(actor, right)


def _record(
    ticket: SupportTicket,
    actor: CustomUser,
    action: str,
    reason: str,
    metadata: dict[str, Any] | None = None,
) -> SupportAction:
    ticket.assigned_admin = actor
    ticket.save(update_fields=["assigned_admin"])
    row = SupportAction.objects.create(
        ticket=ticket,
        actor=actor,
        action=action,
        reason=reason,
        metadata=metadata or {},
    )
    _log(actor, f"{ACTION_LABELS.get(action, action)}: звернення #{ticket.id}")
    return row


def _notify_user(ticket: SupportTicket, text: str) -> None:
    if ticket.user_id is not None:
        notify(
            ticket.user,
            "support",
            "support_decision",
            text[:255],
            {"ticket_id": ticket.id},
            url=f"/pages/support.html?ticket={ticket.id}",
        )


def _notify_order_user(user: CustomUser, order: Order, text: str, kind: str) -> None:
    notify(
        user,
        "orders",
        kind,
        text[:255],
        {"order_id": order.id},
        url=f"/pages/orders.html?order={order.id}",
    )


def _send_reply(ticket: SupportTicket, actor: CustomUser, text: str) -> None:
    if ticket.user_id is None and not ticket.contact_email:
        raise ValidationError({"email": "У зверненні гостя немає email для відповіді."})
    try:
        message = SupportMessage.objects.create(
            ticket=ticket,
            sender=actor,
            from_side="admin",
            text=text,
        )
        if ticket.user_id is None:
            _email_guest_reply(ticket, message)
    except (BadHeaderError, OSError, smtplib.SMTPException, ValueError) as exc:
        raise ValidationError(
            {"email": "Не вдалося надіслати відповідь. Перевірте налаштування пошти."}
        ) from exc
    if ticket.status != "closed":
        ticket.status = "answered"
        ticket.save(update_fields=["status"])


def _reason(data: Any, field: str = "reason") -> str:
    value = str(data.get(field) or "").strip()
    if not value:
        raise ValidationError({field: "Це поле обов'язкове."})
    if len(value) > 4000:
        raise ValidationError({field: "Максимум 4000 символів."})
    return value


def _active_ticket_ban(ticket: SupportTicket) -> Ban:
    if ticket.appeal_ban_id:
        ban = Ban.objects.active().filter(pk=ticket.appeal_ban_id).select_related("user").first()
    else:
        user = ticket.user
        if user is None and ticket.target_username:
            user = CustomUser.objects.filter(username__iexact=ticket.target_username).first()
        ban = (
            Ban.objects.active().filter(user=user).select_related("user").first()
            if user is not None
            else None
        )
    if ban is None:
        raise ValidationError({"ban": "Активний бан не знайдено."})
    return ban


def _require_ban_control(actor: CustomUser, ticket: SupportTicket, ban: Ban) -> None:
    if ticket.request_type == "appeal" and not is_admin1(actor):
        raise PermissionDenied("Апеляції розглядають лише адміни 1-го рівня та головний адмін.")
    _require_action_right(actor, "bans")
    if not _ban_access(actor, ban):
        raise PermissionDenied("Ви не можете змінювати бан, виданий адміном вищого рівня.")


class AdminSupportCategoryView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        actor = cast(CustomUser, request.user)
        _require_support(actor)
        ticket = _ticket(pk, actor)
        if ticket.request_type == "appeal":
            raise ValidationError({"request_type": "Категорію апеляції змінити не можна."})
        category = str(request.data.get("request_type") or "").strip()
        choices = dict(SupportTicket.TYPE_CHOICES)
        choices.pop("appeal", None)
        if category not in choices:
            raise ValidationError({"request_type": "Оберіть звичайну категорію звернення."})
        reason = _reason(request.data)
        previous = ticket.get_request_type_display()
        ticket.request_type = category
        ticket.save(update_fields=["request_type"])
        _record(
            ticket,
            actor,
            "category_changed",
            reason,
            {"from": previous, "to": choices[category]},
        )
        return Response(_admin_detail(ticket, actor))


class AdminSupportBugStatusView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        actor = cast(CustomUser, request.user)
        _require_support(actor)
        ticket = _ticket(pk, actor)
        if ticket.request_type != "bug":
            raise ValidationError({"request_type": "Дія доступна лише для помилки."})
        status_value = str(request.data.get("status") or "")
        if status_value not in dict(SupportTicket.BUG_STATUS_CHOICES):
            raise ValidationError({"status": "Оберіть статус помилки."})
        note = str(request.data.get("internal_note") or "").strip()
        if len(note) > 4000:
            raise ValidationError({"internal_note": "Максимум 4000 символів."})
        ticket.bug_status = status_value
        if "internal_note" in request.data:
            ticket.internal_note = note
            ticket.save(update_fields=["bug_status", "internal_note"])
        else:
            ticket.save(update_fields=["bug_status"])
        _record(
            ticket,
            actor,
            "bug_status",
            f"Статус змінено на «{ticket.get_bug_status_display()}».",
            {"status": status_value},
        )
        return Response(_admin_detail(ticket, actor))


class AdminSupportIdeaStatusView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        actor = cast(CustomUser, request.user)
        _require_support(actor)
        ticket = _ticket(pk, actor)
        if ticket.request_type != "suggestion":
            raise ValidationError({"request_type": "Дія доступна лише для пропозиції."})
        status_value = str(request.data.get("status") or "")
        if status_value not in dict(SupportTicket.IDEA_STATUS_CHOICES):
            raise ValidationError({"status": "Оберіть статус пропозиції."})
        note = str(request.data.get("internal_note") or "").strip()
        if len(note) > 4000:
            raise ValidationError({"internal_note": "Максимум 4000 символів."})
        ticket.idea_status = status_value
        if "internal_note" in request.data:
            ticket.internal_note = note
            ticket.save(update_fields=["idea_status", "internal_note"])
        else:
            ticket.save(update_fields=["idea_status"])
        _record(
            ticket,
            actor,
            "idea_status",
            f"Статус змінено на «{ticket.get_idea_status_display()}».",
            {"status": status_value},
        )
        return Response(_admin_detail(ticket, actor))


class AdminSupportInternalNoteView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        actor = cast(CustomUser, request.user)
        _require_support(actor)
        ticket = _ticket(pk, actor)
        if ticket.request_type not in ("bug", "suggestion"):
            raise ValidationError({"request_type": "Внутрішні примітки тут недоступні."})
        note = _reason(request.data, "note")
        ticket.internal_note = note
        ticket.save(update_fields=["internal_note"])
        _record(ticket, actor, "internal_note", note)
        return Response(_admin_detail(ticket, actor))


class AdminSupportTemplateReplyView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        actor = cast(CustomUser, request.user)
        _require_support(actor)
        ticket = _ticket(pk, actor)
        template = str(request.data.get("template") or "")
        if ticket.request_type == "bug":
            text = BUG_TEMPLATES.get(template)
        elif ticket.request_type == "suggestion":
            text = IDEA_TEMPLATES.get(template)
        elif ticket.request_type == "policy":
            section = str(request.data.get("section") or "")
            if section not in POLICY_SECTIONS:
                raise ValidationError({"section": "Оберіть розділ політики."})
            text = (
                f"Дякуємо за звернення. Ознайомтеся з розділом "
                f"«{POLICY_SECTIONS[section]}» у політиці платформи: "
                f"{request.build_absolute_uri(f'/pages/policy.html#policy-section-{section}')}"
            )
        else:
            raise ValidationError({"request_type": "Шаблонна відповідь тут недоступна."})
        if not text:
            raise ValidationError({"template": "Оберіть шаблон відповіді."})
        with transaction.atomic():
            _send_reply(ticket, actor, text)
            _record(ticket, actor, "template_reply", text, {"template": template})
        return Response(_admin_detail(ticket, actor), status=201)


class AdminSupportBanActionView(APIView):
    permission_classes = [IsAuthenticated]
    action_name = ""

    def post(self, request, pk):
        actor = cast(CustomUser, request.user)
        _require_support(actor)
        ticket = _ticket(pk, actor)
        if ticket.request_type not in ("unban", "appeal"):
            raise ValidationError({"request_type": "Дія доступна лише для звернення про бан."})
        ban = _active_ticket_ban(ticket)
        _require_ban_control(actor, ticket, ban)
        reason = _reason(request.data)

        with transaction.atomic():
            if self.action_name == "lift":
                ban.lifted_by = actor
                ban.lifted_at = timezone.now()
                ban.save(update_fields=["lifted_by", "lifted_at"])
                response_text = f"Бан знято. Рішення підтримки: {reason}"
            elif self.action_name == "reduce":
                raw_date = str(request.data.get("ends_at") or "").strip()
                try:
                    date_value = datetime.strptime(raw_date, "%Y-%m-%d").date()
                except ValueError as exc:
                    raise ValidationError({"ends_at": "Укажіть коректну дату у форматі РРРР-ММ-ДД."}) from exc
                end = timezone.make_aware(
                    datetime.combine(date_value + timedelta(days=1), datetime.min.time()),
                    timezone.get_current_timezone(),
                )
                if end <= timezone.now() or (ban.ends_at is not None and end >= ban.ends_at):
                    raise ValidationError({"ends_at": "Нова дата має бути в майбутньому й скорочувати бан."})
                ban.ends_at = end
                ban.save(update_fields=["ends_at"])
                response_text = f"Строк бану скорочено до {date_value:%d.%m.%Y}. Примітка: {reason}"
            else:
                action = "appeal_rejected" if ticket.request_type == "appeal" else "unban_rejected"
                row = _record(
                    ticket,
                    actor,
                    action,
                    reason,
                    {"ban_id": ban.id},
                )
                response_text = f"У розбані відмовлено. Причина: {reason}"
                _send_reply(ticket, actor, response_text)
                if ticket.request_type != "appeal":
                    ticket.status = "answered"
                    ticket.save(update_fields=["status"])
                return Response(_admin_detail(ticket, actor), status=201)

            _record(
                ticket,
                actor,
                f"ban_{self.action_name}",
                reason,
                {"ban_id": ban.id, "ends_at": ban.ends_at.isoformat() if ban.ends_at else None},
            )
            _send_reply(ticket, actor, response_text)
        return Response(_admin_detail(ticket, actor), status=201)


class AdminSupportBanLiftView(AdminSupportBanActionView):
    action_name = "lift"


class AdminSupportBanReduceView(AdminSupportBanActionView):
    action_name = "reduce"


class AdminSupportBanRejectView(AdminSupportBanActionView):
    action_name = "reject"


class AdminSupportWalletView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        actor = cast(CustomUser, request.user)
        _require_action_right(actor, "wallet")
        ticket = _ticket(pk, actor)
        if ticket.request_type != "payment" or ticket.user_id is None:
            raise ValidationError({"user": "Для перегляду потрібен платіжний запит від користувача."})
        try:
            offset = int(request.query_params.get("offset", "0"))
        except (TypeError, ValueError) as exc:
            raise ValidationError({"offset": "Зміщення має бути невід'ємним цілим числом."}) from exc
        if offset < 0:
            raise ValidationError({"offset": "Зміщення має бути невід'ємним цілим числом."})
        raw_order_id = str(request.query_params.get("order_id") or "").strip()
        if raw_order_id and not raw_order_id.isdecimal():
            raise ValidationError({"order_id": "Номер замовлення має бути цілим числом."})
        wallet, _ = Wallet.objects.get_or_create(user=ticket.user)
        totals: dict[int, Decimal] = {}
        for hold in EscrowHold.objects.filter(buyer=ticket.user, status="held"):
            totals[hold.order_id] = totals.get(hold.order_id, Decimal("0")) + hold.amount_mc
        transactions = wallet.transactions.select_related("order").order_by("-created_at", "-id")
        if raw_order_id:
            transactions = transactions.filter(order_id=int(raw_order_id))
        total_transactions = transactions.count()
        transaction_page = transactions[offset : offset + 10]
        return Response(
            {
                "username": ticket.user.username,
                "balance": {
                    "free": f"{wallet.free_balance_mc:.6f}",
                    "held": f"{wallet.held_mc:.6f}",
                    "frozen": f"{wallet.frozen_mc:.6f}",
                    "deposit_locked": f"{wallet.deposit_locked_mc:.6f}",
                    "debt": f"{wallet.debt_mc:.6f}",
                },
                "held_orders": [
                    {"order_id": order_id, "amount_mc": f"{amount:.6f}"}
                    for order_id, amount in totals.items()
                ],
                "transactions": [
                    {
                        "amount_mc": f"{row.amount_mc:.6f}",
                        "kind": row.get_kind_display(),
                        "note": row.note,
                        "order_id": row.order_id,
                        "created_at": row.created_at,
                    }
                    for row in transaction_page
                ],
                "transactions_has_more": total_transactions > offset + len(transaction_page),
                "can_issue": actor.is_admin_main,
            }
        )

class AdminSupportCoinIssueView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        actor = cast(CustomUser, request.user)
        _require_action_right(actor, "wallet")
        if not actor.is_admin_main:
            raise PermissionDenied("Видавати MC може лише головний адмін.")
        ticket = _ticket(pk, actor)
        if ticket.request_type != "payment" or ticket.user_id is None:
            raise ValidationError({"user": "Дія потребує платіжного звернення від користувача."})
        reason = _reason(request.data)
        try:
            amount = money(Decimal(str(request.data.get("amount_mc"))))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValidationError({"amount_mc": "Укажіть коректну суму."}) from exc
        if amount <= 0 or amount > Decimal("999999999"):
            raise ValidationError({"amount_mc": "Сума має бути більшою за нуль і не перевищувати 999 999 999 MC."})
        with transaction.atomic():
            wallet, _ = Wallet.objects.select_for_update().get_or_create(user=ticket.user)
            if balance_over_limit(wallet.free_balance_mc + amount):
                raise ValidationError(BALANCE_LIMIT_MESSAGE)
            wallet.free_balance_mc += amount
            wallet.save(update_fields=["free_balance_mc", "updated_at"])
            WalletTransaction.objects.create(
                wallet=wallet,
                amount_mc=amount,
                kind="coin_issue",
                note=f"{reason} (звернення #{ticket.id})",
            )
            CoinIssue.objects.create(issued_by=actor, to_user=ticket.user, amount_mc=amount)
            _record(
                ticket,
                actor,
                "wallet_issue",
                reason,
                {"amount_mc": f"{amount:.6f}", "user": ticket.user.username},
            )
        return Response({"amount_mc": f"{amount:.6f}"}, status=201)


class AdminSupportHeldRefundView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        actor = cast(CustomUser, request.user)
        _require_action_right(actor, "wallet")
        ticket = _ticket(pk, actor)
        if ticket.request_type != "payment" or ticket.user_id is None:
            raise ValidationError({"user": "Дія потребує платіжного звернення від користувача."})
        reason = _reason(request.data)
        try:
            order_id = int(request.data.get("order_id"))
        except (TypeError, ValueError) as exc:
            raise ValidationError({"order_id": "Укажіть номер замовлення."}) from exc
        with transaction.atomic():
            order = Order.objects.select_for_update().filter(pk=order_id, buyer=ticket.user).first()
            if order is None:
                raise NotFound("Замовлення цього покупця не знайдено.")
            holds = list(
                EscrowHold.objects.select_for_update().filter(order=order, status="held")
            )
            amount = money(sum((hold.amount_mc for hold in holds), Decimal("0")))
            if amount <= 0:
                raise ValidationError({"order_id": "Для цього замовлення немає утриманих коштів."})
            wallet = Wallet.objects.select_for_update().get(user=ticket.user)
            if wallet.held_mc < amount:
                raise ValidationError({"order_id": "Баланс утриманих коштів не відповідає ескроу."})
            if balance_over_limit(wallet.free_balance_mc + amount):
                raise ValidationError(BALANCE_LIMIT_MESSAGE)
            wallet.held_mc -= amount
            wallet.free_balance_mc += amount
            wallet.save(update_fields=["held_mc", "free_balance_mc", "updated_at"])
            now = timezone.now()
            for hold in holds:
                hold.amount_mc = Decimal("0")
                hold.status = "refunded"
                hold.resolved_at = now
                hold.save(update_fields=["amount_mc", "status", "resolved_at"])
            WalletTransaction.objects.create(
                wallet=wallet,
                amount_mc=amount,
                kind="refund",
                order=order,
                note=f"{reason} (звернення #{ticket.id})",
            )
            Refund.objects.create(
                order=order,
                amount_mc=amount,
                fee_returned_mc=Decimal("0"),
                source="escrow",
                approved_by=actor,
            )
            _record(
                ticket,
                actor,
                "wallet_refund_held",
                reason,
                {"order_id": order.id, "amount_mc": f"{amount:.6f}"},
            )
            _notify_user(ticket, f"Повернули {amount:.6f} MC за замовленням №{order.id}.")
        return Response({"amount_mc": f"{amount:.6f}"}, status=201)


class AdminSupportOrderView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        actor = cast(CustomUser, request.user)
        _require_action_right(actor, "orders")
        ticket = _ticket(pk, actor)
        if ticket.request_type != "order":
            raise ValidationError({"request_type": "Пошук доступний лише для замовлення."})
        raw_id = str(request.query_params.get("order_id") or ticket.order_reference).strip()
        try:
            order_id = int(raw_id)
        except ValueError:
            raise ValidationError({"order_id": "Номер замовлення має бути числом."})
        order = (
            Order.objects.filter(pk=order_id)
            .select_related("buyer", "seller")
            .first()
        )
        if order is None:
            raise NotFound("Замовлення не знайдено.")
        held = sum(
            (row.amount_mc for row in order.escrow_holds.filter(status="held")),
            Decimal("0"),
        )
        return Response(
            {
                "id": order.id,
                "status": order.get_status_display(),
                "buyer": order.buyer.username,
                "seller": order.seller.username,
                "amount_mc": f"{order.total_mc:.6f}",
                "held_mc": f"{held:.6f}",
                "can_manage": actor.has_right("orders"),
                "complaints_url": f"/pages/admin/complaints.html?q={quote(str(order.id))}",
            }
        )

class AdminSupportOrderActionView(APIView):
    permission_classes = [IsAuthenticated]
    action_name = ""

    def post(self, request, pk):
        actor = cast(CustomUser, request.user)
        _require_action_right(actor, "orders")
        ticket = _ticket(pk, actor)
        if ticket.request_type != "order":
            raise ValidationError({"request_type": "Дія доступна лише для замовлення."})
        reason = _reason(request.data)
        try:
            order_id = int(request.data.get("order_id"))
        except (TypeError, ValueError) as exc:
            raise ValidationError({"order_id": "Укажіть номер замовлення."}) from exc
        action = self.action_name
        with transaction.atomic():
            order = (
                Order.objects.select_for_update()
                .filter(pk=order_id)
                .select_related("buyer", "seller")
                .first()
            )
            if order is None:
                raise NotFound("Замовлення не знайдено.")
            holds = list(
                EscrowHold.objects.select_for_update().filter(order=order, status="held")
            )
            if not holds:
                raise ValidationError({"order_id": "Для замовлення немає утриманих коштів."})
            buyer_wallet = Wallet.objects.select_for_update().get(user=order.buyer)
            now = timezone.now()
            total = money(sum((hold.amount_mc for hold in holds), Decimal("0")))
            if action == "refund_buyer":
                if balance_over_limit(buyer_wallet.free_balance_mc + total):
                    raise ValidationError(BALANCE_LIMIT_MESSAGE)
                buyer_wallet.held_mc -= total
                if buyer_wallet.held_mc < 0:
                    raise ValidationError({"order_id": "Баланс утриманих коштів не відповідає ескроу."})
                buyer_wallet.free_balance_mc += total
                buyer_wallet.save(update_fields=["held_mc", "free_balance_mc", "updated_at"])
                for hold in holds:
                    hold.amount_mc = Decimal("0")
                    hold.status = "refunded"
                    hold.resolved_at = now
                    hold.save(update_fields=["amount_mc", "status", "resolved_at"])
                WalletTransaction.objects.create(
                    wallet=buyer_wallet,
                    amount_mc=total,
                    kind="refund",
                    order=order,
                    note=f"{reason} (звернення #{ticket.id})",
                )
                Refund.objects.create(
                    order=order,
                    amount_mc=total,
                    fee_returned_mc=Decimal("0"),
                    source="escrow",
                    approved_by=actor,
                )
                _notify_order_user(
                    order.buyer,
                    order,
                    f"Повернули {total:.6f} MC за замовленням №{order.id}.",
                    "order_refund",
                )
            else:
                seller_wallet, _ = Wallet.objects.select_for_update().get_or_create(
                    user=order.seller
                )
                payout_total = money(
                    sum((hold.amount_mc - hold.fee_mc for hold in holds), Decimal("0"))
                )
                if payout_total < 0 or balance_over_limit(seller_wallet.frozen_mc + payout_total):
                    raise ValidationError(BALANCE_LIMIT_MESSAGE)
                if buyer_wallet.held_mc < total:
                    raise ValidationError({"order_id": "Баланс утриманих коштів не відповідає ескроу."})
                freeze_days = SiteSettings.get_solo().payout_freeze_days
                seller_wallet.frozen_mc += payout_total
                for hold in holds:
                    net = money(hold.amount_mc - hold.fee_mc)
                    buyer_wallet.held_mc -= hold.amount_mc
                    hold.status = "released"
                    hold.resolved_at = now
                    hold.save(update_fields=["status", "resolved_at"])
                    WalletTransaction.objects.create(
                        wallet=buyer_wallet,
                        amount_mc=-hold.amount_mc,
                        kind="escrow_to_frozen",
                        order=order,
                        note=f"{reason} (звернення #{ticket.id})",
                    )
                    WalletTransaction.objects.create(
                        wallet=seller_wallet,
                        amount_mc=net,
                        kind="escrow_to_frozen",
                        order=order,
                        note=f"Виплата заморожена на {freeze_days} днів за зверненням #{ticket.id}",
                    )
                    FrozenPayout.objects.create(
                        seller=order.seller,
                        order=order,
                        amount_mc=net,
                        release_at=now + timedelta(days=freeze_days),
                    )
                buyer_wallet.save(update_fields=["held_mc", "updated_at"])
                seller_wallet.save(update_fields=["frozen_mc", "updated_at"])
                _notify_order_user(
                    order.seller,
                    order,
                    f"Кошти за замовленням №{order.id} передано продавцю.",
                    "order_payout",
                )
            _record(
                ticket,
                actor,
                f"order_{action}",
                reason,
                {"order_id": order.id, "amount_mc": f"{total:.6f}"},
            )
        return Response({"amount_mc": f"{total:.6f}"}, status=201)


class AdminSupportOrderPayView(AdminSupportOrderActionView):
    action_name = "pay_seller"


class AdminSupportOrderRefundView(AdminSupportOrderActionView):
    action_name = "refund_buyer"


class AdminSupportProductView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        actor = cast(CustomUser, request.user)
        _require_action_right(actor, "products")
        ticket = _ticket(pk, actor)
        if ticket.request_type != "product":
            raise ValidationError({"request_type": "Пошук доступний лише для товару або продавця."})
        query = str(request.query_params.get("q") or "").strip()
        products = Product.objects.select_related("seller")
        if query:
            if query.isdecimal():
                products = products.filter(pk=int(query))
            else:
                products = products.filter(name__icontains=query)
        results = [
            {
                "id": product.id,
                "name": product.name,
                "status": product.get_status_display(),
                "moderation": product.get_moderation_display(),
                "seller": product.seller.username,
                "seller_id": product.seller_id,
                "complaints_count": Complaint.objects.filter(product=product).count(),
                "seller_profile_url": f"/pages/admin/products.html?q={quote(product.seller.username)}",
                "complaints_url": f"/pages/admin/complaints.html?q={quote(product.name)}",
            }
            for product in products.order_by("-created_at")[:10]
        ]
        sellers = []
        if query:
            sellers = [
                {"id": user.id, "username": user.username,
                 "profile_url": f"/pages/admin/products.html?q={quote(user.username)}",
                 "complaints_url": f"/pages/admin/complaints.html?q={quote(user.username)}"}
                for user in CustomUser.objects.filter(username__icontains=query)[:10]
            ]
        return Response({"products": results, "sellers": sellers})

class AdminSupportProductActionView(APIView):
    permission_classes = [IsAuthenticated]
    action_name = ""

    def post(self, request, pk):
        actor = cast(CustomUser, request.user)
        _require_action_right(actor, "products")
        ticket = _ticket(pk, actor)
        if ticket.request_type != "product":
            raise ValidationError({"request_type": "Дія доступна лише для товару або продавця."})
        try:
            product_id = int(request.data.get("product_id"))
        except (TypeError, ValueError) as exc:
            raise ValidationError({"product_id": "Укажіть ID товару."}) from exc
        action = self.action_name
        reason = _reason(request.data)
        product = Product.objects.filter(pk=product_id).first()
        if product is None:
            raise NotFound("Товар не знайдено.")
        if action == "restore" and product.moderation != "ok":
            raise ValidationError({"product_id": "Спершу потрібно зняти обмеження модерації товару."})
        product.status = "inactive" if action == "deactivate" else "active"
        product.moderated_by = actor
        product.moderated_at = timezone.now()
        product.moderation_reason = reason
        product.save(
            update_fields=[
                "status", "moderated_by", "moderated_at", "moderation_reason", "updated_at"
            ]
        )
        _record(
            ticket,
            actor,
            f"product_{action}",
            reason,
            {"product_id": product.id, "name": product.name},
        )
        return Response({"ok": True, "status": product.get_status_display()}, status=201)


class AdminSupportProductDeactivateView(AdminSupportProductActionView):
    action_name = "deactivate"


class AdminSupportProductRestoreView(AdminSupportProductActionView):
    action_name = "restore"
