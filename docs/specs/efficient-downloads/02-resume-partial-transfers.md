# 02: Resume partial transfers

**Spec:** `docs/specs/efficient-downloads.md`

**What to build:** A partial transfer is Resumed instead of restarted. If a connection drops partway through a large file, the retry asks the server only for the missing bytes; if the whole call fails, the partial file is kept and the next `download_items` call continues it. Resume never produces a file spliced from two versions of the remote file, and it never leaves a partial file where a finished one is expected. Skip-existing for finished files is unchanged.

**Blocked by:** 01 (Retry only failures that can succeed)

**Status:** ready-for-agent

- [ ] A retry within one call sends a Range request for the bytes not yet received and appends them to the partial file
- [ ] After a call ends in failure, the partial file remains; a later call for the same asset continues it with a Range request
- [ ] The Range request is conditional on the ETag recorded with the partial file; if the remote ETag differs, the transfer restarts from zero
- [ ] If the server sends no ETag, or answers a Range request with the full body, the transfer restarts from zero and the result is still a correct file
- [ ] The partial file becomes the final file only by rename on completion; `local_paths` never points at a partial file
- [ ] `overwrite=True` removes both the finished file and any partial file before transferring
- [ ] A finished file is still skipped on later calls without any request (Skip-existing)
- [ ] The Resume bookkeeping stored next to the partial file can describe several completed ranges, not only a single offset
- [ ] Tests go through `download_items` with `respx`, covering Resume within a call, Resume across two calls, ETag mismatch, a server without range support, and `overwrite`
- [ ] User-facing documentation uses Skip-existing and Resume as defined in `CONTEXT.md`
