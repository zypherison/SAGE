"""The /api/geo surface, plus the Earth Engine failure contract (offline)."""
from __future__ import annotations

import numpy as np
import pytest
import rasterio
from fastapi.testclient import TestClient
from rasterio.transform import from_origin

from app import config
from app.errors import GEOSTACK_UNAVAILABLE, AppError
from app.geostack import population
from app.main import app

client = TestClient(app)

AOI = {
    "type": "Polygon",
    "coordinates": [
        [[78.4623, 17.4086], [78.4868, 17.4086], [78.4868, 17.4374], [78.4623, 17.4086]]
    ],
}


def test_capabilities_lists_installed_versions():
    response = client.get("/api/geo/capabilities")
    assert response.status_code == 200
    body = response.json()
    assert body["stack"] == "geostack"
    assert body["raster_ready"] is True
    assert "rasterio" in body["installed"]
    assert isinstance(body["missing"], list)


def test_aoi_endpoint_measures_a_triangle():
    response = client.post("/api/geo/aoi", json={"geometry": AOI})
    assert response.status_code == 200
    body = response.json()
    assert 3.9 < body["area_km2"] < 4.4
    assert body["projected"]["crs"].startswith("EPSG:326")
    assert body["audit"]["repaired"] is False


def test_point_aoi_returns_422():
    response = client.post("/api/geo/aoi", json={"geometry": {"type": "Point", "coordinates": [78.4, 17.4]}})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_arguments"


def test_missing_geometry_is_rejected_by_the_schema():
    assert client.post("/api/geo/aoi", json={}).status_code == 422


def test_composite_without_credentials_is_503(tmp_path, monkeypatch):
    for name in ("SAGE_EE_PROJECT", "SAGE_EE_SERVICE_ACCOUNT", "SAGE_EE_KEY_FILE"):
        monkeypatch.delenv(name, raising=False)
    response = client.post(
        "/api/geo/composite",
        json={"geometry": AOI, "date_from": "2025-10-01", "date_to": "2025-11-01"},
    )
    assert response.status_code == 503
    body = response.json()["error"]
    assert body["code"] == GEOSTACK_UNAVAILABLE
    assert "SAGE_EE_PROJECT" in body["message"]


def test_population_endpoint_uses_the_cached_raster(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORLDPOP_CACHE_DIR", str(tmp_path))
    path = population.cache_path("IND", 2020)
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        crs="EPSG:4326",
        transform=from_origin(78.4, 17.5, 0.01, 0.01),
        width=10,
        height=10,
        count=1,
        dtype="float32",
        nodata=-99999.0,
    ) as ds:
        ds.write(np.full((10, 10), 250, dtype="float32"), 1)

    response = client.post("/api/geo/population", json={"geometry": AOI, "iso3": "IND", "year": 2020})
    assert response.status_code == 200
    body = response.json()
    assert body["population"] > 0
    assert body["pixels_used"] > 0
    assert body["area_km2"] > 3.0


def test_map_is_written_and_served(tmp_path, monkeypatch):
    pytest.importorskip("geemap")
    monkeypatch.setattr(config, "MAP_DIR", str(tmp_path))
    response = client.post("/api/geo/map", json={"geometry": AOI, "title": "Hussain Sagar"})
    assert response.status_code == 200
    body = response.json()
    assert len(body["map_id"]) == 21  # 16 hex chars + .html
    assert body["bytes"] > 1000
    assert "AOI" in body["layers"]

    served = client.get(body["url"])
    assert served.status_code == 200
    assert served.headers["content-type"].startswith("text/html")


def test_unknown_map_name_is_rejected():
    # A malformed name is a bad argument (422); a well-formed but missing one is 404.
    assert client.get("/api/geo/map/not-a-map").status_code == 422
    assert client.get("/api/geo/map/0123456789abcdef.html").status_code == 404


def test_earth_engine_init_names_the_missing_variables(monkeypatch):
    from app.geostack import earth

    for name in ("SAGE_EE_PROJECT", "SAGE_EE_SERVICE_ACCOUNT", "SAGE_EE_KEY_FILE"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(AppError) as err:
        earth.index_stats(AOI, date_from="2025-10-01", date_to="2025-11-01")
    assert err.value.code == GEOSTACK_UNAVAILABLE
