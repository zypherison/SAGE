"""Offline tests for the index time series.

The live behaviour that justifies the design (lockRasterIds is honoured, and the
composite equals the clearest acquisition) was probed rather than assumed; here
only the selection, the change block and the failure codes are asserted.
"""
from __future__ import annotations

import asyncio

import pytest

from app.errors import AppError
from app.services import timeseries
from app.services.download import Acquisition

BB = [78.3, 17.3, 78.6, 17.5]


def acq(oid: str, day: str, cloud: float = 1.0) -> Acquisition:
    return Acquisition(day=day, objectid=oid, datetime=f"{day}T05:24:00Z", cloud_cover=cloud)


def run(monkeypatch, *, picks, means=None, **kwargs):
    async def fake_acquisitions(fetch, **kw):
        return picks

    async def fake_stats(fetch, *, bbox, raster_function, objectid=None):
        value = (means or {}).get(objectid)
        if value is None:
            return {"count": 0, "mean": None, "min": None, "max": None}
        return {"count": 1000, "mean": value, "min": value - 0.1, "max": value + 0.1,
                "std": 0.02, "median": value, "histogram": None}

    monkeypatch.setattr(timeseries, "acquisitions", fake_acquisitions)
    monkeypatch.setattr(timeseries.arcgis, "index_statistics", fake_stats)

    return asyncio.run(
        timeseries.build(
            object(),  # type: ignore[arg-type]
            bbox=BB,
            index=kwargs.get("index", "ndvi"),
            date_from="2026-09-01",
            date_to="2026-11-12",
            max_points=kwargs.get("max_points"),
        )
    )


def test_points_are_ordered_and_carry_their_acquisition(monkeypatch):
    result = run(
        monkeypatch,
        picks=[acq("22", "2026-11-03", 0.9), acq("11", "2026-10-19", 0.2)],
        means={"11": 0.0975, "22": 0.0248},
    )

    assert result["count"] == 2
    assert [p["date"] for p in result["points"]] == ["2026-10-19", "2026-11-03"]
    assert [p["objectid"] for p in result["points"]] == ["11", "22"]
    assert result["points"][0]["mean"] == pytest.approx(0.0975)


def test_change_is_a_difference_not_a_trend(monkeypatch):
    result = run(
        monkeypatch,
        picks=[acq("11", "2026-10-19"), acq("22", "2026-11-03")],
        means={"11": 0.20, "22": 0.05},
    )

    assert result["change"]["delta"] == pytest.approx(-0.15)
    assert result["change"]["between"] == ["2026-10-19", "2026-11-03"]
    assert "not a fitted trend" in result["change"]["interpretation"]


def test_two_points_warns_against_reading_a_trend(monkeypatch):
    result = run(
        monkeypatch,
        picks=[acq("11", "2026-10-19"), acq("22", "2026-11-03")],
        means={"11": 0.2, "22": 0.3},
    )
    assert any("fewer than 3" in n.lower() for n in result["notes"])


def test_days_without_pixels_are_skipped_not_zeroed(monkeypatch):
    result = run(
        monkeypatch,
        picks=[acq("11", "2026-10-19"), acq("22", "2026-11-03")],
        means={"11": 0.2},  # 22 returns count 0
    )

    assert result["count"] == 1
    assert result["skipped"][0]["objectid"] == "22"
    assert result["change"] is None  # one point is not a change


def test_no_acquisitions_is_a_coded_error(monkeypatch):
    with pytest.raises(AppError) as exc:
        run(monkeypatch, picks=[], means={})
    assert exc.value.code == "no_imagery"


def test_unknown_index_is_rejected_before_the_network(monkeypatch):
    async def forbidden(fetch, **kw):  # pragma: no cover
        raise AssertionError("must not query the catalogue for an invalid index")

    monkeypatch.setattr(timeseries, "acquisitions", forbidden)

    with pytest.raises(AppError) as exc:
        asyncio.run(
            timeseries.build(object(), bbox=BB, index="savi",  # type: ignore[arg-type]
                             date_from="2026-09-01", date_to="2026-11-12")
        )
    assert exc.value.code == "unknown_index"


def test_a_single_point_request_is_rejected(monkeypatch):
    with pytest.raises(AppError) as exc:
        run(monkeypatch, picks=[acq("11", "2026-10-19")], means={"11": 0.2}, max_points=1)
    assert exc.value.code == "invalid_arguments"
