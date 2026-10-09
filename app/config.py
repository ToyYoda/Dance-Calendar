"""Application settings, read once from environment variables."""

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env support: KEY=VALUE lines; real environment variables win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(BASE_DIR / ".env")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _phones(raw: str) -> set[str]:
    from app.phone import normalize_phone

    result = set()
    for part in raw.split(","):
        part = part.strip()
        if part:
            result.add(normalize_phone(part, _env("DEFAULT_COUNTRY_CODE", "49")))
    return result


@dataclass
class Settings:
    base_url: str = field(default_factory=lambda: _env("BASE_URL", "http://localhost:8000").rstrip("/"))
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL", f"sqlite:///{BASE_DIR / 'data' / 'dance.db'}"))
    media_dir: Path = field(default_factory=lambda: Path(_env("MEDIA_DIR", str(BASE_DIR / "data" / "media"))))
    timezone: str = field(default_factory=lambda: _env("TIMEZONE", "Europe/Berlin"))
    default_country_code: str = field(default_factory=lambda: _env("DEFAULT_COUNTRY_CODE", "49"))

    # Server-side admin whitelist (DC-42)
    admin_phones: set[str] = field(default_factory=lambda: _phones(_env("ADMIN_PHONES")))

    # AI (DC-11, DC-12): any OpenAI-compatible endpoint; default is a local Ollama
    ai_provider: str = field(default_factory=lambda: _env("AI_PROVIDER", "mock"))  # "openai" | "mock"
    ai_base_url: str = field(default_factory=lambda: _env("AI_BASE_URL", "http://localhost:11434/v1"))
    ai_api_key: str = field(default_factory=lambda: _env("AI_API_KEY", "ollama"))
    ai_model: str = field(default_factory=lambda: _env("AI_MODEL", "llama3.2-vision"))
    ai_classifier_model: str = field(default_factory=lambda: _env("AI_CLASSIFIER_MODEL", ""))
    ai_timeout: float = field(default_factory=lambda: float(_env("AI_TIMEOUT", "120")))

    # Geocoding (DC-14)
    geocoder: str = field(default_factory=lambda: _env("GEOCODER", "nominatim"))  # "nominatim" | "off"
    nominatim_url: str = field(default_factory=lambda: _env("NOMINATIM_URL", "https://nominatim.openstreetmap.org"))
    geocoder_user_agent: str = field(default_factory=lambda: _env("GEOCODER_USER_AGENT", "dance-calendar/1.0"))

    # WhatsApp (DC-10, DC-40): "console" logs messages, "cloud" uses the WhatsApp Cloud API
    messenger: str = field(default_factory=lambda: _env("MESSENGER", "console"))
    whatsapp_token: str = field(default_factory=lambda: _env("WHATSAPP_TOKEN"))
    whatsapp_phone_number_id: str = field(default_factory=lambda: _env("WHATSAPP_PHONE_NUMBER_ID"))
    whatsapp_verify_token: str = field(default_factory=lambda: _env("WHATSAPP_VERIFY_TOKEN"))
    whatsapp_app_secret: str = field(default_factory=lambda: _env("WHATSAPP_APP_SECRET"))
    whatsapp_api_version: str = field(default_factory=lambda: _env("WHATSAPP_API_VERSION", "v21.0"))

    # Show OTP codes and magic links on screen when no real messenger is configured
    dev_mode: bool = field(default_factory=lambda: _env("DEV_MODE", "0") == "1")
    cookie_secure: bool = field(default_factory=lambda: _env("COOKIE_SECURE", "0") == "1")

    # Run ingestion inline instead of as a background task (tests)
    ingest_sync: bool = field(default_factory=lambda: _env("INGEST_SYNC", "0") == "1")


settings = Settings()
