from django.core.management.base import BaseCommand
from django.db import transaction

from apps.core.models import SiteBlock


class Command(BaseCommand):
    help = "Ідемпотентно створює початкові блоки головної сторінки."

    def handle(self, *args, **options):
        blocks = [
            {
                "key": "what_is_multishop",
                "eyebrow": "Головна",
                "title": "Що таке MultiShop",
                "text": (
                    "MultiShop — навчальний маркетплейс для фізичних товарів, цифрових "
                    "файлів, ключів, послуг і підписок. Розрахунки ведуться в умовних "
                    "мультикоїнах MC."
                ),
                "layout": "wide",
                "style": "accent",
                "order": 10,
            },
            {
                "key": "how_to_buy",
                "eyebrow": "Для покупця",
                "title": "Як купувати",
                "text": (
                    "Обирайте товар і варіант, перевіряйте ціну та залишок, вкажіть кількість "
                    "і потрібні контакти. При оформленні кошти утримуються до підтвердження "
                    "отримання замовлення."
                ),
                "layout": "standard",
                "style": "paper",
                "order": 20,
            },
            {
                "key": "how_to_sell",
                "eyebrow": "Для продавця",
                "title": "Як продавати",
                "text": (
                    "Створіть оголошення, оберіть категорію й варіанти. Для продажу потрібна "
                    "верифікація віком від 18 років. Застава становить 1% від загальної "
                    "вартості активних і прихованих товарів продавця."
                ),
                "layout": "standard",
                "style": "paper",
                "order": 30,
            },
            {
                "key": "coins_and_commission",
                "eyebrow": "Розрахунки",
                "title": "Мультикоїни й комісія",
                "text": (
                    "MC — внутрішня умовна валюта навчального проєкту: 1 MC дорівнює 10 грн "
                    "лише у демонстраційних розрахунках. Комісія платформи становить 3%, "
                    "а застава продавця — 1%."
                ),
                "layout": "standard",
                "style": "accent",
                "order": 40,
            },
            {
                "key": "escrow_and_security",
                "eyebrow": "Безпека угод",
                "title": "Ескроу й підтвердження",
                "text": (
                    "Кошти утримуються за замовленням до підтвердження отримання. Після "
                    "підтвердження виплата продавцю заморожується на 14 днів, а скаргу на "
                    "замовлення можна подати протягом цього строку."
                ),
                "layout": "standard",
                "style": "dark",
                "order": 50,
            },
            {
                "key": "complaints_and_support",
                "eyebrow": "Допомога",
                "title": "Скарги й підтримка",
                "text": (
                    "Скаргу можна подати з замовлення, товару, профілю або коментаря. "
                    "Звичайним користувачам приховано, хто саме подав скаргу. Через "
                    "підтримку можна отримати допомогу або апелювати рішення."
                ),
                "layout": "standard",
                "style": "paper",
                "order": 60,
            },
            {
                "key": "platform_rules",
                "eyebrow": "Правила",
                "title": "Правила платформи",
                "text": (
                    "Перед користуванням прийміть чинну редакцію політики. Заборонені шахрайство, "
                    "підробки, незаконний контент і порушення прав третіх осіб."
                ),
                "layout": "standard",
                "style": "paper",
                "order": 70,
            },
        ]

        with transaction.atomic():
            for block in blocks:
                defaults = {key: value for key, value in block.items() if key != "key"}
                SiteBlock.objects.update_or_create(
                    key=block["key"],
                    defaults=defaults,
                )

        self.stdout.write(self.style.SUCCESS("Початкові блоки головної оновлено без дублікатів."))
