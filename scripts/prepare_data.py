"""Build the city data files from public sources. Run once, then monthly:

    python scripts/prepare_data.py                  # everything
    python scripts/prepare_data.py --landcover-only # only refresh tree / green / built-up cover
    python scripts/prepare_data.py --skip-osm       # only wards + elevation (+ satellite land cover)

Needs internet. Takes about 2-5 minutes. Writes data/wards.json, data/places.json, data/waterways.json.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shapely.geometry import LineString, Point, Polygon, mapping, shape  # noqa: E402
from shapely.ops import unary_union  # noqa: E402
from shapely.strtree import STRtree  # noqa: E402

from app.config import settings  # noqa: E402
from app.data import _area_km2, _clean_name  # noqa: E402
from app.sources import openmeteo, osm, worldcover  # noqa: E402
from app.sources.http import close_client  # noqa: E402


def _safe_poly(ring):
    try:
        p = Polygon(ring).buffer(0)
        return p if p.is_valid and not p.is_empty else None
    except (ValueError, TypeError):
        return None


def interior_samples(geom, n: int = 4) -> list[tuple[float, float]]:
    """Up to n (lat, lon) points inside the polygon on a regular grid, plus its representative point."""
    rp = geom.representative_point()
    pts = [(rp.y, rp.x)]
    minx, miny, maxx, maxy = geom.bounds
    k = math.ceil(math.sqrt(n * 2))
    for i in range(k):
        for j in range(k):
            x = minx + (maxx - minx) * (i + 0.5) / k
            y = miny + (maxy - miny) * (j + 0.5) / k
            if geom.contains(Point(x, y)):
                pts.append((y, x))
    return pts[: n + 1]


def satellite_landcover(wards: list[dict], cache_dir: Path) -> bool:
    """Tree, vegetation, built-up and water share per ward from ESA WorldCover (10 m). True on success."""
    print("Satellite land cover (ESA WorldCover 10 m, 2021) ...")
    cache = cache_dir / "worldcover_city.npz"
    try:
        try:
            arr, tr = worldcover.read_window(settings.bbox, cache)
        except Exception as e:  # noqa: BLE001 - fall back to downloading whole tiles once
            print(f"  streaming the window failed ({e}); downloading the tile instead (one time, ~50-150 MB) ...")
            local = []
            for t in worldcover.tiles_for(settings.bbox):
                dest = cache_dir / f"worldcover_{t}.tif"
                if not dest.exists():
                    _download(worldcover.URL.format(tile=t), dest)
                local.append(dest)
            old = worldcover.URL
            worldcover.URL = str(cache_dir / "worldcover_{tile}.tif")
            try:
                arr, tr = worldcover.read_window(settings.bbox, cache)
            finally:
                worldcover.URL = old
    except Exception as e:  # noqa: BLE001
        print(f"  Satellite land cover failed ({e}).")
        return False
    print(f"  read {arr.shape[1]} x {arr.shape[0]} pixels; measuring each ward ...")
    fr = worldcover.ward_fractions([w["geom"] for w in wards], arr, tr)
    missing = 0
    for w, f in zip(wards, fr):
        if f is None:
            missing += 1
            continue
        w.update(f)
        w["landcover_source"] = "worldcover"
    trees = sorted(w["tree_frac"] for w in wards if w.get("tree_frac") is not None)
    if trees:
        print(f"  tree cover per ward: median {trees[len(trees) // 2]:.0%}, range {trees[0]:.0%} to {trees[-1]:.0%}"
              f" ({missing} wards without pixels)")
    return True


def _download(url: str, dest: Path) -> None:
    import httpx

    tmp = dest.with_suffix(".part")
    with httpx.stream("GET", url, timeout=120, follow_redirects=True) as r:
        r.raise_for_status()
        size = int(r.headers.get("content-length") or 0)
        done = 0
        with open(tmp, "wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
                done += len(chunk)
                if size:
                    print(f"\r  {done / 1e6:.0f} of {size / 1e6:.0f} MB", end="", flush=True)
    print()
    tmp.replace(dest)


def landcover_only() -> None:
    """Refresh land cover in an existing wards.json without touching elevation or OpenStreetMap."""
    path = settings.data_dir / "wards.json"
    if not path.exists():
        sys.exit("data/wards.json does not exist yet. Run: python scripts/prepare_data.py")
    data = json.loads(path.read_text())
    wards = [{**w, "geom": shape(w["geometry"])} for w in data]
    cache_dir = settings.data_dir / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    if not satellite_landcover(wards, cache_dir):
        sys.exit(1)
    for w in wards:
        w.pop("geom")
    path.write_text(json.dumps(wards, separators=(",", ":")))
    print("done. wards.json updated with satellite land cover. Restart the server to use it.")


async def main(skip_osm: bool, skip_landcover: bool = False) -> None:
    raw = json.loads((settings.data_dir / "delhi_wards_raw.geojson").read_text())
    wards = []
    for i, f in enumerate(raw["features"]):
        geom = shape(f["geometry"]).buffer(0)
        code = (f["properties"].get("Ward_No") or str(i)).strip()
        rp = geom.representative_point()
        wards.append({"id": i, "code": code, "name": _clean_name(f["properties"].get("Ward_Name"), code),
                      "geom": geom, "lat": round(rp.y, 5), "lon": round(rp.x, 5), "area_km2": round(_area_km2(geom), 3)})
    print(f"{len(wards)} wards")

    # --- elevation (Copernicus DEM GLO-90 through Open-Meteo) ---
    samples, owner = [], []
    for w in wards:
        for p in interior_samples(w["geom"]):
            samples.append(p)
            owner.append(w["id"])
    print(f"elevation for {len(samples)} sample points ...")
    print("  (paced to stay inside Open-Meteo's free limits, about 3 minutes; safe to stop and re-run)")
    cache_dir = settings.data_dir / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    elev = await openmeteo.elevation(samples, progress_file=cache_dir / "elevation_progress.json")
    per: dict[int, list[float]] = {}
    for wid, e in zip(owner, elev):
        if e is not None and not math.isnan(e):
            per.setdefault(wid, []).append(e)
    for w in wards:
        es = per.get(w["id"]) or []
        w["elevation_m"] = round(sum(es) / len(es), 1) if es else None
        w["elevation_min_m"] = round(min(es), 1) if es else None

    have_sat = False if skip_landcover else satellite_landcover(wards, cache_dir)

    places, ways = [], []
    if not skip_osm:
        async def stage(name: str, fn):
            """Run one download once; later runs reuse the saved copy."""
            path = cache_dir / f"osm_{name}.json"
            if path.exists():
                print(f"OpenStreetMap {name}: using saved copy")
                return json.loads(path.read_text())
            print(f"OpenStreetMap {name} ...")
            value = await fn()
            path.write_text(json.dumps(value))
            return value

        places = await stage("places", lambda: osm.fetch_places(settings.bbox))
        print(f"  {len(places)} places")
        ways = await stage("waterways", lambda: osm.fetch_waterways(settings.bbox))
        print(f"  {len(ways)} waterways")
        try:
            if skip_landcover or have_sat:
                print("Using satellite land cover." if have_sat else "Skipping land cover (--skip-landcover).")
                raise RuntimeError("skipped")
            lc = await stage("landcover", lambda: osm.fetch_landcover(settings.bbox, cache_dir=cache_dir))
        except Exception as e:  # noqa: BLE001
            if str(e) != "skipped":
                print(f"  Land cover could not be downloaded ({e}).")
                print("  Continuing without green cover; run this script again later to add it.")
            lc = None
        if lc:
            green = [p for p in (_safe_poly(r) for r in lc["green"]) if p is not None]
            water = [p for p in (_safe_poly(r) for r in lc["water"]) if p is not None]
            print(f"  {len(green)} green, {len(water)} water polygons")
            for name, polys in (("green_frac", green), ("water_frac", water)):
                tree = STRtree(polys) if polys else None
                for w in wards:
                    idx = tree.query(w["geom"]) if tree is not None else []
                    if len(idx) == 0:
                        w[name] = 0.0
                        continue
                    inter = unary_union([polys[int(k)] for k in idx]).intersection(w["geom"])
                    w[name] = round(min(1.0, inter.area / w["geom"].area), 3) if w["geom"].area else 0.0
            for w in wards:
                w["landcover_source"] = "osm"

        rivers = [LineString(x["coords"]) for x in ways if x["kind"] == "river"]
        drains = [LineString(x["coords"]) for x in ways if x["kind"] in {"drain", "canal"}]
        for w in wards:
            p = Point(w["lon"], w["lat"])
            k = math.cos(math.radians(w["lat"])) * 111_320
            w["dist_river_m"] = round(min(r.distance(p) for r in rivers) * k) if rivers else None
            w["dist_drain_m"] = round(min(d.distance(p) for d in drains) * k) if drains else None

    out = []
    for w in wards:
        g = w.pop("geom").simplify(0.00012, preserve_topology=True)
        out.append({**w, "geometry": mapping(g)})
    (settings.data_dir / "wards.json").write_text(json.dumps(out, separators=(",", ":")))
    if not skip_osm:
        (settings.data_dir / "places.json").write_text(json.dumps(places, separators=(",", ":")))
        (settings.data_dir / "waterways.json").write_text(json.dumps(ways, separators=(",", ":")))
    await close_client()
    missing = sum(w.get("elevation_m") is None for w in out)
    print(f"done. wards.json written ({missing} wards without elevation).")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-osm", action="store_true")
    ap.add_argument("--skip-landcover", action="store_true", help="finish without green cover (add it later)")
    ap.add_argument("--landcover-only", action="store_true", help="only refresh satellite tree and land cover")
    args = ap.parse_args()
    if args.landcover_only:
        landcover_only()
    else:
        asyncio.run(main(args.skip_osm, args.skip_landcover))
