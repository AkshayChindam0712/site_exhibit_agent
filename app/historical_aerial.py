"""
historical_aerial.py

Historical aerial series for the site-exhibit-agent project.

Source rules:
    - 2010 onward: USDA NAIP through Microsoft Planetary Computer STAC.
    - 2003-2009: EarthExplorer. Manual georeferenced TIFFs are supported
      because EarthExplorer requires an account and manual downloads are
      explicitly allowed for test sites.
    - 2000-2002: recorded as missing because NAIP coverage does not begin
      until 2003.
    - Imagery without map coordinates/CRS is skipped and recorded missing.

The program:
    1. Resolves the existing parcel using app.parcels.
    2. Searches Planetary Computer STAC for actual NAIP coverage.
    3. Uses ONE STAC item per available year.
    4. Downloads ONE image per available year.
    5. Renders each downloaded image using the existing historical page frame.
    6. Combines all rendered pages into one PDF, newest first.
    7. Writes historical_manifest.json.

Run from the project root:
    python -m app.historical_aerial

For EarthExplorer 2003-2009 manual files, place georeferenced TIFFs in:
    cache/earth_explorer/
with names such as:
    2009.tif
    2008.tif
    2007.tif
"""

from __future__ import annotations

import io
import json
import math
import os
import sys
from pathlib import Path
from datetime import datetime, timezone

import requests
import rasterio
import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps, ImageDraw, ImageFont
from pyproj import CRS, Transformer
from rasterio.warp import transform_bounds
from shapely.geometry import shape, box
from shapely.ops import transform as shapely_transform

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader

# ============================================================
# CRS CONFIGURATION
# ============================================================

WGS84_CRS = "EPSG:4326"

# Existing project geometry is returned in the site's project CRS.
PROJECT_CRS = "EPSG:32614"


# ============================================================
# PLANETARY COMPUTER / NAIP CONFIGURATION
# ============================================================

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
NAIP_COLLECTION = "naip"

# Assignment requirement: NAIP through Planetary Computer from 2010 onward.
NAIP_START_YEAR = 2010

# Historical search starts at 2000 as required by Task 5.
START_YEAR = 2000
END_YEAR = datetime.now().year

# Requested rendered/export dimensions.
EXPORT_WIDTH = 3000
EXPORT_HEIGHT = 3000

REQUEST_TIMEOUT = 180
NAIP_MIN_BYTES = 10000


# ============================================================
# EARTH EXPLORER MANUAL DOWNLOAD AREA
# ============================================================

EARTH_EXPLORER_DIR = (
    Path(__file__).resolve().parent.parent
    / "cache"
    / "earth_explorer"
)


# ============================================================
# PDF / RENDER CONFIGURATION
# ============================================================

PAGE_WIDTH, PAGE_HEIGHT = letter
DPI = 200

MAP_LEFT = 42
MAP_RIGHT = 42
MAP_TOP = 42
MAP_BOTTOM = 170

SITE_LINE_WIDTH = 5


# ============================================================
# DIRECTORIES
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent

CACHE_DIR = PROJECT_ROOT / "cache"
IMAGE_DIR = CACHE_DIR / "layers"
RENDER_DIR = PROJECT_ROOT / "output" / "historical_aerial"
OUTPUT_DIR = PROJECT_ROOT / "output"

COMBINED_PDF = (
    OUTPUT_DIR / "SITE_PLAN_HISTORICAL_SERIES.pdf"
)

MANIFEST_FILE = (
    OUTPUT_DIR / "historical_manifest.json"
)

for directory in [
    CACHE_DIR,
    IMAGE_DIR,
    RENDER_DIR,
    OUTPUT_DIR,
    EARTH_EXPLORER_DIR,
]:
    directory.mkdir(
        parents=True,
        exist_ok=True,
    )


# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": (
            "site-exhibit-agent/"
            "historical-aerial"
        )
    }
)


# ============================================================
# PLANETARY COMPUTER
# ============================================================

try:
    import pystac_client
    import planetary_computer
except ImportError as exc:
    raise ImportError(
        "Planetary Computer support requires:\n\n"
        "    python -m pip install pystac-client planetary-computer\n"
    ) from exc


# ============================================================
# FONT
# ============================================================

def get_font(size: int, bold: bool = False):
    """
    Try Windows fonts first.
    Fall back to PIL default font.
    """

    candidates = []

    if bold:
        candidates.extend(
            [
                r"C:\Windows\Fonts\arialbd.ttf",
                r"C:\Windows\Fonts\calibrib.ttf",
            ]
        )
    else:
        candidates.extend(
            [
                r"C:\Windows\Fonts\arial.ttf",
                r"C:\Windows\Fonts\calibri.ttf",
            ]
        )

    for path in candidates:

        if os.path.exists(path):

            try:
                return ImageFont.truetype(
                    path,
                    size=size,
                )
            except Exception:
                pass

    return ImageFont.load_default()


# ============================================================
# PARCEL RESOLUTION
# ============================================================


def resolve_parcel(
    county,
    parcel_ids=None,
    coords=None,
    project_number=None,
):
    """
    Use the project's existing parcel resolver.
    """

    try:
        from app.parcels import resolve_site

    except ImportError:

        try:
            from .parcels import resolve_site

        except ImportError as exc:

            raise ImportError(
                "Could not import app.parcels.resolve_site.\n"
                "Run this program from the project root using:\n\n"
                "    python -m app.historical_aerial\n"
            ) from exc

    print()
    print("=" * 70)
    print("RESOLVING SITE")
    print("=" * 70)

    site = resolve_site(
        county=county,
        parcel_ids=parcel_ids,
        coords=coords,
        project_number=project_number,
    )

    return site
# ============================================================
# GEOMETRY
# ============================================================


def get_site_geometry(site):
    """
    Extract site geometry from resolve_site() output.
    """

    geometry_geojson = site.get(
        "geometry_geojson"
    )

    if not geometry_geojson:

        raise ValueError(
            "resolve_site() did not return "
            "'geometry_geojson'."
        )

    if "geometry" in geometry_geojson:

        geometry_data = geometry_geojson[
            "geometry"
        ]

    else:

        geometry_data = geometry_geojson

    geometry = shape(
        geometry_data
    )

    if geometry.is_empty:

        raise ValueError(
            "Site geometry is empty."
        )

    return geometry


# ============================================================
# DETERMINE UTM
# ============================================================


def get_utm_crs(geometry_wgs84):
    """
    Automatically determine UTM zone.
    """

    centroid = geometry_wgs84.centroid

    lon = centroid.x
    lat = centroid.y

    zone = int(
        math.floor(
            (lon + 180) / 6
        ) + 1
    )

    if lat >= 0:

        epsg = 32600 + zone

    else:

        epsg = 32700 + zone

    return f"EPSG:{epsg}"


# ============================================================
# PROJECT GEOMETRY
# ============================================================


def project_geometry(
    geometry,
    source_crs=WGS84_CRS,
    target_crs=PROJECT_CRS,
):
    """
    Project geometry between coordinate systems.
    """

    if geometry is None:
        raise ValueError("Geometry is None")

    print(
        f"Projecting geometry: "
        f"{source_crs} -> {target_crs}"
    )

    transformer = Transformer.from_crs(
        source_crs,
        target_crs,
        always_xy=True,
    )

    return shapely_transform(
        transformer.transform,
        geometry,
    )
# ============================================================
# SEARCH ALL NAIP RECORDS
# ============================================================



# ============================================================
# SEARCH PLANETARY COMPUTER NAIP
# ============================================================

def search_all_naip_records(
    bbox,
    crs,
):
    """
    Search the Planetary Computer NAIP STAC collection.

    2010 onward is searched from the STAC API. The search is
    spatial + temporal, so the returned years are actual years
    available at this location.

    One STAC item is selected per year. We do not download
    multiple tiles for a single year.
    """

    minx, miny, maxx, maxy = bbox

    transformer = Transformer.from_crs(
        crs,
        WGS84_CRS,
        always_xy=True,
    )

    stac_bbox = (
        *transformer.transform(minx, miny),
        *transformer.transform(maxx, maxy),
    )

    # Make sure bbox ordering is correct.
    stac_bbox = [
        min(stac_bbox[0], stac_bbox[2]),
        min(stac_bbox[1], stac_bbox[3]),
        max(stac_bbox[0], stac_bbox[2]),
        max(stac_bbox[1], stac_bbox[3]),
    ]

    print()
    print("=" * 70)
    print("SEARCHING PLANETARY COMPUTER NAIP STAC")
    print("=" * 70)
    print("STAC:", STAC_URL)
    print("COLLECTION:", NAIP_COLLECTION)
    print("SEARCH BBOX EPSG:4326:", stac_bbox)
    print(
        "SEARCH YEARS:",
        NAIP_START_YEAR,
        "to",
        END_YEAR,
    )

    catalog = pystac_client.Client.open(
        STAC_URL
    )

    search = catalog.search(
        collections=[NAIP_COLLECTION],
        bbox=stac_bbox,
        datetime=(
            f"{NAIP_START_YEAR}-01-01T00:00:00Z/"
            f"{END_YEAR}-12-31T23:59:59Z"
        ),
    )

    items = search.item_collection()

    print("STAC ITEMS RETURNED:", len(items))

    site_box_wgs84 = box(*stac_bbox)

    records = []

    for item in items:
        if item.datetime is None:
            continue

        year = int(item.datetime.year)

        if year < NAIP_START_YEAR or year > END_YEAR:
            continue

        if not item.geometry:
            continue

        item_geometry = shape(item.geometry)
        overlap = item_geometry.intersection(
            site_box_wgs84
        ).area

        if overlap <= 0:
            continue

        image_asset = item.assets.get("image")

        if image_asset is None:
            continue

        records.append(
            {
                "id": item.id,
                "year": year,
                "datetime": item.datetime.isoformat(),
                "href": image_asset.href,
                "gsd": item.properties.get("gsd"),
                "proj_epsg": item.properties.get("proj:epsg"),
                "overlap": overlap,
                "source": "USDA NAIP (Planetary Computer)",
            }
        )

    # One best item per year.
    # Largest spatial overlap wins; GSD then acquisition date
    # provide stable tie breaking.
    best_by_year = {}

    for record in records:
        year = record["year"]

        current = best_by_year.get(year)

        if current is None:
            best_by_year[year] = record
            continue

        current_key = (
            float(current.get("overlap") or 0),
            -float(current.get("gsd") or 999999),
            current.get("datetime") or "",
        )

        record_key = (
            float(record.get("overlap") or 0),
            -float(record.get("gsd") or 999999),
            record.get("datetime") or "",
        )

        if record_key > current_key:
            best_by_year[year] = record

    selected = list(best_by_year.values())
    selected.sort(
        key=lambda r: r["year"],
        reverse=True,
    )

    print()
    print("=" * 70)
    print("AVAILABLE PLANETARY COMPUTER NAIP YEARS")
    print("=" * 70)

    for record in selected:
        print(
            f'{record["year"]} -> '
            f'{record["id"]} | '
            f'GSD={record.get("gsd")} | '
            f'CRS={record.get("proj_epsg")}'
        )

    print("=" * 70)

    return selected


# ============================================================
# GROUP RECORDS BY YEAR
# ============================================================

def group_records_by_year(records):
    grouped = {}

    for record in records:
        year = int(record["year"])

        grouped.setdefault(
            year,
            []
        ).append(record)

    return grouped


# ============================================================
# SELECT ONE STAC ITEM PER YEAR
# ============================================================

def select_record_for_year(records, year):
    if not records:
        return None

    return max(
        records,
        key=lambda r: (
            float(r.get("overlap") or 0),
            -float(r.get("gsd") or 999999),
            r.get("datetime") or "",
        ),
    )


# ============================================================
# PRINT YEAR SUMMARY
# ============================================================

def print_year_summary(grouped):
    print()
    print("=" * 70)
    print("AVAILABLE NAIP YEARS")
    print("=" * 70)

    for year in sorted(
        grouped.keys(),
        reverse=True,
    ):
        record = select_record_for_year(
            grouped[year],
            year,
        )

        print(
            f'{year} -> {record["id"]}'
        )

    print("=" * 70)


# ============================================================
# DOWNLOAD ONE PLANETARY COMPUTER NAIP IMAGE
# ============================================================

def download_naip_record(
    record,
    export_bbox,
    crs,
    year,
):
    """
    Download exactly one Planetary Computer NAIP COG for
    the requested year and crop it to the project export bbox.

    The STAC item is signed by Planetary Computer before the
    COG is opened. Rasterio then reads only the required
    spatial window rather than downloading the entire tile.
    """

    item_id = record["id"]

    safe_id = "".join(
        c if c.isalnum() or c in "-_." else "_"
        for c in item_id
    )

    output_path = (
        IMAGE_DIR
        / f"naip_{year}_pc_{safe_id}.tif"
    )

    if output_path.exists():
        try:
            with rasterio.open(output_path) as src:
                if (
                    src.width > 0
                    and src.height > 0
                    and src.count >= 3
                    and src.crs is not None
                ):
                    print()
                    print(
                        f"EXACT NAIP {year} FOUND IN CACHE"
                    )
                    print("FILE:", output_path)
                    return output_path
        except Exception:
            try:
                output_path.unlink()
            except Exception:
                pass

    print()
    print("=" * 70)
    print(f"DOWNLOADING NAIP {year} FROM PLANETARY COMPUTER")
    print("=" * 70)
    print("STAC ITEM:", item_id)
    print("OUTPUT SIZE:", EXPORT_WIDTH, "x", EXPORT_HEIGHT)

    # The STAC search metadata is unsigned in a normal Client.
    # Sign the asset URL before opening the COG.
    signed_href = planetary_computer.sign(
        record["href"]
    )

    minx, miny, maxx, maxy = export_bbox

    with rasterio.Env(
        GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"
    ):
        with rasterio.open(
            signed_href
        ) as src:

            if src.count < 3:
                raise ValueError(
                    f"NAIP item {item_id} has fewer than 3 bands."
                )

            if src.crs is None:
                raise ValueError(
                    f"NAIP item {item_id} has no map coordinates/CRS."
                )

            print("SOURCE CRS:", src.crs)
            print("SOURCE SIZE:", src.width, "x", src.height)
            print("SOURCE BOUNDS:", src.bounds)

            # Convert project UTM bbox into the raster's CRS.
            source_bbox = transform_bounds(
                crs,
                src.crs,
                minx,
                miny,
                maxx,
                maxy,
                densify_pts=21,
            )

            print("SOURCE WINDOW BBOX:", source_bbox)

            requested = rasterio.windows.from_bounds(
                *source_bbox,
                transform=src.transform,
            )

            full_window = rasterio.windows.Window(
                0,
                0,
                src.width,
                src.height,
            )

            try:
                window = requested.intersection(
                    full_window
                )
            except Exception as exc:
                raise ValueError(
                    f"Requested site extent does not overlap "
                    f"NAIP item {item_id}."
                ) from exc

            if window.width <= 1 or window.height <= 1:
                raise ValueError(
                    f"NAIP item {item_id} has no usable pixels "
                    f"for the site extent."
                )

            data = src.read(
                [1, 2, 3, 4]
                if src.count >= 4
                else [1, 2, 3],
                window=window,
                out_shape=(
                    4 if src.count >= 4 else 3,
                    EXPORT_HEIGHT,
                    EXPORT_WIDTH,
                ),
                resampling=rasterio.enums.Resampling.bilinear,
            )

            window_transform = rasterio.windows.transform(
                window,
                src.transform,
            )

            # Preserve the actual source georeferencing.
            profile = src.profile.copy()
            profile.update(
                {
                    "driver": "GTiff",
                    "height": EXPORT_HEIGHT,
                    "width": EXPORT_WIDTH,
                    "count": data.shape[0],
                    "dtype": "uint8",
                    "transform": window_transform,
                    "compress": "deflate",
                    "predictor": 2,
                    "BIGTIFF": "IF_SAFER",
                }
            )

            temp_path = output_path.with_suffix(
                ".tmp.tif"
            )

            with rasterio.open(
                temp_path,
                "w",
                **profile,
            ) as dst:
                dst.write(
                    data.astype("uint8")
                )

    # Validate the local result.
    with rasterio.open(
        temp_path
    ) as check:

        if check.crs is None:
            raise ValueError(
                "Downloaded NAIP image has no CRS."
            )

        if (
            check.width <= 0
            or check.height <= 0
            or check.count < 3
        ):
            raise ValueError(
                "Downloaded NAIP image is invalid."
            )

        sample = check.read(
            1,
            out_shape=(100, 100),
        )

        print()
        print("VALIDATING NAIP", year)
        print("CRS:", check.crs)
        print(
            "SIZE:",
            check.width,
            "x",
            check.height,
        )
        print("BANDS:", check.count)
        print("BOUNDS:", check.bounds)
        print("Pixel min:", int(sample.min()))
        print("Pixel max:", int(sample.max()))

    if output_path.exists():
        output_path.unlink()

    temp_path.replace(
        output_path
    )

    print()
    print(f"SUCCESS: NAIP {year}")
    print("Saved:", output_path)

    return output_path


# ============================================================
# EARTH EXPLORER MANUAL FILE
# ============================================================

def find_earthexplorer_image(year):
    """
    EarthExplorer requires a free account for downloads.
    Manual downloads are explicitly allowed for the test site.

    Put a georeferenced TIFF for a year in:
        cache/earth_explorer/

    Accepted names:
        2009.tif
        2009.tiff
        naip_2009.tif
        doq_1999.tif

    Files without a CRS are rejected as required by the task.
    """

    candidates = [
        EARTH_EXPLORER_DIR / f"{year}.tif",
        EARTH_EXPLORER_DIR / f"{year}.tiff",
        EARTH_EXPLORER_DIR / f"naip_{year}.tif",
        EARTH_EXPLORER_DIR / f"naip_{year}.tiff",
        EARTH_EXPLORER_DIR / f"doq_{year}.tif",
        EARTH_EXPLORER_DIR / f"doq_{year}.tiff",
    ]

    for path in candidates:
        if not path.exists():
            continue

        try:
            with rasterio.open(path) as src:
                if (
                    src.crs is None
                    or src.width <= 0
                    or src.height <= 0
                    or src.count < 3
                ):
                    print(
                        f"SKIPPING EARTH EXPLORER {year}: "
                        "file has no usable map coordinates."
                    )
                    return None
        except Exception as exc:
            print(
                f"SKIPPING EARTH EXPLORER {year}: {exc}"
            )
            return None

        return path

    return None


# ============================================================
# MANUAL EARTH EXPLORER YEARS
# ============================================================

def get_earth_explorer_records():
    records = []

    # NAIP began in 2003. 2000-2002 therefore remain missing.
    for year in range(
        2003,
        min(NAIP_START_YEAR, END_YEAR + 1),
    ):
        path = find_earthexplorer_image(year)

        if path is not None:
            records.append(
                {
                    "year": year,
                    "path": str(path.resolve()),
                    "source": "USGS EarthExplorer",
                    "id": path.name,
                    "datetime": None,
                }
            )

    return records


# ============================================================
# PROJECT EXPORT EXTENT
# ============================================================



def make_export_extent(
    geometry_utm,
    scale_factor=5.0,
):
    """
    Create a larger map extent around the site.

    This makes the historical map useful visually
    instead of exporting only the tiny parcel itself.
    """

    minx, miny, maxx, maxy = (
        geometry_utm.bounds
    )

    width = maxx - minx
    height = maxy - miny

    width = max(
        width,
        100.0,
    )

    height = max(
        height,
        100.0,
    )

    cx = (
        minx + maxx
    ) / 2

    cy = (
        miny + maxy
    ) / 2

    export_width = (
        width * scale_factor
    )

    export_height = (
        height * scale_factor
    )

    return (
        cx - export_width / 2,
        cy - export_height / 2,
        cx + export_width / 2,
        cy + export_height / 2,
    )


# ============================================================
# TIFF -> RGB PIL
# ============================================================


def tiff_to_rgb(
    tif_path,
):
    """
    Read NAIP TIFF.

    Uses bands 1,2,3 for natural RGB.

    Handles 4-band NAIP correctly by ignoring
    the NIR band for the displayed image.
    """

    with rasterio.open(
        tif_path
    ) as src:

        if src.count < 3:

            raise ValueError(
                "NAIP TIFF does not contain "
                "at least 3 bands."
            )

        rgb = src.read(
            [1, 2, 3]
        )

    # --------------------------------------------------------
    # Percentile stretch
    # --------------------------------------------------------

    channels = []

    for band in rgb:

        low = float(
            __import__(
                "numpy"
            ).percentile(
                band,
                2
            )
        )

        high = float(
            __import__(
                "numpy"
            ).percentile(
                band,
                98
            )
        )

        if high <= low:
            high = low + 1

        band = (
            (
                band.astype(
                    "float32"
                )
                - low
            )
            / (
                high - low
            )
            * 255.0
        )

        band = band.clip(
            0,
            255
        ).astype(
            "uint8"
        )

        channels.append(
            band
        )

    import numpy as np

    arr = np.stack(
        channels,
        axis=2
    )

    image = Image.fromarray(
        arr,
        mode="RGB"
    )

    return image


# ============================================================
# DRAW NORTH ARROW
# ============================================================


def draw_north_arrow(
    image,
    x,
    y,
):
    draw = ImageDraw.Draw(image)

    # Larger N font
    font = get_font(
        40,
        bold=True
    )

    # --------------------------------------------------------
    # Arrow
    # --------------------------------------------------------

    arrow_top = y
    arrow_bottom = y + 120

    # Arrow shaft
    draw.line(
        [
            (x, arrow_bottom),
            (x, arrow_top),
        ],
        fill="black",
        width=8,
    )

    # Larger arrow head
    draw.polygon(
        [
            (x, arrow_top),
            (x - 20, arrow_top + 40),
            (x + 20, arrow_top + 40),
        ],
        fill="black",
    )

    # --------------------------------------------------------
    # N box
    # --------------------------------------------------------

    box_left = x - 35
    box_top = y + 135
    box_right = x + 35
    box_bottom = y + 195

    draw.rounded_rectangle(
        [
            box_left,
            box_top,
            box_right,
            box_bottom,
        ],
        radius=8,
        fill="white",
        outline="black",
        width=3,
    )

    # --------------------------------------------------------
    # N label
    # --------------------------------------------------------

    text_bbox = draw.textbbox(
        (0, 0),
        "N",
        font=font,
    )

    text_width = (
        text_bbox[2] - text_bbox[0]
    )

    text_height = (
        text_bbox[3] - text_bbox[1]
    )

    text_x = (
        x - text_width / 2
    )

    text_y = (
        box_top
        + (box_bottom - box_top - text_height) / 2
        - text_bbox[1]
    )

    draw.text(
        (
            text_x,
            text_y,
        ),
        "N",
        fill="black",
        font=font,
    )

# ============================================================
# DRAW SITE BOUNDARY
# ============================================================


def geometry_to_pixel_points(
    geometry_utm,
    raster,
    image_width,
    image_height,
):
    """
    Convert UTM geometry coordinates to image pixels.
    """

    bounds = raster.bounds

    minx = bounds.left
    maxx = bounds.right

    miny = bounds.bottom
    maxy = bounds.top

    def convert(
        x,
        y,
    ):

        px = (
            (x - minx)
            / (maxx - minx)
            * image_width
        )

        py = (
            (maxy - y)
            / (maxy - miny)
            * image_height
        )

        return (
            px,
            py
        )

    points = []

    geom = geometry_utm

    if geom.geom_type == "Polygon":

        rings = [
            geom.exterior
        ]

    elif geom.geom_type == "MultiPolygon":

        rings = []

        for polygon in geom.geoms:

            rings.append(
                polygon.exterior
            )

    else:

        return points

    for ring in rings:

        pts = []

        for x, y in ring.coords:

            pts.append(
                convert(
                    x,
                    y
                )
            )

        points.append(
            pts
        )

    return points


# ============================================================
# SCALE BAR
# ============================================================


def draw_scale_bar(
    image,
    raster,
):
    """
    Draw an approximate metric scale bar.
    """

    draw = ImageDraw.Draw(
        image
    )

    font = get_font(
        22
    )

    width = image.width
    height = image.height

    raster_width = (
        raster.bounds.right
        - raster.bounds.left
    )

    # Choose sensible scale length.
    candidates = [
        10,
        20,
        50,
        100,
        200,
        500,
        1000,
    ]

    scale_m = candidates[0]

    for candidate in candidates:

        if (
            candidate
            / raster_width
            * width
            < 250
        ):

            scale_m = candidate

    bar_width = (
        scale_m
        / raster_width
        * width
    )

    x1 = 40
    y1 = height - 70
    x2 = x1 + bar_width
    y2 = y1 + 20

    draw.rectangle(
        [
            x1,
            y1,
            x2,
            y2,
        ],
        fill="white",
        outline="black",
        width=2,
    )

    draw.rectangle(
        [
            x1 + 4,
            y1 + 4,
            x2 - 4,
            y2 - 4,
        ],
        fill="black",
    )

    label = (
        f"{scale_m:g} m"
    )

    bbox = draw.textbbox(
        (0, 0),
        label,
        font=font,
    )

    text_width = (
        bbox[2] - bbox[0]
    )

    draw.text(
        (
            x1
            + (
                bar_width
                - text_width
            ) / 2,
            y1 + 25,
        ),
        label,
        fill="black",
        font=font,
    )


# ============================================================
# RENDER HISTORICAL PAGE
# ============================================================


def render_historical_page(
    tif_path,
    geometry_utm,
    county,
    parcel_id,
    year,
    output_path,
    source_text="SOURCE: USDA NAIP (PLANETARY COMPUTER)",
):
    """
    Render the downloaded historical aerial image
    into a page-like PNG.
    """

    print()
    print("=" * 70)
    print(
        f"RENDERING HISTORICAL AERIAL {year}"
    )
    print("=" * 70)

    image = tiff_to_rgb(
        tif_path
    )

    # --------------------------------------------------------
    # Open raster for georeferencing
    # --------------------------------------------------------

    with rasterio.open(
        tif_path
    ) as raster:

        points = geometry_to_pixel_points(
            geometry_utm,
            raster,
            image.width,
            image.height,
        )

        # ----------------------------------------------------
        # Boundary
        # ----------------------------------------------------

        draw = ImageDraw.Draw(
            image
        )

        for ring in points:

            if len(ring) >= 2:

                draw.line(
                    ring,
                    fill="red",
                    width=SITE_LINE_WIDTH,
                    joint="curve",
                )

        # ----------------------------------------------------
        # Site location
        # ----------------------------------------------------

        centroid = geometry_utm.centroid

        site_points = geometry_to_pixel_points(
            centroid,
            raster,
            image.width,
            image.height,
        )

        # Easier direct coordinate conversion.
        cx = (
            (
                centroid.x
                - raster.bounds.left
            )
            /
            (
                raster.bounds.right
                - raster.bounds.left
            )
            * image.width
        )

        cy = (
            (
                raster.bounds.top
                - centroid.y
            )
            /
            (
                raster.bounds.top
                - raster.bounds.bottom
            )
            * image.height
        )

        label_font = get_font(
            25,
            bold=True
        )

        label = "SITE LOCATION"

        bbox = draw.textbbox(
            (0, 0),
            label,
            font=label_font,
        )

        label_width = (
            bbox[2] - bbox[0]
        )

        label_height = (
            bbox[3] - bbox[1]
        )

        label_x = min(
            image.width
            - label_width
            - 80,
            max(
                80,
                int(cx + 180)
            )
        )

        label_y = max(
            80,
            int(cy - 180)
        )

        # ----------------------------------------------------
        # Label box
        # ----------------------------------------------------

        padding = 8

        draw.rounded_rectangle(
            [
                label_x - padding,
                label_y - padding,
                label_x
                + label_width
                + padding,
                label_y
                + label_height
                + padding,
            ],
            radius=6,
            fill="white",
            outline="black",
            width=2,
        )

        draw.text(
            (
                label_x,
                label_y,
            ),
            label,
            fill="black",
            font=label_font,
        )

        # ----------------------------------------------------
        # Arrow from label to site
        # ----------------------------------------------------

        start_x = (
            label_x
            + label_width / 2
        )

        start_y = (
            label_y
            + label_height
            + 10
        )

        end_x = cx
        end_y = cy

        draw.line(
            [
                (
                    start_x,
                    start_y
                ),
                (
                    end_x,
                    end_y
                ),
            ],
            fill="black",
            width=4,
        )

        # Arrow head
        angle = math.atan2(
            end_y - start_y,
            end_x - start_x,
        )

        arrow_length = 18
        arrow_angle = math.radians(
            28
        )

        p1 = (
            end_x,
            end_y
        )

        p2 = (
            end_x
            - arrow_length
            * math.cos(
                angle
                - arrow_angle
            ),
            end_y
            - arrow_length
            * math.sin(
                angle
                - arrow_angle
            ),
        )

        p3 = (
            end_x
            - arrow_length
            * math.cos(
                angle
                + arrow_angle
            ),
            end_y
            - arrow_length
            * math.sin(
                angle
                + arrow_angle
            ),
        )

        draw.polygon(
            [
                p1,
                p2,
                p3,
            ],
            fill="black",
        )

        # ----------------------------------------------------
        # North arrow
        # ----------------------------------------------------

        draw_north_arrow(
            image,
            100,
            100,
        )

        # ----------------------------------------------------
        # Scale bar
        # ----------------------------------------------------

        draw_scale_bar(
            image,
            raster,
        )

    # ========================================================
    # PAGE CANVAS
    # ========================================================

    page_width = 1800
    page_height = 2300

    page = Image.new(
        "RGB",
        (
            page_width,
            page_height,
        ),
        "white",
    )

    # Map dimensions
    map_margin = 80

    map_width = (
        page_width
        - map_margin * 2
    )

    map_height = 1750

    image.thumbnail(
        (
            map_width,
            map_height,
        ),
        Image.Resampling.LANCZOS,
    )

    map_x = (
        page_width
        - image.width
    ) // 2

    map_y = 60

    page.paste(
        image,
        (
            map_x,
            map_y,
        ),
    )
    # --------------------------------------------------------
    # Map drawing
    # --------------------------------------------------------

    draw = ImageDraw.Draw(
        page
    )
    # --------------------------------------------------------
    # YEAR BOX — OVER AERIAL IMAGE
    # --------------------------------------------------------

    year_text = str(year)

    year_font = get_font(
        45,
        bold=True
    )

    year_bbox = draw.textbbox(
        (0, 0),
        year_text,
        font=year_font,
    )

    year_width = (
        year_bbox[2] - year_bbox[0]
    )

    year_height = (
        year_bbox[3] - year_bbox[1]
    )

    year_padding_x = 25
    year_padding_y = 15

    year_box_x = (
        map_x + 20
    )

    year_box_y = (
        map_y + 20
    )

    draw.rounded_rectangle(
        [
            year_box_x,
            year_box_y,
            year_box_x
            + year_width
            + year_padding_x * 2,
            year_box_y
            + year_height
            + year_padding_y * 2,
        ],
        radius=6,
        fill="white",
        outline="black",
        width=3,
    )

    draw.text(
        (
            year_box_x
            + year_padding_x,
            year_box_y
            + year_padding_y
            - year_bbox[1],
        ),
        year_text,
        fill="black",
        font=year_font,
    )

    # --------------------------------------------------------
    # Map border
    # --------------------------------------------------------

    draw = ImageDraw.Draw(
        page
    )

    draw.rectangle(
        [
            map_x,
            map_y,
            map_x + image.width,
            map_y + image.height,
        ],
        outline="black",
        width=4,
    )

    # --------------------------------------------------------
    # Information panel
    # --------------------------------------------------------

    panel_y = (
        map_y
        + image.height
        + 30
    )

    draw.line(
        [
            (
                map_margin,
                panel_y
            ),
            (
                page_width
                - map_margin,
                panel_y
            ),
        ],
        fill="black",
        width=4,
    )

    title_font = get_font(
        42,
        bold=True
    )

    heading_font = get_font(
        25,
        bold=True
    )

    normal_font = get_font(
        23
    )

    # --------------------------------------------------------
    # Title
    # --------------------------------------------------------

    title = (
        f"{county.upper()} "
        f"PARCEL {parcel_id}"
    )

    draw.text(
        (
            map_margin,
            panel_y + 25,
        ),
        title,
        fill="black",
        font=title_font,
    )

    # --------------------------------------------------------
    # Aerial / year
    # --------------------------------------------------------

    aerial_text = "AERIAL"

    bbox = draw.textbbox(
        (0, 0),
        aerial_text,
        font=heading_font,
    )

    aerial_width = (
        bbox[2] - bbox[0]
    )

    draw.text(
        (
            page_width
            - map_margin
            - aerial_width,
            panel_y + 30,
        ),
        aerial_text,
        fill="black",
        font=heading_font,
    )

    # --------------------------------------------------------
    # Year
    # --------------------------------------------------------

    draw.text(
        (
            map_margin,
            panel_y + 85,
        ),
        f"YEAR: {year}",
        fill="black",
        font=normal_font,
    )

    # --------------------------------------------------------
    # Source
    # --------------------------------------------------------

    # Source is supplied by the downloader so the page records
    # the actual source used for that year.
    source_text = source_text

    bbox = draw.textbbox(
        (0, 0),
        source_text,
        font=normal_font,
    )

    source_width = (
        bbox[2] - bbox[0]
    )

    

    # --------------------------------------------------------
    # Parcel
    # --------------------------------------------------------

    draw.text(
        (
            map_margin,
            panel_y + 135,
        ),
        f"PARCEL ID: {parcel_id}",
        fill="black",
        font=normal_font,
    )

    # --------------------------------------------------------
    # Historical label
    # --------------------------------------------------------

    draw.text(
        (
            map_margin,
            panel_y + 185,
        ),
        "HISTORICAL AERIAL PHOTOGRAPH",
        fill="black",
        font=heading_font,
    )

    # --------------------------------------------------------
    # Save
    # --------------------------------------------------------

    page.save(
        output_path,
        "PNG",
    )

    print(
        "Rendered:",
        output_path,
    )

    return output_path


# ============================================================
# COMBINE PNGs INTO PDF
# ============================================================


def combine_pages_to_pdf(
    pages,
    output_pdf,
):
    """
    Combine rendered pages in newest -> oldest order.
    """

    print()
    print("=" * 70)
    print("CREATING COMBINED HISTORICAL PDF")
    print("=" * 70)

    if not pages:

        raise RuntimeError(
            "No rendered historical pages "
            "are available."
        )

    c = canvas.Canvas(
        str(output_pdf),
        pagesize=letter,
    )

    for page_path in pages:

        image = Image.open(
            page_path
        )

        image = image.convert(
            "RGB"
        )

        temp = io.BytesIO()

        image.save(
            temp,
            format="JPEG",
            quality=95,
        )

        temp.seek(0)

        c.drawImage(
            ImageReader(temp),
            0,
            0,
            width=PAGE_WIDTH,
            height=PAGE_HEIGHT,
            preserveAspectRatio=True,
            anchor="c",
        )

        c.showPage()

    c.save()

    print(
        "Combined PDF:",
        output_pdf,
    )

    return output_pdf


# ============================================================
# MANIFEST
# ============================================================


def write_manifest(
    county,
    parcel_id,
    available_years,
    missing_years,
    pages,
    downloaded,
):
    manifest = {
        "created_at": datetime.utcnow().isoformat()
        + "Z",

        "county": county,

        "parcel_id": parcel_id,

        "start_year": START_YEAR,

        "available": [
            int(y)
            for y in sorted(
                available_years,
                reverse=True,
            )
        ],

        "missing": [
            int(y)
            for y in sorted(
                missing_years,
                reverse=True,
            )
        ],

        "pages": [
            str(Path(p).resolve())
            for p in pages
        ],

        "downloads": downloaded,

        "combined_pdf": str(
            COMBINED_PDF.resolve()
        ),
    }

    with open(
        MANIFEST_FILE,
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            manifest,
            f,
            indent=2,
        )

    print(
        "Manifest:",
        MANIFEST_FILE,
    )

    return manifest


def generate_historical_aerial(
    county,
    parcel_ids=None,
    coords=None,
    project_number=None,
):
    """
    Generate the historical aerial series for a site.

    Returns the historical manifest.
    """

    site = resolve_parcel(
        county=county,
        parcel_ids=parcel_ids,
        coords=coords,
        project_number=project_number
    )
    
    geometry_project = get_site_geometry(
        site
    )
    resolved_parcel_id = site["parcel_ids"][0]
    project_crs = PROJECT_CRS

    export_bbox = make_export_extent(
        geometry_project,
        scale_factor=5.0,
    )

    # Search Planetary Computer
    pc_records = search_all_naip_records(
        export_bbox,
        project_crs,
    )

    pc_grouped = group_records_by_year(
        pc_records
    )

    # EarthExplorer manual records
    ee_records = get_earth_explorer_records()

    all_records_by_year = {}

    for year, records in pc_grouped.items():
        all_records_by_year.setdefault(
            year,
            []
        ).extend(records)

    for record in ee_records:
        all_records_by_year.setdefault(
            int(record["year"]),
            []
        ).append(record)

    available_years = sorted(
        all_records_by_year.keys(),
        reverse=True,
    )

    expected_years = set(
        range(
            START_YEAR,
            END_YEAR + 1,
        )
    )

    missing_years = sorted(
        expected_years
        - set(available_years),
        reverse=True,
    )

    rendered_pages = []
    downloaded_info = []

    for year in available_years:

        record = select_record_for_year(
            all_records_by_year[year],
            year,
        )

        if record is None:
            continue

        try:

            if record.get("source") == (
                "USDA NAIP (Planetary Computer)"
            ):

                tif_path = download_naip_record(
                    record=record,
                    export_bbox=export_bbox,
                    crs=project_crs,
                    year=year,
                )

                page_source = (
                    "SOURCE: USDA NAIP "
                    "(PLANETARY COMPUTER)"
                )

            else:

                tif_path = Path(
                    record["path"]
                )

                page_source = (
                    "SOURCE: USGS EARTH EXPLORER"
                )

            rendered_path = (
                RENDER_DIR
                / f"historical_aerial_{year}.png"
            )

            render_historical_page(
                tif_path=tif_path,
                geometry_utm=geometry_project,
                county=county,
                parcel_id=resolved_parcel_id,
                year=year,
                output_path=rendered_path,
                source_text=page_source,
            )

            rendered_pages.append(
                rendered_path
            )

            downloaded_info.append(
                {
                    "year": year,
                    "source": record.get("source"),
                    "item_id": record.get("id"),
                    "datetime": record.get("datetime"),
                    "gsd": record.get("gsd"),
                    "tiff": str(
                        Path(tif_path).resolve()
                    ),
                    "rendered": str(
                        rendered_path.resolve()
                    ),
                    "status": "success",
                }
            )

        except Exception as exc:

            downloaded_info.append(
                {
                    "year": year,
                    "source": record.get("source"),
                    "item_id": record.get("id"),
                    "status": "failed",
                    "error": str(exc),
                }
            )

            if year not in missing_years:
                missing_years.append(year)

    missing_years = sorted(
        set(missing_years),
        reverse=True,
    )

    if rendered_pages:

        combine_pages_to_pdf(
            rendered_pages,
            COMBINED_PDF,
        )

    manifest = write_manifest(
        county=county,
        parcel_id=resolved_parcel_id,
        available_years=available_years,
        missing_years=missing_years,
        pages=rendered_pages,
        downloaded=downloaded_info,
    )

    return manifest


# ============================================================
# MAIN
# ============================================================
def main():

    COUNTY = "Douglas County, NE"

    PARCELS = [
        "0100370012"
    ]

    generate_historical_aerial(
        county=COUNTY,
        parcel_ids=PARCELS,
    )