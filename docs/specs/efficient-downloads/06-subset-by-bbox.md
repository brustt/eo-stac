# 06: Subset of a gridded raster by `bbox`

**Spec:** `docs/specs/efficient-downloads.md`

**What to build:** A caller passes `bbox` to `download_items` with explicitly requested raster assets and receives a Subset of each one instead of the Provider's whole file. The Subset keeps the Provider's pixels unchanged (ADR 0003), is stored at a path that identifies the area, and coexists with the whole file and with Subsets of other areas.

**Blocked by:** 04 (Streamed COG writer for `process=True`), 05 (Asset media types in search results)

**Status:** ready-for-agent

- [ ] `download_items` accepts `bbox` in EPSG:4326 as `[west, south, east, north]`
- [ ] For a requested raster asset whose media type is GeoTIFF, COG or JPEG2000, a Subset is written instead of the whole file
- [ ] The Subset window is the bbox reprojected into the raster's CRS and snapped outward to whole source pixels; the Subset covers at least the requested area
- [ ] Pixel values, CRS, resolution, data type and nodata equal those of the same window in the source; nothing is resampled
- [ ] The Subset is a COG produced by the shared writer
- [ ] PathManager places it at `<item_id>_<asset>_sub-<8 hex>`, with the tag derived deterministically from the area
- [ ] The same area maps to the same path, so Skip-existing skips it on a later call; a different area produces a different file; the whole file is untouched
- [ ] `local_paths` stays keyed by the Band Alias or Asset Key the caller requested
- [ ] It works with a Signing callable
- [ ] Without `bbox`, behaviour is unchanged
- [ ] Tests go through `download_items` with small local GeoTIFF files in a projected CRS as asset hrefs
