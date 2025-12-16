from django.apps import apps

from app.models import MediaTypes

EXCLUDED_FIELDS = {"id", "item", "user", "related_tv", "related_season"}


def delete_entry(media_type, history_id, user):
    """Delete a history entry for a given media type and user."""
    historical_model = apps.get_model(
        app_label="app",
        model_name=f"historical{media_type.lower()}",
    )

    historical_model.objects.get(
        history_id=history_id,
        history_user=user,
    ).delete()


def get_entry(media_type, history_id, user):
    """Retrieve and serialize a history entry for a given media type and user."""
    historical_model = apps.get_model(
        app_label="app",
        model_name=f"historical{media_type.lower()}",
    )

    return historical_model.objects.get(
        history_id=history_id,
        history_user=user,
    )


def process_history_entries(history_records, media_type):
    """Process history records into structured API timeline entries."""
    timeline_entries = []
    last = history_records.first()

    while last:
        entry = _build_history_entry(last, last.prev_record, media_type)
        if entry["changes"]:
            timeline_entries.append(entry)
        last = last.prev_record

    return timeline_entries


def _build_history_entry(new_record, old_record, media_type):
    """Build a single history entry with changes."""
    entry = {
        "id": new_record.history_id,
        "timestamp": new_record.history_date,
        "changes": [],
    }

    if old_record:
        delta = new_record.diff_against(old_record)
        for change in delta.changes:
            if _should_include_field(change.field, media_type):
                entry["changes"].append(
                    {
                        "field": change.field,
                        "old_value": change.old,
                        "new_value": change.new,
                    },
                )
    else:
        for field in new_record._meta.fields:
            if _should_include_field(field.name, media_type):
                value = getattr(new_record, field.name, None)
                if value is not None:
                    entry["changes"].append(
                        {
                            "field": field.name,
                            "old_value": None,
                            "new_value": value,
                        },
                    )

    return entry


def _should_include_field(field_name, media_type):
    """Check if a field should be included in the history entry."""
    if field_name.startswith("history_"):
        return False
    if field_name in EXCLUDED_FIELDS:
        return False
    return not (field_name == "progress" and media_type == MediaTypes.MOVIE.value)
