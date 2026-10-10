"""SACHET (NDMA): India's official Common Alerting Protocol feed. IMD, CWC and state disaster
authorities publish warnings here. Feed list: https://sachet.ndma.gov.in/CapFeed
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime

from shapely.geometry import Polygon, box

from ..config import settings
from .http import get, UpstreamError

log = logging.getLogger("urbanshield.sachet")
CAP_NS = {"cap": "urn:oasis:names:tc:emergency:cap:1.2"}


def parse_rss(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    items = []
    for it in root.iter("item"):
        items.append({
            "title": (it.findtext("title") or "").strip(),
            "link": (it.findtext("link") or "").strip(),
            "guid": (it.findtext("guid") or it.findtext("link") or "").strip(),
            "pub_date": (it.findtext("pubDate") or "").strip(),
            "author": (it.findtext("author") or "").strip(),
        })
    return items


def _iso(s: str | None) -> str | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s).isoformat()
    except ValueError:
        return None


def parse_cap(xml_text: str) -> dict | None:
    root = ET.fromstring(xml_text)
    info = root.find("cap:info", CAP_NS)
    if info is None:
        return None

    def t(path: str, node=info) -> str:
        return (node.findtext(path, default="", namespaces=CAP_NS) or "").strip()

    polygons, areas = [], []
    for area in info.findall("cap:area", CAP_NS):
        areas.append(t("cap:areaDesc", area))
        for poly in area.findall("cap:polygon", CAP_NS):
            pts = []
            for pair in (poly.text or "").split():
                try:
                    lat, lon = (float(v) for v in pair.split(",")[:2])
                    pts.append((lon, lat))  # CAP is lat,lon; we store lon,lat
                except ValueError:
                    continue
            if len(pts) >= 3:
                polygons.append(pts)
    return {
        "identifier": t("cap:identifier", root), "sender": t("cap:senderName") or t("cap:sender", root),
        "event": t("cap:event"), "category": t("cap:category"), "urgency": t("cap:urgency"),
        "severity": t("cap:severity"), "certainty": t("cap:certainty"), "headline": t("cap:headline"),
        "description": t("cap:description"), "instruction": t("cap:instruction"),
        "effective": _iso(t("cap:effective") or t("cap:onset")), "expires": _iso(t("cap:expires")),
        "areas": areas, "polygons": polygons,
    }


def relevant_to_city(alert: dict, bbox, city_name: str) -> bool:
    city_box = box(*bbox)
    for pts in alert.get("polygons", []):
        try:
            if Polygon(pts).buffer(0).intersects(city_box):
                return True
        except ValueError:
            continue
    text = " ".join(alert.get("areas", [])) + " " + alert.get("headline", "")
    return bool(re.search(rf"\b{re.escape(city_name)}\b", text, re.I))


async def fetch_city_alerts(max_items: int = 60) -> list[dict]:
    rss = await get(settings.sachet_rss_url, as_text=True, headers={"Accept": "application/rss+xml,*/*"})
    out = []
    for item in parse_rss(rss)[:max_items]:
        if not item["link"]:
            continue
        try:
            cap = parse_cap(await get(item["link"], as_text=True, retries=2, headers={"Accept": "application/xml,*/*"}))
        except (UpstreamError, ET.ParseError) as e:
            log.info("skip CAP %s: %s", item["link"], e)
            continue
        if cap and relevant_to_city(cap, settings.bbox, settings.city_name):
            cap["link"] = item["link"]
            out.append(cap)
    return out
