from pathlib import Path
import math
import re
import os
import json
from datetime import datetime

import matplotlib

# Windows / PowerShell
matplotlib.use("Agg")

import matplotlib.pyplot as plt

from matplotlib_scalebar.scalebar import ScaleBar
from PIL import Image as PILImage
from shapely.geometry import shape

from pypdf import PdfWriter


# ============================================================
# CONFIGURATION
# ============================================================

OUTPUT_DIR = Path("output")

DPI = 300

# US Letter portrait
PAGE_WIDTH = 8.5
PAGE_HEIGHT = 11.0

PAGE_SIZE = (
    PAGE_WIDTH,
    PAGE_HEIGHT,
)

# ============================================================
# MAP FRAME
# ============================================================

MAP_LEFT = 0.45
MAP_RIGHT = 0.45

MAP_BOTTOM = 2.05
MAP_TOP = 10.45

MAP_WIDTH = (
    PAGE_WIDTH
    - MAP_LEFT
    - MAP_RIGHT
)

MAP_HEIGHT = (
    MAP_TOP
    - MAP_BOTTOM
)


# ============================================================
# BOUNDARY
# ============================================================

def _get_boundary_geometry(boundary):
    """
    Convert supported boundary formats into Shapely geometry.

    Supported:
        - Shapely geometry
        - resolve_site() result
        - GeoJSON Feature
        - GeoJSON geometry
    """

    if hasattr(boundary, "geom_type"):
        return boundary

    if not isinstance(boundary, dict):
        raise TypeError(
            "boundary must be a Shapely geometry "
            "or GeoJSON dictionary"
        )

    if "geometry_geojson" in boundary:
        boundary = boundary["geometry_geojson"]

    if boundary.get("type") == "Feature":
        boundary = boundary["geometry"]

    return shape(boundary)


# ============================================================
# METADATA
# ============================================================

def _get_metadata(boundary):
    """
    Extract metadata from resolve_site().
    """

    metadata = {
        "county": None,
        "parcel_ids": [],
        "area_acres": None,
        "area_m2": None,
        "crs": None,
        "source": None,
        "snapping_tolerance_m": None,
    }

    if not isinstance(boundary, dict):
        return metadata

    properties = {}

    if isinstance(boundary.get("properties"), dict):
        properties.update(
            boundary["properties"]
        )

    geometry_geojson = boundary.get(
        "geometry_geojson"
    )

    if isinstance(geometry_geojson, dict):

        if isinstance(
            geometry_geojson.get("properties"),
            dict,
        ):
            properties.update(
                geometry_geojson["properties"]
            )

    for key in metadata:

        if key in boundary:
            metadata[key] = boundary[key]

    for key in metadata:

        if (
            metadata[key] is None
            or metadata[key] == []
        ):

            if key in properties:
                metadata[key] = properties[key]

    parcel_ids = metadata.get(
        "parcel_ids"
    )

    if parcel_ids is None:
        parcel_ids = []

    if isinstance(parcel_ids, str):
        parcel_ids = [parcel_ids]

    metadata["parcel_ids"] = [
        str(x)
        for x in parcel_ids
    ]

    return metadata


def _format_parcels(parcel_ids):

    if not parcel_ids:
        return "N/A"

    return ", ".join(
        str(x)
        for x in parcel_ids
    )


# ============================================================
# SCALE
# ============================================================

def _nice_number(value):

    if value <= 0:
        return 1.0

    exponent = math.floor(
        math.log10(value)
    )

    fraction = (
        value
        / (10 ** exponent)
    )

    if fraction <= 1:
        nice = 1
    elif fraction <= 2:
        nice = 2
    elif fraction <= 5:
        nice = 5
    else:
        nice = 10

    return nice * (
        10 ** exponent
    )


def _choose_scale(
    site_width_m,
    site_height_m,
):
    """
    Choose a standard exhibit scale.
    """

    max_dimension = max(
        site_width_m,
        site_height_m,
    )

    candidate_scales = [
        1200,
        1800,
        2400,
        3000,
        3600,
        4800,
        6000,
        7200,
        9600,
        12000,
        15000,
        18000,
        24000,
        30000,
        36000,
    ]

    for scale in candidate_scales:

        map_width_m = (
            MAP_WIDTH
            * 0.0254
            * scale
        )

        if (
            max_dimension
            <= map_width_m * 0.55
        ):
            return scale

    return candidate_scales[-1]


def _get_scaled_extent(
    geometry,
    scale_denominator,
):
    """
    Calculate the map extent from the selected scale.

    The parcel remains centered and occupies approximately
    half of the map frame.
    """

    minx, miny, maxx, maxy = (
        geometry.bounds
    )

    site_width = max(
        maxx - minx,
        1.0,
    )

    site_height = max(
        maxy - miny,
        1.0,
    )

    map_width_m = (
        MAP_WIDTH
        * 0.0254
        * scale_denominator
    )

    map_height_m = (
        MAP_HEIGHT
        * 0.0254
        * scale_denominator
    )

    map_width_m = max(
        map_width_m,
        site_width * 2.0,
    )

    map_height_m = max(
        map_height_m,
        site_height * 2.0,
    )

    cx = (
        minx + maxx
    ) / 2.0

    cy = (
        miny + maxy
    ) / 2.0

    extent_minx = (
        cx
        - map_width_m / 2.0
    )

    extent_maxx = (
        cx
        + map_width_m / 2.0
    )

    extent_miny = (
        cy
        - map_height_m / 2.0
    )

    extent_maxy = (
        cy
        + map_height_m / 2.0
    )

    return (
        extent_minx,
        extent_maxx,
        extent_miny,
        extent_maxy,
    )


# ============================================================
# IMAGE LOADING
# ============================================================

def _load_image(
    image_path,
):
    """
    Load imagery.

    Rasterio is preferred for TIFF files because it reads the
    georeferenced raster bands directly and avoids PIL TIFF
    decoding artifacts seen with some tiled NAIP imagery.

    Falls back to PIL when rasterio cannot be used.
    """

    if not image_path:
        return None

    path = Path(
        image_path
    )

    if not path.exists():
        return None

    # --------------------------------------------------------
    # RASTERIO PATH
    # --------------------------------------------------------

    try:

        import rasterio

        with rasterio.open(
            path
        ) as src:

            width = src.width
            height = src.height

            if width <= 0 or height <= 0:
                return None

            count = src.count

            if count >= 3:

                data = src.read(
                    [1, 2, 3]
                )

                image = data.transpose(
                    1,
                    2,
                    0
                )

            elif count == 1:

                data = src.read(
                    1
                )

                image = data

            else:
                raise ValueError(
                    "Raster contains no usable bands."
                )

            return {
                "image": image,
                "bounds": (
                    float(src.bounds.left),
                    float(src.bounds.bottom),
                    float(src.bounds.right),
                    float(src.bounds.top),
                ),
                "crs": (
                    str(src.crs)
                    if src.crs
                    else None
                ),
            }

    except Exception as exc:

        print(
            "Rasterio image load failed; "
            "trying PIL:",
            exc,
        )

    # --------------------------------------------------------
    # PIL FALLBACK
    # --------------------------------------------------------

    try:

        PILImage.MAX_IMAGE_PIXELS = None

        image = PILImage.open(
            path
        )

        width, height = (
            image.size
        )

        max_pixels = 30_000_000

        pixels = (
            width * height
        )

        if pixels > max_pixels:

            factor = math.sqrt(
                max_pixels
                / pixels
            )

            new_width = max(
                1,
                int(
                    width * factor
                ),
            )

            new_height = max(
                1,
                int(
                    height * factor
                ),
            )

            image = image.resize(
                (
                    new_width,
                    new_height,
                ),
                PILImage.Resampling.LANCZOS,
            )

        return {
            "image": image.convert(
                "RGB"
            ),
            "bounds": None,
            "crs": None,
        }

    except Exception as exc:

        print(
            f"Image rendering failed: {exc}"
        )

        return None


# ============================================================
# BACKGROUND IMAGE
# ============================================================

def _draw_single_background(
    ax,
    image_path,
    extent,
    kind,
):
    """
    Draw exactly one background.

    For georeferenced TIFFs, use the raster's real bounds
    rather than stretching the entire image blindly over the
    map extent. This prevents the repeated-tile artifact that
    was visible in the previous NAIP output.
    """

    (
        extent_minx,
        extent_maxx,
        extent_miny,
        extent_maxy,
    ) = extent

    ax.set_facecolor(
        "#e6e6e6"
    )

    if not image_path:

        print(
            f"WARNING: No {kind} image supplied."
        )

        return False

    path = Path(
        image_path
    )

    if not path.exists():

        print(
            f"WARNING: {kind} file does not exist: "
            f"{path}"
        )

        return False

    print(
        f"Drawing {kind}: {path}"
    )

    loaded = _load_image(
        path
    )

    if loaded is None:
        return False

    image = loaded["image"]
    raster_bounds = loaded["bounds"]

    # --------------------------------------------------------
    # GEOSPATIAL TIFF
    # --------------------------------------------------------

    if raster_bounds is not None:

        (
            raster_minx,
            raster_miny,
            raster_maxx,
            raster_maxy,
        ) = raster_bounds

        # Only display the portion of the raster that
        # intersects the map frame.
        display_minx = max(
            extent_minx,
            raster_minx,
        )

        display_maxx = min(
            extent_maxx,
            raster_maxx,
        )

        display_miny = max(
            extent_miny,
            raster_miny,
        )

        display_maxy = min(
            extent_maxy,
            raster_maxy,
        )

        if (
            display_minx < display_maxx
            and display_miny < display_maxy
        ):

            ax.imshow(
                image,
                extent=[
                    raster_minx,
                    raster_maxx,
                    raster_miny,
                    raster_maxy,
                ],
                origin="upper",
                interpolation="bilinear",
                aspect="auto",
                alpha=1.0,
                zorder=1,
            )

            return True

    # --------------------------------------------------------
    # NON-GEOREFERENCED FALLBACK
    # --------------------------------------------------------

    ax.imshow(
        image,
        extent=[
            extent_minx,
            extent_maxx,
            extent_miny,
            extent_maxy,
        ],
        origin="upper",
        interpolation="nearest",
        aspect="auto",
        alpha=1.0,
        zorder=1,
    )

    return True


# ============================================================
# PARCEL OUTLINE
# ============================================================

def _plot_polygon(
    ax,
    polygon,
):

    x, y = (
        polygon.exterior.xy
    )

    ax.plot(
        x,
        y,
        color="red",
        linewidth=2.5,
        zorder=20,
    )

    for interior in polygon.interiors:

        x, y = (
            interior.xy
        )

        ax.plot(
            x,
            y,
            color="red",
            linewidth=2.5,
            zorder=20,
        )


def _draw_boundary(
    ax,
    boundary,
):

    if boundary.geom_type == "Polygon":

        _plot_polygon(
            ax,
            boundary,
        )

    elif boundary.geom_type == "MultiPolygon":

        for polygon in boundary.geoms:

            _plot_polygon(
                ax,
                polygon,
            )

    else:

        raise TypeError(
            "Unsupported boundary type: "
            f"{boundary.geom_type}"
        )


# ============================================================
# SITE LOCATION
# ============================================================

def _draw_site_callout(
    ax,
    boundary,
):
    """
    Draw SITE LOCATION inside the map frame.

    The text position is defined in axes coordinates,
    preventing the previous long diagonal line.
    """

    centroid = (
        boundary.representative_point()
    )

    x = centroid.x
    y = centroid.y

    ax.annotate(
        "SITE LOCATION",

        xy=(
            x,
            y,
        ),

        xycoords="data",

        xytext=(
            0.72,
            0.82,
        ),

        textcoords="axes fraction",

        fontsize=9,

        fontweight="bold",

        ha="left",

        va="bottom",

        arrowprops=dict(
            arrowstyle="->",
            linewidth=1.2,
            color="black",
            shrinkA=3,
            shrinkB=3,
        ),

        bbox=dict(
            boxstyle="round,pad=0.35",
            facecolor="white",
            edgecolor="black",
            linewidth=0.9,
            alpha=0.95,
        ),

        zorder=40,

        annotation_clip=True,
    )


# ============================================================
# NORTH ARROW
# ============================================================

def _draw_north_arrow(
    ax,
):

    ax.annotate(
        "N",

        xy=(
            0.08,
            0.90,
        ),

        xycoords="axes fraction",

        xytext=(
            0.08,
            0.78,
        ),

        textcoords="axes fraction",

        fontsize=12,

        fontweight="bold",

        ha="center",

        va="center",

        arrowprops=dict(
            arrowstyle="-|>",
            linewidth=1.5,
            color="black",
        ),

        bbox=dict(
            boxstyle="round,pad=0.2",
            facecolor="white",
            edgecolor="black",
            alpha=0.9,
        ),

        zorder=40,
    )


# ============================================================
# SCALE BAR
# ============================================================
def _draw_scale_bar(
    ax,
    map_width_m,
):

    # Use approximately 22% of the map width,
    # rounded to a clean engineering value.
    scale_bar_length_m = _nice_number(
        map_width_m * 0.22
    )

    scalebar = ScaleBar(
        dx=1.0,
        units="m",
        dimension="si-length",
        location="lower left",
        length_fraction=0.22,
        width_fraction=0.015,
        box_alpha=0.85,
        border_pad=0.5,
        sep=4,
        frameon=True,
        color="black",
        scale_loc="bottom",
        fixed_value=scale_bar_length_m,
        fixed_units="m",
    )

    ax.add_artist(
        scalebar
    )

    return scale_bar_length_m
# ============================================================
# TITLE BLOCK
# ============================================================

def _draw_title_block(
    fig,
    title,
    year,
    metadata,
    scale_denominator,
    map_type,
):
    """
    Draw the existing Task 3 title/information block.
    """

    left = 0.45

    # --------------------------------------------------------
    # TOP BORDER
    # --------------------------------------------------------

    fig.lines.append(
        plt.Line2D(
            [
                left / PAGE_WIDTH,
                (PAGE_WIDTH - 0.45)
                / PAGE_WIDTH,
            ],
            [
                1.82 / PAGE_HEIGHT,
                1.82 / PAGE_HEIGHT,
            ],
            transform=fig.transFigure,
            color="black",
            linewidth=1.2,
        )
    )

    # --------------------------------------------------------
    # TITLE
    # --------------------------------------------------------

    title_text = str(
        title
    )

    fig.text(
        left / PAGE_WIDTH,
        1.67 / PAGE_HEIGHT,
        title_text.upper(),
        fontsize=15,
        fontweight="bold",
        ha="left",
        va="center",
        color="black",
        zorder=102,
    )

    # --------------------------------------------------------
    # MAP TYPE
    # --------------------------------------------------------

    fig.text(
        7.10 / PAGE_WIDTH,
        1.67 / PAGE_HEIGHT,
        map_type,
        fontsize=9,
        fontweight="bold",
        ha="center",
        va="center",
        color="black",
        zorder=102,
    )

    # --------------------------------------------------------
    # YEAR
    # --------------------------------------------------------

    if map_type == "AERIAL":

        year_text = (
            f"YEAR: {year}"
            if year is not None
            else "YEAR: N/A"
        )

    else:

        year_text = "YEAR: USGS TOPO"

    fig.text(
        left / PAGE_WIDTH,
        1.45 / PAGE_HEIGHT,
        year_text,
        fontsize=9,
        ha="left",
        va="center",
        color="black",
        zorder=102,
    )

    # --------------------------------------------------------
    # COUNTY
    # --------------------------------------------------------

    county = metadata.get(
        "county"
    )

    if not county:
        county = "N/A"

    fig.text(
        left / PAGE_WIDTH,
        1.13 / PAGE_HEIGHT,
        f"COUNTY: {county}",
        fontsize=9,
        ha="left",
        va="center",
        color="black",
        zorder=102,
    )

    # --------------------------------------------------------
    # PARCEL ID
    # --------------------------------------------------------

    parcel_text = _format_parcels(
        metadata.get(
            "parcel_ids",
            [],
        )
    )

    fig.text(
        2.90 / PAGE_WIDTH,
        1.13 / PAGE_HEIGHT,
        f"PARCEL ID: {parcel_text}",
        fontsize=9,
        ha="left",
        va="center",
        color="black",
        zorder=102,
    )

    # --------------------------------------------------------
    # SCALE
    # --------------------------------------------------------

    fig.text(
        left / PAGE_WIDTH,
        0.86 / PAGE_HEIGHT,
        f"MAP SCALE: 1:{scale_denominator:,}",
        fontsize=8,
        ha="left",
        va="center",
        color="black",
        zorder=102,
    )

    # --------------------------------------------------------
    # AREA
    # --------------------------------------------------------

    area_acres = metadata.get(
        "area_acres"
    )

    if area_acres is not None:

        try:

            area_text = (
                f"AREA: {float(area_acres):.3f} ACRES"
            )

        except Exception:

            area_text = "AREA: N/A"

    else:

        area_text = "AREA: N/A"

    fig.text(
        2.90 / PAGE_WIDTH,
        0.86 / PAGE_HEIGHT,
        area_text,
        fontsize=8,
        ha="left",
        va="center",
        color="black",
        zorder=102,
    )

    # --------------------------------------------------------
    # PROJECTION
    # --------------------------------------------------------

    projection = metadata.get(
        "crs"
    )

    if projection:

        projection_text = (
            f"PROJECTION: {projection}"
        )

    else:

        projection_text = (
            "PROJECTION: EPSG:32614"
        )

    fig.text(
        left / PAGE_WIDTH,
        0.61 / PAGE_HEIGHT,
        projection_text,
        fontsize=8,
        ha="left",
        va="center",
        color="black",
        zorder=102,
    )

    # --------------------------------------------------------
    # SOURCE
    # --------------------------------------------------------

    if map_type == "AERIAL":

        source_text = (
            "SOURCE: NAIP AERIAL IMAGERY"
        )

    else:

        source_text = (
            "SOURCE: USGS TOPO"
        )

    fig.text(
        4.10 / PAGE_WIDTH,
        0.61 / PAGE_HEIGHT,
        source_text,
        fontsize=8,
        ha="left",
        va="center",
        color="black",
        zorder=102,
    )

    # --------------------------------------------------------
    # FIGURE
    # --------------------------------------------------------

    fig.text(
        7.68 / PAGE_WIDTH,
        1.52 / PAGE_HEIGHT,
        "FIGURE",
        fontsize=7.5,
        ha="center",
        va="center",
        color="black",
        zorder=102,
    )

    fig.text(
        7.68 / PAGE_WIDTH,
        0.92 / PAGE_HEIGHT,
        "1",
        fontsize=26,
        fontweight="bold",
        ha="center",
        va="center",
        color="black",
        zorder=102,
    )

    # --------------------------------------------------------
    # BOTTOM BORDER
    # --------------------------------------------------------

    fig.lines.append(
        plt.Line2D(
            [
                left / PAGE_WIDTH,
                (PAGE_WIDTH - 0.45)
                / PAGE_WIDTH,
            ],
            [
                0.36 / PAGE_HEIGHT,
                0.36 / PAGE_HEIGHT,
            ],
            transform=fig.transFigure,
            color="black",
            linewidth=1.2,
        )
    )


# ============================================================
# SAFE OUTPUT NAME
# ============================================================

def _safe_name(
    title,
):

    text = str(
        title
    ).strip()

    text = re.sub(
        r"[^A-Za-z0-9 _-]+",
        "",
        text,
    )

    text = re.sub(
        r"\s+",
        "_",
        text,
    )

    text = re.sub(
        r"_+",
        "_",
        text,
    )

    text = text.strip(
        "_"
    )

    if not text:
        text = "site_exhibit"

    reserved = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "COM1",
        "COM2",
        "COM3",
        "COM4",
        "COM5",
        "COM6",
        "COM7",
        "COM8",
        "COM9",
        "LPT1",
        "LPT2",
        "LPT3",
        "LPT4",
        "LPT5",
        "LPT6",
        "LPT7",
        "LPT8",
        "LPT9",
    }

    if text.upper() in reserved:
        text = f"site_{text}"

    return text


# ============================================================
# SAFE PNG SAVE
# ============================================================

def _save_png(
    fig,
    png_path,
):

    png_path = Path(
        png_path
    ).resolve()

    png_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    tmp_path = png_path.with_suffix(
        ".tmp.png"
    )

    try:

        if tmp_path.exists():
            tmp_path.unlink()

        fig.savefig(
            str(tmp_path),
            format="png",
            dpi=DPI,
            facecolor="white",
            edgecolor="white",
            bbox_inches=None,
            pad_inches=0,
        )

        if not tmp_path.exists():

            raise OSError(
                f"Matplotlib did not create "
                f"{tmp_path}"
            )

        if png_path.exists():
            png_path.unlink()

        os.replace(
            str(tmp_path),
            str(png_path),
        )

        return True

    except Exception:

        if tmp_path.exists():

            try:
                tmp_path.unlink()
            except Exception:
                pass

        raise


# ============================================================
# PDF CREATION
# ============================================================

def _create_pdf_from_png(
    png_path,
    pdf_path,
):

    png_path = Path(
        png_path
    ).resolve()

    pdf_path = Path(
        pdf_path
    ).resolve()

    try:

        with PILImage.open(
            png_path
        ) as rendered:

            rendered = rendered.convert(
                "RGB"
            )

            rendered.save(
                str(pdf_path),
                format="PDF",
                resolution=DPI,
            )

        return True

    except Exception as exc:

        print(
            f"PDF creation failed: {exc}"
        )

        return False


# ============================================================
# RENDER ONE MAP
# ============================================================

def _render_single(
    geometry,
    metadata,
    image_path,
    map_type,
    title,
    year,
    scale_denominator,
    extent,
    layer_bbox,
    output_name=None,
):
    """
    Render one independent map.
    """

    print()
    print("=" * 70)
    print(
        f"RENDERING {map_type} MAP"
    )
    print("=" * 70)

    fig = plt.figure(
        figsize=PAGE_SIZE,
        dpi=DPI,
        facecolor="white",
    )

    ax = fig.add_axes(
        [
            MAP_LEFT / PAGE_WIDTH,
            MAP_BOTTOM / PAGE_HEIGHT,
            MAP_WIDTH / PAGE_WIDTH,
            MAP_HEIGHT / PAGE_HEIGHT,
        ]
    )

    ax.set_aspect(
        "equal",
        adjustable="box",
    )

    (
        extent_minx,
        extent_maxx,
        extent_miny,
        extent_maxy,
    ) = extent

    ax.set_xlim(
        extent_minx,
        extent_maxx,
    )

    ax.set_ylim(
        extent_miny,
        extent_maxy,
    )

    # --------------------------------------------------------
    # BACKGROUND
    # --------------------------------------------------------

    _draw_single_background(
        ax,
        image_path,
        extent,
        map_type.lower(),
    )

    # --------------------------------------------------------
    # PARCEL
    # --------------------------------------------------------

    _draw_boundary(
        ax,
        geometry,
    )

    # --------------------------------------------------------
    # SITE LOCATION
    # --------------------------------------------------------

    _draw_site_callout(
        ax,
        geometry,
    )

    # --------------------------------------------------------
    # NORTH
    # --------------------------------------------------------

    _draw_north_arrow(
        ax
    )

    # --------------------------------------------------------
    # SCALE BAR
    # --------------------------------------------------------
    map_width_m = (
        extent_maxx
        - extent_minx
    )

    scale_bar_length_m = _draw_scale_bar(
        ax,
        map_width_m,
    )

    # --------------------------------------------------------
    # MAP FRAME
    # --------------------------------------------------------

    ax.set_xticks([])
    ax.set_yticks([])

    for spine in ax.spines.values():

        spine.set_visible(True)
        spine.set_linewidth(1.4)
        spine.set_color("black")

    # --------------------------------------------------------
    # TITLE BLOCK
    # --------------------------------------------------------

    _draw_title_block(
        fig,
        title,
        year,
        metadata,
        scale_denominator,
        map_type,
    )

    # --------------------------------------------------------
    # OUTPUT NAME
    # --------------------------------------------------------

    clean_title = _safe_name(
        title
    )

    prefix = (
        "Site_Plan"
        if map_type == "AERIAL"
        else "Site_Location"
    )

    if output_name:

        clean_output = _safe_name(
            output_name
        )

        filename = (
            f"{prefix}_{clean_output}"
        )

    else:

        filename = (
            f"{prefix}_{clean_title}"
        )

    png_path = (
        OUTPUT_DIR
        / f"{filename}.png"
    )

    pdf_path = (
        OUTPUT_DIR
        / f"{filename}.pdf"
    )

    png_path = png_path.resolve()
    pdf_path = pdf_path.resolve()

    print()
    print(
        f"{map_type} PNG:"
    )
    print(
        png_path
    )

    print(
        f"{map_type} PDF:"
    )
    print(
        pdf_path
    )

    _save_png(
        fig,
        png_path,
    )

    plt.close(
        fig
    )

    pdf_created = (
        _create_pdf_from_png(
            png_path,
            pdf_path,
        )
    )
    elements_drawn = {
        "background_image": True,
        "site_outline": True,
        "site_location_callout": True,
        "north_arrow": True,
        "scale_bar": True,
        "title_block": True,
        "year": (
            year is not None
            and map_type == "AERIAL"
        ),
        "frame_border": True,
    }
    return {
        "png": str(
            png_path
        ),

        "pdf": str(
            pdf_path
        ),

        "pdf_created": pdf_created,

        "map_type": map_type,

        "map_name": (
            "Site Plan"
            if map_type == "AERIAL"
            else "Site Location"
        ),

        "source": (
            "NAIP"
            if map_type == "AERIAL"
            else "USGS Topo"
        ),

        "year": (
            year
            if map_type == "AERIAL"
            else None
        ),

        "bbox": (
            extent_minx,
            extent_miny,
            extent_maxx,
            extent_maxy,
        ),

        "layer_bbox": layer_bbox,
               
        "elements_drawn": elements_drawn,

        "scale_bar_length_metres": (
            scale_bar_length_m
        ),

        "map_width_metres": (
            map_width_m
        ),

        "scale_denominator": (
            scale_denominator
        ),
    }


# ============================================================
# MAIN RENDER FUNCTION
# ============================================================

def render(
    layer,
    boundary,
    title,
    year,
    map_type="AERIAL",
):
    """
    Render the existing Task 3 aerial + topo outputs.

    The existing behavior is preserved.
    """

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    geometry = _get_boundary_geometry(
        boundary
    )

    metadata = _get_metadata(
        boundary
    )

    if not isinstance(
        layer,
        dict,
    ):
        raise TypeError(
            "layer must be a dictionary"
        )

    layer_bbox = layer.get(
        "bbox"
    )

    if layer_bbox is None:

        raise ValueError(
            "Layer is missing bbox"
        )

    layer_bbox = tuple(
        map(
            float,
            layer_bbox,
        )
    )

    # --------------------------------------------------------
    # GET AERIAL / TOPO
    # --------------------------------------------------------

    (
        aerial_path,
        topo_path,
    ) = _get_layer_paths(
        layer
    )
    # Test and simple layer objects may provide one generic path.
    if layer.get("path"):
        if map_type == "AERIAL" and not aerial_path:
            aerial_path = layer["path"]

        if map_type != "AERIAL" and not topo_path:
            topo_path = layer["path"]

    print()
    print("=" * 70)
    print("BACKGROUND LAYERS")
    print("=" * 70)

    print(
        "BACKGROUND AERIAL:",
        aerial_path,
    )

    print(
        "BACKGROUND TOPO:",
        topo_path,
    )

    if map_type == "AERIAL":

        if map_type.upper() == "AERIAL":
            if not aerial_path:
                raise FileNotFoundError(
                    "Aerial layer path was not supplied."
                )

        elif map_type.upper() == "TOPO":
            if not topo_path:
                raise FileNotFoundError(
                    "Topo layer path was not supplied."
                )

    else:

        if not topo_path:
            raise FileNotFoundError(
                "Topo layer path was not supplied."
            )

        if not Path(topo_path).exists():
            raise FileNotFoundError(
                f"Topo layer does not exist: "
                f"{topo_path}"
            )
    # --------------------------------------------------------
    # SCALE
    # --------------------------------------------------------

    minx, miny, maxx, maxy = (
        geometry.bounds
    )

    site_width = max(
        maxx - minx,
        1.0,
    )

    site_height = max(
        maxy - miny,
        1.0,
    )

    scale_denominator = _choose_scale(
        site_width,
        site_height,
    )

    print(
        "Scale:",
        f"1:{scale_denominator:,}",
    )

    # --------------------------------------------------------
    # EXTENT
    # --------------------------------------------------------

    extent = _get_scaled_extent(
        geometry,
        scale_denominator,
    )

    print(
        "Extent:",
        extent,
    )

    # --------------------------------------------------------
    # AERIAL
    # --------------------------------------------------------
    # --------------------------------------------------------
    # RENDER AERIAL
    # --------------------------------------------------------

    aerial_result = None
    topo_result = None

    if aerial_path:

        aerial_result = _render_single(
            geometry=geometry,
            metadata=metadata,
            image_path=aerial_path,
            map_type="AERIAL",
            title=title,
            year=year,
            scale_denominator=scale_denominator,
            extent=extent,
            layer_bbox=layer_bbox,
        )

    # --------------------------------------------------------
    # RENDER TOPO
    # --------------------------------------------------------

    if topo_path:

        topo_result = _render_single(
            geometry=geometry,
            metadata=metadata,
            image_path=topo_path,
            map_type="TOPO",
            title=title,
            year=year,
            scale_denominator=scale_denominator,
            extent=extent,
            layer_bbox=layer_bbox,
        )

    return {
        "png": (
            aerial_result["png"]
            if aerial_result
            else topo_result["png"]
            ),

        "pdf": (
            aerial_result["pdf"]
            if aerial_result
            else topo_result["pdf"]
            ),


        "aerial_png": (
            aerial_result["png"]
            if aerial_result
            else None
        ),

        "aerial_pdf": (
            aerial_result["pdf"]
            if aerial_result
            else None
        ),

        "topo_png": (
            topo_result["png"]
            if topo_result
            else None
        ),

        "topo_pdf": (
            topo_result["pdf"]
            if topo_result
            else None
        ),

        "dpi": DPI,

        "page_size": "US Letter",

        "year": year,

        "scale": (
            f"1:{scale_denominator:,}"
        ),

        "bbox": extent,

        "layer_bbox": layer_bbox,

        "aerial": (
            str(
                Path(aerial_path).resolve()
            )
            if aerial_path
            else None
        ),

        "topo": (
            str(
                Path(topo_path).resolve()
            )
            if topo_path
            else None
        ),

        "aerial_pdf_created": (
            aerial_result["pdf_created"]
            if aerial_result
            else False
        ),

        "topo_pdf_created": (
            topo_result["pdf_created"]
            if topo_result
            else False
        ),

        "elements_drawn": (
            aerial_result["elements_drawn"]
            if aerial_result
            else topo_result["elements_drawn"]
        ),

        "scale_bar_length_metres": (
            aerial_result["scale_bar_length_metres"]
            if aerial_result
            else topo_result[
                "scale_bar_length_metres"
            ]
        ),

        "map_width_metres": (
            aerial_result["map_width_metres"]
            if aerial_result
            else topo_result[
                "map_width_metres"
            ]
        ),

        "scale_denominator": (
            aerial_result["scale_denominator"]
            if aerial_result
            else topo_result[
                "scale_denominator"
            ]
        ),
    }
# ============================================================
# LAYER PATH HELPERS
# ============================================================

def _get_layer_paths(layer):

    if not isinstance(
        layer,
        dict,
    ):
        return None, None

    # --------------------------------------------------------
    # AERIAL
    # --------------------------------------------------------

    aerial_value = (
        layer.get("aerial_path")
        or layer.get("aerial")
        or layer.get("path")
        or layer.get("filepath")
        or layer.get("file")
    )

    if isinstance(
        aerial_value,
        dict,
    ):

        aerial_path = (
            aerial_value.get("path")
            or aerial_value.get("filepath")
            or aerial_value.get("file")
        )

    elif aerial_value:

        aerial_path = str(
            aerial_value
        )

    else:

        aerial_path = None

    # --------------------------------------------------------
    # TOPO
    # --------------------------------------------------------

    topo_value = (
        layer.get("topo_path")
        or layer.get("topo")
        or layer.get("topographic_path")
        or layer.get("topographic")
    )

    if isinstance(
        topo_value,
        dict,
    ):

        topo_path = (
            topo_value.get("path")
            or topo_value.get("filepath")
            or topo_value.get("file")
        )

    elif topo_value:

        topo_path = str(
            topo_value
        )

    else:

        topo_path = None

    return (
        aerial_path,
        topo_path,
    )


# ============================================================
# PDF MERGE HELPER
# ============================================================



# ============================================================
# TASK 5 - AERIAL SERIES
# ============================================================
