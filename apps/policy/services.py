"""Допоміжні функції політики."""
from .models import PolicyAcceptance, PolicyVersion


def current_version():
    """Поточна редакція політики або None."""
    return PolicyVersion.objects.filter(is_current=True).first()


def accepted_version(user):
    """Остання прийнята користувачем редакція або None."""
    acceptance = (
        PolicyAcceptance.objects.filter(user=user).select_related("version").order_by("-accepted_at").first()
    )
    return acceptance.version if acceptance else None


def has_accepted_current(user):
    """Чи прийняв користувач поточну редакцію."""
    if not user or not user.is_authenticated:
        return True
    current = current_version()
    if current is None:
        return True
    return PolicyAcceptance.objects.filter(user=user, version=current).exists()
