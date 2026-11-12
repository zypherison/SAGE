"""ArcGIS Sentinel-2 ImageServer adapter.

This is the only source in the prototype that can compute index statistics
server-side without authentication. Verified live (SEMINAR2.md §0.1).

LIMITATION (must be shown in the UI): rolling ~14-month window, daily updates.
A query for 2024 correctly returns nothing; that is not a bug, it is coverage.
"""
from __future__ import annotations

import json
import urllib.parse
from datetime import date, datetime, timezone
from typing import Any

from app import config
from app.errors import AppError, PROVIDER_UNAVAILABLE
from app.schemas import Scene
from app.services.net import Fetch

NAME = "arcgis-sentinel2"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _epoch_ms(day: str, *, end_of_day: bool = False) -> int:
    """Midnight UTC, or 23:59:59 UTC when `end_of_day`.

    The end matters: `date_from == date_to` ("imagery for 8 November") would
    otherwise become a zero-length window and return nothing at all. That made a
    single-day request fail while the same day sat inside a range.
    """
    d = date.fromisoformat(day)
    if end_of_day:
        moment = datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=timezone.utc)
    else:
        moment = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    return int(moment.timestamp() * 1000)


def _ms_to_iso(ms: float | int | None) -> str | None:
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _envelope_from_ring(ring: list[list[float]]) -> list[float] | None:
    pts = [p for p in ring if len(p) >= 2]
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return [min(xs), min(ys), max(xs), max(ys)]


# --------------------------------------------------------------------------
# catalogue search
# --------------------------------------------------------------------------
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
    if mission and mission != "sentinel-2-l2a":
        return []  # this service is Sentinel-2 only

    where: list[str] = ["1=1"]
    if max_cloud is not None:
        where.append(f"cloudcover < {float(max_cloud)}")

    params: dict[str, Any] = {
        "where": " AND ".join(where),
        "outFields": "objectid,name,acquisitiondate,cloudcover,tile_id",
        "returnGeometry": "true",
        "outSR": "4326",
        "resultRecordCount": str(max(1, min(limit, config.MAX_LIMIT))),
        "orderByFields": "acquisitiondate DESC",
        "f": "json",
    }

    if geometry is not None:
        params["geometry"] = _geojson_to_esri_geometry(geometry)
        params["geometryType"] = (
            "esriGeometryPolygon" if geometry.get("type") == "Polygon" else "esriGeometryEnvelope"
        )
        params["inSR"] = "4326"
        params["spatialRel"] = "esriSpatialRelIntersects"
    elif bbox is not None:
        params["geometry"] = f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}"
        params["geometryType"] = "esriGeometryEnvelope"
        params["inSR"] = "4326"
        params["spatialRel"] = "esriSpatialRelIntersects"

    if date_from or date_to:
        start = _epoch_ms(date_from) if date_from else _epoch_ms("2015-05-01")
        end = (
            _epoch_ms(date_to, end_of_day=True)
            if date_to
            else int(datetime.now(timezone.utc).timestamp() * 1000)
        )
        params["time"] = f"{start},{end}"

    payload, _cached = await fetch.get_json(f"{config.ARCGIS_URL}/query", params=params)
    if "error" in (payload or {}):
        raise AppError(PROVIDER_UNAVAILABLE, f"ArcGIS error: {payload['error']}")

    scenes: list[Scene] = []
    for feat in (payload or {}).get("features", []):
        attrs = feat.get("attributes") or {}
        scene_bbox = None
        rings = (feat.get("geometry") or {}).get("rings") or []
        if rings and rings[0]:
            scene_bbox = _envelope_from_ring(rings[0])
        scenes.append(
            Scene(
                id=str(attrs.get("objectid")),
                provider=NAME,
                mission="sentinel-2-l2a",
                datetime=_ms_to_iso(attrs.get("acquisitiondate")),
                # Units verified by a discriminating count probe: `where cloudcover<1`
                # returns 4,648,959 while `<20` and `<100` both return 4,951,994.
                # A 0-1 fraction would make all three equal, so this is PERCENT.
                cloud_cover=attrs.get("cloudcover"),
                bbox=scene_bbox,
                resolution_m=10.0,
                thumbnail=None,
                index_support=True,
                assets=[],
                source_url=config.ARCGIS_URL,
            )
        )
    return scenes


def _geojson_to_esri_geometry(geometry: dict[str, Any]) -> str:
    """Convert a GeoJSON Polygon/MultiPolygon to an Esri JSON geometry string."""
    gtype = geometry.get("type")
    if gtype == "Polygon":
        rings = [[[float(x), float(y)] for x, y in ring] for ring in geometry.get("coordinates", [])]
        return str({"rings": rings, "spatialReference": {"wkid": 4326}})
    if gtype == "MultiPolygon":
        rings: list[list[list[float]]] = []
        for poly in geometry.get("coordinates", []):
            for ring in poly:
                rings.append([[float(x), float(y)] for x, y in ring])
        return str({"rings": rings, "spatialReference": {"wkid": 4326}})
    bbox = geometry.get("bbox")
    if bbox:
        return f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}"
    raise ValueError("Unsupported geometry for ArcGIS")


# --------------------------------------------------------------------------
# deterministic index statistics
# --------------------------------------------------------------------------
async def index_statistics(
    fetch: Fetch,
    *,
    bbox: list[float],
    raster_function: str,
    objectid: str | None = None,
) -> dict[str, Any] | None:
    """Mean/min/max/sd over the AOI using the service's own raster function.

    Pass `objectid` to pin one acquisition. Verified 2026-11-12: the endpoint
    honours `lockRasterIds`, so a per-date series is real acquisitions rather
    than the daily composite (composite 0.1297 vs per-date 0.0975/0.0248).
    """
    geometry = str(
        {
            "xmin": bbox[0], "ymin": bbox[1], "xmax": bbox[2], "ymax": bbox[3],
            "spatialReference": {"wkid": 4326},
        }
    )
    params = {
        "geometry": geometry,
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "renderingRule": str({"rasterFunction": raster_function}),
        "f": "json",
    }
    if objectid:
        ids: list[Any] = [int(objectid)] if str(objectid).isdigit() else [objectid]
        params["mosaicRule"] = json.dumps(
            {"mosaicMethod": "esriMosaicLockRaster", "lockRasterIds": ids}
        )
    payload, _cached = await fetch.get_json(
        f"{config.ARCGIS_URL}/computeStatisticsHistograms", params=params
    )
    if "error" in (payload or {}):
        raise AppError(PROVIDER_UNAVAILABLE, f"ArcGIS stats error: {payload['error']}")

    stats_list = (payload or {}).get("statistics") or []
    if not stats_list:
        return None
    s = stats_list[0]
    hist = ((payload or {}).get("histograms") or [{}])[0]
    return {
        "min": s.get("min"),
        "max": s.get("max"),
        "mean": s.get("mean"),
        "std": s.get("standardDeviation"),
        "median": s.get("median"),
        "count": s.get("count") or 0,
        "histogram": {
            "counts": hist.get("counts") or [],
            "min": hist.get("min"),
            "max": hist.get("max"),
        } if hist else None,
    }


def export_params(
    bbox: list[float],
    *,
    objectid: str | None = None,
    rendering_rule: str | None = None,
    size: tuple[int, int] = (1024, 768),
) -> dict[str, Any]:
    """Query parameters for one `exportImage` render (shared by URL and bytes)."""
    params: dict[str, Any] = {
        "bbox": f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}",
        "bboxSR": "4326",
        "imageSR": "4326",
        "size": f"{int(size[0])},{int(size[1])}",
        "format": "png",
        "f": "image",
    }
    if objectid:
        ids: list[Any] = [int(objectid)] if str(objectid).isdigit() else [objectid]
        params["mosaicRule"] = json.dumps(
            {"mosaicMethod": "esriMosaicLockRaster", "lockRasterIds": ids}
        )
    if rendering_rule:
        params["renderingRule"] = json.dumps({"rasterFunction": rendering_rule})
    return params


def scene_url(
    bbox: list[float],
    *,
    objectid: str,
    rendering_rule: str | None = None,
    size: tuple[int, int] = (1024, 768),
) -> str:
    """Direct PNG URL for one pinned acquisition, for the before/after panels."""
    query = urllib.parse.urlencode(
        export_params(bbox, objectid=objectid, rendering_rule=rendering_rule, size=size)
    )
    return f"{config.ARCGIS_URL}/exportImage?{query}"


async def export_scene(
    fetch: Fetch,
    *,
    bbox: list[float],
    objectid: str,
    rendering_rule: str | None = None,
    size: tuple[int, int] = (1024, 768),
) -> bytes:
    """Render ONE pinned acquisition over the AOI, as PNG bytes.

    `time` is deliberately NOT used to select the date: a day-window `time`
    returned a 4 KB blank for both true colour and NDVI, byte-identical, while
    `lockRasterIds` returned the actual scene (1.87 MB / 349 KB at 1024x768).
    Pinning by objectid is both correct and better provenance, because the
    catalogue id of the rendered acquisition is recorded in the download
    manifest. See app/services/download.py.
    """
    params = export_params(
        bbox, objectid=objectid, rendering_rule=rendering_rule, size=size
    )

    blob, _cached = await fetch.get_bytes(f"{config.ARCGIS_URL}/exportImage", params=params)
    if blob[:1] == b"{":
        raise AppError(
            PROVIDER_UNAVAILABLE, f"ArcGIS returned an error body instead of a PNG: {blob[:200]!r}"
        )
    return blob


def visual_url(bbox: list[float], raster_function: str, size: int = 512) -> str:
    """PNG URL for an index rendering over the AOI (used directly by the UI)."""
    params = {
        "bbox": f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}",
        "bboxSR": "4326",
        "imageSR": "4326",
        "size": f"{size},{size}",
        "format": "png",
        "renderingRule": str({"rasterFunction": raster_function}),
        "f": "image",
    }
    return f"{config.ARCGIS_URL}/exportImage?{urllib.parse.urlencode(params)}"
