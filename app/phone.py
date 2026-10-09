"""Phone number normalisation to E.164 digits without '+'."""

import re


class InvalidPhone(ValueError):
    pass


def normalize_phone(raw: str, default_cc: str | None = None) -> str:
    if default_cc is None:
        from app.config import settings

        default_cc = settings.default_country_code
    s = re.sub(r"[\s\-/().]", "", raw or "")
    if s.startswith("+"):
        s = s[1:]
    elif s.startswith("00"):
        s = s[2:]
    elif s.startswith("0"):
        s = default_cc + s[1:]
    if not s.isdigit() or not 8 <= len(s) <= 15:
        raise InvalidPhone("Bitte eine gültige Telefonnummer eingeben.")
    return s


def format_phone(digits: str) -> str:
    return "+" + digits if digits else ""
