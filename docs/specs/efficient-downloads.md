# Efficient downloads for large files

Status: ready-for-agent

## Problem Statement

I download a few dozen full Sentinel-1 scenes per run, each between several hundred MB and about 2 GB, from Providers such as Planetary Computer. Today the download path is wasteful for files of that size:

- A connection drop near the end of a file restarts the transfer from zero. What the documentation calls "resume" is really Skip-existing: it only avoids re-fetching files that are already complete.
- Failures that can never succeed (HTTP 403, 404) are retried three times with backoff before being reported, so a misconfigured run (for example a missing Signing callable) wastes time on every asset.
- `process=True` loads the whole raster into memory before writing it, so a handful of concurrent scenes can exhaust RAM.
- When I only need a small area, I still have to transfer the Provider's whole file.
- I have no measurements telling me whether more connections per file, or non-blocking disk writes, would make my runs faster.

## Solution

`download_items` becomes robust and economical for large files:

- A partial transfer is Resumed from the bytes already received, both when a retry happens inside one call and when I rerun the script later.
- Only failures that can plausibly succeed are retried; the rest fail at once with a clear message.
- `process=True` writes a COG with flat memory use, whatever the size of the source.
- I can pass an area (`bbox` or `polygon`) and receive a Subset of each gridded raster instead of the whole file, with the Provider's pixels unchanged.
- A benchmark script measures my reference workload so that the two remaining optimisations (several connections per file, non-blocking writes) are decided on numbers.

## User Stories

1. As a remote-sensing analyst, I want an interrupted download to continue from the bytes already received when it is retried, so that a connection drop at 90% of a 1.8 GB scene does not cost me the whole file again.
2. As a remote-sensing analyst, I want a download that failed in a previous run to continue when I rerun my script, so that I do not have to babysit long runs.
3. As a remote-sensing analyst, I want a partial file to be discarded and restarted if the remote file changed since the partial transfer, so that I never end up with a file spliced from two versions.
4. As a remote-sensing analyst, I want a transfer to restart from zero when the server does not support range requests or sends no ETag, so that Resume never produces a corrupt file on Providers with weaker HTTP support.
5. As a remote-sensing analyst, I want `overwrite=True` to discard any partial file as well as the finished one, so that "overwrite" always means a fresh transfer.
6. As a remote-sensing analyst, I want a finished file to still be skipped on later calls, so that Skip-existing keeps working exactly as it does today.
7. As a remote-sensing analyst, I want a partial file never to be mistaken for a finished one, so that downstream code only ever sees complete files in `local_paths`.
8. As a remote-sensing analyst, I want HTTP 403 and 404 to fail immediately, so that a missing Signing callable or a bad href is reported in seconds rather than after backoff on every asset.
9. As a remote-sensing analyst, I want connection errors, timeouts and HTTP 408, 429 and 5xx to be retried, so that transient Provider problems do not fail my run.
10. As a remote-sensing analyst, I want the library to wait as long as the server's `Retry-After` header asks, so that I am not rate-limited harder for retrying too soon.
11. As a remote-sensing analyst, I want retries from many concurrent downloads to be spread out in time, so that they do not all hit the Provider at the same instant.
12. As a remote-sensing analyst, I want Signing to be re-applied on every attempt, so that a token that expired during a long transfer is refreshed on retry.
13. As a remote-sensing analyst, I want a failure message that says whether the error was retried and what the final status was, so that I can tell a transient outage from a permanent error.
14. As a remote-sensing analyst, I want one asset's failure to remain a warning that does not stop the other downloads, so that a long run yields everything that could be fetched.
15. As a remote-sensing analyst, I want `process=True` to use roughly constant memory regardless of raster size, so that I can process several large scenes concurrently on a laptop.
16. As a remote-sensing analyst, I want `process=True` to normalise any GDAL-readable raster (GeoTIFF, COG, JPEG2000) to a COG, so that files from different Providers end up in one format.
17. As a remote-sensing analyst, I want `process=True` to preserve the source's data type, nodata value and georeferencing, so that the COG is a faithful re-encoding.
18. As a remote-sensing analyst, I want `process=True` to work with a Signing callable, so that it is usable on Providers that require signed hrefs.
19. As a remote-sensing analyst, I want to pass a `bbox` to `download_items` and receive a Subset of each gridded raster asset, so that I transfer only the area I study.
20. As a remote-sensing analyst, I want to pass a `polygon` instead of a `bbox`, so that I can reuse the geometry I searched with.
21. As a remote-sensing analyst, I want a Subset to contain the Provider's pixels unchanged, in the native CRS, resolution, data type and nodata, so that a Subset and the whole file agree exactly.
22. As a remote-sensing analyst, I want a Subset to cover at least my requested area, snapped outward to whole pixels, so that I never lose edge pixels.
23. As a remote-sensing analyst, I want a Subset of an area that only partly overlaps the raster to contain the overlapping part, so that scenes at the edge of my area are still usable.
24. As a remote-sensing analyst, I want an asset with no overlap with my area to be skipped with a warning, so that I do not get empty files.
25. As a remote-sensing analyst, I want Subsets of different areas and the whole file to coexist under the same output directory, so that changing my area does not overwrite earlier downloads.
26. As a remote-sensing analyst, I want Skip-existing to apply per area, so that rerunning with the same area skips the Subsets I already have and a new area produces new files.
27. As a remote-sensing analyst, I want the requested area recorded in the metadata GeoPackage, so that I can tell later which area a Subset file covers.
28. As a remote-sensing analyst, I want non-raster assets (thumbnails, XML schemas, manifests) to be transferred whole when I pass an area, so that `assets=None` with an area still gives me the complete set.
29. As a remote-sensing analyst, I want a clear error when I ask for a Subset of a raster that has no pixel grid (for example Sentinel-1 GRD), so that I know to take the whole file or use a gridded Collection instead.
30. As a remote-sensing analyst, I want that error to affect only the ungridded asset, so that the other assets in the same call are still processed.
31. As a remote-sensing analyst, I want Subsets to work with a Signing callable, so that I can subset Sentinel-1 RTC from Planetary Computer.
32. As a remote-sensing analyst, I want Subsets written as COGs, so that everything the library writes itself has one format.
33. As a remote-sensing analyst, I want passing both an area and `process=True` to be accepted and behave like the area alone, so that I do not have to change my other arguments.
34. As a remote-sensing analyst, I want `local_paths` to stay keyed by the Band Alias or Asset Key I requested, whether the file is whole or a Subset, so that my downstream code does not change.
35. As a remote-sensing analyst, I want search results to carry each asset's media type, so that I can see which assets are rasters.
36. As a maintainer, I want a benchmark script that downloads a fixed set of full Sentinel-1 scenes at several `max_workers` values, so that I can see how throughput scales with concurrency.
37. As a maintainer, I want the benchmark to compare blocking and threaded disk writes, so that I know whether non-blocking writes are worth adding.
38. As a maintainer, I want the benchmark to fetch one file over 1, 4 and 8 range connections, so that I know whether the Provider caps throughput per connection.
39. As a maintainer, I want the benchmark kept out of the test suite, so that ordinary test runs never transfer gigabytes.
40. As a maintainer, I want the Resume bookkeeping to be able to describe several completed ranges of a file, so that adding several connections per file later does not require rewriting it.

## Implementation Decisions

- **Scope of change**: the download module, the search module (one new GDF column), the storage module (Subset path), plus a new benchmark script. The public entry point stays `download_items`.
- **`download_items` interface**: gains `bbox` and `polygon` parameters, both in EPSG:4326 and with the same precedence rule as `search_items` (polygon wins). No other signature change.
- **Retry policy**: retryable failures are connection errors, timeouts, and HTTP 408, 429 and 5xx. Any other 4xx fails at once. `Retry-After` is honoured when present. Backoff stays exponential with three attempts and gains jitter.
- **Signing per attempt**: the Signing callable is applied to the unsigned href before every attempt, not once per asset. This is consistent with ADR 0002.
- **Resume**: a partial file is kept on failure. The next attempt, in the same call or a later one, sends a Range request for the missing bytes, conditional on the ETag recorded with the partial file. If the server answers with the full body, sends no ETag, or the ETag differs, the transfer restarts from zero. The partial file becomes the final file only by rename on completion.
- **Resume bookkeeping**: recorded next to the partial file, in a form that can describe several completed ranges, not only a single offset.
- **`overwrite=True`**: removes both the finished file and any partial file before transferring.
- **Streamed COG writer**: one writer produces every raster the library writes itself. It copies from the source through GDAL without loading the full array, preserving data type, nodata and georeferencing. `process=True` uses it for the whole raster; Subsets use it for a window.
- **Subset window**: the requested area is reprojected into the raster's CRS, intersected with the raster extent, and snapped outward to whole source pixels. No resampling, no masking; a polygon is reduced to its bounding box. This is ADR 0003.
- **Ungridded rasters**: a raster with no CRS and only ground control points has no Subset; requesting one raises `DownloadError` for that asset, reported through the existing per-asset warning mechanism.
- **Which assets become Subsets**: decided by STAC media type (GeoTIFF, COG, JPEG2000). All other assets are transferred whole even when an area is given.
- **GDF schema**: `search_items` adds an `asset_types` column (Asset Key → media type) beside `assets` and `asset_roles`. Like those, it is dropped from the metadata GeoPackage.
- **Subset naming**: PathManager produces `<item_id>_<asset>_sub-<8 hex>` with the tag derived deterministically from the area, so the same area always maps to the same path. `local_paths` keys are unchanged.
- **Metadata**: when an area is given, it is recorded in the metadata GeoPackage.
- **Subsets and Resume**: Subsets are not resumable. They rely on GDAL's own HTTP retry settings and on the library's retry loop around the whole Subset read.
- **Dependencies**: `rasterio` becomes an explicit dependency; it is currently only transitive.
- **Benchmark**: a standalone script under a `benchmarks` directory. Workload: about eight full Sentinel-1 `vv` files from Planetary Computer (roughly 5 GB per run). It reports wall time and MB/s for `max_workers` 4, 8 and 16; blocking versus threaded writes; and one file over 1, 4 and 8 range connections.
- **Order of work**: (1) retry policy and Resume, (2) benchmark, (3) streamed `process=True`, (4) Subsets.

## Testing Decisions

- **What makes a good test here**: it calls `download_items` and asserts on what a caller can observe: which requests reached the server (method, Range and conditional headers, signed URL), which files exist on disk and their content, what `local_paths` contains, and which warnings were raised. It does not assert on private helpers, retry counters or internal file layouts beyond the documented paths.
- **Single seam**: `download_items`. Two kinds of fake sit behind it:
  - HTTP is faked with `respx`, as the existing download tests do. This covers the retry policy, `Retry-After`, Signing per attempt, Resume within a call, Resume across two calls, ETag mismatch, servers without range support, and `overwrite`.
  - Raster reads are exercised with small local GeoTIFF files used as asset hrefs, following the `tiny_geotiff` fixture in the viz tests. This covers `process=True` (data type, nodata, georeferencing preserved, output is a COG), Subset fidelity (pixels equal to the source window), partial overlap, no overlap, an ungridded raster built with ground control points only, non-raster assets passed through whole, and Subset naming and Skip-existing per area.
- **Search seam**: the `asset_types` column is tested through the existing item-to-GDF tests, alongside `asset_roles`.
- **Backoff in tests**: sleeping is patched, as the existing retry tests do, so tests stay fast.
- **Existing tests of private helpers**: left in place where they still describe behaviour; new behaviour is tested through `download_items` only.
- **Benchmark**: not tested and not part of the test suite; it needs the network and transfers gigabytes.
- **Live check**: one manual run against Planetary Computer for a Sentinel-1 RTC Subset and an interrupted-then-resumed full file, reported in the pull request, not automated.

## Out of Scope

- Several connections per file (parallel range requests) and non-blocking disk writes. They are decided after the benchmark; only the Resume bookkeeping is shaped to allow the former.
- Subsets of ungridded rasters by estimating a window from ground control points.
- Any resampling: reprojection to EPSG:4326, exact-extent crops, masking by polygon, or aligning Subsets from different UTM zones onto a common grid.
- Named Provider presets or any Provider-specific code in the library.
- Byte-level progress reporting; the progress bar stays per file.
- Integrity checks beyond ETag-guarded Resume (for example checksum verification against STAC `file:checksum`).

## Further Notes

- Vocabulary follows `CONTEXT.md`: Provider, Item, Asset Key, Band Alias, Signing, Subset, Skip-existing, Resume, Downloadable Asset.
- Relevant decisions: ADR 0002 (Signing happens at download time) and ADR 0003 (Subsets never resample).
- Facts measured on Planetary Computer on 2026-10-06: `sentinel-1-grd` `vv` is about 660 MB with no CRS and 210 ground control points; `sentinel-1-rtc` `vv` is about 1.8 GB on a regular UTM grid; both are tiled with overviews and both honour range requests and send an ETag.
- The Provider-agnostic work that precedes this spec (Signing hook, `extra_query`, CQL2 polarizations filter, `asset_roles`) is implemented in the working tree but not yet committed.
