"""Shared HTTP client with retries, plus a small TTL cache that also survives restarts on disk."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

from ..config import settings

log = logging.getLogger("urbanshield.http")
_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(25.0, connect=10.0),
            headers={"User-Agent": settings.user_agent, "Accept": "application/json"},
            follow_redirects=True,
        )
    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


class UpstreamError(RuntimeError):
    """An external data source failed after retries."""


async def get(url: str, params: dict | None = None, *, retries: int = 3, as_text: bool = False,
              headers: dict | None = None) -> Any:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            r = await client().get(url, params=params, headers=headers)
            if r.status_code == 429:
                await asyncio.sleep(min(60, int(r.headers.get("Retry-After") or 0) or 15 * (attempt + 1)))
                last = httpx.HTTPStatusError("429 rate limited", request=r.request, response=r)
                continue
            if r.status_code >= 500:
                raise httpx.HTTPStatusError(f"{r.status_code}", request=r.request, response=r)
            r.raise_for_status()
            return r.text if as_text else r.json()
        except (httpx.HTTPError, ValueError) as e:
            last = e
            await asyncio.sleep(0.6 * (2**attempt))
    raise UpstreamError(f"GET {url} failed: {last}")


async def post_form(url: str, data: dict, *, retries: int = 3, auth: tuple[str, str] | None = None) -> Any:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            r = await client().post(url, data=data, auth=auth)
            if r.status_code == 429 or r.status_code >= 500:
                raise httpx.HTTPStatusError(f"{r.status_code}", request=r.request, response=r)
            r.raise_for_status()
            ctype = r.headers.get("content-type", "")
            return r.json() if "json" in ctype else r.text
        except (httpx.HTTPError, ValueError) as e:
            last = e
            await asyncio.sleep(0.6 * (2**attempt))
    raise UpstreamError(f"POST {url} failed: {last}")


class TTLCache:
    """In-memory cache with a disk copy, so a restart can serve the last good data immediately."""

    def __init__(self, name: str):
        self.dir = settings.data_dir / "cache"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.name = name
        self._mem: dict[str, tuple[float, Any]] = {}

    def _path(self, key: str) -> Path:
        return self.dir / f"{self.name}-{hashlib.sha1(key.encode()).hexdigest()[:16]}.json"

    def get(self, key: str, ttl: float, allow_stale: bool = False) -> Any | None:
        now = time.time()
        hit = self._mem.get(key)
        if hit is None:
            p = self._path(key)
            if p.exists():
                try:
                    blob = json.loads(p.read_text())
                    hit = (blob["t"], blob["v"])
                    self._mem[key] = hit
                except (ValueError, KeyError):
                    hit = None
        if hit is None:
            return None
        if allow_stale or now - hit[0] < ttl:
            return hit[1]
        return None

    def age(self, key: str) -> float | None:
        hit = self._mem.get(key)
        return None if hit is None else time.time() - hit[0]

    def set(self, key: str, value: Any) -> None:
        t = time.time()
        self._mem[key] = (t, value)
        try:
            self._path(key).write_text(json.dumps({"t": t, "v": value}))
        except OSError as e:  # pragma: no cover
            log.warning("cache write failed: %s", e)
