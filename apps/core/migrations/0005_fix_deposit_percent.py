from decimal import Decimal

from django.db import migrations, models


def fix_deposit_percent(apps, schema_editor):
    SiteSettings = apps.get_model("core", "SiteSettings")
    SiteSettings.objects.filter(deposit_percent=Decimal("3")).update(
        deposit_percent=Decimal("1")
    )

    SiteBlock = apps.get_model("core", "SiteBlock")
    for block in SiteBlock.objects.all():
        new_text = block.text.replace(
            "застава 3% від суми товарів",
            "застава 1% від загальної вартості товарів продавця",
        )
        new_text = new_text.replace(
            "застава 3%",
            "застава 1%",
        )
        if new_text != block.text:
            block.text = new_text
            block.save(update_fields=["text"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0004_update_collateral_copy"),
    ]

    operations = [
        migrations.RunPython(
            fix_deposit_percent,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="sitesettings",
            name="deposit_percent",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("1"),
                max_digits=5,
                verbose_name="Застава, %",
            ),
        ),
    ]
