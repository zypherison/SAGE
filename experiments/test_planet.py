"""Offline checks for the Planet Data API prototype.

No network: these pin the request shape and the two summaries, which is the part
that is easy to get wrong. What the key is allowed to see is measured in
RESULTS.md, not tested here.
"""
from __future__ import annotations

from experiments.planet import (
    bbox_polygon,
    best_scenes,
    search_body,
    stats_body,
    summarise_monthly,
)

BB = [78.4623, 17.4086, 78.4868, 17.4374]


def test_bbox_becomes_a_closed_polygon_ring():
    ring = bbox_polygon(BB)["coordinates"][0]
    assert ring[0] == ring[-1]
    assert len(ring) == 5
    assert ring[0] == [78.4623, 17.4086]


def test_search_body_clips_geometry_and_date_window():
    body = search_body(
        item_types=["PSScene"], geometry=bbox_polygon(BB),
        start="2025-10-01", end="2025-11-30", limit=25,
    )
    assert body["item_types"] == ["PSScene"]
    assert body["limit"] == 25
    geo, dates = body["filter"]["config"]
    assert geo["type"] == "GeometryFilter"
    assert geo["config"]["type"] == "Polygon"
    assert dates["config"]["gte"] == "2025-10-01T00:00:00Z"
    assert dates["config"]["lte"] == "2025-11-30T23:59:59Z"


def test_explicit_timestamps_are_passed_through():
    body = stats_body(
        item_types=["PSScene"], geometry=bbox_polygon(BB),
        start="2025-10-01T06:00:00Z", end="2025-11-30T18:00:00Z",
    )
    dates = body["filter"]["config"][1]["config"]
    assert dates["gte"] == "2025-10-01T06:00:00Z"
    assert dates["lte"] == "2025-11-30T18:00:00Z"


def test_monthly_summary_reads_the_measured_stats_payload():
    payload = {
        "buckets": [
            {"count": 50, "start_time": "2025-10-01T00:00:00.000000Z"},
            {"count": 43, "start_time": "2025-11-01T00:00:00.000000Z"},
        ],
        "interval": "month",
        "utc_offset": "+0h",
    }
    rows = summarise_monthly(payload)
    assert rows == [
        {"month": "2025-10", "count": 50},
        {"month": "2025-11", "count": 43},
    ]


def test_best_scenes_filters_cloud_and_sorts_clear_first():
    features = [
        {"id": "a", "properties": {"cloud_cover": 60, "acquired": "2025-11-01T00:00:00Z"}},
        {"id": "b", "properties": {"cloud_cover": 0.2, "acquired": "2025-11-02T00:00:00Z"}},
        {"id": "c", "properties": {"cloud_cover": None, "acquired": "2025-11-03T00:00:00Z"}},
    ]
    rows = best_scenes(features, limit=5, max_cloud=10)
    assert [r["id"] for r in rows] == ["b", "c"]
    assert rows[0]["acquired"] == "2025-11-02"
