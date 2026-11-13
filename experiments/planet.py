#!/usr/bin/env python3
"""Probe what a Planet Data API key can reach for SAGE's Hussain Sagar AOI.

Planet is the one commercial provider in new-resources.md, so the question is
narrow: does a key we can actually get hold of give access to pixels the app can
compare with its keyless ArcGIS path? This script answers that by measuring,
not by reading the sales page.

    PLANET_API_KEY=... python experiments/planet.py --stats
    PLANET_API_KEY=... python experiments/planet.py --list --max-cloud 10
    PLANET_API_KEY=... python experiments/planet.py --assets PSScene/<item-id>
    python experiments/planet.py --help

Nothing under backend/ or frontend/ imports this; the branch adds files only.
Standard library only, like the rest of experiments/.

Auth is HTTP Basic with the API key as the username and an empty password, which
is what the Data API expects.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

API = "https://api.planet.com/data/v1"
HUSSAIN_SAGAR = [78.4623, 17.4086, 78.4868, 17.4374]
DEFAULT_ITEM_TYPE = "PSScene"


def bbox_polygon(bbox: list[float]) -> dict[str, Any]:
    """A closed GeoJSON ring for the bbox, so the filter is a real polygon."""
    west, south, east, north = bbox
    return {
        "type": "Polygon",
        "coordinates": [
            [[west, south], [east, south], [east, north], [west, north], [west, south]]
        ],
    }


def _iso(day: str, *, end: bool = False) -> str:
    """Accept `YYYY-MM-DD` or a full timestamp and return one Planet accepts."""
    if "T" in day:
        return day
    return f"{day}T{'23:59:59' if end else '00:00:00'}Z"


def _filter(geometry: dict[str, Any], start: str, end: str) -> dict[str, Any]:
    return {
        "type": "AndFilter",
        "config": [
            {"type": "GeometryFilter", "field_name": "geometry", "config": geometry},
            {
                "type": "DateRangeFilter",
                "field_name": "acquired",
                "config": {"gte": _iso(start), "lte": _iso(end, end=True)},
            },
        ],
    }


def search_body(
    *,
    item_types: list[str] | tuple[str, ...],
    geometry: dict[str, Any],
    start: str,
    end: str,
    limit: int = 10,
) -> dict[str, Any]:
    """Body for POST /quick-search."""
    return {
        "item_types": list(item_types),
        "filter": _filter(geometry, start, end),
        "limit": int(limit),
    }


def stats_body(
    *,
    item_types: list[str] | tuple[str, ...],
    geometry: dict[str, Any],
    start: str,
    end: str,
    interval: str = "month",
) -> dict[str, Any]:
    """Body for POST /stats, which returns per-bucket item counts."""
    return {
        "item_types": list(item_types),
        "filter": _filter(geometry, start, end),
        "interval": interval,
    }


def summarise_monthly(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten the /stats buckets into month/count rows."""
    rows = []
    for bucket in payload.get("buckets") or []:
        start = str(bucket.get("start_time") or "")
        rows.append({"month": start[:7], "count": int(bucket.get("count") or 0)})
    return rows


def best_scenes(
    features: list[dict[str, Any]],
    *,
    limit: int = 10,
    max_cloud: float | None = None,
) -> list[dict[str, Any]]:
    """Compact scene rows, least cloudy first, optionally filtered by cloud."""
    rows = []
    for feature in features:
        props = feature.get("properties") or {}
        cloud = props.get("cloud_cover")
        if max_cloud is not None and cloud is not None and cloud > max_cloud:
            continue
        rows.append(
            {
                "id": feature.get("id"),
                "item_type": props.get("item_type"),
                "acquired": str(props.get("acquired") or "")[:10],
                "cloud_cover": cloud,
                "thumbnail": (feature.get("_links") or {}).get("thumbnail"),
            }
        )
    rows.sort(key=lambda r: (r["cloud_cover"] is None, r["cloud_cover"] or 0.0))
    return rows[:limit]


def _request(key: str, method: str, url: str, body: Any = None) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    token = base64.b64encode(f"{key}:".encode()).decode()
    request.add_header("Authorization", f"Basic {token}")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")[:300]
        raise RuntimeError(f"{method} {url} -> HTTP {error.code}: {detail}") from None


def stats(key: str, **kwargs: Any) -> list[dict[str, Any]]:
    return summarise_monthly(_request(key, "POST", f"{API}/stats", stats_body(**kwargs)))


def search(key: str, **kwargs: Any) -> list[dict[str, Any]]:
    payload = _request(key, "POST", f"{API}/quick-search", search_body(**kwargs))
    return payload.get("features") or []


def item_assets(key: str, item_type: str, item_id: str) -> dict[str, Any]:
    """Asset list plus `_permissions`, which is what download rights hang on."""
    return _request(key, "GET", f"{API}/item-types/{item_type}/items/{item_id}/assets/")


def item_types(key: str) -> list[str]:
    payload = _request(key, "GET", f"{API}/item-types?count=100")
    return sorted(t["id"] for t in payload.get("item_types") or [])


def _geometry(path: str | None) -> dict[str, Any]:
    if not path:
        return bbox_polygon(HUSSAIN_SAGAR)
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if data.get("type") == "Feature":
        return data["geometry"]
    if data.get("type") != "Polygon":
        raise SystemExit(f"{path} is not a GeoJSON Polygon or Feature")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="start", default="2025-10-01")
    parser.add_argument("--to", dest="end", default="2025-11-30")
    parser.add_argument("--item-type", action="append", default=None)
    parser.add_argument("--geometry", help="GeoJSON Polygon/Feature file")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--max-cloud", type=float, default=None)
    parser.add_argument("--json", dest="out", help="write the result to this file")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--stats", action="store_true", help="monthly item counts")
    group.add_argument("--list", action="store_true", help="best scenes as rows")
    group.add_argument("--types", action="store_true", help="item types this key sees")
    group.add_argument("--assets", metavar="ITEM_TYPE/ITEM_ID")
    args = parser.parse_args(argv)

    key = os.environ.get("PLANET_API_KEY", "").strip()
    if not key:
        print(
            "PLANET_API_KEY is not set. Put the key in a local env file outside the\n"
            "repo and source it, then re-run.",
            file=sys.stderr,
        )
        return 2

    types = args.item_type or [DEFAULT_ITEM_TYPE]
    geometry = _geometry(args.geometry)
    result: Any

    try:
        if args.types:
            result = item_types(key)
            print("\n".join(result))
        elif args.assets:
            item_type, _, item_id = args.assets.partition("/")
            if not item_id:
                raise SystemExit("--assets wants ITEM_TYPE/ITEM_ID, e.g. PSScene/20251130_...")
            result = item_assets(key, item_type, item_id)
            print(json.dumps(result, indent=2))
        elif args.list:
            scenes = search(
                key,
                item_types=types,
                geometry=geometry,
                start=args.start,
                end=args.end,
                limit=args.limit,
            )
            result = best_scenes(scenes, limit=args.limit, max_cloud=args.max_cloud)
            print(f"{len(scenes)} item(s) returned for {', '.join(types)}")
            for row in result:
                print(
                    f"  {row['id']}  {row['acquired']}  cloud {row['cloud_cover']}"
                )
        else:
            result = stats(
                key,
                item_types=types,
                geometry=geometry,
                start=args.start,
                end=args.end,
            )
            total = sum(row["count"] for row in result)
            print(f"{args.start}..{args.end}  {', '.join(types)}  total {total}")
            for row in result:
                print(f"  {row['month']}  {row['count']}")
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 1

    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2)
            handle.write("\n")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
