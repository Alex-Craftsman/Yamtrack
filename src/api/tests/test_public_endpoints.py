from django.conf import settings

from .base import ApiTestCase


class PublicEndpointsTests(ApiTestCase):
    """Verify public endpoints stay accessible without auth."""

    def test_health_endpoint(self):
        """Health endpoint test."""
        response = self.call_api("get", "api_health")

        self.assertIn(response.status_code, [200, 500])
        payload = response.json()
        self.assertIn("status", payload)
        self.assertIn("timestamp", payload)
        self.assertIn("checks", payload)

    def test_info_endpoint(self):
        """Info endpoint test."""
        response = self.call_api("get", "api_info")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("version", payload)
        self.assertEqual(payload["version"], settings.VERSION)
        self.assertIn("debug", payload)
        self.assertEqual(payload["debug"], settings.DEBUG)
        self.assertIn("frontend_url", payload)
        self.assertIn("language", payload)
        self.assertEqual(payload["language"], settings.LANGUAGE_CODE)
        self.assertIn("timezone", payload)
        self.assertEqual(payload["timezone"], settings.TIME_ZONE)
        self.assertIn("admin_enabled", payload)
        self.assertEqual(payload["admin_enabled"], settings.ADMIN_ENABLED)
        self.assertIn("track_time", payload)
        self.assertEqual(payload["track_time"], settings.TRACK_TIME)
