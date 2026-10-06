"""eostac — search and download satellite imagery via STAC."""

from eostac.aliases import ALIAS_TABLE, resolve
from eostac.catalog import STACSource
from eostac.download import download_items
from eostac.params import (
    DEMParams,
    LandsatParams,
    OpticalParams,
    SARParams,
    SearchParams,
    Sentinel1Params,
    Sentinel2Params,
)
from eostac.search import search_items
from eostac.storage import PathManager
from eostac import viz

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
