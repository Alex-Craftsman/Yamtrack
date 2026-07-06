import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from app import release_approval
from app.models import ReleaseApprovalCandidate, ReleaseApprovalItem, UserMessage

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


def sync_release_approval_items():
    """Sync Seerr movie requests into Yamtrack."""
    seerr_requests = release_approval.seerr_requests()
    radarr_movies = release_approval.radarr_movies_by_tmdb()
    synced = 0

    for seerr_request in seerr_requests:
        media = seerr_request.get("media") or {}
        tmdb_id = media.get("tmdbId")
        if not tmdb_id:
            continue

        movie = radarr_movies.get(int(tmdb_id), {})
        ReleaseApprovalItem.objects.update_or_create(
            seerr_request_id=seerr_request["id"],
            defaults={
                "media_type": ReleaseApprovalItem.MediaType.MOVIE,
                "tmdb_id": int(tmdb_id),
                "title": movie.get("title")
                or media.get("title")
                or media.get("originalTitle")
                or f"tmdb:{tmdb_id}",
                "year": movie.get("year"),
                "seerr_status": release_approval.request_status_label(seerr_request),
                "radarr_movie_id": movie.get("id"),
                "has_file": bool(movie.get("hasFile")),
                "request_data": seerr_request,
                "movie_data": movie,
            },
        )
        synced += 1

    return synced


def sync_release_approval_candidates():
    """Refresh candidates for a bounded set of relevant movie requests."""
    items = (
        ReleaseApprovalItem.objects.filter(
            media_type=ReleaseApprovalItem.MediaType.MOVIE,
            radarr_movie_id__isnull=False,
            has_file=False,
        )
        .order_by("synced_at")[: settings.RELEASE_APPROVAL_SYNC_CANDIDATES_LIMIT]
    )
    synced = 0

    for item in items:
        movie = release_approval.radarr_movie(item.radarr_movie_id)
        item.movie_data = movie
        item.has_file = bool(movie.get("hasFile"))
        item.save(update_fields=["movie_data", "has_file", "synced_at"])
        sync_release_approval_item_candidates(item, movie)
        synced += 1

    return synced


def sync_release_approval_item_candidates(item, movie):
    """Sync and score Radarr release candidates for one item."""
    releases = release_approval.radarr_releases(movie["id"])
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
