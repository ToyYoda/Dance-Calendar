"""iCalendar export: single events and subscription feeds (DC-07)."""

from datetime import timedelta
from zoneinfo import ZoneInfo

from icalendar import Calendar
from icalendar import Event as ICalEvent

from app.config import settings
from app.models import Event, utcnow

DEFAULT_DURATION = timedelta(hours=4)


def build_calendar(events: list[Event], name: str) -> bytes:
    tz = ZoneInfo(settings.timezone)
    cal = Calendar()
    cal.add("prodid", "-//Dance Calendar//DE")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")
    cal.add("x-wr-calname", name)
    cal.add("x-wr-timezone", settings.timezone)
    cal.add("refresh-interval;value=duration", "PT6H")

    for ev in events:
        if ev.start is None:
            continue
        item = ICalEvent()
        host = settings.base_url.split("://", 1)[-1].split("/")[0]
        item.add("uid", f"event-{ev.id}@{host}")
        item.add("dtstamp", utcnow().replace(tzinfo=ZoneInfo("UTC")))
        item.add("last-modified", (ev.updated_at or utcnow()).replace(tzinfo=ZoneInfo("UTC")))
        item.add("sequence", ev.sequence or 0)
        item.add("dtstart", ev.start.replace(tzinfo=tz))
        item.add("dtend", (ev.end or ev.start + DEFAULT_DURATION).replace(tzinfo=tz))
        title = ev.title or "Tanzevent"
        url = f"{settings.base_url}/events/{ev.id}"
        if ev.is_cancelled:
            item.add("status", "CANCELLED")
            title = f"ABGESAGT: {title}"
        else:
            item.add("status", "CONFIRMED")
        item.add("summary", title)
        location = ", ".join(p for p in [ev.location_name, ev.full_address] if p)
        if location:
            item.add("location", location)
        if ev.lat is not None and ev.lng is not None:
            item.add("geo", (ev.lat, ev.lng))
        lines = []
        if ev.dance_styles:
            lines.append(" · ".join(ev.dance_styles))
        if ev.price:
            lines.append(f"Preis: {ev.price}")
        if ev.doors:
            lines.append(f"Einlass: {ev.doors}")
        if ev.description:
            lines.append(ev.description)
        lines.append(f"Details und Flyer: {url}")
        item.add("description", "\n".join(lines))
        item.add("url", url)
        cal.add_component(item)
    if hasattr(cal, "add_missing_timezones"):  # icalendar >= 6: VTIMEZONE for Outlook & co.
        cal.add_missing_timezones()
    return cal.to_ical()
