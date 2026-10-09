"""STACSource — entry point for STAC APIs."""

from __future__ import annotations

import functools

import pystac_client

from eo_stac.exceptions import EostacError


class STACSource:
    """Wraps a pystac_client.Client connected to a STAC API."""

    def __init__(self, client: pystac_client.Client) -> None:
        self._client = client

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------

    @classmethod
    def from_url(cls, url: str, headers: dict | None = None) -> "STACSource":
        """Connect to a STAC API endpoint.

        Only HTTP/HTTPS URLs are supported.  Local file paths (or ``file://``
        URLs) raise :class:`~eo_stac.exceptions.EostacError`.
        """
        if url.startswith("file://") or (
            not url.startswith("http://") and not url.startswith("https://")
        ):
            raise EostacError(
                f"Only HTTP(S) STAC API endpoints are supported, got {url!r}."
            )

        try:
            client = pystac_client.Client.open(url, headers=headers or {})
            return cls(client)
        except Exception as exc:
            raise EostacError(f"Cannot open STAC API at {url!r}: {exc}") from exc

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @functools.cached_property
    def collections(self) -> list[str]:
        """Return a list of collection IDs available in this source."""
        return [c.id for c in self._client.get_collections()]
