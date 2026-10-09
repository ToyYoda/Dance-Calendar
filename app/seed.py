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
# Poster palettes (background, title, accent): ink black, oxblood, paper
COLORS = [
    ((16, 14, 13), (235, 229, 218), (192, 57, 43)),
    ((150, 34, 26), (16, 14, 13), (235, 229, 218)),
    ((232, 225, 211), (20, 18, 16), (163, 34, 26)),
    ((16, 14, 13), (192, 57, 43), (235, 229, 218)),
    ((40, 36, 33), (235, 229, 218), (192, 57, 43)),
]
SERIF_FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSerif.ttf",
    "/Library/Fonts/Didot.ttc",
    "C:/Windows/Fonts/BOD_R.TTF",
    "C:/Windows/Fonts/georgia.ttf",
]
SANS_FONTS = [
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/Library/Fonts/Arial.ttf",
    "C:/Windows/Fonts/arial.ttf",
]


def _font(size: int, sans: bool = False):
    for path in SANS_FONTS if sans else SERIF_FONTS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def make_flyer(title: str, when: str, where: str, palette) -> bytes:
    """A typographic demo poster: flat colour, big serif title, one accent rule, print grain."""
    bg, fg, accent = palette
    w, h = 800, 1100
    img = Image.new("RGB", (w, h), bg)
    draw = ImageDraw.Draw(img)
    rnd = random.Random(title)
    margin = 64
    # accent bar along the left edge, length varies per poster
    bar_top = rnd.randint(80, 260)
    draw.rectangle([margin, bar_top, margin + 10, bar_top + rnd.randint(260, 480)], fill=accent)
    # huge stacked title
    size = 118 if len(title) < 22 else 96
    lines = _wrap(title, 11 if size > 100 else 13)
    max_width = w - 2 * margin - 44
    while size > 48 and max(draw.textlength(line, font=_font(size)) for line in lines) > max_width:
        size -= 6
    y = bar_top
    for line in lines:
        draw.text((margin + 44, y), line, fill=fg, font=_font(size))
        y += int(size * 1.02)
    # footer: date and venue, small sans, separated by a hairline
    draw.line([margin, h - 190, w - margin, h - 190], fill=accent, width=2)
    draw.text((margin, h - 165), when, fill=fg, font=_font(40, sans=True))
    draw.text((margin, h - 110), where, fill=accent, font=_font(32, sans=True))
    # print grain
    noise = Image.effect_noise((w, h), 40).convert("RGB")
    img = Image.blend(img, noise, 0.06)
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
