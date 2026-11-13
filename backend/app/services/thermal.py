"""Thermal change over years: same month, Landsat 8/9, brightness temperature.

This exists because the Sentinel-2 source cannot answer a temperature question at
all — it has no thermal band — and because the question "did this get hotter?"
needs years, not the 14 months that source holds. Landsat 8/9 goes back to 2020
on the service probed here, so the same calendar month can be compared across up
to five years.

The claim this module makes is deliberately narrow: **at-sensor brightness
temperature**, in °C, averaged over the AOI. Not land surface temperature. LST
would need per-pixel emissivity and an atmospheric correction, neither of which
this endpoint offers; the difference is a few kelvin and it is not hidden.

Cloud is the dominant error, and it is measured rather than assumed. Catalogue
cloud cover is a whole-scene figure, so a scene labelled 0.9% cloudy averaged
-12 °C over a small AOI (cloud top) while a clear scene over the same water read
+26 °C. Every scene is therefore checked against a physical range, rejected
scenes are listed with their reason, and the spread across a year's scenes is
returned so the reader can see how much the number wobbles.
"""
from __future__ import annotations

import calendar
from datetime import date
from statistics import median
from typing import Any

from app import config
from app.errors import INVALID_ARGUMENTS, NO_IMAGERY, AppError
from app.services.geo import bbox_area_km2, geometry_area_km2
from app.services.net import Fetch
from app.services.providers import landsat


def _window(year: int, month: int) -> tuple[date, date]:
    last = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last)


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    centre = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2
    return round(centre, 2)


async def build(
    fetch: Fetch,
    *,
    bbox: list[float],
    month: int | None = None,
    years: int | None = None,
    max_scenes: int | None = None,
    geometry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    today = date.today()
    month = today.month if month is None else int(month)
    if not 1 <= month <= 12:
        raise AppError(INVALID_ARGUMENTS, "month must be between 1 and 12")

    wanted = config.THERMAL_MAX_YEARS if years is None else int(years)
    if wanted < 1:
        raise AppError(INVALID_ARGUMENTS, "years must be at least 1")
    wanted = min(wanted, config.THERMAL_MAX_YEARS)

    limit = config.THERMAL_MAX_SCENES if max_scenes is None else int(max_scenes)
    limit = max(1, min(limit, config.THERMAL_MAX_SCENES))

    low, high = config.THERMAL_PLAUSIBLE_C
    series: list[dict[str, Any]] = []
    skipped_years: list[dict[str, Any]] = []

    for year in range(today.year - wanted + 1, today.year + 1):
        start, end = _window(year, month)
        scenes = await landsat.search(fetch, bbox=bbox, date_from=start, date_to=end)
        if not scenes:
            skipped_years.append({"year": year, "reason": "no Landsat acquisition in this month"})
            continue

        # Clearest first, so the limited request budget goes to usable scenes.
        scenes.sort(key=lambda s: 999.0 if s["cloud_cover_percent"] is None else float(s["cloud_cover_percent"]))

        readings: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for scene in scenes[:limit]:
            try:
                stats_kwargs: dict[str, Any] = {
                    "bbox": bbox,
                    "band_index": config.LANDSAT_THERMAL_BAND,
                    "objectid": scene["objectid"],
                }
                if geometry is not None:
                    stats_kwargs["geometry"] = geometry
                stats = await landsat.band_statistics(fetch, **stats_kwargs)
            except AppError as exc:
                rejected.append({**scene, "reason": str(exc)[:160]})
                continue
            if not stats or not stats.get("count"):
                rejected.append({**scene, "reason": "no valid pixels over this AOI"})
                continue

            temp = landsat.mean_brightness_temperature_c(stats, scene["sensor"])
            if temp is None:
                rejected.append({**scene, "reason": "thermal band could not be calibrated"})
                continue
            if not (low <= temp <= high):
                rejected.append(
                    {
                        **scene,
                        "reason": (
                            f"{temp:.1f} C is outside the plausible range {low:g}..{high:g} C; "
                            "cloud tops read about -23 C, so this scene is cloud-contaminated"
                        ),
                        "value_c": round(temp, 2),
                    }
                )
                continue

            readings.append({**scene, "value_c": round(temp, 2), "pixels": int(stats.get("count") or 0)})

        if not readings:
            skipped_years.append(
                {"year": year, "reason": "every acquisition was unusable", "rejected": rejected}
            )
            continue

        values = [r["value_c"] for r in readings]
        clearest = readings[0]
        series.append(
            {
                "year": year,
                "month": month,
                "acquisitions": len(readings),
                "clearest": clearest,
                "median_c": _median(values),
                "min_c": round(min(values), 2),
                "max_c": round(max(values), 2),
                "spread_c": round(max(values) - min(values), 2),
                "readings": readings,
                "rejected": rejected,
            }
        )

    if not series:
        raise AppError(
            NO_IMAGERY,
            f"No usable Landsat thermal acquisition in month {month:02d} across the "
            "requested years.",
        )

    change: dict[str, Any] | None = None
    if len(series) >= 2:
        first, last = series[0], series[-1]
        if first["median_c"] is not None and last["median_c"] is not None:
            change = {
                "first_year": first["year"],
                "last_year": last["year"],
                "first_c": first["median_c"],
                "last_c": last["median_c"],
                "delta_c": round(last["median_c"] - first["median_c"], 2),
                "basis": "median of the year's usable scenes, same calendar month",
                "interpretation": (
                    "difference between two month-long samples, not a climate trend; "
                    "land-cover change and cloud both move it"
                ),
            }

    notes = [
        "At-sensor brightness temperature from Landsat 8/9 Level-1 TIRS, NOT land surface "
        "temperature: no emissivity or atmospheric correction is applied.",
        "Landsat crosses around 10:30 local solar time, so values are comparable between "
        "years but are not daily means and are not air temperature.",
        "Scenes reading outside the plausible range are rejected as cloud-contaminated and "
        "listed under `rejected`; cloud cover in the catalogue is a whole-scene figure and "
        "is not reliable over a small AOI.",
        f"At most {config.THERMAL_MAX_SCENES} scenes per year are read, clearest first.",
    ]

    return {
        "variable": "brightness_temperature",
        "label": "At-sensor brightness temperature",
        "unit": "°C",
        "month": month,
        "bbox": [round(v, 6) for v in bbox],
        "aoi_km2": round(
            geometry_area_km2(geometry) if geometry is not None else bbox_area_km2(bbox), 1
        ),
        "source": {
            "provider": landsat.NAME,
            "service": config.LANDSAT_URL,
            "sensors": ["Landsat 8 OLI/TIRS", "Landsat 9 OLI-2/TIRS-2"],
            "band": "ThermalInfrared1 (B10), 100 m native",
            "coverage": "2020 to present",
        },
        "count": len(series),
        "series": series,
        "skipped_years": skipped_years,
        "change": change,
        "notes": notes,
    }
