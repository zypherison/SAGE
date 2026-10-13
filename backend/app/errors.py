"""Machine-readable failures, modelled on alephgeo's `RequestError`.

Aleph's CLI returns `{"status": "error", "error": {"code", "message", **details}}`
and maps codes to exit statuses. Adopting the shape here means a caller can branch
on `code` instead of parsing prose, and an ambiguity can carry its candidates.
"""
from __future__ import annotations

from typing import Any

# codes (kept as constants so they cannot drift between raise-site and UI)
INVALID_ARGUMENTS = "invalid_arguments"
PLACE_NOT_FOUND = "place_not_found"
AMBIGUOUS_PLACE = "ambiguous_place"
PROVIDER_UNAVAILABLE = "provider_unavailable"
REQUEST_FAILED = "request_failed"
AOI_TOO_LARGE = "aoi_too_large"
UNKNOWN_INDEX = "unknown_index"
NO_IMAGERY = "no_imagery"
EVIDENCE_NOT_FOUND = "evidence_not_found"

STATUS_BY_CODE = {
    INVALID_ARGUMENTS: 422,
    PLACE_NOT_FOUND: 404,
    AMBIGUOUS_PLACE: 409,  # the request is well-formed; the choice is unresolved
    PROVIDER_UNAVAILABLE: 502,
    REQUEST_FAILED: 502,
    AOI_TOO_LARGE: 422,
    UNKNOWN_INDEX: 422,
    NO_IMAGERY: 422,  # a valid request that the coverage window cannot satisfy
    EVIDENCE_NOT_FOUND: 404,
}


class AppError(Exception):
    """A failure with a stable code plus optional structured details."""

    def __init__(self, code: str, message: str, *, status: int | None = None, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details
        self.status = status if status is not None else STATUS_BY_CODE.get(code, 400)

    def envelope(self) -> dict[str, Any]:
        return {"status": "error", "error": {"code": self.code, "message": self.message, **self.details}}
