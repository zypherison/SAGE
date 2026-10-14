"""Paced, cached, retrying HTTP.

This is the SAGE analogue of alephgeo's `Client` + `CachedClient`:

  * per-host pacing between calls (`delay`), so we never hammer a free service
  * bounded retries with exponential backoff on transport errors and 5xx
  * a sha256-keyed response cache that reports `hits`/`misses`

One deliberate divergence, which the report must state: Aleph caches quick-query
responses to DISK for 7 days. Index statistics here track a mosaic that the
provider updates daily, so caching them for a week would return stale numbers.
We therefore keep the cache in-process with a short TTL (default 300 s).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app import config
from app.errors import AppError, PROVIDER_UNAVAILABLE


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0

    def as_dict(self) -> dict[str, int]:
        return {"hits": self.hits, "misses": self.misses}


# Process-wide store. A per-request cache could never hit, because every API call
# builds a fresh Fetch; the store has to outlive the request while the hit/miss
# counters stay per-Fetch so each response reports its own numbers.
_CACHE: dict[str, tuple[float, Any]] = {}
_LAST_CALL: dict[str, float] = {}
_LOCKS: dict[str, asyncio.Lock] = {}


class Fetch:
    """Shared connection pool, TTL cache, and per-host pacing for one request."""

    def __init__(self, client: httpx.AsyncClient, *, delay: float | None = None) -> None:
        self.client = client
        self.delay = config.REQUEST_DELAY if delay is None else delay
        self.stats = CacheStats()

    # -- helpers ----------------------------------------------------------
    @staticmethod
    def _key(method: str, url: str, params: Any, body: Any) -> str:
        blob = json.dumps(
            {"m": method, "u": url, "p": params, "b": body},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    async def _pace(self, host: str) -> None:
        if self.delay <= 0:
            return
        lock = _LOCKS.setdefault(host, asyncio.Lock())
        async with lock:
            elapsed = time.monotonic() - _LAST_CALL.get(host, float("-inf"))
            wait = self.delay - elapsed
            if wait > 0:
                await asyncio.sleep(wait)
            _LAST_CALL[host] = time.monotonic()

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: Any = None,
        body: Any = None,
        ttl: float | None,
        retries: int,
        expect_json: bool,
    ) -> tuple[Any, bool]:
        ttl = config.CACHE_TTL if ttl is None else ttl
        key = self._key(method, url, params, body)

        if ttl > 0 and key in _CACHE:
            stored_at, value = _CACHE[key]
            if time.monotonic() - stored_at < ttl:
                self.stats.hits += 1
                return value, True

        host = httpx.URL(url).host or "unknown"
        last_error: Exception | None = None

        for attempt in range(retries + 1):
            if attempt:
                await asyncio.sleep(0.4 * (2 ** (attempt - 1)))
            await self._pace(host)
            try:
                resp = await self.client.request(
                    method, url, params=params, json=body, timeout=config.REQUEST_TIMEOUT
                )
            except httpx.HTTPError as exc:
                last_error = exc
                continue

            if resp.status_code >= 500 or resp.status_code == 429:
                last_error = RuntimeError(f"HTTP {resp.status_code} from {host}")
                continue
            if resp.status_code >= 400:
                # 4xx is our fault, not a transient outage: do not retry, but keep
                # the provider's own text because it is what makes the bug findable.
                raise AppError(
                    PROVIDER_UNAVAILABLE,
                    f"{host} rejected the request: HTTP {resp.status_code}: {resp.text[:200]}",
                )

            value: Any = resp.json() if expect_json else resp.content
            if ttl > 0:
                _CACHE[key] = (time.monotonic(), value)
            self.stats.misses += 1
            return value, False

        self.stats.misses += 1
        raise AppError(
            PROVIDER_UNAVAILABLE,
            f"{host} did not respond after {retries + 1} attempts: {last_error}",
        )

    # -- public -----------------------------------------------------------
    async def get_json(self, url: str, *, params: Any = None, ttl: float | None = None) -> tuple[Any, bool]:
        return await self._request(
            "GET", url, params=params, ttl=ttl, retries=config.RETRIES, expect_json=True
        )

    async def post_json(self, url: str, *, json_body: Any, ttl: float | None = None) -> tuple[Any, bool]:
        return await self._request(
            "POST", url, body=json_body, ttl=ttl, retries=config.RETRIES, expect_json=True
        )

    async def get_bytes(self, url: str, *, params: Any = None, ttl: float | None = None) -> tuple[bytes, bool]:
        return await self._request(
            "GET", url, params=params, ttl=ttl, retries=config.RETRIES, expect_json=False
        )
