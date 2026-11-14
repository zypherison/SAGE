"""Verify Earth Engine credentials end to end, with a diagnosis for each failure.

Run it the same way the API runs, i.e. with the three environment variables set:

    SAGE_EE_PROJECT=my-project \
    SAGE_EE_SERVICE_ACCOUNT=sage-geo@my-project.iam.gserviceaccount.com \
    SAGE_EE_KEY_FILE="$HOME/.config/sage/ee-key.json" \
      backend/.venv/bin/python backend/scripts/ee_check.py

Exit codes: 0 ok, 2 configuration incomplete, 3 initialisation failed,
4 authenticated but the query failed (usually project registration or IAM).

Nothing here reads a key from the repository, and the key is never printed.
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.geostack import capabilities  # noqa: E402

HUSSAIN_SAGAR = {
    "type": "Polygon",
    "coordinates": [
        [[78.4623, 17.4086], [78.4868, 17.4086], [78.4868, 17.4374], [78.4623, 17.4374], [78.4623, 17.4086]]
    ],
}


def hint_for(exc: Exception) -> str:
    text = str(exc)
    if "403" in text or "PERMISSION_DENIED" in text:
        return (
            "Permission denied. Most often the Cloud project is not registered for Earth Engine "
            "(https://code.earthengine.google.com/register), or the service account lacks a role. "
            "Grant it Earth Engine Resource Viewer and Service Usage Consumer."
        )
    if "401" in text or "invalid_grant" in text or "Invalid JWT" in text:
        return (
            "The key was rejected. Check that the JSON is a service-account key (not an OAuth "
            "client), that the file is readable, and that its clock is sane."
        )
    if "not found" in text.lower() or "404" in text:
        return (
            "The project was not found. SAGE_EE_PROJECT must be the Cloud project ID "
            "(lowercase, hyphens), not the project display name."
        )
    if "not signed up" in text.lower() or "registration" in text.lower():
        return "The project exists but is not registered for Earth Engine. Register it, then retry."
    return text


def main() -> int:
    config = capabilities.earth_engine_config()
    shown = {k: v for k, v in config.items() if k != "key_file"}
    print("configuration")
    print(json.dumps(shown, indent=2))

    missing = [
        name
        for name in ("SAGE_EE_PROJECT", "SAGE_EE_SERVICE_ACCOUNT", "SAGE_EE_KEY_FILE")
        if not os.environ.get(name)
    ]
    if missing or not config["configured"]:
        print("\nnot configured yet")
        if missing:
            print("  missing environment variables: " + ", ".join(missing))
        if os.environ.get("SAGE_EE_KEY_FILE") and not os.path.exists(os.environ["SAGE_EE_KEY_FILE"]):
            print("  the key file path does not exist on disk")
        print("\nfollow the setup steps, then re-run this script.")
        return 2

    from app.geostack import earth

    print("\ninitialising")
    started = time.time()
    try:
        ee = earth.init()
    except Exception as exc:  # noqa: BLE001 - the whole point is to explain it
        print(f"  failed after {time.time() - started:.1f}s: {type(exc).__name__}")
        print("  " + hint_for(exc))
        return 3
    print(f"  ok ({time.time() - started:.1f}s)")

    errors = 0

    print("\ncheck 1 — arithmetic round trip")
    try:
        value = ee.Number(20).add(22).getInfo()
        print(f"  20 + 22 = {value}")
        errors += int(value != 42)
    except Exception as exc:  # noqa: BLE001
        errors += 1
        print("  failed: " + hint_for(exc))

    print("\ncheck 2 — catalogue access (last 60 days of Sentinel-2 over Hussain Sagar)")
    try:
        end = time.strftime("%Y-%m-%d", time.gmtime())
        start = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 60 * 86400))
        collection = (
            ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterBounds(ee.Geometry(HUSSAIN_SAGAR))
            .filterDate(f"{start}T00:00:00Z", f"{end}T23:59:59Z")
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 40))
        )
        count = int(collection.size().getInfo())
        print(f"  {count} scenes between {start} and {end}")
        errors += int(count == 0)
    except Exception as exc:  # noqa: BLE001
        errors += 1
        print("  failed: " + hint_for(exc))

    print("\ncheck 3 — compute a real statistic (NDVI median composite, reduced at 60 m)")
    try:
        composite = collection.median()
        ndvi = composite.normalizedDifference(["B8", "B4"]).rename("ndvi")
        reduced = ndvi.reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=ee.Geometry(HUSSAIN_SAGAR),
            scale=60,
            maxPixels=int(1e9),
            bestEffort=True,
        ).getInfo()
        print(f"  {reduced}")
        errors += int(not reduced.get("ndvi"))
    except Exception as exc:  # noqa: BLE001
        errors += 1
        print("  failed: " + hint_for(exc))

    print("\nresult")
    if errors:
        print(f"  {errors} check(s) failed; /api/geo/composite will not work yet.")
        return 4
    print("  all checks passed. Set the same three variables before starting uvicorn and")
    print("  /api/geo/composite, plus the map layer, will work.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
