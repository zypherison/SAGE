"""STAC sourcing: find a scene and resolve the band COGs an index needs.

Reuses the Earth Search endpoint the core API already depends on, and repeats its
two measured quirks: `datetime` must be full RFC3339, and the `query` extension is
the filter that actually works. Asset naming follows Earth Search's S2 L2A
collection (`swir16` is SWIR1 at ~20 m, `swir22` is SWIR2).
"""
from __future__ import annotations

from typing import Any

import httpx

from app import config
from app.errors import INVALID_ARGUMENTS, NO_IMAGERY, PROVIDER_UNAVAILABLE, AppError
from app.geostack import raster

# band name used in formulas -> Earth Search asset key
ASSET_FOR_BAND: dict[str, str] = {
    "red": "red",
    "green": "green",
    "nir": "nir",
    "swir1": "swir16",
    "swir2": "swir22",
    "scl": "scl",
}


def _datetime(date_from: str | None, date_to: str | None) -> str | None:
    if not date_from and not date_to:
        return None
    start = f"{date_from}T00:00:00Z" if date_from else ".."
    end = f"{date_to}T23:59:59Z" if date_to else ".."
    return f"{start}/{end}"


def reflectance(item: dict[str, Any]) -> dict[str, Any]:
    """How to turn this scene's DN into reflectance, from item metadata.

    `raster:bands[].offset` is unreliable for Earth Search S2 items
    (github.com/Element84/earth-search/issues/71): it claims -0.1 even when the
    +1000 DN shift is already in the pixels. The item-level
    `earthsearch:boa_offset_applied` flag is the authority; the baseline is only
    a fallback, and "unknown" means no correction.
    """
    props = item.get("properties") or {}
    band = (item.get("assets") or {}).get("red") or {}
    meta = (band.get("raster:bands") or [{}])[0]
    scale = float(meta.get("scale") or config.S2_REFLECTANCE_SCALE)

    applied = props.get("earthsearch:boa_offset_applied")
    if applied is True:
        offset, source = 0.0, "earthsearch:boa_offset_applied=true"
    elif applied is False:
        offset, source = config.S2_LEGACY_OFFSET, "earthsearch:boa_offset_applied=false"
    else:
        baseline = props.get("s2:processing_baseline")
        try:
            legacy = baseline is not None and float(baseline) >= 4.0
        except (TypeError, ValueError):
            legacy = False
        offset = config.S2_LEGACY_OFFSET if legacy else 0.0
        source = f"baseline {baseline}" if legacy else "no flag; assuming no correction"

    return {
        "scale": scale,
        "offset": offset,
        "source": source,
        "declared_asset_offset": meta.get("offset"),
        "note": "asset-level offset is ignored; it is unreliable for this collection",
    }


def describe(item: dict[str, Any]) -> dict[str, Any]:
    props = item.get("properties") or {}
    return {
        "id": item.get("id"),
        "collection": item.get("collection"),
        "datetime": props.get("datetime"),
        "cloud_cover": props.get("eo:cloud_cover"),
        "platform": props.get("platform"),
        "bbox": item.get("bbox"),
        "tile": props.get("s2:tile_id"),
        "epsg": props.get("proj:epsg"),
        "source_url": f"{config.EARTH_SEARCH_URL}/collections/{item.get('collection')}/items/{item.get('id')}",
    }


def search(
    geometry: dict[str, Any],
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    max_cloud: float | None = 20.0,
    limit: int = 5,
    collection: str = "sentinel-2-l2a",
) -> list[dict[str, Any]]:
    if not date_from and not date_to:
        raise AppError(INVALID_ARGUMENTS, "give date_from and/or date_to for a scene search")
    body: dict[str, Any] = {
        "collections": [collection],
        "intersects": geometry,
        "limit": max(1, min(limit, 20)),
    }
    dt = _datetime(date_from, date_to)
    if dt:
        body["datetime"] = dt
    if max_cloud is not None:
        body["query"] = {"eo:cloud_cover": {"lt": float(max_cloud)}}

    try:
        resp = httpx.post(
            f"{config.EARTH_SEARCH_URL}/search",
            json=body,
            timeout=config.REQUEST_TIMEOUT,
            headers={"User-Agent": config.USER_AGENT},
        )
        resp.raise_for_status()
        items = resp.json().get("features", [])
    except httpx.HTTPError as exc:
        raise AppError(PROVIDER_UNAVAILABLE, f"Earth Search search failed: {exc}") from exc

    # Re-filter client-side: the same provider has been seen ignoring filters, so
    # a "clear scene" ask must not return a 96% cloud scene.
    if max_cloud is not None:
        items = [
            i for i in items
            if (i.get("properties", {}).get("eo:cloud_cover") is None
                or i["properties"]["eo:cloud_cover"] < max_cloud)
        ]
    return items


def assets_for(item: dict[str, Any], index: str) -> dict[str, str]:
    """Map the index's bands to COG hrefs; raise if the scene lacks one."""
    definition = raster.spec(index)
    assets = item.get("assets") or {}
    hrefs: dict[str, str] = {}
    missing: list[str] = []
    for band in (*definition["bands"], "scl"):
        key = ASSET_FOR_BAND.get(band)
        asset = assets.get(key, {}) if key else {}
        href = asset.get("href")
        if href:
            hrefs[band] = href
        elif band != "scl":  # SCL is optional: no cloud mask, but still an answer
            missing.append(band)
    if missing:
        raise AppError(
            NO_IMAGERY,
            f"scene {item.get('id')} has no asset for {', '.join(missing)}",
            missing=missing,
        )
    return hrefs


def latest(
    geometry: dict[str, Any],
    *,
    index: str = "ndvi",
    date_from: str | None = None,
    date_to: str | None = None,
    max_cloud: float | None = 20.0,
) -> tuple[dict[str, Any], dict[str, str], list[dict[str, Any]]]:
    """Newest matching scene, its assets, and the other candidates considered."""
    items = search(geometry, date_from=date_from, date_to=date_to, max_cloud=max_cloud, limit=5)
    if not items:
        raise AppError(
            NO_IMAGERY,
            "no scene matched the AOI, window and cloud limit",
            window={"from": date_from, "to": date_to, "max_cloud": max_cloud},
        )
    items.sort(key=lambda i: i.get("properties", {}).get("datetime") or "", reverse=True)
    chosen = items[0]
    return chosen, assets_for(chosen, index), [describe(i) for i in items]
