"""HTTP surface for the optional geospatial stack, mounted at /api/geo.

Handlers are synchronous on purpose: rasterio and geopandas block, so FastAPI
runs these in its threadpool instead of stalling the event loop. No handler
imports a heavy module at module scope, and a missing one becomes a 503 with the
package named, so this router can be mounted on a machine without GDAL.
"""
from __future__ import annotations

import hashlib
import importlib
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app import config
from app.errors import AOI_TOO_LARGE, GEOSTACK_UNAVAILABLE, INVALID_ARGUMENTS, AppError
from app.geostack import capabilities
from app.services import evidence

router = APIRouter(prefix="/api/geo", tags=["geostack"])


def _load(name: str) -> Any:
    """Import a geostack submodule, translating a missing dependency to 503."""
    try:
        return importlib.import_module(f"app.geostack.{name}")
    except ImportError as exc:
        raise AppError(
            GEOSTACK_UNAVAILABLE,
            f"{name} needs a package that is not installed: {exc}",
            module=name,
            install="pip install -r backend/requirements-geo.txt",
        ) from exc


def _listing(directory: str, pattern: str) -> list[dict[str, Any]]:
    root = Path(directory)
    if not root.exists():
        return []
    return [{"name": p.name, "bytes": p.stat().st_size} for p in sorted(root.glob(pattern))]


class AoiRequest(BaseModel):
    geometry: dict[str, Any]


class SceneRequest(BaseModel):
    geometry: dict[str, Any]
    date_from: str | None = None
    date_to: str | None = None
    max_cloud: float | None = 20.0
    index: str = "ndvi"


class RasterIndexRequest(BaseModel):
    geometry: dict[str, Any]
    index: str = "ndvi"
    date_from: str | None = None
    date_to: str | None = None
    max_cloud: float | None = 20.0
    cloud_mask: bool = True
    preview: bool = False


class PopulationRequest(BaseModel):
    geometry: dict[str, Any]
    iso3: str = "IND"
    year: int = 2020


class CompositeRequest(BaseModel):
    geometry: dict[str, Any]
    index: str = "ndvi"
    date_from: str
    date_to: str
    max_cloud: float = 20.0
    scale: int = 10


class MapRequest(BaseModel):
    geometry: dict[str, Any]
    title: str = Field("SAGE AOI", max_length=120)
    index: str = "ndvi"
    date_from: str | None = None
    date_to: str | None = None
    landcover: bool = False


class LandcoverRequest(BaseModel):
    geometry: dict[str, Any]
    date_from: str
    date_to: str
    scale: int = 10
    buildings: bool = True


class FeatureRequest(BaseModel):
    geometry: dict[str, Any]
    category: str
    limit: int = 3000


class BuildingsRequest(BaseModel):
    geometry: dict[str, Any]
    confidence: float = 0.7
    limit: int = 3000
    heights: bool = True


class DatesRequest(BaseModel):
    geometry: dict[str, Any]
    date_from: str | None = None
    date_to: str | None = None
    max_cloud: float | None = 20.0


class LandcoverOverlayRequest(LandcoverRequest):
    size: int = 1600
    include_buildings: bool = True
    buildings_limit: int = 3000
    focus: int | None = None


def _check_area(vector: Any, geometry: dict[str, Any]) -> dict[str, Any]:
    metrics = vector.analyze(geometry)
    if metrics["area_km2"] > config.MAX_AOI_AREA_KM2:
        raise AppError(
            AOI_TOO_LARGE,
            f"AOI is {metrics['area_km2']:.1f} km², above the {config.MAX_AOI_AREA_KM2:.0f} km² limit",
            area_km2=metrics["area_km2"],
        )
    return metrics


# --------------------------------------------------------------------------
# capability discovery (must work with nothing installed)
# --------------------------------------------------------------------------
@router.get("/capabilities")
def stack_capabilities() -> dict[str, Any]:
    report = capabilities.report()
    report["worldpop_cached"] = _listing(config.WORLDPOP_CACHE_DIR, "*.tif")
    report["maps"] = _listing(config.MAP_DIR, "*.html")[:5]
    return report


# --------------------------------------------------------------------------
# vector
# --------------------------------------------------------------------------
@router.post("/aoi")
def aoi_metrics(body: AoiRequest) -> dict[str, Any]:
    vector = _load("vector")
    metrics = vector.analyze(body.geometry)
    if metrics["area_km2"] > config.MAX_AOI_AREA_KM2:
        raise AppError(AOI_TOO_LARGE, "AOI is above the configured area limit", area_km2=metrics["area_km2"])
    return {"status": "ok", **metrics, "source": "geopandas/pyproj, WGS84 ellipsoid"}


# --------------------------------------------------------------------------
# sourcing
# --------------------------------------------------------------------------
@router.post("/scene")
def find_scene(body: SceneRequest) -> dict[str, Any]:
    vector = _load("vector")
    stac = _load("stac")
    _check_area(vector, body.geometry)
    items = stac.search(
        body.geometry,
        date_from=body.date_from,
        date_to=body.date_to,
        max_cloud=body.max_cloud,
        limit=8,
    )
    if not items:
        return {"status": "no_imagery", "count": 0, "candidates": []}
    items.sort(key=lambda i: i.get("properties", {}).get("datetime") or "", reverse=True)
    return {
        "status": "ok",
        "count": len(items),
        "candidates": [stac.describe(i) for i in items],
        "assets": sorted((items[0].get("assets") or {}).keys()),
        "query": {"from": body.date_from, "to": body.date_to, "max_cloud": body.max_cloud},
    }


# --------------------------------------------------------------------------
# raster statistics
# --------------------------------------------------------------------------
@router.post("/index")
def raster_index(body: RasterIndexRequest) -> dict[str, Any]:
    vector = _load("vector")
    raster = _load("raster")
    stac = _load("stac")

    metrics = _check_area(vector, body.geometry)
    item, hrefs, candidates = stac.latest(
        body.geometry,
        index=body.index,
        date_from=body.date_from,
        date_to=body.date_to,
        max_cloud=body.max_cloud,
    )
    convention = stac.reflectance(item)
    result = raster.compute_index(
        hrefs,
        body.geometry,
        body.index,
        cloud_mask=body.cloud_mask,
        scale=convention["scale"],
        offset=convention["offset"],
    )
    summary = raster.summarize(result, area_km2=metrics["area_km2"])

    scene = stac.describe(item)
    now = datetime.now(timezone.utc)
    payload = {
        "geometry": body.geometry,
        "index": body.index,
        "scene": scene["id"],
        "assets": {k: hrefs[k] for k in sorted(hrefs)},
        "cloud_mask": body.cloud_mask,
        "grid": result["grid"],
        "reflectance": convention,
    }
    stored = {
        "status": summary["status"],
        "stats": summary.get("stats"),
        "pixels": summary.get("pixels"),
        "index": body.index,
        "scene": scene["id"],
    }
    evidence_id = evidence.store(payload, stored) if summary["status"] == "ok" else None

    response: dict[str, Any] = {
        "status": summary["status"],
        "index": body.index,
        "label": result["label"],
        "formula": result["formula"],
        "stats": summary.get("stats"),
        "histogram": summary.get("histogram"),
        "pixels": result["pixels"],
        "grid": result["grid"],
        "scene": scene,
        "candidates": candidates,
        "cloud_mask": {"applied": result["pixels"]["cloud_mask_applied"], "classes": list(config.S2_CLOUD_CLASSES)},
        "reflectance": {**convention, "applied": result["reflectance"]},
        "area_km2": metrics["area_km2"],
        "read_seconds": result["read_seconds"],
        "source": "Earth Search STAC -> Sentinel-2 L2A COG, read in place with rasterio",
        "evidence_id": evidence_id,
        "generated_at": now.isoformat(),
        "citation": (
            f"Sentinel-2 L2A scene {scene['id']} ({scene['datetime']}), COG assets read "
            f"via Earth Search with rasterio; accessed {now.date().isoformat()}. "
            "Contains modified Copernicus Sentinel data."
        ),
    }
    if body.preview:
        preview_id = hashlib.sha256(f"{scene['id']}|{body.index}|{body.geometry}".encode()).hexdigest()[:16]
        directory = Path(config.MAP_DIR) / "previews"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{preview_id}.png"
        raster.write_preview(
            result["values"],
            str(path),
            ramp="ndvi" if body.index == "ndvi" else ("water" if body.index == "ndwi" else "default"),
        )
        response["preview_url"] = f"/api/geo/preview/{path.name}"
        response["preview_bytes"] = path.stat().st_size
    return response


@router.get("/preview/{name}")
def preview(name: str) -> FileResponse:
    if not name.endswith(".png") or len(name) != 20:
        raise AppError(INVALID_ARGUMENTS, "preview name must be a 16-character hex id plus .png")
    try:
        int(name[:16], 16)
    except ValueError:
        raise AppError(INVALID_ARGUMENTS, "preview name must be a 16-character hex id plus .png") from None
    path = Path(config.MAP_DIR) / "previews" / name
    if not path.exists():
        raise AppError("evidence_not_found", f"no preview named {name}")
    return FileResponse(path, media_type="image/png")


# --------------------------------------------------------------------------
# population
# --------------------------------------------------------------------------
@router.post("/population")
def worldpop(body: PopulationRequest) -> dict[str, Any]:
    vector = _load("vector")
    module = _load("population")

    metrics = _check_area(vector, body.geometry)
    result = module.estimate(body.geometry, iso3=body.iso3, year=body.year)
    result["aoi"] = metrics
    if result.get("pixels_used", 0) < 25:
        result.setdefault("notes", []).append(
            "fewer than 25 raster pixels intersect the AOI, so the total is coarse"
        )
    return result


# --------------------------------------------------------------------------
# Earth Engine
# --------------------------------------------------------------------------
@router.post("/composite")
def earth_composite(body: CompositeRequest) -> dict[str, Any]:
    vector = _load("vector")
    raster = _load("raster")
    earth = _load("earth")

    _check_area(vector, body.geometry)
    raster.spec(body.index)  # raises unknown_index with the valid list
    result = earth.index_stats(
        body.geometry,
        index=body.index,
        date_from=body.date_from,
        date_to=body.date_to,
        max_cloud=body.max_cloud,
        scale=body.scale,
    )
    return {**result, "source": "Google Earth Engine, COPERNICUS/S2_SR_HARMONIZED"}


@router.post("/landcover")
def earth_landcover(body: LandcoverRequest) -> dict[str, Any]:
    vector = _load("vector")
    earth = _load("earth")

    _check_area(vector, body.geometry)
    return earth.landcover(
        body.geometry,
        date_from=body.date_from,
        date_to=body.date_to,
        scale=body.scale,
        buildings=body.buildings,
    )


@router.post("/landcover/overlay")
def landcover_overlay(body: LandcoverOverlayRequest) -> dict[str, Any]:
    vector = _load("vector")
    earth = _load("earth")

    _check_area(vector, body.geometry)
    started = time.time()
    overlay = earth.landcover_overlay(
        body.geometry,
        date_from=body.date_from,
        date_to=body.date_to,
        size=body.size,
        focus=body.focus,
    )

    directory = Path(config.MAP_DIR) / "overlays"
    directory.mkdir(parents=True, exist_ok=True)
    # `focus` must be part of the key: a tree-only overlay and the full class
    # mosaic share the same AOI and window, and hashing them the same way made
    # the focused image overwrite the full one (caught 2026-11-14).
    digest = hashlib.sha256(
        f"{body.geometry}|{body.date_from}|{body.date_to}|{body.size}|{body.focus}".encode()
    ).hexdigest()[:16]
    path = directory / f"{digest}.png"
    path.write_bytes(overlay["png"])

    payload: dict[str, Any] = {
        "status": "ok",
        "image_url": f"/api/geo/overlay/{path.name}",
        "bytes": len(overlay["png"]),
        "bounds": overlay["bounds"],
        "scene_count": overlay["scene_count"],
        "focus": overlay["focus"],
        "classes": [
            {"id": entry["id"], "name": entry["name"], "color": entry["color"]}
            for entry in earth.LAND_COVER
        ],
        "elapsed_seconds": round(time.time() - started, 2),
    }
    if body.include_buildings:
        try:
            payload["buildings"] = earth.building_footprints(
                body.geometry, limit=body.buildings_limit
            )
        except AppError:
            raise
        except Exception as exc:  # noqa: BLE001 - overlay must survive a dataset hiccup
            payload["buildings"] = {
                "type": "FeatureCollection",
                "features": [],
                "total": None,
                "returned": 0,
                "truncated": False,
                "error": type(exc).__name__,
            }
    return payload


@router.get("/overlay/{name}")
def serve_overlay(name: str) -> FileResponse:
    if not name.endswith(".png") or len(name) != 20:
        raise AppError(INVALID_ARGUMENTS, "overlay name must be a 16-character hex id plus .png")
    try:
        int(name[:16], 16)
    except ValueError:
        raise AppError(INVALID_ARGUMENTS, "overlay name must be a 16-character hex id plus .png") from None
    path = Path(config.MAP_DIR) / "overlays" / name
    if not path.exists():
        raise AppError("evidence_not_found", f"no overlay named {name}")
    return FileResponse(path, media_type="image/png")


@router.post("/buildings")
def open_buildings(body: BuildingsRequest) -> dict[str, Any]:
    vector = _load("vector")
    earth = _load("earth")

    _check_area(vector, body.geometry)
    return earth.building_footprints(
        body.geometry,
        confidence=body.confidence,
        limit=body.limit,
        heights=body.heights,
    )


@router.post("/dates")
def acquisition_dates(body: DatesRequest) -> dict[str, Any]:
    """Which Sentinel-2 passes cover this AOI, grouped by calendar day."""
    vector = _load("vector")
    stac = _load("stac")

    _check_area(vector, body.geometry)
    items = stac.search(
        body.geometry,
        date_from=body.date_from,
        date_to=body.date_to,
        max_cloud=body.max_cloud,
        limit=20,
    )
    items.sort(key=lambda item: item.get("properties", {}).get("datetime") or "", reverse=True)

    days: dict[str, dict[str, Any]] = {}
    for item in items:
        properties = item.get("properties") or {}
        stamp = properties.get("datetime") or ""
        day = stamp[:10]
        if not day:
            continue
        cloud = properties.get("eo:cloud_cover")
        entry = days.setdefault(day, {"date": day, "scenes": [], "cloud_min": None})
        entry["scenes"].append(
            {
                "id": item.get("id"),
                "datetime": stamp,
                "cloud": None if cloud is None else round(float(cloud), 2),
            }
        )
        if cloud is not None:
            current = entry["cloud_min"]
            entry["cloud_min"] = round(float(cloud), 2) if current is None else min(current, round(float(cloud), 2))

    return {
        "status": "ok" if days else "no_imagery",
        "days": [days[key] for key in sorted(days, reverse=True)],
        "scene_count": len(items),
        "window": {"from": body.date_from, "to": body.date_to, "max_cloud": body.max_cloud},
        "source": "Earth Search STAC (Sentinel-2 L2A acquisition times)",
        "note": "Times are scene sensing times; a day can carry more than one overpass.",
    }


@router.post("/features")
def osm_features(body: FeatureRequest) -> dict[str, Any]:
    vector = _load("vector")
    osm = _load("osm")

    _check_area(vector, body.geometry)
    return osm.fetch(body.geometry, body.category, limit=body.limit)


# --------------------------------------------------------------------------
# interactive map
# --------------------------------------------------------------------------
@router.post("/map")
def make_map(body: MapRequest) -> dict[str, Any]:
    vector = _load("vector")
    maps = _load("maps")

    _check_area(vector, body.geometry)
    return maps.build(
        body.geometry,
        title=body.title,
        index=body.index,
        date_from=body.date_from,
        date_to=body.date_to,
        landcover=body.landcover,
    )


@router.get("/map/{name}")
def serve_map(name: str) -> FileResponse:
    maps = _load("maps")
    return FileResponse(maps.path_for(name), media_type="text/html")
