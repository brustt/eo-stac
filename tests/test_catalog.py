"""Tests for eostac.catalog.STACSource."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import responses as resp_lib

from eostac.catalog import STACSource
from eostac.exceptions import EostacError
from tests.conftest import (
    FAKE_API_URL,
    FAKE_COLLECTIONS,
    FAKE_CONFORMANCE,
    FAKE_LANDING,
)


# ---------------------------------------------------------------------------
# from_json
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_from_json_loads_static_catalog(static_catalog_dir: Path) -> None:
    catalog_json = static_catalog_dir / "catalog.json"
    source = STACSource.from_json(catalog_json)
    assert not source.is_api


@pytest.mark.unit
def test_from_json_bad_path_raises() -> None:
    with pytest.raises(EostacError, match="Cannot load catalog"):
        STACSource.from_json("/nonexistent/path/catalog.json")


# ---------------------------------------------------------------------------
# from_url — STAC API
# ---------------------------------------------------------------------------


@pytest.mark.unit
@resp_lib.activate
def test_from_url_api_is_api_true() -> None:
    resp_lib.add(resp_lib.GET, FAKE_API_URL, json=FAKE_LANDING, status=200)
    resp_lib.add(
        resp_lib.GET, f"{FAKE_API_URL}/conformance", json=FAKE_CONFORMANCE, status=200
    )
    resp_lib.add(
        resp_lib.GET, f"{FAKE_API_URL}/collections", json=FAKE_COLLECTIONS, status=200
    )

    source = STACSource.from_url(FAKE_API_URL)
    assert source.is_api


@pytest.mark.unit
@resp_lib.activate
def test_from_url_api_collections() -> None:
    resp_lib.add(resp_lib.GET, FAKE_API_URL, json=FAKE_LANDING, status=200)
    resp_lib.add(
        resp_lib.GET, f"{FAKE_API_URL}/conformance", json=FAKE_CONFORMANCE, status=200
    )
    resp_lib.add(
        resp_lib.GET, f"{FAKE_API_URL}/collections", json=FAKE_COLLECTIONS, status=200
    )

    source = STACSource.from_url(FAKE_API_URL)
    assert "sentinel-2-l2a" in source.collections


# ---------------------------------------------------------------------------
# from_url — static fallback
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_from_url_static_fallback(static_catalog_dir: Path) -> None:
    catalog_json = static_catalog_dir / "catalog.json"
    source = STACSource.from_url(str(catalog_json))
    assert not source.is_api


@pytest.mark.unit
@resp_lib.activate
def test_from_url_unreachable_raises() -> None:
    resp_lib.add(resp_lib.GET, "https://no-such-host.invalid/", status=500)
    with pytest.raises(EostacError):
        STACSource.from_url("https://no-such-host.invalid/")


# ---------------------------------------------------------------------------
# is_api property
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_is_api_false_for_static_catalog(static_catalog_dir: Path) -> None:
    source = STACSource.from_json(static_catalog_dir / "catalog.json")
    assert source.is_api is False
