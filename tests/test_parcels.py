from app.parcels import resolve_site
from app.parcels import clean_parcel_id
import matplotlib.pyplot as plt

from app.render import _draw_scale_bar
def test_douglas_county_two_parcels():

    result = resolve_site(
        county="Douglas County, NE",
        parcel_ids=[
            "0387581351",
            "6816572479"
        ],
        project_number="PA-2026-0101"
    )

    assert result["parts"] == 1

    assert abs(
        result["area_acres"] - 2.774
    ) < 0.01

def test_l_shaped_three_plot_site():

    result = resolve_site(
        county="Wake County, NC",
        parcel_ids=[
            "9491706",
            "5849695",
            "1368684"
        ],
        project_number="PA-2026-0115"
    )

    # The three plots must form one connected outline.
    assert result["parts"] == 1

    # Expected outline is approximately 2.834 acres.
    assert abs(
        result["area_acres"] - 2.834
    ) < 0.01

def test_20mm_gap_is_snapped():

    result = resolve_site(
        county="Suffolk County, NY",
        parcel_ids=[
            "3523-796.87-36.94-205.647",
            "9752-785.79-28.19-370.086"
        ],
        project_number="PA-2026-0111"
    )

    # The 20 mm gap should be closed by
    # our 30 mm snapping tolerance.
    assert result["parts"] == 1

    assert result["snapping_tolerance_m"] == 0.03

    assert abs(
        result["area_acres"] - 2.774
    ) < 0.01

def test_coordinate_only_lookup():

    result = resolve_site(
        county="Douglas County, NE",
        parcel_ids=None,
        coords={
            "latitude": 41.2565,
            "longitude": -95.9345
        },
        project_number="PA-2026-0101"
    )

    # Coordinates should resolve to a plot.
    assert len(result["parcel_ids"]) == 1

    # The site should be one connected outline.
    assert result["parts"] == 1

    # It should have been resolved from coordinates.
    assert result["source"] == "coordinates"

def test_lancaster_county_two_parcels():

    result = resolve_site(
        county="Lancaster County, NE",
        parcel_ids=[
            "1710344206",
            "9039343425"
        ],
        project_number="PA-2026-0103"
    )

    assert result["parts"] == 1

    assert abs(
        result["area_acres"] - 2.774
    ) < 0.01

    assert result["source"] == "parcel_ids"


def test_johnson_county_single_parcel():

    result = resolve_site(
        county="Johnson County, KS",
        parcel_ids=[
            "84-038-60-0-53-24-894-564"
        ],
        project_number="PA-2026-0104"
    )

    assert result["parts"] == 1

    assert abs(
        result["area_acres"] - 1.773
    ) < 0.01

    assert result["source"] == "parcel_ids"

def test_site_returns_geojson():

    result = resolve_site(
        county="Douglas County, NE",
        parcel_ids=[
            "0100370004"
        ],
        project_number="PA-2026-0101"
    )

    geojson = result["geometry_geojson"]

    assert geojson["type"] == "Feature"

    assert geojson["geometry"]["type"] in (
        "Polygon",
        "MultiPolygon"
    )

    assert "coordinates" in geojson["geometry"]

    assert geojson["properties"]["parcel_ids"] == [
        "0100370004"
    ]

def test_coordinates_are_inside_site():

    result = resolve_site(
        county="Douglas County, NE",
        parcel_ids=["0100370004"],
        coords={
            "latitude": 41.2912,
            "longitude": -96.3609
        },
        project_number="PA-2026-0101"
    )

    assert result["parts"] == 1
    assert result["source"] == "parcel_ids"
    assert result["coordinates_verified"] is True

    from app.parcels import clean_parcel_id


def test_clean_parcel_id_spaces():
    assert clean_parcel_id(
        " 0387581351 "
    ) == "0387581351"


def test_clean_parcel_id_dashes():
    assert clean_parcel_id(
        "84-038-60-0-53-24-894-564"
    ) == "84-038-60-0-53-24-894-564"


def test_clean_parcel_id_extra_zeros():
    result = clean_parcel_id(
        "0000387581351"
    )

    assert result

def test_resolve_site_bad_parcel_id():
    import pytest

    from app.parcels import resolve_site

    with pytest.raises(Exception) as exc:

        resolve_site(
            "Douglas County, NE",
            ["BAD-PARCEL-ID"],
        )

    assert "BAD-PARCEL-ID" in str(
        exc.value
    )

def test_scale_bar_returns_numeric_length():

    

    fig, ax = plt.subplots()

    result = _draw_scale_bar(
        ax,
        1000.0,
    )

    assert isinstance(
        result,
        (int, float),
    )

    assert result > 0
    assert result <= 1000.0

    plt.close(fig)