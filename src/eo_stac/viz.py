"""Visualization utilities — folium-based maps for STAC search results."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import folium
import geopandas as gpd
import pandas as pd


def plot_footprints(
    gdf: gpd.GeoDataFrame,
    column: str | None = "cloud_cover",
    tiles: str = "OpenStreetMap",
) -> folium.Map:
    """Render item footprints as an interactive folium map.

    Parameters
    ----------
    gdf:
        GeoDataFrame with a ``geometry`` column (EPSG:4326).
    column:
        Property column used to colour polygons.  ``None`` → uniform colour.
    tiles:
        Folium tile layer name (e.g. ``"OpenStreetMap"``, ``"CartoDB positron"``).

    Returns
    -------
    folium.Map
    """
    if gdf.empty:
        return folium.Map(tiles=tiles)

    gdf_wgs = gdf.to_crs("EPSG:4326") if gdf.crs and gdf.crs.to_epsg() != 4326 else gdf

    centroid = gdf_wgs.geometry.union_all().centroid
    m = folium.Map(location=[centroid.y, centroid.x], zoom_start=6, tiles=tiles)

    values = gdf_wgs[column].dropna() if column and column in gdf_wgs.columns else None
    vmin = float(values.min()) if values is not None and len(values) else 0.0
    vmax = float(values.max()) if values is not None and len(values) else 100.0

    colormap = None
    if values is not None and vmax > vmin:
        import branca.colormap as cm

        colormap = cm.linear.RdBu_11.scale(vmin, vmax).to_step(10)
        colormap.caption = column
        colormap.add_to(m)

    for _, row in gdf_wgs.iterrows():
        geom = row.geometry
        if geom is None:
            continue

        fill_color = "#3388ff"
        if colormap and column and column in row.index:
            val = row[column]
            if not pd.isna(val):
                fill_color = colormap(float(val))

        tooltip_parts = [f"<b>{row.get('item_id', '')}</b>"]
        if column and column in row.index:
            tooltip_parts.append(f"{column}: {row[column]}")
        tooltip = "<br>".join(tooltip_parts)

        folium.GeoJson(
            geom.__geo_interface__,
            style_function=lambda _, c=fill_color: {
                "fillColor": c,
                "color": "#333",
                "weight": 1,
                "fillOpacity": 0.4,
            },
            tooltip=tooltip,
        ).add_to(m)

    m.fit_bounds(
        [
            [gdf_wgs.total_bounds[1], gdf_wgs.total_bounds[0]],
            [gdf_wgs.total_bounds[3], gdf_wgs.total_bounds[2]],
        ]
    )
    return m


def plot_thumbnails(
    gdf: gpd.GeoDataFrame,
    max_items: int = 9,
    sign: Callable[[str], str] | None = None,
) -> folium.Map:
    """Overlay thumbnail images on a folium map.

    Thumbnails are sourced from the ``thumbnail`` asset key.  Items without a
    thumbnail asset are shown as footprint polygons instead.

    Parameters
    ----------
    gdf:
        GeoDataFrame from :func:`~eo_stac.search.search_items`.
    max_items:
        Maximum number of items to display.
    sign:
        Callable turning a thumbnail href into a fetchable one, for providers
        whose hrefs need a token (e.g. ``planetary_computer.sign``).

    Returns
    -------
    folium.Map
    """
    subset = gdf.head(max_items)
    if subset.empty:
        return folium.Map()

    gdf_wgs = (
        subset.to_crs("EPSG:4326")
        if subset.crs and subset.crs.to_epsg() != 4326
        else subset
    )

    centroid = gdf_wgs.geometry.union_all().centroid
    m = folium.Map(location=[centroid.y, centroid.x], zoom_start=6)

    for _, row in gdf_wgs.iterrows():
        geom = row.geometry
        if geom is None:
            continue

        assets: dict = row.get("assets") or {}
        thumb_href = assets.get("thumbnail") or assets.get("visual")
        if thumb_href and sign is not None:
            thumb_href = sign(thumb_href)

        bounds = geom.bounds  # (minx, miny, maxx, maxy)
        img_bounds = [[bounds[1], bounds[0]], [bounds[3], bounds[2]]]

        if thumb_href:
            folium.raster_layers.ImageOverlay(
                image=thumb_href,
                bounds=img_bounds,
                opacity=0.8,
                name=row.get("item_id", ""),
            ).add_to(m)
        else:
            folium.GeoJson(
                geom.__geo_interface__,
                tooltip=str(row.get("item_id", "")),
            ).add_to(m)

    m.fit_bounds(
        [
            [gdf_wgs.total_bounds[1], gdf_wgs.total_bounds[0]],
            [gdf_wgs.total_bounds[3], gdf_wgs.total_bounds[2]],
        ]
    )
    return m


def quick_view(
    path_or_href: str | Path,
    band_indices: list[int] | None = None,
    overview_level: int | None = 2,
    sign: Callable[[str], str] | None = None,
) -> folium.Map:
    """Open a raster at a reduced overview and render it on a folium map.

    Parameters
    ----------
    path_or_href:
        Local path or remote URL to any rioxarray-readable raster.
    band_indices:
        Band indices to load (1-based, e.g. ``[3, 2, 1]`` for RGB).
        ``None`` loads the first band.
    overview_level:
        Overview pyramid level (higher = smaller/faster).
    sign:
        Callable turning a remote href into a fetchable one, for providers
        whose hrefs need a token (e.g. ``planetary_computer.sign``).

    Returns
    -------
    folium.Map
    """
    import base64
    import io

    import numpy as np
    import rioxarray
    from PIL import Image

    href = str(path_or_href)
    if sign is not None:
        href = sign(href)
    open_kwargs: dict[str, object] = {}
    if overview_level is not None:
        open_kwargs["overview_level"] = overview_level
    da = rioxarray.open_rasterio(href, **open_kwargs)
    if band_indices:
        da = da.sel(band=band_indices)

    # Reproject to WGS84 for folium
    da = da.rio.reproject("EPSG:4326")

    # Normalise to 0-255 PNG
    arr = da.values
    if arr.ndim == 3:
        arr = np.moveaxis(arr, 0, -1)  # (bands, H, W) → (H, W, bands)
    else:
        arr = arr[:, :, np.newaxis]

    arr_min, arr_max = arr.min(), arr.max()
    if arr_max > arr_min:
        arr = ((arr - arr_min) / (arr_max - arr_min) * 255).astype(np.uint8)
    else:
        arr = np.zeros_like(arr, dtype=np.uint8)

    img = Image.fromarray(arr.squeeze() if arr.shape[-1] == 1 else arr)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    img_b64 = base64.b64encode(buffer.getvalue()).decode()
    img_src = f"data:image/png;base64,{img_b64}"

    # Compute WGS84 bounds
    left, bottom, right, top = da.rio.bounds()
    img_bounds = [[bottom, left], [top, right]]

    m = folium.Map(
        location=[(bottom + top) / 2, (left + right) / 2],
        zoom_start=8,
    )
    folium.raster_layers.ImageOverlay(
        image=img_src,
        bounds=img_bounds,
        opacity=0.9,
        name="raster",
    ).add_to(m)
    folium.LayerControl().add_to(m)

    da.close()
    return m
