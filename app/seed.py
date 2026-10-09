"""Demo data: python -m app.seed"""

import io
import random
from datetime import timedelta

from PIL import Image, ImageDraw, ImageFont

from app import images
from app.db import SessionLocal, init_db
from app.models import Event, EventSource, EventStatus, Media, MediaStatus, User
from app.services import local_now

VENUES = [
    ("Club Havana", "Oranienstraße 12", "10999", "Berlin", 52.5016, 13.4185),
    ("Tanzhaus West", "Gutleutstraße 294", "60327", "Frankfurt am Main", 50.0985, 8.6455),
    ("Salón Latino", "Schanzenstraße 75", "20357", "Hamburg", 53.5625, 9.9636),
    ("Studio Ritmo", "Venloer Straße 40", "50672", "Köln", 50.9440, 6.9370),
    ("Kulturfabrik", "Lindwurmstraße 88", "80337", "München", 48.1270, 11.5560),
]
EVENTS = [
    ("Salsa Night Deluxe", ["Salsa"], "Party", "Open", "10 €"),
    ("Bachata Sensual Workshop", ["Bachata"], "Workshop", "Mittelstufe", "25 €"),
    ("Kizomba & Urban Kiz Social", ["Kizomba"], "Social", "Open", "8 €"),
    ("Brazilian Zouk Festival", ["Zouk"], "Festival", "Fortgeschritten", "ab 89 €"),
    ("Latin Mix Friday", ["Salsa", "Bachata"], "Party", "Open", "12 € / AK 15 €"),
    ("Salsa Cubana für Anfänger", ["Salsa"], "Workshop", "Anfänger", "15 €"),
    ("Bachata Moonlight Social", ["Bachata"], "Social", "Open", "frei"),
    ("Kizomba Marathon", ["Kizomba"], "Festival", "Open", "45 €"),
]
COLORS = [(214, 36, 110), (124, 58, 237), (13, 148, 136), (217, 119, 6), (30, 64, 175)]


def make_flyer(title: str, when: str, where: str, color) -> bytes:
    img = Image.new("RGB", (800, 1100), color)
    draw = ImageDraw.Draw(img)
    rnd = random.Random(title)
    for _ in range(30):
        x, y, r = rnd.randint(0, 800), rnd.randint(0, 1100), rnd.randint(20, 160)
        shade = tuple(min(255, c + rnd.randint(20, 80)) for c in color)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=shade)
    try:
        big, small = ImageFont.load_default(size=64), ImageFont.load_default(size=36)
    except TypeError:
        big = small = ImageFont.load_default()
    y = 380
    for word in title.split():
        draw.text((60, y), word.upper(), fill="white", font=big)
        y += 80
    draw.text((60, y + 40), when, fill="white", font=small)
    draw.text((60, y + 90), where, fill="white", font=small)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def main() -> None:
    init_db()
    db = SessionLocal()
    if db.query(Event).count():
        print("Datenbank enthält schon Events – nichts zu tun.")
        return
    organizer = User(phone="491700000001", name="Demo-Veranstalter", role="ORGANIZER")
    db.add(organizer)
    db.flush()
    today = local_now().replace(hour=0, minute=0)
    rnd = random.Random(42)
    for i in range(24):
        title, styles, etype, level, price = EVENTS[i % len(EVENTS)]
        venue = VENUES[i % len(VENUES)]
        start = today + timedelta(days=rnd.randint(0, 40), hours=rnd.choice([19, 20, 21, 22]))
        key, w, h = images.store_image(make_flyer(title, f"{start:%d.%m.%Y %H:%M}", venue[0], COLORS[i % len(COLORS)]))
        media = Media(original_path=key, width=w, height=h, source=EventSource.FORM, status=MediaStatus.FLYER)
        db.add(media)
        db.flush()
        status = EventStatus.LIVE
        if i == 5:
            status = EventStatus.CANCELLED
        db.add(Event(
            title=title, dance_styles=styles, event_type=etype, level=level, price=price,
            start=start, end=start + timedelta(hours=5), doors=f"{start.hour - 1}:30",
            location_name=venue[0], address=venue[1], postcode=venue[2], city=venue[3], lat=venue[4], lng=venue[5],
            description="Beste Musik, toller Floor und nette Leute. Komm vorbei!",
            status=status, source=EventSource.FORM, owner_id=organizer.id, media_id=media.id,
        ))
    # Two bot drafts waiting for review
    for j, conf in enumerate([0.72, 0.9]):
        title, styles, etype, level, price = EVENTS[j]
        key, w, h = images.store_image(make_flyer(title, "Sa 21:00", "?", COLORS[j]))
        media = Media(original_path=key, width=w, height=h, source=EventSource.BOT, status=MediaStatus.FLYER,
                      sender_phone="491700000002", classifier_label="flyer", classifier_score=0.97,
                      classifier_reason="Flyer mit Datum und Ort", classifier_ms=180)
        db.add(media)
        db.flush()
        db.add(Event(
            title=title, dance_styles=styles, event_type=etype, start=today + timedelta(days=6 + j, hours=21),
            location_name="Unbekannte Bar", city="Berlin", status=EventStatus.ADMIN_REVIEW, source=EventSource.BOT,
            media_id=media.id, submitter_phone="491700000002", confidence=conf,
            field_confidence={"title": 0.95, "dance_styles": 0.93, "event_type": 0.9, "start": 0.88,
                              "location_name": conf, "city": 0.8},
        ))
    db.commit()
    print("Demo-Daten angelegt: 24 Events, 2 Bot-Entwürfe in der Prüfliste.")


if __name__ == "__main__":
    main()
