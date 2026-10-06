"""Built-in Alias Table: Band Alias -> ordered list of candidate Asset Keys."""

from __future__ import annotations

from types import MappingProxyType

ALIAS_TABLE: MappingProxyType[str, list[str]] = MappingProxyType({
    # Optical: spectral bands (Sentinel-2 / Landsat 8-9 / generic)
    "coastal":     ["coastal", "B01", "SR_B1", "B01_60m"],
    "blue":        ["blue", "B02", "SR_B2", "B02_10m"],
    "green":       ["green", "B03", "SR_B3", "B03_10m"],
    "red":         ["red", "B04", "SR_B4", "B04_10m"],
    "rededge1":    ["rededge1", "B05", "B05_20m"],
    "rededge2":    ["rededge2", "B06", "B06_20m"],
    "rededge3":    ["rededge3", "B07", "B07_20m"],
    "nir":         ["nir", "B08", "B8", "SR_B5", "B08_10m"],
    "nir08":       ["nir08", "B8A", "B08A"],
    "water_vapor": ["water_vapor", "B09", "B09_60m"],
    "cirrus":      ["cirrus", "B10", "B10_60m", "SR_B9"],
    "swir1":       ["swir1", "B11", "SR_B6", "B11_20m"],
    "swir2":       ["swir2", "B12", "SR_B7", "B12_20m"],
    "pan":         ["pan", "SR_B8"],
    "thermal":     ["thermal", "ST_B10", "ST_B6"],
    # Optical: processed / visual assets
    "visual":      ["visual", "TCI", "TCI_10m", "true_color"],
    "scl":         ["scl", "SCL", "SCL_20m"],
    # SAR polarizations (Sentinel-1)
    "vv":          ["vv", "VV", "vv-db"],
    "vh":          ["vh", "VH", "vh-db"],
    "hh":          ["hh", "HH", "hh-db"],
    "hv":          ["hv", "HV", "hv-db"],
    # DEM (Copernicus DEM uses "data" as the elevation asset key)
    "elevation":   ["elevation", "dem", "DEM", "data"],
    # Common metadata / overview assets
    "thumbnail":   ["thumbnail", "preview", "overview"],
})


def resolve(
    requested: str,
    item_assets: dict[str, str],
    extra_aliases: dict[str, list[str]] | None = None,
) -> tuple[str, str]:
    """Resolve *requested* (Band Alias or raw Asset Key) to ``(requested, href)``.

    Resolution order:
    1. Merge *extra_aliases* on top of the built-in ``ALIAS_TABLE``.
    2. If *requested* is in the merged table, try each candidate key in order
       against *item_assets*.
    3. Otherwise try *requested* as a raw Asset Key directly.

    Returns ``(requested, href)`` so the caller preserves the alias as the
    local file name.  Raises ``KeyError`` if nothing resolves.
    """
    table: dict[str, list[str]] = {**ALIAS_TABLE, **(extra_aliases or {})}

    if requested in table:
        for candidate in table[requested]:
            if candidate in item_assets:
                return requested, item_assets[candidate]
        raise KeyError(requested)

    if requested in item_assets:
        return requested, item_assets[requested]

    raise KeyError(requested)
