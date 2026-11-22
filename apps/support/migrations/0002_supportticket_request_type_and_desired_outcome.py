from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("support", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="supportticket",
            name="request_type",
            field=models.CharField(
                choices=[
                    ("unban", "Розбан або оскарження блокування"),
                    ("bug", "Виправлення помилки"),
                    ("account", "Проблема з акаунтом"),
                    ("payment", "Оплата або баланс MC"),
                    ("order", "Замовлення або доставка"),
                    ("product", "Товар або продавець"),
                    ("policy", "Питання щодо правил"),
                    ("suggestion", "Пропозиція або побажання"),
                    ("other", "Інше"),
                ],
                default="other",
                max_length=24,
                verbose_name="Тип звернення",
            ),
        ),
        migrations.AddField(
            model_name="supportticket",
            name="desired_outcome",
            field=models.TextField(
                blank=True, default="", verbose_name="Бажаний результат"
            ),
        ),
    ]
