"""Offline tests for one-click download.

The network is never touched: `arcgis.search` and `arcgis.export_scene` are
replaced. What is asserted is the part that could be silently wrong — which
acquisition is chosen per day, how a long window is sampled, what the archive
contains, and that an empty result is a coded error rather than a 0-byte zip.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import zipfile

import pytest

from app.errors import AppError
from app.schemas import Scene
from app.services import download

BB = [78.3, 17.3, 78.6, 17.5]
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 24_000  # comfortably above DOWNLOAD_MIN_BYTES


def scene(oid: int, iso: str, cloud: float) -> Scene:
    return Scene(
        id=str(oid),
        provider="arcgis-sentinel2",
        mission="sentinel-2-l2a",
        datetime=iso,
        cloud_cover=cloud,
        bbox=BB,
        index_support=True,
    )


def build(**overrides):
    return asyncio.run(
        download.build(
            object(),
            bbox=BB,
            date_from=overrides.get("date_from", "2026-10-01"),
            date_to=overrides.get("date_to", "2026-11-01"),
            kind=overrides.get("kind", "truecolor"),
            index=overrides.get("index", "ndvi"),
        )
    )


# --------------------------------------------------------------------------
# selection
# --------------------------------------------------------------------------
def test_one_acquisition_per_day_keeps_the_least_cloudy():
    scenes = [
        scene(1, "2026-10-05T05:24:00Z", 42.0),
        scene(2, "2026-10-05T05:24:00Z", 3.1),
        scene(3, "2026-10-07T05:24:00Z", 12.0),
    ]
    picks = download.select_acquisitions(scenes, 8)

    assert [p.day for p in picks] == ["2026-10-05", "2026-10-07"]
    assert picks[0].objectid == "2"  # the 3.1 % cloud scene, not the 42 %
    assert picks[0].cloud_cover == 3.1


def test_cap_samples_the_whole_window_not_just_the_tail():
    scenes = [scene(i, f"2026-10-{i:02d}T05:00:00Z", 10.0) for i in range(1, 11)]
    picks = download.select_acquisitions(scenes, 4)

    assert len(picks) == 4
    assert picks[0].day == "2026-10-01"  # the start is represented
    assert picks[-1].day == "2026-10-10"  # and so is the end
    assert len({p.day for p in picks}) == 4  # no duplicate days


def test_scenes_without_a_datetime_are_ignored():
    picks = download.select_acquisitions([scene(1, None, 5.0)], 8)  # type: ignore[arg-type]
    assert picks == []


def test_zero_max_images_is_a_bad_request():
    with pytest.raises(AppError) as exc:
        download.select_acquisitions([scene(1, "2026-10-01T05:00:00Z", 5.0)], 0)
    assert exc.value.code == "invalid_arguments"


def test_export_size_preserves_ground_aspect_ratio():
    wide = download.export_size([78.0, 17.0, 78.6, 17.2])
    tall = download.export_size([78.0, 17.0, 78.2, 17.8])

    assert wide[0] == 1024 and wide[1] < 1024
    assert tall[1] == 1024 and tall[0] < 1024
    assert wide != (1024, 1024)


# --------------------------------------------------------------------------
# archive
# --------------------------------------------------------------------------
def test_archive_carries_images_manifest_and_attribution(monkeypatch):
    async def fake_search(fetch, **kwargs):
        return [
            scene(11, "2026-10-05T05:24:00Z", 2.5),
            scene(12, "2026-10-12T05:24:00Z", 18.0),
        ]

    async def fake_export(fetch, *, bbox, objectid, rendering_rule=None, size=(1024, 768)):
        return PNG

    monkeypatch.setattr(download.arcgis, "search", fake_search)
    monkeypatch.setattr(download.arcgis, "export_scene", fake_export)

    blob, manifest = build()

    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        names = sorted(archive.namelist())
        manifest_json = json.loads(archive.read("manifest.json"))
        readme = archive.read("README.txt").decode("utf-8")
        first = archive.read("images/S2_2026-10-05_truecolor.png")

    assert names == [
        "README.txt",
        "images/S2_2026-10-05_truecolor.png",
        "images/S2_2026-10-12_truecolor.png",
        "manifest.json",
    ]
    assert first == PNG
    assert manifest["count"] == 2
    assert manifest_json["format"] == "sage-download"
    assert manifest_json["source"]["provider"] == "arcgis-sentinel2"
    # provenance: the objectid that was rendered is recorded, not just the date
    assert [i["objectid"] for i in manifest_json["images"]] == ["11", "12"]
    assert manifest_json["images"][0]["sha256"] == hashlib.sha256(PNG).hexdigest()
    assert "Copernicus Sentinel data" in readme


def test_index_kind_records_the_rendering_rule(monkeypatch):
    async def fake_search(fetch, **kwargs):
        return [scene(21, "2026-10-05T05:24:00Z", 1.0)]

    async def fake_export(fetch, *, bbox, objectid, rendering_rule=None, size=(1024, 768)):
        assert rendering_rule == "NDVI Colormap"
        return PNG

    monkeypatch.setattr(download.arcgis, "search", fake_search)
    monkeypatch.setattr(download.arcgis, "export_scene", fake_export)

    _blob, manifest = build(kind="index", index="ndvi")
    assert manifest["images"][0]["rendering"] == "NDVI Colormap"
    assert manifest["images"][0]["file"].endswith("_ndvi.png")


# --------------------------------------------------------------------------
# coded failures (never an empty archive)
# --------------------------------------------------------------------------
def test_no_acquisitions_is_a_coded_error(monkeypatch):
    async def fake_search(fetch, **kwargs):
        return []

    monkeypatch.setattr(download.arcgis, "search", fake_search)

    with pytest.raises(AppError) as exc:
        build()
    assert exc.value.code == "no_imagery"
    assert "14-month" in exc.value.message


def test_all_blank_exports_is_a_coded_error(monkeypatch):
    async def fake_search(fetch, **kwargs):
        return [scene(31, "2026-10-05T05:24:00Z", 1.0)]

    async def fake_export(fetch, *, bbox, objectid, rendering_rule=None, size=(1024, 768)):
        return b"\x89PNG\r\n\x1a\n" + b"0" * 100  # ~108 B: the measured blank shape

    monkeypatch.setattr(download.arcgis, "search", fake_search)
    monkeypatch.setattr(download.arcgis, "export_scene", fake_export)

    with pytest.raises(AppError) as exc:
        build()
    assert exc.value.code == "no_imagery"
    assert "usable pixels" in exc.value.message


def test_partial_blank_exports_are_recorded_as_skipped(monkeypatch):
    async def fake_search(fetch, **kwargs):
        return [
            scene(41, "2026-10-05T05:24:00Z", 1.0),
            scene(42, "2026-10-09T05:24:00Z", 1.0),
        ]

    async def fake_export(fetch, *, bbox, objectid, rendering_rule=None, size=(1024, 768)):
        return b"" if objectid == "41" else PNG

    monkeypatch.setattr(download.arcgis, "search", fake_search)
    monkeypatch.setattr(download.arcgis, "export_scene", fake_export)

    _blob, manifest = build()
    assert manifest["count"] == 1
    assert manifest["images"][0]["objectid"] == "42"
    assert manifest["skipped"][0]["status"] == "no_data"


def test_unknown_index_fails_before_any_network_call(monkeypatch):
    async def forbidden(fetch, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("the catalogue must not be queried for an invalid index")

    monkeypatch.setattr(download.arcgis, "search", forbidden)

    with pytest.raises(AppError) as exc:
        build(kind="index", index="savi")
    assert exc.value.code == "unknown_index"


def test_unknown_kind_is_rejected():
    with pytest.raises(AppError) as exc:
        build(kind="thermal")
    assert exc.value.code == "invalid_arguments"
