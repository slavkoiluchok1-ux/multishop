"""Адмінське API скарг: видимість за правами, розгляд, наслідки, верифікація.

Право «complaints» — базовий доступ до скарг; для окремих наслідків потрібні
права «orders», «products», «bans», а «restrict_complaints» і «revoke_rights»
доступні лише адміну 1-го рівня та головному. Адмін не розглядає скаргу, де він
сам є стороною чи ціллю.
"""
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, cast

from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.admin_api import (
    _log,
    ban_ends_at,
    can_ban,
    duration_label,
    is_admin1,
    is_main,
    require_admin,
    require_right,
    rights_list,
)
from apps.accounts.models import Ban, CustomUser, SellerVerification, UserProfile
from apps.catalog.admin_api import _apply_moderation
from apps.core.models import SiteSettings
from apps.notifications.services import notify
from apps.orders.models import Order, OrderStatusHistory, Refund
from apps.wallet.models import EscrowHold, FrozenPayout, Wallet, WalletTransaction
from apps.wallet.services import (
    BALANCE_LIMIT_MESSAGE,
    balance_over_limit,
    forfeit_deposit,
    money,
    recover_seller_refund,
)

from .api_views import OPEN_STATUSES, _complaint_payload, _file_response
from .models import Complaint, ComplaintComment, ComplaintType
from .services import lift_complaint_block, register_false_complaint

PAGE_SIZE = 20
# Які права дають доступ до розділу скарг загалом
COMPLAINT_RIGHTS = ("complaints", "products", "bans")
ORDERS_RIGHTS = ("complaints",)
PRODUCTS_RIGHTS = ("products", "complaints")
PREVIEW_LEN = 20

CONSEQUENCE_CODES = {code for code, _ in Complaint.CONSEQUENCE_CHOICES}
RESOLVE_STATUSES = ("upheld", "rejected", "false")


# --- Видимість ---

def _visible_types(actor: CustomUser) -> list[ComplaintType]:
    """Типи скарг, які адмін має право обробляти (правила PROJECT_VISION 18)."""
    types = list(ComplaintType.objects.filter(is_active=True))
    if is_admin1(actor):
        return types
    rights = set(rights_list(actor))
    result = []
    for node in types:
        handlers = set(node.handler_rights or [])
        if handlers & rights:
            result.append(node)
        elif "complaints" in rights and not handlers <= {"admin_1", "admin_main"}:
            result.append(node)
    return result


def _require_complaints_access(actor: CustomUser) -> None:
    if not any(actor.has_right(right) for right in COMPLAINT_RIGHTS):
        raise PermissionDenied("Недостатньо прав для роботи зі скаргами.")


def _handleable(actor: CustomUser, complaint: Complaint) -> None:
    """Перевіряє, що скарга доступна цьому адміну (тип, сторони, ціль)."""
    if complaint.author_id == actor.id or complaint.target_user_id == actor.id:
        raise PermissionDenied("Ви є стороною цієї скарги.")
    type_ids = {node.id for node in _visible_types(actor)}
    if complaint.complaint_type_id not in type_ids:
        raise PermissionDenied("Ця скарга недоступна за вашими правами.")
    target = complaint.target_user
    if target is not None and getattr(target, "is_staff_admin", False):
        if getattr(target, "is_admin_main", False):
            raise PermissionDenied("Скарги на головного адміна не розглядаються.")
        if getattr(target, "is_admin_1", False) and not is_main(actor):
            raise PermissionDenied("Скарги на адміна 1-го рівня розглядає лише головний.")
        if getattr(target, "is_admin_2", False) and not is_admin1(actor):
            raise PermissionDenied(
                "Скарги на адміна 2-го рівня розглядає адмін 1-го рівня або головний."
            )


def _get_complaint(actor: CustomUser, pk: int) -> Complaint:
    complaint = (
        Complaint.objects.filter(pk=pk)
        .select_related("complaint_type", "author", "target_user", "product", "order")
        .prefetch_related("evidence", "comments__author")
        .first()
    )
    if complaint is None:
        raise NotFound("Скаргу не знайдено.")
    _handleable(actor, complaint)
    return complaint


def _reviewable_query(actor: CustomUser):
    """Базовий набір скарг, які цей адмін може розглядати."""
    type_ids = [node.id for node in _visible_types(actor)]
    queryset = Complaint.objects.filter(complaint_type_id__in=type_ids)
    queryset = queryset.exclude(author=actor).exclude(target_user=actor)
    # Не показуємо скарги на адмінів, яких цей адмін не уповноважений розглядати
    queryset = queryset.exclude(target_user__role=CustomUser.ROLE_ADMIN_MAIN)
    if not is_main(actor):
        queryset = queryset.exclude(target_user__role=CustomUser.ROLE_ADMIN_1)
    if not is_admin1(actor):
        queryset = queryset.exclude(target_user__role=CustomUser.ROLE_ADMIN_2)
    return queryset


def open_complaints_count(actor: CustomUser) -> int:
    """Кількість нерозв'язаних скарг, доступних адміну (для лічильника)."""
    return _reviewable_query(actor).filter(status__in=OPEN_STATUSES).count()


# --- Наслідки ---

def _require_any_right(actor: CustomUser, rights: tuple[str, ...], message: str) -> None:
    if not any(actor.has_right(right) for right in rights):
        raise PermissionDenied(message)


def _refund_buyer(actor: CustomUser, complaint: Complaint, amount: Decimal | None) -> Decimal:
    """Повертає покупцю кошти з ескроу або за рахунок продавця."""
    order = complaint.order
    if order is None:
        raise ValidationError({"consequences": "Повернення потребує замовлення."})
    holds = list(EscrowHold.objects.select_for_update().filter(order=order, status="held"))
    refunded_before = Refund.objects.filter(order=order).aggregate(total=Sum("amount_mc"))["total"]
    refundable = money(order.total_mc - (refunded_before or Decimal("0")))
    total = refundable if amount is None else money(amount)
    if total <= 0:
        raise ValidationError({"refund_amount_mc": "Немає коштів для додаткового повернення."})
    if total > refundable:
        raise ValidationError({"refund_amount_mc": "Сума перевищує неповернений залишок замовлення."})

    buyer_wallet = Wallet.objects.select_for_update().get(user=order.buyer)
    escrow_available = money(sum((hold.amount_mc for hold in holds), Decimal("0")))
    from_escrow = min(total, escrow_available)
    if buyer_wallet.held_mc < from_escrow:
        raise ValidationError({"refund_amount_mc": "Баланс утриманих коштів не відповідає ескроу."})
    buyer_wallet.held_mc -= from_escrow
    seller_refund = money(total - from_escrow)
    debt_created = Decimal("0")
    source = "escrow"
    if seller_refund:
        debt_created, source = recover_seller_refund(
            order, seller_refund, f"Повернення за скаргою №{complaint.id}"
        )
    buyer_wallet.free_balance_mc += total
    if balance_over_limit(buyer_wallet.free_balance_mc):
        raise ValidationError(BALANCE_LIMIT_MESSAGE)
    buyer_wallet.save(update_fields=["held_mc", "free_balance_mc", "updated_at"])
    WalletTransaction.objects.create(
        wallet=buyer_wallet,
        amount_mc=total,
        kind="refund",
        order=order,
        note=f"Повернення коштів за скаргою №{complaint.id}",
    )
    now = timezone.now()
    remaining = from_escrow
    for hold in holds:
        if remaining <= 0:
            break
        take = min(hold.amount_mc, remaining)
        hold.amount_mc -= take
        remaining -= take
        if hold.amount_mc <= 0:
            hold.status = "refunded"
            hold.resolved_at = now
        hold.save(update_fields=["amount_mc", "status", "resolved_at"])
    Refund.objects.create(
        order=order,
        amount_mc=total,
        fee_returned_mc=Decimal("0"),
        debt_mc=debt_created,
        source=source,
    )
    _notify(
        order.buyer,
        f"За скаргою №{complaint.id} повернено {total} MC.",
        complaint,
        kind="complaint_refund",
    )
    return total


def _pay_seller(actor: CustomUser, complaint: Complaint) -> Decimal:
    """Виплачує продавцю утримані кошти із замороженням на 14 днів."""
    order = complaint.order
    if order is None:
        raise ValidationError({"consequences": "Виплата потребує замовлення."})
    holds = list(EscrowHold.objects.select_for_update().filter(order=order, status="held"))
    if not holds:
        raise ValidationError({"consequences": "Для замовлення немає утриманих коштів."})
    now = timezone.now()
    freeze_days = SiteSettings.get_solo().payout_freeze_days
    buyer_wallet = Wallet.objects.select_for_update().get(user=order.buyer)
    seller_wallet, _ = Wallet.objects.select_for_update().get_or_create(user=order.seller)
    paid = Decimal("0")
    for hold in holds:
        net = money(hold.amount_mc - hold.fee_mc)
        buyer_wallet.held_mc -= hold.amount_mc
        seller_wallet.frozen_mc += net
        held = hold.amount_mc
        hold.status = "released"
        hold.resolved_at = now
        hold.save(update_fields=["status", "resolved_at"])
        WalletTransaction.objects.create(
            wallet=buyer_wallet,
            amount_mc=-held,
            kind="escrow_to_frozen",
            order=order,
            note=f"Виплата продавцю за рішенням скарги №{complaint.id}",
        )
        WalletTransaction.objects.create(
            wallet=seller_wallet,
            amount_mc=net,
            kind="escrow_to_frozen",
            order=order,
            note="Виплата заморожена на 14 днів",
        )
        FrozenPayout.objects.create(
            seller=order.seller,
            order=order,
            amount_mc=net,
            release_at=now + timedelta(days=freeze_days),
        )
        paid += net
    buyer_wallet.save(update_fields=["held_mc", "updated_at"])
    seller_wallet.save(update_fields=["frozen_mc", "updated_at"])
    _notify(
        order.seller,
        f"За скаргою №{complaint.id} вам виплачено {paid} MC.",
        complaint,
        kind="complaint_payout",
    )
    return paid


def _cancel_order(actor: CustomUser, complaint: Complaint) -> None:
    order = complaint.order
    if order is None:
        raise ValidationError({"consequences": "Скасування потребує замовлення."})
    old_status = order.status
    order.status = "cancelled"
    order.save(update_fields=["status"])
    OrderStatusHistory.objects.create(
        order=order,
        old_status=old_status,
        new_status="cancelled",
        changed_by=actor,
        note=f"Скасовано за рішенням скарги №{complaint.id}",
    )


def _apply_ban(actor: CustomUser, complaint: Complaint, data: Any) -> None:
    target = complaint.target_user
    if target is None:
        raise ValidationError({"consequences": "Бан потребує користувача."})
    allowed, message = can_ban(actor, target)
    if not allowed:
        raise PermissionDenied(message)
    if target.is_banned():
        raise ValidationError({"consequences": "Користувач уже забанений."})
    code = str(data.get("ban_duration") or "1d").strip()
    ends_at = ban_ends_at(code, data.get("ban_ends_at"))
    Ban.objects.create(
        user=target,
        issued_by=actor,
        reason=str(data.get("ban_reason") or "").strip()
        or f"За рішенням скарги №{complaint.id}",
        source="complaint",
        complaint=complaint,
        ends_at=ends_at,
    )
    _log(actor, f"Забанив {target.username} ({duration_label(code)})", target)
    if code == "forever":
        lost = forfeit_deposit(target, actor)
        if lost > 0:
            _log(actor, f"Застава {lost} MC втрачена остаточно", target)


def _restrict_complaints(actor: CustomUser, complaint: Complaint) -> None:
    target = complaint.target_user
    if target is None:
        raise ValidationError({"consequences": "Обмеження потребує користувача."})
    settings_row = SiteSettings.get_solo()
    profile, _ = UserProfile.objects.get_or_create(user=target)
    days = (
        settings_row.complaints_block_first_days
        if profile.complaints_block_count == 0
        else settings_row.complaints_block_repeat_days
    )
    profile.complaints_block_count += 1
    profile.complaints_blocked_until = timezone.now() + timedelta(days=days)
    profile.save(update_fields=["complaints_block_count", "complaints_blocked_until"])
    _notify(
        target,
        f"Подачу скарг обмежено за рішенням скарги №{complaint.id}.",
        complaint,
        kind="complaint_submission_blocked",
    )


def _revoke_rights(actor: CustomUser, complaint: Complaint) -> None:
    target = complaint.target_user
    if target is None:
        raise ValidationError({"consequences": "Зняття прав потребує користувача."})
    if getattr(target, "is_admin_main", False):
        raise PermissionDenied("Зняти права головного адміна неможливо.")
    if getattr(target, "is_admin_1", False) and not is_main(actor):
        raise PermissionDenied("Змінити адміна 1-го рівня може лише головний.")
    if not getattr(target, "is_staff_admin", False):
        raise ValidationError({"consequences": "Користувач не є адміністратором."})
    target.role = CustomUser.ROLE_USER
    target.save(update_fields=["role"])
    target.admin_rights.all().delete()  # pyright: ignore[reportAttributeAccessIssue]
    _log(actor, f"Зняв права адміністратора з {target.username}", target)


def _apply_consequences(
    actor: CustomUser, complaint: Complaint, consequences: list[str], data: Any
) -> list[str]:
    """Застосовує вибрані наслідки, кожен з яких потребує відповідного права."""
    applied: list[str] = []
    refund_amount = _parse_amount(data.get("refund_amount_mc"))
    for code in consequences:
        if code == "none":
            continue
        if code in ("refund", "pay_seller", "cancel_order"):
            _require_any_right(actor, ORDERS_RIGHTS, "Потрібне право «complaints».")
            if code == "refund":
                _refund_buyer(actor, complaint, refund_amount)
            elif code == "pay_seller":
                _pay_seller(actor, complaint)
            else:
                _cancel_order(actor, complaint)
        elif code in ("hide_product", "delete_product"):
            _require_any_right(actor, PRODUCTS_RIGHTS, "Потрібне право «products».")
            product = complaint.product or (complaint.order and None)
            if product is None:
                raise ValidationError({"consequences": "Наслідок потребує товару."})
            moderation = "blocked" if code == "hide_product" else "deleted"
            reason = f"Рішення за скаргою №{complaint.id}"
            _apply_moderation(actor, product, moderation, reason)
            _notify(
                product.seller,
                f"Товар «{product.name}» опрацьовано за скаргою №{complaint.id}.",
                complaint,
                kind="product_moderation",
            )
        elif code == "delete_comment":
            _require_any_right(actor, PRODUCTS_RIGHTS, "Потрібне право «products».")
            if complaint.comment is None:
                raise ValidationError({"consequences": "Наслідок потребує коментаря."})
            comment = complaint.comment
            author = comment.author
            comment.delete()
            _notify(
                author,
                f"Ваш відгук видалено за скаргою №{complaint.id}.",
                complaint,
                kind="review_deleted",
            )
        elif code == "ban":
            require_right(actor, "bans")
            _apply_ban(actor, complaint, data)
        elif code == "restrict_complaints":
            if not is_admin1(actor):
                raise PermissionDenied("Обмежити скарги може адмін 1-го рівня або головний.")
            _restrict_complaints(actor, complaint)
        elif code == "revoke_rights":
            if not is_admin1(actor):
                raise PermissionDenied("Зняти права може адмін 1-го рівня або головний.")
            _revoke_rights(actor, complaint)
        elif code in ("warning", "request_fix"):
            text = (
                f"Попередження за скаргою №{complaint.id}."
                if code == "warning"
                else f"Прохання виправити за скаргою №{complaint.id}."
            )
            _notify(complaint.target_user, text, complaint, kind=f"complaint_{code}")
        else:
            raise ValidationError({"consequences": "Невідомий наслідок."})
        applied.append(code)
    return applied


def _parse_amount(raw: Any) -> Decimal | None:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return money(Decimal(str(raw)))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError({"refund_amount_mc": "Укажіть коректну суму."}) from exc


def _notify(
    user: CustomUser | None,
    text: str,
    complaint: Complaint,
    *,
    kind: str = "complaint",
    extra_payload: dict | None = None,
) -> None:
    notify(
        user,
        "complaints",
        kind,
        text,
        {"complaint_id": complaint.id, **(extra_payload or {})},
        url=f"/pages/complaints.html?id={complaint.id}",
    )


# --- Списки ---

def _target_of(complaint: Complaint):
    """Ключ і підписи групи (товар, користувач або замовлення)."""
    if complaint.target_kind == "order" and complaint.order is not None:
        order = complaint.order
        return (
            ("order", order.id),
            "order",
            order.id,
            f"Замовлення № {order.id}",
            f"Покупець: {order.buyer.username} · Продавець: {order.seller.username}",
            None,
            {
                "buyer": order.buyer.username,
                "seller": order.seller.username,
                "status": order.get_status_display(),
                "total_mc": str(order.total_mc),
                "created_at": order.created_at,
            },
        )
    if complaint.target_kind == "product" and complaint.product is not None:
        product = complaint.product
        return (
            ("product", product.id),
            "product",
            product.id,
            product.name,
            product.seller.username,
            product.seller_id,
            None,
        )
    if complaint.target_user is not None:
        user = complaint.target_user
        return (
            ("user", user.id),
            "user",
            user.id,
            user.username,
            user.get_role_display(),  # pyright: ignore[reportAttributeAccessIssue]
            user.id,
            None,
        )
    return (
        (complaint.target_kind, complaint.id),
        complaint.target_kind,
        complaint.id,
        complaint.get_target_kind_display(),  # pyright: ignore[reportAttributeAccessIssue]
        "",
        None,
        None,
    )


def _complaint_preview(complaint: Complaint) -> dict[str, Any]:
    text = (complaint.text or "").strip().replace("\n", " ")
    return {
        "id": complaint.id,
        "type_path": complaint.complaint_type.path,
        "status": complaint.status,
        "status_display": complaint.get_status_display(),  # pyright: ignore[reportAttributeAccessIssue]
        "preview": text[:PREVIEW_LEN] + ("…" if len(text) > PREVIEW_LEN else ""),
        "created_at": complaint.created_at,
    }


class AdminComplaintsView(APIView):
    """GET /api/admin/complaints?view=&status=&q=&page= — групи скарг за ціллю."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = cast(CustomUser, request.user)
        require_admin(actor)
        _require_complaints_access(actor)

        view = str(request.query_params.get("view") or "all")
        status_filter = str(request.query_params.get("status") or "all")
        query = str(request.query_params.get("q") or "").strip()

        queryset = _reviewable_query(actor).select_related(
            "complaint_type", "author", "target_user", "product__seller", "order__buyer", "order__seller"
        )
        view_map = {"products": "products", "sellers": "users_content", "orders": "orders"}
        if view in view_map:
            queryset = queryset.filter(complaint_type__view=view_map[view])
        if status_filter != "all":
            queryset = queryset.filter(status=status_filter)

        groups: dict[tuple, dict[str, Any]] = {}
        for complaint in queryset.order_by("-created_at"):
            key, kind, target_id, title, subtitle, user_id, order_details = _target_of(complaint)
            group = groups.get(key)
            if group is None:
                group = {
                    "kind": kind,
                    "target_id": target_id,
                    "title": title,
                    "subtitle": subtitle,
                    "order_details": order_details,
                    "user_id": user_id,
                    "count": 0,
                    "open": 0,
                    "last_at": complaint.created_at,
                    "complaints": [],
                }
                groups[key] = group
            group["count"] += 1
            if complaint.status in OPEN_STATUSES:
                group["open"] += 1
            group["complaints"].append(_complaint_preview(complaint))

        if query:
            lowered = query.lower()
            normalized_query = lowered.replace("№ ", "№")
            filtered_groups: dict[tuple, dict[str, Any]] = {}
            for key, group in groups.items():
                searchable = " ".join(
                    (
                        group["title"],
                        group["subtitle"],
                        *(
                            value
                            for complaint in group["complaints"]
                            for value in (complaint["type_path"], complaint["preview"])
                        ),
                    )
                ).lower()
                if normalized_query in searchable.replace("№ ", "№"):
                    filtered_groups[key] = group
            groups = filtered_groups

        items = sorted(
            groups.values(),
            key=lambda group: (group["open"], group["count"], group["last_at"]),
            reverse=True,
        )
        for group in items:
            if group["user_id"]:
                group["is_banned"] = Ban.objects.active().filter(user_id=group["user_id"]).exists()
            else:
                group["is_banned"] = False

        paginator = Paginator(items, PAGE_SIZE)
        page = paginator.get_page(request.query_params.get("page"))
        return Response(
            {
                "count": paginator.count,
                "page": page.number,
                "pages": paginator.num_pages,
                "view": view,
                "results": page.object_list,
            }
        )


class AdminComplaintDetailView(APIView):
    """GET /api/admin/complaints/{id} — повні дані скарги для адміна."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        actor = cast(CustomUser, request.user)
        require_admin(actor)
        _require_complaints_access(actor)
        complaint = _get_complaint(actor, pk)
        payload = _complaint_payload(complaint, actor, with_evidence=True, for_admin=True)
        payload["can_take"] = complaint.status == "new"
        payload["can_resolve"] = complaint.status in OPEN_STATUSES
        payload["is_handled"] = complaint.handled_by_id is not None
        payload["handled_by"] = (
            complaint.handled_by.username if complaint.handled_by_id else None
        )
        return Response(payload)


class AdminComplaintTakeView(APIView):
    """POST /api/admin/complaints/{id}/take — узяти скаргу в роботу."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        actor = cast(CustomUser, request.user)
        require_admin(actor)
        _require_complaints_access(actor)
        with transaction.atomic():
            complaint = _get_complaint(actor, pk)
            if complaint.status != "new":
                raise ValidationError({"status": "Узяти в роботу можна лише нову скаргу."})
            complaint.status = "in_review"
            complaint.handled_by = actor
            complaint.save(update_fields=["status", "handled_by"])
            _log(actor, f"Узяв у роботу скаргу #{complaint.id}", complaint.target_user)
        return Response(_complaint_payload(complaint, actor, with_evidence=True, for_admin=True))


class AdminComplaintCommentView(APIView):
    """POST /api/admin/complaints/{id}/comments — коментар адміна."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        actor = cast(CustomUser, request.user)
        require_admin(actor)
        _require_complaints_access(actor)
        complaint = _get_complaint(actor, pk)
        text = str(request.data.get("text") or "").strip()
        if not text:
            raise ValidationError({"text": "Напишіть коментар."})
        comment = ComplaintComment.objects.create(
            complaint=complaint, author=actor, side="admin", text=text
        )
        payload = {"comment_id": comment.pk}
        _notify(
            complaint.author,
            f"Адмін відповів у скарзі №{complaint.id}.",
            complaint,
            kind="complaint_comment",
            extra_payload=payload,
        )
        _notify(
            complaint.target_user,
            f"Адмін відповів у скарзі №{complaint.id}.",
            complaint,
            kind="complaint_comment",
            extra_payload=payload,
        )
        return Response(
            {"id": comment.id, "side": "admin", "text": comment.text,
             "created_at": comment.created_at},
            status=201,
        )


class AdminComplaintResolveView(APIView):
    """POST /api/admin/complaints/{id}/resolve — рішення з наслідками."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        actor = cast(CustomUser, request.user)
        require_admin(actor)
        _require_complaints_access(actor)

        status = str(request.data.get("status") or "").strip()
        if status not in RESOLVE_STATUSES:
            raise ValidationError({"status": "Оберіть рішення."})
        raw_consequences = request.data.get("consequences") or []
        if not isinstance(raw_consequences, list):
            raise ValidationError({"consequences": "Очікується список наслідків."})
        consequences = [str(code) for code in raw_consequences]
        unknown = [code for code in consequences if code not in CONSEQUENCE_CODES]
        if unknown:
            raise ValidationError({"consequences": "Невідомий наслідок."})
        if status != "upheld":
            consequences = []

        with transaction.atomic():
            complaint = _get_complaint(actor, pk)
            if complaint.status not in OPEN_STATUSES:
                raise ValidationError({"status": "Скаргу вже вирішено."})
            applied = _apply_consequences(actor, complaint, consequences, request.data)
            complaint.status = status
            complaint.handled_by = actor
            complaint.consequences = applied
            complaint.refund_amount_mc = _parse_amount(request.data.get("refund_amount_mc"))
            complaint.resolution = str(request.data.get("resolution") or "").strip()
            complaint.resolved_at = timezone.now()
            complaint.save(
                update_fields=[
                    "status",
                    "handled_by",
                    "consequences",
                    "refund_amount_mc",
                    "resolution",
                    "resolved_at",
                ]
            )
            if status == "false":
                register_false_complaint(complaint.author)
            _log(
                actor,
                f"Розглянув скаргу #{complaint.id}: {complaint.get_status_display()}",  # pyright: ignore[reportAttributeAccessIssue]
                complaint.target_user,
            )
            _notify(
                complaint.author,
                f"Скаргу №{complaint.id} вирішено: {complaint.get_status_display()}.",  # pyright: ignore[reportAttributeAccessIssue]
                complaint,
                kind="complaint_resolved",
            )
            _notify(
                complaint.target_user,
                f"Скаргу №{complaint.id} вирішено: {complaint.get_status_display()}.",  # pyright: ignore[reportAttributeAccessIssue]
                complaint,
                kind="complaint_resolved",
            )
        return Response(_complaint_payload(complaint, actor, with_evidence=True, for_admin=True))


class AdminLiftComplaintBlockView(APIView):
    """POST /api/admin/users/{id}/lift-complaint-block — зняти блок скарг."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        actor = cast(CustomUser, request.user)
        require_admin(actor)
        if not is_admin1(actor):
            raise PermissionDenied("Зняти блок скарг може адмін 1-го рівня або головний.")
        target = CustomUser.objects.filter(pk=pk).first()
        if target is None:
            raise NotFound("Користувача не знайдено.")
        with transaction.atomic():
            lift_complaint_block(target)
            _log(actor, f"Зняв блок скарг з {target.username}", target)
        return Response({"ok": True})


# --- Верифікація продавця (PROJECT_VISION розділ 11) ---

def _require_verification_access(actor: CustomUser) -> None:
    if is_admin1(actor):
        return
    if not actor.has_right("complaints"):
        raise PermissionDenied("Недостатньо прав для перегляду верифікації.")


class AdminVerificationView(APIView):
    """GET /api/admin/verifications/{user_id} — дані верифікації продавця."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        actor = cast(CustomUser, request.user)
        require_admin(actor)
        _require_verification_access(actor)
        verification = (
            SellerVerification.objects.filter(user_id=pk).select_related("user").first()
        )
        if verification is None:
            raise NotFound("Верифікацію не знайдено.")
        _log(
            actor,
            f"Переглянув дані верифікації {verification.user.username}",
            verification.user,
        )
        return Response(
            {
                "user_id": pk,
                "username": verification.user.username,
                "age": verification.age,
                "created_at": verification.created_at,
                "has_photo": bool(verification.photo),
                "photo_url": f"/api/admin/verifications/{pk}/photo",
            }
        )


class AdminVerificationPhotoView(APIView):
    """GET /api/admin/verifications/{user_id}/photo — приватне фото верифікації."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        actor = cast(CustomUser, request.user)
        require_admin(actor)
        _require_verification_access(actor)
        verification = (
            SellerVerification.objects.filter(user_id=pk).select_related("user").first()
        )
        if verification is None or not verification.photo:
            raise NotFound("Фото верифікації не знайдено.")
        _log(
            actor,
            f"Переглянув фото верифікації {verification.user.username}",
            verification.user,
        )
        return _file_response(verification.photo)
