"""HTTP surface for the optional geospatial stack, mounted at /api/geo.

Handlers are synchronous on purpose: rasterio and geopandas block, so FastAPI
runs these in its threadpool instead of stalling the event loop. Imports of the
heavy modules happen inside each handler, which keeps the core API importable on
a machine without GDAL.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app import config
from app.services import evidence
from app.errors import AOI_TOO_LARGE, INVALID_ARGUMENTS, AppError
from app.geostack import capabilities

router = APIRouter(prefix="/api/geo", tags=["geostack"])

PREVIEW_RE = re.compile(r"^[a-f0-9]{16}\.png$")


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


def _check_area(geometry: dict[str, Any]) -> dict[str, Any]:
    from app.geostack import vector

    metrics = vector.analyze(geometry)
    if metrics["area_km2"] > config.MAX_AOI_AREA_KM2:
        raise AppError(
            AOI_TOO_LARGE,
            f"AOI is {metrics['area_km2']:.1f} km², above the {config.MAX_AOI_AREA_KM2:.0f} km² limit",
            area_km2=metrics["area_km2"],
        )
    return metrics


# --------------------------------------------------------------------------
# capability discovery
# --------------------------------------------------------------------------
@router.get("/capabilities")
def stack_capabilities() -> dict[str, Any]:
    from app.geostack import maps, population

    report = capabilities.report()
    report["worldpop_cached"] = population.cached()
    report["maps"] = maps.stored()[:5]
    return report


# --------------------------------------------------------------------------
# vector
# --------------------------------------------------------------------------
@router.post("/aoi")
def aoi_metrics(body: AoiRequest) -> dict[str, Any]:
    from app.geostack import vector

    metrics = vector.analyze(body.geometry)
    if metrics["area_km2"] > config.MAX_AOI_AREA_KM2:
        raise AppError(AOI_TOO_LARGE, "AOI is above the configured area limit", area_km2=metrics["area_km2"])
    return {"status": "ok", **metrics, "source": "geopandas/pyproj, WGS84 ellipsoid"}


# --------------------------------------------------------------------------
# sourcing
# --------------------------------------------------------------------------
@router.post("/scene")
def find_scene(body: SceneRequest) -> dict[str, Any]:
    from app.geostack import stac

    _check_area(body.geometry)
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
    from app.geostack import raster, stac

    metrics = _check_area(body.geometry)
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
    if not PREVIEW_RE.match(name):
        raise AppError(INVALID_ARGUMENTS, "preview name must be a 16-character hex id plus .png")
    path = Path(config.MAP_DIR) / "previews" / name
    if not path.exists():
        raise AppError("evidence_not_found", f"no preview named {name}")
    return FileResponse(path, media_type="image/png")


# --------------------------------------------------------------------------
# population
# --------------------------------------------------------------------------
@router.post("/population")
def worldpop(body: PopulationRequest) -> dict[str, Any]:
    from app.geostack import population as wp

    metrics = _check_area(body.geometry)
    result = wp.estimate(body.geometry, iso3=body.iso3, year=body.year)
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
    from app.geostack import earth

    _check_area(body.geometry)
    if body.index not in ("ndvi", "ndwi", "ndbi", "nbr", "ndmi"):
        from app.geostack import raster

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


# --------------------------------------------------------------------------
# interactive map
# --------------------------------------------------------------------------
@router.post("/map")
def make_map(body: MapRequest) -> dict[str, Any]:
    from app.geostack import maps

    _check_area(body.geometry)
    return maps.build(
        body.geometry,
        title=body.title,
        index=body.index,
        date_from=body.date_from,
        date_to=body.date_to,
    )


@router.get("/map/{name}")
def serve_map(name: str) -> FileResponse:
    from app.geostack import maps

    return FileResponse(maps.path_for(name), media_type="text/html")
