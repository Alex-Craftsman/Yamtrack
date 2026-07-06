import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0061_episode_item_not_null"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ReleaseApprovalItem",
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
                (
                    "media_type",
                    models.CharField(
                        choices=[("movie", "Movie"), ("tv", "TV")],
                        default="movie",
                        max_length=10,
                    ),
                ),
                ("seerr_request_id", models.PositiveIntegerField(unique=True)),
                ("tmdb_id", models.PositiveIntegerField()),
                ("title", models.TextField()),
                ("year", models.PositiveIntegerField(blank=True, null=True)),
                ("seerr_status", models.CharField(max_length=80)),
                ("radarr_movie_id", models.PositiveIntegerField(blank=True, null=True)),
                ("has_file", models.BooleanField(default=False)),
                ("request_data", models.JSONField()),
                ("movie_data", models.JSONField(default=dict)),
                ("synced_at", models.DateTimeField(auto_now=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="ReleaseApprovalCandidate",
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
                ("identity", models.TextField()),
                ("title", models.TextField()),
                ("indexer", models.CharField(blank=True, max_length=255)),
                ("info_url", models.URLField(blank=True)),
                ("quality", models.CharField(blank=True, max_length=80)),
                ("size", models.BigIntegerField(default=0)),
                ("seeders", models.IntegerField(default=0)),
                ("score", models.IntegerField()),
                ("verdict", models.CharField(max_length=40)),
                ("score_reasons", models.JSONField(default=list)),
                ("score_warnings", models.JSONField(default=list)),
                ("release_data", models.JSONField()),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("approved", "Approved"),
                            ("rejected", "Rejected"),
                        ],
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("approved_at", models.DateTimeField(blank=True, null=True)),
                ("synced_at", models.DateTimeField(auto_now=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "approved_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "item",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="candidates",
                        to="app.releaseapprovalitem",
                    ),
                ),
            ],
            options={
                "ordering": ["-score", "title"],
            },
        ),
        migrations.AddIndex(
            model_name="releaseapprovalitem",
            index=models.Index(
                fields=["media_type", "tmdb_id"],
                name="app_release_media_t_25f8da_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="releaseapprovalitem",
            index=models.Index(
                fields=["synced_at"],
                name="app_release_synced__554805_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="releaseapprovalcandidate",
            index=models.Index(
                fields=["status", "-score"],
                name="app_release_status_58c9a5_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="releaseapprovalcandidate",
            index=models.Index(
                fields=["synced_at"],
                name="app_release_synced__1217de_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="releaseapprovalcandidate",
            constraint=models.UniqueConstraint(
                fields=("item", "identity"),
                name="unique_release_approval_candidate_identity",
            ),
        ),
    ]
