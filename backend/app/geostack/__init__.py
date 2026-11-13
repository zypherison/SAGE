"""Optional geospatial stack: vectors, rasters, WorldPop, Earth Engine, maps.

This package is deliberately separate from `app.services`. The core API installs
with five small dependencies and stays importable on a machine with no GDAL; the
heavy libraries live here and are imported lazily, endpoint by endpoint, so a
missing one produces a `geostack_unavailable` error instead of a dead app.

Structure:
    capabilities  what is installed and what is configured (no side effects)
    vector        geopandas: AOI validation, repair, geodesic metrics
    raster        rasterio: STAC COG reads, index maths, zonal statistics
    stac          Earth Search: scene search and band asset resolution
    population    WorldPop: cached country raster, population inside an AOI
    earth         Earth Engine: server-side composites (needs credentials)
    maps          geemap: an interactive HTML map for an AOI
"""
from app.geostack.capabilities import report

__all__ = ["report"]
