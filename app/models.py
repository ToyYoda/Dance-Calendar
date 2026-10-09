"""Database models.

Event times (start, end) are naive wall-clock times in settings.timezone.
All bookkeeping timestamps (created_at, expires_at, ...) are naive UTC.
"""

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Role(enum.StrEnum):
    USER = "USER"
    ORGANIZER = "ORGANIZER"
    ADMIN = "ADMIN"


class EventStatus(enum.StrEnum):
    DRAFT = "DRAFT"
    ADMIN_REVIEW = "ADMIN_REVIEW"
    LIVE = "LIVE"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class EventSource(enum.StrEnum):
    FORM = "FORM"
    BOT = "BOT"


class MediaStatus(enum.StrEnum):
    RECEIVED = "RECEIVED"
    FLYER = "FLYER"
    SPAM = "SPAM"
    FAILED = "FAILED"


DANCE_STYLES = ["Salsa", "Bachata", "Kizomba", "Zouk", "Tango", "West Coast Swing", "Lindy Hop", "Sonstiges"]
EVENT_TYPES = ["Party", "Workshop", "Festival", "Social"]
LEVELS = ["Open", "Anfänger", "Mittelstufe", "Fortgeschritten"]

# Fields the extractor fills and the reviewer checks (DC-12, DC-31)
EXTRACTED_FIELDS = [
    "title",
    "dance_styles",
    "event_type",
    "level",
    "start",
    "end",
    "doors",
    "location_name",
    "address",
    "postcode",
    "city",
    "price",
    "ticket_url",
    "description",
]


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    phone: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(80))
    role: Mapped[Role] = mapped_column(String(16), default=Role.USER)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    @property
    def display_name(self) -> str:
        if self.name:
            return self.name
        return "…" + self.phone[-4:]


class OtpCode(Base):
    __tablename__ = "otp_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    phone: Mapped[str] = mapped_column(String(20), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AuthSession(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped[User] = relationship()


class MagicLink(Base):
    __tablename__ = "magic_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class Media(Base):
    """An incoming image (WhatsApp or form upload) with its WebP variants (DC-16)."""

    __tablename__ = "media"

    id: Mapped[int] = mapped_column(primary_key=True)
    sender_phone: Mapped[str | None] = mapped_column(String(20))
    source: Mapped[EventSource] = mapped_column(String(8), default=EventSource.FORM)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    original_path: Mapped[str] = mapped_column(String(255))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[MediaStatus] = mapped_column(String(12), default=MediaStatus.RECEIVED)
    classifier_label: Mapped[str | None] = mapped_column(String(16))
    classifier_score: Mapped[float | None] = mapped_column(Float)
    classifier_reason: Mapped[str | None] = mapped_column(Text)
    classifier_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (Index("ix_events_status_start", "status", "start"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str | None] = mapped_column(Text)
    dance_styles: Mapped[list] = mapped_column(JSON, default=list)
    event_type: Mapped[str | None] = mapped_column(String(32))
    level: Mapped[str | None] = mapped_column(String(32))
    start: Mapped[datetime | None] = mapped_column(DateTime)
    end: Mapped[datetime | None] = mapped_column(DateTime)
    doors: Mapped[str | None] = mapped_column(String(16))
    location_name: Mapped[str | None] = mapped_column(String(200))
    address: Mapped[str | None] = mapped_column(String(255))
    postcode: Mapped[str | None] = mapped_column(String(16))
    city: Mapped[str | None] = mapped_column(String(100), index=True)
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    price: Mapped[str | None] = mapped_column(String(100))
    ticket_url: Mapped[str | None] = mapped_column(String(500))

    status: Mapped[EventStatus] = mapped_column(String(16), default=EventStatus.DRAFT)
    source: Mapped[EventSource] = mapped_column(String(8), default=EventSource.FORM)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    submitter_phone: Mapped[str | None] = mapped_column(String(20))
    media_id: Mapped[int | None] = mapped_column(ForeignKey("media.id", ondelete="SET NULL"))

    # AI bookkeeping (DC-13, DC-14, DC-31)
    confidence: Mapped[float | None] = mapped_column(Float)
    field_confidence: Mapped[dict] = mapped_column(JSON, default=dict)
    auto_filled: Mapped[list] = mapped_column(JSON, default=list)
    verified_fields: Mapped[list] = mapped_column(JSON, default=list)
    address_candidates: Mapped[list] = mapped_column(JSON, default=list)
    auto_published: Mapped[bool] = mapped_column(Boolean, default=False)

    # Editing lock (DC-34)
    locked_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime)

    # iCal SEQUENCE, bumped on every visible change (DC-07)
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    owner: Mapped[User | None] = relationship(foreign_keys=[owner_id])
    locked_by: Mapped[User | None] = relationship(foreign_keys=[locked_by_id])
    media: Mapped[Media | None] = relationship()
    attendances: Mapped[list["Attendance"]] = relationship(back_populates="event", cascade="all, delete-orphan")

    @property
    def is_cancelled(self) -> bool:
        return self.status == EventStatus.CANCELLED

    @property
    def full_address(self) -> str:
        city = " ".join(p for p in [self.postcode, self.city] if p)
        return ", ".join(p for p in [self.address, city] if p)

    def is_locked_for(self, user_id: int, now: datetime | None = None) -> bool:
        now = now or utcnow()
        return bool(
            self.locked_by_id
            and self.locked_by_id != user_id
            and self.locked_until
            and self.locked_until > now
        )


class Attendance(Base):
    __tablename__ = "attendances"
    __table_args__ = (UniqueConstraint("user_id", "event_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    event: Mapped[Event] = relationship(back_populates="attendances")


class FilterProfile(Base):
    __tablename__ = "filter_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(60))
    query: Mapped[str] = mapped_column(Text)  # url-encoded filter parameters
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)


class CalendarFeed(Base):
    """Secret-token iCal subscription: either the user's attendances or a saved filter (DC-07)."""

    __tablename__ = "calendar_feeds"

    id: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16))  # "attending" | "filter"
    query: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class FieldCorrection(Base):
    """An admin changed an AI-extracted field; feeds the monitoring dashboard (DC-35)."""

    __tablename__ = "field_corrections"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id", ondelete="SET NULL"))
    field: Mapped[str] = mapped_column(String(32), index=True)
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    admin_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ReviewDecision(Base):
    """Outcome of a reviewed bot event, for automation statistics and undo (DC-32, DC-35)."""

    __tablename__ = "review_decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id", ondelete="SET NULL"))
    admin_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(16))  # approve | reject | delete | not_event
    previous_status: Mapped[str] = mapped_column(String(16))
    undone: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class CancellationRequest(Base):
    """A cancellation message that could not be matched to exactly one event (DC-15)."""

    __tablename__ = "cancellation_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    sender_phone: Mapped[str] = mapped_column(String(20))
    text: Mapped[str] = mapped_column(Text)
    candidate_ids: Mapped[list] = mapped_column(JSON, default=list)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class OutboundMessage(Base):
    """Every WhatsApp message the system sends, for audit and the dev console."""

    __tablename__ = "outbound_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    to_phone: Mapped[str] = mapped_column(String(20), index=True)
    body: Mapped[str] = mapped_column(Text)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(255))
