"""Flyer storage with WebP variants: thumb 200 px, preview 800 px, original (DC-16)."""

import io
import uuid
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from app.config import settings

VARIANTS = {"thumb": 200, "preview": 800}
MAX_UPLOAD_BYTES = 15 * 1024 * 1024


class InvalidImage(ValueError):
    pass


def media_root() -> Path:
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    return settings.media_dir


def open_image(data: bytes) -> Image.Image:
    if len(data) > MAX_UPLOAD_BYTES:
        raise InvalidImage("Das Bild ist größer als 15 MB.")
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise InvalidImage("Die Datei ist kein lesbares Bild.") from exc
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA" if "transparency" in img.info else "RGB")
    return img


def store_image(data: bytes) -> tuple[str, int, int]:
    """Save original + WebP variants. Returns (key, width, height); key is the file stem."""
    img = open_image(data)
    key = uuid.uuid4().hex
    root = media_root()
    img.save(root / f"{key}.webp", "WEBP", quality=90)
    for name, size in VARIANTS.items():
        variant = img.copy()
        variant.thumbnail((size, size * 3))  # limit width; tall flyers keep their aspect ratio
        variant.save(root / f"{key}_{name}.webp", "WEBP", quality=80)
    return key, img.width, img.height


def image_path(key: str, variant: str = "original") -> Path:
    suffix = "" if variant == "original" else f"_{variant}"
    return media_root() / f"{key}{suffix}.webp"


def delete_image(key: str) -> None:
    for variant in ["original", *VARIANTS]:
        image_path(key, variant).unlink(missing_ok=True)


def to_jpeg_bytes(key: str, max_side: int) -> bytes:
    """Downscaled JPEG for the vision model."""
    with Image.open(image_path(key)) as img:
        img = img.convert("RGB")
        img.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85)
        return buf.getvalue()
