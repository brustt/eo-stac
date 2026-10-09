"""Tests for eo_stac.search.search_items()."""

from __future__ import annotations

import json
import warnings
from datetime import datetime, timezone

import geopandas as gpd
import pystac
import pytest
import responses as resp_lib

from eo_stac.catalog import STACSource
from eo_stac.exceptions import SearchError
from eo_stac.params import OpticalParams, Sentinel1Params
from eo_stac.search import (
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
    make_item,
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


_FILTER_CONFORMANCE = "https://api.stacspec.org/v1.0.0/item-search#filter"


def _setup_api_mocks(items=None, with_filter: bool = False) -> None:
    landing, conformance = FAKE_LANDING, FAKE_CONFORMANCE
    if with_filter:
        conforms_to = [*FAKE_CONFORMANCE["conformsTo"], _FILTER_CONFORMANCE]
        landing = {**FAKE_LANDING, "conformsTo": conforms_to}
        conformance = {"conformsTo": conforms_to}
    resp_lib.add(resp_lib.GET, FAKE_API_URL, json=landing, status=200)
    resp_lib.add(
        resp_lib.GET, f"{FAKE_API_URL}/conformance", json=conformance, status=200
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


def _post_bodies() -> list[dict]:
    posts = [c for c in resp_lib.calls if c.request.method == "POST"]
    return [json.loads(c.request.body) for c in posts]


@pytest.mark.unit
def test_items_to_gdf_keeps_asset_roles(sample_item) -> None:
    gdf = _items_to_gdf([sample_item])
    assert gdf.iloc[0]["asset_roles"] == {
        "B04": ["data"],
        "B03": ["data"],
        "thumbnail": ["thumbnail"],
    }


@pytest.mark.unit
@resp_lib.activate
def test_extra_query_overrides_params_per_property() -> None:
    _setup_api_mocks()
    source = STACSource.from_url(FAKE_API_URL)
    search_items(
        source,
        params=Sentinel1Params(platform="SENTINEL-1A", orbit_state="ascending"),
        extra_query={"platform": {"eq": "sentinel-1a"}, "s1:slice_number": {"eq": 3}},
    )
    assert _post_bodies()[-1]["query"] == {
        "platform": {"eq": "sentinel-1a"},
        "sat:orbit_state": {"eq": "ascending"},
        "s1:slice_number": {"eq": 3},
    }


@pytest.mark.unit
@resp_lib.activate
def test_polarizations_sent_as_cql2_filter() -> None:
    _setup_api_mocks(with_filter=True)
    source = STACSource.from_url(FAKE_API_URL)
    search_items(
        source,
        params=Sentinel1Params(polarizations=["VV", "VH"], orbit_state="ascending"),
    )
    body = _post_bodies()[-1]
    assert "query" not in body
    assert body["filter-lang"] == "cql2-json"
    assert body["filter"] == {
        "op": "and",
        "args": [
            {"op": "=", "args": [{"property": "sat:orbit_state"}, "ascending"]},
            {
                "op": "a_contains",
                "args": [{"property": "sar:polarizations"}, ["VV", "VH"]],
            },
        ],
    }


@pytest.mark.unit
@resp_lib.activate
def test_polarizations_without_filter_support_raises() -> None:
    _setup_api_mocks()
    source = STACSource.from_url(FAKE_API_URL)
    with pytest.raises(SearchError, match="sar:polarizations"):
        search_items(source, params=Sentinel1Params(polarizations=["VV"]))


def _setup_filtered_empty_then_sample(sample_items) -> None:
    """Landing mocks, then POST /search: first response empty, second the sample."""
    resp_lib.add(resp_lib.GET, FAKE_API_URL, json=FAKE_LANDING, status=200)
    resp_lib.add(
        resp_lib.GET, f"{FAKE_API_URL}/conformance", json=FAKE_CONFORMANCE, status=200
    )
    resp_lib.add(
        resp_lib.POST,
        f"{FAKE_API_URL}/search",
        json=make_fake_search_response([]),
        status=200,
    )
    resp_lib.add(
        resp_lib.POST,
        f"{FAKE_API_URL}/search",
        json=make_fake_search_response(sample_items),
        status=200,
    )


@pytest.mark.unit
@resp_lib.activate
def test_zero_results_warns_naming_missing_property() -> None:
    _setup_filtered_empty_then_sample([make_item()])
    source = STACSource.from_url(FAKE_API_URL)
    with pytest.warns(UserWarning, match="sar:product_type"):
        gdf = search_items(source, params=Sentinel1Params(product_type="GRD"))
    assert len(gdf) == 0
    assert "query" not in _post_bodies()[-1]


@pytest.mark.unit
@resp_lib.activate
def test_zero_results_no_warning_when_property_exists() -> None:
    _setup_filtered_empty_then_sample([make_item()])
    source = STACSource.from_url(FAKE_API_URL)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        gdf = search_items(source, params=OpticalParams(max_cloud_cover=1))
    assert len(gdf) == 0


@pytest.mark.unit
def test_items_to_gdf_keeps_asset_media_types(sample_item) -> None:
    sample_item.add_asset("manifest", pystac.Asset(href="https://example.com/m"))
    gdf = _items_to_gdf([sample_item])
    assert gdf.iloc[0]["asset_types"] == {
        "B04": "image/tiff",
        "B03": "image/tiff",
        "thumbnail": "image/jpeg",
        "manifest": "",
    }


@pytest.mark.unit
def test_items_to_gdf_empty_has_asset_types_column() -> None:
    assert "asset_types" in _items_to_gdf([]).columns
