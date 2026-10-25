"""Discovery, ranking and evidence tests (no network)."""
from __future__ import annotations

import pytest

from app.schemas import Scene
from app.services import evidence
from app.services.discovery import dedupe, rank


def make(pid: str, provider: str, dt: str, cloud: float, bbox=None, index=False) -> Scene:
    return Scene(
        id=pid,
        provider=provider,
        mission="sentinel-2-l2a",
        datetime=dt,
        cloud_cover=cloud,
        bbox=bbox or [78.3, 17.3, 78.6, 17.5],
        index_support=index,
    )


# --------------------------------------------------------------------------
# dedup
# --------------------------------------------------------------------------
def test_same_acquisition_from_two_providers_merges():
    a = make("S2A_1", "earth-search", "2024-06-26T05:26:41Z", 4.1)
    b = make("S2A_1", "planetary-computer", "2024-06-26T05:26:41Z", 4.1, index=True)
    out = dedupe([a, b])
    assert len(out) == 1
    # the richer record wins
    assert out[0].index_support is True


def test_different_days_are_kept():
    a = make("a", "earth-search", "2024-06-26T05:26:41Z", 4.1)
    b = make("b", "earth-search", "2024-06-28T05:26:41Z", 4.1)
    assert len(dedupe([a, b])) == 2


def test_different_places_same_day_are_kept():
    a = make("a", "earth-search", "2024-06-26T05:26:41Z", 4.1, bbox=[78.0, 17.0, 78.4, 17.4])
    b = make("b", "earth-search", "2024-06-26T05:26:41Z", 4.1, bbox=[70.0, 20.0, 70.4, 20.4])
    assert len(dedupe([a, b])) == 2


def test_different_missions_not_merged():
    a = make("a", "earth-search", "2024-06-26T05:26:41Z", 4.1)
    b = make("b", "earth-search", "2024-06-26T05:26:41Z", 4.1)
    b.mission = "landsat-c2-l2"
    assert len(dedupe([a, b])) == 2


# --------------------------------------------------------------------------
# ranking
# --------------------------------------------------------------------------
def test_clear_scene_outranks_cloudy():
    clear = make("clear", "earth-search", "2024-06-26T00:00:00Z", 1.0)
    cloudy = make("cloudy", "earth-search", "2024-06-26T00:00:00Z", 95.0)
    ranked = rank([cloudy, clear], "2024-06-01", "2024-06-30")
    assert ranked[0].id == "clear"


def test_rank_parts_sum_within_one():
    s = make("x", "earth-search", "2024-06-26T00:00:00Z", 10.0)
    rank([s], "2024-06-01", "2024-06-30")
    assert 0.0 <= s.score <= 1.0
    assert set(s.score_parts) == {"clear", "temporal", "index", "resolution"}


def test_temporal_proximity_matters():
    near = make("near", "earth-search", "2024-06-15T00:00:00Z", 10.0)
    far = make("far", "earth-search", "2024-01-01T00:00:00Z", 10.0)
    ranked = rank([far, near], "2024-06-01", "2024-06-30")
    assert ranked[0].id == "near"


def test_missing_cloud_is_not_rewarded():
    unknown = make("unknown", "arcgis-sentinel2", "2024-06-26T00:00:00Z", None)
    known = make("known", "earth-search", "2024-06-26T00:00:00Z", 40.0)
    rank([unknown, known], "2024-06-01", "2024-06-30")
    assert unknown.score_parts["clear"] == 0.0
    assert known.score_parts["clear"] == pytest.approx(0.6)


# --------------------------------------------------------------------------
# evidence
# --------------------------------------------------------------------------
def test_evidence_id_is_deterministic():
    payload = {"index": "ndvi", "bbox": [78.3, 17.3, 78.6, 17.5]}
    assert evidence.make_id(payload) == evidence.make_id(dict(payload))


def test_evidence_id_changes_with_payload():
    a = evidence.make_id({"index": "ndvi", "bbox": [78.3, 17.3, 78.6, 17.5]})
    b = evidence.make_id({"index": "ndwi", "bbox": [78.3, 17.3, 78.6, 17.5]})
    assert a != b


def test_compare_detects_match_and_drift():
    base = {"result": {"stats": {"mean": 0.12966, "min": -0.26677, "max": 0.51638}}}
    same = {"result": {"stats": {"mean": 0.12966, "min": -0.26677, "max": 0.51638}}}
    drifted = {"result": {"stats": {"mean": 0.14, "min": -0.26677, "max": 0.51638}}}

    assert evidence.compare(base, same)["matched"] is True
    drifted_result = evidence.compare(base, drifted)
    assert drifted_result["matched"] is False
    assert drifted_result["absolute_differences"]["mean"] > 0.01


def test_compare_without_stats_is_not_a_match():
    assert evidence.compare({"result": {}}, {"result": {}})["matched"] is False


def test_compare_accepts_a_bare_result_dict():
    # Regression: the replay endpoint passes the fresh result dict unwrapped, while
    # the store returns {"result": ...}. Reading a single shape produced an empty
    # diff set, which reported a false mismatch on a byte-identical replay.
    recorded = {"result": {"stats": {"mean": 0.12966, "min": -0.26677, "max": 0.51638}}}
    fresh = {"stats": {"mean": 0.12966, "min": -0.26677, "max": 0.51638}}

    out = evidence.compare(recorded, fresh)
    assert out["matched"] is True
    assert out["absolute_differences"]["mean"] == 0.0
