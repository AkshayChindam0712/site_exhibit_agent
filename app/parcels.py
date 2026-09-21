import json
import requests
from pathlib import Path
from pyproj import Transformer
import re
import geopandas as gpd
from shapely.geometry import Point, shape
from shapely.ops import snap, unary_union
from shapely.geometry import shape, Point, mapping

PARCEL_DATA_DIR = Path(__file__).resolve().parent.parent / "sites" / "parcel_data"
# 30 millimetres
SNAP_TOLERANCE_M = 0.03


def clean_parcel_id(parcel_id):
    """
    Clean a plot ID before searching.

    Handles:
    - None
    - leading/trailing spaces
    - spaces inside the ID
    - dashes
    """

    if parcel_id is None:
        return None

    value = str(parcel_id).strip()
    value = re.sub(r"\s+", "", value)

    return value

def load_parcel_data(project_number):
    """
    Load the synthetic plot fixture for a project.
    """

    file_path = (
        PARCEL_DATA_DIR
        / f"{project_number}.json"
    )

    if not file_path.exists():
        raise ValueError(
            f"Plot data not found for "
            f"{project_number}"
        )

    with open(
        file_path,
        "r",
        encoding="utf-8"
    ) as file:
        return json.load(file)


def find_parcel(data, requested_id):
    """
    Find one plot inside the fixture.
    """

    requested_id = clean_parcel_id(
        requested_id
    )

    for feature in data.get(
        "features",
        []
    ):

        properties = feature.get(
            "properties",
            {}
        )

        stored_id = clean_parcel_id(
            properties.get("parcel_id")
        )

        if stored_id == requested_id:
            return feature

    return None


def find_plot_by_coordinates(
    data,
    latitude,
    longitude
):
    """
    Find the plot containing the supplied
    latitude/longitude point.
    """
    if data is None or not isinstance(data, dict):
        return None

    point = Point(
        float(longitude),
        float(latitude)
    )

    for feature in data.get(
        "features",
        []
    ):

        geometry = shape(
            feature["geometry"]
        )

        if geometry.covers(point):

            return feature

    return None

def fetch_plot_from_douglas_api(plot_id):
    """
    Fetch one plot from the live Douglas County GIS API.
    """

    url = (
        "https://dcgis.org/server/rest/services/"
        "vector/Parcels_public/FeatureServer/0/query"
    )

    plot_id = clean_parcel_id(plot_id)

    params = {
        "where": f"PIN = '{plot_id}'",
        "outFields": "PIN,ACRES,ADDRESS1",
        "returnGeometry": "true",
        "outSR": "4326",
        "f": "geojson",
    }

    response = requests.get(
        url,
        params=params,
        timeout=30
    )

    response.raise_for_status()

    data = response.json()

    if "error" in data:
        raise ValueError(
            f"Douglas County API error: "
            f"{data['error']}"
        )

    features = data.get(
        "features",
        []
    )

    if not features:
        raise ValueError(
            f"Plot ID '{plot_id}' "
            f"was not found in Douglas County."
        )

    return features[0]

def fetch_plot_from_lancaster_api(plot_id):
    """
    Fetch one plot from Lancaster County, NE GIS API.
    """

    url = (
        "https://maps.lincoln.ne.gov/"
        "arcgis/rest/services/"
        "Parcel/MapServer/0/query"
    )

    plot_id = clean_parcel_id(plot_id)

    params = {
        "where": f"PARCEL_ID = '{plot_id}'",
        "outFields": "*",
        "returnGeometry": "true",
        "outSR": "4326",
        "f": "geojson",
    }

    response = requests.get(
        url,
        params=params,
        timeout=30
    )

    response.raise_for_status()

    data = response.json()

    if "error" in data:
        raise ValueError(
            f"Lancaster County API error: "
            f"{data['error']}"
        )

    features = data.get("features", [])

    if not features:
        raise ValueError(
            f"Plot ID '{plot_id}' "
            f"was not found in Lancaster County."
        )

    return features[0]


def fetch_plot_from_johnson_api(plot_id):
    """
    Fetch one plot from Johnson County, KS GIS API.
    """

    url = (
        "https://maps.jocogov.org/"
        "arcgis/rest/services/"
        "Parcel/MapServer/0/query"
    )

    plot_id = clean_parcel_id(plot_id)

    params = {
        "where": f"PARCELID = '{plot_id}'",
        "outFields": "*",
        "returnGeometry": "true",
        "outSR": "4326",
        "f": "geojson",
    }

    response = requests.get(
        url,
        params=params,
        timeout=30
    )

    response.raise_for_status()

    data = response.json()

    if "error" in data:
        raise ValueError(
            f"Johnson County API error: "
            f"{data['error']}"
        )

    features = data.get("features", [])

    if not features:
        raise ValueError(
            f"Plot ID '{plot_id}' "
            f"was not found in Johnson County."
        )

    return features[0]

def fetch_plot(county, plot_id, data):
    """
    Get a plot from the supplied fixture first.
    Fall back to the live Douglas County API only
    when fixture data does not contain the parcel.
    """

    # Always prefer supplied fixture data.
    if data is not None:
        feature = find_parcel(data, plot_id)

        if feature is not None:
            return feature

    # Douglas County live API fallback.
    if county == "Douglas County, NE":
        try:
            return fetch_plot_from_douglas_api(plot_id)

        except (ValueError, requests.RequestException):
            raise ValueError(
                f"Plot ID '{plot_id}' "
                f"was not found in Douglas County."
            )

    raise ValueError(
        f"Plot ID '{plot_id}' "
        f"was not found in {county}."
    )
def resolve_site(
    county,
    parcel_ids=None,
    coords=None,
    project_number=None,
):
    """
    Resolve one site from plot IDs or coordinates.

    Returns:
        geometry
        plot IDs
        CRS
        area in square metres
        area in acres
        number of geometry parts
        source
        snapping tolerance
    """

    # --------------------------------------------------
    # Validate input
    # --------------------------------------------------

    if not parcel_ids and not coords:
        raise ValueError(
            "No plot IDs or coordinates were provided."
        )

    print(
        f"Resolving site in {county}"
    )

    # --------------------------------------------------
    # Load fixture
    # --------------------------------------------------

    data = None

    if project_number:
        data = load_parcel_data(
            project_number
        )

    # Track how the site was resolved.
    resolved_from_coords = False

    # --------------------------------------------------
    # Coordinate-only lookup
    # --------------------------------------------------

    if not parcel_ids and coords:

        resolved_from_coords = True

        latitude = float(
            coords["latitude"]
        )

        longitude = float(
            coords["longitude"]
        )

        feature = find_plot_by_coordinates(
            data,
            latitude,
            longitude
        )

        if feature is None:
            raise ValueError(
                f"No plot contains coordinates "
                f"({latitude}, {longitude})."
            )

        resolved_plot_id = feature[
            "properties"
        ]["parcel_id"]

        parcel_ids = [
            resolved_plot_id
        ]

    # --------------------------------------------------
    # Resolve every plot ID
    # --------------------------------------------------

    geometries = []
    found_ids = []

    for parcel_id in parcel_ids:

        print(
            f"Searching plot: {parcel_id}"
        )

        feature = fetch_plot(
            county,
            parcel_id,
            data
        )

        if feature is None:
            raise ValueError(
                f"Plot ID "
                f"'{parcel_id}' "
                f"was not found."
            )

        geometry = shape(
            feature["geometry"]
        )

        geometries.append(
            geometry
        )

        found_ids.append(
            clean_parcel_id(
                parcel_id
            )
        )

    # --------------------------------------------------
    # Create GeoDataFrame
    # --------------------------------------------------

    plots = gpd.GeoDataFrame(
        {
            "parcel_id": found_ids
        },
        geometry=geometries,
        crs="EPSG:4326"
    )

    # --------------------------------------------------
    # Find appropriate UTM zone
    # --------------------------------------------------

    combined_wgs84 = (
        plots
        .union_all()
    )

    centroid = (
        combined_wgs84
        .centroid
    )

    longitude = centroid.x
    latitude = centroid.y

    zone = int(
        (longitude + 180) / 6
    ) + 1

    if latitude >= 0:
        utm_epsg = 32600 + zone
    else:
        utm_epsg = 32700 + zone

    print(
        f"Using UTM EPSG:{utm_epsg}"
    )

    # --------------------------------------------------
    # Convert to UTM
    # --------------------------------------------------

    plots_utm = plots.to_crs(
        epsg=utm_epsg
    )

    # --------------------------------------------------
    # Snap nearby plot boundaries
    # --------------------------------------------------

    print(
        f"Using snapping tolerance: "
        f"{SNAP_TOLERANCE_M} m"
    )

    snapped_geometries = []

    for geometry in plots_utm.geometry:

        snapped = geometry

        for other in plots_utm.geometry:

            if geometry is not other:

                snapped = snap(
                    snapped,
                    other,
                    SNAP_TOLERANCE_M
                )

        snapped_geometries.append(
            snapped
        )

    # --------------------------------------------------
    # Combine plots
    # --------------------------------------------------

    site_geometry = unary_union(
        snapped_geometries
    )

    # --------------------------------------------------
    # Verify supplied coordinates are inside site
    # --------------------------------------------------

    coordinates_verified = None

    if coords:

        latitude = float(
            coords["latitude"]
        )

        longitude = float(
            coords["longitude"]
        )

        transformer = Transformer.from_crs(
            "EPSG:4326",
            f"EPSG:{utm_epsg}",
            always_xy=True
        )

        x, y = transformer.transform(
            longitude,
            latitude
        )

        point_utm = Point(
            x,
            y
        )

        coordinates_verified = (
            site_geometry.covers(
                point_utm
            )
        )

        if not coordinates_verified:
            raise ValueError(
                "Provided coordinates are not "
                "inside the resolved site outline."
            )

    # --------------------------------------------------
    # Calculate area
    # --------------------------------------------------

    area_m2 = (
        site_geometry.area
    )

    area_acres = (
        area_m2
        / 4046.8564224
    )

    # --------------------------------------------------
    # Count geometry parts
    # --------------------------------------------------

    if (
        site_geometry.geom_type
        == "MultiPolygon"
    ):

        parts = len(
            site_geometry.geoms
        )

    else:

        parts = 1

    # --------------------------------------------------
    # Return result
    # --------------------------------------------------

    return {
        "geometry_geojson": {
            "type": "Feature",
            "geometry": mapping(
                site_geometry
            ),
            "properties": {
                "parcel_ids": found_ids,
                "area_m2": area_m2,
                "area_acres": area_acres,
                "crs": f"EPSG:{utm_epsg}",
                "source": (
                    "coordinates"
                    if resolved_from_coords
                    else "parcel_ids"
                ),
                "snapping_tolerance_m":
                    SNAP_TOLERANCE_M,
            },
        },

        "parcel_ids": found_ids,

        "crs": f"EPSG:{utm_epsg}",

        "area_m2": area_m2,

        "area_acres": area_acres,

        "parts": parts,

        "source": (
            "coordinates"
            if resolved_from_coords
            else "parcel_ids"
        ),

        "snapping_tolerance_m":
            SNAP_TOLERANCE_M,

        "coordinates_verified":
            coordinates_verified,
    }