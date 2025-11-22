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

    class Meta:
        model = BasicMedia
        fields = "__all__"

    def get_item_id(self, obj):
        item = getattr(obj, "item", None)
        if not item:
            return None
        return f"{item.media_type}/{item.source}/{item.media_id}"


class EventSerializer(serializers.ModelSerializer):
    """Serializer used for calendar events."""

    item = ItemSerializer()

    item_id = serializers.SerializerMethodField()

    class Meta:
        model = Event
        fields = "__all__"

    def get_item_id(self, obj):
        item = getattr(obj, "item", None)
        if not item:
            return None
        return f"{item.media_type}/{item.source}/{item.media_id}"


class TimelineItemSerializer(serializers.ModelSerializer):
    """Compact serializer used for timeline entries to reduce payload size."""

    item_id = serializers.SerializerMethodField()
    title = serializers.SerializerMethodField()
    image = serializers.SerializerMethodField()
    media_type = serializers.SerializerMethodField()
    source = serializers.SerializerMethodField()

    class Meta:
        model = BasicMedia
        fields = "__all__"

    def get_item_id(self, obj):
        item = getattr(obj, "item", None)
        if not item:
            return None
        return f"{item.media_type}/{item.source}/{item.media_id}"

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
