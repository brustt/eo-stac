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


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------


class _DroppedStream(httpx.AsyncByteStream):
    """Yields *data*, then fails as a dropped connection would."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    async def __aiter__(self):
        yield self._data
        raise httpx.ReadError("connection dropped")


def _dropped(data: bytes, etag: str | None = '"v1"') -> httpx.Response:
    headers = {"ETag": etag} if etag else {}
    return httpx.Response(200, headers=headers, stream=_DroppedStream(data))


_RESUMED_WORLD = httpx.Response(
    206,
    headers={"ETag": '"v1"', "Content-Range": "bytes 6-10/11"},
    content=b"world",
)


def _part_files(tmp_path: Path) -> list[Path]:
    return [p for p in tmp_path.rglob("*.part") if p.is_file()]


@pytest.mark.unit
@respx.mock
@patch("asyncio.sleep", new_callable=AsyncMock)
def test_retry_resumes_from_bytes_already_received(
    mock_sleep, sample_gdf, tmp_path: Path
) -> None:
    route = respx.get(_B04).mock(
        side_effect=[
            _dropped(b"hello "),
            _RESUMED_WORLD,
        ]
    )

    result = _download_b04(sample_gdf, tmp_path)

    resumed = route.calls[1].request
    assert resumed.headers["Range"] == "bytes=6-"
    assert resumed.headers["If-Range"] == '"v1"'
    assert _b04_path(result).read_bytes() == b"hello world"
    assert _part_files(tmp_path) == []


@pytest.mark.unit
@respx.mock
@patch("asyncio.sleep", new_callable=AsyncMock)
def test_later_call_resumes_a_failed_transfer(
    mock_sleep, sample_gdf, tmp_path: Path
) -> None:
    route = respx.get(_B04).mock(
        side_effect=[
            _dropped(b"hello "),
            httpx.Response(503),
            httpx.Response(503),
            _RESUMED_WORLD,
        ]
    )

    with pytest.warns(UserWarning, match="failed to download"):
        failed = _download_b04(sample_gdf, tmp_path)
    assert _b04_path(failed) is None
    assert len(_part_files(tmp_path)) == 1

    result = _download_b04(sample_gdf, tmp_path)

    assert route.calls[3].request.headers["Range"] == "bytes=6-"
    assert _b04_path(result).read_bytes() == b"hello world"


@pytest.mark.unit
@respx.mock
@patch("asyncio.sleep", new_callable=AsyncMock)
def test_changed_remote_file_restarts_from_zero(
    mock_sleep, sample_gdf, tmp_path: Path
) -> None:
    # The server ignores If-Range for a stale ETag and sends the new file whole.
    respx.get(_B04).mock(
        side_effect=[
            _dropped(b"old old "),
            httpx.Response(200, headers={"ETag": '"v2"'}, content=b"new file"),
        ]
    )

    result = _download_b04(sample_gdf, tmp_path)

    assert _b04_path(result).read_bytes() == b"new file"


@pytest.mark.unit
@respx.mock
@patch("asyncio.sleep", new_callable=AsyncMock)
def test_no_etag_restarts_from_zero(mock_sleep, sample_gdf, tmp_path: Path) -> None:
    route = respx.get(_B04).mock(
        side_effect=[
            _dropped(b"hello ", etag=None),
            httpx.Response(200, content=b"hello world"),
        ]
    )

    result = _download_b04(sample_gdf, tmp_path)

    assert "Range" not in route.calls[1].request.headers
    assert _b04_path(result).read_bytes() == b"hello world"


@pytest.mark.unit
@respx.mock
@patch("asyncio.sleep", new_callable=AsyncMock)
def test_server_without_range_support_restarts_from_zero(
    mock_sleep, sample_gdf, tmp_path: Path
) -> None:
    # A Range request answered with 200 and the full body.
    respx.get(_B04).mock(
        side_effect=[
            _dropped(b"hello "),
            httpx.Response(200, headers={"ETag": '"v1"'}, content=b"hello world"),
        ]
    )

    result = _download_b04(sample_gdf, tmp_path)

    assert _b04_path(result).read_bytes() == b"hello world"


@pytest.mark.unit
@respx.mock
@patch("asyncio.sleep", new_callable=AsyncMock)
def test_overwrite_discards_partial_file(
    mock_sleep, sample_gdf, tmp_path: Path
) -> None:
    route = respx.get(_B04).mock(
        side_effect=[
            _dropped(b"hello "),
            httpx.Response(503),
            httpx.Response(503),
            httpx.Response(200, headers={"ETag": '"v1"'}, content=b"fresh"),
        ]
    )
    with pytest.warns(UserWarning):
        _download_b04(sample_gdf, tmp_path)

    result = _download_b04(sample_gdf, tmp_path, overwrite=True)

    assert "Range" not in route.calls[3].request.headers
    assert _b04_path(result).read_bytes() == b"fresh"


@pytest.mark.unit
@respx.mock
@patch("asyncio.sleep", new_callable=AsyncMock)
def test_fully_received_partial_file_is_recovered(
    mock_sleep, sample_gdf, tmp_path: Path
) -> None:
    # Every byte arrived but the transfer was not finalised: the server answers
    # the Range request with 416, and the download starts over cleanly.
    respx.get(_B04).mock(
        side_effect=[
            _dropped(b"complete"),
            httpx.Response(416),
            httpx.Response(200, headers={"ETag": '"v1"'}, content=b"complete"),
        ]
    )

    result = _download_b04(sample_gdf, tmp_path)

    assert _b04_path(result).read_bytes() == b"complete"


@pytest.mark.unit
@respx.mock
@patch("asyncio.sleep", new_callable=AsyncMock)
def test_server_ignoring_if_range_does_not_splice_a_changed_file(
    mock_sleep, sample_gdf, tmp_path: Path
) -> None:
    # Some servers (Azure Blob) ignore If-Range and answer 206 for the new file.
    respx.get(_B04).mock(
        side_effect=[
            _dropped(b"old old "),
            httpx.Response(
                206,
                headers={"ETag": '"v2"', "Content-Range": "bytes 8-10/11"},
                content=b"ile",
            ),
            httpx.Response(200, headers={"ETag": '"v2"'}, content=b"new file!!!"),
        ]
    )

    result = _download_b04(sample_gdf, tmp_path)

    assert _b04_path(result).read_bytes() == b"new file!!!"
