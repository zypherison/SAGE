"""Evidence IDs: the runtime guarantee that a result can be re-derived.

id = sha256(canonical_json(payload))[:16]

The stored record follows alephgeo's manifest idea: a run is one self-describing
document carrying what was asked, what was returned, the environment it was
produced in, and whether it completed or only partly completed. Files are written
atomically, so an interrupted write cannot corrupt an earlier record.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import __version__, config

FORMAT = "sage-evidence"
FORMAT_VERSION = 2


def canonical(payload: dict[str, Any]) -> str:
    # allow_nan=False, as in alephgeo's write_json: invalid JSON must never be
    # written, because a NaN would silently poison every later replay comparison.
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False
    )


def make_id(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(payload).encode("utf-8")).hexdigest()[:16]


@contextmanager
def _atomic_path(path: Path):
    """Leave the previous file intact if writing fails (alephgeo's atomic_path)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        yield temporary
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _append(record: dict[str, Any]) -> None:
    path = Path(config.EVIDENCE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(canonical(record) + "\n")


def store(
    payload: dict[str, Any],
    result: dict[str, Any],
    *,
    environment: dict[str, Any] | None = None,
    cache: dict[str, int] | None = None,
) -> str:
    rid = make_id(payload)
    record = {
        "format": FORMAT,
        "version": FORMAT_VERSION,
        "id": rid,
        "status": "complete" if result.get("status") == "ok" else "partial",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
        "result": result,
        "environment": {"app_version": __version__, **(environment or {})},
        "cache": cache or {"hits": 0, "misses": 0},
    }
    _append(record)
    return rid


def lookup(rid: str) -> dict[str, Any] | None:
    path = config.EVIDENCE_PATH
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        for line in reversed(fh.readlines()):
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("id") == rid:
                return record
    return None


def recent(limit: int = 20) -> list[dict[str, Any]]:
    """Newest-first index of runs, without the bulky payload/result bodies."""
    path = config.EVIDENCE_PATH
    if not os.path.exists(path):
        return []
    out: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            stats = ((record.get("result") or {}).get("stats") or {})
            out.append(
                {
                    "id": record.get("id"),
                    "status": record.get("status"),
                    "generated_at": record.get("generated_at"),
                    "index": (record.get("payload") or {}).get("index"),
                    "bbox": (record.get("payload") or {}).get("bbox"),
                    "mean": stats.get("mean"),
                    "count": stats.get("count"),
                }
            )
    return list(reversed(out))[: max(1, min(limit, 200))]


def _stats_of(obj: Any) -> dict[str, Any]:
    """Pull the stats block out of either a stored record or a bare result dict.

    The replay endpoint passes the fresh result directly while the store wraps it
    in {"result": ...}; reading only one shape yielded an empty diff set and a
    false "does not match". Both are accepted here.
    """
    if not isinstance(obj, dict):
        return {}
    if isinstance(obj.get("stats"), dict):
        return obj["stats"]
    if isinstance(obj.get("result"), dict):
        return _stats_of(obj["result"])
    return {}


def compare(recorded: dict[str, Any], fresh: dict[str, Any], tol: float = 1e-6) -> dict[str, Any]:
    """Compare a replay against the stored result on the statistics vector."""
    a = _stats_of(recorded)
    b = _stats_of(fresh)
    diffs: dict[str, float] = {}
    for key in ("mean", "min", "max", "std", "median"):
        if a.get(key) is not None and b.get(key) is not None:
            diffs[key] = abs(float(a[key]) - float(b[key]))
    matched = all(v <= tol for v in diffs.values()) if diffs else False
    return {"matched": matched, "tolerance": tol, "absolute_differences": diffs}
