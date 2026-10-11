"""Safer routes: real road routes (Amazon Location Service on AWS, OSRM otherwise), scored against
live flood hazards. With Amazon Location we also ask for a route that avoids every hazard outright.

Hazards are (a) open waterlogging reports from citizens in the last 6 hours and (b) underpasses the
flood model expects to be knee-deep or worse for the rain now forecast. Each OSRM alternative gets a
risk score from how close it passes to each hazard; we recommend the lowest-risk route and say why.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import Report
from ..geo import point_line_m
from ..sources import geo
from . import flood
from .weather import weather

DEPTH_WEIGHT = {"ankle": 0.4, "knee": 1.0, "waist": 2.0, "above_waist": 3.0, None: 0.8}
HIT_M = 60  # passing within this distance counts as driving through it


def hazards(s: Session) -> list[dict]:
    since = datetime.now(timezone.utc) - timedelta(hours=6)
    rows = s.scalars(select(Report).where(Report.kind == "waterlogging", Report.created_at >= since,
                                          Report.status.not_in(["resolved", "rejected"]))).all()
    # photos where Amazon Rekognition saw no water wait for an officer before they steer routes
    rows = [r for r in rows if not (r.photo_verified is False and r.status == "new")]
    out = [{"type": "report", "name": r.address or r.ward_name or "Waterlogging", "lat": r.lat, "lon": r.lon,
            "weight": DEPTH_WEIGHT.get(r.depth, 0.8), "detail": f"{r.depth or 'unknown'} deep, reported"} for r in rows]
    if weather.ready:
        try:
            i0 = weather.now_index()
            rain6 = max(sum((g["hourly"]["precipitation"][k] or 0) for k in range(i0, i0 + 6)) for g in weather.grid)
            if rain6 >= 15:
                for u in flood.flood_scenario(rain6, hours=6)["underpasses"]:
                    if u["depth_m"] >= 0.3:
                        out.append({"type": "forecast", "name": u["name"], "lat": u["lat"], "lon": u["lon"],
                                    "weight": DEPTH_WEIGHT[u["label"]] if u["label"] in DEPTH_WEIGHT else 1.0,
                                    "detail": f"expected {u['label']} deep with {rain6:.0f} mm forecast"})
        except flood.NotPrepared:
            pass
    return out


def score(route: dict, hz: list[dict]) -> dict:
    hits, risk = [], 0.0
    for h in hz:
        d = point_line_m(h["lat"], h["lon"], route["coords"])
        if d <= HIT_M:
            hits.append({**h, "distance_m": round(d)})
            risk += h["weight"]
        elif d <= 200:
            risk += h["weight"] * 0.1
    return {**route, "risk": round(risk, 2), "hazards": hits}


async def safe_route(s: Session, a: tuple[float, float], b: tuple[float, float], profile: str = "driving") -> dict:
    alts = await geo.routes(a, b)
    hz = hazards(s)
    scored = [score(r, hz) for r in alts]
    if hz and scored and min(r["risk"] for r in scored) > 0:
        # every normal option passes a hazard: ask Amazon Location for a route around all of them
        around = await geo.routes(a, b, avoid=[(h["lat"], h["lon"]) for h in hz])
        for r in around:
            r = score(r, hz)
            r["avoids_hazards"] = True
            if all(abs(r["distance_m"] - x["distance_m"]) > 50 for x in scored):
                scored.append(r)
    fastest = min(scored, key=lambda r: r["duration_s"])
    safest = min(scored, key=lambda r: (r["risk"], r["duration_s"]))
    return {
        "recommended": safest, "fastest": fastest, "alternatives": scored,
        "hazards_considered": len(hz), "same_route": safest is fastest, "provider": geo.provider(),
        "note": None if safest["risk"] == 0 else "Every route passes near a reported or expected flooded spot. Take extra care.",
    }
