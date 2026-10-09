from datetime import datetime, timedelta

import pytest

from app import ai, geocode, ingestion
from app.ai import Classification, Extraction, normalize_extraction
from app.models import CancellationRequest, Event, EventStatus, Media, MediaStatus, OutboundMessage
from app.services import local_now, set_threshold
from tests.conftest import ADMIN_PHONE, OTHER_PHONE, USER_PHONE, flyer_bytes, make_event


def _extraction(conf: float, **values) -> Extraction:
    start = local_now().replace(hour=21, minute=0) + timedelta(days=4)
    base = {"title": "Bachata Fever", "dance_styles": ["Bachata"], "start": start, "location_name": "Studio 54",
            "city": "Köln", "event_type": "Party"}
    base.update(values)
    return Extraction(values=base, confidence={k: conf for k in base})


# --- DC-12 normalisation ---------------------------------------------------------


def test_normalize_extraction_drops_unknown_and_empty_values():
    ex = normalize_extraction({
        "title": {"value": " Salsa Night ", "confidence": 0.9},
        "dance_styles": {"value": ["salsa", "Breakdance", "Bachata"], "confidence": 0.8},
        "event_type": {"value": "party", "confidence": 0.9},
        "level": {"value": "intermediate", "confidence": 0.7},
        "start": {"value": "2026-11-14T21:00", "confidence": 0.95},
        "end": {"value": "2026-11-14T20:00", "confidence": 0.5},
        "doors": {"value": "20.30 Uhr", "confidence": 0.6},
        "city": {"value": None, "confidence": 0.0},
        "ticket_url": {"value": "tickets.example.com", "confidence": 0.8},
        "price": {"value": "null", "confidence": 0.2},
    })
    assert ex.values["title"] == "Salsa Night"
    assert ex.values["dance_styles"] == ["Salsa", "Bachata"]
    assert ex.values["event_type"] == "Party"
    assert ex.values["level"] == "Mittelstufe"
    assert ex.values["start"] == datetime(2026, 11, 14, 21, 0)
    assert "end" not in ex.values  # end before start is dropped, not guessed
    assert ex.values["doors"] == "20:30"
    assert ex.values["ticket_url"] == "https://tickets.example.com"
    assert "city" not in ex.values and "price" not in ex.values
    assert ex.overall == 0.0  # city missing → not sure at all


def test_overall_confidence_is_weakest_core_field():
    ex = _extraction(0.99)
    ex.confidence["city"] = 0.7
    assert ex.overall == 0.7


# --- DC-10, DC-11, DC-13 pipeline -------------------------------------------------


def _receive(db, phone=USER_PHONE, seed=1):
    media = ingestion.receive_image(db, flyer_bytes(seed), phone)
    return media, ingestion.process_media(db, media.id)


def test_low_confidence_goes_to_review(db, monkeypatch):
    monkeypatch.setattr(ai, "extract", lambda key, today=None: _extraction(0.9))
    media, ev = _receive(db)
    assert ev.status == EventStatus.ADMIN_REVIEW
    assert ev.source == "BOT" and ev.submitter_phone == USER_PHONE and ev.owner.phone == USER_PHONE
    assert media.status == MediaStatus.FLYER
    bodies = [m.body for m in db.query(OutboundMessage).filter_by(to_phone=USER_PHONE)]
    assert any("angekommen" in b for b in bodies)
    assert any("prüft" in b for b in bodies)
    assert any("/disown" in b for b in bodies)  # DC-21 notification


def test_high_confidence_is_auto_published(db, monkeypatch):
    monkeypatch.setattr(ai, "extract", lambda key, today=None: _extraction(0.99))
    _, ev = _receive(db)
    assert ev.status == EventStatus.LIVE and ev.auto_published
    assert any("jetzt im Kalender" in m.body for m in db.query(OutboundMessage))


def test_threshold_is_configurable(db, monkeypatch):
    monkeypatch.setattr(ai, "extract", lambda key, today=None: _extraction(0.9))
    set_threshold(db, "auto_publish_threshold", 0.8)
    _, ev = _receive(db)
    assert ev.status == EventStatus.LIVE


def test_non_flyer_goes_to_spam(db, monkeypatch):
    monkeypatch.setattr(ai, "classify", lambda key: Classification(False, 0.97, "Meme"))
    media, ev = _receive(db)
    assert ev is None
    db.refresh(media)
    assert media.status == MediaStatus.SPAM and media.classifier_reason == "Meme"
    assert db.query(Event).count() == 0


def test_prefilter_rejects_tiny_and_single_color_images(db):
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (100, 100), (255, 0, 0)).save(buf, "PNG")
    media = ingestion.receive_image(db, buf.getvalue(), USER_PHONE)
    assert ingestion.process_media(db, media.id) is None
    db.refresh(media)
    assert media.status == MediaStatus.SPAM and media.classifier_ms is not None


def test_ai_failure_marks_media_failed(db, monkeypatch):
    def boom(key, today=None):
        raise ai.AIError("offline")

    monkeypatch.setattr(ai, "extract", boom)
    media, ev = _receive(db)
    assert ev is None
    db.refresh(media)
    assert media.status == MediaStatus.FAILED and "offline" in media.error


def test_mock_provider_end_to_end(db):
    _, ev = _receive(db)
    assert ev.title.startswith("Salsa Night") and ev.status == EventStatus.ADMIN_REVIEW
    assert ev.field_confidence["location_name"] == 0.7


# --- DC-14 address completion -------------------------------------------------------


def test_geocode_rank_unique_and_ambiguous():
    a = {"label": "A", "address": "X 1", "postcode": "1", "city": "Köln", "lat": 1.0, "lng": 1.0, "importance": 0.95}
    b = {"label": "B", "address": "Y 2", "postcode": "2", "city": "Köln", "lat": 2.0, "lng": 2.0, "importance": 0.05}
    c = {"label": "C", "address": "Z 3", "postcode": "3", "city": "Bonn", "lat": 3.0, "lng": 3.0, "importance": 0.9}
    assert geocode.rank([a], "Köln").status == "unique"
    assert geocode.rank([a, b], "Köln").best["label"] == "A"
    assert geocode.rank([dict(a, importance=0.5), dict(b, importance=0.4)], "Köln").status == "ambiguous"
    assert geocode.rank([a, c], "Köln").best["label"] == "A"  # other cities are ignored
    assert geocode.rank([], "Köln").status == "none"


def test_address_completion_unique_and_ambiguous(db, monkeypatch):
    hit = {"label": "Studio 54, Köln", "address": "Hauptstr. 1", "postcode": "50667", "city": "Köln",
           "lat": 50.9, "lng": 6.9, "importance": 0.8}
    monkeypatch.setattr(ai, "extract", lambda key, today=None: _extraction(0.99))
    monkeypatch.setattr(geocode, "search", lambda *a: geocode.GeoResult("unique", hit, [hit]))
    _, ev = _receive(db)
    assert ev.address == "Hauptstr. 1" and ev.postcode == "50667" and ev.lat == 50.9
    assert set(ev.auto_filled) == {"address", "postcode"}
    assert ev.status == EventStatus.LIVE

    monkeypatch.setattr(geocode, "search", lambda *a: geocode.GeoResult("ambiguous", None, [hit, hit]))
    _, ev2 = _receive(db, seed=2)
    assert ev2.status == EventStatus.ADMIN_REVIEW  # ambiguity lowers the score
    assert ev2.confidence == pytest.approx(0.99 * ingestion.GEO_AMBIGUOUS_PENALTY)
    assert len(ev2.address_candidates) == 2


# --- DC-15 cancellations ------------------------------------------------------------


def test_is_cancellation():
    assert ingestion.is_cancellation("Die Party fällt leider aus!")
    assert ingestion.is_cancellation("Workshop ABGESAGT")
    assert ingestion.is_cancellation("Event storniert")
    assert not ingestion.is_cancellation("Wer kommt heute?")


def test_owner_cancels_single_event(db):
    ev = make_event(db, owner_phone=USER_PHONE)
    reply = ingestion.handle_text(db, USER_PHONE, "Heute fällt aus, sorry")
    db.refresh(ev)
    assert ev.status == EventStatus.CANCELLED and "ABGESAGT" in reply


def test_cancellation_matches_by_date_or_title(db):
    a = make_event(db, title="Salsa Sunday", owner_phone=USER_PHONE)
    b = make_event(db, title="Bachata Monday", owner_phone=USER_PHONE, start=a.start + timedelta(days=1))
    ingestion.handle_text(db, USER_PHONE, f"Am {b.start:%d.%m.} ist abgesagt")
    db.refresh(a), db.refresh(b)
    assert b.status == EventStatus.CANCELLED and a.status == EventStatus.LIVE
    ingestion.handle_text(db, USER_PHONE, "Salsa Sunday fällt aus")
    db.refresh(a)
    assert a.status == EventStatus.CANCELLED


def test_unclear_cancellation_goes_to_admin(db):
    a = make_event(db, title="Salsa Sunday", owner_phone=USER_PHONE)
    make_event(db, title="Bachata Monday", owner_phone=USER_PHONE, start=a.start + timedelta(days=1))
    reply = ingestion.handle_text(db, USER_PHONE, "Leider abgesagt")
    assert "Admin" in reply
    req = db.query(CancellationRequest).one()
    assert len(req.candidate_ids) == 2
    assert db.query(Event).filter_by(status=EventStatus.CANCELLED).count() == 0


def test_only_owner_or_admin_can_cancel(db):
    ev = make_event(db, title="Kizomba Night", owner_phone=OTHER_PHONE)
    reply = ingestion.handle_text(db, USER_PHONE, "Kizomba Night fällt aus")
    db.refresh(ev)
    assert ev.status == EventStatus.LIVE and "nur für eigene Events" in reply
    ingestion.handle_text(db, ADMIN_PHONE, "Kizomba Night fällt aus")
    db.refresh(ev)
    assert ev.status == EventStatus.CANCELLED


def test_other_text_gets_help_reply(db):
    assert "Flyer" in ingestion.handle_text(db, USER_PHONE, "Hallo?")


def test_media_pipeline_persists_media_row(db):
    media, _ = _receive(db)
    assert db.get(Media, media.id).sender_phone == USER_PHONE
