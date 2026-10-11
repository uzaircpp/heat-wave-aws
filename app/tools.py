"""Tools over Rahat's live data, shared by the in-app assistant (Bedrock tool use) and the MCP server.

Every tool returns plain JSON built from the same services the website uses, so the assistant can only
talk about real numbers: the forecast, safe working hours, mapped shade and water, alerts, routes and
the flood model. Each tool takes `args` (what the model asked for) and `ctx` (who is asking: their
position, ward and type of work, plus a database session).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from sqlalchemy.orm import Session

from .data import Ward, city
from .services import alerts, flood, heat, routing
from .services.weather import weather
from .sources import geo
from .sources.http import UpstreamError

ROLES = {"rider": "rider", "delivery": "rider", "construction": "construction", "builder": "construction",
         "vendor": "vendor", "street vendor": "vendor"}
SOURCES = {"sachet": "NDMA / IMD official warning", "forecast": "Rahat forecast check (IMD thresholds)",
           "reports": "citizen reports"}
PLACE_KINDS = ["drinking_water", "shelter", "community_centre", "park", "hospital"]


class ToolError(Exception):
    pass


@dataclass
class Ctx:
    lat: float | None = None
    lon: float | None = None
    ward_id: int | None = None
    role: str = "rider"
    session: Session | None = None
    used: list = field(default_factory=list)
    me_lat: float | None = None     # the phone's GPS position, even when the app shows another ward
    me_lon: float | None = None


# ---------- helpers ----------
def _ward(args: dict, ctx: Ctx) -> Ward:
    c = city()
    name = (args.get("ward") or "").strip()
    if name:
        hits = c.search_wards(name, limit=1)
        if hits:
            return hits[0]
        from .names import find_ward
        w = find_ward(name, cutoff=0.8)
        if w:
            return w
        raise ToolError(f"No Delhi ward called '{name}'. Try find_place for streets and landmarks.")
    if args.get("lat") is not None and args.get("lon") is not None:
        return c.nearest_ward(float(args["lat"]), float(args["lon"]))
    if ctx.lat is not None and ctx.lon is not None:
        return c.nearest_ward(ctx.lat, ctx.lon)
    if ctx.ward_id is not None and c.ward(ctx.ward_id):
        return c.ward(ctx.ward_id)
    raise ToolError("I don't know where you are. Share your location in the app or name a ward or place.")


def _point(args: dict, ctx: Ctx) -> tuple[float, float, str]:
    if args.get("lat") is not None and args.get("lon") is not None:
        w = city().ward_at(float(args["lat"]), float(args["lon"]))
        return float(args["lat"]), float(args["lon"]), (args.get("label") or (f"that spot in {w.name}" if w else "that spot"))
    if not args.get("ward") and ctx.me_lat is not None and ctx.me_lon is not None:
        return ctx.me_lat, ctx.me_lon, "you"          # nearest to where the phone actually is
    if not args.get("ward") and ctx.lat is not None and ctx.lon is not None:
        return ctx.lat, ctx.lon, "you"
    w = _ward(args, ctx)
    return w.lat, w.lon, f"the centre of {w.name}"


def _role(args: dict, ctx: Ctx) -> str:
    r = (args.get("work") or args.get("role") or ctx.role or "rider").lower().strip()
    return ROLES.get(r, r if r in heat.ROLES else "rider")


def _need_weather():
    if not weather.ready:
        raise ToolError("Weather data is still loading on the server. Try again in a minute.")


def _hour(h: int) -> str:
    return f"{(h % 12) or 12} {'PM' if h >= 12 else 'AM'}"


# ---------- tools ----------
async def get_conditions(args: dict, ctx: Ctx) -> dict:
    """Weather and heat risk right now for a ward or the user's location."""
    _need_weather()
    w = _ward(args, ctx)
    role = _role(args, ctx)
    now = weather.ward_now(w)
    sh = heat.safe_hours(w, role)
    hourly = weather.ward_hourly(w)
    i0 = weather.now_index()
    rain6 = round(sum((x or 0) for x in hourly["precipitation"][i0:i0 + 6]), 1)
    sun = hourly["shortwave_radiation"][i0] if i0 < len(hourly["shortwave_radiation"]) else None
    return {
        "ward": w.name, "time": now.get("time"),
        "temperature_c": now.get("temperature_2m"), "feels_like_c": now.get("apparent_temperature"),
        "humidity_pct": now.get("relative_humidity_2m"), "uv_index": now.get("uv_index"),
        "wind_kmh": now.get("wind_speed_10m"), "rain_next_6h_mm": rain6,
        "sunshine_w_m2": sun, "direct_sun_strong": bool(sun and sun > 600),
        "tree_cover_pct": None if w.tree_frac is None else round(w.tree_frac * 100),
        "work_type": sh["role_label"],
        "heat_risk_now": sh["now"]["category"] if sh["now"] else "outside working hours (6 AM to 10 PM)",
        "rest_until": sh["rest_until"],
        "unsafe_today": f"{sh['unsafe_window'][0]} to {sh['unsafe_window'][1]}" if sh["unsafe_window"] else None,
        "advice": sh["advice"],
    }


async def get_safe_hours(args: dict, ctx: Ctx) -> dict:
    """Hour-by-hour heat risk (safe, caution, unsafe) for outdoor work today or on a later day."""
    _need_weather()
    w = _ward(args, ctx)
    role = _role(args, ctx)
    day = int(args.get("day_offset") or 0)
    sh = heat.safe_hours(w, role, max(0, min(6, day)))
    return {
        "ward": w.name, "work_type": sh["role_label"], "day_offset": day,
        "rest_until": sh["rest_until"],
        "unsafe_window": f"{sh['unsafe_window'][0]} to {sh['unsafe_window'][1]}" if sh["unsafe_window"] else None,
        "hours": [{"hour": _hour(h["hour"]), "feels_like_c": round(h["feels_like"]), "risk": h["category"]} for h in sh["hours"]],
        "limits": f"caution from WBGT {sh['limits_wbgt']['action_limit']} C, unsafe from {sh['limits_wbgt']['tlv']} C",
        "advice": sh["advice"],
    }


async def find_nearby(args: dict, ctx: Ctx) -> dict:
    """Nearest drinking water, shade (parks), shelters, community centres or hospitals."""
    kinds = args.get("kinds") or ["drinking_water", "park", "shelter"]
    if isinstance(kinds, str):
        kinds = [kinds]
    kinds = [k for k in kinds if k in PLACE_KINDS] or ["drinking_water", "park", "shelter"]
    lat, lon, origin = _point(args, ctx)
    limit = max(1, min(8, int(args.get("limit") or 3)))
    rows = city().places_near(lat, lon, set(kinds), limit=limit, max_m=6000)
    return {"from": origin, "from_point": [lat, lon], "places": [
        {"name": p["name"], "kind": p["kind"], "distance_m": p["distance_m"], "opening_hours": p.get("opening_hours"),
         "lat": p["lat"], "lon": p["lon"]} for p in rows],
        "note": "From OpenStreetMap; check opening hours and access on the spot." if rows else "Nothing mapped within 6 km."}


async def get_alerts(args: dict, ctx: Ctx) -> dict:
    """Active heat, rain and flood alerts for Delhi: official NDMA/IMD, forecast rules, citizen report clusters."""
    if ctx.session is None:
        raise ToolError("Alerts are unavailable right now.")
    rows = alerts.active(ctx.session, 8)
    return {"count": len(rows), "alerts": [
        {"severity": a["severity"], "title": a["title"], "source": SOURCES.get(a["source"], a["source"]), "area": a["area"],
         "details": (a["body"] or "")[:300]} for a in rows]}


async def get_forecast(args: dict, ctx: Ctx) -> dict:
    """7-day forecast for a ward with heat and flood risk labels (IMD thresholds)."""
    _need_weather()
    from .services.weather import city_now
    w = _ward(args, ctx)
    d = weather.ward_daily(w)
    today = city_now().strftime("%Y-%m-%d")
    days = []
    for i, day in enumerate(d["time"]):
        if day < today:
            continue
        tmax, feels, rain = d["temperature_2m_max"][i], d["apparent_temperature_max"][i], d["precipitation_sum"][i] or 0
        risk = "flood" if rain >= 64.5 else "heat" if (tmax or 0) >= 40 or (feels or 0) >= 45 else "moderate" if (feels or 0) >= 40 or rain >= 15 else "low"
        days.append({"date": day, "max_c": tmax, "min_c": d["temperature_2m_min"][i], "feels_like_max_c": feels,
                     "rain_mm": rain, "rain_chance_pct": d["precipitation_probability_max"][i], "risk": risk})
    return {"ward": w.name, "days": days}


async def plan_route(args: dict, ctx: Ctx) -> dict:
    """Road route between two places that avoids reported and expected flooding."""
    if ctx.session is None:
        raise ToolError("Routing is unavailable right now.")

    async def resolve(v: str | None, label: str) -> tuple[float, float, str]:
        v = (v or "").strip()
        if not v or v.lower() in {"me", "my location", "here", "current location", "mere location", "यहां", "यहाँ"}:
            if ctx.me_lat is not None:
                return ctx.me_lat, ctx.me_lon, "My location"
            if ctx.lat is not None:
                return ctx.lat, ctx.lon, "My location"
            w = city().ward(ctx.ward_id) if ctx.ward_id is not None else None
            if w:   # no GPS: start from the ward shown in the app, and say so
                return w.lat, w.lon, f"the centre of {w.name}"
            raise ToolError("I don't know where you are starting. Tap 'Share my location', or say "
                            "'route from <place> to <place>'.")
        if "," in v:
            try:
                a, b = (float(x) for x in v.split(",")[:2])
                return a, b, v
            except ValueError:
                pass
        hits = [w for w in city().search_wards(v, 1)]
        if not hits:
            from .names import find_ward
            fw = find_ward(v, cutoff=0.82)
            hits = [fw] if fw else []
        if hits:
            return hits[0].lat, hits[0].lon, hits[0].name
        try:
            res = await geo.geocode(v)
        except UpstreamError:
            res = []
        if not res:
            raise ToolError(f"I couldn't find '{v}' in Delhi.")
        return res[0]["lat"], res[0]["lon"], res[0]["name"].split(",")[0]

    a = await resolve(args.get("start"), "route")
    b = await resolve(args.get("destination"), "route")
    if args.get("destination_label"):
        b = (b[0], b[1], str(args["destination_label"])[:80])
    try:
        r = await routing.safe_route(ctx.session, (a[0], a[1]), (b[0], b[1]))
    except UpstreamError as e:
        raise ToolError(f"Routing service unavailable: {e}")
    rec = r["recommended"]
    return {
        "from": a[2], "to": b[2], "from_point": [a[0], a[1]], "to_point": [b[0], b[1]],
        "minutes": round(rec["duration_s"] / 60), "km": round(rec["distance_m"] / 1000, 1),
        "via": [x for x in rec["via"] if x][:3],
        "flood_spots_on_route": [h["name"].split(",")[0] for h in rec["hazards"]][:3],
        "avoids_all_flooding": bool(rec.get("avoids_hazards")) or rec["risk"] == 0,
        "fastest_minutes": round(r["fastest"]["duration_s"] / 60), "note": r["note"], "provider": r.get("provider"),
    }


async def simulate_flood(args: dict, ctx: Ctx) -> dict:
    """What-if: which wards and underpasses flood for a given rainfall."""
    rain = float(args.get("rain_mm") or 80)
    hours = float(args.get("hours") or 2)
    clean = bool(args.get("drains_clean", True))
    try:
        r = flood.flood_scenario(max(0, min(400, rain)), max(.5, min(24, hours)), clean)
    except flood.NotPrepared as e:
        raise ToolError(str(e))
    top = sorted(r["wards"], key=lambda x: -x["depth_m"])[:5]
    return {"rain_mm": rain, "hours": hours, "drains_clean": clean,
            "wards_knee_deep_or_worse": r["wards_wet"], "wards_waist_deep": r["wards_unsafe"],
            "deepest_wards": [{"ward": city().ward(x["ward_id"]).name, "depth_m": x["depth_m"]} for x in top if x["depth_m"] > 0],
            "underpasses_unsafe": r["underpasses_unsafe"],
            "worst_underpasses": [{"name": u["name"], "depth_m": u["depth_m"]} for u in r["underpasses"][:3]],
            "method": "screening model from terrain, land cover and drain capacity, not a hydraulic simulation"}


async def find_place(args: dict, ctx: Ctx) -> dict:
    """Look up a Delhi ward, street or landmark and say which ward it is in."""
    q = (args.get("query") or "").strip()
    if len(q) < 2:
        raise ToolError("Say the place to look up.")
    out = [{"name": w.name, "type": "ward", "lat": w.lat, "lon": w.lon} for w in city().search_wards(q, 3)]
    if len(out) < 3:
        try:
            for g in (await geo.geocode(q))[:3]:
                w = city().ward_at(g["lat"], g["lon"])
                out.append({"name": g["name"].split(",")[0], "type": g.get("type") or "place", "lat": g["lat"], "lon": g["lon"],
                            "ward": w.name if w else None})
        except UpstreamError:
            pass
    return {"query": q, "results": out[:5]}


_LOC = {"lat": {"type": "number", "description": "Latitude (optional; defaults to the user's location)"},
        "lon": {"type": "number", "description": "Longitude (optional)"},
        "ward": {"type": "string", "description": "Delhi ward name (optional)"}}
_WORK = {"work": {"type": "string", "enum": ["rider", "construction", "vendor"],
                  "description": "Type of outdoor work: delivery rider (moderate effort), construction (heavy), street vendor (light)"}}

TOOLS: list[dict[str, Any]] = [
    {"name": "get_conditions", "fn": get_conditions, "description": get_conditions.__doc__,
     "schema": {"type": "object", "properties": {**_LOC, **_WORK}}},
    {"name": "get_safe_hours", "fn": get_safe_hours, "description": get_safe_hours.__doc__,
     "schema": {"type": "object", "properties": {**_LOC, **_WORK,
                "day_offset": {"type": "integer", "description": "0 today, 1 tomorrow, up to 6"}}}},
    {"name": "find_nearby", "fn": find_nearby, "description": find_nearby.__doc__,
     "schema": {"type": "object", "properties": {**_LOC,
                "kinds": {"type": "array", "items": {"type": "string", "enum": PLACE_KINDS},
                          "description": "drinking_water for water, park for shade, shelter or community_centre to cool down, hospital"},
                "limit": {"type": "integer", "description": "How many places, 1 to 8"}}}},
    {"name": "get_alerts", "fn": get_alerts, "description": get_alerts.__doc__, "schema": {"type": "object", "properties": {}}},
    {"name": "get_forecast", "fn": get_forecast, "description": get_forecast.__doc__,
     "schema": {"type": "object", "properties": {**_LOC}}},
    {"name": "plan_route", "fn": plan_route, "description": plan_route.__doc__,
     "schema": {"type": "object", "properties": {
         "start": {"type": "string", "description": "Start: 'my location', a place name, or 'lat,lon'"},
         "destination": {"type": "string", "description": "Destination: a place name or 'lat,lon' (for example a place from find_nearby)"},
         "destination_label": {"type": "string", "description": "Optional name to show for a 'lat,lon' destination"}},
         "required": ["destination"]}},
    {"name": "simulate_flood", "fn": simulate_flood, "description": simulate_flood.__doc__,
     "schema": {"type": "object", "properties": {
         "rain_mm": {"type": "number", "description": "Rainfall in mm"}, "hours": {"type": "number"},
         "drains_clean": {"type": "boolean"}}}},
    {"name": "find_place", "fn": find_place, "description": find_place.__doc__,
     "schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
]
BY_NAME: dict[str, Callable[[dict, Ctx], Awaitable[dict]]] = {t["name"]: t["fn"] for t in TOOLS}


async def call(name: str, args: dict | None, ctx: Ctx) -> dict:
    fn = BY_NAME.get(name)
    if not fn:
        return {"error": f"unknown tool {name}"}
    try:
        out = await fn(args or {}, ctx)
    except ToolError as e:
        out = {"error": str(e)}
    ctx.used.append({"tool": name, "args": args or {}, "result": out})
    return out
