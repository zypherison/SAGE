"""Before/after: two dates, one AOI, the difference between them.

This is the question the secondary audience actually asks — "the lake shrank,
the landfill grew" — and it is not answerable by one composite. Each side is
pinned to a single acquisition by objectid (verified working on both
`computeStatisticsHistograms` and `exportImage`), so the two numbers describe
two real scenes rather than two renderings of the same mosaic.

Deliberately honest about asymmetry: a change between two dates can be caused by
the seasons, by cloud, or by the thing the user cares about. The response
returns the cloud cover of each side and a neutral delta, and does not label the
change as a trend or a cause.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from app import config
from app.errors import INVALID_ARGUMENTS, NO_IMAGERY, AppError
from app.services import indices
from app.services.download import export_size
from app.services.net import Fetch
from app.services.providers import arcgis


async def _nearest(
    fetch: Fetch,
    *,
    bbox: list[float],
    day: str,
    max_cloud: float | None,
    window_days: int,
) -> tuple[Any, str]:
    """The acquisition closest to the requested day, within a few days.

    Imagery lags by a day or two, so a literal "today" has no overpass and an
    exact-day lookup dead-ends the user for no useful reason. Snapping a short
    distance and reporting the date actually used is both simpler and more
    truthful than pretending the requested date exists.
    """
    requested = date.fromisoformat(day)
    scenes = await arcgis.search(
        fetch,
        bbox=bbox,
        geometry=None,
        date_from=(requested - timedelta(days=window_days)).isoformat(),
        date_to=(requested + timedelta(days=window_days)).isoformat(),
        max_cloud=max_cloud,
        mission="sentinel-2-l2a",
        limit=config.MAX_LIMIT,
    )

    best: dict[str, tuple[date, Any]] = {}
    for scene in scenes:
        if not scene.datetime:
            continue
        try:
            scene_day = datetime.fromisoformat(scene.datetime.replace("Z", "+00:00")).date()
        except ValueError:
            continue
        key = scene_day.isoformat()
        current = best.get(key)
        if current is None or (scene.cloud_cover or 999) < (current[1].cloud_cover or 999):
            best[key] = (scene_day, scene)

    if not best:
        raise AppError(
            NO_IMAGERY,
            f"No Sentinel-2 acquisition within {window_days} days of {day}. Pick a date "
            "inside the ~14-month window, or widen the cloud tolerance.",
        )

    scene_day, scene = min(best.values(), key=lambda item: abs((item[0] - requested).days))
    return scene, scene_day.isoformat()


async def _one(
    fetch: Fetch,
    *,
    bbox: list[float],
    spec: dict[str, Any],
    day: str,
    max_cloud: float | None,
    rendering: str | None,
    size: tuple[int, int],
    window_days: int,
) -> dict[str, Any]:
    pick, actual_day = await _nearest(
        fetch, bbox=bbox, day=day, max_cloud=max_cloud, window_days=window_days
    )

    stats = await arcgis.index_statistics(
        fetch,
        bbox=bbox,
        raster_function=str(spec["arcgis_function"]),
        objectid=pick.id,
    )
    if not stats or not stats.get("count"):
        raise AppError(NO_IMAGERY, f"The acquisition on {actual_day} has no valid pixels over this AOI.")

    def value(key: str) -> float | None:
        raw = stats.get(key)
        return None if raw is None else float(raw)

    return {
        "date": actual_day,
        "requested_date": day,
        "substituted": actual_day != day,
        "objectid": pick.id,
        "cloud_cover_percent": pick.cloud_cover,
        "mean": value("mean"),
        "min": value("min"),
        "max": value("max"),
        "count": int(stats.get("count") or 0),
        "image_url": arcgis.scene_url(
            bbox, objectid=pick.id, rendering_rule=rendering, size=size
        ),
        "width": size[0],
        "height": size[1],
    }


async def build(
    fetch: Fetch,
    *,
    bbox: list[float],
    index: str = "ndvi",
    date_a: str,
    date_b: str,
    max_cloud: float | None = 40.0,
) -> dict[str, Any]:
    if not date_a or not date_b:
        raise AppError(INVALID_ARGUMENTS, "Both dates are required.")
    if date_a == date_b:
        raise AppError(INVALID_ARGUMENTS, "Pick two different dates to compare.")
    if date_a > date_b:
        date_a, date_b = date_b, date_a  # order the pair rather than rejecting it

    spec = indices.definition(index)  # raises UNKNOWN_INDEX on a typo
    if not spec.get("arcgis_function"):
        raise AppError(INVALID_ARGUMENTS, f"No raster function is published for {spec['label']}.")

    size = export_size(bbox)
    rendering = str(spec["arcgis_visual"])

    before = await _one(
        fetch, bbox=bbox, spec=spec, day=date_a,
        max_cloud=max_cloud, rendering=rendering, size=size,
        window_days=config.COMPARE_WINDOW_DAYS,
    )
    after = await _one(
        fetch, bbox=bbox, spec=spec, day=date_b,
        max_cloud=max_cloud, rendering=rendering, size=size,
        window_days=config.COMPARE_WINDOW_DAYS,
    )

    delta = None
    if before["mean"] is not None and after["mean"] is not None:
        raw = after["mean"] - before["mean"]
        delta = {
            "mean": round(raw, 4),
            "absolute": round(abs(raw), 4),
            "direction": "increase" if raw > 0 else ("decrease" if raw < 0 else "unchanged"),
        }

    notes = [
        "Each side is one acquisition pinned by objectid, not the daily composite.",
        "A difference between two dates is not a trend and does not identify a cause; "
        "seasonality and cloud cover can both move it.",
    ]
    for side, label in ((before, "before"), (after, "after")):
        if side["substituted"]:
            notes.append(
                f"The {label} date {side['requested_date']} had no overpass; used the "
                f"nearest acquisition, {side['date']}."
            )
    if delta is None:
        notes.append("One side had no usable pixels, so no difference is reported.")
    clouds = [side["cloud_cover_percent"] for side in (before, after)]
    if all(c is not None and c > 20 for c in clouds):
        notes.append("Both sides are fairly cloudy; read the difference with care.")

    return {
        "index": index.lower(),
        "label": spec["label"],
        "formula": spec["formula"],
        "bbox": [round(v, 6) for v in bbox],
        "before": before,
        "after": after,
        "delta": delta,
        "notes": notes,
    }
