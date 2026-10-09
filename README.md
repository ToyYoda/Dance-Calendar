# Dance Calendar

KI-gestützter Kalender für Tanzevents (Salsa, Bachata, Kizomba, Zouk …). Flyer, die bisher nur in WhatsApp-Gruppen kursieren, werden per WhatsApp-Bot eingesammelt, von einem **lokalen Vision-Modell** ausgelesen und landen nach automatischer oder manueller Prüfung im Kalender.

Die zusammengeführten User Stories und alle Designentscheidungen stehen in [`docs/user-stories.md`](docs/user-stories.md).

## Funktionen

**Für Tänzer:innen**
- Monats-, Wochen-, Listen- und Kartenansicht; Klick auf einen Tag zeigt dessen Events
- Filter nach Tanzstil, Event-Typ, Level, Stadt, Zeitraum plus Freitextsuche
- Gespeicherte Filterprofile, ein Standardprofil wird beim Öffnen angewendet
- „Bin dabei“ mit Zähler (aktualisiert alle 15 s), Zusagen im Profil
- `.ics`-Download pro Event, abonnierbare Feeds für Zusagen und für jede Filterauswahl; Änderungen und Absagen aktualisieren die Kalendereinträge
- Karte mit Pin und „Route planen“, ohne Standortabfrage
- Abgesagte Events bleiben sichtbar, mit rotem Banner und durchgestrichen
- Hell- und Dunkelmodus, mobil optimiert

**Für Veranstalter:innen**
- **Mit Flyer starten:** Flyer auf „Event eintragen“ hochladen, das Modell füllt das Formular vor (unsichere Felder markiert, fehlende Adresse über OpenStreetMap ergänzt), prüfen, veröffentlichen
- Formular mit Live-Validierung, Flyer-Upload, Entwurf / Veröffentlichen, Bearbeiten, Absagen, Löschen
- Flyer einfach per WhatsApp schicken; Absagen per Textnachricht („… fällt aus“)
- „Nicht mein Event“ gibt per Bot angelegte Events an die Admins ab

**KI-Pipeline** (`app/ingestion.py`)
1. Bild wird gespeichert und in WebP gewandelt (Thumb 200 px, Preview 800 px, Original)
2. **Triage**: billige Pillow-Vorprüfung, dann kurzer Ja/Nein-Prompt; Nicht-Flyer landen im Spam-Ordner
3. **Extraktion** aller Felder mit Confidence pro Feld; Unlesbares bleibt leer
4. **Adressvervollständigung** über OpenStreetMap Nominatim; mehrdeutige Treffer gehen zur Auswahl ins Review
5. **Routing**: Gesamtscore > 0,95 → sofort live, sonst Admin-Review (Schwellen im Dashboard einstellbar)

**Für Admins** (`/admin`, Login per Magic Link an Whitelist-Nummern)
- Prüfliste nach Score, Datum, Quelle; Review mit Flyer neben dem Formular, unsichere Felder gelb, Tastenkürzel A/R/D/N
- Swipe-Review fürs Handy (rechts = freigeben, links = ablehnen, Rückgängig, Pinch-Zoom)
- Galerie mit Mehrfachauswahl: freigeben, ablehnen, „kein Event“, Ort zuweisen, löschen
- Bearbeitungssperre (5 min, first come, first served) und Schutz vor stillem Überschreiben
- Spam-Ordner, unklare Absagen, Monitoring (Automation Rate, Fehlerquote pro Feld, Triage-Genauigkeit und -Dauer)
- Bot-Simulator zum Testen ohne WhatsApp

## Schnellstart

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env          # ADMIN_PHONES auf deine Nummer setzen
.venv/bin/python -m app.seed  # optional: Demo-Daten
.venv/bin/uvicorn app.main:app --reload
```

Dann <http://localhost:8000> öffnen. Im Entwicklungsmodus (`DEV_MODE=1`, `MESSENGER=console`) werden Login-Codes und Admin-Links direkt auf der Seite angezeigt, es braucht also kein WhatsApp. Admin-Login: <http://localhost:8000/admin/login> mit einer Nummer aus `ADMIN_PHONES`.

Tests: `.venv/bin/python -m pytest`

## Lokales KI-Modell

Standard ist `AI_PROVIDER=mock` (feste Beispieldaten). Für echte Flyer-Erkennung mit [Ollama](https://ollama.com):

```bash
ollama pull llama3.2-vision
# .env
AI_PROVIDER=openai
AI_BASE_URL=http://localhost:11434/v1
AI_MODEL=llama3.2-vision
```

Das `openai`-Paket wird nur als Client für die OpenAI-kompatible Schnittstelle genutzt; die Flyer verlassen den Server nicht. Mit `AI_BASE_URL=https://api.openai.com/v1` und einem API-Key ginge auch die OpenAI-Cloud.

## WhatsApp anbinden

1. In der Meta-Developer-Konsole eine WhatsApp-Cloud-API-App anlegen.
2. `.env`: `MESSENGER=cloud`, `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_APP_SECRET`, `DEV_MODE=0`.
3. Webhook-URL `https://<deine-domain>/webhooks/whatsapp` mit dem Verify-Token eintragen und das Feld `messages` abonnieren.

Eingehende Webhooks werden per `X-Hub-Signature-256` geprüft.

## Betrieb

```bash
docker build -t dance-calendar .
docker run -p 8000:8000 -v dance-data:/data --env-file .env dance-calendar
```

Hinter HTTPS `COOKIE_SECURE=1` setzen. SQLite reicht für eine Region; für mehr Last `DATABASE_URL` auf PostgreSQL umstellen (SQLAlchemy, kein Code-Umbau nötig, Treiber `psycopg` nachinstallieren).

## Aufbau

```
app/
  main.py          App, Router, Fehlerbehandlung
  config.py        Einstellungen aus Umgebung / .env
  models.py        Datenmodell (SQLAlchemy)
  security.py      OTP, Sessions, Magic Links, Rollen, Sperren
  ingestion.py     WhatsApp-Pipeline: Triage → Extraktion → Adresse → Routing, Absagen
  ai.py            Vision-Modell (OpenAI-kompatibel), Normalisierung, Scores
  geocode.py       OpenStreetMap Nominatim
  ical.py          iCalendar-Export
  services.py      Filter, Statuswechsel, Schwellenwerte
  routers/         public, auth, me, organizer, admin, webhooks
  templates/       Jinja2-Templates (Deutsch)
  static/          CSS, JS (ohne Build-Schritt)
tests/             pytest-Suite über alle Stories
docs/              konsolidierte User Stories und Entscheidungen
```
