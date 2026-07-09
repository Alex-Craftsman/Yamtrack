import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db.models import Count, Q
from django.utils import timezone

from app import release_approval
from app.models import ReleaseApprovalCandidate, ReleaseApprovalItem, UserMessage
from users.models import User

logger = logging.getLogger(__name__)


@shared_task(name="Cleanup user messages")
def cleanup_user_messages():
    """Delete shown user messages older than the configured retention window."""
    cutoff = timezone.now() - timedelta(days=settings.USER_MESSAGE_RETENTION_DAYS)
    deleted_count, _ = UserMessage.objects.filter(
        shown_at__isnull=False,
        shown_at__lt=cutoff,
    ).delete()

    logger.info("Deleted %s old shown user messages.", deleted_count)

    return deleted_count


@shared_task(name="Sync release approval")
def sync_release_approval():
    """Sync Seerr requests and a bounded number of Radarr release candidates."""
    if not release_approval.is_configured():
        logger.info("Release approval sync skipped: not configured.")
        return {"items": 0, "candidates": 0}

    items = sync_release_approval_items()
    candidates = sync_release_approval_candidates()
    logger.info(
        "Release approval sync completed: %s items, %s candidate sets.",
        items,
        candidates,
    )
    return {"items": items, "candidates": candidates}


@shared_task(name="Grab release approval candidate")
def grab_release_approval_candidate(candidate_id, user_id=None):
    """Send an approved release candidate to the right Arr app outside the request path."""
    candidate = ReleaseApprovalCandidate.objects.select_related("item").get(
        id=candidate_id,
    )

    try:
        grab_release_approval_candidate_payload(candidate)
    except Exception as error:
        if (
            (candidate.release_data or {}).get("source") != "prowlarr_generic"
            and release_approval_candidate_was_grabbed(candidate)
        ):
            mark_release_approval_candidate_approved(candidate, user_id)
            return {"candidate": candidate.id, "status": "approved"}

        logger.exception(
            "Release approval grab failed for candidate %s",
            candidate.id,
        )
        candidate.status = ReleaseApprovalCandidate.Status.FAILED
        candidate.grab_error = str(error)[:4000]
        candidate.save(update_fields=["status", "grab_error", "synced_at"])
        return {"candidate": candidate.id, "status": "failed", "error": str(error)}

    mark_release_approval_candidate_approved(candidate, user_id)
    return {"candidate": candidate.id, "status": "approved"}


def mark_release_approval_candidate_approved(candidate, user_id=None):
    """Mark a candidate as successfully sent to its Arr app."""
    candidate.status = ReleaseApprovalCandidate.Status.APPROVED
    candidate.approved_by = User.objects.filter(id=user_id).first() if user_id else None
    candidate.approved_at = timezone.now()
    candidate.grab_error = ""
    candidate.save(
        update_fields=[
            "status",
            "approved_by",
            "approved_at",
            "grab_error",
            "synced_at",
        ],
    )


def release_approval_candidate_was_grabbed(candidate):
    """Return whether the relevant Arr app already queued/grabbed the candidate."""
    item = candidate.item
    if not item.radarr_movie_id:
        return False

    if item.media_type == ReleaseApprovalItem.MediaType.TV:
        queue = release_approval.sonarr_request(
            "GET",
            "queue",
            params={"seriesId": item.radarr_movie_id},
        )
        if queue.get("totalRecords", 0):
            return True

        history = release_approval.sonarr_request(
            "GET",
            "history/series",
            params={
                "seriesId": item.radarr_movie_id,
                "page": 1,
                "pageSize": 20,
                "sortKey": "date",
                "sortDirection": "descending",
            },
        )
        records = history.get("records", []) if isinstance(history, dict) else history
        return any(record.get("eventType") == "grabbed" for record in records)

    queue = release_approval.radarr_request(
        "GET",
        "queue",
        params={"movieId": item.radarr_movie_id},
    )
    if queue.get("totalRecords", 0):
        return True

    history = release_approval.radarr_request(
        "GET",
        "history/movie",
        params={
            "movieId": item.radarr_movie_id,
            "page": 1,
            "pageSize": 20,
            "sortKey": "date",
            "sortDirection": "descending",
        },
    )
    return any(
        record.get("eventType") == "grabbed"
        for record in history.get("records", [])
    )


def grab_release_approval_candidate_payload(candidate):
    """Grab a release, refreshing the Arr release cache when needed."""
    try:
        return grab_release_approval_candidate_release(candidate)
    except release_approval.ReleaseApprovalError as error:
        if "find requested release in cache" not in str(error).lower():
            raise

    fresh_release = find_fresh_release_for_candidate(candidate)
    if fresh_release is None:
        app_name = (
            "Sonarr"
            if candidate.item.media_type == ReleaseApprovalItem.MediaType.TV
            else "Radarr"
        )
        raise release_approval.ReleaseApprovalError(
            f"{app_name} release cache expired and the release was not found after a fresh search.",
        )

    update_release_approval_candidate_payload(candidate, fresh_release)
    return grab_release_approval_candidate_release(candidate)


def grab_release_approval_candidate_release(candidate):
    """Grab a candidate via Radarr or Sonarr."""
    if candidate.item.media_type == ReleaseApprovalItem.MediaType.TV:
        if (candidate.release_data or {}).get("source") == "prowlarr_generic":
            download_url = (
                candidate.release_data.get("downloadUrl")
                or candidate.release_data.get("magnetUrl")
            )
            if not download_url:
                raise release_approval.ReleaseApprovalError(
                    "Prowlarr candidate has no download URL.",
                )
            return release_approval.qbit_add_torrent_url(
                download_url,
                settings.QBIT_SONARR_CATEGORY,
            )
        return release_approval.grab_sonarr_release(candidate.release_data)
    return release_approval.grab_release(candidate.release_data)


def find_fresh_release_for_candidate(candidate):
    """Find a current Arr release matching a stored approval candidate."""
    item = candidate.item
    if not item.radarr_movie_id:
        return None

    releases = release_approval_candidate_releases(candidate)
    candidate_values = release_match_values(candidate.release_data)
    for release in releases:
        if candidate_values & release_match_values(release):
            return release
    return None


def release_approval_candidate_releases(candidate):
    """Fetch live releases for a stored approval item."""
    item = candidate.item
    if item.media_type == ReleaseApprovalItem.MediaType.TV:
        if (candidate.release_data or {}).get("source") == "prowlarr_generic":
            return []
        releases = []
        season_numbers = release_approval_search_seasons(item, item.movie_data)
        if season_numbers:
            for season_number in season_numbers:
                releases.extend(
                    release_approval.sonarr_releases(
                        item.radarr_movie_id,
                        season_number,
                    ),
                )
            return releases
        return release_approval.sonarr_releases(item.radarr_movie_id)
    return release_approval.radarr_releases(item.radarr_movie_id)


def release_match_values(release):
    """Return stable release values suitable for matching cached/live releases."""
    release = release or {}
    return {
        str(release.get(key))
        for key in ("guid", "infoUrl", "downloadUrl")
        if release.get(key)
    }


def update_release_approval_candidate_payload(candidate, release):
    """Persist a refreshed Radarr release payload before grabbing it."""
    quality = ((release.get("quality") or {}).get("quality") or {}).get("name") or ""
    candidate.release_data = release
    candidate.identity = release_approval.release_identity(release)
    candidate.title = release.get("title") or candidate.title
    candidate.indexer = release.get("indexer") or candidate.indexer
    candidate.info_url = release.get("infoUrl") or candidate.info_url
    candidate.quality = quality
    candidate.size = int(release.get("size") or 0)
    candidate.seeders = int(release.get("seeders") or 0)
    candidate.save(
        update_fields=[
            "release_data",
            "identity",
            "title",
            "indexer",
            "info_url",
            "quality",
            "size",
            "seeders",
            "synced_at",
        ],
    )


def sync_release_approval_items():
    """Sync Seerr movie and TV requests into Yamtrack."""
    seerr_requests = release_approval.seerr_requests()
    radarr_movies = release_approval.radarr_movies_by_tmdb()
    sonarr_series = release_approval.sonarr_series_by_tmdb()
    synced = 0

    for seerr_request in seerr_requests:
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
        synced += 1

    return synced


def sync_release_approval_candidates():
    """Refresh candidates for a bounded set of relevant requests."""
    items = (
        ReleaseApprovalItem.objects.filter(
            radarr_movie_id__isnull=False,
        )
        .filter(Q(has_file=False) | Q(media_type=ReleaseApprovalItem.MediaType.TV))
        .annotate(candidate_count=Count("candidates"))
        .order_by("candidate_count", "synced_at")[
            : settings.RELEASE_APPROVAL_SYNC_CANDIDATES_LIMIT
        ]
    )
    synced = 0

    for item in items:
        try:
            media = release_approval_item_media(item)
            item.movie_data = media
            item.has_file = release_approval_item_has_file(item, media)
            item.save(update_fields=["movie_data", "has_file", "synced_at"])
            sync_release_approval_item_candidates(item, media)
            synced += 1
        except release_approval.ReleaseApprovalError as error:
            if "404 client error" in str(error).lower():
                logger.warning(
                    "Release approval item has stale Arr id for %s tmdb:%s arr:%s; clearing it.",
                    item.media_type,
                    item.tmdb_id,
                    item.radarr_movie_id,
                )
                item.radarr_movie_id = None
                item.save(update_fields=["radarr_movie_id", "synced_at"])
                continue
            logger.exception(
                "Release approval candidate sync failed for %s tmdb:%s arr:%s",
                item.media_type,
                item.tmdb_id,
                item.radarr_movie_id,
            )

    return synced


def release_approval_item_media(item):
    """Fetch current Arr media payload for an approval item."""
    if item.media_type == ReleaseApprovalItem.MediaType.TV:
        return release_approval.sonarr_series(item.radarr_movie_id)
    return release_approval.radarr_movie(item.radarr_movie_id)


def release_approval_item_has_file(item, media):
    """Return whether the item already has downloaded media."""
    if item.media_type == ReleaseApprovalItem.MediaType.TV:
        return bool((media.get("statistics") or {}).get("episodeFileCount"))
    return bool(media.get("hasFile"))


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


def sync_release_approval_item_candidates(item, media):
    """Sync and score Arr release candidates for one item."""
    releases = release_approval_item_releases(item, media)
    scored = release_approval.score_releases(media, releases)
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
                "title": release_approval.release_display_title(media, release),
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


def release_approval_item_releases(item, media):
    """Fetch release candidates for an approval item."""
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
