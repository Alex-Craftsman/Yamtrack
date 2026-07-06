import contextlib

from django.apps import apps
from django.contrib import admin
from django.contrib.admin.sites import AlreadyRegistered

from app.models import (
    Episode,
    Item,
    ReleaseApprovalCandidate,
    ReleaseApprovalItem,
    UserMessage,
)


# Custom ModelAdmin classes with search functionality
@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    """Custom admin for Item model with search and filter options."""

    search_fields = ["title", "media_id", "source"]
    list_display = [
        "title",
        "media_id",
        "season_number",
        "episode_number",
        "media_type",
        "source",
    ]
    list_filter = ["media_type", "source"]


@admin.register(Episode)
class EpisodeAdmin(admin.ModelAdmin):
    """Custom admin for Episode model with search and filter options."""

    search_fields = ["item__title", "related_season__item__title"]
    list_display = ["__str__", "end_date"]


@admin.register(UserMessage)
class UserMessageAdmin(admin.ModelAdmin):
    """Custom admin for persistent user messages."""

    search_fields = ["user__username", "message"]
    list_display = ["message", "level", "user", "created_at", "shown_at"]
    list_filter = ["level", "shown_at"]


@admin.register(ReleaseApprovalItem)
class ReleaseApprovalItemAdmin(admin.ModelAdmin):
    """Admin for synced release approval requests."""

    search_fields = ["title", "tmdb_id", "seerr_request_id"]
    list_display = [
        "title",
        "media_type",
        "tmdb_id",
        "seerr_request_id",
        "seerr_status",
        "has_file",
        "synced_at",
    ]
    list_filter = ["media_type", "has_file", "seerr_status"]


@admin.register(ReleaseApprovalCandidate)
class ReleaseApprovalCandidateAdmin(admin.ModelAdmin):
    """Admin for stored release candidates."""

    search_fields = ["title", "indexer", "item__title", "item__tmdb_id"]
    list_display = [
        "title",
        "item",
        "indexer",
        "quality",
        "score",
        "verdict",
        "status",
        "approved_at",
    ]
    list_filter = ["status", "verdict", "indexer", "quality"]


class MediaAdmin(admin.ModelAdmin):
    """Custom admin for regular media model with search and filter options."""

    search_fields = ["item__title", "user__username", "notes"]
    list_display = ["__str__", "status", "score", "user"]
    list_filter = ["status"]


# Register models with custom admin classes


# Auto-register remaining models
app_models = apps.get_app_config("app").get_models()
SpecialModels = [
    "Item",
    "Episode",
    "BasicMedia",
    "UserMessage",
    "ReleaseApprovalItem",
    "ReleaseApprovalCandidate",
]
for model in app_models:
    if (
        not model.__name__.startswith("Historical")
        and model.__name__ not in SpecialModels
    ):
        with contextlib.suppress(AlreadyRegistered):
            admin.site.register(model, MediaAdmin)
