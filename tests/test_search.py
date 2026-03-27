"""Tests for eostac.search.search_items()."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import pytest
import responses as resp_lib

from eostac.catalog import STACSource
from eostac.search import search_items, _items_to_gdf, _get_cloud_cover, _normalize_datetime
from eostac.exceptions import SearchError
from tests.conftest import (
    FAKE_API_URL,
    FAKE_COLLECTIONS,
    FAKE_CONFORMANCE,
    FAKE_LANDING,
    make_item,
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
# _get_cloud_cover normalisation
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_get_cloud_cover_eo_prefix() -> None:
    assert _get_cloud_cover({"eo:cloud_cover": 20.0}) == 20.0


@pytest.mark.unit
def test_get_cloud_cover_fractional_normalised() -> None:
    # 0–1 range (exclusive) should be normalised to 0–100
    assert _get_cloud_cover({"eo:cloud_cover": 0.2}) == pytest.approx(20.0)


@pytest.mark.unit
def test_get_cloud_cover_boundary_one_percent() -> None:
    # 1.0 is ambiguous (1% vs 100%) — spec says 0–100, so 1.0 should stay as 1%
    assert _get_cloud_cover({"eo:cloud_cover": 1.0}) == pytest.approx(1.0)


@pytest.mark.unit
def test_get_cloud_cover_missing() -> None:
    assert _get_cloud_cover({}) is None


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
# Static catalog search
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_search_static_catalog_returns_gdf(static_catalog_dir: Path) -> None:
    source = STACSource.from_json(static_catalog_dir / "catalog.json")
    gdf = search_items(source)
    assert isinstance(gdf, gpd.GeoDataFrame)
    assert len(gdf) >= 1


@pytest.mark.unit
def test_search_static_bbox_overlapping_returns_items(static_catalog_dir: Path) -> None:
    source = STACSource.from_json(static_catalog_dir / "catalog.json")
    gdf = search_items(source, bbox=[9.0, 44.0, 13.0, 48.0])
    assert len(gdf) >= 1


@pytest.mark.unit
def test_search_static_bbox_non_overlapping_returns_empty(static_catalog_dir: Path) -> None:
    source = STACSource.from_json(static_catalog_dir / "catalog.json")
    gdf = search_items(source, bbox=[100.0, 0.0, 101.0, 1.0])
    assert len(gdf) == 0


@pytest.mark.unit
def test_search_static_cloud_cover_filter(static_catalog_dir: Path) -> None:
    source = STACSource.from_json(static_catalog_dir / "catalog.json")
    # Item has cloud_cover = 10; filtering at 5 should exclude it
    gdf = search_items(source, max_cloud_cover=5.0)
    assert len(gdf) == 0


@pytest.mark.unit
def test_search_static_limit(static_catalog_dir: Path) -> None:
    source = STACSource.from_json(static_catalog_dir / "catalog.json")
    gdf = search_items(source, limit=1)
    assert len(gdf) <= 1


@pytest.mark.unit
def test_search_static_datetime_within_range_returns_items(static_catalog_dir: Path) -> None:
    source = STACSource.from_json(static_catalog_dir / "catalog.json")
    gdf = search_items(source, datetime="2020-01-01/2020-12-31")
    assert len(gdf) >= 1


@pytest.mark.unit
def test_search_static_datetime_outside_range_returns_empty(static_catalog_dir: Path) -> None:
    source = STACSource.from_json(static_catalog_dir / "catalog.json")
    gdf = search_items(source, datetime="2021-01-01/2021-12-31")
    assert len(gdf) == 0


@pytest.mark.unit
def test_search_static_product_type_match_returns_items(static_catalog_dir: Path) -> None:
    source = STACSource.from_json(static_catalog_dir / "catalog.json")
    gdf = search_items(source, product_type="L2A")
    assert len(gdf) >= 1


@pytest.mark.unit
def test_search_static_product_type_mismatch_returns_empty(static_catalog_dir: Path) -> None:
    source = STACSource.from_json(static_catalog_dir / "catalog.json")
    gdf = search_items(source, product_type="L1C")
    assert len(gdf) == 0


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
