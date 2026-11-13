"""Offline tests for turning an index histogram into km².

The histogram is the only thing this module sees, so the tests are simple: known
bin counts, known thresholds, and an arithmetic check that the reported area is
share × AOI and that the boundary bin is reported as uncertainty rather than
folded into the estimate.
"""
from __future__ import annotations

from app.services import areas

# 4 bins over [-1, 1], so width 0.5 and centres -0.75, -0.25, 0.25, 0.75.
# NDVI's vegetation threshold is > 0.30, so bin 3 is inside and bin 2 (edges
# 0.0-0.5) straddles the threshold.
HIST = {"counts": [10, 10, 10, 70], "min": -1.0, "max": 1.0}


def test_area_is_share_times_the_aoi():
    result = areas.estimate("ndvi", HIST, aoi_km2=100.0)
    assert result is not None
    veg = result["classes"][0]
    assert veg["name"] == "vegetation"
    assert veg["fraction"] == 0.7
    assert veg["area_km2"] == 70.0
    assert veg["pixels"] == 70


def test_the_boundary_bin_is_reported_as_uncertainty_not_hidden():
    result = areas.estimate("ndvi", HIST, aoi_km2=100.0)
    veg = result["classes"][0]
    # The 10 pixels that could fall either side of 0.30 are the honest error bar.
    assert veg["uncertainty_km2"] == 10.0


def test_threshold_direction_is_respected():
    result = areas.estimate("nbr", HIST, aoi_km2=100.0)
    low = result["classes"][0]
    # NBR < 0.10 selects the centre -0.75 and -0.25 bins: 20 of 100 pixels.
    assert low["name"] == "low_nbr"
    assert low["area_km2"] == 20.0


def test_unknown_index_and_missing_histogram_return_none():
    assert areas.estimate("savi", HIST, 10.0) is None
    assert areas.estimate("ndvi", None, 10.0) is None
    assert areas.estimate("ndvi", {"counts": []}, 10.0) is None


def test_a_constant_raster_is_all_or_nothing():
    flat = {"counts": [0, 50, 0], "min": 0.4, "max": 0.4}
    result = areas.estimate("ndvi", flat, aoi_km2=10.0)
    assert result["classes"][0]["area_km2"] == 10.0
    assert result["classes"][0]["uncertainty_km2"] == 0.0


def test_change_reports_both_directions_in_km2():
    before = areas.estimate("ndvi", {"counts": [50, 50, 0, 0], "min": -1.0, "max": 1.0}, 100.0)
    after = areas.estimate("ndvi", {"counts": [10, 10, 10, 70], "min": -1.0, "max": 1.0}, 100.0)
    rows = areas.change(before, after)
    assert rows[0]["from_km2"] == 0.0
    assert rows[0]["to_km2"] == 70.0
    assert rows[0]["change_km2"] == 70.0
    assert rows[0]["direction"] == "increase"


def test_median_across_acquisitions_is_robust_to_one_bad_scene():
    clean = {"counts": [10, 10, 10, 70], "min": -1.0, "max": 1.0}
    cloudy = {"counts": [90, 10, 0, 0], "min": -1.0, "max": 1.0}
    samples = [
        areas.estimate("ndvi", clean, 100.0),
        areas.estimate("ndvi", cloudy, 100.0),
        areas.estimate("ndvi", clean, 100.0),
    ]
    summary = areas.median_areas(samples)
    assert summary is not None
    veg = summary["classes"][0]
    assert veg["area_km2"] == 70.0  # the mean would have been 46.7
    assert veg["samples"] == 3
    assert veg["min_km2"] == 0.0
    assert veg["max_km2"] == 70.0
    assert "not a pixel-wise composite" in summary["aggregation"]
