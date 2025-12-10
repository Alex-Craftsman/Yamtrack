from rest_framework import serializers

from app.models import BasicMedia, Item
from events.models import Event


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

    class Meta:
        model = BasicMedia
        fields = "__all__"

    def get_item_id(self, obj):
        """Generate the item_id string based on media type and identifiers."""
        item = getattr(obj, "item", None)
        if not item:
            return None
        children = ""
        media_type = item.media_type
        if item.media_type == "season" and hasattr(item, "season_number"):
            children += f"/{item.season_number}"
            media_type = "tv"

        return f"{media_type}/{item.source}/{item.media_id}{children}"

    def get_parent_id(self, obj):
        """Generate the parent_id string for seasons and episodes."""
        item = getattr(obj, "item", None)
        if not item:
            return None
        if item.media_type == "season":
            return f"tv/{item.source}/{item.media_id}"

        return None


class EpisodeSerializer(serializers.Serializer):
    """Serializer used for `Episode` items.

    Uses the same shape of `MediaSerializer` objects.
    """

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
        """Return the ID of the Episode instance."""
        return getattr(obj, "id", None) if hasattr(obj, "id") else None

    def get_created_at(self, obj):
        """Return the creation timestamp of the Episode instance."""
        return getattr(obj, "created_at", None) if hasattr(obj, "created_at") else None

    def get_score(self, obj):
        """Return the score of the episode.

        Always null since an episode cannot have its own score.
        """
        return getattr(obj, "score", None) if hasattr(obj, "score") else None

    def get_progress(self, obj):
        """Return the progress of the episode.

        Always true since an episode can be added to Yamtrack only if it's finished.
        """
        return getattr(obj, "progress", None) if hasattr(obj, "progress") else 1

    def get_status(self, obj):
        """Return the status of the episode.

        Always `Completed` since an episode can be added to Yamtrack only if it's finished.
        """
        return getattr(obj, "status", None) if hasattr(obj, "status") else "Completed"

    def get_start_date(self, obj):
        """Return the start_date of the Episode instance.

        Always null since episodes do not have a start_date.
        """
        return getattr(obj, "start_date", "") if hasattr(obj, "start_date") else None

    def get_end_date(self, obj):
        """Return the end_date of the Episode instance."""
        return getattr(obj, "end_date", None)

    def get_notes(self, obj):
        """Return the notes of the Episode instance.

        Always empty since episodes do not have notes.
        """
        return getattr(obj, "notes", "") if hasattr(obj, "notes") else ""

    def get_item_id(self, obj):
        """Generate the item_id string based on media type and identifiers."""
        item = getattr(obj, "item", None)
        if not item:
            return None
        children = ""
        media_type = item.media_type
        if (
            item.media_type == "episode"
            and hasattr(item, "season_number")
            and hasattr(
                item,
                "episode_number",
            )
        ):
            children += f"/{item.season_number}/{item.episode_number}"
            media_type = "tv"
        return f"{media_type}/{item.source}/{item.media_id}{children}"

    def get_parent_id(self, obj):
        """Generate the parent_id string for seasons and episodes."""
        item = getattr(obj, "item", None)
        if not item:
            return None
        if item.media_type == "episode":
            if hasattr(item, "season_number"):
                return f"tv/{item.source}/{item.media_id}/{item.season_number}"
            return None
        return None


class EventSerializer(serializers.ModelSerializer):
    """Serializer used for calendar events."""

    item = ItemSerializer()

    item_id = serializers.SerializerMethodField()
    parent_id = serializers.SerializerMethodField()

    class Meta:
        model = Event
        fields = "__all__"

    def get_item_id(self, obj):
        """Generate the item_id string based on media type and identifiers."""
        item = getattr(obj, "item", None)
        if not item:
            return None
        children = ""
        media_type = item.media_type
        if item.media_type == "season" and hasattr(item, "season_number"):
            children += f"/{item.season_number}"
            media_type = "tv"
        return f"{media_type}/{item.source}/{item.media_id}{children}"

    def get_parent_id(self, obj):
        """Generate the parent_id string for seasons and episodes."""
        item = getattr(obj, "item", None)
        if not item:
            return None
        if item.media_type == "season":
            return f"tv/{item.source}/{item.media_id}"
        return None


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
        fields = "__all__"

    def get_item_id(self, obj):
        """Generate the item_id string based on media type and identifiers."""
        item = getattr(obj, "item", None)
        if not item:
            return None
        children = ""
        media_type = item.media_type
        if item.media_type == "season" and hasattr(item, "season_number"):
            children += f"/{item.season_number}"
            media_type = "tv"
        return f"{media_type}/{item.source}/{item.media_id}{children}"

    def get_parent_id(self, obj):
        """Generate the parent_id string for seasons and episodes."""
        item = getattr(obj, "item", None)
        if not item:
            return None
        if item.media_type == "season":
            return f"tv/{item.source}/{item.media_id}"
        return None

    def get_title(self, obj):
        item = getattr(obj, "item", None)
        return getattr(item, "title", None) if item is not None else None

    def get_image(self, obj):
        item = getattr(obj, "item", None)
        return getattr(item, "image", None) if item is not None else None

    def get_media_type(self, obj):
        item = getattr(obj, "item", None)
        return getattr(item, "media_type", None) if item is not None else None

    def get_source(self, obj):
        item = getattr(obj, "item", None)
        return getattr(item, "source", None) if item is not None else None
