from icalendar import Calendar

from app.models import CalendarFeed, EventStatus
from tests.conftest import USER_PHONE, make_event


def _events(body: bytes):
    return [c for c in Calendar.from_ical(body).walk("VEVENT")]


def test_single_event_ics(client, db):
    ev = make_event(db, title="Kizomba Fusion", price="12 €", lat=52.5, lng=13.4)
    r = client.get(f"/events/{ev.id}.ics")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/calendar")
    (item,) = _events(r.content)
    assert str(item["summary"]) == "Kizomba Fusion"
    assert "Club Havana" in str(item["location"])
    assert f"/events/{ev.id}" in str(item["description"])
    assert item["dtstart"].dt.tzinfo is not None


def test_changes_and_cancellation_update_sequence(user_client, db):
    ev = make_event(db, owner_phone=USER_PHONE)
    seq0 = int(_events(user_client.get(f"/events/{ev.id}.ics").content)[0]["sequence"])
    user_client.post(f"/events/{ev.id}/cancel")
    (item,) = _events(user_client.get(f"/events/{ev.id}.ics").content)
    assert str(item["status"]) == "CANCELLED"
    assert int(item["sequence"]) > seq0
    assert str(item["summary"]).startswith("ABGESAGT")


def test_filter_feed_url(client, db):
    make_event(db, title="Salsa A", dance_styles=["Salsa"])
    make_event(db, title="Bachata B", dance_styles=["Bachata"])
    titles = [str(e["summary"]) for e in _events(client.get("/feed.ics?style=Bachata").content)]
    assert titles == ["Bachata B"]


def test_attending_feed(user_client, db):
    ev = make_event(db, title="Going")
    make_event(db, title="Not going")
    user_client.post(f"/api/events/{ev.id}/attendance", json={"attend": True})
    user_client.post("/me/feeds", data={"kind": "attending"})
    feed = db.query(CalendarFeed).one()
    body = user_client.get(f"/feeds/{feed.token}.ics").content
    assert [str(e["summary"]) for e in _events(body)] == ["Going"]
    # cancelled events stay in the feed, marked as cancelled
    ev.status = EventStatus.CANCELLED
    db.commit()
    (item,) = _events(user_client.get(f"/feeds/{feed.token}.ics").content)
    assert str(item["status"]) == "CANCELLED"
    assert user_client.get("/feeds/doesnotexist.ics").status_code == 404
