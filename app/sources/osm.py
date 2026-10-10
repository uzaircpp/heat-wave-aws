"""OpenStreetMap: Overpass for places, OSRM for routes, Nominatim for addresses.

All three public servers have usage policies (send a real User-Agent with contact, keep volume low,
cache results). For production traffic, self-host OSRM and Nominatim or use a commercial provider.
"""
from __future__ import annotations

from ..config import settings
from .http import client, get, UpstreamError


# ---------- Overpass ----------
PLACE_QUERIES = {
    "drinking_water": 'node["amenity"="drinking_water"]',
    "water_point": 'node["amenity"="water_point"]',
    "shelter": 'nwr["amenity"="shelter"]',
    "community_centre": 'nwr["amenity"="community_centre"]',
    "hospital": 'nwr["amenity"~"^(hospital|clinic)$"]',
    "park": 'nwr["leisure"="park"]',
    "underpass": 'way["highway"~"^(motorway|trunk|primary|secondary|tertiary|unclassified|residential)(_link)?$"]["tunnel"="yes"]',
}


OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]


async def overpass(query: str, attempts: int = 6) -> dict:
    """Run an Overpass query, rotating through public mirrors when one is busy or times out."""
    import asyncio

    body = f"[out:json][timeout:180];{query}"
    servers = [settings.overpass_url] + [m for m in OVERPASS_MIRRORS if m != settings.overpass_url]
    last: Exception | None = None
    for attempt in range(attempts):
        url = servers[attempt % len(servers)]
        try:
            r = await client().post(url, data={"data": body}, timeout=240)
            if r.status_code in (429, 502, 503, 504):
                raise UpstreamError(f"{r.status_code} from {url}")
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001 - Overpass fails in many ways
            last = e
            print(f"  Overpass busy ({e}); trying {'another server' if attempt + 1 < attempts else 'nothing else'} ...")
            await asyncio.sleep(min(30, 5 * (attempt + 1)))
    raise UpstreamError(f"Overpass failed: {last}")


def tiles(bbox, n: int = 3) -> list[tuple[float, float, float, float]]:
    min_lon, min_lat, max_lon, max_lat = bbox
    dx, dy = (max_lon - min_lon) / n, (max_lat - min_lat) / n
    return [(min_lon + i * dx, min_lat + j * dy, min_lon + (i + 1) * dx, min_lat + (j + 1) * dy)
            for i in range(n) for j in range(n)]


def bbox_str(bbox) -> str:
    min_lon, min_lat, max_lon, max_lat = bbox
    return f"({min_lat},{min_lon},{max_lat},{max_lon})"


async def fetch_places(bbox) -> list[dict]:
    b = bbox_str(bbox)
    parts = "".join(f"{q}{b};" for q in PLACE_QUERIES.values())
    data = await overpass(f"({parts});out center tags;")
    places = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lon = el.get("lon") or (el.get("center") or {}).get("lon")
        if lat is None or lon is None:
            continue
        kind = classify_place(tags)
        if not kind:
            continue
        places.append({
            "id": f"osm:{el['type']}/{el['id']}", "kind": kind,
            "name": tags.get("name:en") or tags.get("name") or _default_name(kind),
            "lat": round(lat, 6), "lon": round(lon, 6),
            "opening_hours": tags.get("opening_hours"), "highway": tags.get("highway"),
        })
    return places


def classify_place(tags: dict) -> str | None:
    if tags.get("amenity") in {"drinking_water", "water_point"}:
        return "drinking_water"
    if tags.get("amenity") == "shelter":
        return "shelter"
    if tags.get("amenity") == "community_centre":
        return "community_centre"
    if tags.get("amenity") in {"hospital", "clinic"}:
        return "hospital"
    if tags.get("leisure") == "park":
        return "park"
    if tags.get("tunnel") == "yes" and tags.get("highway"):
        return "underpass"
    return None


def _default_name(kind: str) -> str:
    return {"drinking_water": "Drinking water", "shelter": "Shelter", "community_centre": "Community centre",
            "hospital": "Hospital", "park": "Park", "underpass": "Underpass"}[kind]


async def fetch_waterways(bbox) -> list[dict]:
    b = bbox_str(bbox)
    data = await overpass(f'(way["waterway"~"^(river|canal|drain|ditch)$"]{b};);out geom tags;')
    out = []
    for el in data.get("elements", []):
        geom = el.get("geometry") or []
        if len(geom) < 2:
            continue
        tags = el.get("tags", {})
        out.append({
            "id": f"osm:way/{el['id']}", "kind": tags.get("waterway"),
            "name": tags.get("name:en") or tags.get("name"),
            "coords": [[round(g["lon"], 5), round(g["lat"], 5)] for g in geom],
        })
    return out


async def fetch_landcover(bbox, n_tiles: int = 3, cache_dir=None) -> dict:
    """Green and water polygons for the city, fetched tile by tile so no single query is too heavy."""
    green_tpl = ('way["leisure"~"^(park|garden|nature_reserve)$"]{b};way["landuse"~"^(forest|grass|meadow|recreation_ground|village_green)$"]{b};'
                 'way["natural"~"^(wood|scrub|grassland)$"]{b};relation["leisure"="park"]{b};relation["landuse"="forest"]{b};')
    water_tpl = 'way["natural"="water"]{b};relation["natural"="water"]{b};way["landuse"="reservoir"]{b};'
    green: dict[str, list] = {}
    water: dict[str, list] = {}
    parts = tiles(bbox, n_tiles)
    import json
    for k, t in enumerate(parts, 1):
        b = bbox_str(t)
        tile_file = cache_dir / f"osm_landcover_tile{k}.json" if cache_dir else None
        if tile_file and tile_file.exists():
            g, w = json.loads(tile_file.read_text())
        else:
            g = await overpass(f"({green_tpl.replace('{b}', b)});out geom;")
            w = await overpass(f"({water_tpl.replace('{b}', b)});out geom;")
            if tile_file:
                tile_file.write_text(json.dumps([g, w]))
        for el, ring in _polys_with_ids(g):
            green[el] = ring
        for el, ring in _polys_with_ids(w):
            water[el] = ring
        print(f"  land cover tile {k}/{len(parts)}: {len(green)} green, {len(water)} water so far")
    return {"green": list(green.values()), "water": list(water.values())}


def _polys_with_ids(data: dict):
    for el in data.get("elements", []):
        for i, ring in enumerate(_polys({"elements": [el]})):
            yield f"{el['type']}/{el['id']}/{i}", ring


def _polys(data: dict) -> list[list[list[float]]]:
    polys = []
    for el in data.get("elements", []):
        if el["type"] == "way" and el.get("geometry"):
            ring = [[p["lon"], p["lat"]] for p in el["geometry"]]
            if len(ring) >= 4:
                polys.append(ring)
        elif el["type"] == "relation":
            for m in el.get("members", []):
                if m.get("role") == "outer" and m.get("geometry"):
                    ring = [[p["lon"], p["lat"]] for p in m["geometry"]]
                    if len(ring) >= 4:
                        polys.append(ring)
    return polys


# ---------- OSRM ----------
async def routes(a: tuple[float, float], b: tuple[float, float], profile: str = "driving") -> list[dict]:
    """Up to 3 alternative routes from a to b, each (lat, lon). Geometry is GeoJSON (lon, lat)."""
    coords = f"{a[1]:.6f},{a[0]:.6f};{b[1]:.6f},{b[0]:.6f}"
    data = await get(f"{settings.osrm_url}/route/v1/{profile}/{coords}",
                     {"alternatives": "3", "overview": "full", "geometries": "geojson", "steps": "true"})
    if data.get("code") != "Ok":
        raise UpstreamError(f"OSRM: {data.get('code')} {data.get('message', '')}")
    out = []
    for r in data["routes"]:
        names = []
        for leg in r.get("legs", []):
            for s in leg.get("steps", []):
                n = s.get("name")
                if n and n not in names:
                    names.append(n)
        out.append({"distance_m": r["distance"], "duration_s": r["duration"],
                    "coords": r["geometry"]["coordinates"], "via": names[:6]})
    return out


# ---------- Nominatim ----------
async def geocode(q: str, bbox, limit: int = 6) -> list[dict]:
    min_lon, min_lat, max_lon, max_lat = bbox
    data = await get(f"{settings.nominatim_url}/search", {
        "q": q, "format": "jsonv2", "limit": limit, "countrycodes": "in",
        "viewbox": f"{min_lon},{max_lat},{max_lon},{min_lat}", "bounded": 1,
    })
    return [{"name": d.get("display_name"), "lat": float(d["lat"]), "lon": float(d["lon"]), "type": d.get("type")}
            for d in data]


async def reverse(lat: float, lon: float) -> str | None:
    data = await get(f"{settings.nominatim_url}/reverse",
                     {"lat": f"{lat:.6f}", "lon": f"{lon:.6f}", "format": "jsonv2", "zoom": 17})
    addr = data.get("address") or {}
    parts = [addr.get(k) for k in ("road", "neighbourhood", "suburb") if addr.get(k)]
    return ", ".join(parts[:2]) or data.get("display_name")
