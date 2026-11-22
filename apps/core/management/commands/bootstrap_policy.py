from django.core.management.base import BaseCommand
from django.db import transaction

from apps.policy.models import PolicyVersion


class Command(BaseCommand):
    help = "Створює й публікує початкову редакцію політики, якщо редакцій ще немає."

    def handle(self, *args, **options):
        if PolicyVersion.objects.exists():
            self.stdout.write("Редакція політики вже існує; bootstrap пропущено.")
            return

        text = (
            "Політика платформи MultiShop\n\n"
            "Користувачі платформи погоджуються дотримуватися правил "
            "торгівлі, розрахунків, безпеки та розгляду скарг."
        )

        with transaction.atomic():
            PolicyVersion.objects.filter(is_current=True).update(is_current=False)
            PolicyVersion.objects.update_or_create(
                number=1,
                defaults={"text": text, "is_current": True},
            )

        self.stdout.write(self.style.SUCCESS("Початкову політику опубліковано."))
