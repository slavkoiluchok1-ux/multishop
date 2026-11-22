"""Міграція: додаємо поле target_username у SupportTicket."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("support", "0002_supportticket_request_type_and_desired_outcome"),
    ]

    operations = [
        migrations.AddField(
            model_name="supportticket",
            name="target_username",
            field=models.CharField(
                blank=True, default="", max_length=150, verbose_name="Нік акаунта"
            ),
        ),
    ]
