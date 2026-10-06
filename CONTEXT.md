# eostac

A Python library for searching and downloading satellite imagery via STAC. Callers work with semantic band concepts; the library handles provider-specific naming differences at runtime.

## Language

### Search & Catalog

**Provider**: The organisation hosting a STAC API (e.g., Planetary Computer, Earth Search, CDSE). Providers differ in Asset Keys, property names, and whether asset hrefs need Signing.
_Avoid_: source, backend, host

**STACSource**: A connection to one Provider's live STAC API. Static catalogs are not supported.
_Avoid_: catalog, client, connection

**Item**: A single STAC record representing one satellite acquisition over a specific area and time.
_Avoid_: scene, image, record

**Collection**: A named group of Items sharing the same sensor, processing level, and provider (e.g., `"sentinel-2-l2a"`).
_Avoid_: dataset, product, layer

### Asset Handling

**Asset Key**: The provider-specific string that identifies a file within an Item's assets dict (e.g., `"B04"`, `"red"`, `"SR_B4"`). Varies by provider for the same physical band.
_Avoid_: band name, file key, asset name

**Band Alias**: A semantic, provider-agnostic name for a spectral band (e.g., `"red"`, `"nir"`, `"swir1"`). Used by callers in `download_items(assets=[...])`.
_Avoid_: band name, semantic key, canonical name

**Alias Table**: The built-in mapping of Band Alias → ordered list of candidate Asset Keys, merged with any caller-supplied `extra_aliases`. Used during Resolution.
_Avoid_: band mapping, lookup table, registry

**Resolution**: The runtime process of finding the matching Asset Key for a requested Band Alias by checking which candidate keys actually exist in the Item's assets dict.
_Avoid_: lookup, translation, mapping

### Download

**Downloadable Asset**: An asset that points to a file rather than a service endpoint — any asset whose STAC roles do not include `tiles`. `assets=None` means every Downloadable Asset in the Item.
_Avoid_: data asset, real asset, file asset

**Subset**: The part of a raster asset covering a caller-supplied area, written by the library as a new GeoTIFF instead of transferring the Provider's whole file. Only defined for rasters that have a pixel grid in map coordinates. A Subset keeps the source pixels unchanged and may extend slightly beyond the requested area.
_Avoid_: crop, clip, window, extract

**Skip-existing**: A finished local file is not fetched again on a later call.
_Avoid_: resume, cache

**Resume**: A partial transfer continues from the bytes already received, within one call or on a later call, provided the remote file has not changed.
_Avoid_: skip-existing, restart, retry

**Signing**: A Provider-specific transformation that turns an asset href into one that can actually be fetched (e.g., appending a short-lived token). Supplied by the caller and applied at download time, so search results carry unsigned hrefs.
_Avoid_: authentication, token refresh, modifier

**PathManager**: Determines the deterministic local file path for a downloaded asset under `root/<collection>/<date>/<item_id>/<item_id>_<band_alias>.tif`.
_Avoid_: path resolver, file namer

## Relationships

- A **Provider** is reached through a **STACSource**
- A **STACSource** exposes one or more **Collections**
- A **Collection** contains many **Items**
- An **Item** has one or more assets, each identified by an **Asset Key**
- A **Band Alias** resolves to an **Asset Key** via the **Alias Table** during **Resolution**
- **PathManager** uses the **Band Alias** (not the resolved Asset Key) for local file naming

## Example dialogue

> **Dev:** "When the user passes `assets=["nir"]` for a Copernicus item, what key does the file get?"
> **Domain expert:** "Resolution finds `"B08"` in the Item's assets dict as the matching Asset Key. But the downloaded file is named `<item_id>_nir.tif` — the Band Alias, not the Asset Key."

> **Dev:** "What if I pass `"B08"` directly instead of `"nir"`?"
> **Domain expert:** "Not in the Alias Table as a top-level alias, so Resolution tries it as-is. If `"B08"` exists in the Item's assets dict, it works. If not, DownloadError."

## Language

### Params Classes

**SearchParams**: Base dataclass holding sensor-agnostic filter fields (`platform`). Passed to `search_items(params=...)` instead of individual sensor kwargs.
_Avoid_: filter object, query params, search config

**OpticalParams**: `SearchParams` subclass for optical sensors. Adds `max_cloud_cover`.
_Avoid_: optical filter, cloud filter

**SARParams**: `SearchParams` subclass for SAR sensors. Adds `orbit_state`, `polarizations`, `instrument_mode`, `product_type`. `polarizations` means the Item carries at least all the listed polarizations.
_Avoid_: radar params, SAR filter

**Sentinel1Params / Sentinel2Params / LandsatParams / DEMParams**: Leaf classes. Sentinel2Params is a typed alias of OpticalParams (no extra fields — self-documenting + extension point). LandsatParams adds `wrs_path`, `wrs_row`. Sentinel1Params adds `orbit_number`. DEMParams adds `dem_type`.
_Avoid_: sensor config, sensor filter

## Decisions

- **Alias Table structure**: flat global table (Band Alias → ordered list of candidate Asset Keys). Not per-collection. Simpler; Resolution handles fallback by trying each candidate.
- **Alias Table scope**: covers all sensor types — optical (red/green/blue/nir/swir1/swir2/rededge…), SAR (vv/vh/hh/hv), DEM (elevation), plus thumbnail/overview. Callers never need `extra_aliases` for common sensors.
- **`extra_aliases` placement**: parameter on `download_items(extra_aliases=...)` only. Not on STACSource or a standalone object. Resolution is download-time only.
- **Resolution failure**: raises `DownloadError` (hard error per item/asset). Not warn+skip. Rationale: a missing alias is a caller mistake (wrong alias for this sensor), not a transient network issue.
- **`search_items` sensor-specific params**: `orbit_state` (`sat:orbit_state`), `platform` (`platform`), `polarizations` (`sar:polarizations`), `instrument_mode` (`sar:instrument_mode`), `product_type` (`sar:product_type`, SAR only). All filtering is server-side; the client never re-filters returned Items. Provider-specific properties go through a raw `extra_query` escape hatch.
- **Source agnosticism**: the library knows no Provider by name. Provider-specific behaviour (Signing, unusual property names) is supplied by the caller through generic hooks.
- **Signing time**: download time, not search time. Search results stay Provider-neutral and do not expire. See ADR 0002.
- **Signing hook**: a caller-supplied `sign` callable (href → href) on `download_items`, `viz.plot_thumbnails` and `viz.quick_view`. Not stored on STACSource or in the GDF.
- **`assets=None`**: every Downloadable Asset. Explicitly requested keys are always attempted, whatever their roles. Roles are kept in an `asset_roles` column beside `assets`.
- **Polarizations without CQL2**: if the API does not advertise the filter extension, `search_items` raises `SearchError`. No client-side fallback, no silent drop.
- **Subsets are part of download**: `download_items(bbox=...)` writes Subsets instead of whole files.
- **Subsets of ungridded rasters**: a raster georeferenced only by ground control points (e.g. Sentinel-1 GRD) has no Subset; requesting one raises `DownloadError`. No estimated window, no warping.
- **Resume scope**: across calls. A failed transfer keeps its partial file and the next call continues it.
- **Retryable failures**: connection errors, timeouts, HTTP 408/429/5xx. Any other 4xx fails at once. Signing is re-applied on every attempt.
- **Subset naming**: the file name carries a short tag derived from the area, so Subsets of different areas and the whole file coexist and Skip-existing stays correct. The area is recorded in the metadata GeoPackage.
- **Subset area**: `bbox` or `polygon` in EPSG:4326, as in search. A polygon is reduced to its bounding box; pixels are never masked.
- **Subset fidelity**: never resampled. Native CRS, resolution, data type and nodata are kept; the window is snapped outward to whole source pixels. Partial overlap gives the overlapping part; no overlap skips the asset with a warning. See ADR 0003.
- **Non-raster assets with an area**: transferred whole. Only georeferenced rasters, identified by STAC media type, become Subsets.
- **Written format**: everything the library writes itself (Subsets, `process=True`) is a COG from one shared writer. `bbox` with `process=True` is allowed and equivalent to `bbox` alone.
- **Parallel connections per file, non-blocking writes**: deferred until a benchmark shows they matter. Reference workload: a few dozen full Sentinel-1 scenes (hundreds of MB to GB each) in one run.
- **`process=True`**: kept, for normalising any raster format to COG, with flat memory use.
- **`extra_query` precedence**: overrides `params` per property.
- **Incompatible param warning**: when a filtered search returns zero Items, one unfiltered sample Item is fetched and `warnings.warn` names each filtered property that the sample does not carry (e.g. `max_cloud_cover` set but the Item has no `eo:cloud_cover`). No static collection registry.
- **`assets=` accepts both**: Band Aliases (resolved via Alias Table) and raw Asset Keys (tried as-is if not found in table). If neither resolves, DownloadError.
- **Local file naming**: PathManager uses the value the caller passed in `assets=` (the Band Alias or raw key) — never the resolved Asset Key. `local_paths` dict keyed the same way.

## Flagged ambiguities

- "resume" was used for skipping files that already exist — resolved: that is **Skip-existing**. **Resume** means continuing a partial transfer.
- "band name" was used loosely to mean both Band Alias and Asset Key — resolved: these are distinct concepts. Band Alias is provider-agnostic; Asset Key is provider-specific.
- "asset key" in `local_paths` output always contains the Band Alias (what the caller requested), never the resolved Asset Key.
