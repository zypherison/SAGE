"""Earth Engine: server-side composites, when credentials exist.

Kept deliberately small. Earth Engine's value to SAGE is the same as Sentinel Hub's
in `experiments/`: an archive that reaches past ArcGIS's rolling window, computed
server-side instead of transferring scenes. The contract here is a single call
that either returns statistics or explains which credential is missing.

A service account is the only unattended shape: `SAGE_EE_PROJECT`,
`SAGE_EE_SERVICE_ACCOUNT`, `SAGE_EE_KEY_FILE`. Nothing is read from a user's home
directory, and no interactive OAuth flow is attempted from a server process.
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from app.errors import GEOSTACK_UNAVAILABLE, NO_IMAGERY, AppError
from app.geostack import capabilities, raster

# formula band -> Sentinel-2 SR band
S2_BAND = {"nir": "B8", "red": "B4", "green": "B3", "swir1": "B11", "swir2": "B12"}
CLOUD_CLASSES = (3, 8, 9, 10, 11)

_INITIALISED = False


def _ee():
    return capabilities.require("ee")


def init():
    """Initialise once, or raise a 503-shaped error naming what is missing."""
    global _INITIALISED
    ee = _ee()
    cfg = capabilities.earth_engine_config()
    if not cfg["configured"]:
        raise AppError(
            GEOSTACK_UNAVAILABLE,
            "Earth Engine is not configured: set SAGE_EE_PROJECT, "
            "SAGE_EE_SERVICE_ACCOUNT and SAGE_EE_KEY_FILE (a service-account JSON)",
            required=["SAGE_EE_PROJECT", "SAGE_EE_SERVICE_ACCOUNT", "SAGE_EE_KEY_FILE"],
        )
    if not _INITIALISED:
        credentials = ee.ServiceAccountCredentials(cfg["service_account"], cfg["key_file"])
        ee.Initialize(credentials, project=cfg["project"])
        _INITIALISED = True
    return ee


def _geometry(ee, geometry: dict[str, Any]):
    return ee.Geometry(geometry)


def _masked_collection(ee, geometry, date_from: str, date_to: str, max_cloud: float):
    collection = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(geometry)
        .filterDate(f"{date_from}T00:00:00Z", f"{date_to}T23:59:59Z")
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", float(max_cloud)))
    )

    def mask(image):
        scl = image.select("SCL")
        valid = scl.neq(CLOUD_CLASSES[0])
        for klass in CLOUD_CLASSES[1:]:
            valid = valid.And(scl.neq(klass))
        return image.updateMask(valid)

    return collection.map(mask)


def index_stats(
    geometry: dict[str, Any],
    *,
    index: str = "ndvi",
    date_from: str,
    date_to: str,
    max_cloud: float = 20.0,
    scale: int = 10,
    product_id: str | None = None,
) -> dict[str, Any]:
    """Median composite over the window, reduced over the AOI.

    A median across acquisitions is the honest way to handle clouds here: the
    ArcGIS ImageServer silently ignores `mosaicMethod: median` (measured
    2026-11-13), whereas Earth Engine can actually compute it.

    `product_id` pins the collection to one acquisition (EE `PRODUCT_ID`, e.g.
    `S2B_MSIL2A_..._T44QKE_...`), which is how the COG path's single scene can be
    compared with this engine on identical input.
    """
    definition = raster.spec(index)
    band_a, band_b = definition["bands"]
    ee = init()
    started = time.time()

    geom = _geometry(ee, geometry)
    collection = _masked_collection(ee, geometry=geom, date_from=date_from, date_to=date_to, max_cloud=max_cloud)
    if product_id:
        collection = collection.filter(ee.Filter.eq("PRODUCT_ID", product_id))
    scene_count = int(collection.size().getInfo())
    if scene_count == 0:
        raise AppError(
            NO_IMAGERY,
            "Earth Engine found no Sentinel-2 scene for the AOI and window",
            window={"from": date_from, "to": date_to, "max_cloud": max_cloud},
            product_id=product_id,
        )

    composite = collection.select([S2_BAND[band_a], S2_BAND[band_b]]).median()
    values = composite.normalizedDifference([S2_BAND[band_a], S2_BAND[band_b]])

    reducer = (
        ee.Reducer.mean()
        .combine(ee.Reducer.percentile([10, 50, 90]), "", True)
        .combine(ee.Reducer.minMax(), "", True)
        .combine(ee.Reducer.count(), "", True)
    )
    # bestEffort lets Earth Engine coarsen the scale rather than fail on a large
    # AOI; `scale` stays in the response so the caller knows what was asked.
    reduced = values.reduceRegion(
        reducer=reducer, geometry=geom, scale=scale, maxPixels=int(1e9), bestEffort=True
    ).getInfo() or {}

    def pick(suffix: str) -> float | None:
        for key, value in reduced.items():
            if key.endswith(f"_{suffix}"):
                return None if value is None else round(float(value), 6)
        return None

    stats = {
        "mean": pick("mean"),
        "median": pick("p50"),
        "p10": pick("p10"),
        "p90": pick("p90"),
        "min": pick("min"),
        "max": pick("max"),
        "count": int(pick("count") or 0),
    }
    return {
        "status": "ok" if stats["count"] else "insufficient_data",
        "index": index,
        "label": definition["label"],
        "formula": definition["formula"],
        "stats": stats,
        "scene_count": scene_count,
        "window": {"from": date_from, "to": date_to, "max_cloud": max_cloud},
        "product_id": product_id,
        "reducer": f"median composite of {scene_count} scenes, SCL-masked, reduced at {scale} m",
        "requested_scale_m": scale,
        "elapsed_seconds": round(time.time() - started, 2),
    }


def population(
    geometry: dict[str, Any], *, year: int = 2020, scale: int = 100
) -> dict[str, Any]:
    """WorldPop via Earth Engine, for years outside the 1 km raster series."""
    ee = init()
    started = time.time()
    collection = (
        ee.ImageCollection("WorldPop/GP/100m/pop")
        .filterDate(f"{year}-01-01", f"{year}-12-31")
        .filterBounds(_geometry(ee, geometry))
    )
    if int(collection.size().getInfo()) == 0:
        raise AppError(NO_IMAGERY, f"Earth Engine has no WorldPop image for {year}", year=year)
    image = collection.mosaic()
    reduced = image.reduceRegion(
        reducer=ee.Reducer.sum(), geometry=_geometry(ee, geometry), scale=scale,
        maxPixels=int(1e9), bestEffort=True,
    ).getInfo() or {}
    total = next((v for v in reduced.values() if v is not None), None)
    return {
        "status": "ok" if total is not None else "insufficient_data",
        "population": None if total is None else int(round(float(total))),
        "year": year,
        "scale_m": scale,
        "source": "WorldPop/GP/100m/pop (Earth Engine)",
        "elapsed_seconds": round(time.time() - started, 2),
    }


def image(
    geometry: dict[str, Any],
    *,
    index: str = "ndvi",
    date_from: str,
    date_to: str,
    max_cloud: float = 20.0,
):
    """The composite image itself, for a map layer."""
    definition = raster.spec(index)
    band_a, band_b = definition["bands"]
    ee = init()
    geom = _geometry(ee, geometry)
    collection = _masked_collection(ee, geometry=geom, date_from=date_from, date_to=date_to, max_cloud=max_cloud)
    composite = collection.select([S2_BAND[band_a], S2_BAND[band_b]]).median()
    return composite.normalizedDifference([S2_BAND[band_a], S2_BAND[band_b]]).rename(index)


# Dynamic World v1 label values, in the order the dataset documents them.
LAND_COVER = (
    {"id": 0, "name": "Water", "color": "#419BDF"},
    {"id": 1, "name": "Trees", "color": "#397D49"},
    {"id": 2, "name": "Grass", "color": "#88B053"},
    {"id": 3, "name": "Flooded vegetation", "color": "#7A87C6"},
    {"id": 4, "name": "Crops", "color": "#E49635"},
    {"id": 5, "name": "Shrub and scrub", "color": "#DFC35A"},
    {"id": 6, "name": "Built-up", "color": "#C4281B"},
    {"id": 7, "name": "Bare", "color": "#A59B8F"},
    {"id": 8, "name": "Snow and ice", "color": "#B39FE1"},
)
LAND_COVER_PALETTE = [entry["color"] for entry in LAND_COVER]


def _landcover_collection(ee, geom, date_from: str, date_to: str):
    return (
        # Catalogue id as of 2026-11: GOOGLE/DYNAMICWORLD/V1 (the older spaced
        # "GOOGLE/Dynamic World-LULC/v1" no longer resolves).
        ee.ImageCollection("GOOGLE/DYNAMICWORLD/V1")
        .filterBounds(geom)
        .filterDate(f"{date_from}T00:00:00Z", f"{date_to}T23:59:59Z")
    )


def landcover_image(geometry: dict[str, Any], *, date_from: str, date_to: str):
    """Modal Dynamic World label over the window, for a map layer."""
    ee = init()
    geom = _geometry(ee, geometry)
    collection = _landcover_collection(ee, geom, date_from, date_to)
    if int(collection.size().getInfo()) == 0:
        raise AppError(
            NO_IMAGERY,
            "Dynamic World has no land-cover image for the AOI and window",
            window={"from": date_from, "to": date_to},
        )
    return collection.select("label").mode().rename("landcover")


def landcover_overlay(
    geometry: dict[str, Any],
    *,
    date_from: str,
    date_to: str,
    size: int = 1600,
    focus: int | None = None,
) -> dict[str, Any]:
    """A palette-rendered PNG of the modal land-cover classes, plus its bounds.

    Earth Engine does the rendering server-side, so the browser gets one image
    instead of nine polygon layers. The bounds are the AOI bbox from the request,
    which is what an image overlay needs to line itself up.
    """
    ee = init()
    geom = _geometry(ee, geometry)
    collection = _landcover_collection(ee, geom, date_from, date_to)
    scene_count = int(collection.size().getInfo())
    if scene_count == 0:
        raise AppError(
            NO_IMAGERY,
            "Dynamic World has no land-cover image for the AOI and window",
            window={"from": date_from, "to": date_to},
        )

    label = collection.select("label").mode()
    if focus is not None:
        # Marking one class means the others must not tint the imagery.
        label = label.updateMask(label.eq(int(focus)))
    thumb_url = label.getThumbURL(
        {
            "min": 0,
            "max": 8,
            "palette": LAND_COVER_PALETTE,
            "region": geom,
            "dimensions": int(size),
            "format": "png",
        }
    )
    try:
        response = httpx.get(thumb_url, timeout=180, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise AppError(NO_IMAGERY, f"Earth Engine thumbnail failed: {exc}") from exc

    ring = (geometry.get("coordinates") or [[]])[0]
    if not ring:
        raise AppError(NO_IMAGERY, "geometry has no exterior ring")
    lons = [float(point[0]) for point in ring]
    lats = [float(point[1]) for point in ring]
    return {
        "png": response.content,
        "bounds": [[min(lats), min(lons)], [max(lats), max(lons)]],
        "scene_count": scene_count,
        "focus": focus,
    }


def building_footprints(
    geometry: dict[str, Any],
    *,
    confidence: float = 0.7,
    limit: int = 3000,
    simplify_m: float = 2.0,
    heights: bool = False,
) -> dict[str, Any]:
    """Open Buildings polygons as GeoJSON, simplified for a browser overlay.

    The count is always the true count; `returned` says how many outlines came
    back, so a truncated layer is never presented as the complete set.

    With `heights`, each polygon also gets a `height` from the Open Buildings 2.5D
    Temporal collection (latest available year at 4 m). Those are *modelled*
    estimates, not surveyed heights, and the response says so.
    """
    ee = init()
    geom = _geometry(ee, geometry)
    collection = (
        ee.FeatureCollection("GOOGLE/Research/open-buildings/v3/polygons")
        .filterBounds(geom)
        .filter(ee.Filter.gte("confidence", confidence))
    )
    total = int(collection.size().getInfo())
    if total == 0:
        return {
            "type": "FeatureCollection",
            "features": [],
            "total": 0,
            "returned": 0,
            "truncated": False,
            "confidence": confidence,
        }

    simplified = collection.limit(limit).map(
        lambda feature: ee.Feature(feature.geometry().simplify(simplify_m))
    )

    height_source = None
    if heights:
        temporal = (
            ee.ImageCollection("GOOGLE/Research/open-buildings-temporal/v1")
            .filterBounds(geom)
            .select("building_height")
            .sort("system:time_start", False)
        )
        if int(temporal.size().getInfo()) > 0:
            # `reduceRegions` does not take maxPixels; tileScale keeps the 4 m
            # reduction within the request budget.
            reduced = temporal.first().reduceRegions(
                collection=simplified,
                reducer=ee.Reducer.max(),
                scale=4,
                tileScale=4,
            )
            data = reduced.getInfo() or {}
            features = []
            for feature in data.get("features", []):
                value = (feature.get("properties") or {}).get("max")
                features.append(
                    {
                        "type": "Feature",
                        "geometry": feature.get("geometry"),
                        "properties": {
                            "height": None if value is None else round(float(value), 1)
                        },
                    }
                )
            height_source = "Open Buildings 2.5D Temporal, latest year (modelled)"
            return {
                "type": "FeatureCollection",
                "features": features,
                "total": total,
                "returned": len(features),
                "truncated": total > limit,
                "confidence": confidence,
                "heights": True,
                "height_source": height_source,
            }

    data = simplified.getInfo() or {}
    features = data.get("features", [])
    return {
        "type": "FeatureCollection",
        "features": features,
        "total": total,
        "returned": len(features),
        "truncated": total > limit,
        "confidence": confidence,
        "heights": False,
        "height_source": height_source,
    }


def landcover(
    geometry: dict[str, Any],
    *,
    date_from: str,
    date_to: str,
    scale: int = 10,
    buildings: bool = True,
) -> dict[str, Any]:
    """Area per Dynamic World class, plus a count of mapped structures.

    This is land *cover*, not object detection: `built-up` is the share of pixels
    classified as built, and the Open Buildings count comes from a separate
    polygon dataset. Neither one outlines individual buildings, so the response
    says so rather than implying a segmentation model.
    """
    ee = init()
    started = time.time()
    geom = _geometry(ee, geometry)
    collection = _landcover_collection(ee, geom, date_from, date_to)
    scene_count = int(collection.size().getInfo())
    if scene_count == 0:
        raise AppError(
            NO_IMAGERY,
            "Dynamic World has no land-cover image for the AOI and window",
            window={"from": date_from, "to": date_to},
        )

    label = collection.select("label").mode()
    areas = ee.Image.cat(
        [
            label.eq(entry["id"]).multiply(ee.Image.pixelArea()).rename(f"c{entry['id']}")
            for entry in LAND_COVER
        ]
    )
    reduced = areas.reduceRegion(
        reducer=ee.Reducer.sum(),
        geometry=geom,
        scale=scale,
        maxPixels=int(1e9),
        bestEffort=True,
    ).getInfo() or {}

    square_metres = {entry["id"]: float(reduced.get(f"c{entry['id']}") or 0) for entry in LAND_COVER}
    total_m2 = sum(square_metres.values())
    classes = []
    for entry in LAND_COVER:
        m2 = square_metres[entry["id"]]
        classes.append(
            {
                **entry,
                "km2": round(m2 / 1e6, 4),
                "percent": round(m2 / total_m2 * 100, 2) if total_m2 else 0.0,
            }
        )

    structures: dict[str, Any] | None = None
    if buildings:
        try:
            polygons = (
                ee.FeatureCollection("GOOGLE/Research/open-buildings/v3/polygons")
                .filterBounds(geom)
                .filter(ee.Filter.gte("confidence", 0.7))
            )
            structures = {
                "count": int(polygons.size().getInfo()),
                "confidence": 0.7,
                "source": "Google Open Buildings v3 polygons",
            }
        except Exception as exc:  # noqa: BLE001 - optional dataset, must not fail the call
            structures = {
                "count": None,
                "confidence": 0.7,
                "source": "Google Open Buildings v3 polygons",
                "note": f"count unavailable: {type(exc).__name__}",
            }

    return {
        "status": "ok" if total_m2 else "insufficient_data",
        "classes": classes,
        "total_km2": round(total_m2 / 1e6, 4),
        "structures": structures,
        "scene_count": scene_count,
        "window": {"from": date_from, "to": date_to},
        "scale_m": scale,
        "elapsed_seconds": round(time.time() - started, 2),
        "source": "GOOGLE/DYNAMICWORLD/V1, modal label (Earth Engine), 10 m",
        "notes": [
            "Classes are modelled land cover, not object outlines; built-up is a pixel share.",
            "The structure count comes from Open Buildings polygons, not from the classes.",
        ],
    }


VIS = {
    "ndvi": {"min": -0.2, "max": 0.9, "palette": ["#313695", "#a50026", "#ffffbf", "#91cf60", "#004529"]},
    "ndwi": {"min": -0.6, "max": 0.4, "palette": ["#a50026", "#ffffbf", "#4393c3", "#081d58"]},
    "ndbi": {"min": -0.5, "max": 0.5, "palette": ["#313695", "#ffffbf", "#a50026"]},
    "nbr": {"min": -0.5, "max": 0.8, "palette": ["#a50026", "#ffffbf", "#004529"]},
    "ndmi": {"min": -0.5, "max": 0.6, "palette": ["#a50026", "#ffffbf", "#2166ac"]},
}
