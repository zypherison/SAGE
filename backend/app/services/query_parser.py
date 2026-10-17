"""Deterministic natural-language intent parser.

Ported from the Seminar 1 parser (which reached 100% slot accuracy on the
36-utterance pilot set) and extended with an `index` slot.

Design rules that matter and were each learned from a real failure:
  1. "last <season>" must beat bare "<season>"
  2. "<month> to <month> this year" must beat bare "this year"
  3. "<season> <year>" must beat bare "<year>"
  4. month/season tokens must be stripped from place candidates
     (Seminar 1 bug: "Tokyo June" -> geocoded a hairdresser in Tokyo)
  5. year separators accept to | through | and | - | en-dash
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from datetime import date, timedelta
from typing import Any

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
MONTHS_ALT = "|".join(sorted(MONTHS, key=len, reverse=True))

SEASONS = {
    "summer": (6, 8), "winter": (12, 2), "spring": (3, 5),
    "autumn": (9, 11), "fall": (9, 11), "monsoon": (6, 9), "dry season": (11, 2),
}
SEASONS_ALT = "|".join(sorted(SEASONS, key=len, reverse=True))

INDEX_PATTERNS = [
    (re.compile(
        r"\bndvi\b|\bvegetation(?:\s+(?:index|health|cover))?\b|\bgreenness\b|\bcrop health\b",
        re.I,
    ), "ndvi"),
    (re.compile(r"\bndwi\b|\bwater (?:index|body|bodies)\b|\bflood\b", re.I), "ndwi"),
    (re.compile(r"\bnbr\b|\bburn(?:e?d|t|ing)?\b|\bfire severity\b|\bwildfire\b", re.I), "nbr"),
    (re.compile(r"\bndbi\b|\bbuilt[- ]?up\b|\burban (?:index|growth|expansion)\b", re.I), "ndbi"),
]

MISSIONS = [
    (re.compile(r"\bsentinel[- ]?(?:2|two)\b|\bs2\b", re.I), "sentinel-2-l2a"),
    (re.compile(r"\blandsat[- ]?(?:8|9|eight|nine)?\b", re.I), "landsat-c2-l2"),
]

CLOUD_FREE = re.compile(r"\bcloud[- ]?free\b|\bcloudless\b|\bno clouds?\b|\bclear sky\b", re.I)
CLOUD_BEFORE = re.compile(
    r"\b(?:under|below|less than|max(?:imum)?(?: cloud)?(?: cover)?(?: of)?|<|up to)\s*(\d{1,3})\s*%?", re.I
)
CLOUD_AFTER = re.compile(r"\b(\d{1,3})\s*%\s*cloud(?: cover)?\s*(?:max)?\b", re.I)
LAST_N_PERIOD = re.compile(r"\blast\s+(\d{1,3})\s*(day|week|month|year)s?\b", re.I)
# "last month" (no numeral) — a distinct case from "last 3 months"
LAST_PERIOD = re.compile(r"\blast\s+(day|week|month|year)\b", re.I)

TRAILING_FILLER = (
    r"(?:from|during|between|with|without|for|in|over|last|this|under|near|"
    r"taken|captured|showing|of|on|at)"
)

NON_PLACE = {
    "images", "image", "imagery", "pictures", "photos", "scenes", "tiles", "data", "view",
    "satellite", "sentinel", "landsat", "s2", "cloud", "clouds", "cloudless", "free",
    "clear", "high", "resolution", "latest", "recent", "show", "find", "get", "me",
    "please", "any", "want", "see", "how", "does", "the", "a", "an", "of", "in", "on",
    "at", "over", "around", "near", "with", "without", "from", "during", "between",
    "for", "last", "this", "under", "and", "to", "region", "area", "ndvi", "ndwi",
    "nbr", "ndbi", "index", "vegetation", "water", "burn", "burned", "urban", "built",
    "map", "true", "color", "colour", "rgb",
}


@dataclass
class Intent:
    place: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    max_cloud: float | None = None
    mission: str | None = None
    index: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------
# dates
# --------------------------------------------------------------------------
def _month_span(y: int, m1: int, m2: int) -> tuple[date, date]:
    start = date(y, m1, 1)
    end = date(y + (1 if m2 == 12 else 0), (m2 % 12) + 1, 1) - timedelta(days=1)
    return start, end


def resolve_dates(text: str, today: date | None = None) -> tuple[str | None, str | None, list[str]]:
    today = today or date.today()
    low = text.lower()
    sep = r"(?:-|–|to|through|and|until)"

    # 2. month range + "this year"
    m = re.search(rf"\b({MONTHS_ALT})\s*{sep}\s*({MONTHS_ALT})\b[^\d]*\bthis year\b", low)
    if m:
        s, e = _month_span(today.year, MONTHS[m.group(1)], MONTHS[m.group(2)])
        return s.isoformat(), e.isoformat(), ["month range within this year"]

    if re.search(r"\bthis year\b", low):
        return f"{today.year}-01-01", f"{today.year}-12-31", [f"this year ({today.year})"]

    # year range
    m = re.search(rf"\b((?:19|20)\d{{2}})\s*{sep}\s*((?:19|20)\d{{2}})\b", low)
    if m:
        return f"{m.group(1)}-01-01", f"{m.group(2)}-12-31", [f"year range {m.group(1)}-{m.group(2)}"]

    # month range + explicit year
    m = re.search(
        rf"\b({MONTHS_ALT})\s*{sep}\s*({MONTHS_ALT})\s*,?\s*((?:19|20)\d{{2}})\b", low
    )
    if m:
        s, e = _month_span(int(m.group(3)), MONTHS[m.group(1)], MONTHS[m.group(2)])
        return s.isoformat(), e.isoformat(), [f"month range in {m.group(3)}"]

    # single month + year
    m = re.search(rf"\b({MONTHS_ALT})\s*((?:19|20)\d{{2}})\b", low)
    if m:
        s, e = _month_span(int(m.group(2)), MONTHS[m.group(1)], MONTHS[m.group(1)])
        return s.isoformat(), e.isoformat(), [f"{m.group(1)} {m.group(2)}"]

    # relative "last N days/weeks/months/years"
    unit_days = {"day": 1, "week": 7, "month": 30, "year": 365}
    m = LAST_N_PERIOD.search(text)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        delta = timedelta(days=n * unit_days[unit])
        return (today - delta).isoformat(), today.isoformat(), [f"last {n} {unit}(s)"]

    # relative "last month" with no numeral (n = 1)
    m = LAST_PERIOD.search(text)
    if m:
        unit = m.group(1).lower()
        delta = timedelta(days=unit_days[unit])
        return (today - delta).isoformat(), today.isoformat(), [f"last {unit}"]

    # 1. "last <season>" before bare season
    m = re.search(rf"\blast\s+({SEASONS_ALT})\b", low)
    if m:
        name = m.group(1)
        y = today.year - 1
        m1, m2 = SEASONS[name]
        if m1 <= m2:
            s, e = _month_span(y, m1, m2)
        else:
            s, e = date(y - 1, m1, 1), date(y, m2 % 12 + 1, 1) - timedelta(days=1)
        return s.isoformat(), e.isoformat(), [f"last {name} ({y})"]

    # 3. "<season> <year>"
    m = re.search(rf"\b({SEASONS_ALT})\s*((?:19|20)\d{{2}})\b", low)
    if m:
        name, y = m.group(1), int(m.group(2))
        m1, m2 = SEASONS[name]
        if m1 <= m2:
            s, e = _month_span(y, m1, m2)
        else:
            s, e = date(y - 1, m1, 1), date(y, m2 % 12 + 1, 1) - timedelta(days=1)
        return s.isoformat(), e.isoformat(), [f"{name} {y}"]

    # bare season (current year)
    m = re.search(rf"\b({SEASONS_ALT})\b", low)
    if m:
        name = m.group(1)
        m1, m2 = SEASONS[name]
        s, e = _month_span(today.year, m1, m2)
        return s.isoformat(), e.isoformat(), [f"{name} {today.year}"]

    # bare year
    m = re.search(r"\b((?:19|20)\d{2})\b", low)
    if m:
        y = m.group(1)
        return f"{y}-01-01", f"{y}-12-31", [f"year {y}"]

    return None, None, []


# --------------------------------------------------------------------------
# place
# --------------------------------------------------------------------------
def clean_place(candidate: str) -> str | None:
    place = candidate.strip(" ,.'-")

    # strip quantified cloud phrases first: they span several tokens, so the
    # single-token stripper below would leave "Under 20% Cloud" behind.
    place = re.sub(
        r"\s*(?:under|below|less than|max(?:imum)?(?: of)?|up to)?\s*\d{1,3}\s*%\s*cloud"
        r"(?:\s*cover)?(?:\s*max)?",
        "", place, flags=re.I,
    )
    place = re.sub(r"\s*\b(?:cloudless|cloud[- ]?free|cloud cover|clouds?)\b", "", place, flags=re.I)
    place = re.sub(r"\s*(?:under|below|less than|up to|max)$", "", place, flags=re.I)

    place = re.sub(
        rf"\s+(?:{MONTHS_ALT}|{SEASONS_ALT}|\d{{1,3}}\s*%|(?:19|20)\d{{2}})\s*$",
        "", place, flags=re.I,
    )
    # "last month", "last 30 days", "this week": relative periods, not place names.
    place = re.sub(
        r"\s+(?:(?:last|this|next)\s+)?\d{0,3}\s*(?:day|week|month|year)s?$",
        "", place, flags=re.I,
    )
    place = re.sub(rf"\s+{TRAILING_FILLER}$", "", place, flags=re.I)
    place = place.strip(" ,.'-")
    if not place:
        return None
    words = re.findall(r"[a-z]+", place.lower())
    if words and all(w in NON_PLACE for w in words):
        return None
    return " ".join(w.capitalize() if not w.isupper() else w for w in place.split())


def extract_place(text: str) -> str | None:
    boundary = (
        rf"(?=\s+{TRAILING_FILLER}\b|\s+(?:{MONTHS_ALT})\b|\s+(?:{SEASONS_ALT})\b"
        rf"|\s+(?:19|20)\d{{2}}\b|\s+ndvi\b|\s+ndwi\b|\s*$|[,.;!?])"
    )
    patterns = [
        re.compile(rf"\b(?:of|in|over|around|at|near|for)\s+([a-z][a-z\s\.'-]{{2,60}}?){boundary}", re.I),
        re.compile(
            rf"\b(?:images?|imagery|pictures?|photos?|scenes?|tiles?|data)\s+(?:of|over|for)\s+"
            rf"([a-z][a-z\s\.'-]{{2,60}}?){boundary}", re.I,
        ),
        re.compile(rf"\b(?:see|view|explore|visit|show|map)\s+(?:me\s+)?([a-z][a-z\s\.'-]{{2,60}}?){boundary}", re.I),
    ]
    for pat in patterns:
        m = pat.search(text)
        if m:
            cleaned = clean_place(m.group(1))
            if cleaned:
                return cleaned

    # Fallback for "<Place> <month|season|year>" with no preposition, e.g.
    # "Tokyo June 2024". Without this, that case falls through to no place at all.
    m = re.match(
        rf"^\s*([a-z][a-z\s\.'-]{{2,60}}?)(?=\s+(?:{MONTHS_ALT})\b"
        rf"|\s+(?:{SEASONS_ALT})\b|\s+(?:19|20)\d{{2}}\b)",
        text, re.I,
    )
    if m:
        cleaned = clean_place(m.group(1))
        if cleaned:
            return cleaned

    return _bare_place(text)


def _bare_place(text: str) -> str | None:
    """A query that is only a name: "Charminar", "Godavari", "Hussain Sagar".

    The pattern-based extractor needs a preposition, so a person who types just
    a place — which is the most natural thing to type — used to get "No place
    found in the query". The guards keep generic asks from becoming place names:
    a leading generic word, any digit, or a long sentence disqualifies it.
    """
    raw = text.strip()
    if not raw or re.search(r"\d", raw) or len(raw.split()) > 6:
        return None

    cleaned = clean_place(raw)
    if not cleaned:
        return None

    # Drop trailing generic words: "Hussain Sagar water" -> "Hussain Sagar".
    parts = cleaned.split()
    while len(parts) > 1 and parts[-1].lower().strip(".,;:!?") in NON_PLACE:
        parts.pop()
    cleaned = " ".join(parts)

    first = parts[0].lower().strip(".,;:!?")
    if first in NON_PLACE:
        return None
    words = re.findall(r"[a-z]+", cleaned.lower())
    if words and all(w in NON_PLACE for w in words):
        return None
    return cleaned


# --------------------------------------------------------------------------
# main entry
# --------------------------------------------------------------------------
def parse(text: str, *, today: date | None = None) -> Intent:
    intent = Intent()

    for pat, coll in MISSIONS:
        if pat.search(text):
            intent.mission = coll
            intent.notes.append(f"mission → {coll}")
            break

    for pat, name in INDEX_PATTERNS:
        if pat.search(text):
            intent.index = name
            intent.notes.append(f"index → {name.upper()}")
            break

    if CLOUD_FREE.search(text):
        intent.max_cloud = 5.0
        intent.notes.append("cloud-free requested → max_cloud 5%")

    m = CLOUD_BEFORE.search(text) or CLOUD_AFTER.search(text)
    if m:
        val = float(m.group(1))
        if 0 <= val <= 100:
            intent.max_cloud = val
            intent.notes.append(f"explicit cloud limit {val:g}%")

    intent.date_from, intent.date_to, date_notes = resolve_dates(text, today)
    intent.notes.extend(date_notes)

    intent.place = extract_place(text)
    if intent.place:
        intent.notes.append(f"place → {intent.place}")

    return intent
