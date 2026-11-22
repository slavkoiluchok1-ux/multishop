import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="TelegramBotState",
            fields=[
                (
                    "id",
                    models.PositiveSmallIntegerField(
                        default=1,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("last_update_id", models.BigIntegerField(default=0)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name="TelegramLink",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("chat_id", models.BigIntegerField(blank=True, null=True, unique=True)),
                (
                    "telegram_user_id",
                    models.BigIntegerField(blank=True, null=True, unique=True),
                ),
                ("telegram_username", models.CharField(blank=True, max_length=64)),
                (
                    "connect_code_hash",
                    models.CharField(blank=True, db_index=True, max_length=64),
                ),
                ("connect_code_expires_at", models.DateTimeField(blank=True, null=True)),
                ("last_notification_id", models.PositiveBigIntegerField(default=0)),
                ("connected_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="telegram_link",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
    ]
