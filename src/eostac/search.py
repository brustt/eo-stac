"""search_items() — query a STACSource and return a GeoDataFrame."""

from __future__ import annotations

import warnings
from datetime import datetime as dt, timezone as _tz
from typing import TYPE_CHECKING

import geopandas as gpd
import pystac
import pystac_client
from shapely.geometry import mapping, shape

from eostac.exceptions import SearchError

if TYPE_CHECKING:
    from shapely.geometry.base import BaseGeometry

    from eostac.catalog import STACSource


def search_items(
    source: "STACSource",
    collections: list[str] | None = None,
    bbox: list[float] | None = None,
    polygon: dict | "BaseGeometry" | None = None,
    datetime: str | tuple | None = None,
    max_cloud_cover: float | None = None,
    product_type: str | None = None,
    limit: int | None = None,
    extra_query: list[str] | None = None,
) -> gpd.GeoDataFrame:
    """Search a STAC catalog and return matching items as a GeoDataFrame.

    Parameters
    ----------
    source:
        A :class:`~eostac.catalog.STACSource` instance.
    collections:
        Collection IDs to search within.
    bbox:
        Bounding box as ``[west, south, east, north]``.
    polygon:
        GeoJSON geometry dict **or** a Shapely geometry.  When both *bbox* and
        *polygon* are supplied, *polygon* takes precedence for the spatial filter
        on API searches.
    datetime:
        ISO-8601 string range ``"YYYY-MM-DD/YYYY-MM-DD"`` **or** a ``(start, end)``
        tuple of strings/datetime objects.
    max_cloud_cover:
        Upper bound on cloud cover (0–100).
    product_type:
        Value matched against ``properties.product_type``.
    limit:
        Maximum number of items to return.
    extra_query:
        Additional CQL2 / filter strings passed through to the API unchanged.

    Returns
    -------
    geopandas.GeoDataFrame
        Columns: ``geometry``, ``item_id``, ``collection``, ``datetime``,
        ``cloud_cover`` (normalised 0–100 if present), ``assets`` (dict), plus
        all other flattened STAC properties.
    """
    try:
        if source.is_api:
            items = _search_api(
                source._client,
                collections=collections,
                bbox=bbox,
                polygon=polygon,
                datetime=datetime,
                max_cloud_cover=max_cloud_cover,
                product_type=product_type,
                limit=limit,
                extra_query=extra_query,
            )
        else:
            items = _search_static(
                source._client,
                collections=collections,
                bbox=bbox,
                polygon=polygon,
                datetime=datetime,
                max_cloud_cover=max_cloud_cover,
                product_type=product_type,
                limit=limit,
            )
    except Exception as exc:
        raise SearchError(str(exc)) from exc

    return _items_to_gdf(items)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _normalize_datetime(datetime: str | tuple | None) -> str | None:
    """Return a pystac_client-compatible datetime string."""
    if datetime is None:
        return None
    if isinstance(datetime, str):
        return datetime
    # tuple of (start, end) — each may be a string or datetime object
    start, end = datetime
    start_str = start.isoformat() if isinstance(start, dt) else str(start)
    end_str = end.isoformat() if isinstance(end, dt) else str(end)
    return f"{start_str}/{end_str}"


def _geom_to_dict(polygon: dict | "BaseGeometry") -> dict:
    """Convert a Shapely geometry or GeoJSON dict to a GeoJSON dict."""
    if isinstance(polygon, dict):
        return polygon
    return mapping(polygon)


def _search_api(
    client: pystac_client.Client,
    collections: list[str] | None,
    bbox: list[float] | None,
    polygon: dict | "BaseGeometry" | None,
    datetime: str | tuple | None,
    max_cloud_cover: float | None,
    product_type: str | None,
    limit: int | None,
    extra_query: list[str] | None,
) -> list[pystac.Item]:
    kwargs: dict[str, object] = {}

    if collections:
        kwargs["collections"] = collections
    if bbox and polygon is None:
        kwargs["bbox"] = bbox
    if polygon is not None:
        kwargs["intersects"] = _geom_to_dict(polygon)
    if datetime is not None:
        kwargs["datetime"] = _normalize_datetime(datetime)
    if limit is not None:
        kwargs["max_items"] = limit

    # Build query filters
    query: list[str] = list(extra_query or [])
    if max_cloud_cover is not None:
        query.append(f"eo:cloud_cover<={max_cloud_cover}")
    if product_type is not None:
        query.append(f"product_type={product_type}")

    if query:
        # Try filter parameter (CQL2 Text) — fall back to legacy query if unsupported
        try:
            search = client.search(
                **kwargs,
                filter=" AND ".join(query),
                filter_lang="cql2-text",
            )
            return list(search.items())
        except pystac_client.exceptions.APIError as exc:
            warnings.warn(
                f"Server does not support CQL2 filters ({exc}); applying filters client-side.",
                stacklevel=2,
            )
            search = client.search(**kwargs)
            items = list(search.items())
            return _filter_items(items, max_cloud_cover, product_type)
    else:
        search = client.search(**kwargs)
        return list(search.items())


def _search_static(
    catalog: pystac.Catalog,
    collections: list[str] | None,
    bbox: list[float] | None,
    polygon: dict | "BaseGeometry" | None,
    datetime: str | tuple | None,
    max_cloud_cover: float | None,
    product_type: str | None,
    limit: int | None,
) -> list[pystac.Item]:
    """Walk a static catalog and filter items client-side."""
    items: list[pystac.Item] = []

    # Determine spatial filter shape
    filter_geom = None
    if polygon is not None:
        geom_dict = _geom_to_dict(polygon)
        filter_geom = shape(geom_dict)
    elif bbox is not None:
        from shapely.geometry import box

        filter_geom = box(*bbox)

    # Parse datetime range
    dt_start: dt | None = None
    dt_end: dt | None = None
    if datetime is not None:
        dt_str = _normalize_datetime(datetime)
        if dt_str and "/" in dt_str:
            s, e = dt_str.split("/", 1)
            dt_start = _parse_dt(s)
            dt_end = _parse_dt(e)

    for item in catalog.get_items(recursive=True):
        # Collection filter
        if collections and item.collection_id not in collections:
            continue

        # Spatial filter
        if filter_geom is not None and item.geometry:
            item_shape = shape(item.geometry)
            if not item_shape.intersects(filter_geom):
                continue

        # Temporal filter
        if dt_start or dt_end:
            item_dt = item.datetime
            if item_dt is None:
                continue
            if dt_start and item_dt < dt_start:
                continue
            if dt_end and item_dt > dt_end:
                continue

        if not _filter_items([item], max_cloud_cover, product_type):
            continue

        items.append(item)

        if limit is not None and len(items) >= limit:
            break

    return items


def _filter_items(
    items: list[pystac.Item],
    max_cloud_cover: float | None,
    product_type: str | None,
) -> list[pystac.Item]:
    result = []
    for item in items:
        props = item.properties or {}

        if max_cloud_cover is not None:
            cc = _get_cloud_cover(props)
            if cc is not None and cc > max_cloud_cover:
                continue

        if product_type is not None:
            if props.get("product_type") != product_type:
                continue

        result.append(item)
    return result


def _get_cloud_cover(props: dict) -> float | None:
    """Return normalised cloud cover (0–100) or None."""
    for key in ("eo:cloud_cover", "cloud_cover", "cloudCover"):
        val = props.get(key)
        if val is not None:
            val = float(val)
            # Normalise from 0-1 to 0-100 if needed
            if 0 <= val < 1.0:
                val = val * 100
            return val
    return None


def _parse_dt(s: str) -> dt:
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            parsed = dt.strptime(s, fmt)
            return parsed.replace(tzinfo=_tz.utc)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse datetime: {s!r}")


def _items_to_gdf(items: list[pystac.Item]) -> gpd.GeoDataFrame:
    """Convert a list of pystac Items to a GeoDataFrame."""
    if not items:
        return gpd.GeoDataFrame(
            columns=["geometry", "item_id", "collection", "datetime", "cloud_cover", "assets"],
            geometry="geometry",
            crs="EPSG:4326",
        )

    rows = []
    for item in items:
        props = item.properties or {}

        geom = shape(item.geometry) if item.geometry else None
        cloud_cover = _get_cloud_cover(props)

        assets = {key: asset.href for key, asset in item.assets.items()}

        row: dict[str, object] = {
            "geometry": geom,
            "item_id": item.id,
            "collection": item.collection_id,
            "datetime": item.datetime,
            "cloud_cover": cloud_cover,
            "assets": assets,
        }

        # Flatten remaining properties, stripping common prefixes
        for k, v in props.items():
            if k in ("eo:cloud_cover", "cloud_cover", "cloudCover", "datetime"):
                continue
            clean_key = k.split(":")[-1] if ":" in k else k
            row.setdefault(clean_key, v)

        rows.append(row)

    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
    return gdf
