from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.policy.models import PolicyVersion


class Command(BaseCommand):
    help = "Створює й публікує початкову редакцію політики, якщо редакцій ще немає."

    def handle(self, *args, **options):
        if PolicyVersion.objects.exists():
            first = PolicyVersion.objects.filter(number=1).first()
            second = PolicyVersion.objects.filter(number=2).first()
            first_text = (first.text if first is not None else "").strip()
            is_placeholder = (
                len(first_text) < 1000
                or "ТИМЧАСОВИЙ ТЕКСТ-ЗАГЛУШКА" in first_text.upper()
                or "заглуш" in first_text.casefold()
            )
            if first is not None and second is None and is_placeholder:
                text = self.load_full_policy()
                with transaction.atomic():
                    PolicyVersion.objects.update(is_current=False)
                    PolicyVersion.objects.update_or_create(
                        number=2,
                        defaults={"text": text, "is_current": True},
                    )
                self.stdout.write(self.style.SUCCESS("Створено повну редакцію №2 замість заглушки №1."))
            else:
                self.stdout.write("Повна редакція політики вже налаштована; bootstrap пропущено.")
            return

        text = self.load_full_policy()

        with transaction.atomic():
            PolicyVersion.objects.filter(is_current=True).update(is_current=False)
            PolicyVersion.objects.update_or_create(
                number=1,
                defaults={"text": text, "is_current": True},
            )

        self.stdout.write(self.style.SUCCESS("Початкову політику опубліковано."))

    def load_full_policy(self):
        policy_path = Path(settings.BASE_DIR) / "seed" / "policy_full.txt"
        if policy_path.is_file():
            return policy_path.read_text(encoding="utf-8")
        return (
            "Політика платформи MultiShop\n\n"
            "Користувачі платформи погоджуються дотримуватися правил "
            "торгівлі, розрахунків, безпеки та розгляду скарг."
        )
