from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("trading", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="PageVisit",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("visited_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("path", models.CharField(default="/", max_length=200)),
                ("referer", models.CharField(blank=True, max_length=512)),
                (
                    "referer_source",
                    models.CharField(db_index=True, default="direct", max_length=64),
                ),
                ("referer_host", models.CharField(blank=True, max_length=128)),
                ("user_agent", models.CharField(blank=True, max_length=256)),
                ("ip_hash", models.CharField(db_index=True, max_length=32)),
                ("is_bot", models.BooleanField(db_index=True, default=False)),
            ],
            options={
                "verbose_name": "Sivukäynti",
                "verbose_name_plural": "Sivukäynnit",
                "ordering": ["-visited_at"],
                "indexes": [
                    models.Index(
                        fields=["-visited_at", "referer_source"],
                        name="trading_pag_visited_6a0f0d_idx",
                    ),
                ],
            },
        ),
    ]
