"""Rasters the library writes itself (process=True, Subsets), via download_items()."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import numpy as np
import pystac
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box, mapping

from eostac.download import download_items
from eostac.search import _items_to_gdf

# A 64×64, 10 m grid in UTM 31N whose top-left corner is (500000, 5400000):
# x spans 500000–500640, y spans 5399360–5400000.
_ORIGIN = (500000.0, 5400000.0)
_SIZE = 64
_RES = 10.0
_GEOTIFF = "image/tiff; application=geotiff"


def write_raster(
    path: Path,
    dtype: str = "uint16",
    count: int = 2,
    nodata: float | None = 0,
    driver: str = "GTiff",
) -> np.ndarray:
    """Write a small gridded raster with distinct pixel values; return them."""
    data = np.arange(1, count * _SIZE * _SIZE + 1).reshape(count, _SIZE, _SIZE)
    data = data.astype(dtype)
    with rasterio.open(
        path,
        "w",
        driver=driver,
        width=_SIZE,
        height=_SIZE,
        count=count,
        dtype=dtype,
        crs="EPSG:32631",
        transform=from_origin(*_ORIGIN, _RES, _RES),
        nodata=nodata,
    ) as dst:
        dst.write(data)
    return data


def gdf_with_assets(assets: dict[str, tuple[str, str | None]]) -> gpd.GeoDataFrame:
    """One-item GDF whose assets are ``{key: (href, media_type)}``."""
    footprint = [2.9, 48.7, 3.1, 48.8]
    item = pystac.Item(
        id="item-001",
        geometry=mapping(box(*footprint)),
        bbox=footprint,
        datetime=datetime(2024, 6, 15, tzinfo=timezone.utc),
        properties={},
    )
    item.collection_id = "test-collection"
    for key, (href, media_type) in assets.items():
        item.add_asset(key, pystac.Asset(href=href, media_type=media_type))
    return _items_to_gdf([item])


def _download(gdf, tmp_path: Path, **kwargs):
    return download_items(
        gdf, output_dir=tmp_path / "out", show_progress=False, **kwargs
    )


# ---------------------------------------------------------------------------
# process=True
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("dtype", "nodata"), [("uint16", 0), ("float32", -9999.0), ("uint8", None)]
)
def test_process_writes_faithful_cog(dtype, nodata, tmp_path: Path) -> None:
    source = tmp_path / "source.tif"
    data = write_raster(source, dtype=dtype, nodata=nodata)
    gdf = gdf_with_assets({"vv": (str(source), _GEOTIFF)})

    result = _download(gdf, tmp_path, assets=["vv"], process=True)

    with rasterio.open(result.iloc[0]["local_paths"]["vv"]) as out:
        assert out.tags(ns="IMAGE_STRUCTURE")["LAYOUT"] == "COG"
        assert out.dtypes == (dtype, dtype)
        assert out.nodata == nodata
        assert out.crs.to_epsg() == 32631
        assert out.transform == from_origin(*_ORIGIN, _RES, _RES)
        np.testing.assert_array_equal(out.read(), data)


@pytest.mark.unit
def test_process_normalises_jpeg2000_to_cog(tmp_path: Path) -> None:
    if "JP2OpenJPEG" not in rasterio.drivers.raster_driver_extensions().values():
        pytest.skip("GDAL built without JPEG2000 support")
    source = tmp_path / "source.jp2"
    write_raster(source, driver="JP2OpenJPEG")
    gdf = gdf_with_assets({"B04": (str(source), "image/jp2")})

    result = _download(gdf, tmp_path, assets=["B04"], process=True)

    path = result.iloc[0]["local_paths"]["B04"]
    assert path.suffix == ".tif"
    with rasterio.open(path) as out:
        assert out.driver == "GTiff"
        assert out.tags(ns="IMAGE_STRUCTURE")["LAYOUT"] == "COG"


@pytest.mark.unit
def test_process_signs_the_href_before_reading(tmp_path: Path) -> None:
    source = tmp_path / "source.tif"
    data = write_raster(source)
    gdf = gdf_with_assets({"vv": ("https://example.com/unsigned.tif", _GEOTIFF)})

    result = _download(
        gdf, tmp_path, assets=["vv"], process=True, sign=lambda href: str(source)
    )

    with rasterio.open(result.iloc[0]["local_paths"]["vv"]) as out:
        np.testing.assert_array_equal(out.read(), data)


@pytest.mark.unit
def test_process_skips_existing_output(tmp_path: Path) -> None:
    source = tmp_path / "source.tif"
    write_raster(source)
    gdf = gdf_with_assets({"vv": (str(source), _GEOTIFF)})
    first = _download(gdf, tmp_path, assets=["vv"], process=True)
    assert "vv" in first.iloc[0]["local_paths"]
    source.unlink()  # a second read would fail

    second = _download(gdf, tmp_path, assets=["vv"], process=True)

    assert second.iloc[0]["local_paths"] == first.iloc[0]["local_paths"]


@pytest.mark.unit
def test_process_unreadable_source_fails_without_leftovers(tmp_path: Path) -> None:
    gdf = gdf_with_assets({"vv": (str(tmp_path / "missing.tif"), _GEOTIFF)})

    with pytest.warns(UserWarning, match="not retried"):
        result = _download(gdf, tmp_path, assets=["vv"], process=True)

    assert result.iloc[0]["local_paths"] == {}
    leftovers = [p for p in (tmp_path / "out").rglob("*") if p.is_file()]
    assert all(p.suffix == ".gpkg" for p in leftovers)
