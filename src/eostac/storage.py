"""PathManager — consistent directory/file naming for downloaded assets."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from eostac.exceptions import StorageError


class PathManager:
    """Resolve deterministic paths under a root download directory.

    Directory layout::

        root/
        ├── <collection>/
        │   └── <YYYY-MM-DD>/
        │       └── <item_id>/
        │           ├── <item_id>_<asset_key>.tif
        │           └── ...
        └── metadata/
            └── <name>.gpkg
    """

    def __init__(self, root_dir: str | Path) -> None:
        self.root = Path(root_dir)

    def item_dir(self, collection: str, item_id: str, date: datetime | None) -> Path:
        """Return the directory path for a single item's assets (does not create it)."""
        date_str = date.strftime("%Y-%m-%d") if date is not None else "unknown-date"
        return self.root / collection / date_str / item_id

    def asset_path(
        self,
        collection: str,
        item_id: str,
        date: datetime | None,
        asset_key: str,
        suffix: str = ".tif",
    ) -> Path:
        """Return the full path for a downloaded asset file."""
        directory = self.item_dir(collection, item_id, date)
        filename = f"{item_id}_{asset_key}{suffix}"
        return directory / filename

    def metadata_path(self, name: str) -> Path:
        """Return the path for a GeoPackage metadata file."""
        meta_dir = self.root / "metadata"
        try:
            meta_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StorageError(f"Cannot create metadata directory {meta_dir}: {exc}") from exc
        return meta_dir / f"{name}.gpkg"
