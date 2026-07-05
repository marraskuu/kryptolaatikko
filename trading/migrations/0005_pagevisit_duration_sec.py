from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("trading", "0004_pagevisit_client_isp"),
    ]

    operations = [
        migrations.AddField(
            model_name="pagevisit",
            name="duration_sec",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
    ]
