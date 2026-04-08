from unittest.mock import patch

from app.models import MediaTypes, Sources, Status

from .base import YamtrackApiTestCase
from .helpers import (
    check_complete_media_structure,
    check_consumption_structure,
    check_media_structure,
    check_minimized_lists_structure,
    check_pagination_structure,
)


class MediaCoreTests(YamtrackApiTestCase):
    """Validate media endpoint behavior for core media types."""

    def setUp(self):
        """Set up."""
        super().setUp()

    def test_media_list_get_returns_paginated_payload(self):
        """Media list endpoint should return standard pagination payload."""
        response = self.call_api("get", "api_media_list", headers=self.auth_headers)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("pagination", payload)
        check_pagination_structure(
            self,
            payload["pagination"],
            total=sum(
                len(items)
                for media_type, items in self.items_by_type.items()
                if media_type not in {MediaTypes.SEASON.value, MediaTypes.EPISODE.value}
            ),
            limit=20,
            offset=0,
        )
        self.assertIn("results", payload)
        for item in payload["results"]:
            check_media_structure(self, item)

    def test_media_list_get_with_type_filter_returns_filtered_results(self):
        """Media list endpoint should filter results by media type."""
        response = self.call_api(
            "get",
            "api_media_list",
            params={"media_type": MediaTypes.TV.value},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("pagination", payload)
        self.assertIn("results", payload)
        check_pagination_structure(
            self,
            payload["pagination"],
            total=len(self.tv_medias),
            limit=20,
            offset=0,
        )
        for item in payload["results"]:
            check_media_structure(self, item)
            self.assertEqual(item["item"]["media_type"], MediaTypes.TV.value)

    def test_media_list_get_with_status_filter_returns_filtered_results(self):
        """Media list endpoint should filter results by status."""
        completed_movie = self.movie_medias[0]
        completed_movie.status = Status.COMPLETED.value
        completed_movie.save(update_fields=["status"])

        response = self.call_api(
            "get",
            "api_media_list",
            params={
                "media_type": MediaTypes.MOVIE.value,
                "status": "3",
            },
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("results", payload)
        self.assertGreaterEqual(len(payload["results"]), 1)
        for item in payload["results"]:
            check_media_structure(self, item)
            self.assertEqual(item["item"]["media_type"], MediaTypes.MOVIE.value)
            self.assertEqual(item["status"], 3)

    def test_media_list_get_with_search_filter_returns_filtered_results(self):
        """Media list endpoint should filter results by search query."""
        response = self.call_api(
            "get",
            "api_media_list",
            params={
                "media_type": MediaTypes.MOVIE.value,
                "search": "Movie 1",
            },
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("results", payload)
        self.assertGreaterEqual(len(payload["results"]), 1)
        for item in payload["results"]:
            check_media_structure(self, item)
            self.assertIn("movie 1", item["item"]["title"].lower())

    def test_media_list_get_with_sort_filter_returns_sorted_results(self):
        """Media list endpoint should sort results when requested."""
        response = self.call_api(
            "get",
            "api_media_list",
            params={"media_type": MediaTypes.MOVIE.value, "sort": "title_desc"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        titles = [item["item"]["title"] for item in payload["results"]]
        self.assertEqual(titles, sorted(titles, reverse=True))

    def test_media_list_get_with_exclude_filter_excludes_type(self):
        """Media list endpoint should exclude requested media types."""
        response = self.call_api(
            "get",
            "api_media_list",
            params={"exclude": MediaTypes.MOVIE.value},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        for item in payload["results"]:
            check_media_structure(self, item)
            self.assertNotEqual(item["item"]["media_type"], MediaTypes.MOVIE.value)

    def test_media_list_get_invalid_status_returns_not_found(self):
        """Media list endpoint should reject unsupported status values."""
        response = self.call_api(
            "get",
            "api_media_list",
            params={"status": "abc"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 404)

    def test_media_list_get_invalid_sort_returns_not_found(self):
        """Media list endpoint should reject unsupported sort values."""
        response = self.call_api(
            "get",
            "api_media_list",
            params={"sort": "unknown_desc"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 404)

    def test_media_type_list_invalid_type_returns_bad_request(self):
        """Media-type list endpoint should reject unsupported media types."""
        response = self.call_api(
            "get",
            "api_media_type_list",
            args=("invalid",),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 400)

    def test_media_type_list_get_returns_paginated_payload(self):
        """Media-type list endpoint should return standard pagination payload."""
        response = self.call_api(
            "get",
            "api_media_type_list",
            args=(MediaTypes.MOVIE.value,),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("pagination", payload)
        check_pagination_structure(
            self,
            payload["pagination"],
            total=len(self.movie_medias),
            limit=20,
            offset=0,
        )
        self.assertIn("results", payload)
        for item in payload["results"]:
            check_media_structure(self, item)

    def test_media_type_list_get_with_search_filter_returns_filtered_results(self):
        """Media-type list endpoint should filter by search query."""
        response = self.call_api(
            "get",
            "api_media_type_list",
            args=(MediaTypes.MOVIE.value,),
            params={"search": "Movie 1"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("results", payload)
        self.assertGreaterEqual(len(payload["results"]), 1)
        for item in payload["results"]:
            check_media_structure(self, item)
            self.assertIn("movie 1", item["item"]["title"].lower())

    def test_media_type_list_get_with_sort_filter_returns_sorted_results(self):
        """Media-type list endpoint should sort by requested field."""
        response = self.call_api(
            "get",
            "api_media_type_list",
            args=(MediaTypes.MOVIE.value,),
            params={"sort": "title_desc"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        titles = [item["item"]["title"] for item in payload["results"]]
        self.assertEqual(titles, sorted(titles, reverse=True))

    def test_media_type_list_get_with_status_filter_returns_filtered_results(self):
        """Media-type list endpoint should filter by status."""
        completed_movie = self.movie_medias[0]
        completed_movie.status = Status.COMPLETED.value
        completed_movie.save(update_fields=["status"])

        response = self.call_api(
            "get",
            "api_media_type_list",
            args=(MediaTypes.MOVIE.value,),
            params={"status": "3"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("results", payload)
        self.assertGreaterEqual(len(payload["results"]), 1)
        for item in payload["results"]:
            check_media_structure(self, item)
            self.assertEqual(item["status"], 3)

    def test_media_type_list_post_creates_media(self):
        """Media-type create endpoint should create new media items."""
        response = self.call_api(
            "post",
            "api_media_type_list",
            args=(MediaTypes.MOVIE.value,),
            payload={
                "source": "manual",
                "title": "Manual Movie",
                "image": "https://example.com/poster.jpg",
                "status": 3,
            },
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, 201)
        payload = response.json()

        check_media_structure(self, payload)
        self.assertEqual(payload["item"]["media_id"], 1)
        self.assertEqual(payload["item"]["source"], "manual")
        self.assertEqual(payload["item"]["media_type"], MediaTypes.MOVIE.value)

    def test_media_type_list_post_invalid_type_returns_bad_request(self):
        """Media-type create endpoint should reject unsupported media types."""
        response = self.call_api(
            "post",
            "api_media_type_list",
            args=("invalid",),
            payload={"source": "tmdb", "media_id": "501"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 400)

    def test_media_type_list_post_missing_body_returns_bad_request(self):
        """Media-type create endpoint should reject missing payloads."""
        response = self.call_api(
            "post",
            "api_media_type_list",
            args=(MediaTypes.MOVIE.value,),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 400)

    def test_media_type_list_post_missing_media_id_returns_bad_request(self):
        """Provider-backed create should require media_id."""
        response = self.call_api(
            "post",
            "api_media_type_list",
            args=(MediaTypes.MOVIE.value,),
            payload={"source": "tmdb"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 400)

    @patch("api.views.services.get_media_metadata")
    def test_media_detail_get_returns_expected_shape(self, mock_metadata):
        """Media detail GET should return a complete serialized payload."""
        # TODO: Use real mock data fixtures instead of hardcoding values
        tv_item = self.items_by_type[MediaTypes.TV.value][0]
        mock_metadata.return_value = {
            "id": None,
            "media_id": 1,
            "source": "tmdb",
            "source_url": "https://www.themoviedb.org/tv/1",
            "media_type": "tv",
            "title": "Pride",
            "max_progress": 11,
            "image": "https://image.tmdb.org/t/p/w500/rnahKduAA2VZFgrXemu97Fh6OD2.jpg",
            "synopsis": "Haru Satonaka is the captain of an ice-hockey team, a star athlete who stakes everything on hockey but can only consider love as a game. Aki Murase is a woman who has been waiting for her lover who went abroad two years ago. These two persons start a relationship while frankly admitting to each other that it is only a love game. …The result is the unfolding of a drama of people with their respective pasts and with their pride as individuals.",
            "genres": [
                "Drama"
            ],
            "score": 7.8,
            "score_count": 30,
            "details": {
                "format": "TV",
                "first_air_date": "2004-01-12",
                "last_air_date": "2004-03-22",
                "status": "Ended",
                "seasons": 1,
                "episodes": 11,
                "runtime": "1h 0m",
                "studios": [
                "Fuji Television Network"
                ],
                "country": "Japan",
                "languages": [
                "Japanese"
                ],
                "tvdb_id": 84831,
                "last_episode_season": 1,
                "next_episode_season": None
            },
            "related": {
                "seasons": [
                {
                    "id": None,
                    "consumption_id": None,
                    "item": {
                    "media_id": 1,
                    "source": "tmdb",
                    "media_type": "season",
                    "title": "Season 1",
                    "image": "https://image.tmdb.org/t/p/w500/nCcGD18HmDFunCl8KBigqUPlIi8.jpg",
                    "season_number": 1,
                    "episode_number": None
                    },
                    "item_id": "tv/tmdb/1/1",
                    "parent_id": "tv/tmdb/1",
                    "tracked": False,
                    "created_at": None,
                    "score": None,
                    "status": None,
                    "progress": None,
                    "progressed_at": None,
                    "start_date": None,
                    "end_date": None,
                    "notes": None,
                    "lists": []
                }
                ]
            },
            "item_id": "tv/tmdb/1",
            "parent_id": None,
            "tracked": False,
            "consumptions_number": 0,
            "consumptions": [],
            "lists": []
        }

        response = self.call_api(
            "get",
            "api_media_detail",
            args=(MediaTypes.TV.value, tv_item.source, tv_item.media_id),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        check_complete_media_structure(self, payload)

    def test_media_detail_get_invalid_type_returns_bad_request(self):
        """Media detail endpoint should reject unsupported media types."""
        response = self.call_api(
            "get",
            "api_media_detail",
            args=("invalid", "tmdb", 501),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 400)

    def test_media_detail_get_invalid_media_id_returns_internal_server_error(self):
        """Media detail endpoint currently returns 500 for invalid provider ids."""
        response = self.call_api(
            "get",
            "api_media_detail",
            args=(MediaTypes.MOVIE.value, "tmdb", 999999),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 500)

    def test_media_detail_patch_with_unknown_field_returns_bad_request(self):
        """Media PATCH should reject fields outside the allowed whitelist."""
        movie_item = self.items_by_type[MediaTypes.MOVIE.value][0]
        response = self.call_api(
            "patch",
            "api_media_detail",
            args=(MediaTypes.MOVIE.value, movie_item.source, movie_item.media_id),
            payload={"unknown_field": "value"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("no valid fields", response.json().get("detail", "").lower())
