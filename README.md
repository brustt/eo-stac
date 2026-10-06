# eostac

A Python library for searching and downloading satellite imagery via STAC (SpatioTemporal Asset Catalog). Works with any STAC API (HTTP/HTTPS) from any provider — Earth Search, Planetary Computer, CDSE, … — and returns structured GeoDataFrame results with concurrent downloads and folium-based visualization.

## Installation

```bash
uv sync
```

## Quick start

```python
from eostac import STACSource, Sentinel2Params, search_items, download_items, viz

# Connect to a STAC API
source = STACSource.from_url("https://earth-search.aws.element84.com/v1")

# Search for items
gdf = search_items(
    source,
    collections=["sentinel-2-l2a"],
    bbox=[10.0, 45.0, 12.0, 47.0],
    datetime="2020-06-01/2020-09-01",
    params=Sentinel2Params(max_cloud_cover=20.0),
)

# Download assets
gdf = download_items(gdf, output_dir="downloads/", assets=["B04", "B03", "B02"])

# Visualize
m = viz.plot_footprints(gdf, column="cloud_cover")
m.save("map.html")
```

### Sentinel-1 from Planetary Computer

Planetary Computer asset hrefs must be signed with a short-lived token. Install
its signing package (`uv add planetary-computer`) and pass its `sign` function
at download time — search results keep plain, non-expiring hrefs.

```python
import planetary_computer
from eostac import STACSource, Sentinel1Params, search_items, download_items

source = STACSource.from_url("https://planetarycomputer.microsoft.com/api/stac/v1")

gdf = search_items(
    source,
    collections=["sentinel-1-grd"],
    bbox=[2.2, 48.8, 2.5, 48.9],
    datetime="2024-06-01/2024-06-20",
    params=Sentinel1Params(
        orbit_state="ascending",
        instrument_mode="IW",
        polarizations=["VV", "VH"],  # items carrying at least VV and VH
    ),
)

gdf = download_items(
    gdf,
    output_dir="downloads/",
    assets=["vv", "vh"],
    sign=planetary_computer.sign,
)
```

## API

### `STACSource`

Entry point for a STAC API. Static catalogs and local paths are not supported.

```python
source = STACSource.from_url("https://...")

# With authentication
source = STACSource.from_url("https://...", headers={"Authorization": "Bearer TOKEN"})

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
    params=Sentinel2Params(max_cloud_cover=20.0),   # sensor-specific filters
    extra_query={"s2:mgrs_tile": {"eq": "32TQM"}},  # raw provider-specific filters
    limit=100,
)
```

Returns a `GeoDataFrame` with columns: `geometry`, `item_id`, `collection`, `datetime`, `cloud_cover`, `assets` (dict of key → href), `asset_roles` (dict of key → STAC roles), `asset_types` (dict of key → media type), plus all other flattened STAC properties.

`params` takes a `SearchParams` subclass: `Sentinel2Params`, `LandsatParams`, `Sentinel1Params`, `DEMParams` (or the generic `OpticalParams` / `SARParams`). All filtering happens server-side.

Property names differ between providers. `extra_query` is a raw STAC query-extension dict (`{property: {operator: value}}`) for anything provider-specific; it overrides `params` per property. If a filtered search returns nothing because the provider's items do not carry a filtered property, a warning names that property.

`SARParams.polarizations` is sent as a CQL2 filter; a `SearchError` is raised if the API does not support the filter extension.

### `download_items()`

Downloads run concurrently via `asyncio` and `httpx`. All assets start immediately; `max_workers` caps concurrent connections via a semaphore. When `process=True`, rioxarray/GDAL encoding runs in a worker thread so the event loop stays unblocked.

```python
gdf = download_items(
    gdf,
    output_dir="downloads/",
    assets=["B04", "B03"],  # band aliases ("red") or asset keys; None → all downloadable assets
    max_workers=4,          # max concurrent downloads (async semaphore)
    overwrite=False,        # skip existing files by default
    process=False,          # True → re-encode as COG GeoTIFF via rioxarray
    show_progress=True,
    sign=None,              # href → href callable for providers needing signed URLs
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
viz.plot_thumbnails(gdf, max_items=9)           # thumbnail image overlays (accepts sign=)
viz.quick_view("path/to/raster.tif", band_indices=[3, 2, 1])  # raster preview
```

All functions return a `folium.Map`. `plot_thumbnails` and `quick_view` accept the same `sign` callable as `download_items`.

## Development

```bash
uv sync --extra dev

uv run pytest                    # all tests
uv run pytest -m "not integration"  # skip network tests
uv run ruff check .
uv run ruff format .
```
