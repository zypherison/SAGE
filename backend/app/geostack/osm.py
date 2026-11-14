"""OSM features via the Overpass API, for marking things a raster cannot name.

Roads and monuments are not in the Sentinel-2 or Dynamic World catalogues, and
they are exactly what people ask for by name. Overpass is keyless, rate-limited
and occasionally slow, so every call is bounded: a hard element limit, a server
timeout, one retry-free request, and an honest `truncated` flag.

Only nodes and ways are converted. Relations (route and site relations) come back
with member geometry that would need fully resolving to draw, so they are skipped
rather than rendered as a broken line. The OSM tags are the source of completeness:
a road that is untagged in OSM simply is not here.
"""
from __future__ import annotations

from typing import Any

import httpx

from app import config
from app.errors import INVALID_ARGUMENTS, PROVIDER_UNAVAILABLE, AppError

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Bbox token order for Overpass is (south, west, north, east).
CATEGORIES: dict[str, str] = {
    "roads": (
        'way["highway"~"^(motorway|trunk|primary|secondary|tertiary|residential|'
        'unclassified|living_street|service|track)$"]({bbox});'
    ),
    "monuments": (
        'node["historic"~"^(monument|memorial|archaeological_site)$"]({bbox});'
        'way["historic"~"^(monument|memorial|archaeological_site)$"]({bbox});'
        'node["tourism"="attraction"]["name"]({bbox});'
        'way["tourism"="attraction"]["name"]({bbox});'
    ),
}

CATEGORY_LABELS = {"roads": "Roads", "monuments": "Monuments"}


def _bbox(geometry: dict[str, Any]) -> str:
    rings = geometry.get("coordinates") or []
    if not rings or not rings[0]:
        raise AppError(INVALID_ARGUMENTS, "geometry has no exterior ring")
    ring = rings[0]
    lons = [float(point[0]) for point in ring]
    lats = [float(point[1]) for point in ring]
    return f"{min(lats):.6f},{min(lons):.6f},{max(lats):.6f},{max(lons):.6f}"


def _feature(element: dict[str, Any], category: str) -> dict[str, Any] | None:
    tags = element.get("tags") or {}
    kind = element.get("type")
    if kind == "node" and "lat" in element:
        geometry = {"type": "Point", "coordinates": [element["lon"], element["lat"]]}
    elif kind == "way" and element.get("geometry"):
        coords = [
            [point["lon"], point["lat"]]
            for point in element["geometry"]
            if "lon" in point and "lat" in point
        ]
        if len(coords) < 2:
            return None
        geometry = {"type": "LineString", "coordinates": coords}
    else:
        return None

    return {
        "type": "Feature",
        "geometry": geometry,
        "properties": {
            "name": tags.get("name"),
            "kind": tags.get("highway") or tags.get("historic") or tags.get("tourism"),
            "category": category,
            "osm": f"{kind}/{element.get('id')}",
        },
    }


def fetch(
    geometry: dict[str, Any],
    category: str,
    *,
    limit: int = 3000,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    if category not in CATEGORIES:
        raise AppError(
            INVALID_ARGUMENTS,
            f"unknown feature category {category!r}; try {', '.join(CATEGORIES)}",
            categories=sorted(CATEGORIES),
        )
    limit = max(1, min(int(limit), 5000))
    query = (
        f"[out:json][timeout:{min(timeout_seconds, 120)}];"
        f"({CATEGORIES[category].format(bbox=_bbox(geometry))});"
        f"out geom {limit};"
    )

    try:
        # GET, not POST: overpass-api.de answers a form POST without an explicit
        # content type with 406. Measured 2026-11-14.
        response = httpx.get(
            OVERPASS_URL,
            params={"data": query},
            timeout=timeout_seconds,
            headers={"User-Agent": config.USER_AGENT},
        )
        response.raise_for_status()
        elements = response.json().get("elements", [])
    except httpx.HTTPError as exc:
        raise AppError(PROVIDER_UNAVAILABLE, f"Overpass request failed: {exc}") from exc
    except ValueError as exc:
        raise AppError(PROVIDER_UNAVAILABLE, "Overpass returned a non-JSON response") from exc

    features = [feature for element in elements if (feature := _feature(element, category))]
    return {
        "type": "FeatureCollection",
        "features": features,
        "category": category,
        "label": CATEGORY_LABELS[category],
        "count": len(features),
        "truncated": len(elements) >= limit,
        "limit": limit,
        "source": "OpenStreetMap via Overpass API",
        "licence": "ODbL 1.0, © OpenStreetMap contributors",
        "note": "Completeness depends on OSM tags for this area; relations are skipped.",
    }
