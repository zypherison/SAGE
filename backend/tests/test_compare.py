"""Offline tests for the before/after comparison.

The behaviour that matters here came from a real dead end: the UI defaulted the
"after" date to today, today has no overpass, and the user got `no_imagery`.
So the tests assert that a comparison snaps to the nearest acquisition inside
`COMPARE_WINDOW_DAYS`, reports the substitution, and still pins each side to the
objectid that produced its statistic.
"""
from __future__ import annotations

import asyncio

import pytest

from app.errors import AppError
from app.schemas import Scene
from app.services import compare

BB = [78.3, 17.3, 78.6, 17.5]


def scene(oid: int, iso: str, cloud: float = 1.0) -> Scene:
    return Scene(
        id=str(oid),
        provider="arcgis-sentinel2",
        mission="sentinel-2-l2a",
        datetime=iso,
        cloud_cover=cloud,
        bbox=BB,
        index_support=True,
    )


def run(monkeypatch, *, scenes, means, date_a="2026-10-19", date_b="2026-11-13", index="ndvi"):
    async def fake_search(fetch, **kwargs):
        return scenes

    async def fake_stats(fetch, *, bbox, raster_function, objectid=None):
        value = means.get(objectid)
        if value is None:
            return None
        return {"count": 1000, "mean": value, "min": value - 0.1, "max": value + 0.1,
                "std": 0.02, "median": value, "histogram": None}

    monkeypatch.setattr(compare.arcgis, "search", fake_search)
    monkeypatch.setattr(compare.arcgis, "index_statistics", fake_stats)

    return asyncio.run(
        compare.build(object(), bbox=BB, index=index, date_a=date_a, date_b=date_b)  # type: ignore[arg-type]
    )


def test_a_future_date_snaps_to_the_nearest_acquisition(monkeypatch):
    result = run(
        monkeypatch,
        scenes=[scene(11, "2026-10-19T05:24:00Z"), scene(22, "2026-11-11T05:24:00Z")],
        means={"11": 0.20, "22": 0.15},
        date_b="2026-11-13",
    )

    assert result["after"]["requested_date"] == "2026-11-13"
    assert result["after"]["date"] == "2026-11-11"
    assert result["after"]["substituted"] is True
    assert any("nearest acquisition" in n for n in result["notes"])


def test_empty_after_date_explains_the_window(monkeypatch):
    with pytest.raises(AppError) as exc:
        run(monkeypatch, scenes=[], means={})
    assert exc.value.code == "no_imagery"
    assert "within 7 days" in exc.value.message


def test_each_side_is_pinned_to_its_own_objectid(monkeypatch):
    result = run(
        monkeypatch,
        scenes=[scene(11, "2026-10-19T05:24:00Z"), scene(22, "2026-11-11T05:24:00Z")],
        means={"11": 0.20, "22": 0.15},
        date_b="2026-11-11",
    )

    assert result["before"]["objectid"] == "11"
    assert result["after"]["objectid"] == "22"
    assert "11" in result["before"]["image_url"]
    assert "22" in result["after"]["image_url"]
    assert "lockRasterIds" in result["before"]["image_url"]


def test_delta_keeps_its_sign(monkeypatch):
    result = run(
        monkeypatch,
        scenes=[scene(11, "2026-10-19T05:24:00Z"), scene(22, "2026-11-11T05:24:00Z")],
        means={"11": 0.20, "22": 0.15},
        date_b="2026-11-11",
    )
    assert result["delta"]["mean"] == pytest.approx(-0.05)
    assert result["delta"]["direction"] == "decrease"


def test_reversed_pair_is_ordered_not_rejected(monkeypatch):
    result = run(
        monkeypatch,
        scenes=[scene(11, "2026-10-19T05:24:00Z"), scene(22, "2026-11-11T05:24:00Z")],
        means={"11": 0.20, "22": 0.15},
        date_a="2026-11-11",
        date_b="2026-10-19",
    )
    assert result["before"]["date"] == "2026-10-19"
    assert result["after"]["date"] == "2026-11-11"


def test_same_date_twice_is_a_bad_request(monkeypatch):
    with pytest.raises(AppError) as exc:
        run(monkeypatch, scenes=[], means={}, date_a="2026-11-11", date_b="2026-11-11")
    assert exc.value.code == "invalid_arguments"


def test_unknown_index_fails_before_the_network(monkeypatch):
    async def forbidden(*args, **kwargs):  # pragma: no cover
        raise AssertionError("must not query for an invalid index")

    monkeypatch.setattr(compare.arcgis, "search", forbidden)

    with pytest.raises(AppError) as exc:
        asyncio.run(
            compare.build(object(), bbox=BB, index="savi",  # type: ignore[arg-type]
                          date_a="2026-10-19", date_b="2026-11-13")
        )
    assert exc.value.code == "unknown_index"
