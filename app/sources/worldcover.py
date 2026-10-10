"""ESA WorldCover 2021 (v200): 10 m land cover from Sentinel-1 and Sentinel-2 satellites. CC BY 4.0.

Docs: https://esa-worldcover.org   Data: https://registry.opendata.aws/esa-worldcover-vito/
Tiles are 3 x 3 degree Cloud-Optimised GeoTIFFs named after their south-west corner (Delhi sits in
N27E075). We read only the window over the city (a few MB), not the whole tile.

Class codes: 10 tree cover, 20 shrubland, 30 grassland, 40 cropland, 50 built-up, 60 bare,
80 permanent water, 90 herbaceous wetland, 95 mangroves.
"""
from __future__ import annotations

import math
import os
from pathlib import Path

import numpy as np

URL = os.environ.get(
    "WORLDCOVER_URL",
    "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_{tile}_Map.tif",
)
TREE, SHRUB, GRASS, CROP, BUILT, BARE, WATER, WETLAND, MANGROVE = 10, 20, 30, 40, 50, 60, 80, 90, 95
GREEN = (TREE, SHRUB, GRASS, CROP, WETLAND, MANGROVE)
SOURCE = "ESA WorldCover 10 m 2021 v200 (Sentinel-1/2), CC BY 4.0"


def tile_name(lat: float, lon: float) -> str:
    la, lo = math.floor(lat / 3) * 3, math.floor(lon / 3) * 3
    return f"{'N' if la >= 0 else 'S'}{abs(la):02d}{'E' if lo >= 0 else 'W'}{abs(lo):03d}"


def tiles_for(bbox) -> list[str]:
    min_lon, min_lat, max_lon, max_lat = bbox
    names = []
    for lat in (min_lat, max_lat):
        for lon in (min_lon, max_lon):
            t = tile_name(lat, lon)
            if t not in names:
                names.append(t)
    return names


def _env():
    import rasterio

    opts = {"GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR", "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
            "GDAL_HTTP_MAX_RETRY": "5", "GDAL_HTTP_RETRY_DELAY": "3", "GDAL_HTTP_TIMEOUT": "60"}
    try:  # GDAL's own HTTP client needs a CA bundle on Windows; httpx already ships one
        import certifi
        opts["CURL_CA_BUNDLE"] = certifi.where()
    except ImportError:
        pass
    return rasterio.Env(**opts)


def read_window(bbox, cache: Path | None = None, pad: float = 0.01) -> tuple[np.ndarray, tuple]:
    """Land-cover classes over bbox (min_lon, min_lat, max_lon, max_lat) and the affine transform."""
    if cache and cache.exists():
        z = np.load(cache)
        return z["arr"], tuple(z["transform"])
    from rasterio.merge import merge
    import rasterio

    b = (bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad)
    urls = [URL.format(tile=t) for t in tiles_for(b)]
    with _env():
        srcs = [rasterio.open(u) for u in urls]
        try:
            arr, tr = merge(srcs, bounds=b, nodata=0)
        finally:
            for s in srcs:
                s.close()
    arr = arr[0]
    transform = tuple(tr)[:6]
    if cache:
        np.savez_compressed(cache, arr=arr, transform=np.array(transform))
    return arr, transform


def ward_fractions(geoms: list, arr: np.ndarray, transform: tuple) -> list[dict]:
    """Share of each ward's area that is trees, all vegetation, built-up and water."""
    from affine import Affine
    from rasterio.features import rasterize

    tr = Affine(*transform)
    n = len(geoms) + 1
    out = [None] * len(geoms)
    for all_touched in (False, True):  # tiny wards may not contain a pixel centre; retry those
        todo = [i for i in range(len(geoms)) if out[i] is None]
        if not todo:
            break
        labels = rasterize([(geoms[i], i + 1) for i in todo], out_shape=arr.shape, transform=tr, fill=0,
                           dtype="int32", all_touched=all_touched)
        valid = (arr > 0) & (labels > 0)
        lab, cls = labels[valid], arr[valid]
        total = np.bincount(lab, minlength=n)

        def share(mask):
            return np.bincount(lab[mask], minlength=n)

        tree, green = share(cls == TREE), share(np.isin(cls, GREEN))
        built, water = share(cls == BUILT), share(cls == WATER)
        for i in todo:
            t = total[i + 1]
            if t == 0:
                continue
            out[i] = {"tree_frac": round(tree[i + 1] / t, 3), "green_frac": round(green[i + 1] / t, 3),
                      "built_frac": round(built[i + 1] / t, 3), "water_frac": round(water[i + 1] / t, 3),
                      "landcover_pixels": int(t)}
    return out
