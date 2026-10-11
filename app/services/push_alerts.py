"""Decides which push alert, if any, each subscribed phone should get, and sends it.

Runs every few minutes after the weather refresh. For each subscription it looks at that person's ward
(or rough position) and type of work and picks at most one message per run, most important first:

  official   a new critical official alert (NDMA / IMD) for Delhi
  rest       heat just turned unsafe for their workload: rest now, with the nearest shade and water
  rain       15 mm or more expected in the next 3 hours: avoid the nearest underpass
  resume     heat eased from unsafe: safe to resume with breaks
  heads_up   heat turns unsafe within the hour: plan a break
  sun        strong direct sun where tree cover is low: work in shade

Each kind is throttled per phone, and between 10 PM and 6 AM only official alerts, rest and rain go out.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import push
from ..data import Ward, city
from ..db import AlertLog, PushSub, SessionLocal, utcnow
from . import flood, heat
from .weather import city_now, weather

log = logging.getLogger("urbanshield.push_alerts")

THROTTLE_H = {"official": 0, "rest": 2, "rain": 6, "heads_up": 6, "resume": 2, "sun": 20, "broadcast": 0}
QUIET_OK = {"official", "rest", "rain", "broadcast"}
PRIORITY = ["official", "rest", "rain", "resume", "heads_up", "sun"]


def _m(n: float, hi: bool) -> str:
    if n < 1000:
        return f"{round(n)} मीटर" if hi else f"{round(n)} m"
    return f"{n / 1000:.1f} किमी" if hi else f"{n / 1000:.1f} km"


def _ward(sub: PushSub) -> Ward | None:
    c = city()
    if sub.lat is not None and sub.lon is not None:
        return c.nearest_ward(sub.lat, sub.lon)
    return c.ward(sub.ward_id) if sub.ward_id is not None else None


def _point(sub: PushSub, w: Ward) -> tuple[float, float]:
    return (sub.lat, sub.lon) if sub.lat is not None else (w.lat, w.lon)


def _nearest(lat: float, lon: float, kinds: set[str]) -> dict | None:
    r = city().places_near(lat, lon, kinds, limit=1, max_m=4000)
    return r[0] if r else None


def _hhmm(h: int) -> str:
    return f"{(h % 12) or 12} {'PM' if h >= 12 else 'AM'}"


def candidates(sub: PushSub, w: Ward, s: Session, sh_cache: dict, now: datetime) -> dict[str, dict]:
    """Every message that applies to this phone right now, by kind (before throttling)."""
    hi = sub.lang == "hi"
    lat, lon = _point(sub, w)
    out: dict[str, dict] = {}
    state = sub.state or {}
    url_safe = "/?go=safe"

    # official critical alerts created in the last 2 hours, not yet forwarded to this phone
    seen = set(state.get("seen_alerts", []))
    since = now - timedelta(hours=2)
    for a in s.scalars(select(AlertLog).where(AlertLog.severity == "crit")
                       .order_by(AlertLog.created_at.desc()).limit(10)):
        made = a.created_at if a.created_at.tzinfo else a.created_at.replace(tzinfo=timezone.utc)
        if made < since or a.id in seen or (a.ward_id is not None and a.ward_id != w.id):
            continue
        out["official"] = {"title": a.title[:80], "body": (a.body or a.area or "")[:180], "url": "/?go=dash",
                           "tag": f"alert-{a.id}", "alert_id": a.id, "urgency": "high"}
        break

    if not weather.ready:
        return out
    key = (w.id, sub.role)
    if key not in sh_cache:
        sh_cache[key] = heat.safe_hours(w, sub.role)
    sh = sh_cache[key]
    cur = sh["now"]
    cat = cur["category"] if cur else None
    last_cat = state.get("heat_cat")
    water = _nearest(lat, lon, {"drinking_water"})
    shade = _nearest(lat, lon, {"park", "shelter", "community_centre"})

    if cat == "unsafe" and last_cat != "unsafe":
        if hi:
            body = f"महसूस {round(cur['feels_like'] or cur['temperature'])}°C। {sh['rest_until'] or 'शाम'} तक लगातार काम न करें।"
            if shade:
                body += f" छाया: {shade['name']}, {_m(shade['distance_m'], hi)}।"
            if water:
                body += f" पानी: {water['name']}, {_m(water['distance_m'], hi)}।"
            title = f"अभी आराम करें: {w.name} में खतरनाक गर्मी"
        else:
            body = f"Feels like {round(cur['feels_like'] or cur['temperature'])}°C. Stop continuous work until {sh['rest_until'] or 'evening'}."
            if shade:
                body += f" Shade: {shade['name']}, {_m(shade['distance_m'], hi)}."
            if water:
                body += f" Water: {water['name']}, {_m(water['distance_m'], hi)}."
            title = f"Rest now: unsafe heat in {w.name}"
        place = shade or water
        out["rest"] = {"title": title, "body": body, "url": url_safe, "tag": "heat", "urgency": "high",
                       **({"place": {"name": place["name"], "lat": place["lat"], "lon": place["lon"]}} if place else {})}

    if cat and cat != "unsafe" and last_cat == "unsafe":
        out["resume"] = {"title": "फिर से काम कर सकते हैं" if hi else "Safe to resume work",
                         "body": ("गर्मी कम हुई। हर घंटे 15 मिनट छाया में आराम करें और पानी पीते रहें।" if hi else
                                  "Heat has eased. Rest 15 minutes in shade every hour and keep drinking water."),
                         "url": url_safe, "tag": "heat"}

    if cat in ("safe", "caution") and cur:
        nxt = next((h for h in sh["hours"] if h["hour"] == cur["hour"] + 1), None)
        if nxt and nxt["category"] == "unsafe":
            out["heads_up"] = {"title": f"{_hhmm(nxt['hour'])} से असुरक्षित गर्मी" if hi else f"Heat turns unsafe at {_hhmm(nxt['hour'])}",
                               "body": (f"ब्रेक की योजना बनाएं।" + (f" सबसे पास पानी: {water['name']}, {_m(water['distance_m'], hi)}।" if water else "")) if hi else
                                       ("Plan a break and refill water now." + (f" Nearest water: {water['name']}, {_m(water['distance_m'], hi)}." if water else "")),
                               "url": url_safe, "tag": "heat"}

    # heavy rain in the next 3 hours for this ward
    hourly = weather.ward_hourly(w)
    i0 = weather.now_index()
    rain3 = sum((x or 0) for x in hourly["precipitation"][i0:i0 + 3])
    if rain3 >= 15:
        up = flood.nearest_underpass(lat, lon)
        avoid = ""
        if up and up["distance_m"] < 4000:
            avoid = (f" {up['name']} ({_m(up['distance_m'], hi)}) से बचें।" if hi else f" Avoid {up['name']} ({_m(up['distance_m'], hi)} away).")
        out["rain"] = {"title": f"भारी बारिश: 3 घंटे में {rain3:.0f} मिमी" if hi else f"Heavy rain: {rain3:.0f} mm in the next 3 hours",
                       "body": ("निचले रास्तों और अंडरपास में पानी भर सकता है।" if hi else "Underpasses and low roads may flood.") + avoid
                               + (" ऐप में सुरक्षित रास्ता देखें।" if hi else " Check the safe route in the app."),
                       "url": "/?go=route", "tag": "rain", "urgency": "high"}

    # strong sun with little canopy, while it is caution (unsafe is already covered by "rest")
    sun = hourly["shortwave_radiation"][i0] if i0 < len(hourly["shortwave_radiation"]) else 0
    if cat == "caution" and (sun or 0) >= 700 and (w.tree_frac is not None and w.tree_frac < 0.12):
        out["sun"] = {"title": "तेज़ धूप, यहां पेड़ कम हैं" if hi else "Strong direct sun here",
                      "body": (f"{w.name} में सिर्फ {round(w.tree_frac * 100)}% पेड़। छाया में काम करें।" if hi else
                               f"Only {round(w.tree_frac * 100)}% tree cover in {w.name}. Work in shade where you can")
                              + ((f" {shade['name']}, {_m(shade['distance_m'], hi)}।" if hi else f"; nearest: {shade['name']}, {_m(shade['distance_m'], hi)}.") if shade else ("" if hi else ".")),
                      "url": url_safe, "tag": "sun"}
    out["_cat"] = {"value": cat}
    return out


def pick(sub: PushSub, cands: dict[str, dict], now: datetime, local_hour: int) -> tuple[str, dict] | None:
    state = sub.state or {}
    quiet = local_hour >= 22 or local_hour < 6
    for kind in PRIORITY:
        msg = cands.get(kind)
        if not msg:
            continue
        if quiet and kind not in QUIET_OK:
            continue
        last = state.get("sent", {}).get(kind)
        if last and THROTTLE_H[kind] and now - datetime.fromisoformat(last) < timedelta(hours=THROTTLE_H[kind]):
            continue
        return kind, msg
    return None


async def _deliver(sub: PushSub, kind: str, msg: dict) -> bool:
    payload = {k: v for k, v in msg.items() if k not in {"urgency", "alert_id"}}
    payload["kind"] = kind
    try:
        await push.send(sub.endpoint, sub.p256dh, sub.auth, payload, ttl=3600 if kind != "official" else 6 * 3600,
                        urgency=msg.get("urgency", "normal"))
        sub.fails = 0
        return True
    except push.Gone:
        sub.active = False
        log.info("push subscription %s gone, removed", sub.id)
    except Exception as e:  # noqa: BLE001
        sub.fails = (sub.fails or 0) + 1
        if sub.fails >= 10:
            sub.active = False
        log.warning("push to %s failed: %s", sub.id, e)
    return False


def _record(sub: PushSub, kind: str | None, msg: dict | None, cat: str | None, now: datetime, quiet: bool) -> None:
    """Remember what this phone has been told, so each change in heat level is announced once."""
    st = dict(sub.state or {})
    last = st.get("heat_cat")
    if cat == "unsafe":
        if kind == "rest":
            st["heat_cat"] = "unsafe"
    elif last != "unsafe" or kind == "resume" or quiet or cat is None:
        st["heat_cat"] = cat
    if kind:
        sent = dict(st.get("sent", {}))
        sent[kind] = now.isoformat()
        st["sent"] = sent
        if kind == "official" and msg:
            st["seen_alerts"] = (list(st.get("seen_alerts", [])) + [msg["alert_id"]])[-30:]
    sub.state = st


async def run_once() -> int:
    """Check every active subscription once. Returns how many pushes were sent."""
    now = utcnow()
    local_hour = city_now().hour
    quiet = local_hour >= 22 or local_hour < 6
    sent = 0
    sh_cache: dict = {}
    with SessionLocal() as s:
        subs = s.scalars(select(PushSub).where(PushSub.active.is_(True))).all()
        jobs = []
        for sub in subs:
            w = _ward(sub)
            if not w:
                continue
            try:
                cands = candidates(sub, w, s, sh_cache, now)
            except Exception as e:  # noqa: BLE001 - one bad subscription must not stop the others
                log.warning("push check for %s failed: %s", sub.id, e)
                continue
            cat = cands.pop("_cat", {}).get("value")
            chosen = pick(sub, cands, now, local_hour)
            jobs.append((sub, chosen, cat))
        sem = asyncio.Semaphore(20)

        async def go(sub, chosen):
            async with sem:
                return await _deliver(sub, *chosen)

        results = await asyncio.gather(*(go(sub, c) for sub, c, _ in jobs if c))
        it = iter(results)
        for sub, chosen, cat in jobs:
            ok = next(it) if chosen else False
            if chosen and ok:
                sent += 1
                _record(sub, chosen[0], chosen[1], cat, now, quiet)
            elif not chosen:
                _record(sub, None, None, cat, now, quiet)
        s.commit()
    if sent:
        log.info("sent %d push alerts", sent)
    return sent


async def status_message(sub: PushSub) -> dict:
    """What a test push says: the real situation for this phone right now."""
    w = _ward(sub)
    hi = sub.lang == "hi"
    if not w:
        return {"title": "Raहत", "body": "Alerts are on." if not hi else "चेतावनी चालू है।", "url": "/"}
    with SessionLocal() as s:
        c = candidates(sub, w, s, {}, utcnow())
    c.pop("_cat", None)
    for k in PRIORITY:
        if k in c:
            m = dict(c[k])
            m.pop("alert_id", None)
            m.pop("urgency", None)
            return {**m, "kind": k}
    lat, lon = _point(sub, w)
    water = _nearest(lat, lon, {"drinking_water"})
    if weather.ready:
        sh = heat.safe_hours(w, sub.role)
        cat = sh["now"]["category"] if sh["now"] else None
        when = f"unsafe {sh['unsafe_window'][0]} to {sh['unsafe_window'][1]}" if sh["unsafe_window"] else "no unsafe hours today"
        when_hi = f"असुरक्षित {sh['unsafe_window'][0]} से {sh['unsafe_window'][1]}" if sh["unsafe_window"] else "आज कोई असुरक्षित घंटा नहीं"
    else:
        cat, when, when_hi = None, "forecast loading", "पूर्वानुमान लोड हो रहा है"
    if hi:
        body = f"अभी: {({'safe': 'सुरक्षित', 'caution': 'सावधानी', 'unsafe': 'असुरक्षित'}).get(cat, '—')}। {when_hi}।"
        if water:
            body += f" सबसे पास पानी: {water['name']}, {_m(water['distance_m'], hi)}।"
        return {"title": f"{w.name} के लिए चेतावनी चालू", "body": body, "url": "/?go=safe", "tag": "test", "kind": "test"}
    body = f"Now: {cat or 'outside work hours'}; {when}."
    if water:
        body += f" Nearest water: {water['name']}, {_m(water['distance_m'], hi)}."
    return {"title": f"Alerts are on for {w.name}", "body": body, "url": "/?go=safe", "tag": "test", "kind": "test"}


async def broadcast(s: Session, message: str, message_hi: str | None, ward_id: int | None) -> int:
    """An officer's broadcast, pushed to every phone in that ward (or all phones)."""
    q = select(PushSub).where(PushSub.active.is_(True))
    subs = [x for x in s.scalars(q).all() if ward_id is None or (_ward(x) and _ward(x).id == ward_id)]
    n = 0
    for sub in subs:
        text = message_hi if (sub.lang == "hi" and message_hi) else message
        ok = await _deliver(sub, "broadcast", {"title": "नगर निगम की सूचना" if sub.lang == "hi" else "Message from your ward office",
                                               "body": text[:220], "url": "/?go=dash", "tag": "broadcast",
                                               "urgency": "high"})
        n += ok
    s.commit()
    return n
