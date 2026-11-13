"""Vector metrics: geodesic area, repair, projection cross-check."""
from __future__ import annotations

import pytest

from app.errors import INVALID_ARGUMENTS, AppError
from app.geostack import vector


def square(size: float = 0.01, west: float = 78.0, south: float = 17.0) -> dict:
    return {
        "type": "Polygon",
        "coordinates": [
            [[west, south], [west + size, south], [west + size, south + size], [west, south + size], [west, south]]
        ],
    }


def test_equatorial_square_area_matches_ellipsoid():
    # 0.01 deg at the equator is 1.111 km on each side, so 1.2346 km².
    metrics = vector.analyze(square(size=0.01, west=0.0, south=0.0))
    assert 1.22 < metrics["area_km2"] < 1.25
    assert metrics["vertices"] == 5  # closed ring
    assert metrics["parts"] == 1
    assert metrics["holes"] == 0


def test_utm_zone_follows_longitude_and_hemisphere():
    assert vector.utm_epsg(78.47, 17.4) == 32644
    assert vector.utm_epsg(-58.4, -34.6) == 32721
    assert vector.utm_epsg(0.0, 0.0) == 32631


def test_projected_and_geodesic_areas_agree():
    metrics = vector.analyze(square(size=0.02))
    assert metrics["projected"]["relative_area_difference"] < 0.002
    assert metrics["compactness"] == pytest.approx(0.785, abs=0.02)  # a square is pi/4


def test_invalid_bowtie_is_repaired_and_reported():
    bowtie = {
        "type": "Polygon",
        "coordinates": [[[78.0, 17.0], [78.01, 17.01], [78.01, 17.0], [78.0, 17.01], [78.0, 17.0]]],
    }
    metrics = vector.analyze(bowtie)
    assert metrics["audit"]["repaired"] is True
    assert metrics["area_km2"] > 0


def test_point_is_rejected_with_a_usable_message():
    with pytest.raises(AppError) as err:
        vector.analyze({"type": "Point", "coordinates": [78.0, 17.0]})
    assert err.value.code == INVALID_ARGUMENTS
    assert "Polygon" in err.value.message


def test_out_of_range_longitude_is_rejected():
    bad = {"type": "Polygon", "coordinates": [[[200.0, 10.0], [201.0, 10.0], [201.0, 11.0], [200.0, 10.0]]]}
    with pytest.raises(AppError) as err:
        vector.analyze(bad)
    assert "longitude" in err.value.message


def test_multipolygon_parts_are_counted():
    multi = {
        "type": "MultiPolygon",
        "coordinates": [
            [[[78.0, 17.0], [78.01, 17.0], [78.01, 17.01], [78.0, 17.01], [78.0, 17.0]]],
            [[[78.02, 17.0], [78.03, 17.0], [78.03, 17.01], [78.02, 17.01], [78.02, 17.0]]],
        ],
    }
    metrics = vector.analyze(multi)
    single = vector.analyze(square(size=0.01))
    assert metrics["parts"] == 2
    assert metrics["area_km2"] == pytest.approx(2 * single["area_km2"], rel=0.01)
