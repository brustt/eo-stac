# Subsets never resample

A Subset (the part of a raster asset covering a caller-supplied area) keeps the Provider's pixels unchanged: the requested area is reprojected into the raster's CRS, the window is snapped outward to whole source pixels, and the native CRS, resolution, data type and nodata are preserved. The Subset can therefore be slightly larger than the requested area and is not in EPSG:4326. A polygon is reduced to its bounding box and pixels outside it are not masked.

Producing an exact crop on the requested area was rejected because it requires resampling, which changes pixel values: a Subset and the whole file would then disagree, results would depend on a resampling method the caller did not choose, and the library would drift from a downloader into a processing tool.

## Consequences

- Rasters georeferenced only by ground control points (e.g. Sentinel-1 GRD) have no pixel grid in map coordinates, so they have no Subset. Requesting one raises `DownloadError`; callers take the whole file or use a gridded collection (e.g. Sentinel-1 RTC).
- Subsets of the same area from Items in different UTM zones have different CRSs and extents. Aligning them onto a common grid is the caller's job.
- Any exact-extent, reprojected or masked output must be produced downstream of the library.
