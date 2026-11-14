"""Compare the Earth Engine path with the COG path on identical scenes.

`/api/geo/index` reads one STAC scene's COGs with rasterio; `/api/geo/composite`
takes a median over every scene in the window inside Earth Engine. Comparing those
two endpoint outputs conflates scene selection with engine behaviour, so this script
joins the two on a single acquisition.

The join key is the granule id: Earth Search's `s2:product_uri` without `.SAFE` is
Earth Engine's `PRODUCT_ID` (verified 2026-11-14 against four scenes).

    SAGE_EE_PROJECT=... SAGE_EE_SERVICE_ACCOUNT=... SAGE_EE_KEY_FILE=... \
      .venv/bin/python scripts/compare_engines.py

Same AOI, same date window, same cloud limit, same SCL mask, same 10 m request.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.geostack import capabilities, earth, raster, stac  # noqa: E402

AOI = {
    "type": "Polygon",
    "coordinates": [
        [[78.4623, 17.4086], [78.4868, 17.4086], [78.4868, 17.4374], [78.4623, 17.4374], [78.4623, 17.4086]]
    ],
}
DATE_FROM = "2026-10-20"
DATE_TO = "2026-11-13"
MAX_CLOUD = 20.0
INDEX = "ndvi"
SCALE = 10


def product_key(item: dict) -> str:
    uri = (item.get("properties") or {}).get("s2:product_uri") or ""
    return uri[:-5] if uri.endswith(".SAFE") else uri


def cog_stats(item: dict) -> dict:
    hrefs = stac.assets_for(item, INDEX)
    convention = stac.reflectance(item)
    result = raster.compute_index(
        hrefs, AOI, INDEX, scale=convention["scale"], offset=convention["offset"]
    )
    summary = raster.summarize(result)
    return {"stats": summary.get("stats") or {}, "pixels": result["pixels"], "convention": convention}


def main() -> int:
    if not capabilities.earth_engine_config()["configured"]:
        print("Earth Engine is not configured; set the three SAGE_EE_* variables.")
        return 2

    items = stac.search(AOI, date_from=DATE_FROM, date_to=DATE_TO, max_cloud=MAX_CLOUD, limit=20)
    if not items:
        print("no scene matched; nothing to compare")
        return 0
    items.sort(key=lambda i: (i["properties"].get("datetime") or ""), reverse=True)

    print(f"AOI 5.6 km2, {DATE_FROM} to {DATE_TO}, cloud < {MAX_CLOUD:.0f}%, index {INDEX.upper()}, 10 m")
    print(f"{len(items)} STAC scene(s):")
    for item in items:
        print(f"  {item['id']}  cloud {item['properties'].get('eo:cloud_cover'):.2f}%")

    header = (
        f"\n{'scene':<30}{'tile':<8}{'COG px':>9}{'EE px':>9}"
        f"{'COG mean':>10}{'EE mean':>10}{'delta':>9}{'COG p50':>10}{'EE p50':>10}"
    )
    print(header)
    print("-" * len(header))

    for item in items:
        key = product_key(item)
        tile = (item["properties"].get("grid:code") or "?").replace("MGRS-", "")
        try:
            cog = cog_stats(item)
        except Exception as exc:  # noqa: BLE001 - one bad scene must not stop the rest
            print(f"{item['id']:<30}{tile:<8}  COG read failed: {type(exc).__name__}: {exc}")
            continue
        try:
            ee = earth.index_stats(
                AOI,
                index=INDEX,
                date_from=DATE_FROM,
                date_to=DATE_TO,
                max_cloud=MAX_CLOUD,
                scale=SCALE,
                product_id=key,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"{item['id']:<30}{tile:<8}  EE query failed: {type(exc).__name__}: {exc}")
            continue

        c, e = cog["stats"], ee["stats"]
        delta = (e.get("mean") or 0) - (c.get("mean") or 0)
        print(
            f"{item['id']:<30}{tile:<8}{c.get('count', 0):>9,}{e.get('count', 0):>9,}"
            f"{c.get('mean', float('nan')):>10.4f}{e.get('mean', float('nan')):>10.4f}"
            f"{delta:>+9.4f}{c.get('median', float('nan')):>10.4f}{e.get('median', float('nan')):>10.4f}"
        )

    composite = earth.index_stats(
        AOI, index=INDEX, date_from=DATE_FROM, date_to=DATE_TO, max_cloud=MAX_CLOUD, scale=SCALE
    )
    stats = composite["stats"]
    print(
        f"\nEE median composite over all {composite['scene_count']} scenes: "
        f"mean {stats['mean']:.4f}, median {stats['median']:.4f}, "
        f"{stats['count']:,} px, {composite['elapsed_seconds']} s"
    )
    print(
        "\nThe COG statistics are computed on the scene's native UTM grid; Earth Engine\n"
        "reprojects to a WGS84 10 m grid, so pixel counts differ even when the input\n"
        "is the same granule. A per-scene delta is engine + resampling; the distance\n"
        "between a per-scene mean and the composite mean is scene selection."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
