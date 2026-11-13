"""Index over time for one AOI — the question a single snapshot cannot answer.

"Did this area get greener or drier?" needs several dates, not one composite.
Each point here is **one real acquisition**, pinned by objectid, because the
statistics endpoint honours `lockRasterIds` (verified 2026-11-12 on the demo AOI:

    composite (no rule)   mean 0.1297
    2026-10-19 lockRaster mean 0.0975
    2026-11-03 lockRaster mean 0.0248
    2026-11-08 lockRaster mean 0.1297   <- the composite's least-cloudy pick

The last line is the useful sanity check: the composite equals the clearest
acquisition, which is exactly what the default mosaic rule claims to do.

Two honesty rules are enforced here:

  * the window is limited by the service's rolling ~14 months, and the response
    says so instead of implying a longer record exists;
  * the `change` block is the difference between the first and last acquisition,
    explicitly **not** a fitted trend — two points are not a regression.
"""
from __future__ import annotations

from typing import Any

from app import config
from app.errors import INVALID_ARGUMENTS, NO_IMAGERY, AppError
from app.services import areas, indices
from app.services.download import acquisitions
from app.services.geo import bbox_area_km2
from app.services.net import Fetch
from app.services.providers import arcgis


def _value(stats: dict[str, Any], key: str) -> float | None:
    raw = stats.get(key)
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


async def build(
    fetch: Fetch,
    *,
    bbox: list[float],
    index: str = "ndvi",
    date_from: str,
    date_to: str,
    max_points: int | None = None,
    max_cloud: float | None = 40.0,
) -> dict[str, Any]:
    spec = indices.definition(index)  # raises UNKNOWN_INDEX on a typo
    function = spec.get("arcgis_function")
    if not function:
        raise AppError(
            INVALID_ARGUMENTS, f"No raster function is published for {spec['label']}."
        )

    limit = config.TIMESERIES_MAX_POINTS if max_points is None else max_points
    if limit < 2:
        raise AppError(INVALID_ARGUMENTS, "A trend needs at least 2 points.")
    limit = min(limit, config.TIMESERIES_MAX_POINTS)

    picks = await acquisitions(
        fetch,
        bbox=bbox,
        date_from=date_from,
        date_to=date_to,
        max_cloud=max_cloud,
        max_images=limit,
    )
    if not picks:
        raise AppError(
            NO_IMAGERY,
            "No acquisition intersects this AOI in the requested window. The service "
            "holds a rolling ~14-month window, so older ranges return nothing by design.",
        )

    aoi_km2 = bbox_area_km2(bbox)
    points: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for pick in picks:
        try:
            stats = await arcgis.index_statistics(
                fetch,
                bbox=bbox,
                raster_function=str(function),
                objectid=pick.objectid,
            )
        except AppError as exc:
            skipped.append({"date": pick.day, "objectid": pick.objectid,
                            "status": "error", "reason": str(exc)[:200]})
            continue

        if not stats or not stats.get("count"):
            skipped.append({"date": pick.day, "objectid": pick.objectid,
                            "status": "no_data", "reason": "no valid pixels"})
            continue

        points.append(
            {
                "date": pick.day,
                "objectid": pick.objectid,
                "cloud_cover_percent": pick.cloud_cover,
                "mean": _value(stats, "mean"),
                "min": _value(stats, "min"),
                "max": _value(stats, "max"),
                "count": int(stats.get("count") or 0),
                # Free: the histogram already came back with the statistics.
                "areas": areas.estimate(index, stats.get("histogram"), aoi_km2),
            }
        )

    if not points:
        raise AppError(
            NO_IMAGERY,
            f"{len(skipped)} acquisition(s) matched but none returned valid pixels.",
        )

    points.sort(key=lambda p: p["date"])
    change = None
    if len(points) >= 2 and points[0]["mean"] is not None and points[-1]["mean"] is not None:
        change = {
            "first": points[0]["mean"],
            "last": points[-1]["mean"],
            "delta": round(points[-1]["mean"] - points[0]["mean"], 4),
            "between": [points[0]["date"], points[-1]["date"]],
            "interpretation": "difference between the first and last acquisition, not a fitted trend",
            "areas": areas.change(points[0].get("areas"), points[-1].get("areas")),
        }

    notes = [
        f"One statistics call per acquisition day ({len(points)} used, {len(skipped)} skipped).",
        "Each point is a single acquisition pinned by objectid, not the daily composite.",
        "The service holds a rolling ~14-month window, so the earliest points fall off over time.",
    ]
    if len(points) < 3:
        notes.append(
            "Fewer than 3 usable acquisitions: read this as two observations, not a trend."
        )

    return {
        "index": index.lower(),
        "label": spec["label"],
        "formula": spec["formula"],
        "bbox": [round(v, 6) for v in bbox],
        "date_from": date_from,
        "date_to": date_to,
        "count": len(points),
        "points": points,
        "skipped": skipped,
        "change": change,
        "notes": notes,
    }
