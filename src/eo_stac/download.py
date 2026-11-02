"""download_items() — concurrent asset downloader with retry and resume."""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import math
import random
import re
import warnings
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

import geopandas as gpd
import httpx
from shapely.geometry import box, shape
from shapely.geometry.base import BaseGeometry
from tqdm import tqdm

from eo_stac.aliases import resolve
from eo_stac.exceptions import DownloadError
from eo_stac.storage import PathManager

_RETRY_COUNT = 3
_CHUNK_SIZE = 1 << 20  # 1 MiB

# Dict-valued columns that a GeoPackage cannot store.
_UNSERIALISABLE_COLUMNS = ("assets", "asset_roles", "asset_types", "local_paths")

# Media types of rasters that can have a Subset.
_RASTER_MEDIA_TYPES = ("image/tiff", "image/jp2")

# HTTP statuses worth retrying, besides every 5xx.
_RETRYABLE_STATUSES = frozenset({408, 429})
# Longest Retry-After worth waiting for; beyond it the asset is given up on.
_MAX_RETRY_AFTER = 300.0

# A polygon Subset reads, and writes, only the blocks touching the polygon.  Those
# are the source's own tiles, so no read straddles tiles it does not need; a
# source that is not tiled is cut into squares of _SPARSE_BLOCK pixels instead,
# and the sparse output is always tiled that size (GDAL wants a multiple of 16).
_SPARSE_BLOCK = 256
# How the library writes a COG.
_COG_OPTIONS = {"driver": "COG", "compress": "DEFLATE", "bigtiff": "IF_SAFER"}

# Above this share of blocks touching the polygon, one dense window is cheaper.
_SPARSE_MAX_SHARE = 0.9

# Roles marking an asset as a service endpoint rather than a downloadable file.
_NON_DOWNLOADABLE_ROLES = frozenset({"tiles"})


def download_items(
    gdf: gpd.GeoDataFrame,
    output_dir: str | Path,
    assets: list[str] | None = None,
    max_workers: int = 4,
    overwrite: bool = False,
    process: bool = False,
    show_progress: bool = True,
    extra_aliases: dict[str, list[str]] | None = None,
    sign: Callable[[str], str] | None = None,
    bbox: Sequence[float] | None = None,
    polygon: dict | BaseGeometry | None = None,
) -> gpd.GeoDataFrame:
    """Download assets for every item in *gdf*.

    Parameters
    ----------
    gdf:
        GeoDataFrame returned by :func:`~eo_stac.search.search_items`.
    output_dir:
        Root directory for downloaded files.
    assets:
        Band Aliases or raw Asset Keys to download.  Resolution tries the
        built-in Alias Table first, then the value as a literal Asset Key.
        ``None`` downloads every downloadable asset (all but those with the
        ``tiles`` role) without alias resolution.
    max_workers:
        Maximum number of concurrent downloads (semaphore size).
    overwrite:
        Re-download files that already exist locally.
    process:
        When ``True``, re-encode each file as a COG GeoTIFF
        (``compress=DEFLATE``) through a streamed GDAL copy, with flat memory
        use.  Slower but normalises the output format.  Ignored when an area
        is given: Subsets are COGs (sparse tiled GeoTIFFs for a thin
        *polygon*) and other assets are copied as-is.
    show_progress:
        Display a ``tqdm`` progress bar.
    extra_aliases:
        Extra entries merged on top of the built-in Alias Table.
        Format: ``{"alias": ["candidate_key1", "candidate_key2"]}``.
    sign:
        Callable turning an asset href into a fetchable one, applied just
        before each download — for providers whose hrefs need a short-lived
        token (e.g. ``planetary_computer.sign``).
    bbox:
        Area of interest as ``[west, south, east, north]`` in EPSG:4326.  When
        given, each raster asset is written as a Subset — the part covering
        the area, as a COG — instead of the provider's whole file.  A Subset
        keeps the source pixels, CRS and resolution: it is never resampled,
        and may extend slightly beyond the area.  Rasters without a pixel
        grid (georeferenced by ground control points only, e.g. Sentinel-1
        GRD) have no Subset and are reported as failed; other assets
        (thumbnails, metadata files) are transferred whole.  The area is
        recorded in the metadata GeoPackage as ``subset_bbox``.
    polygon:
        Area of interest as a GeoJSON geometry dict or a Shapely geometry in
        EPSG:4326, as an alternative to *bbox*; it takes precedence over
        *bbox*.  The Subset has the shape and grid of the polygon's bounding
        box, but only the blocks touching the polygon are fetched and written;
        the rest read as nodata.  Pixels within a written block are not masked,
        and when the polygon fills most of its bounding box the whole window is
        written as a COG.  The polygon is recorded as ``subset_polygon``.

    Returns
    -------
    geopandas.GeoDataFrame
        Copy of *gdf* with an additional ``local_paths`` column (dict mapping
        alias/key → local ``Path``).
    """
    coro = _download_items_async(
        gdf,
        output_dir,
        assets,
        max_workers,
        overwrite,
        process,
        show_progress,
        extra_aliases,
        sign,
        _make_area(bbox, polygon),
    )
    try:
        asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    except RuntimeError:
        return asyncio.run(coro)


async def _download_items_async(
    gdf: gpd.GeoDataFrame,
    output_dir: str | Path,
    assets: list[str] | None = None,
    max_workers: int = 4,
    overwrite: bool = False,
    process: bool = False,
    show_progress: bool = True,
    extra_aliases: dict[str, list[str]] | None = None,
    sign: Callable[[str], str] | None = None,
    area: _Area | None = None,
) -> gpd.GeoDataFrame:
    pm = PathManager(output_dir)
    gdf = gdf.copy()
    gdf["local_paths"] = [{} for _ in gdf.index]

    if area is not None and "asset_types" not in gdf.columns:
        warnings.warn(
            "The GeoDataFrame has no asset_types column, so rasters cannot be "
            "told apart: whole files will be transferred instead of Subsets.",
            stacklevel=2,
        )

    tasks: list[_Task] = []
    for idx, row in gdf.iterrows():
        item_assets: dict[str, str] = row.get("assets") or {}
        selected = _select_assets(
            item_assets,
            assets,
            extra_aliases,
            row.get("item_id", str(idx)),
            row.get("asset_roles") or {},
        )

        item_id = row.get("item_id", str(idx))
        collection = row.get("collection") or "unknown"
        item_dt: datetime | None = row.get("datetime")

        # Resolution may map a Band Alias onto an Asset Key; media types are
        # recorded per Asset Key, so look them up by href.
        asset_types: dict[str, str] = row.get("asset_types") or {}
        type_by_href = {item_assets[k]: t for k, t in asset_types.items()}

        for key, href in selected.items():
            if area is not None and _is_raster(type_by_href.get(href)):
                dest = pm.subset_path(
                    collection, item_id, item_dt, key, area.bounds, area.polygon
                )
                tasks.append(_Task(idx, key, href, dest, area, as_cog=True))
            else:
                suffix = _href_suffix(href)
                dest = pm.asset_path(collection, item_id, item_dt, key, suffix=suffix)
                # With an area, whatever is not a Subset is copied as-is.
                as_cog = process and area is None
                tasks.append(_Task(idx, key, href, dest, as_cog=as_cog))

    # Saved before transferring, so an interrupted run still leaves its manifest.
    meta_name = (
        f"search_results_{datetime.now(timezone.utc).strftime('%Y-%m-%d_%H%M%S')}"
    )
    _save_metadata(gdf, pm.metadata_path(meta_name), area)

    if show_progress and not overwrite:
        _report_existing(tasks)

    results, errors, skipped = await _run_downloads(
        tasks, max_workers, overwrite, show_progress, sign
    )

    for (row_idx, key), local_path in results.items():
        gdf.at[row_idx, "local_paths"][key] = local_path

    if skipped:
        warnings.warn(
            f"{len(skipped)} asset(s) skipped:\n" + "\n".join(skipped),
            stacklevel=2,
        )

    if errors:
        warnings.warn(
            f"{len(errors)} asset(s) failed to download:\n" + "\n".join(errors),
            stacklevel=2,
        )

    return gdf


# ---------------------------------------------------------------------------
# Asset selection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Task:
    """One asset to fetch.  *area* is set when a Subset is wanted."""

    row_idx: int
    key: str
    href: str
    dest: Path
    area: _Area | None = None
    as_cog: bool = False


def _report_existing(tasks: list[_Task]) -> None:
    """Tell the console which files are already complete or partly downloaded."""
    for task in tasks:
        final = task.dest.with_suffix(".tif") if task.as_cog else task.dest
        if final.exists():
            tqdm.write(f"Already downloaded, skipping: {final}")
            continue
        part = task.dest.with_suffix(task.dest.suffix + ".part")
        if not task.as_cog and part.exists():
            tqdm.write(
                f"Incomplete download ({part.stat().st_size} bytes), "
                f"will resume: {final}"
            )


@dataclass(frozen=True)
class _Area:
    """The area of interest: EPSG:4326 *bounds*, and the *polygon* inside them.

    *polygon* is ``None`` when the whole bounding box is wanted.
    """

    bounds: tuple[float, ...]
    polygon: BaseGeometry | None = None


def _make_area(
    bbox: Sequence[float] | None, polygon: dict | BaseGeometry | None
) -> _Area | None:
    """Return the area of interest; *polygon* wins over *bbox*."""
    if polygon is not None:
        geom = shape(polygon) if isinstance(polygon, dict) else polygon
        bounds = tuple(float(v) for v in geom.bounds)
        # A polygon that is its own bounding box masks nothing.
        return _Area(bounds, None if geom.equals(box(*bounds)) else geom)
    if bbox is not None:
        if len(bbox) != 4:
            raise ValueError(
                f"bbox must be [west, south, east, north], got {list(bbox)!r}"
            )
        return _Area(tuple(float(v) for v in bbox))
    return None


def _is_raster(media_type: str | None) -> bool:
    return bool(media_type) and media_type.startswith(_RASTER_MEDIA_TYPES)


def _select_assets(
    item_assets: dict[str, str],
    requested: list[str] | None,
    extra_aliases: dict[str, list[str]] | None,
    item_id: str,
    asset_roles: dict[str, list[str]] | None = None,
) -> dict[str, str]:
    """Return resolved ``{alias_or_key: href}`` dict.

    When *requested* is ``None``, returns every downloadable asset (those whose
    roles in *asset_roles* do not mark a service endpoint) without Resolution.
    Raises :exc:`~eo_stac.exceptions.DownloadError` if any requested alias or
    key cannot be resolved against *item_assets*.
    """
    if not item_assets:
        return {}
    if requested is None:
        roles = asset_roles or {}
        return {
            key: href
            for key, href in item_assets.items()
            if not _NON_DOWNLOADABLE_ROLES.intersection(roles.get(key) or [])
        }

    result: dict[str, str] = {}
    missing: list[str] = []

    for alias in requested:
        try:
            key, href = resolve(alias, item_assets, extra_aliases)
            result[key] = href
        except KeyError:
            missing.append(alias)

    if missing:
        available = list(item_assets)
        raise DownloadError(
            f"Item {item_id!r}: cannot resolve asset(s) {missing!r}. "
            f"Available asset keys: {available!r}"
        )

    return result


# ---------------------------------------------------------------------------
# Async download runner
# ---------------------------------------------------------------------------


async def _run_downloads(
    tasks: list[_Task],
    max_workers: int,
    overwrite: bool,
    show_progress: bool,
    sign: Callable[[str], str] | None = None,
) -> tuple[dict[tuple[int, str], Path], list[str], list[str]]:
    """Run *tasks*; return ``(results, errors, skipped)``."""
    results: dict[tuple[int, str], Path] = {}
    errors: list[str] = []
    skipped: list[str] = []
    sem = asyncio.Semaphore(max_workers)

    if not overwrite:
        # Files already on disk are not transfers: keep them out of the progress total.
        pending = []
        for task in tasks:
            final = task.dest.with_suffix(".tif") if task.as_cog else task.dest
            if final.exists():
                results[(task.row_idx, task.key)] = final
            else:
                pending.append(task)
        tasks = pending

    async with httpx.AsyncClient(follow_redirects=True) as client:

        async def run_one(task: _Task) -> tuple:
            async with sem:
                if task.as_cog:
                    path = await _download_as_cog(
                        task.href, task.dest, overwrite, sign, task.area
                    )
                else:
                    path = await _download_raw(
                        client, task.href, task.dest, overwrite, sign
                    )
                return task.row_idx, task.key, path

        aws = [run_one(task) for task in tasks]

        pbar = tqdm(
            asyncio.as_completed(aws),
            total=len(aws),
            disable=not show_progress,
            unit="file",
            desc="Downloading",
        )
        for coro in pbar:
            try:
                row_idx, key, local_path = await coro
                results[(row_idx, key)] = local_path
            except _SkippedAsset as exc:
                skipped.append(str(exc))
            except DownloadError as exc:
                errors.append(str(exc))

    return results, errors, skipped


# ---------------------------------------------------------------------------
# Retry policy
# ---------------------------------------------------------------------------


class _Transient(Exception):
    """A failure worth retrying (server overload, rate limit, dropped connection)."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class _Permanent(Exception):
    """A failure that a retry cannot fix (missing file, refused access)."""


class _NoSubset(Exception):
    """The raster cannot have a Subset (it has no pixel grid)."""


class _NoOverlap(Exception):
    """The requested area lies outside the raster."""


class _SkippedAsset(DownloadError):
    """Nothing to write for this asset; reported as skipped, not failed."""


def _is_retryable_status(status: int) -> bool:
    return status in _RETRYABLE_STATUSES or status >= 500


def _retry_after(resp: httpx.Response) -> float | None:
    """Return the ``Retry-After`` delay in seconds, if the server sent one."""
    value = resp.headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


def _raise_for_status(resp: httpx.Response) -> None:
    status = resp.status_code
    if status < 400:
        return
    if _is_retryable_status(status):
        raise _Transient(f"HTTP {status}", _retry_after(resp))
    raise _Permanent(f"HTTP {status}")


def _backoff(attempt: int, retry_after: float | None) -> float:
    """Exponential delay with jitter, never shorter than the server's request."""
    delay = 2 ** (attempt - 1) + random.random()
    if retry_after is not None:
        delay = max(delay, retry_after)
    return delay


async def _signed(sign: Callable[[str], str] | None, href: str) -> str:
    if sign is None:
        return href
    try:
        return await asyncio.to_thread(sign, href)
    except Exception as exc:
        raise DownloadError(f"Failed to sign {href}: {exc}") from exc


def _failure(href: str, exc: Exception, attempt: int, note: str = "") -> DownloadError:
    """Build the error reported for *href*, saying whether it was retried."""
    reason = str(exc) or type(exc).__name__
    retried = "not retried" if attempt == 1 else f"after {attempt} attempts"
    return DownloadError(f"Failed to download {href}: {reason} ({retried}{note})")


async def _with_retries(href: str, attempt_once: Callable[[], Awaitable[Path]]) -> Path:
    """Run *attempt_once* until it succeeds, retrying transient failures only."""
    for attempt in range(1, _RETRY_COUNT + 1):
        try:
            return await attempt_once()
        except DownloadError:
            raise
        except (httpx.UnsupportedProtocol, httpx.InvalidURL) as exc:
            raise _failure(href, exc, attempt) from exc
        except (_Transient, httpx.TransportError) as exc:
            if attempt == _RETRY_COUNT:
                raise _failure(href, exc, attempt) from exc
            retry_after = exc.retry_after if isinstance(exc, _Transient) else None
            if retry_after is not None and retry_after > _MAX_RETRY_AFTER:
                # Retrying sooner than asked would be impolite, waiting that
                # long would stall the run: give up on this asset instead.
                note = f"; server asked to wait {retry_after:.0f} s"
                raise _failure(href, exc, attempt, note) from exc
            await asyncio.sleep(_backoff(attempt, retry_after))
        except Exception as exc:
            raise _failure(href, exc, attempt) from exc

    raise DownloadError(f"Exhausted retries for {href}")  # pragma: no cover


# ---------------------------------------------------------------------------
# Download implementations
# ---------------------------------------------------------------------------


async def _download_raw(
    client: httpx.AsyncClient,
    href: str,
    dest: Path,
    overwrite: bool = False,
    sign: Callable[[str], str] | None = None,
) -> Path:
    """Stream *href* to *dest*, resuming any partial transfer.

    Bytes go to ``<dest>.part``, which is kept on failure so that a retry, or a
    later call, asks only for the missing bytes.  The href is signed afresh on
    every attempt.
    """
    if dest.exists() and not overwrite:
        return dest

    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    state = part.with_suffix(part.suffix + ".json")
    if overwrite:
        _discard_partial(part, state)

    async def attempt_once() -> Path:
        url = await _signed(sign, href)
        offset, etag = _resume_point(part, state)
        headers = {"Range": f"bytes={offset}-", "If-Range": etag} if offset else {}

        async with client.stream("GET", url, headers=headers, timeout=60) as resp:
            if offset and resp.status_code == 416:
                # The partial file is not a prefix of the remote file any more.
                _discard_partial(part, state)
                raise _Transient("HTTP 416 on resume")
            _raise_for_status(resp)

            resuming = resp.status_code == 206
            if resuming and resp.headers.get("ETag") != etag:
                # If-Range is not honoured everywhere (Azure Blob ignores it):
                # never append bytes of a different version of the file.
                _discard_partial(part, state)
                raise _Transient("remote file changed during the transfer")
            if resuming and _content_range_start(resp) != offset:
                _discard_partial(part, state)
                raise _Transient("server resumed from an unexpected offset")

            # A compressed body cannot be resumed: byte offsets would not match.
            resumable = "Content-Encoding" not in resp.headers
            expected = _expected_size(resp) if resumable else None
            try:
                with open(part, "ab" if resuming else "wb", _CHUNK_SIZE) as fh:
                    # Write bytes as they arrive (buffered by the file object)
                    # so that a dropped connection loses nothing already received.
                    async for chunk in resp.aiter_bytes():
                        fh.write(chunk)
            finally:
                etag = resp.headers.get("ETag") if resumable else None
                _save_resume_state(part, state, etag)

        # A server may close the connection early without raising: only a file
        # of the advertised size is promoted to ``dest``.  The partial file is
        # kept, so the retry resumes from it.
        if expected is not None and part.stat().st_size != expected:
            raise _Transient(
                f"incomplete transfer: got {part.stat().st_size} of {expected} bytes"
            )

        part.replace(dest)
        state.unlink(missing_ok=True)
        return dest

    return await _with_retries(href, attempt_once)


# ---------------------------------------------------------------------------
# Resume bookkeeping
# ---------------------------------------------------------------------------
#
# ``<dest>.part.json`` records the remote file's ETag and the byte ranges held
# in ``<dest>.part`` as ``[[start, end], ...]`` (end exclusive).  Transfers are
# sequential today, so there is a single range starting at 0.


def _resume_point(part: Path, state: Path) -> tuple[int, str | None]:
    """Return ``(offset, etag)`` to resume from, or ``(0, None)`` to start over."""
    try:
        meta = json.loads(state.read_text())
        size = part.stat().st_size
    except (OSError, ValueError):
        return 0, None

    etag = meta.get("etag")
    ranges = meta.get("ranges") or []
    # Resuming needs a strong ETag (If-Range ignores weak ones) and a partial
    # file that is one contiguous prefix of the remote file.
    if not etag or etag.startswith("W/") or len(ranges) != 1 or ranges[0][0] != 0:
        return 0, None
    # The partial file is append-only, so its size is the truth even if the
    # state file was last written before the final chunks landed.
    return size, etag


def _save_resume_state(part: Path, state: Path, etag: str | None) -> None:
    try:
        size = part.stat().st_size
        state.write_text(json.dumps({"etag": etag, "ranges": [[0, size]]}))
    except OSError:
        pass


def _discard_partial(part: Path, state: Path) -> None:
    part.unlink(missing_ok=True)
    state.unlink(missing_ok=True)


def _content_range_start(resp: httpx.Response) -> int | None:
    """Return the first byte position of a ``Content-Range: bytes a-b/n`` header."""
    value = resp.headers.get("Content-Range", "")
    try:
        return int(value.removeprefix("bytes").strip().split("-")[0])
    except ValueError:
        return None


def _expected_size(resp: httpx.Response) -> int | None:
    """Return the full size of the remote file, or ``None`` if not advertised."""
    if resp.status_code == 206:
        total = resp.headers.get("Content-Range", "").rpartition("/")[2]
    else:
        total = resp.headers.get("Content-Length", "")
    return int(total) if total.isdigit() else None


async def _download_as_cog(
    href: str,
    dest: Path,
    overwrite: bool = False,
    sign: Callable[[str], str] | None = None,
    area: _Area | None = None,
) -> Path:
    """Write *href*, or its Subset over *area*, as a COG in a worker thread."""
    cog_path = dest.with_suffix(".tif")
    if cog_path.exists() and not overwrite:
        return cog_path

    cog_path.parent.mkdir(parents=True, exist_ok=True)

    async def attempt_once() -> Path:
        url = await _signed(sign, href)
        try:
            await asyncio.to_thread(_write_cog, url, cog_path, area)
        except _NoSubset as exc:
            raise DownloadError(f"No Subset for {href}: {exc}") from exc
        except _NoOverlap as exc:
            raise _SkippedAsset(f"{href}: {exc}") from exc
        return cog_path

    return await _with_retries(href, attempt_once)


# ---------------------------------------------------------------------------
# COG writer — the one path for every raster the library writes itself
# ---------------------------------------------------------------------------

_GDAL_HTTP_STATUS = re.compile(r"HTTP (?:error|response) code\s*:\s*(\d{3})")


def _write_cog(href: str, cog_path: Path, area: _Area | None = None) -> None:
    """Copy the raster at *href* to *cog_path* as a COG, with flat memory use.

    GDAL streams the copy block by block, so the full array is never held in
    memory.  Data type, nodata and georeferencing are those of the source.
    With *area*, only the Subset covering it is written; if the area has a
    polygon that fills the Subset thinly, a sparse tiled GeoTIFF holding only
    the blocks that touch the polygon.
    """
    import rasterio
    from rasterio.errors import RasterioIOError
    from rasterio.shutil import copy as rio_copy

    tmp = cog_path.with_suffix(cog_path.suffix + ".part")
    try:
        with rasterio.open(href) as src:
            if area is None:
                rio_copy(src, tmp, **_COG_OPTIONS)
            else:
                _write_subset(src, area, tmp)
        tmp.replace(cog_path)
    except (_NoSubset, _NoOverlap):
        raise
    # RasterioIOError is an OSError: classify it before letting real OSErrors by.
    except RasterioIOError as exc:
        raise _classify_gdal_error(href, exc) from exc
    except OSError:
        raise  # local disk trouble, not a read failure
    except Exception as exc:
        raise _classify_gdal_error(href, exc) from exc
    finally:
        tmp.unlink(missing_ok=True)


def _subset_window(src, bounds: tuple[float, ...]):
    """Return the pixel window of *src* covering *bounds* (EPSG:4326), snapped outward.

    The window is limited to the raster's extent.
    """
    from rasterio.warp import transform_bounds
    from rasterio.windows import Window, from_bounds

    if src.crs is None:
        raise _NoSubset(
            "the raster has no pixel grid (it is georeferenced by ground "
            "control points only); download the whole file or use a gridded "
            "collection"
        )

    bounds = transform_bounds("EPSG:4326", src.crs, *bounds, densify_pts=21)
    exact = from_bounds(*bounds, transform=src.transform)
    # Round away float noise before snapping outward to whole source pixels.
    col_off = math.floor(round(exact.col_off, 6))
    row_off = math.floor(round(exact.row_off, 6))
    col_end = math.ceil(round(exact.col_off + exact.width, 6))
    row_end = math.ceil(round(exact.row_off + exact.height, 6))
    col_off, row_off = max(col_off, 0), max(row_off, 0)
    col_end, row_end = min(col_end, src.width), min(row_end, src.height)
    if col_end <= col_off or row_end <= row_off:
        raise _NoOverlap("the requested area does not overlap the raster")
    return Window(col_off, row_off, col_end - col_off, row_end - row_off)


def _write_subset(src, area: _Area, path: Path) -> None:
    """Write the Subset of *src* over *area* to *path*."""
    from rasterio.shutil import copy as rio_copy

    window = _subset_window(src, area.bounds)
    tiles = None
    if area.polygon is not None:
        tiles = _tiles_touching(src, window, area.polygon)
    if tiles is not None:
        _write_sparse(src, window, tiles, path)
    else:
        with _subset_view(src, window) as view:
            rio_copy(view, path, **_COG_OPTIONS)


def _window_tiles(src, window) -> list:
    """Cut *window* along the source's block grid, as windows relative to it.

    Reading one such tile touches exactly one block of the source.  A source
    that is not tiled has no useful grid: it is cut into ``_SPARSE_BLOCK`` squares.
    """
    from rasterio.windows import Window

    if src.profile.get("tiled"):
        rows, cols = src.block_shapes[0]
    else:
        rows = cols = _SPARSE_BLOCK
    col0, row0 = int(window.col_off), int(window.row_off)
    col1, row1 = col0 + int(window.width), row0 + int(window.height)
    tiles = []
    for top in range(row0 // rows * rows, row1, rows):
        for left in range(col0 // cols * cols, col1, cols):
            r0, c0 = max(top, row0), max(left, col0)
            r1, c1 = min(top + rows, row1), min(left + cols, col1)
            tiles.append(Window(c0 - col0, r0 - row0, c1 - c0, r1 - r0))
    return tiles


def _polygon_in_pixels(src, window, polygon: BaseGeometry) -> BaseGeometry:
    """Return *polygon* (EPSG:4326) in the pixel coordinates of *window*.

    It is projected into the raster's CRS first: straight edges in EPSG:4326 are
    not straight on a projected grid, so the polygon is densified before.
    """
    import shapely
    from pyproj import Transformer
    from shapely.affinity import affine_transform
    from shapely.ops import transform

    to_src = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)
    projected = transform(to_src.transform, shapely.segmentize(polygon, 1e-4))
    a, b, c, d, e, f = tuple(~src.window_transform(window))[:6]
    return affine_transform(projected, [a, b, d, e, c, f])


def _tiles_touching(src, window, polygon: BaseGeometry):
    """Return the tiles of *window* worth writing for *polygon*, or ``None``.

    A tile is kept if it touches *polygon* with a margin of one pixel.  ``None``
    means the polygon covers so much of the window (``_SPARSE_MAX_SHARE``) that
    a dense Subset is the better choice.
    """
    import shapely
    from shapely.prepared import prep

    footprint = prep(_polygon_in_pixels(src, window, polygon).buffer(1.0))
    tiles = _window_tiles(src, window)
    kept = [
        t
        for t in tiles
        if footprint.intersects(
            shapely.box(t.col_off, t.row_off, t.col_off + t.width, t.row_off + t.height)
        )
    ]
    return None if len(kept) > _SPARSE_MAX_SHARE * len(tiles) else kept


def _write_sparse(src, window, tiles, path: Path) -> None:
    """Write the Subset of *src* over *window* to *path*, only at *tiles*.

    Same grid, CRS, pixels and metadata as the dense Subset; the other blocks
    are left unwritten and read back as nodata.  Not a COG: GDAL's COG driver is
    dense.
    """
    import rasterio
    from rasterio.windows import Window

    profile = {
        "driver": "GTiff",
        "width": int(window.width),
        "height": int(window.height),
        "count": src.count,
        "dtype": src.dtypes[0],
        "crs": src.crs,
        "transform": src.window_transform(window),
        "nodata": src.nodata,
        "tiled": True,
        "blockxsize": _SPARSE_BLOCK,
        "blockysize": _SPARSE_BLOCK,
        "compress": "DEFLATE",
        "bigtiff": "IF_SAFER",
        "SPARSE_OK": "YES",
    }
    with rasterio.open(path, "w", **profile) as dst:
        _copy_metadata(src, dst)
        for tile in tiles:
            source = Window(
                window.col_off + tile.col_off,
                window.row_off + tile.row_off,
                tile.width,
                tile.height,
            )
            dst.write(src.read(window=source), window=tile)


def _copy_metadata(src, dst) -> None:
    """Carry the dataset and band metadata of *src* over to *dst*."""
    from rasterio.enums import ColorInterp

    dst.update_tags(**src.tags())
    dst.descriptions = src.descriptions
    dst.scales = src.scales
    dst.offsets = src.offsets
    dst.units = src.units
    dst.colorinterp = src.colorinterp
    for band in src.indexes:
        dst.update_tags(band, **src.tags(band))
        if src.colorinterp[band - 1] == ColorInterp.palette:
            dst.write_colormap(band, src.colormap(band))


def _subset_view(src, window):
    """Return a dataset exposing only the Subset of *src* over *window*.

    The view sits on the source pixel grid, in the source CRS, so nearest-
    neighbour lookup returns each source pixel unchanged: nothing is resampled
    (ADR 0003).
    """
    from rasterio.enums import Resampling
    from rasterio.vrt import WarpedVRT

    return WarpedVRT(
        src,
        crs=src.crs,
        transform=src.window_transform(window),
        width=int(window.width),
        height=int(window.height),
        resampling=Resampling.nearest,
        nodata=src.nodata,
    )


def _classify_gdal_error(href: str, exc: Exception) -> Exception:
    """Map a GDAL failure onto the retry policy used for raw transfers."""
    # *href* may be signed: keep its token out of error messages.
    message = str(exc).replace(href, "the asset")
    match = _GDAL_HTTP_STATUS.search(message)
    if match:
        status = int(match.group(1))
        kind = _Transient if _is_retryable_status(status) else _Permanent
        return kind(f"HTTP {status}")
    # No status to go on: a remote read may have hit a network glitch, a local
    # one will fail the same way again.
    if urlparse(href).scheme in ("http", "https"):
        return _Transient(message)
    return _Permanent(message)


# ---------------------------------------------------------------------------
# Metadata persistence
# ---------------------------------------------------------------------------


def _save_metadata(
    gdf: gpd.GeoDataFrame,
    path: Path,
    area: _Area | None = None,
) -> None:
    try:
        serialisable = gdf.drop(
            columns=[c for c in _UNSERIALISABLE_COLUMNS if c in gdf.columns],
            errors="ignore",
        )
        if area is not None:
            serialisable["subset_bbox"] = ",".join(repr(v) for v in area.bounds)
            if area.polygon is not None:
                serialisable["subset_polygon"] = area.polygon.wkt
        serialisable.to_file(str(path), driver="GPKG")
    except Exception as exc:
        warnings.warn(f"Failed to save metadata to {path}: {exc}", stacklevel=2)


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _href_suffix(href: str) -> str:
    """Return the file extension from an href, defaulting to ``.tif``."""
    parsed = urlparse(href)
    path = Path(parsed.path)
    return path.suffix if path.suffix else ".tif"
