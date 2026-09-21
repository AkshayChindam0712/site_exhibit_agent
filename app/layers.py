# ============================================================
# app/layers.py
# ============================================================

from pathlib import Path
import io
import json

import requests
import rasterio

from PIL import Image
from pyproj import Transformer


# ============================================================
# CONFIGURATION
# ============================================================

CACHE_DIR = Path("cache") / "layers"
CACHE_DIR.mkdir(
    parents=True,
    exist_ok=True,
)

PROJECT_CRS = "EPSG:32614"
WGS84_CRS = "EPSG:4326"
USGS_CRS = "EPSG:3857"

REQUEST_TIMEOUT = 120

TOPO_BUFFER_METRES = 750
TOPO_MAX_PIXELS = 3000

# Keep the existing NAIP export size.
# We can tune this separately for image quality.
NAIP_WIDTH = 2400
NAIP_HEIGHT = 2400

NAIP_MIN_BYTES = 10000


# ============================================================
# USGS SERVICES
# ============================================================

USGS_TOPO_EXPORT_URL = (
    "https://basemap.nationalmap.gov/"
    "arcgis/rest/services/"
    "USGSTopo/MapServer/export"
)

NAIP_BASE_URL = (
    "https://imagery.nationalmap.gov/"
    "arcgis/rest/services/"
    "USGSNAIPImagery/"
    "ImageServer"
)

NAIP_QUERY_URL = (
    NAIP_BASE_URL
    + "/query"
)

NAIP_EXPORT_URL = (
    NAIP_BASE_URL
    + "/exportImage"
)


# ============================================================
# CRS TRANSFORMERS
# ============================================================

_UTM_TO_WGS84 = Transformer.from_crs(
    PROJECT_CRS,
    WGS84_CRS,
    always_xy=True,
)

_UTM_TO_WEB = Transformer.from_crs(
    PROJECT_CRS,
    USGS_CRS,
    always_xy=True,
)


# ============================================================
# BBOX
# ============================================================

def _normalise_bbox(bbox):
    """
    Normalize bbox into:

        minx, miny, maxx, maxy

    Project CRS:
        EPSG:32614
    """

    if bbox is None:
        raise ValueError(
            "Bounding box is required."
        )

    if isinstance(bbox, dict):

        minx = float(
            bbox["minx"]
        )

        miny = float(
            bbox["miny"]
        )

        maxx = float(
            bbox["maxx"]
        )

        maxy = float(
            bbox["maxy"]
        )

    else:

        if len(bbox) != 4:
            raise ValueError(
                "Bounding box must contain "
                "(minx, miny, maxx, maxy)."
            )

        minx, miny, maxx, maxy = map(
            float,
            bbox,
        )

    if minx > maxx:
        minx, maxx = maxx, minx

    if miny > maxy:
        miny, maxy = maxy, miny

    if minx == maxx:
        raise ValueError(
            "Bounding box has zero width."
        )

    if miny == maxy:
        raise ValueError(
            "Bounding box has zero height."
        )

    return (
        minx,
        miny,
        maxx,
        maxy,
    )


# ============================================================
# BBOX EXPANSION
# ============================================================

def _expand_bbox(
    bbox,
    buffer_x,
    buffer_y=None,
):
    """
    Expand a bbox by the supplied number of metres.
    """

    minx, miny, maxx, maxy = (
        _normalise_bbox(bbox)
    )

    if buffer_y is None:
        buffer_y = buffer_x

    return (
        minx - float(buffer_x),
        miny - float(buffer_y),
        maxx + float(buffer_x),
        maxy + float(buffer_y),
    )


# ============================================================
# CRS HELPERS
# ============================================================

def _utm_to_wgs84(bbox):
    """
    Convert UTM bbox to WGS84.
    """

    minx, miny, maxx, maxy = (
        _normalise_bbox(bbox)
    )

    x1, y1 = _UTM_TO_WGS84.transform(
        minx,
        miny,
    )

    x2, y2 = _UTM_TO_WGS84.transform(
        maxx,
        maxy,
    )

    return (
        min(x1, x2),
        min(y1, y2),
        max(x1, x2),
        max(y1, y2),
    )


def _utm_to_web_mercator(bbox):
    """
    Convert UTM bbox to Web Mercator.
    """

    minx, miny, maxx, maxy = (
        _normalise_bbox(bbox)
    )

    x1, y1 = _UTM_TO_WEB.transform(
        minx,
        miny,
    )

    x2, y2 = _UTM_TO_WEB.transform(
        maxx,
        maxy,
    )

    return (
        min(x1, x2),
        min(y1, y2),
        max(x1, x2),
        max(y1, y2),
    )


# ============================================================
# TOPO IMAGE VALIDATION
# ============================================================

def _verify_topo_image(path):
    """
    Verify that the downloaded topo image is usable.
    """

    try:

        with Image.open(path) as image:

            image.load()

            if image.width <= 0:
                return False

            if image.height <= 0:
                return False

            extrema = image.getextrema()

            if isinstance(extrema, tuple):

                if all(
                    isinstance(channel, tuple)
                    and channel[0] == channel[1]
                    for channel in extrema
                ):
                    return False

                if (
                    len(extrema) == 2
                    and extrema[0] == extrema[1]
                ):
                    return False

            return True

    except Exception:

        return False


# ============================================================
# TOPO DOWNLOAD
# ============================================================

def _download_topo_image(
    bbox,
):
    """
    Download USGS Topo background.

    Tries progressively smaller image sizes because
    the USGS export service may return HTTP 504 for
    large requests.
    """

    minx, miny, maxx, maxy = (
        _normalise_bbox(bbox)
    )

    cache_dir = CACHE_DIR

    cache_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    cache_name = (
        f"topo_"
        f"{minx:.6f}_"
        f"{miny:.6f}_"
        f"{maxx:.6f}_"
        f"{maxy:.6f}.png"
    )

    cache_path = (
        cache_dir / cache_name
    )

    # --------------------------------------------------------
    # CACHE
    # --------------------------------------------------------

    if cache_path.exists():

        if _verify_topo_image(
            cache_path
        ):

            print(
                "Using cached topo layer:",
                cache_path,
            )

            return {
                "path": str(cache_path),
                "kind": "topo",
                "year": None,
                "source": "USGS Topo",
                "bbox": (
                    minx,
                    miny,
                    maxx,
                    maxy,
                ),
                "crs": PROJECT_CRS,
                "image_crs": USGS_CRS,
            }

        cache_path.unlink(
            missing_ok=True
        )

    # --------------------------------------------------------
    # CONVERT UTM BBOX TO WEB MERCATOR
    # --------------------------------------------------------

    web_bbox = _utm_to_web_mercator(
        (
            minx,
            miny,
            maxx,
            maxy,
        )
    )

    web_minx, web_miny, web_maxx, web_maxy = (
        web_bbox
    )

    # --------------------------------------------------------
    # FALLBACK OUTPUT SIZES
    # --------------------------------------------------------
    #
    # Large requests can timeout on the USGS server.
    # Try high resolution first, then progressively
    # smaller requests.
    #

    requested_max = min(
        TOPO_MAX_PIXELS,
        4000,
    )

    sizes = [
        requested_max,
        3000,
        2400,
        2000,
        1600,
    ]

    # Remove duplicates while preserving order
    sizes = list(
        dict.fromkeys(sizes)
    )

    last_error = None

    # --------------------------------------------------------
    # TRY EACH SIZE
    # --------------------------------------------------------

    for image_size in sizes:

        print()
        print("=" * 60)
        print("DOWNLOADING USGS TOPO")
        print("=" * 60)

        print(
            "OUTPUT SIZE:",
            f"{image_size} x {image_size}",
        )

        params = {

            "bbox": (
                f"{web_minx},"
                f"{web_miny},"
                f"{web_maxx},"
                f"{web_maxy}"
            ),

            "bboxSR": "3857",

            "imageSR": "3857",

            "size": (
                f"{image_size},"
                f"{image_size}"
            ),

            "format": "png32",

            "transparent": "false",

            "f": "image",
        }

        try:

            response = requests.get(
                USGS_TOPO_EXPORT_URL,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

            print(
                "Topo HTTP status:",
                response.status_code,
            )

            # ------------------------------------------------
            # SERVER ERROR
            # ------------------------------------------------

            if response.status_code != 200:

                print(
                    "Topo export failed."
                )

                print(
                    "Trying next size..."
                )

                last_error = requests.HTTPError(
                    f"{response.status_code} "
                    f"Server Error for URL: "
                    f"{response.url}"
                )

                continue

            # ------------------------------------------------
            # CONTENT TYPE
            # ------------------------------------------------

            content_type = (
                response.headers.get(
                    "Content-Type",
                    "",
                ).lower()
            )

            print(
                "Topo Content-Type:",
                content_type,
            )

            if "image" not in content_type:

                print(
                    "USGS Topo did not return an image."
                )

                print(
                    "Trying next size..."
                )

                last_error = ValueError(
                    "USGS Topo service did not "
                    "return an image. "
                    f"Content-Type: {content_type}"
                )

                continue

            # ------------------------------------------------
            # EMPTY RESPONSE
            # ------------------------------------------------

            if not response.content:

                print(
                    "USGS Topo returned empty response."
                )

                print(
                    "Trying next size..."
                )

                last_error = ValueError(
                    "USGS Topo returned "
                    "an empty response."
                )

                continue

            # ------------------------------------------------
            # OPEN IMAGE
            # ------------------------------------------------

            try:

                image = Image.open(
                    io.BytesIO(
                        response.content
                    )
                )

                image.load()

            except Exception as exc:

                print(
                    "Invalid Topo image:",
                    exc,
                )

                print(
                    "Trying next size..."
                )

                last_error = ValueError(
                    "USGS Topo returned invalid "
                    f"image data: {exc}"
                )

                continue

            print(
                "USGS returned image:",
                image.size,
            )

            # ------------------------------------------------
            # CONVERT TO RGB
            # ------------------------------------------------

            image = image.convert(
                "RGB"
            )

            extrema = image.getextrema()

            print(
                "USGS RGB extrema:",
                extrema,
            )

            # ------------------------------------------------
            # UNIFORM IMAGE CHECK
            # ------------------------------------------------

            if all(
                minimum == maximum
                for minimum, maximum in extrema
            ):

                print(
                    "USGS Topo returned a "
                    "completely uniform image."
                )

                print(
                    "Trying next size..."
                )

                last_error = ValueError(
                    "USGS Topo returned a "
                    "completely uniform image."
                )

                continue

            # ------------------------------------------------
            # SAVE
            # ------------------------------------------------

            image.save(
                cache_path,
                format="PNG",
                optimize=True,
            )

            # ------------------------------------------------
            # VALIDATE SAVED IMAGE
            # ------------------------------------------------

            if not _verify_topo_image(
                cache_path
            ):

                print(
                    "Saved Topo image failed validation."
                )

                cache_path.unlink(
                    missing_ok=True
                )

                print(
                    "Trying next size..."
                )

                last_error = ValueError(
                    "Saved USGS Topo image "
                    "failed validation."
                )

                continue

            # ------------------------------------------------
            # SUCCESS
            # ------------------------------------------------

            print()
            print(
                "SUCCESS: USGS TOPO"
            )

            print(
                "Saved USGS Topo layer:",
                cache_path,
            )

            return {
                "path": str(cache_path),
                "kind": "topo",
                "year": None,
                "source": "USGS Topo",
                "bbox": (
                    minx,
                    miny,
                    maxx,
                    maxy,
                ),
                "crs": PROJECT_CRS,
                "image_crs": USGS_CRS,
            }

        except requests.RequestException as exc:

            print(
                "Topo request failed:",
                exc,
            )

            print(
                "Trying next size..."
            )

            last_error = exc

    # --------------------------------------------------------
    # ALL ATTEMPTS FAILED
    # --------------------------------------------------------

    if last_error is not None:
        raise last_error

    raise RuntimeError(
        "USGS Topo export failed for all "
        "requested output sizes."
    )

# ============================================================
# VALIDATE NAIP TIFF
# ============================================================

def _validate_naip_tiff(
    path,
    year,
):
    """
    Validate downloaded NAIP GeoTIFF.
    """

    path = Path(path)

    if not path.exists():

        raise FileNotFoundError(
            f"NAIP TIFF does not exist: {path}"
        )

    if path.stat().st_size < NAIP_MIN_BYTES:

        raise ValueError(
            f"NAIP {year} TIFF is too small: "
            f"{path.stat().st_size:,} bytes"
        )

    with rasterio.open(
        path
    ) as src:

        print()
        print(
            f"VALIDATING NAIP {year}"
        )

        print(
            "CRS:",
            src.crs,
        )

        print(
            "SIZE:",
            src.width,
            "x",
            src.height,
        )

        print(
            "BANDS:",
            src.count,
        )

        print(
            "BOUNDS:",
            src.bounds,
        )

        if src.width <= 0:
            raise ValueError(
                "Raster width is invalid."
            )

        if src.height <= 0:
            raise ValueError(
                "Raster height is invalid."
            )

        if src.count <= 0:
            raise ValueError(
                "Raster contains no bands."
            )

        if src.crs is None:

            raise ValueError(
                "Raster has no CRS."
            )

        if src.width < 100:
            raise ValueError(
                "Raster width is suspiciously small."
            )

        if src.height < 100:
            raise ValueError(
                "Raster height is suspiciously small."
            )

        sample = src.read(
            1,
            out_shape=(
                1,
                min(
                    src.height,
                    500,
                ),
                min(
                    src.width,
                    500,
                ),
            ),
        )

        if sample.size == 0:

            raise ValueError(
                "Raster contains no pixels."
            )

        try:

            pixel_min = sample.min()
            pixel_max = sample.max()

            print(
                "Pixel min:",
                pixel_min,
            )

            print(
                "Pixel max:",
                pixel_max,
            )

            if pixel_min == pixel_max:

                raise ValueError(
                    "Raster sample is completely uniform."
                )

        except Exception:

            pass


# ============================================================
# DOWNLOAD EXACT NAIP RECORD
# ============================================================

def _download_naip_record(
    bbox,
    record,
):
    """
    Download the exact NAIP raster selected
    from the catalog.

    The OBJECTID is locked using mosaicRule.
    """

    minx, miny, maxx, maxy = (
        _normalise_bbox(bbox)
    )

    objectid = int(
        record["OBJECTID"]
    )

    year = int(
        record["Year"]
    )

    raster_name = (
        record.get(
            "raster_name"
        )
        or record.get(
            "Name"
        )
        or ""
    )

    acquisition_date = (
        record.get(
            "acquisition_date"
        )
    )

    # --------------------------------------------------------
    # Cache filename
    # --------------------------------------------------------

    cache_name = (
        f"naip_{year}_"
        f"{objectid}_"
        f"{minx:.6f}_"
        f"{miny:.6f}_"
        f"{maxx:.6f}_"
        f"{maxy:.6f}.tif"
    )

    cache_path = (
        CACHE_DIR / cache_name
    )

    # --------------------------------------------------------
    # CACHE CHECK
    # --------------------------------------------------------

    if cache_path.exists():

        print()
        print("=" * 70)
        print(
            f"EXACT NAIP {year} FOUND IN CACHE"
        )
        print("=" * 70)

        print(
            "OBJECTID:",
            objectid,
        )

        print(
            "FILE:",
            cache_path,
        )

        try:

            _validate_naip_tiff(
                cache_path,
                year,
            )

            print(
                f"SUCCESS: CACHED NAIP {year}"
            )

            return {
                "path": str(cache_path),
                "kind": "aerial",
                "year": year,
                "source": "cache",
                "bbox": (
                    minx,
                    miny,
                    maxx,
                    maxy,
                ),
                "crs": PROJECT_CRS,
                "objectid": objectid,
                "raster_name": raster_name,
                "acquisition_date":
                    acquisition_date,
            }

        except Exception as exc:

            print(
                "Cached NAIP file is invalid:"
            )

            print(
                exc
            )

            cache_path.unlink(
                missing_ok=True
            )

    # --------------------------------------------------------
    # EXPORT EXTENT
    # --------------------------------------------------------
    #
    # Keep a little surrounding imagery so the
    # rendered map does not have a hard edge.
    #

    site_width = maxx - minx
    site_height = maxy - miny

    padding_x = site_width * 3.0
    padding_y = site_height * 3.0

    export_minx = (
        minx - padding_x
    )

    export_miny = (
        miny - padding_y
    )

    export_maxx = (
        maxx + padding_x
    )

    export_maxy = (
        maxy + padding_y
    )

    # --------------------------------------------------------
    # LOCK RASTER
    # --------------------------------------------------------

    mosaic_rule = {
        "mosaicMethod": "lockRaster",
        "lockRasterIds": [
            objectid
        ],
    }

    # --------------------------------------------------------
    # EXPORT PARAMETERS
    # --------------------------------------------------------

    params = {

        "bbox": (
            f"{export_minx},"
            f"{export_miny},"
            f"{export_maxx},"
            f"{export_maxy}"
        ),

        "bboxSR": "32614",

        "imageSR": "32614",

        "size": (
            f"{NAIP_WIDTH},"
            f"{NAIP_HEIGHT}"
        ),

        "format": "tiff",

        "pixelType": "U8",

        # Keep the existing interpolation for now.
        # We can optimize this separately.
        "interpolation":
            "RSP_Billinear",

        "mosaicRule":
            json.dumps(
                mosaic_rule
            ),

        "f": "image",
    }

    print()
    print("=" * 70)
    print(
        f"DOWNLOADING EXACT NAIP {year}"
    )
    print("=" * 70)

    print(
        "Export URL:",
        NAIP_EXPORT_URL,
    )

    print(
        "Export BBOX:",
        (
            export_minx,
            export_miny,
            export_maxx,
            export_maxy,
        ),
    )

    print(
        "OBJECTID:",
        objectid,
    )

    print(
        "RASTER:",
        raster_name,
    )

    print(
        "MOSAIC RULE:",
        mosaic_rule,
    )

    print(
        "OUTPUT SIZE:",
        f"{NAIP_WIDTH} x {NAIP_HEIGHT}",
    )

    response = requests.get(
        NAIP_EXPORT_URL,
        params=params,
        timeout=REQUEST_TIMEOUT,
    )

    print(
        "HTTP status:",
        response.status_code,
    )

    response.raise_for_status()

    content_type = (
        response.headers.get(
            "Content-Type",
            "",
        ).lower()
    )

    print(
        "Content-Type:",
        content_type,
    )

    if "image" not in content_type:

        # Sometimes ArcGIS returns JSON error
        # with HTTP 200.
        try:
            text = response.text[:1000]
        except Exception:
            text = ""

        raise ValueError(
            "NAIP service did not return "
            f"an image. Content-Type: "
            f"{content_type}. Response: {text}"
        )

    content = response.content

    print(
        "Downloaded:",
        f"{len(content):,}",
        "bytes",
    )

    if len(content) < NAIP_MIN_BYTES:

        raise ValueError(
            f"NAIP {year} response is "
            "suspiciously small."
        )

    # --------------------------------------------------------
    # TEMP FILE
    # --------------------------------------------------------

    temp_path = (
        cache_path.with_suffix(
            ".download.tif"
        )
    )

    temp_path.unlink(
        missing_ok=True
    )

    with open(
        temp_path,
        "wb",
    ) as file:

        file.write(
            content
        )

    # --------------------------------------------------------
    # VALIDATE
    # --------------------------------------------------------

    try:

        _validate_naip_tiff(
            temp_path,
            year,
        )

    except Exception as exc:

        temp_path.unlink(
            missing_ok=True
        )

        raise ValueError(
            f"Downloaded NAIP {year} "
            f"failed validation: {exc}"
        ) from exc

    # --------------------------------------------------------
    # SAVE
    # --------------------------------------------------------

    temp_path.replace(
        cache_path
    )

    print()
    print(
        f"NAIP {year} download successful."
    )

    print(
        "Saved:",
        cache_path,
    )

    return {
        "path": str(cache_path),

        "kind": "aerial",

        "year": year,

        "source": "cache",

        "bbox": (
            minx,
            miny,
            maxx,
            maxy,
        ),

        "crs": PROJECT_CRS,

        "objectid": objectid,

        "raster_name":
            raster_name,

        "acquisition_date":
            acquisition_date,
    }


# ============================================================
# GET ONE AERIAL YEAR
# ============================================================

def _get_aerial_layer(
    bbox,
    year=2026,
    records=None,
):
    """
    Get one exact-year NAIP image.

    Cache is checked BEFORE the catalog so that a cached
    image requires no network request.
    """

    bbox = _normalise_bbox(bbox)
    year = int(year)

    # ========================================================
    # CHECK CACHE FIRST
    # ========================================================

    cached_files = sorted(
        CACHE_DIR.glob(
            f"naip_{year}_*.tif"
        )
    )

    for cache_path in cached_files:

        try:
            _validate_naip_tiff(
                cache_path,
                year,
            )

            print()
            print("=" * 70)
            print(
                f"USING CACHED NAIP {year}"
            )
            print("=" * 70)

            print(
                "FILE:",
                cache_path,
            )

            # Extract OBJECTID from:
            # naip_YEAR_OBJECTID_minx_miny_maxx_maxy.tif
            parts = cache_path.stem.split("_")

            objectid = None

            if len(parts) >= 3:
                try:
                    objectid = int(parts[2])
                except ValueError:
                    pass

            return {
                "path": str(cache_path),
                "kind": "aerial",
                "year": year,
                "source": "cache",
                "bbox": bbox,
                "crs": PROJECT_CRS,
                "objectid": objectid,
            }

        except Exception as exc:

            print(
                "Cached NAIP file is invalid:",
                cache_path,
            )

            print(
                "Reason:",
                exc,
            )

            cache_path.unlink(
                missing_ok=True
            )

    # ========================================================
    # NO VALID CACHE → QUERY CATALOG
    # ========================================================

    print()
    print("=" * 70)
    print(
        f"SEARCHING NAIP CATALOG FOR {year}"
    )
    print("=" * 70)

    record = _find_naip_record(
        bbox,
        year,
        records=records,
    )

    if int(
        record["Year"]
    ) != year:

        raise ValueError(
            f"Requested NAIP {year}, "
            f"but selected {record['Year']}."
        )

    print()
    print(
        f"SELECTED NAIP {year} RECORD"
    )

    print(
        "OBJECTID:",
        record.get("OBJECTID"),
    )

    print(
        "Raster:",
        record.get("raster_name")
        or record.get("Name"),
    )

    print(
        "Acquisition:",
        record.get("acquisition_date"),
    )

    return _download_naip_record(
        bbox,
        record,
    )

# ============================================================
# PUBLIC GET LAYER
# ============================================================

def get_layer(
    kind,
    bbox,
    year=None,
):
    """
    Retrieve a map layer.

    Supported:

        aerial
        naip
        topo

    For aerial/naip, year must be supplied
    unless the caller explicitly wants the
    default latest workflow.
    """

    kind = str(
        kind
    ).strip().lower()

    bbox = _normalise_bbox(
        bbox
    )

    # --------------------------------------------------------
    # AERIAL
    # --------------------------------------------------------

    if kind in (
        "aerial",
        "naip",
    ):

        if year is None:

            # Preserve compatibility with existing
            # callers while avoiding hidden hard-coding
            # in the series downloader.
            year = 2022

        return _get_aerial_layer(
            bbox,
            year=int(year),
        )

    # --------------------------------------------------------
    # TOPO
    # --------------------------------------------------------

    if kind == "topo":

        expanded_bbox = _expand_bbox(
            bbox,
            TOPO_BUFFER_METRES,
        )

        print(
            "Topo expanded bbox:",
            expanded_bbox,
        )

        return _download_topo_image(
            expanded_bbox
        )

    # --------------------------------------------------------
    # UNKNOWN
    # --------------------------------------------------------

    raise ValueError(
        f"Unsupported layer kind: {kind}"
    )


# ============================================================
# CONVENIENCE
# ============================================================

def get_aerial_layer(
    bbox,
    year=None,
):
    """
    Convenience wrapper for exact-year NAIP.
    """

    if year is None:

        year = 2022

    return get_layer(
        "aerial",
        bbox,
        year=year,
    )


# ============================================================
# TOPO COMPATIBILITY
# ============================================================

def _get_topo_layer(
    bbox,
):
    """
    Compatibility wrapper.
    """

    return _download_topo_image(
        bbox
    )