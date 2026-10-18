"""Multi-provider discovery: plan, fan-out, canonicalise, dedupe, rank.

Key behaviours:
  * A provider failure NEVER becomes an empty result set. It is reported in
    `providers[]` with an error string and code, because "0 scenes" and
    "provider down" look identical to a user and that is a correctness bug.
  * Dedup uses mission + time window + bbox IoU, not id, because the same
    acquisition has different ids in different catalogues.
  * `plan()` reports the cost before anything is executed (alephgeo's
    describe-before-download discipline).
"""
from __future__ import annotations

import asyncio
import math
import time
from datetime import date, datetime, timezone
from typing import Any

import httpx

from app import config
from app.errors import AppError
from app.schemas import CacheStats, Plan, ProviderStatus, Scene, SearchResponse
from app.services.geo import bbox_iou
from app.services.net import Fetch
from app.services.providers import arcgis, earth_search, planetary

PROVIDERS = {
    earth_search.NAME: earth_search,
    planetary.NAME: planetary,
    arcgis.NAME: arcgis,
}


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _target_datetime(date_from: str | None, date_to: str | None) -> datetime | None:
    """Midpoint of the requested window, or now if no window."""
    try:
        if date_from and date_to:
            a = datetime.fromisoformat(date_from).replace(tzinfo=timezone.utc)
            b = datetime.fromisoformat(date_to).replace(tzinfo=timezone.utc)
            return a + (b - a) / 2
        if date_from:
            return datetime.fromisoformat(date_from).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return datetime.now(timezone.utc)


def plan(providers: list[str] | None = None, *, note: str | None = None) -> Plan:
    """What a fan-out will cost, without touching the network."""
    selected = [p for p in (providers or list(PROVIDERS)) if p in PROVIDERS]
    seconds = sum(config.ESTIMATED_REQUEST_SECONDS.get(p, 0.7) for p in selected)
    notes = [
        "One catalogue request per provider, issued in parallel.",
        "Index statistics are a separate request, made only when you press Analyze.",
    ]
    if note:
        notes.append(note)
    return Plan(
        providers=selected,
        requests=len(selected),
        estimated_seconds=round(seconds, 2),
        notes=notes,
    )


def dedupe(scenes: list[Scene]) -> list[Scene]:
    """Merge the same acquisition reported by multiple catalogues."""
    kept: list[Scene] = []
    for scene in scenes:
        duplicate = False
        for existing in kept:
            if existing.mission != scene.mission:
                continue
            t1, t2 = _parse_dt(existing.datetime), _parse_dt(scene.datetime)
            if t1 and t2 and abs((t1 - t2).total_seconds()) <= 86400:
                same_place = True
                if existing.bbox and scene.bbox:
                    same_place = bbox_iou(existing.bbox, scene.bbox) >= 0.90
                if same_place:
                    if (scene.index_support, len(scene.assets)) > (
                        existing.index_support, len(existing.assets)
                    ):
                        existing.__dict__.update(scene.__dict__)
                    duplicate = True
                    break
        if not duplicate:
            kept.append(scene)
    return kept


def rank(scenes: list[Scene], date_from: str | None, date_to: str | None) -> list[Scene]:
    target = _target_datetime(date_from, date_to)
    w = config.RANK_WEIGHTS
    for scene in scenes:
        clear = 1.0 - (min(max(scene.cloud_cover or 100.0, 0.0), 100.0) / 100.0)
        temporal = 0.5
        ts = _parse_dt(scene.datetime)
        if ts and target:
            days = abs((ts - target).total_seconds()) / 86400.0
            temporal = math.exp(-days / config.TEMPORAL_TAU_DAYS)
        index = 1.0 if scene.index_support else 0.0
        resolution = 0.0
        if scene.resolution_m:
            resolution = max(0.0, 1.0 - min(scene.resolution_m, 120.0) / 120.0)
        parts = {
            "clear": round(clear, 4),
            "temporal": round(temporal, 4),
            "index": index,
            "resolution": round(resolution, 4),
        }
        scene.score_parts = parts
        scene.score = round(sum(parts[k] * w[k] for k in w), 4)
    return sorted(scenes, key=lambda s: s.score or 0.0, reverse=True)


async def search(
    *,
    bbox: list[float] | None,
    geometry: dict[str, Any] | None,
    date_from: str | None,
    date_to: str | None,
    max_cloud: float | None,
    mission: str | None,
    providers: list[str] | None,
    limit: int,
) -> SearchResponse:
    selected = [p for p in (providers or list(PROVIDERS)) if p in PROVIDERS]
    warnings: list[str] = []
    run_plan = plan(selected)

    async with httpx.AsyncClient(headers={"User-Agent": config.USER_AGENT}) as client:
        fetch = Fetch(client)

        async def run(name: str) -> tuple[str, list[Scene] | Exception, int]:
            started = time.perf_counter()
            module = PROVIDERS[name]
            try:
                result = await module.search(
                    fetch,
                    bbox=bbox,
                    geometry=geometry,
                    date_from=date_from,
                    date_to=date_to,
                    max_cloud=max_cloud,
                    mission=mission,
                    limit=limit,
                )
                return name, result, int((time.perf_counter() - started) * 1000)
            except Exception as exc:  # surfaced, never swallowed
                return name, exc, int((time.perf_counter() - started) * 1000)

        results = await asyncio.gather(*(run(n) for n in selected))
        stats = fetch.stats

    statuses: list[ProviderStatus] = []
    merged: list[Scene] = []
    for name, payload, elapsed in results:
        if isinstance(payload, Exception):
            code = payload.code if isinstance(payload, AppError) else "request_failed"
            statuses.append(
                ProviderStatus(
                    name=name, ok=False, error=str(payload)[:300],
                    error_code=code, elapsed_ms=elapsed,
                )
            )
            warnings.append(f"{name} failed [{code}]: {str(payload)[:160]}")
        else:
            statuses.append(
                ProviderStatus(name=name, ok=True, count=len(payload), elapsed_ms=elapsed)
            )
            merged.extend(payload)

    # The ArcGIS window is ~14 months; if a historical range returned nothing
    # there, say so instead of implying the imagery does not exist.
    arcgis_status = next((s for s in statuses if s.name == arcgis.NAME), None)
    if arcgis_status and arcgis_status.ok and arcgis_status.count == 0 and date_to:
        try:
            if date.fromisoformat(date_to) < date.today().replace(year=date.today().year - 1):
                warnings.append(
                    "ArcGIS Sentinel-2 holds only a rolling ~14-month window, so it "
                    "cannot return scenes this far back by design. Historical results "
                    "come from the STAC catalogues only."
                )
        except ValueError:
            pass

    unique = dedupe(merged)
    ranked = rank(unique[: max(limit * 3, 60)], date_from, date_to)[:limit]

    return SearchResponse(
        count=len(ranked),
        scenes=ranked,
        providers=statuses,
        warnings=warnings,
        plan=run_plan,
        cache=CacheStats(**stats.as_dict()),
    )
