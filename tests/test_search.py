"""Tests for eostac.search.search_items()."""

from __future__ import annotations

from datetime import datetime, timezone

import geopandas as gpd
import pytest
import responses as resp_lib

from eostac.catalog import STACSource
from eostac.params import OpticalParams
from eostac.search import (
    _items_to_gdf,
    _normalize_datetime,
    search_items,
)
from tests.conftest import (
    FAKE_API_URL,
    FAKE_COLLECTIONS,
    FAKE_CONFORMANCE,
    FAKE_LANDING,
    make_fake_search_response,
)

# ---------------------------------------------------------------------------
# _items_to_gdf helpers
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_items_to_gdf_empty() -> None:
    gdf = _items_to_gdf([])
    assert isinstance(gdf, gpd.GeoDataFrame)
    assert len(gdf) == 0
    assert "item_id" in gdf.columns


@pytest.mark.unit
def test_items_to_gdf_columns(sample_item) -> None:
    gdf = _items_to_gdf([sample_item])
    assert gdf.iloc[0]["item_id"] == "test-item-001"
    assert gdf.iloc[0]["collection"] == "sentinel-2-l2a"
    assert gdf.iloc[0]["cloud_cover"] == 10.0
    assert isinstance(gdf.iloc[0]["assets"], dict)
    assert "B04" in gdf.iloc[0]["assets"]


@pytest.mark.unit
def test_items_to_gdf_crs(sample_item) -> None:
    gdf = _items_to_gdf([sample_item])
    assert gdf.crs.to_epsg() == 4326


# ---------------------------------------------------------------------------
# _normalize_datetime
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_normalize_datetime_string_passthrough() -> None:
    assert _normalize_datetime("2020-01-01/2020-12-31") == "2020-01-01/2020-12-31"


@pytest.mark.unit
def test_normalize_datetime_none_returns_none() -> None:
    assert _normalize_datetime(None) is None


@pytest.mark.unit
def test_normalize_datetime_tuple_of_strings() -> None:
    result = _normalize_datetime(("2020-01-01", "2020-12-31"))
    assert result == "2020-01-01/2020-12-31"


@pytest.mark.unit
def test_normalize_datetime_tuple_of_datetimes() -> None:
    start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    end = datetime(2020, 12, 31, tzinfo=timezone.utc)
    result = _normalize_datetime((start, end))
    assert result is not None
    assert "/" in result
    assert "2020-01-01" in result
    assert "2020-12-31" in result


# ---------------------------------------------------------------------------
# API search (mocked)
# ---------------------------------------------------------------------------


def _setup_api_mocks(items=None) -> None:
    resp_lib.add(resp_lib.GET, FAKE_API_URL, json=FAKE_LANDING, status=200)
    resp_lib.add(
        resp_lib.GET, f"{FAKE_API_URL}/conformance", json=FAKE_CONFORMANCE, status=200
    )
    resp_lib.add(
        resp_lib.GET, f"{FAKE_API_URL}/collections", json=FAKE_COLLECTIONS, status=200
    )
    resp_lib.add(
        resp_lib.GET,
        f"{FAKE_API_URL}/search",
        json=make_fake_search_response(items),
        status=200,
    )
    resp_lib.add(
        resp_lib.POST,
        f"{FAKE_API_URL}/search",
        json=make_fake_search_response(items),
        status=200,
    )


@pytest.mark.unit
@resp_lib.activate
def test_search_api_returns_gdf() -> None:
    _setup_api_mocks()
    source = STACSource.from_url(FAKE_API_URL)
    gdf = search_items(source, collections=["sentinel-2-l2a"])
    assert isinstance(gdf, gpd.GeoDataFrame)
    assert len(gdf) == 1


@pytest.mark.unit
@resp_lib.activate
def test_search_api_no_items() -> None:
    _setup_api_mocks(items=[])
    source = STACSource.from_url(FAKE_API_URL)
    gdf = search_items(source)
    assert len(gdf) == 0


@pytest.mark.unit
@resp_lib.activate
def test_search_api_polygon_filter() -> None:
    from shapely.geometry import box

    _setup_api_mocks()
    source = STACSource.from_url(FAKE_API_URL)
    poly = box(9.0, 44.0, 13.0, 48.0)
    gdf = search_items(source, polygon=poly)
    assert isinstance(gdf, gpd.GeoDataFrame)


@pytest.mark.unit
@resp_lib.activate
def test_search_api_params_pushed_as_query() -> None:
    """Sensor params are serialised into the request as a STAC query body."""
    _setup_api_mocks()
    source = STACSource.from_url(FAKE_API_URL)
    gdf = search_items(source, params=OpticalParams(max_cloud_cover=20))
    assert isinstance(gdf, gpd.GeoDataFrame)

    # pystac_client POSTs item-search; assert the query filter reached the wire.
    post_calls = [c for c in resp_lib.calls if c.request.method == "POST"]
    assert post_calls, "expected a POST /search request"
    body = post_calls[-1].request.body
    payload = body.decode() if isinstance(body, (bytes, bytearray)) else body
    assert "eo:cloud_cover" in payload
    assert "lte" in payload
