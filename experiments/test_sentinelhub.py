"""Offline checks for the Sentinel Hub prototype.

The response fixture is the one published in the Statistical API examples, so
`summarise` is tested against the real shape rather than an invented one.
"""
from __future__ import annotations

import pytest

from experiments.sentinelhub import evalscript_for, request_body, summarise

BB = [78.4623, 17.4086, 78.4868, 17.4374]
TRIANGLE = {
    "type": "Polygon",
    "coordinates": [
        [
            [78.4623, 17.4086],
            [78.4868, 17.4086],
            [78.4868, 17.4374],
            [78.4623, 17.4086],
        ]
    ],
}

# Copied from the "Statistics, histogram and percentiles" example.
DOC_RESPONSE = {
    "status": "OK",
    "data": [
        {
            "interval": {
                "from": "2020-07-04T00:00:00Z",
                "to": "2020-07-05T00:00:00Z",
            },
            "outputs": {
                "index": {
                    "bands": {
                        "B0": {
                            "stats": {
                                "min": 0.0797,
                                "max": 0.3096,
                                "mean": 0.1147,
                                "stDev": 0.0343,
                                "sampleCount": 3240,
                                "noDataCount": 0,
                                "percentiles": {"50.0": 0.1036},
                            },
                            "histogram": {
                                "bins": [
                                    {"lowEdge": 0.0, "highEdge": 0.06, "count": 0},
                                    {"lowEdge": 0.06, "highEdge": 0.12, "count": 2458},
                                ],
                                "overflowCount": 3,
                                "underflowCount": 0,
                            },
                        }
                    }
                }
            },
        }
    ],
}


def test_evalscript_returns_the_mask_band_the_api_requires():
    script = evalscript_for("ndvi")
    assert "B08" in script and "B04" in script
    assert 'id: "dataMask"' in script
    assert "dataMask: [s.dataMask]" in script
    with pytest.raises(ValueError):
        evalscript_for("ndbi")


def test_request_defaults_to_bbox_and_asks_for_a_histogram():
    body = request_body(bbox=BB, start="2025-10-01", end="2025-11-30")
    assert body["input"]["bounds"]["bbox"] == BB
    assert body["input"]["data"][0]["dataFilter"]["maxCloudCoverage"] == 40.0
    assert body["aggregation"]["aggregationInterval"]["of"] == "P30D"
    assert body["aggregation"]["timeRange"]["from"] == "2025-10-01T00:00:00Z"
    assert body["calculations"]["default"]["histograms"]["default"]["nBins"] == 10


def test_polygon_replaces_the_bbox_so_statistics_are_clipped():
    body = request_body(
        bbox=BB, start="2025-10-01", end="2025-11-30", geometry=TRIANGLE
    )
    assert body["input"]["bounds"]["geometry"] == TRIANGLE
    assert "bbox" not in body["input"]["bounds"]


def test_summarise_reads_the_published_response_shape():
    rows = summarise(DOC_RESPONSE)
    assert len(rows) == 1
    row = rows[0]
    assert row["from"] == "2020-07-04"
    assert row["mean"] == 0.1147
    assert row["samples"] == 3240
    assert row["percentiles"]["50.0"] == 0.1036
    assert [b["count"] for b in row["bins"]] == [0, 2458]
    assert row["overflow"] == 3


def test_failed_status_is_raised_not_silently_empty():
    with pytest.raises(RuntimeError):
        summarise({"status": "FAILED", "data": []})
    with pytest.raises(RuntimeError):
        summarise({"error": {"message": "bad evalscript"}})
