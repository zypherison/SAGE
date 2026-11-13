"""Offline tests for the same-month-across-years comparison.

The important claims are narrow: each year is a median across that month's
acquisitions, a year with one scene is reported as one scene rather than a
trend, and the response never pretends to be a pixel-wise composite (the source
silently ignores a median mosaic rule).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app import config
from app.errors import AppError
from app.services import seasons

BB = [78.3, 17.3, 78.6, 17.5]
# 4 bins over [-1, 1]: centres -0.75, -0.25, 0.25, 0.75. 50% of pixels are
# above NDVI 0.30 in the "green" histogram.
GREEN = {"counts": [0, 100, 0, 100], "min": -1.0, "max": 1.0}
BROWN = {"counts": [100, 100, 0, 0], "min": -1.0, "max": 1.0}


def pick(oid: int, day: str, cloud: float = 1.0):
    return SimpleNamespace(objectid=oid, day=day, cloud_cover=cloud)


def run(monkeypatch, *, by_year, month=11, years=2, histograms=None):
    async def fake_acquisitions(fetch, *, bbox, date_from, date_to, max_cloud, max_images):
        year = int(date_from[:4])
        return by_year.get(year, [])

    async def fake_stats(fetch, *, bbox, raster_function, objectid=None):
        if histograms is None:
            return {"count": 1000, "mean": 0.2, "min": 0.0, "max": 0.4, "histogram": GREEN}
        histogram, mean = histograms[objectid]
        if histogram is None:
            return None
        return {"count": 1000, "mean": mean, "min": 0.0, "max": 0.4, "histogram": histogram}

    monkeypatch.setattr(seasons, "acquisitions", fake_acquisitions)
    monkeypatch.setattr(seasons.arcgis, "index_statistics", fake_stats)

    return asyncio.run(
        seasons.build(object(), bbox=BB, index="ndvi", month=month, years=years)  # type: ignore[arg-type]
    )


def test_each_year_is_the_median_of_its_acquisitions(monkeypatch):
    result = run(
        monkeypatch,
        by_year={
            2025: [pick(1, "2025-11-03"), pick(2, "2025-11-18"), pick(3, "2025-11-27")],
            2026: [pick(4, "2026-11-05")],
        },
        years=2,
        histograms={
            1: (GREEN, 0.10),
            2: (GREEN, 0.20),
            3: (GREEN, 0.30),
            4: (BROWN, 0.05),
        },
    )

    first = result["series"][0]
    assert first["year"] == 2025
    assert first["acquisitions"] == 3
    assert first["median"] == 0.2  # mean of the three would be 0.2 here
    assert first["min"] == 0.1 and first["max"] == 0.3
    assert first["spread"] == pytest.approx(0.2)


def test_the_median_ignores_a_single_outlier_scene(monkeypatch):
    result = run(
        monkeypatch,
        by_year={2026: [pick(1, "2026-11-03"), pick(2, "2026-11-08"), pick(3, "2026-11-12")]},
        years=1,
        histograms={1: (GREEN, -0.40), 2: (GREEN, 0.20), 3: (GREEN, 0.25)},
    )
    assert result["series"][0]["median"] == 0.2
    assert result["series"][0]["min"] == -0.4


def test_class_areas_are_carried_per_year_and_compared(monkeypatch):
    result = run(
        monkeypatch,
        by_year={2025: [pick(1, "2025-11-03")], 2026: [pick(2, "2026-11-05")]},
        years=2,
        histograms={1: (GREEN, 0.2), 2: (BROWN, 0.1)},
    )
    areas_2025 = result["series"][0]["areas"]["classes"][0]
    areas_2026 = result["series"][1]["areas"]["classes"][0]
    assert areas_2025["area_km2"] > 0
    assert areas_2026["area_km2"] == 0.0
    change = result["change"]["areas"][0]
    assert change["name"] == "vegetation"
    assert change["direction"] == "decrease"
    assert change["change_km2"] < 0


def test_a_missing_year_is_reported_not_silently_dropped(monkeypatch):
    result = run(
        monkeypatch,
        by_year={2025: [pick(1, "2025-11-03")]},
        years=2,
    )
    assert result["count"] == 1
    assert result["change"] is None
    assert result["skipped_years"][0]["year"] == 2026
    assert any("rolling" in n for n in result["notes"])


def test_no_usable_year_is_a_coverage_answer(monkeypatch):
    with pytest.raises(AppError) as exc:
        run(monkeypatch, by_year={}, years=2)
    assert exc.value.code == "no_imagery"


def test_month_outside_one_to_twelve_is_rejected(monkeypatch):
    with pytest.raises(AppError) as exc:
        run(monkeypatch, by_year={}, month=13)
    assert exc.value.code == "invalid_arguments"


def test_the_window_follows_the_requested_month(monkeypatch):
    seen: list[tuple[str, str]] = []

    async def fake_acquisitions(fetch, *, bbox, date_from, date_to, max_cloud, max_images):
        seen.append((date_from, date_to))
        return []

    monkeypatch.setattr(seasons, "acquisitions", fake_acquisitions)
    with pytest.raises(AppError):
        asyncio.run(seasons.build(object(), bbox=BB, index="ndvi", month=2, years=1))  # type: ignore[arg-type]

    assert seen[0][0].endswith("-02-01")
    # A leap year and a non-leap year both end on the true last day of February.
    assert seen[0][1].endswith("-02-28") or seen[0][1].endswith("-02-29")


def test_the_years_cap_limits_the_number_of_windows(monkeypatch):
    years_seen: list[int] = []

    async def fake_acquisitions(fetch, *, bbox, date_from, date_to, max_cloud, max_images):
        years_seen.append(int(date_from[:4]))
        return []

    monkeypatch.setattr(seasons, "acquisitions", fake_acquisitions)
    with pytest.raises(AppError):
        asyncio.run(seasons.build(object(), bbox=BB, index="ndvi", month=11, years=99))  # type: ignore[arg-type]

    assert len(years_seen) == config.SEASONAL_MAX_YEARS
    assert years_seen == sorted(years_seen)
