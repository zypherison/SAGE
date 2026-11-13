#!/usr/bin/env python3
"""Probe every link recorded in new-resources.md and write down what answered.

The review in new-resources.md judged those links by reading them at the time.
This script re-checks that judgement from the machine that runs SAGE: a link
that loaded in a browser may still refuse a scripted fetch (403/406), and that
difference matters when deciding whether anything there is usable.

Experiment branch only: it reads new-resources.md and writes
experiments/link-probe.json. Nothing under backend/ or frontend/ is touched.

    python experiments/probe_links.py
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SOURCE = ROOT / "new-resources.md"
OUT = HERE / "link-probe.json"

UA = "SAGE-link-probe/0.1 (evaluation; one request per link)"
ROW = re.compile(r"^\|\s*(\d+)\s*\|\s*`([^`]+)`", re.M)


def normalise(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("10."):
        return f"https://doi.org/{raw}"
    if not raw.startswith(("http://", "https://")):
        return f"https://{raw}"
    return raw


def load_links() -> list[dict]:
    """Read the table rows; skip anchors and exact repeats of an earlier URL."""
    text = SOURCE.read_text(encoding="utf-8")
    seen: set[str] = set()
    links: list[dict] = []
    for number, raw in ROW.findall(text):
        url = normalise(raw)
        key = url.split("#")[0].rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        links.append({"n": int(number), "listed_as": raw, "url": url})
    return links


def probe(url: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
        },
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read(2048)
            result = {
                "status": response.status,
                "final_url": response.geturl(),
                "content_type": response.headers.get("content-type", ""),
                "bytes_read": len(body),
            }
    except urllib.error.HTTPError as exc:
        result = {
            "status": exc.code,
            "final_url": exc.geturl(),
            "content_type": exc.headers.get("content-type", "") if exc.headers else "",
            "bytes_read": 0,
        }
    except Exception as exc:  # DNS, TLS, timeout: record the type, do not hide it
        result = {
            "status": None,
            "final_url": None,
            "content_type": "",
            "bytes_read": 0,
            "error": f"{type(exc).__name__}: {exc}",
        }
    result["seconds"] = round(time.monotonic() - started, 2)
    return result


def verdict(result: dict) -> str:
    code = result["status"]
    if code is None:
        return "unreachable"
    if 200 <= code < 300:
        return "reachable"
    if code == 401:
        return "needs login"
    if code == 403:
        return "blocked to scripts"
    if code == 406:
        return "refuses scripted fetch"
    if code == 404:
        return "not found"
    return f"http {code}"


def main() -> int:
    links = load_links()
    if not links:
        print("No links parsed from new-resources.md", flush=True)
        return 1

    rows = []
    for link in links:
        result = probe(link["url"])
        row = {**link, **result, "verdict": verdict(result)}
        rows.append(row)
        print(
            f"{link['n']:>2}  {str(result['status'] or 'ERR'):>3}  "
            f"{row['verdict']:<24} {link['url'][:78]}",
            flush=True,
        )
        time.sleep(1.0)  # be a polite guest on 22 unrelated hosts

    OUT.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    print("\n" + ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())))
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
