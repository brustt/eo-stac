# 03: Download benchmark script

**Spec:** `docs/specs/efficient-downloads.md`

**What to build:** A maintainer can run one standalone script and get the numbers needed to decide whether several connections per file and non-blocking disk writes are worth building. The script uses the reference workload from `CONTEXT.md`: full Sentinel-1 scenes from Planetary Computer.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] The script lives in a `benchmarks` directory, outside the test suite, and is never collected by pytest
- [ ] It downloads a fixed set of about eight full Sentinel-1 `vv` files from Planetary Computer (roughly 5 GB per run), using Signing
- [ ] It reports wall time and MB/s for `max_workers` 4, 8 and 16
- [ ] It compares blocking and threaded disk writes on the same workload
- [ ] It fetches one file over 1, 4 and 8 range connections and reports throughput for each
- [ ] Each measurement starts from an empty output directory so Skip-existing and Resume do not distort results
- [ ] Results are printed as a single table, and the script's header comment says how to run it and how much data it transfers
- [ ] The signing package it needs is declared so the script runs with one documented command
