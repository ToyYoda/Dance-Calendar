"""Vision-model access: flyer triage (DC-11) and structured extraction (DC-12, DC-13).

Uses the `openai` SDK against any OpenAI-compatible endpoint. The default is a local
Ollama server (AI_BASE_URL=http://localhost:11434/v1, AI_MODEL=qwen3.5:4b) so flyers
never leave the server. AI_PROVIDER=mock returns canned results for development.
"""

import base64
import json
import re
import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from dateutil import parser as dateparser
from PIL import Image

from app import images
from app.config import settings
from app.models import DANCE_STYLES, EVENT_TYPES, EXTRACTED_FIELDS, LEVELS

log = logging.getLogger(__name__)

CORE_FIELDS = ["title", "start", "location_name", "city", "dance_styles"]


@dataclass
class Classification:
    is_flyer: bool
    score: float
    reason: str
    ms: int = 0


@dataclass
class Extraction:
    values: dict = field(default_factory=dict)
    confidence: dict = field(default_factory=dict)

    @property
    def overall(self) -> float:
        """The weakest core field decides; a missing core field means we are not sure at all."""
        scores = []
        for name in CORE_FIELDS:
            if not self.values.get(name):
                return 0.0
            scores.append(self.confidence.get(name, 0.0))
        return round(min(scores), 3)


class AIError(RuntimeError):
    pass


def _client():
    from openai import OpenAI

    return OpenAI(base_url=settings.ai_base_url, api_key=settings.ai_api_key or "none", timeout=settings.ai_timeout)


def _image_part(key: str, max_side: int) -> dict:
    b64 = base64.b64encode(images.to_jpeg_bytes(key, max_side)).decode()
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}


THINK_BLOCK = re.compile(r"<think>.*?(</think>|$)", re.S | re.I)
TRAILING_COMMA = re.compile(r",\s*([}\]])")
DOUBLE_COMMA = re.compile(r",(\s*,)+")


def _candidates(text: str):
    """Every JSON object embedded in text, longest first (models like to wrap JSON in prose or fences)."""
    decoder = json.JSONDecoder()
    found = []
    repaired = DOUBLE_COMMA.sub(",", text.replace("“", '"').replace("”", '"'))
    for variant in (text, TRAILING_COMMA.sub(r"\1", repaired)):
        for i, ch in enumerate(variant):
            if ch != "{":
                continue
            try:
                obj, end = decoder.raw_decode(variant, i)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                found.append((end - i, obj))
    return [obj for _, obj in sorted(found, key=lambda t: -t[0])]


def _parse_json(text: str, expect: tuple[str, ...] = ()) -> dict:
    # reasoning models (Qwen 3.x, DeepSeek R1 …) may prepend their thoughts
    cleaned = THINK_BLOCK.sub("", text or "").strip()
    if not cleaned:
        raise AIError("Das Modell hat keine Antwort geliefert (evtl. nur nachgedacht und das Token-Limit erreicht).")
    for obj in _candidates(cleaned):
        # some models wrap the result: {"event": {...}} or {"data": {...}}
        if expect and not any(k in obj for k in expect) and len(obj) == 1:
            inner = next(iter(obj.values()))
            if isinstance(inner, dict):
                obj = inner
        hits = [k for k in expect if k in obj]
        # a bare {"title": 0.9, ...} is the confidence map, not the answer
        if not expect or (hits and not all(isinstance(obj[k], (int, float)) for k in hits)):
            return obj
    log.warning("Unbrauchbare Modellantwort: %s", cleaned[:2000])
    snippet = " ".join(cleaned.split())[:300]
    raise AIError(f"Antwort des Modells ist kein gültiges JSON. Anfang der Antwort: «{snippet}»")


def _ask(model: str, prompt: str, key: str, max_side: int, max_tokens: int,
         schema: dict | None = None, expect: tuple[str, ...] = ()) -> dict:
    extra = {}
    if "api.openai.com" not in settings.ai_base_url:
        # Ollama: switch off the "thinking" phase of reasoning models; it costs time and tokens
        extra["extra_body"] = {"think": False}
    messages = [{"role": "user", "content": [{"type": "text", "text": prompt}, _image_part(key, max_side)]}]
    # A JSON schema makes Ollama (and OpenAI) constrain the output to exactly this structure.
    formats = []
    if schema:
        formats.append({"type": "json_schema", "json_schema": {"name": "result", "schema": schema, "strict": True}})
    formats.append({"type": "json_object"})
    client = _client()
    last_error = None
    for fmt in formats:
        try:
            resp = client.chat.completions.create(
                model=model, temperature=0, max_tokens=max_tokens, response_format=fmt, messages=messages, **extra,
            )
        except Exception as exc:
            from openai import APIConnectionError, APITimeoutError

            if isinstance(exc, (APIConnectionError, APITimeoutError)):
                raise AIError(f"KI-Modell nicht erreichbar: {exc}") from exc
            last_error = exc  # e.g. the server does not support json_schema: try the next format
            continue
        choice = resp.choices[0]
        try:
            return _parse_json(choice.message.content, expect)
        except AIError as exc:
            if choice.finish_reason == "length":
                raise AIError("Die Antwort des Modells wurde abgeschnitten (Token-Limit). "
                              "Ein größeres Modell oder AI_TIMEOUT erhöhen hilft meist.") from exc
            last_error = exc
    if isinstance(last_error, AIError):
        raise last_error
    raise AIError(f"KI-Anfrage fehlgeschlagen: {last_error}")


# --- Triage ----------------------------------------------------------------

CLASSIFY_PROMPT = """Is this image a flyer or poster that announces a specific event
(e.g. a dance party, workshop, festival or social) with at least a date or a venue?
Photos of people, memes, screenshots of chats and private pictures are NOT flyers.
Reply with JSON only: {"is_flyer": true|false, "confidence": 0.0-1.0, "reason": "<max 10 words>"}"""


CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "is_flyer": {"type": "boolean"},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["is_flyer", "confidence", "reason"],
    "additionalProperties": False,
}


def prefilter(key: str) -> Classification | None:
    """Cheap Pillow checks that reject obvious non-flyers without calling the model."""
    with Image.open(images.image_path(key, "thumb")) as img:
        w, h = img.size
        colors = img.convert("RGB").getcolors(maxcolors=4096)
    with Image.open(images.image_path(key)) as orig:
        ow, oh = orig.size
    if min(ow, oh) < 150:
        return Classification(False, 0.95, "Bild zu klein für einen Flyer")
    if max(w, h) / max(1, min(w, h)) > 4:
        return Classification(False, 0.9, "Extremes Seitenverhältnis")
    if colors is not None and len(colors) < 4:
        return Classification(False, 0.9, "Einfarbiges Bild")
    return None


def classify(key: str) -> Classification:
    started = time.perf_counter()
    result = prefilter(key)
    if result is None:
        if settings.ai_provider == "mock":
            result = Classification(True, 0.99, "Mock: als Flyer angenommen")
        else:
            data = _ask(settings.ai_classifier_model or settings.ai_model, CLASSIFY_PROMPT, key, 448, 400,
                        schema=CLASSIFY_SCHEMA, expect=("is_flyer",))
            result = Classification(
                bool(data.get("is_flyer")),
                _clamp(data.get("confidence", 0.5)),
                str(data.get("reason", ""))[:200],
            )
    result.ms = int((time.perf_counter() - started) * 1000)
    return result


# --- Extraction ------------------------------------------------------------


def extraction_prompt(today: date) -> str:
    return f"""Read this dance event flyer and return its data as one JSON object. Today is {today.isoformat()}.

Rules:
- Copy only what is printed on the flyer. Use null (or [] for dance_styles) when something is not on it. Never guess.
- dates as "YYYY-MM-DDTHH:MM". If a date has no year, use the next occurrence on or after today.
- dance_styles: any of {", ".join(DANCE_STYLES)}
- event_type: one of {", ".join(EVENT_TYPES)} or null
- level: one of {", ".join(LEVELS)} or null
- address = street and house number only; postcode and city separately
- price as written on the flyer, e.g. "10 € / AK 12 €"
- description: short summary in German, max 400 characters, or null
- confidence: for every field, how sure you are (0.0 to 1.0) that it is printed on the flyer and read correctly

Answer with JSON only, no other text:
{{"title": ..., "dance_styles": [...], "event_type": ..., "level": ..., "start": ..., "end": ..., "doors": "HH:MM",
 "location_name": ..., "address": ..., "postcode": ..., "city": ..., "price": ..., "ticket_url": ..., "description": ...,
 "confidence": {{"title": 0.9, "start": 0.8, ...}}}}"""


def _nullable(schema: dict) -> dict:
    return {"anyOf": [schema, {"type": "null"}]}


EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        **{name: _nullable({"type": "string"}) for name in EXTRACTED_FIELDS},
        "dance_styles": {"type": "array", "items": {"type": "string", "enum": DANCE_STYLES}},
        "event_type": _nullable({"type": "string", "enum": EVENT_TYPES}),
        "level": _nullable({"type": "string", "enum": LEVELS}),
        "confidence": {
            "type": "object",
            "properties": {name: {"type": "number"} for name in EXTRACTED_FIELDS},
            "required": list(EXTRACTED_FIELDS),
            "additionalProperties": False,
        },
    },
    "required": [*EXTRACTED_FIELDS, "confidence"],
    "additionalProperties": False,
}


def _clamp(v) -> float:
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return 0.0


def _match_option(raw: str, options: list[str]) -> str | None:
    if not raw:
        return None
    low = raw.strip().lower()
    for opt in options:
        if opt.lower() == low or opt.lower() in low or low in opt.lower():
            return opt
    aliases = {"intermediate": "Mittelstufe", "beginner": "Anfänger", "advanced": "Fortgeschritten", "all levels": "Open"}
    return aliases.get(low)


def _parse_dt(raw) -> datetime | None:
    if not raw:
        return None
    try:
        return dateparser.parse(str(raw), dayfirst=True).replace(tzinfo=None, second=0, microsecond=0)
    except (ValueError, OverflowError):
        return None


def normalize_extraction(data: dict) -> Extraction:
    """Validate raw model output against our vocabularies; anything unusable becomes empty."""
    ex = Extraction()
    confidences = data.get("confidence") if isinstance(data.get("confidence"), dict) else {}
    for name in EXTRACTED_FIELDS:
        item = data.get(name)
        if isinstance(item, dict):  # {"value": ..., "confidence": ...}
            value, conf = item.get("value"), _clamp(item.get("confidence", 0.0))
        else:  # flat value, confidence in a separate object
            value, conf = item, _clamp(confidences.get(name, 0.5))
        if value in ("", [], None) or (isinstance(value, str) and value.strip().lower() in ("null", "none", "n/a")):
            continue

        if name == "dance_styles":
            raw_list = value if isinstance(value, list) else re.split(r"[,/&+]| und ", str(value))
            styles = []
            for raw in raw_list:
                match = _match_option(str(raw), DANCE_STYLES)
                if match and match not in styles:
                    styles.append(match)
            if not styles:
                continue
            value = styles
        elif name == "event_type":
            value = _match_option(str(value), EVENT_TYPES)
        elif name == "level":
            value = _match_option(str(value), LEVELS)
        elif name in ("start", "end"):
            value = _parse_dt(value)
        elif name == "doors":
            m = re.search(r"(\d{1,2})[:.](\d{2})", str(value))
            value = f"{int(m.group(1)):02d}:{m.group(2)}" if m else None
        elif name == "ticket_url":
            value = str(value).strip()
            if not value.startswith(("http://", "https://")):
                value = "https://" + value if "." in value and " " not in value else None
        else:
            value = str(value).strip()[: 400 if name == "description" else 200]

        if value in (None, "", []):
            continue
        ex.values[name] = value
        ex.confidence[name] = conf

    if ex.values.get("end") and ex.values.get("start") and ex.values["end"] < ex.values["start"]:
        ex.values.pop("end")
        ex.confidence.pop("end", None)
    return ex


MOCK_RESULT = {
    "title": {"value": "Salsa Night (Mock-Extraktion)", "confidence": 0.9},
    "dance_styles": {"value": ["Salsa", "Bachata"], "confidence": 0.92},
    "event_type": {"value": "Party", "confidence": 0.9},
    "start": {"value": None, "confidence": 0.8},
    "location_name": {"value": "Tanzschule Beispiel", "confidence": 0.7},
    "city": {"value": "Berlin", "confidence": 0.88},
    "price": {"value": "10 €", "confidence": 0.6},
}


def extract(key: str, today: date | None = None) -> Extraction:
    today = today or date.today()
    if settings.ai_provider == "mock":
        data = json.loads(json.dumps(MOCK_RESULT))
        data["start"]["value"] = f"{(today + timedelta(days=7)).isoformat()}T21:00"
        return normalize_extraction(data)
    data = _ask(settings.ai_model, extraction_prompt(today), key, 1280, 3000,
                schema=EXTRACTION_SCHEMA, expect=("title", "start", "location_name", "dance_styles"))
    return normalize_extraction(data)
