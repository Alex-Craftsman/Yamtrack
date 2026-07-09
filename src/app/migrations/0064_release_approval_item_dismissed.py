from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0063_release_approval_grab_state"),
    ]

    operations = [
        migrations.AddField(
            model_name="releaseapprovalitem",
            name="dismissed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
