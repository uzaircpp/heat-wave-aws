import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="urbanshield-test-"))
(TMP / "data").mkdir()
shutil.copy(ROOT / "data" / "delhi_wards_raw.geojson", TMP / "data" / "delhi_wards_raw.geojson")


def _fake_worldcover(path: Path) -> None:
    """A stand-in for the ESA WorldCover tile: built-up city, the Ridge forest, a lake, some grass."""
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    res = 1 / 1200  # about 90 m; the real tile is 10 m
    west, north, cols, rows = 76.80, 28.92, int(0.58 / res), int(0.55 / res)
    arr = np.full((rows, cols), 50, dtype="uint8")

    def box(w, s, e, n, v):
        arr[int((north - n) / res):int((north - s) / res), int((w - west) / res):int((e - west) / res)] = v
    box(76.84, 28.42, 76.98, 28.60, 40)   # fields in the south-west
    box(77.15, 28.60, 77.19, 28.66, 10)   # the Ridge forest
    box(77.20, 28.60, 77.21, 28.61, 80)   # a lake
    with rasterio.open(path, "w", driver="GTiff", width=cols, height=rows, count=1, dtype="uint8", nodata=0,
                       crs="EPSG:4326", transform=from_origin(west, north, res, res)) as dst:
        dst.write(arr, 1)


_fake_worldcover(TMP / "worldcover_fake.tif")

os.environ.update({
    "WORLDCOVER_URL": str(TMP / "worldcover_fake.tif"),
    "DATA_DIR": str(TMP / "data"), "UPLOAD_DIR": str(TMP / "uploads"), "WEB_DIR": str(TMP / "web"),
    "DATABASE_URL": os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{TMP / 'test.db'}", "RUN_BACKGROUND_JOBS": "0", "OFFICER_TOKEN": "test-officer",
    "CONTACT_EMAIL": "test@example.org", "ELEVATION_PAUSE_S": "0",
})
sys.path.insert(0, str(ROOT))
