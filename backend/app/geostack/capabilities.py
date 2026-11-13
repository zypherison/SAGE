"""What the optional stack can do right now.

The idea is borrowed from GeoLibre, where features are advertised only when the
engine behind them exists: the client asks what is available and hides the rest,
instead of offering a button that fails on click. Nothing here imports a heavy
module to answer the question — `find_spec` only looks for it.
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from typing import Any

from app.errors import GEOSTACK_UNAVAILABLE, AppError

# distribution name -> import name
MODULES: dict[str, str] = {
    "geopandas": "geopandas",
    "rasterio": "rasterio",
    "pyproj": "pyproj",
    "shapely": "shapely",
    "earthengine-api": "ee",
    "geemap": "geemap",
}


def installed(module: str) -> bool:
    # A broken or half-installed package can raise from find_spec; that is
    # "not available", not a reason for the capability probe to return 500.
    try:
        return importlib.util.find_spec(module) is not None
    except Exception:
        return False


def version(module: str) -> str | None:
    """Version string, or None. Imports only modules that already exist."""
    if not installed(module):
        return None
    try:
        mod = importlib.import_module(module)
    except Exception:  # a broken install is "not available", not a 500
        return None
    return str(getattr(mod, "__version__", "unknown"))


def require(module: str) -> Any:
    """Import a module or fail with a code the client can branch on."""
    try:
        return importlib.import_module(module)
    except Exception as exc:  # ImportError, or a broken native library
        raise AppError(
            GEOSTACK_UNAVAILABLE,
            f"the optional geospatial stack needs `{module}` ({exc.__class__.__name__}): "
            "install backend/requirements-geo.txt",
            module=module,
        ) from exc


def earth_engine_config() -> dict[str, Any]:
    """Earth Engine credentials, read from the environment at call time.

    A service account is the only shape that works unattended. The project id is
    required by `ee.Initialize` since Earth Engine moved to Cloud projects.
    """
    project = os.environ.get("SAGE_EE_PROJECT", "")
    account = os.environ.get("SAGE_EE_SERVICE_ACCOUNT", "")
    key_file = os.environ.get("SAGE_EE_KEY_FILE", "")
    configured = bool(project and account and key_file and os.path.exists(key_file))
    return {
        "configured": configured,
        "project": project or None,
        "service_account": account or None,
        "key_file_present": bool(key_file and os.path.exists(key_file)),
    }


def report() -> dict[str, Any]:
    versions = {dist: version(mod) for dist, mod in MODULES.items()}
    missing = [dist for dist, ver in versions.items() if ver is None]
    ee = earth_engine_config()
    return {
        "stack": "geostack",
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "installed": versions,
        "missing": missing,
        "vector_ready": versions["geopandas"] is not None and versions["pyproj"] is not None,
        "raster_ready": versions["rasterio"] is not None,
        "population_ready": versions["rasterio"] is not None,
        "map_ready": versions["geemap"] is not None,
        "earth_engine": ee,
        "notes": [
            "vector/ raster/ WorldPop work offline once their inputs are local",
            "Earth Engine needs SAGE_EE_PROJECT, SAGE_EE_SERVICE_ACCOUNT and SAGE_EE_KEY_FILE",
            "WorldPop rasters are cached under data/cache/worldpop",
        ],
    }
