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


def check_pagination_structure(test_case, item):
    """Assert that the given item follows the expected pagination structure."""
    test_case.assertIn("total", item)
    test_case.assertIn("limit", item)
    test_case.assertIn("offset", item)
    test_case.assertIn("next", item)
    test_case.assertIn("previous", item)


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
