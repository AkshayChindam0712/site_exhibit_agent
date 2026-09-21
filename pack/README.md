# Site exhibit test pack

Twenty test sites, a fixture builder, three reference exhibit layouts, the
checklist, and a manifest schema — sized to the assignment in
`Site_Exhibit_Agent_Tasks_Simplified.docx`.

```
Site_Exhibit_Agent_Tasks_Refined.docx   the task document (PDF alongside)
sites/          20 request files in the brief's JSON shape, plus registry and negative cases
sites/parcel_data/  20 self-contained parcel files: IDs, lot polygons, dissolved outline
scripts/        discover_parcels.py (fixture builder), make_sample_exhibits.py (the renderer)
samples/        three reference pages, 300 DPI PNG plus US Letter PDF
schema/         manifest.schema.json for Task 6 step 29
checklist.md    Task 3 step 12
```

## Start here

`Site_Exhibit_Agent_Tasks_Refined.docx` is the task document, in the same plain
style and voice as the original. Part A builds the pipeline: two kinds of map,
topographic and aerial, saved as PDFs, with an aerial page for every year
available. Part B does the same job with an agent choosing the steps. Task 11 is
the acceptance test — run both modes over all twenty sites and compare the
manifests.

The vision-model change tables, the automatic visual map check, and the OpenCV
georeferencing stretch task have been removed.

`sites/EXAMPLE_INPUTS.md` is the one-page handout: the three input styles with
worked JSON, the synthetic parcel numbers by county, and the deliberate traps.

## Read this before you run anything

**The parcel IDs are not in the files yet, on purpose.** Every site ships with
`parcel_ids: null` and a seed latitude/longitude at locality scale. PINs change
whenever a county splits or merges a lot, so a list typed from memory is a list
of numbers that fail on the first `resolve_site` call — worse than no list,
because the failures look like bugs in your code.

`scripts/discover_parcels.py` finds a public parcel FeatureServer for each
county, queries the parcel under the seed point, pulls genuinely adjacent
parcels for the multi-parcel sites, and rewrites the site files with real IDs.

```bash
pip install requests
python scripts/discover_parcels.py --sites sites/ --dry-run   # look first
python scripts/discover_parcels.py --sites sites/             # then write
```

Expect a few counties to fail. Some publish parcels behind a portal rather than
an open FeatureServer, and some rename layers. When one fails, find the layer on
that county's open-data site and add it to `KNOWN_LAYERS` at the top of the
script. Working through those failures is genuinely part of Task 1 step 3.

The seed coordinates are city-scale, accurate to a few kilometres. They exist to
land inside the right county, not to identify a property. The script overwrites
them with real parcel centroids.

## The twenty sites

Full detail in `sites/registry.csv`. They are chosen for coverage, not variety
for its own sake — each one breaks something different.

| Project | County | Tests |
|---|---|---|
| PA-2026-0101 | Douglas, NE | the brief's worked example; two parcels dissolve to one outline |
| PA-2026-0102 | Douglas, NE | coordinates only, point-in-parcel lookup |
| PA-2026-0103 | Lancaster, NE | second county |
| PA-2026-0104 | Johnson, KS | third county, clears the "at least three" bar |
| PA-2026-0105 | Maricopa, AZ | huge layer, forces paging past `maxRecordCount` |
| PA-2026-0106 | Cook, IL | 14-digit PIN with dashes |
| PA-2026-0107 | Harris, TX | leading zeros that must survive ID cleaning |
| PA-2026-0108 | Los Angeles, CA | dashed APN, UTM 11N |
| PA-2026-0109 | King, WA | UTM 10N |
| PA-2026-0110 | Miami-Dade, FL | UTM 17N, dense canopy |
| PA-2026-0111 | Suffolk, NY | coastal, triggers the UNMAPPED fill |
| PA-2026-0112 | Denver, CO | real relief under the outline |
| PA-2026-0113 | Salt Lake, UT | UTM 12N |
| PA-2026-0114 | Utah, UT | boundary file only, Site Plan only |
| PA-2026-0115 | Wake, NC | three adjacent parcels, dissolve must leave no slivers |
| PA-2026-0116 | Hennepin, MN | irregular NAIP years |
| PA-2026-0117 | Bernalillo, NM | low-contrast arid imagery, probes "unclear" |
| PA-2026-0118 | Clark, NV | extreme change across the historical series |
| PA-2026-0119 | Fairbanks North Star, AK | **no NAIP coverage at all** |
| PA-2026-0120 | Kauai, HI | UTM 4N, sparse imagery, catches CONUS assumptions |

Three matter more than the rest:

- **0119 (Alaska)** is the one that finds hard failures. NAIP is a lower-48
  programme, so `list_years` returns nothing. The run must produce a Site
  Location map, skip the historical series, and record the gap — not crash and
  not silently emit an empty PDF.
- **0114** is the only boundary-file, single-figure request. It is the third
  request style Task 9 step 41 asks you to test, and it catches pipelines that
  assume every run needs parcels.
- **0111** is the only one that reliably produces an incompletely covered page,
  which is the sole way to exercise step 22.

`sites/negative_cases.json` holds six malformed inputs for the "clear error
naming the bad ID" requirement, so you can test that without spending one of
the twenty.

## Parcel data, one file per site

`sites/parcel_data/` holds one self-contained JSON per site. Each carries the
parcel identifiers, the individual lot polygons, and the dissolved site outline
with its expected acreage and vertex count, so
the pipeline runs end to end before `discover_parcels.py` reaches a single
county service. Dissolve, reprojection, extent selection, rendering, validation
and the manifest can all be exercised with no network at all.

```bash
python scripts/make_parcel_data.py --sites sites/   # deterministic, safe to re-run
```

Every polygon and every identifier in there is fabricated. They are not copies
of any county's cadastre and they describe no real ownership. `index.json`
lists what each fixture contains; `samples/parcel_data_preview.png` shows all
twenty on one sheet, lots shaded and the dissolved outline dashed in red.

The `site_outline` block in each file is the answer key. Compare your dissolve
against `expected_outline_acres` and `expected_outline_parts` rather than just
checking that a polygon came back.

Neighbouring lots share exact vertices, so a correct dissolve yields one ring
with no internal edge. Two fixtures break that on purpose:

- **PA-2026-0115** is an L-shaped three-lot assemblage rather than a strip. A
  dissolve that falls back to a bounding box will claim land the site does not
  own, and the area check will pass anyway unless you compare against
  `expected_total_acres`.
- **PA-2026-0111** leaves a 0.02 m gap between its two lots. A zero-tolerance
  union leaves a hairline sliver. Decide your snapping tolerance deliberately
  and record it, rather than discovering it on a real assemblage.

The identifiers follow per-county patterns — dashes, colons, leading zeros,
trailing letters — which is the point: they are there to break a naive
normaliser offline. **Those patterns are written from familiarity, not verified
against live services.** Treat them as shapes to test cleaning against, not as
ground truth. `discover_parcels.py` records the real `id_field` each county
uses, and a mismatch against the pattern here is expected rather than alarming.

Fixtures must never reach a deliverable. Have `validate()` fail hard when any
feature carries `"synthetic": true`, and have the manifest record
`site_outline.source` so a fixture-derived figure is obvious in review.

## The sample exhibits

`samples/sample_output_package.pdf` is the one to show people. Eight US Letter
pages in the order a finished run should assemble them: Site Location on topo,
Site Plan on the current aerial, then six historical aerial pages newest first,
with the oldest showing a coverage gap. The years are deliberately irregular —
2018, 2012, 2006, 1997, 1988, 1976 — because NAIP coverage is irregular.

Alongside it are the single pages as 300 DPI PNG and PDF: Site Location, Site
Plan, and one historical page showing the UNMAPPED treatment.

**The backgrounds are procedurally generated, not real imagery.** They carry a
visible watermark. Match the frame — margins, title block, callout, north arrow,
scale-bar geometry — and get the pixels from USGS and NAIP yourself. Generating
them rather than clipping real tiles keeps the pack free of any third-party
image licence, and the brief tells you not to reuse its sample images anyway.

`scripts/make_sample_exhibits.py` is the renderer. The parts worth lifting into
your Task 3 `render()`: the page constants, `draw_scale_bar` (which derives bar
length from real ground width in metres, never pixels), and `draw_title_block`.

```bash
pip install matplotlib numpy pillow
python scripts/make_sample_exhibits.py --out ../samples
```

It ends with the same assertion Task 6 step 27 should make, and fails loudly if
the bar geometry drifts.

## One thing the brief gets wrong

Task 3 step 14 wants a 0–2,000 ft scale bar; step 15 suggests rounding to a
standard scale such as 1:2,400. At 1:2,400 a 7.5 in map frame spans 1,500 ft, so
a 2,000 ft bar does not fit on US Letter. Scale and bar length have to be chosen
together. `checklist.md` sets out a rule that satisfies both, and the samples
use 1:6,000, where the bar takes 53% of the frame. Worth raising before you
hard-code 1:2,400.

## Site names

The twenty names are synthetic project labels. They describe land use in neutral
terms and are not tied to any real owner, address, or environmental condition —
the seed coordinates are city-scale, so no specific property is identified. If
you swap in real project names, keep that separation in anything you publish.
