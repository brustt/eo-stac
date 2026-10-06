"""Sensor-specific search parameter dataclasses.

Each ``SearchParams`` subclass owns its own mapping to STAC properties via
:meth:`SearchParams.to_query`, which returns a STAC *query extension* dict of the
form ``{stac_property: {operator: value}}``.  ``None`` fields are omitted so the
resulting query carries only the filters the caller actually set.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _add(query: dict[str, dict], prop: str, op: str, value: Any) -> None:
    """Append ``{prop: {op: value}}`` to *query* when *value* is not ``None``."""
    if value is not None:
        query[prop] = {op: value}


@dataclass
class SearchParams:
    """Base filter params, applicable to any sensor."""

    platform: str | None = None
    product_type: str | None = None

    def to_query(self) -> dict[str, dict]:
        """Return a STAC query-extension dict for the non-``None`` fields."""
        query: dict[str, dict] = {}
        _add(query, "platform", "eq", self.platform)
        _add(query, "product_type", "eq", self.product_type)
        return query


@dataclass
class OpticalParams(SearchParams):
    """Filter params for optical sensors (Sentinel-2, Landsat, etc.)."""

    max_cloud_cover: float | None = None

    def to_query(self) -> dict[str, dict]:
        query = super().to_query()
        _add(query, "eo:cloud_cover", "lte", self.max_cloud_cover)
        return query


@dataclass
class Sentinel2Params(OpticalParams):
    """Filter params for Sentinel-2. Typed alias of OpticalParams."""

    pass


@dataclass
class LandsatParams(OpticalParams):
    """Filter params for Landsat (8/9)."""

    wrs_path: int | None = None
    wrs_row: int | None = None

    def to_query(self) -> dict[str, dict]:
        query = super().to_query()
        _add(query, "landsat:wrs_path", "eq", self.wrs_path)
        _add(query, "landsat:wrs_row", "eq", self.wrs_row)
        return query


@dataclass
class SARParams(SearchParams):
    """Filter params for SAR sensors (Sentinel-1, etc.)."""

    orbit_state: str | None = None  # sat:orbit_state: "ascending"/"descending"
    polarizations: list[str] | None = None  # sar:polarizations, e.g. ["VV", "VH"]
    instrument_mode: str | None = None  # sar:instrument_mode, e.g. "IW", "EW"

    def to_query(self) -> dict[str, dict]:
        query = super().to_query()
        _add(query, "sat:orbit_state", "eq", self.orbit_state)
        _add(query, "sar:polarizations", "in", self.polarizations)
        _add(query, "sar:instrument_mode", "eq", self.instrument_mode)
        return query


@dataclass
class Sentinel1Params(SARParams):
    """Filter params for Sentinel-1."""

    orbit_number: int | None = None  # sat:relative_orbit

    def to_query(self) -> dict[str, dict]:
        query = super().to_query()
        _add(query, "sat:relative_orbit", "eq", self.orbit_number)
        return query


@dataclass
class DEMParams(SearchParams):
    """Filter params for DEM collections (Copernicus DEM, etc.)."""

    dem_type: str | None = None  # dem:type — e.g. "cop-dem-glo-30"

    def to_query(self) -> dict[str, dict]:
        query = super().to_query()
        _add(query, "dem:type", "eq", self.dem_type)
        return query
