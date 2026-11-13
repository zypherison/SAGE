"""The reflectance convention comes from item metadata, not the asset blob."""
from __future__ import annotations

from app.geostack import stac


def item(*, applied=None, baseline="05.13", declared_offset=-0.1) -> dict:
    props = {"s2:processing_baseline": baseline}
    if applied is not None:
        props["earthsearch:boa_offset_applied"] = applied
    return {
        "id": "S2C_44QKE_20261113_0_L2A",
        "collection": "sentinel-2-l2a",
        "properties": props,
        "assets": {
            "red": {
                "href": "https://example.invalid/B04.tif",
                "raster:bands": [{"scale": 0.0001, "offset": declared_offset}],
            }
        },
    }


def test_applied_flag_means_no_subtraction():
    convention = stac.reflectance(item(applied=True))
    assert convention["offset"] == 0.0
    assert convention["scale"] == 0.0001
    assert convention["declared_asset_offset"] == -0.1  # present, deliberately ignored


def test_unapplied_flag_means_subtract_one_thousand_dn():
    convention = stac.reflectance(item(applied=False))
    assert convention["offset"] == -0.1


def test_baseline_is_the_fallback_when_the_flag_is_absent():
    assert stac.reflectance(item(applied=None, baseline="05.13"))["offset"] == -0.1
    assert stac.reflectance(item(applied=None, baseline="03.00"))["offset"] == 0.0
    assert stac.reflectance(item(applied=None, baseline="not-a-number"))["offset"] == 0.0


def test_unknown_metadata_is_conservative():
    convention = stac.reflectance({"assets": {"red": {}}, "properties": {}})
    assert convention["offset"] == 0.0
    assert "assuming" in convention["source"]
