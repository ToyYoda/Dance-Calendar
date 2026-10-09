"""Event form parsing and validation shared by the organizer form (DC-20) and admin review (DC-31)."""

from datetime import datetime

from app.models import DANCE_STYLES, EVENT_TYPES, LEVELS, Event

REQUIRED = ["title", "dance_styles", "start", "location_name", "address", "city"]
TEXT_LIMITS = {
    "title": 200,
    "location_name": 200,
    "address": 255,
    "postcode": 16,
    "city": 100,
    "price": 100,
    "ticket_url": 500,
    "description": 4000,
}


def _dt(raw: str | None) -> datetime | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    return datetime.fromisoformat(raw).replace(second=0, microsecond=0)


def parse_event_form(form, require_complete: bool) -> tuple[dict, dict]:
    """Returns (values, errors). Values contain every editable field, None when empty."""
    values: dict = {}
    errors: dict = {}
    for name, limit in TEXT_LIMITS.items():
        raw = (form.get(name) or "").strip()
        if len(raw) > limit:
            errors[name] = f"Höchstens {limit} Zeichen."
        values[name] = raw[:limit] or None

    values["dance_styles"] = [s for s in form.getlist("dance_styles") if s in DANCE_STYLES]
    event_type = form.get("event_type") or None
    values["event_type"] = event_type if event_type in EVENT_TYPES else None
    level = form.get("level") or None
    values["level"] = level if level in LEVELS else None

    doors = (form.get("doors") or "").strip()
    values["doors"] = doors[:5] if doors else None

    for name in ("start", "end"):
        try:
            values[name] = _dt(form.get(name))
        except ValueError:
            values[name] = None
            errors[name] = "Ungültiges Datum."
    if values.get("start") and values.get("end") and values["end"] <= values["start"]:
        errors["end"] = "Das Ende muss nach dem Beginn liegen."

    url = values.get("ticket_url")
    if url and not url.startswith(("http://", "https://")):
        errors["ticket_url"] = "Der Link muss mit https:// beginnen."

    if require_complete:
        for name in REQUIRED:
            if not values.get(name) and name not in errors:
                errors[name] = "Pflichtfeld."
    elif not values.get("title"):
        errors["title"] = "Bitte gib mindestens einen Titel an."
    return values, errors


def apply_values(event: Event, values: dict) -> list[str]:
    """Write values to the event; returns the names of fields whose value changed."""
    changed = []
    for name, value in values.items():
        if getattr(event, name) != value:
            changed.append(name)
            setattr(event, name, value)
    if "title" in values and event.title is None:
        event.title = ""
    return changed


def form_values(event: Event | None) -> dict:
    if event is None:
        return {"dance_styles": []}
    return {name: getattr(event, name) for name in [*TEXT_LIMITS, "dance_styles", "event_type", "level", "doors", "start", "end"]}
