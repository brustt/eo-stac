"""Search and download Sentinel-2 L2A products from Element84 Earth Search.

Only a 10 km x 10 km (100 km2) area around Paris is downloaded: each raster
asset is written as a COG Subset window rather than the whole tile.

Run with::

    uv run python scripts/element84_s2.py [output_dir]
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

from eo_stac import Sentinel2Params, STACSource, download_items, search_items

API = "https://earth-search.aws.element84.com/v1"
COLLECTION = "sentinel-2-l2a"
PARIS = (2.3522, 48.8566)  # lon, lat
AREA_KM2 = 100.0
START_DATE: str = "2026-08-01"
END_DATE: str = "2026-08-15"
MAX_CLOUD_COVER = 5.0
ASSETS = ["red", "green", "blue", "nir"]


def square_bbox(lon: float, lat: float, area_km2: float) -> list[float]:
    """Return a ``[west, south, east, north]`` square of *area_km2* around a point."""
    half_km = math.sqrt(area_km2) / 2
    dlat = half_km / 111.32
    dlon = half_km / (111.32 * math.cos(math.radians(lat)))
    return [lon - dlon, lat - dlat, lon + dlon, lat + dlat]


def main(output_dir: Path) -> None:
    bbox = square_bbox(*PARIS, AREA_KM2)
    source = STACSource.from_url(API)
    gdf = search_items(
        source,
        collections=[COLLECTION],
        bbox=bbox,
        datetime=f"{START_DATE}/{END_DATE}",
        params=Sentinel2Params(max_cloud_cover=MAX_CLOUD_COVER),
    )
    print(f"{len(gdf)} Sentinel-2 L2A items found (cloud cover <= {MAX_CLOUD_COVER}%)")
    if gdf.empty:
        return

    gdf = download_items(gdf, output_dir, assets=ASSETS, bbox=bbox)
    print(gdf[["item_id", "datetime", "cloud_cover", "local_paths"]].to_string())


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "data/s2_paris"))
