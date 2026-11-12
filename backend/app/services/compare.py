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

from typing import Any

from app.errors import INVALID_ARGUMENTS, NO_IMAGERY, AppError
from app.services import indices
from app.services.download import acquisitions, export_size
from app.services.net import Fetch
from app.services.providers import arcgis


async def _one(
    fetch: Fetch,
    *,
    bbox: list[float],
    spec: dict[str, Any],
    day: str,
    max_cloud: float | None,
    rendering: str | None,
    size: tuple[int, int],
) -> dict[str, Any]:
    picks = await acquisitions(
        fetch,
        bbox=bbox,
        date_from=day,
        date_to=day,
        max_cloud=max_cloud,
        max_images=1,
    )
    if not picks:
        raise AppError(
            NO_IMAGERY,
            f"No Sentinel-2 acquisition covers this AOI on {day}. Pick another date "
            "inside the ~14-month window, or widen the cloud tolerance.",
        )

    pick = picks[0]
    stats = await arcgis.index_statistics(
        fetch,
        bbox=bbox,
        raster_function=str(spec["arcgis_function"]),
        objectid=pick.objectid,
    )
    if not stats or not stats.get("count"):
        raise AppError(NO_IMAGERY, f"The acquisition on {day} has no valid pixels over this AOI.")

    def value(key: str) -> float | None:
        raw = stats.get(key)
        return None if raw is None else float(raw)

    return {
        "date": day,
        "objectid": pick.objectid,
        "cloud_cover_percent": pick.cloud_cover,
        "mean": value("mean"),
        "min": value("min"),
        "max": value("max"),
        "count": int(stats.get("count") or 0),
        "image_url": arcgis.scene_url(
            bbox, objectid=pick.objectid, rendering_rule=rendering, size=size
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
    )
    after = await _one(
        fetch, bbox=bbox, spec=spec, day=date_b,
        max_cloud=max_cloud, rendering=rendering, size=size,
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
