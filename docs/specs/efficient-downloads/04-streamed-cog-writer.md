# 04: Streamed COG writer for `process=True`

**Spec:** `docs/specs/efficient-downloads.md`

**What to build:** `process=True` re-encodes a raster as a COG using roughly constant memory, whatever the size of the source, so several large scenes can be processed concurrently on a laptop. The output is a faithful re-encoding of the source. The writer introduced here is the one shared writer for everything the library writes itself, and Subsets will reuse it.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] `process=True` produces a valid COG from any GDAL-readable source raster (GeoTIFF, COG, JPEG2000)
- [ ] The output has the same data type, nodata value, band count, CRS and geotransform as the source, and identical pixel values
- [ ] The full array is never loaded into memory; the copy is streamed through GDAL
- [ ] It works with a Signing callable, applied before the read
- [ ] Skip-existing, `overwrite` and the retry policy behave as for raw transfers
- [ ] `rasterio` is declared as an explicit dependency
- [ ] Tests go through `download_items` using small local GeoTIFF files as asset hrefs, following the `tiny_geotiff` fixture in the viz tests
