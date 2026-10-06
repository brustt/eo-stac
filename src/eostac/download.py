"""download_items() — concurrent asset downloader with retry and resume."""

from __future__ import annotations

import asyncio
import concurrent.futures
import time
from collections.abc import Callable
from datetime import datetime, timezone
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
                if sign is not None and (overwrite or not dest.exists()):
                    try:
                        href = await asyncio.to_thread(sign, href)
                    except Exception as exc:
                        raise DownloadError(f"Failed to sign {href}: {exc}") from exc
                if process:
                    path = await asyncio.to_thread(
                        _download_processed_sync, href, dest, overwrite
                    )
                else:
                    path = await _download_raw(client, href, dest, overwrite)
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
# Download implementations
# ---------------------------------------------------------------------------


async def _download_raw(
    client: httpx.AsyncClient, href: str, dest: Path, overwrite: bool = False
) -> Path:
    """Stream *href* to *dest* with retry and resume support."""
    if dest.exists() and not overwrite:
        return dest

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")

    for attempt in range(_RETRY_COUNT):
        try:
            async with client.stream("GET", href, timeout=60) as resp:
                resp.raise_for_status()
                with open(tmp, "wb") as fh:
                    async for chunk in resp.aiter_bytes(chunk_size=_CHUNK_SIZE):
                        fh.write(chunk)
            tmp.rename(dest)
            return dest
        except Exception as exc:
            if attempt < _RETRY_COUNT - 1:
                await asyncio.sleep(2**attempt)
            else:
                if tmp.exists():
                    tmp.unlink()
                raise DownloadError(f"Failed to download {href}: {exc}") from exc

    raise DownloadError(f"Exhausted retries for {href}")  # pragma: no cover


def _download_processed_sync(href: str, dest: Path, overwrite: bool = False) -> Path:
    """Download *href* and re-encode as COG GeoTIFF via rioxarray (runs in thread)."""
    if dest.exists() and not overwrite:
        return dest

    dest.parent.mkdir(parents=True, exist_ok=True)

    import rioxarray  # noqa: F401
    import xarray as xr

    for attempt in range(_RETRY_COUNT):
        try:
            ds = xr.open_dataset(href, engine="rasterio")
            cog_path = dest.with_suffix(".tif")
            try:
                ds.rio.to_raster(str(cog_path), driver="COG", compress="DEFLATE")
            finally:
                ds.close()
            return cog_path
        except Exception as exc:
            if attempt < _RETRY_COUNT - 1:
                time.sleep(2**attempt)
            else:
                raise DownloadError(
                    f"Failed to process {href} as COG: {exc}"
                ) from exc

    raise DownloadError(f"Exhausted retries for {href}")  # pragma: no cover


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
