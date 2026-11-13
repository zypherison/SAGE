"""Vector handling with geopandas: validate, repair, measure.

`app.services.geo` deliberately avoids shapely so the prototype installs anywhere.
This module is the other half of that trade: when shapely/geopandas are present,
an AOI can be repaired instead of rejected, measured on the ellipsoid instead of
on the sphere, and reprojected for raster work.

Two numbers are reported side by side on purpose. The geodesic area comes from
pyproj's ellipsoid; the projected area comes from the UTM zone at the AOI's
centre. If they disagree by more than a few tenths of a percent, the projection
is wrong for that latitude and the caller should not trust either silently.
"""
from __future__ import annotations

import math
from typing import Any

from pyproj import Geod
from shapely import make_valid
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from app.errors import INVALID_ARGUMENTS, AppError

GEOD = Geod(ellps="WGS84")


def utm_epsg(lon: float, lat: float) -> int:
    """WGS84 UTM EPSG for a lon/lat. 32600 + zone north, 32700 + zone south."""
    zone = int((lon + 180.0) // 6.0) % 60 + 1
    return (32600 if lat >= 0 else 32700) + zone


def _polygons(geom: BaseGeometry) -> BaseGeometry:
    """Keep only the polygonal part of a make_valid() result."""
    if geom.geom_type in ("Polygon", "MultiPolygon"):
        return geom
    parts = [p for p in getattr(geom, "geoms", []) if p.geom_type in ("Polygon", "MultiPolygon")]
    if not parts:
        raise AppError(INVALID_ARGUMENTS, "geometry has no polygonal area")
    return unary_union(parts)


def load(geometry: dict[str, Any]) -> tuple[BaseGeometry, dict[str, Any]]:
    """Parse a GeoJSON geometry into a valid polygon in EPSG:4326.

    Returns the geometry and a small audit record, because "we repaired your
    polygon" is information the caller needs: a bow-tie usually means a
    hand-drawn AOI, and the repaired shape may not be what was intended.
    """
    if not isinstance(geometry, dict) or "type" not in geometry:
        raise AppError(INVALID_ARGUMENTS, "geometry must be a GeoJSON geometry object")
    try:
        geom = shape(geometry)
    except Exception as exc:
        raise AppError(INVALID_ARGUMENTS, f"could not parse geometry: {exc}") from exc

    if geom.is_empty:
        raise AppError(INVALID_ARGUMENTS, "geometry is empty")

    input_type = geom.geom_type
    if input_type not in ("Polygon", "MultiPolygon"):
        raise AppError(
            INVALID_ARGUMENTS,
            f"expected a Polygon or MultiPolygon AOI, got {input_type}; "
            "buffer points and lines before analysis",
        )

    bounds = geom.bounds
    if not (-180.0 <= bounds[0] <= 180.0 and -180.0 <= bounds[2] <= 180.0):
        raise AppError(INVALID_ARGUMENTS, "longitude outside -180..180", bbox=list(bounds))
    if not (-90.0 <= bounds[1] <= 90.0 and -90.0 <= bounds[3] <= 90.0):
        raise AppError(INVALID_ARGUMENTS, "latitude outside -90..90", bbox=list(bounds))

    repaired = False
    if not geom.is_valid:
        geom = _polygons(make_valid(geom))
        repaired = True

    audit = {"input_type": input_type, "repaired": repaired, "valid": geom.is_valid}
    return geom, audit


def to_geojson(geom: BaseGeometry) -> dict[str, Any]:
    return mapping(geom)


def analyze(geometry: dict[str, Any]) -> dict[str, Any]:
    """Ellipsoid metrics for an AOI, plus the projection used to cross-check them."""
    geom, audit = load(geometry)
    lon, lat = geom.centroid.x, geom.centroid.y
    epsg = utm_epsg(lon, lat)

    geodesic_area, perimeter = GEOD.geometry_area_perimeter(geom)
    area_km2 = abs(geodesic_area) / 1e6
    perimeter_km = abs(perimeter) / 1000.0

    # Projected area for the sanity check: buffer(0) removes duplicate vertices
    # that transformer round-trips can produce.
    from pyproj import Transformer  # local: only this function reprojects
    from shapely.ops import transform as shp_transform

    to_utm = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True).transform
    projected = shp_transform(to_utm, geom)
    projected_km2 = projected.area / 1e6
    utm_perimeter_km = projected.length / 1000.0

    # Isoperimetric quotient: 1.0 for a circle, ~0.79 for a square, and it falls
    # as a shape gets more elongated or fragmented.
    compactness = (4.0 * math.pi * projected.area / (projected.length**2)) if projected.length else None

    return {
        "geometry_type": geom.geom_type,
        "parts": len(getattr(geom, "geoms", [])) if geom.geom_type == "MultiPolygon" else 1,
        "vertices": sum(len(p.exterior.coords) for p in getattr(geom, "geoms", [geom])),
        "holes": sum(len(p.interiors) for p in getattr(geom, "geoms", [geom])),
        "area_km2": round(area_km2, 6),
        "perimeter_km": round(perimeter_km, 6),
        "projected": {
            "crs": f"EPSG:{epsg}",
            "area_km2": round(projected_km2, 6),
            "perimeter_km": round(utm_perimeter_km, 6),
            "relative_area_difference": round(abs(projected_km2 - area_km2) / area_km2, 9) if area_km2 else None,
        },
        "compactness": None if compactness is None else round(compactness, 4),
        "centroid": [round(lon, 7), round(lat, 7)],
        "bbox": [round(v, 7) for v in geom.bounds],
        "audit": audit,
    }


def to_crs(geom: BaseGeometry, epsg: int) -> BaseGeometry:
    from pyproj import Transformer
    from shapely.ops import transform as shp_transform

    if epsg == 4326:
        return geom
    return shp_transform(Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True).transform, geom)
