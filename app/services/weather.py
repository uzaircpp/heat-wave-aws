"""City weather from Open-Meteo, refreshed on a schedule and interpolated to every ward.

Open-Meteo's models resolve roughly 2-11 km, so neighbouring wards share the same forecast. We add a
small, documented urban heat island adjustment from each ward's measured land cover, so leafy wards read
cooler than dense concrete ones:

* satellite land cover (ESA WorldCover 10 m): relative to the city average, +UHI_BUILT_C for a fully
  built-up ward and -UHI_TREE_C for full tree canopy (default 2.0 and 4.0 degrees, i.e. about -0.4 C per
  10% more canopy, inside the range reported in urban canopy studies), capped at +/-UHI_MAX_C;
* otherwise OpenStreetMap green share, as a fallback.

It is a screening adjustment for ranking wards, not a microclimate model. Set UHI_MAX_C=0 to turn it off.
"""
from __future__ import annotations

import logging
import math
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from ..config import settings
from ..data import Ward, city
from ..geo import grid_points, haversine_m
from ..sources import openmeteo
from ..sources.http import TTLCache, UpstreamError

log = logging.getLogger("urbanshield.weather")
UHI_MAX_C = float(os.environ.get("UHI_MAX_C", "2.0"))
UHI_BUILT_C = float(os.environ.get("UHI_BUILT_C", "2.0"))
UHI_TREE_C = float(os.environ.get("UHI_TREE_C", "4.0"))
_means: dict = {}


def _city_means() -> tuple[float, float]:
    c = city()
    if _means.get("city") is not c:
        ws = [w for w in c.wards if w.tree_frac is not None and w.built_frac is not None]
        _means.update(city=c, tree=sum(w.tree_frac for w in ws) / max(1, len(ws)),
                      built=sum(w.built_frac for w in ws) / max(1, len(ws)))
    return _means["tree"], _means["built"]
_cache = TTLCache("weather")
KEY = "grid"


class Weather:
    def __init__(self):
        self.points = grid_points(settings.bbox, settings.weather_grid)
        self.grid: list[dict] | None = None
        self.fetched_at: float | None = None
        self._weights: dict[int, list[tuple[int, float]]] = {}

    # ---------- refresh ----------
    async def refresh(self, force: bool = False) -> None:
        cached = _cache.get(KEY, settings.weather_refresh_s)
        if cached and not force:
            self._set(cached)
            return
        try:
            data = await openmeteo.forecast(self.points, past_days=1, forecast_days=7)
            _cache.set(KEY, data)
            self._set(data)
            log.info("weather refreshed: %d points", len(data))
        except UpstreamError as e:
            stale = _cache.get(KEY, 0, allow_stale=True)
            if stale:
                log.warning("weather refresh failed, serving cached data: %s", e)
                self._set(stale)
            else:
                raise

    def _set(self, data: list[dict]) -> None:
        self.grid = data
        self.fetched_at = time.time() - (_cache.age(KEY) or 0)

    @property
    def ready(self) -> bool:
        return bool(self.grid)

    def status(self) -> dict:
        return {"ready": self.ready, "points": len(self.points),
                "age_s": None if self.fetched_at is None else round(time.time() - self.fetched_at),
                "source": "Open-Meteo (open-meteo.com), CC BY 4.0"}

    # ---------- interpolation ----------
    def _w(self, ward: Ward) -> list[tuple[int, float]]:
        if ward.id not in self._weights:
            d = sorted((haversine_m(ward.lat, ward.lon, p[0], p[1]), i) for i, p in enumerate(self.points))[:4]
            if d[0][0] < 1:
                self._weights[ward.id] = [(d[0][1], 1.0)]
            else:
                ws = [(i, 1 / dist**2) for dist, i in d]
                tot = sum(w for _, w in ws)
                self._weights[ward.id] = [(i, w / tot) for i, w in ws]
        return self._weights[ward.id]

    def _blend(self, ward: Ward, section: str, var: str) -> list[float | None]:
        ws = self._w(ward)
        series = [self.grid[i][section][var] for i, _ in ws]
        out = []
        for k in range(len(series[0])):
            vals = [(s[k], w) for s, (_, w) in zip(series, ws) if s[k] is not None]
            if not vals:
                out.append(None)
                continue
            tot = sum(w for _, w in vals)
            out.append(sum(v * w for v, w in vals) / tot)
        return out

    def _blend_current(self, ward: Ward, var: str) -> float | None:
        vals = [(self.grid[i]["current"].get(var), w) for i, w in self._w(ward)]
        vals = [(v, w) for v, w in vals if v is not None]
        if not vals:
            return None
        tot = sum(w for _, w in vals)
        return sum(v * w for v, w in vals) / tot

    @staticmethod
    def uhi(ward: Ward, extra_tree: float = 0.0) -> float:
        """Degrees to add for this ward's land cover; extra_tree is new canopy (0-1) planted on built land."""
        if UHI_MAX_C == 0:
            return 0.0
        if ward.tree_frac is not None and ward.built_frac is not None:
            mean_tree, mean_built = _city_means()
            tree = min(1.0, ward.tree_frac + extra_tree)
            built = max(0.0, ward.built_frac - extra_tree)
            d = UHI_BUILT_C * (built - mean_built) - UHI_TREE_C * (tree - mean_tree)
            return round(max(-UHI_MAX_C, min(UHI_MAX_C, d)), 2)
        if ward.green_frac is None:
            return 0.0
        g = max(0.0, min(0.5, ward.green_frac + extra_tree))  # OpenStreetMap fallback
        return round(1.5 * (0.35 - g) / 0.35, 2)

    # ---------- public views ----------
    def times(self) -> list[str]:
        return self.grid[0]["hourly"]["time"]

    def now_index(self) -> int:
        """Index of the current hour in the forecast (times are in the city's timezone)."""
        now = city_now()
        ts = self.times()
        stamp = now.strftime("%Y-%m-%dT%H:00")
        if stamp in ts:
            return ts.index(stamp)
        target = now.replace(tzinfo=None)
        return min(range(len(ts)), key=lambda i: abs((datetime.fromisoformat(ts[i]) - target).total_seconds()))

    def ward_hourly(self, ward: Ward) -> dict:
        dt = self.uhi(ward)
        out = {"time": self.times()}
        for var in openmeteo.HOURLY:
            vals = self._blend(ward, "hourly", var)
            if var in {"temperature_2m", "apparent_temperature"}:
                vals = [None if v is None else round(v + dt, 1) for v in vals]
            else:
                vals = [None if v is None else round(v, 2) for v in vals]
            out[var] = vals
        return out

    def ward_now(self, ward: Ward) -> dict:
        dt = self.uhi(ward)
        cur = {var: self._blend_current(ward, var) for var in openmeteo.CURRENT}
        for k in ("temperature_2m", "apparent_temperature"):
            if cur[k] is not None:
                cur[k] = round(cur[k] + dt, 1)
        cur["uhi_adjust_c"] = dt
        cur["time"] = self.grid[0]["current"]["time"]
        return {k: (round(v, 1) if isinstance(v, float) else v) for k, v in cur.items()}

    def ward_daily(self, ward: Ward) -> dict:
        out = {"time": self.grid[0]["daily"]["time"]}
        dt = self.uhi(ward)
        for var in openmeteo.DAILY:
            if var in {"sunrise", "sunset"}:
                out[var] = self.grid[self._w(ward)[0][0]]["daily"][var]
                continue
            vals = self._blend(ward, "daily", var)
            if var.startswith(("temperature", "apparent")):
                vals = [None if v is None else v + dt for v in vals]
            out[var] = [None if v is None else round(v, 1) for v in vals]
        return out

    def city_layer(self, metric: str, hour_offset: int = 0) -> list[dict]:
        """One value per ward: feels-like now (or +N hours), or rain over the next 6 hours."""
        i0 = self.now_index() + hour_offset
        out = []
        for w in city().wards:
            if metric == "feels_like":
                v = self._blend(w, "hourly", "apparent_temperature")[i0]
                v = None if v is None else round(v + self.uhi(w), 1)
            elif metric == "rain_6h":
                series = self._blend(w, "hourly", "precipitation")[i0:i0 + 6]
                v = round(sum(x or 0 for x in series), 1)
            elif metric == "temperature":
                v = self._blend(w, "hourly", "temperature_2m")[i0]
                v = None if v is None else round(v + self.uhi(w), 1)
            else:
                raise ValueError(metric)
            out.append({"ward_id": w.id, "value": v})
        return out

    def city_summary(self) -> dict:
        wards = city().wards
        feels = [x["value"] for x in self.city_layer("feels_like") if x["value"] is not None]
        rain = [x["value"] for x in self.city_layer("rain_6h")]
        i0 = self.now_index()
        mean = lambda xs: round(sum(xs) / len(xs), 1) if xs else None  # noqa: E731
        hourly_city = []
        for k in range(i0, min(i0 + 24, len(self.times()))):
            vals = [g["hourly"]["apparent_temperature"][k] for g in self.grid if g["hourly"]["apparent_temperature"][k] is not None]
            prs = [g["hourly"]["precipitation"][k] or 0 for g in self.grid]
            hourly_city.append({"time": self.times()[k], "feels_like": mean(vals), "rain_mm": mean(prs)})
        return {
            "feels_like_mean": mean(feels), "feels_like_max": max(feels) if feels else None,
            "rain_6h_mean": mean(rain), "rain_6h_max": max(rain) if rain else None,
            "wards": len(wards), "next_24h": hourly_city, **self.status(),
        }


def city_now() -> datetime:
    return datetime.now(ZoneInfo(settings.timezone))


weather = Weather()


async def unsafe_hours_history(ward: Ward, days: int, threshold_c: float) -> list[dict]:
    """Hours per day with feels-like at or above threshold, from Open-Meteo's recent past."""
    key = f"hist:{ward.id}:{days}"
    cached = _cache.get(key, 3 * 3600)
    data = cached or await openmeteo.recent_history(ward.lat, ward.lon, days)
    if not cached:
        _cache.set(key, data)
    dt = Weather.uhi(ward)
    per_day: dict[str, int] = {}
    today = city_now().strftime("%Y-%m-%d")
    for t, v in zip(data["hourly"]["time"], data["hourly"]["apparent_temperature"]):
        day = t[:10]
        if day >= today:
            continue
        per_day.setdefault(day, 0)
        if v is not None and v + dt >= threshold_c:
            per_day[day] += 1
    return [{"date": d, "hours": h} for d, h in sorted(per_day.items())][-days:]


def apparent_from(temp_c: float, rh: float, wind_ms: float = 1.0) -> float:
    """Steadman apparent temperature (the formula family Open-Meteo uses), shade, no radiation."""
    e = rh / 100 * 6.105 * math.exp(17.27 * temp_c / (237.7 + temp_c))
    return temp_c + 0.33 * e - 0.70 * wind_ms - 4.0
