"""AWS services used when the app runs on AWS. Each one is switched on by the AWS_FEATURES setting
(comma separated), so the same code runs on a laptop with none of them.

Settings (APP_* names are used on AWS; the AWS_* spellings also work locally):
    APP_AWS_REGION, APP_FEATURES, APP_SECRET_ID, PHOTO_BUCKET, BEDROCK_MODEL_ID, BEDROCK_REGION

    rekognition  check report photos: flag unsafe images, mark photos that show water as verified
    translate    send broadcasts in Hindi as well as English (Amazon Translate)
    bedrock      draft alerts and officer briefings with a foundation model (Amazon Bedrock, Converse API)
    location     routes that avoid flooded spots, address search (Amazon Location Service, v2 APIs)
    polly        the assistant speaks its answers in Indian English and Hindi (Amazon Polly, voice Kajal)
    PHOTO_BUCKET report photos stored in Amazon S3 instead of the server's disk
    AWS_SECRET_ID secrets loaded from AWS Secrets Manager at start-up (see config.py)

boto3 is synchronous; callers run these through asyncio.to_thread.
"""
from __future__ import annotations

import json
import logging
import os
from functools import lru_cache

log = logging.getLogger("urbanshield.aws")

REGION = os.environ.get("APP_AWS_REGION") or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "ap-south-1"
FEATURES = {f.strip() for f in (os.environ.get("APP_FEATURES") or os.environ.get("AWS_FEATURES", "")).split(",") if f.strip()}
PHOTO_BUCKET = os.environ.get("PHOTO_BUCKET", "")
BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "global.anthropic.claude-haiku-4-5-20251001-v1:0")
BEDROCK_REGION = os.environ.get("BEDROCK_REGION", REGION)
LOCATION_REGION = os.environ.get("LOCATION_REGION", REGION)
POLLY_REGION = os.environ.get("POLLY_REGION", REGION)
WATER_LABELS = {"Flood", "Water", "Puddle", "Rain", "Wet", "River", "Lake", "Canal", "Waterfront", "Ripple"}


def on(feature: str) -> bool:
    return feature in FEATURES


def status() -> dict:
    return {"region": REGION, "rekognition": on("rekognition"), "translate": on("translate"),
            "bedrock": on("bedrock"), "bedrock_model": BEDROCK_MODEL_ID if on("bedrock") else None,
            "location": on("location"), "polly": on("polly"), "s3_photos": bool(PHOTO_BUCKET)}


@lru_cache(maxsize=None)
def client(name: str, region: str | None = None):
    import boto3
    from botocore.config import Config

    return boto3.client(name, region_name=region or REGION,
                        config=Config(retries={"max_attempts": 3, "mode": "standard"}, read_timeout=40))


# ---------- S3 photos ----------
def put_photo(name: str, data: bytes) -> None:
    client("s3").put_object(Bucket=PHOTO_BUCKET, Key=f"photos/{name}", Body=data, ContentType="image/jpeg",
                            ServerSideEncryption="AES256")


def photo_url(name: str, expires: int = 3600) -> str:
    return client("s3").generate_presigned_url("get_object", Params={"Bucket": PHOTO_BUCKET, "Key": f"photos/{name}"},
                                               ExpiresIn=expires)


# ---------- Rekognition ----------
def check_photo(data: bytes) -> dict:
    """Labels and moderation for a report photo. water=True when Rekognition sees flooding or water."""
    rk = client("rekognition")
    labels = rk.detect_labels(Image={"Bytes": data}, MaxLabels=25, MinConfidence=70)["Labels"]
    mod = rk.detect_moderation_labels(Image={"Bytes": data}, MinConfidence=80)["ModerationLabels"]
    names = [lb["Name"] for lb in labels]
    water = [lb for lb in labels if lb["Name"] in WATER_LABELS]
    return {
        "labels": names[:10],
        "water": bool(water),
        "water_confidence": round(max((lb["Confidence"] for lb in water), default=0), 1),
        "unsafe": bool(mod),
        "moderation": sorted({m.get("ParentName") or m["Name"] for m in mod}),
        "service": "Amazon Rekognition",
    }


# ---------- Translate ----------
def translate(text: str, target: str = "hi", source: str = "en") -> str:
    return client("translate").translate_text(Text=text[:9000], SourceLanguageCode=source,
                                              TargetLanguageCode=target)["TranslatedText"]


# ---------- Bedrock ----------
def generate(system: str, prompt: str, max_tokens: int = 500) -> str:
    out = client("bedrock-runtime", BEDROCK_REGION).converse(
        modelId=BEDROCK_MODEL_ID,
        system=[{"text": system}],
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": max_tokens, "temperature": 0.3},
    )
    parts = out["output"]["message"]["content"]
    return "".join(p.get("text", "") for p in parts).strip()


def converse(system: str, messages: list[dict], tools: list[dict] | None = None, max_tokens: int = 700) -> dict:
    """One Converse call with optional tool definitions. Returns the raw response (output.message, stopReason)."""
    req = {"modelId": BEDROCK_MODEL_ID, "system": [{"text": system}], "messages": messages,
           "inferenceConfig": {"maxTokens": max_tokens, "temperature": 0.2}}
    if tools:
        req["toolConfig"] = {"tools": [{"toolSpec": {"name": t["name"], "description": t["description"].strip(),
                                                     "inputSchema": {"json": t["schema"]}}} for t in tools]}
    return client("bedrock-runtime", BEDROCK_REGION).converse(**req)


# ---------- Polly ----------
def speak(text: str, lang: str = "en") -> bytes:
    """MP3 of the text read by Kajal (neural, Indian English or Hindi); falls back to the standard Aditi voice."""
    code = "hi-IN" if lang == "hi" else "en-IN"
    pl = client("polly", POLLY_REGION)
    try:
        out = pl.synthesize_speech(Text=text[:2900], OutputFormat="mp3", VoiceId="Kajal", Engine="neural",
                                   LanguageCode=code)
    except Exception as e:  # noqa: BLE001 - neural voices are not in every region
        log.info("Polly neural failed (%s), using standard voice", e)
        out = pl.synthesize_speech(Text=text[:2900], OutputFormat="mp3", VoiceId="Aditi", Engine="standard",
                                   LanguageCode=code)
    return out["AudioStream"].read()


# ---------- Amazon Location Service (v2 Routes and Places APIs, no resources to create) ----------
def _names(leg: dict) -> list[str]:
    names: list[str] = []
    det = leg.get("VehicleLegDetails") or leg.get("PedestrianLegDetails") or {}
    for sp in det.get("Spans") or []:
        for n in sp.get("Names") or []:
            v = n.get("Value")
            if v and v not in names:
                names.append(v)
    for st in det.get("TravelSteps") or []:
        for key in ("CurrentRoad", "NextRoad"):
            for n in (st.get(key) or {}).get("RoadName") or []:
                v = n.get("Value")
                if v and v not in names:
                    names.append(v)
    return names


def _dist_dur(route: dict) -> tuple[float, float]:
    s = route.get("Summary") or {}
    if s.get("Distance") is not None and s.get("Duration") is not None:
        return float(s["Distance"]), float(s["Duration"])
    d = t = 0.0
    for leg in route.get("Legs") or []:
        det = leg.get("VehicleLegDetails") or leg.get("PedestrianLegDetails") or {}
        ov = (det.get("Summary") or {}).get("Overview") or {}
        d += float(ov.get("Distance") or 0)
        t += float(ov.get("Duration") or 0)
    return d, t


def routes(a: tuple[float, float], b: tuple[float, float], avoid: list[tuple[float, float]] | None = None,
           radius_m: float = 120, alternatives: int = 2) -> list[dict]:
    """Car routes from a to b (lat, lon). avoid: points to steer around, each as a small box."""
    req = {
        "Origin": [a[1], a[0]], "Destination": [b[1], b[0]], "TravelMode": "Car",
        "MaxAlternatives": alternatives, "LegGeometryFormat": "Simple",
        "LegAdditionalFeatures": ["Summary", "TravelStepInstructions"],
        "SpanAdditionalFeatures": ["Names"],
    }
    if avoid:
        dlat = radius_m / 111_320
        areas = []
        for lat, lon in avoid[:20]:
            import math
            dlon = radius_m / (111_320 * math.cos(math.radians(lat)))
            areas.append({"Geometry": {"BoundingBox": [lon - dlon, lat - dlat, lon + dlon, lat + dlat]}})
        req["Avoid"] = {"Areas": areas}
    res = client("geo-routes", LOCATION_REGION).calculate_routes(**req)
    out = []
    for r in res.get("Routes") or []:
        coords: list = []
        names: list[str] = []
        for leg in r.get("Legs") or []:
            coords.extend((leg.get("Geometry") or {}).get("LineString") or [])
            names.extend(n for n in _names(leg) if n not in names)
        if len(coords) < 2:
            continue
        dist, dur = _dist_dur(r)
        out.append({"distance_m": dist, "duration_s": dur, "coords": coords, "via": names[:6],
                    "provider": "Amazon Location Service"})
    return out


def geocode(q: str, near: tuple[float, float], bbox, limit: int = 6) -> list[dict]:
    res = client("geo-places", LOCATION_REGION).geocode(
        QueryText=q[:200], BiasPosition=[near[1], near[0]], MaxResults=max(limit * 2, 10), Language="en",
        Filter={"IncludeCountries": ["IND"]})
    min_lon, min_lat, max_lon, max_lat = bbox
    out = []
    for it in res.get("ResultItems") or []:
        pos = it.get("Position") or []
        if len(pos) != 2 or not (min_lon <= pos[0] <= max_lon and min_lat <= pos[1] <= max_lat):
            continue
        label = (it.get("Address") or {}).get("Label") or it.get("Title")
        out.append({"name": label, "lat": pos[1], "lon": pos[0], "type": it.get("PlaceType")})
    return out[:limit]


def reverse(lat: float, lon: float) -> str | None:
    res = client("geo-places", LOCATION_REGION).reverse_geocode(QueryPosition=[lon, lat], MaxResults=1, Language="en")
    items = res.get("ResultItems") or []
    if not items:
        return None
    addr = items[0].get("Address") or {}
    parts = [addr.get("Street"), (addr.get("District") or addr.get("SubDistrict")), addr.get("Locality")]
    parts = [p for p in parts if p]
    return ", ".join(parts[:2]) or addr.get("Label") or items[0].get("Title")


def load_secret(secret_id: str) -> dict:
    raw = client("secretsmanager").get_secret_value(SecretId=secret_id)["SecretString"]
    return json.loads(raw)
