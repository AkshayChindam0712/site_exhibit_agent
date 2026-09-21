#!/usr/bin/env python3
"""
Fills in real parcel IDs for the 20 test sites.

Why this exists
---------------
The site files ship with `parcel_ids: null` and a locality-scale seed coordinate.
Nobody can hand you 20 valid parcel IDs from memory: PINs change with splits and
merges, and a wrong one fails on the first resolve_site call. So the pack
discovers them against live county services instead of asserting them.

For each site this script:
  1. finds a public parcel FeatureServer covering the seed point,
  2. queries the parcel under the point,
  3. optionally pulls N adjacent parcels so multi-parcel sites are genuinely
     adjacent rather than random,
  4. rewrites the site JSON with the real IDs and the true parcel centroid,
  5. records the service URL and ID field in sites/resolved_sources.json.

Run it once after cloning. Re-run whenever a county republishes its layer.

    python scripts/discover_parcels.py --sites sites/
    python scripts/discover_parcels.py --sites sites/ --only PA-2026-0101
    python scripts/discover_parcels.py --sites sites/ --dry-run

Nothing here belongs in your pipeline. resolve_site() is the thing being tested;
this is the fixture builder that gives it real inputs.
"""

import argparse
import csv
import json
import os
import re
import sys
import time
from urllib.parse import urlencode

import requests

HUB_SEARCH = "https://hub.arcgis.com/api/v3/datasets"
UA = {"User-Agent": "site-exhibit-test-pack/1.0 (fixture builder)"}
TIMEOUT = 30

# Field names counties actually use for the parcel identifier, best first.
ID_FIELDS = [
    "PARCEL_ID", "PARCELID", "PIN", "PIN14", "APN", "APN_D", "PARCELNUM",
    "PARCEL_NUM", "PARCELNUMBER", "PROP_ID", "PROPERTY_ID", "ACCOUNT",
    "ACCT_NUM", "TAXPIN", "TAX_ID", "GPIN", "STATE_PARCEL_ID", "PARCELPIN",
]

# Counties whose layer is easier to name directly than to find by search.
# Leave empty to force discovery; add entries as you confirm them.
KNOWN_LAYERS = {
    # "Douglas County, NE": "https://.../FeatureServer/0",
}


def log(msg):
    print(msg, flush=True)


def hub_candidates(county, limit=8):
    """Ask ArcGIS Hub for parcel datasets published by this county."""
    q = urlencode({
        "q": f"{county} parcels",
        "filter[type]": "Feature Service",
        "page[size]": limit,
    })
    try:
        r = requests.get(f"{HUB_SEARCH}?{q}", headers=UA, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json().get("data", [])
    except Exception as exc:
        log(f"    hub search failed: {exc}")
        return []

    out = []
    for d in data:
        a = d.get("attributes", {})
        url = a.get("url") or ""
        name = (a.get("name") or "").lower()
        if not url or "parcel" not in name:
            continue
        if not re.search(r"/(Feature|Map)Server(/\d+)?$", url):
            continue
        if not re.search(r"/\d+$", url):
            url = url.rstrip("/") + "/0"
        out.append((a.get("name"), url))
    return out


def layer_fields(url):
    try:
        r = requests.get(f"{url}?f=json", headers=UA, timeout=TIMEOUT)
        r.raise_for_status()
        return [f["name"].upper() for f in r.json().get("fields", [])]
    except Exception:
        return []


def pick_id_field(fields):
    for want in ID_FIELDS:
        if want in fields:
            return want
    for f in fields:                       # last resort: anything parcel-ish
        if "PARCEL" in f and "ID" in f:
            return f
    return None


def query_point(url, lat, lon, out_fields="*"):
    params = {
        "geometry": json.dumps({"x": lon, "y": lat,
                                "spatialReference": {"wkid": 4326}}),
        "geometryType": "esriGeometryPoint",
        "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": out_fields,
        "returnGeometry": "true",
        "outSR": 4326,
        "f": "json",
    }
    r = requests.get(f"{url}/query", params=params, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json().get("features", [])


def query_neighbours(url, geometry, id_field, exclude, want):
    """Parcels touching the seed parcel's envelope, nearest first."""
    rings = geometry.get("rings") or []
    if not rings:
        return []
    xs = [p[0] for ring in rings for p in ring]
    ys = [p[1] for ring in rings for p in ring]
    pad = 0.0009                                  # roughly 100 m
    env = {"xmin": min(xs) - pad, "ymin": min(ys) - pad,
           "xmax": max(xs) + pad, "ymax": max(ys) + pad,
           "spatialReference": {"wkid": 4326}}
    params = {
        "geometry": json.dumps(env),
        "geometryType": "esriGeometryEnvelope",
        "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": id_field,
        "returnGeometry": "false",
        "resultRecordCount": 40,
        "f": "json",
    }
    r = requests.get(f"{url}/query", params=params, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    ids = []
    for feat in r.json().get("features", []):
        v = feat.get("attributes", {}).get(id_field)
        if v is None:
            continue
        v = str(v).strip()
        if v and v not in exclude and v not in ids:
            ids.append(v)
        if len(ids) >= want:
            break
    return ids


def centroid_of(geometry):
    rings = geometry.get("rings") or []
    pts = [p for ring in rings for p in ring]
    if not pts:
        return None, None
    return (sum(p[1] for p in pts) / len(pts),
            sum(p[0] for p in pts) / len(pts))


def resolve_one(county, lat, lon, want_parcels):
    urls = []
    if county in KNOWN_LAYERS:
        urls.append(("known", KNOWN_LAYERS[county]))
    urls += hub_candidates(county)

    if not urls:
        return None, "no candidate parcel service found"

    for name, url in urls:
        fields = layer_fields(url)
        if not fields:
            continue
        id_field = pick_id_field(fields)
        if not id_field:
            continue
        try:
            feats = query_point(url, lat, lon, out_fields=id_field)
        except Exception as exc:
            log(f"    {name}: query failed ({exc})")
            continue
        if not feats:
            log(f"    {name}: no parcel under the seed point")
            continue

        seed = feats[0]
        pid = str(seed["attributes"].get(id_field, "")).strip()
        if not pid:
            continue
        ids = [pid]
        if want_parcels > 1:
            ids += query_neighbours(url, seed.get("geometry", {}), id_field,
                                    exclude={pid}, want=want_parcels - 1)
        clat, clon = centroid_of(seed.get("geometry", {}))
        return {
            "parcel_ids": ids[:want_parcels],
            "service_url": url,
            "service_name": name,
            "id_field": id_field,
            "latitude": clat if clat is not None else lat,
            "longitude": clon if clon is not None else lon,
        }, None

    return None, "no candidate service returned a parcel at the seed point"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sites", default="sites")
    ap.add_argument("--only", action="append", default=[])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--sleep", type=float, default=1.0)
    args = ap.parse_args()

    reg_path = os.path.join(args.sites, "registry.csv")
    with open(reg_path, newline="") as f:
        registry = list(csv.DictReader(f))

    sources, ok, failed = {}, 0, []

    for row in registry:
        proj = row["project_number"]
        if args.only and proj not in args.only:
            continue
        if row["input_mode"] == "boundary":
            log(f"{proj}  skipped (boundary file input)")
            continue

        want = max(1, int(row["parcels_expected"]))
        log(f"{proj}  {row['county']}  looking for {want} parcel(s)")

        res, err = resolve_one(row["county"], float(row["seed_latitude"]),
                               float(row["seed_longitude"]), want)
        if err:
            log(f"    FAILED: {err}")
            failed.append((proj, row["county"], err))
            time.sleep(args.sleep)
            continue

        log(f"    {res['id_field']} = {', '.join(res['parcel_ids'])}")
        sources[proj] = {k: res[k] for k in
                         ("service_url", "service_name", "id_field")}

        if not args.dry_run:
            path = os.path.join(args.sites, f"{proj}.json")
            with open(path) as f:
                site = json.load(f)
            if row["input_mode"] == "parcel_ids":
                site["parcel_ids"] = res["parcel_ids"]
                site["latitude"] = None
                site["longitude"] = None
            else:                                  # latlong mode
                site["parcel_ids"] = None
                site["latitude"] = round(res["latitude"], 6)
                site["longitude"] = round(res["longitude"], 6)
            with open(path, "w") as f:
                json.dump(site, f, indent=2)
                f.write("\n")
        ok += 1
        time.sleep(args.sleep)

    if not args.dry_run and sources:
        with open(os.path.join(args.sites, "resolved_sources.json"), "w") as f:
            json.dump(sources, f, indent=2)

    log(f"\nresolved {ok}, failed {len(failed)}")
    for proj, county, err in failed:
        log(f"  {proj}  {county}: {err}")
    if failed:
        log("\nFor a failure, open the county's open-data portal, find the parcel")
        log("FeatureServer, and add it to KNOWN_LAYERS at the top of this file.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
