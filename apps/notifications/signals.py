"""Подієві сповіщення для товарів, гаманця, підтримки й акаунтів."""
from decimal import Decimal

from django.db.models import Min, Sum
from django.db.models.signals import post_delete, post_save, pre_delete, pre_save
from django.dispatch import receiver
from django.utils import timezone

from apps.accounts.models import AdminRight, Ban, CustomUser, SellerVerification, UserProfile
from apps.catalog.models import Product, ProductVariant
from apps.complaints.models import Complaint
from apps.favorites.models import Favorite
from apps.policy.models import PolicyVersion
from apps.support.models import SupportMessage, SupportTicket
from apps.wallet.models import WalletTransaction

from .services import notify, notify_admin_digest


def _product_url(product_id):
    return f"/pages/product.html?id={product_id}"


def _format_money(value):
    if value is None:
        return "—"
    text = f"{Decimal(value):f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _notify_favorites(
    product_id,
    *,
    old_price=None,
    old_stock=None,
    variant_action=None,
    variant_id=None,
):
    product = Product.objects.filter(pk=product_id).first()
    if product is None:
        return
    new_price = ProductVariant.objects.filter(product_id=product_id).aggregate(
        value=Min("price_mc")
    )["value"]
    new_stock = ProductVariant.objects.filter(product_id=product_id).aggregate(
        value=Sum("stock")
    )["value"] or 0
    favorites = list(Favorite.objects.filter(product_id=product_id).select_related("user"))
    for favorite in favorites:
        changes = []
        prior_price = favorite.last_price if favorite.last_price is not None else old_price
        if prior_price is not None and new_price is not None and Decimal(prior_price) != Decimal(new_price):
            changes.append(
                f"ціна {_format_money(prior_price)} → {_format_money(new_price)} MC"
            )
        if old_stock is not None and old_stock > 0 and new_stock == 0:
            changes.append("товар закінчився")
        elif old_stock == 0 and new_stock > 0:
            changes.append("товар знову в наявності")
        if variant_action == "added":
            changes.append("додано варіант")
        elif variant_action == "deleted":
            changes.append("видалено варіант")

        if (
            favorite.last_status
            and favorite.last_status != product.status
            and product.status in ("active", "sold_out", "hidden", "inactive")
            and old_stock is None
        ):
            status_text = {
                "active": "товар знову в продажу",
                "sold_out": "товар закінчився",
                "hidden": "товар приховано",
                "inactive": "продавець зупинив продаж",
            }
            changes.append(status_text[product.status])

        if changes:
            notify(
                favorite.user,
                "favorites",
                "product_update",
                f"Оновлення товару «{product.name}»: {', '.join(changes)}.",
                {
                    "product_id": product.pk,
                    "price_mc": _format_money(new_price),
                    "changes": changes,
                    "variant_id": variant_id,
                },
                url=_product_url(product.pk),
            )

    Favorite.objects.filter(product_id=product_id).update(
        last_price=new_price,
        last_status=product.status,
    )


def notify_favorites_about_moderated_product(product, moderation):
    """Сповіщає обране після bulk-дії, яка не викликає model signals."""
    for favorite in Favorite.objects.filter(product_id=product.pk).select_related("user"):
        notify(
            favorite.user,
            "favorites",
            "product_moderation",
            f"Товар «{product.name}» {moderation}.",
            {"product_id": product.pk, "moderation": product.moderation},
            url=_product_url(product.pk),
        )
    Favorite.objects.filter(product_id=product.pk).update(
        last_price=ProductVariant.objects.filter(product_id=product.pk).aggregate(
            value=Min("price_mc")
        )["value"],
        last_status=product.status,
    )


@receiver(pre_save, sender=Product)
def remember_product_state(sender, instance, **kwargs):
    instance._notification_old = None
    if instance.pk:
        instance._notification_old = (
            Product.objects.filter(pk=instance.pk)
            .values("status", "moderation")
            .first()
        )


@receiver(post_save, sender=Product)
def notify_product_change(sender, instance, created, **kwargs):
    old_state = getattr(instance, "_notification_old", None)
    if created or old_state is None:
        return
    if old_state["status"] == instance.status and old_state["moderation"] == instance.moderation:
        return
    product_id = instance.pk
    old_moderation = old_state["moderation"]

    def send_after_commit():
        product = Product.objects.filter(pk=product_id).first()
        if product is None:
            return
        if old_moderation != product.moderation:
            action = {
                "blocked": "заблоковано",
                "deleted": "видалено",
                "ok": "відновлено",
            }.get(product.moderation, "змінено")
            for favorite in Favorite.objects.filter(product_id=product_id).select_related("user"):
                notify(
                    favorite.user,
                    "favorites",
                    "product_moderation",
                    f"Товар «{product.name}» {action} адміністратором.",
                    {"product_id": product_id, "moderation": product.moderation},
                    url=_product_url(product_id),
                )
            Favorite.objects.filter(product_id=product_id).update(
                last_price=product.min_price_mc,
                last_status=product.status,
            )
            return
        _notify_favorites(product_id)

    from django.db import transaction

    transaction.on_commit(send_after_commit)


def _variant_state(product_id):
    return {
        "price": ProductVariant.objects.filter(product_id=product_id).aggregate(
            value=Min("price_mc")
        )["value"],
        "stock": ProductVariant.objects.filter(product_id=product_id).aggregate(
            value=Sum("stock")
        )["value"] or 0,
    }


@receiver(pre_save, sender=ProductVariant)
def remember_variant_state(sender, instance, **kwargs):
    instance._notification_before = _variant_state(instance.product_id)
    instance._notification_created = instance.pk is None


@receiver(post_save, sender=ProductVariant)
def notify_variant_change(sender, instance, created, **kwargs):
    before = getattr(instance, "_notification_before", None)
    if before is None:
        return
    after = _variant_state(instance.product_id)
    action = "added" if created else None
    if before["price"] == after["price"] and before["stock"] == after["stock"] and action is None:
        return
    from django.db import transaction

    transaction.on_commit(
        lambda: _notify_favorites(
            instance.product_id,
            old_price=before["price"],
            old_stock=before["stock"],
            variant_action=action,
            variant_id=instance.pk,
        )
    )


@receiver(pre_delete, sender=ProductVariant)
def remember_deleted_variant(sender, instance, **kwargs):
    instance._notification_before_delete = _variant_state(instance.product_id)


@receiver(post_delete, sender=ProductVariant)
def notify_deleted_variant(sender, instance, **kwargs):
    before = getattr(instance, "_notification_before_delete", None)
    if before is None:
        return
    from django.db import transaction

    transaction.on_commit(
        lambda: _notify_favorites(
            instance.product_id,
            old_price=before["price"],
            old_stock=before["stock"],
            variant_action="deleted",
            variant_id=instance.pk,
        )
    )


@receiver(post_save, sender=WalletTransaction)
def notify_wallet_transaction(sender, instance, created, **kwargs):
    if not created:
        return
    amount = abs(instance.amount_mc)
    if instance.kind == "coin_issue":
        text = f"Вам нараховано {amount} MC."
    elif instance.kind == "frozen_release":
        text = f"Виплата {amount} MC стала доступною."
    elif instance.kind == "deposit_return":
        text = f"Повернення застави {amount} MC заморожено."
    elif instance.kind == "refund":
        text = f"Повернення коштів: {amount} MC."
    elif instance.kind == "debt_add":
        text = f"Нараховано борг {amount} MC."
    elif instance.kind == "debt_pay":
        text = f"Погашено борг на {amount} MC."
    else:
        return
    payload = {"transaction_id": instance.pk}
    url = "/pages/profile.html"
    if instance.order_id:
        payload["order_id"] = instance.order_id
        url = f"/pages/orders.html?order={instance.order_id}"
    notify(instance.wallet.user, "wallet", instance.kind, text, payload, url=url)


@receiver(post_save, sender=Complaint)
def notify_complaint_admin_pool(sender, instance, created, **kwargs):
    if created:
        notify_admin_digest(
            "complaints",
            f"Нова скарга №{instance.pk}.",
            {"event_id": instance.pk},
            "/pages/admin/complaints.html",
        )


@receiver(post_save, sender=SupportMessage)
def notify_support_message(sender, instance, created, **kwargs):
    if not created:
        return
    ticket = instance.ticket
    if instance.from_side == "admin":
        if ticket.user_id:
            notify(
                ticket.user,
                "support",
                "support_reply",
                f"Адміністратор відповів у зверненні №{ticket.pk}.",
                {"ticket_id": ticket.pk, "message_id": instance.pk},
                url=f"/pages/support.html?ticket={ticket.pk}",
            )
        return
    notify_admin_digest(
        "support",
        f"Нове повідомлення у зверненні №{ticket.pk}.",
        {"event_id": instance.pk, "ticket_id": ticket.pk},
        "/pages/admin/support.html",
    )


@receiver(pre_save, sender=SupportTicket)
def remember_support_status(sender, instance, **kwargs):
    instance._notification_old_status = None
    if instance.pk:
        instance._notification_old_status = (
            SupportTicket.objects.filter(pk=instance.pk).values_list("status", flat=True).first()
        )


@receiver(post_save, sender=SupportTicket)
def notify_support_status(sender, instance, created, **kwargs):
    old_status = getattr(instance, "_notification_old_status", None)
    if created or old_status is None or old_status == instance.status or instance.user_id is None:
        return
    notify(
        instance.user,
        "support",
        "support_status",
        f"Статус звернення №{instance.pk} змінено: {instance.get_status_display()}.",
        {"ticket_id": instance.pk, "status": instance.status},
        url=f"/pages/support.html?ticket={instance.pk}",
    )


@receiver(pre_save, sender=Ban)
def remember_ban_state(sender, instance, **kwargs):
    instance._notification_old_ban = None
    if instance.pk:
        instance._notification_old_ban = (
            Ban.objects.filter(pk=instance.pk)
            .values("lifted_at", "ends_at")
            .first()
        )


@receiver(pre_save, sender=UserProfile)
def remember_complaint_block(sender, instance, **kwargs):
    instance._notification_old_complaint_block = None
    if instance.pk:
        instance._notification_old_complaint_block = (
            UserProfile.objects.filter(pk=instance.pk)
            .values_list("complaints_blocked_until", flat=True)
            .first()
        )


@receiver(post_save, sender=UserProfile)
def notify_complaint_block(sender, instance, created, **kwargs):
    old_until = getattr(instance, "_notification_old_complaint_block", None)
    if created or old_until == instance.complaints_blocked_until:
        return
    if instance.complaints_blocked_until and instance.complaints_blocked_until > timezone.now():
        notify(
            instance.user,
            "moderation",
            "complaint_submission_blocked",
            f"Подання скарг обмежено до {instance.complaints_blocked_until.strftime('%d.%m.%Y %H:%M')}.",
            {},
            url="/pages/complaints.html",
        )
    elif old_until is not None and instance.complaints_blocked_until is None:
        notify(
            instance.user,
            "moderation",
            "complaint_submission_restored",
            "Можливість подавати скарги відновлено.",
            {},
            url="/pages/complaints.html",
        )


@receiver(post_save, sender=Ban)
def notify_ban_change(sender, instance, created, **kwargs):
    old = getattr(instance, "_notification_old_ban", None)
    active = instance.lifted_at is None and (instance.ends_at is None or instance.ends_at > timezone.now())
    was_active = bool(
        old
        and old["lifted_at"] is None
        and (old["ends_at"] is None or old["ends_at"] > timezone.now())
    )
    if created and active:
        until = (
            f" до {instance.ends_at.strftime('%d.%m.%Y %H:%M')}"
            if instance.ends_at
            else " безстроково"
        )
        text = f"Акаунт заблоковано{until}. Причина: {instance.reason or 'не вказана'}"
        notify(
            instance.user,
            "moderation",
            "user_banned",
            text,
            {"ban_id": instance.pk},
            url="/pages/support.html",
        )
    elif old and was_active and not active and instance.lifted_at is not None:
        notify(
            instance.user,
            "moderation",
            "user_unbanned",
            "Блокування акаунта знято.",
            {"ban_id": instance.pk},
            url="/pages/support.html",
        )


@receiver(post_save, sender=AdminRight)
def notify_admin_right_changed(sender, instance, created, **kwargs):
    if not created:
        return
    notify(
        instance.user,
        "moderation",
        "admin_right_added",
        f"Ваше право адміністратора «{instance.get_right_display()}» додано.",
        {"right": instance.right},
        url="/pages/admin/cabinet.html",
    )


@receiver(post_delete, sender=AdminRight)
def notify_admin_right_removed(sender, instance, **kwargs):
    notify(
        instance.user,
        "moderation",
        "admin_right_removed",
        f"Право адміністратора «{instance.get_right_display()}» знято.",
        {"right": instance.right},
        url="/pages/admin/cabinet.html",
    )


@receiver(pre_save, sender=CustomUser)
def remember_user_role(sender, instance, **kwargs):
    instance._notification_old_role = None
    if instance.pk:
        instance._notification_old_role = (
            CustomUser.objects.filter(pk=instance.pk).values_list("role", flat=True).first()
        )


@receiver(post_save, sender=CustomUser)
def notify_user_role_change(sender, instance, created, **kwargs):
    old_role = getattr(instance, "_notification_old_role", None)
    if created or old_role is None or old_role == instance.role:
        return
    notify(
        instance,
        "moderation",
        "admin_role_changed",
        "Рівень адміністратора вашого акаунта змінено.",
        {"role": instance.role},
        url="/pages/profile.html",
    )


@receiver(post_save, sender=SellerVerification)
def notify_seller_verification(sender, instance, created, **kwargs):
    if created:
        notify(
            instance.user,
            "moderation",
            "seller_verification",
            "Верифікацію продавця завершено.",
            {"verified": instance.age >= 18},
            url="/pages/profile.html",
        )


@receiver(pre_save, sender=PolicyVersion)
def remember_policy_version(sender, instance, **kwargs):
    instance._notification_was_current = bool(
        instance.pk
        and PolicyVersion.objects.filter(pk=instance.pk, is_current=True).exists()
    )


@receiver(post_save, sender=PolicyVersion)
def notify_new_policy(sender, instance, created, **kwargs):
    if not instance.is_current or (not created and getattr(instance, "_notification_was_current", False)):
        return
    for user in CustomUser.objects.filter(is_active=True).iterator():
        notify(
            user,
            "moderation",
            "policy_updated",
            f"Опубліковано нову редакцію правил №{instance.number}.",
            {"version": instance.number},
            url="/pages/policy.html",
        )


def notify_expired_bans():
    now = timezone.now()
    expired = Ban.objects.filter(
        lifted_at__isnull=True,
        ends_at__lte=now,
        expired_notified_at__isnull=True,
    ).select_related("user")
    for ban in expired.iterator():
        updated = Ban.objects.filter(
            pk=ban.pk,
            expired_notified_at__isnull=True,
        ).update(expired_notified_at=now)
        if updated:
            notify(
                ban.user,
                "moderation",
                "user_ban_expired",
                "Строк блокування акаунта завершився.",
                {"ban_id": ban.pk},
                url="/pages/support.html",
            )
