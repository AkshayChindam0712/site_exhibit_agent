# Example inputs

Hand these to whoever is building the tool. Three input styles, all supported.

**The parcel numbers below are synthetic** — correct in format, fabricated in value.
They pair with the polygons in `sites/parcel_data/`, so the whole pipeline runs
offline. Generation is deterministic, so these values do not drift if the
generator is re-run. Use `scripts/discover_parcels.py` to replace them with real
IDs from live county services.

## 1. Parcel numbers

```json
{
  "project_number": "PA-2026-0101",
  "site_name": "Dodge Street Commercial Pad",
  "county": "Douglas County, NE",
  "parcel_ids": [
    "0387581351",
    "6816572479"
  ],
  "latitude": null,
  "longitude": null,
  "boundary_geojson": null,
  "figures": [
    "site_location",
    "site_plan",
    "historical"
  ],
  "output_dir": "./out/PA-2026-0101"
}
```

## 2. Coordinates only

The tool must find the parcel containing the point.

```json
{
  "project_number": "PA-2026-0102",
  "site_name": "Elkhorn Draw Acreage",
  "county": "Douglas County, NE",
  "parcel_ids": null,
  "latitude": 41.286,
  "longitude": -96.236,
  "boundary_geojson": null,
  "figures": [
    "site_location",
    "site_plan",
    "historical"
  ],
  "output_dir": "./out/PA-2026-0102"
}
```

## 3. Boundary polygon

Site Plan only, no parcel lookup at all.

```json
{
  "project_number": "PA-2026-0114",
  "site_name": "Spanish Fork Parkway Pad",
  "county": "Utah County, UT",
  "parcel_ids": null,
  "latitude": null,
  "longitude": null,
  "boundary_geojson": "./sites/parcel_data/PA-2026-0114.json",
  "figures": [
    "site_plan"
  ],
  "output_dir": "./out/PA-2026-0114"
}
```

## Synthetic parcel numbers by county

| Project | County | Parcel numbers | Format | Outline |
|---|---|---|---|---|
| PA-2026-0101 | Douglas County, NE | `0387581351`, `6816572479` | ########## | 2.77 ac |
| PA-2026-0102 | Douglas County, NE | `0417955835` | ########## | 1.77 ac |
| PA-2026-0103 | Lancaster County, NE | `1710344206`, `9039343425` | ########## | 2.77 ac |
| PA-2026-0104 | Johnson County, KS | `84-038-60-0-53-24-894-564` | ##-###-##-#-##-##-###-### | 1.77 ac |
| PA-2026-0105 | Maricopa County, AZ | `044-11-119A`, `666-00-119A`, `210-90-000A` | ###-##-###A | 3.55 ac |
| PA-2026-0106 | Cook County, IL | `34-83-355-062-4804`, `16-12-144-901-6835` | ##-##-###-###-#### | 2.77 ac |
| PA-2026-0107 | Harris County, TX | `8843839391910`, `4192728229235` | ############# | 2.77 ac |
| PA-2026-0108 | Los Angeles County, CA | `7557-504-648` | ####-###-### | 1.77 ac |
| PA-2026-0109 | King County, WA | `0100618844`, `6649617783` | ########## | 2.77 ac |
| PA-2026-0110 | Miami-Dade County, FL | `99-9090-572-8064` | ##-####-###-#### | 1.77 ac |
| PA-2026-0111 | Suffolk County, NY | `3523-796.87-36.94-205.647`, `9752-785.79-28.19-370.086` | ####-###.##-##.##-###.### | 2.77 ac, 2 parts |
| PA-2026-0112 | Denver County, CO | `5434735152`, `1772725080` | ########## | 2.77 ac |
| PA-2026-0113 | Salt Lake County, UT | `61-24-572-279-7223`, `25-53-361-317-9475` | ##-##-###-###-#### | 2.77 ac |
| PA-2026-0114 | Utah County, UT | `45:147:4961` | ##:###:#### | 1.77 ac |
| PA-2026-0115 | Wake County, NC | `9491706`, `5849695`, `1368684` | ####### | 2.83 ac |
| PA-2026-0116 | Hennepin County, MN | `68-809-05-21-7496`, `22-088-94-15-5691` | ##-###-##-##-#### | 2.77 ac |
| PA-2026-0117 | Bernalillo County, NM | `726867106421875926` | ################## | 1.77 ac |
| PA-2026-0118 | Clark County, NV | `352-60-405-231`, `997-40-694-513` | ###-##-###-### | 2.77 ac |
| PA-2026-0119 | Fairbanks North Star Borough, AK | `0143526` | ####### | 1.77 ac |
| PA-2026-0120 | Kauai County, HI | `(6) 4-4-309:743` | (#) #-#-###:### | 1.77 ac |

Formats are written from familiarity with each county's numbering, not verified
against live services. They exist to break a naive ID normaliser offline. Treat a
mismatch against the real `id_field` as expected.

## Deliberate traps

- **PA-2026-0111** — 0.02 m gap from the adjoining lot; union with zero tolerance leaves a sliver
- **PA-2026-0115** — L-shaped assemblage; a bbox dissolve over-claims land
- **PA-2026-0119** — Fairbanks, Alaska. NAIP does not cover Alaska, so the historical
  series must come back empty and be recorded as such, not crash.
- **PA-2026-0111** — coastal; the page runs over open water, which is the only site
  that reliably exercises the UNMAPPED fill.

Six malformed inputs for error handling are in `sites/negative_cases.json`.

Each file in `sites/parcel_data/` also carries a `site_outline` block: the dissolved
boundary with its expected acreage and part count. That is the answer key for Task 1.
