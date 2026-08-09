from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies: list[tuple[str, str]] = []

    operations = [
        migrations.CreateModel(
            name="OperationalCheckState",
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
                ("key", models.CharField(max_length=100, unique=True)),
                ("label", models.CharField(max_length=200)),
                (
                    "status",
                    models.CharField(
                        choices=[("healthy", "Healthy"), ("failed", "Failed")],
                        max_length=10,
                    ),
                ),
                ("message", models.TextField(blank=True)),
                ("checked_at", models.DateTimeField()),
                ("changed_at", models.DateTimeField()),
            ],
        )
    ]
