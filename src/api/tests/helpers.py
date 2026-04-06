from django.conf import settings


def check_calendar_event_structure(test_case, item):
    """Assert that the given item follows the calendar event structure."""
    test_case.assertIn("id", item)
    test_case.assertIn("item", item)
    check_item_structure(test_case, item["item"])
    test_case.assertIn("item_id", item)
    check_item_id_structure(test_case, item["item_id"])
    test_case.assertIn("parent_id", item)
    check_item_id_structure(test_case, item["parent_id"]) if item[
        "parent_id"
    ] else test_case.assertIsNone(item["parent_id"])
    test_case.assertIn("content_number", item)
    test_case.assertIn("datetime", item)
    test_case.assertIn("notification_sent", item)


def check_changes_history_change_structure(test_case, item):
    """Assert that the given item follows a single changes-history change shape."""
    test_case.assertIn("field", item)
    test_case.assertIn("old_value", item)
    test_case.assertIn("new_value", item)


def check_changes_history_entry_structure(test_case, item):
    """Assert that the given item follows the changes-history entry structure."""
    test_case.assertIn("id", item)
    test_case.assertIn("item_id", item)
    if item["item_id"] is not None:
        check_item_id_structure(test_case, item["item_id"])
    test_case.assertIn("timestamp", item)
    test_case.assertIn("changes", item)
    test_case.assertIsInstance(item["changes"], list)
    for change in item["changes"]:
        check_changes_history_change_structure(test_case, change)


def check_consumption_structure(test_case, item):
    """Assert that the given item follows the expected consumption structure."""
    test_case.assertIn("consumption_id", item)
    test_case.assertIn("created", item)
    test_case.assertIn("score", item)
    test_case.assertIn("progress", item)
    test_case.assertIn("status", item)
    test_case.assertIn("start_date", item)
    test_case.assertIn("end_date", item)
    test_case.assertIn("notes", item)


def check_health_structure(test_case, item):
    """Assert that the given item follows the expected health endpoint structure."""
    test_case.assertIn("status", item)
    test_case.assertIn(item["status"], ["ok", "unavailable"])
    test_case.assertIn("timestamp", item)
    test_case.assertIn("checks", item)
    for check in item["checks"].values():
            test_case.assertIn("status", check)
            test_case.assertIn(check["status"], ["ok", "error"])
            test_case.assertIn("error", check)


def check_info_structure(test_case, item):
    """Assert that the given item follows the expected info endpoint structure."""
    test_case.assertIn("version", item)
    test_case.assertEqual(item["version"], settings.VERSION)
    test_case.assertIn("debug", item)
    test_case.assertEqual(item["debug"], settings.DEBUG)
    test_case.assertIn("frontend_url", item)
    test_case.assertIn("language", item)
    test_case.assertEqual(item["language"], settings.LANGUAGE_CODE)
    test_case.assertIn("timezone", item)
    test_case.assertEqual(item["timezone"], settings.TIME_ZONE)
    test_case.assertIn("admin_enabled", item)
    test_case.assertEqual(item["admin_enabled"], settings.ADMIN_ENABLED)
    test_case.assertIn("track_time", item)
    test_case.assertEqual(item["track_time"], settings.TRACK_TIME)


def check_item_structure(test_case, item):
    """Assert that the given item follows the expected media item structure."""
    test_case.assertIn("media_id", item)
    test_case.assertIn("source", item)
    test_case.assertIn("media_type", item)
    test_case.assertIn("title", item)
    test_case.assertIn("image", item)
    test_case.assertIn("season_number", item)
    test_case.assertIn("episode_number", item)


def check_item_id_structure(test_case, item):
    """Assert that the given item ID follows the expected structure."""
    test_case.assertIsInstance(item, str)
    parts = item.split("/")
    test_case.assertEqual(len(parts), 3)


def check_media_structure(test_case, item):
    """Assert that the given item follows the expected media structure."""
    test_case.assertIn("id", item)
    test_case.assertIn("consumption_id", item)
    test_case.assertIn("item", item)
    check_item_structure(test_case, item["item"])
    test_case.assertIn("item_id", item)
    check_item_id_structure(test_case, item["item_id"])
    test_case.assertIn("parent_id", item)
    check_item_id_structure(test_case, item["parent_id"]) if item[
        "parent_id"
    ] else test_case.assertIsNone(item["parent_id"])
    test_case.assertIn("tracked", item)
    test_case.assertIn("created_at", item)
    test_case.assertIn("score", item)
    test_case.assertIn("status", item)
    test_case.assertIn("progress", item)
    test_case.assertIn("progressed_at", item)
    test_case.assertIn("start_date", item)
    test_case.assertIn("end_date", item)
    test_case.assertIn("notes", item)
    test_case.assertIn("lists", item)
    for lst in item["lists"]:
        check_minimized_lists_structure(test_case, lst)


def check_minimized_lists_structure(test_case, lists):
    """Assert that the given list follows the expected minimized structure."""
    test_case.assertIn("list_id", lists)
    test_case.assertIn("list_item_id", lists)


def check_pagination_structure(test_case, item, *, total=None, limit=None, offset=None):
    """Assert that the given item follows the expected pagination structure."""
    test_case.assertIn("total", item)
    if total is not None:
        test_case.assertEqual(item["total"], total)
    test_case.assertIn("limit", item)
    if limit is not None:
        test_case.assertEqual(item["limit"], limit)
    test_case.assertIn("offset", item)
    if offset is not None:
        test_case.assertEqual(item["offset"], offset)
    test_case.assertIn("next", item)
    test_case.assertIn("previous", item)
