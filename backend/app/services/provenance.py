"""Provenance recovery for uploaded satellite images.

Cascade, most-confident first:
  1. Sentinel-2 product filename grammar  -> mission + sensing date + MGRS tile
  2. EXIF/XMP GPS                          -> centroid + bbox
  3. Otherwise abstain.

We deliberately do NOT guess a coordinate. [36] shows geo-localisation is an
open research problem; a fabricated centroid would be worse than "unknown".
"""
from __future__ import annotations

import io
import re
from datetime import datetime
from typing import Any

# S2A_MSIL2A_20240626T052641_N0510_R019_T43QHV_20240626T084512.SAFE
S2_PATTERN = re.compile(
    r"(?P<mission>S2[ABC])_MSI(?P<level>L1C|L2A)_(?P<date>\d{8})T(?P<time>\d{6})_"
    r"[A-Z0-9_]*?T(?P<tile>\d{2}[A-Z]{3})",
    re.I,
)
S2_LOOSE = re.compile(r"(?P<mission>S2[ABC])_MSI(?P<level>L1C|L2A)_(?P<date>\d{8})", re.I)

MISSION_NAMES = {
    "S2A": "Sentinel-2A",
    "S2B": "Sentinel-2B",
    "S2C": "Sentinel-2C",
}


def parse_s2_filename(name: str) -> dict[str, Any]:
    m = S2_PATTERN.search(name)
    if m:
        raw_date = m.group("date")
        try:
            iso = datetime.strptime(raw_date, "%Y%m%d").date().isoformat()
        except ValueError:
            iso = None
        tile = m.group("tile").upper()
        return {
            "confidence": "tile",
            "mission": MISSION_NAMES.get(m.group("mission").upper(), m.group("mission").upper()),
            "acquisition_date": iso,
            "tile": tile,
            "fields": {
                "level": m.group("level").upper(),
                "sensing_time_utc": m.group("time"),
                "mgrs_tile": tile,
            },
            "notes": [
                "Recovered from the Sentinel-2 product filename grammar.",
                "MGRS tile gives a ~110 km footprint, not a precise location.",
            ],
        }
    m = S2_LOOSE.search(name)
    if m:
        raw_date = m.group("date")
        try:
            iso = datetime.strptime(raw_date, "%Y%m%d").date().isoformat()
        except ValueError:
            iso = None
        return {
            "confidence": "tile",
            "mission": MISSION_NAMES.get(m.group("mission").upper(), m.group("mission").upper()),
            "acquisition_date": iso,
            "tile": None,
            "fields": {"level": m.group("level").upper()},
            "notes": ["Mission and date recovered; tile id absent from the filename."],
        }
    return {}


def _exif_gps(data: bytes) -> dict[str, Any]:
    try:
        from PIL import Image, ExifTags  # local import keeps the module importable without Pillow
    except Exception:
        return {}

    try:
        img = Image.open(io.BytesIO(data))
        exif = img.getexif()
        if not exif:
            return {}
        gps = exif.get_ifd(ExifTags.IFD.GPSInfo) or {}
        if not gps:
            return {}

        def to_deg(values: Any, ref: str | None) -> float | None:
            try:
                d, m, s = (float(v) for v in values)
            except Exception:
                return None
            deg = d + m / 60.0 + s / 3600.0
            if ref in ("S", "W"):
                deg = -deg
            return round(deg, 6)

        lat = to_deg(gps.get(2), gps.get(1))
        lon = to_deg(gps.get(4), gps.get(3))
        if lat is None or lon is None:
            return {}

        fields: dict[str, Any] = {"gps_lat": lat, "gps_lon": lon}
        dt_raw = exif.get(306) or gps.get(29)  # DateTime / GPSDateStamp
        if isinstance(dt_raw, str):
            for fmt in ("%Y:%m:%d %H:%M:%S", "%Y:%m:%d", "%Y-%m-%d"):
                try:
                    fields["exif_datetime"] = dt_raw
                    break
                except Exception:
                    continue
        return fields
    except Exception:
        return {}


def analyze(filename: str, data: bytes | None = None) -> dict[str, Any]:
    """Combine all available signals into a tiered provenance record."""
    result: dict[str, Any] = {
        "confidence": "unknown",
        "mission": None,
        "acquisition_date": None,
        "tile": None,
        "centroid": None,
        "bbox": None,
        "fields": {},
        "notes": [],
    }

    from_name = parse_s2_filename(filename or "")
    if from_name:
        result.update(
            {
                "confidence": from_name["confidence"],
                "mission": from_name["mission"],
                "acquisition_date": from_name["acquisition_date"],
                "tile": from_name["tile"],
                "fields": from_name["fields"],
                "notes": from_name["notes"],
            }
        )

    if data:
        exif = _exif_gps(data)
        if exif:
            lat, lon = exif.get("gps_lat"), exif.get("gps_lon")
            result["fields"].update(exif)
            if lat is not None and lon is not None:
                result["centroid"] = [lon, lat]
                d = 0.01  # ~1 km box; EXIF GPS has no footprint information
                result["bbox"] = [lon - d, lat - d, lon + d, lat + d]
                result["confidence"] = "exact"
                result["notes"].append(
                    "Centroid taken from embedded EXIF GPS. Footprint is a ~1 km "
                    "placeholder, not the true scene extent."
                )
            if not result.get("acquisition_date") and exif.get("exif_datetime"):
                result["acquisition_date"] = exif["exif_datetime"]

    if result["confidence"] == "unknown":
        result["notes"].append(
            "No Sentinel-2 filename pattern and no GPS metadata found. "
            "The system abstains rather than guessing a location."
        )

    return result
