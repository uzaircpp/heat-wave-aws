"""The Rahat assistant: chat and voice over the app's own live data.

On AWS, Amazon Bedrock (Converse API with tool use) decides which tools to call and writes the answer;
Amazon Polly reads it aloud in Indian English or Hindi. Without AWS, a small rule-based assistant calls
the same tools and answers from templates, so the feature works on a laptop too. Either way every number
comes from the tools in app/tools.py, and questions outside heat, rain, flooding and safety are declined.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections import OrderedDict, defaultdict, deque

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import aws, names, tools
from ..data import city
from ..db import get_session
from ..services.weather import city_now
from ..sources import geo
from ..sources.http import UpstreamError

log = logging.getLogger("urbanshield.assistant")
router = APIRouter(prefix="/api/assistant")

MAX_ROUNDS = 5
SUGGESTIONS = {
    "en": ["Is it safe to work now?", "Nearest drinking water", "When should I rest today?",
           "Any flood alerts?", "Safe route to Connaught Place", "Forecast for this week"],
    "hi": ["क्या अभी काम करना सुरक्षित है?", "सबसे पास पीने का पानी", "आज कब आराम करूं?",
           "कोई बाढ़ चेतावनी?", "कनॉट प्लेस का सुरक्षित रास्ता", "इस हफ्ते का मौसम"],
}


def system_prompt(ctx: tools.Ctx, lang: str) -> str:
    c = city()
    where = "unknown (ask them to share location or name a ward)"
    if ctx.lat is not None:
        w = c.nearest_ward(ctx.lat, ctx.lon)
        where = f"lat {ctx.lat:.5f}, lon {ctx.lon:.5f}, in {w.name} ward"
    elif ctx.ward_id and c.ward(ctx.ward_id):
        where = f"{c.ward(ctx.ward_id).name} ward (chosen in the app)"
    if ctx.me_lat is not None and ctx.lat is None:
        me = c.nearest_ward(ctx.me_lat, ctx.me_lon)
        where += f". Their phone is actually at lat {ctx.me_lat:.5f}, lon {ctx.me_lon:.5f}, in {me.name} (use this for 'my location')"
    reply_lang = "Hindi in Devanagari script, simple everyday words" if lang == "hi" else "English"
    return f"""You are the Rahat (राहत) assistant, a heat and flood safety helper for outdoor workers and residents of Delhi, India.
Current time in Delhi: {city_now().strftime('%A %d %B %Y, %I:%M %p')}.
User location: {where}. Type of work: {tools.heat.ROLES.get(ctx.role, tools.heat.ROLES['rider'])[0]}.

Rules:
- Only answer questions about heat, weather, rain, flooding, safe working hours, water, shade, cooling places, safe routes, alerts, and how to use the Rahat app. For anything else, say briefly that you can only help with heat and flood safety in Delhi.
- Always get facts from the tools. Never guess temperatures, places, distances or times. If a tool returns an error, say so plainly.
- Answers are often read aloud: keep them to 1 to 3 short sentences, give the single most useful action first, name the place and distance. No markdown, lists, emojis or links.
- Distances: under 1000 m say metres, otherwise km with one decimal.
- If someone describes heat illness (confusion, fainting, vomiting, no sweating, very high body temperature) tell them to stop work, move to shade, cool down with water and call 112 or 108 for an ambulance.
- Reply in {reply_lang}."""


# ---------- request limits (protects Bedrock and Polly credits) ----------
_hits: dict[str, deque] = defaultdict(deque)


def _limit(request: Request, per_5min: int) -> None:
    ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else "?")
    q, now = _hits[ip], time.monotonic()
    while q and now - q[0] > 300:
        q.popleft()
    if len(q) >= per_5min:
        raise HTTPException(429, "Too many requests. Wait a minute and try again.")
    q.append(now)


class Msg(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    text: str = Field(max_length=2000)


class ChatIn(BaseModel):
    messages: list[Msg] = Field(min_length=1, max_length=20)
    lat: float | None = Field(None, ge=-90, le=90)
    lon: float | None = Field(None, ge=-180, le=180)
    ward_id: int | None = None
    role: str = "rider"
    lang: str = Field("en", pattern="^(en|hi)$")
    my_lat: float | None = Field(None, ge=-90, le=90)    # GPS, sent even when another ward is shown
    my_lon: float | None = Field(None, ge=-180, le=180)


@router.get("/info")
def info():
    return {"provider": "Amazon Bedrock" if aws.on("bedrock") else "offline",
            "model": aws.BEDROCK_MODEL_ID if aws.on("bedrock") else None,
            "voice": "Amazon Polly (Kajal)" if aws.on("polly") else "browser",
            "tools": [{"name": t["name"], "description": t["description"]} for t in tools.TOOLS],
            "suggestions": SUGGESTIONS}


@router.post("/chat")
async def chat(body: ChatIn, request: Request, s: Session = Depends(get_session)):
    _limit(request, 40)
    ctx = tools.Ctx(lat=body.lat, lon=body.lon, ward_id=body.ward_id, me_lat=body.my_lat, me_lon=body.my_lon,
                    role=body.role if body.role in tools.heat.ROLES else "rider", session=s)
    provider = "offline"
    reply = None
    if aws.on("bedrock"):
        try:
            reply = await _bedrock(body, ctx)
            provider = "Amazon Bedrock"
        except Exception as e:  # noqa: BLE001 - fall back to the offline assistant, never fail the chat
            log.warning("Bedrock chat failed, using offline assistant: %s", e)
            ctx.used.clear()
    if reply is None:
        reply = await offline(body.messages[-1].text, ctx, body.lang)
    acts = actions(ctx)
    if TAKE_ME.search(body.messages[-1].text.lower()):
        for a in acts:
            if a["type"] == "route":
                a["auto"] = True      # the app opens the map with this route straight away
                break
    return {"reply": reply, "lang": body.lang, "provider": provider,
            "tools_used": [u["tool"] for u in ctx.used], "actions": acts}


def _history(msgs: list[Msg]) -> list[dict]:
    out: list[dict] = []
    for m in msgs[-12:]:
        t = m.text.strip()
        if not t:
            continue
        if not out and m.role != "user":
            continue
        if out and out[-1]["role"] == m.role:
            out[-1]["content"][0]["text"] += "\n" + t
        else:
            out.append({"role": m.role, "content": [{"text": t}]})
    if not out or out[-1]["role"] != "user":
        raise HTTPException(422, "The last message must be from the user.")
    return out


async def _bedrock(body: ChatIn, ctx: tools.Ctx) -> str:
    system = system_prompt(ctx, body.lang)
    messages = _history(body.messages)
    for _ in range(MAX_ROUNDS):
        res = await asyncio.to_thread(aws.converse, system, messages, tools.TOOLS)
        msg = res["output"]["message"]
        messages.append(msg)
        uses = [c["toolUse"] for c in msg["content"] if "toolUse" in c]
        if res.get("stopReason") != "tool_use" or not uses:
            return "".join(c.get("text", "") for c in msg["content"]).strip() or "Sorry, I have no answer for that."
        results = []
        for u in uses:
            out = await tools.call(u["name"], u.get("input") or {}, ctx)
            results.append({"toolResult": {"toolUseId": u["toolUseId"], "content": [{"json": _jsonable(out)}],
                                           **({"status": "error"} if "error" in out else {})}})
        messages.append({"role": "user", "content": results})
    raise RuntimeError("tool loop did not finish")


def _jsonable(v):
    return json.loads(json.dumps(v, default=str))


def actions(ctx: tools.Ctx) -> list[dict]:
    """Buttons under the answer that open the matching screen of the app."""
    out: list[dict] = []
    for u in ctx.used:
        r = u["result"]
        if "error" in r:
            continue
        if u["tool"] == "find_nearby" and r.get("places"):
            p = r["places"][0]
            if not any(x["tool"] == "plan_route" for x in ctx.used):
                start = "My location" if r["from"] == "you" else r["from"][:1].upper() + r["from"][1:]
                out.append({"type": "route", "label": f"Route to {p['name']}", "from": r["from_point"],
                            "to": [p["lat"], p["lon"]], "from_name": start, "to_name": p["name"]})
            out.append({"type": "map", "label": f"Show {p['name']} on map", "lat": p["lat"], "lon": p["lon"], "name": p["name"]})
            out.append({"type": "directions", "label": "Directions",
                        "url": f"https://www.google.com/maps/dir/?api=1&destination={p['lat']},{p['lon']}&travelmode=walking"})
        elif u["tool"] == "plan_route":
            out.insert(0, {"type": "route", "label": f"Route to {r['to']} on map", "from": r["from_point"], "to": r["to_point"],
                        "from_name": r["from"], "to_name": r["to"]})
        elif u["tool"] in {"get_safe_hours", "get_conditions"}:
            out.append({"type": "screen", "label": "Open safe hours", "screen": "safe"})
        elif u["tool"] == "get_alerts" and r.get("count"):
            out.append({"type": "screen", "label": "See all alerts", "screen": "dash"})
        elif u["tool"] == "simulate_flood":
            out.append({"type": "screen", "label": "Open flood simulator", "screen": "sim"})
    no_gps = ctx.me_lat is None and ctx.lat is None
    if no_gps and any(u["tool"] in {"plan_route", "find_nearby"} for u in ctx.used):
        out.insert(0, {"type": "locate", "label": "Share my location"})
    seen, uniq = set(), []
    for a in out:
        k = (a["type"], a["label"])
        if k not in seen:
            seen.add(k)
            uniq.append(a)
    return uniq[:3]


# ---------- offline assistant (no AWS needed) ----------
def _m(n) -> str:
    return f"{n} m" if n < 1000 else f"{n / 1000:.1f} km"


_HI_WORK = {"Delivery rider": "डिलीवरी राइडर", "Construction": "निर्माण मज़दूर", "Street vendor": "रेहड़ी-पटरी विक्रेता"}
_HI_RISK = {"safe": "सुरक्षित", "caution": "सावधानी", "unsafe": "असुरक्षित"}
_HI_KIND = {"drinking_water": "पीने का पानी", "park": "छाया (पार्क)", "shelter": "आश्रय", "community_centre": "सामुदायिक केंद्र",
            "hospital": "अस्पताल"}
_EN_KIND = {"drinking_water": "drinking water", "park": "shade (park)", "shelter": "shelter",
            "community_centre": "community centre", "hospital": "hospital"}
OFF_TOPIC = {"en": "I can only help with heat, rain and flood safety in Delhi: try asking if it is safe to work now, "
                   "where the nearest water is, or for a safe route.",
             "hi": "मैं सिर्फ दिल्ली में गर्मी, बारिश और बाढ़ से सुरक्षा में मदद कर सकता हूं। पूछिए: क्या अभी काम करना सुरक्षित है, "
                   "सबसे पास पानी कहां है, या सुरक्षित रास्ता।"}

def _p(latin: str, deva: str = "") -> str:
    return rf"\b(?:{latin})\b" + (f"|{deva}" if deva else "")


TAKE_ME = re.compile(_p(r"take me|route|routes|directions?|navigate|guide me|show me the way|how (?:do i|to) (?:get|go|reach)|"
                        r"le chalo|le chal|rasta|raasta|kaise jaun|kaise jau", "ले चलो|ले चल|रास्ता|रास्ते|कैसे जाऊं|कैसे जाएं|पहुंचाओ"))

NEAREST = re.compile(_p(r"nearest|nearby|closest|near me|around me|paas|pass|najdik|nazdeek|kareeb|qareeb", "पास|नज़दीक|नजदीक|करीब"))

_KW = {
    "emergency": _p(r"faint\w*|dizzy|vomit\w*|collaps\w*|unconscious|heat ?stroke|chakkar", "बेहोश|चक्कर|उल्टी|लू लग"),
    "nearby": _p(r"water|drink\w*|thirst\w*|shade|shadow|trees?|parks?|cool\w*|shelters?|hospitals?|toilets?|pani|paani|chhaya|chaya",
                 "पानी|छाया|आश्रय|अस्पताल|ठंड"),
    "route": _p(r"route|way to|go to|reach|directions?|road to|rasta|raasta", "रास्ता|रास्ते|जाना|जाऊं|पहुंच"),
    "flood_sim": r"(\d+)\s*mm|मिमी|" + _p(r"what if|simulat\w*"),
    "alerts": _p(r"alerts?|warnings?|floods?|flooding|waterlog\w*|underpass\w*|chetavani", "चेतावनी|बाढ़|जलभराव"),
    "forecast": _p(r"forecast|tomorrow|week|next days|kal|hafte", "कल|हफ्ते"),
    "safe": _p(r"safe|work\w*|hours?|break|rest|when|kaam|aaram|surakshit", "काम|आराम|सुरक्षित|कब"),
    "now": _p(r"weather|temp\w*|hot|heat|humid\w*|sun|sunny|uv|garmi|dhoop|mausam|mosam|tapman|taapmaan", "गर्मी|धूप|तापमान|गरम|मौसम"),
    "app": _p(r"how (?:do|to) (?:i )?(?:use|report)|report\w*|app|kaise", "कैसे|रिपोर्ट"),
}


def intent(text: str) -> str:
    t = text.lower()
    for name, pat in _KW.items():
        if re.search(pat, t):
            return name
    return "none"


_FILLER = re.compile(r"(?i)^(?:the|a|an|my|please|pls|me)\s+|\s+(?:please|pls|now|today|right now|by road|by bike|"
                     r"route|rasta|raasta|ka rasta|tak|jana hai|jaana hai|kaise jaun|kaise jaayein)$")


def _clean(p: str) -> str:
    p = p.strip(" ?.!।,")
    for _ in range(3):
        p = _FILLER.sub("", p).strip(" ?.!।,")
    p = re.sub(r"\s+(?:तक|का|के|की|को|से|जाना|है|कैसे|रास्ता|जाऊं|जाएं)$", "", p).strip()
    return p


def route_ends(text: str) -> tuple[str, str]:
    """(start, destination) from things like 'route to X', 'from X to Y', 'between X and Y', 'X se Y',
    'X से Y तक'. Start defaults to 'my location'."""
    t = text.strip().rstrip("?.!। ")
    pats = [
        (r"(?i)\bbetween\s+(.+?)\s+(?:and|&)\s+(.+)$", False),
        (r"(?i)\bfrom\s+(.+?)\s+(?:to|till|towards)\s+(.+)$", False),
        (r"(?i).*\b(?:to|till|towards)\s+(.+?)\s+from\s+(.+)$", True),
        (r"(?i)(?:^|\s)([a-z][a-z .'-]+?)\s+se\s+([a-z][a-z .'-]+?)(?:\s+(?:tak|ka|ke|ki|jana|jaana|kaise)\b.*)?$", False),
        (r"(\S+(?:\s\S+){0,2})\s+से\s+(\S+(?:\s\S+){0,2}?)(?:\s+(?:तक|का|के|की|जाना|कैसे)(?:\s.*)?)?$", False),
    ]
    for pat, swap in pats:
        m = re.search(pat, t)
        if m:
            x, y = _clean(m.group(1)), _clean(m.group(2))
            x = re.sub(r"(?i)^.*\b(?:route|rasta|way|path)\s+", "", x)   # "safest route Zakir Nagar" -> "Zakir Nagar"
            if x and y:
                return (y, x) if swap else (x, y)
    found = names.find_wards(t)
    if len(found) >= 2:                      # "Zakir Nagar to Karol Bagh route"
        return found[0].name, found[1].name
    m = re.search(r"(?i).*\b(?:to|till|tak)\s+(.+)$", t)
    if m:
        return "my location", _clean(m.group(1))
    if len(found) == 1:
        return "my location", found[0].name
    m = re.search(r"(.+?)\s+(?:का|के लिए|तक|को|कैसे)\s", t + " ")
    if m:
        return "my location", _clean(re.sub(r"^(मुझे|मैं|से)\s+", "", m.group(1)))
    return "my location", _clean(re.sub(r"(?i).*\b(route|rasta|way)\s*", "", t))


_NOT_PLACES = {"the", "my", "me", "here", "my area", "my location", "this ward", "my ward", "work", "today", "tomorrow",
               "delhi", "new delhi", "the city", "shade", "water", "rain", "heat", "sun", "this week", "next week"}


async def mentioned_place(text: str) -> dict:
    """A ward or place named in the question ("in Saket", "Rohini mein"), as tool arguments. {} means the
    user's own location, which the tools use by default."""
    best = names.find_ward(text)      # English, Hinglish or Hindi script, small spelling slips ok
    if best:
        return {"ward": best.name}
    m = re.search(r"(?i)\b(?:in|at|near|for|around|of)\s+([a-z][a-z .'-]{2,40}?)\s*(?:\b(?:today|tomorrow|now|right now|this week|ward|area|mein|me)\b|[?.!,]|$)", text.strip())
    m = m or re.search(r"([^\s]+(?:\s[^\s]+)?)\s+(?:में|मे|का|की|के|mein|ka|ki|ke)(?:\s|$)", text.strip())
    if not m:
        return {}
    q = m.group(1).strip(" .'-")
    if q.lower() in _NOT_PLACES or len(q) < 3:
        return {}
    hits = city().search_wards(q, 1)
    if hits:
        return {"ward": hits[0].name}
    try:
        res = await geo.geocode(q)
    except UpstreamError:
        res = []
    if res:
        return {"lat": res[0]["lat"], "lon": res[0]["lon"]}
    return {}


async def offline(text: str, ctx: tools.Ctx, lang: str) -> str:
    hi = lang == "hi"
    kind = intent(text)
    if kind == "nearby" and TAKE_ME.search(text.lower()) and not NEAREST.search(text.lower()):
        kind = "route"     # "route to Lodhi Garden park" is a named place, not "the nearest park"
    if kind == "emergency":
        return ("तुरंत काम रोकें, छाया में जाएं, पानी से शरीर ठंडा करें और 112 या 108 पर एम्बुलेंस बुलाएं।" if hi else
                "Stop work now, move into shade, cool the body with water and call 112 or 108 for an ambulance.")
    if kind == "none":
        return OFF_TOPIC[lang]
    if kind == "app":
        return ("जलभराव या गर्मी की समस्या दिखे तो 'Report' में फोटो के साथ भेजें। 'Safe hours' में आज के सुरक्षित घंटे देखें।" if hi else
                "To report waterlogging or a heat problem, open Report and send a photo with your location. "
                "Safe hours shows when it is safe to work today, and alerts arrive on this phone if you turn them on.")
    where = {} if kind in ("route", "flood_sim", "alerts") else await mentioned_place(text)
    if kind == "nearby":
        t = text.lower()
        kinds = (["drinking_water"] if re.search(_p(r"water|drink\w*|thirst\w*|pani|paani", "पानी"), t) else []) + \
                (["park"] if re.search(_p(r"shade|shadow|trees?|parks?|chhaya|chaya", "छाया"), t) else []) + \
                (["hospital"] if re.search(_p(r"hospitals?", "अस्पताल"), t) else []) + \
                (["shelter", "community_centre"] if re.search(_p(r"shelters?|cool\w*|rest", "आश्रय|ठंड"), t) else [])
        r = await tools.call("find_nearby", {**where, "kinds": kinds or ["drinking_water", "park", "shelter"], "limit": 3}, ctx)
        if "error" in r:
            return r["error"]
        if not r["places"]:
            return "6 किलोमीटर के अंदर कुछ नहीं मिला।" if hi else f"Nothing like that is mapped within 6 km of {r['from']}."
        p = r["places"][0]
        if TAKE_ME.search(t):
            o = r["from_point"]
            rt = await tools.call("plan_route", {"start": "my location" if r["from"] == "you" else f"{o[0]},{o[1]}",
                                                 "destination": f"{p['lat']},{p['lon']}", "destination_label": p["name"]}, ctx)
            if "error" not in rt:
                kind_en, kind_hi = _EN_KIND.get(p["kind"], p["kind"]), _HI_KIND.get(p["kind"], p["kind"])
                if hi:
                    return (f"सबसे पास {kind_hi}: {p['name']}, {_m(p['distance_m'])} दूर। रास्ता: लगभग {rt['minutes']} मिनट"
                            + (f", {', '.join(rt['via'][:1])} से होकर" if rt["via"] else "") + "। नक्शे पर रास्ता दिखा रहा हूं।")
                return (f"Nearest {kind_en}: {p['name']}, {_m(p['distance_m'])} away. About {rt['minutes']} minutes"
                        + (f" via {', '.join(rt['via'][:2])}" if rt["via"] else "") + ". Showing the route on the map.")
        if hi:
            more = "; ".join(f"{x['name']} {_m(x['distance_m'])}" for x in r["places"][1:])
            return f"सबसे पास {_HI_KIND.get(p['kind'], p['kind'])}: {p['name']}, {_m(p['distance_m'])} दूर।" + (f" और: {more}।" if more else "")
        more = "; ".join(f"{x['name']} ({_EN_KIND.get(x['kind'], x['kind'])}, {_m(x['distance_m'])})" for x in r["places"][1:])
        to = f" to {r['from']}" if where else ""
        return f"Nearest {_EN_KIND.get(p['kind'], p['kind'])}{to}: {p['name']}, {_m(p['distance_m'])} away." + (f" Also: {more}." if more else "")
    if kind == "route":
        start, dest = route_ends(text)
        r = await tools.call("plan_route", {"start": start, "destination": dest}, ctx)
        if "error" in r:
            return r["error"]
        via = f" via {', '.join(r['via'][:2])}" if r["via"] else ""
        if r["from"].startswith("the centre of"):
            via += f", starting from {r['from']} (share your location for a route from where you are)"
        if hi:
            safe = "रास्ते में कोई जलभराव नहीं।" if r["avoids_all_flooding"] else f"ध्यान दें: {', '.join(r['flood_spots_on_route'])} पर पानी हो सकता है।"
            start = "" if r["from"] == "My location" or r["from"].startswith("the centre of") else f"{r['from']} से "
            return f"{start}{r['to']} तक सुरक्षित रास्ता: {r['km']} किमी, लगभग {r['minutes']} मिनट। {safe}"
        safe = "No flooding reported on the way." if r["avoids_all_flooding"] else f"Careful: water expected near {', '.join(r['flood_spots_on_route'])}."
        start = "" if r["from"] in ("My location",) or r["from"].startswith("the centre of") else f" from {r['from']}"
        faster = (f" The fastest road takes {r['fastest_minutes']} minutes but passes flooding, so this safer one is recommended."
                  if r["fastest_minutes"] < r["minutes"] else "")
        return f"Safest route to {r['to']}{start}: {r['km']} km, about {r['minutes']} minutes{via}. {safe}{faster}"
    if kind == "flood_sim":
        mm = re.search(r"(\d+)\s*mm", text)
        r = await tools.call("simulate_flood", {"rain_mm": float(mm.group(1)) if mm else 80}, ctx)
        if "error" in r:
            return r["error"]
        worst = ", ".join(u["name"] for u in r["worst_underpasses"][:2])
        return (f"{r['rain_mm']:.0f} मिमी बारिश में {r['wards_knee_deep_or_worse']} वार्डों में घुटने तक पानी, {r['underpasses_unsafe']} अंडरपास असुरक्षित।" if hi else
                f"With {r['rain_mm']:.0f} mm in {r['hours']:.0f} hours, about {r['wards_knee_deep_or_worse']} wards get knee-deep water "
                f"and {r['underpasses_unsafe']} underpass{'es' if r['underpasses_unsafe'] != 1 else ''} become{'s' if r['underpasses_unsafe'] == 1 else ''} unsafe" + (f", worst at {worst}." if worst else "."))
    if kind == "alerts":
        r = await tools.call("get_alerts", {}, ctx)
        if "error" in r:
            return r["error"]
        if not r["count"]:
            return "अभी दिल्ली के लिए कोई चेतावनी नहीं है।" if hi else "There are no active heat or flood alerts for Delhi right now."
        a = r["alerts"][0]
        return (f"{r['count']} चेतावनी सक्रिय। सबसे गंभीर: {a['title']}।" if hi else
                f"{r['count']} active alert{'s' if r['count'] > 1 else ''}. Most serious: {a['title']}, from {a['source']}.")
    if kind == "forecast":
        r = await tools.call("get_forecast", where, ctx)
        if "error" in r:
            return r["error"]
        days = r["days"][:7]
        if not days:
            return "Forecast unavailable."
        hot = max(days, key=lambda d: d["feels_like_max_c"] or 0)
        wet = max(days, key=lambda d: d["rain_mm"] or 0)
        d1 = days[1] if len(days) > 1 else days[0]
        if hi:
            return (f"{r['ward']}: कल अधिकतम {d1['max_c']:.0f}°C, महसूस {d1['feels_like_max_c']:.0f}°C। "
                    f"सबसे गर्म दिन {hot['date']}" + (f", सबसे ज़्यादा बारिश {wet['date']} ({wet['rain_mm']:.0f} मिमी)।" if wet["rain_mm"] >= 5 else "।"))
        return (f"In {r['ward']}, tomorrow peaks at {d1['max_c']:.0f}°C, feeling like {d1['feels_like_max_c']:.0f}°C. "
                f"Hottest day this week is {hot['date']}" + (f"; most rain on {wet['date']} ({wet['rain_mm']:.0f} mm)." if wet["rain_mm"] >= 5 else ", with little rain."))
    # "safe" and "now"
    tomorrow = re.search(r"tomorrow|kal|कल", text.lower())
    if kind == "safe" or tomorrow:
        r = await tools.call("get_safe_hours", {**where, "day_offset": 1 if tomorrow else 0}, ctx)
        if "error" in r:
            return r["error"]
        if hi:
            win = (f"असुरक्षित समय {r['unsafe_window'].replace(' to ', ' से ')} तक।" if r["unsafe_window"] else "आज कोई असुरक्षित घंटा नहीं।")
            rest = f" अभी आराम करें, {r['rest_until']} तक। छाया में रहें और पानी पीते रहें।" if r["rest_until"] else ""
            day = "कल" if tomorrow else "आज"
            return f"{day} {r['ward']} में {_HI_WORK.get(r['work_type'], r['work_type'])} के लिए {win}{rest}"
        win = f"unsafe from {r['unsafe_window']}" if r["unsafe_window"] else "no unsafe hours"
        rest = f" Rest now until {r['rest_until']}." if r["rest_until"] else ""
        day = "Tomorrow" if tomorrow else "Today"
        return f"{day} in {r['ward']} for {r['work_type'].lower()} work: {win}.{rest} {r['advice'] or ''}".strip()
    r = await tools.call("get_conditions", where, ctx)
    if "error" in r:
        return r["error"]
    risk = r["heat_risk_now"]
    if hi:
        return (f"{r['ward']} में अभी {r['temperature_c']:.0f}°C, महसूस {r['feels_like_c']:.0f}°C। जोखिम: {_HI_RISK.get(risk, risk)}।"
                + (f" {r['rest_until']} तक आराम करें।" if r["rest_until"] else ""))
    return (f"It is {r['temperature_c']:.0f}°C in {r['ward']}, feeling like {r['feels_like_c']:.0f}°C. Heat risk for your work: {risk}."
            + (f" Rest until {r['rest_until']}." if r["rest_until"] else f" {r['advice'] or ''}")).strip()


# ---------- voice ----------
class SpeakIn(BaseModel):
    text: str = Field(min_length=1, max_length=1500)
    lang: str = Field("en", pattern="^(en|hi)$")


_audio: OrderedDict[str, bytes] = OrderedDict()


@router.post("/speak")
async def speak(body: SpeakIn, request: Request):
    """MP3 from Amazon Polly. 204 when Polly is off, and the browser's own voice reads the text instead."""
    if not aws.on("polly"):
        return Response(status_code=204)
    _limit(request, 60)
    key = hashlib.sha1(f"{body.lang}:{body.text}".encode()).hexdigest()
    if key not in _audio:
        try:
            _audio[key] = await asyncio.to_thread(aws.speak, body.text, body.lang)
        except Exception as e:  # noqa: BLE001
            log.warning("Polly failed: %s", e)
            return Response(status_code=204)
        while len(_audio) > 200:
            _audio.popitem(last=False)
    return Response(_audio[key], media_type="audio/mpeg", headers={"Cache-Control": "private, max-age=3600"})
