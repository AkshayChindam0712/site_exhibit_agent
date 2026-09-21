#!/usr/bin/env python3
"""
Builds synthetic parcel fixtures for all twenty test sites.

These are FABRICATED parcels. They are not copies of any county's cadastre and
they describe no real ownership. Their job is to let the whole pipeline run
offline -- dissolve, projection, extent, render, validate, manifest -- before
discover_parcels.py has managed to reach a single county service.

Each fixture is a FeatureCollection of parcel polygons in EPSG:4326, laid out
around the site's seed coordinate as a realistic frontage of lots. Adjacent
parcels share exact vertices, so a correct dissolve produces one ring with no
internal boundary. Two sites carry deliberate traps, documented below.

    python scripts/make_parcel_fixtures.py --sites sites/

Swap them out the moment real IDs resolve. A fixture that silently survives
into a deliverable is a bug.
"""

import argparse
import csv
import zlib
import json
import math
import os

M_PER_DEG_LAT = 111_320.0
SQM_PER_ACRE = 4046.8564224

# Parcel-ID patterns per county. These are shape approximations written from
# familiarity with each county's numbering, NOT verified against live services.
# They exist so you can test ID cleaning offline: digit counts, dashes, colons
# and leading zeros are the things that break naive normalisers. Confirm the
# real pattern from the id_field that discover_parcels.py records, and treat a
# mismatch here as expected rather than surprising.
ID_PATTERNS = {
    "Douglas County, NE":              ("##########",                  "digits only, 10"),
    "Lancaster County, NE":            ("##########",                  "digits only, 10"),
    "Johnson County, KS":              ("##-###-##-#-##-##-###-###",   "long dashed quarter-section"),
    "Maricopa County, AZ":             ("###-##-###A",                 "APN with a trailing letter"),
    "Cook County, IL":                 ("##-##-###-###-####",          "14 digits, dashed"),
    "Harris County, TX":               ("#############",               "13-digit account, leading zeros"),
    "Los Angeles County, CA":          ("####-###-###",                "dashed APN"),
    "King County, WA":                 ("##########",                  "10 digits, leading zeros"),
    "Miami-Dade County, FL":           ("##-####-###-####",            "13-digit folio, dashed"),
    "Suffolk County, NY":              ("####-###.##-##.##-###.###",   "SCTM, dots and dashes"),
    "Denver County, CO":               ("##########",                  "schedule number"),
    "Salt Lake County, UT":            ("##-##-###-###-####",          "16 digits, dashed"),
    "Utah County, UT":                 ("##:###:####",                 "colon separated"),
    "Wake County, NC":                 ("#######",                     "7-digit PIN"),
    "Hennepin County, MN":             ("##-###-##-##-####",           "13 digits, dashed"),
    "Bernalillo County, NM":           ("##################",          "18-digit UPC"),
    "Clark County, NV":                ("###-##-###-###",              "dashed APN"),
    "Fairbanks North Star Borough, AK": ("#######",                    "7-digit PAN"),
    "Kauai County, HI":                ("(#) #-#-###:###",             "TMK with zone prefix"),
}
DEFAULT_PATTERN = ("##########", "digits only, 10")


def synth_id(pattern, seed):
    """Fill a pattern with digits derived from seed. Deterministic, fabricated."""
    out, n = [], seed
    for ch in pattern:
        if ch == "#":
            n = (n * 1103515245 + 12345) & 0x7FFFFFFF
            out.append(str((n >> 16) % 10))
        else:
            out.append(ch)
    return "".join(out)


def local_to_wgs84(lat0, lon0, dx, dy):
    lat = lat0 + dy / M_PER_DEG_LAT
    lon = lon0 + dx / (M_PER_DEG_LAT * math.cos(math.radians(lat0)))
    return [round(lon, 7), round(lat, 7)]


def rect(x0, y0, w, h):
    return [(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h)]


def rotate(pts, deg, cx=0.0, cy=0.0):
    a = math.radians(deg)
    ca, sa = math.cos(a), math.sin(a)
    return [((x - cx) * ca - (y - cy) * sa + cx,
             (x - cx) * sa + (y - cy) * ca + cy) for x, y in pts]


def ring_area_m2(pts):
    a = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        a += x1 * y2 - x2 * y1
    return abs(a) / 2.0


def outline_rings(lots, project):
    """
    Analytic union of the lot rectangles, in local metres.

    The lots are axis-aligned before rotation and share exact edges, so the
    union is constructible by hand rather than needing a geometry engine.
    Returns a list of rings: one for a clean site, two where a gap splits it.
    """
    xs = [x for lot in lots for x, _ in lot]
    ys = [y for lot in lots for _, y in lot]

    if project == "PA-2026-0115":                    # L-shape
        a, b, c = lots
        x0 = min(x for x, _ in a)
        x1 = max(x for x, _ in b)
        x2 = max(x for x, _ in c)
        ylo, yhi = min(ys), max(y for _, y in a)
        ystep = max(y for _, y in c)
        return [[(x0, ylo), (x2, ylo), (x2, ystep), (x1, ystep),
                 (x1, yhi), (x0, yhi)]]

    if project == "PA-2026-0111":                    # 20 mm gap, stays split
        return [[(min(x for x, _ in lot), min(y for _, y in lot)),
                 (max(x for x, _ in lot), min(y for _, y in lot)),
                 (max(x for x, _ in lot), max(y for _, y in lot)),
                 (min(x for x, _ in lot), max(y for _, y in lot))]
                for lot in lots]

    return [[(min(xs), min(ys)), (max(xs), min(ys)),
             (max(xs), max(ys)), (min(xs), max(ys))]]


def layout(n_parcels, project):
    """
    Lots along a shared frontage. Neighbours share exact vertices so a correct
    dissolve leaves no internal edge.

    Traps, both documented in the fixture properties:
      PA-2026-0115  three lots in an L, not a strip -- a bbox-based dissolve
                    will swallow land the site does not own.
      PA-2026-0111  a 0.02 m gap between the two lots -- a zero-tolerance
                    union leaves a sliver, which validation should catch.
    """
    depth = 92.0
    widths = {1: [78.0], 2: [64.0, 58.0], 3: [52.0, 46.0, 58.0]}[min(n_parcels, 3)]

    lots, x = [], 0.0
    for i, w in enumerate(widths):
        gap = 0.02 if (project == "PA-2026-0111" and i == 1) else 0.0
        lots.append(rect(x + gap, 0.0, w, depth))
        x += w + gap

    if project == "PA-2026-0115":                    # L-shaped assemblage
        lots[2] = rect(widths[0] + widths[1], 0.0, widths[2], depth * 0.46)

    total_w = x
    return [[(px - total_w / 2, py - depth / 2) for px, py in lot] for lot in lots]


def build(project, name, county, lat, lon, n_parcels, mode):
    pattern, pattern_note = ID_PATTERNS.get(county, DEFAULT_PATTERN)
    # crc32, not hash(): Python randomises hash() per process, which would give
    # different identifiers and rotations on every regeneration.
    bearing = (zlib.crc32(project.encode()) % 34) - 17
    seed = zlib.crc32((project + county).encode()) & 0xFFFF

    lots = layout(n_parcels, project)
    feats = []
    for i, lot in enumerate(lots):
        pts = rotate(lot, bearing)
        coords = [local_to_wgs84(lat, lon, x, y) for x, y in pts]
        coords.append(coords[0])
        acres = ring_area_m2(pts) / SQM_PER_ACRE

        props = {
            "parcel_id": synth_id(pattern, seed + i * 7919),
            "county": county,
            "site_name": name,
            "project_number": project,
            "area_acres": round(acres, 3),
            "synthetic": True,
            "id_pattern": pattern,
            "id_pattern_note": pattern_note,
            "source": "fabricated fixture; replace with county data before use",
        }
        if project == "PA-2026-0111" and i == 1:
            props["trap"] = "0.02 m gap from the adjoining lot; union with zero tolerance leaves a sliver"
        if project == "PA-2026-0115" and i == 2:
            props["trap"] = "L-shaped assemblage; a bbox dissolve over-claims land"
        if project == "PA-2026-0119":
            props["note"] = "NAIP does not cover Alaska; expect an empty historical series"

        feats.append({"type": "Feature", "properties": props,
                      "geometry": {"type": "Polygon", "coordinates": [coords]}})

    rings = outline_rings(lots, project)
    outline = []
    for ring in rings:
        pts = rotate(ring, bearing)
        cs = [local_to_wgs84(lat, lon, x, y) for x, y in pts]
        cs.append(cs[0])
        outline.append([cs])
    outline_acres = sum(ring_area_m2(rotate(r, bearing)) for r in rings) / SQM_PER_ACRE
    outline_geom = ({"type": "Polygon", "coordinates": outline[0]} if len(outline) == 1
                    else {"type": "MultiPolygon", "coordinates": outline})

    return {
        "type": "FeatureCollection",
        "name": f"{project} synthetic parcels",
        "crs": {"type": "name",
                "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "metadata": {
            "synthetic": True,
            "purpose": "offline pipeline fixture",
            "warning": "Fabricated geometry and identifiers. Not a cadastral record. "
                       "Do not publish a figure drawn from these.",
            "input_mode": mode,
            "expected_parcels": n_parcels,
            "expected_total_acres": round(sum(f["properties"]["area_acres"] for f in feats), 3),
            "expected_outline_acres": round(outline_acres, 3),
            "expected_outline_parts": len(outline),
            "expected_outline_vertices": len(outline[0][0]) - 1,
        },
        "site_outline": {
            "type": "Feature",
            "properties": {
                "project_number": project,
                "synthetic": True,
                "derivation": "analytic union of the parcel rectangles",
                "area_acres": round(outline_acres, 3),
                "parts": len(outline),
                "note": ("Compare your dissolve against this. A bounding-box fallback "
                         "will not reproduce it where the assemblage is L-shaped."),
            },
            "geometry": outline_geom,
        },
        "features": feats,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sites", default="sites")
    args = ap.parse_args()

    out_dir = os.path.join(args.sites, "parcel_data")
    os.makedirs(out_dir, exist_ok=True)

    with open(os.path.join(args.sites, "registry.csv"), newline="") as f:
        rows = list(csv.DictReader(f))

    index = {}
    for row in rows:
        proj = row["project_number"]
        n = max(1, int(row["parcels_expected"]))
        fc = build(proj, row["site_name"], row["county"],
                   float(row["seed_latitude"]), float(row["seed_longitude"]),
                   n, row["input_mode"])
        with open(os.path.join(out_dir, f"{proj}.json"), "w") as f:
            json.dump(fc, f, indent=2)
            f.write("\n")
        index[proj] = {
            "county": row["county"],
            "parcel_ids": [ft["properties"]["parcel_id"] for ft in fc["features"]],
            "id_pattern": fc["features"][0]["properties"]["id_pattern"],
            "total_acres": fc["metadata"]["expected_total_acres"],
            "outline_acres": fc["metadata"]["expected_outline_acres"],
            "outline_parts": fc["metadata"]["expected_outline_parts"],
            "traps": [ft["properties"]["trap"] for ft in fc["features"]
                      if "trap" in ft["properties"]],
        }
        parts = fc["metadata"]["expected_outline_parts"]
        print(f"{proj}  {n} parcel(s)  "
              f"{fc['metadata']['expected_total_acres']:6.2f} ac  "
              f"outline {fc['metadata']['expected_outline_acres']:6.2f} ac"
              f"{'  SPLIT' if parts > 1 else '':7}  "
              f"{index[proj]['parcel_ids'][0]}")

    with open(os.path.join(out_dir, "index.json"), "w") as f:
        json.dump({
            "warning": "Every identifier and polygon below is fabricated.",
            "sites": index,
        }, f, indent=2)
    print(f"\nwrote {len(index)} parcel files to {out_dir}")


if __name__ == "__main__":
    main()
