from rest_framework import serializers

from app.models import (
    TV,
    Anime,
    BasicMedia,
    Book,
    Comic,
    Episode,
    Game,
    Item,
    Manga,
    MediaTypes,
    Movie,
    Season,
)
from events.models import Event

from .helpers import build_item_id, build_parent_id, get_http_message, get_media_status
from .history_processor import get_changes_from_diff, get_changes_from_new_record


class ItemIdField(serializers.Field):
    """Custom field to generate item_id string."""

    def to_representation(self, item):  # noqa: D102
        return build_item_id(item)


class ParentIdField(serializers.Field):
    """Custom field to generate parent_id string for seasons and episodes."""

    def to_representation(self, item):  # noqa: D102
        return build_parent_id(item)


class StatusField(serializers.Field):
    """Custom field to convert status string to numeric value."""

    def to_representation(self, obj):  # noqa: D102
        return get_media_status(getattr(obj, "status", None))


class ItemSerializer(serializers.ModelSerializer):
    """Serializer used for item details."""

    class Meta:  # noqa: D106
        model = Item
        fields = "__all__"


class CompleteEpisodeSerializer(serializers.Serializer):
    """Serializer that builds a CompleteEpisode response."""

    def to_representation(self, instance):
        """Transform episode data into CompleteEpisode response."""
        media_metadata = instance.get("media_metadata", {})
        episode = instance.get("episode", {})
        user_media = instance.get("user_media", {})
        media_type = media_metadata.get("media_type")

        temp_episode = type("TempEpisode", (), {})()
        temp_episode.media_type = "episode"
        temp_episode.source = media_metadata.get("source")
        temp_episode.media_id = media_metadata.get("media_id")
        temp_episode.season_number = media_metadata.get("season_number")
        temp_episode.episode_number = episode.get("episode_number")

        season_source_url = media_metadata.get("source_url")
        source_url = ""
        if season_source_url:
            source_url = f"{season_source_url}/episode/{episode.get('episode_number')}"

        image = (
            "https://image.tmdb.org/t/p/original" + episode.get("still_path")
            if episode.get("still_path")
            else None
        )

        return {
            "media_id": int(media_metadata.get("media_id")),
            "source": media_metadata.get("source"),
            "source_url": source_url,
            "media_type": media_type,
            "title": episode.get("name"),
            "max_progress": 1,
            "image": image,
            "synopsis": episode.get("overview"),
            "genres": media_metadata.get("genres", []),
            "score": float(episode.get("vote_average")),
            "score_count": episode.get("vote_count"),
            "details": {
                "air_date": episode.get("air_date"),
                "episode_number": episode.get("episode_number"),
                "season_number": episode.get("season_number"),
                "runtime": episode.get("runtime"),
                "episode_type": episode.get("episode_type"),
                "crew": episode.get("crew", []),
                "guest_stars": episode.get("guest_stars", []),
            },
            "related": {},
            "item_id": ItemIdField().to_representation(temp_episode),
            "parent_id": ParentIdField().to_representation(temp_episode),
            "tracked": bool(user_media),
            "user_created": user_media.created_at,
            "user_score": None,
            "user_progress": 1 if bool(user_media) else 0,
            "user_progressed_at": user_media.created_at,
            "user_status": 3 if bool(user_media) else None,
            "user_start_date": user_media.created_at,
            "user_end_date": user_media.created_at,
            "user_notes": "",
        }


class CompleteMediaSerializer(serializers.Serializer):
    """Serializer that builds a CompleteMedia response."""

    def _process_seasons(self, media_metadata):
        """Process seasons in related data."""
        if "related" not in media_metadata or media_metadata["related"] is None:
            media_metadata["related"] = {}
        if (
            "seasons" not in media_metadata["related"]
            or media_metadata["related"]["seasons"] is None
        ):
            media_metadata["related"]["seasons"] = []

        for season in media_metadata["related"]["seasons"]:
            temp_season = type("TempSeason", (), season)()
            season["item_id"] = ItemIdField().to_representation(temp_season)
            season["parent_id"] = ParentIdField().to_representation(temp_season)

    def _process_episodes(self, media_metadata):
        """Process episodes in media data."""
        if "related" not in media_metadata or media_metadata["related"] is None:
            media_metadata["related"] = {}

        if (
            "episodes" not in media_metadata["related"]
            or media_metadata["related"]["episodes"] is None
        ):
            media_metadata["related"]["episodes"] = []

        if "related" not in media_metadata or media_metadata["related"] is None:
            media_metadata["related"] = {}

        episodes = media_metadata.pop("episodes")
        for episode in episodes:
            temp_episode = type("TempEpisode", (), {})()
            temp_episode.media_type = "episode"
            temp_episode.source = media_metadata.get("source")
            temp_episode.media_id = episode.get("show_id")
            temp_episode.season_number = episode.get("season_number")
            temp_episode.episode_number = episode.get("episode_number")
            episode["item_id"] = ItemIdField().to_representation(temp_episode)
            episode["parent_id"] = ParentIdField().to_representation(temp_episode)

        media_metadata["related"]["episodes"] = episodes

    def to_representation(self, instance):
        """Transform media_metadata and user data into CompleteMedia response."""
        media_metadata = instance.get("media_metadata", {})
        user_media = instance.get("user_media")
        media_type = media_metadata.get("media_type")

        if media_type == MediaTypes.TV.value:
            self._process_seasons(media_metadata)
        elif media_type == MediaTypes.SEASON.value:
            self._process_episodes(media_metadata)

        temp_media = type("TempMedia", (), media_metadata)()

        details = media_metadata.get("details", {})
        if "tvdb_id" in media_metadata:
            details["tvdb_id"] = media_metadata.pop("tvdb_id")
        if "last_episode_season" in media_metadata:
            details["last_episode_season"] = media_metadata.pop("last_episode_season")
        if "next_episode_season" in media_metadata:
            details["next_episode_season"] = media_metadata.pop("next_episode_season")
        if "last_issue_id" in media_metadata:
            details["last_issue_id"] = media_metadata.pop("last_issue_id")
        related = media_metadata.get("related", {})

        return {
            "media_id": int(media_metadata.get("media_id")),
            "source": media_metadata.get("source"),
            "source_url": media_metadata.get("source_url"),
            "media_type": media_metadata.get("media_type"),
            "title": media_metadata.pop("season_title", None)
            or media_metadata.get("title"),
            "max_progress": int(media_metadata.get("max_progress")),
            "image": media_metadata.get("image"),
            "synopsis": media_metadata.get("synopsis"),
            "genres": media_metadata.get("genres"),
            "score": float(media_metadata.get("score")),
            "score_count": int(media_metadata.get("score_count")),
            "details": details,
            "related": related,
            "item_id": ItemIdField().to_representation(temp_media),
            "parent_id": ParentIdField().to_representation(temp_media),
            "tracked": bool(user_media),
            "user_created": user_media.created_at
            if hasattr(user_media, "created_at")
            else None,
            "user_score": float(user_media.score)
            if hasattr(user_media, "score")
            else None,
            "user_progress": int(user_media.progress)
            if hasattr(user_media, "progress")
            else None,
            "user_progressed_at": user_media.progressed_at
            if hasattr(user_media, "progressed_at")
            else None,
            "user_status": StatusField().to_representation(user_media),
            "user_start_date": user_media.start_date
            if hasattr(user_media, "start_date")
            else None,
            "user_end_date": user_media.end_date
            if hasattr(user_media, "end_date")
            else None,
            "user_notes": user_media.notes if hasattr(user_media, "notes") else None,
        }


class EpisodeSerializer(serializers.ModelSerializer):
    """Serializer used for Episode items."""

    def to_representation(self, instance):
        """Serialize an Episode with item details."""
        source = self.context.get("source")

        temp_episode = type("TempEpisode", (), {})()
        temp_episode.media_type = "episode"
        temp_episode.source = source
        temp_episode.media_id = instance.get("show_id")
        temp_episode.season_number = instance.get("season_number")
        temp_episode.episode_number = instance.get("episode_number")

        image = (
            "https://image.tmdb.org/t/p/original" + instance.get("still_path")
            if instance.get("still_path")
            else None
        )

        instance["still_path"] = image

        instance["item_id"] = ItemIdField().to_representation(temp_episode)
        instance["parent_id"] = ParentIdField().to_representation(temp_episode)

        return instance


class EventSerializer(serializers.ModelSerializer):
    """Serializer used for calendar events."""

    item = ItemSerializer()
    item_id = ItemIdField(source="item", read_only=True)
    parent_id = ParentIdField(source="item", read_only=True)

    class Meta:  # noqa: D106
        model = Event
        fields = "__all__"

    def to_representation(self, instance):
        """Transform item to episode when content_number is present."""
        data = super().to_representation(instance)

        if instance.content_number is not None and data.get("item"):
            item_data = data["item"]
            item_data["episode_number"] = instance.content_number
            if item_data.get("media_type") == "season":
                item_data["media_type"] = "episode"

            class TempItem:
                def __init__(self, item_dict):
                    for key, value in item_dict.items():
                        setattr(self, key, value)

            temp_item = TempItem(item_data)
            data["item_id"] = ItemIdField().to_representation(temp_item)
            data["parent_id"] = ParentIdField().to_representation(temp_item)

        return data


class HistoryEntrySerializer(serializers.Serializer):
    """Serializer that builds a change-based history entry."""

    def to_representation(self, instance):
        """Build history entry with changes."""
        media_type = None
        if self.context:
            media_type = self.context.get("media_type")

        prev = getattr(instance, "prev_record", None)
        if prev is not None:
            changes = get_changes_from_diff(instance, prev, media_type)
        else:
            changes = get_changes_from_new_record(instance, media_type)

        for change in changes:
            if change.get("field") == "status":

                class TempObj:
                    def __init__(self, status_value):
                        self.status = status_value

                status_field = StatusField()
                if change.get("old_value") is not None:
                    change["old_value"] = status_field.to_representation(
                        TempObj(change["old_value"]),
                    )
                if change.get("new_value") is not None:
                    change["new_value"] = status_field.to_representation(
                        TempObj(change["new_value"]),
                    )

        item_obj = getattr(instance, "item_obj", None)
        item_id = build_item_id(item_obj) if item_obj is not None else None

        return {
            "id": getattr(instance, "history_id", None),
            "item_id": item_id,
            "timestamp": getattr(instance, "history_date", None),
            "changes": changes,
        }


class MediaSerializer(serializers.ModelSerializer):
    """Serializer used for media items."""

    item = ItemSerializer()
    item_id = ItemIdField(source="item", read_only=True)
    parent_id = ParentIdField(source="item", read_only=True)
    status = StatusField(source="item", read_only=True)

    class Meta:  # noqa: D106
        model = BasicMedia
        exclude = ("user",)


class MixedMediaSerializer(serializers.Serializer):
    """Serializer that handles mixed media types by checking every item."""

    def to_representation(self, instance):
        """Detect instance type and use appropriate serializer."""
        instance_type = type(instance)
        serializer_class = serializer_map.get(instance_type)

        if serializer_class is None:
            msg = (
                f"{get_http_message(500)} No serializer found for type {instance_type}. "
                f"Supported types: {list(serializer_map.keys())}."
            )
            raise ValueError(msg)

        context = self.context or {}
        serializer = serializer_class(instance, context=context)
        return serializer.data


class SeasonSerializer(serializers.ModelSerializer):
    """Serializer used for Season items."""

    def to_representation(self, instance):
        """Serialize a Season with item details."""

        class TempItem:
            def __init__(self, season_dict):
                for key, value in season_dict.items():
                    setattr(self, key, value)

        temp_item = TempItem(instance)

        instance["item_id"] = ItemIdField().to_representation(temp_item)
        instance["parent_id"] = ParentIdField().to_representation(temp_item)

        return instance


class TimelineItemSerializer(serializers.ModelSerializer):
    """Compact serializer used for timeline entries to reduce payload size."""

    item_id = ItemIdField(source="item", read_only=True)
    parent_id = ParentIdField(source="item", read_only=True)
    title = serializers.CharField(source="item.title", read_only=True, allow_null=True)
    image = serializers.URLField(source="item.image", read_only=True, allow_null=True)
    media_type = serializers.CharField(
        source="item.media_type",
        read_only=True,
        allow_null=True,
    )
    source = serializers.CharField(
        source="item.source",
        read_only=True,
        allow_null=True,
    )

    class Meta:  # noqa: D106
        model = BasicMedia
        exclude = ("user",)


serializer_map = {
    Anime: MediaSerializer,
    BasicMedia: MediaSerializer,
    Book: MediaSerializer,
    Comic: MediaSerializer,
    Episode: EpisodeSerializer,
    Event: EventSerializer,
    Game: MediaSerializer,
    Item: ItemSerializer,
    Manga: MediaSerializer,
    Movie: MediaSerializer,
    Season: MediaSerializer,
    TV: MediaSerializer,
}


def serialize_data(
    data,
    *,
    many=False,
    context=None,
    serializer_class=None,
    homogeneus=True,
):
    """Serialize data using the appropriate serializer class."""
    # If serializer class is explicitly provided, use it
    if serializer_class is not None:
        kwargs = {"many": many}
        if context is not None:
            kwargs["context"] = context
        serializer = serializer_class(data, **kwargs)
        return serializer.data

    # Auto-detect serializer based on data type
    if many:
        data_list = list(data) if not isinstance(data, list) else data
        if not data_list:
            return []

        # Check if data is homogeneous (all same type)
        first_type = type(data_list[0])
        if homogeneus:
            detected_serializer_class = serializer_map.get(first_type)

            if detected_serializer_class is None:
                msg = (
                    f"{get_http_message(500)} No serializer found for data type {first_type}. "
                    f"Supported types: {list(serializer_map.keys())}. "
                    f"Pass serializer_class explicitly if needed."
                )
                raise ValueError(msg)

            kwargs = {"many": True}
            if context is not None:
                kwargs["context"] = context
            serializer = detected_serializer_class(data_list, **kwargs)
            return serializer.data
        kwargs = {"many": True}
        if context is not None:
            kwargs["context"] = context
        serializer = MixedMediaSerializer(data_list, **kwargs)
        return serializer.data
    sample_item = data

    data_type = type(sample_item)
    detected_serializer_class = serializer_map.get(data_type)

    if detected_serializer_class is None:
        msg = (
            f"{get_http_message(500)} No serializer found for data type {data_type}. "
            f"Supported types: {list(serializer_map.keys())}. "
            f"Pass serializer_class explicitly if needed."
        )
        raise ValueError(msg)

    kwargs = {"many": False}
    if context is not None:
        kwargs["context"] = context
    serializer = detected_serializer_class(sample_item, **kwargs)
    return serializer.data
