import os
import tempfile
import time
from pathlib import Path
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from app import posters
from app.models import Sources
from app.templatetags import app_tags


class PosterCacheTests(SimpleTestCase):
    """Tests for local poster filesystem caching."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.override = override_settings(
            POSTER_CACHE_DIR=Path(self.temp_dir.name),
            POSTER_CACHE_TIMEOUT=48 * 60 * 60,
            POSTER_CACHE_MAX_CONCURRENT_DOWNLOADS=5,
        )
        self.override.enable()
        self.addCleanup(self.override.disable)

    def mock_image_response(self, content=b"image-bytes"):
        response = Mock()
        response.content = content
        response.headers = {
            "Content-Type": "image/jpeg",
            "Content-Length": str(len(content)),
        }
        response.iter_content.return_value = [content]
        response.raise_for_status.return_value = None
        return response

    def test_get_poster_url_writes_metadata_and_returns_local_url(self):
        image_url = "https://example.com/images/poster.jpg"

        url = posters.get_poster_url(Sources.MAL.value, image_url)
        cache_key = posters.get_cache_key(image_url)

        self.assertEqual(url, f"/poster/{Sources.MAL.value}/{cache_key}/poster.jpg")
        self.assertEqual(
            posters.read_metadata(Sources.MAL.value, cache_key),
            {"url": image_url},
        )

    def test_poster_url_filter_leaves_local_urls_unchanged(self):
        image_url = "/poster/tmdb/cache-key/poster.jpg"

        self.assertEqual(app_tags.poster_url(image_url, Sources.TMDB.value), image_url)

    def test_poster_url_filter_uses_manual_source_by_default(self):
        image_url = "https://example.com/images/poster.jpg"
        cache_key = posters.get_cache_key(image_url)

        self.assertEqual(
            app_tags.poster_url(image_url),
            f"/poster/{Sources.MANUAL.value}/{cache_key}/poster.jpg",
        )

    @patch("app.posters.refresh_poster_in_background")
    def test_poster_redirects_and_caches_missing_file_in_background(
        self,
        mock_refresh,
    ):
        image_url = "https://example.com/images/poster.jpg"
        url = posters.get_poster_url(Sources.MAL.value, image_url)
        cache_key = posters.get_cache_key(image_url)

        response = self.client.get(url)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], image_url)
        mock_refresh.assert_called_once_with(
            Sources.MAL.value,
            cache_key,
            "poster.jpg",
        )

    @patch("app.posters.threading.Thread")
    @patch("app.posters.refresh_poster_safely")
    def test_refresh_poster_in_background_starts_download_when_slot_is_free(
        self,
        mock_refresh,
        mock_thread,
    ):
        def run_thread_target():
            mock_thread.call_args.kwargs["target"]()

        mock_thread.return_value.start.side_effect = run_thread_target

        posters.refresh_poster_in_background(
            Sources.MAL.value,
            "cache-key",
            "poster.jpg",
        )

        self.assertEqual(mock_refresh.call_count, 1)
        _, kwargs = mock_refresh.call_args
        self.assertIsNotNone(kwargs["semaphore"])

    @patch("app.posters.refresh_poster_safely")
    def test_refresh_poster_in_background_skips_when_limit_is_full(
        self,
        mock_refresh,
    ):
        with self.settings(POSTER_CACHE_MAX_CONCURRENT_DOWNLOADS=1):
            semaphore = posters.get_download_semaphore()
            self.assertTrue(semaphore.acquire(blocking=False))
            self.addCleanup(semaphore.release)

            posters.refresh_poster_in_background(
                Sources.MAL.value,
                "cache-key",
                "poster.jpg",
            )

        mock_refresh.assert_not_called()

    @patch("app.posters.refresh_poster_in_background")
    def test_poster_redirects_to_external_url_when_download_limit_is_full(
        self,
        mock_refresh,
    ):
        image_url = "https://example.com/images/poster.jpg"
        url = posters.get_poster_url(Sources.MAL.value, image_url)
        response = self.client.get(url)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], image_url)
        mock_refresh.assert_called_once()

    @patch("app.posters.requests.get")
    def test_poster_serves_fresh_cached_file_without_external_request(self, mock_get):
        image_url = "https://example.com/images/poster.jpg"
        url = posters.get_poster_url(Sources.MAL.value, image_url)
        cache_key = posters.get_cache_key(image_url)
        path = posters.get_poster_path(Sources.MAL.value, cache_key, "poster.jpg")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"cached-image")

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"cached-image")
        mock_get.assert_not_called()

    @patch("app.posters.requests.get")
    def test_refresh_poster_downloads_and_caches_file(self, mock_get):
        image_url = "https://example.com/images/poster.jpg"
        cache_key = posters.get_cache_key(image_url)
        posters.get_poster_url(Sources.MAL.value, image_url)
        mock_get.return_value = self.mock_image_response(b"refreshed-image")

        path = posters.refresh_poster(Sources.MAL.value, cache_key, "poster.jpg")

        self.assertEqual(path.read_bytes(), b"refreshed-image")
        mock_get.assert_called_once_with(image_url, timeout=120, stream=True)
        mock_get.return_value.close.assert_called_once()

    @patch("app.posters.refresh_poster_in_background")
    def test_poster_serves_stale_file_and_refreshes_in_background(self, mock_refresh):
        image_url = "https://example.com/images/poster.jpg"
        url = posters.get_poster_url(Sources.MAL.value, image_url)
        cache_key = posters.get_cache_key(image_url)
        path = posters.get_poster_path(Sources.MAL.value, cache_key, "poster.jpg")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"stale-image")
        stale_time = time.time() - (49 * 60 * 60)
        os.utime(path, (stale_time, stale_time))

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"stale-image")
        mock_refresh.assert_called_once_with(
            Sources.MAL.value,
            cache_key,
            "poster.jpg",
        )
