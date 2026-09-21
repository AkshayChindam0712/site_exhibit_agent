from pathlib import Path
import json
import traceback
from app.historical_aerial import generate_historical_aerial
from app.parcels import resolve_site
from app.layers import get_layer
from app.render import render


# ============================================================
# CONFIG
# ============================================================



# ============================================================
# BBOX
# ============================================================

def get_bbox(geometry):

    if geometry["type"] == "Polygon":

        points = geometry["coordinates"][0]

    elif geometry["type"] == "MultiPolygon":

        points = []

        for polygon in geometry["coordinates"]:
            for ring in polygon:
                points.extend(ring)

    else:
        raise ValueError(
            f"Unsupported geometry type: {geometry['type']}"
        )

    xs = [float(p[0]) for p in points]
    ys = [float(p[1]) for p in points]

    return (
        min(xs),
        min(ys),
        max(xs),
        max(ys),
    )


# ============================================================
# EXPAND BBOX
# ============================================================

def expand_bbox(bbox, factor=3.0):

    minx, miny, maxx, maxy = bbox

    width = maxx - minx
    height = maxy - miny

    return (
        minx - width * factor,
        miny - height * factor,
        maxx + width * factor,
        maxy + height * factor,
    )


# ============================================================
# MAIN
# ============================================================


def generate_site(site_json):

    site_json = Path(site_json)

    with open(
        site_json,
        "r",
        encoding="utf-8",
    ) as f:
        config = json.load(f)

    latitude = config.get("latitude")
    longitude = config.get("longitude")  

    if config.get("type") == "FeatureCollection":

        features = config.get("features", [])

        if not features:
            raise ValueError(
                f"No parcel features found in {site_json}"
            )

        COUNTY = features[0]["properties"]["county"]

        parcel_ids = [
            feature["properties"]["parcel_id"]
            for feature in features
            if feature.get("properties", {}).get("parcel_id")
        ]

        if not parcel_ids:
            raise ValueError(
                f"No parcel IDs found in {site_json}"
            )

        project_number = (
            config.get("metadata", {}).get("project_number")
            or features[0]["properties"].get("project_number")
            or site_json.stem
        )

    else:

        COUNTY = config.get("county")
        parcel_ids = config.get("parcel_ids")


        if parcel_ids:
            PARCEL_ID = parcel_ids[0]
        else:
            PARCEL_ID = None

        if not parcel_ids and (latitude is None or longitude is None):
            raise ValueError(
                f"No parcel IDs or coordinates provided in {site_json}"
            )

        project_number = config.get(
            "project_number",
            site_json.stem,
        )

    PARCEL_ID = parcel_ids[0] if parcel_ids else None
    AERIAL_YEAR = config.get(
        "aerial_year",
        2022,
    )

    OUTPUT_DIR = Path(
        config.get("output_dir", f"./out/{project_number}")
    )
    print()
    print("=" * 70)
    print("SITE EXHIBIT  PIPELINE")
    print("=" * 70)

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    # --------------------------------------------------------
    # 1. RESOLVE PARCEL
    # --------------------------------------------------------

    print()
    print("STEP 1: RESOLVING PARCEL")

    print("DEBUG COUNTY:", COUNTY)
    print("DEBUG PARCEL IDS:", parcel_ids)
    print("DEBUG COORDS:", {
        "latitude": latitude,
        "longitude": longitude,
    })

    site = resolve_site(
        county=COUNTY,
        parcel_ids=parcel_ids,
        coords={
            "latitude": latitude,
            "longitude": longitude,
        },
        project_number=project_number,
    )

    if PARCEL_ID is None:
        PARCEL_ID = site["parcel_ids"][0]
    geometry_geojson = site["geometry_geojson"]
    geometry = geometry_geojson["geometry"]

    parcel_bbox = get_bbox(
        geometry
    )

    print(
        "Parcel BBOX:",
        parcel_bbox
    )

    # --------------------------------------------------------
    # 2. EXPAND MAP EXTENT
    # --------------------------------------------------------

    map_bbox = expand_bbox(
        parcel_bbox,
        factor=3.0,
    )

    print(
        "Map BBOX:",
        map_bbox
    )

    # --------------------------------------------------------
    # 3. RETRIEVE AERIAL
    # --------------------------------------------------------

    print()
    print("STEP 2: RETRIEVING AERIAL")

    aerial = get_layer(
        "aerial",
        map_bbox,
        AERIAL_YEAR,
    )

    print(
        "Aerial:",
        aerial
    )

    # --------------------------------------------------------
    # 4. RETRIEVE TOPO
    # --------------------------------------------------------

    print()
    print("STEP 3: RETRIEVING TOPO")

    topo = get_layer(
        "topo",
        map_bbox,
    )

    print(
        "Topo:",
        topo
    )

    # --------------------------------------------------------
    # 5. BUILD RENDER LAYER
    # --------------------------------------------------------

    layer = dict(aerial)

    layer["aerial_path"] = aerial.get(
        "path"
    )

    layer["topo_path"] = topo.get(
        "path"
    )

    layer["aerial"] = aerial
    layer["topo"] = topo


    # --------------------------------------------------------
    # 6. SITE PLAN
    # --------------------------------------------------------

    print()
    print("STEP 4: RENDERING SITE PLAN")

    site_plan = render(
        layer,
        geometry_geojson,
        f"{COUNTY} Parcel {PARCEL_ID or 'Site'}",
        aerial.get(
            "year",
            AERIAL_YEAR,
        ),
        map_type="AERIAL",
    )

    print(
        "Site Plan:",
        site_plan
    )

    # --------------------------------------------------------
    # 7. SITE LOCATION
    # --------------------------------------------------------

    print()
    print("STEP 5: RENDERING SITE LOCATION")

    site_location = render(
        layer,
        geometry_geojson,
        f"{COUNTY} Parcel {PARCEL_ID or 'Site'}",
        topo.get(
            "year",
            None,
        ),
        map_type="TOPO",
    )

    print(
        "Site Location:",
        site_location
    )

    # --------------------------------------------------------
    # 6. HISTORICAL AERIAL
    # --------------------------------------------------------

    print()
    print("STEP 6: GENERATING HISTORICAL AERIAL SERIES")

    historical = generate_historical_aerial(
        county=COUNTY,
        parcel_ids=parcel_ids,
        coords={
            "latitude": latitude,
            "longitude": longitude,
        },
        project_number=project_number,
    )

    print(
        "Historical Aerial:",
        historical,
    )
    # --------------------------------------------------------
    # 8. MANIFEST
    # --------------------------------------------------------

    print()
    print("STEP 6: CREATING MANIFEST")

    manifest = {

            "site": {
                "county": COUNTY,
                "parcel_id": PARCEL_ID,
                "projection": site.get(
                    "crs"
                ),
                "site_outline_source": (
                    site.get("source")
                    or "parcel_ids"
                ),
            },

            "site_plan": {
                "map_type": "AERIAL",

                "outputs": site_plan,

                "source": aerial.get(
                    "source"
                ),

                "photo_year": aerial.get(
                    "year"
                ),

                "projection": aerial.get(
                    "crs"
                ),

                "scale": (
                    site_plan.get(
                        "scale"
                    )
                    if isinstance(
                        site_plan,
                        dict
                    )
                    else None
                ),

                "scale_bar_length_metres": (
                    site_plan.get(
                        "scale_bar_length_metres"
                    )
                    if isinstance(
                        site_plan,
                        dict
                    )
                    else None
                ),

                "map_width_metres": (
                    site_plan.get(
                        "map_width_metres"
                    )
                    if isinstance(
                        site_plan,
                        dict
                    )
                    else None
                ),

                "scale_denominator": (
                    site_plan.get(
                        "scale_denominator"
                    )
                    if isinstance(
                        site_plan,
                        dict
                    )
                    else None
                ),

                "bbox": (
                    site_plan.get(
                        "bbox"
                    )
                    if isinstance(
                        site_plan,
                        dict
                    )
                    else None
                ),

                "elements_drawn": (
                    site_plan.get(
                        "elements_drawn"
                    )
                    if isinstance(
                        site_plan,
                        dict
                    )
                    else None
                ),

                "site_outline_source": (
                    site.get("source")
                    or "parcel_ids"
                ),
            },

            "site_location": {
                "map_type": "TOPO",

                "outputs": site_location,

                "source": topo.get(
                    "source"
                ),

                "photo_year": topo.get(
                    "year"
                ),

                "projection": topo.get(
                    "crs"
                ),

                "scale": (
                    site_location.get(
                        "scale"
                    )
                    if isinstance(
                        site_location,
                        dict
                    )
                    else None
                ),

                "scale_bar_length_metres": (
                    site_location.get(
                        "scale_bar_length_metres"
                    )
                    if isinstance(
                        site_location,
                        dict
                    )
                    else None
                ),

                "map_width_metres": (
                    site_location.get(
                        "map_width_metres"
                    )
                    if isinstance(
                        site_location,
                        dict
                    )
                    else None
                ),

                "scale_denominator": (
                    site_location.get(
                        "scale_denominator"
                    )
                    if isinstance(
                        site_location,
                        dict
                    )
                    else None
                ),

                "bbox": (
                    site_location.get(
                        "bbox"
                    )
                    if isinstance(
                        site_location,
                        dict
                    )
                    else None
                ),

                "elements_drawn": (
                    site_location.get(
                        "elements_drawn"
                    )
                    if isinstance(
                        site_location,
                        dict
                    )
                    else None
                ),

                "site_outline_source": (
                    site.get("source")
                    or "parcel_ids"
                ),
            },

            "historical": historical,
        }
    manifest_path = (
            OUTPUT_DIR /
            "manifest.json"
        )

    with open(
        manifest_path,
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            manifest,
            f,
            indent=2,
            default=str,
        )

    print()
    print(
        "Manifest:",
        manifest_path
    )

    print()
    print("=" * 70)
    print("SITE EXHIBIT GENERATION COMPLETE")
    print("=" * 70)


# ============================================================
# ERROR HANDLING
# ============================================================

if __name__ == "__main__":

    try:
        generate_site(
            "sites/PA-2026-0101.json"
        )

    except Exception as exc:

        print()
        print("=" * 70)
        print("PIPELINE FAILED")
        print("=" * 70)

        print(
            type(exc).__name__,
            ":",
            exc,
        )

        raise