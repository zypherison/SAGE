"""WorldPop zonal sum on a synthetic 1 km raster (no network)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from app import config
from app.errors import INVALID_ARGUMENTS, AppError
from app.geostack import population


def _write_country(path: Path, value: int = 100) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.full((10, 10), value, dtype="float32")
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        crs="EPSG:4326",
        transform=from_origin(78.0, 17.5, 0.01, 0.01),
        width=10,
        height=10,
        count=1,
        dtype="float32",
        nodata=-99999.0,
    ) as ds:
        ds.write(data, 1)


@pytest.fixture()
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORLDPOP_CACHE_DIR", str(tmp_path))
    _write_country(population.cache_path("IND", 2020))
    return tmp_path


def test_sum_of_pixels_inside_one_cell(cache):
    geometry = {
        "type": "Polygon",
        # covers the centre of the first 0.01 deg cell (78.005, 17.495)
        "coordinates": [[[78.001, 17.491], [78.009, 17.491], [78.009, 17.499], [78.001, 17.499], [78.001, 17.491]]],
    }
    result = population.estimate(geometry, iso3="IND", year=2020, allow_download=False)
    assert result["status"] == "ok"
    assert result["population"] == 100
    assert result["pixels_used"] == 1
    assert result["density_per_km2"] > 0


def test_iso3_is_validated():
    with pytest.raises(AppError) as err:
        population.estimate({"type": "Polygon", "coordinates": []}, iso3="India")
    assert err.value.code == INVALID_ARGUMENTS


def test_years_outside_the_series_are_refused():
    with pytest.raises(AppError) as err:
        population.ensure_raster("IND", 2024)
    assert "2000..2020" in err.value.message


def test_missing_cache_without_download_is_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORLDPOP_CACHE_DIR", str(tmp_path / "empty"))
    geometry = {
        "type": "Polygon",
        "coordinates": [[[78.001, 17.491], [78.009, 17.491], [78.009, 17.499], [78.001, 17.499], [78.001, 17.491]]],
    }
    with pytest.raises(AppError) as err:
        population.estimate(geometry, allow_download=False)
    assert "not cached" in err.value.message
