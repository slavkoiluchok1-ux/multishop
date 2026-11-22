"""API оформлення, перегляду та зміни замовлень."""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from mimetypes import guess_type
from typing import Any, cast

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.http import FileResponse, Http404
from django.template.loader import render_to_string
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import CustomUser, SellerVerification
from apps.cart.models import Cart, CartItem
from apps.catalog.category_data import (
    is_one_time_development_path,
    uses_activation_codes,
    uses_digital_delivery,
)
from apps.catalog.models import (
    DigitalFile,
    Product,
    ProductVariant,
    sync_stock_status,
)
from apps.complaints.models import Complaint, ComplaintType
from apps.core.models import SiteSettings
from apps.notifications.services import notify
from apps.wallet.models import (
    EscrowHold,
    FrozenPayout,
    Wallet,
    WalletTransaction,
)
from apps.wallet.services import (
    BALANCE_LIMIT_MESSAGE,
    balance_over_limit,
    commission_for,
    money,
    recalculate_seller_deposit,
)

from .models import (
    ActivationCode,
    DownloadGrant,
    Order,
    OrderCancellation,
    OrderComment,
    OrderItem,
    OrderStatusHistory,
    Refund,
)


def _variant_uses_activation_codes(variant: ProductVariant) -> bool:
    product = variant.product
    return uses_activation_codes(
        product.product_type,
        [
            value.value
            for value in variant.attribute_values.all()
            if value.attribute.name == "Видача"
        ],
    )


def _contact_snapshot(user: CustomUser, data: dict[str, Any]):
    profile = user.profile
    email = str(data.get("contact_email") or user.email).strip()
    phone = str(data.get("contact_phone") or profile.phone).strip()
    telegram = str(data.get("contact_telegram") or profile.telegram).strip()
    if "contact_other" in data:
        other = str(data.get("contact_other") or "").strip()
    else:
        other = ", ".join(
            f"{label}: {value}"
            for label, value in (
                ("Instagram", profile.instagram),
                ("Viber", profile.viber),
                ("Facebook", profile.facebook),
                ("WhatsApp", profile.whatsapp),
            )
            if value
        )
    if not any((email, phone, telegram, other)):
        raise ValidationError({"contact_phone": "Укажіть хоча б один контакт."})
    return {
        "contact_email": email,
        "contact_phone": phone,
        "contact_telegram": telegram,
        "contact_other": other,
    }


def _delivery_snapshot(user: CustomUser, data: dict[str, Any]):
    profile = user.profile
    address_data = data.get("delivery_address")
    if not isinstance(address_data, dict):
        address_data = {}
    country = str(address_data.get("country") or data.get("delivery_country") or profile.delivery_country).strip()
    city = str(address_data.get("city") or data.get("delivery_city") or profile.delivery_city).strip()
    street = str(address_data.get("street") or data.get("delivery_street") or profile.delivery_street).strip()
    postcode = str(address_data.get("postcode") or data.get("delivery_postcode") or profile.delivery_postcode).strip()
    recipient = str(
        address_data.get("recipient_name")
        or data.get("recipient_name")
        or profile.recipient_name
        or user.username
    ).strip()
    if not country or not city or not street:
        raise ValidationError(
            {"delivery_address": "Для фізичного товару вкажіть країну, місто й адресу."}
        )
    lines = [country, city, street]
    if postcode:
        lines.append(postcode)
    return recipient, ", ".join(lines)


def _send_order_email(order_id):
    order = (
        Order.objects.select_related("buyer", "seller")
        .prefetch_related("items")
        .get(pk=order_id)
    )
    context = {"order": order}
    html = render_to_string("emails/order_created.html", context)
    text = render_to_string("emails/order_created.txt", context)
    message = EmailMultiAlternatives(
        subject=f"Замовлення №{order.pk} оформлено",
        body=text,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@multishop.local"),
        to=[order.contact_email or order.buyer.email],
    )
    message.attach_alternative(html, "text/html")
    message.send(fail_silently=True)


def _item_data(item: OrderItem, viewer: CustomUser):
    product = item.variant.product
    downloads = []
    activation_codes = []
    if viewer.id == item.order.buyer_id:
        grants = item.downloads.select_related("file").all()
        downloads = [
            {
                "id": grant.file_id,
                "title": grant.file.title or grant.file.file.name.rsplit("/", 1)[-1],
                "url": f"/api/downloads/{grant.file_id}",
            }
            for grant in grants
        ]
        activation_codes = list(
            item.activation_codes.order_by("id").values_list("code", flat=True)
        )
    return {
        "id": item.id,
        "product_id": product.id,
        "product_name": item.product_name,
        "product_type": product.product_type,
        "variant_id": item.variant_id,
        "sku": item.variant.sku,
        # Читабельний підпис варіанта: «Колір: чорний, Пам'ять: 256 ГБ».
        "variant_label": ", ".join(
            f"{value.attribute.name}: {value.value}"
            for value in item.variant.attribute_values.all()
        ),
        "quantity": item.quantity,
        "unit_price_mc": str(item.unit_price_mc),
        "subtotal_mc": str(item.unit_price_mc * item.quantity),
        "downloads": downloads,
        "activation_codes": activation_codes,
    }


def _order_card(order: Order, viewer: CustomUser):
    """Короткий рядок списку замовлень: без контактів і позицій."""
    is_buyer = viewer.id == order.buyer_id
    counterpart = order.seller if is_buyer else order.buyer
    items = list(order.items.all())
    first_item = None
    if items:
        first_item = {
            "product_name": items[0].product_name,
            "quantity": items[0].quantity,
        }
    return {
        "id": order.id,
        "status": order.status,
        "status_display": order.get_status_display(),
        "counterparty": counterpart.username,
        "total_mc": str(order.total_mc),
        "created_at": order.created_at,
        "paid_at": order.paid_at,
        "first_item": first_item,
    }


def _order_payload(order: Order, viewer: CustomUser):
    """Повні дані замовлення для покупця, продавця або адміна з правом orders.

    Показуються лише дані цього замовлення: контакти й адреса покупця,
    збережені в самому замовленні. Контакти продавця не розкриваються.
    """
    is_buyer = viewer.id == order.buyer_id
    is_seller = viewer.id == order.seller_id
    counterpart = order.seller if is_buyer else order.buyer
    cancellation = getattr(order, "cancellation", None)
    return {
        "id": order.id,
        "checkout_id": str(order.checkout_id),
        "status": order.status,
        "status_display": order.get_status_display(),
        "total_mc": str(order.total_mc),
        "fee_mc": str(order.fee_mc),
        "counterparty": counterpart.username,
        "is_buyer": is_buyer,
        "buyer_contacts": {
            "email": order.contact_email,
            "phone": order.contact_phone,
            "telegram": order.contact_telegram,
            "other": order.contact_other,
        },
        "recipient_name": order.recipient_name,
        "delivery_address": order.delivery_address,
        "paid_at": order.paid_at,
        "received_at": order.received_at,
        "complaint_deadline_at": order.complaint_deadline_at,
        "created_at": order.created_at,
        "items": [_item_data(item, viewer) for item in order.items.select_related(
            "variant__product"
        ).prefetch_related(
            "variant__attribute_values__attribute",
            "downloads__file",
            "activation_codes",
        )],
        # Відгуки, які глядач залишив саме за цим замовленням.
        "my_reviews": [
            {
                "product_id": review.product_id,
                "product_name": review.product.name,
                "text": review.text,
                "created_at": review.created_at,
            }
            for review in order.reviews.all()
            if review.author_id == viewer.id
        ],
        "status_history": [
            {
                "old_status": row.old_status,
                "new_status": row.new_status,
                "new_status_display": row.get_new_status_display(),
                "note": row.note,
                "created_at": row.created_at,
            }
            for row in order.status_history.all()
        ],
        "cancellation": (
            {
                "path": cancellation.path,
                "status": cancellation.status,
                "initiated_by_id": cancellation.initiated_by_id,
                "buyer_agreed": cancellation.buyer_agreed,
                "seller_agreed": cancellation.seller_agreed,
                "is_proposer": cancellation.initiated_by_id == viewer.id,
                "can_accept": (
                    cancellation.path == "mutual"
                    and cancellation.status == "pending"
                    and cancellation.initiated_by_id != viewer.id
                ),
                "can_reject": (
                    cancellation.path == "mutual"
                    and cancellation.status == "pending"
                    and cancellation.initiated_by_id != viewer.id
                ),
                "can_withdraw": (
                    cancellation.path == "mutual"
                    and cancellation.status == "pending"
                    and cancellation.initiated_by_id == viewer.id
                ),
            }
            if cancellation
            else None
        ),
        "comments": [
            {
                "id": comment.id,
                "author": comment.author.username,
                "text": comment.text,
                "created_at": comment.created_at,
                "is_mine": comment.author_id == viewer.id,
            }
            for comment in order.comments.select_related("author").all()
        ],
        "can_ship": is_seller and order.status == "paid",
        "can_confirm": is_buyer and order.status in ("paid", "shipped"),
        "can_comment": is_buyer or is_seller,
        "can_propose_mutual": (is_buyer or is_seller)
        and order.received_at is None
        and order.status in ("paid", "shipped")
        and (
            cancellation is None
            or cancellation.path == "mutual"
            and cancellation.status in ("rejected", "withdrawn")
        ),
        "can_open_complaint": (is_buyer or is_seller)
        and order.received_at is None
        and order.status in ("paid", "shipped")
        and cancellation is None,
    }


def _notify(
    user: CustomUser,
    text: str,
    order: Order,
    *,
    kind: str = "order",
    payload: dict[str, int] | None = None,
):
    notify(
        user,
        "orders",
        kind,
        text,
        payload or {"order_id": order.id},
        url=f"/pages/orders.html?order={order.id}",
    )


@transaction.atomic
def _complete_mutual_cancellation(order, cancellation, changed_by):
    """Повертає кошти з ескроу, відновлює залишок і закриває замовлення."""
    now = timezone.now()
    held_holds = list(
        EscrowHold.objects.select_for_update().filter(order=order, status="held")
    )
    refund_total = money(sum((hold.amount_mc for hold in held_holds), Decimal("0")))
    if refund_total <= 0:
        raise ValidationError("Для цього замовлення немає коштів в ескроу для повернення.")

    buyer_wallet = Wallet.objects.select_for_update().get(user=order.buyer)
    if balance_over_limit(buyer_wallet.free_balance_mc + refund_total):
        raise ValidationError(BALANCE_LIMIT_MESSAGE)
    buyer_wallet.held_mc -= refund_total
    buyer_wallet.free_balance_mc += refund_total
    buyer_wallet.save(update_fields=["held_mc", "free_balance_mc", "updated_at"])
    WalletTransaction.objects.create(
        wallet=buyer_wallet,
        amount_mc=refund_total,
        kind="refund",
        order=order,
        note="Повернення за взаємною згодою сторін",
    )
    for hold in held_holds:
        hold.status = "refunded"
        hold.resolved_at = now
        hold.save(update_fields=["status", "resolved_at"])

    for item in order.items.select_related("variant__product__category"):
        product = item.variant.product
        if uses_digital_delivery(product.product_type, product.category.path):
            continue
        ProductVariant.objects.filter(pk=item.variant_id).update(
            stock=F("stock") + item.quantity
        )
        product.refresh_from_db(fields=["status"])
        # Повернення залишку відновлює продаж (sold_out -> active)
        sync_stock_status(product)

    Refund.objects.create(
        order=order,
        amount_mc=refund_total,
        fee_returned_mc=Decimal("0"),
        source="escrow",
    )
    order.status = "cancelled"
    order.save(update_fields=["status"])
    cancellation.status = "completed"
    cancellation.save(update_fields=["status"])
    OrderStatusHistory.objects.create(
        order=order,
        old_status="paid",
        new_status="cancelled",
        changed_by=changed_by,
        note="Скасовано за згодою обох сторін",
    )
    recalculate_seller_deposit(order.seller, order=order, allow_increase=False)
    _notify(
        order.buyer,
        f"Замовлення №{order.id} скасовано, кошти повернено.",
        order,
        kind="order_cancel_refund",
    )
    _notify(
        order.seller,
        f"Замовлення №{order.id} скасовано за згодою сторін.",
        order,
        kind="order_cancelled",
    )


class CheckoutView(APIView):
    """POST /api/checkout — атомарно оформлює кошик і створює окремі замовлення продавцям."""

    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        buyer = cast(CustomUser, request.user)
        cart = Cart.objects.filter(user=buyer).first()
        if cart is None:
            raise ValidationError({"cart": "Кошик порожній."})

        cart_items = list(
            CartItem.objects.select_for_update()
            .filter(cart=cart)
            .select_related("variant__product__seller", "variant__product__category")
            .order_by("variant_id")
        )
        if not cart_items:
            raise ValidationError({"cart": "Кошик порожній."})

        contacts = _contact_snapshot(buyer, request.data)
        order_comment = str(request.data.get("order_comment") or "").strip()
        if len(order_comment) > 2000:
            raise ValidationError(
                {"order_comment": "Коментар має містити не більше 2000 символів."}
            )
        physical = any(item.variant.product.product_type == "physical" for item in cart_items)
        recipient, address = ("", "")
        if physical:
            recipient, address = _delivery_snapshot(buyer, request.data)

        variant_ids = [item.variant_id for item in cart_items]
        variants = {
            variant.id: variant
            for variant in ProductVariant.objects.select_for_update()
            .filter(id__in=variant_ids)
            .select_related("product__seller", "product__category")
            .prefetch_related("attribute_values__attribute")
            .order_by("id")
        }
        grouped = defaultdict(list)
        seller_ids = set()
        for item in cart_items:
            variant = variants.get(item.variant_id)
            if variant is None:
                raise ValidationError({"cart": "Один із варіантів більше недоступний."})
            product = variant.product
            if product.seller_id == buyer.id:
                raise ValidationError({"cart": "Не можна придбати власний товар."})
            if product.status not in ("active", "hidden"):
                raise ValidationError(
                    {"cart": f"Товар «{product.name}» недоступний для купівлі."}
                )
            # Заблокований чи видалений адміном товар купити не можна
            if product.moderation != "ok":
                raise ValidationError(
                    {"cart": f"Товар «{product.name}» недоступний."}
                )
            if item.quantity < 1 or variant.stock < item.quantity:
                raise ValidationError(
                    {"cart": f"Товар «{product.name}» закінчився."}
                )
            if is_one_time_development_path(product.category.path):
                if OrderItem.objects.filter(variant__product=product).exists():
                    raise ValidationError(
                        {"cart": f"Готову розробку «{product.name}» уже придбали."}
                    )
                inventory = ProductVariant.objects.filter(product=product).aggregate(
                    total=Sum("stock"),
                    variants=Count("id"),
                )
                if (
                    item.quantity != 1
                    or inventory["total"] != 1
                    or inventory["variants"] != 1
                ):
                    raise ValidationError(
                        {"cart": f"Готову розробку «{product.name}» можна придбати лише один раз."}
                    )
            if not SellerVerification.objects.filter(
                user_id=product.seller_id, age__gte=18
            ).exists():
                raise ValidationError({"cart": f"Продавець товару «{product.name}» не верифікований."})
            if uses_digital_delivery(product.product_type, product.category.path) and not DigitalFile.objects.filter(
                product=product, is_trial=False
            ).filter(models_q_variant_or_product(variant.id)).exists():
                raise ValidationError(
                    {"cart": f"Для товару «{product.name}» ще немає файлу для завантаження після покупки."}
                )
            if _variant_uses_activation_codes(variant):
                available_codes = ActivationCode.objects.filter(
                    variant=variant,
                    order_item__isnull=True,
                ).count()
                if available_codes < item.quantity:
                    raise ValidationError(
                        {"cart": f"Для товару «{product.name}» не вистачає кодів активації."}
                    )
            grouped[product.seller_id].append((item, variant))
            seller_ids.add(product.seller_id)

        user_ids = sorted({buyer.id, *seller_ids})
        for user_id in user_ids:
            Wallet.objects.get_or_create(user_id=user_id)
        wallets = {
            wallet.user_id: wallet
            for wallet in Wallet.objects.select_for_update()
            .filter(user_id__in=user_ids)
            .order_by("user_id")
        }
        buyer_wallet = wallets[buyer.id]
        order_totals = {
            seller_id: money(
                sum(
                    (variant.price_mc * item.quantity for item, variant in lines),
                    Decimal("0"),
                )
            )
            for seller_id, lines in grouped.items()
        }
        total_due = money(sum(order_totals.values(), Decimal("0")))
        if total_due <= 0:
            raise ValidationError({"cart": "Сума замовлення має бути більшою за нуль."})
        if buyer_wallet.free_balance_mc < total_due:
            raise ValidationError(
                {"wallet": f"Недостатньо MC. Потрібно {total_due} MC."}
            )

        now = timezone.now()
        buyer_wallet.free_balance_mc -= total_due
        buyer_wallet.held_mc += total_due
        buyer_wallet.save(update_fields=["free_balance_mc", "held_mc", "updated_at"])

        checkout_id = None
        created_orders = []
        seller_orders = {}
        for seller_id, lines in grouped.items():
            seller = lines[0][1].product.seller
            seller_wallet = wallets[seller_id]
            order_total = order_totals[seller_id]
            order = Order.objects.create(
                buyer=buyer,
                seller=seller,
                total_mc=order_total,
                contact_email=contacts["contact_email"],
                contact_phone=contacts["contact_phone"],
                contact_telegram=contacts["contact_telegram"],
                contact_other=contacts["contact_other"],
                recipient_name=recipient if any(
                    variant.product.product_type == "physical" for _, variant in lines
                ) else "",
                delivery_address=address if any(
                    variant.product.product_type == "physical" for _, variant in lines
                ) else "",
                paid_at=now,
            )
            checkout_id = checkout_id or order.checkout_id
            Order.objects.filter(pk=order.pk).update(checkout_id=checkout_id)
            order.checkout_id = checkout_id
            seller_orders[seller_id] = order

            groups = {True: [], False: []}
            for cart_item, variant in lines:
                order_item = OrderItem.objects.create(
                    order=order,
                    variant=variant,
                    product_name=variant.product.name,
                    unit_price_mc=variant.price_mc,
                    quantity=cart_item.quantity,
                )
                if _variant_uses_activation_codes(variant):
                    codes = list(
                        ActivationCode.objects.select_for_update()
                        .filter(variant=variant, order_item__isnull=True)
                        .order_by("id")[:cart_item.quantity]
                    )
                    if len(codes) != cart_item.quantity:
                        raise ValidationError(
                            {"cart": f"Для товару «{variant.product.name}» не вистачає кодів активації."}
                        )
                    for code in codes:
                        code.order_item = order_item
                    ActivationCode.objects.bulk_update(codes, ["order_item"])
                groups[
                    uses_digital_delivery(
                        variant.product.product_type,
                        variant.product.category.path,
                    ) or _variant_uses_activation_codes(variant)
                ].append(
                    (cart_item, variant)
                )
                variant.stock -= cart_item.quantity
                variant.save(update_fields=["stock"])
                # Нуль залишку — товар стає «Закінчився»
                sync_stock_status(variant.product)

            order_fee = Decimal("0")
            for is_digital, entries in groups.items():
                if not entries:
                    continue
                subtotal = money(
                    sum(
                        (variant.price_mc * item.quantity for item, variant in entries),
                        Decimal("0"),
                    )
                )
                fee = commission_for(subtotal)
                order_fee += fee
                hold = EscrowHold.objects.create(
                    order=order,
                    buyer=buyer,
                    seller=seller,
                    amount_mc=subtotal,
                    fee_mc=fee,
                    status="released" if is_digital else "held",
                    resolved_at=now if is_digital else None,
                )
                if is_digital:
                    net = subtotal - fee
                    buyer_wallet.held_mc -= subtotal
                    seller_wallet.frozen_mc += net
                    WalletTransaction.objects.create(
                        wallet=buyer_wallet,
                        amount_mc=-subtotal,
                        kind="escrow_to_frozen",
                        order=order,
                        note="Оплата цифрового файлу",
                    )
                    WalletTransaction.objects.create(
                        wallet=seller_wallet,
                        amount_mc=net,
                        kind="escrow_to_frozen",
                        order=order,
                        note="Виплата за цифрові файли заморожена на 14 днів",
                    )
                    if fee:
                        WalletTransaction.objects.create(
                            wallet=seller_wallet,
                            amount_mc=-fee,
                            kind="fee",
                            order=order,
                            note="Комісія сайту 3%",
                        )
                    settings_row = SiteSettings.get_solo()
                    FrozenPayout.objects.create(
                        seller=seller,
                        order=order,
                        amount_mc=net,
                        release_at=now + timedelta(days=settings_row.payout_freeze_days),
                    )
                    for item, variant in entries:
                        order_item = OrderItem.objects.get(order=order, variant=variant)
                        files = DigitalFile.objects.filter(
                            product=variant.product,
                            is_trial=False,
                        ).filter(
                            models_q_variant_or_product(variant.id)
                        )
                        for file in files:
                            DownloadGrant.objects.get_or_create(
                                order_item=order_item, file=file
                            )

            order.fee_mc = money(order_fee)
            all_digital = all(
                uses_digital_delivery(
                    variant.product.product_type,
                    variant.product.category.path,
                ) or _variant_uses_activation_codes(variant)
                for _, variant in lines
            )
            if all_digital:
                order.status = "delivered"
                order.received_at = now
                order.complaint_deadline_at = now + timedelta(
                    days=SiteSettings.get_solo().complaint_deadline_days
                )
            order.save(
                update_fields=[
                    "checkout_id",
                    "fee_mc",
                    "status",
                    "received_at",
                    "complaint_deadline_at",
                ]
            )
            OrderStatusHistory.objects.create(
                order=order,
                old_status="",
                new_status="paid",
                changed_by=buyer,
                note="Замовлення оплачено",
            )
            if all_digital:
                OrderStatusHistory.objects.create(
                    order=order,
                    old_status="paid",
                    new_status="delivered",
                    changed_by=None,
                    note="Цифровий товар доступний після оплати",
                )
            WalletTransaction.objects.create(
                wallet=buyer_wallet,
                amount_mc=-order_total,
                kind="escrow_hold",
                order=order,
                note=f"Оплата замовлення №{order.id}",
            )
            _notify(seller, f"Нове замовлення №{order.id}.", order, kind="order_new")
            if all_digital:
                _notify(
                    buyer,
                    f"Цифровий товар у замовленні №{order.id} доставлено.",
                    order,
                    kind="digital_delivery",
                )
            if order_comment:
                comment = OrderComment.objects.create(
                    order=order,
                    author=buyer,
                    text=order_comment,
                )
                _notify(
                    seller,
                    f"Новий коментар до замовлення №{order.id}.",
                    order,
                    kind="order_comment",
                    payload={"order_id": order.id, "comment_id": comment.id},
                )
            created_orders.append(order)
            transaction.on_commit(lambda order_id=order.id: _send_order_email(order_id))

        buyer_wallet.save(update_fields=["held_mc", "updated_at"])
        for seller_id, seller_wallet in wallets.items():
            if seller_id != buyer.id:
                seller_wallet.save(update_fields=["frozen_mc", "updated_at"])

        # Зменшення залишку повертає відповідну частину застави в заморожений баланс.
        for seller_id in seller_ids:
            recalculate_seller_deposit(
                grouped[seller_id][0][1].product.seller,
                order=seller_orders[seller_id],
                allow_increase=False,
            )
        cart.items.all().delete()
        return Response(
            {"orders": [_order_payload(order, buyer) for order in created_orders]},
            status=status.HTTP_201_CREATED,
        )


def models_q_variant(variant_id):
    return Q(variant_id=variant_id)


def models_q_variant_or_product(variant_id):
    return Q(variant_id=variant_id) | Q(variant__isnull=True)


def models_q_participant(user_id):
    return Q(buyer_id=user_id) | Q(seller_id=user_id)


class OrdersListView(APIView):
    """GET /api/orders/mine — покупки користувача (короткий список)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        buyer = cast(CustomUser, request.user)
        orders = Order.objects.filter(buyer=buyer).select_related("seller").prefetch_related(
            "items"
        )
        return Response({"orders": [_order_card(order, buyer) for order in orders]})


class IncomingOrdersView(APIView):
    """GET /api/orders/incoming — замовлення продавця (короткий список)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        seller = cast(CustomUser, request.user)
        orders = Order.objects.filter(seller=seller).select_related("buyer").prefetch_related(
            "items"
        )
        return Response({"orders": [_order_card(order, seller) for order in orders]})


class OrderDetailView(APIView):
    """GET /api/orders/{id} — деталі для покупця, продавця або адміна з наглядом."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        viewer = cast(CustomUser, request.user)
        order = (
            Order.objects.filter(pk=pk)
            .select_related("buyer", "seller")
            .prefetch_related(
                "items__variant__product",
                "items__variant__attribute_values__attribute",
                "items__downloads__file",
                "reviews__product",
                "status_history",
                "cancellation",
            )
            .first()
        )
        if order is None:
            return Response({"error": "Замовлення не знайдено."}, status=status.HTTP_404_NOT_FOUND)
        is_participant = viewer.id in (order.buyer_id, order.seller_id)
        # Нагляд за замовленнями мають адміни з правом «complaints» (спори)
        # або «bans» (розслідування користувачів).
        is_overseer = viewer.has_right("complaints") or viewer.has_right("bans")
        if not is_participant and not is_overseer:
            return Response({"error": "Замовлення не знайдено."}, status=status.HTTP_404_NOT_FOUND)
        return Response(_order_payload(order, viewer))


class OrderStatusView(APIView):
    """PATCH /api/orders/{id}/status — продавець позначає відправлення/передачу."""

    permission_classes = [IsAuthenticated]

    def patch(self, request, pk):
        seller = cast(CustomUser, request.user)
        with transaction.atomic():
            order = Order.objects.select_for_update().filter(pk=pk, seller=seller).first()
            if order is None:
                return Response({"error": "Замовлення не знайдено."}, status=status.HTTP_404_NOT_FOUND)
            if request.data.get("status") != "shipped" or order.status != "paid":
                raise ValidationError({"status": "Продавець може змінити лише «Оплачено» на «Відправлено»."})
            old_status = order.status
            order.status = "shipped"
            order.save(update_fields=["status"])
            OrderStatusHistory.objects.create(
                order=order,
                old_status=old_status,
                new_status=order.status,
                changed_by=seller,
            )
            _notify(
                order.buyer,
                f"Замовлення №{order.id} відправлено продавцем.",
                order,
                kind="order_shipped",
            )
        return Response(_order_payload(order, seller))


class ConfirmReceivedView(APIView):
    """POST /api/orders/{id}/confirm-received — переводить ескроу в 14-денну заморозку."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        buyer = cast(CustomUser, request.user)
        with transaction.atomic():
            order = Order.objects.select_for_update().filter(pk=pk, buyer=buyer).first()
            if order is None:
                return Response({"error": "Замовлення не знайдено."}, status=status.HTTP_404_NOT_FOUND)
            if order.status not in ("paid", "shipped") or order.received_at is not None:
                raise ValidationError({"order": "Це замовлення не можна підтвердити."})
            now = timezone.now()
            holds = list(
                EscrowHold.objects.select_for_update().filter(order=order, status="held")
            )
            if not holds:
                raise ValidationError({"order": "Для замовлення немає утриманих коштів."})
            seller_wallet, _ = Wallet.objects.select_for_update().get_or_create(user=order.seller)
            buyer_wallet, _ = Wallet.objects.select_for_update().get_or_create(user=order.buyer)
            payout_days = SiteSettings.get_solo().payout_freeze_days
            for hold in holds:
                net = hold.amount_mc - hold.fee_mc
                buyer_wallet.held_mc -= hold.amount_mc
                seller_wallet.frozen_mc += net
                hold.status = "released"
                hold.resolved_at = now
                hold.save(update_fields=["status", "resolved_at"])
                WalletTransaction.objects.create(
                    wallet=buyer_wallet,
                    amount_mc=-hold.amount_mc,
                    kind="escrow_to_frozen",
                    order=order,
                    note="Покупець підтвердив отримання",
                )
                WalletTransaction.objects.create(
                    wallet=seller_wallet,
                    amount_mc=net,
                    kind="escrow_to_frozen",
                    order=order,
                    note="Виплата заморожена на 14 днів",
                )
                if hold.fee_mc:
                    WalletTransaction.objects.create(
                        wallet=seller_wallet,
                        amount_mc=-hold.fee_mc,
                        kind="fee",
                        order=order,
                        note="Комісія сайту 3%",
                    )
                FrozenPayout.objects.create(
                    seller=order.seller,
                    order=order,
                    amount_mc=net,
                    release_at=now + timedelta(days=payout_days),
                )
            buyer_wallet.save(update_fields=["held_mc", "updated_at"])
            seller_wallet.save(update_fields=["frozen_mc", "updated_at"])
            old_status = order.status
            order.status = "delivered"
            order.received_at = now
            order.complaint_deadline_at = now + timedelta(
                days=SiteSettings.get_solo().complaint_deadline_days
            )
            order.save(update_fields=["status", "received_at", "complaint_deadline_at"])
            OrderStatusHistory.objects.create(
                order=order,
                old_status=old_status,
                new_status="delivered",
                changed_by=buyer,
                note="Покупець підтвердив отримання",
            )
            _notify(
                order.seller,
                f"Покупець підтвердив отримання замовлення №{order.id}.",
                order,
                kind="order_received",
            )
            recalculate_seller_deposit(order.seller, order=order, allow_increase=False)
        return Response(_order_payload(order, buyer))


class CancelOrderView(APIView):
    """POST /api/orders/{id}/cancel — скасування зі створенням скарги."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        actor = cast(CustomUser, request.user)
        with transaction.atomic():
            order = Order.objects.select_for_update().filter(pk=pk).filter(
                models_q_participant(actor.id)
            ).first()
            if order is None:
                return Response({"error": "Замовлення не знайдено."}, status=status.HTTP_404_NOT_FOUND)
            if order.received_at is not None or order.status not in ("paid", "shipped"):
                raise ValidationError({"order": "Скасувати можна лише до підтвердження отримання."})
            if hasattr(order, "cancellation"):
                raise ValidationError({"order": "Для цього замовлення скасування вже розпочато."})

            complaint_type = ComplaintType.objects.filter(
                pk=request.data.get("complaint_type_id"),
                view="orders",
                is_active=True,
            ).first()
            if complaint_type is None or not complaint_type.is_leaf:
                raise ValidationError({"complaint_type_id": "Оберіть кінцевий тип скарги на замовлення."})
            text = str(request.data.get("text") or "").strip()
            if not text:
                raise ValidationError({"text": "Опишіть причину скасування."})

            is_buyer = actor.id == order.buyer_id
            target_user = order.seller if is_buyer else order.buyer
            complaint = Complaint.objects.create(
                author=actor,
                complaint_type=complaint_type,
                target_kind="order",
                order=order,
                target_user=target_user,
                text=text,
                fields=request.data.get("fields") or {},
            )
            OrderCancellation.objects.create(
                order=order,
                initiated_by=actor,
                path="complaint",
                complaint=complaint,
            )
            old_status = order.status
            order.status = "dispute"
            order.save(update_fields=["status"])
            OrderStatusHistory.objects.create(
                order=order,
                old_status=old_status,
                new_status="dispute",
                changed_by=actor,
                note="Відкрито скаргу під час скасування",
            )
            _notify(
                order.seller if is_buyer else order.buyer,
                f"Нова скарга щодо замовлення №{order.id}.",
                order,
                kind="order_dispute",
            )
        return Response(
            {"order": _order_payload(order, actor), "complaint_id": complaint.id},
            status=status.HTTP_201_CREATED,
        )


class MutualCancelView(APIView):
    """POST /api/orders/{id}/cancel-agree — пропозиція або відповідь на неї."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        actor = cast(CustomUser, request.user)
        with transaction.atomic():
            order = Order.objects.select_for_update().filter(pk=pk).filter(
                models_q_participant(actor.id)
            ).first()
            if order is None:
                return Response({"error": "Замовлення не знайдено."}, status=status.HTTP_404_NOT_FOUND)
            if order.received_at is not None or order.status not in ("paid", "shipped"):
                raise ValidationError({"order": "Скасувати можна лише до підтвердження отримання."})
            cancellation = (
                OrderCancellation.objects.select_for_update().filter(order=order).first()
            )
            action = request.data.get("action")
            if action is None:
                action = (
                    "accept"
                    if cancellation
                    and cancellation.status == "pending"
                    and cancellation.initiated_by_id != actor.id
                    else "propose"
                )
            if action not in ("propose", "accept", "reject", "withdraw"):
                raise ValidationError({"action": "Невідома дія зі скасуванням."})

            if action == "propose":
                notify_other = False
                if cancellation and cancellation.path != "mutual":
                    raise ValidationError({"order": "Для цього замовлення вже відкрито скаргу."})
                if cancellation and cancellation.status == "pending":
                    if cancellation.initiated_by_id != actor.id:
                        raise ValidationError({"order": "Спершу дайте відповідь на пропозицію."})
                elif cancellation:
                    cancellation.initiated_by = actor
                    cancellation.status = "pending"
                    cancellation.buyer_agreed = actor.id == order.buyer_id
                    cancellation.seller_agreed = actor.id == order.seller_id
                    cancellation.created_at = timezone.now()
                    cancellation.save(
                        update_fields=[
                            "initiated_by",
                            "status",
                            "buyer_agreed",
                            "seller_agreed",
                            "created_at",
                        ]
                    )
                    notify_other = True
                else:
                    cancellation = OrderCancellation.objects.create(
                        order=order,
                        initiated_by=actor,
                        path="mutual",
                        buyer_agreed=actor.id == order.buyer_id,
                        seller_agreed=actor.id == order.seller_id,
                    )
                    notify_other = True
                if notify_other:
                    other = order.seller if actor.id == order.buyer_id else order.buyer
                    _notify(
                        other,
                        f"Запропоновано взаємне скасування замовлення №{order.id}.",
                        order,
                        kind="order_cancel_proposed",
                    )
            else:
                if (
                    cancellation is None
                    or cancellation.path != "mutual"
                    or cancellation.status != "pending"
                ):
                    raise ValidationError({"order": "Активної пропозиції скасування немає."})
                is_proposer = cancellation.initiated_by_id == actor.id
                other = order.seller if actor.id == order.buyer_id else order.buyer
                if action == "accept":
                    if is_proposer:
                        raise ValidationError({"action": "Власну пропозицію не можна прийняти."})
                    if actor.id == order.buyer_id:
                        cancellation.buyer_agreed = True
                    else:
                        cancellation.seller_agreed = True
                    cancellation.save(update_fields=["buyer_agreed", "seller_agreed"])
                    _complete_mutual_cancellation(order, cancellation, actor)
                    _notify(
                        cancellation.initiated_by,
                        f"Пропозицію скасування замовлення №{order.id} прийнято.",
                        order,
                        kind="order_cancel_accepted",
                    )
                elif action == "reject":
                    if is_proposer:
                        raise ValidationError({"action": "Власну пропозицію не можна відхилити."})
                    cancellation.status = "rejected"
                    cancellation.save(update_fields=["status"])
                    _notify(
                        cancellation.initiated_by,
                        f"Пропозицію скасування замовлення №{order.id} відхилено.",
                        order,
                        kind="order_cancel_rejected",
                    )
                else:
                    if not is_proposer:
                        raise ValidationError({"action": "Відкликати може лише автор пропозиції."})
                    cancellation.status = "withdrawn"
                    cancellation.save(update_fields=["status"])
                    _notify(
                        other,
                        f"Пропозицію скасування замовлення №{order.id} відкликано.",
                        order,
                        kind="order_cancel_withdrawn",
                    )
        order.refresh_from_db()
        return Response(_order_payload(order, actor))


class OrderCommentView(APIView):
    """POST /api/orders/{id}/comments — додає повідомлення в обговорення замовлення."""

    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk):
        actor = cast(CustomUser, request.user)
        order = Order.objects.filter(pk=pk).filter(models_q_participant(actor.id)).first()
        if order is None:
            return Response({"error": "Замовлення не знайдено."}, status=status.HTTP_404_NOT_FOUND)

        text = str(request.data.get("text") or "").strip()
        if not text:
            raise ValidationError({"text": "Напишіть коментар."})
        if len(text) > 2000:
            raise ValidationError({"text": "Коментар має містити не більше 2000 символів."})

        comment = OrderComment.objects.create(order=order, author=actor, text=text)
        other = order.seller if actor.id == order.buyer_id else order.buyer
        _notify(
            other,
            f"Новий коментар до замовлення №{order.id}.",
            order,
            kind="order_comment",
            payload={"order_id": order.id, "comment_id": comment.id},
        )
        return Response(
            {
                "id": comment.id,
                "author": actor.username,
                "text": comment.text,
                "created_at": comment.created_at,
                "is_mine": True,
            },
            status=status.HTTP_201_CREATED,
        )


class PaidDownloadView(APIView):
    """GET /api/downloads/{file_id} — віддає платний файл лише покупцю."""

    permission_classes = [IsAuthenticated]

    def get(self, request, file_id):
        buyer = cast(CustomUser, request.user)
        grant = DownloadGrant.objects.select_related(
            "file", "order_item__order"
        ).filter(
            file_id=file_id,
            order_item__order__buyer=buyer,
            order_item__order__status__in=("paid", "shipped", "delivered"),
        ).first()
        if grant is None:
            raise Http404("Файл недоступний.")
        return _file_response(grant.file.file)


class TrialDownloadView(APIView):
    """GET /api/products/{product_id}/trial-files/{file_id} — пробний файл."""

    permission_classes = []

    def get(self, request, product_id, file_id):
        file_record = (
            DigitalFile.objects.filter(
                pk=file_id,
                product_id=product_id,
                is_trial=True,
            )
            .first()
        )
        if file_record is None:
            raise Http404("Пробний файл не знайдено.")
        return _file_response(file_record.file)


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
