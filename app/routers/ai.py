"""AI help for ward officers, on AWS: Amazon Bedrock writes, Amazon Translate translates.

Every answer is grounded in the live numbers this server already has (forecast, safe hours, alerts,
reports, flood model), passed to the model in the prompt. Officers review the text before sending.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import aws
from ..data import city
from ..db import Report, get_session
from ..services import alerts, flood, heat
from ..services.weather import weather
from .ops import officer

router = APIRouter(prefix="/api/ai")

SYSTEM = ("You help municipal ward officers in Delhi, India, warn people about extreme heat and urban flooding. "
          "Use only the facts given. Be specific: name places, times and numbers from the facts. Write plain, "
          "calm, practical language that an outdoor worker can act on. No markdown, no emojis, no headings.")


def _need(feature: str) -> None:
    if not aws.on(feature):
        raise HTTPException(503, f"Amazon {feature.title()} is not switched on for this server.")


def facts(s: Session, ward_id: int) -> str:
    w = city().ward(ward_id)
    if not w:
        raise HTTPException(404, "Unknown ward")
    lines = [f"Ward: {w.name}, Delhi."]
    if w.tree_frac is not None:
        lines.append(f"Tree cover (satellite): {w.tree_frac:.0%}; built-up: {w.built_frac:.0%}.")
    if weather.ready:
        now = weather.ward_now(w)
        lines.append(f"Now: {now.get('temperature_2m')} C, feels-like {now.get('apparent_temperature')} C, "
                     f"humidity {now.get('relative_humidity_2m')}%.")
        d = weather.ward_daily(w)
        for i, day in enumerate(d["time"][:4]):
            lines.append(f"{day}: max {d['temperature_2m_max'][i]} C, feels-like max {d['apparent_temperature_max'][i]} C, "
                         f"rain {d['precipitation_sum'][i]} mm ({d['precipitation_probability_max'][i]}% chance).")
        for role in ("rider", "construction"):
            sh = heat.safe_hours(w, role)
            uw = sh["unsafe_window"]
            lines.append(f"Today for {sh['role_label']}: " + (f"unsafe {uw[0]} to {uw[1]}." if uw else "no unsafe hours."))
    for a in alerts.active(s, 5):
        lines.append(f"Active alert ({a['severity']}, {a['source']}): {a['title']}.")
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    reps = s.scalars(select(Report).where(Report.ward_id == ward_id, Report.created_at >= since)).all()
    if reps:
        lines.append(f"Citizen reports in this ward, last 24 h: {len(reps)}; e.g. " + "; ".join(
            f"{r.kind}{' ' + r.depth if r.depth else ''} at {r.address or 'unknown street'} ({r.status})" for r in reps[:5]) + ".")
    try:
        if weather.ready:
            rain = max(weather.ward_daily(w)["precipitation_sum"][:3] or [0]) or 0
            sim = flood.flood_scenario(max(rain, 60), hours=3, drains_clean=False)
            top = sorted(sim["wards"], key=lambda r: -r["depth_m"])[:3]
            lines.append("Flood model, " + f"{max(rain, 60):.0f} mm with choked drains: deepest wards " +
                         ", ".join(f"{city().ward(r['ward_id']).name} ({r['depth_m']} m)" for r in top) + ".")
    except flood.NotPrepared:
        pass
    return "\n".join(lines)


class TranslateIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)
    target: str = Field("hi", pattern="^(hi|ur|pa|bn)$")


@router.post("/translate")
async def translate(body: TranslateIn, actor: str = Depends(officer)):
    _need("translate")
    return {"text": await asyncio.to_thread(aws.translate, body.text, body.target), "service": "Amazon Translate"}


class DraftIn(BaseModel):
    ward_id: int
    topic: str = Field("auto", pattern="^(auto|heat|flood)$")


@router.post("/draft-alert")
async def draft_alert(body: DraftIn, actor: str = Depends(officer), s: Session = Depends(get_session)):
    _need("bedrock")
    f = facts(s, body.ward_id)
    prompt = (f"Facts:\n{f}\n\nWrite one public alert for residents and outdoor workers of this ward"
              f"{'' if body.topic == 'auto' else ' about ' + body.topic}. Pick the most urgent risk from the facts. "
              "At most 3 short sentences and under 320 characters. Say what, where, when, and one clear action.")
    try:
        en = await asyncio.to_thread(aws.generate, SYSTEM, prompt, 300)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Amazon Bedrock: {e}")
    hi = None
    if aws.on("translate"):
        try:
            hi = await asyncio.to_thread(aws.translate, en, "hi")
        except Exception:  # noqa: BLE001
            hi = None
    return {"en": en, "hi": hi, "model": aws.BEDROCK_MODEL_ID, "facts": f}


@router.get("/ops-brief")
async def ops_brief(ward_id: int, actor: str = Depends(officer), s: Session = Depends(get_session)):
    _need("bedrock")
    f = facts(s, ward_id)
    prompt = (f"Facts:\n{f}\n\nWrite a briefing for the ward officer for the next 72 hours: what to do today, "
              "where to send drain and cooling crews, and when. 4 to 6 short sentences.")
    try:
        text = await asyncio.to_thread(aws.generate, SYSTEM, prompt, 450)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Amazon Bedrock: {e}")
    return {"text": text, "model": aws.BEDROCK_MODEL_ID}
