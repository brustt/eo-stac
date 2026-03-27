"""download_items() — concurrent asset downloader with retry and resume."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import geopandas as gpd
import httpx
from tqdm import tqdm

from eostac.exceptions import DownloadError
from eostac.storage import PathManager

_RETRY_COUNT = 3
_CHUNK_SIZE = 1 << 20  # 1 MiB


def download_items(
    gdf: gpd.GeoDataFrame,
    output_dir: str | Path,
    assets: list[str] | None = None,
    max_workers: int = 4,
    overwrite: bool = False,
    process: bool = False,
    show_progress: bool = True,
) -> gpd.GeoDataFrame:
    """Download assets for every item in *gdf*.

    Parameters
    ----------
    gdf:
        GeoDataFrame returned by :func:`~eostac.search.search_items`.
    output_dir:
        Root directory for downloaded files.
    assets:
        Asset keys to download.  ``None`` selects assets whose role includes
        ``"data"``, falling back to all assets if none are role-tagged.
    max_workers:
        Maximum number of concurrent downloads (semaphore size).
    overwrite:
        Re-download files that already exist locally.
    process:
        When ``True``, re-encode each file via *rioxarray* as a COG GeoTIFF
        (``compress=DEFLATE``).  Slower but normalises the output format.
    show_progress:
        Display a ``tqdm`` progress bar.

    Returns
    -------
    geopandas.GeoDataFrame
        Copy of *gdf* with an additional ``local_paths`` column (dict mapping
        asset key → local ``Path``).
    """
    coro = _download_items_async(
        gdf, output_dir, assets, max_workers, overwrite, process, show_progress
    )
    try:
        asyncio.get_running_loop()
        # Already inside a running event loop (e.g. Jupyter): run in a new
        # thread so it gets its own loop and asyncio.run() works normally.
        import concurrent.futures

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
) -> gpd.GeoDataFrame:
    """Async implementation of :func:`download_items`."""
    pm = PathManager(output_dir)
    gdf = gdf.copy()
    gdf["local_paths"] = [{} for _ in gdf.index]

    # Build work list: (row_idx, asset_key, href, dest_path)
    tasks: list[tuple[int, str, str, Path]] = []
    for idx, row in gdf.iterrows():
        item_assets: dict[str, str] = row.get("assets") or {}
        selected = _select_assets(item_assets, assets)

        item_id = row.get("item_id", str(idx))
        collection = row.get("collection") or "unknown"
        item_dt: datetime | None = row.get("datetime")

        for key, href in selected.items():
            suffix = _href_suffix(href)
            dest = pm.asset_path(collection, item_id, item_dt, key, suffix=suffix)
            tasks.append((idx, key, href, dest))

    results, errors = await _run_downloads(tasks, process, max_workers, overwrite, show_progress)
    

    # Write results back to GDF
    for (row_idx, key), local_path in results.items():
        gdf.at[row_idx, "local_paths"][key] = local_path

    if errors:
        import warnings

        warnings.warn(
            f"{len(errors)} asset(s) failed to download:\n" + "\n".join(errors),
            stacklevel=2,
        )

    # Persist GeoDataFrame as GeoPackage
    meta_name = f"search_results_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}"
    _save_metadata(gdf, pm.metadata_path(meta_name))

    return gdf


# ---------------------------------------------------------------------------
# Asset selection
# ---------------------------------------------------------------------------


def _select_assets(
    item_assets: dict[str, str],
    requested: list[str] | None,
) -> dict[str, str]:
    """Return the subset of assets to download."""
    if not item_assets:
        return {}
    if requested is not None:
        return {k: v for k, v in item_assets.items() if k in requested}
    return item_assets


# ---------------------------------------------------------------------------
# Async download runner
# ---------------------------------------------------------------------------


async def _run_downloads(
    tasks: list[tuple[int, str, str, Path]],
    process: bool,
    max_workers: int,
    overwrite: bool,
    show_progress: bool,
) -> tuple[dict[tuple[int, str], Path], list[str]]:
    results: dict[tuple[int, str], Path] = {}
    errors: list[str] = []
    sem = asyncio.Semaphore(max_workers)

    async with httpx.AsyncClient(follow_redirects=True) as client:

        async def run_one(row_idx: int, key: str, href: str, dest: Path) -> tuple:
            async with sem:
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

    import rioxarray  # noqa: F401  — activates the rio accessor
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
    """Save GeoDataFrame to GeoPackage, skipping non-serialisable columns."""
    try:
        serialisable = gdf.drop(
            columns=[c for c in ("assets", "local_paths") if c in gdf.columns],
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
