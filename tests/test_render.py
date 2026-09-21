from pathlib import Path

import matplotlib
matplotlib.use("Agg")

from shapely.geometry import Polygon

from app.render import render


def make_boundary():
    """
    Small test parcel in UTM metres.
    """
    return Polygon([
        (500000, 4500000),
        (500050, 4500000),
        (500050, 4500050),
        (500000, 4500050),
        (500000, 4500000),
    ])


def make_layer(tmp_path):
    """
    Create a small test background image.
    """

    from PIL import Image

    image_path = tmp_path / "background.png"

    image = Image.new(
        "RGB",
        (200, 200),
        "white",
    )

    image.save(image_path)

    return {
        "path": str(image_path),
        "kind": "topo",
        "year": None,
        "source": "test",
        "bbox": (
            499950,
            4499950,
            500100,
            4500100,
        ),
    }


def test_render_returns_output_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    layer = make_layer(tmp_path)
    boundary = make_boundary()

    result = render(
        layer=layer,
        boundary=boundary,
        title="TEST MAP",
        year=2022,
    )

    assert "png" in result
    assert "pdf" in result

    assert Path(result["png"]).exists()
    assert Path(result["pdf"]).exists()


def test_render_uses_300_dpi(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    layer = make_layer(tmp_path)
    boundary = make_boundary()

    result = render(
        layer=layer,
        boundary=boundary,
        title="DPI TEST",
        year=2022,
    )

    assert result["dpi"] == 300


def test_render_is_us_letter(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    layer = make_layer(tmp_path)
    boundary = make_boundary()

    result = render(
        layer=layer,
        boundary=boundary,
        title="LETTER TEST",
        year=2022,
    )

    assert result["page_size"] == "US Letter"


def test_render_accepts_resolve_site_result(
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)

    layer = make_layer(tmp_path)

    boundary = {
        "geometry_geojson": {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [500000, 4500000],
                    [500050, 4500000],
                    [500050, 4500050],
                    [500000, 4500050],
                    [500000, 4500000],
                ]],
            },
            "properties": {},
        }
    }

    result = render(
        layer=layer,
        boundary=boundary,
        title="GEOJSON TEST",
        year=2022,
    )

    assert Path(result["png"]).exists()
    assert Path(result["pdf"]).exists()


def test_render_missing_background(
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)

    layer = {
        "path": str(
            tmp_path / "does_not_exist.png"
        ),
        "kind": "aerial",
        "year": 2022,
        "source": "test",
        "bbox": (
            499950,
            4499950,
            500100,
            4500100,
        ),
    }

    boundary = make_boundary()

    result = render(
        layer=layer,
        boundary=boundary,
        title="UNMAPPED TEST",
        year=2022,
    )

    assert Path(result["png"]).exists()
    assert Path(result["pdf"]).exists()