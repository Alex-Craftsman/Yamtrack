from urllib.parse import urlencode

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

from .serializers import EventSerializer, MediaSerializer

MEDIA_TYPE_MODEL_MAP = {
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


def get_media_status(status):
    """Transform the media status from integer to a valid class."""
    match status:
        case 0:
            return MediaStatusChoices.PLANNING
        case 1:
            return MediaStatusChoices.IN_PROGRESS
        case 2:
            return MediaStatusChoices.PAUSED
        case 3:
            return MediaStatusChoices.COMPLETED
        case 4:
            return MediaStatusChoices.DROPPED
        case _:
            return MediaStatusChoices.ALL


def make_page_url(request, limit, new_offset):
    """Build a page URL with the given limit and offset."""
    params = {k: v for k, v in request.GET.items() if v is not None and v != ""}
    params["limit"] = str(limit)
    params["offset"] = str(new_offset)
    return request.build_absolute_uri(request.path + "?" + urlencode(params))


def paginate_data(request, results, limit, offset, data_type):
    """Paginate the results based on the limit and offset."""
    total = len(results)
    start = offset
    end = offset + limit
    paginated = results[start:end]

    if data_type == "media":
        serialized = MediaSerializer(paginated, many=True)
    elif data_type == "events":
        serialized = EventSerializer(paginated, many=True)

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

    return {"pagination": pagination, "results": serialized.data}


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
                Response({"detail": "Invalid 'limit' parameter"}, status=400),
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
                Response({"detail": "Invalid 'offset' parameter"}, status=400),
            )
    if limit <= 0 or offset < 0:
        return (
            None,
            None,
            Response(
                {
                    "detail": "Bad Request. 'limit' must be > 0 and 'offset' must be >= 0",
                },
                status=400,
            ),
        )

    limit = min(limit, MAX_RESULT_LIMIT)
    return limit, offset, None


def parse_sort_filter(sort_filter):
    """Return (sort, sort_order) tuple from a sort_filter string like 'title_desc'."""
    if sort_filter and sort_filter != "":
        parts = sort_filter.split("_", 1)
        if len(parts) == 2:
            return parts[0], parts[1]
        return parts[0], ""
    return "", ""


def itemid_key(media):
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


def fetch_media_list(user, media_type, status, sort_filter, search):
    """Wrapper around BasicMedia.objects.get_media_list that returns a plain list."""
    return list(
        BasicMedia.objects.get_media_list(
            user=user,
            media_type=media_type,
            status_filter=status,
            sort_filter=sort_filter,
            search=search,
        ),
    )


def apply_manual_sort_for_type(results, sort):
    """Apply manual sorts used when a single media type is requested."""
    match sort:
        case "added":
            results.sort(key=lambda media: media.created_at)
        case "itemid":
            results.sort(key=itemid_key)
        case "updated":
            results.sort(key=lambda media: media.progressed_at)
        case _:
            return Response({"detail": "Not Found. Invalid sorting"}, status=404)
    return results


def apply_aggregated_sort(results, sort):
    """Apply sorting for the aggregated (multi-type) results."""
    match sort:
        case "added":
            results.sort(key=lambda media: media.created_at)
        case "ended":
            results.sort(key=lambda media: media.end_date)
        case "id":
            results.sort(key=lambda media: int(media.id))
        case "itemid":
            results.sort(key=itemid_key)
        case "mediaid":
            results.sort(key=lambda media: media.item.id)
        case "progress":
            results.sort(key=lambda media: int(media.progress))
        case "source":
            results.sort(key=lambda media: media.item.source)
        case "started":
            results.sort(key=lambda media: media.start_date)
        case "title":
            results.sort(key=lambda media: media.item.title.lower())
        case "type":
            results.sort(key=lambda media: media.item.media_type)
        case "updated":
            results.sort(key=lambda media: media.progressed_at)
        case _:
            return Response({"detail": "Not Found. Invalid sorting"}, status=404)
    return results
