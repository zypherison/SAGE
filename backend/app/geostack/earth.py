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
) -> dict[str, Any]:
    """Median composite over the window, reduced over the AOI.

    A median across acquisitions is the honest way to handle clouds here: the
    ArcGIS ImageServer silently ignores `mosaicMethod: median` (measured
    2026-11-13), whereas Earth Engine can actually compute it.
    """
    definition = raster.spec(index)
    band_a, band_b = definition["bands"]
    ee = init()
    started = time.time()

    geom = _geometry(ee, geometry)
    collection = _masked_collection(ee, geometry=geom, date_from=date_from, date_to=date_to, max_cloud=max_cloud)
    scene_count = int(collection.size().getInfo())
    if scene_count == 0:
        raise AppError(
            NO_IMAGERY,
            "Earth Engine found no Sentinel-2 scene for the AOI and window",
            window={"from": date_from, "to": date_to, "max_cloud": max_cloud},
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


VIS = {
    "ndvi": {"min": -0.2, "max": 0.9, "palette": ["#313695", "#a50026", "#ffffbf", "#91cf60", "#004529"]},
    "ndwi": {"min": -0.6, "max": 0.4, "palette": ["#a50026", "#ffffbf", "#4393c3", "#081d58"]},
    "ndbi": {"min": -0.5, "max": 0.5, "palette": ["#313695", "#ffffbf", "#a50026"]},
    "nbr": {"min": -0.5, "max": 0.8, "palette": ["#a50026", "#ffffbf", "#004529"]},
    "ndmi": {"min": -0.5, "max": 0.6, "palette": ["#a50026", "#ffffbf", "#2166ac"]},
}
