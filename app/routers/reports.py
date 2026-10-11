"""Citizen reports (public) and alert subscriptions."""
from __future__ import annotations

import hashlib
import io
import re
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import aws
from ..config import settings
from ..data import city
from ..db import Report, ReportEvent, Subscriber, get_session
from ..services.events import bus
from ..sources import geo
from ..sources.http import UpstreamError

router = APIRouter(prefix="/api")

KINDS = {"waterlogging", "drain", "heat", "shade_water", "other"}
DEPTHS = {"ankle", "knee", "waist", "above_waist"}
_SALT = secrets.token_hex(16)  # per process; hashes only used to rate limit
MAX_PER_HOUR = 10


def severity_for(kind: str, depth: str | None) -> str:
    if kind == "waterlogging":
        return "crit" if depth in {"waist", "above_waist"} else "warn" if depth == "knee" else "info"
    if kind in {"drain", "heat"}:
        return "warn"
    return "info"


def public_report(r: Report) -> dict:
    return {
        "id": r.id, "ref": r.ref, "kind": r.kind, "depth": r.depth, "description": r.description,
        "lat": r.lat, "lon": r.lon, "ward_id": r.ward_id, "ward_name": r.ward_name, "address": r.address,
        "photo_url": f"/api/photos/{r.photo_path}" if r.photo_path else None, "severity": r.severity,
        "status": r.status, "assignee": r.assignee, "created_at": r.created_at, "updated_at": r.updated_at,
        "resolved_at": r.resolved_at, "photo_verified": r.photo_verified,
        # a waterlogging photo without visible water waits for an officer before it raises alerts or changes routes
        "needs_review": bool(r.photo_verified is False and r.status == "new"),
        "photo_labels": (r.photo_check or {}).get("labels") if r.photo_check else None,
    }


def _jpeg(data: bytes) -> bytes:
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"Photo larger than {settings.max_upload_mb} MB")
    try:
        img = Image.open(io.BytesIO(data))
        img.verify()
        img = Image.open(io.BytesIO(data))
    except (UnidentifiedImageError, OSError):
        raise HTTPException(415, "That file is not a photo we can read")
    img = img.convert("RGB")
    img.thumbnail((1600, 1600))
    buf = io.BytesIO()
    # Re-encoding drops EXIF, including any GPS and device data, before it is stored.
    img.save(buf, "JPEG", quality=82, optimize=True)
    return buf.getvalue()


def _store_photo(jpeg: bytes, ref: str) -> str:
    name = f"{ref}.jpg"
    if aws.PHOTO_BUCKET:
        aws.put_photo(name, jpeg)
    else:
        (settings.upload_dir / name).write_bytes(jpeg)
    return name


async def _check_photo(jpeg: bytes) -> dict | None:
    if not aws.on("rekognition"):
        return None
    import asyncio
    try:
        return await asyncio.to_thread(aws.check_photo, jpeg)
    except Exception as e:  # noqa: BLE001 - a failed check never blocks a report
        import logging
        logging.getLogger("urbanshield.reports").warning("Rekognition check failed: %s", e)
        return None


@router.post("/reports", status_code=201)
async def create_report(
    request: Request,
    kind: str = Form(...), lat: float = Form(...), lon: float = Form(...),
    depth: str | None = Form(None), description: str | None = Form(None, max_length=1000),
    photo: UploadFile | None = File(None), s: Session = Depends(get_session),
):
    if kind not in KINDS:
        raise HTTPException(422, f"kind must be one of {sorted(KINDS)}")
    if depth is not None and depth not in DEPTHS:
        raise HTTPException(422, f"depth must be one of {sorted(DEPTHS)}")
    ward = city().ward_at(lat, lon)
    if not ward:
        raise HTTPException(422, f"That location is outside {settings.city_name}")
    ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else ""))
    rh = hashlib.sha256((_SALT + ip).encode()).hexdigest()
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    recent = s.scalar(select(func.count()).select_from(Report).where(Report.reporter_hash == rh, Report.created_at >= since))
    if recent >= MAX_PER_HOUR:
        raise HTTPException(429, "Too many reports from this connection in the last hour")

    year = datetime.now(timezone.utc).year
    seq = (s.scalar(select(func.max(Report.id))) or 0) + 1
    ref = f"US-{year}-{seq:05d}"
    photo_path = check = None
    if photo and photo.filename:
        jpeg = _jpeg(await photo.read())
        check = await _check_photo(jpeg)
        if check and check["unsafe"]:
            raise HTTPException(422, "This photo can't be accepted. Please send a photo of the problem itself.")
        photo_path = await __import__("asyncio").to_thread(_store_photo, jpeg, ref)
    try:
        address = await geo.reverse(lat, lon)
    except UpstreamError:
        address = None
    desc = re.sub(r"\s+", " ", description).strip() if description else None
    verified = check["water"] if check and kind == "waterlogging" else None
    severity = severity_for(kind, depth)
    if verified is True and severity == "info":
        severity = "warn"          # Rekognition saw water: treat it as confirmed and raise it
    elif verified is False:
        severity = "info"          # photo shows no water: lowest priority until an officer checks it
    r = Report(ref=ref, kind=kind, depth=depth if kind == "waterlogging" else None, description=desc,
               lat=lat, lon=lon, ward_id=ward.id, ward_name=ward.name, address=address, photo_path=photo_path,
               severity=severity, reporter_hash=rh, photo_check=check, photo_verified=verified)
    s.add(r)
    s.flush()
    s.add(ReportEvent(report_id=r.id, actor="citizen", action="created", data={}))
    s.commit()
    out = public_report(r)
    bus.publish("report", out)
    return out


@router.get("/reports")
def list_reports(status: str | None = None, kind: str | None = None, ward_id: int | None = None,
                 severity: str | None = None, hours: int = 72, limit: int = 100, s: Session = Depends(get_session)):
    q = select(Report).where(Report.created_at >= datetime.now(timezone.utc) - timedelta(hours=min(hours, 24 * 90)))
    if status:
        q = q.where(Report.status.in_(status.split(",")))
    if kind:
        q = q.where(Report.kind.in_(kind.split(",")))
    if ward_id is not None:
        q = q.where(Report.ward_id == ward_id)
    if severity:
        q = q.where(Report.severity.in_(severity.split(",")))
    rows = s.scalars(q.order_by(Report.created_at.desc()).limit(min(limit, 500))).all()
    return {"reports": [public_report(r) for r in rows]}


@router.get("/reports/{ref}")
def get_report(ref: str, s: Session = Depends(get_session)):
    r = s.scalar(select(Report).where(Report.ref == ref))
    if not r:
        raise HTTPException(404, "No report with that reference")
    return {**public_report(r), "history": [{"at": e.at, "action": e.action, "data": e.data} for e in r.events]}


# ---------- subscriptions ----------
PHONE = re.compile(r"^\+[1-9]\d{7,14}$")


class SubscribeIn(BaseModel):
    phone: str = Field(..., description="E.164, for example +919812345678")
    lat: float | None = None
    lon: float | None = None
    ward_id: int | None = None
    audiences: list[str] = ["residents"]
    whatsapp: bool = False


@router.post("/subscribe", status_code=201)
def subscribe(body: SubscribeIn, s: Session = Depends(get_session)):
    if not PHONE.match(body.phone):
        raise HTTPException(422, "Phone must be in international format, for example +919812345678")
    ward_id = body.ward_id
    if ward_id is None and body.lat is not None and body.lon is not None:
        w = city().ward_at(body.lat, body.lon)
        ward_id = w.id if w else None
    sub = s.scalar(select(Subscriber).where(Subscriber.phone == body.phone))
    if sub:
        sub.ward_id, sub.audiences, sub.whatsapp, sub.active = ward_id, body.audiences, body.whatsapp, True
    else:
        sub = Subscriber(phone=body.phone, ward_id=ward_id, audiences=body.audiences, whatsapp=body.whatsapp)
        s.add(sub)
    s.commit()
    return {"ok": True, "ward_id": ward_id}


@router.delete("/subscribe")
def unsubscribe(phone: str, s: Session = Depends(get_session)):
    sub = s.scalar(select(Subscriber).where(Subscriber.phone == phone))
    if sub:
        sub.active = False
        s.commit()
    return {"ok": True}
