"""Адмінське API підтримки (право «support»): звернення, відповіді, відновлення.

Адмін з правом «support» бачить контакти людини, може відповісти у зверненні
(з вкладенням) і відновити акаунт (згенерувати тимчасовий пароль). Пул звернень
спільний для всіх адмінів із цим правом; лічильник показує ще не закриті.
"""
import mimetypes
import smtplib
from typing import Any, cast

from django.conf import settings
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.mail import EmailMessage
from django.core.mail.message import BadHeaderError
from django.core.paginator import Paginator
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import get_random_string
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.admin_api import _log, is_admin1, require_admin, require_right
from apps.accounts.models import Ban, CustomUser
from apps.core.storage import validate_upload
from apps.core.throttles import UploadRateThrottle
from apps.notifications.services import notify

from .api_views import STATUS_LABELS, _message_payload, _with_ticket_list_stats
from .contacts import ticket_contact_payload
from .models import AccountRecovery, SupportAction, SupportMessage, SupportTicket

PAGE_SIZE = 20
# Символи без 0/O, 1/l/I — щоб тимчасовий пароль легко прочитати й переказати.
PASSWORD_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"
ACTION_LABELS = {
    "reply": "Відповідь людині",
    "ticket_status": "Зміна статусу звернення",
    "category_changed": "Зміна категорії",
    "account_recovery": "Відновлення акаунта",
    "unban_rejected": "Відмова у розбані",
    "appeal_rejected": "Відмова в апеляції",
    "ban_lift": "Зняття бану",
    "ban_reduce": "Скорочення бану",
    "bug_status": "Статус помилки",
    "idea_status": "Статус пропозиції",
    "internal_note": "Внутрішня примітка",
    "template_reply": "Шаблонна відповідь",
    "wallet_issue": "Видача MC",
    "wallet_refund_held": "Повернення утриманих коштів",
    "order_pay_seller": "Виплата продавцю",
    "order_refund_buyer": "Повернення коштів покупцю",
    "product_deactivate": "Деактивація товару",
    "product_restore": "Повернення товару у продаж",
    "appeal_submitted": "Подання апеляції",
}


def _delete_user_sessions(user_id: int) -> None:
    """Видаляє всі активні сесії користувача (викидає з усіх пристроїв).

    Django не зберігає прямий зв'язок session -> user, тому декодуємо
    session_data і перевіряємо _auth_user_id.
    """
    now = timezone.now()
    for session in Session.objects.filter(expire_date__gte=now):
        data = session.get_decoded()
        if str(data.get("_auth_user_id")) == str(user_id):
            session.delete()


def _require_support(actor: CustomUser) -> None:
    require_admin(actor)
    require_right(actor, "support")


def open_support_count(actor: CustomUser) -> int:
    """Кількість відкритих звернень (спільний пул для адмінів із правом)."""
    if not actor.has_right("support"):
        return 0
    tickets = SupportTicket.objects.filter(status="open")
    if not is_admin1(actor):
        tickets = tickets.exclude(request_type="appeal")
    return tickets.count()


def _owner_name(ticket: SupportTicket) -> str:
    if ticket.user is not None:
        return ticket.user.username
    return ticket.contact_email or "Гість"


def _ticket_account(ticket: SupportTicket) -> CustomUser | None:
    if ticket.user_id is not None:
        return ticket.user
    if ticket.target_username:
        return CustomUser.objects.filter(username__iexact=ticket.target_username).first()
    return None


def _ban_access(actor: CustomUser, ban: Ban | None) -> bool:
    if ban is None or not actor.has_right("bans"):
        return False
    if is_admin1(actor):
        return True
    return actor.is_admin_2 and ban.issued_by_role in ("admin_2", "system")


def _email_guest_reply(ticket: SupportTicket, message: SupportMessage) -> None:
    if not ticket.contact_email:
        raise ValidationError({"email": "У зверненні гостя немає email для відповіді."})

    text = message.text or "До цього листа додано вкладення."
    body = (
        f"Вітаємо!\n\nПідтримка MultiShop відповіла на ваше звернення "
        f"«{ticket.subject}» (№{ticket.pk}):\n\n{text}\n\n"
        "Якщо потрібна додаткова допомога, створіть нове звернення та вкажіть номер цього."
    )
    email = EmailMessage(
        subject=f"MultiShop — відповідь на звернення №{ticket.pk}",
        body=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[ticket.contact_email],
    )
    attachment = message.attachment
    if attachment and attachment.name:
        filename = attachment.name.rsplit("/", 1)[-1]
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        with attachment.open("rb") as attachment_file:
            email.attach(filename, attachment_file.read(), content_type)

    if email.send(fail_silently=False) != 1:
        raise ValidationError({"email": "Поштовий сервер не прийняв відповідь для надсилання."})


def _admin_card(ticket: SupportTicket) -> dict[str, Any]:
    if hasattr(ticket, "list_last_message_text"):
        preview = (ticket.list_last_message_text or "")[:140]
        message_count = ticket.list_message_count
    else:
        last = ticket.messages.last()
        preview = last.text[:140] if last else ""
        message_count = ticket.messages.count()
    return {
        "id": ticket.id,
        "subject": ticket.subject,
        "request_type": ticket.request_type,
        "request_type_display": ticket.get_request_type_display(),
        "desired_outcome": ticket.desired_outcome,
        "status": ticket.status,
        "status_display": STATUS_LABELS.get(ticket.status, ticket.status),
        "guest": ticket.user_id is None,
        "from_banned": ticket.from_banned,
        "owner": _owner_name(ticket),
        "contact_email": ticket.contact_email,
        "contact_info": ticket.contact_info,
        "target_username": ticket.target_username,
        "order_reference": ticket.order_reference,
        "contacts": ticket_contact_payload(ticket),
        "assigned_admin": (ticket.assigned_admin.username if ticket.assigned_admin else None),
        "preview": preview,
        "message_count": message_count,
        "created_at": ticket.created_at,
        "appeal": ticket.request_type == "appeal",
        "appeal_ban": (
            {
                "id": ticket.appeal_ban_id,
                "reason": ticket.appeal_ban.reason,
                "starts_at": ticket.appeal_ban.starts_at,
                "ends_at": ticket.appeal_ban.ends_at,
                "issued_by": (
                    ticket.appeal_ban.issued_by.username
                    if ticket.appeal_ban.issued_by
                    else "Система"
                ),
                "issued_by_role_display": ticket.appeal_ban.issued_by_role_display,
            }
            if ticket.appeal_ban_id and ticket.appeal_ban
            else None
        ),
    }


def _admin_detail(ticket: SupportTicket, actor: CustomUser | None = None) -> dict[str, Any]:
    data = _admin_card(ticket)
    data["messages"] = [
        _message_payload(m, for_admin=True, owner_name=_owner_name(ticket))
        for m in ticket.messages.all()
    ]
    data["recoveries"] = [
        {
            "id": row.id,
            "target_user": row.target_user.username,
            "admin": (row.admin.username if row.admin else None),
            "contacted_via": row.contacted_via,
            "note": row.note,
            "created_at": row.created_at,
        }
        for row in ticket.recoveries.select_related("target_user", "admin")
    ]
    account = _ticket_account(ticket)
    active_ban = (
        Ban.objects.active()
        .filter(user=account)
        .select_related("issued_by", "user")
        .first()
        if account is not None
        else None
    )
    data["ban"] = (
        {
            "id": active_ban.pk,
            "reason": active_ban.reason,
            "starts_at": active_ban.starts_at,
            "ends_at": active_ban.ends_at,
            "issued_by": active_ban.issued_by.username if active_ban.issued_by else "Система",
            "issued_by_role": active_ban.issued_by_role,
            "issued_by_role_display": active_ban.issued_by_role_display,
            "can_manage": bool(actor and _ban_access(actor, active_ban)),
        }
        if active_ban is not None
        else None
    )
    data["bug_status"] = ticket.bug_status
    data["bug_status_display"] = ticket.get_bug_status_display() if ticket.bug_status else ""
    data["idea_status"] = ticket.idea_status
    data["idea_status_display"] = ticket.get_idea_status_display() if ticket.idea_status else ""
    data["internal_note"] = ticket.internal_note
    data["actions"] = [
        {
            "id": row.id,
            "action": row.action,
            "action_display": ACTION_LABELS.get(row.action, row.action),
            "reason": row.reason,
            "metadata": row.metadata,
            "actor": row.actor.username if row.actor else "—",
            "created_at": row.created_at,
        }
        for row in ticket.action_history.select_related("actor")
    ]
    return data


class AdminSupportTicketsView(APIView):
    """GET /api/admin/support — список звернень (право «support»)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = cast(CustomUser, request.user)
        _require_support(actor)
        queryset = _with_ticket_list_stats(
            SupportTicket.objects.select_related(
                "user", "assigned_admin", "appeal_ban"
            )
        )
        if not is_admin1(actor):
            queryset = queryset.exclude(request_type="appeal")
        status_filter = request.query_params.get("status")
        if status_filter in ("open", "answered", "closed"):
            queryset = queryset.filter(status=status_filter)
        request_type = request.query_params.get("request_type")
        if request_type in dict(SupportTicket.TYPE_CHOICES):
            queryset = queryset.filter(request_type=request_type)
        paginator = Paginator(queryset, PAGE_SIZE)
        page = paginator.get_page(request.query_params.get("page"))
        return Response(
            {
                "count": paginator.count,
                "page": page.number,
                "pages": paginator.num_pages,
                "results": [_admin_card(t) for t in page.object_list],
            }
        )


class AdminSupportTicketDetailView(APIView):
    """GET/POST /api/admin/support/{id} — перегляд звернення та відповідь."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [UploadRateThrottle]

    @staticmethod
    def _get_ticket(pk: int, actor: CustomUser | None = None) -> SupportTicket:
        ticket = (
            SupportTicket.objects.filter(pk=pk)
            .select_related("user", "assigned_admin", "appeal_ban")
            .prefetch_related("messages__sender")
            .first()
        )
        if ticket is None:
            raise NotFound("Звернення не знайдено.")
        if ticket.request_type == "appeal" and (actor is None or not is_admin1(actor)):
            raise NotFound("Звернення не знайдено.")
        return ticket

    def get(self, request, pk):
        actor = cast(CustomUser, request.user)
        _require_support(actor)
        return Response(_admin_detail(self._get_ticket(pk, actor), actor))

    def post(self, request, pk):
        """Відповідь адміна у зверненні (можна додати вкладення)."""
        actor = cast(CustomUser, request.user)
        _require_support(actor)
        ticket = self._get_ticket(pk, actor)
        text = str(request.data.get("text") or "").strip()
        attachment = request.FILES.get("attachment")
        if not text and attachment is None:
            raise ValidationError({"text": "Напишіть відповідь."})
        if attachment is not None:
            try:
                validate_upload(attachment)
            except DjangoValidationError as exc:
                message = exc.messages[0] if getattr(exc, "messages", None) else str(exc)
                raise ValidationError({"attachment": message}) from exc

        if ticket.user is None and not ticket.contact_email:
            raise ValidationError({"email": "У зверненні гостя немає email для відповіді."})
        try:
            with transaction.atomic():
                message = SupportMessage.objects.create(
                    ticket=ticket,
                    sender=actor,
                    from_side="admin",
                    text=text,
                    attachment=attachment,
                )
                if ticket.user is None:
                    _email_guest_reply(ticket, message)
                ticket.assigned_admin = actor
                if ticket.status != "closed":
                    ticket.status = "answered"
                ticket.save(update_fields=["assigned_admin", "status"])
        except (BadHeaderError, OSError, smtplib.SMTPException, ValueError) as exc:
            raise ValidationError(
                {"email": "Не вдалося надіслати email із відповіддю. Перевірте налаштування пошти."}
            ) from exc

        SupportAction.objects.create(
            ticket=ticket,
            actor=actor,
            action="reply",
            reason=text or "Відповідь із вкладенням.",
        )
        _log(actor, f"Відповів у зверненні підтримки #{ticket.id}")
        return Response(_admin_detail(ticket, actor), status=201)


class AdminSupportStatusView(APIView):
    """POST /api/admin/support/{id}/status — відкрити або закрити звернення."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        actor = cast(CustomUser, request.user)
        _require_support(actor)
        ticket = SupportTicket.objects.filter(pk=pk).first()
        if ticket is None:
            raise NotFound("Звернення не знайдено.")
        if ticket.request_type == "appeal" and not is_admin1(actor):
            raise NotFound("Звернення не знайдено.")
        status_value = str(request.data.get("status") or "")
        if status_value not in ("open", "answered", "closed"):
            raise ValidationError({"status": "Невідомий статус."})
        ticket.status = status_value
        ticket.save(update_fields=["status"])
        SupportAction.objects.create(
            ticket=ticket,
            actor=actor,
            action="ticket_status",
            reason=f'Статус змінено на «{STATUS_LABELS.get(status_value)}».',
        )
        _log(actor, f'Змінив статус звернення #{ticket.id} на «{STATUS_LABELS.get(status_value)}»')
        return Response(_admin_detail(ticket, actor))


class AdminSupportRecoverView(APIView):
    """POST /api/admin/support/{id}/recover-account — тимчасовий пароль акаунта.

    Адмін спершу зв'язується з людиною за контактами, тоді обирає акаунт за
    ніком чи email. Система генерує тимчасовий пароль, користувач змінює його
    при першому вході. Дія записується в журнал.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        actor = cast(CustomUser, request.user)
        _require_support(actor)
        ticket = SupportTicket.objects.filter(pk=pk).select_related("user").first()
        if ticket is None:
            raise NotFound("Звернення не знайдено.")
        if ticket.request_type == "appeal" and not is_admin1(actor):
            raise NotFound("Звернення не знайдено.")
        if ticket.request_type != "account":
            raise ValidationError({"request_type": "Відновлення доступне лише для проблем з акаунтом."})

        if ticket.user is None:
            raise PermissionDenied(
                "Відновлення доступне лише для акаунта, пов'язаного зі зверненням."
            )

        login = str(request.data.get("target_login") or "").strip()
        target_id = request.data.get("target_user_id")
        if target_id and str(target_id) != str(ticket.user_id):
            raise PermissionDenied(
                "Відновлення доступне лише для акаунта, пов'язаного зі зверненням."
            )
        if login:
            target_by_login = CustomUser.find_by_login(login)
            if target_by_login is None or target_by_login.pk != ticket.user_id:
                raise PermissionDenied(
                    "Відновлення доступне лише для акаунта, пов'язаного зі зверненням."
                )
        target = ticket.user
        if target.is_admin_main:
            raise PermissionDenied("Акаунт головного адміна відновити не можна.")
        if target.is_staff_admin and not actor.is_admin_main:
            raise PermissionDenied(
                "Відновлення акаунтів адміністраторів доступне лише головному адміну."
            )

        temp_password = get_random_string(12, PASSWORD_ALPHABET)
        target.set_password(temp_password)
        target.must_change_password = True
        target.save(update_fields=["password", "must_change_password"])

        # Викидаємо користувача з усіх інших сесій: старий пароль більше не діє,
        # а сесії, створені до зміни паролю, також мають бути закриті.
        _delete_user_sessions(target.pk)

        recovery = AccountRecovery.objects.create(
            ticket=ticket,
            target_user=target,
            admin=actor,
            contacted_via=str(request.data.get("contacted_via") or "").strip(),
            note=str(request.data.get("note") or "").strip(),
        )
        SupportAction.objects.create(
            ticket=ticket,
            actor=actor,
            action="account_recovery",
            reason=f"Акаунт {target.username} відновлено.",
            metadata={"contacted_via": recovery.contacted_via},
        )
        ticket.assigned_admin = actor
        ticket.save(update_fields=["assigned_admin"])
        notify(
            target,
            "support",
            "account_recovery",
            "Акаунт відновлено через підтримку. Змініть пароль при вході.",
            {"ticket_id": ticket.id},
            url=f"/pages/support.html?ticket={ticket.id}",
        )
        _log(actor, f"Відновив акаунт {target.username} (звернення #{ticket.id})", target)
        return Response(
            {
                "recovery": {
                    "id": recovery.id,
                    "target_user": target.username,
                    "contacted_via": recovery.contacted_via,
                    "created_at": recovery.created_at,
                },
                "temp_password": temp_password,
            },
            status=201,
        )
