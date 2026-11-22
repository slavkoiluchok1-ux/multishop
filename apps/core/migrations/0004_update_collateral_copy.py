from django.db import migrations


def update_collateral_copy(apps, schema_editor):
    SiteBlock = apps.get_model("core", "SiteBlock")
    for block in SiteBlock.objects.filter(text__contains="застава 1% від суми товарів"):
        block.text = block.text.replace(
            "застава 1% від суми товарів",
            "застава 3% від суми товарів",
        )
        block.save(update_fields=["text"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0003_siteblock_eyebrow_siteblock_layout_siteblock_style"),
    ]

    operations = [
        migrations.RunPython(update_collateral_copy, migrations.RunPython.noop),
    ]
