from calendar import monthrange
from datetime import date

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError
from django.utils.timezone import datetime, localdate, make_aware
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

from .helpers import (
    MANUAL_SORTS,
    MEDIA_TYPE_COMPLETE_MODEL_MAP,
    MEDIA_TYPE_VALID_LIST,
    apply_aggregated_sort,
    apply_manual_sort_for_type,
    check_source_type,
    check_valid_type,
    fetch_media_list,
    get_complete_sorts,
    get_http_message,
    get_media_status,
    make_page_url,
    paginate_data,
    parse_limit_offset,
    parse_sort_filter,
    try_parse_date,
)
from .history_processor import delete_entry, get_entry, process_history_entries
from .serializers import HistoryEntrySerializer, MediaSerializer, TimelineItemSerializer

# TODO!!!: Deduplicate helper code in helpers.py and history_processor.py


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

        if start_date:
            if end_date:
                try:
                    start_date_parsed = parse_date(start_date)
                    end_date_parsed = parse_date(end_date)
                    if not start_date_parsed or not end_date_parsed:
                        raise ValueError("Invalid date format")
                    first_day = start_date_parsed
                    last_day = end_date_parsed
                except (TypeError, ValueError):
                    return Response(
                        {"detail": f"{get_http_message(400)} Invalid date format."},
                        status=400,
                    )
            else:
                try:
                    start_date_parsed = parse_date(start_date)
                    if not start_date_parsed:
                        raise ValueError("Invalid date format")
                    first_day = start_date_parsed
                    last_day = timezone.localdate()
                except (TypeError, ValueError):
                    return Response(
                        {"detail": f"{get_http_message(400)} Invalid date format."},
                        status=400,
                    )
        else:
            try:
                if month_q and year_q:
                    current = date(int(year_q), int(month_q), 1)
                else:
                    current = timezone.localdate()
            except (TypeError, ValueError):
                current = timezone.localdate()

            month = current.month
            year = current.year

            is_december = month == 12

            first_day = date(year, month, 1)
            if is_december:
                last_day = date(year, 12, 31)
            else:
                last_day = date(year, month + 1, 1) - timedelta(days=1)

        try:
            releases = Event.objects.get_user_events(request.user, first_day, last_day)
        except Exception as e:
            return Response(
                {"detail": f"{get_http_message(500)}", "errors": str(e)},
                status=500,
            )

        paginated_data = paginate_data(request, releases, limit, offset, "events")

        return Response(paginated_data)


# /api/v1/calendar/update/
class UpdateCalendarView(drf_views.APIView):
    """Update calendar view."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        """Trigger calendar events update for the authenticated user."""
        tasks.reload_calendar.delay(request.user)
        return Response(
            {"detail": f"{get_http_message(202)} Task queued"},
            status=202,
        )


# /api/v1/history/[media_type]/[history_id]
class MediaTypeHistoryDetailView(drf_views.APIView):
    """History record view."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, media_type, history_id):
        """Retrieve the history record for a specific media."""
        if not check_valid_type(media_type, complete=True):
            return Response(
                {"detail": f"{get_http_message(400)} Unsupported media type."},
                status=400,
            )

        try:
            history_entry = get_entry(media_type, history_id, request.user)
            serialized = HistoryEntrySerializer(history_entry)
            return Response(serialized.data, status=200)
        except Exception as e:
            return Response(
                {
                    "detail": f"{get_http_message(404)} History record not found",
                    "errors": str(e),
                },
                status=404,
            )

    def delete(self, request, media_type, history_id):
        """Delete the history record for a specific media."""
        if not check_valid_type(media_type, complete=True):
            return Response(
                {"detail": f"{get_http_message(400)} Unsupported media type."},
                status=400,
            )

        try:
            delete_entry(media_type, history_id, request.user)
            return Response({"detail": "Record removed correctly"}, status=204)
        except Exception as e:
            return Response(
                {
                    "detail": f"{get_http_message(404)} History record not found",
                    "errors": str(e),
                },
                status=404,
            )


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
        exclude = (
            request.GET.get("exclude", "").split(",")
            if request.GET.get("exclude")
            else []
        )

        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        if not status:
            status = MediaStatusChoices.ALL
        else:
            try:
                status = get_media_status(int(status))
            except (TypeError, ValueError):
                return Response(
                    {"detail": f"{get_http_message(404)} Invalid status"},
                    status=404,
                )

        results = []
        sort, sort_order = parse_sort_filter(sort_filter)
        already_sorted = False

        if media_type:
            if not check_valid_type(media_type, complete=True):
                return Response(
                    {"detail": f"{get_http_message(400)} Unsupported media type."},
                    status=400,
                )

            sort_list = get_complete_sorts(media_type)

            if sort in sort_list:
                media_sort = sort
                already_sorted = True
            else:
                media_sort = ""

            results.extend(
                fetch_media_list(user, media_type, status, media_sort, search),
            )

            if not already_sorted and sort != "":
                if sort in MANUAL_SORTS:
                    results = apply_manual_sort_for_type(results, sort)
                    if isinstance(results, Response):
                        return results
                else:
                    return Response(
                        {"detail": f"{get_http_message(404)} Invalid sorting"},
                        status=404,
                    )

        else:
            # Exclude EPISODES and SEASONS from results by default
            # to declutter the results
            excluded_set = {e.strip().lower() for e in exclude if e and e.strip()}
            allowed_types = [t for t in MEDIA_TYPE_VALID_LIST if t not in excluded_set]

            for t in allowed_types:
                results.extend(fetch_media_list(user, t, status, "", search))

            if sort != "":
                sort_list = get_complete_sorts(None)
                if sort in sort_list:
                    results = apply_aggregated_sort(results, sort)
                    if isinstance(results, Response):
                        return results
                else:
                    return Response(
                        {"detail": f"{get_http_message(404)} Invalid sorting"},
                        status=404,
                    )

        if sort_order == "desc":
            results.reverse()

        paginated_data = paginate_data(request, results, limit, offset, "media")
        return Response(paginated_data)


# /api/v1/media/[media_type]/
class MediaTypeListView(drf_views.APIView):
    """List media by type view."""

    serializer_class = MediaSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, media_type):
        """Retrieve the list of media of a specific media type."""
        user = request.user
        status = request.GET.get("status", "")
        search = request.GET.get("search", "")
        sort_filter = request.GET.get("sort", "")
        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        if not status:
            status = MediaStatusChoices.ALL
        else:
            try:
                status = get_media_status(int(status))
            except (TypeError, ValueError):
                return Response(
                    {"detail": f"{get_http_message(404)} Invalid status"},
                    status=404,
                )

        results = []
        sort, sort_order = parse_sort_filter(sort_filter)
        already_sorted = False

        if not check_valid_type(media_type, complete=True):
            return Response(
                {"detail": f"{get_http_message(400)} Unsupported media type."},
                status=400,
            )

        sort_list = get_complete_sorts(media_type)

        if sort in sort_list:
            media_sort = sort
            already_sorted = True
        else:
            media_sort = ""

        results = fetch_media_list(user, media_type, status, media_sort, search)

        if not already_sorted and sort != "":
            if sort in MANUAL_SORTS:
                results = apply_manual_sort_for_type(results, sort)
                if isinstance(results, Response):
                    return results
            else:
                return Response(
                    {"detail": f"{get_http_message(404)} Invalid sorting"},
                    status=404,
                )

        if sort_order == "desc":
            results.reverse()

        paginated_data = paginate_data(request, results, limit, offset, "media")
        return Response(paginated_data)

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
            serializer = MediaSerializer(media_form.instance)
            return Response(serializer.data, status=201)

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
        serializer = MediaSerializer(media_form.instance)
        return Response(serializer.data, status=201)


# /api/v1/media/[media_type]/[source]/[media_id]/
class MediaDetailView(drf_views.APIView):
    """Media view."""

    serializer_class = MediaSerializer
    permission_classes = [permissions.IsAuthenticated]

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
        except Exception as e:
            return Response(
                {"detail": f"{get_http_message(500)}", "errors": str(e)},
                status=500,
            )

        tracked = False
        created = ""
        score = ""
        progress = ""
        progressed_at = ""
        status = ""
        start_date = ""
        end_date = ""
        notes = ""

        try:
            user_medias = BasicMedia.objects.filter_media_prefetch(
                user,
                media_id,
                media_type,
                source,
            )
        except Exception as e:
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

        if (
            media_type == MediaTypes.TV.value
            and "related" in media_metadata
            and media_metadata["related"] is not None
            and "seasons" in media_metadata["related"]
            and media_metadata["related"]["seasons"] is not None
        ):
            for season in media_metadata["related"]["seasons"]:
                season["item_id"] = (
                    f"{media_type}/{source}/{media_id}/{season.get('season_number')}"
                )
                season["parent_id"] = f"{media_type}/{source}/{media_id}"

        if user_medias:
            serialized = MediaSerializer(user_medias[0]).data
            tracked = True
            created = serialized["created_at"]
            score = serialized["score"]
            progress = serialized["progress"]
            progressed_at = serialized["progressed_at"]
            status = serialized["status"]
            start_date = serialized["start_date"]
            end_date = serialized["end_date"]
            notes = serialized["notes"]

        media_metadata["item_id"] = f"{media_type}/{source}/{media_id}"
        media_metadata["parent_id"] = None
        media_metadata["tracked"] = tracked
        media_metadata["user_created"] = created
        media_metadata["user_score"] = score
        media_metadata["user_progress"] = progress
        media_metadata["user_progressed_at"] = progressed_at
        media_metadata["user_status"] = status
        media_metadata["user_start_date"] = start_date
        media_metadata["user_end_date"] = end_date
        media_metadata["user_notes"] = notes

        if media_type == MediaTypes.TV.value:
            details = media_metadata.get("details", {})
            if "tvdb_id" in media_metadata:
                details["tvdb_id"] = media_metadata.pop("tvdb_id")
            if "last_episode_season" in media_metadata:
                details["last_episode_season"] = media_metadata.pop(
                    "last_episode_season",
                )
            if "next_episode_season" in media_metadata:
                details["next_episode_season"] = media_metadata.pop(
                    "next_episode_season",
                )
            media_metadata["details"] = details
        elif media_type == MediaTypes.COMIC.value:
            details = media_metadata.get("details", {})
            if "last_issue_id" in media_metadata:
                details["last_issue_id"] = media_metadata.pop("last_issue_id")
            media_metadata["details"] = details

        return Response(media_metadata, status=200)

    def patch(self, request, media_type, source, media_id):  # noqa: ARG002, D102
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
        except Exception as e:
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


# /api/v1/media/[media_type]/[source]/[media_id]/history/
class MediaHistoryView(drf_views.APIView):
    """Media history view."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, media_type, source, media_id):
        """Retrieve history timeline entries for a specific media."""
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

        timeline_entries = []
        if user_medias.exists():
            timeline_entries = [
                entry
                for media in user_medias
                if (history := media.history.all())
                for entry in process_history_entries(history, media_type)
            ]

        paginated_data = paginate_data(
            request,
            timeline_entries,
            limit,
            offset,
            "history",
        )
        return Response(paginated_data, status=200)


# /api/v1/media/[media_type]/[source]/[media_id]/seasons/
class MediaSeasonsView(drf_views.APIView):
    """Media seasons view."""

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
        except Exception as e:
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
            seasons = media_metadata["related"]["seasons"] or []
            for season in seasons:
                season_number = season.get("season_number")
                season["item_id"] = (
                    f"{media_type}/{source}/{media_id}/{season_number}"
                    if season_number is not None
                    else f"{media_type}/{source}/{media_id}/"
                )
                season["parent_id"] = f"{media_type}/{source}/{media_id}"

        paginated = paginate_data(request, seasons, limit, offset, "seasons")
        return Response(paginated, status=200)


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


# /api/v1/media/[media_type]/[source]/[media_id]/lists/
class MediaAddToListView(drf_views.APIView):  # noqa: D101
    def post(self, request, media_type, source, media_id):  # noqa: ARG002, D102
        return Response({"detail": f"{get_http_message(501)}"}, status=501)

    def delete(self, request, media_type, source, media_id):  # noqa: ARG002, D102
        return Response({"detail": f"{get_http_message(501)}"}, status=501)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/
class MediaSeasonDetailView(drf_views.APIView):
    """Season view."""

    serializer_class = MediaSerializer
    permission_classes = [permissions.IsAuthenticated]

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

        tracked = False
        created = ""
        score = ""
        progress = ""
        progressed_at = ""
        status = ""
        start_date = ""
        end_date = ""
        notes = ""

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

        episodes = []
        if "episodes" in media_metadata and media_metadata["episodes"] is not None:
            episodes = media_metadata.pop("episodes")
            for episode in episodes:
                episode["item_id"] = (
                    f"{media_type}/{source}/{media_id}/{season_number}/{episode.get('episode_number')}"
                )
                episode["parent_id"] = (
                    f"{media_type}/{source}/{media_id}/{season_number}"
                )

        if user_medias:
            serialized = MediaSerializer(user_medias[0]).data
            tracked = True
            created = serialized["created_at"]
            score = serialized["score"]
            progress = serialized["progress"]
            progressed_at = serialized["progressed_at"]
            status = serialized["status"]
            start_date = serialized["start_date"]
            end_date = serialized["end_date"]
            notes = serialized["notes"]

        media_metadata["related"] = {"episodes": episodes}
        media_metadata["item_id"] = f"{media_type}/{source}/{media_id}/{season_number}"
        media_metadata["parent_id"] = f"{media_type}/{source}/{media_id}"
        media_metadata["tracked"] = tracked
        media_metadata["user_created"] = created
        media_metadata["user_score"] = score
        media_metadata["user_progress"] = progress
        media_metadata["user_progressed_at"] = progressed_at
        media_metadata["user_status"] = status
        media_metadata["user_start_date"] = start_date
        media_metadata["user_end_date"] = end_date
        media_metadata["user_notes"] = notes

        return Response(media_metadata, status=200)


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
            episodes = media_metadata["episodes"] or []
            for episode in episodes:
                episode["item_id"] = (
                    f"{media_type}/{source}/{media_id}/{season_number}/{episode.get('episode_number')}"
                )
                episode["parent_id"] = (
                    f"{media_type}/{source}/{media_id}/{season_number}"
                )

        paginated = paginate_data(request, episodes, limit, offset, "episodes")
        return Response(paginated, status=200)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/history/
class MediaSeasonHistoryView(drf_views.APIView):
    """History season view."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, media_type, source, media_id, season_number):
        """Retrieve history timeline entries for a specific season of a tv serie."""
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

        timeline_entries = []
        if user_medias.exists():
            timeline_entries = [
                entry
                for media in user_medias
                if (history := media.history.all())
                for entry in process_history_entries(history, "season")
            ]

        paginated_data = paginate_data(
            request,
            timeline_entries,
            limit,
            offset,
            "history",
        )
        return Response(paginated_data, status=200)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/sync/
class MediaSeasonSyncView(drf_views.APIView):
    """Sync season."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, _, media_type, source, media_id, season_number):
        """Trigger sync of metadata from provider (non-manual sources only)."""
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

            episode["item_id"] = (
                f"{media_type}/{source}/{media_id}/{season_number}/{episode_number}"
            )
            episode["parent_id"] = f"{media_type}/{source}/{media_id}/{season_number}"

        tracked = False
        created = ""
        score = ""
        progress = ""
        progressed_at = ""
        status = ""
        start_date = ""
        end_date = ""
        notes = ""

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

        if user_medias:
            serialized = MediaSerializer(user_medias[0]).data
            # print(serialized)
            tracked = True
            created = serialized["created_at"]
            score = serialized["score"]
            # progress = serialized["progress"]
            # progressed_at = serialized["progressed_at"]
            status = 3
            start_date = serialized["start_date"]
            end_date = serialized["end_date"]
            # notes = serialized["notes"]

        episode["item_id"] = (
            f"{media_type}/{source}/{media_id}/{season_number}/{episode_number}"
        )
        episode["parent_id"] = f"{media_type}/{source}/{media_id}/{season_number}"
        episode["tracked"] = tracked
        episode["user_created"] = created
        episode["user_score"] = score
        episode["user_progress"] = progress
        episode["user_progressed_at"] = progressed_at
        episode["user_status"] = status
        episode["user_start_date"] = start_date
        episode["user_end_date"] = end_date
        episode["user_notes"] = notes

        # Get season source_url and append episode number
        season_source_url = media_metadata.get("source_url")
        source_url = None
        if season_source_url:
            source_url = f"{season_source_url}/episode/{episode_number}"

        complete_media = {
            "media_id": int(media_id),
            "source": source,
            "source_url": source_url,
            "title": episode.get("name"),
            "max_progress": 1,
            "image": episode.get("still_path"),
            "synopsis": episode.get("overview"),
            "genres": media_metadata.get("genres", []),
            "score": episode.get("vote_average"),
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
            "item_id": episode["item_id"],
            "parent_id": episode["parent_id"],
            "tracked": episode["tracked"],
            "user_created": episode["user_created"],
            "user_score": episode["user_score"],
            "user_progress": episode["user_progress"],
            "user_progressed_at": episode["user_progressed_at"],
            "user_status": episode["user_status"],
            "user_start_date": episode["user_start_date"],
            "user_end_date": episode["user_end_date"],
            "user_notes": episode["user_notes"],
        }

        return Response(complete_media, status=200)


# /api/v1/media/[media_type]/[source]/[media_id]/[season_number]/[episode_number]/history/
class MediaEpisodeHistoryView(drf_views.APIView):
    """History episode view."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, media_type, source, media_id, season_number, episode_number):
        """Retrieve history timeline entries for a specific episode of a tv serie."""
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

        timeline_entries = []
        if user_medias.exists():
            timeline_entries = [
                entry
                for media in user_medias
                if (history := media.history.all())
                for entry in process_history_entries(history, "episode")
            ]

        paginated_data = paginate_data(
            request,
            timeline_entries,
            limit,
            offset,
            "history",
        )
        return Response(paginated_data, status=200)


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

        if start_date == "all" and end_date == "all":
            start_date = None
            end_date = None
        else:
            start_date = parse_date(start_date)
            end_date = parse_date(end_date)

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
            "top_rated": MediaSerializer(top_rated, many=True).data,
            "status_distribution": status_distribution,
            "status_pie_chart_data": status_pie_chart_data,
            "timeline": {
                month: TimelineItemSerializer(
                    items,
                    many=True,
                    context={"request": request},
                ).data
                for month, items in (timeline or {}).items()
            },
        }

        return Response(statistics, status=200)
