from django.db import migrations


DELIVERY_OPTIONS = ("Ключ", "Код активації", "Акаунт", "Посилання-активація")


def add_subscription_delivery(apps, schema_editor):
    database = schema_editor.connection.alias
    Category = apps.get_model("catalog", "Category")
    Attribute = apps.get_model("catalog", "Attribute")
    AttributeOption = apps.get_model("catalog", "AttributeOption")

    subscription_category = Category.objects.using(database).filter(
        path="Підписки",
        parent__isnull=True,
    ).first()
    if subscription_category is not None:
        delivery, _ = Attribute.objects.using(database).update_or_create(
            category_id=subscription_category.pk,
            name="Видача",
            defaults={"kind": "single", "unit": "", "required": True, "order": 99},
        )
        for value in DELIVERY_OPTIONS:
            AttributeOption.objects.using(database).get_or_create(
                attribute_id=delivery.pk,
                value=value,
            )

    for delivery in Attribute.objects.using(database).filter(name="Видача"):
        AttributeOption.objects.using(database).get_or_create(
            attribute_id=delivery.pk,
            value="Код активації",
        )


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0005_product_product_status_mod_created_idx"),
    ]

    operations = [
        migrations.RunPython(add_subscription_delivery, migrations.RunPython.noop),
    ]
