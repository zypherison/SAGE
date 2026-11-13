"""Interactive HTML maps with geemap.

geemap is a Jupyter tool by origin, but its `Map.to_html` gives a self-contained
Leaflet page: no tile server, no running notebook, no credentials. That makes a
good shareable artifact for a report, and it is the only part of the GeoLibre-style
"open the project and look at it" idea that fits a prototype API.

When Earth Engine is configured the NDVI composite is added as a layer too; when
it is not, the map still shows the AOI rather than failing.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
from pathlib import Path
from typing import Any

from app import config
from app.errors import GEOSTACK_UNAVAILABLE, AppError
from app.geostack import capabilities, vector

NAME_RE = re.compile(r"^[a-f0-9]{16}\.html$")


def build(
    geometry: dict[str, Any],
    *,
    title: str = "SAGE AOI",
    index: str = "ndvi",
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    geemap = capabilities.require("geemap")
    # ipyleaflet asks asyncio for the current loop while the Map is built. A
    # FastAPI sync endpoint runs in a worker thread that has none, so install
    # one rather than failing with "no current event loop". It is never run;
    # the loop only has to exist for widget construction.
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    shapely_geom, _audit = vector.load(geometry)
    lon, lat = shapely_geom.centroid.x, shapely_geom.centroid.y

    import geopandas as gpd

    frame = gpd.GeoDataFrame(geometry=[shapely_geom], crs="EPSG:4326")
    # `ee_initialize=False` is load-bearing: the default tries an interactive
    # Earth Engine OAuth flow, which in a server process blocks on stdin and
    # raises EOFError. EE is initialised through `earth.init()` when a layer is
    # actually requested, so the map still renders without credentials.
    m = geemap.Map(center=[lat, lon], zoom=13, ee_initialize=False)
    layers: list[str] = ["AOI"]
    m.add_gdf(frame, layer_name="AOI", style={"color": "#ff4d4f", "weight": 2, "fillOpacity": 0.12})

    ee_note = None
    if date_from and date_to:
        try:
            from app.geostack import earth

            ee_image = earth.image(geometry, index=index, date_from=date_from, date_to=date_to)
            vis = earth.VIS.get(index, earth.VIS["ndvi"])
            add = getattr(m, "add_ee_layer", None) or getattr(m, "addLayer", None)
            if add is not None:
                add(ee_image, vis, f"{index.upper()} {date_from}..{date_to}")
                layers.append(f"{index.upper()} composite")
        except AppError as exc:
            if exc.code != GEOSTACK_UNAVAILABLE:
                raise
            ee_note = "Earth Engine not configured; map shows the AOI only"
    else:
        ee_note = "no date window given; map shows the AOI only"

    if hasattr(m, "add_layer_control"):
        m.add_layer_control()

    digest = hashlib.sha256(f"{title}|{geometry}|{index}|{date_from}|{date_to}".encode()).hexdigest()[:16]
    directory = Path(config.MAP_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{digest}.html"
    m.to_html(str(path))
    return {
        "map_id": path.name,
        "url": f"/api/geo/map/{path.name}",
        "bytes": path.stat().st_size,
        "title": title,
        "layers": layers,
        "centre": [round(lat, 6), round(lon, 6)],
        "note": ee_note,
    }


def path_for(name: str) -> Path:
    if not NAME_RE.match(name):
        raise AppError("invalid_arguments", "map name must be a 16-character hex id plus .html")
    path = Path(config.MAP_DIR) / name
    if not path.exists():
        raise AppError("evidence_not_found", f"no map named {name}")
    return path


def stored() -> list[dict[str, Any]]:
    directory = Path(config.MAP_DIR)
    if not directory.exists():
        return []
    return [
        {"map_id": p.name, "bytes": p.stat().st_size, "url": f"/api/geo/map/{p.name}"}
        for p in sorted(directory.glob("*.html"), key=lambda p: p.stat().st_mtime, reverse=True)
    ]
