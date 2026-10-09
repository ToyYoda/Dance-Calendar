"""Upload a flyer on the website and get a prefilled event form."""

import json
import re
from datetime import timedelta

from app import ai, geocode
from app.ai import Extraction
from app.models import Event, Media
from app.services import local_now
from tests.conftest import OTHER_PHONE, USER_PHONE, event_form, flyer_bytes, login


def _scan(client, data=None, **kw):
    return client.post("/events/new/scan", files={"flyer": ("f.jpg", data or flyer_bytes(), "image/jpeg")}, **kw)


def _hidden(html: str, name: str) -> str:
    return re.search(rf'name="{name}" value="([^"]*)"', html).group(1)


def test_scan_requires_login(client):
    r = _scan(client, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")


def test_new_event_page_offers_flyer_upload(user_client):
    html = user_client.get("/events/new").text
    assert 'action="/events/new/scan"' in html and "Mit Flyer starten" in html


def test_scan_prefills_form_and_marks_low_fields(user_client, db, monkeypatch):
    start = local_now().replace(hour=21, minute=0, second=0, microsecond=0) + timedelta(days=9)
    monkeypatch.setattr(ai, "extract", lambda key, today=None: Extraction(
        values={"title": "Kizomba Noir", "dance_styles": ["Kizomba"], "start": start,
                "location_name": "Club Havana", "city": "Berlin", "price": "12 €"},
        confidence={"title": 0.97, "dance_styles": 0.95, "start": 0.92, "location_name": 0.6,
                    "city": 0.9, "price": 0.88}))
    r = _scan(user_client)
    assert r.status_code == 200
    html = r.text
    assert 'value="Kizomba Noir"' in html and 'value="12 €"' in html
    assert f'value="{start:%Y-%m-%dT%H:%M}"' in html
    assert 'value="Kizomba" checked' in html
    assert "unsicher, 60 %" in html  # location is below the 85 % threshold
    media = db.query(Media).one()
    assert media.sender_phone == USER_PHONE and media.source == "FORM"
    assert f"/media/{media.original_path}/preview" in html
    assert _hidden(html, "media_id") == str(media.id)


def test_scan_fills_missing_address(user_client, monkeypatch):
    monkeypatch.setattr(ai, "extract", lambda key, today=None: Extraction(
        values={"title": "X", "location_name": "Club Havana", "city": "Berlin"},
        confidence={"title": 0.9, "location_name": 0.9, "city": 0.9}))
    hit = {"label": "Club Havana", "address": "Oranienstraße 12", "postcode": "10999", "city": "Berlin",
           "lat": 52.5, "lng": 13.4, "importance": 0.9}
    monkeypatch.setattr(geocode, "search", lambda *a: geocode.GeoResult("unique", hit, [hit]))
    html = _scan(user_client).text
    assert 'value="Oranienstraße 12"' in html and 'value="10999"' in html
    assert "auto ergänzt" in html


def test_submit_after_scan_attaches_flyer_without_reupload(user_client, db):
    html = _scan(user_client).text  # mock provider
    media_id = _hidden(html, "media_id")
    conf = json.loads(_hidden(html, "ai_confidence").replace("&#34;", '"').replace("&quot;", '"'))
    assert conf["title"] == 0.9
    form = event_form(media_id=media_id, ai_confidence=json.dumps(conf))
    r = user_client.post("/events/new", data=form, follow_redirects=False)
    assert r.status_code == 303
    ev = db.query(Event).one()
    assert ev.media_id == int(media_id)
    assert ev.field_confidence["title"] == 0.9
    assert "title" in ev.verified_fields


def test_validation_error_keeps_scanned_flyer(user_client, db):
    media_id = _hidden(_scan(user_client).text, "media_id")
    r = user_client.post("/events/new", data=event_form(media_id=media_id, title=""))
    assert r.status_code == 400
    assert _hidden(r.text, "media_id") == media_id and "review-flyer" in r.text


def test_cannot_claim_someone_elses_flyer(client, db):
    login(client, OTHER_PHONE)
    media_id = _hidden(_scan(client).text, "media_id")
    client.post("/logout")
    login(client, USER_PHONE)
    client.post("/events/new", data=event_form(media_id=media_id))
    assert db.query(Event).one().media_id is None


def test_flyer_cannot_be_attached_twice(user_client, db):
    media_id = _hidden(_scan(user_client).text, "media_id")
    user_client.post("/events/new", data=event_form(media_id=media_id))
    user_client.post("/events/new", data=event_form(media_id=media_id, title="Zweites Event"))
    events = db.query(Event).order_by(Event.id).all()
    assert events[0].media_id == int(media_id) and events[1].media_id is None


def test_scan_ai_failure_still_offers_form(user_client, db, monkeypatch):
    def boom(key, today=None):
        raise ai.AIError("KI-Modell nicht erreichbar: connection refused")

    from app.config import settings

    monkeypatch.setattr(settings, "ai_provider", "openai")
    monkeypatch.setattr(ai, "extract", boom)
    r = _scan(user_client)
    assert r.status_code == 200
    assert "nicht automatisch ausgelesen" in r.text and "connection refused" in r.text
    assert 'name="media_id"' in r.text


def test_scan_rejects_non_images(user_client, db):
    r = user_client.post("/events/new/scan", files={"flyer": ("x.txt", b"hello", "text/plain")})
    assert r.status_code == 400 and "kein lesbares Bild" in r.text
    assert db.query(Media).count() == 0


def test_mock_mode_is_clearly_flagged(user_client):
    assert "Testmodus" in _scan(user_client).text


def test_thinking_output_is_ignored():
    from app.ai import _parse_json

    raw = '<think>Der Flyer zeigt {Dolce Vita} …</think>\n{"title": {"value": "Dolce Vita", "confidence": 0.9}}'
    assert _parse_json(raw)["title"]["value"] == "Dolce Vita"
