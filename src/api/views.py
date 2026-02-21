from calendar import monthrange
from datetime import date

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError
from django.utils.timezone import datetime, localdate, make_aware
from health_check.mixins import CheckMixin
from rest_framework import permissions
from rest_framework import views as drf_views
from rest_framework.response import Response

from app.forms import ManualItemForm, get_form_class
from app.models import BasicMedia, Item, MediaTypes, Sources
from app.providers import services, tmdb
from app.statistics import (
    get_activity_data,
    get_media_type_distribution,
    get_score_distribution,
    get_status_distribution,
    get_status_pie_chart_data,
    get_timeline,
    get_user_media,
)
from events import tasks
from events.models import Event
from users.models import MediaStatusChoices

from .changes_history_processor import (
    delete_changes_history_entry,
    get_changes_history_entries,
    get_changes_history_entry,
)
from .helpers import (
    MEDIA_TYPE_COMPLETE_MODEL_MAP,
    check_source_type,
    check_valid_type,
    fetch_results_all_types,
    fetch_results_for_type,
    get_http_message,
    get_media_status,
    make_page_url,
    paginate_data,
    parse_excluded_items,
    parse_limit_offset,
    parse_sort_filter,
    parse_status_param,
    try_parse_date,
    validate_body,
)
from .serializers import (
    ChangesHistoryEntrySerializer,
    CompleteEpisodeSerializer,
    CompleteMediaSerializer,
    EpisodeSerializer,
    HealthResponseSerializer,
    HistorySerializer,
    InfoSerializer,
    MediaSerializer,
    SeasonSerializer,
    TimelineItemSerializer,
    serialize_data,
)

# TODO!: check sorters and filters in paginate_data since data is not serialized yet.

# TODO!: for children items, it should return an error if user is trying to access a non existing season/episode (for example if it's requested the season 4 of a 2 season show)

# TODO: Implement search for already tracked media (item_id and tracked fields)

# TODO: Implement global search endpoint for every media_type

# TODO: Implement admin commands to manage users (add admins, remove/add users, etc)


# /api/v1/calendar/
class CalendarView(drf_views.APIView):
    """Calendar view."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        """Retrieve calendar events for the authenticated user."""
        start_date = request.GET.get("start_date")
        end_date = request.GET.get("end_date")
        month_q = request.GET.get("month")
        year_q = request.GET.get("year")

        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        try:
            if start_date:
                first_day = try_parse_date(start_date)
                last_day = try_parse_date(end_date) if end_date else localdate()
            else:
                try:
                    if year_q:
                        year = int(year_q)
                        if month_q:
                            month = int(month_q)
                            first_day = date(year, month, 1)
                            last_day = date(year, month, monthrange(year, month)[1])
                        else:
                            first_day = date(year, 1, 1)
                            last_day = date(year, 12, 31)
                    else:
                        current = localdate()
                        year = current.year
                        month = current.month
                        first_day = date(year, month, 1)
                        last_day = date(year, month, monthrange(year, month)[1])
                except (TypeError, ValueError):
                    current = localdate()
                    year = current.year
                    month = current.month
                    first_day = date(year, month, 1)
                    last_day = date(year, month, monthrange(year, month)[1])
        except (TypeError, ValueError):
            return Response(
                {"detail": f"{get_http_message(400)} Invalid date format."},
                status=400,
            )

        try:
            releases = Event.objects.get_user_events(request.user, first_day, last_day)
        except Exception as e:
            return Response(
                {"detail": f"{get_http_message(500)}", "errors": str(e)},
                status=500,
            )

        paginated_data = paginate_data(request, releases, limit, offset)
        paginated_data["results"] = serialize_data(
            paginated_data["results"],
            many=True,
            context={"request": request},
        )

        return Response(paginated_data)


# /api/v1/calendar/update/
class CalendarUpdateView(drf_views.APIView):
    """Update calendar view."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        """Trigger calendar events update for the authenticated user."""
        tasks.reload_calendar.delay(request.user)
        return Response(
            {"detail": f"{get_http_message(202)} Task queued"},
            status=202,
        )


# /api/v1/changes_history/[media_type]/[history_id]
class MediaTypeChangesHistoryDetailView(drf_views.APIView):
    """Changes history record view."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, media_type, history_id):
        """Retrieve the changes history record for a specific media."""
        if not check_valid_type(media_type, complete=True):
            return Response(
                {"detail": f"{get_http_message(400)} Unsupported media type."},
                status=400,
            )

        try:
            record = get_changes_history_entry(media_type, history_id, request.user)
            serialized_data = serialize_data(
                record,
                context={"media_type": media_type},
                serializer_class=ChangesHistoryEntrySerializer,
            )
            return Response(serialized_data, status=200)
        except Exception as e:
            return Response(
                {
                    "detail": f"{get_http_message(404)} History record not found",
                    "errors": str(e),
                },
                status=404,
            )

    def delete(self, request, media_type, history_id):
        """Delete the changes history record for a specific media."""
        if not check_valid_type(media_type, complete=True):
            return Response(
                {"detail": f"{get_http_message(400)} Unsupported media type."},
                status=400,
            )

        try:
            delete_changes_history_entry(media_type, history_id, request.user)
            return Response({"detail": "Record removed correctly"}, status=204)
        except Exception as e:
            return Response(
                {
                    "detail": f"{get_http_message(404)} History record not found",
                    "errors": str(e),
                },
                status=404,
            )


# /api/v1/health/
class HealthView(CheckMixin, drf_views.APIView):
    """Health check view."""

    authentication_classes = []
    permission_classes = []

    def get(self, request, *args, **kwargs):
        """Check API health status."""
        errors = self.errors
        plugins = self.plugins
        health_data = {
            "plugins": plugins,
            "errors": errors,
        }
        response_data = serialize_data(
            health_data,
            serializer_class=HealthResponseSerializer,
        )
        status_code = 500 if errors else 200
        return Response(response_data, status=status_code)


# /api/v1/info/
class InfoView(drf_views.APIView):
    """Info endpoint."""

    authentication_classes = []
    permission_classes = []

    def get(self, request):
        """Get application information."""
        info_data = {}
        response_data = serialize_data(
            info_data,
            serializer_class=InfoSerializer,
        )
        return Response(response_data, status=200)


# /api/v1/lists/
class ListsView(drf_views.APIView):  # noqa: D101
    def get(self, request):  # noqa: ARG002, D102
        return Response({"detail": f"{get_http_message(501)}"}, status=501)

    def post(self, request):  # noqa: ARG002, D102
        return Response({"detail": f"{get_http_message(501)}"}, status=501)


# /api/v1/lists/[id]/
class ListDetailView(drf_views.APIView):  # noqa: D101
    def get(self, request, id):  # noqa: A002, ARG002, D102
        return Response({"detail": f"{get_http_message(501)}"}, status=501)

    def patch(self, request, id):  # noqa: A002, ARG002, D102
        return Response({"detail": f"{get_http_message(501)}"}, status=501)

    def delete(self, request, id):  # noqa: A002, ARG002, D102
        return Response({"detail": f"{get_http_message(501)}"}, status=501)


# /api/v1/lists/[id]/items/
class ListAddItemView(drf_views.APIView):  # noqa: D101
    def post(self, request, id):  # noqa: A002, ARG002, D102
        return Response({"detail": f"{get_http_message(501)}"}, status=501)


# /api/v1/lists/[id]/items/[item_id]/
class ListRemoveItemView(drf_views.APIView):  # noqa: D101
    def delete(self, request, id, item_id):  # noqa: A002, ARG002, D102
        return Response({"detail": f"{get_http_message(501)}"}, status=501)


# /api/v1/media/
class MediaListView(drf_views.APIView):
    """List media view."""

    serializer_class = MediaSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        """Retrieve the list of media for the authenticated user."""
        # TODO: check progress sort might not be working
        user = request.user
        media_type = request.GET.get("media_type")
        status = request.GET.get("status", "")
        search = request.GET.get("search", "")
        sort_filter = request.GET.get("sort", "")
        exclude = parse_excluded_items(request)

        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        status = parse_status_param(status)
        if status is None:
            return Response(
                {"detail": f"{get_http_message(404)} Invalid status"},
                status=404,
            )

        sort, sort_order = parse_sort_filter(sort_filter)

        if media_type:
            if not check_valid_type(media_type, complete=True):
                return Response(
                    {"detail": f"{get_http_message(400)} Unsupported media type."},
                    status=400,
                )
            results, has_error = fetch_results_for_type(
                user,
                media_type,
                status,
                sort,
                search,
            )
        else:
            # Exclude EPISODES and SEASONS from results by default
            # to declutter the results
            # TODO: Add an option to return those too? (seasons=true&episodes=false)
            results, has_error = fetch_results_all_types(
                user,
                status,
                sort,
                search,
                exclude,
            )

        if has_error:
            return Response(
                {"detail": f"{get_http_message(404)} Invalid sorting"},
                status=404,
            )

        if isinstance(results, Response):
            return results

        if sort_order == "desc":
            results.reverse()

        paginated_data = paginate_data(request, results, limit, offset)
        serialized_data = serialize_data(
            paginated_data["results"],
            context={"request": request},
            many=True,
            homogeneus=False,
        )
        paginated_data["results"] = serialized_data
        return Response(paginated_data, status=200)


# /api/v1/media/[media_type]/
class MediaTypeListView(drf_views.APIView):
    """List media by type view."""

    serializer_class = MediaSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, media_type):
        """Retrieve the list of media of a specific media type."""
        # TODO: handle multiple consumptions of the same media item
        user = request.user
        status = request.GET.get("status", "")
        search = request.GET.get("search", "")
        sort_filter = request.GET.get("sort", "")
        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        status = parse_status_param(status)
        if status is None:
            return Response(
                {"detail": f"{get_http_message(404)} Invalid status"},
                status=404,
            )

        sort, sort_order = parse_sort_filter(sort_filter)

        if not check_valid_type(media_type, complete=True):
            return Response(
                {"detail": f"{get_http_message(400)} Unsupported media type."},
                status=400,
            )
        results, has_error = fetch_results_for_type(
            user,
            media_type,
            status,
            sort,
            search,
        )

        if has_error:
            return Response(
                {"detail": f"{get_http_message(404)} Invalid sorting"},
                status=404,
            )

        if isinstance(results, Response):
            return results

        if sort_order == "desc":
            results.reverse()

        paginated_data = paginate_data(request, results, limit, offset)
        serialized_data = serialize_data(
            paginated_data["results"],
            context={"request": request},
            many=True,
        )
        paginated_data["results"] = serialized_data
        return Response(paginated_data, status=200)

    def post(self, request, media_type):  # noqa: C901, D102, PLR0911, PLR0912
        return Response({"detail": f"{get_http_message(501)}"}, status=501)

        if not check_valid_type(media_type, complete=True):
            return Response(
                {"detail": f"{get_http_message(400)} Unsupported media type."},
                status=400,
            )

        if not request.data:
            return Response(
                {"detail": f"{get_http_message(400)} Missing body."},
                status=400,
            )

        body = request.data
        body["media_type"] = media_type
        body["status"] = (
            get_media_status(body["status"])
            if "status" in body
            else MediaStatusChoices.PLANNING
        )

        source = body.get("source", Sources.MANUAL.value)

        if str(source) == Sources.MANUAL.value:
            form = ManualItemForm(body, user=request.user)
            if not form.is_valid():
                return Response(
                    {"detail": f"{get_http_message(400)}", "errors": form.errors},
                    status=400,
                )

            try:
                item = form.save()
            except IntegrityError:
                media_name = form.cleaned_data.get("title", "item")
                if form.cleaned_data.get("season_number"):
                    media_name += f" - Season {form.cleaned_data['season_number']}"
                if form.cleaned_data.get("episode_number"):
                    media_name += f" - Episode {form.cleaned_data['episode_number']}"
                return Response(
                    {"detail": f"Conflict. {media_name} already exists."},
                    status=409,
                )

            media_data = dict(body)
            media_data.update({"source": item.source, "media_id": item.media_id})
            media_form = get_form_class(item.media_type)(media_data)
            if not media_form.is_valid():
                item.delete()
                return Response(
                    {"detail": f"{get_http_message(400)}", "errors": media_form.errors},
                    status=400,
                )

            media_form.instance.user = request.user
            media_form.instance.item = item
            if item.media_type == MediaTypes.SEASON.value:
                media_form.instance.related_tv = form.cleaned_data.get("parent_tv")
            elif item.media_type == MediaTypes.EPISODE.value:
                media_form.instance.related_season = form.cleaned_data.get(
                    "parent_season",
                )

            media_form.save()
            serialized_data = serialize_data(media_form.instance)
            return Response(serialized_data, status=201)

        media_id = body.get("media_id")
        if not media_id:
            return Response(
                {
                    "detail": f"{get_http_message(400)} 'media_id' is required for provider sources.",
                },
                status=400,
            )

        season_number = body.get("season_number")

        try:
            metadata = services.get_media_metadata(
                media_type,
                media_id,
                source,
                [season_number],
            )
        except Exception as e:
            return Response(
                {"detail": f"{get_http_message(500)}", "errors": str(e)},
                status=500,
            )

        defaults = {"title": metadata.get("title"), "image": metadata.get("image")}
        item, _ = Item.objects.get_or_create(
            media_id=media_id,
            source=source,
            media_type=media_type,
            season_number=season_number,
            defaults=defaults,
        )

        try:
            item.save()
        except Exception as e:
            return Response(
                {"detail": "Internal Server Error.", "errors": str(e)},
                status=500,
            )

        model = MEDIA_TYPE_COMPLETE_MODEL_MAP.get(media_type)
        if model is None:
            return Response(
                {"detail": f"{get_http_message(400)} Unsupported media type."},
                status=400,
            )

        instance = model(item=item, user=request.user)

        media_data = dict(body)
        media_data.update({"source": item.source, "media_id": item.media_id})
        media_form = get_form_class(media_type)(media_data, instance=instance)
        if not media_form.is_valid():
            return Response(
                {"detail": f"{get_http_message(400)}", "errors": media_form.errors},
                status=400,
            )

        media_form.save()
        serialized_data = serialize_data(media_form.instance)
        return Response(serialized_data, status=201)


# /api/v1/media/[media_type]/[source]/[media_id]/
class MediaDetailView(drf_views.APIView):
    """Media view."""

    serializer_class = MediaSerializer
    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request, media_type, source, media_id):
        """Delete a tracked media item and all its consumptions."""
        user = request.user

        if not check_valid_type(media_type):
            return Response(
                {"detail": f"{get_http_message(400)} Unsupported media type."},
                status=400,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"{get_http_message(400)} Cannot query `{source}` for `{media_type}` media type",
                },
                status=400,
            )

        try:
            user_medias = BasicMedia.objects.filter_media(
                user,
                media_id,
                media_type,
                source,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": f"{get_http_message(500)}", "errors": str(e)},
                status=500,
            )

        if not user_medias:
            return Response(
                {"detail": f"{get_http_message(404)} Media not found or not tracked."},
                status=404,
            )

        for media in user_medias:
            media.delete()

        return Response(
            status=204,
        )

    def get(self, request, media_type, source, media_id):
        """Retrieve details of a specific media for the authenticated user."""
        user = request.user

        if not check_valid_type(media_type):
            return Response(
                {"detail": f"{get_http_message(400)} Unsupported media type."},
                status=400,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"{get_http_message(400)} Cannot query `{source}` for `{media_type}` media type",
                },
                status=400,
            )

        try:
            media_metadata = services.get_media_metadata(media_type, media_id, source)
        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": f"{get_http_message(500)}", "errors": str(e)},
                status=500,
            )

        try:
            user_medias = BasicMedia.objects.filter_media_prefetch(
                user,
                media_id,
                media_type,
                source,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": "Internal Server Error", "errors": str(e)},
                status=500,
            )

        if (
            "related" in media_metadata
            and media_metadata["related"] is not None
            and "recommendations" in media_metadata["related"]
        ):
            media_metadata["related"].pop("recommendations")

        data = {
            "media_metadata": media_metadata,
            "user_medias": user_medias,
        }

        serialized = serialize_data(
            data,
            serializer_class=CompleteMediaSerializer,
        )
        return Response(serialized, status=200)

    def patch(self, request, media_type, source, media_id):
        """Update a tracked media item."""
        user = request.user

        if not check_valid_type(media_type):
            return Response(
                {"detail": f"{get_http_message(400)} Unsupported media type."},
                status=400,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"{get_http_message(400)} Cannot query `{source}` for `{media_type}` media type",
                },
                status=400,
            )

        body = request.data or {}

        try:
            user_medias = BasicMedia.objects.filter_media(
                user,
                media_id,
                media_type,
                source,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": f"{get_http_message(500)}", "errors": str(e)},
                status=500,
            )

        if not user_medias:
            return Response(
                {"detail": f"{get_http_message(404)} Media not found or not tracked."},
                status=404,
            )

        media = user_medias[0]

        validated_body, error = validate_body(body, media_type)

        if error:
            return Response(
                {"detail": f"{get_http_message(400)} {error}"},
                status=400,
            )

        for field, value in validated_body.items():
            if hasattr(media, field):
                setattr(media, field, value)

        try:
            media.save()
        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": f"{get_http_message(400)}", "errors": str(e)},
                status=400,
            )

        media.refresh_from_db()

        try:
            media_metadata = services.get_media_metadata(media_type, media_id, source)
        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": f"{get_http_message(500)}", "errors": str(e)},
                status=500,
            )

        if (
            "related" in media_metadata
            and media_metadata["related"] is not None
            and "recommendations" in media_metadata["related"]
        ):
            media_metadata["related"].pop("recommendations")

        data = {
            "media_metadata": media_metadata,
            "user_medias": user_medias,
        }

        serialized = serialize_data(
            data,
            serializer_class=CompleteMediaSerializer,
        )
        return Response(serialized, status=200)


# /api/v1/media/[media_type]/[source]/[media_id]/changes_history/
class MediaChangesHistoryView(drf_views.APIView):
    """Media changes history view."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, media_type, source, media_id):
        """Retrieve changes history timeline entries for a specific media."""
        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        if not check_valid_type(media_type):
            return Response(
                {"detail": f"{get_http_message(400)} Unsupported media type."},
                status=400,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"{get_http_message(400)} Cannot query `{source}` for `{media_type}` media type",
                },
                status=400,
            )

        user_medias = BasicMedia.objects.filter_media(
            request.user,
            media_id,
            media_type,
            source,
        )

        entries = get_changes_history_entries(user_medias, media_type)

        paginated_data = paginate_data(
            request,
            entries,
            limit,
            offset,
        )
        paginated_data["results"] = serialize_data(
            paginated_data["results"],
            many=True,
            context={"media_type": media_type},
            serializer_class=ChangesHistoryEntrySerializer,
        )
        return Response(paginated_data, status=200)


# /api/v1/media/[media_type]/[source]/[media_id]/history/
class MediaConsumptionHistoryView(drf_views.APIView):
    """Media consumption history view."""

    serializer_class = HistorySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, media_type, source, media_id):
        """Retrieve the history timeline for a specific media."""
        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        if not check_valid_type(media_type):
            return Response(
                {"detail": f"{get_http_message(400)} Unsupported media type."},
                status=400,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"{get_http_message(400)} Cannot query `{source}` for `{media_type}` media type",  # noqa: E501
                },
                status=400,
            )

        try:
            user_medias = BasicMedia.objects.filter_media(
                request.user,
                media_id,
                media_type,
                source,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": f"{get_http_message(500)}", "errors": str(e)},
                status=500,
            )

        # TODO: missing sorting
        paginated_data = paginate_data(
            request,
            user_medias,
            limit,
            offset,
        )
        consumptions = serialize_data(
            paginated_data["results"],
            serializer_class=HistorySerializer,
            many=True,
        )
        paginated_data["results"] = consumptions
        return Response(paginated_data, status=200)


# /api/v1/media/[media_type]/[source]/[media_id]/history/[consumption_id]/
class MediaConsumptionEntryDetailView(drf_views.APIView):
    """Media consumption history entry detail view."""

    serializer_class = HistorySerializer
    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request, media_type, source, media_id, consumption_id):
        """Delete a specific consumption history entry for a specific media."""
        if not check_valid_type(media_type):
            return Response(
                {"detail": f"{get_http_message(400)} Unsupported media type."},
                status=400,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"{get_http_message(400)} Cannot query `{source}` for `{media_type}` media type",  # noqa: E501
                },
                status=400,
            )

        try:
            user_medias = BasicMedia.objects.filter_media(
                request.user,
                media_id,
                media_type,
                source,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": f"{get_http_message(500)}", "errors": str(e)},
                status=500,
            )

        consumption = user_medias.filter(id=consumption_id).first()
        if not consumption:
            return Response(
                {"detail": f"{get_http_message(404)} Consumption entry not found."},
                status=404,
            )

        consumption.delete()

        return Response(status=204)

    def get(self, request, media_type, source, media_id, consumption_id):
        """Retrieve a specific consumption history entry for a specific media."""
        if not check_valid_type(media_type):
            return Response(
                {"detail": f"{get_http_message(400)} Unsupported media type."},
                status=400,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"{get_http_message(400)} Cannot query `{source}` for `{media_type}` media type",  # noqa: E501
                },
                status=400,
            )

        try:
            user_medias = BasicMedia.objects.filter_media(
                request.user,
                media_id,
                media_type,
                source,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": f"{get_http_message(500)}", "errors": str(e)},
                status=500,
            )

        consumption = user_medias.filter(id=consumption_id).first()
        if not consumption:
            return Response(
                {"detail": f"{get_http_message(404)} Consumption entry not found."},
                status=404,
            )

        serialized_data = serialize_data(
            consumption,
            serializer_class=HistorySerializer,
        )
        return Response(serialized_data, status=200)

    def patch(self, request, media_type, source, media_id, consumption_id):
        """Update a specific consumption history entry for a specific media."""
        if not check_valid_type(media_type):
            return Response(
                {"detail": f"{get_http_message(400)} Unsupported media type."},
                status=400,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"{get_http_message(400)} Cannot query `{source}` for `{media_type}` media type",
                },
                status=400,
            )

        try:
            user_medias = BasicMedia.objects.filter_media(
                request.user,
                media_id,
                media_type,
                source,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": f"{get_http_message(500)}", "errors": str(e)},
                status=500,
            )

        consumption = user_medias.filter(id=consumption_id).first()
        if not consumption:
            return Response(
                {"detail": f"{get_http_message(404)} Consumption entry not found."},
                status=404,
            )

        body = request.data or {}

        validated_body, error = validate_body(body, media_type)

        if error:
            return Response(
                {"detail": f"{get_http_message(400)} {error}"},
                status=400,
            )

        for field, value in validated_body.items():
            if hasattr(consumption, field):
                setattr(consumption, field, value)

        try:
            consumption.save()
        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": f"{get_http_message(400)}", "errors": str(e)},
                status=400,
            )

        consumption.refresh_from_db()

        serialized_data = serialize_data(
            consumption,
            serializer_class=HistorySerializer,
        )
        return Response(serialized_data, status=200)


# /api/v1/media/[media_type]/[source]/[media_id]/lists/
class MediaAddToListView(drf_views.APIView):  # noqa: D101
    def post(self, request, media_type, source, media_id):  # noqa: ARG002, D102
        return Response({"detail": f"{get_http_message(501)}"}, status=501)

    def delete(self, request, media_type, source, media_id):  # noqa: ARG002, D102
        return Response({"detail": f"{get_http_message(501)}"}, status=501)


# /api/v1/media/[media_type]/[source]/[media_id]/recommendations/
class MediaRecommendationsView(drf_views.APIView):
    """Media recommendations view."""

    serializer_class = MediaSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get(self, _, media_type, source, media_id):
        """Retrieve recommendations for a specific media."""
        if not check_valid_type(media_type):
            return Response(
                {"detail": f"{get_http_message(400)} Unsupported media type."},
                status=400,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"{get_http_message(400)} Cannot query `{source}` for `{media_type}` media type",
                },
                status=400,
            )

        try:
            media_metadata = services.get_media_metadata(media_type, media_id, source)
        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": f"{get_http_message(500)}", "errors": str(e)},
                status=500,
            )

        recommendations = []
        if (
            "related" in media_metadata
            and media_metadata["related"] is not None
            and "recommendations" in media_metadata["related"]
        ):
            recommendations = media_metadata["related"]["recommendations"]

        return Response(recommendations, status=200)


# /api/v1/media/[media_type]/[source]/[media_id]/seasons/
class MediaSeasonsView(drf_views.APIView):
    """Media seasons view."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, media_type, source, media_id):
        """Retrieve the history timeline for a specific media."""
        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        if not check_valid_type(media_type) or media_type != MediaTypes.TV.value:
            return Response(
                {"detail": f"{get_http_message(400)} Unsupported media type."},
                status=400,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": f"{get_http_message(400)} Seasons are supported only for 'tv' media type.",
                },
                status=400,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"{get_http_message(400)} Cannot query `{source}` for `{media_type}` media type",
                },
                status=400,
            )

        try:
            media_metadata = services.get_media_metadata(media_type, media_id, source)
        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": f"{get_http_message(500)}", "errors": str(e)},
                status=500,
            )

        seasons = []
        if (
            "related" in media_metadata
            and media_metadata["related"] is not None
            and "seasons" in media_metadata["related"]
        ):
            seasons = media_metadata["related"]["seasons"]

        paginated_data = paginate_data(request, seasons, limit, offset)
        paginated_data["results"] = serialize_data(
            paginated_data["results"],
            many=True,
            serializer_class=SeasonSerializer,
        )
        return Response(paginated_data, status=200)


# /api/v1/media/[media_type]/[source]/[media_id]/sync/
class MediaSyncView(drf_views.APIView):
    """Sync media view."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, _, media_type, source, media_id):
        """Trigger sync of metadata from provider (non-manual sources only)."""
        if not check_valid_type(media_type):
            return Response(
                {"detail": f"{get_http_message(400)} Unsupported media type."},
                status=400,
            )

        if source == Sources.MANUAL.value:
            return Response(
                {"detail": f"{get_http_message(400)} Manual items cannot be synced."},
                status=400,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"{get_http_message(400)} Cannot sync `{source}` for `{media_type}` media type",  # noqa: E501
                },
                status=400,
            )

        cache_key = f"{source}_{media_type}_{media_id}"

        ttl = cache.ttl(cache_key)
        if ttl is not None and ttl > (settings.CACHE_TIMEOUT - 3):
            response = Response(
                {
                    "detail": f"{get_http_message(429)} The data was recently synced, please wait a few seconds.",  # noqa: E501
                },
                status=429,
            )
            response["Retry-After"] = str(ttl)
            return response

        cache.delete(cache_key)

        try:
            metadata = services.get_media_metadata(
                media_type,
                media_id,
                source,
            )

            item, _ = Item.objects.update_or_create(
                media_id=media_id,
                source=source,
                media_type=media_type,
                defaults={
                    "title": metadata["title"],
                    "image": metadata["image"],
                },
            )

            item.fetch_releases(delay=False)

            return Response(
                {"detail": f"{get_http_message(202)} Metadata synced successfully."},
                status=202,
            )

        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": f"{get_http_message(500)}", "errors": str(e)},
                status=500,
            )


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/
class MediaSeasonDetailView(drf_views.APIView):
    """Season view."""

    serializer_class = MediaSerializer
    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request, media_type, source, media_id, season_number):
        """Delete a tracked season item for the authenticated user."""
        user = request.user

        if not check_valid_type(media_type):
            return Response(
                {"detail": f"{get_http_message(400)} Unsupported media type."},
                status=400,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": f"{get_http_message(400)} Seasons are supported only for 'tv' media type.",
                },
                status=400,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"{get_http_message(400)} Cannot query `{source}` for `{media_type}` media type",
                },
                status=400,
            )

        try:
            user_medias = BasicMedia.objects.filter_media(
                user,
                media_id,
                "season",
                source,
                season_number=season_number,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": f"{get_http_message(500)}", "errors": str(e)},
                status=500,
            )

        if not user_medias:
            return Response(
                {"detail": f"{get_http_message(404)} Season not found or not tracked."},
                status=404,
            )

        # TODO: Handle better reconsumptions
        for media in user_medias:
            media.delete()

        return Response(
            status=204,
        )

    def get(self, request, media_type, source, media_id, season_number):
        """Retrieve details of a specific season for the authenticated user."""
        user = request.user

        if not check_valid_type(media_type):
            return Response(
                {"detail": f"{get_http_message(400)} Unsupported media type."},
                status=400,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": f"{get_http_message(400)} Seasons are supported only for 'tv' media type.",
                },
                status=400,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"{get_http_message(400)} Cannot query `{source}` for `{media_type}` media type",
                },
                status=400,
            )

        try:
            media_metadata = services.get_media_metadata(
                "season",
                media_id,
                source,
                [season_number],
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": f"{get_http_message(500)}", "errors": str(e)},
                status=500,
            )

        if not media_metadata:
            return Response(
                {"detail": f"{get_http_message(404)} Season not found."},
                status=404,
            )

        try:
            user_medias = BasicMedia.objects.filter_media_prefetch(
                user,
                media_id,
                "season",
                source,
                season_number=season_number,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": f"{get_http_message(500)}", "errors": str(e)},
                status=500,
            )

        data = {
            "media_metadata": media_metadata,
            "user_medias": user_medias,
        }

        serialized = serialize_data(
            data,
            serializer_class=CompleteMediaSerializer,
        )
        return Response(serialized, status=200)

    def patch(self, request, media_type, source, media_id, season_number):
        """Update a tracked season item."""
        user = request.user

        if not check_valid_type(media_type):
            return Response(
                {"detail": f"{get_http_message(400)} Unsupported media type."},
                status=400,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": f"{get_http_message(400)} Seasons are supported only for 'tv' media type.",
                },
                status=400,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"{get_http_message(400)} Cannot query `{source}` for `{media_type}` media type",
                },
                status=400,
            )

        body = request.data or {}

        try:
            user_medias = BasicMedia.objects.filter_media(
                user,
                media_id,
                "season",
                source,
                season_number=season_number,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": f"{get_http_message(500)}", "errors": str(e)},
                status=500,
            )

        if not user_medias:
            return Response(
                {"detail": f"{get_http_message(404)} Season not found or not tracked."},
                status=404,
            )

        media = user_medias[0]

        validated_body, error = validate_body(body, "season")

        if error:
            return Response(
                {"detail": f"{get_http_message(400)} {error}"},
                status=400,
            )

        for field, value in validated_body.items():
            if hasattr(media, field):
                setattr(media, field, value)

        try:
            media.save()
        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": f"{get_http_message(400)}", "errors": str(e)},
                status=400,
            )

        media.refresh_from_db()

        try:
            media_metadata = services.get_media_metadata(
                "season",
                media_id,
                source,
                [season_number],
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": f"{get_http_message(500)}", "errors": str(e)},
                status=500,
            )

        data = {
            "media_metadata": media_metadata,
            "user_medias": user_medias,
        }

        serialized = serialize_data(
            data,
            serializer_class=CompleteMediaSerializer,
        )
        return Response(serialized, status=200)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/changes_history/
class MediaSeasonChangesHistoryView(drf_views.APIView):
    """Changes history season view."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, media_type, source, media_id, season_number):
        """Retrieve changes history timeline entries for a specific season of a tv serie."""
        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        if not check_valid_type(media_type):
            return Response(
                {"detail": f"{get_http_message(400)} Unsupported media type."},
                status=400,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": f"{get_http_message(400)} Seasons are supported only for 'tv' media type.",
                },
                status=400,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"{get_http_message(400)} Cannot query `{source}` for `{media_type}` media type",
                },
                status=400,
            )

        user_medias = BasicMedia.objects.filter_media(
            request.user,
            media_id,
            "season",
            source,
            season_number=season_number,
        )

        entries = get_changes_history_entries(user_medias, media_type)

        paginated_data = paginate_data(
            request,
            entries,
            limit,
            offset,
        )
        paginated_data["results"] = serialize_data(
            paginated_data["results"],
            many=True,
            context={"request": request, "media_type": media_type},
            serializer_class=ChangesHistoryEntrySerializer,
        )
        return Response(paginated_data, status=200)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/episodes/
class MediaSeasonEpisodesView(drf_views.APIView):
    """Season episodes view."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, media_type, source, media_id, season_number):
        """Retrieve the episodes for a specific season of a tv serie."""
        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        if not check_valid_type(media_type):
            return Response(
                {"detail": f"{get_http_message(400)} Unsupported media type."},
                status=400,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": f"{get_http_message(400)} Seasons are supported only for 'tv' media type.",
                },
                status=400,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"{get_http_message(400)} Cannot query `{source}` for `{media_type}` media type",
                },
                status=400,
            )

        try:
            media_metadata = services.get_media_metadata(
                "season",
                media_id,
                source,
                [season_number],
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": f"{get_http_message(500)}", "errors": str(e)},
                status=500,
            )

        episodes = []
        if "episodes" in media_metadata and media_metadata["episodes"] is not None:
            episodes = media_metadata["episodes"]

        paginated = paginate_data(request, episodes, limit, offset)
        paginated["results"] = serialize_data(
            paginated["results"],
            many=True,
            context={"source": source},
            serializer_class=EpisodeSerializer,
        )
        return Response(paginated, status=200)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/history/
class MediaSeasonConsumptionHistoryView(drf_views.APIView):
    """Season consumption history view."""

    serializer_class = HistorySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, media_type, source, media_id, season_number):
        """Retrieve the history timeline for a specific season of a tv serie."""
        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        if not check_valid_type(media_type):
            return Response(
                {"detail": f"{get_http_message(400)} Unsupported media type."},
                status=400,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": f"{get_http_message(400)} Seasons are supported only for 'tv' media type.",
                },
                status=400,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"{get_http_message(400)} Cannot query `{source}` for `{media_type}` media type",  # noqa: E501
                },
                status=400,
            )

        try:
            user_medias = BasicMedia.objects.filter_media(
                request.user,
                media_id,
                "season",
                source,
                season_number=season_number,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": f"{get_http_message(500)}", "errors": str(e)},
                status=500,
            )

        # TODO: missing sorting
        paginated_data = paginate_data(
            request,
            user_medias,
            limit,
            offset,
        )
        consumptions = serialize_data(
            paginated_data["results"],
            serializer_class=HistorySerializer,
            many=True,
        )
        paginated_data["results"] = consumptions
        return Response(paginated_data, status=200)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/history/[consumption_id]/
class MediaSeasonConsumptionEntryDetailView(drf_views.APIView):
    """Season consumption history entry detail view."""

    serializer_class = HistorySerializer
    permission_classes = [permissions.IsAuthenticated]

    def delete(
        self,
        request,
        media_type,
        source,
        media_id,
        season_number,
        consumption_id,
    ):
        """Delete a specific consumption history entry for a specific season of a tv serie."""
        if not check_valid_type(media_type):
            return Response(
                {"detail": f"{get_http_message(400)} Unsupported media type."},
                status=400,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": f"{get_http_message(400)} Seasons are supported only for 'tv' media type.",
                },
                status=400,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"{get_http_message(400)} Cannot query `{source}` for `{media_type}` media type",  # noqa: E501
                },
                status=400,
            )

        try:
            user_medias = BasicMedia.objects.filter_media(
                request.user,
                media_id,
                "season",
                source,
                season_number=season_number,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": f"{get_http_message(500)}", "errors": str(e)},
                status=500,
            )

        consumption = user_medias.filter(id=consumption_id).first()
        if not consumption:
            return Response(
                {"detail": f"{get_http_message(404)} Consumption entry not found."},
                status=404,
            )

        consumption.delete()

        return Response(status=204)

    def get(self, request, media_type, source, media_id, season_number, consumption_id):
        """Retrieve a specific consumption history entry for a specific season of a tv serie."""
        if not check_valid_type(media_type):
            return Response(
                {"detail": f"{get_http_message(400)} Unsupported media type."},
                status=400,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": f"{get_http_message(400)} Seasons are supported only for 'tv' media type.",
                },
                status=400,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"{get_http_message(400)} Cannot query `{source}` for `{media_type}` media type",  # noqa: E501
                },
                status=400,
            )

        try:
            user_medias = BasicMedia.objects.filter_media(
                request.user,
                media_id,
                "season",
                source,
                season_number=season_number,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": f"{get_http_message(500)}", "errors": str(e)},
                status=500,
            )

        consumption = user_medias.filter(id=consumption_id).first()
        if not consumption:
            return Response(
                {"detail": f"{get_http_message(404)} Consumption entry not found."},
                status=404,
            )

        serialized_data = serialize_data(
            consumption,
            serializer_class=HistorySerializer,
        )
        return Response(serialized_data, status=200)

    def patch(
        self,
        request,
        media_type,
        source,
        media_id,
        season_number,
        consumption_id,
    ):
        """Update a specific consumption history entry for a specific season of a tv serie."""
        if not check_valid_type(media_type):
            return Response(
                {"detail": f"{get_http_message(400)} Unsupported media type."},
                status=400,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": f"{get_http_message(400)} Seasons are supported only for 'tv' media type.",
                },
                status=400,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"{get_http_message(400)} Cannot query `{source}` for `{media_type}` media type",
                },
                status=400,
            )

        try:
            user_medias = BasicMedia.objects.filter_media(
                request.user,
                media_id,
                "season",
                source,
                season_number=season_number,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": f"{get_http_message(500)}", "errors": str(e)},
                status=500,
            )

        consumption = user_medias.filter(id=consumption_id).first()
        if not consumption:
            return Response(
                {"detail": f"{get_http_message(404)} Consumption entry not found."},
                status=404,
            )

        body = request.data or {}

        validated_body, error = validate_body(body, "season")

        if error:
            return Response(
                {"detail": f"{get_http_message(400)} {error}"},
                status=400,
            )

        for field, value in validated_body.items():
            if hasattr(consumption, field):
                setattr(consumption, field, value)

        try:
            consumption.save()
        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": f"{get_http_message(400)}", "errors": str(e)},
                status=400,
            )

        consumption.refresh_from_db()

        serialized_data = serialize_data(
            consumption,
            serializer_class=HistorySerializer,
        )
        return Response(serialized_data, status=200)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/sync/
class MediaSeasonSyncView(drf_views.APIView):
    """Sync season."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, _, media_type, source, media_id, season_number):
        """Trigger sync of metadata from provider (non-manual sources only)."""
        # TODO: see if it can be simplified reducing the number of return statements
        if not check_valid_type(media_type):
            return Response(
                {"detail": f"{get_http_message(400)} Unsupported media type."},
                status=400,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": f"{get_http_message(400)} Seasons are supported only for 'tv' media type.",  # noqa: E501
                },
                status=400,
            )

        if source == Sources.MANUAL.value:
            return Response(
                {"detail": f"{get_http_message(400)} Manual items cannot be synced."},
                status=400,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"{get_http_message(400)} Cannot sync `{source}` for `{media_type}` media type",  # noqa: E501
                },
                status=400,
            )

        cache_key = f"{source}_season_{media_id}_{season_number}"

        ttl = cache.ttl(cache_key)
        if ttl is not None and ttl > (settings.CACHE_TIMEOUT - 3):
            response = Response(
                {
                    "detail": f"{get_http_message(429)} The data was recently synced, please wait a few seconds.",  # noqa: E501
                },
                status=429,
            )
            response["Retry-After"] = str(ttl)
            return response

        cache.delete(cache_key)

        try:
            metadata = services.get_media_metadata(
                "season",
                media_id,
                source,
                [season_number],
            )

            item, _ = Item.objects.update_or_create(
                media_id=media_id,
                source=source,
                media_type="season",
                season_number=season_number,
                defaults={
                    "title": metadata["title"],
                    "image": metadata["image"],
                },
            )

            metadata["episodes"] = tmdb.process_episodes(
                metadata,
                [],
            )
            existing_episodes = {
                ep.episode_number: ep
                for ep in Item.objects.filter(
                    source=source,
                    media_type=MediaTypes.EPISODE.value,
                    media_id=media_id,
                    season_number=season_number,
                )
            }

            episodes_to_update = []

            for episode_data in metadata["episodes"]:
                episode_number = episode_data["episode_number"]
                if episode_number in existing_episodes:
                    episode_item = existing_episodes[episode_number]
                    episode_item.title = metadata["title"]
                    episode_item.image = episode_data["image"]
                    episodes_to_update.append(episode_item)

            if episodes_to_update:
                Item.objects.bulk_update(
                    episodes_to_update,
                    ["title", "image"],
                    batch_size=100,
                )

            item.fetch_releases(delay=False)

            return Response(
                {"detail": f"{get_http_message(202)} Metadata synced successfully."},
                status=202,
            )

        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": f"{get_http_message(500)}", "errors": str(e)},
                status=500,
            )


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/[episode_number]/
class MediaEpisodeDetailView(drf_views.APIView):
    """Episode view."""

    serializer_class = MediaSerializer
    permission_classes = [permissions.IsAuthenticated]

    def delete(
        self,
        request,
        media_type,
        source,
        media_id,
        season_number,
        episode_number,
    ):
        """Delete a tracked episode item for the authenticated user."""
        user = request.user

        if not check_valid_type(media_type):
            return Response(
                {"detail": f"{get_http_message(400)} Unsupported media type."},
                status=400,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": f"{get_http_message(400)} Episodes are supported only for 'tv' media type.",
                },
                status=400,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"{get_http_message(400)} Cannot query `{source}` for `{media_type}` media type",
                },
                status=400,
            )

        try:
            user_medias = BasicMedia.objects.filter_media(
                user,
                media_id,
                "episode",
                source,
                season_number=season_number,
                episode_number=episode_number,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": f"{get_http_message(500)}", "errors": str(e)},
                status=500,
            )

        if not user_medias:
            return Response(
                {
                    "detail": f"{get_http_message(404)} Episode not found or not tracked.",
                },
                status=404,
            )

        # TODO: Handle better reconsumptions
        for media in user_medias:
            media.delete()

        return Response(
            status=204,
        )

    def get(self, request, media_type, source, media_id, season_number, episode_number):
        """Retrieve details of a specific episode for the authenticated user."""
        user = request.user

        if not check_valid_type(media_type):
            return Response(
                {"detail": f"{get_http_message(400)} Unsupported media type."},
                status=400,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": f"{get_http_message(400)} Episodes are supported only for 'tv' media type.",  # noqa: E501
                },
                status=400,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"{get_http_message(400)} Cannot query `{source}` for `{media_type}` media type",  # noqa: E501
                },
                status=400,
            )

        try:
            media_metadata = services.get_media_metadata(
                "season",
                media_id,
                source,
                [season_number],
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": f"{get_http_message(500)}", "errors": str(e)},
                status=500,
            )

        if not media_metadata:
            return Response(
                {"detail": f"{get_http_message(404)} Episode not found."},
                status=404,
            )
        if "episodes" in media_metadata and media_metadata["episodes"] is not None:
            episode = next(
                (
                    obj
                    for obj in media_metadata["episodes"]
                    if obj["episode_number"] == int(episode_number)
                ),
                None,
            )

            if not episode:
                return Response(
                    {"detail": "Not Found. Episode not found."},
                    status=404,
                )

        try:
            user_medias = BasicMedia.objects.filter_media_prefetch(
                user,
                media_id,
                "episode",
                source,
                season_number=season_number,
                episode_number=episode_number,
            )
        except Exception as e:
            return Response(
                {"detail": f"{get_http_message(500)}", "errors": str(e)},
                status=500,
            )

        media_metadata.pop("episodes")

        data = {
            "media_metadata": media_metadata,
            "episode": episode,
            "user_medias": user_medias,
        }

        serialized = serialize_data(
            data,
            serializer_class=CompleteEpisodeSerializer,
        )
        return Response(serialized, status=200)

    def patch(
        self,
        request,
        media_type,
        source,
        media_id,
        season_number,
        episode_number,
    ):
        """Update a tracked episode item."""
        user = request.user

        if not check_valid_type(media_type):
            return Response(
                {"detail": f"{get_http_message(400)} Unsupported media type."},
                status=400,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": f"{get_http_message(400)} Episodes are supported only for 'tv' media type.",
                },
                status=400,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"{get_http_message(400)} Cannot query `{source}` for `{media_type}` media type",
                },
                status=400,
            )

        body = request.data or {}

        try:
            user_medias = BasicMedia.objects.filter_media(
                user,
                media_id,
                "episode",
                source,
                season_number=season_number,
                episode_number=episode_number,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": f"{get_http_message(500)}", "errors": str(e)},
                status=500,
            )

        if not user_medias:
            return Response(
                {
                    "detail": f"{get_http_message(404)} Episode not found or not tracked.",
                },
                status=404,
            )

        media = user_medias[0]

        validated_body, error = validate_body(body, "episode")

        if error:
            return Response(
                {"detail": f"{get_http_message(400)} {error}"},
                status=400,
            )

        for field, value in validated_body.items():
            if hasattr(media, field):
                setattr(media, field, value)

        try:
            media.save()
        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": f"{get_http_message(400)}", "errors": str(e)},
                status=400,
            )

        media.refresh_from_db()

        try:
            media_metadata = services.get_media_metadata(
                "season",
                media_id,
                source,
                [season_number],
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": f"{get_http_message(500)}", "errors": str(e)},
                status=500,
            )

        if not media_metadata:
            return Response(
                {"detail": f"{get_http_message(404)} Episode not found."},
                status=404,
            )

        if "episodes" in media_metadata and media_metadata["episodes"] is not None:
            episode = next(
                (
                    obj
                    for obj in media_metadata["episodes"]
                    if obj["episode_number"] == int(episode_number)
                ),
                None,
            )

            if not episode:
                return Response(
                    {"detail": "Not Found. Episode not found."},
                    status=404,
                )

        media_metadata.pop("episodes")

        data = {
            "media_metadata": media_metadata,
            "episode": episode,
            "user_medias": user_medias,
        }

        serialized = serialize_data(
            data,
            serializer_class=CompleteEpisodeSerializer,
        )
        return Response(serialized, status=200)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/[episode_number]/changes_history/
class MediaEpisodeChangesHistoryView(drf_views.APIView):
    """Changes history episode view."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, media_type, source, media_id, season_number, episode_number):
        """Retrieve changes history timeline entries for a specific episode of a tv serie."""
        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        if not check_valid_type(media_type):
            return Response(
                {"detail": f"{get_http_message(400)} Unsupported media type."},
                status=400,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": f"{get_http_message(400)} Episodes are supported only for 'tv' media type.",  # noqa: E501
                },
                status=400,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"{get_http_message(400)} Cannot query `{source}` for `{media_type}` media type",  # noqa: E501
                },
                status=400,
            )

        user_medias = BasicMedia.objects.filter_media(
            request.user,
            media_id,
            "episode",
            source,
            season_number=season_number,
            episode_number=episode_number,
        )

        entries = get_changes_history_entries(user_medias, media_type)

        paginated_data = paginate_data(
            request,
            entries,
            limit,
            offset,
        )
        paginated_data["results"] = serialize_data(
            paginated_data["results"],
            many=True,
            context={"request": request, "media_type": media_type},
            serializer_class=ChangesHistoryEntrySerializer,
        )
        return Response(paginated_data, status=200)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/[episode_number]/history/
class MediaEpisodeConsumptionHistoryView(drf_views.APIView):
    """Episode consumption history view."""

    serializer_class = HistorySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, media_type, source, media_id, season_number, episode_number):
        """Retrieve the history timeline for a specific episode of a tv serie."""
        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        if not check_valid_type(media_type):
            return Response(
                {"detail": f"{get_http_message(400)} Unsupported media type."},
                status=400,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": f"{get_http_message(400)} Episodes are supported only for 'tv' media type.",  # noqa: E501
                },
                status=400,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"{get_http_message(400)} Cannot query `{source}` for `{media_type}` media type",  # noqa: E501
                },
                status=400,
            )

        try:
            user_medias = BasicMedia.objects.filter_media(
                request.user,
                media_id,
                "episode",
                source,
                season_number=season_number,
                episode_number=episode_number,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": f"{get_http_message(500)}", "errors": str(e)},
                status=500,
            )

        # TODO: missing sorting
        paginated_data = paginate_data(
            request,
            user_medias,
            limit,
            offset,
        )
        consumptions = serialize_data(
            paginated_data["results"],
            serializer_class=HistorySerializer,
            many=True,
        )
        paginated_data["results"] = consumptions
        return Response(paginated_data, status=200)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/[episode_number]/history/[consumption_id]/
class MediaEpisodeConsumptionEntryDetailView(drf_views.APIView):
    """Episode consumption history entry detail view."""

    serializer_class = HistorySerializer
    permission_classes = [permissions.IsAuthenticated]

    def delete(
        self,
        request,
        media_type,
        source,
        media_id,
        season_number,
        episode_number,
        consumption_id,
    ):
        """Delete a specific consumption history entry for a specific episode."""
        if not check_valid_type(media_type):
            return Response(
                {"detail": f"{get_http_message(400)} Unsupported media type."},
                status=400,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": f"{get_http_message(400)} Episodes are supported only for 'tv' media type.",  # noqa: E501
                },
                status=400,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"{get_http_message(400)} Cannot query `{source}` for `{media_type}` media type",  # noqa: E501
                },
                status=400,
            )

        try:
            user_medias = BasicMedia.objects.filter_media(
                request.user,
                media_id,
                "episode",
                source,
                season_number=season_number,
                episode_number=episode_number,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": f"{get_http_message(500)}", "errors": str(e)},
                status=500,
            )

        consumption = user_medias.filter(id=consumption_id).first()
        if not consumption:
            return Response(
                {"detail": f"{get_http_message(404)} Consumption entry not found."},
                status=404,
            )

        consumption.delete()

        return Response(status=204)

    def get(
        self,
        request,
        media_type,
        source,
        media_id,
        season_number,
        episode_number,
        consumption_id,
    ):
        """Retrieve a specific consumption history entry for a specific episode."""
        if not check_valid_type(media_type):
            return Response(
                {"detail": f"{get_http_message(400)} Unsupported media type."},
                status=400,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": f"{get_http_message(400)} Episodes are supported only for 'tv' media type.",  # noqa: E501
                },
                status=400,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"{get_http_message(400)} Cannot query `{source}` for `{media_type}` media type",  # noqa: E501
                },
                status=400,
            )

        try:
            user_medias = BasicMedia.objects.filter_media(
                request.user,
                media_id,
                "episode",
                source,
                season_number=season_number,
                episode_number=episode_number,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": f"{get_http_message(500)}", "errors": str(e)},
                status=500,
            )

        consumption = user_medias.filter(id=consumption_id).first()
        if not consumption:
            return Response(
                {"detail": f"{get_http_message(404)} Consumption entry not found."},
                status=404,
            )

        serialized_data = serialize_data(
            consumption,
            serializer_class=HistorySerializer,
        )
        return Response(serialized_data, status=200)

    def patch(
        self,
        request,
        media_type,
        source,
        media_id,
        season_number,
        episode_number,
        consumption_id,
    ):
        """Update a specific consumption history entry for a specific episode."""
        if not check_valid_type(media_type):
            return Response(
                {"detail": f"{get_http_message(400)} Unsupported media type."},
                status=400,
            )

        if media_type != MediaTypes.TV.value:
            return Response(
                {
                    "detail": f"{get_http_message(400)} Episodes are supported only for 'tv' media type.",
                },
                status=400,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"{get_http_message(400)} Cannot query `{source}` for `{media_type}` media type",
                },
                status=400,
            )

        try:
            user_medias = BasicMedia.objects.filter_media(
                request.user,
                media_id,
                "episode",
                source,
                season_number=season_number,
                episode_number=episode_number,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": f"{get_http_message(500)}", "errors": str(e)},
                status=500,
            )

        consumption = user_medias.filter(id=consumption_id).first()
        if not consumption:
            return Response(
                {"detail": f"{get_http_message(404)} Consumption entry not found."},
                status=404,
            )

        body = request.data or {}

        validated_body, error = validate_body(body, "episode")

        if error:
            return Response(
                {"detail": f"{get_http_message(400)} {error}"},
                status=400,
            )

        for field, value in validated_body.items():
            if hasattr(consumption, field):
                setattr(consumption, field, value)

        try:
            consumption.save()
        except Exception as e:  # noqa: BLE001
            return Response(
                {"detail": f"{get_http_message(400)}", "errors": str(e)},
                status=400,
            )

        consumption.refresh_from_db()

        serialized_data = serialize_data(
            consumption,
            serializer_class=HistorySerializer,
        )
        return Response(serialized_data, status=200)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/[episode_number]/sync/
class MediaEpisodeSyncView(drf_views.APIView):
    """Sync episode view."""

    permission_classes = [permissions.IsAuthenticated]

    def post(
        self,
        request,
        media_type,
        source,
        media_id,
        season_number,
        _,
    ):
        """Redirect episode sync to season sync."""
        season_sync = MediaSeasonSyncView()
        return season_sync.post(
            request,
            media_type=media_type,
            source=source,
            media_id=media_id,
            season_number=season_number,
        )


# /api/v1/search/[media_type]/
class SearchProviderView(drf_views.APIView):
    """Search view."""

    serializer_class = MediaSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, media_type):
        """Search for media using the specified provider."""
        search = request.GET.get("search", "")
        source = request.GET.get("source", None)
        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        if not check_valid_type(media_type, complete=True):
            return Response(
                {"detail": f"{get_http_message(400)} Unsupported media type."},
                status=400,
            )
        if media_type in ("season", "episode"):
            # Since data of seasons and episodes (title, author, description,
            # etc.) is not saved in the db but retrieved every time, it's not
            # possible to search for them
            return Response(
                {
                    "detail": f"{get_http_message(400)} Search for {media_type} is not supported.",
                },
            )

        if source == Sources.MANUAL.value:
            # TODO: search for manual source should query only already tracked media
            # Since manual source data is user-defined and not indexed,
            # searching is not supported
            return Response(
                {"detail": "Bad Request. Search for manual source is not supported."},
                status=400,
            )

        results_accum = []
        page = 1
        last_response = None

        try:
            while True:
                last_response = services.search(media_type, search, page, source)
                if (
                    not isinstance(last_response, dict)
                    or "results" not in last_response
                ):
                    break
                page_results = last_response.get("results", []) or []
                results_accum.extend(page_results)
                if len(results_accum) >= offset + limit:
                    break
                total_pages = last_response.get("total_pages")
                if total_pages and page >= total_pages:
                    break
                if not page_results:
                    break

                page += 1

        except Exception as e:
            return Response(
                {"detail": f"{get_http_message(500)}", "errors": str(e)},
                status=500,
            )

        total = (
            last_response.get("total_results")
            if isinstance(last_response, dict)
            else len(results_accum)
        )

        # TODO: use pagination helpers
        sliced = results_accum[offset : offset + limit]
        next_url = None
        prev_url = None
        if offset + limit < (total or len(results_accum)):
            next_url = make_page_url(request, limit, offset + limit)
        if offset > 0:
            prev_offset = max(0, offset - limit)
            prev_url = make_page_url(request, limit, prev_offset)

        payload = {
            "pagination": {
                "total": total or len(results_accum),
                "limit": limit,
                "offset": offset,
                "next": next_url,
                "previous": prev_url,
            },
            "results": sliced,
        }

        return Response(payload, status=200)


# /api/v1/statistics/
class StatisticsView(drf_views.APIView):
    """Statistics view."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        """Retrieve statistics for the authenticated user."""
        # TODO: Possibly don't use WebUI needed statistics but compute them for API
        timeformat = "%Y-%m-%d"
        today = localdate()
        one_year_ago = today.replace(year=today.year - 1).strftime(timeformat)
        today = today.strftime(timeformat)

        user = request.user
        start_date = request.GET.get("start_date", one_year_ago)
        end_date = request.GET.get("end_date", today)
        if not start_date:
            start_date = one_year_ago
        if not end_date:
            end_date = today

        if start_date == "all" and end_date == "all":
            start_date = None
            end_date = None
        else:
            try:
                start_date = try_parse_date(start_date)
                end_date = try_parse_date(end_date)
            except (TypeError, ValueError):
                return Response(
                    {"detail": f"{get_http_message(400)} Invalid date format."},
                    status=400,
                )

            if start_date and end_date:
                start_date = make_aware(
                    datetime.combine(start_date, datetime.min.time()),
                )
                end_date = make_aware(
                    datetime.combine(end_date, datetime.max.time()),
                )
        user_media, media_count = get_user_media(
            user,
            start_date,
            end_date,
        )
        media_type_distribution = get_media_type_distribution(
            media_count,
        )
        score_distribution, top_rated = get_score_distribution(user_media)
        status_distribution = get_status_distribution(user_media)
        status_pie_chart_data = get_status_pie_chart_data(
            status_distribution,
        )
        timeline = get_timeline(user_media)
        activity_data = get_activity_data(request.user, start_date, end_date)

        statistics = {
            "start_date": start_date,
            "end_date": end_date,
            "media_count": media_count,
            "activity_data": activity_data,
            "media_type_distribution": media_type_distribution,
            "score_distribution": score_distribution,
            "top_rated": serialize_data(top_rated, many=True),
            "status_distribution": status_distribution,
            "status_pie_chart_data": status_pie_chart_data,
            "timeline": {
                month: serialize_data(
                    items,
                    many=True,
                    context={"request": request},
                    serializer_class=TimelineItemSerializer,
                )
                for month, items in (timeline or {}).items()
            },
        }

        return Response(statistics, status=200)
