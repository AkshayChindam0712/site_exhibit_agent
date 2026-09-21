from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box

from app.historical_aerial import (
    get_utm_crs,
    project_geometry,
    group_records_by_year,
    select_record_for_year,
    make_export_extent,
    find_earthexplorer_image,
    tiff_to_rgb,
)


def create_test_tiff(path):
    """
    Create a small georeferenced RGB TIFF for testing.
    """

    data = np.zeros(
        (3, 100, 100),
        dtype=np.uint8,
    )

    data[0, :, :] = 100
    data[1, :, :] = 150
    data[2, :, :] = 200

    transform = from_origin(
        500000,
        4600000,
        1,
        1,
    )

    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=100,
        width=100,
        count=3,
        dtype="uint8",
        crs="EPSG:32615",
        transform=transform,
    ) as dst:
        dst.write(data)


def test_get_utm_crs():

    geometry = box(
        -96.0,
        41.0,
        -95.99,
        41.01,
    )

    result = get_utm_crs(
        geometry
    )

    assert result == "EPSG:32615"


def test_project_geometry():

    geometry = box(
        -96.0,
        41.0,
        -95.99,
        41.01,
    )

    projected = project_geometry(
        geometry,
        source_crs="EPSG:4326",
        target_crs="EPSG:32615",
    )

    assert not projected.is_empty

    assert projected.geom_type == "Polygon"

    minx, miny, maxx, maxy = (
        projected.bounds
    )

    assert maxx > minx
    assert maxy > miny


def test_group_records_by_year():

    records = [
        {
            "id": "item-2022",
            "year": 2022,
        },
        {
            "id": "item-2020",
            "year": 2020,
        },
        {
            "id": "item-2022-b",
            "year": 2022,
        },
    ]

    grouped = group_records_by_year(
        records
    )

    assert set(grouped.keys()) == {
        2020,
        2022,
    }

    assert len(
        grouped[2022]
    ) == 2

    assert len(
        grouped[2020]
    ) == 1


def test_select_record_for_year():

    records = [
        {
            "id": "older",
            "year": 2022,
            "overlap": 10,
            "gsd": 1,
            "datetime": "2022-05-01T00:00:00",
        },
        {
            "id": "better",
            "year": 2022,
            "overlap": 20,
            "gsd": 1,
            "datetime": "2022-05-01T00:00:00",
        },
    ]

    result = select_record_for_year(
        records,
        2022,
    )

    assert result["id"] == "better"


def test_select_record_for_year_empty():

    result = select_record_for_year(
        [],
        2022,
    )

    assert result is None


def test_make_export_extent():

    geometry = box(
        500000,
        4600000,
        500100,
        4600100,
    )

    result = make_export_extent(
        geometry,
        scale_factor=5.0,
    )

    assert len(result) == 4

    minx, miny, maxx, maxy = result

    assert maxx > minx
    assert maxy > miny

    # Original geometry should be inside
    # the expanded export extent.
    assert minx <= 500000
    assert maxx >= 500100
    assert miny <= 4600000
    assert maxy >= 4600100


def test_find_earthexplorer_image(
    tmp_path,
    monkeypatch,
):

    earth_explorer_dir = (
        tmp_path / "earth_explorer"
    )

    earth_explorer_dir.mkdir()

    tif_path = (
        earth_explorer_dir / "2009.tif"
    )

    create_test_tiff(
        tif_path
    )

    monkeypatch.setattr(
        "app.historical_aerial.EARTH_EXPLORER_DIR",
        earth_explorer_dir,
    )

    result = find_earthexplorer_image(
        2009
    )

    assert result == tif_path


def test_find_earthexplorer_image_missing(
    tmp_path,
    monkeypatch,
):

    earth_explorer_dir = (
        tmp_path / "earth_explorer"
    )

    earth_explorer_dir.mkdir()

    monkeypatch.setattr(
        "app.historical_aerial.EARTH_EXPLORER_DIR",
        earth_explorer_dir,
    )

    result = find_earthexplorer_image(
        2009
    )

    assert result is None


def test_find_earthexplorer_image_without_crs(
    tmp_path,
    monkeypatch,
):

    earth_explorer_dir = (
        tmp_path / "earth_explorer"
    )

    earth_explorer_dir.mkdir()

    tif_path = (
        earth_explorer_dir / "2009.tif"
    )

    data = np.zeros(
        (3, 100, 100),
        dtype=np.uint8,
    )

    with rasterio.open(
        tif_path,
        "w",
        driver="GTiff",
        height=100,
        width=100,
        count=3,
        dtype="uint8",
    ) as dst:
        dst.write(data)

    monkeypatch.setattr(
        "app.historical_aerial.EARTH_EXPLORER_DIR",
        earth_explorer_dir,
    )

    result = find_earthexplorer_image(
        2009
    )

    assert result is None


def test_tiff_to_rgb(
    tmp_path,
):

    tif_path = (
        tmp_path / "test.tif"
    )

    create_test_tiff(
        tif_path
    )

    image = tiff_to_rgb(
        tif_path
    )

    assert image.mode == "RGB"

    assert image.width == 100
    assert image.height == 100


def test_tiff_to_rgb_requires_three_bands(
    tmp_path,
):

    tif_path = (
        tmp_path / "single_band.tif"
    )

    data = np.zeros(
        (1, 100, 100),
        dtype=np.uint8,
    )

    transform = from_origin(
        500000,
        4600000,
        1,
        1,
    )

    with rasterio.open(
        tif_path,
        "w",
        driver="GTiff",
        height=100,
        width=100,
        count=1,
        dtype="uint8",
        crs="EPSG:32615",
        transform=transform,
    ) as dst:
        dst.write(data)

    try:
        tiff_to_rgb(
            tif_path
        )
        assert False, (
            "Expected ValueError"
        )
    except ValueError as exc:
        assert (
            "at least 3 bands"
            in str(exc)
        )