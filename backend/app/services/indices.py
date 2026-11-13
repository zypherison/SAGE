"""Deterministic index engine.

Every number here comes from a documented raster function on an authoritative
service — never from a language model. This is the architectural rule defended
in SEMINAR2.md §3.5 and justified by [45] (best agent 32.7% on exact ground
truth) and [47] (code-first beat black-box 50% vs 0%).

QC is explicit: if too few valid pixels come back, we return
`insufficient_data` rather than a number that looks authoritative but is not.
"""
from __future__ import annotations

from app import config
from app.errors import AOI_TOO_LARGE, UNKNOWN_INDEX, AppError
from app.services import areas
from app.services.geo import bbox_area_km2, bbox_span_km, geometry_area_km2, validate_bbox
from app.services.net import Fetch
from app.services.providers import arcgis

# Kept for backwards compatibility with callers that still ask for the old name.
UnsupportedIndex = AppError


def definition(index: str) -> dict:
    key = (index or "").lower()
    if key not in config.INDEX_DEFS:
        raise AppError(
            UNKNOWN_INDEX,
            f"unknown index '{index}'; supported: {sorted(config.INDEX_DEFS)}",
        )
    return config.INDEX_DEFS[key]


async def compute(
    fetch: Fetch, *, index: str, bbox: list[float], geometry: dict | None = None
) -> dict:
    """Return a normalized result dict; never raises for data-quality issues."""
    # An unknown index is a bad request, not a data condition. Letting the coded
    # UNKNOWN_INDEX error propagate keeps the vocabulary honest: previously this
    # was caught and returned as HTTP 200 "unsupported", which told a caller its
    # typo was a data problem.
    spec = definition(index)

    key = index.lower()
    try:
        bbox = validate_bbox(bbox)
    except ValueError as exc:
        return {
            "index": key,
            "label": spec["label"],
            "formula": spec["formula"],
            "status": "insufficient_data",
            "message": str(exc),
            "stats": None,
            "histogram": None,
            "visual_png_url": None,
        }

    # The drawn polygon's own area, so a winding AOI is not charged for the
    # corners of its envelope. Falls back to the bbox area for a rectangle.
    area = geometry_area_km2(geometry) if geometry is not None else bbox_area_km2(bbox)
    span = bbox_span_km(bbox)

    # Span, not area. The service renders at native 10 m, so a 45x5 km strip is
    # refused while a 38x38 km box succeeds (measured — see config).
    if span > config.MAX_STATS_SPAN_KM:
        return {
            "index": key,
            "label": spec["label"],
            "formula": spec["formula"],
            "status": "insufficient_data",
            "message": (
                f"AOI is {span:,.0f} km across ({area:,.0f} km²). The statistics service "
                f"renders at 10 m and has a {config.MAX_STATS_SPAN_KM:,.0f} km per-side "
                "limit, so draw a smaller box."
            ),
            "stats": None,
            "histogram": None,
            "visual_png_url": arcgis.visual_url(bbox, str(spec["arcgis_visual"])),
        }

    function = spec.get("arcgis_function")
    if not function:
        return {
            "index": key,
            "label": spec["label"],
            "formula": spec["formula"],
            "status": "unsupported",
            "message": f"No raster function is published for {spec['label']}.",
            "stats": None,
            "histogram": None,
            "visual_png_url": None,
        }

    try:
        kwargs: dict = {"bbox": bbox, "raster_function": str(function)}
        if geometry is not None:
            kwargs["geometry"] = geometry
        raw = await arcgis.index_statistics(fetch, **kwargs)
    except Exception as exc:
        detail = str(exc)
        # The provider states this in an `error` object with HTTP 200, so it is
        # not a transport failure — it is a size answer and must read like one.
        if "exceeds the size limit" in detail:
            message = (
                f"The statistics service refused this AOI as too large ({span:,.0f} km "
                f"across); the limit is {config.MAX_STATS_SPAN_KM:,.0f} km per side."
            )
        else:
            message = f"Index service error: {detail[:200]}"
        return {
            "index": key,
            "label": spec["label"],
            "formula": spec["formula"],
            "status": "insufficient_data" if "exceeds the size limit" in detail else "error",
            "message": message,
            "stats": None,
            "histogram": None,
            "visual_png_url": arcgis.visual_url(bbox, str(spec["arcgis_visual"])),
        }

    if not raw or not raw.get("count"):
        return {
            "index": key,
            "label": spec["label"],
            "formula": spec["formula"],
            "status": "insufficient_data",
            "message": "No valid pixels returned for this area and date window.",
            "stats": None,
            "histogram": None,
            "visual_png_url": arcgis.visual_url(bbox, str(spec["arcgis_visual"])),
        }

    # expected pixel count at 10 m resolution, used for the validity gate
    expected = max(1.0, (area * 1_000_000) / (10.0 * 10.0))
    valid_fraction = min(1.0, raw["count"] / expected)
    if valid_fraction < config.MIN_VALID_PIXEL_FRACTION:
        return {
            "index": key,
            "label": spec["label"],
            "formula": spec["formula"],
            "status": "insufficient_data",
            "message": (
                f"Only {valid_fraction:.0%} of the expected pixels were valid "
                f"(threshold {config.MIN_VALID_PIXEL_FRACTION:.0%})."
            ),
            "stats": None,
            "histogram": None,
            "visual_png_url": arcgis.visual_url(bbox, str(spec["arcgis_visual"])),
        }

    lo, hi = spec["range"]
    stats = raw["stats"] if "stats" in raw else raw
    return {
        "index": key,
        "label": spec["label"],
        "formula": spec["formula"],
        "status": "ok",
        "message": None,
        "stats": {
            "min": max(lo, float(stats["min"])),
            "max": min(hi, float(stats["max"])),
            "mean": float(stats["mean"]),
            "std": stats.get("std"),
            "median": stats.get("median"),
            "count": int(stats["count"]),
            "source": arcgis.NAME,
            "raster_function": str(function),
        },
        "histogram": raw.get("histogram"),
        # The histogram is already in this response, so the class areas cost no
        # extra request. They are a second reading of the same pixels, not an
        # independent measurement of land cover.
        "areas": areas.estimate(key, raw.get("histogram"), area),
        "visual_png_url": arcgis.visual_url(bbox, str(spec["arcgis_visual"])),
    }
