"""Turn a named feature into an AOI that the services can actually serve.

Not every named thing is an area. The geocoder returns three shapes:

  * an **area** — a city, district, lake polygon. Its bbox is a usable AOI.
  * a **point** — a peak, a monument, a temple. Its bbox is degenerate (metres).
  * a **line** — a river, a canal, a highway. Its bbox is the whole feature, so
    "Godavari" resolves to a box spanning several states: either too large to
    render or so coarse that the statistics describe everything and nothing.

So the fit is: keep the bbox when it is a sane area, and otherwise build a
square AOI around the feature's centre. The note returned alongside says which
of the two happened, because a user who typed "Godavari" is entitled to know
they got a 3 km window near the named point rather than the river.
"""
from __future__ import annotations

from typing import Any

from app import config
from app.services.geo import bbox_area_km2, bbox_span_km, buffer_bbox


def fit(place: dict[str, Any]) -> tuple[list[float], list[str]]:
    """Return (bbox, notes) for a geocoder candidate."""
    notes: list[str] = []
    bbox = place.get("bbox")
    centre = place.get("centre")
    label = place.get("name") or place.get("label") or "the feature"

    if bbox and len(bbox) == 4:
        area = bbox_area_km2(bbox)
        span = bbox_span_km(bbox)

        if span <= config.MAX_STATS_SPAN_KM and area >= config.MIN_LOCALITY_KM2:
            return bbox, notes

        if span > config.MAX_STATS_SPAN_KM:
            notes.append(
                f"'{label}' spans about {span:,.0f} km; the statistics service can "
                f"only render up to {config.MAX_STATS_SPAN_KM:,.0f} km across, so this "
                f"is a {config.LOCALITY_BUFFER_KM:g} km box around its centre. Draw a "
                "box to choose a different part."
            )
        else:
            notes.append(
                f"'{label}' is a point feature, so this is a "
                f"{config.LOCALITY_BUFFER_KM:g} km box around it. Draw a box to widen it."
            )
    else:
        notes.append(f"'{label}' has no bounding box; using a point-centred AOI.")

    if centre:
        return buffer_bbox(centre, config.LOCALITY_BUFFER_KM), notes

    # No usable geometry at all: refuse rather than invent coordinates.
    raise ValueError(f"'{label}' has neither a bounding box nor a centre point")
