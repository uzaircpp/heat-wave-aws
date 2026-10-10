"""End-to-end tests: data prep, weather, safe hours, alerts, reports, officer workflow, routing, simulators."""
import asyncio
import io

import pytest
import respx
from fastapi.testclient import TestClient
from PIL import Image

from tests import fakes

OFFICER = {"Authorization": "Bearer test-officer"}


@pytest.fixture(scope="module")
def mock():
    with respx.mock(assert_all_called=False) as router:
        fakes.install(router)
        yield router


@pytest.fixture(scope="module")
def client(mock):
    from scripts import prepare_data
    asyncio.run(prepare_data.main(skip_osm=False))
    from app import data
    data.city.cache_clear()
    from app.main import app
    from app.services.weather import weather
    with TestClient(app) as c:
        asyncio.run(weather.refresh(force=True))
        yield c


def jpeg_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (200, 120, 40)).save(buf, "JPEG")
    return buf.getvalue()


# ---------- data prep ----------
def test_prepared_wards_have_real_attributes(client):
    from app.data import city
    c = city()
    assert c.prepared and len(c.wards) == 290
    assert all(w.elevation_m is not None for w in c.wards)
    east = min(c.wards, key=lambda w: -w.lon)
    west = min(c.wards, key=lambda w: w.lon)
    assert east.elevation_m < west.elevation_m  # floodplain lower than the west, as in the fake DEM
    assert any(w.green_frac and w.green_frac > 0.3 for w in c.wards)  # Ridge forest polygon
    assert {p["kind"] for p in c.places} >= {"drinking_water", "community_centre", "park", "underpass"}
    assert all(p["kind"] != "bench" for p in c.places)


def test_health_and_meta(client):
    h = client.get("/api/health").json()
    assert h["ok"] and h["weather"]["ready"] and h["data_prepared"] and h["wards"] == 290
    m = client.get("/api/meta").json()
    assert m["channels"] == {"app": True, "sms": False, "whatsapp": False}
    assert any(s["name"] == "Open-Meteo" for s in m["sources"])


def test_wards_endpoints(client):
    gj = client.get("/api/wards").json()
    assert gj["type"] == "FeatureCollection" and len(gj["features"]) == 290
    hits = client.get("/api/wards/search", params={"q": "karol"}).json()
    assert hits and "Karol" in hits[0]["name"]
    w = client.get("/api/wards/locate", params={"lat": 28.6519, "lon": 77.1909}).json()
    assert w["id"] >= 0
    assert client.get("/api/wards/locate", params={"lat": 19.07, "lon": 72.87}).status_code == 404


# ---------- weather ----------
def test_ward_weather_and_layers(client):
    r = client.get("/api/weather/ward", params={"lat": 28.6519, "lon": 77.1909, "hours": 24}).json()
    assert len(r["hourly"]["time"]) == 24 and r["now"]["apparent_temperature"] is not None
    assert len(r["daily"]["time"]) == 8
    lay = client.get("/api/layers/feels_like").json()
    assert len(lay["values"]) == 290 and all(v["value"] is not None for v in lay["values"])
    rain = client.get("/api/layers/rain_6h", params={"offset_h": 0}).json()
    assert len(rain["values"]) == 290
    city = client.get("/api/weather/city").json()
    assert city["feels_like_max"] >= city["feels_like_mean"] and len(city["next_24h"]) == 24


def test_green_wards_read_cooler(client):
    from app.data import city
    from app.services.weather import Weather
    green = max(city().wards, key=lambda w: w.green_frac or 0)
    bare = min(city().wards, key=lambda w: w.green_frac or 0)
    assert Weather.uhi(green) < Weather.uhi(bare)


def test_forecast_risk_labels(client):
    r = client.get("/api/forecast", params={"ward_id": 10}).json()
    assert len(r["days"]) == 7  # today plus six more; yesterday is history, not forecast
    assert {d["risk"] for d in r["days"]} <= {"flood", "heat", "moderate", "low"}


def test_safe_hours_wbgt(client):
    r = client.get("/api/safe-hours", params={"role": "construction", "lat": 28.6519, "lon": 77.1909}).json()
    assert r["role_label"] == "Construction" and len(r["hours"]) == 17
    cats = {h["category"] for h in r["hours"]}
    assert "unsafe" in cats and "safe" in cats or "caution" in cats
    light = client.get("/api/safe-hours", params={"role": "vendor", "ward_id": 5}).json()
    heavy = client.get("/api/safe-hours", params={"role": "construction", "ward_id": 5}).json()
    count = lambda x: sum(h["category"] == "unsafe" for h in x["hours"])  # noqa: E731
    assert count(heavy) >= count(light)  # heavier work hits the limit sooner
    assert client.get("/api/safe-hours", params={"role": "pilot", "ward_id": 5}).status_code == 422


def test_wbgt_formula():
    from app.services.heat import wbgt_shade, wet_bulb_stull
    assert wet_bulb_stull(20, 50) == pytest.approx(13.7, abs=0.1)  # Stull 2011 worked example
    assert 29.0 < wbgt_shade(40, 40) < 32.5


def test_unsafe_hours_history(client):
    r = client.get("/api/history/unsafe-hours", params={"ward_id": 3, "days": 30}).json()
    assert len(r["days"]) == 30 and all(d["hours"] >= 0 for d in r["days"])


# ---------- simulators ----------
def test_flood_simulation_uses_terrain(client):
    dry = client.post("/api/simulate/flood", json={"rain_mm": 20}).json()
    wet = client.post("/api/simulate/flood", json={"rain_mm": 140, "drains_clean": False}).json()
    assert dry["wards_wet"] == 0 and wet["wards_wet"] > 0
    clean = client.post("/api/simulate/flood", json={"rain_mm": 140, "drains_clean": True}).json()
    assert clean["wards_wet"] <= wet["wards_wet"]
    from app.data import city
    deep = max(wet["wards"], key=lambda r: r["depth_m"])
    assert city().wards[deep["ward_id"]].elevation_m < sum(w.elevation_m for w in city().wards) / 290
    assert wet["underpasses"] and wet["underpasses"][0]["depth_m"] > 0


def test_heat_simulation_trees_cool(client):
    base = client.post("/api/simulate/heat", json={"air_temp_c": 44, "hour": 15}).json()
    trees = client.post("/api/simulate/heat", json={"air_temp_c": 44, "hour": 15, "extra_green_pct": 30, "cool_roofs": True}).json()
    assert trees["mean"] < base["mean"] and len(base["wards"]) == 290


# ---------- alerts ----------
def test_alerts_official_and_forecast(client):
    from app.services import alerts
    asyncio.run(alerts.refresh())
    items = client.get("/api/alerts").json()["alerts"]
    srcs = {a["source"] for a in items}
    assert "sachet" in srcs and "forecast" in srcs
    assert any("Delhi" in a["area"] for a in items if a["source"] == "sachet")
    assert not any("Ernakulam" in a["area"] for a in items)  # other states filtered out
    assert any(a["kind"] == "flood" and "burst" in a["title"].lower() for a in items)
    n = len(items)
    asyncio.run(alerts.refresh())  # idempotent
    assert len(client.get("/api/alerts").json()["alerts"]) == n


# ---------- reports & officer workflow ----------
def test_report_lifecycle(client):
    r = client.post("/api/reports", data={"kind": "waterlogging", "depth": "waist", "lat": 28.6335, "lon": 77.2265,
                                          "description": "Bus stalled  at the entry"},
                    files={"photo": ("p.jpg", jpeg_bytes(), "image/jpeg")})
    assert r.status_code == 201, r.text
    rep = r.json()
    assert rep["severity"] == "crit" and rep["ward_name"] and rep["address"] == "Minto Road, Connaught Place"
    assert rep["photo_url"] and client.get(rep["photo_url"]).status_code == 200
    assert rep["description"] == "Bus stalled at the entry"

    assert client.post("/api/reports", data={"kind": "waterlogging", "lat": 19.0, "lon": 72.8}).status_code == 422
    assert client.post("/api/reports", data={"kind": "lava", "lat": 28.63, "lon": 77.22}).status_code == 422
    bad = client.post("/api/reports", data={"kind": "heat", "lat": 28.63, "lon": 77.22},
                      files={"photo": ("x.jpg", b"not an image", "image/jpeg")})
    assert bad.status_code == 415

    assert client.patch(f"/api/reports/{rep['id']}", json={"status": "resolved"}).status_code == 401
    up = client.patch(f"/api/reports/{rep['id']}", json={"assignee": "Crew B"}, headers=OFFICER).json()
    assert up["status"] == "assigned" and up["assignee"] == "Crew B"
    done = client.patch(f"/api/reports/{rep['id']}", json={"status": "resolved"}, headers=OFFICER).json()
    assert done["resolved_at"]
    hist = client.get(f"/api/reports/{rep['ref']}").json()["history"]
    assert [h["action"] for h in hist] == ["created", "updated", "updated"]

    s = client.get("/api/ops/summary", headers=OFFICER).json()
    assert s["resolved_this_week"] >= 1 and s["avg_fix_hours"] is not None
    csv = client.get("/api/ops/export.csv", headers=OFFICER)
    assert csv.status_code == 200 and rep["ref"] in csv.text


def test_report_cluster_raises_alert(client):
    from app.services import alerts
    for i in range(3):
        assert client.post("/api/reports", data={"kind": "waterlogging", "depth": "knee",
                                                 "lat": 28.5065 + i * 0.0005, "lon": 77.2875}).status_code == 201
    asyncio.run(alerts.refresh())
    items = client.get("/api/alerts").json()["alerts"]
    assert any(a["source"] == "reports" for a in items)


def test_rate_limit(client):
    from app.routers import reports
    old = reports.MAX_PER_HOUR
    reports.MAX_PER_HOUR = 1
    try:
        codes = [client.post("/api/reports", data={"kind": "heat", "lat": 28.65, "lon": 77.19}).status_code for _ in range(2)]
        assert 429 in codes
    finally:
        reports.MAX_PER_HOUR = old


def test_reports_listing_filters(client):
    allr = client.get("/api/reports").json()["reports"]
    crit = client.get("/api/reports", params={"severity": "crit"}).json()["reports"]
    assert allr and all(r["severity"] == "crit" for r in crit)
    assert "reporter_hash" not in allr[0]


# ---------- routing ----------
def test_safe_route_avoids_flooded_report(client):
    client.post("/api/reports", data={"kind": "waterlogging", "depth": "knee", "lat": fakes.MINTO[0], "lon": fakes.MINTO[1]})
    resp = client.get("/api/route", params={"from": "28.62,77.20", "to": "28.65,77.24"})
    assert resp.status_code == 200, resp.text
    r = resp.json()
    assert r["fastest"]["via"] == ["Minto Road"] and r["fastest"]["risk"] > 0
    assert r["recommended"]["via"] == ["Panchkuian Road"] and r["recommended"]["risk"] == 0
    assert r["same_route"] is False


# ---------- places, geocode, subscriptions, broadcasts ----------
def test_places_and_geocode(client):
    p = client.get("/api/places", params={"lat": 28.63, "lon": 77.22, "kinds": "drinking_water"}).json()["places"]
    assert p and p[0]["kind"] == "drinking_water" and p[0]["distance_m"] < 2000
    g = client.get("/api/geocode", params={"q": "Karol Bagh"}).json()["results"]
    assert g[0]["lat"] == pytest.approx(28.6519)
    rv = client.get("/api/reverse", params={"lat": 28.6335, "lon": 77.2265}).json()
    assert rv["address"] and rv["ward"]


def test_subscribe_and_broadcast(client):
    assert client.post("/api/subscribe", json={"phone": "98123"}).status_code == 422
    ok = client.post("/api/subscribe", json={"phone": "+919812345678", "lat": 28.6335, "lon": 77.2265,
                                             "audiences": ["commuters"]}).json()
    assert ok["ok"] and ok["ward_id"] is not None
    b = client.post("/api/broadcasts", headers=OFFICER, json={"message": "Minto Bridge flooded, avoid until 7 PM",
                                                              "ward_id": ok["ward_id"], "audiences": ["commuters"],
                                                              "channels": ["app", "sms"]}).json()
    assert b["detail"]["skipped"] == ["sms"]  # no Twilio keys in tests
    assert client.post("/api/broadcasts", json={"message": "x" * 10}).status_code == 401
    assert client.get("/api/broadcasts", headers=OFFICER).json()["broadcasts"]


def test_event_bus_delivers():
    from app.services.events import Bus
    b = Bus()
    q = b.subscribe()
    b.publish("report", {"ref": "US-1"})
    assert "US-1" in q.get_nowait()


def test_city_layers_for_map(client):
    p = client.get("/api/places/city", params={"kinds": "drinking_water,shelter"}).json()["places"]
    assert p and all(x["kind"] in {"drinking_water", "shelter"} for x in p)
    w = client.get("/api/waterways").json()["waterways"]
    assert w and all(len(x["coords"]) >= 2 for x in w)


def test_satellite_tree_cover(client):
    from app.data import city
    from app.services.weather import Weather
    wards = city().wards
    assert all(w.landcover_source == "worldcover" for w in wards)
    ridge = city().ward_at(28.63, 77.17)
    dense = max(wards, key=lambda w: w.built_frac)
    assert ridge.tree_frac > 0.5 and dense.built_frac > 0.9
    assert Weather.uhi(ridge) < 0 < Weather.uhi(dense)
    assert Weather.uhi(dense, extra_tree=0.3) < Weather.uhi(dense)  # planting trees cools
    attrs = client.get("/api/wards/attributes").json()
    assert attrs["landcover_source"] == "worldcover" and len(attrs["wards"]) == len(wards)
    hot = client.post("/api/simulate/heat", json={"air_temp_c": 44, "hour": 15}).json()
    cool = client.post("/api/simulate/heat", json={"air_temp_c": 44, "hour": 15, "extra_green_pct": 20}).json()
    assert cool["mean"] < hot["mean"]


def test_landcover_only_refresh(client):
    import json
    from scripts import prepare_data
    from app.config import settings
    path = settings.data_dir / "wards.json"
    data = json.loads(path.read_text())
    for w in data:
        w.pop("tree_frac", None)
    path.write_text(json.dumps(data))
    prepare_data.landcover_only()
    again = json.loads(path.read_text())
    assert all(w.get("tree_frac") is not None for w in again) and "elevation_m" in again[0]


@pytest.fixture
def aws_on(monkeypatch):
    from app import aws
    monkeypatch.setattr(aws, "FEATURES", {"rekognition", "translate", "bedrock", "location"})
    monkeypatch.setattr(aws, "reverse", lambda lat, lon: "Pusa Road, Karol Bagh")
    monkeypatch.setattr(aws, "geocode", lambda q, near, bbox, limit=6: [{"name": "Karol Bagh", "lat": 28.65, "lon": 77.19, "type": "Locality"}])
    return aws


def test_rekognition_verifies_water_photo(client, aws_on, monkeypatch):
    monkeypatch.setattr(aws_on, "check_photo", lambda b: {"labels": ["Flood", "Road"], "water": True, "water_confidence": 96.0,
                                                          "unsafe": False, "moderation": [], "service": "Amazon Rekognition"})
    r = client.post("/api/reports", data={"kind": "waterlogging", "depth": "knee", "lat": 28.6505, "lon": 77.1905},
                    files={"photo": ("p.jpg", jpeg_bytes(), "image/jpeg")}, headers={"x-forwarded-for": "10.9.0.1"})
    assert r.status_code == 201, r.text
    assert r.json()["photo_verified"] is True and "Flood" in r.json()["photo_labels"]


def test_rekognition_blocks_unsafe_photo(client, aws_on, monkeypatch):
    monkeypatch.setattr(aws_on, "check_photo", lambda b: {"labels": [], "water": False, "water_confidence": 0, "unsafe": True,
                                                          "moderation": ["Violence"], "service": "Amazon Rekognition"})
    r = client.post("/api/reports", data={"kind": "heat", "lat": 28.6505, "lon": 77.1905},
                    files={"photo": ("p.jpg", jpeg_bytes(), "image/jpeg")}, headers={"x-forwarded-for": "10.9.0.2"})
    assert r.status_code == 422


def test_broadcast_in_hindi_and_ai_drafts(client, aws_on, monkeypatch):
    monkeypatch.setattr(aws_on, "translate", lambda text, target="hi", source="en": "हिंदी: " + text)
    monkeypatch.setattr(aws_on, "generate", lambda system, prompt, max_tokens=500: "Heat alert for Karol Bagh: rest 12 to 4 PM.")
    b = client.post("/api/broadcasts", json={"message": "Heat alert for Karol Bagh", "ward_id": 3}, headers=OFFICER).json()
    assert b["message_hi"].startswith("हिंदी")
    d = client.post("/api/ai/draft-alert", json={"ward_id": 3}, headers=OFFICER).json()
    assert d["en"].startswith("Heat alert") and d["hi"].startswith("हिंदी") and "Ward:" in d["facts"]
    assert client.get("/api/ai/ops-brief", params={"ward_id": 3}, headers=OFFICER).json()["text"]
    assert client.post("/api/ai/draft-alert", json={"ward_id": 3}).status_code == 401
    assert client.get("/api/meta").json()["aws"]["bedrock"] is True


def test_location_routes_avoid_hazards(client, aws_on, monkeypatch):
    calls = []

    def fake_routes(a, b, avoid=None, radius_m=120, alternatives=2):
        calls.append(avoid)
        if avoid:
            return [{"distance_m": 7400, "duration_s": 1200, "coords": [[a[1], a[0]], [77.15, 28.60], [b[1], b[0]]],
                     "via": ["Outer Ring Road"], "provider": "Amazon Location Service"}]
        return [{"distance_m": 5200, "duration_s": 720, "coords": [[a[1], a[0]], [77.2265, 28.6335], [b[1], b[0]]],
                 "via": ["Minto Road"], "provider": "Amazon Location Service"}]
    monkeypatch.setattr(aws_on, "routes", fake_routes)
    client.post("/api/reports", data={"kind": "waterlogging", "depth": "waist", "lat": 28.6335, "lon": 77.2265},
                headers={"x-forwarded-for": "10.9.0.3"})
    r = client.get("/api/route", params={"from": "28.66,77.20", "to": "28.61,77.24"}).json()
    assert calls[-1] and r["recommended"].get("avoids_hazards") and r["recommended"]["risk"] < 1
    assert not r["recommended"]["hazards"]  # it no longer drives through any flooded spot
    assert r["provider"] == "Amazon Location Service"


def test_photo_served_from_s3(client, monkeypatch):
    from app import aws
    monkeypatch.setattr(aws, "PHOTO_BUCKET", "bucket")
    monkeypatch.setattr(aws, "photo_url", lambda name, expires=3600: f"https://bucket.s3.amazonaws.com/photos/{name}?sig=1")
    r = client.get("/api/photos/US-2026-00001.jpg", follow_redirects=False)
    assert r.status_code == 302 and "s3.amazonaws.com" in r.headers["location"]
    assert client.get("/api/photos/../../etc/passwd").status_code == 404
