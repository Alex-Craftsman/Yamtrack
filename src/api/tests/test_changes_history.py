"""Contract tests for changes history endpoints."""

from .base import ApiTestCase
from .helpers import check_changes_history_entry_structure, check_pagination_structure


class ChangesHistoryTests(ApiTestCase):
    """Validate changes history endpoint contracts."""

    def test_changes_history_list_get_returns_paginated_entries(self):
        """Changes-history list should return paginated entries with expected shape."""
        movie_item = self.items_by_type["movie"][0]
        response = self.call_api(
            "get",
            "api_media_changes_history",
            args=("movie", movie_item.source, movie_item.media_id),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("pagination", payload)
        check_pagination_structure(self, payload["pagination"])
        self.assertIn("results", payload)
        self.assertGreater(len(payload["results"]), 0)

        returned_ids = {str(entry["id"]) for entry in payload["results"]}
        expected_id = str(self.changes_history_entries["movie"].history_id)
        self.assertIn(expected_id, returned_ids)

        for entry in payload["results"]:
            check_changes_history_entry_structure(self, entry)

    def test_changes_history_detail_get_returns_entry_structure(self):
        """Changes-history detail should return an entry with expected shape."""
        history_entry = self.changes_history_entries["movie"]
        response = self.call_api(
            "get",
            "api_media_changes_history_detail",
            args=("movie", str(history_entry.history_id)),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        check_changes_history_entry_structure(self, payload)
        self.assertEqual(str(payload["id"]), str(history_entry.history_id))

    def test_changes_history_detail_delete_removes_entry(self):
        """Changes-history detail delete should remove an existing history entry."""
        history_entry = self.changes_history_entries["movie"]

        delete_response = self.call_api(
            "delete",
            "api_media_changes_history_detail",
            args=("movie", str(history_entry.history_id)),
            headers=self.auth_headers,
        )
        self.assertEqual(delete_response.status_code, 204)

        get_response = self.call_api(
            "get",
            "api_media_changes_history_detail",
            args=("movie", str(history_entry.history_id)),
            headers=self.auth_headers,
        )
        self.assertEqual(get_response.status_code, 404)

    def test_changes_history_detail_invalid_type_returns_bad_request(self):
        """Changes-history detail endpoints should reject invalid types."""
        get_response = self.call_api(
            "get",
            "api_media_changes_history_detail",
            args=("invalid", "1"),
            headers=self.auth_headers,
        )
        self.assertEqual(get_response.status_code, 400)

        delete_response = self.call_api(
            "delete",
            "api_media_changes_history_detail",
            args=("invalid", "1"),
            headers=self.auth_headers,
        )
        self.assertEqual(delete_response.status_code, 400)

    def test_changes_history_detail_not_found_returns_404(self):
        """Changes-history detail should return 404 for missing records."""
        response = self.call_api(
            "get",
            "api_media_changes_history_detail",
            args=("movie", "99999"),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 404)
