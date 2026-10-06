"""download_items() — concurrent asset downloader with retry and resume."""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import random
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

import geopandas as gpd
import httpx
from tqdm import tqdm

from eostac.aliases import resolve
from eostac.exceptions import DownloadError
from eostac.storage import PathManager

_RETRY_COUNT = 3
_CHUNK_SIZE = 1 << 20  # 1 MiB

# HTTP statuses worth retrying, besides every 5xx.
_RETRYABLE_STATUSES = frozenset({408, 429})
# Upper bound on how long a server's Retry-After can make one download wait.
_MAX_RETRY_AFTER = 300.0

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
) -> gpd.GeoDataFrame:
    """Download assets for every item in *gdf*.

    Parameters
    ----------
    gdf:
        GeoDataFrame returned by :func:`~eostac.search.search_items`.
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
        When ``True``, re-encode each file via *rioxarray* as a COG GeoTIFF
        (``compress=DEFLATE``).  Slower but normalises the output format.
    show_progress:
        Display a ``tqdm`` progress bar.
    extra_aliases:
        Extra entries merged on top of the built-in Alias Table.
        Format: ``{"alias": ["candidate_key1", "candidate_key2"]}``.
    sign:
        Callable turning an asset href into a fetchable one, applied just
        before each download — for providers whose hrefs need a short-lived
        token (e.g. ``planetary_computer.sign``).

    Returns
    -------
    geopandas.GeoDataFrame
        Copy of *gdf* with an additional ``local_paths`` column (dict mapping
        alias/key → local ``Path``).
    """
    coro = _download_items_async(
        gdf, output_dir, assets, max_workers, overwrite, process, show_progress,
        extra_aliases, sign,
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
) -> gpd.GeoDataFrame:
    pm = PathManager(output_dir)
    gdf = gdf.copy()
    gdf["local_paths"] = [{} for _ in gdf.index]

    tasks: list[tuple[int, str, str, Path]] = []
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

        for key, href in selected.items():
            suffix = _href_suffix(href)
            dest = pm.asset_path(collection, item_id, item_dt, key, suffix=suffix)
            tasks.append((idx, key, href, dest))

    results, errors = await _run_downloads(
        tasks, process, max_workers, overwrite, show_progress, sign
    )

    for (row_idx, key), local_path in results.items():
        gdf.at[row_idx, "local_paths"][key] = local_path

    if errors:
        import warnings

        warnings.warn(
            f"{len(errors)} asset(s) failed to download:\n" + "\n".join(errors),
            stacklevel=2,
        )

    meta_name = f"search_results_{datetime.now(timezone.utc).strftime('%Y-%m-%d_%H%M%S')}"
    _save_metadata(gdf, pm.metadata_path(meta_name))

    return gdf


# ---------------------------------------------------------------------------
# Asset selection
# ---------------------------------------------------------------------------


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
    Raises :exc:`~eostac.exceptions.DownloadError` if any requested alias or
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
    tasks: list[tuple[int, str, str, Path]],
    process: bool,
    max_workers: int,
    overwrite: bool,
    show_progress: bool,
    sign: Callable[[str], str] | None = None,
) -> tuple[dict[tuple[int, str], Path], list[str]]:
    results: dict[tuple[int, str], Path] = {}
    errors: list[str] = []
    sem = asyncio.Semaphore(max_workers)

    async with httpx.AsyncClient(follow_redirects=True) as client:

        async def run_one(row_idx: int, key: str, href: str, dest: Path) -> tuple:
            async with sem:
                if process:
                    path = await _download_processed(href, dest, overwrite, sign)
                else:
                    path = await _download_raw(client, href, dest, overwrite, sign)
                return row_idx, key, path

        aws = [run_one(row_idx, key, href, dest) for row_idx, key, href, dest in tasks]

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
            except DownloadError as exc:
                errors.append(str(exc))

    return results, errors


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
        delay = max(delay, min(retry_after, _MAX_RETRY_AFTER))
    return delay


async def _signed(sign: Callable[[str], str] | None, href: str) -> str:
    if sign is None:
        return href
    try:
        return await asyncio.to_thread(sign, href)
    except Exception as exc:
        raise DownloadError(f"Failed to sign {href}: {exc}") from exc


async def _with_retries(href: str, attempt_once: Callable[[], Awaitable[Path]]) -> Path:
    """Run *attempt_once* until it succeeds, retrying transient failures only."""
    for attempt in range(1, _RETRY_COUNT + 1):
        try:
            return await attempt_once()
        except DownloadError:
            raise
        except (_Transient, httpx.TransportError) as exc:
            reason = str(exc) or type(exc).__name__
            if attempt == _RETRY_COUNT:
                raise DownloadError(
                    f"Failed to download {href}: {reason} "
                    f"(after {_RETRY_COUNT} attempts)"
                ) from exc
            await asyncio.sleep(_backoff(attempt, getattr(exc, "retry_after", None)))
        except Exception as exc:
            reason = str(exc) or type(exc).__name__
            raise DownloadError(
                f"Failed to download {href}: {reason} (not retried)"
            ) from exc

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
            if resuming and _content_range_start(resp) != offset:
                _discard_partial(part, state)
                raise _Transient("server resumed from an unexpected offset")

            # A compressed body cannot be resumed: byte offsets would not match.
            resumable = "Content-Encoding" not in resp.headers
            try:
                with open(part, "ab" if resuming else "wb", _CHUNK_SIZE) as fh:
                    # Write bytes as they arrive (buffered by the file object)
                    # so that a dropped connection loses nothing already received.
                    async for chunk in resp.aiter_bytes():
                        fh.write(chunk)
            finally:
                etag = resp.headers.get("ETag") if resumable else None
                _save_resume_state(part, state, etag)

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


async def _download_processed(
    href: str,
    dest: Path,
    overwrite: bool = False,
    sign: Callable[[str], str] | None = None,
) -> Path:
    """Re-encode *href* as a COG GeoTIFF via rioxarray, in a worker thread."""
    cog_path = dest.with_suffix(".tif")
    if cog_path.exists() and not overwrite:
        return cog_path

    cog_path.parent.mkdir(parents=True, exist_ok=True)

    async def attempt_once() -> Path:
        url = await _signed(sign, href)
        await asyncio.to_thread(_write_cog, url, cog_path)
        return cog_path

    return await _with_retries(href, attempt_once)


def _write_cog(href: str, cog_path: Path) -> None:
    import rioxarray  # noqa: F401
    import xarray as xr

    ds = xr.open_dataset(href, engine="rasterio")
    try:
        ds.rio.to_raster(str(cog_path), driver="COG", compress="DEFLATE")
    finally:
        ds.close()


# ---------------------------------------------------------------------------
# Metadata persistence
# ---------------------------------------------------------------------------


def _save_metadata(gdf: gpd.GeoDataFrame, path: Path) -> None:
    try:
        serialisable = gdf.drop(
            columns=[
                c for c in ("assets", "asset_roles", "local_paths") if c in gdf.columns
            ],
            errors="ignore",
        )
        serialisable.to_file(str(path), driver="GPKG")
    except Exception as exc:
        import warnings
        warnings.warn(f"Failed to save metadata to {path}: {exc}", stacklevel=2)


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _href_suffix(href: str) -> str:
    """Return the file extension from an href, defaulting to ``.tif``."""
    parsed = urlparse(href)
    path = Path(parsed.path)
    return path.suffix if path.suffix else ".tif"
