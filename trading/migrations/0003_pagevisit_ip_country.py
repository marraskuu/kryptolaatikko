from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("trading", "0002_pagevisit"),
    ]

    operations = [
        migrations.AddField(
            model_name="pagevisit",
            name="client_ip",
            field=models.GenericIPAddressField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="pagevisit",
            name="country_code",
            field=models.CharField(blank=True, db_index=True, default="", max_length=2),
        ),
    ]
