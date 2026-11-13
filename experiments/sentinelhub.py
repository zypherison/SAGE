#!/usr/bin/env python3
"""Evaluate Sentinel Hub's Statistical API against SAGE's stated limits.

new-resources.md (#15) marks this as the one real technical lead in the supplied
list: server-side statistics over a multi-year archive, with the evalscript
deciding which pixels count. That is precisely what SAGE's keyless ArcGIS path
cannot do - its archive is short and cloud masking is per-scene, not per-pixel.

This file exists to test the claim, not to wire it into the app. Nothing under
backend/ or frontend/ imports it, and main is untouched.

Request and response shapes follow the published examples:
  https://docs.sentinel-hub.com/api/latest/api/statistical/examples/

    python experiments/sentinelhub.py --dry-run          # request body, no network
    python experiments/sentinelhub.py --probe            # what answers without a token
    SENTINELHUB_CLIENT_ID=... SENTINELHUB_CLIENT_SECRET=... \
      python experiments/sentinelhub.py --run --from 2025-10-01 --to 2025-11-30

The AOI defaults to Hussain Sagar, the same box the seminar document reports on,
so a future run can be compared with the numbers already recorded.

Credentials: this API uses OAuth2 client credentials, so it needs a **client ID
plus the client secret** from an OAuth client created in Dashboard -> User
Settings -> OAuth clients. An account ID or an instance ID is not accepted by the
token endpoint and is not used here; the secret is shown once at creation and
cannot be retrieved later.

Measured on this branch: Sentinel Hub now also accepts a **Planet JWT**, because
Sentinel Hub runs inside the Planet Insights Platform. Set `SENTINELHUB_TOKEN` to
that token and the OAuth exchange is skipped entirely. Planet tokens expire after
about two hours, so they are passed per run and never stored here.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from typing import Any

STATS_URL = "https://services.sentinel-hub.com/api/v1/statistics"
TOKEN_URL = (
    "https://services.sentinel-hub.com/auth/realms/main/"
    "protocol/openid-connect/token"
)
CRS84 = "http://www.opengis.net/def/crs/OGC/1.3/CRS84"
HUSSAIN_SAGAR = [78.4623, 17.4086, 78.4868, 17.4374]

# Resolution is in the units of CRS84, i.e. degrees, not metres: asking for
# `resx: 10` means 10 degrees and the API rejects it (3203 m/px > 1500 limit).
# 0.0001 deg is ~10.5 m at 17 N, close to Sentinel-2's native 10 m grid.
RES_DEGREES = 0.0001

# Only the two indices SAGE already reports, so a result is comparable.
INDEXES = {
    "ndvi": ("B04", "B08", "(s.B08 - s.B04) / (s.B08 + s.B04 + 0.000001)"),
    "ndwi": ("B03", "B08", "(s.B03 - s.B08) / (s.B03 + s.B08 + 0.000001)"),
}

_EVALSCRIPT = """//VERSION=3
function setup() {
  return {
    input: [{ bands: [{bands}] }],
    output: [
      { id: "index", bands: 1, sampleType: "FLOAT32" },
      { id: "dataMask", bands: 1 }
    ]
  };
}
function evaluatePixel(s) {
{setup}  return { index: [{formula}], dataMask: [{mask}] };
}
"""

# Scene Classification Layer classes that are not usable ground: cloud shadow,
# cloud (medium and high probability), thin cirrus and snow. Water and built-up
# stay in, since NDVI is meaningful over them.
_SCL_BAD = (3, 8, 9, 10, 11)


def evalscript_for(index: str, *, cloud_mask: bool = False) -> str:
    """Evalscript returning the index plus a dataMask, which is mandatory here.

    With `cloud_mask`, the SCL band is read as well and cloud, shadow, cirrus and
    snow pixels are dropped by zeroing their dataMask - the per-pixel masking
    SAGE's keyless pending path cannot do.
    """
    key = index.lower()
    if key not in INDEXES:
        raise ValueError(f"unsupported index {index!r}; choose from {sorted(INDEXES)}")
    b1, b2, formula = INDEXES[key]
    bands = [b1, b2, "dataMask"] + (["SCL"] if cloud_mask else [])
    if cloud_mask:
        setup = (
            "  var mask = s.dataMask;\n  var scl = s.SCL;\n"
            + "".join(f"  if (scl === {c}) mask = 0;\n" for c in _SCL_BAD)
        )
        mask = "mask"
    else:
        setup, mask = "", "s.dataMask"
    return (
        _EVALSCRIPT.replace("{bands}", ", ".join(f'"{b}"' for b in bands))
        .replace("{setup}", setup)
        .replace("{formula}", formula)
        .replace("{mask}", mask)
    )


def request_body(
    *,
    bbox: list[float],
    start: str,
    end: str,
    index: str = "ndvi",
    max_cloud: float | None = 40.0,
    geometry: dict[str, Any] | None = None,
    n_bins: int = 10,
    cloud_mask: bool = False,
    interval: str = "P30D",
) -> dict[str, Any]:
    """One P30D-aggregated request.

    Passing `geometry` is what clips the statistics to a drawn polygon; without
    it the bbox is used, exactly as in SAGE's own services. Both are WGS84 here
    (CRS84) because SAGE's AOIs are stored in lon/lat.
    """
    bounds: dict[str, Any] = {
        "bounds": {"bbox": list(bbox), "properties": {"crs": CRS84}}
    }
    if geometry is not None:
        bounds = {"bounds": {"geometry": geometry, "properties": {"crs": CRS84}}}

    data_filter: dict[str, Any] = {"mosaickingOrder": "leastCC"}
    if max_cloud is not None:
        data_filter["maxCloudCoverage"] = max_cloud

    return {
        "input": {
            **bounds,
            "data": [{"type": "sentinel-2-l2a", "dataFilter": data_filter}],
        },
        "aggregation": {
            "timeRange": {
                "from": f"{start}T00:00:00Z",
                "to": f"{end}T23:59:59Z",
            },
            "aggregationInterval": {"of": interval},
            "evalscript": evalscript_for(index, cloud_mask=cloud_mask),
            "resx": RES_DEGREES,
            "resy": RES_DEGREES,
        },
        "calculations": {
            "default": {
                "histograms": {
                    "default": {"nBins": n_bins, "lowEdge": -1.0, "highEdge": 1.0}
                },
                "statistics": {"default": {"percentiles": {"k": [10, 50, 90]}}},
            }
        },
    }


def summarise(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten the response into one row per interval, keeping the raw histogram."""
    if payload.get("status") not in (None, "OK"):
        raise RuntimeError(f"Statistical API status={payload.get('status')}: {payload}")
    if "error" in payload:
        raise RuntimeError(f"Statistical API error: {payload['error']}")

    rows = []
    for entry in payload.get("data", []):
        band = (
            entry.get("outputs", {})
            .get("index", {})
            .get("bands", {})
            .get("B0", {})
        )
        stats = band.get("stats", {})
        histogram = band.get("histogram")
        rows.append(
            {
                "from": entry.get("interval", {}).get("from", "")[:10],
                "to": entry.get("interval", {}).get("to", "")[:10],
                "mean": stats.get("mean"),
                "min": stats.get("min"),
                "max": stats.get("max"),
                "stdev": stats.get("stDev"),
                "samples": stats.get("sampleCount"),
                "no_data": stats.get("noDataCount"),
                "percentiles": stats.get("percentiles"),
                "bins": (histogram or {}).get("bins"),
                "overflow": (histogram or {}).get("overflowCount"),
                "underflow": (histogram or {}).get("underflowCount"),
            }
        )
    return rows


def span_days(start: str, end: str) -> int:
    """Inclusive length of the requested window in days."""
    first = date.fromisoformat(start[:10])
    last = date.fromisoformat(end[:10])
    return (last - first).days + 1


def _post_json(
    url: str, body: dict[str, Any], token: str | None = None, timeout: int = 90
) -> tuple[int, dict[str, Any]]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = {"body": raw[:800].decode("utf-8", "replace")}
        return exc.code, parsed


def fetch_token(timeout: int = 30) -> str:
    """Bearer token: a supplied Planet JWT, else OAuth2 client credentials."""
    token = (os.environ.get("SENTINELHUB_TOKEN") or "").strip()
    if token:
        return token
    client_id = os.environ.get("SENTINELHUB_CLIENT_ID")
    client_secret = os.environ.get("SENTINELHUB_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise SystemExit(
            "SENTINELHUB_CLIENT_ID / SENTINELHUB_CLIENT_SECRET are not set. "
            "Create an OAuth client in the Sentinel Hub dashboard, then re-run."
        )
    body = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())["access_token"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="print the request body")
    mode.add_argument("--probe", action="store_true", help="call without credentials")
    mode.add_argument("--run", action="store_true", help="call with credentials")
    parser.add_argument("--index", default="ndvi", choices=sorted(INDEXES))
    parser.add_argument("--from", dest="start", default="2025-10-01")
    parser.add_argument("--to", dest="end", default="2025-11-30")
    parser.add_argument("--bbox", type=float, nargs=4, default=HUSSAIN_SAGAR)
    parser.add_argument(
        "--geometry",
        help="GeoJSON polygon as a file path or inline JSON; clips the statistics",
    )
    parser.add_argument("--bins", type=int, default=10)
    parser.add_argument(
        "--interval",
        default="P30D",
        help="aggregation interval (P10D, P30D, ...); it must fit the window",
    )
    parser.add_argument(
        "--cloud-mask",
        action="store_true",
        help="drop SCL cloud/shadow/cirrus/snow pixels instead of counting them",
    )
    args = parser.parse_args()

    days = span_days(args.start, args.end)
    if args.interval == "P30D" and days < 30:
        print(
            f"warning: P30D needs a window of at least 30 days; {args.start}.."
            f"{args.end} is {days}, and the API answers with no intervals at all. "
            "Use --interval P10D or widen the range.",
            file=sys.stderr,
        )

    geometry = None
    if args.geometry:
        try:
            geometry = json.loads(args.geometry)
        except ValueError:
            with open(args.geometry, encoding="utf-8") as handle:
                geometry = json.load(handle)

    body = request_body(
        bbox=list(args.bbox),
        start=args.start,
        end=args.end,
        index=args.index,
        geometry=geometry,
        n_bins=args.bins,
        cloud_mask=args.cloud_mask,
        interval=args.interval,
    )

    if args.dry_run:
        print(json.dumps(body, indent=2))
        print(
            f"\nAOI: {'polygon' if geometry else 'bbox'}  "
            f"index: {args.index}  bins: {args.bins}  "
            f"cloud mask: {'SCL' if args.cloud_mask else 'none'}",
            flush=True,
        )
        return 0

    if args.probe:
        status, payload = _post_json(STATS_URL, body)
        message = payload.get("error", {}).get("message") or payload.get("message")
        print(json.dumps({"status": status, "message": message}, indent=2))
        return 0

    status, payload = _post_json(STATS_URL, body, token=fetch_token())
    if status >= 400:
        print(json.dumps({"status": status, "payload": payload}, indent=2))
        return 2
    rows = summarise(payload)
    print(json.dumps(rows, indent=2))
    print(f"\n{len(rows)} intervals", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
