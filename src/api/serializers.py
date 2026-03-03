from django.conf import settings
from django.utils.timezone import now
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
from lists.models import CustomList, CustomListItem

from .changes_history_processor import (
    get_changes_from_diff,
    get_changes_from_new_record,
)
from .helpers import (
    build_item_id,
    build_parent_id,
    get_http_message,
    get_media_status,
    get_progress_from_status,
)


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
        exclude = ("id",)


class ChangesHistoryEntrySerializer(serializers.Serializer):
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


class CompleteEpisodeSerializer(serializers.Serializer):
    # TODO: add lists field with list_id and list_item_id for each media
    """Serializer that builds a CompleteEpisode response."""

    def to_representation(self, instance):
        """Transform episode data into CompleteEpisode response."""
        media_metadata = instance.get("media_metadata", {})
        episode = instance.get("episode", {})
        user_medias = instance.get("user_medias", [])
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

        consumptions_number = len(user_medias)
        consumptions = serialize_data(
            user_medias,
            serializer_class=HistorySerializer,
            many=True,
        )

        return {
            "id": user_medias[0].item_id if user_medias else None,
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
            "tracked": consumptions_number > 0,
            "consumptions_number": consumptions_number,
            "consumptions": consumptions,
        }


class CompleteMediaSerializer(serializers.Serializer):
    # TODO: add lists field with list_id and list_item_id for each media
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
        user_medias = instance.get("user_medias")
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

        consumptions_number = len(user_medias)
        consumptions = serialize_data(
            user_medias,
            serializer_class=HistorySerializer,
            many=True,
        )

        # TODO: Check why some informations take a while to update after a change

        return {
            "id": user_medias[0].item_id if user_medias else None,
            "media_id": int(media_metadata.get("media_id")),
            "source": media_metadata.get("source"),
            "source_url": media_metadata.get("source_url"),
            "media_type": media_metadata.get("media_type"),
            "title": media_metadata.pop("season_title", None)
            or media_metadata.get("title"),
            "max_progress": int(media_metadata.get("max_progress"))
            if media_metadata.get("max_progress") is not None
            else 1,
            "image": media_metadata.get("image"),
            "synopsis": media_metadata.get("synopsis"),
            "genres": media_metadata.get("genres"),
            "score": float(media_metadata.get("score")),
            "score_count": int(media_metadata.get("score_count")),
            "details": details,
            "related": related,
            "item_id": ItemIdField().to_representation(temp_media),
            "parent_id": ParentIdField().to_representation(temp_media),
            "tracked": consumptions_number > 0,
            "consumptions_number": consumptions_number,
            "consumptions": consumptions,
        }


class EpisodeSerializer(serializers.ModelSerializer):
    # TODO: add lists field with list_id and list_item_id for each media
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


class HealthResponseSerializer(serializers.Serializer):
    """Serializer for health check response."""

    def to_representation(self, instance):
        """Transform reports from health-check library to json."""
        plugins = instance.get("plugins", {})
        errors = instance.get("errors", [])

        checks = {}
        for plugin_identifier, plugin in plugins.items():
            plugin_has_errors = bool(plugin.errors)

            checks[plugin_identifier] = {
                "status": "error" if plugin_has_errors else "ok",
                "error": plugin.pretty_status() if plugin_has_errors else None,
            }

        overall_status = "unavailable" if errors else "ok"

        return {
            "status": overall_status,
            "timestamp": now().isoformat(),
            "checks": checks,
        }


class HistorySerializer(serializers.Serializer):
    """Serializer for watch history entries."""

    def to_representation(self, instance):
        """Transform a user media instance into a watch history entry."""
        # For Episode instances, use simplified structure
        if isinstance(instance, Episode):
            return {
                "consumption_id": instance.id,
                "created": instance.created_at
                if hasattr(instance, "created_at")
                else None,
                "score": None,
                "progress": 1 if bool(instance) else 0,
                "progressed_at": instance.created_at
                if hasattr(instance, "created_at")
                else None,
                "status": 3 if bool(instance) else None,
                "start_date": instance.created_at
                if hasattr(instance, "created_at")
                else None,
                "end_date": instance.end_date
                if hasattr(instance, "end_date")
                else None,
                "notes": "",
            }
        status = StatusField().to_representation(instance)

        return {
            "consumption_id": instance.id,
            "created": instance.created_at
            if hasattr(instance, "created_at") and instance.created_at is not None
            else None,
            "score": float(instance.score)
            if hasattr(instance, "score") and instance.score is not None
            else None,
            "progress": get_progress_from_status(status),
            "progressed_at": instance.progressed_at
            if hasattr(instance, "progressed_at") and instance.progressed_at is not None
            else None,
            "status": status,
            "start_date": instance.start_date
            if hasattr(instance, "start_date") and instance.start_date is not None
            else None,
            "end_date": instance.end_date
            if hasattr(instance, "end_date") and instance.end_date is not None
            else None,
            "notes": instance.notes
            if hasattr(instance, "notes") and instance.notes is not None
            else None,
        }


class InfoSerializer(serializers.Serializer):
    """Serializer for the info endpoint."""

    def to_representation(self, instance):
        """Transform to representation."""
        return {
            "version": settings.VERSION,
            "debug": settings.DEBUG,
            "frontend_url": settings.BASE_URL or "http://localhost:8000",
            "language": settings.LANGUAGE_CODE,
            "timezone": settings.TIME_ZONE,
            "admin_enabled": settings.ADMIN_ENABLED,
            "track_time": settings.TRACK_TIME,
        }


class ListSerializer(serializers.Serializer):
    """Serializer used for custom lists."""

    def to_representation(self, instance):
        """Serialize a CustomList."""
        item_count = instance.items.count()
        include_items = True
        if self.context and "include_items" in self.context:
            include_items = self.context["include_items"]

        items = []

        if self.context and self.context.get("paginated_items") is not None:
            items_context = self.context["paginated_items"]
            nested_context = {
                **self.context,
                "serialize_items_as_media": True,
            }

            if isinstance(items_context, dict) and "results" in items_context:
                items = {
                    "pagination": items_context.get("pagination", {}),
                    "results": serialize_data(
                        items_context.get("results", []),
                        many=True,
                        context=nested_context,
                        homogeneous=False,
                    ),
                }
            else:
                items = items_context

        response = {
            "id": instance.id,
            "name": instance.name,
            "description": instance.description,
            "image": instance.image,
            "owner": {
                "id": instance.owner.id,
                "username": instance.owner.username,
            },
            "collaborators": [
                {"id": collaborator.id, "username": collaborator.username}
                for collaborator in instance.collaborators.all()
            ],
            "items_count": item_count,
            "latest_update": CustomListItem.objects.get_last_added_date(instance),
        }

        if include_items:
            response["items"] = items

        return response


class MediaSerializer(serializers.ModelSerializer):
    # TODO: add lists field with list_id and list_item_id for each media
    """Serializer used for media items."""

    id = serializers.IntegerField(source="item.id", read_only=True)
    consumption_id = serializers.IntegerField(source="id", read_only=True)
    item = ItemSerializer()
    item_id = ItemIdField(source="item", read_only=True)
    parent_id = ParentIdField(source="item", read_only=True)
    tracked = serializers.SerializerMethodField()
    status = StatusField(source="*", read_only=True)

    class Meta:  # noqa: D106
        model = BasicMedia
        exclude = ("user",)

    def get_tracked(self, obj):
        """Return True because MediaSerializer handles tracked media instances."""
        return True


class MixedMediaSerializer(serializers.Serializer):
    # TODO: add lists field with list_id and list_item_id for each media
    """Serializer that handles mixed media types by checking every item."""

    def to_representation(self, instance):
        """Detect instance type and use appropriate serializer."""
        if isinstance(instance, Item) and self.context.get("serialize_items_as_media"):
            serializer = UntrackedMediaSerializer(instance, context=self.context)
            return serializer.data

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


class UntrackedMediaSerializer(serializers.Serializer):
    # TODO: add lists field with list_id and list_item_id for each media
    """Serialize an untracked Item with a Media-like response shape."""

    def to_representation(self, instance):
        """Return media-compatible payload for an untracked Item."""
        return {
            "id": instance.id,
            "consumption_id": None,
            "item": serialize_data(instance, serializer_class=ItemSerializer),
            "item_id": ItemIdField().to_representation(instance),
            "parent_id": ParentIdField().to_representation(instance),
            "tracked": False,
            "created_at": None,
            "score": None,
            "status": None,
            "progress": None,
            "start_date": None,
            "end_date": None,
            "notes": None,
            "lists": [],
        }


class SeasonSerializer(serializers.ModelSerializer):
    # TODO: add lists field with list_id and list_item_id for each media
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
    CustomList: ListSerializer,
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
    homogeneous=True,
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
        if homogeneous:
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
