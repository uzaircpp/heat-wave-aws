"""Push alert subscriptions (Web Push). The browser gives us an endpoint and two keys; we keep them with
the ward or rough position (rounded to about 100 m) and the type of work, nothing else."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import push
from ..db import PushSub, get_session, utcnow
from ..services import heat, push_alerts
from .assistant import _limit

router = APIRouter(prefix="/api/push")


class Keys(BaseModel):
    p256dh: str = Field(min_length=40, max_length=200)
    auth: str = Field(min_length=8, max_length=64)


class Subscription(BaseModel):
    endpoint: str = Field(pattern=r"^https://", max_length=1000)
    keys: Keys


class SubIn(BaseModel):
    subscription: Subscription
    ward_id: int | None = None
    lat: float | None = Field(None, ge=-90, le=90)
    lon: float | None = Field(None, ge=-180, le=180)
    role: str = "rider"
    lang: str = Field("en", pattern="^(en|hi)$")


class EndpointIn(BaseModel):
    endpoint: str = Field(max_length=1000)


@router.get("/key")
def key():
    return {"public_key": push.public_key()}


@router.post("/subscribe")
def subscribe(body: SubIn, request: Request, s: Session = Depends(get_session)):
    _limit(request, 30)
    sub = s.scalar(select(PushSub).where(PushSub.endpoint == body.subscription.endpoint))
    if sub is None:
        sub = PushSub(endpoint=body.subscription.endpoint, state={})
        s.add(sub)
    sub.p256dh, sub.auth = body.subscription.keys.p256dh, body.subscription.keys.auth
    sub.ward_id = body.ward_id
    sub.lat = None if body.lat is None else round(body.lat, 3)
    sub.lon = None if body.lon is None else round(body.lon, 3)
    if (sub.role or "rider") != body.role or sub.lat != (sub.state or {}).get("lat"):
        sub.state = {k: v for k, v in (sub.state or {}).items() if k != "heat_cat"}   # re-announce for the new place/work
    sub.role = body.role if body.role in heat.ROLES else "rider"
    sub.lang = body.lang
    sub.active, sub.fails, sub.updated_at = True, 0, utcnow()
    s.commit()
    return {"ok": True, "id": sub.id}


@router.delete("/subscribe")
def unsubscribe(body: EndpointIn, s: Session = Depends(get_session)):
    sub = s.scalar(select(PushSub).where(PushSub.endpoint == body.endpoint))
    if sub:
        s.delete(sub)
        s.commit()
    return {"ok": True}


@router.post("/test")
async def test(body: EndpointIn, request: Request, s: Session = Depends(get_session)):
    """Send this phone one alert now, saying what it would be told about the current situation."""
    _limit(request, 10)
    sub = s.scalar(select(PushSub).where(PushSub.endpoint == body.endpoint, PushSub.active.is_(True)))
    if not sub:
        raise HTTPException(404, "This browser is not subscribed.")
    msg = await push_alerts.status_message(sub)
    try:
        await push.send(sub.endpoint, sub.p256dh, sub.auth, msg, ttl=600, urgency="high")
    except push.Gone:
        sub.active = False
        s.commit()
        raise HTTPException(410, "The browser has dropped this subscription. Turn alerts on again.")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Push service error: {e}")
    return {"ok": True, "message": msg}
