"""Sending alerts. Every broadcast goes to the live app feed (SSE). SMS and WhatsApp go out through
Twilio when TWILIO_* settings exist; otherwise those channels are reported as not configured.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import aws
from ..config import settings
from ..db import Broadcast, Subscriber
from ..sources.http import UpstreamError, post_form
from .events import bus

log = logging.getLogger("urbanshield.notify")


def channels_available() -> dict:
    return {"app": True, "sms": bool(settings.twilio_sid and settings.twilio_token and settings.twilio_from),
            "whatsapp": bool(settings.twilio_sid and settings.twilio_token and settings.twilio_whatsapp_from)}


async def _twilio(to: str, body: str, whatsapp: bool) -> None:
    url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_sid}/Messages.json"
    sender = settings.twilio_whatsapp_from if whatsapp else settings.twilio_from
    await post_form(url, {"To": f"whatsapp:{to}" if whatsapp else to, "From": sender, "Body": body[:1500]},
                    auth=(settings.twilio_sid, settings.twilio_token), retries=2)


async def broadcast(s: Session, *, actor: str, message: str, ward_id: int | None, audiences: list[str],
                    channels: list[str], hindi: bool = True) -> Broadcast:
    import asyncio

    avail = channels_available()
    detail: dict = {"skipped": [c for c in channels if not avail.get(c)]}
    delivered = failed = 0
    message_hi = None
    if hindi and aws.on("translate"):
        try:
            message_hi = await asyncio.to_thread(aws.translate, message, "hi")
            detail["message_hi"] = message_hi
            detail["translated_by"] = "Amazon Translate"
        except Exception as e:  # noqa: BLE001 - send in English if translation fails
            log.warning("translate failed: %s", e)
    if "app" in channels:
        bus.publish("broadcast", {"message": message, "message_hi": message_hi, "ward_id": ward_id,
                                  "audiences": audiences})
        try:
            from . import push_alerts
            detail["push_delivered"] = await push_alerts.broadcast(s, message, message_hi, ward_id)
        except Exception as e:  # noqa: BLE001 - the app feed already has it
            log.warning("push broadcast failed: %s", e)
    text = message + (f"\n\n{message_hi}" if message_hi else "")
    targets = []
    if ("sms" in channels and avail["sms"]) or ("whatsapp" in channels and avail["whatsapp"]):
        q = select(Subscriber).where(Subscriber.active.is_(True))
        if ward_id is not None:
            q = q.where(Subscriber.ward_id == ward_id)
        subs = s.scalars(q).all()
        targets = [x for x in subs if not audiences or set(x.audiences or []) & set(audiences)]
        for sub in targets:
            use_wa = "whatsapp" in channels and avail["whatsapp"] and sub.whatsapp
            if not use_wa and not ("sms" in channels and avail["sms"]):
                continue
            try:
                await _twilio(sub.phone, text, use_wa)
                delivered += 1
            except UpstreamError as e:
                failed += 1
                log.warning("send to subscriber %s failed: %s", sub.id, e)
    detail["recipients"] = len(targets)
    row = Broadcast(actor=actor, message=message, ward_id=ward_id, audiences=audiences, channels=channels,
                    delivered=delivered, failed=failed, detail=detail)
    s.add(row)
    s.commit()
    return row
