# 05: Asset media types in search results

**Spec:** `docs/specs/efficient-downloads.md`

**What to build:** Search results carry each asset's STAC media type, so a caller can see which assets are rasters and the library can later decide which assets become Subsets.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] `search_items` returns an `asset_types` column mapping each Asset Key to its media type, beside `assets` and `asset_roles`
- [ ] An asset with no declared media type maps to an empty value rather than being omitted
- [ ] An empty search result still has the `asset_types` column
- [ ] `asset_types` is left out of the metadata GeoPackage, like `assets` and `asset_roles`
- [ ] The column is documented alongside the other GDF columns
- [ ] Tests extend the existing item-to-GDF tests
