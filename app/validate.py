"""
Task 6 - Output Validation

Run from project root:

    python -m app.validate output

Checks:
1. manifest.json exists and is valid JSON
2. manifest.json conforms to manifest.schema.json
3. generated map output files exist
4. site outline contains supplied input coordinates
5. recorded scale/bbox values are consistent
6. render recorded every required checklist element
7. synthetic/fabricated parcel geometry is rejected
8. validation_report.json is written
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

try:
    from jsonschema import Draft7Validator
except ImportError:
    Draft7Validator = None

try:
    from shapely.geometry import Point, shape
except ImportError:
    Point = None
    shape = None


# ================================================================
# REQUIRED RENDER ELEMENTS
# ================================================================

REQUIRED_ELEMENTS = {
    "site_plan": {
        "image",
        "red site outline",
        "SITE LOCATION callout",
        "north arrow",
        "scale bar",
        "title block",
    },

    "site_location": {
        "image",
        "red site outline",
        "SITE LOCATION callout",
        "north arrow",
        "scale bar",
        "title block",
    },
}


# ================================================================
# BASIC HELPERS
# ================================================================

def load_json(path: Path):
    with path.open(
        "r",
        encoding="utf-8-sig",
    ) as f:
        return json.load(f)


def as_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalise_bbox(value):
    """
    Accept a list/tuple:

        [minx, miny, maxx, maxy]
    """

    if not isinstance(
        value,
        (list, tuple),
    ):
        return None

    if len(value) != 4:
        return None

    values = [
        as_float(v)
        for v in value
    ]

    if any(
        v is None
        for v in values
    ):
        return None

    minx, miny, maxx, maxy = values

    if minx > maxx:
        minx, maxx = maxx, minx

    if miny > maxy:
        miny, maxy = maxy, miny

    return (
        minx,
        miny,
        maxx,
        maxy,
    )


def scale_denominator(value):
    """
    Converts:

        '1:1200' -> 1200
        '1200'   -> 1200
        1200     -> 1200
    """

    if value is None:
        return None

    if isinstance(
        value,
        str,
    ):
        value = value.strip()

        if ":" in value:
            value = value.split(
                ":",
                1,
            )[1]

        value = value.replace(
            ",",
            "",
        )

    return as_float(value)


def resolve_path(
    output_dir: Path,
    value,
):
    """
    Convert a manifest path to an actual filesystem path.

    IMPORTANT:
    Only strings are accepted.

    This prevents values such as:

        300
        2022
        True
        None
        [bbox]

    from being treated as filenames.
    """

    if not isinstance(
        value,
        str,
    ):
        return None

    value = value.strip()

    if not value:
        return None

    path = Path(value)

    if path.is_absolute():
        return path

    return output_dir / path


# ================================================================
# CHECK 1
# ================================================================

def check_manifest(output_dir):
    manifest_path = (
        output_dir / "manifest.json"
    )

    if not manifest_path.exists():

        return (
            False,
            "manifest.json was not found.",
            {},
            None,
        )

    try:
        manifest = load_json(
            manifest_path
        )

    except Exception as exc:

        return (
            False,
            f"manifest.json could not be parsed: {exc}",
            {},
            None,
        )

    if not isinstance(
        manifest,
        dict,
    ):

        return (
            False,
            "manifest.json root must be an object.",
            {},
            None,
        )

    return (
        True,
        "manifest.json was read successfully.",
        {
            "path": str(
                manifest_path
            )
        },
        manifest,
    )


# ================================================================
# CHECK 2 - JSON SCHEMA
# ================================================================

def check_schema(
    manifest,
):
    schema_path = (
        Path(__file__).parent
        / "manifest.schema.json"
    )

    if not schema_path.exists():

        return (
            False,
            f"Schema file not found: {schema_path}",
            {},
        )

    try:
        schema = load_json(
            schema_path
        )

    except Exception as exc:

        return (
            False,
            f"Schema could not be parsed: {exc}",
            {
                "schema": str(
                    schema_path
                )
            },
        )

    if Draft7Validator is None:

        return (
            False,
            "jsonschema is not installed. "
            "Run: python -m pip install jsonschema",
            {},
        )

    try:

        validator = Draft7Validator(
            schema
        )

        errors = sorted(
            validator.iter_errors(
                manifest
            ),
            key=lambda error: list(
                error.absolute_path
            ),
        )

    except Exception as exc:

        return (
            False,
            f"Schema validation failed: {exc}",
            {},
        )

    if errors:

        messages = []

        for error in errors:

            location = ".".join(
                str(x)
                for x in error.absolute_path
            )

            if not location:
                location = "<root>"

            messages.append(
                f"{location}: {error.message}"
            )

        return (
            False,
            "manifest.json does not conform to schema.",
            {
                "errors": messages
            },
        )

    return (
        True,
        "manifest.json conforms to manifest.schema.json.",
        {
            "schema": str(
                schema_path
            )
        },
    )


# ================================================================
# OUTPUT FILE EXTRACTION
# ================================================================
def extract_output_paths(output_dir, map_entry):
    """
    Extract actual generated map files from the project's
    manifest structure.

    Current manifest structure:

        site_plan:
            outputs:
                aerial_png
                aerial_pdf
                topo_png
                topo_pdf

        site_location:
            outputs:
                aerial_png
                aerial_pdf
                topo_png
                topo_pdf
    """

    paths = []

    if not isinstance(map_entry, dict):
        return paths

    outputs = map_entry.get("outputs")

    if not isinstance(outputs, dict):
        return paths

    for key in (
        "aerial_png",
        "aerial_pdf",
        "topo_png",
        "topo_pdf",
    ):

        value = outputs.get(key)

        if not isinstance(value, str):
            continue

        path = Path(value)

        if not path.is_absolute():
            path = output_dir / path

        paths.append(path)

    # Remove duplicates
    unique = []
    seen = set()

    for path in paths:

        key = str(path).lower()

        if key not in seen:
            seen.add(key)
            unique.append(path)

    return unique
# ================================================================
# CHECK 3 - GENERATED MAPS
# ================================================================

def check_generated_maps(
    output_dir,
    manifest,
):
    found = []
    missing = []

    map_names = (
        "site_plan",
        "site_location",
        "historical_aerial",
    )

    entries_found = 0

    for map_name in map_names:

        entry = manifest.get(
            map_name
        )

        if not isinstance(
            entry,
            dict,
        ):
            continue

        entries_found += 1

        paths = extract_output_paths(
            output_dir,
            entry,
        )

        for path in paths:

            if (
                path.exists()
                and path.is_file()
            ):

                found.append(
                    str(path)
                )

            else:

                missing.append(
                    f"{map_name}: {path}"
                )

    found = list(
        dict.fromkeys(
            found
        )
    )

    missing = list(
        dict.fromkeys(
            missing
        )
    )

    if missing:

        return (
            False,
            "Generated map files are missing.",
            {
                "found": found,
                "missing": missing,
            },
        )

    if not found:

        return (
            False,
            "No generated map output files were found "
            "in the manifest.",
            {
                "found": [],
                "missing": [],
                "entries_found": entries_found,
            },
        )

    return (
        True,
        "All referenced generated map files exist.",
        {
            "found": found,
            "missing": [],
        },
    )


# ================================================================
# CHECK 4 - INPUT COORDINATES
# ================================================================

def find_input_coordinates(
    manifest,
):
    candidates = []

    for key in (
        "input_coordinates",
        "coordinates",
        "input_points",
        "site_coordinates",
    ):

        if key in manifest:
            candidates.append(
                manifest[key]
            )

    site = manifest.get(
        "site"
    )

    if isinstance(
        site,
        dict,
    ):

        for key in (
            "input_coordinates",
            "coordinates",
            "input_points",
            "site_coordinates",
        ):

            if key in site:
                candidates.append(
                    site[key]
                )

    points = []

    def visit(value):

        if isinstance(
            value,
            dict,
        ):

            if (
                "x" in value
                and "y" in value
            ):

                x = as_float(
                    value["x"]
                )

                y = as_float(
                    value["y"]
                )

                if (
                    x is not None
                    and y is not None
                ):

                    points.append(
                        (
                            x,
                            y,
                        )
                    )

            elif (
                "lon" in value
                and "lat" in value
            ):

                x = as_float(
                    value["lon"]
                )

                y = as_float(
                    value["lat"]
                )

                if (
                    x is not None
                    and y is not None
                ):

                    points.append(
                        (
                            x,
                            y,
                        )
                    )

            for child in value.values():
                visit(child)

        elif isinstance(
            value,
            (list, tuple),
        ):

            if (
                len(value) == 2
                and as_float(value[0])
                is not None
                and as_float(value[1])
                is not None
            ):

                points.append(
                    (
                        float(value[0]),
                        float(value[1]),
                    )
                )

            else:

                for child in value:
                    visit(child)

    for candidate in candidates:
        visit(candidate)

    return list(
        dict.fromkeys(
            points
        )
    )


def find_outline(
    manifest,
):
    candidates = [
        manifest.get(
            "geometry_geojson"
        ),
        manifest.get(
            "site_geometry"
        ),
        manifest.get(
            "site_outline"
        ),
    ]

    site = manifest.get(
        "site"
    )

    if isinstance(
        site,
        dict,
    ):

        candidates.extend(
            [
                site.get(
                    "geometry_geojson"
                ),
                site.get(
                    "geometry"
                ),
                site.get(
                    "site_outline"
                ),
            ]
        )

    for candidate in candidates:

        if not isinstance(
            candidate,
            dict,
        ):
            continue

        if candidate.get(
            "type"
        ) == "Feature":

            return candidate.get(
                "geometry"
            )

        if candidate.get(
            "type"
        ) in (
            "Polygon",
            "MultiPolygon",
        ):

            return candidate

        if isinstance(
            candidate.get(
                "geometry"
            ),
            dict,
        ):

            return candidate[
                "geometry"
            ]

    return None


def check_site_outline(
    manifest,
):
    coordinates = find_input_coordinates(
        manifest
    )

    if not coordinates:

        return (
            True,
            "SKIPPED: no input coordinates were provided.",
            {
                "skipped": True
            },
        )

    geometry_data = find_outline(
        manifest
    )

    if geometry_data is None:

        return (
            False,
            "Input coordinates were provided, "
            "but no site outline was found.",
            {
                "coordinates": coordinates
            },
        )

    if (
        shape is None
        or Point is None
    ):

        return (
            False,
            "Shapely is required for coordinate containment.",
            {},
        )

    try:

        geometry = shape(
            geometry_data
        )

    except Exception as exc:

        return (
            False,
            f"Invalid site outline geometry: {exc}",
            {},
        )

    outside = []

    for x, y in coordinates:

        point = Point(
            x,
            y,
        )

        if not (
            geometry.contains(
                point
            )
            or geometry.touches(
                point
            )
        ):

            outside.append(
                [
                    x,
                    y,
                ]
            )

    if outside:

        return (
            False,
            "One or more input coordinates are outside "
            "the site outline.",
            {
                "outside": outside
            },
        )

    return (
        True,
        "All input coordinates are contained "
        "by the site outline.",
        {
            "coordinates_checked": len(
                coordinates
            )
        },
    )


# ================================================================
# CHECK 5 - SCALE BAR
# ================================================================

def check_scale_bar(
    manifest,
):
    site_plan = manifest.get(
        "site_plan"
    )

    if not isinstance(
        site_plan,
        dict,
    ):

        return (
            False,
            "site_plan metadata was not found.",
            {},
        )

    scale = site_plan.get(
        "scale"
    )

    bar_length = site_plan.get(
        "scale_bar_length_metres"
    )

    if bar_length is None:

        bar_length = site_plan.get(
            "scale_bar_metres"
        )

    bbox = site_plan.get(
        "bbox"
    )

    denominator = scale_denominator(
        scale
    )

    bar_metres = as_float(
        bar_length
    )

    bbox = normalise_bbox(
        bbox
    )

    if (
        denominator is None
        or bar_metres is None
        or bbox is None
    ):

        return (
            False,
            "No recorded scale/bbox values were available.",
            {
                "scale": scale,
                "scale_bar_length_metres": bar_length,
                "bbox": site_plan.get(
                    "bbox"
                ),
            },
        )

    minx, miny, maxx, maxy = bbox

    map_width = (
        maxx - minx
    )

    if map_width <= 0:

        return (
            False,
            "Recorded map bbox has invalid width.",
            {
                "bbox": bbox
            },
        )

    if bar_metres <= 0:

        return (
            False,
            "Scale bar length must be greater than zero.",
            {
                "scale_bar_length_metres":
                    bar_metres
            },
        )

    # A scale bar cannot be larger than the real-world
    # width represented by the map.
    if bar_metres > (
        map_width * 1.01
    ):

        return (
            False,
            "Scale bar is larger than the recorded "
            "real-world map width.",
            {
                "scale_bar_length_metres":
                    bar_metres,
                "map_width_metres":
                    map_width,
            },
        )

    return (
        True,
        "Recorded scale, scale-bar length and map "
        "width are consistent.",
        {
            "scale": scale,
            "scale_denominator":
                denominator,
            "scale_bar_length_metres":
                bar_metres,
            "map_width_metres":
                map_width,
        },
    )


# ================================================================
# CHECK 6 - RENDER CHECKLIST
# ================================================================
def find_elements_drawn(
    entry,
):
    if not isinstance(
        entry,
        dict,
    ):
        return None

    for key in (
        "elements_drawn",
        "render_elements",
        "drawn_elements",
    ):

        value = entry.get(key)

        # ----------------------------------------------------
        # LIST FORMAT
        # ----------------------------------------------------

        if isinstance(
            value,
            list,
        ):

            return [
                str(item).strip()
                for item in value
            ]

        # ----------------------------------------------------
        # DICT FORMAT FROM render.py
        # ----------------------------------------------------

        if isinstance(
            value,
            dict,
        ):

            mapping = {
                "background_image":
                    "image",

                "site_outline":
                    "red site outline",

                "site_location_callout":
                    "SITE LOCATION callout",

                "north_arrow":
                    "north arrow",

                "scale_bar":
                    "scale bar",

                "title_block":
                    "title block",

            }

            recorded = []

            for render_key, checklist_name in mapping.items():

                if value.get(render_key) is True:

                    recorded.append(
                        checklist_name
                    )

            return recorded

    outputs = entry.get(
        "outputs"
    )

    if isinstance(
        outputs,
        dict,
    ):

        for key in (
            "elements_drawn",
            "render_elements",
            "drawn_elements",
        ):

            value = outputs.get(key)

            if isinstance(
                value,
                list,
            ):

                return [
                    str(item).strip()
                    for item in value
                ]

            if isinstance(
                value,
                dict,
            ):

                mapping = {
                    "background_image":
                        "image",

                    "site_outline":
                        "red site outline",

                    "site_location_callout":
                        "SITE LOCATION callout",

                    "north_arrow":
                        "north arrow",

                    "scale_bar":
                        "scale bar",

                    "title_block":
                        "title block",
                }

                recorded = []

                for render_key, checklist_name in mapping.items():

                    if value.get(render_key) is True:

                        recorded.append(
                            checklist_name
                        )

                return recorded

    return None

def check_render_checklist(
    manifest,
):
    failures = []
    results = {}

    for map_name, required in (
        REQUIRED_ELEMENTS.items()
    ):

        entry = manifest.get(
            map_name
        )

        if not isinstance(
            entry,
            dict,
        ):
            continue

        recorded = find_elements_drawn(
            entry
        )

        if recorded is None:

            failures.append(
                f"{map_name}: render did not "
                "record elements_drawn"
            )

            results[map_name] = {
                "required":
                    sorted(required),
                "recorded":
                    None,
                "missing":
                    sorted(required),
            }

            continue

        recorded_lower = {
            item.lower()
            for item in recorded
        }

        missing = sorted(
            item
            for item in required
            if item.lower()
            not in recorded_lower
        )

        results[map_name] = {
            "required":
                sorted(required),
            "recorded":
                sorted(recorded),
            "missing":
                missing,
        }

        if missing:

            failures.append(
                f"{map_name}: missing "
                + ", ".join(missing)
            )

    if failures:

        return (
            False,
            "Checklist validation failed.",
            {
                "failures":
                    failures,
                "maps":
                    results,
            },
        )

    return (
        True,
        "Every required checklist element "
        "was recorded by render.",
        {
            "maps":
                results
        },
    )


# ================================================================
# CHECK 7 - SYNTHETIC PARCEL
# ================================================================

def check_synthetic_parcel(
    manifest,
):
    locations = []

    def walk(
        value,
        location="$",
    ):

        if isinstance(
            value,
            dict,
        ):

            for key, child in value.items():

                if (
                    str(key).lower()
                    == "synthetic"
                    and child is True
                ):

                    locations.append(
                        location
                        + ".synthetic"
                    )

                walk(
                    child,
                    location
                    + "."
                    + str(key),
                )

        elif isinstance(
            value,
            list,
        ):

            for index, child in enumerate(
                value
            ):

                walk(
                    child,
                    f"{location}[{index}]",
                )

        elif isinstance(
            value,
            str,
        ):

            text = value.lower()

            forbidden = (
                "synthetic parcel",
                "synthetic geometry",
                "fabricated parcel",
                "fabricated fixture",
            )

            for phrase in forbidden:

                if phrase in text:

                    locations.append(
                        location
                    )

                    break

    walk(
        manifest
    )

    if locations:

        return (
            False,
            "Synthetic/fabricated parcel geometry "
            "was found.",
            {
                "locations":
                    locations
            },
        )

    return (
        True,
        "No synthetic/fabricated parcel marker was found.",
        {
            "locations": []
        },
    )


# ================================================================
# MAIN VALIDATOR
# ================================================================

def validate(
    output_dir,
):
    output_dir = Path(
        output_dir
    ).resolve()

    checks = []

    # ------------------------------------------------------------
    # Manifest
    # ------------------------------------------------------------

    ok, message, details, manifest = (
        check_manifest(
            output_dir
        )
    )

    checks.append(
        {
            "check":
                "manifest",
            "status":
                "PASS" if ok else "FAIL",
            "message":
                message,
            "details":
                details,
        }
    )

    if manifest is None:
        manifest = {}

    # ------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------

    schema_ok, schema_message, schema_details = (
        check_schema(
            manifest
        )
    )

    checks.append(
        {
            "check":
                "manifest JSON schema",
            "status":
                "PASS"
                if schema_ok
                else "FAIL",
            "message":
                schema_message,
            "details":
                schema_details,
        }
    )

    # ------------------------------------------------------------
    # Generated maps
    # ------------------------------------------------------------

    maps_ok, maps_message, maps_details = (
        check_generated_maps(
            output_dir,
            manifest,
        )
    )

    checks.append(
        {
            "check":
                "generated maps",
            "status":
                "PASS"
                if maps_ok
                else "FAIL",
            "message":
                maps_message,
            "details":
                maps_details,
        }
    )

    # ------------------------------------------------------------
    # Site outline
    # ------------------------------------------------------------

    outline_ok, outline_message, outline_details = (
        check_site_outline(
            manifest
        )
    )

    checks.append(
        {
            "check":
                "site outline contains input coordinates",
            "status":
                "PASS"
                if outline_ok
                else "FAIL",
            "message":
                outline_message,
            "details":
                outline_details,
        }
    )

    # ------------------------------------------------------------
    # Scale
    # ------------------------------------------------------------

    scale_ok, scale_message, scale_details = (
        check_scale_bar(
            manifest
        )
    )

    checks.append(
        {
            "check":
                "scale bar",
            "status":
                "PASS"
                if scale_ok
                else "FAIL",
            "message":
                scale_message,
            "details":
                scale_details,
        }
    )

    # ------------------------------------------------------------
    # Render checklist
    # ------------------------------------------------------------

    checklist_ok, checklist_message, checklist_details = (
        check_render_checklist(
            manifest
        )
    )

    checks.append(
        {
            "check":
                "render checklist",
            "status":
                "PASS"
                if checklist_ok
                else "FAIL",
            "message":
                checklist_message,
            "details":
                checklist_details,
        }
    )

    # ------------------------------------------------------------
    # Synthetic parcel
    # ------------------------------------------------------------

    synthetic_ok, synthetic_message, synthetic_details = (
        check_synthetic_parcel(
            manifest
        )
    )

    checks.append(
        {
            "check":
                "synthetic parcel rejection",
            "status":
                "PASS"
                if synthetic_ok
                else "FAIL",
            "message":
                synthetic_message,
            "details":
                synthetic_details,
        }
    )

    # ------------------------------------------------------------
    # Overall
    # ------------------------------------------------------------

    valid = all(
        check["status"] == "PASS"
        for check in checks
    )

    report = {
        "valid":
            valid,
        "output_dir":
            str(output_dir),
        "checks":
            checks,
    }

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    report_path = (
        output_dir
        / "validation_report.json"
    )

    with report_path.open(
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            report,
            f,
            indent=2,
            ensure_ascii=False,
        )

    # ------------------------------------------------------------
    # PRINT
    # ------------------------------------------------------------

    print()
    print("=" * 70)
    print("TASK 6 VALIDATION")
    print("=" * 70)

    for check in checks:

        print(
            f"[{check['status']}] "
            f"{check['check']}: "
            f"{check['message']}"
        )

        if (
            check["status"] == "FAIL"
            and check.get("details")
        ):

            details = check[
                "details"
            ]

            for key, value in details.items():

                if value:

                    print(
                        f"    {key}: {value}"
                    )

    print()
    print("=" * 70)

    if valid:
        print(
            "VALIDATION: PASS"
        )
    else:
        print(
            "VALIDATION: FAIL"
        )

    print("=" * 70)

    print(
        "Report:",
        report_path,
    )

    return report


# ================================================================
# COMMAND LINE
# ================================================================

if __name__ == "__main__":

    output_dir = (
        sys.argv[1]
        if len(sys.argv) > 1
        else "output"
    )

    try:

        report = validate(
            output_dir
        )

        sys.exit(
            0
            if report["valid"]
            else 1
        )

    except Exception as exc:

        print()
        print("=" * 70)
        print("TASK 6 VALIDATION ERROR")
        print("=" * 70)

        print(
            type(exc).__name__,
            ":",
            exc,
        )

        sys.exit(1)