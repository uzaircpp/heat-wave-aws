"""Public read endpoints: wards, weather, safe hours, alerts, places, routes and simulators."""
from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import aws
from ..config import settings
from ..data import Ward, city
from ..db import get_session
from ..services import alerts, flood, heat, notify, routing
from ..services.events import bus
from ..services.weather import city_now, unsafe_hours_history, weather
from ..sources import geo
from ..sources.http import TTLCache, UpstreamError

router = APIRouter(prefix="/api")
_geo_cache = TTLCache("geocode")


def need_weather():
    if not weather.ready:
        raise HTTPException(503, "Weather data is still loading. Try again in a few seconds.")


def resolve_ward(ward_id: int | None, lat: float | None, lon: float | None) -> Ward:
    if ward_id is not None:
        w = city().ward(ward_id)
        if not w:
            raise HTTPException(404, "Unknown ward")
        return w
    if lat is not None and lon is not None:
        return city().nearest_ward(lat, lon)
    raise HTTPException(422, "Pass ward_id, or lat and lon")


@router.get("/health")
def health():
    return {"ok": True, "weather": weather.status(), "data_prepared": city().prepared,
            "places": len(city().places), "wards": len(city().wards)}


@router.get("/meta")
def meta():
    return {
        "city": settings.city_name, "timezone": settings.timezone, "bbox": settings.bbox,
        "aws": aws.status(), "routing_provider": geo.provider(),
        "data_prepared": city().prepared, "channels": notify.channels_available(),
        "roles": {k: v[0] for k, v in heat.ROLES.items()},
        "sources": [
            {"name": "Open-Meteo", "use": "forecast, recent past, elevation", "url": "https://open-meteo.com", "license": "CC BY 4.0"},
            {"name": "OpenStreetMap", "use": "places, underpasses, drains, land cover, routes, addresses",
             "url": "https://www.openstreetmap.org/copyright", "license": "ODbL"},
            {"name": "SACHET / NDMA", "use": "official CAP warnings", "url": "https://sachet.ndma.gov.in"},
            {"name": "DataMeet", "use": "Delhi ward boundaries", "url": "https://github.com/datameet/Municipal_Spatial_Data", "license": "ODbL"},
            {"name": "Copernicus DEM GLO-90", "use": "terrain for the flood model", "url": "https://spacedata.copernicus.eu"},
        ],
    }


# ---------- wards ----------
@router.get("/wards")
def wards_geojson(simplify: float = Query(0.0004, ge=0, le=0.01)):
    return city().geojson(simplify)


@router.get("/wards/attributes")
def wards_attributes():
    """Compact per-ward land cover and terrain, in ward id order (for map layers)."""
    return {"wards": [{"id": w.id, "tree_frac": w.tree_frac, "green_frac": w.green_frac, "built_frac": w.built_frac,
                       "elevation_m": w.elevation_m} for w in city().wards],
            "landcover_source": next((w.landcover_source for w in city().wards if w.landcover_source), None)}


@router.get("/wards/search")
def wards_search(q: str = Query(..., min_length=2)):
    return [w.public() for w in city().search_wards(q)]


@router.get("/wards/locate")
def wards_locate(lat: float, lon: float):
    w = city().ward_at(lat, lon)
    if not w:
        raise HTTPException(404, f"That point is outside {settings.city_name}'s wards")
    return w.public()


# ---------- weather ----------
@router.get("/weather/city")
def weather_city():
    need_weather()
    return weather.city_summary()


@router.get("/weather/ward")
def weather_ward(ward_id: int | None = None, lat: float | None = None, lon: float | None = None,
                 hours: int = Query(48, ge=1, le=168)):
    need_weather()
    w = resolve_ward(ward_id, lat, lon)
    h = weather.ward_hourly(w)
    i0 = weather.now_index()
    return {"ward": w.public(), "now": weather.ward_now(w),
            "hourly": {k: v[i0:i0 + hours] for k, v in h.items()}, "daily": weather.ward_daily(w),
            **weather.status()}


@router.get("/layers/{metric}")
def layer(metric: Literal["feels_like", "rain_6h", "temperature"], offset_h: int = Query(0, ge=0, le=72)):
    need_weather()
    return {"metric": metric, "offset_h": offset_h, "values": weather.city_layer(metric, offset_h), **weather.status()}


@router.get("/forecast")
def forecast(ward_id: int | None = None, lat: float | None = None, lon: float | None = None):
    need_weather()
    w = resolve_ward(ward_id, lat, lon)
    d = weather.ward_daily(w)
    days = []
    today = city_now().strftime("%Y-%m-%d")
    for i, day in enumerate(d["time"]):
        if day < today:  # the feed includes yesterday for history; the forecast starts today
            continue
        tmax, feels, rain = d["temperature_2m_max"][i], d["apparent_temperature_max"][i], d["precipitation_sum"][i] or 0
        risk = "flood" if rain >= 64.5 else "heat" if (tmax or 0) >= 40 or (feels or 0) >= 45 else "moderate" if (feels or 0) >= 40 or rain >= 15 else "low"
        days.append({"date": day, "temp_max": tmax, "temp_min": d["temperature_2m_min"][i], "feels_max": feels,
                     "rain_mm": rain, "rain_chance": d["precipitation_probability_max"][i], "uv_max": d["uv_index_max"][i],
                     "risk": risk})
    return {"ward": w.public(), "days": days, **weather.status()}


@router.get("/safe-hours")
def safe_hours(role: str = "rider", ward_id: int | None = None, lat: float | None = None, lon: float | None = None,
               day_offset: int = Query(0, ge=0, le=6)):
    need_weather()
    w = resolve_ward(ward_id, lat, lon)
    try:
        out = heat.safe_hours(w, role, day_offset)
    except ValueError as e:
        raise HTTPException(422, str(e))
    near = city().places_near(w.lat, w.lon, {"drinking_water", "shelter", "community_centre", "park"}, limit=6) \
        if lat is None else city().places_near(lat, lon, {"drinking_water", "shelter", "community_centre", "park"}, limit=6)
    return {**out, "near": near}


@router.get("/history/unsafe-hours")
async def history_unsafe(ward_id: int | None = None, lat: float | None = None, lon: float | None = None,
                         days: int = Query(30, ge=1, le=92), threshold_c: float = 42.0):
    w = resolve_ward(ward_id, lat, lon)
    try:
        rows = await unsafe_hours_history(w, days, threshold_c)
    except UpstreamError as e:
        raise HTTPException(502, f"Open-Meteo unavailable: {e}")
    return {"ward": w.public(), "threshold_c": threshold_c, "days": rows, "source": "Open-Meteo, past model data"}


# ---------- alerts ----------
@router.get("/alerts")
def list_alerts(s: Session = Depends(get_session), limit: int = Query(30, le=100)):
    return {"alerts": alerts.active(s, limit)}


# ---------- places & routing ----------
@router.get("/places")
def places(lat: float, lon: float, kinds: str = "drinking_water,shelter,community_centre,park",
           limit: int = Query(10, le=50), max_m: float = Query(5000, le=20000)):
    if not city().places:
        raise HTTPException(503, "Places not prepared yet. Run scripts/prepare_data.py")
    return {"places": city().places_near(lat, lon, set(kinds.split(",")), limit, max_m)}


@router.get("/places/city")
def places_city(kinds: str = "drinking_water,shelter,community_centre"):
    """Every mapped point of these kinds in the city (compact), for map layers."""
    want = set(kinds.split(","))
    return {"places": [{"kind": p["kind"], "name": p["name"], "lat": p["lat"], "lon": p["lon"],
                        "opening_hours": p.get("opening_hours")}
                       for p in city().places if p["kind"] in want]}


_ways_cache: dict = {}


@router.get("/waterways")
def waterways(kinds: str = "drain,canal,river", tolerance: float = Query(0.0002, ge=0, le=0.005)):
    """Drains, canals and the Yamuna from OpenStreetMap, simplified for drawing."""
    key = (kinds, tolerance)
    if key not in _ways_cache:
        from shapely.geometry import LineString
        want = set(kinds.split(","))
        out = []
        for w in city().waterways:
            if w["kind"] not in want or len(w["coords"]) < 2:
                continue
            line = LineString(w["coords"]).simplify(tolerance)
            out.append({"kind": w["kind"], "name": w.get("name"),
                        "coords": [[round(x, 5), round(y, 5)] for x, y in line.coords]})
        _ways_cache[key] = {"waterways": out}
    return _ways_cache[key]


@router.get("/underpasses")
def underpasses():
    return {"underpasses": [p for p in city().places if p["kind"] == "underpass"]}


def _pair(v: str) -> tuple[float, float]:
    try:
        a, b = (float(x) for x in v.split(","))
        return a, b
    except ValueError:
        raise HTTPException(422, "Use lat,lon")


@router.get("/route")
async def route(frm: str = Query(..., alias="from"), to: str = Query(...), s: Session = Depends(get_session)):
    try:
        return await routing.safe_route(s, _pair(frm), _pair(to))
    except UpstreamError as e:
        raise HTTPException(502, f"Routing service unavailable: {e}")


@router.get("/geocode")
async def geocode(q: str = Query(..., min_length=3)):
    key = q.lower().strip()
    hit = _geo_cache.get(key, 7 * 86400)
    if hit is not None:
        return {"results": hit}
    try:
        res = await geo.geocode(q)
    except UpstreamError as e:
        raise HTTPException(502, f"Geocoder unavailable: {e}")
    _geo_cache.set(key, res)
    return {"results": res}


@router.get("/reverse")
async def reverse(lat: float, lon: float):
    w = city().ward_at(lat, lon)
    try:
        address = await geo.reverse(lat, lon)
    except UpstreamError:
        address = None
    return {"address": address, "ward": w.public() if w else None}


# ---------- simulators ----------
class FloodIn(BaseModel):
    rain_mm: float = Field(80, ge=0, le=400)
    hours: float = Field(2, gt=0, le=24)
    drains_clean: bool = True
    extra_storage_mm: float = Field(0, ge=0, le=100)


@router.post("/simulate/flood")
def simulate_flood(body: FloodIn):
    try:
        return flood.flood_scenario(body.rain_mm, body.hours, body.drains_clean, body.extra_storage_mm)
    except flood.NotPrepared as e:
        raise HTTPException(409, str(e))


class HeatIn(BaseModel):
    air_temp_c: float = Field(42, ge=25, le=52)
    hour: int = Field(15, ge=0, le=23)
    extra_green_pct: float = Field(0, ge=0, le=50)
    cool_roofs: bool = False


@router.post("/simulate/heat")
def simulate_heat(body: HeatIn):
    need_weather()
    return heat.heat_scenario(body.air_temp_c, body.hour, body.extra_green_pct, body.cool_roofs)


# ---------- live stream ----------
@router.get("/stream")
async def stream(request: Request):
    q = bus.subscribe()

    async def gen():
        try:
            yield "retry: 5000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    yield await asyncio.wait_for(q.get(), timeout=20)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
