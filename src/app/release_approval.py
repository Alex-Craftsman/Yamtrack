"""Release approval helpers for Seerr/Radarr/Sonarr requests."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

import requests
from django.conf import settings


class ReleaseApprovalError(Exception):
    """Raised when an upstream release approval API call fails."""


def clean_title(value: str | None) -> str:
    """Return a normalized title for conservative release matching."""
    value = (value or "").lower().replace("&", " and ")
    value = re.sub(r"[\[\]().,:;!?'\"/\\|_+-]+", " ", value)
    value = re.sub(
        r"\b(2160p|1080p|720p|480p|web[- ]?dl|webrip|bdrip|bluray|hdr|dv|"
        r"dolby|vision|hevc|x265|h265|h264|amzn)\b",
        " ",
        value,
    )
    return re.sub(r"\s+", " ", value).strip()


def title_aliases(movie: dict[str, Any]) -> list[str]:
    """Return normalized requested/alternate titles for an Arr media item."""
    titles = [
        movie.get("title"),
        movie.get("originalTitle"),
        *[
            alt.get("title")
            for alt in movie.get("alternateTitles", [])
            if alt.get("title")
        ],
    ]
    return [clean_title(value) for value in titles if clean_title(value)]


def strip_mapped_movie_prefix(title: str, movie: dict[str, Any]) -> str:
    """Remove Radarr's mapped movie prefix from a tracker release title.

    Radarr can return a release title as
    "<requested movie> (year) <actual tracker title>" when it has mapped an
    indexer result to a movie search. The tracker did not publish that prefix,
    so we strip it for display and scoring when the remainder looks like a real
    title instead of just quality/source tokens.
    """
    title = title or ""
    normalized_title = clean_title(title)
    aliases = sorted(title_aliases(movie), key=len, reverse=True)
    year = str(movie.get("year") or "")
    quality_tokens = {
        "2160p",
        "1080p",
        "720p",
        "480p",
        "web",
        "webrip",
        "webdl",
        "bdrip",
        "bluray",
        "hdr",
        "dv",
        "uhd",
    }

    for alias in aliases:
        if not normalized_title.startswith(alias):
            continue

        # Work on normalized text to decide whether stripping is appropriate,
        # then strip the human-readable prefix from the original title below.
        remainder = normalized_title[len(alias) :].strip()
        if year and remainder.startswith(year):
            remainder = remainder[len(year) :].strip()
        remainder_words = remainder.split()
        if not remainder_words or remainder_words[0] in quality_tokens:
            continue

        original_pattern = re.compile(
            rf"^\s*{re.escape(movie.get('title') or '')}\s*(?:\({re.escape(year)}\)|{re.escape(year)})?\s*",
            re.I,
        )
        stripped = original_pattern.sub("", title, count=1).strip()
        if stripped and stripped != title:
            return stripped

    return title


def release_display_title(movie: dict[str, Any], release: dict[str, Any]) -> str:
    """Return the title Yamtrack should show and score for a release."""
    return strip_mapped_movie_prefix(release.get("title") or "", movie)


def request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    json: dict[str, Any] | None = None,
    timeout: int = 60,
) -> Any:
    """Perform an HTTP JSON request and normalize errors."""
    try:
        session = requests.Session()
        session.trust_env = False
        response = session.request(
            method,
            url,
            headers=headers,
            params=params,
            json=json,
            timeout=timeout,
        )
        response.raise_for_status()
    except requests.RequestException as error:
        msg = f"{method} {url} failed: {error}"
        if getattr(error, "response", None) is not None:
            msg += f" {error.response.text[:500]}"
        raise ReleaseApprovalError(msg) from error

    if not response.content:
        return None
    return response.json()


def is_configured() -> bool:
    """Return whether all required upstream settings are configured."""
    return all(
        [
            settings.SEERR_API_KEY,
            settings.SEERR_URL,
            settings.RADARR_API_KEY,
            settings.RADARR_URL,
            settings.SONARR_API_KEY,
            settings.SONARR_URL,
        ],
    )


def seerr_requests(media_type: str | None = None) -> list[dict[str, Any]]:
    """Fetch recent requests from Seerr."""
    take = settings.RELEASE_APPROVAL_REQUEST_TAKE
    results = []
    skip = 0

    while True:
        params = {
            "take": take,
            "skip": skip,
            "filter": "all",
            "sort": "added",
            "sortDirection": "desc",
        }
        if media_type:
            params["mediaType"] = media_type

        payload = request_json(
            "GET",
            f"{settings.SEERR_URL.rstrip('/')}/api/v1/request",
            headers={"X-Api-Key": settings.SEERR_API_KEY},
            params=params,
        )
        page_results = payload.get("results", [])
        results.extend(page_results)
        if len(page_results) < take:
            return results
        skip += take


def radarr_request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json: dict[str, Any] | None = None,
    timeout: int = 60,
) -> Any:
    """Call Radarr API v3."""
    query = {"apikey": settings.RADARR_API_KEY}
    if params:
        query.update(params)
    return request_json(
        method,
        f"{settings.RADARR_URL.rstrip('/')}/api/v3/{path.lstrip('/')}",
        params=query,
        json=json,
        timeout=timeout,
    )


def radarr_movies_by_tmdb() -> dict[int, dict[str, Any]]:
    """Return Radarr movies keyed by TMDB ID."""
    return {
        int(movie["tmdbId"]): movie
        for movie in radarr_request("GET", "movie")
        if movie.get("tmdbId")
    }


def sonarr_request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json: dict[str, Any] | None = None,
    timeout: int = 60,
) -> Any:
    """Call Sonarr API v3."""
    query = {"apikey": settings.SONARR_API_KEY}
    if params:
        query.update(params)
    return request_json(
        method,
        f"{settings.SONARR_URL.rstrip('/')}/api/v3/{path.lstrip('/')}",
        params=query,
        json=json,
        timeout=timeout,
    )


def sonarr_series_by_tmdb() -> dict[int, dict[str, Any]]:
    """Return Sonarr series keyed by TMDB ID."""
    return {
        int(series["tmdbId"]): series
        for series in sonarr_request("GET", "series")
        if series.get("tmdbId")
    }


def sonarr_series(series_id: int) -> dict[str, Any]:
    """Fetch a Sonarr series."""
    return sonarr_request("GET", f"series/{series_id}")


def radarr_movie(movie_id: int) -> dict[str, Any]:
    """Fetch a Radarr movie."""
    return radarr_request("GET", f"movie/{movie_id}")


def radarr_releases(movie_id: int) -> list[dict[str, Any]]:
    """Fetch release candidates for a Radarr movie."""
    return radarr_request(
        "GET",
        "release",
        params={"movieId": movie_id},
        timeout=120,
    )


def radarr_movie_history(movie_id: int, page_size: int = 50) -> list[dict[str, Any]]:
    """Fetch recent Radarr history records for a movie."""
    payload = radarr_request(
        "GET",
        "history/movie",
        params={
            "movieId": movie_id,
            "page": 1,
            "pageSize": page_size,
            "sortKey": "date",
            "sortDirection": "descending",
        },
    )
    return payload.get("records", []) if isinstance(payload, dict) else payload


def sonarr_releases(
    series_id: int,
    season_number: int | None = None,
    timeout: int = 120,
) -> list[dict[str, Any]]:
    """Fetch release candidates for a Sonarr series or season."""
    params: dict[str, Any] = {"seriesId": series_id}
    if season_number is not None:
        params["seasonNumber"] = season_number
    return sonarr_request(
        "GET",
        "release",
        params=params,
        timeout=timeout,
    )


def sonarr_episode_files(series_id: int) -> list[dict[str, Any]]:
    """Fetch Sonarr episode files for a series."""
    return sonarr_request("GET", "episodefile", params={"seriesId": series_id})


def sonarr_series_history(series_id: int, page_size: int = 250) -> list[dict[str, Any]]:
    """Fetch recent Sonarr history records for a series."""
    payload = sonarr_request(
        "GET",
        "history/series",
        params={
            "seriesId": series_id,
            "page": 1,
            "pageSize": page_size,
            "sortKey": "date",
            "sortDirection": "descending",
        },
    )
    return payload.get("records", []) if isinstance(payload, dict) else payload


def prowlarr_request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: int = 60,
) -> Any:
    """Call Prowlarr API."""
    if not settings.PROWLARR_URL or not settings.PROWLARR_API_KEY:
        raise ReleaseApprovalError("Prowlarr is not configured.")

    query = {"apikey": settings.PROWLARR_API_KEY}
    if params:
        query.update(params)
    return request_json(
        method,
        f"{settings.PROWLARR_URL.rstrip('/')}/api/v1/{path.lstrip('/')}",
        params=query,
        timeout=timeout,
    )


def prowlarr_search(query: str, limit: int = 50) -> list[dict[str, Any]]:
    """Run a generic Prowlarr search."""
    query = (query or "").strip()
    if not query:
        return []
    return prowlarr_request(
        "GET",
        "search",
        params={"query": query, "type": "search", "limit": limit},
        timeout=90,
    )


def qbit_add_torrent_url(download_url: str, category: str) -> Any:
    """Download a torrent via Yamtrack and add it to qBittorrent."""
    if not settings.QBIT_URL:
        raise ReleaseApprovalError("qBittorrent is not configured.")

    download_url = normalize_internal_prowlarr_url(download_url)
    try:
        session = requests.Session()
        session.trust_env = False
        if settings.QBIT_USER or settings.QBIT_PASS:
            login = session.post(
                f"{settings.QBIT_URL.rstrip('/')}/api/v2/auth/login",
                data={"username": settings.QBIT_USER, "password": settings.QBIT_PASS},
                timeout=30,
            )
            login.raise_for_status()
            if login.status_code != 204 and login.text.strip().lower() != "ok.":
                raise ReleaseApprovalError(f"qBittorrent login failed: {login.text[:200]}")

        torrent_response = session.get(download_url, timeout=90)
        torrent_response.raise_for_status()
        content_type = (torrent_response.headers.get("content-type") or "").lower()
        content = torrent_response.content or b""
        if not content.startswith(b"d") or b"<rss" in content[:200].lower():
            raise ReleaseApprovalError(
                "Prowlarr did not return a torrent file: "
                f"status={torrent_response.status_code}, "
                f"content-type={content_type}, "
                f"body={content[:200]!r}",
            )
        add_response = session.post(
            f"{settings.QBIT_URL.rstrip('/')}/api/v2/torrents/add",
            data={"category": category},
            files={
                "torrents": (
                    "release.torrent",
                    torrent_response.content,
                    "application/x-bittorrent",
                ),
            },
            timeout=60,
        )
        add_response.raise_for_status()
        if add_response.text.strip().lower() not in {"", "ok."}:
            raise ReleaseApprovalError(
                f"qBittorrent rejected the torrent: {add_response.text[:500]}",
            )
    except requests.RequestException as error:
        msg = f"qBittorrent add failed: {error}"
        if getattr(error, "response", None) is not None:
            msg += f" {error.response.text[:500]}"
        raise ReleaseApprovalError(msg) from error

    return add_response.text


def normalize_internal_prowlarr_url(url: str) -> str:
    """Make Prowlarr download URLs usable from inside the Docker network."""
    if not settings.PROWLARR_URL:
        return url
    return re.sub(
        r"^https?://(?:127\.0\.0\.1|localhost):9696",
        settings.PROWLARR_URL.rstrip("/"),
        url or "",
    )


def grab_release(release: dict[str, Any]) -> Any:
    """Send a selected release to Radarr for download."""
    return radarr_request("POST", "release", json=release, timeout=120)


def grab_sonarr_release(release: dict[str, Any]) -> Any:
    """Send a selected release to Sonarr for download."""
    return sonarr_request("POST", "release", json=release, timeout=120)


def release_identity(release: dict[str, Any]) -> str:
    """Return a stable identity for a release from current Radarr results."""
    for key in ("guid", "downloadUrl", "infoUrl"):
        if release.get(key):
            return str(release[key])
    return release.get("title", "")


def request_status_label(request: dict[str, Any]) -> str:
    """Return a compact Seerr request/media status label."""
    request_map = {
        1: "pending",
        2: "approved",
        3: "declined",
        4: "failed",
        5: "completed",
    }
    media_map = {
        1: "unknown",
        2: "pending",
        3: "processing",
        4: "partially available",
        5: "available",
        6: "deleted",
    }
    media = request.get("media") or {}
    return (
        f"{request_map.get(request.get('status'), request.get('status'))} / "
        f"{media_map.get(media.get('status'), media.get('status'))}"
    )


@dataclass
class ReleaseScore:
    """Scored release candidate."""

    score: int
    verdict: str
    reasons: list[str]
    warnings: list[str]


def score_release(movie: dict[str, Any], release: dict[str, Any]) -> ReleaseScore:
    """Score a release with conservative title/external-id heuristics."""
    score = 0
    reasons: list[str] = []
    warnings: list[str] = []

    title = release_display_title(movie, release)
    title_clean = clean_title(title)
    valid_titles = title_aliases(movie)

    release_tmdb = int(release.get("tmdbId") or 0)
    release_tvdb = int(release.get("tvdbId") or 0)
    release_imdb = str(release.get("imdbId") or "").strip()
    movie_tmdb = int(movie.get("tmdbId") or 0)
    movie_tvdb = int(movie.get("tvdbId") or 0)
    movie_imdb = str(movie.get("imdbId") or "").strip()
    is_tv = bool(movie_tvdb)

    if release_tmdb and release_tmdb == movie_tmdb:
        score += 140
        reasons.append("tmdb id matches")
    elif release_tmdb and release_tmdb != movie_tmdb:
        score -= 500
        warnings.append(f"tmdb id mismatch: {release_tmdb}")
    elif release_tvdb and movie_tvdb and release_tvdb == movie_tvdb:
        score += 140
        reasons.append("tvdb id matches")
    elif release_tvdb and movie_tvdb and release_tvdb != movie_tvdb:
        score -= 500
        warnings.append(f"tvdb id mismatch: {release_tvdb}")
    elif release_imdb and movie_imdb and release_imdb == movie_imdb:
        score += 120
        reasons.append("imdb id matches")
    elif release_imdb and movie_imdb and release_imdb != movie_imdb:
        score -= 500
        warnings.append(f"imdb id mismatch: {release_imdb}")
    else:
        score -= 25
        warnings.append("no external id")

    exact_title_hit = any(
        value and re.search(rf"(^|\b){re.escape(value)}(\b|$)", title_clean)
        for value in valid_titles
    )
    starts_title = any(value and title_clean.startswith(value) for value in valid_titles)
    if starts_title:
        score += 50
        reasons.append("title starts with requested title")
    elif exact_title_hit:
        score += 35
        reasons.append("title contains requested/alternate title")
    else:
        score -= 100
        warnings.append("requested title not found")

    year = int(movie.get("year") or 0)
    years = [int(value) for value in re.findall(r"\b(19\d{2}|20\d{2})\b", title)]
    if year and year in years:
        score += 25
        reasons.append("year matches")
    elif year and years:
        score -= 35
        warnings.append(f"year mismatch: {', '.join(map(str, sorted(set(years))))}")

    if not is_tv:
        slash_parts = [clean_title(part) for part in re.split(r"\s+/\s+", title)]
        unknown_slash_parts = []
        for part in slash_parts[1:]:
            if part and not any(part == valid or part in valid or valid in part for valid in valid_titles):
                unknown_slash_parts.append(part)
        if unknown_slash_parts:
            score -= 120
            warnings.append(
                "contains another title after slash: "
                + ", ".join(unknown_slash_parts[:2]),
            )

        year_tail = re.split(r"\b(?:19\d{2}|20\d{2})\b", title, maxsplit=1)
        if len(year_tail) == 2:
            tail = clean_title(year_tail[1])
            tail_words = [
                word
                for word in tail.split()[:5]
                if word not in {"web", "dl", "uhd", "hdr", "rip"}
            ]
            if tail_words:
                tail_head = " ".join(tail_words[:3])
                if tail_head and not any(
                    tail_head in valid or valid in tail_head for valid in valid_titles
                ):
                    score -= 45
                    warnings.append(f"possible second title after year: {tail_head}")

        genres = [genre.lower() for genre in movie.get("genres", [])]
        if "horror" not in genres and re.search(r"\b(ужасы|horror)\b", title, re.I):
            score -= 80
            warnings.append("release says horror, requested movie is not horror")

    if release.get("rejected"):
        score -= 200
        warnings.append("Arr rejected this release")
    warnings.extend(str(rejection) for rejection in release.get("rejections") or [])

    quality = ((release.get("quality") or {}).get("quality") or {}).get("name")
    if quality:
        reasons.append(quality)
        if "2160" in quality:
            score += 12
        elif "1080" in quality:
            score += 8

    seeders = int(release.get("seeders") or 0)
    if seeders > 0:
        score += min(20, int(math.log2(seeders + 1) * 4))
        reasons.append(f"{seeders} seeders")
    else:
        warnings.append("no seeders")

    if score >= 100 and not warnings:
        verdict = "High confidence"
    elif score >= 40:
        verdict = "Review"
    else:
        verdict = "Suspicious"

    return ReleaseScore(score, verdict, reasons, warnings)


def score_releases(movie: dict[str, Any], releases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return releases enriched with scores, sorted best-first."""
    scored = []
    for release in releases:
        release_score = score_release(movie, release)
        scored.append(
            {
                "release": release,
                "identity": release_identity(release),
                "score": release_score,
            },
        )
    return sorted(scored, key=lambda item: item["score"].score, reverse=True)
