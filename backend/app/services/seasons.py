"""Same month, several years: the comparison a single-season snapshot cannot make.

"Did this area get greener or more built-up?" cannot be answered by two dates a
fortnight apart — that measures the season. The standard fix is to compare the
same calendar month across years, so phenology and sun angle are held roughly
constant.

What this module CANNOT do, and says so, is build a pixel-wise median
composite. The image service silently ignores `mosaicMethod: median` (probed
2026-11-13: it returns the identical value to the nonsense method "banana",
while `esriMosaicNadir` returns a different one, so the rule is parsed but the
method is invalid). What is available is the median across ACQUISITIONS of the
AOI statistic, which is what `median` below means throughout.

The other limit is coverage, and it is not ours to fix: the Sentinel-2 source
holds a rolling ~14-month window, so at most two instances of the same month
exist at any time. A year-by-year series from this service is therefore short by
construction, and the response states that rather than implying a long record.
"""
from __future__ import annotations

import calendar
from datetime import date
from statistics import median
from typing import Any

from app import config
from app.errors import AOI_TOO_LARGE, INVALID_ARGUMENTS, NO_IMAGERY, AppError
from app.services import areas, indices
from app.services.download import acquisitions
from app.services.geo import bbox_area_km2, bbox_span_km, geometry_area_km2
from app.services.net import Fetch
from app.services.providers import arcgis


def _window(year: int, month: int) -> tuple[str, str]:
    last = calendar.monthrange(year, month)[1]
    return f"{year}-{month:02d}-01", f"{year}-{month:02d}-{last:02d}"


def _median_row(rows: list[dict[str, Any]], note_when_missing: str) -> dict[str, Any]:
    """Median of the per-acquisition means, plus the spread that produced it."""
    values = sorted(r["mean"] for r in rows if r.get("mean") is not None)
    if not values:
        return {"median": None, "min": None, "max": None, "spread": None, "note": note_when_missing}
    mid = len(values) // 2
    centre = values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2
    return {
        "median": round(centre, 4),
        "min": round(values[0], 4),
        "max": round(values[-1], 4),
        "spread": round(values[-1] - values[0], 4),
    }


async def build(
    fetch: Fetch,
    *,
    bbox: list[float],
    index: str = "ndvi",
    month: int | None = None,
    years: int | None = None,
    max_cloud: float | None = 40.0,
    geometry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    spec = indices.definition(index)  # raises UNKNOWN_INDEX on a typo
    function = spec.get("arcgis_function")
    if not function:
        raise AppError(INVALID_ARGUMENTS, f"No raster function is published for {spec['label']}.")

    today = date.today()
    month = today.month if month is None else int(month)
    if not 1 <= month <= 12:
        raise AppError(INVALID_ARGUMENTS, "month must be between 1 and 12")

    wanted = config.SEASONAL_MAX_YEARS if years is None else int(years)
    if wanted < 1:
        raise AppError(INVALID_ARGUMENTS, "years must be at least 1")
    wanted = min(wanted, config.SEASONAL_MAX_YEARS)

    span = bbox_span_km(bbox)
    if span > config.MAX_STATS_SPAN_KM:
        raise AppError(
            AOI_TOO_LARGE,
            f"AOI is {span:,.0f} km across; the statistics service allows "
            f"{config.MAX_STATS_SPAN_KM:,.0f} km per side.",
        )

    aoi_km2 = geometry_area_km2(geometry) if geometry is not None else bbox_area_km2(bbox)
    series: list[dict[str, Any]] = []
    skipped_years: list[dict[str, Any]] = []

    for year in range(today.year - wanted + 1, today.year + 1):
        date_from, date_to = _window(year, month)
        picks = await acquisitions(
            fetch,
            bbox=bbox,
            date_from=date_from,
            date_to=date_to,
            max_cloud=max_cloud,
            max_images=config.SEASONAL_MAX_SCENES,
        )
        if not picks:
            skipped_years.append({"year": year, "reason": "no acquisition in this month"})
            continue

        rows: list[dict[str, Any]] = []
        area_samples: list[dict[str, Any] | None] = []
        for pick in picks:
            try:
                stats_kwargs: dict[str, Any] = {
                    "bbox": bbox,
                    "raster_function": str(function),
                    "objectid": pick.objectid,
                }
                if geometry is not None:
                    stats_kwargs["geometry"] = geometry
                stats = await arcgis.index_statistics(fetch, **stats_kwargs)
            except AppError:
                continue
            if not stats or not stats.get("count"):
                continue
            estimate = areas.estimate(index, stats.get("histogram"), aoi_km2)
            area_samples.append(estimate)
            rows.append(
                {
                    "date": pick.day,
                    "objectid": pick.objectid,
                    "cloud_cover_percent": pick.cloud_cover,
                    "mean": float(stats["mean"]) if stats.get("mean") is not None else None,
                    "pixels": int(stats.get("count") or 0),
                }
            )

        if not rows:
            skipped_years.append({"year": year, "reason": "acquisitions returned no valid pixels"})
            continue

        summary = _median_row(rows, "no usable mean")
        series.append(
            {
                "year": year,
                "month": month,
                "acquisitions": len(rows),
                "used": rows,
                "median": summary["median"],
                "min": summary["min"],
                "max": summary["max"],
                "spread": summary["spread"],
                "areas": areas.median_areas(area_samples),
            }
        )

    if not series:
        raise AppError(
            NO_IMAGERY,
            f"No usable {spec['label']} acquisition in month {month:02d} across the "
            "requested years. The source holds a rolling ~14-month window.",
        )

    change: dict[str, Any] | None = None
    if len(series) >= 2 and series[0]["median"] is not None and series[-1]["median"] is not None:
        first, last = series[0], series[-1]
        change = {
            "first_year": first["year"],
            "last_year": last["year"],
            "first": first["median"],
            "last": last["median"],
            "delta": round(last["median"] - first["median"], 4),
            "interpretation": (
                "difference between the medians of two same-month windows, not a fitted trend"
            ),
        }
        if first.get("areas") and last.get("areas"):
            change["areas"] = areas.change(first["areas"], last["areas"])

    notes = [
        f"Each year is the median of {config.SEASONAL_MAX_SCENES} acquisition(s) at most in "
        f"month {month:02d}, not the mean of one scene and not a pixel-wise composite.",
        "The statistics service ignores a median mosaic rule, so pixels are never averaged "
        "across scenes; only the AOI statistic is.",
        "The source holds a rolling ~14-month window, so at most two instances of any month "
        "exist. This is a comparison of two seasons, not a multi-year trend.",
        "Cloud cover and the number of acquisitions differ per year; `spread` shows how much "
        "the within-month scenes disagreed.",
    ]
    if len(series) == 1:
        notes.append(
            f"Only {series[0]['year']} has data in this month, so no change is reported."
        )

    return {
        "index": index.lower(),
        "label": spec["label"],
        "formula": spec["formula"],
        "month": month,
        "bbox": [round(v, 6) for v in bbox],
        "aoi_km2": round(aoi_km2, 1),
        "count": len(series),
        "series": series,
        "skipped_years": skipped_years,
        "change": change,
        "notes": notes,
    }
