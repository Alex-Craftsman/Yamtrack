import logging
from urllib.parse import urlencode

from django.utils.dateparse import parse_date
from rest_framework.response import Response

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
from users.models import MediaStatusChoices

logger = logging.getLogger(__name__)

HTTP_STATUS_MAP = {
    200: "OK.",
    201: "Created.",
    202: "Accepted.",
    204: "No content.",
    400: "Bad request.",
    401: "Unauthorized.",
    403: "Permission denied.",
    404: "Not found.",
    405: "Method not allowed.",
    500: "Internal server error.",
    501: "Not implemented.",
}

MEDIA_STATUS_MAP = {
    "Planning": 0,
    "In progress": 1,
    "Paused": 2,
    "Completed": 3,
    "Dropped": 4,
}

MEDIA_TYPE_COMPLETE_MODEL_MAP = {
    MediaTypes.TV.value: TV,
    MediaTypes.SEASON.value: Season,
    MediaTypes.EPISODE.value: Episode,
    MediaTypes.MOVIE.value: Movie,
    MediaTypes.ANIME.value: Anime,
    MediaTypes.MANGA.value: Manga,
    MediaTypes.GAME.value: Game,
    MediaTypes.BOOK.value: Book,
    MediaTypes.COMIC.value: Comic,
}

MEDIA_TYPE_COMPLETE_VALID_LIST = list(MEDIA_TYPE_COMPLETE_MODEL_MAP.keys())

MEDIA_TYPE_MODEL_MAP = {
    MediaTypes.TV.value: TV,
    MediaTypes.MOVIE.value: Movie,
    MediaTypes.ANIME.value: Anime,
    MediaTypes.MANGA.value: Manga,
    MediaTypes.GAME.value: Game,
    MediaTypes.BOOK.value: Book,
    MediaTypes.COMIC.value: Comic,
}

MEDIA_TYPE_VALID_LIST = list(MEDIA_TYPE_MODEL_MAP.keys())

MAX_RESULT_LIMIT = 200

EXISTING_SORTS = [
    "start_date",
    "end_date",
] + [f.name for f in Item._meta.fields]

SEASONS_ADDITIONAL_SORTS = [
    "progress",
]

EPISODES_ADDITIONAL_SORTS = [
    "progress",
]

MANUAL_SORTS = [
    "added",
    "updated",
    "itemid",
]

VALID_SOURCES = {
    MediaTypes.TV.value: ["tmdb", "manual"],
    MediaTypes.SEASON.value: ["tmdb", "manual"],
    MediaTypes.EPISODE.value: ["tmdb", "manual"],
    MediaTypes.MOVIE.value: ["tmdb", "manual"],
    MediaTypes.ANIME.value: ["mal", "manual"],
    MediaTypes.MANGA.value: ["mal", "mangaupdates", "manual"],
    MediaTypes.GAME.value: ["igdb", "manual"],
    MediaTypes.BOOK.value: ["openlibrary", "hardcover", "manual"],
    MediaTypes.COMIC.value: ["comicvine", "manual"],
}


def build_item_id(item):
    """Build the item_id string for the given item."""
    if not item:
        return None
    media_type = item.media_type
    children = ""

    if item.media_type == "season":
        children = f"/{item.season_number}"
        media_type = "tv"
    elif item.media_type == "episode":
        children = f"/{item.season_number}/{item.episode_number}"
        media_type = "tv"

    return f"{media_type}/{item.source}/{item.media_id}{children}"


def build_parent_id(item):
    """Build the parent_id string for seasons and episodes."""
    if not item:
        return None
    if item.media_type == "season":
        return f"tv/{item.source}/{item.media_id}"
    if item.media_type == "episode" and hasattr(item, "season_number"):
        return f"tv/{item.source}/{item.media_id}/{item.season_number}"
    return None


def check_valid_type(media_type, *, complete=False):
    """Check if the media type is valid."""
    if complete:
        return media_type in MEDIA_TYPE_COMPLETE_VALID_LIST
    return media_type in MEDIA_TYPE_VALID_LIST


def check_source_type(media_type, source):
    """Check the source is valid for the given media type."""
    if media_type in VALID_SOURCES:
        return source in VALID_SOURCES[media_type]
    return False


def fetch_media_list(user, media_type, status, sort_filter, search):
    """Return a plain list of the requested media."""
    if media_type == MediaTypes.EPISODE.value:
        qs = Episode.objects.filter(related_season__user=user)
        if status and status != MediaStatusChoices.ALL:
            try:
                qs = qs.filter(related_season__status=status)
            except Exception:
                return []
        if search:
            qs = qs.filter(item__title__icontains=search)
        return qs

    return list(
        BasicMedia.objects.get_media_list(
            user=user,
            media_type=media_type,
            status_filter=status,
            sort_filter=sort_filter,
            search=search,
        ),
    )


def fetch_results_for_type(user, media_type, status, sort, search):
    """Fetch and sort results for a specific media type."""
    sort_list = get_sorts(media_type, sort_type="existing")
    media_sort = sort if sort in sort_list else ""
    already_sorted = bool(media_sort)

    results = fetch_media_list(user, media_type, status, media_sort, search)

    if not already_sorted and sort:
        if sort in get_sorts(media_type, sort_type="manual"):
            results = apply_manual_sort_for_type(results, sort)
        else:
            return None, True

    return results, False


def fetch_results_all_types(user, status, sort, search, exclude):
    """Fetch and sort results across all media types."""
    excluded_set = {e.strip().lower() for e in exclude if e and e.strip()}
    allowed_types = [t for t in MEDIA_TYPE_VALID_LIST if t not in excluded_set]

    results = []
    for t in allowed_types:
        results.extend(fetch_media_list(user, t, status, "", search))

    if sort:
        sort_list = get_sorts(None, sort_type="all")
        if sort in sort_list:
            results = apply_aggregated_sort(results, sort)
        else:
            return None, True

    return results, False


def get_sorts(media_type, *, sort_type="all"):
    """Return the list of valid sorts for complete media types."""
    if sort_type == "all":
        sort_list = EXISTING_SORTS.copy()
        if media_type == MediaTypes.SEASON.value:
            sort_list += SEASONS_ADDITIONAL_SORTS
        if media_type == MediaTypes.EPISODE.value:
            sort_list += EPISODES_ADDITIONAL_SORTS
        sort_list += MANUAL_SORTS
        return sort_list
    if sort_type == "manual":
        return MANUAL_SORTS
    if sort_type == "existing":
        sort_list = EXISTING_SORTS.copy()
        if media_type == MediaTypes.SEASON.value:
            sort_list += SEASONS_ADDITIONAL_SORTS
        if media_type == MediaTypes.EPISODE.value:
            sort_list += EPISODES_ADDITIONAL_SORTS
        return sort_list
    return []


def get_http_message(status):
    """Return the standard HTTP status message for the given status code."""
    return HTTP_STATUS_MAP.get(status, "Unknown status.")


def get_media_status(status):
    """Transform the media status from integer to a valid class."""
    return MEDIA_STATUS_MAP.get(status)


def get_progress_from_status(status):
    """Return the progress value based on the media status."""
    if status == MEDIA_STATUS_MAP["Completed"]:
        return 1
    return 0


def make_page_url(request, limit, new_offset):
    """Build a page URL with the given limit and offset."""
    params = {k: v for k, v in request.GET.items() if v is not None and v != ""}
    params["limit"] = str(limit)
    params["offset"] = str(new_offset)
    return request.build_absolute_uri(request.path + "?" + urlencode(params))


def paginate_data(request, results, limit, offset):
    """Paginate the results based on the limit and offset.

    Returns raw paginated data without serialization.
    Serialization should be handled by the view.
    """
    total = len(results)
    start = offset
    end = offset + limit
    paginated = results[start:end]

    next_url = None
    prev_url = None
    if end < total:
        next_url = make_page_url(request, limit, end)
    if start > 0:
        prev_offset = max(0, start - limit)
        prev_url = make_page_url(request, limit, prev_offset)

    pagination = {
        "total": total,
        "limit": limit,
        "offset": offset,
        "next": next_url,
        "previous": prev_url,
    }
    return {"pagination": pagination, "results": paginated}


def parse_excluded_items(request):
    """Parse excluded items from the request query parameters."""
    exclude_param = request.GET.get("exclude", "")
    if exclude_param:
        return exclude_param.split(",")
    return []


def parse_limit_offset(request):
    """Parse and validate limit/offset query params.

    If no error, error_response is None. On validation error, returns a DRF Response.
    """
    raw_limit = request.GET.get("limit")
    raw_offset = request.GET.get("offset")

    if raw_limit in [None, ""]:
        limit = 20
    else:
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            return (
                None,
                None,
                Response(
                    {"detail": f"{get_http_message(400)} Invalid limit parameter"},
                    status=400,
                ),
            )
    if raw_offset in [None, ""]:
        offset = 0
    else:
        try:
            offset = int(raw_offset)
        except (TypeError, ValueError):
            return (
                None,
                None,
                Response(
                    {"detail": f"{get_http_message(400)} Invalid offset parameter"},
                    status=400,
                ),
            )
    if limit <= 0 or offset < 0:
        return (
            None,
            None,
            Response(
                {
                    "detail": f"{get_http_message(400)} limit must be >0 and offset must be >=0",
                },
                status=400,
            ),
        )

    limit = min(limit, MAX_RESULT_LIMIT)
    return limit, offset, None


def parse_status_param(status):
    """Parse and validate status parameter."""
    if not status:
        return MediaStatusChoices.ALL
    try:
        return get_media_status(int(status))
    except (TypeError, ValueError):
        return None


def try_parse_date(value):
    """Parse a date string and raise ValueError if invalid."""
    parsed = parse_date(value)
    if not parsed:
        msg = "Invalid date format"
        raise ValueError(msg)
    return parsed


# ---- Sorting ----


def parse_sort_filter(sort_filter):
    """Return (sort, sort_order) tuple from a sort_filter string like 'title_desc'."""
    if sort_filter and sort_filter != "":
        parts = sort_filter.split("_", 1)
        if len(parts) == 2:  # noqa: PLR2004
            return parts[0], parts[1]
        return parts[0], ""
    return "", ""


def itemid_key_compare(media):
    """Key function for sorting by item_id."""
    item = getattr(media, "item", None)
    media_type = getattr(item, "media_type", "")
    source = getattr(item, "source", "")
    media_id_raw = getattr(item, "media_id", 0)
    try:
        media_id_num = int(str(media_id_raw))
    except (TypeError, ValueError):
        media_id_num = 0
    return (media_type, source, media_id_num)


_AGGREGATED_MANUAL_SORT_KEYS = {
    "added": lambda media: media.created_at,
    "itemid": itemid_key_compare,
    "updated": lambda media: media.progressed_at,
}


def apply_manual_sort_for_type(results, sort):
    """Apply manual sorts used when a single media type is requested."""
    if sort not in _AGGREGATED_MANUAL_SORT_KEYS:
        return Response(
            {"detail": f"{get_http_message(400)} Invalid sorting"},
            status=400,
        )
    return results


_AGGREGATED_SORT_KEYS = {
    "added": lambda media: media.created_at,
    "ended": lambda media: media.end_date,
    "id": lambda media: int(media.id),
    "itemid": itemid_key_compare,
    "mediaid": lambda media: media.item.id,
    "progress": lambda media: int(media.progress),
    "source": lambda media: media.item.source,
    "started": lambda media: media.start_date,
    "title": lambda media: media.item.title.lower(),
    "type": lambda media: media.item.media_type,
    "updated": lambda media: media.progressed_at,
}


def apply_aggregated_sort(results, sort):
    """Apply sorting for the aggregated (multi-type) results."""
    if sort not in _AGGREGATED_SORT_KEYS:
        return Response(
            {"detail": f"{get_http_message(400)} Bad Request. Invalid sorting"},
            status=400,
        )
    results.sort(key=_AGGREGATED_SORT_KEYS[sort])
    return results
