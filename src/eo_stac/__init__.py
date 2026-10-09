"""eo_stac — search and download satellite imagery via STAC."""

from eo_stac import viz
from eo_stac.aliases import ALIAS_TABLE, resolve
from eo_stac.catalog import STACSource
from eo_stac.download import download_items
from eo_stac.params import (
    DEMParams,
    LandsatParams,
    OpticalParams,
    SARParams,
    SearchParams,
    Sentinel1Params,
    Sentinel2Params,
)
from eo_stac.search import search_items
from eo_stac.storage import PathManager

__all__ = [
    "STACSource",
    "search_items",
    "download_items",
    "PathManager",
    "viz",
    "ALIAS_TABLE",
    "resolve",
    "SearchParams",
    "OpticalParams",
    "Sentinel2Params",
    "LandsatParams",
    "SARParams",
    "Sentinel1Params",
    "DEMParams",
]
