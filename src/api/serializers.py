from rest_framework import serializers

from app.models import BasicMedia, Item
from events.models import Event

HISTORY_TYPE_MAP = {
    "+": 1,
    "~": 2,
    "-": 3,
}

MEDIA_STATUS_MAP = {
    "Planning": 0,
    "In progress": 1,
    "Paused": 2,
    "Completed": 3,
    "Dropped": 4,
}


def _build_item_id(item):
    """Generate item_id string from item object."""
    if not item:
        return None
    media_type = item.media_type
    children = ""

    if item.media_type == "season" and hasattr(item, "season_number"):
        children = f"/{item.season_number}"
        media_type = "tv"
    elif (
        item.media_type == "episode"
        and hasattr(item, "season_number")
        and hasattr(item, "episode_number")
    ):
        children = f"/{item.season_number}/{item.episode_number}"
        media_type = "tv"

    return f"{media_type}/{item.source}/{item.media_id}{children}"


def _build_parent_id(item):
    """Generate parent_id string for seasons and episodes."""
    if not item:
        return None
    if item.media_type == "season":
        return f"tv/{item.source}/{item.media_id}"
    if item.media_type == "episode" and hasattr(item, "season_number"):
        return f"tv/{item.source}/{item.media_id}/{item.season_number}"
    return None


class ItemSerializer(serializers.ModelSerializer):
    """Serializer used for item details."""

    class Meta:
        model = Item
        fields = "__all__"


class MediaSerializer(serializers.ModelSerializer):
    """Serializer used for media items."""

    item = ItemSerializer()
    item_id = serializers.SerializerMethodField()
    parent_id = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()

    class Meta:
        model = BasicMedia
        exclude = ("user",)

    def get_item_id(self, obj):
        """Generate the item_id string based on media type and identifiers."""
        return _build_item_id(getattr(obj, "item", None))

    def get_parent_id(self, obj):
        """Generate the parent_id string for seasons and episodes."""
        return _build_parent_id(getattr(obj, "item", None))

    def get_status(self, obj):
        """Convert status string to numeric value."""
        return MEDIA_STATUS_MAP.get(getattr(obj, "status", None))


class EpisodeSerializer(serializers.Serializer):
    """Serializer used for Episode items."""

    id = serializers.SerializerMethodField()
    item = ItemSerializer()
    item_id = serializers.SerializerMethodField()
    parent_id = serializers.SerializerMethodField()
    created_at = serializers.SerializerMethodField()
    score = serializers.SerializerMethodField()
    progress = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    start_date = serializers.SerializerMethodField()
    end_date = serializers.SerializerMethodField()
    notes = serializers.SerializerMethodField()

    class Meta:
        model = BasicMedia
        fields = "__all__"

    def get_id(self, obj):
        return getattr(obj, "id", None)

    def get_created_at(self, obj):
        return getattr(obj, "created_at", None)

    def get_score(self, obj):
        return getattr(obj, "score", None)

    def get_progress(self, obj):
        return getattr(obj, "progress", None) or 1

    def get_status(self, obj):
        return MEDIA_STATUS_MAP.get(getattr(obj, "status", None), None)

    def get_start_date(self, obj):
        return getattr(obj, "start_date", None)

    def get_end_date(self, obj):
        return getattr(obj, "end_date", None)

    def get_notes(self, obj):
        return getattr(obj, "notes", "") or ""

    def get_item_id(self, obj):
        return _build_item_id(getattr(obj, "item", None))

    def get_parent_id(self, obj):
        return _build_parent_id(getattr(obj, "item", None))


class EventSerializer(serializers.ModelSerializer):
    """Serializer used for calendar events."""

    item = ItemSerializer()
    item_id = serializers.SerializerMethodField()
    parent_id = serializers.SerializerMethodField()

    class Meta:
        model = Event
        fields = "__all__"

    def get_item_id(self, obj):
        return _build_item_id(getattr(obj, "item", None))

    def get_parent_id(self, obj):
        return _build_parent_id(getattr(obj, "item", None))


class HistoryEntrySerializer(serializers.Serializer):
    """Serializer for historical records snapshot."""

    # TODO?: is it possible to get the media_id of the history entry related media?

    def to_representation(self, instance):
        """Return essential fields from history entry."""
        history_type = getattr(instance, "history_type", None)
        type_numeric = HISTORY_TYPE_MAP.get(history_type, 0) if history_type else 0

        return {
            "id": instance.history_id if hasattr(instance, "history_id") else None,
            "timestamp": instance.history_date
            if hasattr(instance, "history_date")
            else None,
            "type": type_numeric,
            "change_reason": instance.history_change_reason
            if hasattr(instance, "history_change_reason")
            else None,
            "score": instance.score if hasattr(instance, "score") else None,
            "progress": instance.progress if hasattr(instance, "progress") else None,
            "status": MEDIA_STATUS_MAP.get(instance.status, None)
            if hasattr(instance, "status")
            else None,
            "start_date": instance.start_date
            if hasattr(instance, "start_date")
            else None,
            "end_date": instance.end_date if hasattr(instance, "end_date") else None,
            "notes": instance.notes if hasattr(instance, "notes") else None,
        }


class TimelineItemSerializer(serializers.ModelSerializer):
    """Compact serializer used for timeline entries to reduce payload size."""

    item_id = serializers.SerializerMethodField()
    parent_id = serializers.SerializerMethodField()
    title = serializers.SerializerMethodField()
    image = serializers.SerializerMethodField()
    media_type = serializers.SerializerMethodField()
    source = serializers.SerializerMethodField()

    class Meta:
        model = BasicMedia
        exclude = ("user",)

    def get_item_id(self, obj):
        return _build_item_id(getattr(obj, "item", None))

    def get_parent_id(self, obj):
        return _build_parent_id(getattr(obj, "item", None))

    def get_title(self, obj):
        item = getattr(obj, "item", None)
        return getattr(item, "title", None) if item else None

    def get_image(self, obj):
        item = getattr(obj, "item", None)
        return getattr(item, "image", None) if item else None

    def get_media_type(self, obj):
        item = getattr(obj, "item", None)
        return getattr(item, "media_type", None) if item else None

    def get_source(self, obj):
        item = getattr(obj, "item", None)
        return getattr(item, "source", None) if item else None
