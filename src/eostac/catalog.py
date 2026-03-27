"""STACSource — unified entry point for STAC API and static catalogs."""

from __future__ import annotations

from pathlib import Path

import pystac
import pystac_client

from eostac.exceptions import EostacError


class STACSource:
    """Wraps either a pystac_client.Client (STAC API) or a pystac.Catalog (static)."""

    def __init__(self, client: pystac_client.Client | pystac.Catalog) -> None:
        self._client = client

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------

    @classmethod
    def from_url(cls, url: str, headers: dict | None = None) -> "STACSource":
        """Connect to a STAC endpoint.

        For HTTP/HTTPS URLs, tries ``pystac_client.Client.open()`` first
        (STAC API) and falls back to ``pystac.Catalog.from_file()`` for
        static catalogs.  Local file paths always use the static path.
        """
        _is_local = url.startswith("file://") or (
            not url.startswith("http://") and not url.startswith("https://")
        )
        if _is_local:
            return cls.from_json(url)

        api_exc: Exception | None = None
        try:
            client = pystac_client.Client.open(url, headers=headers or {})
            return cls(client)
        except Exception as exc:
            api_exc = exc

        try:
            catalog = pystac.Catalog.from_file(url)
            return cls(catalog)
        except Exception as exc:
            raise EostacError(
                f"Cannot open STAC source at {url!r}: {exc}"
                f" (API attempt also failed: {api_exc})"
            ) from exc

    @classmethod
    def from_json(cls, path: str | Path) -> "STACSource":
        """Load a static STAC catalog from a local JSON file."""
        try:
            catalog = pystac.Catalog.from_file(str(path))
            return cls(catalog)
        except Exception as exc:
            raise EostacError(f"Cannot load catalog from {path!r}: {exc}") from exc

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_api(self) -> bool:
        """True when the underlying client is a STAC API (pystac_client.Client)."""
        return isinstance(self._client, pystac_client.Client)

    @property
    def collections(self) -> list[str]:
        """Return a list of collection IDs available in this source."""
        if self.is_api:
            return [c.id for c in self._client.get_collections()]
        # Static catalog: walk one level of children treated as collections
        return [child.id for child in self._client.get_children()]
