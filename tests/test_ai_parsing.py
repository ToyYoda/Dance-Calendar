"""Model answers in the shapes small local models actually produce."""

import json
from types import SimpleNamespace

import pytest

from app import ai, images
from app.ai import AIError, _parse_json
from app.config import settings
from tests.conftest import flyer_bytes

FLAT = {"title": "Dolce Vita", "dance_styles": ["Salsa", "Bachata"], "start": "2026-10-03T20:00",
        "location_name": "Gegenüber dem ICE-Bahnhof Wilhelmshöhe", "address": "Wilhelmshöher Allee 254",
        "postcode": "34119", "city": "Kassel", "price": None,
        "confidence": {"title": 0.95, "dance_styles": 0.9, "start": 0.9, "location_name": 0.6, "city": 0.95}}


@pytest.mark.parametrize("raw", [
    json.dumps(FLAT),
    "Hier sind die Daten:\n```json\n" + json.dumps(FLAT, indent=2) + "\n```\nViel Spaß!",
    "<think>Der Flyer {zeigt} ...</think>" + json.dumps(FLAT),
    json.dumps({"event": FLAT}),
    json.dumps(FLAT, indent=2).replace('"Kassel"', '"Kassel",').replace('"Kassel",\n', '"Kassel",\n'),
])
def test_parse_variants(raw):
    data = _parse_json(raw, expect=("title", "start"))
    assert data["title"] == "Dolce Vita"


def test_trailing_commas_are_repaired():
    assert _parse_json('{"title": "X", "city": "Kassel",}', expect=("title",))["city"] == "Kassel"


def test_garbage_reports_beginning_of_answer():
    with pytest.raises(AIError, match="Anfang der Antwort: «Ich kann leider"):
        _parse_json("Ich kann leider keine Daten finden.", expect=("title",))


def test_flat_answer_is_normalized_with_confidences():
    ex = ai.normalize_extraction(FLAT)
    assert ex.values["title"] == "Dolce Vita"
    assert ex.values["dance_styles"] == ["Salsa", "Bachata"]
    assert ex.values["address"] == "Wilhelmshöher Allee 254" and ex.values["postcode"] == "34119"
    assert ex.confidence["location_name"] == 0.6
    assert ex.confidence["address"] == 0.5  # missing confidence → neutral
    assert "price" not in ex.values


class FakeCompletions:
    def __init__(self, answers):
        self.answers = list(answers)
        self.formats = []

    def create(self, **kw):
        self.formats.append(kw["response_format"]["type"])
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        content, finish = answer if isinstance(answer, tuple) else (answer, "stop")
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish)])


@pytest.fixture
def fake_model(monkeypatch):
    """OpenAI-compatible server (not Ollama)."""
    monkeypatch.setattr(settings, "ai_provider", "openai")
    monkeypatch.setattr(settings, "ai_base_url", "http://llm.example:8000/v1")

    def install(*answers):
        completions = FakeCompletions(answers)
        monkeypatch.setattr(ai, "_client", lambda: SimpleNamespace(chat=SimpleNamespace(completions=completions)))
        return completions

    key, _, _ = images.store_image(flyer_bytes())
    return install, key


def test_extract_uses_json_schema(fake_model):
    install, key = fake_model
    calls = install(json.dumps(FLAT))
    ex = ai.extract(key)
    assert ex.values["city"] == "Kassel"
    assert calls.formats == ["json_schema"]


def test_extract_falls_back_when_schema_unsupported(fake_model):
    install, key = fake_model
    calls = install(ValueError("response_format json_schema not supported"), json.dumps(FLAT))
    assert ai.extract(key).values["title"] == "Dolce Vita"
    assert calls.formats == ["json_schema", "json_object"]


def test_extract_retries_plain_json_after_unusable_answer(fake_model):
    install, key = fake_model
    calls = install("Sorry, ich sehe nur ein Bild.", json.dumps(FLAT))
    assert ai.extract(key).values["title"] == "Dolce Vita"
    assert calls.formats == ["json_schema", "json_object"]


def test_truncated_answer_is_explained(fake_model):
    install, key = fake_model
    install(('{"title": "Dolce Vita", "dance_sty', "length"))
    with pytest.raises(AIError, match="abgeschnitten"):
        ai.extract(key)


def test_unreachable_server(fake_model):
    from openai import APIConnectionError

    install, key = fake_model
    install(APIConnectionError(request=SimpleNamespace(method="POST", url="http://localhost:11434/v1")))
    with pytest.raises(AIError, match="nicht erreichbar"):
        ai.extract(key)


# --- Ollama native API ----------------------------------------------------------


class FakeOllama:
    def __init__(self, answers):
        self.answers = list(answers)
        self.payloads = []

    def __call__(self, url, json=None, timeout=None):
        assert url == "http://localhost:11434/api/chat"
        self.payloads.append(json)
        status, body = self.answers.pop(0)
        return SimpleNamespace(status_code=status, text=str(body), json=lambda: body)


@pytest.fixture
def fake_ollama(monkeypatch):
    import requests

    monkeypatch.setattr(settings, "ai_provider", "openai")
    monkeypatch.setattr(settings, "ai_base_url", "http://localhost:11434/v1")

    def install(*answers):
        fake = FakeOllama(answers)
        monkeypatch.setattr(requests, "post", fake)
        return fake

    key, _, _ = images.store_image(flyer_bytes())
    return install, key


def _ok(content, reason="stop", thinking=""):
    return 200, {"message": {"content": content, "thinking": thinking}, "done_reason": reason}


def test_ollama_native_disables_thinking_and_uses_schema(fake_ollama):
    install, key = fake_ollama
    fake = install(_ok(json.dumps(FLAT)))
    ex = ai.extract(key)
    assert ex.values["city"] == "Kassel"
    payload = fake.payloads[0]
    assert payload["think"] is False and payload["stream"] is False
    assert payload["format"]["type"] == "object"  # JSON schema, not just "json"
    assert payload["messages"][0]["images"]
    assert payload["options"]["num_predict"] == 3000


def test_ollama_model_without_thinking_mode(fake_ollama):
    install, key = fake_ollama
    fake = install((400, '{"error":"model does not support thinking"}'), _ok(json.dumps(FLAT)))
    assert ai.extract(key).values["title"] == "Dolce Vita"
    assert "think" not in fake.payloads[1]


def test_ollama_truncation_explains_thinking(fake_ollama):
    install, key = fake_ollama
    install(_ok('{"title": "Dol', "length", thinking="Hmm " * 500), _ok('{"title"', "length", thinking="x" * 10))
    with pytest.raises(AIError, match="nachgedacht"):
        ai.extract(key)


def test_ollama_falls_back_to_plain_json(fake_ollama):
    install, key = fake_ollama
    fake = install(_ok("Ich sehe einen Flyer."), _ok(json.dumps(FLAT)))
    assert ai.extract(key).values["title"] == "Dolce Vita"
    assert fake.payloads[1]["format"] == "json"


def test_ollama_unreachable(fake_ollama, monkeypatch):
    import requests

    _, key = fake_ollama

    def down(*a, **kw):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(requests, "post", down)
    with pytest.raises(AIError, match="nicht erreichbar"):
        ai.extract(key)
