import json

from app.tools import dispatch


def assert_json(result):
    json.dumps(result)
    assert isinstance(result, dict)


def test_resolve_site_dispatch():
    result = dispatch(
        "resolve_site",
        {
            "county": "Douglas County, NE",
            "coords": {
                "latitude": 41.2565,
                "longitude": -95.9345,
            },
            "project_number": "PA-2026-0101",
        },
    )

    assert_json(result)
    assert "error" not in result


def test_get_layer_invalid_request():
    result = dispatch(
        "get_layer",
        {
            "kind": "invalid",
            "bbox": [1, 2, 3, 4],
        },
    )

    assert_json(result)
    assert result["error"]["code"] == "invalid_request"


def test_unknown_tool():
    result = dispatch(
        "unknown_tool",
        {},
    )

    assert_json(result)
    assert result["error"]["code"] == "not_found"


def test_validate_dispatch():
    result = dispatch(
        "validate",
        {
            "output_dir": "output/PA-2026-0142",
        },
    )

    assert_json(result)