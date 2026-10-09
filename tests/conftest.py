"""Shared test fixtures."""

from __future__ import annotations

from datetime import datetime, timezone

import geopandas as gpd
import pystac
import pytest
from shapely.geometry import box, mapping

# ---------------------------------------------------------------------------
# Item / GDF fixtures
# ---------------------------------------------------------------------------


def make_item(
    item_id: str = "test-item-001",
    collection: str = "sentinel-2-l2a",
    cloud_cover: float = 10.0,
    date: str = "2020-06-15",
    bbox: list[float] | None = None,
) -> pystac.Item:
    """Create a minimal pystac.Item for testing."""
    if bbox is None:
        bbox = [10.0, 45.0, 12.0, 47.0]
    geom = mapping(box(*bbox))
    dt = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    item = pystac.Item(
        id=item_id,
        geometry=geom,
        bbox=bbox,
        datetime=dt,
        properties={
            "datetime": dt.isoformat(),
            "eo:cloud_cover": cloud_cover,
            "platform": "sentinel-2a",
            "product_type": "L2A",
        },
    )
    item.collection_id = collection
    item.add_asset(
        "B04",
        pystac.Asset(
            href="https://example.com/B04.tif",
            media_type="image/tiff",
            roles=["data"],
        ),
    )
    item.add_asset(
        "B03",
        pystac.Asset(
            href="https://example.com/B03.tif",
            media_type="image/tiff",
            roles=["data"],
        ),
    )
    item.add_asset(
        "thumbnail",
        pystac.Asset(
            href="https://example.com/thumb.jpg",
            media_type="image/jpeg",
            roles=["thumbnail"],
        ),
    )
    return item


@pytest.fixture
def sample_item() -> pystac.Item:
    return make_item()


@pytest.fixture
def sample_gdf() -> gpd.GeoDataFrame:
    """GeoDataFrame with 3 items across different cloud covers."""
    from eo_stac.search import _items_to_gdf

    items = [
        make_item("item-001", cloud_cover=5.0, bbox=[10.0, 45.0, 12.0, 47.0]),
        make_item("item-002", cloud_cover=50.0, bbox=[11.0, 46.0, 13.0, 48.0]),
        make_item("item-003", cloud_cover=90.0, bbox=[12.0, 47.0, 14.0, 49.0]),
    ]
    return _items_to_gdf(items)


# ---------------------------------------------------------------------------
# HTTP mock helpers (used per test with `responses` library)
# ---------------------------------------------------------------------------

FAKE_API_URL = "https://fake-stac-api.example.com"

FAKE_CONFORMANCE = {
    "conformsTo": [
        "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/core",
        "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/oas30",
        "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/geojson",
        "https://api.stacspec.org/v1.0.0/core",
        "https://api.stacspec.org/v1.0.0/item-search",
        "https://api.stacspec.org/v1.0.0/item-search#query",
        "https://api.stacspec.org/v1.0.0/ogcapi-features",
    ]
}

FAKE_LANDING = {
    "type": "Catalog",
    "id": "fake-api",
    "title": "Fake STAC API",
    "description": "A fake STAC API for testing",
    "stac_version": "1.0.0",
    "links": [
        {"rel": "self", "href": FAKE_API_URL, "type": "application/json"},
        {"rel": "root", "href": FAKE_API_URL, "type": "application/json"},
        {
            "rel": "conformance",
            "href": f"{FAKE_API_URL}/conformance",
            "type": "application/json",
        },
        {
            "rel": "data",
            "href": f"{FAKE_API_URL}/collections",
            "type": "application/json",
        },
        {
            "rel": "search",
            "href": f"{FAKE_API_URL}/search",
            "type": "application/json",
            "method": "GET",
        },
        {
            "rel": "search",
            "href": f"{FAKE_API_URL}/search",
            "type": "application/json",
            "method": "POST",
        },
    ],
    "conformsTo": FAKE_CONFORMANCE["conformsTo"],
    "api_version": "1.0.0",
}

FAKE_COLLECTIONS = {
    "collections": [
        {
            "type": "Collection",
            "id": "sentinel-2-l2a",
            "title": "Sentinel-2 L2A",
            "description": "Sentinel-2 Level-2A",
            "stac_version": "1.0.0",
            "links": [],
            "extent": {
                "spatial": {"bbox": [[-180, -90, 180, 90]]},
                "temporal": {"interval": [["2015-06-23T00:00:00Z", None]]},
            },
            "license": "proprietary",
        }
    ],
    "links": [],
}


def make_fake_search_response(items: list[pystac.Item] | None = None) -> dict:
    if items is None:
        item = make_item()
        items = [item]
    return {
        "type": "FeatureCollection",
        "features": [i.to_dict() for i in items],
        "links": [],
        "context": {"matched": len(items), "returned": len(items)},
    }
