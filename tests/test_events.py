from datetime import timedelta

from app.models import Attendance, Event, EventStatus, FilterProfile, Role, User
from app.services import local_now
from tests.conftest import OTHER_PHONE, USER_PHONE, event_form, flyer_bytes, login, make_event


# --- DC-01 / DC-02 calendar & filters ------------------------------------------


def test_only_live_and_future_events_are_listed(client, db):
    make_event(db, title="Live Party")
    make_event(db, title="Draft Party", status=EventStatus.DRAFT)
    make_event(db, title="Review Party", status=EventStatus.ADMIN_REVIEW)
    make_event(db, title="Old Party", start=local_now() - timedelta(days=2))
    make_event(db, title="Cancelled Party", status=EventStatus.CANCELLED)
    html = client.get("/?view=list").text
    assert "Live Party" in html
    assert "Cancelled Party" in html and "ABGESAGT" in html
    for hidden in ("Draft Party", "Review Party", "Old Party"):
        assert hidden not in html


def test_filters_style_type_level_city_and_search(client, db):
    make_event(db, title="Salsa Berlin", dance_styles=["Salsa"], city="Berlin", event_type="Party")
    make_event(db, title="Kizomba Hamburg", dance_styles=["Kizomba"], city="Hamburg", event_type="Workshop",
               level="Mittelstufe", description="Mit Live-DJ")
    html = client.get("/?view=list&style=Kizomba").text
    assert "Kizomba Hamburg" in html and "Salsa Berlin" not in html
    html = client.get("/?view=list&city=berlin").text
    assert "Salsa Berlin" in html and "Kizomba Hamburg" not in html
    html = client.get("/?view=list&type=Workshop&level=Mittelstufe").text
    assert "Kizomba Hamburg" in html and "Salsa Berlin" not in html
    html = client.get("/?view=list&q=live-dj").text
    assert "Kizomba Hamburg" in html and "Salsa Berlin" not in html


def test_date_range_filter(client, db):
    soon = local_now().replace(hour=20, minute=0) + timedelta(days=2)
    make_event(db, title="Soon", start=soon)
    make_event(db, title="Later", start=soon + timedelta(days=30))
    html = client.get(f"/?view=list&to={(soon + timedelta(days=1)).date().isoformat()}").text
    assert "Soon" in html and "Later" not in html


def test_month_week_views_and_day_list(client, db):
    ev = make_event(db, title="Zouk Night", dance_styles=["Zouk"])
    day = ev.start.date().isoformat()
    html = client.get(f"/?view=month&date={day}&day={day}").text
    assert "has-events" in html and "Zouk Night" in html
    html = client.get(f"/?view=week&date={day}").text
    assert "Zouk Night" in html


def test_map_view_contains_markers(client, db):
    make_event(db, title="Mapped", lat=52.5, lng=13.4)
    make_event(db, title="Unmapped")
    html = client.get("/?view=map").text
    assert '"title": "Mapped"' in html and '"title": "Unmapped"' not in html


def test_detail_page_and_private_drafts(client, db):
    live = make_event(db, title="Visible", price="10 €", doors="20:00", description="Hallo")
    draft = make_event(db, title="Secret", status=EventStatus.DRAFT)
    html = client.get(f"/events/{live.id}").text
    assert "10 €" in html and "20:00" in html and "Route planen" in html
    assert client.get(f"/events/{draft.id}").status_code == 404


# --- DC-20 organizer form, DC-43 ownership ----------------------------------------


def test_create_event_requires_login(client):
    assert client.post("/events/new", data=event_form(), follow_redirects=False).headers["location"].startswith("/login")


def test_organizer_creates_draft_then_publishes(user_client, db):
    r = user_client.post("/events/new", data=event_form(action="draft", start="", location_name=""),
                         follow_redirects=False)
    assert r.status_code == 303
    ev = db.query(Event).one()
    assert ev.status == EventStatus.DRAFT and ev.source == "FORM"
    assert db.query(User).filter_by(phone=USER_PHONE).one().role == Role.ORGANIZER

    # submitting requires the mandatory fields
    r = user_client.post(f"/events/{ev.id}/edit", data=event_form(sequence=str(ev.sequence), location_name=""))
    assert r.status_code == 400 and "Pflichtfeld" in r.text
    r = user_client.post(f"/events/{ev.id}/edit", data=event_form(sequence=str(ev.sequence)), follow_redirects=False)
    assert r.status_code == 303
    db.refresh(ev)
    assert ev.status == EventStatus.LIVE and ev.location_name == "Tanzhaus"


def test_form_validation_errors(user_client, db):
    r = user_client.post("/events/new", data=event_form(title="", dance_styles=[], ticket_url="ftp://x"))
    assert r.status_code == 400
    assert r.text.count("Pflichtfeld") >= 2
    assert "https://" in r.text
    assert db.query(Event).count() == 0


def test_flyer_upload_creates_webp_variants(user_client, db, tmp_path):
    r = user_client.post("/events/new", data=event_form(), files={"flyer": ("f.jpg", flyer_bytes(), "image/jpeg")},
                         follow_redirects=False)
    assert r.status_code == 303
    ev = db.query(Event).one()
    key = ev.media.original_path
    for variant in ("original", "thumb", "preview"):
        resp = user_client.get(f"/media/{key}/{variant}")
        assert resp.status_code == 200 and resp.headers["content-type"] == "image/webp"
    from PIL import Image
    import io
    assert Image.open(io.BytesIO(user_client.get(f"/media/{key}/thumb").content)).width == 200
    assert Image.open(io.BytesIO(user_client.get(f"/media/{key}/preview").content)).width == 600  # never upscaled


def test_only_owner_can_edit(client, db):
    ev = make_event(db, owner_phone=OTHER_PHONE)
    login(client, USER_PHONE)
    assert client.get(f"/events/{ev.id}/edit").status_code == 403
    assert client.post(f"/events/{ev.id}/delete").status_code == 403
    assert db.get(Event, ev.id) is not None


def test_concurrent_edit_is_not_silently_overwritten(client, db):
    ev = make_event(db, owner_phone=USER_PHONE)
    login(client, USER_PHONE)
    stale = str(ev.sequence)
    assert client.get(f"/events/{ev.id}/edit").status_code == 200
    ev.title = "Changed by admin"
    ev.sequence += 1
    db.commit()
    r = client.post(f"/events/{ev.id}/edit", data=event_form(sequence=stale, action="save"))
    assert r.status_code == 409 and "inzwischen geändert" in r.text
    db.refresh(ev)
    assert ev.title == "Changed by admin"


def test_owner_cancels_event(user_client, db):
    ev = make_event(db, owner_phone=USER_PHONE)
    user_client.post(f"/events/{ev.id}/cancel")
    db.refresh(ev)
    assert ev.status == EventStatus.CANCELLED
    assert "ABGESAGT" in user_client.get(f"/events/{ev.id}").text


def test_disown_hands_event_to_admins(user_client, db):
    ev = make_event(db, owner_phone=USER_PHONE, source="BOT")
    assert "Nicht mein Event" in user_client.get(f"/events/{ev.id}").text
    user_client.post(f"/events/{ev.id}/disown")
    db.refresh(ev)
    assert ev.owner_id is None
    assert "Salsa Night" not in user_client.get("/me").text.split("Meine Events")[1].split("Gespeicherte Filter")[0]


# --- DC-05 attendance ------------------------------------------------------------


def test_attendance_toggle_and_count(client, db):
    ev = make_event(db)
    assert client.post(f"/api/events/{ev.id}/attendance", json={"attend": True}).status_code == 401
    login(client, USER_PHONE)
    r = client.post(f"/api/events/{ev.id}/attendance", json={"attend": True})
    assert r.json() == {"count": 1, "attending": True}
    client.post(f"/api/events/{ev.id}/attendance", json={"attend": True})
    assert db.query(Attendance).count() == 1  # at most one per person
    assert client.get(f"/api/events/{ev.id}/attendance").json()["count"] == 1
    assert "Salsa Night" in client.get("/me").text
    r = client.post(f"/api/events/{ev.id}/attendance", json={"attend": False})
    assert r.json() == {"count": 0, "attending": False}


def test_no_attendance_for_cancelled_events(user_client, db):
    ev = make_event(db, status=EventStatus.CANCELLED)
    r = user_client.post(f"/api/events/{ev.id}/attendance", json={"attend": True})
    assert r.json()["attending"] is False


# --- DC-04 filter profiles --------------------------------------------------------


def test_default_filter_profile_applies_on_open(user_client, db):
    make_event(db, title="Kizz Party", dance_styles=["Kizomba"])
    make_event(db, title="Sabor Party", dance_styles=["Salsa"])
    user_client.post("/me/profiles", data={"name": "Kizomba", "query": "style=Kizomba&view=list&foo=1", "default": "1"})
    prof = db.query(FilterProfile).one()
    assert prof.query == "style=Kizomba" and prof.is_default
    r = user_client.get("/", follow_redirects=False)
    assert r.status_code == 303 and "style=Kizomba" in r.headers["location"]
    html = user_client.get(r.headers["location"] + "&view=list").text
    assert "Kizz Party" in html and "Sabor Party" not in html
    # "Alle" bypasses the default
    assert user_client.get("/?all=1", follow_redirects=False).status_code == 200
    user_client.post(f"/me/profiles/{prof.id}/rename", data={"name": "Kiz"})
    user_client.post(f"/me/profiles/{prof.id}/delete")
    assert db.query(FilterProfile).count() == 0
