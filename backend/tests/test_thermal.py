"""Offline tests for the Landsat brightness-temperature service.

Two things are being pinned down. First the calibration: a Level-1 thermal DN
must go through radiance and the inverse Planck function with the right sensor's
constants, and the AOI mean must be taken over the histogram rather than from the
mean DN. Second the cloud rule: a scene whose AOI reads at cloud-top temperature
is rejected and listed, not averaged into the year.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app import config
from app.errors import AppError
from app.services import thermal
from app.services.providers import landsat

BB = [78.4623, 17.4086, 78.4868, 17.4374]


def dn(value: float, count: int = 1000) -> dict:
    """A one-bin histogram centred on `value`."""
    return {"mean": value, "min": value - 5, "max": value + 5, "count": count,
            "histogram": {"counts": [count], "min": value - 5, "max": value + 5}}


def scene(oid: int, day: str, cloud: float = 1.0, name: str = "LC08_L1TP_144048_20251103_20251103_02_T1"):
    return {
        "objectid": oid,
        "day": day,
        "datetime": f"{day}T05:00:00+00:00",
        "cloud_cover_percent": cloud,
        "name": name,
        "sensor": name[:4],
    }


def run(monkeypatch, *, by_year, stats_by_oid, month=11, years=2):
    async def fake_search(fetch, *, bbox, date_from, date_to, limit=40):
        return by_year.get(date_from.year, [])

    async def fake_band(fetch, *, bbox, band_index, objectid=None):
        return stats_by_oid.get(objectid)

    monkeypatch.setattr(thermal.landsat, "search", fake_search)
    monkeypatch.setattr(thermal.landsat, "band_statistics", fake_band)

    return asyncio.run(
        thermal.build(object(), bbox=BB, month=month, years=years)  # type: ignore[arg-type]
    )


def test_dn_to_brightness_temperature_uses_radiance_and_planck():
    # DN 30000 -> L = 10.03 W m-2 sr-1 um-1 -> ~303 K -> ~30 C for Landsat 8.
    value = landsat.brightness_temperature_c(30000, "LC08")
    assert value == pytest.approx(29.9, abs=0.5)
    # Colder DN is colder temperature, and a zero DN has no answer at all.
    assert landsat.brightness_temperature_c(10000, "LC08") < value
    assert landsat.brightness_temperature_c(0, "LC08") is None


def test_the_two_sensors_use_their_own_constants():
    l8 = landsat.brightness_temperature_c(30000, "LC08")
    l9 = landsat.brightness_temperature_c(30000, "LC09")
    assert l8 != l9
    # A real difference, not a shared constant — and not large enough to be a
    # units error either.
    assert 0.1 < abs(l8 - l9) < 5.0


def test_mean_is_taken_over_the_histogram_not_the_dn_mean():
    # Two bins, 28000 and 32000 DN, equal counts. The mean DN is 30000, but the
    # Planck curve is non-linear, so the mean of the two temperatures is lower
    # than the temperature of the mean DN.
    stats = {
        "mean": 30000,
        "min": 27995,
        "max": 32005,
        "count": 2000,
        "histogram": {"counts": [1000, 1000], "min": 27995, "max": 32005},
    }
    by_bins = landsat.mean_brightness_temperature_c(stats, "LC08")
    by_mean = landsat.brightness_temperature_c(30000, "LC08")
    assert by_bins is not None and by_mean is not None
    assert by_bins < by_mean
    assert by_bins == pytest.approx(
        (landsat.brightness_temperature_c(29000, "LC08")
         + landsat.brightness_temperature_c(31000, "LC08")) / 2, abs=0.2
    )


def test_a_year_reports_the_median_and_the_spread(monkeypatch):
    result = run(
        monkeypatch,
        by_year={2025: [scene(1, "2025-11-03"), scene(2, "2025-11-18")],
                 2026: [scene(3, "2026-11-05")]},
        stats_by_oid={1: dn(28000), 2: dn(30000), 3: dn(32000)},
    )
    assert result["count"] == 2
    assert result["series"][0]["acquisitions"] == 2
    assert result["series"][0]["spread_c"] > 0
    assert result["change"]["delta_c"] > 0
    assert "NOT land surface temperature" in result["notes"][0]


def test_a_cloud_top_scene_is_rejected_and_listed(monkeypatch):
    result = run(
        monkeypatch,
        by_year={2026: [scene(1, "2026-11-03", cloud=0.9), scene(2, "2026-11-08", cloud=1.0)]},
        stats_by_oid={1: dn(11800), 2: dn(30000)},  # 11800 DN ~ -23 C: cloud top
        years=1,
    )
    year = result["series"][0]
    assert year["acquisitions"] == 1
    assert year["median_c"] == pytest.approx(29.9, abs=0.6)
    assert year["rejected"][0]["objectid"] == 1
    assert "cloud-contaminated" in year["rejected"][0]["reason"]


def test_a_year_with_only_cloud_is_skipped_with_the_reason(monkeypatch):
    result = run(
        monkeypatch,
        by_year={2025: [scene(1, "2025-11-03"), scene(2, "2025-11-18")],
                 2026: [scene(3, "2026-11-05")]},
        stats_by_oid={1: dn(11800), 2: dn(12000), 3: dn(30000)},
    )
    assert result["count"] == 1
    assert result["skipped_years"][0]["year"] == 2025
    assert result["change"] is None


def test_records_named_lc09_use_the_landsat_nine_branch(monkeypatch):
    result = run(
        monkeypatch,
        by_year={2026: [scene(9, "2026-11-03", name="LC09_L1TP_144048_20261103_20261103_02_T1")]},
        stats_by_oid={9: dn(30000)},
        years=1,
    )
    reading = result["series"][0]["clearest"]
    assert reading["sensor"] == "LC09"
    assert reading["value_c"] == pytest.approx(landsat.brightness_temperature_c(30000, "LC09"), abs=0.01)


def test_no_usable_year_is_a_coverage_answer(monkeypatch):
    with pytest.raises(AppError) as exc:
        run(monkeypatch, by_year={}, stats_by_oid={})
    assert exc.value.code == "no_imagery"


def test_the_plausible_range_is_the_one_config_declares():
    assert config.THERMAL_PLAUSIBLE_C[0] < 0 < config.THERMAL_PLAUSIBLE_C[1]
