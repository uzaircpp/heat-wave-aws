"""UrbanShield API. Run: uvicorn app.main:app --host 0.0.0.0 --port 8000"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from .config import settings
from .data import city
from .db import init_db
from .routers import ai, ops, public, reports
from .services import alerts
from .services.weather import weather
from .sources.http import close_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("urbanshield")


async def _loop(name: str, every_s: int, fn) -> None:
    while True:
        try:
            await fn()
        except Exception as e:  # noqa: BLE001 - keep the loop alive, log and retry
            log.warning("%s failed: %s", name, e)
        await asyncio.sleep(every_s)


async def _weather_then_alerts():
    await weather.refresh()
    await alerts.refresh()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    city()  # load wards once
    tasks = []
    if settings.run_background_jobs:
        tasks.append(asyncio.create_task(_loop("weather", settings.weather_refresh_s, weather.refresh)))
        tasks.append(asyncio.create_task(_loop("alerts", settings.alerts_refresh_s, _weather_then_alerts)))
    yield
    for t in tasks:
        t.cancel()
    await close_client()


app = FastAPI(title="UrbanShield API", version="1.0", lifespan=lifespan,
              description="Heat and flood early warning for Indian cities, built on open data.")
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins), allow_methods=["*"],
                   allow_headers=["*"])
app.include_router(public.router)
app.include_router(reports.router)
app.include_router(ops.router)
app.include_router(ai.router)


@app.get("/api/photos/{name}", include_in_schema=False)
def photo(name: str):
    """Report photos: from S3 (short-lived signed link) on AWS, from local disk otherwise."""
    import re

    from fastapi import HTTPException
    from fastapi.responses import FileResponse, RedirectResponse

    from . import aws
    if not re.fullmatch(r"US-\d{4}-\d{5}\.jpg", name):
        raise HTTPException(404)
    if aws.PHOTO_BUCKET:
        return RedirectResponse(aws.photo_url(name), status_code=302)
    path = settings.upload_dir / name
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg")


if settings.web_dir.exists():
    app.mount("/", StaticFiles(directory=settings.web_dir, html=True), name="web")
