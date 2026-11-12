"""Offline tests for the before/after comparison.

Asserts the parts that could silently be wrong: each side is pinned to its own
acquisition, the delta carries a sign, the pair is ordered rather than rejected,
and a date without coverage is a coded error instead of a zero.
"""
from __future__ import annotations

import asyncio

import pytest

from app.errors import AppError
from app.services import compare
from app.services.download import Acquisition

BB = [78.3, 17.3, 78.6, 17.5]


def run(monkeypatch, *, picks, means, date_a="2026-10-19", date_b="2026-11-08", index="ndvi"):
    async def fake_acquisitions(fetch, *, bbox, date_from, date_to, max_cloud, max_images):
        oid = picks.get(date_from)
        if oid is None:
            return []
        return [Acquisition(day=date_from, objectid=oid,
                            datetime=f"{date_from}T05:24:00Z", cloud_cover=1.5)]

    async def fake_stats(fetch, *, bbox, raster_function, objectid=None):
        value = means.get(objectid)
        if value is None:
            return None
        return {"count": 1000, "mean": value, "min": value - 0.1, "max": value + 0.1,
                "std": 0.02, "median": value, "histogram": None}

    monkeypatch.setattr(compare, "acquisitions", fake_acquisitions)
    monkeypatch.setattr(compare.arcgis, "index_statistics", fake_stats)

    return asyncio.run(
        compare.build(object(), bbox=BB, index=index, date_a=date_a, date_b=date_b)  # type: ignore[arg-type]
    )


def test_each_side_is_its_own_acquisition(monkeypatch):
    result = run(
        monkeypatch,
        picks={"2026-10-19": "11", "2026-11-08": "22"},
        means={"11": 0.20, "22": 0.15},
    )

    assert result["before"]["objectid"] == "11"
    assert result["after"]["objectid"] == "22"
    # the image URL must pin the same objectid that produced the statistic
    assert "lockRasterIds" in result["before"]["image_url"]
    assert "11" in result["before"]["image_url"]
    assert "22" in result["after"]["image_url"]


def test_delta_keeps_its_sign(monkeypatch):
    result = run(
        monkeypatch,
        picks={"2026-10-19": "11", "2026-11-08": "22"},
        means={"11": 0.20, "22": 0.15},
    )

    assert result["delta"]["mean"] == pytest.approx(-0.05)
    assert result["delta"]["direction"] == "decrease"
    assert result["delta"]["absolute"] == pytest.approx(0.05)


def test_a_later_first_date_is_reordered_not_rejected(monkeypatch):
    result = run(
        monkeypatch,
        picks={"2026-10-19": "11", "2026-11-08": "22"},
        means={"11": 0.20, "22": 0.15},
        date_a="2026-11-08",
        date_b="2026-10-19",
    )
    assert result["before"]["date"] == "2026-10-19"
    assert result["after"]["date"] == "2026-11-08"


def test_same_date_twice_is_a_bad_request(monkeypatch):
    with pytest.raises(AppError) as exc:
        run(monkeypatch, picks={}, means={}, date_a="2026-11-08", date_b="2026-11-08")
    assert exc.value.code == "invalid_arguments"


def test_a_date_without_coverage_is_a_coded_error(monkeypatch):
    with pytest.raises(AppError) as exc:
        run(monkeypatch, picks={"2026-10-19": "11"}, means={"11": 0.2})
    assert exc.value.code == "no_imagery"
    assert "2026-11-08" in exc.value.message


def test_unknown_index_fails_before_the_network(monkeypatch):
    async def forbidden(*args, **kwargs):  # pragma: no cover
        raise AssertionError("must not query for an invalid index")

    monkeypatch.setattr(compare, "acquisitions", forbidden)

    with pytest.raises(AppError) as exc:
        asyncio.run(
            compare.build(object(), bbox=BB, index="savi",  # type: ignore[arg-type]
                          date_a="2026-10-19", date_b="2026-11-08")
        )
    assert exc.value.code == "unknown_index"
