"""Retry policy and Resume, exercised through download_items()."""

from __future__ import annotations

import warnings
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from eostac.download import download_items

_B04 = "https://example.com/B04.tif"


def _download_b04(gdf, tmp_path: Path, **kwargs):
    return download_items(
        gdf.head(1), output_dir=tmp_path, assets=["B04"], show_progress=False, **kwargs
    )


def _b04_path(result) -> Path | None:
    return result.iloc[0]["local_paths"].get("B04")


# ---------------------------------------------------------------------------
# Retry policy
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("status", [403, 404])
@respx.mock
@patch("asyncio.sleep", new_callable=AsyncMock)
def test_permanent_http_error_is_not_retried(
    mock_sleep, status, sample_gdf, tmp_path: Path
) -> None:
    route = respx.get(_B04).mock(return_value=httpx.Response(status))

    with pytest.warns(UserWarning, match=rf"HTTP {status}.*not retried"):
        result = _download_b04(sample_gdf, tmp_path)

    assert route.call_count == 1
    mock_sleep.assert_not_called()
    assert _b04_path(result) is None


@pytest.mark.unit
@pytest.mark.parametrize("status", [408, 429, 500, 503])
@respx.mock
@patch("asyncio.sleep", new_callable=AsyncMock)
def test_transient_http_error_is_retried(
    mock_sleep, status, sample_gdf, tmp_path: Path
) -> None:
    route = respx.get(_B04).mock(
        side_effect=[httpx.Response(status), httpx.Response(200, content=b"data")]
    )

    result = _download_b04(sample_gdf, tmp_path)

    assert route.call_count == 2
    assert _b04_path(result).read_bytes() == b"data"


@pytest.mark.unit
@respx.mock
@patch("asyncio.sleep", new_callable=AsyncMock)
def test_connection_error_is_retried(mock_sleep, sample_gdf, tmp_path: Path) -> None:
    route = respx.get(_B04).mock(
        side_effect=[httpx.ConnectError("boom"), httpx.Response(200, content=b"data")]
    )

    result = _download_b04(sample_gdf, tmp_path)

    assert route.call_count == 2
    assert _b04_path(result).read_bytes() == b"data"


@pytest.mark.unit
@respx.mock
@patch("asyncio.sleep", new_callable=AsyncMock)
def test_retry_after_header_is_honoured(mock_sleep, sample_gdf, tmp_path: Path) -> None:
    respx.get(_B04).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(200, content=b"data"),
        ]
    )

    _download_b04(sample_gdf, tmp_path)

    (delay,) = mock_sleep.call_args.args
    assert delay >= 7


@pytest.mark.unit
@respx.mock
@patch("asyncio.sleep", new_callable=AsyncMock)
def test_gives_up_after_three_attempts(mock_sleep, sample_gdf, tmp_path: Path) -> None:
    route = respx.get(_B04).mock(return_value=httpx.Response(503))

    with pytest.warns(UserWarning, match=r"HTTP 503.*after 3 attempts"):
        result = _download_b04(sample_gdf, tmp_path)

    assert route.call_count == 3
    assert _b04_path(result) is None


@pytest.mark.unit
@respx.mock
@patch("asyncio.sleep", new_callable=AsyncMock)
def test_signing_is_reapplied_on_every_attempt(
    mock_sleep, sample_gdf, tmp_path: Path
) -> None:
    tokens = iter(["t1", "t2"])
    expired = respx.get(f"{_B04}?token=t1").mock(return_value=httpx.Response(503))
    fresh = respx.get(f"{_B04}?token=t2").mock(
        return_value=httpx.Response(200, content=b"data")
    )

    result = _download_b04(
        sample_gdf, tmp_path, sign=lambda href: f"{href}?token={next(tokens)}"
    )

    assert expired.call_count == 1
    assert fresh.call_count == 1
    assert _b04_path(result).read_bytes() == b"data"


@pytest.mark.unit
@respx.mock
@patch("asyncio.sleep", new_callable=AsyncMock)
def test_one_failed_asset_does_not_stop_the_others(
    mock_sleep, sample_gdf, tmp_path: Path
) -> None:
    respx.get(_B04).mock(return_value=httpx.Response(404))
    respx.get("https://example.com/B03.tif").mock(
        return_value=httpx.Response(200, content=b"b03")
    )

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = download_items(
            sample_gdf.head(1),
            output_dir=tmp_path,
            assets=["B04", "B03"],
            show_progress=False,
        )

    paths = result.iloc[0]["local_paths"]
    assert "B04" not in paths
    assert paths["B03"].read_bytes() == b"b03"
