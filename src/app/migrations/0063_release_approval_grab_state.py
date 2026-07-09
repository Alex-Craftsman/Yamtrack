from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0062_release_approval"),
    ]

    operations = [
        migrations.AddField(
            model_name="releaseapprovalcandidate",
            name="grab_error",
            field=models.TextField(blank=True),
        ),
        migrations.AlterField(
            model_name="releaseapprovalcandidate",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("grabbing", "Grabbing"),
                    ("approved", "Approved"),
                    ("failed", "Failed"),
                    ("rejected", "Rejected"),
                ],
                default="pending",
                max_length=20,
            ),
        ),
    ]
