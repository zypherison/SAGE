"""Raster analysis with rasterio: read COGs in place, compute indices, summarise.

Nothing here downloads a scene. A Sentinel-2 L2A COG is opened through GDAL's
/vsicurl driver and only the AOI window is fetched, which is the cloud-native
habit GeoLibre is built around and the reason a 100 km² analysis costs seconds.

Two measured quirks are encoded:

  * Reflectance is `DN * 1e-4` when `earthsearch:boa_offset_applied` is true, and
    `(DN - 1000) / 10000` when it is false. The asset's own `raster:bands.offset`
    says -0.1 either way and must not be trusted: applying it to a shifted scene
    turned a 0.087 median red reflectance into -0.013 and an NDVI mean into 1.28.
    The caller passes the convention in (`stac.reflectance`), never a guess.
  * WorldPop's server refuses HTTP range requests, so it cannot be read this way
    at all (see `population.py`, which downloads once and caches).
"""
from __future__ import annotations

import time
from typing import Any

import numpy as np
import rasterio
from affine import Affine
from rasterio.errors import RasterioIOError
from rasterio.features import geometry_mask
from rasterio.windows import from_bounds
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry

from app import config
from app.errors import AOI_TOO_LARGE, INVALID_ARGUMENTS, NO_IMAGERY, UNKNOWN_INDEX, AppError
from app.geostack.vector import to_crs

INDEXES: dict[str, dict[str, Any]] = {
    "ndvi": {
        "label": "NDVI",
        "formula": "(NIR - Red) / (NIR + Red)",
        "bands": ("nir", "red"),
        "description": "Normalized Difference Vegetation Index",
    },
    "ndwi": {
        "label": "NDWI",
        "formula": "(Green - NIR) / (Green + NIR)",
        "bands": ("green", "nir"),
        "description": "Normalized Difference Water Index (McFeeters)",
    },
    "ndbi": {
        "label": "NDBI",
        "formula": "(SWIR1 - NIR) / (SWIR1 + NIR)",
        "bands": ("swir1", "nir"),
        "description": "Normalized Difference Built-up Index",
    },
    "nbr": {
        "label": "NBR",
        "formula": "(NIR - SWIR2) / (NIR + SWIR2)",
        "bands": ("nir", "swir2"),
        "description": "Normalized Burn Ratio",
    },
    "ndmi": {
        "label": "NDMI",
        "formula": "(NIR - SWIR1) / (NIR + SWIR1)",
        "bands": ("nir", "swir1"),
        "description": "Normalized Difference Moisture Index",
    },
}

# Colour ramps for the PNG preview, as (position 0..1, (r, g, b)).
RAMPS: dict[str, list[tuple[float, tuple[int, int, int]]]] = {
    "ndvi": [
        (0.0, (49, 54, 149)),
        (0.35, (165, 0, 38)),
        (0.5, (255, 255, 191)),
        (0.7, (145, 207, 96)),
        (1.0, (0, 69, 41)),
    ],
    "water": [
        (0.0, (165, 0, 38)),
        (0.4, (255, 255, 191)),
        (0.55, (67, 147, 195)),
        (1.0, (8, 29, 88)),
    ],
    "default": [
        (0.0, (49, 54, 149)),
        (0.5, (255, 255, 191)),
        (1.0, (165, 0, 38)),
    ],
}


def spec(index: str) -> dict[str, Any]:
    try:
        return INDEXES[index]
    except KeyError:
        raise AppError(
            UNKNOWN_INDEX,
            f"unknown index {index!r}; known: {', '.join(sorted(INDEXES))}",
        ) from None


def remote_path(href: str) -> str:
    """GDAL path for a local file or an HTTP(S) COG."""
    if href.startswith(("http://", "https://")):
        return f"/vsicurl/{href}"
    return href


def _window(ds: rasterio.DatasetReader, geom: BaseGeometry):
    """Pixel window covering an AOI, never smaller than one pixel.

    `round_lengths()` collapses a window smaller than half a pixel to zero, which
    is exactly what a small AOI on a 1 km raster produces. A zero-size read
    raises deep inside GDAL, so the floor is applied here.
    """
    window = from_bounds(*geom.bounds, ds.transform).round_offsets()
    return rasterio.windows.Window(
        window.col_off,
        window.row_off,
        max(1, int(round(window.width))),
        max(1, int(round(window.height))),
    ).intersection(rasterio.windows.Window(0, 0, ds.width, ds.height))


def _read_grid(ds: rasterio.DatasetReader, geom: BaseGeometry, shape: tuple[int, int] | None = None):
    """Read the AOI window, optionally resampled to `shape`, and its transform."""
    window = _window(ds, geom)
    if shape is None:
        data = ds.read(1, window=window)
        transform = ds.window_transform(window)
    else:
        height, width = shape
        data = ds.read(1, window=window, out_shape=(height, width), resampling=rasterio.enums.Resampling.nearest)
        # out_shape resamples inside the same window, so the pixel size changes
        # with it; forgetting this scale is how a mask lands one pixel off.
        base = ds.window_transform(window)
        transform = Affine(
            base.a * (window.width / width), base.b, base.c,
            base.d, base.e * (window.height / height), base.f,
        )
    return data, transform


def compute_index(
    hrefs: dict[str, str],
    geometry: dict[str, Any],
    index: str = "ndvi",
    *,
    cloud_mask: bool = True,
    scale: float = config.S2_REFLECTANCE_SCALE,
    offset: float = config.S2_REFLECTANCE_OFFSET,
    max_pixels: int = config.GEOSTACK_MAX_PIXELS,
) -> dict[str, Any]:
    """Compute an index over an AOI from band COG hrefs.

    Returns the masked float array plus the grid it lives on, so a caller can
    preview it without re-reading anything.
    """
    definition = spec(index)
    bands = definition["bands"]
    missing = [b for b in bands if not hrefs.get(b)]
    if missing:
        raise AppError(NO_IMAGERY, f"scene is missing band asset(s): {', '.join(missing)}", bands=bands)

    geom, _audit = _load_geom(geometry)
    started = time.time()

    with rasterio.open(remote_path(hrefs[bands[0]])) as first:
        epsg = first.crs.to_epsg()
        if epsg is None:
            raise AppError(NO_IMAGERY, "band has no EPSG code; cannot reproject the AOI")
        if first.width * first.height > 0 and first.res[0] <= 0:
            raise AppError(NO_IMAGERY, "band has no geotransform")
        geom_native = to_crs(geom, epsg)
        a_dn, transform = _read_grid(first, geom_native)
        crs = first.crs
        if a_dn.size > max_pixels:
            raise AppError(
                AOI_TOO_LARGE,
                f"AOI window is {a_dn.size:,} pixels, above the {max_pixels:,} limit",
                pixels=int(a_dn.size),
            )
        band_a = first.tags().get("BAND_NAME") or bands[0]

        with rasterio.open(remote_path(hrefs[bands[1]])) as second:
            if second.crs != crs:
                raise AppError(
                    NO_IMAGERY,
                    f"band assets disagree on CRS ({crs} vs {second.crs}); refusing to guess",
                )
            b_dn, _ = _read_grid(second, geom_native, shape=a_dn.shape)

        scl = None
        if cloud_mask and hrefs.get("scl"):
            with rasterio.open(remote_path(hrefs["scl"])) as scl_ds:
                if scl_ds.crs == crs:
                    scl, _ = _read_grid(scl_ds, geom_native, shape=a_dn.shape)
                else:
                    raise AppError(
                        NO_IMAGERY,
                        f"SCL asset CRS {scl_ds.crs} does not match bands {crs}; refusing to guess",
                    )

    inside = geometry_mask(
        [mapping(geom_native)], out_shape=a_dn.shape, transform=transform, invert=True
    )
    valid = inside & (a_dn > 0) & (b_dn > 0)
    if scl is not None:
        cloud = np.isin(scl, np.asarray(config.S2_CLOUD_CLASSES))
        valid &= ~cloud
        cloud_pixels = int((inside & cloud).sum())
    else:
        cloud_pixels = 0

    # Reflectance, offset included. Guard the denominator: two near-zero
    # reflectance pixels would otherwise divide to +/-700 and poison the mean.
    # A 1e-3 floor is far below any real land surface (red + NIR is ~0.05 even
    # for dark water) and only rejects numerical noise.
    a = a_dn.astype("float32") * scale + offset
    b = b_dn.astype("float32") * scale + offset
    with np.errstate(divide="ignore", invalid="ignore"):
        denominator = a + b
        ratio = np.where(np.abs(denominator) > 1e-3, (a - b) / denominator, np.nan)

    # The index is bounded by definition; clipping removes sensor artefacts that
    # survive the denominator guard instead of letting them set the minimum.
    ratio = np.clip(ratio, -1.0, 1.0)
    values = np.where(valid, ratio, np.nan).astype("float32")
    total = int(inside.sum())
    return {
        "values": values,
        "valid": valid,
        "index": index,
        "label": definition["label"],
        "formula": definition["formula"],
        "grid": {
            "crs": str(crs),
            "width": int(a_dn.shape[1]),
            "height": int(a_dn.shape[0]),
            "res_m": [round(float(v), 3) for v in (abs(transform.a), abs(transform.e))],
        },
        "transform": transform,
        "pixels": {
            "inside_aoi": total,
            "valid": int(valid.sum()),
            "cloud_masked": cloud_pixels,
            "cloud_mask_applied": scl is not None,
        },
        "reflectance": {"scale": scale, "offset": offset},
        "read_seconds": round(time.time() - started, 2),
        "band_a": band_a,
    }


def summarize(result: dict[str, Any], *, area_km2: float | None = None) -> dict[str, Any]:
    """Statistics over the valid pixels, in a shape the rest of SAGE understands."""
    values = result["values"]
    flat = values[np.isfinite(values)]
    if flat.size == 0:
        return {
            "status": "insufficient_data",
            "count": 0,
            "pixels": result["pixels"],
            "message": "no valid pixels after masking",
        }

    p10, p50, p90 = (float(v) for v in np.percentile(flat, [10, 50, 90]))
    stats = {
        "min": round(float(flat.min()), 6),
        "max": round(float(flat.max()), 6),
        "mean": round(float(flat.mean()), 6),
        "median": round(p50, 6),
        "std": round(float(flat.std()), 6),
        "p10": round(p10, 6),
        "p90": round(p90, 6),
        "count": int(flat.size),
    }
    if area_km2:
        stats["area_km2"] = round(area_km2, 6)

    counts, edges = np.histogram(flat, bins=10, range=(-1.0, 1.0))
    return {
        "status": "ok",
        "stats": stats,
        "pixels": result["pixels"],
        "histogram": {
            "bins": [
                {"low": round(float(edges[i]), 3), "high": round(float(edges[i + 1]), 3), "count": int(counts[i])}
                for i in range(len(counts))
            ],
            "range": [-1.0, 1.0],
        },
    }


def _load_geom(geometry: dict[str, Any]):
    from app.geostack.vector import load

    return load(geometry)


def _ramp_color(value: float, stops: list[tuple[float, tuple[int, int, int]]]) -> tuple[int, int, int]:
    for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
        if value <= p1:
            span = p1 - p0 or 1e-9
            t = (value - p0) / span
            return tuple(int(round(c0[i] + (c1[i] - c0[i]) * t)) for i in range(3))
    return stops[-1][1]


def write_preview(values: np.ndarray, path: str, *, ramp: str = "default", stretch: tuple[float, float] = (-1.0, 1.0)) -> str:
    """Write a small PNG of the index so a map can show it without a tile server."""
    from PIL import Image

    lo, hi = stretch
    rgb = np.zeros((*values.shape, 3), dtype="uint8")
    stops = RAMPS.get(ramp, RAMPS["default"])
    finite = np.isfinite(values)
    scaled = np.clip((values - lo) / (hi - lo), 0.0, 1.0)
    scaled = np.nan_to_num(scaled, nan=0.0, posinf=1.0, neginf=0.0)
    # Vectorised ramp: precompute 256 LUT entries rather than looping per pixel.
    lut = np.array([_ramp_color(i / 255.0, stops) for i in range(256)], dtype="uint8")
    idx = (scaled * 255).astype("uint8")
    rgb[finite] = lut[idx[finite]]
    Image.fromarray(rgb, mode="RGB").save(path, format="PNG", optimize=True)
    return path


def raster_meta(path: str) -> dict[str, Any]:
    try:
        with rasterio.open(remote_path(path)) as ds:
            return {
                "crs": str(ds.crs),
                "width": ds.width,
                "height": ds.height,
                "bands": ds.count,
                "dtype": ds.dtypes[0],
                "res": [float(v) for v in ds.res],
            }
    except RasterioIOError as exc:
        raise AppError(NO_IMAGERY, f"could not open raster: {exc}") from exc


def require_polygon(geometry: dict[str, Any]) -> BaseGeometry:
    geom, _ = _load_geom(geometry)
    if geom.geom_type not in ("Polygon", "MultiPolygon"):
        raise AppError(INVALID_ARGUMENTS, "expected a polygonal AOI")
    return geom
