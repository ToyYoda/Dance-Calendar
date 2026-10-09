"""Event queries, status transitions and settings shared by all routers."""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.messaging import send_message
from app.models import (
    DANCE_STYLES,
    EVENT_TYPES,
    LEVELS,
    Attendance,
    Event,
    EventStatus,
    ReviewDecision,
    Role,
    Setting,
    User,
    utcnow,
)

def local_now() -> datetime:
    """Naive wall-clock time in the calendar's timezone (the unit event times are stored in)."""
    return datetime.now(ZoneInfo(settings.timezone)).replace(tzinfo=None, second=0, microsecond=0)


# --- Tunable thresholds (DC-13, DC-35) --------------------------------------

DEFAULTS = {"auto_publish_threshold": 0.95, "field_threshold": 0.85}


def get_threshold(db: Session, key: str) -> float:
    row = db.get(Setting, key)
    return float(row.value) if row else DEFAULTS[key]


def set_threshold(db: Session, key: str, value: float) -> None:
    value = max(0.0, min(1.0, value))
    row = db.get(Setting, key)
    if row:
        row.value = str(value)
    else:
        db.add(Setting(key=key, value=str(value)))
    db.commit()


# --- Public filters (DC-02, DC-04, DC-07) -----------------------------------


@dataclass
class Filters:
    styles: list[str] = field(default_factory=list)
    types: list[str] = field(default_factory=list)
    levels: list[str] = field(default_factory=list)
    city: str = ""
    q: str = ""
    date_from: date | None = None
    date_to: date | None = None

    @classmethod
    def from_params(cls, params) -> "Filters":
        """Accepts a Starlette QueryParams/FormData or any mapping with getlist()."""

        def _date(name):
            raw = (params.get(name) or "").strip()
            try:
                return date.fromisoformat(raw) if raw else None
            except ValueError:
                return None

        return cls(
            styles=[s for s in params.getlist("style") if s in DANCE_STYLES],
            types=[t for t in params.getlist("type") if t in EVENT_TYPES],
            levels=[lv for lv in params.getlist("level") if lv in LEVELS],
            city=(params.get("city") or "").strip()[:100],
            q=(params.get("q") or "").strip()[:100],
            date_from=_date("from"),
            date_to=_date("to"),
        )

    def to_query(self) -> str:
        pairs = [("style", s) for s in self.styles] + [("type", t) for t in self.types]
        pairs += [("level", lv) for lv in self.levels]
        for key, val in [("city", self.city), ("q", self.q)]:
            if val:
                pairs.append((key, val))
        if self.date_from:
            pairs.append(("from", self.date_from.isoformat()))
        if self.date_to:
            pairs.append(("to", self.date_to.isoformat()))
        return urlencode(pairs)

    @property
    def active(self) -> bool:
        return bool(self.to_query())


def public_events(db: Session, filters: Filters, start: datetime | None = None, end: datetime | None = None):
    """LIVE and CANCELLED events that have not ended yet, matching the filters (DC-01, DC-02)."""
    now = local_now()
    stmt = select(Event).where(Event.status.in_([EventStatus.LIVE, EventStatus.CANCELLED]), Event.start.is_not(None))
    # hide past events: an event is past when its end (or start + 6 h) lies behind us
    stmt = stmt.where(or_(Event.end >= now, (Event.end.is_(None) & (Event.start >= now - timedelta(hours=6)))))
    lower = max(filter(None, [start, _at(filters.date_from)]), default=None)
    if lower:
        stmt = stmt.where(Event.start >= lower)
    upper = min(filter(None, [end, _at(filters.date_to, end_of_day=True)]), default=None)
    if upper:
        stmt = stmt.where(Event.start <= upper)
    if filters.types:
        stmt = stmt.where(Event.event_type.in_(filters.types))
    if filters.levels:
        stmt = stmt.where(Event.level.in_(filters.levels))
    if filters.city:
        stmt = stmt.where(func.lower(Event.city) == filters.city.lower())
    if filters.q:
        like = f"%{filters.q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Event.title).like(like),
                func.lower(Event.location_name).like(like),
                func.lower(Event.city).like(like),
                func.lower(Event.description).like(like),
            )
        )
    events = list(db.scalars(stmt.order_by(Event.start)))
    if filters.styles:
        # dance_styles is a JSON list; filter in Python to stay database-agnostic
        wanted = set(filters.styles)
        events = [e for e in events if wanted & set(e.dance_styles or [])]
    return events


def _at(d: date | None, end_of_day: bool = False) -> datetime | None:
    if d is None:
        return None
    return datetime.combine(d, datetime.max.time() if end_of_day else datetime.min.time())


def known_cities(db: Session) -> list[str]:
    rows = db.scalars(
        select(Event.city).where(Event.status == EventStatus.LIVE, Event.city.is_not(None)).distinct()
    )
    return sorted({c for c in rows if c})


def attendance_count(db: Session, event_id: int) -> int:
    return db.scalar(select(func.count()).select_from(Attendance).where(Attendance.event_id == event_id)) or 0


# --- Status transitions -----------------------------------------------------


def event_url(event: Event) -> str:
    return f"{settings.base_url}/events/{event.id}"


def touch(event: Event) -> None:
    """Visible change: bump the iCal SEQUENCE so subscribed calendars update (DC-07)."""
    event.sequence = (event.sequence or 0) + 1


def make_organizer(user: User | None) -> None:
    if user and user.role == Role.USER:
        user.role = Role.ORGANIZER


def notify_submitter(db: Session, event: Event, text: str) -> None:
    phone = event.submitter_phone or (event.owner.phone if event.owner else None)
    if phone:
        send_message(db, phone, text)


def publish(db: Session, event: Event, admin: User | None = None, auto: bool = False) -> None:
    previous = event.status
    event.status = EventStatus.LIVE
    event.auto_published = auto
    touch(event)
    if admin is not None:
        db.add(ReviewDecision(event_id=event.id, admin_id=admin.id, action="approve", previous_status=previous))
    db.commit()
    if previous != EventStatus.LIVE and (auto or admin is not None):
        notify_submitter(db, event, f"Dein Event „{event.title}“ ist jetzt im Kalender: {event_url(event)}")


def reject(db: Session, event: Event, admin: User, action: str = "reject") -> None:
    previous = event.status
    event.status = EventStatus.REJECTED
    touch(event)
    db.add(ReviewDecision(event_id=event.id, admin_id=admin.id, action=action, previous_status=previous))
    db.commit()
    if action == "reject":
        notify_submitter(db, event, f"Dein eingereichtes Event „{event.title or 'ohne Titel'}“ wurde nicht veröffentlicht.")


def cancel(db: Session, event: Event) -> None:
    event.status = EventStatus.CANCELLED
    event.cancelled_at = utcnow()
    touch(event)
    db.commit()
