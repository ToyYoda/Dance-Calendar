"""WhatsApp ingestion pipeline (DC-10 … DC-15).

image → store + WebP (DC-16) → triage (DC-11) → extraction (DC-12) → address completion (DC-14)
      → confidence routing (DC-13) → notify owner (DC-21)
text  → cancellation detection (DC-15)
"""

import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import ai, geocode, images
from app.config import settings
from app.messaging import send_message
from app.models import (
    CancellationRequest,
    Event,
    EventSource,
    EventStatus,
    Media,
    MediaStatus,
)
from app.security import get_or_create_user, is_whitelisted_admin
from app.services import cancel, event_url, get_threshold, local_now, make_organizer, publish

log = logging.getLogger(__name__)

GEO_AMBIGUOUS_PENALTY = 0.8


# --- Images -----------------------------------------------------------------


def receive_image(db: Session, data: bytes, sender_phone: str) -> Media:
    """Store an incoming flyer and confirm receipt. Processing happens in process_media()."""
    key, width, height = images.store_image(data)
    media = Media(
        sender_phone=sender_phone,
        source=EventSource.BOT,
        original_path=key,
        width=width,
        height=height,
    )
    db.add(media)
    get_or_create_user(db, sender_phone)
    db.commit()
    send_message(db, sender_phone, "📥 Danke! Dein Bild ist angekommen und wird gerade geprüft.")
    return media


def process_media(db: Session, media_id: int) -> Event | None:
    media = db.get(Media, media_id)
    if media is None or media.status not in (MediaStatus.RECEIVED, MediaStatus.FAILED):
        return None
    try:
        verdict = ai.classify(media.original_path)
        media.classifier_label = "flyer" if verdict.is_flyer else "no_flyer"
        media.classifier_score = verdict.score
        media.classifier_reason = verdict.reason
        media.classifier_ms = verdict.ms
        if not verdict.is_flyer:
            media.status = MediaStatus.SPAM
            db.commit()
            log.info("Media %s classified as non-flyer: %s", media.id, verdict.reason)
            return None
        media.status = MediaStatus.FLYER
        db.commit()
        return create_event_from_media(db, media, classifier_score=verdict.score)
    except ai.AIError as exc:
        media.status = MediaStatus.FAILED
        media.error = str(exc)[:1000]
        db.commit()
        log.warning("AI processing of media %s failed: %s", media.id, exc)
        return None


def create_event_from_media(db: Session, media: Media, classifier_score: float = 1.0) -> Event:
    extraction = ai.extract(media.original_path, today=local_now().date())
    owner = get_or_create_user(db, media.sender_phone) if media.sender_phone else None
    event = Event(
        source=EventSource.BOT,
        media_id=media.id,
        owner_id=owner.id if owner else None,
        submitter_phone=media.sender_phone,
        status=EventStatus.DRAFT,
        dance_styles=[],
    )
    for name, value in extraction.values.items():
        setattr(event, name, value)
    event.title = event.title or ""
    field_conf = dict(extraction.confidence)

    confidence = min(extraction.overall, classifier_score)
    confidence = complete_address(event, field_conf, confidence)
    event.field_confidence = field_conf
    event.confidence = round(confidence, 3)

    db.add(event)
    make_organizer(owner)
    db.commit()

    if event.confidence > get_threshold(db, "auto_publish_threshold") and event.start:
        publish(db, event, auto=True)
    else:
        event.status = EventStatus.ADMIN_REVIEW
        db.commit()
        if event.submitter_phone:
            send_message(
                db,
                event.submitter_phone,
                "🔎 Wir haben deinen Flyer erkannt. Ein Admin prüft die Daten kurz, dann erscheint das Event im Kalender.",
            )
    if owner and event.submitter_phone:
        send_message(
            db,
            event.submitter_phone,
            f"Für deine Nummer wurde das Event „{event.title or 'ohne Titel'}“ angelegt. "
            f"Nicht dein Event? Hier abgeben: {event_url(event)}/disown",
        )
    return event


def complete_address(event: Event, field_conf: dict, confidence: float) -> float:
    """Fill missing street/postcode/coordinates (DC-14). Returns the adjusted overall confidence."""
    if event.lat is not None and event.address and event.postcode:
        return confidence
    result = geocode.search(event.location_name, event.address, event.city)
    if result.status == "unique":
        best = result.best
        auto = []
        for name in ("address", "postcode", "city"):
            if not getattr(event, name) and best.get(name):
                setattr(event, name, best[name])
                field_conf[name] = 0.9
                auto.append(name)
        event.lat, event.lng = best["lat"], best["lng"]
        event.auto_filled = auto
        event.address_candidates = []
    elif result.status == "ambiguous":
        event.address_candidates = result.candidates
        confidence *= GEO_AMBIGUOUS_PENALTY
    return confidence


# --- Text messages / cancellations -----------------------------------------

CANCEL_PATTERN = re.compile(
    r"\b(f[äa]llt\s+(heute\s+|leider\s+|morgen\s+)*aus|abgesagt|absagen|storniert|cancel+ed|cancel|entf[äa]llt)\b",
    re.IGNORECASE,
)
DATE_PATTERN = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.?(\d{2,4})?\b")


def is_cancellation(text: str) -> bool:
    return bool(CANCEL_PATTERN.search(text or ""))


def _match_score(event: Event, text: str) -> int:
    score = 0
    low = text.lower()
    words = [w for w in re.findall(r"\w{4,}", (event.title or "").lower())]
    if words and any(w in low for w in words):
        score += 1
    for m in DATE_PATTERN.finditer(text):
        day, month = int(m.group(1)), int(m.group(2))
        if event.start and event.start.day == day and event.start.month == month:
            score += 2
    return score


def handle_text(db: Session, phone: str, text: str) -> str:
    """Process a text message sent to the bot. Returns the reply that was sent."""
    if not is_cancellation(text):
        reply = (
            "Hallo! Schick mir einen Event-Flyer als Bild, dann trage ich ihn in den Kalender ein. "
            f"Kalender: {settings.base_url}"
        )
        send_message(db, phone, reply)
        return reply

    user = get_or_create_user(db, phone)
    now = local_now()
    stmt = select(Event).where(
        Event.status.in_([EventStatus.LIVE, EventStatus.ADMIN_REVIEW]),
        Event.start.is_not(None),
        Event.start >= now.replace(hour=0, minute=0),
    )
    admin = is_whitelisted_admin(user)
    if not admin:
        stmt = stmt.where(Event.owner_id == user.id)
    candidates = list(db.scalars(stmt.order_by(Event.start)))

    if not candidates:
        reply = (
            "Ich habe kein kommendes Event gefunden, das du absagen kannst. "
            "Absagen sind nur für eigene Events möglich."
        )
        send_message(db, phone, reply)
        db.commit()
        return reply

    scored = sorted(((_match_score(e, text), e) for e in candidates), key=lambda p: -p[0])
    best_score = scored[0][0]
    best = [e for s, e in scored if s == best_score]
    if best_score > 0 and len(best) == 1:
        match = best[0]
    elif len(candidates) == 1 and not admin:
        match = candidates[0]
    else:
        match = None

    if match is not None:
        cancel(db, match)
        reply = f"🛑 „{match.title}“ am {match.start:%d.%m.} ist jetzt als ABGESAGT markiert. {event_url(match)}"
    else:
        db.add(
            CancellationRequest(
                sender_phone=phone,
                text=text[:2000],
                candidate_ids=[e.id for _, e in scored[:10]],
            )
        )
        db.commit()
        reply = "Ich konnte die Absage keinem Event eindeutig zuordnen. Ein Admin kümmert sich darum."
    send_message(db, phone, reply)
    return reply


def locate_event(db: Session, event_id: int) -> None:
    """Coordinates for the map on form-created events (DC-06, DC-14)."""
    event = db.get(Event, event_id)
    if event is None or event.lat is not None:
        return
    complete_address(event, {}, 1.0)
    db.commit()


def run_detached(fn, *args) -> None:
    """Run a pipeline step with its own database session (for background tasks)."""
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        fn(db, *args)
    except Exception:
        log.exception("Background task %s failed", getattr(fn, "__name__", fn))
    finally:
        db.close()
