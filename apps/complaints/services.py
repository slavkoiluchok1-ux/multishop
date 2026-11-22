"""Службові операції скарг: облік хибних скарг і блокування подачі.

Поля лічильників уже є в UserProfile (false_complaints_count,
complaints_blocked_until, complaints_block_count) — дублів не створюємо.
"""
from datetime import datetime, timedelta

from django.utils import timezone

from apps.accounts.models import CustomUser, UserProfile
from apps.core.models import SiteSettings


def _profile(user: CustomUser) -> UserProfile:
    """Профіль користувача, який створюється сигналом разом із користувачем."""
    profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile


def complaint_block_status(user: CustomUser) -> tuple[bool, datetime | None]:
    """(чи заблоковано, дата завершення) — для подачі й відображення."""
    profile = _profile(user)
    until = profile.complaints_blocked_until
    blocked = bool(until and until > timezone.now())
    return blocked, (until if blocked else None)


def register_false_complaint(user: CustomUser) -> UserProfile:
    """Зарахувати хибну скаргу автору.

    Після більш ніж false_complaints_limit хибних скарг подача блокується на
    complaints_block_first_days днів, при повторному порушенні — на
    complaints_block_repeat_days. Після блокування лічильник обнуляється.
    """
    profile = _profile(user)
    settings_row = SiteSettings.get_solo()
    profile.false_complaints_count += 1
    update_fields = ["false_complaints_count"]
    if profile.false_complaints_count > settings_row.false_complaints_limit:
        days = (
            settings_row.complaints_block_first_days
            if profile.complaints_block_count == 0
            else settings_row.complaints_block_repeat_days
        )
        profile.complaints_block_count += 1
        profile.complaints_blocked_until = timezone.now() + timedelta(days=days)
        profile.false_complaints_count = 0
        update_fields += ["complaints_block_count", "complaints_blocked_until"]
    profile.save(update_fields=update_fields)
    return profile


def lift_complaint_block(user: CustomUser) -> UserProfile:
    """Зняти блокування подачі скарг і обнулити лічильник хибних."""
    profile = _profile(user)
    profile.complaints_blocked_until = None
    profile.false_complaints_count = 0
    profile.save(update_fields=["complaints_blocked_until", "false_complaints_count"])
    return profile
