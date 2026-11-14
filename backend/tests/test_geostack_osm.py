"""Offline checks for the Overpass conversion; no network is touched."""
import pytest

from app.errors import AppError
from app.geostack import osm


def test_way_becomes_linestring_with_provenance():
    element = {
        "type": "way",
        "id": 42,
        "geometry": [
            {"lat": 17.4000, "lon": 78.4700},
            {"lat": 17.4100, "lon": 78.4800},
        ],
        "tags": {"highway": "primary", "name": "Tank Bund Road"},
    }

    feature = osm._feature(element, "roads")

    assert feature is not None
    assert feature["geometry"]["type"] == "LineString"
    assert feature["geometry"]["coordinates"][0] == [78.47, 17.4]
    assert feature["properties"]["name"] == "Tank Bund Road"
    assert feature["properties"]["osm"] == "way/42"
    assert feature["properties"]["kind"] == "primary"


def test_node_becomes_point():
    element = {"type": "node", "id": 7, "lat": 17.42, "lon": 78.48, "tags": {"historic": "memorial"}}

    feature = osm._feature(element, "monuments")

    assert feature is not None
    assert feature["geometry"] == {"type": "Point", "coordinates": [78.48, 17.42]}
    assert feature["properties"]["kind"] == "memorial"


def test_relation_is_skipped_not_rendered_broken():
    assert osm._feature({"type": "relation", "id": 9, "tags": {}}, "monuments") is None


def test_way_without_geometry_is_skipped():
    assert osm._feature({"type": "way", "id": 9, "tags": {"highway": "service"}}, "roads") is None


def test_unknown_category_is_rejected_before_any_request():
    geometry = {
        "type": "Polygon",
        "coordinates": [[[78.46, 17.40], [78.49, 17.40], [78.49, 17.44], [78.46, 17.44], [78.46, 17.40]]],
    }

    with pytest.raises(AppError):
        osm.fetch(geometry, "rivers")
