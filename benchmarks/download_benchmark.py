"""Download benchmark: whole Sentinel-1 files from Planetary Computer.

Measures what decides two open optimisations (see docs/specs/efficient-downloads.md):
several connections per file, and non-blocking disk writes.

Run with::

    uv run --extra bench python benchmarks/download_benchmark.py

Sections (choose with ``--sections``):

``workers``  ``download_items`` over all files at ``max_workers`` 4, 8 and 16.
``writes``   The same files with blocking, then threaded, disk writes.
``ranges``   One file fetched over 1, 4 and 8 range connections.

Every measurement starts from an empty directory, deleted afterwards, so
Skip-existing and Resume never shorten a run.

Data volume: a Sentinel-1 GRD ``vv`` file is about 0.66 GB, so each pass over
the default 8 files is roughly 5 GB.  In total, ``workers`` transfers ~16 GB, ``writes`` ~11 GB and ``ranges`` ~2 GB:
about 29 GB in total.  Use ``--files 2`` for a quicker run (~9 GB), and
``--work-dir`` to pick the disk being measured.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import tempfile
import time
from pathlib import Path

import httpx
import planetary_computer

from eostac import STACSource, download_items, search_items

_API = "https://planetarycomputer.microsoft.com/api/stac/v1"
# A fixed, closed query: every matching Item is listed and the first ones by
# id are kept, so every run measures the same files.
_COLLECTION = "sentinel-1-grd"
_BBOX = [0.0, 44.0, 6.0, 49.0]
_DATETIME = "2024-06-01/2024-06-07"
_CHUNK = 1 << 20


def find_hrefs(asset: str, count: int) -> tuple[object, list[str]]:
    """Return the search GDF and the unsigned hrefs of *asset*, oldest first."""
    source = STACSource.from_url(_API)
    gdf = search_items(
        source,
        collections=[_COLLECTION],
        bbox=_BBOX,
        datetime=_DATETIME,
    )
    gdf = gdf[gdf["assets"].apply(lambda assets: asset in assets)]
    gdf = gdf.sort_values("item_id").head(count)
    if len(gdf) < count:
        raise SystemExit(f"Only {len(gdf)} item(s) carry the {asset!r} asset.")
    return gdf, [assets[asset] for assets in gdf["assets"]]


class Measurement:
    """Times a block and sums the size of the files it left in a fresh directory."""

    def __init__(self, work_dir: Path | None) -> None:
        self._work_dir = work_dir

    def __enter__(self) -> "Measurement":
        self.directory = Path(
            tempfile.mkdtemp(prefix="eostac-bench-", dir=self._work_dir)
        )
        self._start = time.perf_counter()
        return self

    def __exit__(self, *exc_info) -> None:
        self.seconds = time.perf_counter() - self._start
        files = [p for p in self.directory.rglob("*") if p.is_file()]
        self.megabytes = (
            sum(p.stat().st_size for p in files if p.suffix != ".gpkg") / 1e6
        )
        shutil.rmtree(self.directory, ignore_errors=True)

    def row(self, section: str, setting: str) -> tuple[str, str, str, str, str]:
        rate = self.megabytes / self.seconds if self.seconds else 0.0
        return (
            section,
            setting,
            f"{self.megabytes:,.0f}",
            f"{self.seconds:,.1f}",
            f"{rate:,.1f}",
        )


# ---------------------------------------------------------------------------
# Section: workers — the library as it is
# ---------------------------------------------------------------------------


def bench_workers(gdf, asset: str, work_dir: Path | None) -> list[tuple]:
    rows = []
    for workers in (4, 8, 16):
        with Measurement(work_dir) as m:
            download_items(
                gdf,
                m.directory,
                assets=[asset],
                max_workers=workers,
                show_progress=False,
                sign=planetary_computer.sign,
            )
        rows.append(m.row("workers", f"max_workers={workers}"))
    return rows


# ---------------------------------------------------------------------------
# Section: writes — blocking versus threaded disk writes
# ---------------------------------------------------------------------------


async def _fetch(
    client: httpx.AsyncClient, url: str, path: Path, threaded: bool
) -> None:
    with open(path, "wb") as fh:
        async with client.stream("GET", url, timeout=60) as resp:
            resp.raise_for_status()
            async for chunk in resp.aiter_bytes(chunk_size=_CHUNK):
                if threaded:
                    await asyncio.to_thread(fh.write, chunk)
                else:
                    fh.write(chunk)


async def _fetch_all(
    urls: list[str], directory: Path, threaded: bool, workers: int
) -> None:
    sem = asyncio.Semaphore(workers)
    async with httpx.AsyncClient(follow_redirects=True) as client:

        async def one(index: int, url: str) -> None:
            async with sem:
                await _fetch(client, url, directory / f"file-{index}", threaded)

        await asyncio.gather(*(one(i, url) for i, url in enumerate(urls)))


def bench_writes(hrefs: list[str], workers: int, work_dir: Path | None) -> list[tuple]:
    rows = []
    for threaded in (False, True):
        urls = [planetary_computer.sign(href) for href in hrefs]
        with Measurement(work_dir) as m:
            asyncio.run(_fetch_all(urls, m.directory, threaded, workers))
        mode = "threaded" if threaded else "blocking"
        rows.append(m.row("writes", f"{mode}, {workers} files at once"))
    return rows


# ---------------------------------------------------------------------------
# Section: ranges — several connections for one file
# ---------------------------------------------------------------------------


async def _fetch_range(
    client: httpx.AsyncClient, url: str, fd: int, start: int, end: int
) -> None:
    headers = {"Range": f"bytes={start}-{end}"}
    offset = start
    async with client.stream("GET", url, headers=headers, timeout=60) as resp:
        resp.raise_for_status()
        if resp.status_code != 206:
            raise SystemExit("The server ignored the Range request.")
        async for chunk in resp.aiter_bytes(chunk_size=_CHUNK):
            os.pwrite(fd, chunk, offset)
            offset += len(chunk)


async def _fetch_in_ranges(url: str, path: Path, connections: int) -> None:
    async with httpx.AsyncClient(follow_redirects=True) as client:
        head = await client.head(url, timeout=60)
        head.raise_for_status()
        size = int(head.headers["Content-Length"])
        step = -(-size // connections)  # ceiling division
        fd = os.open(path, os.O_WRONLY | os.O_CREAT)
        try:
            await asyncio.gather(
                *(
                    _fetch_range(client, url, fd, start, min(start + step, size) - 1)
                    for start in range(0, size, step)
                )
            )
        finally:
            os.close(fd)


def bench_ranges(href: str, work_dir: Path | None) -> list[tuple]:
    rows = []
    for connections in (1, 4, 8):
        url = planetary_computer.sign(href)
        with Measurement(work_dir) as m:
            asyncio.run(_fetch_in_ranges(url, m.directory / "file", connections))
        rows.append(m.row("ranges", f"1 file, {connections} connection(s)"))
    return rows


# ---------------------------------------------------------------------------


def print_table(rows: list[tuple]) -> None:
    header = ("section", "setting", "MB", "seconds", "MB/s")
    widths = [max(len(str(r[i])) for r in [header, *rows]) for i in range(len(header))]
    for row in [header, tuple("-" * w for w in widths), *rows]:
        cells = [
            str(c).ljust(w) if i < 2 else str(c).rjust(w)
            for i, (c, w) in enumerate(zip(row, widths))
        ]
        print("  ".join(cells))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--files", type=int, default=8, help="number of Items")
    parser.add_argument("--asset", default="vv", help="asset key to download")
    parser.add_argument(
        "--sections",
        nargs="+",
        choices=("workers", "writes", "ranges"),
        default=("workers", "writes", "ranges"),
    )
    parser.add_argument(
        "--write-workers",
        type=int,
        default=8,
        help="files downloaded at once in the writes section",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=None,
        help="directory to download into (default: the system temp directory)",
    )
    args = parser.parse_args()

    gdf, hrefs = find_hrefs(args.asset, args.files)
    print(f"{len(hrefs)} x {args.asset!r} from {_COLLECTION}\n")

    rows: list[tuple] = []
    if "workers" in args.sections:
        rows += bench_workers(gdf, args.asset, args.work_dir)
    if "writes" in args.sections:
        rows += bench_writes(hrefs, args.write_workers, args.work_dir)
    if "ranges" in args.sections:
        rows += bench_ranges(hrefs[0], args.work_dir)
    print_table(rows)


if __name__ == "__main__":
    main()
