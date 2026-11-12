"""SAGE — centralized configuration.

Every endpoint here was probed live (see SEMINAR2.md §0.1). Provider quirks are
documented next to the constant that encodes them, because two of them (RFC3339
datetime and silently-ignored CQL2 filters) cause hard-to-see correctness bugs.
"""
from __future__ import annotations

import os


def env(name: str, default: str) -> str:
    return os.environ.get(name, default)


VERSION = "2.0.0"

# --------------------------------------------------------------------------
# Providers
# --------------------------------------------------------------------------
EARTH_SEARCH_URL = env("SAGE_EARTH_SEARCH_URL", "https://earth-search.aws.element84.com/v1")
PLANETARY_URL = env("SAGE_PLANETARY_URL", "https://planetarycomputer.microsoft.com/api/stac/v1")

# ArcGIS Sentinel-2 ImageServer — no auth for query/export/stats.
# LIMITATION: rolling ~14-month window, updated daily. Not an archive.
ARCGIS_URL = env(
    "SAGE_ARCGIS_URL",
    "https://sentinel.arcgis.com/arcgis/rest/services/Sentinel2/ImageServer",
)
ARCGIS_MAX_EXPORT = 4000  # server-side maxImageWidth/Height

NOMINATIM_URL = env("SAGE_NOMINATIM_URL", "https://nominatim.openstreetmap.org")
USER_AGENT = env("SAGE_USER_AGENT", "SAGE/2.0 (student research prototype; MJCET CSE)")

REQUEST_TIMEOUT = float(env("SAGE_REQUEST_TIMEOUT", "25"))

# Networking policy, adopted from alephgeo's Client/CachedClient.
REQUEST_DELAY = float(env("SAGE_REQUEST_DELAY", "0.25"))  # seconds between calls to the same host
RETRIES = int(env("SAGE_RETRIES", "3"))                  # bounded retries with exponential backoff
CACHE_TTL = float(env("SAGE_CACHE_TTL", "300"))          # index/metadata cache, seconds
GEOCODE_CACHE_TTL = float(env("SAGE_GEOCODE_CACHE_TTL", "86400"))  # names rarely move

# Nominal per-provider latency for the pre-flight plan. These are ESTIMATES from
# observed runs, shown to the user as ~seconds; they are not measurements.
ESTIMATED_REQUEST_SECONDS = {
    "earth-search": 0.60,
    "planetary-computer": 0.75,
    "arcgis-sentinel2": 0.70,
}

# --------------------------------------------------------------------------
# Limits
# --------------------------------------------------------------------------
DEFAULT_LIMIT = 40
MAX_LIMIT = 120
MAX_AOI_AREA_KM2 = 5000.0  # above this, index statistics are refused, not timed out
MIN_VALID_PIXEL_FRACTION = 0.30  # below this, return insufficient_data
MIN_AOI_KM2 = 0.01  # reject degenerate boxes before any network call
MAX_AOI_SPAN_DEG = 180.0  # larger east-west spans must be split (date line)

# The statistics service renders the AOI at native 10 m and refuses anything
# wider than ~38 km — measured 2026-11-12: 38 km square OK, 39 km fails, and a
# 45x5 km strip fails too, so the gate is the LONGEST SIDE, not the area.
# `pixelSize` does not lower it: it returns HTTP 200 with an EMPTY statistics
# array (a silent empty, the same trap class as §4.2), so the AOI is capped here.
MAX_STATS_SPAN_KM = 38.0

# Named features that are points or lines (a peak, a monument, a river) get a
# square AOI of this half-width around their centre.
LOCALITY_BUFFER_KM = 3.0
MIN_LOCALITY_KM2 = 1.0  # below this, a geocoder bbox is a point, not an area

# --------------------------------------------------------------------------
# One-click download
# --------------------------------------------------------------------------
# One render per acquisition day. 8 keeps a three-month request useful without
# turning a single click into a 30-image, tens-of-megabytes download.
DOWNLOAD_MAX_IMAGES = int(env("SAGE_DOWNLOAD_MAX_IMAGES", "8"))
DOWNLOAD_LONG_SIDE = int(env("SAGE_DOWNLOAD_LONG_SIDE", "1024"))  # px, aspect preserved
# A blank exportImage comes back as ~4 KB of PNG; a real 1024-wide render is
# 240 KB+. Verified by probe (see services/download.py), so this threshold is
# measured rather than guessed.
DOWNLOAD_MIN_BYTES = int(env("SAGE_DOWNLOAD_MIN_BYTES", "6000"))

# One statistics call per point, so the cap is about requests, not file size.
TIMESERIES_MAX_POINTS = int(env("SAGE_TIMESERIES_MAX_POINTS", "10"))

# Imagery lags a day or two, so a literal "today" has no overpass. A comparison
# snaps each side to the nearest acquisition inside this window and reports the
# date it actually used.
COMPARE_WINDOW_DAYS = int(env("SAGE_COMPARE_WINDOW_DAYS", "7"))

# --------------------------------------------------------------------------
# Index definitions: our name -> (formula label, ArcGIS raster function, bands)
# Raster function names verified against the service's rasterFunctionInfos list.
# --------------------------------------------------------------------------
INDEX_DEFS: dict[str, dict[str, object]] = {
    "ndvi": {
        "label": "NDVI",
        "description": "Normalized Difference Vegetation Index",
        "formula": "(NIR - Red) / (NIR + Red)",
        "bands": {"nir": "B8", "red": "B4"},
        "arcgis_function": "NDVI Raw",
        "arcgis_visual": "NDVI Colormap",
        "range": (-1.0, 1.0),
    },
    "ndwi": {
        "label": "NDWI",
        "description": "Normalized Difference Water Index",
        "formula": "(Green - NIR) / (Green + NIR)",
        "bands": {"green": "B3", "nir": "B8"},
        "arcgis_function": "NDWI Raw",
        "arcgis_visual": "NDWI - with VRE Raw",
        "range": (-1.0, 1.0),
    },
    "nbr": {
        "label": "NBR",
        "description": "Normalized Burn Ratio",
        "formula": "(NIR - SWIR2) / (NIR + SWIR2)",
        "bands": {"nir": "B8", "swir2": "B12"},
        "arcgis_function": "Normalized Burn Ratio",
        "arcgis_visual": "Normalized Burn Ratio",
        "range": (-1.0, 1.0),
    },
    "ndbi": {
        "label": "NDBI",
        "description": "Normalized Difference Built-up Index",
        "formula": "(SWIR1 - NIR) / (SWIR1 + NIR)",
        "bands": {"swir1": "B11", "nir": "B8"},
        "arcgis_function": "Normalized Difference Built-Up Index (NDBI)",
        "arcgis_visual": "Normalized Difference Built-Up Index (NDBI)",
        "range": (-1.0, 1.0),
    },
}

# Rank weights (exposed in the UI so ranking is explainable)
RANK_WEIGHTS = {
    "clear": 0.45,      # 1 - cloud/100
    "temporal": 0.30,   # exp(-|t - t_target| / tau)
    "index": 0.15,      # provider can compute indices server-side
    "resolution": 0.10,  # finer GSD is better
}
TEMPORAL_TAU_DAYS = 15.0

# Evidence store (append-only JSONL; no DB dependency for a prototype)
EVIDENCE_PATH = env(
    "SAGE_EVIDENCE_PATH",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "evidence.jsonl"),
)
