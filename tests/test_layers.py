from pathlib import Path

from app.layers import get_layer

DOUGLAS_BBOX = (
    -96.362,
    41.290,
    -96.359,
    41.293,
)


ALASKA_BBOX = (
    -149.95,
    61.20,
    -149.90,
    61.25,
)


def test_get_topo_layer():

    result = get_layer(
        kind="topo",
        bbox=DOUGLAS_BBOX,
    )

    assert result["kind"] == "topo"

    assert result["source"] in (
        "USGS Topo",
        "cache",
    )

    assert Path(
        result["path"]
    ).exists()


def test_aerial_layer_uses_cache():
    """
    A second request for the same aerial layer
    should return the cached file.
    """

    bbox = (
        -96.362,
        41.290,
        -96.359,
        41.293,
    )

    result = get_layer(
        kind="aerial",
        bbox=bbox,
        year=2022,
    )

    assert result["kind"] == "aerial"
    assert result["year"] == 2022
    assert result["source"] == "cache"
    assert Path(result["path"]).exists()