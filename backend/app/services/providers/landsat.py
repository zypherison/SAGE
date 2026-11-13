"""Landsat 8/9 thermal adapter (ArcGIS `Landsat8_Views` ImageServer).

Probed live 2026-11-13 and reachable without a key, which is why it is here:

    bandCount 11, thermal at indices 9 and 10, pixelType S16
    time extent 2020-01-01 .. 2026-11-08   (five years, vs Sentinel-2's 14 months)
    catalogue fields OBJECTID, AcquisitionDate, CloudCover, Name, ProductName
    statistics WITHOUT a rendering rule -> one entry per band, so band 9 is
    addressable by position; with an NDVI function you only ever get one entry.

What the thermal band actually contains, and what that costs:

  * These are Level-1 TIRS digital numbers (`ProductName: Level1` on every scene
    probed). DN -> radiance is `DN * 0.0003342`; radiance -> brightness
    temperature is the inverse Planck function with the per-sensor K1/K2.
  * So the value is **at-sensor brightness temperature**, NOT land surface
    temperature. LST additionally needs emissivity per pixel and an atmospheric
    correction. Skipping those is a bias of a few kelvin, not a cosmetic detail,
    and the response labels it accordingly.
  * A scene's catalogue cloud cover is a whole-scene number, useless over a small
    AOI: a scene listed at 0.9% cloud averaged -12 C over Hussain Sagar (cloud
    top) while a clear scene over the same water read +26 C. There is no
    server-side QA masking through this endpoint, so cloud-contaminated scenes
    are flagged by physical plausibility and their spread is reported.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timezone
from typing import Any

from app import config
from app.errors import AppError, PROVIDER_UNAVAILABLE
from app.services.net import Fetch

NAME = "landsat-arcgis"


def _ms(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1000)


def _sensor(name: str | None) -> str:
    """`LC08_L1TP_...` -> `LC08`, so the right Planck constants are used."""
    if name and name[:4] in config.THERMAL_K1_K2:
        return name[:4]
    return "LC08"


async def search(
    fetch: Fetch,
    *,
    bbox: list[float],
    date_from: date,
    date_to: date,
    limit: int = 40,
) -> list[dict[str, Any]]:
    """Catalogue scenes intersecting the AOI inside a date window."""
    params: dict[str, Any] = {
        "where": "1=1",
        "outFields": "OBJECTID,AcquisitionDate,CloudCover,Name",
        "geometry": f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "returnGeometry": "false",
        "time": f"{_ms(date_from)}, {_ms(date_to) + 86_399_000}",
        "orderByFields": "AcquisitionDate DESC",
        "resultRecordCount": str(max(1, min(limit, config.MAX_LIMIT))),
        "f": "json",
    }
    payload, _cached = await fetch.get_json(f"{config.LANDSAT_URL}/query", params=params)
    if "error" in (payload or {}):
        raise AppError(PROVIDER_UNAVAILABLE, f"Landsat error: {payload['error']}")

    scenes: list[dict[str, Any]] = []
    for feature in (payload or {}).get("features", []):
        attrs = feature.get("attributes") or {}
        # The query response uses the service's capitalised field names, not the
        # lowercase ones listed in the service metadata. Both were read directly.
        stamp = attrs.get("AcquisitionDate")
        if not stamp:
            continue
        moment = datetime.fromtimestamp(stamp / 1000, tz=timezone.utc)
        scenes.append(
            {
                "objectid": attrs.get("OBJECTID"),
                "day": moment.date().isoformat(),
                "datetime": moment.isoformat(),
                "cloud_cover_percent": attrs.get("CloudCover"),
                "name": attrs.get("Name"),
                "sensor": _sensor(attrs.get("Name")),
            }
        )
    return scenes


async def band_statistics(
    fetch: Fetch,
    *,
    bbox: list[float],
    band_index: int = config.LANDSAT_THERMAL_BAND,
    objectid: Any = None,
) -> dict[str, Any] | None:
    """Statistics and histogram for one band of one pinned scene."""
    import json

    geometry = str(
        {
            "xmin": bbox[0], "ymin": bbox[1], "xmax": bbox[2], "ymax": bbox[3],
            "spatialReference": {"wkid": 4326},
        }
    )
    params: dict[str, Any] = {
        "geometry": geometry,
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "f": "json",
    }
    if objectid is not None:
        params["mosaicRule"] = json.dumps(
            {"mosaicMethod": "esriMosaicLockRaster", "lockRasterIds": [objectid]}
        )

    payload, _cached = await fetch.get_json(
        f"{config.LANDSAT_URL}/computeStatisticsHistograms", params=params
    )
    if "error" in (payload or {}):
        raise AppError(PROVIDER_UNAVAILABLE, f"Landsat stats error: {payload['error']}")

    stats_list = (payload or {}).get("statistics") or []
    if len(stats_list) <= band_index:
        return None
    hist_list = (payload or {}).get("histograms") or []
    hist = hist_list[band_index] if len(hist_list) > band_index else {}

    s = stats_list[band_index]
    return {
        "mean": s.get("mean"),
        "min": s.get("min"),
        "max": s.get("max"),
        "count": s.get("count") or 0,
        "histogram": {
            "counts": hist.get("counts") or [],
            "min": hist.get("min"),
            "max": hist.get("max"),
        }
        if hist
        else None,
    }


def brightness_temperature_c(dn: float | None, sensor: str = "LC08") -> float | None:
    """Level-1 thermal DN -> at-sensor brightness temperature in °C."""
    if dn is None or dn <= 0:
        return None
    k1, k2 = config.THERMAL_K1_K2.get(sensor, config.THERMAL_K1_K2["LC08"])
    radiance = float(dn) * config.THERMAL_RADIANCE_SCALE
    if radiance <= 0:
        return None
    try:
        kelvin = k2 / math.log(k1 / radiance + 1.0)
    except (ValueError, ZeroDivisionError):
        return None
    return kelvin - 273.15


def mean_brightness_temperature_c(stats: dict[str, Any] | None, sensor: str) -> float | None:
    """Brightness temperature averaged over the histogram, not over the DN mean.

    The Planck curve is non-linear, so converting the mean DN is not the mean
    temperature. With 256 bins available the difference is small but free to
    remove, so the mean is taken bin by bin.
    """
    if not stats:
        return None
    hist = stats.get("histogram") or {}
    counts = hist.get("counts") or []
    lo = hist.get("min")
    hi = hist.get("max")
    if not counts or lo is None or hi is None:
        return brightness_temperature_c(stats.get("mean"), sensor)

    total = sum(counts)
    if total <= 0:
        return brightness_temperature_c(stats.get("mean"), sensor)
    width = (float(hi) - float(lo)) / len(counts)
    if width <= 0:
        return brightness_temperature_c(stats.get("mean"), sensor)

    accumulated = 0.0
    weighted = 0
    for i, count in enumerate(counts):
        if not count:
            continue
        temp = brightness_temperature_c(float(lo) + (i + 0.5) * width, sensor)
        if temp is None:
            continue
        accumulated += temp * count
        weighted += count
    return accumulated / weighted if weighted else None
