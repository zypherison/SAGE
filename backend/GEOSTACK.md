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
| POST | `/api/geo/map` | writes an interactive geemap HTML map, optionally with an EE layer |
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

Earth Engine is **not** configured yet; `/api/geo/composite` and EE map layers stay
disabled until the three variables are set, which is the intended behaviour rather
than a failure.

## Geo Workbench (`frontend-geo/`)

A second, branch-only frontend that speaks this API directly. It is not the SAGE UI
with a new skin: the layout follows GeoLibre's working surface — full-bleed dark
map, a floating layer card, a right-hand tool dock, and a status bar carrying
cursor, area, valid pixels and scene — because an index raster is easier to read
when the map is the page and the controls are on top of it.

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

Limits: one AOI at a time, no tile layer (the preview is a single PNG), and the
Earth Engine layer appears only once credentials exist.

## Tests

```bash
.venv/bin/python -m pytest tests -q        # 169 pass, 32 of them geostack
```

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
