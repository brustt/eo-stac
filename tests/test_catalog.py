"""Tests for eo_stac.catalog.STACSource."""

from __future__ import annotations

import pytest
import responses as resp_lib

from eo_stac.catalog import STACSource
from eo_stac.exceptions import EostacError
from tests.conftest import (
    FAKE_API_URL,
    FAKE_COLLECTIONS,
    FAKE_CONFORMANCE,
    FAKE_LANDING,
)

# ---------------------------------------------------------------------------
# from_url — STAC API
# ---------------------------------------------------------------------------


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


@pytest.mark.unit
@resp_lib.activate
def test_from_url_unreachable_raises() -> None:
    resp_lib.add(resp_lib.GET, "https://no-such-host.invalid/", status=500)
    with pytest.raises(EostacError):
        STACSource.from_url("https://no-such-host.invalid/")


# ---------------------------------------------------------------------------
# from_url — local paths rejected (API only)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_from_url_local_path_raises() -> None:
    with pytest.raises(EostacError, match="HTTP"):
        STACSource.from_url("/tmp/some/catalog.json")


@pytest.mark.unit
def test_from_url_file_scheme_raises() -> None:
    with pytest.raises(EostacError, match="HTTP"):
        STACSource.from_url("file:///tmp/some/catalog.json")
