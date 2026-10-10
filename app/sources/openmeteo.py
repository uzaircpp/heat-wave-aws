"""Open-Meteo: forecasts, recent past and elevation. Free, no key for non-commercial use.

Docs: https://open-meteo.com/en/docs  (forecast), /en/docs/historical-weather-api, /en/docs/elevation-api
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Sequence

import httpx

from ..config import settings
from .http import UpstreamError, client, get

HOURLY = [
    "temperature_2m", "relative_humidity_2m", "apparent_temperature", "precipitation",
    "precipitation_probability", "uv_index", "wind_speed_10m", "cloud_cover", "shortwave_radiation",
]
DAILY = [
    "temperature_2m_max", "temperature_2m_min", "apparent_temperature_max", "precipitation_sum",
    "precipitation_probability_max", "uv_index_max", "wind_speed_10m_max", "sunrise", "sunset",
]
CURRENT = [
    "temperature_2m", "relative_humidity_2m", "apparent_temperature", "precipitation",
    "uv_index", "wind_speed_10m", "is_day", "weather_code",
]


def _key(params: dict) -> dict:
    if settings.open_meteo_api_key:
        params["apikey"] = settings.open_meteo_api_key
    return params


async def forecast(points: Sequence[tuple[float, float]], *, past_days: int = 1, forecast_days: int = 7) -> list[dict]:
    """Hourly + daily + current weather for many (lat, lon) points in one request.

    Open-Meteo returns a list when several coordinates are passed, a dict for one.
    """
    params = _key({
        "latitude": ",".join(f"{p[0]:.4f}" for p in points),
        "longitude": ",".join(f"{p[1]:.4f}" for p in points),
        "hourly": ",".join(HOURLY), "daily": ",".join(DAILY), "current": ",".join(CURRENT),
        "timezone": settings.timezone, "past_days": past_days, "forecast_days": forecast_days,
    })
    data = await get(f"{settings.open_meteo_url}/forecast", params)
    return data if isinstance(data, list) else [data]


async def recent_history(lat: float, lon: float, days: int = 30) -> dict:
    """Hourly feels-like for the past `days` days. The forecast API serves up to 92 past days
    with the same models as the forecast, which is fresher than the archive (5 day lag)."""
    params = _key({
        "latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}",
        "hourly": "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation",
        "timezone": settings.timezone, "past_days": min(days, 92), "forecast_days": 1,
    })
    return await get(f"{settings.open_meteo_url}/forecast", params)


async def elevation(points: Sequence[tuple[float, float]], *, chunk: int = 100, pause_s: float | None = None,
                    progress_file: Path | None = None) -> list[float]:
    """Copernicus DEM GLO-90 elevation (metres), up to 100 points per request.

    Open-Meteo's free tier counts every coordinate as one call (about 600 a minute, 5,000 an hour), so we
    pace requests, wait out HTTP 429 and keep progress on disk so an interrupted run resumes.
    """
    pause = float(os.environ.get("ELEVATION_PAUSE_S", "12")) if pause_s is None else pause_s
    sig = hashlib.sha1(json.dumps([list(p) for p in points]).encode()).hexdigest()
    out: list[float] = []
    if progress_file and progress_file.exists():
        saved = json.loads(progress_file.read_text())
        if saved.get("sig") == sig:
            out = saved["elevation"]
            if out:
                print(f"  resuming: {len(out)} of {len(points)} points already done")
    url = f"{settings.open_meteo_url}/elevation"
    for i in range(len(out), len(points), chunk):
        part = points[i:i + chunk]
        params = _key({
            "latitude": ",".join(f"{p[0]:.5f}" for p in part),
            "longitude": ",".join(f"{p[1]:.5f}" for p in part),
        })
        for attempt in range(8):
            try:
                r = await client().get(url, params=params)
            except httpx.HTTPError as e:
                await asyncio.sleep(5 * (attempt + 1))
                last = str(e)
                continue
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After") or 0) or 65
                print(f"  Open-Meteo rate limit reached, waiting {wait} s (attempt {attempt + 1} of 8) ...")
                await asyncio.sleep(wait)
                last = "429"
                continue
            if r.status_code >= 500:
                await asyncio.sleep(10 * (attempt + 1))
                last = str(r.status_code)
                continue
            r.raise_for_status()
            out.extend(float(e) if e is not None else float("nan") for e in r.json()["elevation"])
            break
        else:
            raise UpstreamError(f"Open-Meteo elevation kept failing ({last}). Wait an hour and run again; progress is saved.")
        if progress_file:
            progress_file.write_text(json.dumps({"sig": sig, "elevation": out}))
        print(f"  elevation {len(out)}/{len(points)}")
        if len(out) < len(points):
            await asyncio.sleep(pause)
    return out
