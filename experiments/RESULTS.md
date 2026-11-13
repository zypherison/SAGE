# Experiment branch — testing `new-resources.md`

Branch: `experiments/new-resources`, cut from `main` at `0f7b279`. Nothing on `main`
changes: this branch adds the `experiments/` directory only, and the application in
`backend/` and `frontend/` is byte-identical to `main`.

Two questions were open after the review:

1. Do the 22 links still answer the way the review recorded? It judged several as
   403/406 and marked them unverified.
2. Is Sentinel Hub's Statistical API (#15) actually usable, and does it fix the two
   limits SAGE states openly — the short archive window and scene-level cloud masking?

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

What is still untested, and cannot be tested without an account:

- Whether its archive really reaches past ArcGIS's ~14-month window for this AOI.
- Whether SCL-based cloud masking removes the contaminated Landsat scenes that forced
  the plausibility filter in `thermal.py`.
- Whether its NDVI mean/histogram agrees with the ArcGIS numbers already recorded.
- Quota, pricing and licence terms.

### What credentials it needs

Not an account ID. The Statistical API uses OAuth2 client credentials: a **client
ID** (a UUID) and the **client secret** shown once when that client is created.
Measured on 2026-11-13, the token endpoint answers
`401 {"error":"invalid_client"}` for the supplied ID with an empty or dummy secret.
That error is identical for a non-existent client and for a real client with the
wrong secret, so the value's kind cannot be checked from outside. If it is an
**instance ID** (the UUID used by WMS/WMTS configurations), this API does not use
it at all.

To finish the comparison: create an OAuth client, then

```bash
SENTINELHUB_CLIENT_ID=... SENTINELHUB_CLIENT_SECRET=... \
  python experiments/sentinelhub.py --run --from 2025-10-01 --to 2025-11-30
```

and compare the rows with the Hussain Sagar figures in the seminar document. Passing
`--geometry <file.geojson>` sends the polygon instead of the bbox, matching the new
polygon AOI behaviour on `main`.

## Reproduce

```bash
python experiments/probe_links.py                    # ~20 requests, 1 s apart
python experiments/sentinelhub.py --dry-run          # no network
python experiments/sentinelhub.py --probe            # one request, expect 401
python -m pytest experiments -q                      # offline, 5 tests
```
