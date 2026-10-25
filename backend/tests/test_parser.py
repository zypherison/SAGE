"""Parser tests — each block corresponds to a bug that was observed in Seminar 1."""
from __future__ import annotations

from datetime import date

import pytest

from app.services.query_parser import clean_place, parse, resolve_dates

TODAY = date(2026, 11, 12)


# --------------------------------------------------------------------------
# date resolution precedence
# --------------------------------------------------------------------------
def test_last_season_beats_bare_season():
    df, dt, _ = resolve_dates("last summer", today=TODAY)
    assert df.startswith("2025-06")
    assert dt.startswith("2025-08")


def test_bare_season_uses_current_year():
    df, dt, _ = resolve_dates("summer imagery", today=TODAY)
    assert df.startswith("2026-06")


def test_season_year_beats_bare_year():
    df, _, _ = resolve_dates("winter 2023 in Kashmir", today=TODAY)
    assert df.startswith("2022-12")  # winter spans a year boundary


def test_month_range_this_year():
    df, dt, _ = resolve_dates("march to may this year", today=TODAY)
    assert df == "2026-03-01"
    assert dt == "2026-05-31"


def test_this_year_beats_bare_year():
    df, dt, _ = resolve_dates("this year", today=TODAY)
    assert df == "2026-01-01"
    assert dt == "2026-12-31"


def test_month_range_with_year():
    df, dt, _ = resolve_dates("June through August 2024", today=TODAY)
    assert df == "2024-06-01"
    assert dt == "2024-08-31"


def test_single_month_and_year():
    df, dt, _ = resolve_dates("Tokyo June 2024", today=TODAY)
    assert df == "2024-06-01"
    assert dt == "2024-06-30"


def test_year_range_with_to():
    df, dt, _ = resolve_dates("2019 to 2021", today=TODAY)
    assert df == "2019-01-01"
    assert dt == "2021-12-31"


def test_year_range_en_dash():
    df, dt, _ = resolve_dates("2019–2021", today=TODAY)
    assert df == "2019-01-01"
    assert dt == "2021-12-31"


def test_relative_last_n_days():
    df, dt, _ = resolve_dates("last 10 days", today=TODAY)
    assert dt == "2026-11-12"
    assert df == "2026-11-02"


def test_no_date_returns_none():
    assert resolve_dates("Hyderabad", today=TODAY) == (None, None, [])


# --------------------------------------------------------------------------
# cloud
# --------------------------------------------------------------------------
def test_cloud_free_strict():
    intent = parse("cloud free imagery of Giza", today=TODAY)
    assert intent.max_cloud == 5.0


def test_explicit_cloud_under():
    intent = parse("scenes under 20% cloud over Pune", today=TODAY)
    assert intent.max_cloud == 20.0


def test_cloud_trailing_form():
    intent = parse("Mumbai 10% cloud max", today=TODAY)
    assert intent.max_cloud == 10.0


def test_cloud_out_of_range_ignored():
    intent = parse("Nagpur under 250% cloud", today=TODAY)
    assert intent.max_cloud is None


# --------------------------------------------------------------------------
# place extraction
# --------------------------------------------------------------------------
def test_place_strips_month():
    # Seminar 1 bug: "Tokyo June" geocoded to a hairdresser named Tokyo June
    assert clean_place("Tokyo June") == "Tokyo"


def test_place_strips_cloud_phrase():
    assert clean_place("Hyderabad under 20% cloud") == "Hyderabad"


def test_place_requires_real_place():
    assert clean_place("satellite imagery") is None


def test_extract_place_from_of():
    intent = parse("show NDVI of the Sundarbans in March 2024", today=TODAY)
    assert intent.place is not None
    assert "Sundarbans" in intent.place


def test_extract_index_and_mission():
    intent = parse("Sentinel-2 NDWI over Chilika lake last month", today=TODAY)
    assert intent.index == "ndwi"
    assert intent.mission == "sentinel-2-l2a"


def test_index_nbr_from_wildfire_phrase():
    intent = parse("wildfire burn severity in Uttarakhand", today=TODAY)
    assert intent.index == "nbr"


def test_index_ndbi_from_built_up():
    intent = parse("urban growth around Bengaluru", today=TODAY)
    assert intent.index == "ndbi"


def test_no_place_flagged_not_invented():
    intent = parse("vegetation index last summer", today=TODAY)
    assert intent.place is None
    assert intent.index == "ndvi"


def test_notes_are_recorded():
    intent = parse("cloud free NDVI of Goa in 2024", today=TODAY)
    assert len(intent.notes) >= 2


@pytest.mark.parametrize(
    "text,expected",
    [
        ("ndvi", "ndvi"),
        ("NDVI change", "ndvi"),
        ("water index", "ndwi"),
        ("burnt area", "nbr"),
        ("built-up expansion", "ndbi"),
    ],
)
def test_index_detection_matrix(text, expected):
    assert parse(text, today=TODAY).index == expected
