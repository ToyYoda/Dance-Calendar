"""Demo data: python -m app.seed"""

import io
import random
from datetime import timedelta

from PIL import Image, ImageDraw, ImageFilter, ImageFont

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
# Accent glow per flyer: wine, plum, smoky gold, midnight teal, ember
COLORS = [(150, 18, 52), (96, 36, 120), (160, 120, 50), (24, 84, 90), (170, 60, 30)]
GOLD = (201, 164, 92)
SERIF_FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSerif.ttf",
    "/Library/Fonts/Georgia.ttf",
    "C:/Windows/Fonts/georgia.ttf",
]


def _font(size: int, italic: bool = False):
    paths = SERIF_FONTS[1:2] + SERIF_FONTS if italic else SERIF_FONTS
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def make_flyer(title: str, when: str, where: str, color) -> bytes:
    """A dark, candle-lit demo flyer: black base, blurred accent spotlight, thin gold frame, serif type."""
    w, h = 800, 1100
    img = Image.new("RGB", (w, h), (10, 7, 9))
    glow = Image.new("RGB", (w, h), (10, 7, 9))
    gd = ImageDraw.Draw(glow)
    rnd = random.Random(title)
    cx, cy = rnd.randint(250, 550), rnd.randint(250, 420)
    gd.ellipse([cx - 360, cy - 300, cx + 360, cy + 300], fill=color)
    gd.ellipse([w - 260, h - 300, w + 120, h + 80], fill=tuple(c // 2 for c in color))
    img = Image.blend(img, glow.filter(ImageFilter.GaussianBlur(140)), 0.9)
    draw = ImageDraw.Draw(img)
    # a thin gold ring as a spotlight / moon motif
    draw.ellipse([cx - 150, cy - 150, cx + 150, cy + 150], outline=tuple(c * 2 // 3 for c in GOLD), width=1)
    draw.rectangle([28, 28, w - 28, h - 28], outline=GOLD, width=2)
    draw.rectangle([40, 40, w - 40, h - 40], outline=tuple(c // 2 for c in GOLD), width=1)
    small, mid = _font(30), _font(34, italic=True)
    draw.text((w // 2, 110), "—  NIGHT  OF  DANCE  —", fill=GOLD, font=small, anchor="mm")
    y = 620
    for line in _wrap(title.upper(), 15):
        draw.text((w // 2, y), line, fill=(240, 228, 214), font=_font(60), anchor="mm")
        y += 76
    draw.line([w // 2 - 80, y, w // 2 + 80, y], fill=GOLD, width=1)
    draw.text((w // 2, y + 50), when, fill=GOLD, font=mid, anchor="mm")
    draw.text((w // 2, y + 100), where, fill=(200, 188, 176), font=small, anchor="mm")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return buf.getvalue()


def _wrap(text: str, width: int) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    return lines + [line] if line else lines


def main() -> None:
    init_db()
    db = SessionLocal()
    if db.query(Event).count():
        print("Datenbank enthält schon Events – nichts zu tun. (Neu anlegen: Datenbank-Datei löschen.)")
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
