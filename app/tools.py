import json


# ============================================================
# TOOL DEFINITIONS
# ============================================================
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "resolve_site",
            "description": "Resolve a site to its parcel geometry and metadata.",
            "parameters": {
                "type": "object",
                "properties": {
                    "county": {
                        "type": "string",
                        "description": "County containing the site."
                    },
                    "parcel_ids": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "Optional parcel IDs."
                    },
                    "coords": {
                        "type": "object",
                        "properties": {
                            "latitude": {
                                "type": "number"
                            },
                            "longitude": {
                                "type": "number"
                            }
                        },
                        "required": [
                            "latitude",
                            "longitude"
                        ]
                    },
                    "project_number": {
                        "type": "string",
                        "description": "Optional project/test-site number."
                    }
                },
                "required": [
                    "county"
                ]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_years",
            "description": "List available historical imagery years for a site.",
            "parameters": {
                "type": "object",
                "properties": {
                    "bbox": {
                        "type": "array",
                        "items": {
                            "type": "number"
                        },
                        "minItems": 4,
                        "maxItems": 4
                    }
                },
                "required": [
                    "bbox"
                ]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_layer",
            "description": "Retrieve an aerial or topographic map layer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": [
                            "aerial",
                            "naip",
                            "topo"
                        ]
                    },
                    "bbox": {
                        "type": "array",
                        "items": {
                            "type": "number"
                        },
                        "minItems": 4,
                        "maxItems": 4
                    },
                    "year": {
                        "type": "integer"
                    },
                    "county": {
                        "type": "string",
                        "description": "Optional county/state used to identify an Alaska aerial coverage gap."
                    }
                },
                "required": [
                    "kind",
                    "bbox"
                ]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "render",
            "description": "Render exactly one map using the supplied layer. AERIAL must receive an aerial/NAIP layer; TOPO must receive a topo layer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "layer": {
                        "type": "object"
                    },
                    "boundary": {
                        "type": "object"
                    },
                    "title": {
                        "type": "string"
                    },
                    "year": {
                        "type": [
                            "integer",
                            "null"
                        ]
                    },
                    "map_type": {
                        "type": "string",
                        "enum": [
                            "AERIAL",
                            "TOPO"
                        ],
                        "default": "AERIAL"
                    }
                },
                "required": [
                    "layer",
                    "boundary",
                    "title"
                ]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "validate",
            "description": "Validate a generated site exhibit package.",
            "parameters": {
                "type": "object",
                "properties": {
                    "output_dir": {
                        "type": "string"
                    }
                },
                "required": [
                    "output_dir"
                ]
            }
        }
    }
]


# ============================================================
# ERROR HANDLING
# ============================================================

def _error(code, message):
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": str(message),
        },
    }


def _success(data):
    return {
        "ok": True,
        "data": data,
    }


def _human_required(
    code,
    question,
    tried,
    found,
    needs_from_human,
):
    return {
        "ok": False,
        "status": "human_required",
        "error_code": code,
        "question": question,
        "tried": tried,
        "found": found,
        "needs_from_human": needs_from_human,
    }


def _classify_error(exc):
    """
    Convert Python exceptions into safe tool errors.

    Exceptions and stack traces are never returned
    to the model.
    """

    message = str(exc)

    if isinstance(exc, (TypeError, KeyError, ValueError)):
        return "invalid_request"

    if isinstance(exc, FileNotFoundError):
        return "not_found"

    if "not found" in message.lower():
        return "not_found"

    if "coverage" in message.lower():
        return "no_coverage"

    if "unsupported" in message.lower():
        return "unsupported_county"

    return "invalid_request"


# ============================================================
# TOOL IMPLEMENTATIONS
# ============================================================

def _resolve_site(inputs):
    from app.parcels import resolve_site

    county = inputs.get("county")
    parcel_ids = inputs.get("parcel_ids")
    coords = inputs.get("coords")
    project_number = inputs.get("project_number")

    try:
        result = resolve_site(
            county=county,
            parcel_ids=parcel_ids,
            coords=coords,
            project_number=project_number,
        )

        # Keep the real resolved site bbox available to the agent.
        # This prevents the model from inventing a bbox for list_years.
        if (
            isinstance(result, dict)
            and isinstance(result.get("geometry_geojson"), dict)
        ):
            from shapely.geometry import shape

            geometry = shape(result["geometry_geojson"])
            result["bbox"] = list(geometry.bounds)

        return result

    except ValueError as exc:
        message = str(exc)
        lower_message = message.lower()

        # ----------------------------------------------------
        # TASK 10: NO PARCEL MATCH
        # ----------------------------------------------------
        if (
            "plot id" in lower_message
            and "not found" in lower_message
        ) or (
            "parcel" in lower_message
            and "not found" in lower_message
        ) or (
            "no parcel" in lower_message
        ):

            return _human_required(
                code="NO_PARCEL_MATCH",
                question=(
                    "I could not find a parcel matching "
                    "the supplied site information. "
                    "Please provide the correct parcel ID "
                    "or additional site information."
                ),
                tried=[
                    (
                        "Tried to resolve the supplied "
                        "county and parcel ID."
                    )
                ],
                found=[
                    message
                ],
                needs_from_human=[
                    (
                        "A correct parcel ID or additional "
                        "site information."
                    )
                ],
            )

        # ----------------------------------------------------
        # TASK 10: COORDINATES OUTSIDE PARCELS
        # ----------------------------------------------------
        if (
            "no plot contains coordinates" in lower_message
            or (
                "coordinates" in lower_message
                and "outside" in lower_message
            )
        ):

            return _human_required(
                code="COORDINATES_OUTSIDE_PARCELS",
                question=(
                    "The supplied coordinates are not inside "
                    "any matching parcel. Please provide "
                    "corrected coordinates or the correct "
                    "parcel ID."
                ),
                tried=[
                    (
                        "Tried to resolve the supplied "
                        "coordinates."
                    ),
                    (
                        "Checked the coordinates against "
                        "the available parcel geometry."
                    )
                ],
                found=[
                    message
                ],
                needs_from_human=[
                    (
                        "Correct coordinates or the correct "
                        "parcel ID."
                    )
                ],
            )

        # Keep all other ValueErrors as normal errors.
        raise

def _list_years(inputs):
    """
    Historical-year implementation will be connected here
    once the project's public list_years function exists.
    """

    from app.historical_aerial import search_all_naip_records

    bbox = inputs.get("bbox")

    if not isinstance(bbox, list) or len(bbox) != 4:
        raise ValueError(
            "bbox must contain four numbers."
        )

    records = search_all_naip_records(
        bbox,
        "EPSG:32614",
    )

    years = sorted(
        {
            int(record["year"])
            for record in records
        },
        reverse=True,
    )

    return {
        "years": years
    }


def _get_layer(inputs):
    from app.layers import get_layer

    kind = inputs.get("kind")
    bbox = inputs.get("bbox")
    year = inputs.get("year")

    try:
        return get_layer(
            kind=kind,
            bbox=bbox,
            year=year,
        )

    except Exception as exc:
        message = str(exc)
        lower_message = message.lower()

        if (
            kind in ("aerial", "naip")
            and "coverage" in lower_message
        ):
            county = str(
                inputs.get(
                    "county",
                    ""
                )
            )

            # Alaska is an allowed data gap.
            # Continue without aerial imagery.
            if "alaska" in county.lower():
                return {
                    "coverage_gap": True,
                    "kind": "aerial",
                    "year": year,
                    "reason": (
                        "No aerial coverage was available "
                        "for this Alaska site."
                    ),
                }

            return _human_required(
                (
                    "NO_IMAGERY_FOR_YEAR"
                    if year is not None
                    else "NO_AERIAL_COVERAGE"
                ),
                (
                    f"No aerial imagery is available "
                    f"for the requested year {year}. "
                    f"Which available imagery year should I use?"
                    if year is not None
                    else
                    "No aerial imagery is available for "
                    "this site. Please provide an available "
                    "imagery year or confirm how to proceed."
                ),
                [
                    "Tried to retrieve the requested aerial imagery."
                ],
                [
                    message
                ],
                [
                    "An available imagery year or "
                    "confirmation of how to proceed."
                ],
            )

        raise


def _render(inputs):
    """
    Render exactly ONE map.

    The caller must supply the actual layer returned by get_layer.
    AERIAL requires an aerial/NAIP layer.
    TOPO requires a topo layer.
    """
    from app.render import render

    layer = inputs.get("layer")
    boundary = inputs.get("boundary")
    title = inputs.get("title")
    year = inputs.get("year")
    map_type = str(inputs.get("map_type", "AERIAL")).upper()

    if not isinstance(layer, dict):
        raise ValueError("render requires the actual layer object returned by get_layer.")

    if not isinstance(boundary, dict):
        raise ValueError("render requires the resolved site boundary.")

    if not title:
        raise ValueError("title is required.")

    layer_kind = str(layer.get("kind", "")).lower()

    # HARD GUARD: never render a TOPO map with an aerial layer.
    if map_type == "TOPO":
        if layer_kind not in ("topo", "topographic"):
            raise ValueError(
                "TOPO render requires a topo layer. "
                "An aerial/NAIP layer cannot be used as the topo background."
            )
        year = None

    # HARD GUARD: never render an AERIAL map with a topo layer.
    elif map_type == "AERIAL":
        if layer_kind not in ("aerial", "naip"):
            raise ValueError(
                "AERIAL render requires an aerial/NAIP layer."
            )

    else:
        raise ValueError(
            "map_type must be either AERIAL or TOPO."
        )

    result = render(
        layer=layer,
        boundary=boundary,
        title=title,
        year=year,
        map_type=map_type,
    )

    # Preserve the actual layer metadata in the tool result so the
    # manifest cannot accidentally claim that topo used aerial imagery.
    if isinstance(result, dict):
        result["_render_layer_kind"] = layer_kind
        result["_render_map_type"] = map_type
        result["_render_layer_source"] = layer.get("source")
        result["_render_layer_path"] = layer.get("path")
        result["_render_layer_year"] = layer.get("year")

    return result


def _validate(inputs):
    from app.validate import validate

    return validate(
        inputs.get("output_dir")
    )


# ============================================================
# DISPATCHER
# ============================================================

_TOOL_FUNCTIONS = {
    "resolve_site": _resolve_site,
    "list_years": _list_years,
    "get_layer": _get_layer,
    "render": _render,
    "validate": _validate,
}


def dispatch(tool_name, inputs):
    """
    Dispatch a tool request to the correct Python function.

    Always returns JSON-serializable data.
    """

    if not isinstance(tool_name, str):
        return _error(
            "invalid_request",
            "Tool name must be a string.",
        )

    if not isinstance(inputs, dict):
        return _error(
            "invalid_request",
            "Tool inputs must be a JSON object.",
        )

    function = _TOOL_FUNCTIONS.get(
        tool_name
    )

    if function is None:
        return _error(
            "not_found",
            f"Unknown tool: {tool_name}",
        )

    try:
        result = function(inputs)

        # Human-required results already have
        # the standard Task 10 format.
        if (
            isinstance(result, dict)
            and result.get("status") == "human_required"
        ):
            json.dumps(result)
            return result

        # Ensure the result can actually be
        # serialized as JSON.
        json.dumps(result)

        return _success(result)

    except Exception as exc:
        return _error(
            _classify_error(exc),
            str(exc),
        )