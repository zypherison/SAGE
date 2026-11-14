# Geostack: vectors, rasters, Earth Engine, WorldPop and maps

`app/geostack` is an optional analysis layer behind `/api/geo`. It adds geopandas,
rasterio, pyproj, Earth Engine and geemap **without** making them a requirement for
the rest of SAGE: every heavy import happens inside a handler, and a missing library
returns `503 geostack_unavailable` naming the package instead of taking the app down.

```
geostack/
  capabilities.py   what is installed / configured (no imports, no side effects)
  vector.py         geopandas + pyproj: AOI repair, geodesic metrics, projection check
  raster.py         rasterio: read COGs in place, index maths, zonal stats, PNG preview
  stac.py           Earth Search: scene search, band assets, reflectance convention
  population.py     WorldPop: cached country raster, population sum inside an AOI
  earth.py          Earth Engine: median composites and population (needs credentials)
  maps.py           geemap: a self-contained interactive HTML map
  router.py         the /api/geo endpoints
```

## What GeoLibre contributed

[GeoLibre](https://geolibre.app/) is a cloud-native GIS (Tauri + React + MapLibre +
DuckDB-WASM) that reads COG, GeoParquet, PMTiles, FlatGeobuf, STAC and Zarr in place,
runs 1,000+ tools in the browser, and saves a map as an open project file. Its Python
sidecar is a FastAPI service, so its architecture is close enough to be instructive.

Borrowed, in order of how much it changed the design:

| Idea | How it appears here |
| --- | --- |
| Capability discovery instead of hard dependencies | `/api/geo/capabilities` reports versions and credentials; the UI can hide what is missing rather than offering a button that fails |
| Read cloud-native data in place | Sentinel-2 COG windows are fetched through GDAL `/vsicurl`; a 8.3 km² AOI costs megabytes, not a scene download |
| STAC as the sourcing layer, not a file list | `stac.py` turns an AOI and a window into band hrefs, then straight into rasterio |
| Colour ramps are part of the output | `raster.RAMPS` + `write_preview` produce a legend-consistent PNG |
| Deterministic, serialisable results | every index run is stored with the same evidence id the rest of SAGE uses, so it can be replayed |
| Honest degradation | no Earth Engine credentials means the map still renders and the AOI is still measured |

Deliberately **not** copied: WebAssembly compute, DuckDB spatial, in-browser
processing, multi-renderer support, project-file format. Those solve a different
problem (an installable desktop GIS) and would be scope without purpose here.

## Endpoints

| Method | Path | What it does |
| --- | --- | --- |
| GET | `/api/geo/capabilities` | installed versions, missing packages, EE config, WorldPop cache, recent maps |
| POST | `/api/geo/aoi` | ellipsoid area/perimeter, centroid, bbox, compactness, UTM cross-check, repair audit |
| POST | `/api/geo/scene` | STAC candidates for an AOI and window, newest first |
| POST | `/api/geo/index` | index statistics from a real scene: COG window, SCL mask, histogram, evidence id, optional PNG |
| POST | `/api/geo/population` | WorldPop population inside the AOI, with the raster's provenance |
| POST | `/api/geo/composite` | Earth Engine median composite over a window (503 until configured) |
| POST | `/api/geo/landcover` | Dynamic World class areas, plus an Open Buildings structure count |
| POST | `/api/geo/map` | writes an interactive geemap HTML map, optionally with EE and land-cover layers |
| GET | `/api/geo/map/{id}` | serves that map |
| GET | `/api/geo/preview/{id}` | serves an index PNG |

```bash
curl -s -X POST localhost:8000/api/geo/index -H 'content-type: application/json' -d '{
  "geometry": {"type":"Polygon","coordinates":[[[78.4623,17.4086],[78.4868,17.4086],
                [78.4868,17.4374],[78.4623,17.4374],[78.4623,17.4086]]]},
  "index": "ndvi", "date_from": "2026-10-20", "date_to": "2026-11-13", "preview": true }'
```

## Measured on 2026-11-13

Hussain Sagar, 8.30 km² box, scene `S2C_43QHV_20261113_0_L2A` (cloud 9.6 %):

| Quantity | Value |
| --- | --- |
| NDVI mean / median | 0.172 / 0.052 |
| NDVI p10 / p90 | −0.128 / 0.724 |
| Valid pixels | 83,134 of 83,146 inside the AOI |
| Cloud-masked pixels | 12 (SCL 3, 8, 9, 10, 11) |
| Wall clock, cold | 37.6 s (search + three COG reads) |
| WorldPop population | 20,066 in 5 of 9 one-kilometre cells, 2,418 / km² |
| Map HTML | 32,171 bytes, self-contained |
| Index preview PNG | 121,846 bytes |

The ArcGIS path in the same app returns NDVI mean 0.067 for the same box. That is
**not** a discrepancy to fix: ArcGIS serves a rolling mosaic with its own masking,
while this number is one acquisition, and the two grids differ. The two agree in
sign and order of magnitude, which is all that can be claimed without pinning the
same scene on both sides.

## Three quirks worth keeping

1. **The asset-level reflectance offset is a trap.** Earth Search items advertise
   `raster:bands.offset: -0.1` for every scene with baseline ≥ 04.00, but the
   `+1000` DN shift is already baked into the COG. Applying the offset took a median
   red reflectance of 0.087 to −0.013 and produced an NDVI mean of 1.28 with values
   above 2. The item-level `earthsearch:boa_offset_applied` flag is the authority
   ([Element84 issue #71](https://github.com/Element84/earth-search/issues/71)); the
   convention is echoed in every `/api/geo/index` response.
2. **WorldPop cannot be read in place.** Its file server sends `Accept-Ranges: bytes`
   but GDAL answers `Range downloading not supported by this server`, and the stats
   API returns 404. The country raster (18.3 MB for India 2020) is downloaded once
   to `data/cache/worldpop/` and reused.
3. **`geemap.Map` authenticates Earth Engine on construction.** In a server that
   blocks on stdin and raises `EOFError`. `maps.py` passes `ee_initialize=False` and
   initialises EE explicitly only when a layer is requested.

Smaller ones: a window smaller than half a pixel rounds to zero and GDAL then raises
`width and height must be > 0`, so windows are floored at one pixel; and a
triangle AOI over a lake can intersect a single 1 km WorldPop cell that happens to
be nodata, which is reported as `no_population_pixels`, not as "0 people".

## Configuration

| Variable | Purpose |
| --- | --- |
| `SAGE_EE_PROJECT` | Google Cloud project id for Earth Engine |
| `SAGE_EE_SERVICE_ACCOUNT` | service-account email |
| `SAGE_EE_KEY_FILE` | path to the service-account JSON key |
| `SAGE_WORLDPOP_CACHE` | where country rasters are cached |
| `SAGE_MAP_DIR` | where HTML maps and previews are written |
| `SAGE_GEOSTACK_MAX_PIXELS` | refuse an AOI window above this many pixels (default 40 M) |

Earth Engine stays optional: `/api/geo/composite` and EE map layers return a 503 with
the variable names until all three are set, which is the intended behaviour rather
than a failure. It was configured on 2026-11-14 against project `sage-geospatial` with
service account `sage-geo@sage-geospatial.iam.gserviceaccount.com` and a key kept
outside the repository at `~/.config/sage/ee-key.json`.

### Setting it up from scratch

A service account is the only shape that works unattended; user OAuth expires.

1. Create a Cloud project and enable the Earth Engine API on it.
2. Register that project at <https://code.earthengine.google.com/register>. Enabling
the API alone is not enough — calls then fail with `PERMISSION_DENIED`.
3. Create a service account and grant it **Earth Engine Resource Viewer** and
**Service Usage Consumer**. The second one is the usual omission: without it the
account authenticates and every request still returns 403.
4. Create a JSON key and move it out of the repo (`chmod 600`). `*.ee.json`,
`ee-key*.json` and `backend/.ee/` are gitignored as a second line of defence.

Then check it end to end, without starting the API:

```bash
SAGE_EE_PROJECT=sage-geospatial \
SAGE_EE_SERVICE_ACCOUNT=sage-geo@sage-geospatial.iam.gserviceaccount.com \
SAGE_EE_KEY_FILE="$HOME/.config/sage/ee-key.json" \
  .venv/bin/python scripts/ee_check.py
```

`scripts/ee_check.py` prints the config (never the key), initialises, counts scenes
and runs a real NDVI reduction, and names the likely cause of each failure: 403 is
project registration or a missing IAM role, 401 a rejected key, 404 a wrong project
id.

## Geo Workbench (`frontend-geo/`)

A second, branch-only frontend that speaks this API directly. It is not the SAGE UI
with a new skin: the layout follows GeoLibre's working surface — full-bleed dark
map, a light cartographic basemap by default, a floating layer card, a right-hand
sectioned panel, and a status bar carrying cursor, area and valid pixels — because an
index raster is easier to read when the map is the page and the controls sit on top,
while measurements stay legible against a neutral background.

```bash
cd frontend-geo
npm install
npm run dev        # http://127.0.0.1:5174, proxies /api to :8000
```

It runs on 5174 so it can sit next to the classic frontend on 5173 against one
backend. What it can do today: draw a box or a polygon, see ellipsoid area and the
projection cross-check, run any of the five indices over a date window with SCL
masking, read the stats, histogram, scene and reflectance convention, view the PNG
preview, estimate WorldPop population, and publish a geemap HTML map. Capability
chips reflect `/api/geo/capabilities`, and error envelopes are shown by code rather
than as "HTTP 422".

Verified on 2026-11-13 through the dev proxy: `/api/geo/index` returned 200 in
25.8 s, mean NDVI 0.1723, 83,134 valid pixels, a 121,846-byte 266×324 PNG preview,
and the deterministic evidence id `6510d1b2d73c15ef`.

The basemap switch uses Esri's ArcGIS Online tiles for all three modes. CARTO's
raster basemaps began answering "API key required", so Light and Dark were moved to
Esri's grey canvases plus their label reference layers, and Satellite is the default
— all keyless (each tile URL checked for a 200 on 2026-11-14).

A **3D** toggle replaces the map with MapLibre GL: Esri satellite draped over the
AWS/Mapterhorn Terrarium DEM at 1.4× exaggeration, orbit and pitch enabled. That is
exactly what GeoLibre's 3D mode buys — real terrain relief — without its WASM/DuckDB
stack. MapLibre is a lazy chunk (1.06 MB, loaded only when 3D is opened), which is why
the initial bundle stayed at 322 kB. Drawing is disabled in 3D because the draw tools
belong to the Leaflet surface; the AOI is shown as an overlay instead.

**Land cover** runs Dynamic World through the same Earth Engine project and returns
area per class plus a structure count, so the workbench can answer "how much of this
is water, trees, built-up" rather than only how green it is. Two limits are stated in
the UI and the response: classes are modelled 10 m cover, not object outlines, and the
structure count comes from Open Buildings polygons, not from the class raster. Marking
*individual* buildings, trees or river centrelines would need a detection model, not a
land-cover product.

Limits: one AOI at a time and no raster tile layer (the preview is a single PNG). The
Earth Engine options appear once credentials exist.

### Measured with Earth Engine, 2026-11-14

Same AOI and window (2026-10-20 → 2026-11-13, max cloud 20) that the COG path ran
the day before:

| Call | Result |
| --- | --- |
| `POST /api/geo/composite` | 200 in 7.0 s wall (4.0 s server), 4 scenes, 88,224 valid px at 10 m |
| stats | mean NDVI 0.1605, median 0.0508, p10 −0.1286, p90 0.6758 |
| `POST /api/geo/map` | 200 in 2 s, 36,748-byte geemap HTML, layers AOI + NDVI composite |

The COG path gave mean 0.1724 over a window ending 2026-11-13. Comparing those two
endpoint outputs conflates scene selection with engine behaviour, so
`scripts/compare_engines.py` pins both sides to one granule (STAC `s2:product_uri`
minus `.SAFE` is EE `PRODUCT_ID`) and compares four acquisitions:

| Scene | COG mean | EE mean | delta |
| --- | --- | --- | --- |
| S2C_43QHV_20261113 | 0.1724 | 0.1726 | +0.0002 |
| S2C_44QKE_20261113 | 0.1718 | 0.1723 | +0.0005 |
| S2B_43QHV_20261108 | 0.1534 | 0.1536 | +0.0002 |
| S2B_44QKE_20261108 | 0.1524 | 0.1527 | +0.0003 |

On identical input the engines agree to within 0.0005 NDVI, so the earlier 0.17 vs
0.16 gap was the date window's scene mix, not a convention error. Pixel counts still
differ (83,134 vs 88,214) because the COG path reads the native UTM grid while Earth
Engine reprojects to a WGS84 10 m grid; that is resampling, not a bug.

### Land cover, 2026-11-14

`POST /api/geo/landcover` over the same AOI and window returned 200 in 14 s: 3
Dynamic World images, 8.30 km² total — water 56.0 %, built-up 32.9 %, trees 8.9 %,
flooded vegetation 0.9 %, crops 0.8 % — and 5,522 structures from Google Open
Buildings v3 at confidence ≥ 0.7. Publishing the map with `landcover: true` produced
a 42,653-byte HTML with AOI, NDVI and land-cover layers in 4 s.

The catalogue id is `GOOGLE/DYNAMICWORLD/V1`; the older spaced
`GOOGLE/Dynamic World-LULC/v1` now fails with asset-not-found (tested 2026-11-14),
which is why that error arrives as a 500 rather than a 503 — the credentials are
fine, the dataset name was not.

## Tests

```bash
.venv/bin/python -m pytest tests -q        # 170 pass, 32 of them geostack
```

## Starting with credentials loaded

`scripts/start.sh` reads `~/.config/sage/ee.env` (override with `SAGE_ENV_FILE`) and
exports whatever it defines before exec'ing uvicorn, so the service account does not
have to be re-typed per shell:

```bash
backend/scripts/start.sh            # host 127.0.0.1:8000 by default
PORT=8001 backend/scripts/start.sh
```

The env file is `KEY=value` with `chmod 600`, outside the repository. A missing file
is not an error: the API starts and Earth Engine endpoints report 503.

The geostack tests are offline: synthetic GeoTIFFs for the raster and WorldPop
maths, a bow-tie polygon for repair, fake STAC items for the reflectance
convention, and a 503 assertion for unconfigured Earth Engine. Nothing in the suite
needs a credential or a network.

## Limits

- No tile server: previews are PNGs of the AOI window, maps are static HTML.
- One scene at a time; compositing is Earth Engine's job.
- Index maths assumes the two bands share a CRS and grid and refuses otherwise.
- The frontend does not call these endpoints yet — they are API-only.
- Cloud masking uses SCL only; cirrus and terrain shadows are not modelled.
