"""Microsoft Planetary Computer STAC adapter.

Search is open; reading assets needs a SAS token we deliberately do not proxy.
The client is therefore told to link out rather than download.
"""
from __future__ import annotations

from typing import Any

from app import config
from app.schemas import Scene
from app.services.net import Fetch

NAME = "planetary-computer"

COLLECTIONS = ["sentinel-2-l2a", "landsat-c2-l2"]


def _dt_range(date_from: str | None, date_to: str | None) -> str | None:
    if not date_from and not date_to:
        return None
    start = f"{date_from}T00:00:00Z" if date_from else ".."
    end = f"{date_to}T23:59:59Z" if date_to else ".."
    return f"{start}/{end}"


def _pick_thumb(assets: dict[str, Any]) -> str | None:
    for key in ("rendered_preview", "thumbnail", "visual", "tci"):
        asset = assets.get(key)
        if asset and str(asset.get("href", "")).startswith("http"):
            return asset["href"]
    return None


def _normalize(item: dict[str, Any]) -> Scene:
    props = item.get("properties") or {}
    assets = item.get("assets") or {}
    return Scene(
        id=str(item.get("id")),
        provider=NAME,
        mission=str(item.get("collection") or "unknown"),
        datetime=props.get("datetime"),
        cloud_cover=props.get("eo:cloud_cover"),
        bbox=item.get("bbox"),
        resolution_m=props.get("gsd"),
        thumbnail=_pick_thumb(assets),
        index_support=False,
        assets=sorted(assets.keys()),
        source_url=f"https://planetarycomputer.microsoft.com/api/stac/v1/collections/"
                   f"{item.get('collection')}/items/{item.get('id')}",
    )


async def search(
    fetch: Fetch,
    *,
    bbox: list[float] | None,
    geometry: dict[str, Any] | None,
    date_from: str | None,
    date_to: str | None,
    max_cloud: float | None,
    mission: str | None,
    limit: int,
) -> list[Scene]:
    colls = [mission] if mission in COLLECTIONS else COLLECTIONS
    body: dict[str, Any] = {
        "collections": colls,
        "limit": max(1, min(limit, config.MAX_LIMIT)),
    }
    if geometry is not None:
        body["intersects"] = geometry
    elif bbox is not None:
        body["bbox"] = bbox

    dt = _dt_range(date_from, date_to)
    if dt:
        body["datetime"] = dt
    if max_cloud is not None:
        body["query"] = {"eo:cloud_cover": {"lt": float(max_cloud)}}

    payload, _cached = await fetch.post_json(f"{config.PLANETARY_URL}/search", json_body=body)
    items = (payload or {}).get("features", [])

    scenes = [_normalize(i) for i in items]
    if max_cloud is not None:
        scenes = [s for s in scenes if (s.cloud_cover is None or s.cloud_cover < max_cloud)]
    return scenes
