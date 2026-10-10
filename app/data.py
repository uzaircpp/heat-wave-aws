"""Static city layers: ward polygons with real attributes, OSM places and waterways.

`scripts/prepare_data.py` builds these files from public sources:
  data/wards.json      ward polygons + elevation (Copernicus DEM via Open-Meteo) + OSM land cover
  data/places.json     drinking water, shelters, parks, hospitals, underpasses (OpenStreetMap)
  data/waterways.json  drains, canals and the Yamuna (OpenStreetMap)
Until they exist we fall back to the raw ward boundaries so the API still starts.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from shapely.geometry import Point, shape
from shapely.strtree import STRtree

from .config import settings
from .geo import haversine_m

log = logging.getLogger("urbanshield.data")


@dataclass
class Ward:
    id: int
    name: str
    code: str
    geom: object  # shapely Polygon / MultiPolygon
    lat: float
    lon: float
    area_km2: float
    elevation_m: float | None = None
    elevation_min_m: float | None = None
    green_frac: float | None = None
    water_frac: float | None = None
    tree_frac: float | None = None
    built_frac: float | None = None
    landcover_source: str | None = None
    dist_river_m: float | None = None
    dist_drain_m: float | None = None
    neighbours: list[int] = field(default_factory=list)

    def public(self) -> dict:
        return {
            "id": self.id, "name": self.name, "code": self.code, "lat": self.lat, "lon": self.lon,
            "area_km2": round(self.area_km2, 3), "elevation_m": self.elevation_m,
            "green_frac": self.green_frac, "tree_frac": self.tree_frac, "built_frac": self.built_frac,
            "landcover_source": self.landcover_source, "dist_river_m": self.dist_river_m, "dist_drain_m": self.dist_drain_m,
        }


def _clean_name(raw: str | None, code: str) -> str:
    if not raw:
        return f"Ward {code}" if code else "Ward"
    name = " ".join(raw.split()).title()
    for a, b in (("Ndmc", "NDMC"), ("Cantt", "Cantt"), (" Charge ", " ")):
        name = name.replace(a, b)
    return name


class CityData:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.wards: list[Ward] = []
        self.places: list[dict] = []
        self.waterways: list[dict] = []
        self.prepared = False
        self._tree: STRtree | None = None
        self.load()

    # ---------- loading ----------
    def load(self) -> None:
        prepared = self.data_dir / "wards.json"
        if prepared.exists():
            self._load_prepared(prepared)
            self.prepared = True
        else:
            raw = self.data_dir / "delhi_wards_raw.geojson"
            if not raw.exists():
                raise FileNotFoundError(f"No ward boundaries at {raw}")
            log.warning("wards.json missing, run scripts/prepare_data.py. Using raw boundaries without elevation.")
            self._load_raw(raw)
        self._tree = STRtree([w.geom for w in self.wards])
        self._compute_neighbours()
        places = self.data_dir / "places.json"
        self.places = self._clean_places(json.loads(places.read_text())) if places.exists() else []
        ways = self.data_dir / "waterways.json"
        self.waterways = json.loads(ways.read_text()) if ways.exists() else []

    def _load_raw(self, path: Path) -> None:
        gj = json.loads(path.read_text())
        for i, f in enumerate(gj["features"]):
            geom = shape(f["geometry"]).buffer(0)
            rp = geom.representative_point()
            code = (f["properties"].get("Ward_No") or str(i)).strip()
            self.wards.append(Ward(
                id=i, code=code, name=_clean_name(f["properties"].get("Ward_Name"), code), geom=geom,
                lat=round(rp.y, 5), lon=round(rp.x, 5), area_km2=_area_km2(geom),
            ))

    def _load_prepared(self, path: Path) -> None:
        for w in json.loads(path.read_text()):
            geom = shape(w["geometry"])
            self.wards.append(Ward(
                id=w["id"], code=w["code"], name=f"Ward {w['name']}" if str(w["name"]).isdigit() else w["name"],
                geom=geom, lat=w["lat"], lon=w["lon"],
                area_km2=w["area_km2"], elevation_m=w.get("elevation_m"), elevation_min_m=w.get("elevation_min_m"),
                green_frac=w.get("green_frac"), water_frac=w.get("water_frac"), tree_frac=w.get("tree_frac"),
                built_frac=w.get("built_frac"), landcover_source=w.get("landcover_source"),
                dist_river_m=w.get("dist_river_m"), dist_drain_m=w.get("dist_drain_m"),
            ))

    def _clean_places(self, places: list[dict]) -> list[dict]:
        """OSM maps a long underpass as many short ways. Merge those within 150 m into one and name
        unnamed ones after their ward, so the city sees a few hundred real underpasses, not every segment."""
        others = [p for p in places if p["kind"] != "underpass"]
        ups = sorted((p for p in places if p["kind"] == "underpass"), key=lambda p: p["name"] == "Underpass")
        kept: list[dict] = []
        for p in ups:
            if any(haversine_m(p["lat"], p["lon"], k["lat"], k["lon"]) < 150 for k in kept):
                continue
            kept.append(dict(p))
        for p in kept:
            if p["name"] in ("Underpass", None, ""):
                w = self.nearest_ward(p["lat"], p["lon"])
                p["name"] = f"Underpass near {w.name}"
        return others + kept

    def _compute_neighbours(self) -> None:
        for w in self.wards:
            idx = self._tree.query(w.geom.buffer(0.0008))
            w.neighbours = [int(i) for i in idx if int(i) != w.id and self.wards[int(i)].geom.distance(w.geom) < 0.0008]

    # ---------- queries ----------
    def ward_at(self, lat: float, lon: float) -> Ward | None:
        p = Point(lon, lat)
        for i in self._tree.query(p):
            if self.wards[int(i)].geom.contains(p):
                return self.wards[int(i)]
        return None

    def nearest_ward(self, lat: float, lon: float) -> Ward:
        hit = self.ward_at(lat, lon)
        if hit:
            return hit
        return min(self.wards, key=lambda w: haversine_m(lat, lon, w.lat, w.lon))

    def ward(self, ward_id: int) -> Ward | None:
        return self.wards[ward_id] if 0 <= ward_id < len(self.wards) else None

    def search_wards(self, q: str, limit: int = 8) -> list[Ward]:
        q = q.lower().strip()
        starts = [w for w in self.wards if w.name.lower().startswith(q)]
        contains = [w for w in self.wards if q in w.name.lower() and w not in starts]
        return (starts + contains)[:limit]

    def places_near(self, lat: float, lon: float, kinds: set[str] | None = None, limit: int = 10,
                    max_m: float = 5000) -> list[dict]:
        out = []
        for p in self.places:
            if kinds and p["kind"] not in kinds:
                continue
            d = haversine_m(lat, lon, p["lat"], p["lon"])
            if d <= max_m:
                out.append({**p, "distance_m": round(d)})
        out.sort(key=lambda p: p["distance_m"])
        return out[:limit]

    def geojson(self, simplify: float = 0.0004) -> dict:
        from shapely.geometry import mapping
        return {"type": "FeatureCollection", "features": [
            {"type": "Feature", "id": w.id, "properties": w.public(),
             "geometry": mapping(w.geom.simplify(simplify, preserve_topology=True))}
            for w in self.wards
        ]}


def _area_km2(geom) -> float:
    # Equal-area enough for wards: scale degrees by local metres per degree.
    import math
    lat = geom.centroid.y
    return geom.area * (111.32 * math.cos(math.radians(lat))) * 110.54


@lru_cache(maxsize=1)
def city() -> CityData:
    return CityData(settings.data_dir)
