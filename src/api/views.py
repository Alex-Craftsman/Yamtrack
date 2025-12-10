from datetime import date, timedelta

from django.db import IntegrityError
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.timezone import datetime
from rest_framework import permissions
from rest_framework import views as drf_views
from rest_framework.response import Response

from app.forms import ManualItemForm, get_form_class
from app.models import BasicMedia, Item, MediaTypes, Sources
from app.providers import services
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
    EPISODES_ADDITIONAL_SORTS,
    EXISTING_SORTS,
    MANUAL_SORTS,
    MEDIA_TYPE_COMPLETE_MODEL_MAP,
    MEDIA_TYPE_VALID_LIST,
    SEASONS_ADDITIONAL_SORTS,
    apply_aggregated_sort,
    apply_manual_sort_for_type,
    check_source_type,
    check_valid_type,
    fetch_media_list,
    get_media_status,
    make_page_url,
    paginate_data,
    parse_limit_offset,
    parse_sort_filter,
)
from .history_processor import delete_entry, process_history_entries
from .serializers import MediaSerializer, TimelineItemSerializer

# TODO!!!: Implement seasons and episodes
# - tv type has children seasons
# - season type has parent tv and children episodes (tv/source/id/season)
# - episode type has parent season (tv/source/id/season/episode)
# serializers need to be updated to include nested relationships, and also the logic for seasons and episodes in the views


# /api/v1/media/
class MediaListView(drf_views.APIView):
    """Retrieve the list of media for the authenticated user."""

    serializer_class = MediaSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
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
                return Response({"detail": "Not Found. Invalid status"}, status=404)

        results = []
        sort, sort_order = parse_sort_filter(sort_filter)
        already_sorted = False

        if media_type:
            if not check_valid_type(media_type, complete=True):
                return Response(
                    {"detail": "Bad Request. Unsupported media type."},
                    status=400,
                )

            sort_list = EXISTING_SORTS
            if media_type == "season":
                sort_list += SEASONS_ADDITIONAL_SORTS
            elif media_type == "episode":
                sort_list += EPISODES_ADDITIONAL_SORTS

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
                        {"detail": "Not Found. Invalid sorting"},
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
                sort_list = EXISTING_SORTS + MANUAL_SORTS
                if sort in sort_list:
                    results = apply_aggregated_sort(results, sort)
                    if isinstance(results, Response):
                        return results
                else:
                    return Response(
                        {"detail": "Not Found. Invalid sorting"},
                        status=404,
                    )

        if sort_order == "desc":
            results.reverse()

        paginated_data = paginate_data(request, results, limit, offset, "media")
        return Response(paginated_data)


# /api/v1/media/[media_type]/
class MediaTypeListView(drf_views.APIView):
    """Retrieve the list of media for the authenticated user for a specific media type."""

    serializer_class = MediaSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, media_type):
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
                return Response({"detail": "Not Found. Invalid status"}, status=404)

        results = []
        sort, sort_order = parse_sort_filter(sort_filter)
        already_sorted = False

        if not check_valid_type(media_type, complete=True):
            return Response(
                {"detail": "Bad Request. Unsupported media type."},
                status=400,
            )

        sort_list = EXISTING_SORTS
        if media_type == "season":
            sort_list += SEASONS_ADDITIONAL_SORTS
        elif media_type == "episode":
            sort_list += EPISODES_ADDITIONAL_SORTS

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
                    {"detail": "Not Found. Invalid sorting"},
                    status=404,
                )

        if sort_order == "desc":
            results.reverse()

        paginated_data = paginate_data(request, results, limit, offset, "media")
        return Response(paginated_data)

    def post(self, request, media_type):
        return Response({"detail": "Not Implemented"}, status=501)

        if not check_valid_type(media_type, complete=True):
            return Response(
                {"detail": "Bad Request. Unsupported media type."},
                status=400,
            )

        if not request.data:
            return Response({"detail": "Bad Request. Missing body."}, status=400)

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
                    {"detail": "Bad Request.", "errors": form.errors},
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
                    {"detail": "Bad Request.", "errors": media_form.errors},
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
                {"detail": "Bad Request. 'media_id' is required for provider sources."},
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
                {"detail": "Internal Server Error", "errors": str(e)},
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
                {"detail": "Bad Request. Unsupported media type."},
                status=400,
            )

        instance = model(item=item, user=request.user)

        media_data = dict(body)
        media_data.update({"source": item.source, "media_id": item.media_id})
        media_form = get_form_class(media_type)(media_data, instance=instance)
        if not media_form.is_valid():
            return Response(
                {"detail": "Bad Request.", "errors": media_form.errors},
                status=400,
            )

        media_form.save()
        serializer = MediaSerializer(media_form.instance)
        return Response(serializer.data, status=201)


# /api/v1/media/[media_type]/[source]/[media_id]/
class MediaDetailView(drf_views.APIView):
    """Operations on a specific media for the authenticated user."""

    serializer_class = MediaSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, media_type, source, media_id):
        """Retrieve details of a specific media for the authenticated user."""
        user = request.user

        if not check_valid_type(media_type):
            return Response(
                {"detail": "Bad Request. Unsupported media type."},
                status=400,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"Bad Request. Cannot query `{source}` for `{media_type}` media type",
                },
                status=400,
            )

        try:
            media_metadata = services.get_media_metadata(media_type, media_id, source)
        except Exception as e:
            return Response(
                {"detail": "Internal Server Error", "errors": str(e)},
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

        return Response(media_metadata)

    def patch(self, request, media_type, source, media_id):
        return Response({"detail": "Not Implemented"}, status=501)

    def delete(self, request, media_type, source, media_id):
        return Response({"detail": "Not Implemented"}, status=501)


# /api/v1/media/[media_type]/[source]/[media_id]/recommendations/
class MediaRecommendationsView(drf_views.APIView):
    """Retrieve recommendations for a specific media."""

    serializer_class = MediaSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, media_type, source, media_id):
        if not check_valid_type(media_type):
            return Response(
                {"detail": "Bad Request. Unsupported media type."},
                status=400,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"Bad Request. Cannot query `{source}` for `{media_type}` media type",
                },
                status=400,
            )

        try:
            media_metadata = services.get_media_metadata(media_type, media_id, source)
        except Exception as e:
            return Response(
                {"detail": "Internal Server Error", "errors": str(e)},
                status=500,
            )

        recommendations = []
        if (
            "related" in media_metadata
            and media_metadata["related"] is not None
            and "recommendations" in media_metadata["related"]
        ):
            recommendations = media_metadata["related"]["recommendations"]

        return Response(recommendations)


# /api/v1/media/[media_type]/[source]/[media_id]/history/
class MediaHistoryView(drf_views.APIView):
    """Retrieve the history timeline for a specific media."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, media_type, source, media_id):
        """Retrieve history timeline entries for a specific media."""
        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        if not check_valid_type(media_type):
            return Response(
                {"detail": "Bad Request. Unsupported media type."},
                status=400,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"Bad Request. Cannot query `{source}` for `{media_type}` media type",
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
        return Response(paginated_data)


# /api/v1/media/[media_type]/[source]/[media_id]/history/[history_id]
class MediaHistoryDetailView(drf_views.APIView):
    """Delete the history record for a specific media."""

    # TODO?: Should this be available at `/api/v1/history/[history_id]` since the history_id is not based on specific media?

    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request, media_type, source, media_id, history_id):
        if not check_valid_type(media_type):
            return Response(
                {"detail": "Bad Request. Unsupported media type."},
                status=400,
            )

        if not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"Bad Request. Cannot query `{source}` for `{media_type}` media type",
                },
                status=400,
            )

        try:
            delete_entry(media_type, history_id, request.user)
            return Response({"detail": "Record removed correctly"}, status=204)
        except Exception as e:
            return Response(
                {"detail": "Record not found", "errors": str(e)}, status=404
            )


# /api/v1/media/[media_type]/[source]/[media_id]/sync/
class MediaSyncView(drf_views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, media_type, source, media_id):
        return Response({"detail": "Not implemented"}, status=501)


# /api/v1/media/[media_type]/[source]/[media_id]/lists/
class MediaAddToListView(drf_views.APIView):
    def post(self, request, media_type, source, media_id):
        return Response({"detail": "Not implemented"}, status=501)

    def delete(self, request, media_type, source, media_id):
        return Response({"detail": "Not implemented"}, status=501)


# /api/v1/search/[media_type]/
class SearchProviderView(drf_views.APIView):
    """Search for media using the specified provider."""

    serializer_class = MediaSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, media_type):
        search = request.GET.get("search", "")
        source = request.GET.get("source", None)
        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        if not check_valid_type(media_type, complete=True):
            return Response(
                {"detail": "Bad Request. Unsupported media type."},
                status=400,
            )
        if media_type in ("season", "episode"):
            # Since data of seasons and episodes (title, author, description,
            # etc.) is not saved in the db but retrieved every time, it's not
            # possible to search for them
            return Response(
                {"detail": f"Bad Request. Search for {media_type} is not supported."},
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
                {"detail": "Internal Server Error", "errors": str(e)},
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

        return Response(payload)


# /api/v1/statistics/
class StatisticsView(drf_views.APIView):
    """Retrieve statistics for the authenticated user."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        # TODO: Possibly don't use WebUI needed statistics but compute them for API
        timeformat = "%Y-%m-%d"
        today = timezone.localdate()
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
                start_date = timezone.make_aware(
                    datetime.combine(start_date, datetime.min.time()),
                )
                end_date = timezone.make_aware(
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


# /api/v1/lists/
class ListsView(drf_views.APIView):
    def get(self, request):
        return Response({"detail": "Not implemented"}, status=501)

    def post(self, request):
        return Response({"detail": "Not implemented"}, status=501)


# /api/v1/lists/[id]/
class ListDetailView(drf_views.APIView):
    def get(self, request, id):
        return Response({"detail": "Not implemented"}, status=501)

    def patch(self, request, id):
        return Response({"detail": "Not implemented"}, status=501)

    def delete(self, request, id):
        return Response({"detail": "Not implemented"}, status=501)


# /api/v1/lists/[id]/items/
class ListAddItemView(drf_views.APIView):
    def post(self, request, id):
        return Response({"detail": "Not implemented"}, status=501)


# /api/v1/lists/[id]/items/[item_id]/
class ListRemoveItemView(drf_views.APIView):
    def delete(self, request, id, item_id):
        return Response({"detail": "Not implemented"}, status=501)


# /api/v1/calendar/
class CalendarView(drf_views.APIView):
    """Retrieve calendar events for the authenticated user."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
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
                        {"detail": "Bad Request. Invalid date format."},
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
                        {"detail": "Bad Request. Invalid date format."},
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
                {"detail": "Internal Server Error", "errors": str(e)},
                status=500,
            )

        paginated_data = paginate_data(request, releases, limit, offset, "events")

        return Response(paginated_data)


# /api/v1/calendar/update/
class UpdateCalendarView(drf_views.APIView):
    """Trigger calendar events update for the authenticated user."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        tasks.reload_calendar.delay(request.user)
        return Response({"detail": "Accepted. Task queued"}, status=202)
