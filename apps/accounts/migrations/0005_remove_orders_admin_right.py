from django.db import migrations, models


def move_orders_to_complaints(apps, schema_editor):
    """Право «orders» прибрано: нагляд за замовленнями перейшов до «complaints».

    Наявні записи переносимо на «complaints», щоб адмін не втратив можливість
    обробляти скарги на замовлення. Якщо «complaints» уже є — дублікат видаляємо.
    """
    AdminRight = apps.get_model("accounts", "AdminRight")
    for row in AdminRight.objects.filter(right="orders"):
        if AdminRight.objects.filter(user_id=row.user_id, right="complaints").exists():
            row.delete()
        else:
            row.right = "complaints"
            row.save(update_fields=["right"])


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_alter_customuser_managers"),
    ]

    operations = [
        migrations.AlterField(
            model_name="adminright",
            name="right",
            field=models.CharField(
                choices=[
                    ("home", "Головна сторінка"),
                    ("products", "Товари"),
                    ("support", "Підтримка"),
                    ("complaints", "Скарги"),
                    ("bans", "Бани"),
                ],
                max_length=20,
                verbose_name="Право",
            ),
        ),
        migrations.RunPython(move_orders_to_complaints, migrations.RunPython.noop),
    ]
