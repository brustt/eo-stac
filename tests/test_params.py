"""Tests for eostac.params — to_query() STAC query-extension mapping."""

from __future__ import annotations

import pytest

from eostac.params import (
    DEMParams,
    LandsatParams,
    OpticalParams,
    SARParams,
    SearchParams,
    Sentinel1Params,
    Sentinel2Params,
)


@pytest.mark.unit
def test_empty_params_yield_empty_query() -> None:
    assert SearchParams().to_query() == {}


@pytest.mark.unit
def test_none_fields_are_omitted() -> None:
    # Only product_type set → platform stays out of the query.
    assert SearchParams(product_type="L2A").to_query() == {
        "product_type": {"eq": "L2A"}
    }


@pytest.mark.unit
def test_base_platform_and_product_type() -> None:
    assert SearchParams(platform="sentinel-2a", product_type="L2A").to_query() == {
        "platform": {"eq": "sentinel-2a"},
        "product_type": {"eq": "L2A"},
    }


@pytest.mark.unit
def test_optical_cloud_cover_lte() -> None:
    assert OpticalParams(max_cloud_cover=20).to_query() == {
        "eo:cloud_cover": {"lte": 20}
    }


@pytest.mark.unit
def test_sentinel2_alias_inherits_optical() -> None:
    assert Sentinel2Params(max_cloud_cover=5).to_query() == {
        "eo:cloud_cover": {"lte": 5}
    }


@pytest.mark.unit
def test_landsat_wrs_and_cloud_cover() -> None:
    assert LandsatParams(max_cloud_cover=10, wrs_path=193, wrs_row=26).to_query() == {
        "eo:cloud_cover": {"lte": 10},
        "landsat:wrs_path": {"eq": 193},
        "landsat:wrs_row": {"eq": 26},
    }


@pytest.mark.unit
def test_sar_full_mapping() -> None:
    assert SARParams(
        orbit_state="ascending",
        polarizations=["VV", "VH"],
        instrument_mode="IW",
    ).to_query() == {
        "sat:orbit_state": {"eq": "ascending"},
        "sar:polarizations": {"in": ["VV", "VH"]},
        "sar:instrument_mode": {"eq": "IW"},
    }


@pytest.mark.unit
def test_sentinel1_adds_relative_orbit() -> None:
    assert Sentinel1Params(orbit_state="ascending", orbit_number=42).to_query() == {
        "sat:orbit_state": {"eq": "ascending"},
        "sat:relative_orbit": {"eq": 42},
    }


@pytest.mark.unit
def test_dem_type() -> None:
    assert DEMParams(dem_type="cop-dem-glo-30").to_query() == {
        "dem:type": {"eq": "cop-dem-glo-30"}
    }
