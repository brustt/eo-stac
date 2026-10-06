# 01: Retry only failures that can succeed

**Spec:** `docs/specs/efficient-downloads.md`

**What to build:** When a download fails, `download_items` retries it only if a retry can plausibly succeed. A permanent error such as HTTP 403 or 404 is reported at once instead of after three backoff delays, so a run with a missing Signing callable or a bad href fails in seconds. Transient errors are retried politely, and a token that expired during a long transfer is refreshed on retry. One asset's failure stays a warning and does not stop the other downloads.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] Connection errors, timeouts and HTTP 408, 429 and 5xx are retried, up to three attempts
- [ ] Any other 4xx status fails after a single request, with no backoff delay
- [ ] When the server sends `Retry-After`, the wait before the next attempt is at least that long
- [ ] Backoff stays exponential and includes jitter, so concurrent retries are spread out
- [ ] The Signing callable is applied to the unsigned href before every attempt, not once per asset (ADR 0002)
- [ ] The failure message states the final status or error and whether the request was retried
- [ ] A failed asset is reported through the existing warning and is absent from `local_paths`; other assets in the call still download
- [ ] Tests go through `download_items` with HTTP faked by `respx` and sleeping patched, asserting on requests made, files on disk and warnings
