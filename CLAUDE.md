# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`eostac` — a Python library for searching and downloading satellite imagery via STAC (SpatioTemporal Asset Catalog). It queries any STAC API (HTTP/HTTPS), returns structured GeoDataFrame results, downloads assets concurrently, and provides folium-based visualization utilities.

Import as: `from eostac import STACSource, search_items, download_items, PathManager, viz`

## Commands

### Install dependencies
```
uv sync --extra dev
```

### Run tests
```
uv run pytest                              # all tests
uv run pytest tests/test_search.py        # single file
uv run pytest -k "test_cloud_cover"       # single test by name
uv run pytest -m "not integration"        # skip network tests
```

### Lint / format
```
uv run ruff check .
uv run ruff format .
```

## Architecture

```
src/eostac/
├── __init__.py      # Public API re-exports
├── catalog.py       # STACSource — unified URL/JSON entry point
├── search.py        # search_items() → GeoDataFrame
├── download.py      # download_items() with asyncio + httpx
├── storage.py       # PathManager — naming conventions
├── viz.py           # Folium-based visualization utilities
└── exceptions.py    # EostacError, SearchError, DownloadError, StorageError
```

### Entry points

- **`STACSource`** (`catalog.py`) — wraps a `pystac_client.Client` (STAC API). Use `from_url()` with an HTTP/HTTPS endpoint. Local paths and `file://` URLs are rejected (`EostacError`); static catalogs are not supported.

- **`search_items(source, ...)`** (`search.py`) — queries the API with optional bbox/polygon/datetime filters plus sensor-specific `params` (a `SearchParams` subclass). All filtering is pushed to the server: `params.to_query()` produces a STAC query-extension dict — there is no client-side filtering. Returns a `geopandas.GeoDataFrame` with columns: `geometry`, `item_id`, `collection`, `datetime`, `cloud_cover`, `assets` (dict), plus flattened STAC properties.

- **`download_items(gdf, output_dir, ...)`** (`download.py`) — concurrent download via `asyncio` and `httpx.AsyncClient`. Concurrency is bounded by `asyncio.Semaphore(max_workers)`. Supports resume (skip existing files), overwrite, raw streaming, or COG re-encoding via rioxarray (offloaded to a worker thread with `asyncio.to_thread`). Returns the GDF with a `local_paths` column added. Always saves a GeoPackage to `output_dir/metadata/`.

- **`PathManager`** (`storage.py`) — resolves deterministic paths under a root:
  ```
  root/<collection>/<YYYY-MM-DD>/<item_id>/<item_id>_<asset_key>.tif
  root/metadata/<name>.gpkg
  ```

- **`viz`** (`viz.py`) — three folium utilities:
  - `plot_footprints(gdf)` — item footprints coloured by a GDF column
  - `plot_thumbnails(gdf)` — thumbnail image overlays
  - `quick_view(path_or_href)` — raster overview rendered as ImageOverlay

### Key design decisions

- **Async not threads** — downloads are I/O bound; `asyncio` + `httpx.AsyncClient` avoids thread overhead; all tasks start immediately, bounded by `Semaphore(max_workers)`
- **Retry with backoff** — `_download_raw` retries 3× with exponential delay (`await asyncio.sleep(2^n)`)
- **Server-side filtering only** — sensor filters are pushed via the STAC query extension (`SearchParams.to_query()`); the client never re-filters returned items
- **COG processing in thread** — `process=True` runs rioxarray/GDAL synchronously in a worker thread via `asyncio.to_thread`; default is raw async streaming for speed
- **Exception hierarchy** — all errors derive from `EostacError` for easy catch-all handling



### To do
* check if files doesn't exists
* make a stac catalog with names, url and assets names. Creating a new global mapping for bands and mapped them to each stac assets names.