"""Class areas: turn an index histogram into km².

`computeStatisticsHistograms` already returns, in the same response the mean
comes from, 256 bins spanning the observed index range over the AOI. A threshold
on those bins gives the share of pixels on the "water" or "vegetation" side, and
share × AOI area is an area in km².

Why this matters more than the mean: "NDVI rose 0.03" is not a result a jury of
examiners will accept as "vegetation increased". "2.3 km² more of this AOI
crossed the vegetation threshold between the two dates" is, provided the
threshold is stated and the binning error is carried alongside it.

Three honesty rules are enforced here:

  * the threshold is a DEFINITION, not a measurement, so every class carries the
    reason it was chosen and the threshold rides along in the response;
  * the bin edges come from the observed min/max, so the estimate cannot be
    finer than one bin — the boundary bin's pixels are reported as
    `uncertainty_km2` rather than pretended away;
  * the fraction is a share of *valid* pixels, not of the AOI, so an AOI with
    nodata edge pixels reports the area it actually observed.
"""
from __future__ import annotations

from typing import Any

from app import config


def _fraction(counts: list[int], lo: float, hi: float, threshold: float, above: bool) -> tuple[float, float]:
    """Return (share of pixels in the class, share sitting in the boundary bin)."""
    total = sum(counts)
    if total <= 0:
        return 0.0, 0.0

    bins = len(counts)
    width = (hi - lo) / bins if bins else 0.0
    if width <= 0:
        # A constant raster: every pixel is the same value, so the class is
        # all-or-nothing and there is no binning error to report.
        inside = (lo >= threshold) if above else (lo < threshold)
        return (1.0 if inside else 0.0), 0.0

    selected = 0
    boundary = 0
    for i, count in enumerate(counts):
        if not count:
            continue
        centre = lo + (i + 0.5) * width
        inside = (centre >= threshold) if above else (centre < threshold)
        if inside:
            selected += count
        # The bin whose edge straddles the threshold is where the estimate is
        # genuinely uncertain: those pixels could fall either side.
        edge_lo, edge_hi = lo + i * width, lo + (i + 1) * width
        if edge_lo <= threshold < edge_hi:
            boundary = count
    return selected / total, (boundary / total if boundary else 0.0)


def estimate(
    index: str,
    histogram: dict[str, Any] | None,
    aoi_km2: float,
) -> dict[str, Any] | None:
    """Class areas for one AOI, or None when the index has no thresholds defined."""
    specs = config.AREA_CLASSES.get((index or "").lower())
    if not specs:
        return None
    if not histogram:
        return None

    counts = histogram.get("counts") or []
    lo = histogram.get("min")
    hi = histogram.get("max")
    if not counts or lo is None or hi is None:
        return None

    total = sum(counts)
    if total <= 0 or aoi_km2 <= 0:
        return None

    classes: list[dict[str, Any]] = []
    for spec in specs:
        threshold = float(spec["threshold"])  # type: ignore[arg-type]
        above = bool(spec["above"])
        share, boundary = _fraction(counts, float(lo), float(hi), threshold, above)
        classes.append(
            {
                "name": spec["name"],
                "label": spec["label"],
                "threshold": threshold,
                "above": above,
                "basis": spec["basis"],
                "fraction": round(share, 4),
                "area_km2": round(share * aoi_km2, 2),
                "uncertainty_km2": round(boundary * aoi_km2, 2),
                "pixels": int(round(share * total)),
            }
        )

    bin_width = (float(hi) - float(lo)) / len(counts)
    return {
        "aoi_km2": round(aoi_km2, 1),
        "observed_pixels": int(total),
        "bin_width": round(bin_width, 4),
        "classes": classes,
        "method": (
            "share of valid pixels whose histogram bin centre crosses the threshold, "
            "multiplied by the AOI area"
        ),
        "notes": [
            "Thresholds are definitions applied to this index, not ground-truth land cover.",
            "The area assumes the AOI is fully covered; `observed_pixels` is the valid count.",
            "uncertainty_km2 is the boundary bin — the pixels that could fall either side.",
        ],
    }


def change(before: dict[str, Any] | None, after: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Per-class area difference between two estimates, in km²."""
    if not before or not after:
        return []
    by_name = {c["name"]: c for c in before.get("classes", [])}
    out: list[dict[str, Any]] = []
    for cls in after.get("classes", []):
        old = by_name.get(cls["name"])
        if old is None:
            continue
        delta = cls["area_km2"] - old["area_km2"]
        out.append(
            {
                "name": cls["name"],
                "label": cls["label"],
                "threshold": cls["threshold"],
                "from_km2": old["area_km2"],
                "to_km2": cls["area_km2"],
                "change_km2": round(delta, 2),
                "direction": "increase" if delta > 0 else ("decrease" if delta < 0 else "unchanged"),
                "uncertainty_km2": round(
                    max(cls.get("uncertainty_km2") or 0.0, old.get("uncertainty_km2") or 0.0), 2
                ),
            }
        )
    return out


def median_areas(samples: list[dict[str, Any] | None]) -> dict[str, Any] | None:
    """Median area per class across several acquisitions in the same window.

    A single acquisition can be half cloud, which drags a class area around. The
    median across the window's acquisitions is the robust summary, and it is
    still *not* a pixel-wise composite — nothing here averages pixels.
    """
    usable = [s for s in samples if s]
    if not usable:
        return None

    names: list[str] = []
    for sample in usable:
        for cls in sample.get("classes", []):
            if cls["name"] not in names:
                names.append(cls["name"])

    classes: list[dict[str, Any]] = []
    for name in names:
        rows = [c for s in usable for c in s.get("classes", []) if c["name"] == name]
        if not rows:
            continue
        areas = sorted(r["area_km2"] for r in rows)
        mid = len(areas) // 2
        median = areas[mid] if len(areas) % 2 else round((areas[mid - 1] + areas[mid]) / 2, 2)
        classes.append(
            {
                "name": name,
                "label": rows[0]["label"],
                "threshold": rows[0]["threshold"],
                "area_km2": round(median, 2),
                "min_km2": areas[0],
                "max_km2": areas[-1],
                "samples": len(areas),
            }
        )

    if not classes:
        return None
    return {
        "aoi_km2": usable[0]["aoi_km2"],
        "classes": classes,
        "aggregation": "median across acquisitions in the window, not a pixel-wise composite",
    }
