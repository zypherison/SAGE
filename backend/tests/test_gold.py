"""Gold-set tests: these make the fixtures in data/gold load-bearing."""
from __future__ import annotations

from datetime import date

from app.services.geo import bbox_area_km2
from app.services.query_parser import parse


def _normalise(name: str) -> str:
    return " ".join(name.lower().replace("the ", "").split())


def test_parser_gold_cases(gold):
    data = gold("parser_cases.json")
    today = date.fromisoformat(data["today"])
    failures: list[str] = []

    for case in data["cases"]:
        intent = parse(case["text"], today=today)
        for slot, expected in case["expect"].items():
            actual = getattr(intent, slot)
            if expected is None:
                ok = actual is None
            elif slot == "place":
                ok = actual is not None and _normalise(actual) == _normalise(str(expected))
            elif slot == "max_cloud":
                ok = actual is not None and abs(float(actual) - float(expected)) < 1e-9
            else:
                ok = actual == expected
            if not ok:
                failures.append(f"{case['text']!r} -> {slot}: expected {expected!r}, got {actual!r}")

    assert not failures, "gold parser failures:\n" + "\n".join(failures)


def test_arcgis_gold_reference_area_is_consistent(gold):
    ref = gold("arcgis_ndvi_reference.json")
    actual = bbox_area_km2(ref["bbox"])
    expected = ref["bbox_area_km2_expected"]
    assert abs(actual - expected) / expected < 0.05, f"area {actual:.0f} vs stated {expected}"


def test_arcgis_gold_reference_values_are_in_range(gold):
    ref = gold("arcgis_ndvi_reference.json")
    ndvi = ref["ndvi"]
    assert -1.0 <= ndvi["min"] <= ndvi["max"] <= 1.0
    assert ndvi["min"] <= ndvi["mean"] <= ndvi["max"]
    assert ndvi["count"] > 0


def test_gold_records_the_provider_quirks_we_depend_on(gold):
    """The Earth Search quirks are load-bearing; if they change, the adapter must too."""
    quirks = gold("arcgis_ndvi_reference.json")["earth_search_quirks"]
    assert "RFC3339" in quirks["datetime_short_form"]
    assert "IGNORED" in quirks["cql2_filter"]
    assert "query_extension" in quirks
