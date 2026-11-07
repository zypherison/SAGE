"""One-click imagery download: single day or a whole window, as one ZIP.

What is actually downloadable without a login was established by probe before
this module existed (SEMINAR2.md §0.1 addendum):

    exportImage ... &mosaicRule={"mosaicMethod":"esriMosaicLockRaster",
                                 "lockRasterIds":[29123587]}
        -> 1,874,937 B true colour, 349,461 B NDVI at 1024x768   (real pixels)

    exportImage ... &time=<day window 00:00:00Z..23:59:59Z>
        -> 4,041 B, byte-identical for true colour and NDVI      (no pixels)

So filtering by date silently produces an empty image, while pinning the
acquisition by its objectid renders it. The download therefore *locks the
raster* and records the objectid in the manifest, instead of trusting a time
filter that fails quietly. That failure mode is the third instance of the same
class of trap as the RFC3339 and CQL2 incidents in §4.2.

Every archive carries `manifest.json` and `README.txt` so the image a user puts
in a report can be traced back to its acquisition, date and rendering rule.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app import __version__, config
from app.errors import INVALID_ARGUMENTS, NO_IMAGERY, AppError
from app.schemas import Scene
from app.services import indices
from app.services.net import Fetch
from app.services.providers import arcgis

FORMAT = "sage-download"
FORMAT_VERSION = 1

ATTRIBUTION = "Contains modified Copernicus Sentinel data"
SERVICE_CREDITS = "Esri, European Commission, ESA, AWS"
LICENCE = (
    "Copernicus Sentinel data is free and open under the Copernicus Data Policy. "
    "The rendering service credits Esri, the European Commission, ESA and AWS."
)

NOTES = [
    "Rendered server-side by the ArcGIS Sentinel-2 ImageServer; this is a 10 m "
    "Sentinel-2 L2A mosaic, not single-scene radiance data.",
    "The service holds a rolling ~14-month window, so older ranges return nothing "
    "by design rather than by failure.",
    "Each date is pinned by acquisition objectid, because a date-window filter "
    "returns an empty image (verified by probe).",
    "Cloud masking is whatever the service applies by default; the cloud cover of "
    "the selected acquisition is recorded in the manifest.",
]


# --------------------------------------------------------------------------
# selection
# --------------------------------------------------------------------------
@dataclass
class Acquisition:
    """One chosen acquisition: its UTC day, catalogue objectid and cloud cover."""

    day: str
    objectid: str
    datetime: str | None
    cloud_cover: float | None


def _day(iso: str | None) -> str | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def export_size(bbox: list[float], long_side: int | None = None) -> tuple[int, int]:
    """Pixel size that keeps the AOI's ground aspect ratio (no stretching).

    Degrees are not square: at 17°N a degree of longitude is ~0.956 of a degree
    of latitude, so a fixed 1024×768 export would distort the image.
    """
    side = long_side or config.DOWNLOAD_LONG_SIDE
    lat = math.radians((bbox[1] + bbox[3]) / 2.0)
    w_km = abs(bbox[2] - bbox[0]) * 111.32 * math.cos(lat)
    h_km = abs(bbox[3] - bbox[1]) * 111.32
    if w_km <= 0 or h_km <= 0:
        return side, side
    if w_km >= h_km:
        return side, max(64, round(side * h_km / w_km))
    return max(64, round(side * w_km / h_km)), side


def select_acquisitions(scenes: list[Scene], max_images: int) -> list[Acquisition]:
    """One acquisition per UTC day (least cloudy), evenly sampled if capped.

    Sampling evenly rather than taking the newest N keeps a three-month request
    showing the start, middle and end of the window instead of only its tail.
    """
    if max_images < 1:
        raise AppError(INVALID_ARGUMENTS, "max_images must be at least 1")

    best: dict[str, Scene] = {}
    for scene in scenes:
        day = _day(scene.datetime)
        if not day:
            continue
        current = best.get(day)
        if current is None or _cloud(scene) < _cloud(current):
            best[day] = scene

    ordered = [best[d] for d in sorted(best)]
    if len(ordered) > max_images:
        if max_images == 1:
            ordered = [ordered[len(ordered) // 2]]
        else:
            # Interpolate across the full span so the first and last days are
            # always kept. A plain step (i * n / cap) rounds 7.5 down and silently
            # drops the end of the window, which is exactly the day a user asking
            # for "March to June" is most likely to want.
            span = len(ordered) - 1
            positions: list[int] = []
            for i in range(max_images):
                position = round(i * span / (max_images - 1))
                if position not in positions:
                    positions.append(position)
            ordered = [ordered[p] for p in positions]

    return [
        Acquisition(
            day=_day(s.datetime) or "",
            objectid=s.id,
            datetime=s.datetime,
            cloud_cover=s.cloud_cover,
        )
        for s in ordered
    ]


def _cloud(scene: Scene) -> float:
    return 999.0 if scene.cloud_cover is None else float(scene.cloud_cover)


async def acquisitions(
    fetch: Fetch,
    *,
    bbox: list[float],
    date_from: str,
    date_to: str,
    max_cloud: float | None,
    max_images: int,
) -> list[Acquisition]:
    """Ask the catalogue which days actually cover this AOI, then pick one each."""
    scenes = await arcgis.search(
        fetch,
        bbox=bbox,
        geometry=None,
        date_from=date_from,
        date_to=date_to,
        max_cloud=max_cloud,
        mission="sentinel-2-l2a",
        limit=config.MAX_LIMIT,
    )
    return select_acquisitions(scenes, max_images)


# --------------------------------------------------------------------------
# archive
# --------------------------------------------------------------------------
def _readme(manifest: dict[str, Any]) -> str:
    lines = [
        "SAGE — imagery download",
        "=" * 60,
        "",
        f"Area of interest : {manifest['request']['bbox']}",
        f"Window           : {manifest['request']['date_from']} to {manifest['request']['date_to']}",
        f"Rendering        : {manifest['request']['kind']}"
        + (f" ({manifest['request']['index'].upper()})" if manifest["request"].get("index") else ""),
        f"Images           : {manifest['count']}",
        "",
        "How to cite",
        "-----------",
        f"{ATTRIBUTION}, {manifest['generated_at'][:4]}.",
        f"Rendered with the ArcGIS Sentinel-2 ImageServer ({SERVICE_CREDITS}).",
        "",
        "Limitations",
        "-----------",
    ]
    lines += [f"- {note}" for note in manifest["notes"]]
    if manifest["skipped"]:
        lines += ["", "Skipped acquisitions"]
        lines += [f"- {s['date']} ({s['objectid']}): {s['reason']}" for s in manifest["skipped"]]
    lines += [
        "",
        "Per-image provenance, checksums and the exact rendering rule are in",
        "manifest.json, which is machine-readable and generated with this archive.",
        "",
    ]
    return "\n".join(lines)


def _zip(images: list[dict[str, Any]], blobs: list[bytes], manifest: dict[str, Any]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for entry, blob in zip(images, blobs):
            archive.writestr(entry["file"], blob)
        archive.writestr("manifest.json", json.dumps(manifest, indent=2, allow_nan=False) + "\n")
        archive.writestr("README.txt", _readme(manifest))
    return buffer.getvalue()


async def build(
    fetch: Fetch,
    *,
    bbox: list[float],
    date_from: str,
    date_to: str,
    kind: str = "truecolor",
    index: str = "ndvi",
    max_images: int | None = None,
    max_cloud: float | None = 40.0,
    origin: dict[str, Any] | None = None,
) -> tuple[bytes, dict[str, Any]]:
    """Return (zip bytes, manifest). Raises coded AppErrors, never empty archives."""
    if kind not in ("truecolor", "index"):
        raise AppError(INVALID_ARGUMENTS, "kind must be 'truecolor' or 'index'")

    limit = config.DOWNLOAD_MAX_IMAGES if max_images is None else max_images
    if limit < 1:
        raise AppError(INVALID_ARGUMENTS, "max_images must be at least 1")
    limit = min(limit, config.DOWNLOAD_MAX_IMAGES)

    spec: dict[str, Any] | None = None
    if kind == "index":
        spec = indices.definition(index)  # raises UNKNOWN_INDEX for a typo

    picks = await acquisitions(
        fetch,
        bbox=bbox,
        date_from=date_from,
        date_to=date_to,
        max_cloud=max_cloud,
        max_images=limit,
    )
    if not picks:
        raise AppError(
            NO_IMAGERY,
            "No Sentinel-2 acquisition intersects this AOI inside the requested "
            "window. The service holds a rolling ~14-month window, so an older "
            "range returns nothing by design — try a recent window or a larger AOI.",
        )

    size = export_size(bbox)
    rendering = str(spec["arcgis_visual"]) if spec else None
    image_entries: list[dict[str, Any]] = []
    blobs: list[bytes] = []
    skipped: list[dict[str, Any]] = []

    for pick in picks:
        try:
            blob = await arcgis.export_scene(
                fetch,
                bbox=bbox,
                objectid=pick.objectid,
                rendering_rule=rendering,
                size=size,
            )
        except AppError as exc:
            skipped.append(
                {"date": pick.day, "objectid": pick.objectid,
                 "status": "error", "reason": str(exc)[:200]}
            )
            continue

        if len(blob) < config.DOWNLOAD_MIN_BYTES:
            # Verified blank exports are ~4 KB; a real 1024-wide render is 240 KB+.
            skipped.append(
                {"date": pick.day, "objectid": pick.objectid, "status": "no_data",
                 "reason": f"only {len(blob)} bytes returned — no usable pixels over this AOI"}
            )
            continue

        label = "truecolor" if kind == "truecolor" else index.lower()
        name = f"S2_{pick.day}_{label}.png"
        blobs.append(blob)
        image_entries.append(
            {
                "file": f"images/{name}",
                "date": pick.day,
                "datetime": pick.datetime,
                "objectid": pick.objectid,
                "cloud_cover_percent": pick.cloud_cover,
                "rendering": rendering or "true colour",
                "width": size[0],
                "height": size[1],
                "bytes": len(blob),
                "sha256": hashlib.sha256(blob).hexdigest(),
            }
        )

    if not image_entries:
        reasons = "; ".join(s["reason"] for s in skipped[:2])
        raise AppError(
            NO_IMAGERY,
            f"{len(skipped)} acquisition(s) matched but none rendered usable "
            f"pixels ({reasons}). Try a larger AOI, a wider window, or a higher "
            "cloud tolerance.",
        )

    request = {
        "bbox": [round(v, 6) for v in bbox],
        "date_from": date_from,
        "date_to": date_to,
        "kind": kind,
        "index": index.lower() if kind == "index" else None,
        "max_images": limit,
        "max_cloud": max_cloud,
        "origin": origin or {},
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            {"request": request, "images": [i["date"] for i in image_entries]},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:16]

    manifest = {
        "format": FORMAT,
        "version": FORMAT_VERSION,
        "id": fingerprint,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "app_version": __version__,
        "request": request,
        "source": {
            "provider": arcgis.NAME,
            "service": config.ARCGIS_URL,
            "mission": "sentinel-2-l2a",
            "resolution_m": 10,
        },
        "licence": {
            "data": LICENCE,
            "attribution": ATTRIBUTION,
            "service_credits": SERVICE_CREDITS,
        },
        "count": len(image_entries),
        "images": image_entries,
        "skipped": skipped,
        "notes": NOTES,
    }
    return _zip(image_entries, blobs, manifest), manifest
