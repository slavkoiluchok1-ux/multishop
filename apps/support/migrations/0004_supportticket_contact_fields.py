from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("support", "0003_supportticket_target_username"),
    ]

    operations = [
        migrations.AddField(
            model_name="supportticket",
            name="contact_phone",
            field=models.CharField(blank=True, max_length=32, verbose_name="Телефон"),
        ),
        migrations.AddField(
            model_name="supportticket",
            name="contact_telegram",
            field=models.CharField(blank=True, max_length=64, verbose_name="Telegram"),
        ),
        migrations.AddField(
            model_name="supportticket",
            name="contact_instagram",
            field=models.CharField(blank=True, max_length=64, verbose_name="Instagram"),
        ),
        migrations.AddField(
            model_name="supportticket",
            name="contact_viber",
            field=models.CharField(blank=True, max_length=32, verbose_name="Viber"),
        ),
        migrations.AddField(
            model_name="supportticket",
            name="contact_facebook",
            field=models.CharField(blank=True, max_length=128, verbose_name="Facebook"),
        ),
        migrations.AddField(
            model_name="supportticket",
            name="contact_whatsapp",
            field=models.CharField(blank=True, max_length=32, verbose_name="WhatsApp"),
        ),
    ]
