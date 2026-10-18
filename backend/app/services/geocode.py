"""Geocoding with explicit disambiguation.

Adopted from alephgeo's `places.geocode` / `places.choose`:

  * every candidate carries a STABLE id in Aleph's `TYPE/ID` form (`N/123`),
    built from the OSM element type and id
  * an ambiguous name is REFUSED, not silently resolved to the first hit —
    `choose()` raises `ambiguous_place` with the candidates attached

Seminar 1 silently took `candidates[0]`. That is how "Tokyo June" became a
hairdresser: a wrong place looks exactly like a right one.
"""
from __future__ import annotations

from typing import Any

from app import config
from app.errors import AMBIGUOUS_PLACE, AppError, PLACE_NOT_FOUND, PROVIDER_UNAVAILABLE
from app.services.net import Fetch

SOURCE = "OpenStreetMap via Nominatim"
SOURCE_URL = "https://www.openstreetmap.org"

# Nominatim's osm_type -> Aleph's identity prefix
PREFIX = {"node": "N", "way": "W", "relation": "R"}


def identity(osm_type: str | None, osm_id: int | str | None) -> str | None:
    """`N/123`-style stable id, matching alephgeo's convention."""
    prefix = PREFIX.get((osm_type or "").lower())
    if not prefix or osm_id in (None, ""):
        return None
    return f"{prefix}/{int(osm_id)}"


def _normalise(item: dict[str, Any]) -> dict[str, Any]:
    bbox = None
    centre = None
    raw = item.get("boundingbox")  # [south, north, west, east] as strings
    if raw and len(raw) == 4:
        try:
            south, north, west, east = (float(v) for v in raw)
        except ValueError:
            south = north = west = east = None  # type: ignore[assignment]
        if None not in (south, north, west, east):
            bbox = [west, south, east, north]
            centre = [(west + east) / 2, (south + north) / 2]

    name = item.get("name") or item.get("display_name") or "Unnamed place"
    osm_id = item.get("osm_id")
    ident = identity(item.get("osm_type"), osm_id)
    return {
        "id": ident or f"Q/{hash(item.get('display_name') or '') & 0xFFFFFFFF:08x}",
        "name": name,
        "label": item.get("display_name") or name,
        "type": item.get("type"),
        "klass": item.get("class"),
        "importance": item.get("importance"),
        "bbox": bbox,
        "centre": centre,
        "source": SOURCE,
        "source_url": f"{SOURCE_URL}/{ident}" if ident else None,
    }


async def geocode(fetch: Fetch, query: str, *, limit: int = 8) -> list[dict[str, Any]]:
    """Resolve a place name to ranked candidates (best first)."""
    if not query or not query.strip():
        raise AppError("invalid_arguments", "Enter a nonempty place name.")

    params = {
        "q": query.strip(),
        "format": "jsonv2",
        "limit": max(1, min(limit, 20)),
        "polygon_geojson": 0,
        "addressdetails": 0,
    }
    try:
        raw, _cached = await fetch.get_json(
            f"{config.NOMINATIM_URL}/search", params=params, ttl=config.GEOCODE_CACHE_TTL
        )
    except AppError:
        raise
    except Exception as exc:  # transport layer
        raise AppError(PROVIDER_UNAVAILABLE, f"Geocoder request failed: {exc}") from exc

    if not isinstance(raw, list):
        raise AppError(PROVIDER_UNAVAILABLE, "The geocoder returned an unreadable response.")

    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        candidate = _normalise(item)
        if candidate["id"] in seen:
            continue
        seen.add(candidate["id"])
        out.append(candidate)

    out.sort(key=lambda c: c.get("importance") or 0.0, reverse=True)
    return out


def choose(
    candidates: list[dict[str, Any]],
    *,
    place_id: str | None = None,
    best_match: bool = False,
) -> dict[str, Any]:
    """Select one candidate or refuse. Mirrors alephgeo's `choose()`."""
    if not candidates:
        raise AppError(
            PLACE_NOT_FOUND,
            "No matching place was found. Include a region or country, or draw the AOI instead.",
        )

    if place_id is not None:
        for candidate in candidates:
            if candidate["id"] == place_id:
                return candidate
        raise AppError(
            PLACE_NOT_FOUND,
            f"Place id '{place_id}' was not among the candidates for this name.",
            candidates=candidates,
        )

    if len(candidates) == 1 or best_match:
        return candidates[0]

    raise AppError(
        AMBIGUOUS_PLACE,
        f"{len(candidates)} places match that name. Choose one by id.",
        candidates=candidates,
    )
