"""Small geometry helpers. Coordinates are (lon, lat) unless a name says otherwise."""
from __future__ import annotations

import math
from typing import Iterable, Sequence

EARTH_R = 6_371_000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(a))


def point_segment_m(lat: float, lon: float, a: Sequence[float], b: Sequence[float]) -> float:
    """Distance in metres from a point to segment a-b (each (lon, lat)), on a local flat projection."""
    k = math.cos(math.radians(lat)) * 111_320.0
    px, py = lon * k, lat * 110_540.0
    ax, ay, bx, by = a[0] * k, a[1] * 110_540.0, b[0] * k, b[1] * 110_540.0
    dx, dy = bx - ax, by - ay
    if dx == dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def point_line_m(lat: float, lon: float, line: Sequence[Sequence[float]]) -> float:
    return min(point_segment_m(lat, lon, line[i], line[i + 1]) for i in range(len(line) - 1))


def idw(lat: float, lon: float, samples: Iterable[tuple[float, float, float]], power: float = 2.0) -> float:
    """Inverse distance weighting over (lat, lon, value) samples."""
    num = den = 0.0
    for slat, slon, value in samples:
        d = haversine_m(lat, lon, slat, slon)
        if d < 1:
            return value
        w = 1.0 / d**power
        num += w * value
        den += w
    return num / den if den else float("nan")


def grid_points(bbox: Sequence[float], n: int) -> list[tuple[float, float]]:
    """n x n (lat, lon) points spread over a bbox (min_lon, min_lat, max_lon, max_lat)."""
    min_lon, min_lat, max_lon, max_lat = bbox
    out = []
    for i in range(n):
        for j in range(n):
            lat = min_lat + (max_lat - min_lat) * (i + 0.5) / n
            lon = min_lon + (max_lon - min_lon) * (j + 0.5) / n
            out.append((round(lat, 4), round(lon, 4)))
    return out
