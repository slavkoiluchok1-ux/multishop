"""API скарг: довідник типів, подача, перегляд, коментарі, докази."""
import json
from mimetypes import guess_type
from typing import Any, cast

from django.db import transaction
from django.http import FileResponse, Http404
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.paginator import Paginator
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import CustomUser
from apps.catalog.api_views import _can_view
from apps.catalog.models import Product, Review
from apps.core.storage import validate_upload
from apps.core.throttles import ComplaintRateThrottle, UploadRateThrottle
from apps.notifications.services import notify, notify_admin_digest
from apps.orders.models import Order

from .models import Complaint, ComplaintComment, ComplaintEvidence, ComplaintType
from .services import complaint_block_status

PAGE_SIZE = 20
SIDE_LABELS = {"complainant": "Скаржник", "respondent": "Відповідач", "admin": "Адмін"}
OPEN_STATUSES = ("new", "in_review")


def _is_admin(user) -> bool:
    return bool(getattr(user, "is_staff_admin", False))


def _json_object(raw: Any) -> dict:
    """Поля форми приходять або словником, або JSON-рядком із multipart."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _file_response(field_file):
    filename = field_file.name.rsplit("/", 1)[-1]
    content_type = guess_type(filename)[0] or "application/octet-stream"
    try:
        file_handle = field_file.open("rb")
    except FileNotFoundError as exc:
        raise Http404("Файл більше недоступний.") from exc
    return FileResponse(
        file_handle,
        as_attachment=True,
        filename=filename,
        content_type=content_type,
    )


def _evidence_payload(evidence: ComplaintEvidence) -> dict[str, Any]:
    filename = evidence.file.name
    if not filename:
        raise Http404("Файл доказу більше недоступний.")
    return {
        "id": evidence.id,
        "name": filename.rsplit("/", 1)[-1],
        "url": f"/api/complaints/evidence/{evidence.id}",
    }


def _comment_payload(comment: ComplaintComment, for_admin: bool) -> dict[str, Any]:
    author = comment.author.username if for_admin else SIDE_LABELS.get(comment.side, "Сторона")
    return {
        "id": comment.id,
        "side": comment.side,
        "author": author,
        "text": comment.text,
        "created_at": comment.created_at,
    }


def _complaint_card(complaint: Complaint, viewer: CustomUser) -> dict[str, Any]:
    """Короткий рядок списку скарг користувача."""
    is_author = complaint.author_id == viewer.id
    counterparty = (
        complaint.target_user.username
        if is_author and complaint.target_user
        else "Скаржник"
    )
    return {
        "id": complaint.id,
        "type_path": complaint.complaint_type.path,
        "view": complaint.complaint_type.view,
        "view_display": complaint.complaint_type.get_view_display(),  # pyright: ignore[reportAttributeAccessIssue]
        "target_kind": complaint.target_kind,
        "status": complaint.status,
        "status_display": complaint.get_status_display(),  # pyright: ignore[reportAttributeAccessIssue]
        "text": complaint.text,
        "counterparty": counterparty,
        "role": "author" if is_author else "respondent",
        "created_at": complaint.created_at,
        "resolved_at": complaint.resolved_at,
    }


def _complaint_payload(
    complaint: Complaint,
    viewer: CustomUser,
    *,
    with_evidence: bool = False,
    for_admin: bool = False,
) -> dict[str, Any]:
    """Повні дані скарги. Нік автор і докази приховані для відповідача."""
    is_author = complaint.author_id == viewer.id
    is_respondent = complaint.target_user_id == viewer.id
    if for_admin:
        role = "admin"
    elif is_author:
        role = "author"
    else:
        role = "respondent"
    return {
        "id": complaint.id,
        "type_path": complaint.complaint_type.path,
        "view": complaint.complaint_type.view,
        "view_display": complaint.complaint_type.get_view_display(),  # pyright: ignore[reportAttributeAccessIssue]
        "target_kind": complaint.target_kind,
        "status": complaint.status,
        "status_display": complaint.get_status_display(),  # pyright: ignore[reportAttributeAccessIssue]
        "text": complaint.text,
        "fields": complaint.fields,
        "role": role,
        "author": (
            complaint.author.username if (for_admin or is_author) else "Скаржник"
        ),
        "target_user": (
            complaint.target_user.username if complaint.target_user else None
        ),
        "target_user_id": complaint.target_user_id,
        "order_id": complaint.order_id,
        "product_id": complaint.product_id,
        "consequences": complaint.consequences,
        "refund_amount_mc": (
            str(complaint.refund_amount_mc)
            if complaint.refund_amount_mc is not None
            else None
        ),
        "resolution": complaint.resolution,
        "evidence": (
            [_evidence_payload(row) for row in complaint.evidence.all()]
            if with_evidence
            else []
        ),
        "comments": [
            _comment_payload(comment, for_admin) for comment in complaint.comments.all()
        ],
        "can_comment": complaint.status in OPEN_STATUSES and (is_author or is_respondent),
        "created_at": complaint.created_at,
        "resolved_at": complaint.resolved_at,
    }


def _notify(
    user: CustomUser | None,
    text: str,
    complaint: Complaint,
    *,
    kind: str = "complaint",
    extra_payload: dict | None = None,
) -> None:
    payload = {"complaint_id": complaint.id, **(extra_payload or {})}
    notify(
        user,
        "complaints",
        kind,
        text,
        payload,
        url=f"/pages/complaints.html?id={complaint.id}",
    )


def _resolve_target(author: CustomUser, complaint_type: ComplaintType, data: Any):
    """Перевіряє ціль скарги, повертає (target_kind, order, product, target_user, comment)."""
    view = complaint_type.view
    if view == "orders":
        order = Order.objects.filter(pk=data.get("order_id")).first()
        if order is None:
            raise ValidationError({"order_id": "Оберіть замовлення."})
        if author.id not in (order.buyer_id, order.seller_id):
            raise ValidationError({"order_id": "Це замовлення вам недоступне."})
        if order.received_at is not None:
            deadline = order.complaint_deadline_at
            if deadline is not None and timezone.now() > deadline:
                raise ValidationError(
                    {"order_id": "Строк подання скарги на це замовлення минув."}
                )
        target = order.seller if author.id == order.buyer_id else order.buyer
        return "order", order, None, target, None

    if view == "products":
        product = (
            Product.objects.filter(pk=data.get("product_id"))
            .select_related("seller")
            .first()
        )
        if product is None:
            raise ValidationError({"product_id": "Оберіть товар."})
        if product.seller_id == author.id:
            raise ValidationError({"product_id": "Не можна скаржитись на власний товар."})
        if not _can_view(product, author):
            raise ValidationError({"product_id": "Цей товар вам недоступний."})
        return "product", None, product, product.seller, None

    # users_content: або користувач, або коментар до товару
    comment_id = data.get("comment_id")
    if comment_id:
        review = Review.objects.filter(pk=comment_id).select_related("author").first()
        if review is None:
            raise ValidationError({"comment_id": "Коментар не знайдено."})
        if review.author_id == author.id:
            raise ValidationError({"comment_id": "Не можна скаржитись на власний коментар."})
        return "comment", None, review.product, review.author, review

    target = CustomUser.objects.filter(pk=data.get("target_user_id")).first()
    if target is None:
        raise ValidationError({"target_user_id": "Оберіть користувача."})
    if target.id == author.id:
        raise ValidationError({"target_user_id": "Не можна скаржитись на себе."})
    kind = "admin" if getattr(target, "is_staff_admin", False) else "user"
    return kind, None, None, target, None


class ComplaintTypesView(APIView):
    """GET /api/complaint-types — дерево типів (лише читання)."""

    permission_classes = [AllowAny]

    def get(self, request):
        view = request.query_params.get("view")
        queryset = ComplaintType.objects.filter(is_active=True)
        if view:
            queryset = queryset.filter(view=view)

        parent_id = request.query_params.get("parent")
        if parent_id:
            queryset = queryset.filter(parent_id=parent_id)
        elif request.query_params.get("roots") == "1":
            queryset = queryset.filter(parent__isnull=True)
        elif request.query_params.get("leaves") == "1":
            queryset = queryset.filter(parent__isnull=False)

        rows = list(queryset.order_by("path", "id"))
        # Кількість активних дітей кожного вузла — для плиток і ознаки листка.
        children_count: dict[int, int] = {}
        for parent in ComplaintType.objects.filter(
            is_active=True,
            parent_id__in=[row.id for row in rows],
        ).values_list("parent_id", flat=True):
            children_count[parent] = children_count.get(parent, 0) + 1
        return Response(
            {
                "types": [
                    {
                        "id": row.id,
                        "name": row.name,
                        "path": row.path,
                        "parent_id": row.parent_id,
                        "is_leaf": children_count.get(row.id, 0) == 0,
                        "has_children": children_count.get(row.id, 0) > 0,
                        "children_count": children_count.get(row.id, 0),
                        "required_fields": row.required_fields or [],
                    }
                    for row in rows
                    if request.query_params.get("leaves") != "1"
                    or row.id not in children_count
                ]
            }
        )


class ComplaintListCreateView(APIView):
    """GET/POST /api/complaints — список своїх/проти себе та подача скарги."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [ComplaintRateThrottle, UploadRateThrottle]

    def get(self, request):
        viewer = cast(CustomUser, request.user)
        tab = request.query_params.get("tab") or "mine"
        queryset = Complaint.objects.select_related(
            "complaint_type", "author", "target_user"
        )
        if tab == "against":
            queryset = queryset.filter(target_user=viewer)
        else:
            queryset = queryset.filter(author=viewer)
        paginator = Paginator(queryset, PAGE_SIZE)
        page = paginator.get_page(request.query_params.get("page"))
        return Response(
            {
                "count": paginator.count,
                "page": page.number,
                "pages": paginator.num_pages,
                "results": [_complaint_card(row, viewer) for row in page.object_list],
            }
        )

    def post(self, request):
        author = cast(CustomUser, request.user)
        if author.has_right("complaints"):
            raise PermissionDenied("Адміністратори не подають скарг.")
        blocked, until = complaint_block_status(author)
        if blocked and until is not None:
            when = timezone.localtime(until).strftime("%d.%m.%Y")
            raise PermissionDenied(
                f"Подача скарг заблокована до {when}. Ви можете написати в підтримку."
            )

        complaint_type = ComplaintType.objects.filter(
            pk=request.data.get("complaint_type_id"), is_active=True
        ).first()
        if complaint_type is None:
            raise ValidationError({"complaint_type_id": "Оберіть тип скарги."})
        if not complaint_type.is_leaf:
            raise ValidationError({"complaint_type_id": "Оберіть кінцевий тип скарги."})

        text = str(request.data.get("text") or "").strip()
        if not text:
            raise ValidationError({"text": "Опишіть проблему."})

        fields = _json_object(request.data.get("fields"))
        missing = [
            name
            for name in (complaint_type.required_fields or [])
            if not str(fields.get(name) or "").strip()
        ]
        if missing:
            raise ValidationError({"fields": "Заповніть усі обов'язкові поля."})

        target_kind, order, product, target_user, comment = _resolve_target(
            author, complaint_type, request.data
        )

        with transaction.atomic():
            complaint = Complaint.objects.create(
                author=author,
                complaint_type=complaint_type,
                target_kind=target_kind,
                order=order,
                product=product,
                target_user=target_user,
                comment=comment,
                text=text,
                fields=fields,
            )
            for uploaded in request.FILES.getlist("evidence"):
                try:
                    validate_upload(uploaded)
                except DjangoValidationError as exc:
                    message = exc.messages[0] if getattr(exc, "messages", None) else str(exc)
                    raise ValidationError({"evidence": message}) from exc
                ComplaintEvidence.objects.create(complaint=complaint, file=uploaded)
            _notify(
                target_user,
                f"Нова скарга №{complaint.id}. Ви можете надати пояснення.",
                complaint,
                kind="complaint_received",
            )
            notify_admin_digest(
                "complaints",
                f"Нова скарга №{complaint.id}.",
                {"event_id": complaint.id},
                "/pages/admin/complaints.html",
            )

        return Response(
            _complaint_payload(complaint, author, with_evidence=True),
            status=201,
        )


class ComplaintDetailView(APIView):
    """GET /api/complaints/{id} — деталі скарги для сторін або адміна."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        viewer = cast(CustomUser, request.user)
        complaint = (
            Complaint.objects.filter(pk=pk)
            .select_related("complaint_type", "author", "target_user")
            .prefetch_related("evidence", "comments__author")
            .first()
        )
        if complaint is None:
            raise Http404("Скаргу не знайдено.")
        is_author = complaint.author_id == viewer.id
        is_respondent = complaint.target_user_id == viewer.id
        is_admin = _is_admin(viewer)
        is_reviewer = is_admin and not (is_author or is_respondent)
        if not (is_author or is_respondent):
            if not is_admin:
                raise Http404("Скаргу не знайдено.")
            from .admin_api import _handleable

            _handleable(viewer, complaint)
        return Response(
            _complaint_payload(
                complaint,
                viewer,
                with_evidence=is_author or is_reviewer,
                for_admin=is_reviewer,
            )
        )


class ComplaintCommentView(APIView):
    """POST /api/complaints/{id}/comments — коментар сторони, поки скарга відкрита."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [ComplaintRateThrottle]

    def post(self, request, pk):
        viewer = cast(CustomUser, request.user)
        complaint = Complaint.objects.filter(pk=pk).select_related("author", "target_user").first()
        if complaint is None:
            raise Http404("Скаргу не знайдено.")
        is_author = complaint.author_id == viewer.id
        is_respondent = complaint.target_user_id == viewer.id
        if not (is_author or is_respondent):
            raise PermissionDenied("Лише сторони скарги можуть коментувати.")
        if complaint.status not in OPEN_STATUSES:
            raise ValidationError({"comment": "Скаргу вже вирішено, коментувати не можна."})
        text = str(request.data.get("text") or "").strip()
        if not text:
            raise ValidationError({"text": "Напишіть коментар."})
        comment = ComplaintComment.objects.create(
            complaint=complaint,
            author=viewer,
            side="complainant" if is_author else "respondent",
            text=text,
        )
        other = complaint.target_user if is_author else complaint.author
        _notify(
            other,
            f"Новий коментар у скарзі №{complaint.id}.",
            complaint,
            kind="complaint_comment",
            extra_payload={"comment_id": comment.pk},
        )
        return Response(_comment_payload(comment, False), status=201)


class ComplaintEvidenceView(APIView):
    """GET /api/complaints/evidence/{id} — приватний доказ для скаржника й адмінів."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        viewer = cast(CustomUser, request.user)
        evidence = (
            ComplaintEvidence.objects.select_related("complaint")
            .filter(pk=pk)
            .first()
        )
        if evidence is None:
            raise Http404("Доказ не знайдено.")
        is_author = evidence.complaint.author_id == viewer.id
        is_admin = _is_admin(viewer)
        if not is_author and not is_admin:
            raise Http404("Доказ не знайдено.")
        if is_admin and not is_author:
            from .admin_api import _handleable

            _handleable(viewer, evidence.complaint)
        return _file_response(evidence.file)
