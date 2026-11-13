# experiments/

Scratch work that stays off `main` until it earns its place.

| File | Purpose |
|---|---|
| `probe_links.py` | Re-requests every link in `new-resources.md`, writes `link-probe.json` |
| `sentinelhub.py` | Builds (and optionally runs) a Sentinel Hub Statistical API request |
| `test_sentinelhub.py` | Offline tests for the request/response shape |
| `RESULTS.md` | What the two experiments actually measured |
| `link-probe.json` | Raw probe output, committed as evidence |

Nothing here is imported by `backend/` or `frontend/`; the branch adds files only.
Both scripts use the standard library, so any Python 3.10+ works — no extra install.

```bash
python experiments/probe_links.py
python experiments/sentinelhub.py --dry-run
python experiments/sentinelhub.py --probe
python -m pytest experiments -q
```

The Sentinel Hub run needs `SENTINELHUB_CLIENT_ID` and `SENTINELHUB_CLIENT_SECRET`;
without them `--run` stops with a message instead of guessing. Read `RESULTS.md` for
what has and has not been verified.
