# Exhibit checklist

Task 3 step 12 asks for this list. Every element below must be drawn by
`render()` and recorded in the manifest, because Task 6 step 28 validates
against the recorded values rather than against pixels.

Compare against `samples/sample_site_plan.png`.

## Required on every page

| # | Element | Recorded as | Notes |
|---|---------|-------------|-------|
| 1 | Background image | `layer.kind`, `layer.source` | topo or aerial, filling the map frame |
| 2 | Site outline | `boundary.geojson_sha1` | red, ~2.4 pt, no fill, closed |
| 3 | SITE LOCATION callout | `callout.anchor`, `callout.position` | leader must touch the outline and must not cross it |
| 4 | North arrow | `north_arrow.position` | grid north; note declination only if you rotate the frame |
| 5 | Scale bar | `scale_bar.total_ft`, `scale_bar.frame_width_m` | 0–2,000 ft, derived from ground width in metres |
| 6 | Year | `year` | omit on Site Location, required on Site Plan and every historical page |
| 7 | Title block | `title_block.*` | project number, site name, county, scale, projection, source, photo date, figure number |
| 8 | Frame border | — | 1.1 pt, encloses the map only, not the title block |

## Conditional

| # | Element | Fires when |
|---|---------|-----------|
| 9 | UNMAPPED grey fill | imagery does not cover the whole frame (step 22) |
| 10 | RMS error in title block | page uses georeferenced historical scans (Task 13) |
| 11 | Missing-year list | historical PDF is assembled with gaps |

## Checks worth automating

- Callout box does not overlap the site outline bounding box.
- Scale bar length in inches equals `total_ft / (map_scale / 12)` within 1%.
- Site outline lies wholly inside the frame, with at least 15% margin on every side.
- Input coordinates, when supplied, fall inside the outline.
- Every figure in `figures` produced both a PNG and a PDF.
- Site fills roughly half the page: outline bbox is 30–70% of frame width.

## A note on scale selection

The brief suggests rounding to a standard scale such as 1:2,400 and showing a
0–2,000 ft bar. Those two requirements collide. At 1:2,400 one paper inch is
200 ft, so a 7.5 in frame spans 1,500 ft and a 2,000 ft bar runs off the page.

Scale selection has to be bar-aware. Pick the smallest standard scale from
1:1,200, 1:2,400, 1:4,800, 1:6,000, 1:12,000 that satisfies both: the site fills
about half the frame, and the bar fits inside about 70% of the frame width. The
samples use 1:6,000, where the bar occupies 53% of a 3,750 ft frame. Drop to a
shorter bar (0–500 ft or 0–1,000 ft) for small urban sites rather than forcing
2,000 ft onto a tight page, and record the chosen bar length in the manifest so
validation checks the bar that was actually drawn.
