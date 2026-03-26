from unittest.mock import patch

from django.utils import timezone

from events.models import Event

from .base import ApiTestCase
from .helpers import check_calendar_event_structure, check_pagination_structure


class CalendarTests(ApiTestCase):
    """Validate calendar endpoint contracts."""

    def setUp(self):
        """Create events and patch side effects for calendar tests."""
        super().setUp()
        self._calendar_patcher = patch(
            "app.models.Item.fetch_releases",
            return_value=None,
        )
        self._calendar_patcher.start()
        self.addCleanup(self._calendar_patcher.stop)

        self.calendar_events = [
            Event.objects.create(
                item=item,
                content_number=None,
                datetime=timezone.now(),
            )
            for item in self.items_by_type["movie"]
        ]

    def test_calendar_get(self):
        """Calendar GET should return paginated response data."""
        response = self.call_api("get", "api_calendar", headers=self.auth_headers)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("pagination", payload)
        check_pagination_structure(self, payload["pagination"])
        self.assertIn("results", payload)

        returned_media_ids = {
            result["item"]["media_id"] for result in payload["results"]
        }
        expected_media_ids = {item.media_id for item in self.items_by_type["movie"]}
        self.assertTrue(expected_media_ids.issubset(returned_media_ids))

        for item in payload["results"]:
            check_calendar_event_structure(self, item)

    @patch("api.views.tasks.reload_calendar.delay")
    def test_calendar_update_queues_task(self, mock_delay):
        """Calendar update should queue async task and return 202."""
        response = self.call_api(
            "post",
            "api_update_calendar",
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 202)
        mock_delay.assert_called_once()
