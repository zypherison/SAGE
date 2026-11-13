# SAGE

Satellite imagery **area-of-interest search** with **deterministic spectral-index statistics**
and **replayable evidence IDs**.

The planning document that this implements is [`../SEMINAR2.md`](../SEMINAR2.md).

## What it actually does

1. Takes a natural-language query, a bare place name, or a hand-drawn AOI.
2. Parses intent with a **rule-based parser** (no language model anywhere in the request path).
3. Fans out across three open catalogues and merges duplicates.
4. Computes NDVI / NDWI / NBR / NDBI statistics **server-side** on the ArcGIS Sentinel-2
   ImageServer — the only source in this set that returns index values without authentication.
5. Returns an **evidence id** = `sha256(payload)[:16]` that can be replayed.
6. Downloads imagery in one click — a single day, or one image per acquisition day across a
   range — as a zip containing `manifest.json` (objectids, dates, sha256, rendering rule) and
   `README.txt` (attribution and limitations).
7. Plots an index **per acquisition day** as a trend line with a CSV export, and prints a
   one-page report (Print / Save as PDF) carrying the statistics, image, evidence id and citation.
8. Serves `/api/demo`, a cached reference measurement, so a demonstration survives a dead
   network. It is labelled as a fixture in `stats.source` and in the UI, never as live data.
9. Compares two dates side by side — two pinned acquisitions, two images, a signed difference —
   and puts a copy-ready citation line (`Sentinel-2 L2A … accessed <date>`) in the printed report.
10. Draws the AOI as a **box or a polygon** (click each corner, double-click to close). The
    polygon is sent to the statistics service, so the count and every km² figure describe the
    shape actually drawn — not its envelope, which for a lake shore or a river reach includes
    land the user did not select.
11. Converts the index histogram it already receives into **class areas in km²** — e.g. NDVI > 0.30
    covers 132.4 km² ± 9.1 — with the threshold, its basis and the boundary bin's uncertainty
    reported next to the number. The threshold is a stated definition, not a land-cover product.
12. Compares the **same calendar month across years** (`/api/seasonal`), so a change is not just
    the season. Each year is a median across that month's acquisitions — not a pixel-wise
    composite, because the source silently ignores a median mosaic rule (probed).
13. Reads **Landsat 8/9 thermal** (`/api/thermal`): at-sensor brightness temperature in °C, same
    month across up to five years, cloud-contaminated scenes rejected by physical plausibility
    and listed with the reason. Labelled as brightness temperature, never as land-surface
temperature.
14. Serves an optional **geostack** (`/api/geo`): geopandas/pyproj AOI metrics with a projection
    cross-check, rasterio index statistics read straight from Sentinel-2 COGs through STAC,
    WorldPop population inside the AOI, Earth Engine composites when credentials exist, and a
    self-contained geemap HTML map. The heavy libraries are optional — without them the rest of
    the API is unchanged. See [`backend/GEOSTACK.md`](backend/GEOSTACK.md).

## Requirements

- Python **3.10+** (3.9 cannot evaluate the `X | None` annotations pydantic uses here)
- Node **18+**

## Run

```bash
# terminal 1 — backend
cd sage2/backend
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload --port 8000

# terminal 2 — frontend
cd sage2/frontend
npm install
npm run dev
```

Open <http://localhost:5173>. API docs: <http://localhost:8000/docs>.

## Optional geostack

```bash
backend/.venv/bin/pip install -r backend/requirements-geo.txt
```

Without it, `/api/geo/*` answers `503 geostack_unavailable` and everything else keeps
working. Earth Engine additionally needs `SAGE_EE_PROJECT`, `SAGE_EE_SERVICE_ACCOUNT`
and `SAGE_EE_KEY_FILE`; until then those endpoints report what is missing.

## Tests

```bash
cd sage2/backend
.venv/bin/python -m pytest -q
```

The suite is offline: network providers are unit-tested through pure functions only.
`data/gold/` holds the live reference measurements and the parser regression set.

## Known limitations (deliberate, not bugs)

| Limitation | Why |
|---|---|
| ArcGIS source only covers a **rolling ~14-month window** | Service design; it is not an archive. Queries older than that correctly return `0` scenes, and the API says so instead of failing silently. |
| Earth Search needs **full RFC3339** datetimes | Short `date/date` form returns HTTP 400. |
| CQL2 `filter` on Earth Search is **silently ignored** | Verified: a `<20 %` filter still returned a 96.7 % cloud scene. The adapter uses the `query` extension *and* re-filters client-side. |
| Planetary Computer assets need a **SAS token** | We link out rather than proxy credentials. |
| Index statistics are refused above **38 km across** — a span, not an area | Measured: 38 km works, 39 km fails, and a 45×5 km strip fails too, so the longest side is what the service enforces. `pixelSize` is not a workaround (HTTP 200 with an empty `statistics` array), so the AOI is capped before the call and the message says how wide is allowed. |
| A drawn **polygon** clips statistics but not previews | The service accepts the ring for `computeStatisticsHistograms`, so counts and areas describe the drawn shape; `exportImage` and the download are still rendered over its bounding box, so the preview may show a little land outside the polygon. |
| A named **river, peak or landmark** gets a 3 km AOI around the feature | Its bbox is either degenerate (a monument) or spans the entire feature (a river across several states). The run says which happened; draw a box to pick a different reach. |
| A download is **one image per acquisition day**, capped at 8 | A date-window `time` filter on the image service returns a blank 4 KB image (verified — see below), so each day is pinned by catalogue objectid instead. The cap keeps one click from producing a 30-file archive. |
| Basemaps are keyless and openly licensed only | OSM standard (ODbL) and OpenTopoMap (CC-BY-SA). CARTO's proprietary `dark_all` style was removed because it is not open data, so there is no hosted dark basemap — the map is light while the panels stay dark. |
| Provenance never guesses coordinates | Filename gives mission/date/tile; GPS comes from EXIF; otherwise it abstains. |
| An ambiguous place name returns **409 `ambiguous_place`** with candidates | Adopted from `alephgeo`'s `choose()`: taking the first geocoder hit is how "Tokyo June" became a hairdresser. Pick one by id, or send `best_match: true`. |
| A **pixel-wise median composite** is not available from the current source | `mosaicMethod: median` is accepted and silently ignored — it returns the identical value to the nonsense method `"banana"`, while `esriMosaicNadir` differs. So `/api/seasonal` reports the median across *acquisitions* of the AOI statistic, and says so in `notes`; it never claims to average pixels. |
| Thermal is **brightness temperature**, not land-surface temperature | The Landsat Level-1 thermal band gives at-sensor temperature after the Planck calibration; LST additionally needs per-pixel emissivity and atmospheric correction, so the response is labelled `brightness_temperature` and the difference is stated as a few kelvin. |
| Thermal scenes are rejected by **plausibility**, not by a cloud mask | Catalogue cloud cover is a whole-scene figure: a scene listed at 0.9 % cloud averaged −12 °C over a small AOI (cloud top) while a clear one over the same water read +26 °C. There is no server-side QA mask here, so scenes outside −5…60 °C are listed under `rejected` with that reason. |
| Responses are cached in-process for **300 s** (names 24 h) | Index statistics describe a mosaic updated daily; a longer cache would return wrong numbers. Every response reports `cache {hits, misses}`. |

## Layout

```
sage2/
  backend/
    app/
      main.py              FastAPI routes
      config.py            endpoints, index definitions, limits, rank weights
      schemas.py           canonical Scene / Intent / Index / Provenance models
      errors.py            coded failure envelope (adopted from alephgeo)
      services/
        query_parser.py    rule-based intent parser
        geocode.py         Nominatim + explicit ambiguity handling
        net.py             paced, cached, retrying HTTP (Fetch)
        geo.py             geodesic area, IoU, Douglas-Peucker, bbox validation
        discovery.py       plan, fan-out, dedupe, ranking
        download.py        one-click imagery zip + provenance manifest
        timeseries.py      one index point per acquisition day
        compare.py         two dates, two pinned acquisitions, one difference
        indices.py         deterministic index engine + QC gates
        evidence.py        evidence manifests + replay comparison
        provenance.py      image provenance cascade
        providers/         earth_search.py, planetary.py, arcgis.py
    data/gold/             live reference fixtures
    tests/                 107 offline tests
  frontend/
    src/App.tsx            single screen
    src/components/MapView.tsx  Leaflet + drag-to-draw AOI
    src/lib/api.ts         typed client
```
