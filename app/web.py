"""Template rendering, flash messages and formatting helpers."""

from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import quote, unquote
from zoneinfo import ZoneInfo

from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.models import DANCE_STYLES, EVENT_TYPES, LEVELS
from app.phone import format_phone

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
WEEKDAYS_LONG = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
MONTHS = [
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
]
STATUS_LABELS = {
    "DRAFT": "Entwurf",
    "ADMIN_REVIEW": "In Prüfung",
    "LIVE": "Live",
    "REJECTED": "Abgelehnt",
    "CANCELLED": "Abgesagt",
}
FIELD_LABELS = {
    "title": "Titel",
    "dance_styles": "Tanzstil",
    "event_type": "Event-Typ",
    "level": "Level",
    "start": "Beginn",
    "end": "Ende",
    "doors": "Einlass",
    "location_name": "Location",
    "address": "Straße",
    "postcode": "PLZ",
    "city": "Stadt",
    "price": "Preis",
    "ticket_url": "Ticketlink",
    "description": "Beschreibung",
}
STYLE_CLASSES = {
    "Salsa": "salsa",
    "Bachata": "bachata",
    "Kizomba": "kizomba",
    "Zouk": "zouk",
}

FLASH_COOKIE = "dc_flash"


def fmt_date(d: date | datetime | None, long: bool = False) -> str:
    if d is None:
        return ""
    if long:
        return f"{WEEKDAYS_LONG[d.weekday()]}, {d.day}. {MONTHS[d.month - 1]} {d.year}"
    return f"{WEEKDAYS[d.weekday()]}, {d.day:02d}.{d.month:02d}."


def fmt_time(d: datetime | None) -> str:
    return f"{d:%H:%M}" if d else ""


def fmt_dt_input(d: datetime | None) -> str:
    return f"{d:%Y-%m-%dT%H:%M}" if d else ""


def fmt_local_time(utc_naive: datetime | None) -> str:
    """Bookkeeping timestamps are naive UTC; show them in the calendar's timezone."""
    if utc_naive is None:
        return ""
    local = utc_naive.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(settings.timezone))
    return f"{local:%H:%M}"


def media_url(key: str | None, variant: str = "preview") -> str:
    return f"/media/{key}/{variant}" if key else ""


def static_map_url(lat: float, lng: float) -> str:
    d = 0.004
    bbox = f"{lng - d},{lat - d},{lng + d},{lat + d}"
    return f"https://www.openstreetmap.org/export/embed.html?bbox={bbox}&layer=mapnik&marker={lat},{lng}"


def route_url(event) -> str:
    """'Route planen': geo: opens the native maps app on phones, the https fallback works everywhere."""
    dest = ", ".join(p for p in [event.location_name, event.full_address] if p)
    return f"https://www.google.com/maps/dir/?api=1&destination={quote(dest)}"


templates.env.filters.update(
    date=fmt_date,
    time=fmt_time,
    dtinput=fmt_dt_input,
    localtime=fmt_local_time,
    phone=format_phone,
)
templates.env.globals.update(
    DANCE_STYLES=DANCE_STYLES,
    EVENT_TYPES=EVENT_TYPES,
    LEVELS=LEVELS,
    STATUS_LABELS=STATUS_LABELS,
    FIELD_LABELS=FIELD_LABELS,
    STYLE_CLASSES=STYLE_CLASSES,
    MONTHS=MONTHS,
    WEEKDAYS=WEEKDAYS,
    media_url=media_url,
    static_map_url=static_map_url,
    route_url=route_url,
    settings=settings,
)


def render(request: Request, name: str, status_code: int = 200, **context):
    sess = getattr(request.state, "auth_session", None)
    context.setdefault("user", sess.user if sess else None)
    context.setdefault("is_admin", bool(sess and sess.is_admin))
    raw_flash = request.cookies.get(FLASH_COOKIE)
    context["flash"] = unquote(raw_flash) if raw_flash else None
    response = templates.TemplateResponse(request, name, context, status_code=status_code)
    if raw_flash:
        response.delete_cookie(FLASH_COOKIE)
    return response


def redirect(url: str, flash: str | None = None) -> RedirectResponse:
    response = RedirectResponse(url, status_code=303)
    if flash:
        response.set_cookie(FLASH_COOKIE, quote(flash), max_age=60, httponly=True, samesite="lax")
    return response


def safe_next(url: str | None, default: str = "/") -> str:
    """Only allow local redirect targets."""
    if url and url.startswith("/") and not url.startswith("//"):
        return url
    return default
