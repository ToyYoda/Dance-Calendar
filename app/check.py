"""Diagnose the AI setup:  python -m app.check [pfad/zum/flyer.jpg]"""

import json
import os
import sys
import time
from pathlib import Path

from app.config import BASE_DIR, DOTENV_LOADED, DOTENV_PATH, SYSTEM_ENV_KEYS, _read_dotenv, settings


def line(ok: bool | None, text: str) -> None:
    mark = {True: "OK  ", False: "FEHLER", None: "INFO"}[ok]
    print(f"[{mark}] {text}")


def main() -> int:
    print(f"Projektordner: {BASE_DIR}")
    if DOTENV_LOADED:
        line(True, f".env gefunden: {DOTENV_PATH}")
    else:
        line(False, f"keine .env gefunden unter {DOTENV_PATH}")
        stray = [p.name for p in BASE_DIR.iterdir() if p.name.lower().startswith(".env") and p.name != ".env.example"]
        if stray:
            line(None, f"ähnliche Dateien: {', '.join(stray)} – die Datei muss genau '.env' heißen")
        else:
            line(None, "Lege sie an mit:  copy .env.example .env")

    if "AI_PROVIDER" in SYSTEM_ENV_KEYS:
        line(False, f"AI_PROVIDER ist als Windows-/System-Umgebungsvariable gesetzt ({os.environ['AI_PROVIDER']!r}) "
                    "und hat Vorrang vor der .env. Entfernen mit:  [Environment]::SetEnvironmentVariable('AI_PROVIDER', $null, 'User')"
                    "  und PowerShell neu öffnen.")
    elif DOTENV_LOADED:
        lines = [ln.strip() for ln in DOTENV_PATH.read_text(encoding="utf-8-sig").splitlines()
                 if ln.strip().startswith("AI_PROVIDER")]
        line(None, f"AI_PROVIDER-Zeilen in .env: {lines or 'keine'} → gilt: {_read_dotenv(DOTENV_PATH).get('AI_PROVIDER', '–')!r}")
    line(None, f"KI-Modus: {settings.ai_provider}")
    if settings.ai_provider == "mock":
        line(False, "Testmodus aktiv – es kommen feste Beispieldaten. In .env: AI_PROVIDER=openai")
        return 1

    line(None, f"Modell: {settings.ai_model}  ·  Adresse: {settings.ai_base_url}")
    from openai import OpenAI

    client = OpenAI(base_url=settings.ai_base_url, api_key=settings.ai_api_key or "none", timeout=15)
    try:
        names = [m.id for m in client.models.list()]
    except Exception as exc:
        line(False, f"Ollama nicht erreichbar: {exc}")
        line(None, "Läuft Ollama? (Ollama-App starten oder 'ollama serve')")
        return 1
    line(True, "Ollama erreichbar")
    if settings.ai_model not in names:
        line(False, f"Modell '{settings.ai_model}' nicht installiert. Vorhanden: {', '.join(names) or '–'}")
        return 1
    line(True, f"Modell '{settings.ai_model}' installiert")

    if len(sys.argv) < 2:
        line(None, "Für einen echten Test:  python -m app.check C:\\pfad\\zum\\flyer.jpg")
        return 0

    from app import ai, images

    data = Path(sys.argv[1]).read_bytes()
    key, _, _ = images.store_image(data)
    try:
        started = time.perf_counter()
        ex = ai.extract(key)
        line(True, f"Flyer ausgelesen in {time.perf_counter() - started:.1f} s")
        print(json.dumps({k: {"wert": str(v), "sicherheit": ex.confidence.get(k)} for k, v in ex.values.items()},
                         ensure_ascii=False, indent=2))
    except ai.AIError as exc:
        line(False, str(exc))
        return 1
    finally:
        images.delete_image(key)
    return 0


if __name__ == "__main__":
    sys.exit(main())
