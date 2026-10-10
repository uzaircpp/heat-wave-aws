"""Recorded-shape fakes for every external API, so the whole backend can be tested offline.
Shapes follow the real responses of Open-Meteo, Overpass, OSRM, Nominatim and SACHET CAP 1.2."""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import httpx

TZ = ZoneInfo("Asia/Kolkata")


def _hours(past_days: int, forecast_days: int) -> list[datetime]:
    start = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None) - timedelta(days=past_days)
    return [start + timedelta(hours=h) for h in range(24 * (past_days + forecast_days))]


def _point(lat: float, lon: float, past_days: int, forecast_days: int) -> dict:
    hrs = _hours(past_days, forecast_days)
    tomorrow = (datetime.now(TZ) + timedelta(days=1)).strftime("%Y-%m-%d")
    east = (lon - 76.83) / 0.52  # a little hotter in the east of the grid
    H = {k: [] for k in ["time", "temperature_2m", "relative_humidity_2m", "apparent_temperature", "precipitation",
                         "precipitation_probability", "uv_index", "wind_speed_10m", "cloud_cover", "shortwave_radiation"]}
    for t in hrs:
        hod = t.hour + t.minute / 60
        temp = 29 + 15 * math.exp(-((hod - 15) / 4.5) ** 2) + east
        rh = 55 - 25 * math.exp(-((hod - 15) / 5) ** 2)
        e = rh / 100 * 6.105 * math.exp(17.27 * temp / (237.7 + temp))
        app = temp + 0.33 * e - 0.7 * 1.5 - 4
        rain = 22.0 if (t.strftime("%Y-%m-%d") == tomorrow and t.hour in (16, 17)) else 0.0
        sw = max(0.0, 950 * math.sin(math.pi * (hod - 6) / 13)) if 6 <= hod <= 19 else 0.0
        H["time"].append(t.strftime("%Y-%m-%dT%H:00"))
        H["temperature_2m"].append(round(temp, 1))
        H["relative_humidity_2m"].append(round(rh))
        H["apparent_temperature"].append(round(app, 1))
        H["precipitation"].append(rain)
        H["precipitation_probability"].append(80 if rain else 5)
        H["uv_index"].append(round(sw / 90, 1))
        H["wind_speed_10m"].append(6.0)
        H["cloud_cover"].append(70 if rain else 10)
        H["shortwave_radiation"].append(round(sw))
    days = sorted({t.strftime("%Y-%m-%d") for t in hrs})
    D = {k: [] for k in ["time", "temperature_2m_max", "temperature_2m_min", "apparent_temperature_max", "precipitation_sum",
                         "precipitation_probability_max", "uv_index_max", "wind_speed_10m_max", "sunrise", "sunset"]}
    for d in days:
        idx = [i for i, t in enumerate(H["time"]) if t.startswith(d)]
        D["time"].append(d)
        D["temperature_2m_max"].append(max(H["temperature_2m"][i] for i in idx))
        D["temperature_2m_min"].append(min(H["temperature_2m"][i] for i in idx))
        D["apparent_temperature_max"].append(max(H["apparent_temperature"][i] for i in idx))
        D["precipitation_sum"].append(sum(H["precipitation"][i] for i in idx))
        D["precipitation_probability_max"].append(max(H["precipitation_probability"][i] for i in idx))
        D["uv_index_max"].append(max(H["uv_index"][i] for i in idx))
        D["wind_speed_10m_max"].append(9.0)
        D["sunrise"].append(f"{d}T06:05")
        D["sunset"].append(f"{d}T17:55")
    now = datetime.now(TZ).strftime("%Y-%m-%dT%H:00")
    k = H["time"].index(now) if now in H["time"] else 24
    cur = {"time": now, "interval": 900, "temperature_2m": H["temperature_2m"][k],
           "relative_humidity_2m": H["relative_humidity_2m"][k], "apparent_temperature": H["apparent_temperature"][k],
           "precipitation": H["precipitation"][k], "uv_index": H["uv_index"][k], "wind_speed_10m": 6.0, "is_day": 1,
           "weather_code": 0}
    return {"latitude": lat, "longitude": lon, "timezone": "Asia/Kolkata", "elevation": 215.0,
            "current": cur, "hourly": H, "daily": D}


def open_meteo_forecast(request: httpx.Request) -> httpx.Response:
    q = parse_qs(urlparse(str(request.url)).query)
    lats = [float(x) for x in q["latitude"][0].split(",")]
    lons = [float(x) for x in q["longitude"][0].split(",")]
    past = int(q.get("past_days", ["0"])[0])
    fdays = int(q.get("forecast_days", ["7"])[0])
    pts = [_point(a, b, past, fdays) for a, b in zip(lats, lons)]
    return httpx.Response(200, json=pts if len(pts) > 1 else pts[0])


def elevation_of(lat: float, lon: float) -> float:
    """Synthetic but shaped like Delhi: the Yamuna floodplain in the east is lowest, the ridge is higher."""
    ridge = 18 * math.exp(-(((lon - 77.17) / 0.03) ** 2))
    return round(213 - 14 * (lon - 76.83) / 0.52 + ridge + 3 * math.sin(lat * 40), 1)


def open_meteo_elevation(request: httpx.Request) -> httpx.Response:
    q = parse_qs(urlparse(str(request.url)).query)
    lats = [float(x) for x in q["latitude"][0].split(",")]
    lons = [float(x) for x in q["longitude"][0].split(",")]
    return httpx.Response(200, json={"elevation": [elevation_of(a, b) for a, b in zip(lats, lons)]})


# Real Delhi coordinates for the OSM fakes
MINTO = (28.6335, 77.2265)
PUL_PRAHLADPUR = (28.5065, 77.2875)


def overpass(request: httpx.Request) -> httpx.Response:
    body = parse_qs(request.content.decode())["data"][0]
    if "out center" in body:  # places
        els = [
            {"type": "node", "id": 1, "lat": 28.6315, "lon": 77.2167, "tags": {"amenity": "drinking_water", "name": "CP water point"}},
            {"type": "node", "id": 2, "lat": 28.5677, "lon": 77.2433, "tags": {"amenity": "community_centre", "name": "Lajpat Nagar Community Hall"}},
            {"type": "way", "id": 3, "center": {"lat": 28.6129, "lon": 77.2295}, "tags": {"leisure": "park", "name": "India Gate Lawns"}},
            {"type": "way", "id": 4, "center": {"lat": MINTO[0], "lon": MINTO[1]}, "tags": {"highway": "primary", "tunnel": "yes", "name": "Minto Bridge underpass"}},
            {"type": "way", "id": 5, "center": {"lat": PUL_PRAHLADPUR[0], "lon": PUL_PRAHLADPUR[1]}, "tags": {"highway": "primary", "tunnel": "yes", "name": "Pul Prahladpur underpass"}},
            {"type": "node", "id": 6, "lat": 28.7, "lon": 77.1, "tags": {"amenity": "bench"}},
        ]
        return httpx.Response(200, json={"elements": els})
    if 'waterway' in body:
        yam = [[77.205, 28.888], [77.233, 28.71], [77.25, 28.635], [77.3, 28.545], [77.35, 28.425]]
        naj = [[76.93, 28.565], [77.04, 28.615], [77.13, 28.668], [77.2, 28.712], [77.228, 28.72]]
        return httpx.Response(200, json={"elements": [
            {"type": "way", "id": 10, "tags": {"waterway": "river", "name": "Yamuna"}, "geometry": [{"lon": a, "lat": b} for a, b in yam]},
            {"type": "way", "id": 11, "tags": {"waterway": "drain", "name": "Najafgarh Drain"}, "geometry": [{"lon": a, "lat": b} for a, b in naj]},
        ]})
    if "natural\"=\"water" in body or 'reservoir' in body:
        ring = [(77.20, 28.60), (77.21, 28.60), (77.21, 28.61), (77.20, 28.61), (77.20, 28.60)]
        return httpx.Response(200, json={"elements": [{"type": "way", "id": 20, "geometry": [{"lon": a, "lat": b} for a, b in ring]}]})
    ring = [(77.15, 28.60), (77.19, 28.60), (77.19, 28.66), (77.15, 28.66), (77.15, 28.60)]  # the Ridge forest
    return httpx.Response(200, json={"elements": [{"type": "way", "id": 21, "geometry": [{"lon": a, "lat": b} for a, b in ring]}]})


def osrm(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    from urllib.parse import unquote
    coords = unquote(path.rsplit("/", 1)[1])
    (lon1, lat1), (lon2, lat2) = [tuple(map(float, c.split(","))) for c in coords.split(";")]
    via_minto = [[lon1, lat1], [MINTO[1], MINTO[0]], [lon2, lat2]]
    detour = [[lon1, lat1], [lon1, lat2 - 0.002], [lon2, lat2]]
    mk = lambda c, d, t, n: {"distance": d, "duration": t, "geometry": {"type": "LineString", "coordinates": c},  # noqa: E731
                             "legs": [{"steps": [{"name": n}]}]}
    return httpx.Response(200, json={"code": "Ok", "routes": [mk(via_minto, 5200, 720, "Minto Road"),
                                                              mk(detour, 6100, 1080, "Panchkuian Road")]})


def nominatim(request: httpx.Request) -> httpx.Response:
    if "/reverse" in str(request.url):
        return httpx.Response(200, json={"display_name": "Minto Road, New Delhi", "address": {"road": "Minto Road", "suburb": "Connaught Place"}})
    return httpx.Response(200, json=[{"display_name": "Karol Bagh, Delhi", "lat": "28.6519", "lon": "77.1909", "type": "suburb"}])


RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>SACHET</title>
<item><title>Heat wave warning Delhi</title><link>https://sachet.ndma.gov.in/cap_public_website/FetchXMLFile?identifier=1</link><guid>1</guid><pubDate>Sat, 11 Oct 2026 08:00:00 +0530</pubDate></item>
<item><title>Thunderstorm Kerala</title><link>https://sachet.ndma.gov.in/cap_public_website/FetchXMLFile?identifier=2</link><guid>2</guid></item>
</channel></rss>"""


def cap(identifier: str, area: str, poly: str, event: str) -> str:
    return f"""<?xml version="1.0"?><alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
<identifier>{identifier}</identifier><sender>imd@imd.gov.in</sender><sent>2026-10-11T08:00:00+05:30</sent>
<status>Actual</status><msgType>Alert</msgType><scope>Public</scope>
<info><category>Met</category><event>{event}</event><urgency>Expected</urgency><severity>Severe</severity><certainty>Likely</certainty>
<senderName>India Meteorological Department</senderName><headline>{event} in {area}</headline>
<description>Maximum temperature likely 45-46 C.</description><instruction>Avoid going out between 12 and 4 PM.</instruction>
<effective>2026-10-11T08:00:00+05:30</effective><expires>2099-10-12T08:00:00+05:30</expires>
<area><areaDesc>{area}</areaDesc><polygon>{poly}</polygon></area></info></alert>"""


def sachet(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if url.endswith("rss_india.xml"):
        return httpx.Response(200, text=RSS)
    if "identifier=1" in url:
        return httpx.Response(200, text=cap("IMD-DEL-1", "New Delhi", "28.5,77.1 28.7,77.1 28.7,77.3 28.5,77.3 28.5,77.1", "Heat Wave"))
    return httpx.Response(200, text=cap("IMD-KER-2", "Ernakulam", "9.9,76.2 10.1,76.2 10.1,76.4 9.9,76.2", "Thunderstorm"))


def install(router) -> None:
    """Register every fake on a respx router."""
    router.get(url__regex=r"https://api\.open-meteo\.com/v1/forecast.*").mock(side_effect=open_meteo_forecast)
    router.get(url__regex=r"https://api\.open-meteo\.com/v1/elevation.*").mock(side_effect=open_meteo_elevation)
    router.post(url__regex=r"https://overpass-api\.de/.*").mock(side_effect=overpass)
    router.get(url__regex=r"https://router\.project-osrm\.org/.*").mock(side_effect=osrm)
    router.get(url__regex=r"https://nominatim\.openstreetmap\.org/.*").mock(side_effect=nominatim)
    router.get(url__regex=r"https://sachet\.ndma\.gov\.in/.*").mock(side_effect=sachet)
    router.post(url__regex=r"https://api\.twilio\.com/.*").mock(return_value=httpx.Response(201, json={"sid": "SM1"}))
