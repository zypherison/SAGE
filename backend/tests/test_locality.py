"""Offline tests for named-feature AOIs and the measured statistics span limit.

The span limit is not a guess: 38 km succeeds and 39 km fails on the live
service (2026-11-12), and a 45x5 km strip fails too, so the gate is the longest
side rather than the area.
"""
from __future__ import annotations

import asyncio

import pytest

from app import config
from app.services import indices, locality
from app.services.geo import bbox_span_km, buffer_bbox


def test_area_feature_keeps_its_own_bbox():
    place = {
        "name": "Hyderabad",
        "klass": "place",
        "type": "city",
        "bbox": [78.40, 17.35, 78.55, 17.45],
        "centre": [78.475, 17.40],
    }
    bbox, notes = locality.fit(place)
    assert bbox == place["bbox"]
    assert notes == []  # a sane area is not silently shrunk


def test_river_spanning_states_is_buffered_not_rejected():
    place = {
        "name": "Godavari",
        "klass": "waterway",
        "type": "river",
        "bbox": [73.0, 17.0, 82.5, 22.0],  # the whole river: thousands of km
        "centre": [78.0, 19.5],
    }
    bbox, notes = locality.fit(place)

    assert bbox != place["bbox"]
    assert bbox_span_km(bbox) == pytest.approx(config.LOCALITY_BUFFER_KM * 2, rel=0.02)
    assert "spans about" in notes[0]
    assert "3 km box" in notes[0]  # the user is told what they actually got


def test_peak_with_a_degenerate_bbox_is_buffered():
    place = {
        "name": "Ananthagiri Hills",
        "klass": "natural",
        "type": "peak",
        "bbox": [78.3999, 17.3899, 78.4001, 17.3901],  # ~20 m across
        "centre": [78.4000, 17.3900],
    }
    bbox, notes = locality.fit(place)

    assert bbox_span_km(bbox) == pytest.approx(config.LOCALITY_BUFFER_KM * 2, rel=0.02)
    assert "point feature" in notes[0]


def test_landmark_without_a_bbox_uses_its_centre():
    place = {"name": "Charminar", "klass": "historic", "type": "monument",
             "bbox": None, "centre": [78.4744, 17.3616]}
    bbox, notes = locality.fit(place)

    assert bbox[0] < 78.4744 < bbox[2]
    assert "no bounding box" in notes[0]


def test_feature_with_no_geometry_is_refused_rather_than_invented():
    with pytest.raises(ValueError):
        locality.fit({"name": "Nowhere", "bbox": None, "centre": None})


def test_buffer_is_centred_and_clamped():
    bbox = buffer_bbox([78.4, 17.4], 3.0)
    assert bbox[0] < 78.4 < bbox[2]
    assert bbox_span_km(bbox) == pytest.approx(6.0, rel=0.02)


def test_bare_place_names_are_understood():
    from app.services.query_parser import parse

    assert parse("Charminar").place == "Charminar"
    assert parse("Godavari").place == "Godavari"
    assert parse("Hussain Sagar water last month").place == "Hussain Sagar"


def test_generic_queries_do_not_become_place_names():
    from app.services.query_parser import parse

    assert parse("vegetation map").place is None
    assert parse("cloud free imagery").place is None
    assert parse("last month").place is None
    assert parse("show me satellite images").place is None


def test_statistics_refuse_an_aoi_over_the_measured_span_limit():
    # ~50 km across: above the 38 km limit, so the call must not even be made.
    bbox = [78.0, 17.1754, 78.4703, 18.0]
    assert bbox_span_km(bbox) > config.MAX_STATS_SPAN_KM

    result = asyncio.run(indices.compute(None, index="ndvi", bbox=bbox))  # type: ignore[arg-type]

    assert result["status"] == "insufficient_data"
    assert result["stats"] is None
    assert "38 km" in result["message"]
    assert "Index service error" not in result["message"]
