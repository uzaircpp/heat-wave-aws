"""Ward officer endpoints. Every request needs `Authorization: Bearer <OFFICER_TOKEN>`."""
from __future__ import annotations

import csv
import hmac
import io
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import Broadcast, Report, ReportEvent, get_session
from ..services import flood, notify
from ..services.events import bus
from .reports import public_report

router = APIRouter(prefix="/api")


def officer(authorization: str = Header("")) -> str:
    if not settings.officer_token:
        raise HTTPException(503, "Officer access is off. Set OFFICER_TOKEN on the server.")
    token = authorization.removeprefix("Bearer ").strip()
    if not hmac.compare_digest(token, settings.officer_token):
        raise HTTPException(401, "Officer token required")
    return "officer"


class ReportPatch(BaseModel):
    status: Literal["new", "assigned", "in_progress", "resolved", "rejected"] | None = None
    assignee: str | None = Field(None, max_length=64)
    severity: Literal["crit", "warn", "info"] | None = None
    note: str | None = Field(None, max_length=500)


@router.patch("/reports/{report_id}")
def update_report(report_id: int, body: ReportPatch, actor: str = Depends(officer), s: Session = Depends(get_session)):
    r = s.get(Report, report_id)
    if not r:
        raise HTTPException(404, "No such report")
    changes = body.model_dump(exclude_none=True)
    if "assignee" in changes and "status" not in changes and r.status == "new":
        changes["status"] = "assigned"
    for k in ("status", "assignee", "severity"):
        if k in changes:
            setattr(r, k, changes[k])
    if changes.get("status") == "resolved":
        r.resolved_at = datetime.now(timezone.utc)
    s.add(ReportEvent(report_id=r.id, actor=actor, action="updated", data=changes))
    s.commit()
    out = public_report(r)
    bus.publish("report", out)
    return out


def _aware(d):
    return d if d is None or d.tzinfo else d.replace(tzinfo=timezone.utc)


@router.get("/ops/summary")
def summary(ward_id: int | None = None, actor: str = Depends(officer), s: Session = Depends(get_session)):
    now = datetime.now(timezone.utc)
    week = now - timedelta(days=7)
    base = select(Report)
    if ward_id is not None:
        base = base.where(Report.ward_id == ward_id)
    open_rows = s.scalars(base.where(Report.status.in_(["new", "assigned", "in_progress"]))).all()
    resolved_week = s.scalars(base.where(Report.resolved_at >= week)).all()
    prev_week = s.scalars(base.where(Report.resolved_at >= week - timedelta(days=7), Report.resolved_at < week)).all()

    def avg_fix(rows):
        hrs = [(_aware(r.resolved_at) - _aware(r.created_at)).total_seconds() / 3600 for r in rows if r.resolved_at]
        return round(sum(hrs) / len(hrs), 1) if hrs else None

    week_rows = s.scalars(base.where(Report.created_at >= week)).all()
    spots = Counter((r.address or r.ward_name or "Unknown") for r in week_rows)
    q_b = select(func.count()).select_from(Broadcast).where(Broadcast.created_at >= week)
    if ward_id is not None:
        q_b = q_b.where(Broadcast.ward_id == ward_id)
    reached = s.scalar(select(func.coalesce(func.sum(Broadcast.delivered), 0)).where(Broadcast.created_at >= week))
    return {
        "open": len(open_rows), "open_severe": sum(r.severity == "crit" for r in open_rows),
        "unassigned": sum(r.assignee is None for r in open_rows),
        "avg_fix_hours": avg_fix(resolved_week), "avg_fix_hours_prev_week": avg_fix(prev_week),
        "resolved_this_week": len(resolved_week),
        "drains_cleared_this_week": sum(r.kind == "drain" for r in resolved_week),
        "broadcasts_this_week": s.scalar(q_b), "messages_delivered_this_week": int(reached or 0),
        "hotspots": [{"place": k, "reports": v} for k, v in spots.most_common(6)],
    }


@router.get("/ops/export.csv")
def export_csv(hours: int = 24 * 30, actor: str = Depends(officer), s: Session = Depends(get_session)):
    rows = s.scalars(select(Report).where(Report.created_at >= datetime.now(timezone.utc) - timedelta(hours=hours))
                     .order_by(Report.created_at.desc())).all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["ref", "kind", "depth", "severity", "status", "assignee", "ward", "address", "lat", "lon",
                "created_at", "resolved_at", "description"])
    for r in rows:
        w.writerow([r.ref, r.kind, r.depth, r.severity, r.status, r.assignee, r.ward_name, r.address, r.lat, r.lon,
                    r.created_at.isoformat(), r.resolved_at.isoformat() if r.resolved_at else "", r.description or ""])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=rahat-reports.csv"})


class BroadcastIn(BaseModel):
    message: str = Field(..., min_length=5, max_length=1000)
    ward_id: int | None = None
    audiences: list[Literal["residents", "commuters", "outdoor_workers"]] = ["residents"]
    channels: list[Literal["app", "sms", "whatsapp"]] = ["app"]
    hindi: bool = True


@router.post("/broadcasts", status_code=201)
async def send_broadcast(body: BroadcastIn, actor: str = Depends(officer), s: Session = Depends(get_session)):
    row = await notify.broadcast(s, actor=actor, message=body.message, ward_id=body.ward_id,
                                 audiences=body.audiences, channels=body.channels, hindi=body.hindi)
    return {"id": row.id, "delivered": row.delivered, "failed": row.failed, "detail": row.detail,
            "message_hi": (row.detail or {}).get("message_hi"),
            "channels_available": notify.channels_available()}


@router.get("/broadcasts")
def list_broadcasts(actor: str = Depends(officer), s: Session = Depends(get_session)):
    rows = s.scalars(select(Broadcast).order_by(Broadcast.created_at.desc()).limit(50)).all()
    return {"broadcasts": [{"id": b.id, "created_at": b.created_at, "message": b.message, "ward_id": b.ward_id,
                            "audiences": b.audiences, "channels": b.channels, "delivered": b.delivered,
                            "failed": b.failed} for b in rows]}


@router.get("/simulate/flood/calibration")
def flood_calibration(rain_mm: float = 80, days: int = 365, actor: str = Depends(officer), s: Session = Depends(get_session)):
    rows = s.scalars(select(Report).where(Report.kind == "waterlogging", Report.status != "rejected",
                                          Report.created_at >= datetime.now(timezone.utc) - timedelta(days=days))).all()
    try:
        return flood.calibration([{"lat": r.lat, "lon": r.lon} for r in rows], rain_mm)
    except flood.NotPrepared as e:
        raise HTTPException(409, str(e))
