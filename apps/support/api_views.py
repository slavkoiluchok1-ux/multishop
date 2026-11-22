"""API підтримки: звернення гостя й користувача, повідомлення, вкладення.

Гість подає звернення без входу (вказує email і контакти). Залогінений
користувач бачить список своїх звернень і листується в них. Вкладення
приватні (media/private/support) і віддаються лише власнику звернення.
"""
from mimetypes import guess_type
from typing import Any, cast

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.paginator import Paginator
from django.db.models import Count, OuterRef, Subquery
from django.http import FileResponse, Http404
from django.db import IntegrityError, transaction
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Ban, CustomUser
from apps.core.storage import validate_upload
from apps.core.throttles import SupportRateThrottle, UploadRateThrottle

from .contacts import ticket_contact_payload
from .models import SupportAction, SupportMessage, SupportTicket

PAGE_SIZE = 20
STATUS_LABELS = dict(SupportTicket.STATUS_CHOICES)


def _file_response(field_file):
    filename = field_file.name.rsplit("/", 1)[-1]
    content_type = guess_type(filename)[0] or "application/octet-stream"
    try:
        handle = field_file.open("rb")
    except FileNotFoundError as exc:
        raise Http404("Файл більше недоступний.") from exc
    return FileResponse(handle, as_attachment=True, filename=filename, content_type=content_type)


def _attachment_url(message: SupportMessage) -> str | None:
    attachment = message.attachment
    if not attachment:
        return None
    return f"/api/support/attachments/{message.id}"


def _message_payload(
    message: SupportMessage, *, for_admin: bool, owner_name: str
) -> dict[str, Any]:
    """Повідомлення. Для людини автор — «Ви» або «Підтримка», для адміна — нік."""
    if message.from_side == "admin":
        author = ("Адмін " + message.sender.username) if (for_admin and message.sender) else "Підтримка"
    else:
        author = owner_name if for_admin else "Ви"
    return {
        "id": message.id,
        "side": message.from_side,
        "author": author,
        "text": message.text,
        "attachment": _attachment_url(message),
        "created_at": message.created_at,
    }


def _ticket_card(ticket: SupportTicket) -> dict[str, Any]:
    if hasattr(ticket, "list_last_message_text"):
        preview = (ticket.list_last_message_text or "")[:140]
        message_count = ticket.list_message_count
    else:
        prefetched_messages = getattr(ticket, "_prefetched_objects_cache", {}).get("messages")
        if prefetched_messages is not None:
            prefetched_messages = list(prefetched_messages)
            last = prefetched_messages[-1] if prefetched_messages else None
            preview = last.text[:140] if last else ""
            message_count = len(prefetched_messages)
        else:
            last = ticket.messages.last()
            preview = last.text[:140] if last else ""
            message_count = ticket.messages.count()
    return {
        "id": ticket.id,
        "subject": ticket.subject,
        "request_type": ticket.request_type,
        "request_type_display": ticket.get_request_type_display(),
        "status": ticket.status,
        "status_display": STATUS_LABELS.get(ticket.status, ticket.status),
        "guest": ticket.user_id is None,
        "from_banned": ticket.from_banned,
        "target_username": ticket.target_username,
        "order_reference": ticket.order_reference,
        "contacts": ticket_contact_payload(ticket),
        "preview": preview,
        "message_count": message_count,
        "created_at": ticket.created_at,
    }


def _with_ticket_list_stats(queryset):
    """Include preview and message count in a support-list query."""
    last_message = (
        SupportMessage.objects.filter(ticket_id=OuterRef("pk"))
        .order_by("-created_at", "-pk")
    )
    return (
        queryset.annotate(
            list_message_count=Count("messages"),
            list_last_message_text=Subquery(last_message.values("text")[:1]),
        )
        .order_by("-created_at", "-pk")
    )


def _ticket_detail(ticket: SupportTicket, owner_name: str) -> dict[str, Any]:
    data = _ticket_card(ticket)
    data["desired_outcome"] = ticket.desired_outcome
    data["messages"] = [
        _message_payload(m, for_admin=False, owner_name=owner_name)
        for m in ticket.messages.all()
    ]
    data["can_write"] = ticket.status != "closed"
    data["appeal_of"] = ticket.parent_ticket_id
    data["appeal_rejections"] = [
        {"id": row.id}
        for row in ticket.action_history.filter(
            action="unban_rejected", appeal_ticket__isnull=True
        ).order_by("created_at")
    ]
    return data


def _create_ticket(
    *,
    user: CustomUser | None,
    subject: str,
    request_type: str,
    text: str,
    desired_outcome: str,
    contact_email: str,
    contact_phone: str,
    contact_telegram: str,
    contact_instagram: str,
    contact_viber: str,
    contact_facebook: str,
    contact_whatsapp: str,
    contact_info: str,
    target_username: str,
    order_reference: str,
    attachment,
    appeal_ban_id: str | None = None,
) -> SupportTicket:
    subject = subject.strip()
    text = text.strip()
    request_type = request_type.strip()
    desired_outcome = desired_outcome.strip()
    errors: dict[str, str] = {}
    if not subject:
        errors["subject"] = "Укажіть тему звернення."
    public_request_types = dict(SupportTicket.TYPE_CHOICES)
    public_request_types.pop("appeal", None)
    if request_type not in public_request_types:
        errors["request_type"] = "Оберіть тип звернення."
    if not text:
        errors["text"] = "Опишіть питання."
    if not desired_outcome:
        errors["desired_outcome"] = "Напишіть, якого результату ви очікуєте."
    if len(order_reference) > 32:
        errors["order_reference"] = "Максимум 32 символи."
    if user is None and not contact_email.strip():
        errors["contact_email"] = "Укажіть email для зв'язку."
    target_username = target_username.strip()
    order_reference = order_reference.strip()
    contact_fields = {
        "contact_phone": (contact_phone.strip(), 32),
        "contact_telegram": (contact_telegram.strip(), 64),
        "contact_instagram": (contact_instagram.strip(), 64),
        "contact_viber": (contact_viber.strip(), 32),
        "contact_facebook": (contact_facebook.strip(), 128),
        "contact_whatsapp": (contact_whatsapp.strip(), 32),
    }
    for field, (value, max_length) in contact_fields.items():
        if len(value) > max_length:
            errors[field] = f"Максимум {max_length} символів."
    if request_type == "account":
        if not target_username:
            errors["target_username"] = "Укажіть нік акаунта."
        elif not CustomUser.objects.filter(username__iexact=target_username).exists():
            errors["target_username"] = "Акаунт із таким ніком не знайдено."
    appeal_ban = None
    if appeal_ban_id:
        if user is None or request_type != "unban":
            raise ValidationError({"appeal_ban_id": "Бан можна прикріпити лише до свого звернення про розбан."})
        try:
            parsed_ban_id = int(appeal_ban_id)
        except (TypeError, ValueError) as exc:
            raise ValidationError({"appeal_ban_id": "Некоректний номер бану."}) from exc
        appeal_ban = (
            Ban.objects.active().filter(pk=parsed_ban_id, user=user).first()
        )
        if appeal_ban is None:
            raise ValidationError({"appeal_ban_id": "Активний бан цього акаунта не знайдено."})
    if errors:
        raise ValidationError(errors)

    if user is not None and not contact_email.strip():
        contact_email = user.email

    if attachment is not None:
        try:
            validate_upload(attachment)
        except DjangoValidationError as exc:
            message = exc.messages[0] if getattr(exc, "messages", None) else str(exc)
            raise ValidationError({"attachment": message}) from exc

    ticket = SupportTicket.objects.create(
        user=user,
        contact_email=contact_email.strip(),
        **{field: value for field, (value, _) in contact_fields.items()},
        contact_info=contact_info.strip(),
        target_username=target_username,
        order_reference=order_reference,
        subject=subject,
        request_type=request_type,
        desired_outcome=desired_outcome,
        from_banned=bool(user is not None and user.is_banned()),
        appeal_ban=appeal_ban,
    )
    SupportMessage.objects.create(
        ticket=ticket,
        sender=user,
        from_side="user",
        text=text,
        attachment=attachment,
    )
    if appeal_ban is not None and user is not None:
        SupportAction.objects.create(
            ticket=ticket,
            actor=user,
            action="appeal_submitted",
            reason=text,
            metadata={"ban_id": appeal_ban.pk},
        )
    return ticket


class SupportGuestView(APIView):
    """POST /api/support/guest — звернення без входу (гість або забанений)."""

    permission_classes = [AllowAny]
    throttle_classes = [SupportRateThrottle, UploadRateThrottle]

    def post(self, request):
        user = request.user if getattr(request.user, "is_authenticated", False) else None
        ticket = _create_ticket(
            user=cast(CustomUser | None, user),
            subject=str(request.data.get("subject") or ""),
            request_type=str(request.data.get("request_type") or ""),
            text=str(request.data.get("text") or ""),
            desired_outcome=str(request.data.get("desired_outcome") or ""),
            contact_email=str(request.data.get("contact_email") or ""),
            contact_phone=str(request.data.get("contact_phone") or ""),
            contact_telegram=str(request.data.get("contact_telegram") or ""),
            contact_instagram=str(request.data.get("contact_instagram") or ""),
            contact_viber=str(request.data.get("contact_viber") or ""),
            contact_facebook=str(request.data.get("contact_facebook") or ""),
            contact_whatsapp=str(request.data.get("contact_whatsapp") or ""),
            contact_info=str(request.data.get("contact_info") or ""),
            target_username=str(request.data.get("target_username") or ""),
            order_reference=str(request.data.get("order_reference") or ""),
            attachment=request.FILES.get("attachment"),
        )
        return Response(_ticket_card(ticket), status=201)


class SupportTicketsView(APIView):
    """GET/POST /api/support/tickets — список своїх звернень і створення."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [SupportRateThrottle, UploadRateThrottle]

    def get(self, request):
        user = cast(CustomUser, request.user)
        queryset = _with_ticket_list_stats(SupportTicket.objects.filter(user=user))
        paginator = Paginator(queryset, PAGE_SIZE)
        page = paginator.get_page(request.query_params.get("page"))
        return Response(
            {
                "count": paginator.count,
                "page": page.number,
                "pages": paginator.num_pages,
                "results": [_ticket_card(t) for t in page.object_list],
            }
        )

    def post(self, request):
        user = cast(CustomUser, request.user)
        ticket = _create_ticket(
            user=user,
            subject=str(request.data.get("subject") or ""),
            request_type=str(request.data.get("request_type") or ""),
            text=str(request.data.get("text") or ""),
            desired_outcome=str(request.data.get("desired_outcome") or ""),
            contact_email=str(request.data.get("contact_email") or ""),
            contact_phone=str(request.data.get("contact_phone") or ""),
            contact_telegram=str(request.data.get("contact_telegram") or ""),
            contact_instagram=str(request.data.get("contact_instagram") or ""),
            contact_viber=str(request.data.get("contact_viber") or ""),
            contact_facebook=str(request.data.get("contact_facebook") or ""),
            contact_whatsapp=str(request.data.get("contact_whatsapp") or ""),
            contact_info=str(request.data.get("contact_info") or ""),
            target_username=str(request.data.get("target_username") or ""),
            order_reference=str(request.data.get("order_reference") or ""),
            attachment=request.FILES.get("attachment"),
            appeal_ban_id=str(request.data.get("appeal_ban_id") or "").strip() or None,
        )
        return Response(_ticket_detail(ticket, user.username), status=201)


class SupportTicketDetailView(APIView):
    """GET /api/support/tickets/{id} — своє звернення з повідомленнями."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        user = cast(CustomUser, request.user)
        ticket = (
            SupportTicket.objects.filter(pk=pk, user=user)
            .prefetch_related("messages__sender")
            .first()
        )
        if ticket is None:
            raise Http404("Звернення не знайдено.")
        return Response(_ticket_detail(ticket, user.username))


class SupportTicketMessagesView(APIView):
    """POST /api/support/tickets/{id}/messages — дописати у своє звернення."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [SupportRateThrottle, UploadRateThrottle]

    def post(self, request, pk):
        user = cast(CustomUser, request.user)
        ticket = SupportTicket.objects.filter(pk=pk, user=user).first()
        if ticket is None:
            raise Http404("Звернення не знайдено.")
        if ticket.status == "closed":
            raise ValidationError({"message": "Звернення закрите, написати не можна."})
        text = str(request.data.get("text") or "").strip()
        attachment = request.FILES.get("attachment")
        if not text and attachment is None:
            raise ValidationError({"text": "Напишіть повідомлення."})
        if attachment is not None:
            try:
                validate_upload(attachment)
            except DjangoValidationError as exc:
                message = exc.messages[0] if getattr(exc, "messages", None) else str(exc)
                raise ValidationError({"attachment": message}) from exc
        SupportMessage.objects.create(
            ticket=ticket, sender=user, from_side="user", text=text, attachment=attachment
        )
        # Нове повідомлення людини повертає звернення в статус «Відкрите».
        if ticket.status == "answered":
            ticket.status = "open"
            ticket.save(update_fields=["status"])
        return Response(_ticket_detail(ticket, user.username), status=201)


class SupportTicketAppealView(APIView):
    """POST /api/support/tickets/{id}/appeals — подати апеляцію на відмову."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        user = cast(CustomUser, request.user)
        rejection_id = request.data.get("rejection_id")
        text = str(request.data.get("text") or "").strip()
        if not text:
            raise ValidationError({"text": "Опишіть причину апеляції."})

        with transaction.atomic():
            ticket = (
                SupportTicket.objects.select_for_update()
                .filter(pk=pk, user=user, request_type="unban")
                .first()
            )
            if ticket is None:
                raise NotFound("Звернення не знайдено.")
            rejection = (
                SupportAction.objects.select_for_update()
                .filter(
                    pk=rejection_id,
                    ticket=ticket,
                    action="unban_rejected",
                    appeal_ticket__isnull=True,
                )
                .first()
            )
            if rejection is None:
                raise ValidationError(
                    {"rejection_id": "Цю відмову вже оскаржено або вона недоступна."}
                )
            ban_id = rejection.metadata.get("ban_id")
            from apps.accounts.models import Ban

            if not Ban.objects.active().filter(pk=ban_id, user=user).exists():
                raise ValidationError({"appeal": "Бан уже неактивний — апеляція недоступна."})

            try:
                appeal = SupportTicket.objects.create(
                    user=user,
                    request_type="appeal",
                    subject=f"Апеляція щодо бану — звернення №{ticket.id}",
                    desired_outcome="Переглянути рішення про бан.",
                    parent_ticket=ticket,
                    appeal_ban_id=ban_id,
                    appeal_rejection=rejection,
                    status="open",
                    from_banned=user.is_banned(),
                )
            except IntegrityError as exc:
                raise ValidationError(
                    {"rejection_id": "На цю відмову вже подано апеляцію."}
                ) from exc
            SupportAction.objects.create(
                ticket=appeal,
                actor=user,
                action="appeal_submitted",
                reason=text,
                metadata={"parent_ticket_id": ticket.id, "ban_id": int(ban_id)},
            )
            SupportMessage.objects.create(
                ticket=appeal,
                sender=user,
                from_side="user",
                text=text,
            )

        return Response(_ticket_detail(appeal, user.username), status=201)


class SupportAttachmentView(APIView):
    """GET /api/support/attachments/{id} — приватне вкладення для власника."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        user = cast(CustomUser, request.user)
        message = (
            SupportMessage.objects.select_related("ticket").filter(pk=pk).first()
        )
        if message is None or not message.attachment:
            raise Http404("Вкладення не знайдено.")
        is_owner = message.ticket.user_id == user.id
        is_admin1 = bool(user.is_admin_1 or user.is_admin_main)
        if message.ticket.request_type == "appeal" and not (
            is_admin1 and user.has_right("support")
        ):
            raise PermissionDenied("Апеляції доступні лише адмінам 1-го рівня та головному адміне.")
        if not (is_owner or user.has_right("support")):
            raise PermissionDenied("Вкладення недоступне.")
        return _file_response(message.attachment)
