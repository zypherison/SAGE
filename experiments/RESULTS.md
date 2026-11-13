# Experiment branch — testing `new-resources.md`

Branch: `experiments/new-resources`, cut from `main` at `0f7b279`. Nothing on `main`
changes: this branch adds the `experiments/` directory only, and the application in
`backend/` and `frontend/` is byte-identical to `main`.

Two questions were open after the review:

1. Do the 22 links still answer the way the review recorded? It judged several as
   403/406 and marked them unverified.
2. Is Sentinel Hub's Statistical API (#15) actually usable, and does it fix the two
   limits SAGE states openly — the short archive window and scene-level cloud masking?

A third lead was added later, when a Planet API key became available: can a
commercial key give SAGE pixels its keyless providers cannot? That is section 3.

## 1. Link re-probe

`probe_links.py` re-requests every distinct URL from the table (20 after dropping the
bibliography anchor #7 and the paywall variant #18) from this machine, records status,
content type and final URL, and writes `link-probe.json`.

Result: **15 reachable, 4 blocked to scripts, 1 not found.**

| What changed since the review | Detail |
|---|---|
| #3, #4, #5 (Springer articles) | Now answer **200** instead of 406, but the redirect appends `?error=cookies_not_supported` — a consent/cookie stub, not the abstract. They remain **unread**. |
| #6, #8, #17 (ScienceDirect, OUP, T&F) | Still **403**. Unchanged, still unverified. |
| #9 (NASA ACCESS page) | **403** to this fetcher, though the review recorded it as readable context material. |
| #20 (Bhuvan NOEDA) | **404**, but the URL stored in `new-resources.md` is abbreviated (`/data/download/...`), so this tests the abbreviation, not the portal. |
| Everything else | Same as recorded: portals, indexes and query pages that load but provide no dataset or method. |

The probe verifies reachability, not readability. A 200 on a publisher URL with a
cookie-stub redirect is exactly the case the review warned about, so no article is
upgraded from "unverified" on the strength of this run.

## 2. Sentinel Hub Statistical API

`sentinelhub.py` builds a real Statistical API request for the Hussain Sagar box, the
same AOI the seminar document reports on. It is **not imported by the app**.

Measured on this branch:

- **Unauthenticated call (`--probe`)** returns `401` with
  `"You are not authorized! Please provide a valid access token..."` — so the endpoint
  is live, and a credential is genuinely required. No keyless fallback exists.
- **Request shape (`--dry-run`)** is built from the published examples, and the offline
  tests check it against the published response shape (5 tests, all passing).
- The docs confirm the three capabilities the review claimed:
  server-side **histograms and percentiles**, **multi-interval time ranges** in one
  request, and **`dataMask`** for per-pixel exclusion. The docs also state the mask is
  "especially important when you are requesting statistics for a polygon", because it
  is what excludes pixels inside the bbox but outside the polygon.

### Credentials: a Planet token works

Sentinel Hub now runs inside the Planet Insights Platform, and it accepts a **Planet
JWT** directly. Measured on 2026-11-13, `Authorization: Bearer <planet-jwt>` returns
`200` from both `/api/v1/statistics` and `/api/v1/process`; no OAuth exchange is
needed. The script takes it as `SENTINELHUB_TOKEN` and skips the token request.

That also settles the earlier UUID: it is the **Planet project/account**
id (the token's `pl_project` claim), not a Sentinel Hub OAuth client, which is why
it returned `401 invalid_client`. The OAuth client-ID pair still works for accounts
that have one, but it is no longer the only route. Planet tokens expire after about
two hours, so they are passed per run and never stored in the repo.

### What the account can actually do

| Probe | Result |
|---|---|
| `POST /process`, Hussain Sagar, one day | `200 image/tiff`, 143,910 bytes — real pixels, not just statistics |
| `POST /statistics`, 30-day windows | `200`, 70,560 samples per interval at `resx = 0.0001°` (~10.5 m) |
| **Archive depth** | `2017-06-01..2017-07-01` returns 70,560 samples, mean NDVI 0.213 — so the archive reaches **2017**, far past the ~14-month ArcGIS window |
| `GET /dataimport/quotas` | `403 COMMON_INSUFFICIENT_PERMISSIONS` — no BYOC on this account |

October–November 2025, Hussain Sagar bbox, NDVI at 10.5 m, `P30D`:

| Interval | Cloud mask | Mean | p50 | Samples | No-data | Share ≥ 0.3 |
|---|---|---|---|---|---|---|
| 2025-10-01..10-31 | none | −0.034 | −0.059 | 70,560 | 0 | 3.4 % |
| 2025-10-01..10-31 | SCL | 0.000 | −0.097 | 70,560 | 66,152 | 17.2 % |
| 2025-10-31..11-30 | none | 0.137 | 0.019 | 70,560 | 0 | 25.5 % |
| 2025-10-31..11-30 | SCL | 0.137 | 0.019 | 70,560 | 0 | 25.5 % |

Two things follow, both measured rather than assumed. **Cloud masking changes the
answer where it matters:** October's mosaic is 94 % cloud/shadow by SCL, and dropping
those pixels moves the mean from −0.034 to 0.000. November's mosaic was already
clear, so masking changes nothing there — a reminder that a scene-level filter and a
pixel-level mask agree only in clear months. And **the polygon clips**: sending the
half-size triangle drops 35,036 of 70,560 pixels as outside the ring, giving mean
0.109 against 0.137 for the bbox, a kept-fraction of 0.497 that matches the 0.500
ratio SAGE's ArcGIS path reports for the same triangle.

### The one trap worth knowing

A `P30D` interval shorter than 30 days returns **`200` with `data: []`** — no error,
no intervals, which reads exactly like "no imagery". Every empty window above was a
28-day window until this was found. The script now warns when the range is too short
for the interval, and `--interval P10D` works for shorter ranges.

Still unverified: whether an NDVI figure from this service agrees numerically with
the ArcGIS numbers already recorded, since the two use different grids and
statistic definitions; and quota, pricing and licence terms.

```bash
SENTINELHUB_TOKEN=<planet-jwt> \
  python experiments/sentinelhub.py --run --from 2025-10-01 --to 2025-11-30 --cloud-mask
SENTINELHUB_TOKEN=<planet-jwt> \
  python experiments/sentinelhub.py --run --from 2017-06-01 --to 2017-07-01 --cloud-mask
```

Passing `--geometry <file.geojson>` sends the polygon instead of the bbox, matching
the polygon AOI behaviour on `main`.

## 3. Planet Data API

Planet is not in `new-resources.md`; it was tested because a key was supplied. The
question was narrow: does that key reach pixels the app can compare with its open
ArcGIS numbers? `planet.py` answers it against the same Hussain Sagar AOI.

Measured on 2026-11-13:

| Probe | Result |
|---|---|
| Root endpoints | `GET /data/v1/`, `/item-types`; `POST /quick-search`, `/stats` all **200** — the key authenticates |
| Item types visible | 9: `PSScene`, `PelicanScene`, `REOrthoTile`, `REScene`, `SkySatCollect`, `SkySatScene`, `SkySatVideo`, `TanagerMethane`, `TanagerScene`. **No Sentinel-2 or Landsat**: asking for `Sentinel2L2A` or `Landsat8L1G` returns `400 "Not all item types requested exist or are accessible"` |
| PlanetScope coverage, Oct–Nov 2025 | **93 scenes** over Hussain Sagar (50 in October, 43 in November); several at **0 % cloud** |
| Archive depth | A window in **2016**, **2019** and **2023** each returns an item, so it reaches well past the ~14-month ArcGIS window |
| Pixel access | `_permissions: []`; the item's assets endpoint returns `{"assets": {}}`, and asset activation returns 404. **No analytic download** |
| Preview | The item thumbnail link (`tiles.planet.com/.../thumb`) returns **200, 62,408 bytes** — metadata and preview only |
| Basemaps | `200` with an empty `mosaics` list; `my/subscriptions` returns **403** |

So the key is a **search and metadata** key, not an imagery key. It is genuinely
useful for discovery — 93 PlanetScope scenes in two months, with cloud cover, over an
AOI where the free catalogues are thin — but it cannot produce NDVI, so it cannot be
compared with SAGE's current statistics. Reaching pixels would need an entitlement to
the `ortho_analytic_4b`/`_sr` assets, i.e. a paid Planet subscription; nothing in the
API can grant that from here. Separately, PlanetScope is ~3 m in four bands, so even
with access it is a different basis from Sentinel-2's 10 m, not a drop-in replacement.

## Reproduce

```bash
python experiments/probe_links.py                    # ~20 requests, 1 s apart
python experiments/sentinelhub.py --dry-run          # no network
python experiments/sentinelhub.py --probe            # one request, expect 401
PLANET_API_KEY=... python experiments/planet.py --stats   # monthly scene counts
PLANET_API_KEY=... python experiments/planet.py --assets PSScene/<item-id>
python -m pytest experiments -q                      # offline, 16 tests
```
