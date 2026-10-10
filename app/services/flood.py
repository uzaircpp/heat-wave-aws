"""Rainfall to street-water screening model, driven by real terrain.

Inputs per ward (from scripts/prepare_data.py):
* mean and minimum elevation, Copernicus DEM GLO-90 (via Open-Meteo elevation API)
* green / water share from OpenStreetMap land cover, which sets the runoff coefficient
* distance to drains and the Yamuna from OpenStreetMap waterways

Steps for a storm of R mm over `hours` hours:
1. runoff = R x C, C = 0.90 for sealed ground, 0.30 for green ground (Rational Method coefficients)
2. drains remove `capacity` mm/h (DRAIN_CAPACITY_MMH, about 25 mm/h when cleaned, half when choked)
3. what is left ponds where water collects: wards lower than their neighbours (depression in metres,
   capped at 4 m) and the lowest wards in the city (elevation percentile, cubed so only the very
   lowest stand out). The weights are normalised so the city-wide average depth equals
   excess x ponding factor, so water is moved around, not created.
4. depth = excess x ponding factor x ward weight / mean weight
Calibrated on Delhi's real terrain: 80 mm in 2 h floods a handful of wards knee-deep with clean drains
and about a third of wards with choked drains.

It is a screening model to rank wards, not a hydraulic simulation. Calibrate FLOOD_PONDING with past
waterlogging reports from your own database (see /api/simulate/flood/calibration).
"""
from __future__ import annotations

import os

from ..data import city
from ..geo import haversine_m

DRAIN_CAPACITY_MMH = float(os.environ.get("DRAIN_CAPACITY_MMH", "25"))
FLOOD_PONDING = float(os.environ.get("FLOOD_PONDING", "3"))
DEPRESSION_CAP_M = float(os.environ.get("DEPRESSION_CAP_M", "4"))


class NotPrepared(RuntimeError):
    pass


def _ward_terrain():
    wards = city().wards
    if any(w.elevation_m is None for w in wards):
        raise NotPrepared("Ward elevations are missing. Run: python scripts/prepare_data.py")
    elev = sorted(w.elevation_min_m if w.elevation_min_m is not None else w.elevation_m for w in wards)
    n = len(elev)
    out = {}
    for w in wards:
        nb = [wards[i].elevation_m for i in w.neighbours if wards[i].elevation_m is not None]
        depression = max(0.0, (sum(nb) / len(nb) - w.elevation_m)) if nb else 0.0
        low = w.elevation_min_m if w.elevation_min_m is not None else w.elevation_m
        rank = sum(e <= low for e in elev) / n  # 0 = lowest in city
        out[w.id] = {"depression": depression, "lowness": 1 - rank}
    for v in out.values():
        v["weight"] = 0.15 + 3 * min(v["depression"], DEPRESSION_CAP_M) / DEPRESSION_CAP_M + 2 * v["lowness"] ** 3
    mean_w = sum(v["weight"] for v in out.values()) / len(out)
    for v in out.values():
        v["weight"] /= mean_w
    return out


def depth_label(m: float) -> str:
    if m < 0.05:
        return "dry"
    if m < 0.15:
        return "ankle"
    if m < 0.45:
        return "knee"
    if m < 0.9:
        return "waist"
    return "above_waist"


def flood_scenario(rain_mm: float, hours: float = 2.0, drains_clean: bool = True,
                   extra_storage_mm: float = 0.0) -> dict:
    terrain = _ward_terrain()
    cap = DRAIN_CAPACITY_MMH * (1.0 if drains_clean else 0.5) * hours + extra_storage_mm
    wards = city().wards
    rows = []
    for w in wards:
        g = w.green_frac if w.green_frac is not None else 0.15
        wat = w.water_frac or 0.0
        if w.built_frac is not None:  # satellite land cover: sealed, vegetated, open water, bare soil
            c = 0.90 * w.built_frac + 0.30 * g + 0.60 * max(0.0, 1 - w.built_frac - g - wat)
        else:
            c = 0.90 * (1 - g - wat) + 0.30 * g
        excess = max(0.0, rain_mm * c - cap)
        t = terrain[w.id]
        near_drain = 1.15 if (w.dist_drain_m is not None and w.dist_drain_m < 400 and not drains_clean) else 1.0
        depth = excess / 1000 * FLOOD_PONDING * t["weight"] * near_drain
        rows.append({"ward_id": w.id, "depth_m": round(depth, 2), "label": depth_label(depth)})
    by_id = {r["ward_id"]: r for r in rows}
    underpasses = []
    for p in city().places:
        if p["kind"] != "underpass":
            continue
        ward = city().nearest_ward(p["lat"], p["lon"])
        # an underpass sits below the street, so it fills first; pumps usually stop it past about 1.8 m
        street = by_id[ward.id]["depth_m"]
        d = min(1.8, street * 1.6 + 0.12) if street >= 0.05 else 0.0
        if d >= 0.15:
            underpasses.append({"name": p["name"], "lat": p["lat"], "lon": p["lon"], "ward_id": ward.id,
                                "depth_m": round(d, 2), "label": depth_label(d)})
    underpasses.sort(key=lambda u: -u["depth_m"])
    knee = [r for r in rows if r["depth_m"] >= 0.15]
    return {
        "wards": rows, "wards_wet": len(knee), "wards_unsafe": sum(r["depth_m"] >= 0.45 for r in rows),
        "underpasses_unsafe": len([u for u in underpasses if u["depth_m"] >= 0.3]), "underpasses": underpasses[:25],
        "inputs": {"rain_mm": rain_mm, "hours": hours, "drains_clean": drains_clean, "extra_storage_mm": extra_storage_mm,
                   "drain_capacity_mm": round(cap, 1)},
        "method": __doc__.strip().splitlines()[0],
    }


def calibration(reports: list[dict], rain_mm: float) -> dict:
    """How well the model ranks wards against real waterlogging reports (hit rate in top 20%)."""
    res = flood_scenario(rain_mm)
    ranked = sorted(res["wards"], key=lambda r: -r["depth_m"])
    top = {r["ward_id"] for r in ranked[: max(1, len(ranked) // 5)]}
    wards = [city().nearest_ward(r["lat"], r["lon"]).id for r in reports]
    hits = sum(w in top for w in wards)
    return {"reports": len(wards), "in_top_20pct": hits, "hit_rate": round(hits / len(wards), 2) if wards else None,
            "ponding_factor": FLOOD_PONDING, "drain_capacity_mmh": DRAIN_CAPACITY_MMH}


def nearest_underpass(lat: float, lon: float) -> dict | None:
    ups = [p for p in city().places if p["kind"] == "underpass"]
    if not ups:
        return None
    best = min(ups, key=lambda p: haversine_m(lat, lon, p["lat"], p["lon"]))
    return {**best, "distance_m": round(haversine_m(lat, lon, best["lat"], best["lon"]))}
