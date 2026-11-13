"""Offline tests for polygon AOIs.

The claim being tested is that a polygon is not just a nicer outline: the
statistics request carries the ring, and the km² figures use the polygon's area
rather than its envelope. A triangle over half a box is the cheapest case where
the two areas differ by 2x, so a squared-off implementation fails loudly.
"""
from __future__ import annotations

import asyncio

import pytest

from app.services import compare, indices
from app.services.geo import bbox_area_km2, geometry_area_km2, normalize_aoi
from app.services.providers import arcgis

BB = [78.3, 17.3, 78.6, 17.5]
# Half of the box: the lower-left triangle.
TRIANGLE = {
    "type": "Polygon",
    "coordinates": [[[78.3, 17.3], [78.6, 17.3], [78.6, 17.5], [78.3, 17.3]]],
}


def test_polygon_area_is_used_instead_of_the_envelope():
    bb, geom, area, _centre = normalize_aoi(geometry=TRIANGLE)
    assert bb == pytest.approx(BB, abs=1e-6)
    assert geom["type"] == "Polygon"
    assert area == pytest.approx(bbox_area_km2(BB) / 2, rel=0.01)
    assert geometry_area_km2(TRIANGLE) == pytest.approx(bbox_area_km2(BB) / 2, rel=0.01)


class FakeFetch:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def get_json(self, url, params=None):
        self.calls.append(params or {})
        return (
            {
                "statistics": [{"min": 0.0, "max": 1.0, "mean": 0.5, "count": 3_600_000}],
                "histograms": [{"counts": [1, 1], "min": 0.0, "max": 1.0}],
            },
            False,
        )


def test_the_ring_reaches_the_statistics_service():
    fetch = FakeFetch()
    asyncio.run(
        arcgis.index_statistics(
            fetch, bbox=BB, raster_function="NDVI Raw", geometry=TRIANGLE
        )
    )
    params = fetch.calls[0]
    assert params["geometryType"] == "esriGeometryPolygon"
    assert "rings" in params["geometry"]
    assert "78.3" in params["geometry"]


def test_a_rectangle_still_sends_an_envelope():
    fetch = FakeFetch()
    asyncio.run(arcgis.index_statistics(fetch, bbox=BB, raster_function="NDVI Raw"))
    params = fetch.calls[0]
    assert params["geometryType"] == "esriGeometryEnvelope"
    assert "xmin" in params["geometry"]


def test_index_areas_use_the_polygon_area(monkeypatch):
    seen: dict = {}

    async def fake_stats(fetch, **kwargs):
        seen.update(kwargs)
        return {
            "count": 3_600_000,
            "mean": 0.4,
            "min": 0.0,
            "max": 0.8,
            "std": 0.1,
            "median": 0.4,
            "histogram": {"counts": [0, 100], "min": 0.0, "max": 1.0},
        }

    monkeypatch.setattr(indices.arcgis, "index_statistics", fake_stats)

    result = asyncio.run(
        indices.compute(object(), index="ndvi", bbox=BB, geometry=TRIANGLE)  # type: ignore[arg-type]
    )

    assert seen["geometry"] == TRIANGLE
    assert result["status"] == "ok"
    # Half the box's 707.9 km², not 707.9.
    assert result["areas"]["aoi_km2"] == pytest.approx(bbox_area_km2(BB) / 2, rel=0.01)


def test_compare_passes_the_polygon_to_both_sides(monkeypatch):
    async def fake_search(fetch, **kwargs):
        from app.schemas import Scene

        return [
            Scene(id="11", provider="arcgis-sentinel2", mission="sentinel-2-l2a",
                  datetime="2026-10-19T05:24:00Z", cloud_cover=1.0, bbox=BB,
                  index_support=True),
            Scene(id="22", provider="arcgis-sentinel2", mission="sentinel-2-l2a",
                  datetime="2026-11-11T05:24:00Z", cloud_cover=1.0, bbox=BB,
                  index_support=True),
        ]

    calls: list[dict] = []

    async def fake_stats(fetch, **kwargs):
        calls.append(kwargs)
        return {
            "count": 3_600_000,
            "mean": 0.4,
            "min": 0.0,
            "max": 0.8,
            "histogram": {"counts": [0, 100], "min": 0.0, "max": 1.0},
        }

    monkeypatch.setattr(compare.arcgis, "search", fake_search)
    monkeypatch.setattr(compare.arcgis, "index_statistics", fake_stats)

    result = asyncio.run(
        compare.build(object(), bbox=BB, index="ndvi", date_a="2026-10-19",  # type: ignore[arg-type]
                      date_b="2026-11-11", geometry=TRIANGLE)
    )

    assert len(calls) == 2
    assert all(call["geometry"] == TRIANGLE for call in calls)
    assert result["before"]["areas"]["aoi_km2"] == pytest.approx(
        bbox_area_km2(BB) / 2, rel=0.01
    )
