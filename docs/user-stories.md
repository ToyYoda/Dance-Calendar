# Dance Calendar – konsolidierte User Stories

Grundlage sind zwei Quellen, die sich stark überschneiden:

- **S** = `SonnetCal User Stories.md` (22 Stories, Phasen 1–3)
- **R** = `requirements.md` (Anforderungskatalog, 22 Stories)

Die Nummern beider Dateien passen nicht zueinander (z. B. ist S.05 „iCal-Export“, R.05 aber „Karte“). Deshalb hat jede Story hier eine eigene Nummer **DC-xx**. In Klammern steht, aus welchen Quell-Stories sie zusammengesetzt ist. Akzeptanzkriterien beider Quellen sind vereinigt; wo sie sich widersprechen, steht die Entscheidung im Abschnitt [Entscheidungen](#entscheidungen).

Rollen: **Tänzer:in** (Endnutzer), **Veranstalter:in**, **Admin**, **System** (KI-Ingestion).

Status eines Events: `DRAFT` → `ADMIN_REVIEW` → `LIVE` / `REJECTED`, später ggf. `CANCELLED`.

---

## 1. Endnutzer

**DC-01 Kalenderansicht** (S.01, R.01, R.02)
- Umschalter zwischen Monats-, Wochen- und Listenansicht.
- Monats-/Wochenansicht markiert Tage mit Events; Klick auf einen Tag zeigt die Events dieses Tages.
- Listenansicht zeigt chronologisch alle Events ab heute.
- Nur Events mit Status `LIVE` oder `CANCELLED` (gekennzeichnet); vergangene Events sind ausgeblendet.
- Jeder Eintrag zeigt Titel, Datum, Uhrzeit, Ort und Tanzstil.
- Gefilterte Liste lädt in < 2 s.

**DC-02 Filter und Suche** (S.02, R.01, R.03)
- Filter: Tanzstil (Mehrfachauswahl), Event-Typ (Party, Workshop, Festival, Social), Level, Stadt, Zeitraum.
- Freitextsuche über Titel, Ort und Beschreibung.

**DC-03 Event-Details** (S.02)
- Beschreibung, Adresse, Preis, Einlass, Flyer und Ticketlink, soweit vorhanden.

**DC-04 Gespeicherte Filterprofile** (S.03, R.03)
- Angemeldete Nutzer speichern Filterkombinationen unter einem Namen, rufen sie mit einem Klick auf, benennen sie um und löschen sie.
- Ein Profil kann „Standard“ sein; es wird beim Öffnen der App automatisch angewendet.

**DC-05 „Bin dabei“** (S.04, R.04)
- Button in der Detailansicht; Zusage setzen und widerrufen, höchstens eine pro Person.
- Nur per WhatsApp-OTP verifizierte Nutzer können zusagen.
- Anzahl der Zusagen wird near-realtime angezeigt.
- Eigene Zusagen erscheinen im Profil.

**DC-06 Karte und Route** (S.06, R.05)
- Detailseite zeigt eine Karte mit Pin auf der Location.
- Kartenansicht aller Events mit denselben Filtern wie die Liste; Marker öffnet Kurzansicht mit Link zur Detailseite.
- „Route planen“ öffnet die Adresse in der Karten-App des Geräts.
- Der Standort der Nutzer wird weder abgefragt noch gespeichert (Privacy by Design).

**DC-07 iCal-Export und Feeds** (S.05, R.06)
- Einzelnes Event als `.ics` herunterladen.
- Abonnierbarer Feed der eigenen Zusagen.
- Abonnierbarer Feed für die aktuelle Filterauswahl (eindeutige URL).
- Einträge enthalten Titel, Zeit, Ort und Link zum Event/Flyer in der App.
- Änderungen und Absagen aktualisieren den Kalendereintrag (`SEQUENCE`, `STATUS:CANCELLED`).

**DC-08 Absagen sichtbar** (S.15, R.11)
- Abgesagte Events bleiben sichtbar, mit rotem „ABGESAGT“-Banner und durchgestrichenem Titel.

## 2. Daten-Ingestion und KI

**DC-10 WhatsApp-Eingang** (S.07, R.07)
- Der Bot nimmt Bilder und Textnachrichten entgegen und bestätigt den Eingang.
- Absender wird über die Telefonnummer einem Nutzer zugeordnet (unbekannte Nummern legen ein Profil an).
- Originalbild wird mit Absender und Zeitstempel gespeichert.

**DC-11 Bild-Triage** (S.08, R.08)
- Jedes Bild wird vorab als Flyer / Nicht-Flyer klassifiziert, bevor die teure Extraktion läuft.
- Nicht-Flyer erzeugen kein Event und landen im Spam-Ordner; die Entscheidung wird protokolliert.
- Zielwerte: > 98 % Genauigkeit, < 500 ms pro Bild.

**DC-12 Strukturierte Extraktion** (S.09, R.07)
- Extrahiert werden Titel, Tanzstil, Event-Typ, Level, Start, Ende, Einlass, Location, Adresse, Stadt, Preis, Ticketlink, Beschreibung.
- Nicht erkennbare Felder bleiben leer und werden nicht geraten.
- Verarbeitung mit einem lokalen Modell (z. B. Llama Vision) für Datensouveränität.

**DC-13 Confidence und Routing** (S.10, R.09, R.10)
- Jedes Feld hat einen Score 0–1, das Event einen Gesamtscore.
- Gesamtscore > 0,95 → sofort `LIVE`, Veranstalter bekommt eine WhatsApp-Bestätigung.
- Sonst → `ADMIN_REVIEW`. Felder < 0,85 sind im Review farblich markiert.

**DC-14 Adress-Vervollständigung** (S.11, R.22)
- Fehlende Straße/PLZ/Koordinaten werden aus Location-Name und Stadt gesucht (OSM Nominatim).
- Eindeutige Treffer (> 90 %) werden automatisch übernommen und als „automatisch ergänzt“ markiert.
- Mehrdeutige Treffer senken den Score und werden zur Auswahl im Admin-Review angeboten.

**DC-15 Absage-Erkennung** (S.15, R.11)
- Textnachrichten mit „fällt aus“, „abgesagt“, „storniert“ usw. werden als Absage erkannt.
- Nur Inhaber:in des Events oder ein Admin kann absagen.
- Eindeutige Zuordnung → `CANCELLED` und WhatsApp-Bestätigung; unsichere Zuordnung → Admin-Prüfung.

**DC-16 Optimierte Bilder** (R.21)
- Flyer werden in WebP gewandelt: Thumb (200 px), Preview (800 px), Original.
- Listen laden nur Thumbnails.

## 3. Veranstalter

**DC-20 Veranstalter-Formular** (S.12, R.12)
- Events anlegen, bearbeiten, löschen inkl. Flyer-Upload.
- Pflichtfelder: Titel, Tanzstil, Startdatum, Location, Adresse, Stadt – mit Validierung in Echtzeit.
- Übersicht aller eigenen Events mit Status.

**DC-21 „Nicht mein Event“** (R.19)
- Wird ein Event per Bot für eine Nummer angelegt, bekommt sie eine Nachricht.
- „Nicht mein Event“ überträgt das Ownership an den Admin; das Event verschwindet aus der eigenen Übersicht.

## 4. Admin

**DC-30 Triage-Liste** (S.13, R.13)
- Alle Events in `ADMIN_REVIEW`, sortierbar nach Score und Eventdatum, filterbar nach Quelle und Status.
- Flyer und extrahierte Daten nebeneinander.

**DC-31 Review, Korrektur, Freigabe** (S.14, R.13)
- Jedes Feld bearbeitbar; unsichere Felder hervorgehoben; korrigierte Felder als „manuell geprüft“ markiert.
- Freigeben (`LIVE`), Ablehnen, Löschen; Tastenkürzel für Freigeben, Löschen, Nächster.
- Einreicher bekommt bei Freigabe/Ablehnung eine WhatsApp-Nachricht.

**DC-32 Swipe-Review** (S.17, R.14)
- Karte mit Flyer und Kerndaten; rechts wischen = freigeben, links = ablehnen.
- Letzte Aktion rückgängig machen; Tippen öffnet die volle Bearbeitung.
- Pinch-to-Zoom auf dem Flyer; Touch-Ziele ≥ 44×44 px.

**DC-33 Batch-Aktionen** (S.16, R.16)
- Galerie mit Mehrfachauswahl: freigeben, ablehnen, „kein Event“ (löschen), „Ort zuweisen“.
- Bestätigung mit Anzahl vor der Aktion; Fehlschläge einzeln gemeldet, der Rest läuft durch.

**DC-34 Bearbeitungssperre** (S.22, R.15)
- Öffnen zur Bearbeitung setzt eine Sperre für 5 Minuten (first come, first served).
- Andere sehen Schloss-Symbol und Namen; Sperre endet beim Speichern/Schließen oder nach Ablauf.
- Bearbeitet ein Veranstalter ein Event im Review, bekommt er einen Hinweis statt eines stillen Überschreibens.

**DC-35 Model-Monitoring** (R.17)
- Dashboard: Automation Rate, Fehlerquote pro Feld (aus Admin-Korrekturen), Triage-Statistik.
- Schwellenwerte für Auto-Publish und Feld-Markierung sind im Dashboard einstellbar.

## 5. Identität und Sicherheit

**DC-40 WhatsApp-OTP-Login** (S.18, R.18)
- Nummer eingeben, Code per WhatsApp; einmalig und 10 Minuten gültig.
- Neue Nummern legen automatisch ein Profil an.

**DC-41 Session** (S.19, R.18)
- Session gilt 30 Tage und verlängert sich bei Nutzung (Sliding Refresh).
- Abmelden macht die Session serverseitig ungültig.

**DC-42 Magic-Link für Admins** (S.20, R.20)
- Nur Nummern auf der serverseitigen Whitelist bekommen einen Link.
- Link ist einmalig und 15 Minuten gültig. Admin-Rechte gibt es nur über die Whitelist.

**DC-43 Rollen und Ownership** (S.21)
- Rollen: USER, ORGANIZER, ADMIN.
- Veranstalter ändern nur eigene Events, Admins alle; serverseitig geprüft.

---

## Entscheidungen

| # | Thema | Konflikt / offene Frage | Entscheidung |
|---|---|---|---|
| E1 | Score zwischen 0,85 und 0,95 | S.10 lässt das offen | Geht in `ADMIN_REVIEW`. Nur > Auto-Publish-Schwelle wird automatisch `LIVE`. Beide Schwellen sind im Dashboard einstellbar (DC-35). |
| E2 | Lokales LLM vs. `openai`-Paket | R.09 will lokales Modell, requirements.txt nennt `openai` | Das `openai`-SDK spricht jede OpenAI-kompatible API an. Standard ist ein lokales Ollama (`llama3.2-vision`); die OpenAI-Cloud ist per Konfiguration möglich. Ein `mock`-Modus erlaubt Entwicklung ohne Modell. |
| E3 | Triage-Classifier | > 98 % / < 500 ms ist mit einem großen Vision-Modell kaum zu garantieren | Zweistufig: billige Pillow-Vorprüfung (Größe, Format, Farbvielfalt), danach ein kurzer Ja/Nein-Prompt mit verkleinertem Bild. Jede Entscheidung wird geloggt; Admins können Spam wiederherstellen. Die Zielwerte werden im Dashboard gemessen, nicht zugesichert. |
| E4 | Status nach Formular-Absenden | S.12: DRAFT bis Absenden; R.12: „volle Kontrolle“ | Entwurf speichern → `DRAFT`. Absenden → direkt `LIVE` (Self-Service, kein Review). |
| E5 | Wer darf Events anlegen? | Rollen in S.21, Verifizierung in R.18 | Jede:r verifizierte Nutzer:in. Mit dem ersten Event wird man automatisch `ORGANIZER`. |
| E6 | Admin-Magic-Link | S.20 „per Link“, R.20 „Mobilnummern auf Whitelist“ | Whitelist aus Telefonnummern (`ADMIN_PHONES`), Link wird per WhatsApp verschickt. |
| E7 | Push-Nachrichten (R.19) | Keine native App | „Push“ = WhatsApp-Nachricht über den Bot. |
| E8 | Kalenderansichten | S.01 Monat/Liste, R.01 Monat/Woche | Monat, Woche und Liste. |
| E9 | Karte | R.05 statisch im Detail, S.06 Kartenansicht | Beides, mit OpenStreetMap/Leaflet ohne API-Key und ohne Geolocation. |
| E10 | „Echtzeit“-Zusagen | R.04 Near-Realtime | Polling alle 15 s. |
| E11 | WhatsApp-Anbindung | — | Webhook für die WhatsApp Cloud API. Für Entwicklung gibt es einen Bot-Simulator im Admin-Bereich und einen Konsolen-Messenger, der Nachrichten protokolliert. |
| E12 | iCal-Feeds | S.05 Zusagen-Feed, R.06 Filter-Feed | Beides, jeweils mit geheimem Token in der URL. |
