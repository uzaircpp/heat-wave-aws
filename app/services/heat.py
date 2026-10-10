"""Heat stress for outdoor work, and the heat scenario simulator.

Safe hours use WBGT, the index occupational heat standards are written in:
* Wet-bulb temperature from air temperature and humidity: Stull (2011), J. Appl. Meteor. Climatol. 50.
* WBGT in shade = 0.7 x natural wet bulb + 0.3 x air temperature (no globe term in shade).
* Sun adds up to about 3 C at full solar radiation (scaled by Open-Meteo shortwave radiation),
  in line with the globe-temperature contribution in Liljegren et al. (2008) for light wind.
* Limits per workload follow the ACGIH TLV (acclimatised workers) and Action Limit (unacclimatised):
  above the TLV is unsafe for continuous work, between AL and TLV needs a work/rest cycle.
These are screening values, not medical advice. Edit ROLES if your city adopts its own heat action plan.
"""
from __future__ import annotations

import math

from ..data import Ward, city
from .weather import Weather, apparent_from, weather

ROLES = {
    # role: (label, workload, action_limit_wbgt, tlv_wbgt)
    "vendor": ("Street vendor", "light", 28.0, 31.0),
    "rider": ("Delivery rider", "moderate", 25.0, 28.0),
    "construction": ("Construction", "heavy", 23.0, 26.5),
}


def wet_bulb_stull(temp_c: float, rh: float) -> float:
    """Stull 2011 wet-bulb (C). Valid for RH 5-99% and -20 to 50 C, error within about 1 C."""
    rh = max(5.0, min(99.0, rh))
    return (temp_c * math.atan(0.151977 * (rh + 8.313659) ** 0.5) + math.atan(temp_c + rh)
            - math.atan(rh - 1.676331) + 0.00391838 * rh ** 1.5 * math.atan(0.023101 * rh) - 4.686035)


def wbgt_shade(temp_c: float, rh: float) -> float:
    return 0.7 * wet_bulb_stull(temp_c, rh) + 0.3 * temp_c


def wbgt_sun(temp_c: float, rh: float, shortwave_wm2: float | None) -> float:
    sw = max(0.0, shortwave_wm2 or 0.0)
    return wbgt_shade(temp_c, rh) + min(3.0, 3.0 * sw / 1000.0)


def category(wbgt: float, role: str) -> str:
    _, _, al, tlv = ROLES[role]
    return "unsafe" if wbgt >= tlv else "caution" if wbgt >= al else "safe"


def _fmt_hour(h: int) -> str:
    return f"{(h % 12) or 12}:00 {'PM' if h >= 12 else 'AM'}"


def safe_hours(ward: Ward, role: str, day_offset: int = 0) -> dict:
    if role not in ROLES:
        raise ValueError(f"role must be one of {sorted(ROLES)}")
    hourly = weather.ward_hourly(ward)
    times = hourly["time"]
    i_now = weather.now_index()
    day = times[i_now][:10] if day_offset == 0 else None
    if day is None:
        from datetime import date, timedelta
        day = (date.fromisoformat(times[i_now][:10]) + timedelta(days=day_offset)).isoformat()
    hours = []
    for k, t in enumerate(times):
        if t[:10] != day or not 6 <= int(t[11:13]) <= 22:
            continue
        T, rh = hourly["temperature_2m"][k], hourly["relative_humidity_2m"][k]
        if T is None or rh is None:
            continue
        w = wbgt_sun(T, rh, hourly["shortwave_radiation"][k])
        hours.append({
            "time": t, "hour": int(t[11:13]), "temperature": T, "feels_like": hourly["apparent_temperature"][k],
            "humidity": rh, "uv_index": hourly["uv_index"][k], "wbgt": round(w, 1), "category": category(w, role),
            "is_now": k == i_now,
        })
    unsafe = [h["hour"] for h in hours if h["category"] == "unsafe"]
    now_h = next((h for h in hours if h["is_now"]), None)
    rest_until = None
    if now_h and now_h["category"] == "unsafe":
        later = [h["hour"] for h in hours if h["hour"] > now_h["hour"] and h["category"] != "unsafe"]
        rest_until = _fmt_hour(later[0]) if later else "end of day"
    label, workload, al, tlv = ROLES[role]
    advice = {
        "safe": "Normal work is fine. Drink water every 30 minutes.",
        "caution": "Work 45 minutes, rest 15 minutes in shade every hour. Drink a cup of water every 20 minutes.",
        "unsafe": "Stop continuous work. Rest in shade, drink water, and resume when the hour turns caution or safe.",
    }
    return {
        "ward": ward.public(), "role": role, "role_label": label, "workload": workload,
        "limits_wbgt": {"action_limit": al, "tlv": tlv},
        "now": now_h, "rest_until": rest_until,
        "unsafe_window": [_fmt_hour(unsafe[0]), _fmt_hour(unsafe[-1] + 1)] if unsafe else None,
        "advice": advice[now_h["category"]] if now_h else None,
        "hours": hours,
        "method": "WBGT (Stull wet bulb + solar term) against ACGIH TLV / Action Limit for the workload.",
    }


def heat_scenario(air_temp_c: float, hour: int, extra_green_pct: float, cool_roofs: bool) -> dict:
    """Ward-by-ward feels-like for a what-if day. Humidity and wind for that hour come from the real
    forecast; air temperature is the scenario value; trees and cool roofs change the local adjustment."""
    hourly_times = weather.times()
    i_now = weather.now_index()
    day = hourly_times[i_now][:10]
    k = next((i for i, t in enumerate(hourly_times) if t[:10] == day and int(t[11:13]) == hour), i_now)
    out = []
    for w in city().wards:
        h = weather.ward_hourly(w)
        rh = h["relative_humidity_2m"][k] or 40
        wind = (h["wind_speed_10m"][k] or 5) / 3.6
        uhi = Weather.uhi(w, extra_tree=extra_green_pct / 100)
        built = w.built_frac if w.built_frac is not None else 1 - min(1.0, (w.green_frac or 0.15) + extra_green_pct / 100)
        roof = -0.6 * built if cool_roofs else 0.0  # reflective roofs cool in proportion to roof area
        diurnal = 0.35 + 0.65 * math.exp(-((hour - 15) / 4.2) ** 2)  # scenario temp is the 3 PM peak
        t_air = 27 + (air_temp_c - 27) * diurnal
        feels = apparent_from(t_air + uhi + roof, rh, wind)
        out.append({"ward_id": w.id, "feels_like": round(feels, 1)})
    vals = [o["feels_like"] for o in out]
    return {
        "wards": out, "max": max(vals), "mean": round(sum(vals) / len(vals), 1),
        "wards_above_45": sum(v >= 45 for v in vals),
        "inputs": {"air_temp_c": air_temp_c, "hour": hour, "extra_green_pct": extra_green_pct, "cool_roofs": cool_roofs},
        "method": "Steadman apparent temperature with forecast humidity and wind; urban heat adjustment from "
                  "satellite tree and built-up cover (ESA WorldCover 10 m).",
    }
