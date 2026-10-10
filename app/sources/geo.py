"""Routes and addresses: Amazon Location Service when it is switched on, OpenStreetMap otherwise.

If an AWS call fails, we fall back to the OpenStreetMap services so the app keeps working.
"""
from __future__ import annotations

import asyncio
import logging

from .. import aws
from ..config import settings
from . import osm

log = logging.getLogger("urbanshield.geo")


def provider() -> str:
    return "Amazon Location Service" if aws.on("location") else "OpenStreetMap (OSRM, Nominatim)"


async def routes(a: tuple[float, float], b: tuple[float, float], avoid: list[tuple[float, float]] | None = None) -> list[dict]:
    if aws.on("location"):
        try:
            out = await asyncio.to_thread(aws.routes, a, b, avoid)
            if out:
                return out
        except Exception as e:  # noqa: BLE001
            log.warning("Amazon Location routes failed, using OSRM: %s", e)
    if avoid:
        return []  # OSRM cannot avoid areas
    out = await osm.routes(a, b)
    for r in out:
        r["provider"] = "OSRM (OpenStreetMap)"
    return out


async def geocode(q: str) -> list[dict]:
    if aws.on("location"):
        try:
            min_lon, min_lat, max_lon, max_lat = settings.bbox
            near = ((min_lat + max_lat) / 2, (min_lon + max_lon) / 2)
            out = await asyncio.to_thread(aws.geocode, q, near, settings.bbox)
            if out:
                return out
        except Exception as e:  # noqa: BLE001
            log.warning("Amazon Location geocode failed, using Nominatim: %s", e)
    return await osm.geocode(q, settings.bbox)


async def reverse(lat: float, lon: float) -> str | None:
    if aws.on("location"):
        try:
            out = await asyncio.to_thread(aws.reverse, lat, lon)
            if out:
                return out
        except Exception as e:  # noqa: BLE001
            log.warning("Amazon Location reverse geocode failed, using Nominatim: %s", e)
    return await osm.reverse(lat, lon)
