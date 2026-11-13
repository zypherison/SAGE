# experiments/

Scratch work that stays off `main` until it earns its place.

| File | Purpose |
|---|---|
| `probe_links.py` | Re-requests every link in `new-resources.md`, writes `link-probe.json` |
| `sentinelhub.py` | Builds (and optionally runs) a Sentinel Hub Statistical API request |
| `planet.py` | Probes what a Planet Data API key can search and download |
| `test_sentinelhub.py` | Offline tests for the request/response shape |
| `test_planet.py` | Offline tests for the search body and the summaries |
| `RESULTS.md` | What the two experiments actually measured |
| `link-probe.json` | Raw probe output, committed as evidence |

Nothing here is imported by `backend/` or `frontend/`; the branch adds files only.
Both scripts use the standard library, so any Python 3.10+ works — no extra install.

```bash
python experiments/probe_links.py
python experiments/sentinelhub.py --dry-run
python experiments/sentinelhub.py --probe
python experiments/sentinelhub.py --run --cloud-mask --from 2017-06-01 --to 2017-07-01
python experiments/planet.py --stats             # needs PLANET_API_KEY
python experiments/planet.py --list --max-cloud 10
python -m pytest experiments -q
```

`--run` needs a bearer token: either a **Planet JWT** in `SENTINELHUB_TOKEN`
(accepted since Sentinel Hub moved into the Planet Insights Platform, valid ~2 h) or
an OAuth client pair `SENTINELHUB_CLIENT_ID` / `SENTINELHUB_CLIENT_SECRET`. Account
ids are not credentials. Add `--cloud-mask` for SCL masking and `--interval P10D` for
windows shorter than 30 days — a `P30D` request over a shorter range returns `200`
with no intervals. Read `RESULTS.md` for what was measured.

`planet.py` takes `PLANET_API_KEY` the same way: keep the key in a local env file
outside the repo and source it for the run. The key is not stored here.
