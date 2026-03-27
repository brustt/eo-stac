"""Smoke tests for eostac.viz visualization utilities."""

from __future__ import annotations

from pathlib import Path

import folium
import geopandas as gpd
import numpy as np
import pytest

from eostac.viz import plot_footprints, plot_thumbnails, quick_view


@pytest.fixture
def tiny_geotiff(tmp_path: Path) -> Path:
    """Write a minimal 8×8 single-band GeoTIFF in EPSG:4326."""
    import rasterio
    from rasterio.transform import from_bounds

    path = tmp_path / "test.tif"
    transform = from_bounds(0.0, 0.0, 1.0, 1.0, 8, 8)
    with rasterio.open(
        str(path), "w",
        driver="GTiff",
        width=8, height=8,
        count=1,
        dtype=np.uint8,
        crs="EPSG:4326",
        transform=transform,
    ) as dst:
        dst.write(np.ones((1, 8, 8), dtype=np.uint8) * 128)
    return path


@pytest.mark.unit
def test_plot_footprints_returns_map(sample_gdf) -> None:
    m = plot_footprints(sample_gdf)
    assert isinstance(m, folium.Map)


@pytest.mark.unit
def test_plot_footprints_empty_gdf() -> None:
    empty = gpd.GeoDataFrame(
        columns=["geometry", "cloud_cover"], geometry="geometry", crs="EPSG:4326"
    )
    m = plot_footprints(empty)
    assert isinstance(m, folium.Map)


@pytest.mark.unit
def test_plot_footprints_no_column(sample_gdf) -> None:
    m = plot_footprints(sample_gdf, column=None)
    assert isinstance(m, folium.Map)


@pytest.mark.unit
def test_plot_footprints_custom_tiles(sample_gdf) -> None:
    m = plot_footprints(sample_gdf, tiles="CartoDB positron")
    assert isinstance(m, folium.Map)


@pytest.mark.unit
def test_plot_thumbnails_returns_map(sample_gdf) -> None:
    m = plot_thumbnails(sample_gdf)
    assert isinstance(m, folium.Map)


@pytest.mark.unit
def test_plot_thumbnails_max_items(sample_gdf) -> None:
    m = plot_thumbnails(sample_gdf, max_items=1)
    assert isinstance(m, folium.Map)


@pytest.mark.unit
def test_plot_thumbnails_empty_gdf() -> None:
    empty = gpd.GeoDataFrame(
        columns=["geometry", "assets"], geometry="geometry", crs="EPSG:4326"
    )
    m = plot_thumbnails(empty)
    assert isinstance(m, folium.Map)


@pytest.mark.unit
def test_quick_view_returns_map(tiny_geotiff: Path) -> None:
    m = quick_view(tiny_geotiff, overview_level=None)
    assert isinstance(m, folium.Map)


@pytest.mark.unit
def test_quick_view_accepts_string_path(tiny_geotiff: Path) -> None:
    m = quick_view(str(tiny_geotiff), overview_level=None)
    assert isinstance(m, folium.Map)
