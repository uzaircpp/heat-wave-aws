"""Alerts from three real sources, stored so the feed and counts are auditable:

1. Official: SACHET / NDMA CAP feed (IMD, CWC, state authorities), filtered to the city.
2. Forecast rules on Open-Meteo data, using IMD's published thresholds:
   * heat wave when max temperature >= 45 C (IMD absolute criterion), severe >= 47 C
   * rain categories in 24 h: heavy 64.5-115.5 mm, very heavy 115.6-204.4 mm, extremely heavy >= 204.5 mm
   * short burst: >= 30 mm in any 2 hours, which overwhelms drains built for ~25 mm/h
3. Citizen reports: 3+ waterlogging reports within 400 m in the last 2 hours.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..data import city
from ..db import AlertLog, Report, SessionLocal
from ..geo import haversine_m
from ..sources import sachet
from ..sources.http import UpstreamError
from .events import bus
from .weather import weather

log = logging.getLogger("urbanshield.alerts")

SEV_MAP = {"Extreme": "crit", "Severe": "crit", "Moderate": "warn", "Minor": "info", "Unknown": "info"}


def _upsert(s: Session, key: str, **fields) -> tuple[AlertLog, bool]:
    row = s.scalar(select(AlertLog).where(AlertLog.key == key))
    created = row is None
    if created:
        row = AlertLog(key=key, **fields)
        s.add(row)
    else:
        for k, v in fields.items():
            setattr(row, k, v)
    return row, created


def forecast_alerts() -> list[dict]:
    """Rule-based alerts per day from the city forecast grid."""
    if not weather.ready:
        return []
    out = []
    days = weather.grid[0]["daily"]["time"]
    for d_i, day in enumerate(days):
        tmax = max((g["daily"]["temperature_2m_max"][d_i] or -99) for g in weather.grid)
        rain = max((g["daily"]["precipitation_sum"][d_i] or 0) for g in weather.grid)
        if tmax >= 45:
            sev = "crit" if tmax >= 47 else "warn"
            out.append({"key": f"fc:heat:{day}", "kind": "heat", "severity": sev,
                        "title": f"{'Severe heat wave' if sev == 'crit' else 'Heat wave'} likely on {day}",
                        "body": f"Forecast maximum {tmax:.1f} C in parts of {settings.city_name}.", "day": day})
        if rain >= 64.5:
            cat = "Extremely heavy" if rain >= 204.5 else "Very heavy" if rain >= 115.6 else "Heavy"
            out.append({"key": f"fc:rain:{day}", "kind": "rain", "severity": "crit" if rain >= 115.6 else "warn",
                        "title": f"{cat} rain likely on {day}",
                        "body": f"Up to {rain:.0f} mm in 24 hours forecast. Low roads and underpasses may flood.", "day": day})
    # 2-hour bursts in the next 48 hours
    times = weather.times()
    i0 = weather.now_index()
    for k in range(i0, min(i0 + 47, len(times) - 1)):
        burst = max((g["hourly"]["precipitation"][k] or 0) + (g["hourly"]["precipitation"][k + 1] or 0) for g in weather.grid)
        if burst >= 30:
            out.append({"key": f"fc:burst:{times[k]}", "kind": "flood", "severity": "crit" if burst >= 50 else "warn",
                        "title": f"Intense rain burst around {times[k][11:16]} on {times[k][:10]}",
                        "body": f"About {burst:.0f} mm in two hours. Drains will back up in low wards.", "day": times[k][:10]})
            break
    return out


def report_clusters(s: Session, radius_m: float = 400, window_h: float = 2, min_count: int = 3) -> list[dict]:
    since = datetime.now(timezone.utc) - timedelta(hours=window_h)
    rows = s.scalars(select(Report).where(Report.kind == "waterlogging", Report.created_at >= since,
                                          Report.status != "rejected")).all()
    # unconfirmed photos (no water seen by Rekognition) don't count until an officer accepts them
    rows = [r for r in rows if not (r.photo_verified is False and r.status == "new")]
    used, out = set(), []
    for r in rows:
        if r.id in used:
            continue
        group = [x for x in rows if haversine_m(r.lat, r.lon, x.lat, x.lon) <= radius_m]
        verified = sum(1 for x in group if x.photo_verified)
        # a report whose photo Rekognition verified counts double: 2 reports incl. a verified one is enough
        if len(group) >= min_count or (verified and len(group) >= min_count - 1):
            used.update(x.id for x in group)
            name = r.address or r.ward_name or "a street"
            out.append({"key": f"rep:{r.id}", "kind": "flood", "severity": "crit",
                        "title": f"Waterlogging reported near {name}",
                        "body": f"{len(group)} reports in the last {int(window_h)} hours"
                                + (f", {verified} with photos verified by Amazon Rekognition." if verified else "."),
                        "ward_id": r.ward_id, "lat": r.lat, "lon": r.lon})
    return out


async def refresh() -> int:
    """Pull every source and store new alerts. Returns how many are new."""
    official: list[dict] = []
    try:
        official = await sachet.fetch_city_alerts()
    except UpstreamError as e:
        log.warning("SACHET unavailable: %s", e)
    new = 0
    with SessionLocal() as s:
        for a in official:
            row, created = _upsert(
                s, f"sachet:{a['identifier'] or a['link']}", source="sachet",
                kind=_kind_from_event(a["event"] + " " + a["headline"]), severity=SEV_MAP.get(a["severity"], "info"),
                title=a["headline"] or a["event"], body=(a["description"] + " " + a["instruction"]).strip(),
                area=", ".join(a["areas"])[:250], starts_at=_dt(a["effective"]), ends_at=_dt(a["expires"]),
                data={"sender": a["sender"], "link": a.get("link"), "urgency": a["urgency"], "certainty": a["certainty"]},
            )
            new += created
            if created:
                bus.publish("alert", _public(row))
        for a in forecast_alerts():
            row, created = _upsert(s, a["key"], source="forecast", kind=a["kind"], severity=a["severity"],
                                   title=a["title"], body=a["body"], area=settings.city_name,
                                   starts_at=_dt(a["day"] + "T00:00:00+05:30"), ends_at=_dt(a["day"] + "T23:59:00+05:30"),
                                   data={})
            new += created
            if created:
                bus.publish("alert", _public(row))
        for a in report_clusters(s):
            row, created = _upsert(s, a["key"], source="reports", kind=a["kind"], severity=a["severity"],
                                   title=a["title"], body=a["body"], area=a["title"], ward_id=a["ward_id"],
                                   starts_at=datetime.now(timezone.utc), ends_at=datetime.now(timezone.utc) + timedelta(hours=3),
                                   data={"lat": a["lat"], "lon": a["lon"]})
            new += created
            if created:
                bus.publish("alert", _public(row))
        s.commit()
    return new


def active(s: Session, limit: int = 50) -> list[dict]:
    now = datetime.now(timezone.utc)
    rows = s.scalars(select(AlertLog).order_by(AlertLog.created_at.desc()).limit(300)).all()
    live = [r for r in rows if r.ends_at is None or _aware(r.ends_at) >= now]
    order = {"crit": 0, "warn": 1, "info": 2}
    live.sort(key=lambda r: (order.get(r.severity, 3), -_aware(r.created_at).timestamp()))
    return [_public(r) for r in live[:limit]]


def _public(r: AlertLog) -> dict:
    return {"id": r.id, "source": r.source, "kind": r.kind, "severity": r.severity, "title": r.title, "body": r.body,
            "area": r.area, "ward_id": r.ward_id, "starts_at": r.starts_at, "ends_at": r.ends_at,
            "created_at": r.created_at, "data": r.data}


def _kind_from_event(text: str) -> str:
    t = text.lower()
    if "heat" in t or "temperature" in t:
        return "heat"
    if "flood" in t or "inundation" in t:
        return "flood"
    if "rain" in t or "thunder" in t or "storm" in t:
        return "rain"
    return "other"


def _dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def city_ward_count() -> int:
    return len(city().wards)
