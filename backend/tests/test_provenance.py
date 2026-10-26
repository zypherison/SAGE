"""Provenance and index-definition tests."""
from __future__ import annotations

import pytest

from app import config
from app.services import indices, provenance


# --------------------------------------------------------------------------
# filename grammar
# --------------------------------------------------------------------------
def test_parse_full_safe_name():
    name = "S2A_MSIL2A_20240626T052641_N0510_R019_T43QHV_20240626T084512.SAFE"
    out = provenance.parse_s2_filename(name)
    assert out["confidence"] == "tile"
    assert out["mission"] == "Sentinel-2A"
    assert out["acquisition_date"] == "2024-06-26"
    assert out["tile"] == "43QHV"


def test_parse_tiff_product_name():
    name = "T43QHV_20240626T052641_B04_10m.tif"
    assert provenance.parse_s2_filename(name) == {}


def test_parse_loose_name():
    out = provenance.parse_s2_filename("S2B_MSIL1C_20230101")
    assert out["confidence"] == "tile"
    assert out["mission"] == "Sentinel-2B"
    assert out["acquisition_date"] == "2023-01-01"


def test_parse_unknown_abstains():
    out = provenance.analyze("holiday_photo.jpg", b"\xff\xd8\xff\xe0 not really a jpeg")
    assert out["confidence"] == "unknown"
    assert out["centroid"] is None
    assert out["notes"]  # an explicit reason is always given


def test_analyze_from_filename_only():
    out = provenance.analyze("S2A_MSIL2A_20240626T052641_N0510_R019_T43QHV.SAFE", None)
    assert out["confidence"] == "tile"
    assert out["tile"] == "43QHV"
    assert out["centroid"] is None  # tile is not a point; we do not fabricate one


def test_bad_date_in_filename_is_tolerated():
    out = provenance.parse_s2_filename("S2A_MSIL2A_20241399T052641_N0510_R019_T43QHV")
    assert out.get("acquisition_date") is None or isinstance(out.get("acquisition_date"), str)


# --------------------------------------------------------------------------
# index definitions
# --------------------------------------------------------------------------
def test_all_declared_indices_are_resolvable():
    for key in config.INDEX_DEFS:
        assert indices.definition(key)["label"]


def test_index_lookup_is_case_insensitive():
    assert indices.definition("NDVI")["label"] == "NDVI"


def test_unknown_index_raises():
    with pytest.raises(indices.UnsupportedIndex):
        indices.definition("savi")


def test_every_index_has_a_published_raster_function():
    # If this fails, the UI must show that index as unsupported instead.
    for key, spec in config.INDEX_DEFS.items():
        assert spec.get("arcgis_function"), f"{key} has no raster function"


def test_formulas_reference_declared_bands():
    for spec in config.INDEX_DEFS.values():
        assert "(" in str(spec["formula"])
        assert spec["bands"]


def test_aoi_over_limit_is_refused_without_a_network_call():
    import asyncio

    result = asyncio.run(
        indices.compute(None, index="ndvi", bbox=[60.0, 5.0, 90.0, 35.0])  # type: ignore[arg-type]
    )
    assert result["status"] == "insufficient_data"
    assert "limit" in result["message"].lower()
