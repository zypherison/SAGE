"""Geometry helpers: AOI normalization, geodesic area, simplification.

Deliberately dependency-free (no shapely) so the prototype installs anywhere.
Spherical-excess area is used because planar degree arithmetic is wrong by a
factor that varies with latitude — a classic silent error in GIS prototypes.
"""
from __future__ import annotations

import math
from typing import Any

from app import config

EARTH_RADIUS_KM = 6371.0088


# --------------------------------------------------------------------------
# bbox helpers
# --------------------------------------------------------------------------
def geometry_bbox(geometry: dict[str, Any]) -> list[float]:
    """Return [west, south, east, north] for a GeoJSON geometry."""
    coords: list[tuple[float, float]] = []

    def walk(node: Any) -> None:
        if isinstance(node, (list, tuple)):
            if len(node) >= 2 and all(isinstance(v, (int, float)) for v in node[:2]):
                coords.append((float(node[0]), float(node[1])))
            else:
                for child in node:
                    walk(child)

    walk(geometry.get("coordinates"))
    if not coords:
        raise ValueError("geometry has no coordinates")
    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    return [min(xs), min(ys), max(xs), max(ys)]


def bbox_center(bbox: list[float]) -> list[float]:
    """[lon, lat] centre of a bbox."""
    return [(bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0]


def bbox_polygon(bbox: list[float]) -> dict[str, Any]:
    w, s, e, n = bbox
    return {
        "type": "Polygon",
        "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]],
    }


def bbox_iou(a: list[float], b: list[float]) -> float:
    """Intersection-over-union of two [w,s,e,n] boxes. Used for dedup."""
    iw = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    ih = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


# --------------------------------------------------------------------------
# Geodesic area (spherical excess) for a polygon ring
# --------------------------------------------------------------------------
def _to_radians(deg: float) -> float:
    return deg * math.pi / 180.0


def ring_area_km2(ring: list[list[float]]) -> float:
    """Geodesic area of one closed lon/lat ring, in km^2 (spherical excess)."""
    if len(ring) < 4:
        return 0.0
    total = 0.0
    for i in range(len(ring) - 1):
        lon1, lat1 = _to_radians(ring[i][0]), _to_radians(ring[i][1])
        lon2, lat2 = _to_radians(ring[i + 1][0]), _to_radians(ring[i + 1][1])
        total += (lon2 - lon1) * (2 + math.sin(lat1) + math.sin(lat2))
    return abs(total) * EARTH_RADIUS_KM * EARTH_RADIUS_KM / 2.0


def geometry_area_km2(geometry: dict[str, Any]) -> float:
    """Area of a Polygon / MultiPolygon GeoJSON geometry in km^2."""
    gtype = geometry.get("type")
    coords = geometry.get("coordinates") or []
    if gtype == "Polygon":
        return ring_area_km2(coords[0]) if coords else 0.0
    if gtype == "MultiPolygon":
        return sum(ring_area_km2(poly[0]) for poly in coords if poly)
    if gtype == "Point":
        return 0.0
    return 0.0


def bbox_area_km2(bbox: list[float]) -> float:
    return geometry_area_km2(bbox_polygon(bbox))


def bbox_span_km(bbox: list[float]) -> float:
    """Largest ground dimension of a bbox, in km.

    Area is the wrong gate for the statistics service: it renders the AOI at
    native 10 m, so the failure depends on the *longest side*, and a long thin
    box (a river reach) fails while a compact box of the same area succeeds.
    """
    lat = _to_radians((bbox[1] + bbox[3]) / 2.0)
    width = abs(bbox[2] - bbox[0]) * 111.32 * math.cos(lat)
    height = abs(bbox[3] - bbox[1]) * 111.32
    return max(width, height)


def buffer_bbox(centre: list[float], radius_km: float) -> list[float]:
    """Square AOI of `radius_km` around a lon/lat centre, clamped to valid WGS84.

    Used for features that are points or lines rather than areas — a peak, a
    landmark, a river — where a bounding box is either degenerate or spans the
    whole feature and is useless as an AOI.
    """
    lon, lat = float(centre[0]), float(centre[1])
    dlat = radius_km / 111.32
    dlon = radius_km / (111.32 * max(0.01, math.cos(_to_radians(lat))))
    return [
        max(-180.0, lon - dlon),
        max(-90.0, lat - dlat),
        min(180.0, lon + dlon),
        min(90.0, lat + dlat),
    ]


# --------------------------------------------------------------------------
# Simplification (Douglas-Peucker) to keep geometry small enough for a URL
# --------------------------------------------------------------------------
def _perp_distance_deg(pt: list[float], a: list[float], b: list[float]) -> float:
    if a[0] == b[0] and a[1] == b[1]:
        return math.hypot(pt[0] - a[0], pt[1] - a[1])
    # cross-product / base-length
    num = abs((b[0] - a[0]) * (a[1] - pt[1]) - (a[0] - pt[0]) * (b[1] - a[1]))
    den = math.hypot(b[0] - a[0], b[1] - a[1])
    return num / den


def simplify_ring(ring: list[list[float]], tolerance_deg: float) -> list[list[float]]:
    """Douglas-Peucker on an open ring; caller re-closes it."""
    if len(ring) <= 4:
        return ring
    pts = ring[:-1] if ring[0] == ring[-1] else ring
    if len(pts) < 3:
        return ring

    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        lo, hi = stack.pop()
        if hi <= lo + 1:
            continue
        dmax, index = 0.0, lo
        for i in range(lo + 1, hi):
            d = _perp_distance_deg(pts[i], pts[lo], pts[hi])
            if d > dmax:
                dmax, index = d, i
        if dmax > tolerance_deg:
            keep[index] = True
            stack.append((lo, index))
            stack.append((index, hi))

    out = [pts[i] for i in range(len(pts)) if keep[i]]
    if out[0] != out[-1]:
        out.append(out[0])
    return out


def simplify_geometry(geometry: dict[str, Any], bbox: list[float]) -> dict[str, Any]:
    """Simplify with a tolerance scaled to the AOI size (0.05% of the diagonal)."""
    diag = math.hypot(bbox[2] - bbox[0], bbox[3] - bbox[1])
    tol = max(diag * 0.0005, 1e-6)
    gtype = geometry.get("type")
    if gtype == "Polygon":
        rings = [simplify_ring(r, tol) for r in geometry.get("coordinates", [])]
        return {"type": "Polygon", "coordinates": rings}
    if gtype == "MultiPolygon":
        polys = [[simplify_ring(r, tol) for r in poly] for poly in geometry.get("coordinates", [])]
        return {"type": "MultiPolygon", "coordinates": polys}
    return geometry


def ensure_ccw(ring: list[list[float]]) -> list[list[float]]:
    """Make an exterior ring counter-clockwise (RFC 7946).

    Uses the standard shoelace sum, where positive means counter-clockwise.
    """
    if len(ring) < 3:
        return ring
    signed = 0.0
    for i in range(len(ring) - 1):
        x1, y1 = float(ring[i][0]), float(ring[i][1])
        x2, y2 = float(ring[i + 1][0]), float(ring[i + 1][1])
        signed += x1 * y2 - x2 * y1
    return ring if signed > 0 else list(reversed(ring))


def validate_bbox(bbox: list[float], *, minimum_km2: float | None = None) -> list[float]:
    """Reject a malformed AOI before it reaches a provider.

    Modelled on alephgeo's `bounds()`: finite numbers only, latitude/longitude
    ranges enforced, inverted corners rejected, date-line crossings refused rather
    than silently producing a tiny or negative area. A NaN bbox would otherwise
    propagate into area arithmetic and surface as a meaningless statistic.
    """
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        raise ValueError("bbox must be [west, south, east, north]")
    try:
        west, south, east, north = (float(v) for v in bbox)
    except (TypeError, ValueError) as exc:
        raise ValueError("bbox values must be numbers") from exc

    if not all(math.isfinite(v) for v in (west, south, east, north)):
        raise ValueError("bbox values must be finite numbers")
    if not (-90.0 <= south <= north <= 90.0):
        raise ValueError("bbox latitudes must satisfy -90 <= south <= north <= 90")
    if not (-180.0 <= west <= east <= 180.0):
        raise ValueError("bbox longitudes must satisfy -180 <= west <= east <= 180")
    if east - west > config.MAX_AOI_SPAN_DEG:
        raise ValueError("split areas crossing the date line into two requests")

    minimum = config.MIN_AOI_KM2 if minimum_km2 is None else minimum_km2
    area = bbox_area_km2([west, south, east, north])
    if area <= 0 or area < minimum:
        raise ValueError(f"the area must be at least {minimum:g} km²")

    return [west, south, east, north]


def normalize_aoi(
    *, bbox: list[float] | None = None, geometry: dict[str, Any] | None = None
) -> tuple[list[float], dict[str, Any], float, list[float]]:
    """Return (bbox, simplified geometry, area_km2, centre)."""
    if geometry is not None:
        bb = validate_bbox(geometry_bbox(geometry))
        geometry = geometry
    elif bbox is not None:
        bb = validate_bbox(bbox)
        geometry = bbox_polygon(bb)
    else:
        raise ValueError("Either bbox or geometry must be provided")

    if geometry.get("type") == "Polygon" and geometry.get("coordinates"):
        geometry = {
            "type": "Polygon",
            "coordinates": [ensure_ccw(geometry["coordinates"][0])] + list(geometry["coordinates"][1:]),
        }

    geom = simplify_geometry(geometry, bb)
    area = geometry_area_km2(geom)
    return bb, geom, area, bbox_center(bb)


def esri_envelope(bbox: list[float], wkid: int = 4326) -> dict[str, Any]:
    """Esri JSON envelope for ArcGIS REST calls."""
    return {
        "xmin": bbox[0],
        "ymin": bbox[1],
        "xmax": bbox[2],
        "ymax": bbox[3],
        "spatialReference": {"wkid": wkid},
    }


def haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    p1, p2 = _to_radians(lat1), _to_radians(lat2)
    dphi = _to_radians(lat2 - lat1)
    dlam = _to_radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))
