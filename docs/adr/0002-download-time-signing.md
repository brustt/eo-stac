# Signing happens at download time

Some Providers (e.g., Planetary Computer) serve asset hrefs that cannot be fetched until they are signed with a short-lived token. Signing is applied by `download_items` (and the viz helpers) through a caller-supplied `sign` callable, just before each href is fetched, rather than at search time through `pystac_client`'s `modifier`.

Search-time signing was rejected because: (1) tokens expire in about an hour, so a GDF that is inspected, filtered or saved before downloading would fail with authorisation errors unrelated to anything the caller did; (2) signed hrefs make the GDF Provider-specific and non-reusable, while unsigned hrefs are stable identifiers; (3) the library knows no Provider by name, so Signing has to be a generic hook either way, and the hook belongs where the fetch happens.

## Consequences

- `download_items`, `viz.plot_thumbnails` and `viz.quick_view` each take `sign`. The caller passes it at every call; it is not remembered on the STACSource or in the GDF.
- The `assets` column always holds unsigned hrefs. Fetching one outside the library requires the caller to sign it themselves.
- The library has no dependency on any Provider's signing package; callers install it (e.g., `planetary-computer`) and pass its function.
