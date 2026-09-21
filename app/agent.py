import json
import time
import re
from pathlib import Path

import requests

from app.tools import TOOL_DEFINITIONS, dispatch

def _request_has_coordinates(request: str) -> bool:
    """
    Return True only when the user explicitly supplied
    latitude/longitude coordinates in the request.
    """
    if not request:
        return False

    # Matches decimal coordinate pairs such as:
    # 41.2565, -95.9345
    # (41.2565, -95.9345)
    # 41.2565 -95.9345
    coordinate_pattern = r"""
        (?:
            [-+]?\d{1,3}(?:\.\d+)?     # latitude
            \s*[,]\s*
            [-+]?\d{1,3}(?:\.\d+)?     # longitude
        )
    """

    return re.search(
        coordinate_pattern,
        request,
        re.VERBOSE
    ) is not None


OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "qwen3:1.7b"

MAX_STEPS = 20
MAX_RETRIES_PER_TOOL = 2

LOG_DIR = Path("output")
LOG_DIR.mkdir(parents=True, exist_ok=True)


SYSTEM_PROMPT = """
You are an AI agent for the site exhibit pipeline.

Complete the user's request by calling the available tools.

IMPORTANT TOOL-CALL RULES:

1. Always call tools using the provided tool definitions.
2. Never invent a tool name.
3. The tool name must exactly match one of:
   resolve_site
   list_years
   get_layer
   render
   validate

4. For resolve_site:
   - Use "parcel_ids" as an array when parcel IDs are provided.
   - Use the full county name and state.
   - For Douglas County Nebraska, use exactly:
     "Douglas County, NE"
   - If the user provides a project number, ALWAYS pass it to
     resolve_site as project_number.
   - If the user provides latitude and longitude, preserve them exactly.
   - Coordinate format must be:
     {
       "coords": {
         "latitude": <latitude>,
         "longitude": <longitude>
       }
     }

5. Never invent:
   - parcel IDs
   - project numbers
   - coordinates
   - file paths
   - imagery years
   - site data

6. If the user provides a project number, always pass it to
   resolve_site as project_number.

7. If the user provides parcel IDs, pass those exact parcel IDs
   as the parcel_ids array.

8. For coordinate-only requests, do not invent parcel IDs.

9. If resolve_site fails for a coordinate-based request,
   do not retry with invented parcel IDs.

10. Do not calculate geometry, area, distance, scale, or other
    measurements yourself. Python performs those calculations.

11. If a tool returns an error, determine whether a safe retry
    is possible.

12. Do not repeatedly retry the same failed tool with the same
    inputs.

13. Continue calling tools until the requested work is complete.

14. When all requested work is complete, call validate.

15. Only report the job as complete if:
    - the requested outputs were generated, and
    - validation passed.

16. If required information is unavailable and cannot be safely
    recovered, stop and clearly report that human input is required.

17. Keep final responses short and clear.

18. Every site exhibit requires exactly two maps:
    - Site Plan = AERIAL
    - Site Location = TOPO
    The TOPO Site Location is mandatory even when the user
    does not explicitly request a topographic map.

19. For render, use the actual layer result returned by get_layer.
    Do not invent or reconstruct the layer object.

20. A TOPO render MUST use the layer returned by get_layer(kind="topo").
    It must NEVER reuse an aerial/NAIP layer.

21. An AERIAL render MUST use the layer returned by
    get_layer(kind="aerial" or "naip").

22. Keep the aerial and topo layer results separate. Do not overwrite one
    with the other.

23e. After get_layer(kind="topo") succeeds, the NEXT tool call MUST be:
     render with map_type="TOPO" and title="Site Location".
     Use the real TOPO layer returned by get_layer.
     Do not call get_layer again.
     Do not call validate before the TOPO render succeeds.

23f. A render call is never valid unless the corresponding
     get_layer call has already succeeded:
     - AERIAL render requires successful get_layer(kind="aerial")
     - TOPO render requires successful get_layer(kind="topo")

23g. Call only ONE tool per model turn.
     Do not return multiple dependent tool calls in the same response.

23a. A successful site exhibit ALWAYS requires BOTH maps:
     1. site plan = AERIAL
     2. site location = TOPO
     The task is NOT complete after the aerial map alone.

23b. Before calling validate, you MUST have successfully:
     - called get_layer(kind="aerial") and rendered the AERIAL site plan
     - called get_layer(kind="topo") and rendered the TOPO site location
     Do not call validate until BOTH renders have succeeded.

23c. After a successful AERIAL render, immediately continue the task by
     retrieving the topo layer and rendering the TOPO site location.
     Do not assume that the aerial render completes the exhibit.

23d. If the controller sends a TOPO_REQUIRED workflow correction, your
     next tool call MUST be get_layer with kind="topo". Do not call
     validate and do not call render first.

24. Never invent coordinates.

25. If the user provides a parcel ID but does not provide
    coordinates, do not add coordinates to resolve_site.

26. If the user provides a project number but does not provide
    coordinates, do not add coordinates to resolve_site.

27. Only pass coordinates to resolve_site when the user
    explicitly supplied those coordinates.

28. If resolve_site fails, stop and do not call list_years,
    get_layer, or render using guessed coordinates or a guessed bbox.

29. list_years may only be called after resolve_site succeeds.
    Always use the bbox returned by the successful resolve_site.

30. For list_years, only use the bbox from the successfully resolved
    site. Never invent a bbox.

31. If list_years reports that an explicitly requested imagery year
    is unavailable, do not substitute another year. Stop and return
    the human-required result.

32. If a tool returns status="human_required", stop immediately and
    return that JSON result. Do not call another tool and do not guess.

IMPORTANT VALIDATE RULES:
- validate.output_dir must ALWAYS be a directory.
- Never pass a .tif, .tiff, .png, .jpg, .jpeg, or .pdf file path to validate.
- Never use the get_layer path as validate.output_dir.
- After render succeeds, validate the output directory produced by render.
- Do not invent an output directory.
"""


def _ollama_request(messages):
    """Send one request to Ollama."""

    response = requests.post(
        OLLAMA_URL,
        json={
            "model": MODEL,
            "messages": messages,
            "tools": TOOL_DEFINITIONS,
            "stream": False,
            "think": False,
            "options": {
                "temperature": 0,
                "num_predict": 300,
            },
        },
        timeout=120,
    )

    if not response.ok:
        print("\n========== OLLAMA ERROR ==========")
        print("Status:", response.status_code)
        print("Response:", response.text)

    response.raise_for_status()
    return response.json()


def _extract_tool_calls(message):
    """Return tool calls from an Ollama message."""

    tool_calls = message.get("tool_calls", [])

    if not isinstance(tool_calls, list):
        return []

    return tool_calls


def _get_tool_name(tool_call):
    """Extract tool name safely."""

    function = tool_call.get("function", {})

    if not isinstance(function, dict):
        return None

    return function.get("name")


def _get_tool_arguments(tool_call):
    """Extract and normalize tool arguments."""

    function = tool_call.get("function", {})

    if not isinstance(function, dict):
        return {}

    arguments = function.get("arguments", {})

    if isinstance(arguments, str):
        try:
            return json.loads(arguments)
        except json.JSONDecodeError:
            return {}

    if isinstance(arguments, dict):
        return arguments

    return {}


def _is_successful_result(result):
    """Return True only when a tool result represents success."""

    if not isinstance(result, dict):
        return False

    if result.get("ok") is False:
        return False

    if "error" in result:
        return False

    data = result.get("data")

    if isinstance(data, dict):
        if data.get("valid") is False:
            return False

    return True


def _write_log(log_path, log_data):
    """Write the complete agent log as JSON."""

    log_path.parent.mkdir(parents=True, exist_ok=True)

    with log_path.open("w", encoding="utf-8") as file:
        json.dump(log_data, file, indent=2, default=str)


def _workflow_correction(message):
    """Return a controller-generated correction for Qwen's tool order."""
    return {
        "ok": False,
        "error_code": "WORKFLOW_ORDER",
        "message": message,
        "instruction": (
            "This is a controller correction, not a human-required failure. "
            "Follow the instruction and continue the tool loop."
        ),
    }


def _requested_imagery_year(request):
    """
    Return an imagery year only when the user explicitly associates
    a year with imagery/aerial/photo/NAIP wording.

    A project number such as PA-2026-0101 contains 2026, but that
    is NOT an imagery year.
    """
    if not request:
        return None

    # Remove project numbers before looking for imagery years.
    cleaned = re.sub(
        r"\bPA-\d{4}-\d{4}\b",
        "",
        request,
        flags=re.IGNORECASE,
    )

    patterns = [
        r"(?:imagery|aerial|photo|photography|naip)"
        r"(?:\s+imagery)?"
        r"(?:\s+(?:from|for|of|year))?"
        r"\s+(19\d{2}|20\d{2})\b",

        r"\b(19\d{2}|20\d{2})\s+"
        r"(?:imagery|aerial|photo|photography|naip)\b",

        r"\b(?:year|from)\s+"
        r"(19\d{2}|20\d{2})\b"
        r"(?:\s+(?:imagery|aerial|photo|photography|naip))?",
    ]

    for pattern in patterns:
        match = re.search(pattern, cleaned, re.IGNORECASE)
        if match:
            return int(match.group(1))

    return None


def run_agent(request, log_path=None):

    if log_path is None:
        log_path = LOG_DIR / "agent_run.json"

    log_path = Path(log_path)

    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT,
        },
        {
            "role": "user",
            "content": request,
        },
    ]

    log_data = {
        "request": request,
        "model": MODEL,
        "max_steps": MAX_STEPS,
        "max_retries_per_tool": MAX_RETRIES_PER_TOOL,
        "steps": [],
        "status": "running",
    }

    retry_counts = {}
    final_text = ""
    validation_passed = False
    requested_year = _requested_imagery_year(request)

    # Store successful tool results so later tools can use the
    # actual Python-generated objects rather than model-reconstructed data.
    tool_results = {}

    for step_number in range(1, MAX_STEPS + 1):

        step_started = time.perf_counter()

        try:
            print("\n========== OLLAMA REQUEST ==========")
            print("MODEL:", MODEL)

            result = _ollama_request(messages)

            print("========== OLLAMA RESPONSE ==========")
            print(json.dumps(result, indent=2))

        except Exception as exc:
            runtime = time.perf_counter() - step_started

            log_data["steps"].append(
                {
                    "step": step_number,
                    "type": "model",
                    "result": {
                        "error": {
                            "code": "model_error",
                            "message": str(exc),
                        }
                    },
                    "runtime_seconds": round(runtime, 4),
                }
            )

            log_data["status"] = "failed"
            log_data["error"] = {
                "code": "model_error",
                "message": "The model could not be reached.",
            }

            _write_log(log_path, log_data)

            return {
                "status": "failed",
                "error": log_data["error"],
                "log": str(log_path),
            }

        message = result.get("message", {})

        if not isinstance(message, dict):
            message = {}

        tool_calls = _extract_tool_calls(message)
        final_text = message.get("content", "")

        # Process only one tool call per Qwen turn.
        if len(tool_calls) > 1:
            tool_calls = [tool_calls[0]]
            message = dict(message)
            message["tool_calls"] = tool_calls

        if not tool_calls and not final_text.strip():
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "You must continue the task. "
                        "Select the appropriate tool and call it now. "
                        "Do not return an empty response."
                    ),
                }
            )
            continue

        messages.append(message)

        model_runtime = time.perf_counter() - step_started

        log_data["steps"].append(
            {
                "step": step_number,
                "type": "model",
                "result": final_text,
                "tool_calls": [
                    {
                        "tool": _get_tool_name(tc),
                        "arguments": _get_tool_arguments(tc),
                    }
                    for tc in tool_calls
                ],
                "runtime_seconds": round(model_runtime, 4),
            }
        )

        # If the model returned no tool call, it can only be considered
        # complete when validation has already passed.
        if not tool_calls:

            if validation_passed:
                log_data["status"] = "complete"
                log_data["final_response"] = final_text

                _write_log(log_path, log_data)

                return {
                    "status": "complete",
                    "response": final_text,
                    "log": str(log_path),
                }

            log_data["status"] = "failed"
            log_data["final_response"] = final_text
            log_data["error"] = {
                "code": "incomplete",
                "message": (
                    "The model stopped before successfully "
                    "validating the requested output."
                ),
            }

            _write_log(log_path, log_data)

            return {
                "status": "failed",
                "error": log_data["error"],
                "response": final_text,
                "log": str(log_path),
            }

        # ----------------------------------------------------
        # Process tool calls
        # ----------------------------------------------------
        workflow_corrected = False
        for tool_call in tool_calls:

            runtime_started = time.perf_counter()

            tool_name = _get_tool_name(tool_call)
            arguments = _get_tool_arguments(tool_call)

            if not isinstance(arguments, dict):
                arguments = {}
            # If the user supplied a project number, make sure it is
            # passed to resolve_site even if the small model omitted it.
            # If the user supplied a project number, make sure it is
            # passed to resolve_site even if the small model omitted it.
            if tool_name == "resolve_site":
                # ---------------------------------------------------------
                # Protect resolve_site arguments from model hallucination
                # ---------------------------------------------------------

                # 1. Never allow the model to invent coordinates.
                #    Coordinates are passed only when the user explicitly
                #    supplied them in the original request.
                if not _request_has_coordinates(request):
                    arguments.pop("coords", None)

                # 2. Never allow the model to invent parcel IDs.
                #    Extract parcel IDs directly from the user's request.
                explicit_parcels = re.findall(r"\b\d{10}\b", request)

                if explicit_parcels:
                    arguments["parcel_ids"] = explicit_parcels

                # 3. If the user supplied parcel IDs but the model did not
                #    provide them, force the IDs from the original request.
                elif "parcel_ids" in arguments:
                    arguments.pop("parcel_ids", None)

                # 4. If the user supplied a project number, force the
                #    project number from the original request.
                project_match = re.search(
                    r"\bPA-\d{4}-\d{4}\b",
                    request,
                    re.IGNORECASE
                )

                if project_match:
                    arguments["project_number"] = project_match.group(0).upper()

                # 5. Do not allow the model to continue with invented
                #    coordinates when a parcel was explicitly supplied.
                if explicit_parcels and not _request_has_coordinates(request):
                    arguments.pop("coords", None)

            # For list_years, always use the actual bbox from the
            # successfully resolved site. Never trust a model-invented bbox.
            if tool_name == "get_layer":
                site_info = tool_results.get("resolve_site")
                if (
                    site_info
                    and isinstance(site_info.get("data"), dict)
                ):
                    site_data = site_info["data"]
                    if isinstance(site_data.get("bbox"), list):
                        arguments["bbox"] = site_data["bbox"]
                    if site_data.get("county"):
                        arguments["county"] = site_data["county"]

            if tool_name == "list_years":
                site_info = tool_results.get("resolve_site")

                if (
                    site_info
                    and isinstance(site_info.get("data"), dict)
                    and isinstance(
                        site_info["data"].get("bbox"),
                        list,
                    )
                ):
                    arguments["bbox"] = site_info["data"]["bbox"]

                else:
                    tool_result = {
                        "ok": False,
                        "status": "human_required",
                        "error_code": "MISSING_SITE_INFORMATION",
                        "question": (
                            "I need a resolved site before I can check "
                            "imagery availability. Please provide the "
                            "parcel ID, coordinates, or project number."
                        ),
                        "tried": [
                            "Tried to check the requested imagery years."
                        ],
                        "found": [
                            "No successfully resolved site is available."
                        ],
                        "needs_from_human": [
                            "Parcel ID, coordinates, or project number."
                        ],
                    }

                    log_data["status"] = "human_required"
                    log_data["question"] = tool_result
                    _write_log(log_path, log_data)
                    return tool_result

            # For render, NEVER use the most recent get_layer blindly.
            # Keep aerial and topo layers separate and inject the correct
            # real layer based on the requested map type.
            if tool_name == "render":

                map_type = str(
                    arguments.get("map_type", "AERIAL")
                ).upper()

                if map_type == "TOPO":
                    layer_info = tool_results.get("get_layer_topo")
                    required_kind = ("topo", "topographic")
                    arguments["map_type"] = "TOPO"
                    arguments["year"] = None

                else:
                    required_kind = ("aerial", "naip")
                    arguments["map_type"] = "AERIAL"

                    requested_render_year = arguments.get("year")
                    aerial_by_year = tool_results.get(
                        "get_layer_aerial_by_year",
                        {}
                    )

                    if requested_render_year is not None:
                        layer_info = aerial_by_year.get(
                            str(requested_render_year)
                        )
                    else:
                        layer_info = aerial_by_year.get("current")

                    # Backward-compatible fallback to the latest successful
                    # aerial layer if this is a normal/current render.
                    if (
                        layer_info is None
                        and requested_render_year is None
                    ):
                        layer_info = tool_results.get(
                            "get_layer_aerial"
                        )

                if not (
                    layer_info
                    and isinstance(layer_info.get("data"), dict)
                ):
                    if map_type == "TOPO":
                        instruction = (
                            "WORKFLOW_CORRECTION: The TOPO layer has not been loaded. "
                            "Your NEXT tool call MUST be get_layer with kind='topo'. "
                            "Do not call render yet. "
                            "After get_layer succeeds, call render with "
                            "map_type='TOPO' and title='Site Location'. "
                            "Do not call validate yet."
                        )
                    else:
                        instruction = (
                            "WORKFLOW_CORRECTION: The AERIAL layer has not been loaded. "
                            "Your NEXT tool call MUST be get_layer with kind='aerial'. "
                            "Do not call render yet. "
                            "After get_layer succeeds, call render with "
                            "map_type='AERIAL' and title='Site Plan'. "
                            "Do not call validate yet."
                        )

                    tool_result = _workflow_correction(instruction)

                    print("\n==============================")
                    print("WORKFLOW CORRECTION")
                    print("==============================")
                    print(json.dumps(tool_result, indent=2))

                    messages.append(
                        {
                            "role": "tool",
                            "content": json.dumps(
                                tool_result,
                                default=str,
                            ),
                        }
                    )

                    workflow_corrected = True
                    break

                actual_layer = layer_info["data"]
                actual_kind = str(actual_layer.get("kind", "")).lower()

                if actual_kind not in required_kind:
                    tool_result = {
                        "ok": False,
                        "status": "human_required",
                        "error_code": "WRONG_LAYER_FOR_MAP",
                        "question": (
                            f"The {map_type} render received the wrong "
                            f"background layer ({actual_kind}). "
                            "I will not substitute a different layer."
                        ),
                        "tried": [
                            f"Loaded a layer for the {map_type} render."
                        ],
                        "found": [
                            f"Loaded layer kind: {actual_kind}."
                        ],
                        "needs_from_human": [
                            f"A valid {map_type} layer."
                        ],
                    }
                    log_data["status"] = "human_required"
                    log_data["question"] = tool_result
                    _write_log(log_path, log_data)
                    return tool_result

                arguments["layer"] = actual_layer

                site_info = tool_results.get("resolve_site")

                if (
                    site_info
                    and isinstance(site_info.get("data"), dict)
                ):
                    site_data = site_info["data"]

                    if "geometry_geojson" in site_data:
                        arguments["boundary"] = site_data["geometry_geojson"]

                    arguments["site_info"] = site_data
                    
            if tool_name == "validate":

                # Validation is only allowed after BOTH required maps exist.
                # This is a safety/workflow guard: Qwen still decides to call
                # get_layer/render; Python only prevents premature validation.
                aerial_rendered = bool(
                    tool_results.get("render_aerial")
                )
                topo_rendered = bool(
                    tool_results.get("render_topo")
                )

                if not aerial_rendered or not topo_rendered:
                    missing = []

                    if not aerial_rendered:
                        missing.append(
                            "AERIAL site plan has not been rendered"
                        )

                    if not topo_rendered:
                        missing.append(
                            "TOPO site location has not been rendered"
                        )

                    tool_result = _workflow_correction(
                        "Do not validate yet. "
                        + "; ".join(missing)
                        + ". "
                        "Continue the task by calling the required get_layer "
                        "tool and then render the missing map. "
                        "Both AERIAL and TOPO renders are mandatory."
                    )

                    runtime = time.perf_counter() - runtime_started

                    log_data["steps"].append(
                        {
                            "step": step_number,
                            "type": "tool",
                            "requested_tool": tool_name,
                            "inputs": {},
                            "returned_result": tool_result,
                            "runtime_seconds": round(runtime, 4),
                        }
                    )

                    print("\n==============================")
                    print("TOOL RESULT")
                    print("==============================")
                    print(json.dumps(
                        tool_result,
                        indent=2,
                        default=str,
                    ))

                    messages.append(
                        {
                            "role": "tool",
                            "content": json.dumps(
                                tool_result,
                                default=str,
                            ),
                        }
                    )

                    _write_log(log_path, log_data)

                    # Skip the real validate dispatch and let Qwen continue.
                    continue

                # Render creates the exhibit files in output/.
                arguments = {
                    "output_dir": "output"
                }

            if not tool_name:

                tool_result = {
                    "error": {
                        "code": "invalid_request",
                        "message": (
                            "The model returned an invalid tool call. "
                            "It must provide a valid tool name."
                        ),
                    }
                }

                runtime = time.perf_counter() - runtime_started

                log_data["steps"].append(
                    {
                        "step": step_number,
                        "type": "tool",
                        "requested_tool": None,
                        "inputs": arguments,
                        "returned_result": tool_result,
                        "runtime_seconds": round(runtime, 4),
                    }
                )

                log_data["status"] = "failed"
                log_data["error"] = tool_result["error"]

                _write_log(log_path, log_data)

                return {
                    "status": "failed",
                    "error": tool_result["error"],
                    "log": str(log_path),
                }

            retry_key = json.dumps(
                {
                    "tool": tool_name,
                    "inputs": arguments,
                },
                sort_keys=True,
            )

            retry_counts.setdefault(retry_key, 0)

            if retry_counts[retry_key] >= MAX_RETRIES_PER_TOOL:

                tool_result = {
                    "error": {
                        "code": "retry_limit",
                        "message": (
                            f"Maximum retries reached for request "
                            f"'{tool_name}'."
                        ),
                    }
                }

            else:

                print("\n==============================")
                print("TOOL CALL")
                print("==============================")
                print("Tool:", tool_name)
                print(
                    "Arguments:",
                    json.dumps(
                        arguments,
                        indent=2,
                        default=str,
                    ),
                )

                tool_result = dispatch(
                    tool_name,
                    arguments,
                )

                # Task 10: an explicitly requested imagery year must
                # exist in the actual site's discovered years.
                if (
                    tool_name == "list_years"
                    and _is_successful_result(tool_result)
                    and requested_year is not None
                ):
                    years_data = tool_result.get("data", {})
                    available_years = (
                        years_data.get("years", [])
                        if isinstance(years_data, dict)
                        else []
                    )

                    if requested_year not in available_years:
                        tool_result = {
                            "ok": False,
                            "status": "human_required",
                            "error_code": "NO_IMAGERY_FOR_YEAR",
                            "question": (
                                f"No aerial imagery is available for "
                                f"the requested year {requested_year}. "
                                f"Which available imagery year should I use?"
                            ),
                            "tried": [
                                "Resolved the site.",
                                "Checked available aerial imagery years "
                                "for the resolved site."
                            ],
                            "found": [
                                (
                                    f"Available imagery years: "
                                    f"{available_years}"
                                )
                            ],
                            "needs_from_human": [
                                "An available imagery year."
                            ],
                        }

                if (
                    isinstance(tool_result, dict)
                    and tool_result.get("status") == "human_required"
                ):
                    log_data["status"] = "human_required"
                    log_data["question"] = tool_result

                    _write_log(
                        log_path,
                        log_data
                    )

                    return tool_result

                # Count failed attempts.
                if not _is_successful_result(tool_result):
                    retry_counts[retry_key] += 1

            runtime = time.perf_counter() - runtime_started

            # Save successful tool results for later tool calls.
            if _is_successful_result(tool_result):
                tool_results[tool_name] = tool_result

                # IMPORTANT: keep aerial and topo layers separate.
                # A topo render must never accidentally receive the
                # last aerial layer returned by get_layer.
                if tool_name == "get_layer":
                    layer_data = tool_result.get("data", {})
                    layer_kind = (
                        str(layer_data.get("kind", "")).lower()
                        if isinstance(layer_data, dict)
                        else ""
                    )

                    if layer_kind in ("aerial", "naip"):
                        tool_results["get_layer_aerial"] = tool_result

                        aerial_by_year = tool_results.setdefault(
                            "get_layer_aerial_by_year",
                            {}
                        )

                        requested_layer_year = arguments.get("year")

                        returned_layer_year = (
                            layer_data.get("year")
                            if isinstance(layer_data, dict)
                            else None
                        )

                        # Keep the exact requested/returned year mapped to
                        # the real Python-generated layer.
                        if requested_layer_year is not None:
                            aerial_by_year[
                                str(requested_layer_year)
                            ] = tool_result

                        if returned_layer_year is not None:
                            aerial_by_year[
                                str(returned_layer_year)
                            ] = tool_result

                        # A non-historical/current aerial layer is also
                        # available under the "current" key.
                        if returned_layer_year is not None:
                            aerial_by_year["current"] = tool_result

                    elif layer_kind in ("topo", "topographic"):
                        tool_results["get_layer_topo"] = tool_result
                        
            # Track the two mandatory rendered maps independently.
            if (
                tool_name == "render"
                and _is_successful_result(tool_result)
            ):
                rendered_map_type = str(
                    arguments.get("map_type", "AERIAL")
                ).upper()

                if rendered_map_type == "AERIAL":
                    tool_results["render_aerial"] = tool_result

                elif rendered_map_type == "TOPO":
                    tool_results["render_topo"] = tool_result


# After AERIAL succeeds, explicitly tell Qwen that the
# mandatory TOPO map is still required.
            if (
                tool_name == "render"
                and _is_successful_result(tool_result)
                and str(arguments.get("map_type", "")).upper() == "AERIAL"
            ):
                if not tool_results.get("render_topo"):

                    topo_instruction = {
                        "ok": False,
                        "error_code": "TOPO_REQUIRED",
                        "message": (
                            "The AERIAL site plan has been rendered successfully, "
                            "but the site exhibit is NOT complete."
                        ),
                        "instruction": (
                            "You must now retrieve the TOPO layer. "
                            "Call get_layer with kind='topo'. "
                            "The Python controller will supply the bbox from "
                            "the successful resolve_site result, so do not "
                            "invent or change the bbox. "
                            "After get_layer succeeds, call render with "
                            "map_type='TOPO' for the site location. "
                            "Use the real layer returned by get_layer. "
                            "Do NOT call validate until the TOPO render succeeds."
                        ),
                    }

                    print("\n==============================")
                    print("WORKFLOW INSTRUCTION")
                    print("==============================")
                    print(json.dumps(topo_instruction, indent=2))

                    messages.append(
                        {
                            "role": "user",
                            "content": json.dumps(
                                topo_instruction,
                                default=str,
                            ),
                        }
                    )

            # validate returns ok=True with data.valid=False when
            # validation itself ran but the output did not pass.
            if (
                tool_name == "validate"
                and _is_successful_result(tool_result)
            ):
                validation_passed = True

            step_log = {
                "step": step_number,
                "type": "tool",
                "requested_tool": tool_name,
                "inputs": arguments,
                "returned_result": tool_result,
                "runtime_seconds": round(runtime, 4),
            }

            log_data["steps"].append(step_log)

            print("\n==============================")
            print("TOOL RESULT")
            print("==============================")
            print(
                json.dumps(
                    tool_result,
                    indent=2,
                    default=str,
                )
            )

            # Send the exact Python result back to the model.
            messages.append(
                {
                    "role": "tool",
                    "content": json.dumps(
                        tool_result,
                        default=str,
                    ),
                }
            )
            # After successful TOPO retrieval, force the next Qwen turn
            # to render the TOPO map.
            if (
                tool_name == "get_layer"
                and _is_successful_result(tool_result)
                and isinstance(tool_result.get("data"), dict)
                and str(
                    tool_result["data"].get("kind", "")
                ).lower() in ("topo", "topographic")
                and not tool_results.get("render_topo")
            ):
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "TOPO_LAYER_READY. "
                            "The TOPO layer was retrieved successfully. "
                            "Your NEXT tool call MUST be render. "
                            "Use map_type='TOPO'. "
                            "Use title='Site Location'. "
                            "Use the real TOPO layer returned by get_layer. "
                            "Do not call get_layer again. "
                            "Do not call validate yet."
                        ),
                    }
                )

        if workflow_corrected:
            _write_log(log_path, log_data)
            continue

        # Save progress after every tool round.
        log_data["status"] = "running"
        _write_log(log_path, log_data)

    # --------------------------------------------------------
    # Maximum steps reached
    # --------------------------------------------------------

    log_data["status"] = "failed"
    log_data["final_response"] = final_text
    log_data["error"] = {
        "code": "max_steps",
        "message": (
            f"Agent stopped after reaching the maximum of "
            f"{MAX_STEPS} steps."
        ),
    }

    _write_log(log_path, log_data)

    return {
        "status": "failed",
        "error": log_data["error"],
        "response": final_text,
        "log": str(log_path),
    }


if __name__ == "__main__":

    request = input(
        "\nEnter site exhibit request:\n> "
    )

    result = run_agent(request)

    print("\n==============================")
    print("AGENT RESULT")
    print("==============================")

    print(
        json.dumps(
            result,
            indent=2,
            default=str,
        )
    )
