import hashlib
import hmac
import json
from datetime import timedelta

from app.config import settings
from app.models import (
    CancellationRequest,
    Event,
    EventStatus,
    FieldCorrection,
    Media,
    MediaStatus,
    OutboundMessage,
    ReviewDecision,
    User,
)
from app.security import LOCK_TTL
from app.models import utcnow
from tests.conftest import ADMIN_PHONE, USER_PHONE, admin_login, event_form, flyer_bytes, make_event


def _bot_event(db, **kw):
    defaults = dict(status=EventStatus.ADMIN_REVIEW, source="BOT", submitter_phone=USER_PHONE, confidence=0.7,
                    field_confidence={"title": 0.95, "city": 0.6, "start": 0.9})
    defaults.update(kw)
    return make_event(db, **defaults)


def test_admin_pages_require_admin_session(client):
    for path in ("/admin", "/admin/swipe", "/admin/gallery", "/admin/spam", "/admin/stats", "/admin/bot",
                 "/admin/cancellations"):
        assert client.get(path, follow_redirects=False).status_code == 303
    assert client.get("/admin/api/queue").status_code == 401


def test_triage_list_shows_low_confidence_fields(admin_client, db):
    _bot_event(db, title="Unsure Event")
    make_event(db, title="Already Live")
    html = admin_client.get("/admin").text
    assert "Unsure Event" in html and "Already Live" not in html
    assert "Unsicher: Stadt" in html
    assert "Unsure Event" in admin_client.get("/admin?sort=score&source=BOT").text
    assert "Unsure Event" not in admin_client.get("/admin?source=FORM").text


def test_review_marks_low_fields_and_records_corrections(admin_client, db):
    ev = _bot_event(db)
    html = admin_client.get(f"/admin/events/{ev.id}").text
    assert "is-low" in html and "⚠ 60 %" in html
    form = event_form(title="Salsa Night", city="Köln", action="approve")
    form["start"] = ev.start.strftime("%Y-%m-%dT%H:%M")
    r = admin_client.post(f"/admin/events/{ev.id}", data=form, follow_redirects=False)
    assert r.status_code == 303
    db.refresh(ev)
    assert ev.status == EventStatus.LIVE and ev.city == "Köln"
    assert "city" in ev.verified_fields
    corr = {c.field: c for c in db.query(FieldCorrection)}
    assert corr["city"].old_value == "Berlin" and corr["city"].new_value == "Köln" and corr["city"].confidence == 0.6
    assert "title" not in corr
    assert any("jetzt im Kalender" in m.body for m in db.query(OutboundMessage).filter_by(to_phone=USER_PHONE))
    assert ev.locked_by_id is None


def test_review_reject_and_delete(admin_client, db):
    a, b = _bot_event(db, title="A"), _bot_event(db, title="B")
    admin_client.post(f"/admin/events/{a.id}", data={"action": "reject"})
    db.refresh(a)
    assert a.status == EventStatus.REJECTED
    assert any("nicht veröffentlicht" in m.body for m in db.query(OutboundMessage))
    bid = b.id
    admin_client.post(f"/admin/events/{bid}", data={"action": "delete"})
    db.expire_all()
    assert db.query(Event).filter_by(id=bid).count() == 0


def test_review_address_candidate_selection(admin_client, db):
    cands = [{"label": "One", "address": "Weg 1", "postcode": "11111", "city": "Berlin", "lat": 1.0, "lng": 2.0},
             {"label": "Two", "address": "Weg 2", "postcode": "22222", "city": "Berlin", "lat": 3.0, "lng": 4.0}]
    ev = _bot_event(db, address_candidates=cands, address=None)
    form = event_form(action="save", address="", postcode="", city="Berlin", candidate="1")
    form["start"] = ev.start.strftime("%Y-%m-%dT%H:%M")
    admin_client.post(f"/admin/events/{ev.id}", data=form)
    db.refresh(ev)
    assert (ev.address, ev.postcode, ev.lat) == ("Weg 2", "22222", 3.0)
    assert ev.address_candidates == []


def test_lock_blocks_second_admin(client, db):
    other = User(phone="491709999999", name="Kim")
    db.add(other)
    db.commit()
    ev = _bot_event(db, locked_by_id=other.id, locked_until=utcnow() + LOCK_TTL)
    settings.admin_phones = {ADMIN_PHONE, other.phone}
    admin_login(client)
    html = client.get(f"/admin/events/{ev.id}").text
    assert "Kim bearbeitet dieses Event gerade" in html
    r = client.post(f"/admin/events/{ev.id}", data={"action": "reject"})
    assert r.status_code == 409
    assert client.post(f"/admin/api/events/{ev.id}/decision", json={"action": "approve"}).status_code == 409
    # expired locks are free again (first come, first served)
    ev.locked_until = utcnow() - timedelta(seconds=1)
    db.commit()
    assert "bearbeitet dieses Event gerade" not in client.get(f"/admin/events/{ev.id}").text
    db.refresh(ev)
    assert ev.locked_by.phone == ADMIN_PHONE


def test_organizer_sees_hint_for_event_in_review(client, db):
    from tests.conftest import login

    ev = _bot_event(db, owner_phone=USER_PHONE)
    login(client, USER_PHONE)
    assert "gerade von einem Admin geprüft" in client.get(f"/events/{ev.id}/edit").text


def test_swipe_decision_and_undo(admin_client, db):
    ev = _bot_event(db)
    incomplete = _bot_event(db, title="", location_name=None)
    items = admin_client.get("/admin/api/queue").json()["items"]
    assert {i["id"] for i in items} == {ev.id, incomplete.id}
    r = admin_client.post(f"/admin/api/events/{incomplete.id}/decision", json={"action": "approve"})
    assert r.status_code == 422 and "Titel" in r.json()["error"]
    r = admin_client.post(f"/admin/api/events/{ev.id}/decision", json={"action": "approve"})
    decision_id = r.json()["decision_id"]
    db.refresh(ev)
    assert ev.status == EventStatus.LIVE
    assert admin_client.post(f"/admin/api/events/{ev.id}/decision", json={"action": "reject"}).status_code == 409
    assert admin_client.post("/admin/api/undo", json={"decision_id": decision_id}).json()["ok"]
    db.refresh(ev)
    assert ev.status == EventStatus.ADMIN_REVIEW
    assert admin_client.post("/admin/api/undo", json={"decision_id": decision_id}).status_code == 400


def test_batch_actions_report_failures(admin_client, db):
    ok = _bot_event(db, title="Complete")
    incomplete = _bot_event(db, title="Incomplete", address=None)
    r = admin_client.post("/admin/batch", data={"action": "approve", "ids": [str(ok.id), str(incomplete.id)]})
    assert "<strong>1</strong> Event erfolgreich" in r.text
    assert "Incomplete" in r.text and "Straße" in r.text
    db.refresh(ok), db.refresh(incomplete)
    assert ok.status == EventStatus.LIVE and incomplete.status == EventStatus.ADMIN_REVIEW


def test_batch_set_location_and_not_event(admin_client, db):
    media = Media(original_path="0" * 32, source="BOT", status=MediaStatus.FLYER, classifier_label="flyer")
    db.add(media)
    db.commit()
    a = _bot_event(db, title="A")
    b = _bot_event(db, title="B", media_id=media.id)
    admin_client.post("/admin/batch", data={"action": "set_location", "ids": [str(a.id)],
                                            "location_name": "Neue Halle", "city": "Leipzig"})
    db.refresh(a)
    assert (a.location_name, a.city) == ("Neue Halle", "Leipzig") and "city" in a.verified_fields
    bid = b.id
    admin_client.post("/admin/batch", data={"action": "not_event", "ids": [str(bid)]})
    db.expire_all()
    assert db.query(Event).filter_by(id=bid).count() == 0
    db.refresh(media)
    assert media.status == MediaStatus.SPAM
    assert db.query(ReviewDecision).filter_by(action="not_event").count() == 1


def test_bot_simulator_and_spam_restore(admin_client, db, monkeypatch):
    from app import ai

    monkeypatch.setattr(ai, "classify", lambda key: ai.Classification(False, 0.9, "Foto"))
    r = admin_client.post("/admin/bot/image", data={"phone": "+" + USER_PHONE},
                          files={"image": ("x.jpg", flyer_bytes(), "image/jpeg")})
    assert r.status_code == 200
    media = db.query(Media).one()
    assert media.status == MediaStatus.SPAM
    assert "Foto" in admin_client.get("/admin/spam").text
    admin_client.post(f"/admin/media/{media.id}/restore")
    db.refresh(media)
    assert media.status == MediaStatus.FLYER
    ev = db.query(Event).one()
    assert ev.status == EventStatus.ADMIN_REVIEW and ev.media_id == media.id
    stats = admin_client.get("/admin/stats").text
    assert "1 falsch-negativ" in stats


def test_cancellation_request_resolution(admin_client, db):
    ev = make_event(db, title="Unklar", owner_phone=USER_PHONE)
    req = CancellationRequest(sender_phone=USER_PHONE, text="abgesagt", candidate_ids=[ev.id])
    db.add(req)
    db.commit()
    assert "Unklar" in admin_client.get("/admin/cancellations").text
    admin_client.post(f"/admin/cancellations/{req.id}", data={"event_id": str(ev.id)})
    db.refresh(ev), db.refresh(req)
    assert ev.status == EventStatus.CANCELLED and req.resolved


def test_stats_and_threshold_settings(admin_client, db):
    ev = _bot_event(db, auto_published=True, status=EventStatus.LIVE)
    db.add(FieldCorrection(event_id=ev.id, field="start", old_value="a", new_value="b"))
    db.commit()
    html = admin_client.get("/admin/stats").text
    assert "Automation Rate" in html and "100.0 %" in html
    admin_client.post("/admin/settings", data={"auto_publish_threshold": "0.9", "field_threshold": "0.7"})
    html = admin_client.get("/admin/stats").text
    assert 'value="0.9"' in html and 'value="0.7"' in html


def test_admin_can_edit_any_event(admin_client, db):
    ev = make_event(db, owner_phone=USER_PHONE)
    assert admin_client.get(f"/events/{ev.id}/edit").status_code == 200


# --- WhatsApp webhook ---------------------------------------------------------------


def test_webhook_verification(client, monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_verify_token", "secret")
    ok = client.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "secret",
                                                  "hub.challenge": "42"})
    assert ok.text == "42"
    assert client.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "x"}).status_code == 403


def test_webhook_signature_and_text_message(client, db, monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_app_secret", "s3cret")
    payload = {"entry": [{"changes": [{"value": {"messages": [
        {"from": USER_PHONE, "type": "text", "text": {"body": "Hallo"}}]}}]}]}
    body = json.dumps(payload).encode()
    assert client.post("/webhooks/whatsapp", content=body, headers={"x-hub-signature-256": "sha256=bad"}).status_code == 403
    sig = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    r = client.post("/webhooks/whatsapp", content=body,
                    headers={"x-hub-signature-256": sig, "content-type": "application/json"})
    assert r.status_code == 200
    assert db.query(OutboundMessage).filter_by(to_phone=USER_PHONE).count() == 1


def test_webhook_image_message(client, db, monkeypatch):
    from app.routers import webhooks

    monkeypatch.setattr(webhooks, "download_media", lambda media_id: flyer_bytes())
    payload = {"entry": [{"changes": [{"value": {"messages": [
        {"from": USER_PHONE, "type": "image", "image": {"id": "abc"}}]}}]}]}
    assert client.post("/webhooks/whatsapp", json=payload).status_code == 200
    ev = db.query(Event).one()
    assert ev.source == "BOT" and ev.submitter_phone == USER_PHONE
