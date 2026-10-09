# Download benchmark results

Command: `uv run benchmarks/download_benchmark.py`
Workload: 8 x `vv` assets from `sentinel-1-rtc` (single run, no repeats).

| section | setting                    |     MB | seconds | MB/s |
|---------|----------------------------|-------:|--------:|-----:|
| workers | max_workers=4              | 14,833 |   492.3 | 30.1 |
| workers | max_workers=8              | 14,833 |   490.8 | 30.2 |
| workers | max_workers=16             | 14,833 |   482.4 | 30.7 |
| writes  | blocking, 8 files at once  | 14,833 |   481.7 | 30.8 |
| writes  | threaded, 8 files at once  | 14,833 |   495.8 | 29.9 |
| ranges  | 1 file, 1 connection(s)    |  1,865 |    92.2 | 20.2 |
| ranges  | 1 file, 4 connection(s)    |  1,865 |    66.9 | 27.9 |
| ranges  | 1 file, 8 connection(s)    |  1,865 |    59.1 | 31.6 |

## Conclusion

The bottleneck is bandwidth, not the library. Every configuration lands at about 30 MB/s.

- **Worker count is irrelevant (4, 8, 16).** 30.1 to 30.7 MB/s is within noise, so the pipe is already full at 4 workers.
- **Threaded writes don't help.** Blocking writes (30.8 MB/s) match or beat threaded writes (29.9 MB/s). Disk is not the limiter.
- **A single connection tops out near 20 MB/s**, which looks like a per-connection cap. Range splitting lifts one file to the same ~31 MB/s ceiling (1 -> 4 -> 8 connections: 20.2 -> 27.9 -> 31.6), with diminishing returns.

Implications:

- Range splitting only pays off for one or a few large files. With many files, parallelism across files already reaches the ceiling.
- Keep the default raw async streaming; do not add threaded writes.
- `max_workers=4` is a sensible default; higher values cost nothing but gain nothing.

## Caveats

- Single run: differences under ~3% (the whole workers and writes sections) are noise.
- `max_workers` 1 and 2 were not tested, so the saturation knee is unknown.
- The ranges test used one file in a separate run; it is not strictly comparable to the 8-file runs.
- The source of the ~31 MB/s cap (local link, provider throttle, storage backend) is unknown. Compare against a plain `curl` or `aria2c -x16` on the same hrefs, or rerun from another network.
