import contextlib
import logging
import re
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_not_required
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import prefetch_related_objects
from django.http import Http404, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.text import slugify
from django.utils.timezone import datetime
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from app import config, helpers, history_processor
from app import release_approval
from app import statistics as stats
from app.forms import EpisodeForm, ManualItemForm, get_form_class
from app.models import (
    TV,
    BasicMedia,
    Item,
    MediaTypes,
    Season,
    Sources,
    Status,
    ReleaseApprovalCandidate,
    ReleaseApprovalItem,
    UserMessage,
)
from app.providers import manual, services, tmdb
from app.templatetags import app_tags
from users.models import (
    DateFormatChoices,
    HomeSortChoices,
    MediaSortChoices,
    MediaStatusChoices,
    User,
)

logger = logging.getLogger(__name__)


@require_GET
def home(request):
    """Home page with media items in progress and planning."""
    sort_by = request.user.update_preference("home_sort", request.GET.get("sort"))
    media_type_to_load = request.GET.get("load_media_type")
    status_to_load = request.GET.get("load_status", Status.IN_PROGRESS.value)
    items_limit = 14

    # If this is an HTMX request to load more items for a specific media type
    if request.headers.get("HX-Request") and media_type_to_load:
        list_by_type = BasicMedia.objects.get_home_status(
            user=request.user,
            status=status_to_load,
            sort_by=sort_by,
            items_limit=items_limit,
            specific_media_type=media_type_to_load,
        )
        context = {
            "media_list": list_by_type.get(media_type_to_load, []),
            "home_status": status_to_load,
        }
        return render(request, "app/components/home_grid.html", context)

    home_sections = []
    for status in (Status.IN_PROGRESS.value, Status.PLANNING.value):
        media_types = BasicMedia.objects.get_home_status(
            user=request.user,
            status=status,
            sort_by=sort_by,
            items_limit=items_limit,
        )
        home_sections.append(
            {
                "key": status,
                "id": slugify(status),
                "media_types": media_types,
                "count": sum(
                    media_list["total"] for media_list in media_types.values()
                ),
            },
        )

    context = {
        "home_sections": home_sections,
        "current_sort": sort_by,
        "sort_choices": HomeSortChoices.choices,
        "items_limit": items_limit,
    }
    return render(request, "app/home.html", context)


@require_GET
def release_approval_requests(request):
    """Show Seerr requests that can be approved for download."""
    if not release_approval.is_configured():
        messages.error(request, "Release approval is not configured.")
        return render(request, "app/release_approval_requests.html", {"rows": []})

    if not ReleaseApprovalItem.objects.exists():
        sync_release_approval_items()
    items = (
        ReleaseApprovalItem.objects.filter(dismissed_at__isnull=True)
        .prefetch_related("candidates")
        .order_by("-created_at")
    )
    rows_by_key = {}
    for item in items:
        rows_by_key.setdefault((item.media_type, item.tmdb_id), item)
    rows = list(rows_by_key.values())
    for item in rows:
        approved_candidates = [
            candidate
            for candidate in item.candidates.all()
            if candidate.status == ReleaseApprovalCandidate.Status.APPROVED
        ]
        item.latest_approved_candidate = max(
            approved_candidates,
            key=lambda candidate: candidate.approved_at or candidate.created_at,
            default=None,
        )
        item.visible_candidate_count = sum(
            1
            for candidate in item.candidates.all()
            if not is_suspicious_release_candidate(candidate)
        )
        item.total_candidate_count = len(item.candidates.all())
        item.candidate_count_label = (
            f"{item.visible_candidate_count} / {item.total_candidate_count} candidates"
        )
        item.candidate_count_hint = "visible after default filtering"
        if item.media_type == ReleaseApprovalItem.MediaType.MOVIE and item.has_file:
            movie_file = (item.movie_data or {}).get("movieFile") or {}
            quality = ((movie_file.get("quality") or {}).get("quality") or {}).get("name")
            size = int(movie_file.get("size") or 0)
            item.candidate_count_label = "downloaded"
            if quality:
                item.candidate_count_label = quality
            item.candidate_count_hint = (
                movie_file.get("releaseGroup")
                or movie_file.get("sceneName")
                or (f"{size / 1024 / 1024 / 1024:.1f} GiB" if size else "available in Radarr")
            )
        elif item.media_type == ReleaseApprovalItem.MediaType.TV and item.has_file:
            stats = (item.movie_data or {}).get("statistics") or {}
            item.candidate_count_label = (
                f"{stats.get('episodeFileCount') or 0} files"
            )
            groups = ", ".join(stats.get("releaseGroups") or [])
            item.candidate_count_hint = groups or "available in Sonarr"
        item.poster_url = release_approval_cover_url(item, "poster")
        item.fanart_url = release_approval_cover_url(item, "fanart")
        item.type_label = "TV" if item.media_type == ReleaseApprovalItem.MediaType.TV else "Movie"
        item.service_label = "Sonarr" if item.media_type == ReleaseApprovalItem.MediaType.TV else "Radarr"
        if item.media_type == ReleaseApprovalItem.MediaType.MOVIE:
            item.approval_url = reverse(
                "release_approval_movie",
                kwargs={"tmdb_id": item.tmdb_id},
            )
        else:
            item.approval_url = reverse(
                "release_approval_tv",
                kwargs={"tmdb_id": item.tmdb_id},
            )

    return render(request, "app/release_approval_requests.html", {"rows": rows})


@require_POST
def release_approval_refresh_requests(request):
    """Refresh Seerr movie requests into Yamtrack."""
    if not release_approval.is_configured():
        messages.error(request, "Release approval is not configured.")
        return redirect("release_approval_requests")

    sync_release_approval_items()
    messages.success(request, "Seerr requests refreshed.")
    return redirect("release_approval_requests")


@require_POST
def release_approval_delete_item(request, item_id):
    """Hide a local release approval card from the approval queue."""
    item = get_object_or_404(ReleaseApprovalItem, id=item_id)
    title = item.title
    item.dismissed_at = timezone.now()
    item.save(update_fields=["dismissed_at", "synced_at"])
    messages.success(request, f"{title} removed from release approval.")
    return redirect("release_approval_requests")


def sync_release_approval_items():
    """Sync Seerr movie and TV requests into Yamtrack release approval items."""
    seerr_requests = release_approval.seerr_requests()
    radarr_movies = release_approval.radarr_movies_by_tmdb()
    sonarr_series = release_approval.sonarr_series_by_tmdb()
    synced_request_ids = set()

    for seerr_request in seerr_requests:
        synced_request_ids.add(seerr_request["id"])
        media = seerr_request.get("media") or {}
        tmdb_id = media.get("tmdbId")
        if not tmdb_id:
            continue

        media_type = media.get("mediaType")
        if media_type == ReleaseApprovalItem.MediaType.MOVIE:
            item_media_type = ReleaseApprovalItem.MediaType.MOVIE
            media_data = radarr_movies.get(int(tmdb_id), {})
            arr_id = media_data.get("id")
            title = (
                media_data.get("title")
                or media.get("title")
                or media.get("originalTitle")
                or f"tmdb:{tmdb_id}"
            )
            year = media_data.get("year")
            has_file = bool(media_data.get("hasFile"))
        elif media_type == ReleaseApprovalItem.MediaType.TV:
            item_media_type = ReleaseApprovalItem.MediaType.TV
            media_data = sonarr_series.get(int(tmdb_id), {})
            arr_id = media_data.get("id")
            title = (
                media_data.get("title")
                or media.get("name")
                or media.get("title")
                or f"tmdb:{tmdb_id}"
            )
            year = media_data.get("year")
            has_file = media.get("status") == 5
        else:
            continue

        ReleaseApprovalItem.objects.update_or_create(
            seerr_request_id=seerr_request["id"],
            defaults={
                "media_type": item_media_type,
                "tmdb_id": int(tmdb_id),
                "title": title,
                "year": year,
                "seerr_status": release_approval.request_status_label(seerr_request),
                "radarr_movie_id": arr_id,
                "has_file": has_file,
                "request_data": seerr_request,
                "movie_data": media_data,
            },
        )

    ReleaseApprovalItem.objects.exclude(
        seerr_request_id__in=synced_request_ids,
    ).delete()


def get_release_approval_item_by_tmdb(
    tmdb_id,
    media_type=ReleaseApprovalItem.MediaType.MOVIE,
):
    """Return the most recent synced approval item for a TMDB media ID."""
    item = (
        ReleaseApprovalItem.objects.filter(
            media_type=media_type,
            tmdb_id=tmdb_id,
            dismissed_at__isnull=True,
        )
        .order_by("-created_at")
        .first()
    )
    if item is None:
        raise Http404
    copy_candidates_from_previous_item(item)
    return item


def release_approval_candidate_context(request, item):
    """Build shared filter context for release approval pages."""
    show_all = request.GET.get("show") == "all"
    all_candidates = list(item.candidates.all())
    suspicious_filter = request.GET.get("suspicious") or ("show" if show_all else "hide")
    base_candidates = filter_suspicious_release_candidates(
        all_candidates,
        suspicious_filter,
    )
    candidates = filter_release_approval_candidates(base_candidates, request.GET)
    indexers = sorted({candidate.indexer for candidate in all_candidates if candidate.indexer})
    qualities = sorted({candidate.quality for candidate in all_candidates if candidate.quality})
    verdicts = sorted({candidate.verdict for candidate in all_candidates if candidate.verdict})
    years = sorted(
        {
            year
            for candidate in all_candidates
            for year in release_candidate_years(candidate)
        },
        reverse=True,
    )
    languages = sorted(
        {
            language
            for candidate in all_candidates
            for language in release_candidate_languages(candidate)
        },
    )
    item.poster_url = release_approval_cover_url(item, "poster")
    item.fanart_url = release_approval_cover_url(item, "fanart")
    downloaded_summary = release_approval_downloaded_summary(item)

    filter_params = request.GET.copy()
    filter_params.pop("show", None)
    show_all_query = filter_params.copy()
    show_all_query["show"] = "all"

    reset_route = (
        "release_approval_tv"
        if item.media_type == ReleaseApprovalItem.MediaType.TV
        else "release_approval_movie"
    )
    refresh_route = (
        "release_approval_refresh_tv"
        if item.media_type == ReleaseApprovalItem.MediaType.TV
        else "release_approval_refresh_movie"
    )
    grab_route = (
        "release_approval_grab_tv"
        if item.media_type == ReleaseApprovalItem.MediaType.TV
        else "release_approval_grab_movie"
    )

    service_label = (
        "Sonarr"
        if item.media_type == ReleaseApprovalItem.MediaType.TV
        else "Radarr"
    )
    media_label = "TV" if item.media_type == ReleaseApprovalItem.MediaType.TV else "Movie"

    return {
        "item": item,
        "candidates": candidates,
        "hidden_candidates_count": len(all_candidates) - len(base_candidates),
        "filtered_candidates_count": len(base_candidates) - len(candidates),
        "show_all": show_all,
        "indexers": indexers,
        "qualities": qualities,
        "verdicts": verdicts,
        "filters": {
            "q": request.GET.get("q", ""),
            "indexer": request.GET.get("indexer", ""),
            "quality": request.GET.get("quality", ""),
            "verdict": request.GET.get("verdict", ""),
            "year": request.GET.get("year", ""),
            "min_score": request.GET.get("min_score", ""),
            "min_seeders": request.GET.get("min_seeders", ""),
            "min_size": request.GET.get("min_size", ""),
            "max_size": request.GET.get("max_size", ""),
            "language": request.GET.get("language", ""),
            "external_id": request.GET.get("external_id", ""),
            "rejected": request.GET.get("rejected", ""),
            "suspicious": suspicious_filter,
        },
        "years": years,
        "languages": languages,
        "filter_query": filter_params.urlencode(),
        "show_all_query": show_all_query.urlencode(),
        "refresh_url": reverse(refresh_route, kwargs={"tmdb_id": item.tmdb_id}),
        "delete_url": reverse(
            "release_approval_delete_item",
            kwargs={"item_id": item.id},
        ),
        "reset_url": reverse(reset_route, kwargs={"tmdb_id": item.tmdb_id}),
        "grab_url": reverse(grab_route, kwargs={"tmdb_id": item.tmdb_id}),
        "media_label": media_label,
        "service_label": service_label,
        "requested_seasons": release_approval_requested_seasons(item),
        "search_seasons": release_approval_search_seasons(item, item.movie_data),
        "downloaded_summary": downloaded_summary,
    }


def copy_candidates_from_previous_item(item):
    """Seed a repeated request with candidates from the previous same-TMDB item."""
    if item.candidates.exists():
        return

    previous_item = (
        ReleaseApprovalItem.objects.filter(
            media_type=item.media_type,
            tmdb_id=item.tmdb_id,
        )
        .exclude(id=item.id)
        .filter(candidates__isnull=False)
        .order_by("-created_at")
        .first()
    )
    if previous_item is None:
        return

    candidates = []
    for candidate in previous_item.candidates.all():
        candidates.append(
            ReleaseApprovalCandidate(
                item=item,
                identity=candidate.identity,
                title=candidate.title,
                indexer=candidate.indexer,
                info_url=candidate.info_url,
                quality=candidate.quality,
                size=candidate.size,
                seeders=candidate.seeders,
                score=candidate.score,
                verdict=candidate.verdict,
                score_reasons=candidate.score_reasons,
                score_warnings=candidate.score_warnings,
                release_data=candidate.release_data,
            ),
        )
    ReleaseApprovalCandidate.objects.bulk_create(candidates, ignore_conflicts=True)


@require_GET
def release_approval_movie(request, tmdb_id):
    """Show scored Radarr release candidates for a movie request."""
    if not release_approval.is_configured():
        messages.error(request, "Release approval is not configured.")
        return redirect("release_approval_requests")

    sync_release_approval_items()
    item = get_release_approval_item_by_tmdb(tmdb_id)
    return render(
        request,
        "app/release_approval_movie.html",
        release_approval_candidate_context(request, item),
    )


@require_GET
def release_approval_tv(request, tmdb_id):
    """Show scored Sonarr release candidates for a TV request."""
    if not release_approval.is_configured():
        messages.error(request, "Release approval is not configured.")
        return redirect("release_approval_requests")

    sync_release_approval_items()
    item = get_release_approval_item_by_tmdb(
        tmdb_id,
        ReleaseApprovalItem.MediaType.TV,
    )
    return render(
        request,
        "app/release_approval_movie.html",
        release_approval_candidate_context(request, item),
    )


def filter_release_approval_candidates(candidates, params):
    """Apply UI filters to stored release candidates."""
    query = (params.get("q") or "").strip().lower()
    indexer = params.get("indexer") or ""
    quality = params.get("quality") or ""
    verdict = params.get("verdict") or ""
    year = parse_int_filter(params.get("year"))
    language = params.get("language") or ""
    external_id = params.get("external_id") or ""
    rejected = params.get("rejected") or ""
    suspicious = params.get("suspicious") or "hide"
    min_score = parse_int_filter(params.get("min_score"))
    min_seeders = parse_int_filter(params.get("min_seeders"))
    min_size = parse_size_gib_filter(params.get("min_size"))
    max_size = parse_size_gib_filter(params.get("max_size"))

    filtered = []
    for candidate in candidates:
        if query and query not in candidate.title.lower() and query not in candidate.indexer.lower():
            continue
        if indexer and candidate.indexer != indexer:
            continue
        if quality and candidate.quality != quality:
            continue
        if verdict and candidate.verdict != verdict:
            continue
        if year is not None and year not in release_candidate_years(candidate):
            continue
        if language and language not in release_candidate_languages(candidate):
            continue
        if min_score is not None and candidate.score < min_score:
            continue
        if min_seeders is not None and candidate.seeders < min_seeders:
            continue
        if min_size is not None and candidate.size < min_size:
            continue
        if max_size is not None and candidate.size > max_size:
            continue
        has_external_id = bool(
            (candidate.release_data or {}).get("tmdbId")
            or (candidate.release_data or {}).get("tvdbId")
            or (candidate.release_data or {}).get("imdbId")
        )
        if external_id == "yes" and not has_external_id:
            continue
        if external_id == "no" and has_external_id:
            continue
        if suspicious == "hide" and is_suspicious_release_candidate(candidate):
            continue
        if suspicious == "only" and not is_suspicious_release_candidate(candidate):
            continue
        if rejected == "yes" and not candidate.release_data.get("rejected"):
            continue
        if rejected == "no" and candidate.release_data.get("rejected"):
            continue
        filtered.append(candidate)
    return filtered


def filter_suspicious_release_candidates(candidates, suspicious_filter):
    """Apply the top-level suspicious visibility filter."""
    if suspicious_filter == "show":
        return candidates
    if suspicious_filter == "only":
        return [
            candidate
            for candidate in candidates
            if is_suspicious_release_candidate(candidate)
        ]
    return [
        candidate
        for candidate in candidates
        if not is_suspicious_release_candidate(candidate)
    ]


def is_suspicious_release_candidate(candidate):
    """Return whether a candidate should be hidden by default."""
    return candidate.score < 0 or bool(candidate.release_data.get("rejected"))


def release_approval_cover_url(item, cover_type):
    """Return a Radarr cover URL from stored movie metadata."""
    for image in (item.movie_data or {}).get("images", []):
        if image.get("coverType") == cover_type:
            return image.get("remoteUrl") or image.get("url") or ""
    return ""


def parse_int_filter(value):
    """Parse an integer filter, returning None for empty/invalid values."""
    value = (value or "").strip()
    if not value:
        return None
    with contextlib.suppress(ValueError):
        return int(value)
    return None


def parse_size_gib_filter(value):
    """Parse a GiB size filter into bytes."""
    parsed = parse_int_filter(value)
    if parsed is None:
        return None
    return parsed * 1024 * 1024 * 1024


def release_candidate_years(candidate):
    """Return years found in the displayed and original release titles."""
    text = " ".join(
        [
            candidate.title or "",
            (candidate.release_data or {}).get("title") or "",
        ],
    )
    return {int(value) for value in re.findall(r"\b(19\d{2}|20\d{2})\b", text)}


def release_candidate_languages(candidate):
    """Return language names from an Arr release payload."""
    return {
        language.get("name")
        for language in (candidate.release_data or {}).get("languages", [])
        if language.get("name")
    }


def release_approval_requested_seasons(item):
    """Return requested season numbers from a Seerr TV request."""
    seasons = (item.request_data or {}).get("seasons") or []
    return [
        season.get("seasonNumber")
        for season in seasons
        if season.get("seasonNumber") is not None
    ]


def release_approval_search_seasons(item, media):
    """Return requested TV seasons that still need files and should be searched."""
    if item.media_type != ReleaseApprovalItem.MediaType.TV:
        return []

    requested = {
        season
        for season in release_approval_requested_seasons(item)
        if season and season > 0
    }
    if not requested:
        return []

    seasons_by_number = {
        season.get("seasonNumber"): season
        for season in (media or {}).get("seasons", [])
    }
    missing = []
    for season_number in sorted(requested):
        stats = (seasons_by_number.get(season_number) or {}).get("statistics") or {}
        episode_count = int(stats.get("episodeCount") or 0)
        file_count = int(stats.get("episodeFileCount") or 0)
        if episode_count <= 0 or file_count < episode_count:
            missing.append(season_number)
    return missing


def release_approval_downloaded_summary(item):
    """Build a compact downloaded-files summary for an already handled request."""
    if not item.radarr_movie_id:
        return None

    if item.media_type == ReleaseApprovalItem.MediaType.MOVIE:
        movie_file = (item.movie_data or {}).get("movieFile") or {}
        history = release_approval.radarr_movie_history(item.radarr_movie_id, page_size=50)
        latest_imports = [
            record
            for record in history
            if record.get("eventType") in {"downloadFolderImported", "movieFileImported"}
        ][:8]
        latest_grabs = [
            record
            for record in history
            if record.get("eventType") == "grabbed"
        ][:8]
        if not movie_file and not latest_imports and not latest_grabs:
            return None

        quality = ((movie_file.get("quality") or {}).get("quality") or {}).get("name")
        release_group = movie_file.get("releaseGroup")
        size = int(movie_file.get("size") or 0)
        latest_files = [movie_file] if movie_file else []
        return {
            "file_count": 1 if movie_file else 0,
            "seasons": [],
            "release_groups": [release_group] if release_group else [],
            "qualities": [quality] if quality else [],
            "size": size,
            "latest_files": latest_files,
            "latest_imports": latest_imports or latest_grabs,
        }

    if item.media_type != ReleaseApprovalItem.MediaType.TV:
        return None

    files = release_approval.sonarr_episode_files(item.radarr_movie_id)
    requested = {
        season
        for season in release_approval_requested_seasons(item)
        if season and season > 0
    }
    if requested:
        files = [file for file in files if file.get("seasonNumber") in requested]

    release_groups = sorted(
        {
            file.get("releaseGroup")
            for file in files
            if file.get("releaseGroup")
        },
    )
    qualities = sorted(
        {
            ((file.get("quality") or {}).get("quality") or {}).get("name")
            for file in files
            if ((file.get("quality") or {}).get("quality") or {}).get("name")
        },
    )
    seasons = sorted(
        {
            file.get("seasonNumber")
            for file in files
            if file.get("seasonNumber") is not None
        },
    )
    latest_files = sorted(
        files,
        key=lambda file: file.get("dateAdded") or "",
        reverse=True,
    )[:8]
    history = release_approval.sonarr_series_history(item.radarr_movie_id, page_size=50)
    latest_imports = [
        record
        for record in history
        if record.get("eventType") in {"downloadFolderImported", "episodeFileImported"}
    ][:8]

    if not files and not latest_imports:
        return None

    return {
        "file_count": len(files),
        "seasons": seasons,
        "release_groups": release_groups,
        "qualities": qualities,
        "latest_files": latest_files,
        "latest_imports": latest_imports,
    }


@require_POST
def release_approval_refresh_movie(request, tmdb_id):
    """Refresh Radarr release candidates for a movie request."""
    if not release_approval.is_configured():
        messages.error(request, "Release approval is not configured.")
        return redirect("release_approval_requests")

    sync_release_approval_items()
    item = get_release_approval_item_by_tmdb(tmdb_id)
    if not item.radarr_movie_id:
        messages.error(request, f"tmdb:{tmdb_id} is not in Radarr yet.")
        return redirect("release_approval_requests")

    movie = release_approval.radarr_movie(item.radarr_movie_id)
    item.movie_data = movie
    item.has_file = bool(movie.get("hasFile"))
    item.save(update_fields=["movie_data", "has_file", "synced_at"])
    sync_release_approval_candidates(item, movie)
    messages.success(request, "Release candidates refreshed.")
    return redirect("release_approval_movie", tmdb_id=tmdb_id)


@require_POST
def release_approval_refresh_tv(request, tmdb_id):
    """Refresh Sonarr release candidates for a TV request."""
    if not release_approval.is_configured():
        messages.error(request, "Release approval is not configured.")
        return redirect("release_approval_requests")

    sync_release_approval_items()
    item = get_release_approval_item_by_tmdb(
        tmdb_id,
        ReleaseApprovalItem.MediaType.TV,
    )
    if not item.radarr_movie_id:
        messages.error(request, f"tmdb:{tmdb_id} is not in Sonarr yet.")
        return redirect("release_approval_requests")

    series = release_approval.sonarr_series(item.radarr_movie_id)
    item.movie_data = series
    item.has_file = bool(
        (series.get("statistics") or {}).get("episodeFileCount"),
    )
    item.save(update_fields=["movie_data", "has_file", "synced_at"])
    sync_release_approval_candidates(item, series)
    messages.success(request, "Release candidates refreshed.")
    return redirect("release_approval_tv", tmdb_id=tmdb_id)


def sync_release_approval_candidates(item, movie):
    """Sync and score Arr release candidates into Yamtrack."""
    releases = release_approval_releases(item, movie)
    scored = release_approval.score_releases(movie, releases)
    seen = set()

    for candidate in scored:
        release = candidate["release"]
        score = candidate["score"]
        identity = candidate["identity"]
        seen.add(identity)
        quality = ((release.get("quality") or {}).get("quality") or {}).get("name") or ""
        ReleaseApprovalCandidate.objects.update_or_create(
            item=item,
            identity=identity,
            defaults={
                "title": release_approval.release_display_title(movie, release),
                "indexer": release.get("indexer") or "",
                "info_url": release.get("infoUrl") or "",
                "quality": quality,
                "size": int(release.get("size") or 0),
                "seeders": int(release.get("seeders") or 0),
                "score": score.score,
                "verdict": score.verdict,
                "score_reasons": score.reasons,
                "score_warnings": score.warnings,
                "release_data": release,
            },
        )

    item.candidates.filter(
        status=ReleaseApprovalCandidate.Status.PENDING,
    ).exclude(identity__in=seen).delete()


def release_approval_releases(item, media):
    """Fetch release candidates for a release approval item."""
    if item.media_type == ReleaseApprovalItem.MediaType.TV:
        releases = []
        season_numbers = release_approval_search_seasons(item, media)
        if season_numbers:
            for season_number in season_numbers:
                try:
                    releases.extend(
                        release_approval.sonarr_releases(
                            media["id"],
                            season_number,
                            timeout=20,
                        ),
                    )
                except release_approval.ReleaseApprovalError:
                    logger.warning(
                        "Sonarr TV search failed for tmdb:%s season:%s; using Prowlarr fallback.",
                        item.tmdb_id,
                        season_number,
                    )
            if releases:
                return releases
            return release_approval_prowlarr_fallback_releases(item, media)
        return []
    return release_approval.radarr_releases(media["id"])


def release_approval_prowlarr_fallback_releases(item, media):
    """Fetch generic Prowlarr TV candidates when Sonarr season search is empty."""
    queries = release_approval_fallback_queries(item, media)
    seen = set()
    releases = []
    for query in queries:
        try:
            results = release_approval.prowlarr_search(query)
        except release_approval.ReleaseApprovalError:
            logger.exception("Prowlarr fallback failed for %s", query)
            continue
        for result in results:
            identity = release_approval.release_identity(result)
            if identity in seen:
                continue
            seen.add(identity)
            result["source"] = "prowlarr_generic"
            result["rejected"] = False
            result["rejections"] = []
            result["languages"] = result.get("languages") or []
            releases.append(result)
    return releases


def release_approval_fallback_queries(item, media):
    """Return title queries for Prowlarr fallback search."""
    values = [
        media.get("title"),
        media.get("originalTitle"),
        item.title,
        *[
            alt.get("title")
            for alt in media.get("alternateTitles", [])
            if alt.get("title")
        ],
    ]
    queries = []
    seen = set()
    for value in values:
        value = (value or "").strip()
        if not value or value.lower() in seen:
            continue
        seen.add(value.lower())
        queries.append(value)
    return queries


@require_POST
def release_approval_grab_movie(request, tmdb_id):
    """Approve a selected Radarr release candidate."""
    if not release_approval.is_configured():
        messages.error(request, "Release approval is not configured.")
        return redirect("release_approval_requests")

    candidate_id = request.POST.get("candidate_id")
    if not candidate_id:
        return HttpResponseBadRequest("Missing candidate id")

    candidate = get_object_or_404(
        ReleaseApprovalCandidate,
        id=candidate_id,
        item__media_type=ReleaseApprovalItem.MediaType.MOVIE,
        item__tmdb_id=tmdb_id,
    )

    redirect_url = request.POST.get("next") or reverse(
        "release_approval_movie",
        kwargs={"tmdb_id": tmdb_id},
    )
    if not redirect_url.startswith(f"/release-approval/movie/{tmdb_id}"):
        redirect_url = reverse("release_approval_movie", kwargs={"tmdb_id": tmdb_id})

    candidate.status = ReleaseApprovalCandidate.Status.GRABBING
    candidate.grab_error = ""
    candidate.save(update_fields=["status", "grab_error", "synced_at"])

    from app.tasks import grab_release_approval_candidate

    grab_release_approval_candidate.delay(
        candidate.id,
        request.user.id if request.user.is_authenticated else None,
    )
    messages.success(request, "Release approval queued.")
    return redirect(redirect_url)


@require_POST
def release_approval_grab_tv(request, tmdb_id):
    """Approve a selected Sonarr release candidate."""
    if not release_approval.is_configured():
        messages.error(request, "Release approval is not configured.")
        return redirect("release_approval_requests")

    candidate_id = request.POST.get("candidate_id")
    if not candidate_id:
        return HttpResponseBadRequest("Missing candidate id")

    candidate = get_object_or_404(
        ReleaseApprovalCandidate,
        id=candidate_id,
        item__media_type=ReleaseApprovalItem.MediaType.TV,
        item__tmdb_id=tmdb_id,
    )

    redirect_url = request.POST.get("next") or reverse(
        "release_approval_tv",
        kwargs={"tmdb_id": tmdb_id},
    )
    if not redirect_url.startswith(f"/release-approval/tv/{tmdb_id}"):
        redirect_url = reverse("release_approval_tv", kwargs={"tmdb_id": tmdb_id})

    candidate.status = ReleaseApprovalCandidate.Status.GRABBING
    candidate.grab_error = ""
    candidate.save(update_fields=["status", "grab_error", "synced_at"])

    from app.tasks import grab_release_approval_candidate

    grab_release_approval_candidate.delay(
        candidate.id,
        request.user.id if request.user.is_authenticated else None,
    )
    messages.success(request, "Release approval queued.")
    return redirect(redirect_url)


@require_POST
def progress_edit(request, media_type, instance_id):
    """Increase or decrease the progress of a media item from home page."""
    operation = request.POST["operation"]

    media = helpers.get_owned_media_or_404(
        request, media_type, instance_id, prefetch=True
    )

    if operation == "increase":
        media.increase_progress()
    elif operation == "decrease":
        media.decrease_progress()

    if media_type == MediaTypes.SEASON.value:
        # clear prefetch cache to get the updated episodes
        media.refresh_from_db()
        prefetch_related_objects([media], "episodes")

    context = {
        "media": media,
    }
    return render(
        request,
        "app/components/progress_changer.html",
        context,
    )


@login_not_required
@require_GET
def media_list(request, username, media_type):
    """Return the media list page."""
    target_user = get_object_or_404(User, username=username)

    # if user is looking at own page then update preferences
    if request.user == target_user:
        layout = target_user.update_preference(
            f"{media_type}_layout",
            request.GET.get("layout"),
        )
        sort_filter = target_user.update_preference(
            f"{media_type}_sort",
            request.GET.get("sort"),
        )
        status_filter = target_user.update_preference(
            f"{media_type}_status",
            request.GET.get("status"),
        )
    else:
        # privacy check then media type check
        if target_user.profile_private:
            msg = "User not found"
            raise Http404(msg)

        enabled_media_types = target_user.get_enabled_media_types()
        if not enabled_media_types:
            msg = "User doesn't have any media types enabled"
            raise Http404(msg)

        if media_type not in enabled_media_types:
            return redirect(
                "medialist",
                username=target_user.username,
                media_type=enabled_media_types[0],
            )

        layout = target_user.get_valid_preference(
            f"{media_type}_layout",
            request.GET.get("layout"),
        )
        sort_filter = target_user.get_valid_preference(
            f"{media_type}_sort",
            request.GET.get("sort"),
        )
        status_filter = target_user.get_valid_preference(
            f"{media_type}_status",
            request.GET.get("status"),
        )

    search_query = request.GET.get("search", "")
    page = request.GET.get("page", 1)

    # Prepare status filter for database query
    if not status_filter:
        status_filter = MediaStatusChoices.ALL

    # Get media list with filters applied
    media_queryset = BasicMedia.objects.get_media_list(
        user=target_user,
        media_type=media_type,
        status_filter=status_filter,
        sort_filter=sort_filter,
        search=search_query,
    )

    # Paginate results
    items_per_page = 32
    paginator = Paginator(media_queryset, items_per_page)
    media_page = paginator.get_page(page)

    BasicMedia.objects.annotate_max_progress(
        media_page.object_list,
        media_type,
    )

    context = {
        "media_type": media_type,
        "media_type_plural": app_tags.media_type_readable_plural(media_type).lower(),
        "media_list": media_page,
        "current_layout": layout,
        "layout_class": ".media-grid" if layout == "grid" else "tbody",
        "current_sort": sort_filter,
        "current_status": status_filter,
        "sort_choices": MediaSortChoices.choices,
        "status_choices": MediaStatusChoices.choices,
        "target_user": target_user,
    }

    # Handle HTMX requests for partial updates
    if request.headers.get("HX-Request"):
        # Filtering from empty list
        if request.headers.get("HX-Target") == "empty_list":
            # If still empty, keep user in the same page
            if not media_page.object_list:
                return HttpResponse(status=204)
            response = HttpResponse()
            response["HX-Redirect"] = reverse(
                "medialist", args=[target_user.username, media_type]
            )
            return response
        if layout == "grid":
            template_name = "app/components/media_grid_items.html"
        else:
            template_name = "app/components/media_table_items.html"
    else:
        template_name = "app/media_list.html"

    return render(request, template_name, context)


@require_GET
def media_search(request):
    """Return the media search page."""
    media_type = request.user.update_preference(
        "last_search_type",
        request.GET["media_type"],
    )
    query = request.GET["q"]
    page = int(request.GET.get("page", 1))
    layout = request.GET.get("layout", "grid")

    # only receives source when searching with secondary source
    source = request.GET.get(
        "source",
        config.get_default_source_name(media_type).value,
    )

    data = services.search(media_type, query, page, source)

    # Enrich search results with user tracking data
    if data.get("results"):
        data["results"] = helpers.enrich_items_with_user_data(
            request, data["results"], "search"
        )

    context = {
        "data": data,
        "source": source,
        "media_type": media_type,
        "layout": layout,
    }

    return render(request, "app/search.html", context)


@require_GET
def media_details(request, source, media_type, media_id, title):  # noqa: ARG001 title for URL
    """Return the details page for a media item."""
    media_metadata = services.get_media_metadata(media_type, media_id, source)
    user_medias = BasicMedia.objects.filter_media_prefetch(
        request.user,
        media_id,
        media_type,
        source,
    )
    current_instance = user_medias[0] if user_medias else None

    if current_instance is not None:
        helpers.refresh_item_image_if_missing(
            current_instance.item, media_metadata.get("image")
        )

    # Enrich related items with user tracking data
    if media_metadata.get("related"):
        for section_name, related_items in media_metadata["related"].items():
            if related_items:
                media_metadata["related"][section_name] = (
                    helpers.enrich_items_with_user_data(
                        request, related_items, section_name
                    )
                )

    if media_type in ["tv", "movie"]:
        watch_providers = tmdb.filter_providers(
            media_metadata.get("providers"), request.user.watch_provider_region
        )
    else:
        watch_providers = None

    context = {
        "media": media_metadata,
        "media_type": media_type,
        "user_medias": user_medias,
        "current_instance": current_instance,
        "watch_providers": watch_providers,
        "watch_provider_region": request.user.watch_provider_region,
    }
    return render(request, "app/media_details.html", context)


@require_GET
def season_details(request, source, media_id, title, season_number):  # noqa: ARG001 For URL
    """Return the details page for a season."""
    tv_with_seasons_metadata = services.get_media_metadata(
        "tv_with_seasons",
        media_id,
        source,
        [season_number],
    )
    season_metadata = tv_with_seasons_metadata[f"season/{season_number}"]

    user_medias = BasicMedia.objects.filter_media_prefetch(
        request.user,
        media_id,
        MediaTypes.SEASON.value,
        source,
        season_number=season_number,
    )

    current_instance = user_medias[0] if user_medias else None
    episodes_in_db = current_instance.episodes.all() if current_instance else []

    if current_instance is not None:
        helpers.refresh_item_image_if_missing(
            current_instance.item, season_metadata.get("image")
        )

    if source == Sources.MANUAL.value:
        season_metadata["episodes"] = manual.process_episodes(
            season_metadata,
            episodes_in_db,
        )
    else:
        season_metadata["episodes"] = tmdb.process_episodes(
            season_metadata,
            episodes_in_db,
        )

    # Enrich related items with user tracking data
    if season_metadata.get("related"):
        for section_name, related_items in season_metadata["related"].items():
            if related_items:
                season_metadata["related"][section_name] = (
                    helpers.enrich_items_with_user_data(
                        request,
                        related_items,
                        section_name,
                    )
                )

    context = {
        "media": season_metadata,
        "tv": tv_with_seasons_metadata,
        "media_type": MediaTypes.SEASON.value,
        "user_medias": user_medias,
        "current_instance": current_instance,
        "watch_providers": tmdb.filter_providers(
            season_metadata.get("providers"), request.user.watch_provider_region
        ),
        "watch_provider_region": request.user.watch_provider_region,
    }
    return render(request, "app/media_details.html", context)


@require_POST
def update_media_score(request, media_type, instance_id):
    """Update the user's score for a media item."""
    media = helpers.get_owned_media_or_404(request, media_type, instance_id)

    score = float(request.POST.get("score"))
    media.score = score
    media.save()
    logger.info(
        "%s score updated to %s",
        media,
        score,
    )

    return JsonResponse(
        {
            "success": True,
            "score": score,
        },
    )


@require_POST
def sync_metadata(request, source, media_type, media_id, season_number=None):
    """Refresh the metadata for a media item."""
    if source == Sources.MANUAL.value:
        msg = "Manual items cannot be synced."
        messages.error(request, msg)
        return HttpResponse(
            msg,
            status=400,
            headers={"HX-Redirect": request.POST.get("next", "/")},
        )

    cache_key = f"{source}_{media_type}_{media_id}"
    if media_type == MediaTypes.SEASON.value:
        cache_key += f"_{season_number}"

    ttl = cache.ttl(cache_key)
    logger.debug("%s - Cache TTL for: %s", cache_key, ttl)

    if ttl is not None and ttl > (settings.CACHE_TIMEOUT - 3):
        msg = "The data was recently synced, please wait a few seconds."
        messages.error(request, msg)
        logger.error(msg)
    else:
        deleted = cache.delete(cache_key)
        logger.debug("%s - Old cache deleted: %s", cache_key, deleted)

        metadata = services.get_media_metadata(
            media_type,
            media_id,
            source,
            [season_number],
        )
        item, _ = Item.objects.update_or_create(
            media_id=media_id,
            source=source,
            media_type=media_type,
            season_number=season_number,
            defaults={
                "title": metadata["title"],
                "image": metadata["image"],
            },
        )
        title = metadata["title"]
        if season_number:
            title += f" - Season {season_number}"

        if media_type == MediaTypes.SEASON.value:
            metadata["episodes"] = tmdb.process_episodes(
                metadata,
                [],
            )

            # Create a dictionary of existing episodes keyed by episode number
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
            episode_count = 0

            for episode_data in metadata["episodes"]:
                episode_number = episode_data["episode_number"]
                if episode_number in existing_episodes:
                    episode_item = existing_episodes[episode_number]
                    episode_item.title = metadata["title"]
                    episode_item.image = episode_data["image"]
                    episodes_to_update.append(episode_item)
                    episode_count += 1

            logger.info(
                "Found %s existing episodes to update for %s",
                episode_count,
                title,
            )

            if episodes_to_update:
                updated_count = Item.objects.bulk_update(
                    episodes_to_update,
                    ["title", "image"],
                    batch_size=100,
                )
                logger.info(
                    "Successfully updated %s episodes for %s",
                    updated_count,
                    title,
                )

        item.fetch_releases(delay=False)

        msg = f"{title} was synced to {Sources(source).label} successfully."
        messages.success(request, msg)

    if request.headers.get("HX-Request"):
        return HttpResponse(
            status=204,
            headers={
                "HX-Redirect": request.POST["next"],
            },
        )
    return helpers.redirect_back(request)


@require_GET
def track_modal(
    request,
    source,
    media_type,
    media_id,
    season_number=None,
):
    """Return the tracking form for a media item."""
    instance_id = request.GET.get("instance_id")
    if instance_id:
        media = BasicMedia.objects.get_media(
            request.user,
            media_type,
            instance_id,
        )
    elif request.GET.get("is_create"):
        media = None
    else:
        # no specific instance, try to find the first one
        user_medias = BasicMedia.objects.filter_media(
            request.user,
            media_id,
            media_type,
            source,
            season_number=season_number,
        )
        media = user_medias.first()
        if media:
            instance_id = media.id

    initial_data = {
        "media_id": media_id,
        "source": source,
        "media_type": media_type,
        "season_number": season_number,
        "instance_id": instance_id,
    }

    if media:
        title = media.item
        if media_type == MediaTypes.GAME.value:
            initial_data["progress"] = helpers.minutes_to_hhmm(media.progress)
    else:
        title = services.get_media_metadata(
            media_type,
            media_id,
            source,
            [season_number],
        )["title"]
        if media_type == MediaTypes.SEASON.value:
            title += f" S{season_number}"

    form = get_form_class(media_type)(instance=media, initial=initial_data)

    return render(
        request,
        "app/components/fill_track.html",
        {
            "title": title,
            "form": form,
            "media": media,
            "return_url": request.GET["return_url"],
        },
    )


@require_POST
def media_save(request):
    """Save or update media data to the database."""
    media_id = request.POST["media_id"]
    source = request.POST["source"]
    media_type = request.POST["media_type"]
    season_number = request.POST.get("season_number")
    instance_id = request.POST.get("instance_id")

    if instance_id:
        instance = helpers.get_owned_media_or_404(request, media_type, instance_id)
    else:
        metadata = services.get_media_metadata(
            media_type,
            media_id,
            source,
            [season_number],
        )
        item, _ = Item.objects.get_or_create(
            media_id=media_id,
            source=source,
            media_type=media_type,
            season_number=season_number,
            defaults={
                "title": metadata["title"],
                "image": metadata["image"],
            },
        )
        model = apps.get_model(app_label="app", model_name=media_type)
        instance = model(item=item, user=request.user)

    # Validate the form and save the instance if it's valid
    form_class = get_form_class(media_type)
    form = form_class(request.POST, instance=instance)
    if form.is_valid():
        form.save()
        logger.info("%s saved successfully.", form.instance)
    else:
        logger.error(form.errors.as_json())
        for field, errors in form.errors.items():
            for error in errors:
                messages.error(
                    request,
                    f"{field.replace('_', ' ').title()}: {error}",
                )

    return helpers.redirect_back(request)


@require_POST
def media_delete(request):
    """Delete media data from the database."""
    instance_id = request.POST["instance_id"]
    media_type = request.POST["media_type"]
    media = helpers.get_owned_media_or_404(request, media_type, instance_id)
    media.delete()
    logger.info("%s deleted successfully.", media)

    return helpers.redirect_back(request)


@require_POST
def mark_user_messages_shown(request):
    """Mark all unseen persistent messages for the user as shown."""
    message_ids = [
        int(message_id)
        for message_id in request.POST.getlist("message_ids")
        if message_id.isdigit()
    ]
    if not message_ids:
        return HttpResponse(status=204)

    UserMessage.objects.filter(
        id__in=message_ids,
        user=request.user,
        shown_at__isnull=True,
    ).update(shown_at=timezone.now())
    return HttpResponse(status=204)


@require_POST
def episode_save(request):
    """Handle the creation, deletion, and updating of episodes for a season."""
    media_id = request.POST["media_id"]
    season_number = int(request.POST["season_number"])
    episode_number = int(request.POST["episode_number"])
    source = request.POST["source"]

    form = EpisodeForm(request.POST)
    if not form.is_valid():
        logger.error("Form validation failed: %s", form.errors)
        return HttpResponseBadRequest("Invalid form data")

    try:
        related_season = Season.objects.get(
            item__media_id=media_id,
            item__source=source,
            item__season_number=season_number,
            item__episode_number=None,
            user=request.user,
        )
    except Season.DoesNotExist:
        tv_with_seasons_metadata = services.get_media_metadata(
            "tv_with_seasons",
            media_id,
            source,
            [season_number],
        )
        season_metadata = tv_with_seasons_metadata[f"season/{season_number}"]

        item, _ = Item.objects.get_or_create(
            media_id=media_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            season_number=season_number,
            defaults={
                "title": tv_with_seasons_metadata["title"],
                "image": season_metadata["image"],
            },
        )
        related_season = Season.objects.create(
            item=item,
            user=request.user,
            score=None,
            status=Status.IN_PROGRESS.value,
            notes="",
        )

        logger.info("%s did not exist, it was created successfully.", related_season)

    related_season.watch(episode_number, form.cleaned_data["end_date"])

    return helpers.redirect_back(request)


@require_http_methods(["GET", "POST"])
def create_entry(request):
    """Return the form for manually adding media items."""
    if request.method == "GET":
        media_types = MediaTypes.values
        return render(request, "app/create_entry.html", {"media_types": media_types})

    # Process the form submission
    form = ManualItemForm(request.POST, user=request.user)
    if not form.is_valid():
        # Handle form validation errors
        logger.error(form.errors.as_json())
        helpers.form_error_messages(form, request)
        return redirect("create_entry")

    # Try to save the item
    try:
        item = form.save()
    except IntegrityError:
        # Handle duplicate item
        media_name = form.cleaned_data["title"]
        if form.cleaned_data.get("season_number"):
            media_name += f" - Season {form.cleaned_data['season_number']}"
        if form.cleaned_data.get("episode_number"):
            media_name += f" - Episode {form.cleaned_data['episode_number']}"

        logger.exception("%s already exists in the database.", media_name)
        messages.error(request, f"{media_name} already exists in the database.")
        return redirect("create_entry")

    # Prepare and validate the media form
    updated_request = request.POST.copy()
    updated_request.update({"source": item.source, "media_id": item.media_id})
    media_form = get_form_class(item.media_type)(updated_request)

    if not media_form.is_valid():
        # Handle media form validation errors
        logger.error(media_form.errors.as_json())
        helpers.form_error_messages(media_form, request)

        # Delete the item since the media creation failed
        item.delete()
        logger.info("%s was deleted due to media form validation failure", item)
        return redirect("create_entry")

    # Save the media instance
    media_form.instance.user = request.user
    media_form.instance.item = item

    # Handle relationships based on media type
    if item.media_type == MediaTypes.SEASON.value:
        media_form.instance.related_tv = form.cleaned_data["parent_tv"]
    elif item.media_type == MediaTypes.EPISODE.value:
        media_form.instance.related_season = form.cleaned_data["parent_season"]

    media_form.save()

    # Success message
    msg = f"{item} added successfully."
    messages.success(request, msg)
    logger.info(msg)

    return redirect("create_entry")


@require_GET
def search_parent_tv(request):
    """Return the search results for parent TV shows."""
    query = request.GET.get("q", "").strip()

    if len(query) <= 1:
        return render(request, "app/components/search_parent_tv.html")

    logger.debug(
        "%s - Searching for TV shows with query: %s",
        request.user.username,
        query,
    )

    parent_tvs = TV.objects.filter(
        user=request.user,
        item__source=Sources.MANUAL.value,
        item__media_type=MediaTypes.TV.value,
        item__title__icontains=query,
    )[:5]

    return render(
        request,
        "app/components/search_parent_tv.html",
        {"results": parent_tvs, "query": query},
    )


@require_GET
def search_parent_season(request):
    """Return the search results for parent seasons."""
    query = request.GET.get("q", "").strip()

    if len(query) <= 1:
        return render(request, "app/components/search_parent_tv.html")

    logger.debug(
        "%s - Searching for seasons with query: %s",
        request.user.username,
        query,
    )

    parent_seasons = Season.objects.filter(
        user=request.user,
        item__source=Sources.MANUAL.value,
        item__media_type=MediaTypes.SEASON.value,
        item__title__icontains=query,
    )[:5]

    return render(
        request,
        "app/components/search_parent_season.html",
        {"results": parent_seasons, "query": query},
    )


@require_GET
def history_modal(
    request,
    source,
    media_type,
    media_id,
    season_number=None,
    episode_number=None,
):
    """Return the history page for a media item."""
    user_medias = BasicMedia.objects.filter_media(
        request.user,
        media_id,
        media_type,
        source,
        season_number=season_number,
        episode_number=episode_number,
    )

    total_medias = user_medias.count()
    timeline_entries = []
    for index, media in enumerate(user_medias, start=1):
        if history := media.history.all():
            media_entry_number = total_medias - index + 1
            timeline_entries.extend(
                history_processor.process_history_entries(
                    history,
                    media_type,
                    media_entry_number,
                    request.user,
                ),
            )
    return render(
        request,
        "app/components/fill_history.html",
        {
            "media_type": media_type,
            "timeline": timeline_entries,
            "total_medias": total_medias,
            "return_url": request.GET["return_url"],
        },
    )


@require_http_methods(["DELETE"])
def delete_history_record(request, media_type, history_id):
    """Delete a specific history record."""
    try:
        historical_model = apps.get_model(
            app_label="app",
            model_name=f"historical{media_type.lower()}",
        )

        historical_model.objects.get(
            history_id=history_id,
            history_user=request.user,
        ).delete()

        logger.info(
            "Deleted history record %s",
            str(history_id),
        )

        # Return empty 200 response - the element will be removed by HTMX
        return HttpResponse()

    except historical_model.DoesNotExist:
        logger.exception(
            "History record %s not found for user %s",
            str(history_id),
            str(request.user),
        )
        return HttpResponse("Record not found", status=404)


@require_GET
def statistics(request):
    """Return the statistics page."""
    # Set default date range to last year
    timeformat = "%Y-%m-%d"
    today = timezone.localdate()
    one_year_ago = today.replace(year=today.year - 1)

    # Get date parameters with defaults
    start_date_str = request.GET.get("start-date") or one_year_ago.strftime(timeformat)
    end_date_str = request.GET.get("end-date") or today.strftime(timeformat)

    if start_date_str == "all" and end_date_str == "all":
        start_date = None
        end_date = None
    else:
        start_date = parse_date(start_date_str)
        end_date = parse_date(end_date_str)

        if start_date and end_date:
            # Convert to datetime with timezone awareness
            start_date = timezone.make_aware(
                datetime.combine(start_date, datetime.min.time()),
            )

            # End date should be end of day
            end_date = timezone.make_aware(
                datetime.combine(end_date, datetime.max.time()),
            )

    # Get all user media data in a single operation
    user_media, media_count = stats.get_user_media(
        request.user,
        start_date,
        end_date,
    )

    # Calculate all statistics from the retrieved data
    media_type_distribution = stats.get_media_type_distribution(
        media_count,
    )
    score_distribution, top_rated = stats.get_score_distribution(user_media)
    status_distribution = stats.get_status_distribution(user_media)
    status_pie_chart_data = stats.get_status_pie_chart_data(
        status_distribution,
    )
    timeline = stats.get_timeline(user_media)

    activity_data = stats.get_activity_data(request.user, start_date, end_date)

    context = {
        "start_date": start_date,
        "end_date": end_date,
        "media_count": media_count,
        "activity_data": activity_data,
        "media_type_distribution": media_type_distribution,
        "score_distribution": score_distribution,
        "top_rated": top_rated,
        "status_distribution": status_distribution,
        "status_pie_chart_data": status_pie_chart_data,
        "timeline": timeline,
        "date_format_values": DateFormatChoices.values,
    }

    return render(request, "app/statistics.html", context)


@require_GET
def service_worker():
    """Serve the service worker file."""
    sw_path = Path(settings.STATICFILES_DIRS[0]) / "js" / "serviceworker.js"
    with sw_path.open() as f:
        response = HttpResponse(f.read(), content_type="application/javascript")
        response["Service-Worker-Allowed"] = "/"
        return response
