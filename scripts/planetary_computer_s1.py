"""Search and download Sentinel-1 RTC products from Planetary Computer.

Only the area of ``BBOX_4326`` is downloaded: each raster asset is written as a
COG Subset window rather than the whole scene.

Run with::

    uv run --extra bench python scripts/planetary_computer_s1.py [output_dir]
"""

from __future__ import annotations

import sys
from pathlib import Path

import planetary_computer

from eo_stac import Sentinel1Params, STACSource, download_items, search_items

API = "https://planetarycomputer.microsoft.com/api/stac/v1"
COLLECTION = "sentinel-1-rtc"
BBOX_4326 = [-0.765953, 45.754828, -0.548630, 45.863477]
START_DATE: str = "2026-01-15"
END_DATE: str = "2026-01-30"
ASSETS = ["vv"]
RELATIVE_ORBITS: list[int] | None = [8]  # e.g. [30, 132]; None keeps all


def main(output_dir: Path) -> None:
    source = STACSource.from_url(API)
    gdf = search_items(
        source,
        collections=[COLLECTION],
        bbox=BBOX_4326,
        datetime=f"{START_DATE}/{END_DATE}",
        params=Sentinel1Params(
            orbit_state="descending",
            instrument_mode="IW",
            orbit_number=RELATIVE_ORBITS,
            polarizations=["VV", "VH"],
        ),
    )
    print(f"{len(gdf)} Sentinel-1 RTC items found")
    if gdf.empty:
        return

    gdf = download_items(
        gdf,
        output_dir,
        assets=ASSETS,
        bbox=BBOX_4326,
        sign=planetary_computer.sign,
    )
    print(gdf[["item_id", "datetime", "local_paths"]].to_string())


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "data/s1_rtc"))
