"""WorldPop population inside an AOI, from the published 1 km UN-adjusted raster.

WorldPop's own REST API (`api.worldpop.org/v1/services/stats`) answered 404 for
every shape we tried on 2026-11-13, and the static file server advertises
`Accept-Ranges` while GDAL reports "Range downloading not supported". So this
module downloads the country raster once, caches it, and does the zonal sum
locally with rasterio — which is also the only way to see how the number was
produced.

WorldPop 1 km "Aggregated" rasters store **people per pixel**, so the population
of an AOI is the sum of the pixels inside it, not the mean times 1000.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import rasterio
from rasterio.features import geometry_mask
from rasterio.windows import from_bounds

from app import config
from app.errors import INVALID_ARGUMENTS, PROVIDER_UNAVAILABLE, AppError
from app.geostack.vector import analyze, load, to_crs

_LOCK = threading.Lock()
SUPPORTED_YEARS = tuple(range(2000, 2021))


def _iso3(value: str) -> str:
    iso = (value or "").strip().upper()
    if len(iso) != 3 or not iso.isalpha():
        raise AppError(INVALID_ARGUMENTS, f"iso3 must be a three-letter country code, got {value!r}")
    return iso


def cache_path(iso3: str, year: int) -> Path:
    return Path(config.WORLDPOP_CACHE_DIR) / f"{iso3.lower()}_ppp_{year}_1km.tif"


def url_for(iso3: str, year: int) -> str:
    return config.WORLDPOP_URL.format(year=year, ISO3=iso3, iso3=iso3.lower())


def ensure_raster(iso3: str, year: int) -> Path:
    """Download the country raster once; later calls reuse the cache."""
    if year not in SUPPORTED_YEARS:
        raise AppError(
            INVALID_ARGUMENTS,
            f"WorldPop 1 km rasters cover {SUPPORTED_YEARS[0]}..{SUPPORTED_YEARS[-1]}; got {year}",
        )
    path = cache_path(iso3, year)
    if path.exists() and path.stat().st_size > 0:
        return path

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".part")
    url = url_for(iso3, year)
    with _LOCK:
        if path.exists() and path.stat().st_size > 0:  # another thread won the race
            return path
        try:
            with httpx.stream(
                "GET", url, timeout=180.0, follow_redirects=True,
                headers={"User-Agent": config.USER_AGENT},
            ) as response:
                if response.status_code >= 400:
                    raise AppError(
                        PROVIDER_UNAVAILABLE,
                        f"WorldPop returned HTTP {response.status_code} for {iso3} {year}",
                        url=url,
                    )
                with open(temporary, "wb") as fh:
                    for chunk in response.iter_bytes(1 << 16):
                        fh.write(chunk)
            os.replace(temporary, path)
        except httpx.HTTPError as exc:
            raise AppError(PROVIDER_UNAVAILABLE, f"WorldPop download failed: {exc}") from exc
        finally:
            temporary.unlink(missing_ok=True)
    return path


def estimate(
    geometry: dict[str, Any],
    *,
    iso3: str = "IND",
    year: int = 2020,
    allow_download: bool = True,
) -> dict[str, Any]:
    """Sum WorldPop pixels inside the AOI and normalise by its area."""
    iso = _iso3(iso3)
    geom, audit = load(geometry)
    metrics = analyze(geometry)

    path = cache_path(iso, year)
    if not path.exists():
        if not allow_download:
            raise AppError(
                PROVIDER_UNAVAILABLE,
                f"WorldPop raster not cached for {iso} {year}",
                cache=str(path),
            )
        path = ensure_raster(iso, year)

    with rasterio.open(path) as ds:
        geom_native = to_crs(geom, ds.crs.to_epsg() or 4326)
        window = from_bounds(*geom_native.bounds, ds.transform).round_offsets()
        window = rasterio.windows.Window(
            window.col_off,
            window.row_off,
            max(1, int(round(window.width))),
            max(1, int(round(window.height))),
        ).intersection(rasterio.windows.Window(0, 0, ds.width, ds.height))
        data = ds.read(1, window=window)
        transform = ds.window_transform(window)
        inside = geometry_mask([geom_native], out_shape=data.shape, transform=transform, invert=True)
        nodata = ds.nodata
        valid = inside & np.isfinite(data)
        if nodata is not None:
            valid &= data != nodata
        pixels = data[valid].astype("float64")

        population = float(pixels.sum())
        area_km2 = metrics["area_km2"]
        return {
            # A small AOI over water can contain only nodata cells. That is not
            # "zero people" in the same sense as an inhabited cell with 0.0, so
            # it gets its own status instead of a confident-looking 0.
            "status": "ok" if pixels.size else "no_population_pixels",
            "population": int(round(population)),
            "density_per_km2": round(population / area_km2, 1) if area_km2 else None,
            "area_km2": area_km2,
            "pixels_used": int(pixels.size),
            "pixel_population_min": round(float(pixels.min()), 2) if pixels.size else None,
            "pixel_population_max": round(float(pixels.max()), 2) if pixels.size else None,
            "source": {
                "dataset": "WorldPop 1 km UN-adjusted, aggregated",
                "iso3": iso,
                "year": year,
                "url": url_for(iso, year),
                "cached_at": str(path),
                "crs": str(ds.crs),
                "resolution_deg": [round(v, 9) for v in ds.res],
                "nodata": nodata,
            },
            "audit": audit,
            "notes": [
                "1 km pixels are people per pixel, so the AOI total is their sum",
                *(["every 1 km pixel touching this AOI is nodata (water or outside the national raster)"]
                  if not pixels.size else []),
                "a 4 km² AOI covers only a handful of 1 km pixels: treat the total as a magnitude, not a census",
                "WorldPop is modelled, not counted",
            ],
        }


def cached() -> list[dict[str, Any]]:
    root = Path(config.WORLDPOP_CACHE_DIR)
    if not root.exists():
        return []
    return [
        {"name": p.name, "bytes": p.stat().st_size}
        for p in sorted(root.glob("*.tif"))
    ]
