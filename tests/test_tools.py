from app.tools import (
    TOOL_DEFINITIONS,
    dispatch,
)


def test_tool_definitions_exist():

    names = {
        tool["function"]["name"]
        for tool in TOOL_DEFINITIONS
    }

    assert names == {
        "resolve_site",
        "list_years",
        "get_layer",
        "render",
        "validate",
    }


def test_unknown_tool_returns_json_error():

    result = dispatch(
        "does_not_exist",
        {},
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "not_found"


def test_invalid_inputs_return_json_error():

    result = dispatch(
        "resolve_site",
        {},
    )

    assert result["ok"] is False
    assert "error" in result
    assert "code" in result["error"]


def test_resolve_site_dispatch():

    result = dispatch(
        "resolve_site",
        {
            "county": "Douglas County, NE",
            "parcel_ids": None,
            "coords": {
                "latitude": 41.2565,
                "longitude": -95.9345,
            },
            "project_number": "PA-2026-0101",
        },
    )

    assert result["ok"] is True
    assert "data" in result
    assert result["data"]["parcel_ids"]