"""SAGE API.

Numbers come from deterministic services; the parser is rule-based; nothing in
this process calls a language model. See SEMINAR2.md §3.5 (D4).

Failure shape follows alephgeo's contract:
    {"status": "error", "error": {"code": ..., "message": ..., **details}}
so a client can branch on `code` — notably `ambiguous_place`, which carries the
candidates that must be chosen between.
"""
from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from starlette.requests import Request

from app import __version__, config
from app.errors import (
    EVIDENCE_NOT_FOUND,
    INVALID_ARGUMENTS,
    PLACE_NOT_FOUND,
    AppError,
)
from app.schemas import (
    CacheStats,
    CompareRequest,
    DownloadRequest,
    IndexRequest,
    IndexResponse,
    Intent,
    PlanRequest,
    PlanResponse,
    Place,
    ProvenanceResult,
    QueryRequest,
    QueryResponse,
    SearchRequest,
    SearchResponse,
    SeasonalRequest,
    ThermalRequest,
    TimeseriesRequest,
)
from app.services import (
    compare,
    discovery,
    download,
    evidence,
    geocode,
    indices,
    locality,
    provenance,
    seasons,
    thermal,
    timeseries,
)
from app.services.geo import (
    bbox_area_km2,
    bbox_span_km,
    geometry_bbox,
    normalize_aoi,
    validate_bbox,
)
from app.services.net import Fetch
from app.services.query_parser import parse

app = FastAPI(
    title="SAGE",
    version=__version__,
    description="Satellite AOI search and deterministic index analysis",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # local prototype only; tighten before any deployment
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(AppError)
async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content=exc.envelope())


@asynccontextmanager
async def connection():
    """One pooled, paced, cached HTTP session per request."""
    async with httpx.AsyncClient(headers={"User-Agent": config.USER_AGENT}) as client:
        yield Fetch(client)


def _citation(accessed: datetime) -> str:
    """A copy-ready reference line for a printed figure (§8.1)."""
    return (
        "Sentinel-2 L2A (10 m), ArcGIS Sentinel-2 ImageServer, rolling ~14-month "
        f"mosaic; accessed {accessed.date().isoformat()}. "
        "Contains modified Copernicus Sentinel data, processed by Esri."
    )


def _resolve_aoi(
    bbox: list[float] | None, geometry: dict[str, Any] | None
) -> tuple[list[float], dict[str, Any] | None]:
    """Return (bbox, simplified polygon).

    The bbox still drives the request envelope, imagery and catalogues; the
    polygon is what the statistics service clips to. Both are returned so an
    endpoint can pass the shape through instead of silently squaring it off.
    """
    try:
        if geometry is not None:
            bb, geom, _area, _centre = normalize_aoi(geometry=geometry)
            return bb, geom
        if bbox:
            return validate_bbox(bbox), None
    except ValueError as exc:
        raise AppError(INVALID_ARGUMENTS, str(exc)) from exc
    raise AppError(INVALID_ARGUMENTS, "Provide bbox or geometry")


def _resolve_bbox(bbox: list[float] | None, geometry: dict[str, Any] | None) -> list[float]:
    try:
        if geometry is not None:
            return validate_bbox(geometry_bbox(geometry))
        if bbox:
            return validate_bbox(bbox)
    except ValueError as exc:
        raise AppError(INVALID_ARGUMENTS, str(exc)) from exc
    raise AppError(INVALID_ARGUMENTS, "Provide bbox or geometry")


class GeocodeRequest(BaseModel):
    query: str
    limit: int = 8


# --------------------------------------------------------------------------
# metadata
# --------------------------------------------------------------------------
@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "version": __version__,
        "providers": list(discovery.PROVIDERS),
        "index_support": [k for k, v in config.INDEX_DEFS.items() if v.get("arcgis_function")],
    }


@app.get("/api/meta")
async def meta() -> dict[str, Any]:
    """Everything the UI needs to render controls and explain the ranking."""
    return {
        "version": __version__,
        "indices": {
            k: {
                "label": v["label"],
                "description": v["description"],
                "formula": v["formula"],
                "bands": v["bands"],
                "supported": bool(v.get("arcgis_function")),
            }
            for k, v in config.INDEX_DEFS.items()
        },
        "providers": [
            {"name": discovery.earth_search.NAME, "indices": False,
             "note": "STAC discovery, free"},
            {"name": discovery.planetary.NAME, "indices": False,
             "note": "STAC discovery, asset tokens required"},
            {"name": discovery.arcgis.NAME, "indices": True,
             "note": "rolling ~14-month window"},
        ],
        "rank_weights": config.RANK_WEIGHTS,
        "area_classes": {
            k: [
                {"name": c["name"], "label": c["label"], "threshold": c["threshold"],
                 "above": c["above"], "basis": c["basis"]}
                for c in v
            ]
            for k, v in config.AREA_CLASSES.items()
        },
        "thermal": {
            "variable": "brightness_temperature",
            "unit": "°C",
            "note": "At-sensor brightness temperature (Landsat 8/9 TIRS), not land surface "
                    "temperature: no emissivity or atmospheric correction.",
            "years": config.THERMAL_MAX_YEARS,
            "scenes_per_year": config.THERMAL_MAX_SCENES,
            "plausible_range_c": list(config.THERMAL_PLAUSIBLE_C),
        },
        "limits": {
            "max_stats_span_km": config.MAX_STATS_SPAN_KM,
            "locality_buffer_km": config.LOCALITY_BUFFER_KM,
            "max_aoi_km2": config.MAX_AOI_AREA_KM2,
            "min_aoi_km2": config.MIN_AOI_KM2,
            "min_valid_pixel_fraction": config.MIN_VALID_PIXEL_FRACTION,
            "max_limit": config.MAX_LIMIT,
        },
        "network": {
            "request_delay_seconds": config.REQUEST_DELAY,
            "retries": config.RETRIES,
            "cache_ttl_seconds": config.CACHE_TTL,
        },
    }


# --------------------------------------------------------------------------
# places
# --------------------------------------------------------------------------
@app.post("/api/geocode")
async def geocode_endpoint(req: GeocodeRequest) -> dict[str, Any]:
    async with connection() as fetch:
        candidates = await geocode.geocode(fetch, req.query, limit=req.limit)
        cache = fetch.stats.as_dict()
    if not candidates:
        raise AppError(PLACE_NOT_FOUND, f"No place found for '{req.query}'")
    return {"count": len(candidates), "candidates": candidates, "cache": cache}


@app.post("/api/parse")
async def parse_endpoint(req: QueryRequest) -> dict[str, Any]:
    """Parser only — exposes the interpretation so the user can correct it."""
    intent = parse(req.text)
    return {"intent": intent.to_dict(), "notes": intent.notes}


# --------------------------------------------------------------------------
# plan (no provider fan-out; alephgeo's describe-before-download)
# --------------------------------------------------------------------------
@app.post("/api/plan", response_model=PlanResponse)
async def plan_endpoint(req: PlanRequest) -> PlanResponse:
    warnings: list[str] = []
    candidates: list[dict[str, Any]] = []
    intent_model: Intent | None = None
    bbox: list[float] | None = None

    if req.text and req.text.strip():
        intent = parse(req.text)
        intent_model = Intent(**intent.to_dict())
        warnings.extend(intent.notes)
        if intent.place:
            async with connection() as fetch:
                candidates = await geocode.geocode(fetch, intent.place, limit=8)
            if not candidates:
                warnings.append(f"Place '{intent.place}' was not found; draw the AOI instead.")
            elif len(candidates) > 1:
                warnings.append(
                    f"{len(candidates)} places match '{intent.place}'; the run will ask you to choose."
                )
            else:
                try:
                    bbox, fit_notes = locality.fit(candidates[0])
                    warnings.extend(fit_notes)
                except ValueError:
                    warnings.append(
                        f"'{candidates[0]['label']}' has no usable geometry; draw the AOI instead."
                    )
        else:
            warnings.append("No place name found in the query; draw the AOI instead.")
    elif req.bbox or req.geometry:
        bbox = _resolve_bbox(req.bbox, req.geometry)
    else:
        raise AppError(INVALID_ARGUMENTS, "Provide text, bbox, or geometry")

    area = bbox_area_km2(bbox) if bbox else None
    span = bbox_span_km(bbox) if bbox else None
    if span is not None and span > config.MAX_STATS_SPAN_KM:
        warnings.append(
            f"AOI is {span:,.0f} km across; index statistics are refused above "
            f"{config.MAX_STATS_SPAN_KM:,.0f} km per side, so search will run but "
            "Analyze will not."
        )

    return PlanResponse(
        intent=intent_model,
        bbox=bbox,
        area_km2=round(area, 1) if area else None,
        candidates=[Place(**c) for c in candidates],
        plan=discovery.plan(req.providers),
        warnings=warnings,
    )


# --------------------------------------------------------------------------
# query and search
# --------------------------------------------------------------------------
@app.post("/api/query", response_model=QueryResponse)
async def query_endpoint(req: QueryRequest) -> QueryResponse:
    intent = parse(req.text)

    if not intent.place:
        raise AppError(
            INVALID_ARGUMENTS,
            "No place found in the query. Type a place name or draw the AOI on the map.",
        )

    async with connection() as fetch:
        candidates = await geocode.geocode(fetch, intent.place, limit=8)
        # choose() refuses ambiguity rather than guessing — see geocode.choose
        chosen = geocode.choose(
            candidates, place_id=req.place_id, best_match=req.best_match
        )
        cache = fetch.stats.as_dict()

    # A named feature may be a point (a peak, a monument) or a line (a river),
    # so it is fitted rather than rejected for lacking an area bbox.
    try:
        bbox, fit_notes = locality.fit(chosen)
    except ValueError as exc:
        raise AppError(PLACE_NOT_FOUND, str(exc)) from exc

    intent.notes.append(f"AOI from {chosen['source']}: {chosen['label']} ({chosen['id']})")
    intent.notes.extend(fit_notes)
    parsed_intent = Intent(**intent.to_dict())
    run_plan = discovery.plan()

    if not req.run_search:
        return QueryResponse(
            intent=parsed_intent,
            bbox=bbox,
            place=Place(**chosen),
            candidates=[Place(**c) for c in candidates],
            plan=run_plan,
        )

    search = await discovery.search(
        bbox=bbox,
        geometry=None,
        date_from=intent.date_from,
        date_to=intent.date_to,
        max_cloud=intent.max_cloud,
        mission=intent.mission,
        providers=None,
        limit=req.limit,
    )
    search.warnings.extend(intent.notes)
    search.plan = search.plan or run_plan
    if cache:
        search.cache = CacheStats(**cache)

    return QueryResponse(
        intent=parsed_intent,
        bbox=bbox,
        place=Place(**chosen),
        candidates=[Place(**c) for c in candidates],
        plan=run_plan,
        search=search,
    )


@app.post("/api/search", response_model=SearchResponse)
async def search_endpoint(req: SearchRequest) -> SearchResponse:
    bbox = _resolve_bbox(req.bbox, req.geometry)
    return await discovery.search(
        bbox=bbox,
        geometry=req.geometry,
        date_from=req.date_from,
        date_to=req.date_to,
        max_cloud=req.max_cloud,
        mission=req.mission,
        providers=req.providers,
        limit=req.limit,
    )


# --------------------------------------------------------------------------
# indices
# --------------------------------------------------------------------------
@app.post("/api/index", response_model=IndexResponse)
async def index_endpoint(req: IndexRequest) -> IndexResponse:
    bbox, geometry = _resolve_aoi(req.bbox, req.geometry)

    async with connection() as fetch:
        result = await indices.compute(
            fetch, index=req.index, bbox=bbox, geometry=geometry
        )
        cache = fetch.stats.as_dict()

    payload = {
        "index": req.index.lower(),
        "bbox": [round(v, 6) for v in bbox],
        "geometry": geometry,
    }
    if result.get("status") == "ok":
        result["evidence_id"] = evidence.store(
            payload,
            result,
            environment={
                "source": result["stats"]["source"],
                "raster_function": result["stats"]["raster_function"],
                "aoi_km2": round(bbox_area_km2(bbox), 1),
                "coverage": "rolling ~14-month window",
            },
            cache=cache,
        )
    accessed = datetime.now(timezone.utc)
    result["generated_at"] = accessed.isoformat()
    result["citation"] = _citation(accessed)
    result["cache"] = cache
    return IndexResponse(**result)


# --------------------------------------------------------------------------
# index over time
# --------------------------------------------------------------------------
@app.post("/api/timeseries")
async def timeseries_endpoint(req: TimeseriesRequest) -> dict[str, Any]:
    """One index value per acquisition day, for the trend line and its CSV."""
    bbox, geometry = _resolve_aoi(req.bbox, req.geometry)

    today = datetime.now(timezone.utc).date()
    date_to = req.date_to or today.isoformat()
    date_from = req.date_from or (today - timedelta(days=90)).isoformat()
    if date_from > date_to:
        raise AppError(INVALID_ARGUMENTS, "date_from must be on or before date_to")

    async with connection() as fetch:
        return await timeseries.build(
            fetch,
            bbox=bbox,
            index=req.index,
            date_from=date_from,
            date_to=date_to,
            max_points=req.max_points,
            max_cloud=req.max_cloud,
            geometry=geometry,
        )


@app.post("/api/compare")
async def compare_endpoint(req: CompareRequest) -> dict[str, Any]:
    """Two dates, two pinned acquisitions, one difference."""
    bbox, geometry = _resolve_aoi(req.bbox, req.geometry)
    async with connection() as fetch:
        result = await compare.build(
            fetch,
            bbox=bbox,
            index=req.index,
            date_a=req.date_a,
            date_b=req.date_b,
            max_cloud=req.max_cloud,
            geometry=geometry,
        )
    accessed = datetime.now(timezone.utc)
    result["generated_at"] = accessed.isoformat()
    result["citation"] = _citation(accessed)
    return result


@app.post("/api/seasonal")
async def seasonal_endpoint(req: SeasonalRequest) -> dict[str, Any]:
    """One calendar month across years, so the season is held constant."""
    bbox, geometry = _resolve_aoi(req.bbox, req.geometry)
    async with connection() as fetch:
        result = await seasons.build(
            fetch,
            bbox=bbox,
            index=req.index,
            month=req.month,
            years=req.years,
            max_cloud=req.max_cloud,
            geometry=geometry,
        )
    accessed = datetime.now(timezone.utc)
    result["generated_at"] = accessed.isoformat()
    result["citation"] = _citation(accessed)
    return result


@app.post("/api/thermal")
async def thermal_endpoint(req: ThermalRequest) -> dict[str, Any]:
    """Landsat 8/9 brightness temperature for the same month across years."""
    bbox, geometry = _resolve_aoi(req.bbox, req.geometry)
    async with connection() as fetch:
        result = await thermal.build(
            fetch,
            bbox=bbox,
            month=req.month,
            years=req.years,
            max_scenes=req.max_scenes,
            geometry=geometry,
        )
    accessed = datetime.now(timezone.utc)
    result["generated_at"] = accessed.isoformat()
    result["citation"] = (
        "Landsat 8/9 Level-1 TIRS brightness temperature (100 m), USGS/NASA via ArcGIS "
        f"Landsat8_Views ImageServer; accessed {accessed.date().isoformat()}."
    )
    return result


@app.get("/api/demo")
async def demo_endpoint() -> dict[str, Any]:
    """A cached reference measurement, for a demo without network access.

    Labelled everywhere it appears: `source` says it is a fixture, and the
    message gives the date it was captured. A demo result must never be
    mistakable for a live one (§8.1).
    """
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "gold", "arcgis_ndvi_reference.json",
    )
    try:
        with open(path, encoding="utf-8") as fh:
            fixture = json.load(fh)
    except OSError as exc:
        raise AppError(EVIDENCE_NOT_FOUND, f"Demo fixture unavailable: {exc}") from exc

    ndvi = fixture.get("ndvi") or {}
    captured = fixture.get("probe_date")
    return {
        "bbox": fixture.get("bbox"),
        "index": "ndvi",
        "label": "NDVI",
        "formula": config.INDEX_DEFS["ndvi"]["formula"],
        "status": "ok",
        "message": f"Cached reference measurement captured live on {captured} — not a live request.",
        "stats": {
            "min": ndvi.get("min"),
            "max": ndvi.get("max"),
            "mean": ndvi.get("mean"),
            "std": ndvi.get("std"),
            "median": None,
            "count": ndvi.get("count"),
            "source": f"cached gold fixture (offline demo, {captured})",
            "raster_function": ndvi.get("raster_function"),
        },
        "histogram": None,
        "visual_png_url": None,
        "evidence_id": None,
        "cache": {"hits": 0, "misses": 0},
    }


# --------------------------------------------------------------------------
# one-click download
# --------------------------------------------------------------------------
@app.post("/api/download")
async def download_endpoint(req: DownloadRequest) -> Response:
    """Zip one image per acquisition day for a single day or a date range.

    Returns a binary archive, so the image count and manifest id travel as
    headers; the manifest itself is inside the zip with the images.
    """
    today = datetime.now(timezone.utc).date()
    date_to = req.date_to or req.date_from or today.isoformat()
    date_from = req.date_from or (today - timedelta(days=30)).isoformat()
    if date_from > date_to:
        raise AppError(INVALID_ARGUMENTS, "date_from must be on or before date_to")

    async with connection() as fetch:
        if req.text and req.text.strip():
            # A locality, river, peak or landmark instead of a drawn box.
            candidates = await geocode.geocode(fetch, req.text.strip(), limit=8)
            chosen = geocode.choose(
                candidates, place_id=req.place_id, best_match=req.best_match
            )
            try:
                bbox, fit_notes = locality.fit(chosen)
            except ValueError as exc:
                raise AppError(PLACE_NOT_FOUND, str(exc)) from exc
            origin = {
                "name": chosen.get("name"),
                "label": chosen.get("label"),
                "id": chosen.get("id"),
                "source": chosen.get("source"),
                "aoi_method": "named feature, buffered" if fit_notes else "named feature",
                "notes": fit_notes,
            }
        else:
            bbox = _resolve_bbox(req.bbox, req.geometry)
            origin = {
                "name": None, "label": None, "id": None,
                "source": "drawn AOI or supplied bbox", "aoi_method": "bbox", "notes": [],
            }

        blob, manifest = await download.build(
            fetch,
            bbox=bbox,
            date_from=date_from,
            date_to=date_to,
            kind=req.kind,
            index=req.index,
            max_images=req.max_images,
            max_cloud=req.max_cloud,
            origin=origin,
        )

    span = date_from if date_from == date_to else f"{date_from}_{date_to}"
    return Response(
        content=blob,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="SAGE_{span}_{req.kind}.zip"',
            "X-SAGE-Images": str(manifest["count"]),
            "X-SAGE-Manifest-Id": manifest["id"],
            "Access-Control-Expose-Headers": (
                "X-SAGE-Images, X-SAGE-Manifest-Id, Content-Disposition"
            ),
        },
    )


# --------------------------------------------------------------------------
# provenance and evidence
# --------------------------------------------------------------------------
@app.post("/api/provenance", response_model=ProvenanceResult)
async def provenance_endpoint(file: UploadFile = File(...)) -> ProvenanceResult:
    data = await file.read()
    result = provenance.analyze(file.filename or "", data)
    return ProvenanceResult(**result)


@app.get("/api/evidence")
async def evidence_index(limit: int = 20) -> dict[str, Any]:
    return {"count": len(evidence.recent(limit)), "runs": evidence.recent(limit)}


@app.get("/api/evidence/{evidence_id}")
async def evidence_endpoint(evidence_id: str) -> dict[str, Any]:
    record = evidence.lookup(evidence_id)
    if not record:
        raise AppError(EVIDENCE_NOT_FOUND, "Unknown evidence id")
    return record


@app.post("/api/evidence/{evidence_id}/replay")
async def replay_endpoint(evidence_id: str) -> dict[str, Any]:
    record = evidence.lookup(evidence_id)
    if not record:
        raise AppError(EVIDENCE_NOT_FOUND, "Unknown evidence id")

    payload = record.get("payload") or {}
    async with connection() as fetch:
        fresh = await indices.compute(
            fetch,
            index=payload.get("index", "ndvi"),
            bbox=payload["bbox"],
            geometry=payload.get("geometry"),
        )
        cache = fetch.stats.as_dict()

    comparison = evidence.compare(record, fresh)
    return {
        "evidence_id": evidence_id,
        "original_generated_at": record.get("generated_at"),
        "original_environment": record.get("environment"),
        "replay_status": fresh.get("status"),
        "original_stats": (record.get("result") or {}).get("stats"),
        "replay_stats": fresh.get("stats"),
        "comparison": comparison,
        "cache": cache,
        "interpretation": (
            "Statistics reproduced."
            if comparison["matched"]
            else "Statistics differ. The ArcGIS window rolls forward daily, so the "
            "underlying mosaic may have changed since this id was issued."
        ),
    }
