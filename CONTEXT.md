# eostac

A Python library for searching and downloading satellite imagery via STAC. Callers work with semantic band concepts; the library handles provider-specific naming differences at runtime.

## Language

### Search & Catalog

**STACSource**: A connection to a STAC endpoint — either a live API (`pystac_client.Client`) or a static catalog (`pystac.Catalog`).
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

**PathManager**: Determines the deterministic local file path for a downloaded asset under `root/<collection>/<date>/<item_id>/<item_id>_<band_alias>.tif`.
_Avoid_: path resolver, file namer

## Relationships

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

**SearchParams**: Base dataclass holding sensor-agnostic filter fields (`platform`, `product_type`). Passed to `search_items(params=...)` instead of individual sensor kwargs.
_Avoid_: filter object, query params, search config

**OpticalParams**: `SearchParams` subclass for optical sensors. Adds `max_cloud_cover`.
_Avoid_: optical filter, cloud filter

**SARParams**: `SearchParams` subclass for SAR sensors. Adds `orbit_direction`, `polarizations`, `instrument_mode`.
_Avoid_: radar params, SAR filter

**Sentinel1Params / Sentinel2Params / LandsatParams / DEMParams**: Leaf classes. Sentinel2Params is a typed alias of OpticalParams (no extra fields — self-documenting + extension point). LandsatParams adds `wrs_path`, `wrs_row`. Sentinel1Params adds `orbit_number`. DEMParams adds `dem_type`.
_Avoid_: sensor config, sensor filter

## Decisions

- **Alias Table structure**: flat global table (Band Alias → ordered list of candidate Asset Keys). Not per-collection. Simpler; Resolution handles fallback by trying each candidate.
- **Alias Table scope**: covers all sensor types — optical (red/green/blue/nir/swir1/swir2/rededge…), SAR (vv/vh/hh/hv), DEM (elevation), plus thumbnail/overview. Callers never need `extra_aliases` for common sensors.
- **`extra_aliases` placement**: parameter on `download_items(extra_aliases=...)` only. Not on STACSource or a standalone object. Resolution is download-time only.
- **Resolution failure**: raises `DownloadError` (hard error per item/asset). Not warn+skip. Rationale: a missing alias is a caller mistake (wrong alias for this sensor), not a transient network issue.
- **`search_items` sensor-specific params**: `orbit_direction` (`sat:orbit_state`), `platform` (`platform`), `polarizations` (`sar:polarizations`), `instrument_mode` (`sar:instrument_mode`). Applied client-side via `_filter_items`; pushed to API via `extra_query` CQL2 when possible.
- **Incompatible param warning**: post-search inspection. If a filter param was set but zero returned items carry the target property (e.g. `max_cloud_cover` set but no item has `eo:cloud_cover`), emit `warnings.warn`. No static collection registry.
- **`assets=` accepts both**: Band Aliases (resolved via Alias Table) and raw Asset Keys (tried as-is if not found in table). If neither resolves, DownloadError.
- **Local file naming**: PathManager uses the value the caller passed in `assets=` (the Band Alias or raw key) — never the resolved Asset Key. `local_paths` dict keyed the same way.

## Flagged ambiguities

- "band name" was used loosely to mean both Band Alias and Asset Key — resolved: these are distinct concepts. Band Alias is provider-agnostic; Asset Key is provider-specific.
- "asset key" in `local_paths` output always contains the Band Alias (what the caller requested), never the resolved Asset Key.
