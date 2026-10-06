"""Tests for eostac.download.download_items()."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from eostac.download import _download_raw, _href_suffix, _select_assets, download_items
from eostac.exceptions import DownloadError
from eostac.storage import PathManager


_ASSET_URLS = [
    "https://example.com/B04.tif",
    "https://example.com/B03.tif",
    "https://example.com/thumb.jpg",
]

# ---------------------------------------------------------------------------
# _href_suffix
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_href_suffix_tif() -> None:
    assert _href_suffix("https://example.com/data/band.tif") == ".tif"


@pytest.mark.unit
def test_href_suffix_jp2() -> None:
    assert _href_suffix("https://example.com/data/band.jp2") == ".jp2"


@pytest.mark.unit
def test_href_suffix_fallback() -> None:
    assert _href_suffix("https://example.com/data/band") == ".tif"


# ---------------------------------------------------------------------------
# _select_assets
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_select_assets_all_when_none() -> None:
    assets = {"B04": "https://ex.com/B04.tif", "B03": "https://ex.com/B03.tif"}
    assert _select_assets(assets, None, None, "item-1") == assets


@pytest.mark.unit
def test_select_assets_none_skips_tiles_role() -> None:
    assets = {"vv": "url_vv", "tilejson": "url_tiles", "thumbnail": "url_thumb"}
    roles = {"vv": ["data"], "tilejson": ["tiles"], "thumbnail": ["thumbnail"]}
    assert _select_assets(assets, None, None, "item-1", roles) == {
        "vv": "url_vv",
        "thumbnail": "url_thumb",
    }


@pytest.mark.unit
def test_select_assets_explicit_key_ignores_roles() -> None:
    assets = {"tilejson": "url_tiles"}
    roles = {"tilejson": ["tiles"]}
    assert _select_assets(assets, ["tilejson"], None, "item-1", roles) == {
        "tilejson": "url_tiles"
    }


@pytest.mark.unit
def test_select_assets_filter_by_keys() -> None:
    assets = {"B04": "url1", "B03": "url2", "B02": "url3"}
    assert _select_assets(assets, ["B04", "B02"], None, "item-1") == {"B04": "url1", "B02": "url3"}


@pytest.mark.unit
def test_select_assets_empty_item_assets() -> None:
    assert _select_assets({}, None, None, "item-1") == {}


@pytest.mark.unit
def test_select_assets_alias_resolution() -> None:
    assets = {"B04": "url_red", "B08": "url_nir"}
    result = _select_assets(assets, ["red", "nir"], None, "item-1")
    assert result == {"red": "url_red", "nir": "url_nir"}


@pytest.mark.unit
def test_select_assets_alias_not_found_raises() -> None:
    from eostac.exceptions import DownloadError
    assets = {"VV": "url_vv"}
    with pytest.raises(DownloadError, match="nir"):
        _select_assets(assets, ["nir"], None, "item-1")


@pytest.mark.unit
def test_select_assets_extra_aliases() -> None:
    assets = {"my_red_band": "url_red"}
    extra = {"red": ["my_red_band"]}
    result = _select_assets(assets, ["red"], extra, "item-1")
    assert result == {"red": "url_red"}


# ---------------------------------------------------------------------------
# download_items — raw download
# ---------------------------------------------------------------------------


@pytest.mark.unit
@respx.mock
def test_download_items_creates_files(sample_gdf, tmp_path: Path) -> None:
    for url in _ASSET_URLS:
        respx.get(url).mock(return_value=httpx.Response(200, content=b"\x00" * 100))

    result = download_items(
        sample_gdf,
        output_dir=tmp_path,
        show_progress=False,
    )

    assert "local_paths" in result.columns
    non_empty = result["local_paths"].apply(lambda d: len(d) > 0)
    assert non_empty.any()


@pytest.mark.unit
@respx.mock
def test_download_items_resume_skips_existing(sample_gdf, tmp_path: Path) -> None:
    first_row = sample_gdf.iloc[0]
    pm = PathManager(tmp_path)
    existing = pm.asset_path(
        first_row.get("collection") or "unknown",
        first_row.get("item_id", "item-001"),
        first_row.get("datetime"),
        "B04",
    )
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_bytes(b"existing")

    # B04 exists → skipped (no HTTP request); B03 needs downloading
    respx.get("https://example.com/B03.tif").mock(
        return_value=httpx.Response(200, content=b"\x00" * 100)
    )

    result = download_items(
        sample_gdf.head(1),
        output_dir=tmp_path,
        assets=["B04", "B03"],
        overwrite=False,
        show_progress=False,
    )

    assert result.iloc[0]["local_paths"].get("B04") == existing


@pytest.mark.unit
@respx.mock
def test_download_items_overwrite(sample_gdf, tmp_path: Path) -> None:
    for url in _ASSET_URLS:
        respx.get(url).mock(return_value=httpx.Response(200, content=b"new_content"))

    result = download_items(
        sample_gdf.head(1),
        output_dir=tmp_path,
        overwrite=True,
        show_progress=False,
    )
    assert "local_paths" in result.columns


@pytest.mark.unit
@respx.mock
def test_download_items_asset_filter(sample_gdf, tmp_path: Path) -> None:
    respx.get("https://example.com/B04.tif").mock(
        return_value=httpx.Response(200, content=b"\x00" * 10)
    )

    result = download_items(
        sample_gdf.head(1),
        output_dir=tmp_path,
        assets=["B04"],
        show_progress=False,
    )
    paths = result.iloc[0]["local_paths"]
    assert "B04" in paths
    assert "B03" not in paths


@pytest.mark.unit
@respx.mock
def test_download_items_signs_href_before_fetch(sample_gdf, tmp_path: Path) -> None:
    signed = respx.get("https://example.com/B04.tif?token=abc").mock(
        return_value=httpx.Response(200, content=b"\x00" * 10)
    )

    result = download_items(
        sample_gdf.head(1),
        output_dir=tmp_path,
        assets=["B04"],
        show_progress=False,
        sign=lambda href: f"{href}?token=abc",
    )

    assert signed.called
    assert result.iloc[0]["local_paths"]["B04"].suffix == ".tif"
    # The GDF keeps the unsigned href.
    assert result.iloc[0]["assets"]["B04"] == "https://example.com/B04.tif"


@pytest.mark.unit
def test_download_items_sign_failure_warns(sample_gdf, tmp_path: Path) -> None:
    def broken_sign(href: str) -> str:
        raise RuntimeError("no token")

    with pytest.warns(UserWarning, match="Failed to sign"):
        result = download_items(
            sample_gdf.head(1),
            output_dir=tmp_path,
            assets=["B04"],
            show_progress=False,
            sign=broken_sign,
        )
    assert result.iloc[0]["local_paths"] == {}


@pytest.mark.unit
@respx.mock
def test_download_items_saves_metadata(sample_gdf, tmp_path: Path) -> None:
    for url in _ASSET_URLS:
        respx.get(url).mock(return_value=httpx.Response(200, content=b"\x00" * 10))

    download_items(sample_gdf.head(1), output_dir=tmp_path, show_progress=False)

    meta_files = list((tmp_path / "metadata").glob("*.gpkg"))
    assert len(meta_files) >= 1


@pytest.mark.unit
@respx.mock
def test_download_items_http_error_warns(sample_gdf, tmp_path: Path) -> None:
    for url in _ASSET_URLS:
        respx.get(url).mock(return_value=httpx.Response(404))

    with pytest.warns(UserWarning, match=r"\d+ asset\(s\) failed to download"):
        result = download_items(
            sample_gdf.head(1),
            output_dir=tmp_path,
            show_progress=False,
        )
    assert "local_paths" in result.columns


# ---------------------------------------------------------------------------
# _download_raw — async unit tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_download_raw_success(tmp_path: Path) -> None:
    dest = tmp_path / "file.tif"
    with respx.mock:
        respx.get("https://example.com/file.tif").mock(
            return_value=httpx.Response(200, content=b"data")
        )
        async with httpx.AsyncClient(follow_redirects=True) as client:
            result = await _download_raw(client, "https://example.com/file.tif", dest)
    assert result == dest
    assert dest.read_bytes() == b"data"


@pytest.mark.unit
async def test_download_raw_skips_existing_without_request(tmp_path: Path) -> None:
    dest = tmp_path / "existing.tif"
    dest.write_bytes(b"original")
    async with httpx.AsyncClient(follow_redirects=True) as client:
        result = await _download_raw(client, "https://example.com/file.tif", dest, overwrite=False)
    assert result == dest
    assert dest.read_bytes() == b"original"


@pytest.mark.unit
@patch("asyncio.sleep", new_callable=AsyncMock)
async def test_download_raw_retries_on_error_and_succeeds(mock_sleep, tmp_path: Path) -> None:
    dest = tmp_path / "file.tif"
    call_count = 0

    def side_effect(request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.ConnectError("timeout")
        return httpx.Response(200, content=b"data")

    with respx.mock:
        respx.get("https://example.com/file.tif").mock(side_effect=side_effect)
        async with httpx.AsyncClient(follow_redirects=True) as client:
            result = await _download_raw(client, "https://example.com/file.tif", dest)

    assert result == dest
    assert call_count == 2
    mock_sleep.assert_called_once_with(1)  # 2**0 = 1 before second attempt


@pytest.mark.unit
@patch("asyncio.sleep", new_callable=AsyncMock)
async def test_download_raw_raises_after_exhausted_retries(mock_sleep, tmp_path: Path) -> None:
    dest = tmp_path / "file.tif"
    with respx.mock:
        respx.get("https://example.com/file.tif").mock(
            side_effect=httpx.ConnectError("timeout")
        )
        async with httpx.AsyncClient(follow_redirects=True) as client:
            with pytest.raises(DownloadError, match="Failed to download"):
                await _download_raw(client, "https://example.com/file.tif", dest)
    # sleeps before attempt 2 (sleep(1)) and attempt 3 (sleep(2)); raises on attempt 3
    assert mock_sleep.call_count == 2
