# SAGE

Satellite imagery **area-of-interest search** with **deterministic spectral-index statistics**
and **replayable evidence IDs**.

The planning document that this implements is [`SEMINAR2.md`](SEMINAR2.md).

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
| A named **river, peak or landmark** gets a 3 km AOI around the feature | Its bbox is either degenerate (a monument) or spans the entire feature (a river across several states). The run says which happened; draw a box to pick a different reach. |
| A download is **one image per acquisition day**, capped at 8 | A date-window `time` filter on the image service returns a blank 4 KB image (verified — see below), so each day is pinned by catalogue objectid instead. The cap keeps one click from producing a 30-file archive. |
| Basemaps are keyless and openly licensed only | OSM standard (ODbL) and OpenTopoMap (CC-BY-SA). CARTO's proprietary `dark_all` style was removed because it is not open data, so there is no hosted dark basemap — the map is light while the panels stay dark. |
| Provenance never guesses coordinates | Filename gives mission/date/tile; GPS comes from EXIF; otherwise it abstains. |
| An ambiguous place name returns **409 `ambiguous_place`** with candidates | Adopted from `alephgeo`'s `choose()`: taking the first geocoder hit is how "Tokyo June" became a hairdresser. Pick one by id, or send `best_match: true`. |
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
