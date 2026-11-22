from django.db import migrations


REMOVED_DELIVERY_OPTIONS = ("Акаунт", "Посилання-активація")


def remove_account_and_link_delivery(apps, schema_editor):
    database = schema_editor.connection.alias
    AttributeOption = apps.get_model("catalog", "AttributeOption")
    AttributeOption.objects.using(database).filter(
        attribute__name="Видача",
        value__in=REMOVED_DELIVERY_OPTIONS,
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0006_subscription_activation_delivery"),
    ]

    operations = [
        migrations.RunPython(
            remove_account_and_link_delivery,
            migrations.RunPython.noop,
        ),
    ]
