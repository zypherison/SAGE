"""Raster maths on synthetic COGs: offset handling, cloud mask, statistics."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin
from shapely.geometry import box, mapping
from shapely.ops import transform as shp_transform

from app.errors import NO_IMAGERY, UNKNOWN_INDEX, AppError
from app.geostack import raster

EPSG = 32644  # UTM 44N, Hussain Sagar


def _write(path: Path, data: np.ndarray, *, transform=None, dtype="uint16", nodata=0) -> str:
    transform = transform or from_origin(430000, 1925000, 10, 10)
    profile = {
        "driver": "GTiff",
        "crs": f"EPSG:{EPSG}",
        "transform": transform,
        "width": data.shape[1],
        "height": data.shape[0],
        "count": 1,
        "dtype": dtype,
        "nodata": nodata,
        "tiled": True,
        "blockxsize": 16,
        "blockysize": 16,
    }
    with rasterio.open(path, "w", **profile) as ds:
        ds.write(data, 1)
    return str(path)


def _aoi(offset_m: float = 0.0) -> dict:
    """A WGS84 square inside the raster, in the raster's own CRS."""
    west, south, east, north = 430010, 1924010, 430990, 1924990
    bbox = box(
        west + offset_m, south + offset_m, east + offset_m, north + offset_m
    )
    to_wgs = Transformer.from_crs(f"EPSG:{EPSG}", "EPSG:4326", always_xy=True).transform
    return mapping(shp_transform(to_wgs, bbox))


def test_ndvi_follows_the_caller_reflectance_convention(tmp_path: Path):
    # DN 2000 -> 0.2 and DN 5000 -> 0.5 when the offset is already applied
    # (NDVI 0.4286), or 0.1/0.4 when the legacy -1000 shift still needs removing
    # (NDVI 0.6). The caller decides, from the item's own metadata.
    red = _write(tmp_path / "red.tif", np.full((100, 100), 2000, dtype="uint16"))
    nir = _write(tmp_path / "nir.tif", np.full((100, 100), 5000, dtype="uint16"))

    shifted = raster.compute_index({"red": red, "nir": nir}, _aoi(), "ndvi", cloud_mask=False)
    legacy = raster.compute_index(
        {"red": red, "nir": nir}, _aoi(), "ndvi", cloud_mask=False, offset=-0.1
    )
    summary = raster.summarize(shifted)

    assert summary["status"] == "ok"
    assert summary["stats"]["mean"] == pytest.approx(0.4286, abs=1e-4)
    assert raster.summarize(legacy)["stats"]["mean"] == pytest.approx(0.6, abs=1e-4)
    assert summary["stats"]["count"] == 98 * 98  # 980 m AOI on a 10 m grid


def test_skipping_the_offset_would_change_the_answer(tmp_path: Path):
    # The same DNs computed without the -0.1 offset give a visibly different NDVI,
    # which is why the offset is a config constant with a measurement behind it.
    red = _write(tmp_path / "red.tif", np.full((20, 20), 2000, dtype="uint16"))
    nir = _write(tmp_path / "nir.tif", np.full((20, 20), 5000, dtype="uint16"))
    geometry = _aoi()

    correct = raster.summarize(raster.compute_index({"red": red, "nir": nir}, geometry, "ndvi", cloud_mask=False))
    wrong = raster.summarize(
        raster.compute_index({"red": red, "nir": nir}, geometry, "ndvi", cloud_mask=False, offset=-0.1)
    )
    assert abs(wrong["stats"]["mean"] - correct["stats"]["mean"]) > 0.05


def test_near_zero_denominator_does_not_produce_outliers(tmp_path: Path):
    # Both bands at the offset boundary: reflectance 0 and 0, denominator 0.
    # Without the guard and the clip this produced NDVI around -700.
    red = _write(tmp_path / "red.tif", np.full((100, 100), 1000, dtype="uint16"))
    nir = _write(tmp_path / "nir.tif", np.full((100, 100), 1000, dtype="uint16"))
    red2 = _write(tmp_path / "red2.tif", np.full((100, 100), 1000, dtype="uint16"))
    nir2 = _write(tmp_path / "nir2.tif", np.full((100, 100), 1200, dtype="uint16"))

    empty = raster.summarize(
        raster.compute_index({"red": red, "nir": nir}, _aoi(), "ndvi", cloud_mask=False, offset=-0.1)
    )
    assert empty["status"] == "insufficient_data"

    # Reflectance 0 and 0.02 under the legacy convention: a legitimate extreme
    # that must stay inside [-1, 1] instead of being clipped from an artefact.
    result = raster.compute_index(
        {"red": red2, "nir": nir2}, _aoi(), "ndvi", cloud_mask=False, offset=-0.1
    )
    finite = result["values"][np.isfinite(result["values"])]
    assert finite.size > 0
    assert finite.min() >= -1.0 and finite.max() <= 1.0
    assert finite.mean() == pytest.approx(1.0, abs=1e-3)


def test_scl_cloud_mask_removes_pixels(tmp_path: Path):
    red = _write(tmp_path / "red.tif", np.full((100, 100), 2000, dtype="uint16"))
    nir = _write(tmp_path / "nir.tif", np.full((100, 100), 5000, dtype="uint16"))

    scl = np.full((100, 100), 4, dtype="uint8")  # vegetation
    scl[:, :50] = 9  # cloud, on the western half
    scl_path = _write(tmp_path / "scl.tif", scl, dtype="uint8", nodata=None)

    masked = raster.compute_index({"red": red, "nir": nir, "scl": scl_path}, _aoi(), "ndvi")
    unmasked = raster.compute_index({"red": red, "nir": nir}, _aoi(), "ndvi", cloud_mask=False)

    assert masked["pixels"]["cloud_mask_applied"] is True
    assert masked["pixels"]["cloud_masked"] > 0
    assert masked["pixels"]["valid"] < unmasked["pixels"]["valid"]
    # The mask is half the AOI, so roughly half the pixels go.
    assert 0.4 < masked["pixels"]["valid"] / unmasked["pixels"]["valid"] < 0.6


def test_missing_band_is_a_no_imagery_error(tmp_path: Path):
    red = _write(tmp_path / "red.tif", np.full((10, 10), 2000, dtype="uint16"))
    with pytest.raises(AppError) as err:
        raster.compute_index({"red": red}, _aoi(), "ndvi")
    assert err.value.code == NO_IMAGERY


def test_unknown_index_lists_the_valid_ones():
    with pytest.raises(AppError) as err:
        raster.spec("evi")
    assert err.value.code == UNKNOWN_INDEX
    assert "ndvi" in err.value.message


def test_summary_reports_insufficient_data_when_everything_is_masked(tmp_path: Path):
    red = _write(tmp_path / "red.tif", np.zeros((20, 20), dtype="uint16"))
    nir = _write(tmp_path / "nir.tif", np.zeros((20, 20), dtype="uint16"))
    result = raster.compute_index({"red": red, "nir": nir}, _aoi(), "ndvi", cloud_mask=False)
    summary = raster.summarize(result)
    assert summary["status"] == "insufficient_data"
    assert summary["count"] == 0


def test_preview_png_is_written(tmp_path: Path):
    red = _write(tmp_path / "red.tif", np.full((100, 100), 2000, dtype="uint16"))
    nir = _write(tmp_path / "nir.tif", np.full((100, 100), 5000, dtype="uint16"))
    result = raster.compute_index({"red": red, "nir": nir}, _aoi(), "ndvi", cloud_mask=False)

    out = tmp_path / "preview.png"
    raster.write_preview(result["values"], str(out), ramp="ndvi")
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert out.stat().st_size > 100
