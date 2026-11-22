from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0002_alter_variantattributevalue_unique_together_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="portfolio_url",
            field=models.URLField(
                blank=True,
                max_length=500,
                verbose_name="Посилання на портфоліо",
            ),
        ),
    ]
