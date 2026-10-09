"""Address completion via OpenStreetMap Nominatim (DC-14)."""

import logging
from dataclasses import dataclass, field

import requests

from app.config import settings

log = logging.getLogger(__name__)
UNIQUE_SHARE = 0.9


@dataclass
class GeoResult:
    status: str  # "unique" | "ambiguous" | "none"
    best: dict | None = None
    candidates: list[dict] = field(default_factory=list)


def _candidate(item: dict) -> dict:
    addr = item.get("address", {})
    street = " ".join(p for p in [addr.get("road") or addr.get("pedestrian"), addr.get("house_number")] if p)
    city = addr.get("city") or addr.get("town") or addr.get("village") or addr.get("municipality")
    return {
        "label": item.get("display_name", "")[:200],
        "address": street or None,
        "postcode": addr.get("postcode"),
        "city": city,
        "lat": float(item["lat"]),
        "lng": float(item["lon"]),
        "importance": float(item.get("importance") or 0.0),
    }


def search(location_name: str | None, address: str | None, city: str | None) -> GeoResult:
    if settings.geocoder != "nominatim":
        return GeoResult("none")
    query = ", ".join(p for p in [location_name, address, city] if p)
    if not query:
        return GeoResult("none")
    try:
        resp = requests.get(
            f"{settings.nominatim_url}/search",
            params={"q": query, "format": "jsonv2", "addressdetails": 1, "limit": 5},
            headers={"User-Agent": settings.geocoder_user_agent, "Accept-Language": "de"},
            timeout=10,
        )
        resp.raise_for_status()
        items = resp.json()
    except Exception:
        log.exception("Geocoding failed for %r", query)
        return GeoResult("none")
    return rank([_candidate(i) for i in items], city)


def rank(candidates: list[dict], city: str | None) -> GeoResult:
    """A hit is 'unique' when it carries more than 90 % of the total relevance."""
    if city:
        same_city = [c for c in candidates if c["city"] and c["city"].lower() == city.lower()]
        candidates = same_city or candidates
    if not candidates:
        return GeoResult("none")
    if len(candidates) == 1:
        return GeoResult("unique", candidates[0], candidates)
    weights = [max(c["importance"], 0.01) for c in candidates]
    best = max(range(len(candidates)), key=lambda i: weights[i])
    share = weights[best] / sum(weights)
    if share > UNIQUE_SHARE:
        return GeoResult("unique", candidates[best], candidates)
    return GeoResult("ambiguous", None, candidates)
