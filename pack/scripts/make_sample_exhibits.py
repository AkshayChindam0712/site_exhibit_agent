"""
Renders the three reference exhibit layouts for the Site Exhibit assignment.

These are LAYOUT references, not data products. The backgrounds are procedurally
generated so that nothing here can be mistaken for real USGS or NAIP imagery, and
so the pack carries no third-party image licence. Match the frame, not the pixels.

The frame geometry, the scale-bar maths and the title block are the parts worth
copying into your render() in Task 3.

    python make_sample_exhibits.py --out ../samples
"""

import argparse
import io
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import FancyArrowPatch, Polygon, Rectangle
from PIL import Image, ImageFilter

# --------------------------------------------------------------------------
# Page + frame constants.  US Letter portrait, 300 DPI.
# --------------------------------------------------------------------------
DPI = 300
PAGE_W_IN, PAGE_H_IN = 8.5, 11.0

MARGIN_IN = 0.5
FRAME_W_IN = PAGE_W_IN - 2 * MARGIN_IN          # 7.50 in of map
TITLE_H_IN = 1.35
FRAME_H_IN = PAGE_H_IN - 2 * MARGIN_IN - TITLE_H_IN - 0.12

# Map scale.  1:6000 means 1 inch on paper = 500 ft on the ground.
# NOTE: the brief suggests 1:2,400, but at 1:2,400 a 7.5 in frame spans only
# 1,500 ft, so the required 0-2,000 ft scale bar will not fit on the page.
# Scale selection has to be bar-aware -- see README.
MAP_SCALE = 6000
FT_PER_IN = MAP_SCALE / 12.0                     # 500 ft per paper inch
FRAME_W_FT = FRAME_W_IN * FT_PER_IN              # 3,750 ft
FRAME_H_FT = FRAME_H_IN * FT_PER_IN

M_PER_FT = 0.3048
FRAME_W_M = FRAME_W_FT * M_PER_FT

RED = "#E11B22"
INK = "#141414"
RULE = "#5A5A5A"


# --------------------------------------------------------------------------
# Procedural backgrounds
# --------------------------------------------------------------------------
def _smooth(a, radius):
    im = Image.fromarray((a * 255).clip(0, 255).astype("uint8"))
    im = im.filter(ImageFilter.GaussianBlur(radius))
    return np.asarray(im).astype("float32") / 255.0


def _terrain(w, h, seed):
    rng = np.random.default_rng(seed)
    field = np.zeros((h, w), dtype="float32")
    for octave, weight in ((60, 1.0), (24, 0.45), (9, 0.2)):
        field += weight * _smooth(rng.random((h, w)).astype("float32"), octave)
    field -= field.min()
    return field / field.max()


def topo_background(w, h, seed=7):
    """Cream sheet, brown contours, blue drainage, section grid, roads."""
    rgb = np.ones((h, w, 3), dtype="float32")
    rgb[:] = np.array([0.985, 0.976, 0.953])

    z = _terrain(w, h, seed)

    # contour lines every 5% of relief
    bands = np.floor(z * 22.0)
    edges = np.abs(np.gradient(bands)[0]) + np.abs(np.gradient(bands)[1])
    contour = np.clip(edges, 0, 1)
    contour = _smooth(contour, 0.6)
    rgb *= (1.0 - 0.55 * contour[..., None] * np.array([0.15, 0.45, 0.62]))

    # index contours, heavier
    idx = np.abs(np.gradient(np.floor(z * 4.4))[0]) + np.abs(np.gradient(np.floor(z * 4.4))[1])
    idx = _smooth(np.clip(idx, 0, 1), 0.8)
    rgb *= (1.0 - 0.75 * idx[..., None] * np.array([0.20, 0.55, 0.70]))

    # wooded areas from the low ground
    wood = _smooth((z < 0.36).astype("float32"), 6)
    rgb = rgb * (1 - 0.30 * wood[..., None]) + 0.30 * wood[..., None] * np.array([0.72, 0.86, 0.70])

    # drainage following the valley floor
    yy, xx = np.mgrid[0:h, 0:w]
    stream = np.exp(-((xx - (0.30 * w + 0.16 * w * np.sin(yy / (h / 3.1)))) ** 2) / (2 * (w * 0.0045) ** 2))
    rgb = rgb * (1 - stream[..., None]) + stream[..., None] * np.array([0.42, 0.62, 0.84])

    # section grid (public land survey), thin grey
    for frac in (0.5,):
        c = int(frac * w)
        rgb[:, c - 1:c + 1] *= 0.86
        r = int(frac * h)
        rgb[r - 1:r + 1, :] *= 0.86

    # roads
    def road(vert, frac, width, casing=True):
        if vert:
            c = int(frac * w)
            sl = (slice(None), slice(c - width, c + width))
            cs = (slice(None), slice(c - width - 2, c + width + 2))
        else:
            r = int(frac * h)
            sl = (slice(r - width, r + width), slice(None))
            cs = (slice(r - width - 2, r + width + 2), slice(None))
        if casing:
            rgb[cs] = np.array([0.55, 0.52, 0.50])
        rgb[sl] = np.array([1.0, 1.0, 1.0])

    road(False, 0.62, 5)
    road(True, 0.44, 5)
    road(False, 0.22, 3)
    road(True, 0.80, 3)

    return np.clip(rgb, 0, 1)


def aerial_background(w, h, seed=3, grayscale=False, era="modern"):
    """Procedural farmland / light industrial edge, in the palette of leaf-on NAIP."""
    rng = np.random.default_rng(seed)
    z = _terrain(w, h, seed + 11)

    rgb = np.zeros((h, w, 3), dtype="float32")
    base_soil = np.array([0.62, 0.57, 0.44])
    base_veg = np.array([0.32, 0.42, 0.24])
    mix = _smooth(rng.random((h, w)).astype("float32"), 18)
    mix = (mix - mix.min()) / (mix.max() - mix.min())
    rgb = base_soil * (1 - mix[..., None]) + base_veg * mix[..., None]

    # agricultural blocks with distinct tones
    blocks = 6
    for i in range(blocks):
        for j in range(blocks):
            if rng.random() < 0.45:
                continue
            y0, y1 = int(i * h / blocks), int((i + 1) * h / blocks)
            x0, x1 = int(j * w / blocks), int((j + 1) * w / blocks)
            tone = rng.uniform(0.75, 1.25)
            hue = rng.choice([0, 1], p=[0.55, 0.45])
            patch = rgb[y0:y1, x0:x1] * tone
            if hue:
                patch = patch * np.array([0.85, 1.10, 0.80])
            rgb[y0:y1, x0:x1] = patch

    # field texture
    tex = _smooth(rng.random((h, w)).astype("float32"), 1.2)
    rgb *= (0.88 + 0.24 * tex[..., None])

    # tree canopy along the drainage
    yy, xx = np.mgrid[0:h, 0:w]
    corridor = np.exp(-((xx - (0.30 * w + 0.16 * w * np.sin(yy / (h / 3.1)))) ** 2) / (2 * (w * 0.020) ** 2))
    canopy = corridor * _smooth(rng.random((h, w)).astype("float32"), 2.4)
    canopy = np.clip(canopy * 2.4, 0, 1)
    rgb = rgb * (1 - canopy[..., None]) + canopy[..., None] * np.array([0.16, 0.26, 0.15])

    # roads
    def road(vert, frac, width, tone=0.68):
        if vert:
            c = int(frac * w)
            rgb[:, c - width:c + width] = tone
        else:
            r = int(frac * h)
            rgb[r - width:r + width, :] = tone

    road(False, 0.62, 5)
    road(True, 0.44, 5)
    road(False, 0.22, 2, 0.60)

    # buildings, only in the modern era
    if era == "modern":
        for _ in range(14):
            bw = int(rng.uniform(0.018, 0.060) * w)
            bh = int(rng.uniform(0.014, 0.040) * h)
            x0 = int(rng.uniform(0.05, 0.90) * w)
            y0 = int(rng.uniform(0.05, 0.90) * h)
            rgb[y0:y0 + bh, x0:x0 + bw] = rng.uniform(0.72, 0.92)
            rgb[y0 + bh:y0 + bh + 3, x0 + 3:x0 + bw + 3] *= 0.55   # shadow
    else:
        for _ in range(3):
            bw = int(rng.uniform(0.012, 0.024) * w)
            bh = int(rng.uniform(0.010, 0.020) * h)
            x0 = int(rng.uniform(0.20, 0.75) * w)
            y0 = int(rng.uniform(0.20, 0.75) * h)
            rgb[y0:y0 + bh, x0:x0 + bw] = rng.uniform(0.70, 0.88)

    rgb = np.clip(rgb * (0.94 + 0.10 * z[..., None]), 0, 1)

    if grayscale:
        g = (rgb * np.array([0.299, 0.587, 0.114])).sum(axis=2)
        g = np.clip((g - 0.5) * 0.82 + 0.52, 0, 1)      # flatter, like a scan
        grain = np.random.default_rng(seed).normal(0, 0.022, (h, w))
        g = np.clip(g + grain, 0, 1)
        rgb = np.dstack([g, g, g])

    return rgb


# --------------------------------------------------------------------------
# Frame furniture
# --------------------------------------------------------------------------
def draw_north_arrow(ax, x, y, size=0.055):
    """x, y in axes fraction. Simple filled needle with an N above it."""
    ax.add_patch(Rectangle((x - size * 0.62, y - size * 0.18), size * 1.24, size * 1.62,
                           transform=ax.transAxes, facecolor="white", edgecolor=INK,
                           linewidth=0.8, zorder=20))
    ax.add_patch(Polygon([[x, y + size * 1.10], [x - size * 0.30, y + size * 0.10],
                          [x, y + size * 0.32], [x + size * 0.30, y + size * 0.10]],
                         closed=True, transform=ax.transAxes, facecolor=INK,
                         edgecolor=INK, linewidth=0.6, zorder=21))
    ax.text(x, y - size * 0.06, "N", transform=ax.transAxes, ha="center", va="bottom",
            fontsize=9, fontweight="bold", color=INK, zorder=21)


def draw_scale_bar(ax, frame_w_m, total_ft=2000, y=0.035):
    """
    Ground-truth scale bar.  Length is derived from the real map width in metres,
    never from a pixel count -- this is the check Task 6 step 27 runs against.
    """
    total_m = total_ft * M_PER_FT
    frac = total_m / frame_w_m                     # bar width as a fraction of the frame
    x0 = 0.955 - frac
    h = 0.0085

    ax.add_patch(Rectangle((x0 - 0.026, y - 0.020), frac + 0.052, h + 0.052,
                           transform=ax.transAxes, facecolor="white", edgecolor=INK,
                           linewidth=0.8, zorder=20))

    segs = 4
    for i in range(segs):
        ax.add_patch(Rectangle((x0 + i * frac / segs, y), frac / segs, h,
                               transform=ax.transAxes,
                               facecolor=INK if i % 2 == 0 else "white",
                               edgecolor=INK, linewidth=0.7, zorder=21))
    for i in range(segs + 1):
        ax.text(x0 + i * frac / segs, y + h + 0.006, f"{int(i * total_ft / segs):,}",
                transform=ax.transAxes, ha="center", va="bottom", fontsize=6.4,
                color=INK, zorder=22)
    ax.text(x0 + frac / 2, y - 0.016, "FEET", transform=ax.transAxes, ha="center",
            va="top", fontsize=6.4, color=INK, zorder=22)
    return frac


def draw_callout(ax, tip_xy, text="SITE LOCATION", offset=(0.20, 0.20)):
    tx, ty = tip_xy[0] + offset[0], tip_xy[1] + offset[1]
    ax.add_patch(FancyArrowPatch((tx, ty), tip_xy, transform=ax.transAxes,
                                 arrowstyle="-|>", mutation_scale=13,
                                 linewidth=1.6, color=RED, zorder=25,
                                 shrinkA=2, shrinkB=2))
    ax.text(tx, ty, text, transform=ax.transAxes, ha="center", va="center",
            fontsize=8.2, fontweight="bold", color=RED, zorder=26,
            bbox=dict(boxstyle="square,pad=0.34", facecolor="white",
                      edgecolor=RED, linewidth=1.0))


def draw_year(ax, year):
    ax.text(0.028, 0.968, str(year), transform=ax.transAxes, ha="left", va="top",
            fontsize=17, fontweight="bold", color=INK, zorder=25,
            bbox=dict(boxstyle="square,pad=0.30", facecolor="white",
                      edgecolor=INK, linewidth=0.9))


def draw_title_block(fig, meta):
    """Bottom strip: three cells plus a figure-number box on the right."""
    x0 = MARGIN_IN / PAGE_W_IN
    y0 = MARGIN_IN / PAGE_H_IN
    w = FRAME_W_IN / PAGE_W_IN
    h = TITLE_H_IN / PAGE_H_IN
    ax = fig.add_axes([x0, y0, w, h])
    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
    ax.add_patch(Rectangle((0, 0), 1, 1, facecolor="white", edgecolor=INK, linewidth=1.1))

    fig_box = 0.145
    ax.plot([1 - fig_box, 1 - fig_box], [0, 1], color=INK, linewidth=1.1)
    ax.plot([0, 1 - fig_box], [0.58, 0.58], color=RULE, linewidth=0.7)
    ax.plot([0.50, 0.50], [0, 0.58], color=RULE, linewidth=0.7)

    ax.text(0.018, 0.955, meta["figure_title"], ha="left", va="top",
            fontsize=13.5, fontweight="bold", color=INK)
    ax.text(0.018, 0.790, f'{meta["site_name"]}   |   {meta["county"]}',
            ha="left", va="top", fontsize=8.8, color=INK)
    ax.text(0.018, 0.685, meta["address"], ha="left", va="top", fontsize=7.4, color=RULE)

    def cell(x, rows):
        for i, (k, v) in enumerate(rows):
            yy = 0.455 - i * 0.145
            ax.text(x, yy, k, ha="left", va="center", fontsize=6.2, color=RULE)
            ax.text(x + 0.108, yy, v, ha="left", va="center", fontsize=6.9, color=INK)

    cell(0.018, [("PROJECT", meta["project_number"]),
                 ("SCALE", meta["scale"]),
                 ("PROJECTION", meta["projection"])])
    cell(0.518, [("SOURCE", meta["source"]),
                 ("PHOTO DATE", meta["photo_date"]),
                 ("PREPARED", meta["prepared"])])

    ax.text(1 - fig_box / 2, 0.70, "FIGURE", ha="center", va="center",
            fontsize=7.0, color=RULE)
    ax.text(1 - fig_box / 2, 0.40, meta["figure_number"], ha="center", va="center",
            fontsize=30, fontweight="bold", color=INK)
    return ax


def watermark(ax, light_background=False):
    ax.text(0.5, 0.30, "SAMPLE LAYOUT / SYNTHETIC BACKGROUND", transform=ax.transAxes,
            ha="center", va="center", fontsize=13, fontweight="bold",
            color="#202020" if light_background else "white",
            alpha=0.20 if light_background else 0.30,
            rotation=0, zorder=30)


# --------------------------------------------------------------------------
# Page assembly
# --------------------------------------------------------------------------
SITE_POLY = np.array([
    [0.375, 0.400], [0.560, 0.398], [0.566, 0.505],
    [0.520, 0.560], [0.372, 0.556],
])


def render_page(background, meta, year=None, unmapped=False, out_png=None,
                out_pdf=None, pdf_pages=None):
    fig = plt.figure(figsize=(PAGE_W_IN, PAGE_H_IN), dpi=DPI)
    fig.patch.set_facecolor("white")

    ax = fig.add_axes([
        MARGIN_IN / PAGE_W_IN,
        (MARGIN_IN + TITLE_H_IN + 0.12) / PAGE_H_IN,
        FRAME_W_IN / PAGE_W_IN,
        FRAME_H_IN / PAGE_H_IN,
    ])
    ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values():
        s.set_linewidth(1.1); s.set_color(INK)

    ax.imshow(background, extent=(0, 1, 0, 1), aspect="auto", zorder=1,
              interpolation="bilinear")

    if unmapped:
        # Requirement 22: imagery does not cover the whole page.
        ax.add_patch(Polygon([[0.0, 0.0], [0.30, 0.0], [0.0, 0.46]], closed=True,
                             facecolor="#C9C9C9", edgecolor="#9A9A9A",
                             linewidth=0.8, zorder=6))
        ax.text(0.085, 0.135, "UNMAPPED", fontsize=9.5, fontweight="bold",
                color="#4A4A4A", rotation=63, ha="center", va="center", zorder=7)

    ax.add_patch(Polygon(SITE_POLY, closed=True, facecolor="none",
                         edgecolor=RED, linewidth=2.4, zorder=15))

    watermark(ax, light_background=meta.get('light_background', False))
    draw_callout(ax, tip_xy=(0.545, 0.545), offset=(0.185, 0.205))
    draw_north_arrow(ax, 0.945, 0.895)
    draw_scale_bar(ax, FRAME_W_M)
    if year is not None:
        draw_year(ax, year)

    draw_title_block(fig, meta)

    if out_png:
        fig.savefig(out_png, dpi=DPI, facecolor="white")
    if out_pdf:
        fig.savefig(out_pdf, facecolor="white")
    if pdf_pages is not None:
        pdf_pages.savefig(fig, facecolor="white")
    plt.close(fig)


BASE_META = {
    "site_name": "West Dodge Commercial Pad",
    "county": "Douglas County, NE",
    "address": "Site outline derived from parcels 0123456789 and 0123456790",
    "project_number": "PA-2026-0142",
    "scale": f"1:{MAP_SCALE:,}",
    "projection": "NAD83 / UTM 15N (EPSG:26915)",
    "prepared": "2026-09-02",
}


# Historical years for the package sample.  Deliberately irregular, because
# NAIP coverage is irregular -- do not build a renderer that assumes a cadence.
PACKAGE_YEARS = [2018, 2012, 2006, 1997, 1988, 1976]


def build_package(out_dir, W=1100, page_dpi=150):
    """
    One multi-page PDF, in the order the deliverable should be assembled:
    Site Location, Site Plan, then the historical series newest first.

    This is the artefact to show someone when they ask what the tool produces.

    Pages are rasterised and JPEG-compressed before assembly. A real deliverable
    should keep vector text and 300 DPI rasters; this copy is squeezed so it can
    be emailed. Your pipeline should write the 300 DPI version.
    """
    pages = []

    def capture(background, meta, year=None, unmapped=False):
        buf = io.BytesIO()
        render_page(background, meta, year=year, unmapped=unmapped, out_png=buf)
        buf.seek(0)
        pages.append(Image.open(buf).convert("RGB"))

    capture(topo_background(W, W),
            {**BASE_META, "figure_title": "SITE LOCATION MAP", "figure_number": "1",
             "source": "USGS National Map", "photo_date": "n/a",
             "light_background": True})

    capture(aerial_background(W, W, seed=3, era="modern"),
            {**BASE_META, "figure_title": "SITE PLAN", "figure_number": "2",
             "source": "USDA NAIP (Planetary Computer)", "photo_date": "2024-07-11"},
            year=2024)

    for i, yr in enumerate(PACKAGE_YEARS):
        colour = yr >= 1997
        era = "modern" if yr >= 2000 else "historic"
        if yr >= 2010:
            src, date, proj = "USDA NAIP (Planetary Computer)", f"{yr}-07-11", BASE_META["projection"]
        elif yr >= 2003:
            src, date, proj = "USDA NAIP (EarthExplorer)", f"{yr}-08-02", BASE_META["projection"]
        elif yr >= 1990:
            src, date, proj = "USGS DOQ (EarthExplorer)", f"{yr}-06-18", BASE_META["projection"]
        else:
            src, date = "USGS single frame (EarthExplorer)", f"{yr}-09-20"
            proj = BASE_META["projection"] + ", RMS 3.4 m"

        capture(aerial_background(W, W, seed=20 + i * 5, grayscale=not colour, era=era),
                {**BASE_META, "figure_title": "HISTORICAL AERIAL",
                 "figure_number": f"3-{i + 1:02d}", "source": src, "photo_date": date,
                 "projection": proj, "light_background": not colour},
                year=yr,
                unmapped=(yr == 1976))          # coverage gap on the oldest frame

    path = os.path.join(out_dir, "sample_output_package.pdf")
    target = (int(PAGE_W_IN * page_dpi), int(PAGE_H_IN * page_dpi))
    pages = [im.resize(target, Image.LANCZOS) for im in pages]
    pages[0].save(path, save_all=True, append_images=pages[1:],
                  resolution=page_dpi, quality=82)
    print(f"package        ok  ({len(pages)} pages)")
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="../samples")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    W = H = 1400

    render_page(
        topo_background(W, H),
        {**BASE_META, "figure_title": "SITE LOCATION MAP", "figure_number": "1",
         "source": "USGS National Map", "photo_date": "n/a", "light_background": True},
        out_png=os.path.join(args.out, "sample_site_location.png"),
        out_pdf=os.path.join(args.out, "sample_site_location.pdf"),
    )
    print("site location  ok")

    render_page(
        aerial_background(W, H, seed=3, era="modern"),
        {**BASE_META, "figure_title": "SITE PLAN", "figure_number": "2",
         "source": "USDA NAIP (Planetary Computer)", "photo_date": "2024-07-11"},
        year=2024,
        out_png=os.path.join(args.out, "sample_site_plan.png"),
        out_pdf=os.path.join(args.out, "sample_site_plan.pdf"),
    )
    print("site plan      ok")

    render_page(
        aerial_background(W, H, seed=9, grayscale=True, era="historic"),
        {**BASE_META, "figure_title": "HISTORICAL AERIAL", "figure_number": "3-07",
         "source": "USGS DOQ (EarthExplorer)", "photo_date": "1995-06-18", "light_background": True,
         "projection": "NAD83 / UTM 15N (EPSG:26915), RMS 3.4 m"},
        year=1995, unmapped=True,
        out_png=os.path.join(args.out, "sample_historical_1995.png"),
        out_pdf=os.path.join(args.out, "sample_historical_1995.pdf"),
    )
    print("historical     ok")

    build_package(args.out)

    # Scale-bar self check, the same assertion Task 6 step 27 should make.
    bar_frac = (2000 * M_PER_FT) / FRAME_W_M
    print(f"\nframe width    {FRAME_W_FT:,.0f} ft  ({FRAME_W_M:,.1f} m)")
    print(f"2,000 ft bar   {bar_frac * 100:.2f}% of frame  "
          f"= {bar_frac * FRAME_W_IN:.3f} in on paper")
    assert abs(bar_frac * FRAME_W_IN - 4.0) < 0.01, "scale bar geometry drifted"
    print("scale bar check passed (within 1%)")


if __name__ == "__main__":
    main()
