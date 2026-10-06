"""search_items() — query a STAC API and return a GeoDataFrame."""

from __future__ import annotations

import warnings
from datetime import datetime as dt
from typing import TYPE_CHECKING, Any, Union

import geopandas as gpd
import pystac
import pystac_client
from shapely.geometry import mapping, shape

from eostac.exceptions import SearchError
from eostac.params import SearchParams

if TYPE_CHECKING:
    from shapely.geometry.base import BaseGeometry

    from eostac.catalog import STACSource


def search_items(
    source: "STACSource",
    collections: list[str] | None = None,
    bbox: list[float] | None = None,
    polygon: dict | "BaseGeometry" | None = None,
    datetime: str | tuple | None = None,
    limit: int | None = None,
    params: SearchParams | None = None,
    extra_query: dict[str, dict] | None = None,
) -> gpd.GeoDataFrame:
    """Search a STAC API and return matching items as a GeoDataFrame.

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
        *polygon* are supplied, *polygon* takes precedence.
    datetime:
        ISO-8601 string range ``"YYYY-MM-DD/YYYY-MM-DD"`` **or** a ``(start, end)``
        tuple of strings/datetime objects.
    limit:
        Maximum number of items to return.
    params:
        Sensor-specific filter params.  Pass a subclass of
        :class:`~eostac.params.SearchParams` — e.g.
        ``Sentinel2Params(max_cloud_cover=20)``,
        ``Sentinel1Params(orbit_state="ascending")``.  Its
        :meth:`~eostac.params.SearchParams.to_query` output is pushed to the
        server via the STAC query extension.  Filters the query extension
        cannot express (``polarizations``) are sent as a CQL2 filter instead;
        :class:`~eostac.exceptions.SearchError` is raised if the API does not
        support it.
    extra_query:
        Raw STAC query-extension dict (``{property: {operator: value}}``) for
        provider-specific properties.  Overrides *params* per property.

    Warns
    -----
    UserWarning
        When a filtered search returns no items and a filtered property is
        absent from the items the same search returns without filters.

    Returns
    -------
    geopandas.GeoDataFrame
        Columns: ``geometry``, ``item_id``, ``collection``, ``datetime``,
        ``cloud_cover`` (normalised 0–100 if present), ``assets`` (dict of
        key → href), ``asset_roles`` (dict of key → roles), ``asset_types``
        (dict of key → media type, ``""`` when undeclared), plus all other
        flattened STAC properties.
    """
    query = {**(params.to_query() if params is not None else {}), **(extra_query or {})}
    predicates = params.to_filter() if params is not None else []
    spatiotemporal = dict(
        collections=collections, bbox=bbox, polygon=polygon, datetime=datetime
    )

    try:
        items = _search_api(
            source._client,
            **spatiotemporal,
            limit=limit,
            query=query,
            predicates=predicates,
        )
        if not items and (query or predicates):
            _warn_missing_properties(source._client, spatiotemporal, query, predicates)
    except SearchError:
        raise
    except Exception as exc:
        raise SearchError(str(exc)) from exc

    return _items_to_gdf(items)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _normalize_datetime(datetime: str | tuple | None) -> str | None:
    if datetime is None:
        return None
    if isinstance(datetime, str):
        return datetime
    start, end = datetime
    start_str = start.isoformat() if isinstance(start, dt) else str(start)
    end_str = end.isoformat() if isinstance(end, dt) else str(end)
    return f"{start_str}/{end_str}"


def _geom_to_dict(polygon: dict | "BaseGeometry") -> dict:
    if isinstance(polygon, dict):
        return polygon
    return mapping(polygon)


def _search_api(
    client: pystac_client.Client,
    collections: list[str] | None,
    bbox: list[float] | None,
    polygon: dict | "BaseGeometry" | None,
    datetime: str | tuple | None,
    limit: int | None,
    query: dict[str, dict] | None,
    predicates: list[dict] | None = None,
) -> list[pystac.Item]:
    kwargs: dict[str, Any] = {}

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
    if predicates:
        # A request carries either a query or a filter, never both: once a CQL2
        # predicate is needed, the query-extension filters are expressed as CQL2 too.
        if not client.conforms_to("FILTER"):
            props = ", ".join(sorted(_predicate_properties(predicates)))
            raise SearchError(
                f"This STAC API does not support the filter extension (CQL2), "
                f"required to filter on: {props}."
            )
        args = _query_to_cql2(query or {}) + predicates
        kwargs["filter"] = args[0] if len(args) == 1 else {"op": "and", "args": args}
        kwargs["filter_lang"] = "cql2-json"
    elif query:
        kwargs["query"] = query

    search = client.search(**kwargs)
    return list(search.items())


_QUERY_TO_CQL2_OPS = {
    "eq": "=",
    "neq": "<>",
    "lt": "<",
    "lte": "<=",
    "gt": ">",
    "gte": ">=",
    "in": "in",
}


def _query_to_cql2(query: dict[str, dict]) -> list[dict]:
    """Translate a STAC query-extension dict into a list of CQL2-JSON predicates."""
    predicates = []
    for prop, ops in query.items():
        for op, value in ops.items():
            if op not in _QUERY_TO_CQL2_OPS:
                raise SearchError(
                    f"Query operator {op!r} on {prop!r} cannot be combined "
                    f"with a CQL2 filter."
                )
            predicates.append(
                {"op": _QUERY_TO_CQL2_OPS[op], "args": [{"property": prop}, value]}
            )
    return predicates


def _predicate_properties(predicates: list[dict]) -> set[str]:
    return {
        arg["property"]
        for predicate in predicates
        for arg in predicate.get("args", [])
        if isinstance(arg, dict) and "property" in arg
    }


def _warn_missing_properties(
    client: pystac_client.Client,
    spatiotemporal: dict[str, Any],
    query: dict[str, dict],
    predicates: list[dict],
) -> None:
    """Warn when a filtered property is absent from an unfiltered sample item."""
    sample = _search_api(client, **spatiotemporal, limit=1, query=None)
    if not sample:
        return
    available = sample[0].properties or {}
    filtered = set(query) | _predicate_properties(predicates)
    missing = sorted(prop for prop in filtered if prop not in available)
    if missing:
        warnings.warn(
            f"Search returned no items. Filtered properties not carried by items "
            f"of this source: {missing!r}. Use extra_query with the provider's "
            f"property names.",
            stacklevel=3,
        )


def _normalize_cloud_cover(val: Union[int, float]) -> float:
    """Coerce a cloud-cover value to a 0–100 percentage."""
    val = float(val)
    if 0 <= val <= 1.0:
        val = val * 100
    return val


def _get_cloud_cover(props: dict) -> float | None:
    for key in ("eo:cloud_cover", "cloud_cover", "cloudCover"):
        val = props.get(key)
        if val is not None:
            return _normalize_cloud_cover(val)
    return None


# Universal STAC item fields present regardless of sensor.
_CORE_COLUMNS = [
    "geometry",
    "item_id",
    "collection",
    "datetime",
    "assets",
    "asset_roles",
    "asset_types",
]

# Cloud-cover appears under several raw keys; all normalise to a 0–100 ``cloud_cover``.
_CLOUD_COVER_KEYS = ("eo:cloud_cover", "cloud_cover", "cloudCover")


def _items_to_gdf(items: list[pystac.Item]) -> gpd.GeoDataFrame:
    if not items:
        return gpd.GeoDataFrame(
            columns=_CORE_COLUMNS,
            geometry="geometry",
            crs="EPSG:4326",
        )

    rows = []
    for item in items:
        props = item.properties or {}
        geom = shape(item.geometry) if item.geometry else None
        assets = {key: asset.href for key, asset in item.assets.items()}
        asset_roles = {
            key: list(asset.roles or []) for key, asset in item.assets.items()
        }

        asset_types = {
            key: asset.media_type or "" for key, asset in item.assets.items()
        }

        row: dict[str, object] = {
            "geometry": geom,
            "item_id": item.id,
            "collection": item.collection_id,
            "datetime": item.datetime,
            "assets": assets,
            "asset_roles": asset_roles,
            "asset_types": asset_types,
        }

        # Flatten every property under its namespace-stripped name, so the schema
        # follows whatever the item actually carries — sensor-specific columns
        # (cloud_cover, orbit_state, sar:polarizations, wrs_path, …) appear only
        # when present, with no hardcoded per-platform mapping.
        for k, v in props.items():
            if k == "datetime":
                continue
            if k in _CLOUD_COVER_KEYS:
                row.setdefault("cloud_cover", _normalize_cloud_cover(v))
                continue
            clean_key = k.split(":")[-1] if ":" in k else k
            if clean_key not in row:
                row[clean_key] = v
            else:
                row.setdefault(k, v)

        rows.append(row)

    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
    return gdf
