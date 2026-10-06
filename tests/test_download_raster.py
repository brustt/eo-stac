"""Rasters the library writes itself (process=True, Subsets), via download_items()."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import httpx
import numpy as np
import pystac
import pytest
import rasterio
import respx
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


# ---------------------------------------------------------------------------
# Subsets
# ---------------------------------------------------------------------------

# An area well inside the raster, deliberately not aligned to its 10 m pixels.
_INNER_UTM = (500203.0, 5399507.0, 500398.0, 5399796.0)


def lonlat(utm_bounds: tuple[float, float, float, float]) -> list[float]:
    from rasterio.warp import transform_bounds

    return list(transform_bounds("EPSG:32631", "EPSG:4326", *utm_bounds))


def _source_window(source: Path, out: rasterio.DatasetReader) -> np.ndarray:
    """Read from *source* the pixels lying under *out*'s extent."""
    from rasterio.windows import from_bounds

    with rasterio.open(source) as src:
        window = from_bounds(*out.bounds, transform=src.transform)
        return src.read(window=window.round_offsets().round_lengths())


@pytest.mark.unit
@pytest.mark.parametrize(("dtype", "nodata"), [("uint16", 0), ("float32", -9999.0)])
def test_bbox_writes_unresampled_subset(dtype, nodata, tmp_path: Path) -> None:
    source = tmp_path / "source.tif"
    write_raster(source, dtype=dtype, nodata=nodata)
    gdf = gdf_with_assets({"vv": (str(source), _GEOTIFF)})

    result = _download(gdf, tmp_path, assets=["vv"], bbox=lonlat(_INNER_UTM))

    with rasterio.open(result.iloc[0]["local_paths"]["vv"]) as out:
        left, bottom, right, top = out.bounds
        # Covers the requested area, with at most a couple of pixels to spare.
        assert left <= _INNER_UTM[0] and bottom <= _INNER_UTM[1]
        assert right >= _INNER_UTM[2] and top >= _INNER_UTM[3]
        assert _INNER_UTM[0] - left < 2 * _RES and right - _INNER_UTM[2] < 2 * _RES
        assert _INNER_UTM[1] - bottom < 2 * _RES and top - _INNER_UTM[3] < 2 * _RES
        # Sits on the source pixel grid, in the source CRS and resolution.
        assert (left - _ORIGIN[0]) % _RES == 0 and (_ORIGIN[1] - top) % _RES == 0
        assert out.res == (_RES, _RES)
        assert out.crs.to_epsg() == 32631
        assert out.dtypes == (dtype, dtype)
        assert out.nodata == nodata
        assert out.tags(ns="IMAGE_STRUCTURE")["LAYOUT"] == "COG"
        # Pixels are the source's own.
        np.testing.assert_array_equal(out.read(), _source_window(source, out))


@pytest.mark.unit
def test_subset_path_identifies_the_area(tmp_path: Path) -> None:
    source = tmp_path / "source.tif"
    write_raster(source)
    url = "https://example.com/source.tif"
    gdf = gdf_with_assets({"vv": (url, _GEOTIFF)})
    other_area = lonlat((500400.0, 5399400.0, 500600.0, 5399600.0))

    def to_local(href: str) -> str:
        return str(source)

    with respx.mock:
        respx.get(url).mock(
            return_value=httpx.Response(200, content=source.read_bytes())
        )
        whole = _download(gdf, tmp_path, assets=["vv"])
    subset = _download(
        gdf, tmp_path, assets=["vv"], bbox=lonlat(_INNER_UTM), sign=to_local
    )
    other = _download(gdf, tmp_path, assets=["vv"], bbox=other_area, sign=to_local)

    whole_path = whole.iloc[0]["local_paths"]["vv"]
    subset_path = subset.iloc[0]["local_paths"]["vv"]
    other_path = other.iloc[0]["local_paths"]["vv"]
    assert whole_path.name == "item-001_vv.tif"
    assert subset_path.parent == whole_path.parent
    assert subset_path.name.startswith("item-001_vv_sub-")
    assert len({whole_path, subset_path, other_path}) == 3
    assert all(p.exists() for p in (whole_path, subset_path, other_path))
    assert whole_path.read_bytes() == source.read_bytes()


@pytest.mark.unit
def test_subset_of_same_area_is_skipped_on_later_call(tmp_path: Path) -> None:
    source = tmp_path / "source.tif"
    write_raster(source)
    gdf = gdf_with_assets({"vv": (str(source), _GEOTIFF)})
    first = _download(gdf, tmp_path, assets=["vv"], bbox=lonlat(_INNER_UTM))
    assert "vv" in first.iloc[0]["local_paths"]
    source.unlink()  # a second read would fail

    second = _download(gdf, tmp_path, assets=["vv"], bbox=lonlat(_INNER_UTM))

    assert second.iloc[0]["local_paths"] == first.iloc[0]["local_paths"]


@pytest.mark.unit
def test_subset_signs_the_href_before_reading(tmp_path: Path) -> None:
    source = tmp_path / "source.tif"
    write_raster(source)
    gdf = gdf_with_assets({"vv": ("https://example.com/unsigned.tif", _GEOTIFF)})

    result = _download(
        gdf,
        tmp_path,
        assets=["vv"],
        bbox=lonlat(_INNER_UTM),
        sign=lambda href: str(source),
    )

    with rasterio.open(result.iloc[0]["local_paths"]["vv"]) as out:
        np.testing.assert_array_equal(out.read(), _source_window(source, out))


@pytest.mark.unit
def test_subset_resolves_band_alias(tmp_path: Path) -> None:
    source = tmp_path / "source.tif"
    write_raster(source)
    gdf = gdf_with_assets({"B04": (str(source), _GEOTIFF)})

    result = _download(gdf, tmp_path, assets=["red"], bbox=lonlat(_INNER_UTM))

    assert result.iloc[0]["local_paths"]["red"].name.startswith("item-001_red_sub-")


# ---------------------------------------------------------------------------
# Subset edge cases and mixed assets
# ---------------------------------------------------------------------------


def _subset_bounds(result, key: str = "vv") -> tuple:
    with rasterio.open(result.iloc[0]["local_paths"][key]) as out:
        return tuple(out.bounds)


@pytest.fixture
def raster_gdf(tmp_path: Path) -> gpd.GeoDataFrame:
    source = tmp_path / "source.tif"
    write_raster(source)
    return gdf_with_assets({"vv": (str(source), _GEOTIFF)})


@pytest.mark.unit
def test_polygon_is_reduced_to_its_bounding_box(raster_gdf, tmp_path: Path) -> None:
    from shapely.geometry import Polygon

    west, south, east, north = lonlat(_INNER_UTM)
    triangle = Polygon([(west, south), (east, south), (west, north)])

    from_polygon = _download(
        raster_gdf, tmp_path / "a", assets=["vv"], polygon=triangle
    )
    from_bbox = _download(
        raster_gdf, tmp_path / "b", assets=["vv"], bbox=list(triangle.bounds)
    )

    assert _subset_bounds(from_polygon) == _subset_bounds(from_bbox)
    with rasterio.open(from_polygon.iloc[0]["local_paths"]["vv"]) as out:
        # Nothing is masked: the corner outside the triangle keeps its pixels.
        assert (out.read() != out.nodata).all()


@pytest.mark.unit
def test_polygon_accepts_geojson_and_wins_over_bbox(raster_gdf, tmp_path: Path) -> None:
    geojson = mapping(box(*lonlat(_INNER_UTM)))
    elsewhere = lonlat((500400.0, 5399400.0, 500600.0, 5399600.0))

    result = _download(
        raster_gdf, tmp_path / "a", assets=["vv"], polygon=geojson, bbox=elsewhere
    )
    expected = _download(
        raster_gdf, tmp_path / "b", assets=["vv"], bbox=lonlat(_INNER_UTM)
    )

    assert _subset_bounds(result) == _subset_bounds(expected)


@pytest.mark.unit
def test_partial_overlap_gives_the_overlapping_part(raster_gdf, tmp_path: Path) -> None:
    # Extends 300 m beyond the raster's east and north edges.
    area = lonlat((500400.0, 5399800.0, 500940.0, 5400300.0))

    result = _download(raster_gdf, tmp_path, assets=["vv"], bbox=area)

    left, bottom, right, top = _subset_bounds(result)
    assert (right, top) == (_ORIGIN[0] + _SIZE * _RES, _ORIGIN[1])
    assert left <= 500400.0 and bottom <= 5399800.0


@pytest.mark.unit
def test_no_overlap_skips_the_asset_with_a_warning(raster_gdf, tmp_path: Path) -> None:
    far_away = lonlat((520000.0, 5399400.0, 520500.0, 5399800.0))

    with pytest.warns(UserWarning, match="does not overlap"):
        result = _download(raster_gdf, tmp_path, assets=["vv"], bbox=far_away)

    assert result.iloc[0]["local_paths"] == {}
    assert not list((tmp_path / "out").rglob("*.tif"))


@pytest.mark.unit
def test_ungridded_raster_has_no_subset(tmp_path: Path) -> None:
    from rasterio.control import GroundControlPoint

    ungridded = tmp_path / "grd.tif"
    gcps = [
        GroundControlPoint(row=0, col=0, x=3.0, y=48.75),
        GroundControlPoint(row=0, col=_SIZE, x=3.01, y=48.75),
        GroundControlPoint(row=_SIZE, col=0, x=3.0, y=48.74),
        GroundControlPoint(row=_SIZE, col=_SIZE, x=3.01, y=48.74),
    ]
    with rasterio.open(
        ungridded, "w", driver="GTiff", width=_SIZE, height=_SIZE, count=1,
        dtype="uint16", gcps=gcps, crs="EPSG:4326",
    ) as dst:
        dst.write(np.ones((1, _SIZE, _SIZE), dtype="uint16"))
    gridded = tmp_path / "source.tif"
    write_raster(gridded)
    gdf = gdf_with_assets(
        {"vv": (str(ungridded), _GEOTIFF), "vh": (str(gridded), _GEOTIFF)}
    )

    with pytest.warns(UserWarning, match="no pixel grid.*whole file"):
        result = _download(
            gdf, tmp_path, assets=["vv", "vh"], bbox=[3.002, 48.742, 3.008, 48.748]
        )

    paths = result.iloc[0]["local_paths"]
    assert "vv" not in paths
    assert "vh" in paths  # the gridded asset in the same call is still written


@pytest.mark.unit
@respx.mock
def test_non_raster_assets_are_transferred_whole(tmp_path: Path) -> None:
    source = tmp_path / "source.tif"
    write_raster(source)
    respx.get("https://example.com/thumb.png").mock(
        return_value=httpx.Response(200, content=b"png-bytes")
    )
    respx.get("https://example.com/manifest.xml").mock(
        return_value=httpx.Response(200, content=b"<xml/>")
    )
    gdf = gdf_with_assets(
        {
            "vv": (str(source), _GEOTIFF),
            "thumbnail": ("https://example.com/thumb.png", "image/png"),
            "manifest": ("https://example.com/manifest.xml", None),
        }
    )

    result = _download(gdf, tmp_path, bbox=lonlat(_INNER_UTM))  # assets=None

    paths = result.iloc[0]["local_paths"]
    assert paths["vv"].name.startswith("item-001_vv_sub-")
    assert paths["thumbnail"].read_bytes() == b"png-bytes"
    assert paths["thumbnail"].name == "item-001_thumbnail.png"
    assert paths["manifest"].read_bytes() == b"<xml/>"


@pytest.mark.unit
def test_area_is_recorded_in_metadata(raster_gdf, tmp_path: Path) -> None:
    area = lonlat(_INNER_UTM)

    _download(raster_gdf, tmp_path, assets=["vv"], bbox=area)

    (meta,) = (tmp_path / "out" / "metadata").glob("*.gpkg")
    saved = gpd.read_file(meta)
    recorded = [float(v) for v in saved.iloc[0]["subset_bbox"].split(",")]
    assert recorded == pytest.approx(area)


@pytest.mark.unit
def test_no_area_records_no_subset_bbox(tmp_path: Path) -> None:
    source = tmp_path / "source.tif"
    write_raster(source)
    gdf = gdf_with_assets({"vv": (str(source), _GEOTIFF)})

    _download(gdf, tmp_path, assets=["vv"], process=True)

    (meta,) = (tmp_path / "out" / "metadata").glob("*.gpkg")
    assert "subset_bbox" not in gpd.read_file(meta).columns


@pytest.mark.unit
def test_area_with_process_behaves_like_area_alone(raster_gdf, tmp_path: Path) -> None:
    area = lonlat(_INNER_UTM)

    alone = _download(raster_gdf, tmp_path / "a", assets=["vv"], bbox=area)
    both = _download(
        raster_gdf, tmp_path / "b", assets=["vv"], bbox=area, process=True
    )

    alone_path = alone.iloc[0]["local_paths"]["vv"]
    both_path = both.iloc[0]["local_paths"]["vv"]
    assert both_path.name == alone_path.name
    with rasterio.open(alone_path) as a, rasterio.open(both_path) as b:
        assert a.bounds == b.bounds
        np.testing.assert_array_equal(a.read(), b.read())
