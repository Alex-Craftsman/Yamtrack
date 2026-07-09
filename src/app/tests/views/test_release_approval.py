from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app.models import ReleaseApprovalCandidate, ReleaseApprovalItem
from app.views import sync_release_approval_items


def create_release_approval_item(**overrides):
    """Create a release approval item with sensible defaults."""
    defaults = {
        "media_type": ReleaseApprovalItem.MediaType.MOVIE,
        "seerr_request_id": 1,
        "tmdb_id": 100,
        "title": "Movie",
        "year": 2024,
        "seerr_status": "approved / pending",
        "request_data": {},
        "movie_data": {},
    }
    defaults.update(overrides)
    return ReleaseApprovalItem.objects.create(**defaults)


class ReleaseApprovalSyncTests(TestCase):
    """Test release approval sync behavior."""

    @patch("app.views.release_approval.sonarr_series_by_tmdb", return_value={})
    @patch("app.views.release_approval.radarr_movies_by_tmdb", return_value={})
    @patch("app.views.release_approval.seerr_requests")
    def test_sync_deletes_items_missing_from_seerr(
        self,
        mock_seerr_requests,
        _mock_radarr_movies_by_tmdb,
        _mock_sonarr_series_by_tmdb,
    ):
        """Remove local cards when Seerr no longer returns the request."""
        stale_item = create_release_approval_item(
            seerr_request_id=1,
            tmdb_id=165095,
            title="Stale Movie",
        )
        ReleaseApprovalCandidate.objects.create(
            item=stale_item,
            identity="release-1",
            title="Stale Movie 1080p",
            score=10,
            verdict="ok",
            release_data={},
        )
        create_release_approval_item(
            seerr_request_id=2,
            tmdb_id=200,
            title="Current Movie",
        )
        mock_seerr_requests.return_value = [
            {
                "id": 2,
                "media": {
                    "mediaType": "movie",
                    "tmdbId": 200,
                    "title": "Current Movie",
                },
            },
        ]

        sync_release_approval_items()

        self.assertFalse(ReleaseApprovalItem.objects.filter(id=stale_item.id).exists())
        self.assertFalse(
            ReleaseApprovalCandidate.objects.filter(identity="release-1").exists(),
        )
        self.assertTrue(
            ReleaseApprovalItem.objects.filter(seerr_request_id=2).exists(),
        )


class ReleaseApprovalDeleteViewTests(TestCase):
    """Test manual release approval card deletion."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def test_delete_item_hides_card_and_keeps_tombstone(self):
        """Hide a local release approval card from the UI."""
        item = create_release_approval_item()
        ReleaseApprovalCandidate.objects.create(
            item=item,
            identity="release-1",
            title="Movie 1080p",
            score=10,
            verdict="ok",
            release_data={},
        )

        response = self.client.post(
            reverse("release_approval_delete_item", kwargs={"item_id": item.id}),
        )

        self.assertRedirects(response, reverse("release_approval_requests"))
        item.refresh_from_db()
        self.assertIsNotNone(item.dismissed_at)
        self.assertTrue(
            ReleaseApprovalCandidate.objects.filter(identity="release-1").exists(),
        )
