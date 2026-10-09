"""Tests for eo_stac.storage.PathManager."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from eo_stac.storage import PathManager

DATE = datetime(2020, 6, 15, tzinfo=timezone.utc)
COLLECTION = "sentinel-2-l2a"
ITEM_ID = "S2A_MSIL2A_20200615T101000"


@pytest.mark.unit
def test_item_dir_returns_correct_path(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    d = pm.item_dir(COLLECTION, ITEM_ID, DATE)
    assert d == tmp_path / COLLECTION / "2020-06-15" / ITEM_ID
    assert not d.exists()


@pytest.mark.unit
def test_item_dir_idempotent(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    d1 = pm.item_dir(COLLECTION, ITEM_ID, DATE)
    d2 = pm.item_dir(COLLECTION, ITEM_ID, DATE)
    assert d1 == d2


@pytest.mark.unit
def test_asset_path_correct_name(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    p = pm.asset_path(COLLECTION, ITEM_ID, DATE, "B04")
    assert p.name == f"{ITEM_ID}_B04.tif"
    assert p.parent == tmp_path / COLLECTION / "2020-06-15" / ITEM_ID


@pytest.mark.unit
def test_asset_path_custom_suffix(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    p = pm.asset_path(COLLECTION, ITEM_ID, DATE, "B04", suffix=".jp2")
    assert p.suffix == ".jp2"


@pytest.mark.unit
def test_metadata_path(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    p = pm.metadata_path("results_2020")
    assert p == tmp_path / "metadata" / "results_2020.gpkg"
    assert p.parent.is_dir()


@pytest.mark.unit
def test_metadata_path_creates_dir(tmp_path: Path) -> None:
    pm = PathManager(tmp_path / "new_root")
    p = pm.metadata_path("test")
    assert p.parent.is_dir()


@pytest.mark.unit
def test_different_dates_different_dirs(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    d1 = pm.item_dir(COLLECTION, ITEM_ID, datetime(2020, 6, 15, tzinfo=timezone.utc))
    d2 = pm.item_dir(COLLECTION, ITEM_ID, datetime(2020, 7, 20, tzinfo=timezone.utc))
    assert d1 != d2
    assert "2020-06-15" in str(d1)
    assert "2020-07-20" in str(d2)


@pytest.mark.unit
def test_subset_path_is_deterministic_per_area(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    date = datetime(2024, 6, 15, tzinfo=timezone.utc)
    area = (2.2, 48.8, 2.5, 48.9)

    path = pm.subset_path("col", "item-1", date, "vv", area)

    assert path.parent == pm.item_dir("col", "item-1", date)
    assert re.fullmatch(r"item-1_vv_sub-[0-9a-f]{8}\.tif", path.name)
    assert pm.subset_path("col", "item-1", date, "vv", [2.2, 48.8, 2.5, 48.9]) == path
    assert pm.subset_path("col", "item-1", date, "vv", (2.2, 48.8, 2.5, 48.91)) != path
