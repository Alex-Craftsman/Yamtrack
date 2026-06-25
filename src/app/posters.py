import json
import logging
import mimetypes
import re
import threading
import time
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlparse

import requests
from django.conf import settings
from django.contrib.auth.decorators import login_not_required
from django.http import FileResponse, Http404
from django.urls import reverse
from django.views.decorators.http import require_GET

logger = logging.getLogger(__name__)

_refreshing_posters = set()
_refresh_lock = threading.Lock()


def get_poster_url(source, image_url):
    """Return a local poster URL for an external image URL."""
    if not image_url or image_url == settings.IMG_NONE:
        return settings.IMG_NONE

    parsed_url = urlparse(image_url)
    if parsed_url.scheme not in ("http", "https"):
        return image_url

    cache_key = get_cache_key(image_url)
    filename = get_safe_filename(image_url, cache_key)
    write_metadata(source, cache_key, image_url)
    return reverse("poster", args=[source, cache_key, filename])


def get_cache_key(image_url):
    """Return a stable cache key for an external image URL."""
    return sha256(image_url.encode()).hexdigest()


def get_safe_filename(image_url, cache_key):
    """Return a filesystem and URL-safe filename for an external image URL."""
    filename = Path(urlparse(image_url).path).name
    filename = re.sub(r"[^A-Za-z0-9._-]", "_", filename)
    if filename:
        return filename
    return f"{cache_key}.jpg"


def get_poster_dir(source, cache_key):
    """Return the local cache directory for a poster."""
    return Path(settings.POSTER_CACHE_DIR) / source / cache_key


def get_metadata_path(source, cache_key):
    """Return the metadata path for a cached poster."""
    return get_poster_dir(source, cache_key) / "metadata.json"


def write_metadata(source, cache_key, image_url):
    """Persist the original image URL next to the poster cache file."""
    metadata_path = get_metadata_path(source, cache_key)
    if metadata_path.exists():
        return

    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    with metadata_path.open("w") as file:
        json.dump({"url": image_url}, file)


def read_metadata(source, cache_key):
    """Read cached poster metadata."""
    metadata_path = get_metadata_path(source, cache_key)
    with metadata_path.open() as file:
        return json.load(file)


def get_poster_path(source, cache_key, filename):
    """Return the local filesystem path for a poster."""
    safe_filename = Path(filename).name
    return get_poster_dir(source, cache_key) / safe_filename


def is_cache_fresh(path):
    """Return True when the local poster file is still inside the cache window."""
    if not path.exists():
        return False

    age = time.time() - path.stat().st_mtime
    return age < settings.POSTER_CACHE_TIMEOUT


def refresh_poster(source, cache_key, filename):
    """Download a poster and atomically save it to the local cache."""
    path = get_poster_path(source, cache_key, filename)
    metadata = read_metadata(source, cache_key)
    url = metadata["url"]

    response = requests.get(url, timeout=settings.REQUEST_TIMEOUT)
    response.raise_for_status()

    content_type = response.headers.get("Content-Type", "")
    if not content_type.startswith("image/"):
        msg = f"Unexpected poster content type: {content_type}"
        raise ValueError(msg)

    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(f"{path.suffix}.tmp")
    with temp_path.open("wb") as file:
        file.write(response.content)
    temp_path.replace(path)
    return path


def refresh_poster_safely(source, cache_key, filename):
    """Refresh a poster and log failures without breaking the caller."""
    try:
        refresh_poster(source, cache_key, filename)
    except (
        OSError,
        requests.RequestException,
        ValueError,
        KeyError,
        json.JSONDecodeError,
    ):
        logger.exception(
            "Failed to refresh poster %s/%s/%s",
            source,
            cache_key,
            filename,
        )


def refresh_poster_in_background(source, cache_key, filename):
    """Start a background poster refresh unless one is already running."""
    key = (source, cache_key, filename)
    with _refresh_lock:
        if key in _refreshing_posters:
            return
        _refreshing_posters.add(key)

    def refresh():
        try:
            refresh_poster_safely(source, cache_key, filename)
        finally:
            with _refresh_lock:
                _refreshing_posters.discard(key)

    threading.Thread(target=refresh, daemon=True).start()


@login_not_required
@require_GET
def poster(request, source, cache_key, filename):  # noqa: ARG001
    """Return a cached poster, refreshing stale files in the background."""
    path = get_poster_path(source, cache_key, filename)

    if path.exists():
        if not is_cache_fresh(path):
            refresh_poster_in_background(source, cache_key, filename)
        content_type, _ = mimetypes.guess_type(path)
        return FileResponse(path.open("rb"), content_type=content_type)

    try:
        path = refresh_poster(source, cache_key, filename)
    except (
        OSError,
        requests.RequestException,
        ValueError,
        KeyError,
        json.JSONDecodeError,
    ) as error:
        msg = "Poster not found"
        raise Http404(msg) from error

    content_type, _ = mimetypes.guess_type(path)
    return FileResponse(path.open("rb"), content_type=content_type)
