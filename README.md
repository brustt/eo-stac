# eostac

A Python library for searching and downloading satellite imagery via STAC (SpatioTemporal Asset Catalog). Works with any STAC-compliant catalog — static JSON files or HTTP APIs — and returns structured GeoDataFrame results with concurrent downloads and folium-based visualization.

## Installation

```bash
uv sync
```

## Quick start

```python
from eostac import STACSource, search_items, download_items, viz

# Connect to a STAC API
source = STACSource.from_url("https://earth-search.aws.element84.com/v1")

# Search for items
gdf = search_items(
    source,
    collections=["sentinel-2-l2a"],
    bbox=[10.0, 45.0, 12.0, 47.0],
    datetime="2020-06-01/2020-09-01",
    max_cloud_cover=20.0,
)

# Download assets
gdf = download_items(gdf, output_dir="downloads/", assets=["B04", "B03", "B02"])

# Visualize
m = viz.plot_footprints(gdf, column="cloud_cover")
m.save("map.html")
```

## API

### `STACSource`

Unified entry point for STAC APIs and static catalogs.

```python
# STAC API (pystac-client)
source = STACSource.from_url("https://...")

# With authentication
source = STACSource.from_url("https://...", headers={"Authorization": "Bearer TOKEN"})

# Local static catalog
source = STACSource.from_json("path/to/catalog.json")

source.is_api        # True for STAC APIs, False for static catalogs
source.collections   # list of collection IDs
```

### `search_items()`

```python
gdf = search_items(
    source,
    collections=["sentinel-2-l2a"],   # filter by collection
    bbox=[west, south, east, north],  # spatial filter
    polygon=shapely_geom,             # alternative spatial filter (GeoJSON dict or Shapely)
    datetime="2020-01-01/2020-12-31", # temporal filter (ISO-8601 range or (start, end) tuple)
    max_cloud_cover=20.0,             # 0–100
    product_type="L2A",
    limit=100,
)
```

Returns a `GeoDataFrame` with columns: `geometry`, `item_id`, `collection`, `datetime`, `cloud_cover`, `assets` (dict of key → href), plus all other flattened STAC properties.

Filters are pushed server-side for STAC APIs. For static catalogs, filtering is applied client-side by walking all items.

### `download_items()`

Downloads run concurrently via `asyncio` and `httpx`. All assets start immediately; `max_workers` caps concurrent connections via a semaphore. When `process=True`, rioxarray/GDAL encoding runs in a worker thread so the event loop stays unblocked.

```python
gdf = download_items(
    gdf,
    output_dir="downloads/",
    assets=["B04", "B03"],  # None → all assets
    max_workers=4,          # max concurrent downloads (async semaphore)
    overwrite=False,        # skip existing files by default
    process=False,          # True → re-encode as COG GeoTIFF via rioxarray
    show_progress=True,
)
# gdf now has a 'local_paths' column: dict of asset_key → Path
```

Downloaded files are organized as:
```
downloads/
├── sentinel-2-l2a/
│   └── 2020-06-15/
│       └── S2A_MSIL2A_.../
│           ├── S2A_MSIL2A_..._B04.tif
│           └── S2A_MSIL2A_..._B03.tif
└── metadata/
    └── search_results_2020-06-15_120000.gpkg
```

### `PathManager`

```python
from eostac import PathManager

pm = PathManager("downloads/")
pm.item_dir(collection, item_id, date)              # → Path
pm.asset_path(collection, item_id, date, "B04")    # → Path
pm.metadata_path("my_search")                       # → Path
```

### `viz`

```python
from eostac import viz

viz.plot_footprints(gdf, column="cloud_cover")  # footprints coloured by column
viz.plot_thumbnails(gdf, max_items=9)           # thumbnail image overlays
viz.quick_view("path/to/raster.tif", band_indices=[3, 2, 1])  # raster preview
```

All functions return a `folium.Map`.

## Development

```bash
uv sync --extra dev

uv run pytest                    # all tests
uv run pytest -m "not integration"  # skip network tests
uv run ruff check .
uv run ruff format .
```
