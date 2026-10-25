"""Geometry tests — geodesic area and simplification are easy to get silently wrong."""
from __future__ import annotations

import pytest

from app.services.geo import (
    bbox_area_km2,
    bbox_center,
    bbox_iou,
    ensure_ccw,
    geometry_area_km2,
    geometry_bbox,
    normalize_aoi,
    ring_area_km2,
    simplify_ring,
)


def test_bbox_center():
    assert bbox_center([78.0, 17.0, 79.0, 18.0]) == [78.5, 17.5]


def test_area_of_one_degree_at_equator():
    # 1 deg x 1 deg at the equator ~ 111.32^2 = 12393 km^2 (+/- 0.2%)
    area = bbox_area_km2([0.0, -0.5, 1.0, 0.5])
    assert 12300 < area < 12480


def test_area_shrinks_with_latitude():
    equator = bbox_area_km2([0.0, -0.5, 1.0, 0.5])
    high = bbox_area_km2([0.0, 59.5, 1.0, 60.5])
    assert high < equator * 0.55  # cos(60) = 0.5


def test_known_tile_area():
    # Hyderabad AOI used throughout SEMINAR2.md: 0.3 deg lon at lat 17.4 is
    # 0.3 * 111.32 * cos(17.4) = 31.9 km; 0.2 deg lat is 22.3 km -> ~709 km^2
    area = bbox_area_km2([78.3, 17.3, 78.6, 17.5])
    assert 700 < area < 720


def test_geometry_bbox_from_polygon():
    geom = {
        "type": "Polygon",
        "coordinates": [[[78.1, 17.1], [78.4, 17.1], [78.4, 17.4], [78.1, 17.1]]],
    }
    assert geometry_bbox(geom) == pytest.approx([78.1, 17.1, 78.4, 17.4])


def test_multipolygon_area_is_sum():
    single = {"type": "Polygon", "coordinates": [[[0, 0], [0.1, 0], [0.1, 0.1], [0, 0.1], [0, 0]]]}
    multi = {"type": "MultiPolygon", "coordinates": [[[[0, 0], [0.1, 0], [0.1, 0.1], [0, 0.1], [0, 0]]],
                                                     [[[1, 1], [1.1, 1], [1.1, 1.1], [1, 1.1], [1, 1]]]]}
    assert geometry_area_km2(multi) == pytest.approx(2 * geometry_area_km2(single), rel=0.02)


def test_ring_area_needs_four_points():
    assert ring_area_km2([[0, 0], [1, 0], [0, 0]]) == 0.0


def test_iou_identical_and_disjoint():
    a = [78.0, 17.0, 78.4, 17.4]
    assert bbox_iou(a, a) == pytest.approx(1.0)
    assert bbox_iou(a, [79.0, 18.0, 79.4, 18.4]) == 0.0


def test_iou_half_overlap():
    a = [0, 0, 2, 2]
    b = [1, 0, 3, 2]
    # inter = 2, union = 6
    assert bbox_iou(a, b) == pytest.approx(1 / 3)


def test_simplify_keeps_endpoints_and_reduces_points():
    ring = [[0, 0], [0.5, 0.001], [1, 0], [1, 1], [0.5, 1.001], [0, 1], [0, 0]]
    out = simplify_ring(ring, 0.01)
    assert out[0] == out[-1] == [0, 0]
    assert len(out) < len(ring)


def test_simplify_keeps_sharp_corner():
    ring = [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]
    out = simplify_ring(ring, 0.001)
    assert len(out) == 5


def test_ensure_ccw_keeps_counter_clockwise():
    ccw = [[0, 0], [1, 0], [1, 1], [0, 0]]  # shoelace sum = +1
    assert ensure_ccw(ccw) == ccw


def test_ensure_ccw_reverses_clockwise():
    cw = [[0, 0], [1, 1], [1, 0], [0, 0]]  # shoelace sum = -1
    assert ensure_ccw(cw) == list(reversed(cw))


def test_normalize_aoi_from_bbox():
    bbox, geom, area, centre = normalize_aoi(bbox=[78.3, 17.3, 78.6, 17.5])
    assert bbox == [78.3, 17.3, 78.6, 17.5]
    assert geom["type"] == "Polygon"
    assert area > 0
    assert centre == pytest.approx([78.45, 17.4])


def test_normalize_aoi_requires_input():
    with pytest.raises(ValueError):
        normalize_aoi()
