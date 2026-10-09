# eo-stac

A Python library for searching and downloading satellite imagery via STAC (SpatioTemporal Asset Catalog). Works with any STAC API (HTTP/HTTPS) from any provider — Earth Search, Planetary Computer, CDSE, … — and returns structured GeoDataFrame results with concurrent downloads and folium-based visualization.

## Installation

```bash
uv sync
```

## Quick start

```python
from eo_stac import STACSource, Sentinel2Params, search_items, download_items, viz

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
from eo_stac import STACSource, Sentinel1Params, search_items, download_items

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

Downloads run concurrently via `asyncio` and `httpx`. All assets start immediately; `max_workers` caps concurrent connections via a semaphore. When `process=True`, GDAL encoding runs in a worker thread so the event loop stays unblocked.

```python
gdf = download_items(
    gdf,
    output_dir="downloads/",
    assets=["B04", "B03"],  # band aliases ("red") or asset keys; None → all downloadable assets
    max_workers=4,          # max concurrent downloads (async semaphore)
    overwrite=False,        # finished files are skipped by default
    process=False,          # True → re-encode as COG GeoTIFF (streamed, flat memory)
    show_progress=True,
    sign=None,              # href → href callable for providers needing signed URLs
    bbox=None,              # [west, south, east, north] → write Subsets, see below
    polygon=None,           # alternative to bbox (GeoJSON dict or Shapely)
)
# gdf now has a 'local_paths' column: dict of asset_key → Path
```

**Skip-existing and Resume.** A finished file is never fetched again unless `overwrite=True`. An interrupted transfer is resumed: bytes go to `<file>.part`, which is kept on failure, and a retry or a later call asks the server only for the missing bytes. Resume is guarded by the remote file's ETag, so a file that changed on the server restarts from zero; so does a transfer from a server without range or ETag support. `overwrite=True` discards partial files too.

**Retries.** Connection errors, timeouts and HTTP 408, 429 and 5xx are retried up to three times with backoff, honouring `Retry-After`. Any other 4xx (for example 403 from an unsigned href, or 404) fails at once. A failed asset is reported in a warning and left out of `local_paths`; the other downloads continue.

**Subsets.** With `bbox` or `polygon` (EPSG:4326), each raster asset is written as a Subset: the part covering the area, as a COG, instead of the provider's whole file.

```python
gdf = download_items(gdf, "downloads/", assets=["vv"], bbox=[2.2, 48.8, 2.5, 48.9])
# → downloads/<collection>/<date>/<item_id>/<item_id>_vv_sub-1a2b3c4d.tif
```

- A Subset is never resampled. It keeps the source pixels, CRS, resolution, data type and nodata, so it is slightly larger than the area and is not in EPSG:4326.
- A polygon is reduced to its bounding box; pixels outside it are not masked.
- The file name carries a tag derived from the area, so Subsets of different areas and the whole file coexist. The area is recorded as `subset_bbox` in the metadata GeoPackage.
- An area that only partly overlaps a raster gives the overlapping part. No overlap skips that asset with a warning.
- Rasters georeferenced only by ground control points, such as Sentinel-1 GRD, have no Subset and are reported as failed. Download the whole file, or use a gridded collection such as `sentinel-1-rtc`.
- Non-raster assets (thumbnails, XML, manifests) are transferred whole.

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

### Band aliases

`assets=` accepts provider-neutral band aliases (`"red"`, `"nir"`, `"swir1"`, `"vv"`, `"visual"`, …) as well as raw asset keys. Each alias maps to candidate asset keys, tried in order against every item, so one list works across providers. The built-in mapping is `eo_stac.ALIAS_TABLE`; `eo_stac.resolve()` applies it to an item's assets.

### `PathManager`

```python
from eo_stac import PathManager

pm = PathManager("downloads/")
pm.item_dir(collection, item_id, date)              # → Path
pm.asset_path(collection, item_id, date, "B04")    # → Path
pm.subset_path(collection, item_id, date, "B04", bbox)  # → Path (..._B04_sub-<tag>.tif)
pm.metadata_path("my_search")                       # → Path
```

### `viz`

```python
from eo_stac import viz

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
