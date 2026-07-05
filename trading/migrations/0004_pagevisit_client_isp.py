from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("trading", "0003_pagevisit_ip_country"),
    ]

    operations = [
        migrations.AddField(
            model_name="pagevisit",
            name="client_isp",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
    ]
