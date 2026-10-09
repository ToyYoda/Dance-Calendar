import io
import random
import re
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app import db as dbmod
from app.config import settings
from app.models import Event, EventSource, EventStatus, User
from app.services import local_now

ADMIN_PHONE = "491701111111"
USER_PHONE = "491702222222"
OTHER_PHONE = "491703333333"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(settings, "media_dir", tmp_path / "media")
    monkeypatch.setattr(settings, "admin_phones", {ADMIN_PHONE})
    monkeypatch.setattr(settings, "ai_provider", "mock")
    monkeypatch.setattr(settings, "geocoder", "off")
    monkeypatch.setattr(settings, "messenger", "console")
    monkeypatch.setattr(settings, "dev_mode", True)
    monkeypatch.setattr(settings, "ingest_sync", True)
    monkeypatch.setattr(settings, "whatsapp_app_secret", "")
    dbmod.configure(settings.database_url)
    dbmod.init_db()
    yield
    dbmod.engine.dispose()


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def db():
    session = dbmod.SessionLocal()
    yield session
    session.close()


def login(client: TestClient, phone: str) -> None:
    r = client.post("/login", data={"phone": "+" + phone, "next": "/"})
    assert r.status_code == 200, r.text
    code = re.search(r'class="mono">(\d{6})<', r.text).group(1)
    r = client.post("/login/verify", data={"phone": phone, "code": code, "next": "/"}, follow_redirects=False)
    assert r.status_code == 303


def admin_login(client: TestClient, phone: str = ADMIN_PHONE) -> None:
    r = client.post("/admin/login", data={"phone": "+" + phone})
    link = re.search(r'href="https?://[^/"]+(/admin/login/[^"]+)"', r.text).group(1)
    r = client.post(link, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin"


@pytest.fixture
def user_client(client):
    login(client, USER_PHONE)
    return client


@pytest.fixture
def admin_client(client):
    admin_login(client)
    return client


def flyer_bytes(seed: int = 1, size=(600, 850)) -> bytes:
    rnd = random.Random(seed)
    img = Image.new("RGB", size, (rnd.randint(0, 255), rnd.randint(0, 255), rnd.randint(0, 255)))
    draw = ImageDraw.Draw(img)
    for _ in range(40):
        x, y = rnd.randint(0, size[0]), rnd.randint(0, size[1])
        draw.rectangle([x, y, x + rnd.randint(10, 200), y + rnd.randint(10, 120)],
                       fill=(rnd.randint(0, 255), rnd.randint(0, 255), rnd.randint(0, 255)))
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


def make_event(db, **kw) -> Event:
    start = kw.pop("start", local_now().replace(hour=21, minute=0) + timedelta(days=3))
    owner_phone = kw.pop("owner_phone", None)
    owner = None
    if owner_phone:
        owner = db.query(User).filter_by(phone=owner_phone).one_or_none() or User(phone=owner_phone)
        db.add(owner)
        db.flush()
    defaults = dict(
        title="Salsa Night",
        dance_styles=["Salsa"],
        event_type="Party",
        level="Open",
        start=start,
        location_name="Club Havana",
        address="Hauptstraße 1",
        city="Berlin",
        status=EventStatus.LIVE,
        source=EventSource.FORM,
        owner_id=owner.id if owner else None,
    )
    defaults.update(kw)
    ev = Event(**defaults)
    db.add(ev)
    db.commit()
    return ev


def event_form(**overrides) -> dict:
    start = (datetime.now() + timedelta(days=5)).replace(hour=20, minute=0, second=0, microsecond=0)
    data = {
        "title": "Bachata Social",
        "dance_styles": ["Bachata"],
        "event_type": "Social",
        "level": "Open",
        "start": start.strftime("%Y-%m-%dT%H:%M"),
        "end": (start + timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M"),
        "doors": "19:30",
        "location_name": "Tanzhaus",
        "address": "Ringstraße 5",
        "postcode": "10115",
        "city": "Berlin",
        "price": "8 €",
        "ticket_url": "",
        "description": "Gemütlicher Social.",
        "action": "submit",
    }
    data.update(overrides)
    return data
