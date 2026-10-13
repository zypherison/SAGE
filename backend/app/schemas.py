"""Canonical schemas shared by every provider and the API."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Scene(BaseModel):
    """One imagery acquisition, normalized across providers."""

    id: str
    provider: str
    mission: str
    datetime: str | None = None
    cloud_cover: float | None = Field(None, description="Percent, 0-100")
    bbox: list[float] | None = None
    resolution_m: float | None = None
    thumbnail: str | None = None
    index_support: bool = Field(False, description="Provider can compute indices server-side")
    assets: list[str] = Field(default_factory=list)
    score: float | None = None
    score_parts: dict[str, float] = Field(default_factory=dict)
    source_url: str | None = None


class Place(BaseModel):
    """A geocoding candidate with a stable OSM identity."""

    id: str
    name: str | None = None
    label: str
    type: str | None = None
    klass: str | None = None
    importance: float | None = None
    bbox: list[float] | None = None
    centre: list[float] | None = None
    source: str
    source_url: str | None = None


class Intent(BaseModel):
    """Structured, user-editable interpretation of a natural-language query."""

    place: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    max_cloud: float | None = None
    mission: str | None = None
    index: str | None = None
    notes: list[str] = Field(default_factory=list)


class Plan(BaseModel):
    """What a run will cost, shown before it is executed."""

    providers: list[str]
    requests: int
    estimated_seconds: float
    basis: str = "nominal per-provider latency constants, not a measurement"
    notes: list[str] = Field(default_factory=list)


class CacheStats(BaseModel):
    hits: int = 0
    misses: int = 0


class SearchRequest(BaseModel):
    bbox: list[float] | None = None
    geometry: dict[str, Any] | None = None
    date_from: str | None = None
    date_to: str | None = None
    max_cloud: float | None = None
    mission: str | None = None
    providers: list[str] | None = None
    limit: int = 40


class ProviderStatus(BaseModel):
    name: str
    ok: bool
    count: int = 0
    error: str | None = None
    error_code: str | None = None
    elapsed_ms: int | None = None


class SearchResponse(BaseModel):
    count: int
    scenes: list[Scene]
    providers: list[ProviderStatus]
    warnings: list[str] = Field(default_factory=list)
    plan: Plan | None = None
    cache: CacheStats = Field(default_factory=CacheStats)


class QueryRequest(BaseModel):
    text: str
    limit: int = 40
    run_search: bool = True
    place_id: str | None = None
    best_match: bool = False


class QueryResponse(BaseModel):
    intent: Intent
    bbox: list[float] | None = None
    place: Place | None = None
    candidates: list[Place] = Field(default_factory=list)
    plan: Plan | None = None
    search: SearchResponse | None = None


class PlanRequest(BaseModel):
    text: str | None = None
    bbox: list[float] | None = None
    geometry: dict[str, Any] | None = None
    providers: list[str] | None = None


class PlanResponse(BaseModel):
    intent: Intent | None = None
    bbox: list[float] | None = None
    area_km2: float | None = None
    candidates: list[Place] = Field(default_factory=list)
    plan: Plan
    warnings: list[str] = Field(default_factory=list)


class Stats(BaseModel):
    min: float
    max: float
    mean: float
    std: float | None = None
    median: float | None = None
    count: int
    source: str
    raster_function: str | None = None


class IndexRequest(BaseModel):
    index: str = "ndvi"
    bbox: list[float] | None = None
    geometry: dict[str, Any] | None = None


class IndexResponse(BaseModel):
    index: str
    label: str
    formula: str
    stats: Stats | None = None
    histogram: dict[str, Any] | None = None
    visual_png_url: str | None = None
    status: str = "ok"  # ok | insufficient_data | unsupported | error
    message: str | None = None
    evidence_id: str | None = None
    cache: CacheStats | None = None


class DownloadRequest(BaseModel):
    """One click: imagery for a single day, or one image per day in a window."""

    text: str | None = None  # a locality, river, peak or landmark instead of a bbox
    bbox: list[float] | None = None
    geometry: dict[str, Any] | None = None
    date_from: str | None = None
    date_to: str | None = None
    kind: str = "truecolor"  # truecolor | index
    index: str = "ndvi"
    max_images: int | None = None
    max_cloud: float | None = 40.0
    place_id: str | None = None
    best_match: bool = False


class TimeseriesRequest(BaseModel):
    """One point per acquisition day inside a window."""

    bbox: list[float] | None = None
    geometry: dict[str, Any] | None = None
    index: str = "ndvi"
    date_from: str | None = None
    date_to: str | None = None
    max_points: int | None = None
    max_cloud: float | None = 40.0


class ProvenanceResult(BaseModel):
    confidence: str  # exact | tile | unknown
    mission: str | None = None
    acquisition_date: str | None = None
    tile: str | None = None
    centroid: list[float] | None = None
    bbox: list[float] | None = None
    fields: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
