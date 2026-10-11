"""Rahat as an MCP server: the same live-data tools the in-app assistant uses, for any MCP client
(Claude Desktop, Claude Code, Cursor, an agent on Amazon Bedrock AgentCore...).

Two ways to connect:
  * Remote, streamable HTTP: the API server exposes it at  https://<your-server>/mcp
  * Local, stdio:            python -m app.mcp_server      (loads the data and weather itself)

Tools are read-only and limited to Rahat's data: conditions, safe hours, nearby water and shade,
alerts, forecast, safe routes, the flood what-if and place lookup in Delhi.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Literal

from mcp.server.fastmcp import FastMCP

from . import tools
from .db import SessionLocal

log = logging.getLogger("urbanshield.mcp")

mcp = FastMCP(
    "Rahat",
    instructions=("Live heat and flood safety data for Delhi, India: weather and heat risk per ward, safe hours for "
                  "outdoor work (WBGT against ACGIH limits), nearest drinking water, shade and shelters from "
                  "OpenStreetMap, official and forecast alerts, flood-aware routes and a flood what-if model. "
                  "Locations are Delhi ward names, place names, or lat/lon."),
    host="0.0.0.0",            # served behind the app's own host; no localhost-only rebinding guard
    stateless_http=True,       # every request stands alone, so it scales and survives restarts
    json_response=True,
    streamable_http_path="/mcp",
)

Work = Literal["rider", "construction", "vendor"]
Kind = Literal["drinking_water", "park", "shelter", "community_centre", "hospital"]


async def _run(name: str, args: dict, lat: float | None = None, lon: float | None = None) -> dict:
    args = {k: v for k, v in args.items() if v is not None}
    with SessionLocal() as s:
        ctx = tools.Ctx(lat=lat, lon=lon, session=s, role=args.get("work") or "rider")
        return await tools.call(name, args, ctx)


@mcp.tool()
async def get_conditions(ward: str | None = None, lat: float | None = None, lon: float | None = None,
                         work: Work = "rider") -> dict:
    """Weather and heat risk right now in a Delhi ward or at a point: temperature, feels-like, humidity, UV,
    rain in the next 6 hours, tree cover, and whether outdoor work is safe, caution or unsafe."""
    return await _run("get_conditions", {"ward": ward, "work": work}, lat, lon)


@mcp.tool()
async def get_safe_hours(ward: str | None = None, lat: float | None = None, lon: float | None = None,
                         work: Work = "rider", day_offset: int = 0) -> dict:
    """Hour-by-hour heat risk (6 AM to 10 PM) for outdoor work today (day_offset 0) or up to 6 days ahead,
    with the unsafe window and when to resume."""
    return await _run("get_safe_hours", {"ward": ward, "work": work, "day_offset": day_offset}, lat, lon)


@mcp.tool()
async def find_nearby(ward: str | None = None, lat: float | None = None, lon: float | None = None,
                      kinds: list[Kind] | None = None, limit: int = 3) -> dict:
    """Nearest drinking water, shade (parks), shelters, community centres or hospitals, with distances."""
    return await _run("find_nearby", {"ward": ward, "kinds": kinds, "limit": limit}, lat, lon)


@mcp.tool()
async def get_alerts() -> dict:
    """Active heat, heavy rain and flood alerts for Delhi: official NDMA/IMD warnings, forecast threshold
    alerts and clusters of citizen waterlogging reports."""
    return await _run("get_alerts", {})


@mcp.tool()
async def get_forecast(ward: str | None = None, lat: float | None = None, lon: float | None = None) -> dict:
    """7-day forecast for a Delhi ward with daily heat and flood risk labels (IMD thresholds)."""
    return await _run("get_forecast", {"ward": ward}, lat, lon)


@mcp.tool()
async def plan_route(start: str, destination: str) -> dict:
    """Road route in Delhi that avoids reported and expected flooding. start and destination are place
    names or 'lat,lon'."""
    return await _run("plan_route", {"start": start, "destination": destination})


@mcp.tool()
async def simulate_flood(rain_mm: float = 80, hours: float = 2, drains_clean: bool = True) -> dict:
    """What-if: which Delhi wards and underpasses flood for a given rainfall (screening model)."""
    return await _run("simulate_flood", {"rain_mm": rain_mm, "hours": hours, "drains_clean": drains_clean})


@mcp.tool()
async def find_place(query: str) -> dict:
    """Look up a Delhi ward, street or landmark, with coordinates and the ward it is in."""
    return await _run("find_place", {"query": query})


@mcp.resource("rahat://about")
def about() -> str:
    """What Rahat is and where its data comes from."""
    return ("Rahat (राहत) is heat and flood early warning for Delhi. Weather: Open-Meteo, refreshed every 15 minutes. "
            "Wards: 290 MCD wards. Places: OpenStreetMap. Tree cover: ESA WorldCover 2021. Alerts: NDMA SACHET, "
            "forecast rules on IMD thresholds, citizen reports. Safe hours: WBGT against ACGIH limits per workload.")


http_app = mcp.streamable_http_app()   # created here so the session manager exists for the API's lifespan


async def _stdio() -> None:
    from .data import city
    from .db import init_db
    from .services import alerts
    from .services.weather import weather

    init_db()
    city()
    try:
        await weather.refresh()
        await alerts.refresh()
    except Exception as e:  # noqa: BLE001 - tools report missing weather clearly
        log.warning("start-up data load failed: %s", e)
    await mcp.run_stdio_async()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)   # stderr only: stdout carries the MCP protocol
    asyncio.run(_stdio())
