# 07: Subset edge cases and mixed assets

**Spec:** `docs/specs/efficient-downloads.md`

**What to build:** Subsets behave sensibly in every situation a real run meets: the caller can reuse the polygon they searched with, scenes at the edge of the area are still usable, assets that cannot be Subsets are handled without failing the run, and the area is recorded so files can be traced back to it.

**Blocked by:** 06 (Subset of a gridded raster by `bbox`)

**Status:** ready-for-agent

- [ ] `download_items` accepts `polygon` (GeoJSON dict or Shapely geometry, EPSG:4326); it is reduced to its bounding box and takes precedence over `bbox`, as in `search_items`
- [ ] No pixel is masked: pixels inside the bounding box but outside the polygon keep their source values
- [ ] An area that partly overlaps the raster yields a Subset of the overlapping part
- [ ] An area with no overlap skips that asset with a warning and writes no file
- [ ] A raster with no CRS and only ground control points raises `DownloadError` for that asset, with a message suggesting the whole file or a gridded Collection; other assets in the call are still processed
- [ ] Assets whose media type is not a georeferenced raster (thumbnails, XML, manifests) are transferred whole when an area is given, including under `assets=None`
- [ ] The requested area is recorded in the metadata GeoPackage
- [ ] Passing an area together with `process=True` is accepted and behaves like the area alone
- [ ] README and `CLAUDE.md` document Subsets, including that they are not resampled and may extend beyond the requested area
- [ ] Tests go through `download_items`, including a fixture raster georeferenced only by ground control points
