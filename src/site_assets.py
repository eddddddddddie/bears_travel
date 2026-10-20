"""Static assets for the dashboard in site/: the map outline and the link-preview image.

Run after travel_model.py, since the preview image reads its figures from site/data/*.json.

Outputs: site/data/land_au_nz.json  Australia and New Zealand land outline (GeoJSON) for the map section
         site/og-image.png          1200 x 630 link preview (og:image, twitter:card) with the headline figures

Downloads are cached in data/raw/site/ and fetched only when missing (or with --refresh):
  - Natural Earth 1:50m land (public domain), as the TopoJSON in the world-atlas npm package
  - Barlow, the page font (SIL Open Font License), from the Google Fonts repository
Needs matplotlib for the image.
"""
from __future__ import annotations

import argparse
import json
import sys

import numpy as np
import requests

import config

LAND_URL = "https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/land-50m.json"
FONT_URL = "https://raw.githubusercontent.com/google/fonts/main/ofl/barlow/Barlow-{}.ttf"
FONT_WEIGHTS = ("Regular", "SemiBold", "Bold")
CACHE_DIR = config.RAW_DIR / "site"
SITE_DIR = config.ROOT / "site"
LAND_OUT = SITE_DIR / "data" / "land_au_nz.json"
IMAGE_OUT = SITE_DIR / "og-image.png"

# Polygons with any vertex in this box are kept: Australia, New Zealand and the southern edge of New Guinea
# and the Indonesian islands, which frame the map's north. Polygons that cross the antimeridian (Fiji) are
# dropped, since the image draws plain lon/lat. Coordinates are rounded to about 1 km.
LAND_BOX = (105.0, -50.0, 180.0, -5.0)   # west, south, east, north
LAND_DP = 2

BG, PANEL, LINE = "#0a0a0a", "#181818", "#2a2a2a"
FG, MUTED_TEXT = "#ffffff", "#b8b8b8"
RED, BLUE, MUTED = "#ec1d3a", "#4a90d9", "#4a4a4a"


def fetch(url: str, path, refresh: bool) -> bytes:
    if refresh or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        res = requests.get(url, headers=config.HTTP_HEADERS, timeout=60)
        res.raise_for_status()
        path.write_bytes(res.content)
    return path.read_bytes()


# ---------------------------------------------------------------- land outline
def topo_polygons(topo: dict) -> list[list[list[list[float]]]]:
    """Decode every polygon in a quantized TopoJSON file to lon/lat rings, keeping ring order and winding."""
    (kx, ky), (dx, dy) = topo["transform"]["scale"], topo["transform"]["translate"]
    arcs = []
    for arc in topo["arcs"]:
        q = np.cumsum(np.array(arc, dtype=float), axis=0)
        arcs.append(np.column_stack([q[:, 0] * kx + dx, q[:, 1] * ky + dy]))

    def ring(ids):
        pts = []
        for i in ids:
            a = arcs[i] if i >= 0 else arcs[~i][::-1]
            pts.extend(a[1:] if pts else a)
        return np.array(pts)

    polys = []
    for obj in topo["objects"].values():
        geoms = obj["geometries"] if obj["type"] == "GeometryCollection" else [obj]
        for g in geoms:
            parts = [g["arcs"]] if g["type"] == "Polygon" else g["arcs"] if g["type"] == "MultiPolygon" else []
            polys.extend([[ring(r) for r in p] for p in parts])
    return polys


def land_outline(refresh: bool) -> dict:
    topo = json.loads(fetch(LAND_URL, CACHE_DIR / "land-50m.json", refresh))
    w, s, e, n = LAND_BOX
    keep = []
    for rings in topo_polygons(topo):
        outer = rings[0]
        if (not ((outer[:, 0] >= w) & (outer[:, 0] <= e) & (outer[:, 1] >= s) & (outer[:, 1] <= n)).any()
                or np.ptp(outer[:, 0]) > 180):
            continue
        out = []
        for r in rings:
            r = np.round(r, LAND_DP)
            r = r[np.r_[True, (np.diff(r, axis=0) != 0).any(axis=1)]]   # drop points that rounding merged
            if len(r) >= 4:
                out.append(r.tolist())
        if out:
            keep.append(out)
    return {"type": "Feature", "properties": {"source": "Natural Earth 1:50m land"},
            "geometry": {"type": "MultiPolygon", "coordinates": keep}}


# ---------------------------------------------------------------- preview image
def great_circle(lon1, lat1, lon2, lat2, n=64) -> np.ndarray:
    a, b = (np.array([np.cos(np.radians(la)) * np.cos(np.radians(lo)), np.cos(np.radians(la)) * np.sin(np.radians(lo)),
                      np.sin(np.radians(la))]) for lo, la in ((lon1, lat1), (lon2, lat2)))
    om = np.arccos(np.clip(a @ b, -1, 1))
    t = np.linspace(0, 1, n)[:, None]
    p = (np.sin((1 - t) * om) * a + np.sin(t * om) * b) / np.sin(om)
    return np.column_stack([np.degrees(np.arctan2(p[:, 1], p[:, 0])), np.degrees(np.arcsin(p[:, 2]))])


def preview_image(land: dict, refresh: bool) -> None:
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import font_manager
    import matplotlib.pyplot as plt

    for wt in FONT_WEIGHTS:
        path = CACHE_DIR / f"Barlow-{wt}.ttf"
        try:
            fetch(FONT_URL.format(wt), path, refresh)
            font_manager.fontManager.addfont(str(path))
        except requests.RequestException as err:
            print(f"Barlow {wt} not available ({err}); using the default font.")
    plt.rcParams["font.family"] = ["Barlow", "DejaVu Sans"]

    data = SITE_DIR / "data"
    clubs = {r["team_id"]: r for r in json.loads((data / "projection_2027_clubs.json").read_text())}
    pairs = [r for r in json.loads((data / "projection_2027_pairs.json").read_text()) if r["team_id"] == "perth_bears"]
    seasons = json.loads((data / "team_season_travel.json").read_text())
    bears, sydney = clubs["perth_bears"], clubs["sydney_roosters"]
    ranked = sorted((r for r in seasons if not r["projected"] or r["team_id"] == "perth_bears"),
                    key=lambda r: -r["km_return_home"])
    rank = next(i for i, r in enumerate(ranked, 1) if r["team_id"] == "perth_bears" and r["projected"])
    first, last = min(r["season"] for r in seasons if not r["projected"]), max(r["season"] for r in seasons if not r["projected"])

    fig = plt.figure(figsize=(12, 6.3), dpi=100, facecolor=BG)

    # Map on the right: the outline in a simple equirectangular view scaled for latitude, lines from Perth
    ax = fig.add_axes((0.535, 0.06, 0.445, 0.88), facecolor=BG)
    ax.set_xlim(111, 180); ax.set_ylim(-48, -9)
    ax.set_aspect(1 / np.cos(np.radians(30)))
    ax.axis("off")
    for poly in land["geometry"]["coordinates"]:
        r = np.array(poly[0])
        ax.fill(r[:, 0], r[:, 1], color=PANEL, ec=LINE, lw=0.6)
    # Longest distance band in red, the next in blue, any others muted (the page map uses the same order)
    bands = sorted({p["band"] for p in pairs}, key=lambda b: -min(p["km"] for p in pairs if p["band"] == b))
    colour = dict(zip(bands, [RED, BLUE] + [MUTED] * len(bands)))
    for p in sorted(pairs, key=lambda p: -p["km"]):
        c = great_circle(p["lon"], p["lat"], p["opp_lon"], p["opp_lat"])
        ax.plot(c[:, 0], c[:, 1], color=colour[p["band"]], lw=1.6, alpha=0.85, solid_capstyle="round")
        ax.plot(p["opp_lon"], p["opp_lat"], "o", ms=4.5, color=FG, mec=BG, mew=1)
    ax.plot(pairs[0]["lon"], pairs[0]["lat"], "o", ms=9, color=RED, mec=FG, mew=1.6)

    # Text on the left
    x0 = 0.055
    fig.text(x0, 0.875, "INDEPENDENT ANALYSIS", color=FG, fontsize=11, fontweight="semibold",
             bbox=dict(boxstyle="round,pad=0.45", fc=RED, ec="none"))
    fig.text(x0, 0.70, "Perth Bears\ntravel impact", color=FG, fontsize=50, fontweight="bold", linespacing=0.95, va="center")
    fig.text(x0, 0.535, f"NRL matches {first} to {last} and a 2027 projection", color=MUTED_TEXT, fontsize=17)
    tiles = [(f"{bears['away_cost_per_game']:+.1f}", "points per away game", RED),
             (f"{bears['home_away_swing']:.1f}", f"home-away swing\n(Sydney club {sydney['home_away_swing']:.1f})", FG),
             (f"{bears['season_km_return_home'] / 1000:,.0f}k", f"km in a season,\nrank {rank} of {len(ranked)}", FG)]
    for i, (big, lab, col) in enumerate(tiles):
        x = x0 + i * 0.158
        fig.text(x, 0.37, big, color=col, fontsize=38, fontweight="bold", va="center")
        fig.text(x, 0.29, lab, color=MUTED_TEXT, fontsize=12.5, va="top", linespacing=1.2)
    fig.text(x0, 0.075, "bears.thisaithat.com", color=FG, fontsize=14, fontweight="semibold")
    fig.text(x0 + 0.2, 0.075, "Not affiliated with the Perth Bears or the NRL", color=MUTED_TEXT, fontsize=12)
    fig.add_artist(plt.Line2D([x0, 0.49], [0.14, 0.14], color=LINE, lw=1))

    fig.savefig(IMAGE_OUT, dpi=100, facecolor=BG)
    plt.close(fig)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--refresh", action="store_true", help="download the land outline and fonts again")
    args = ap.parse_args()
    if not (SITE_DIR / "data" / "projection_2027_pairs.json").exists():
        print("Run src/travel_model.py first.")
        return 1
    land = land_outline(args.refresh)
    LAND_OUT.write_text(json.dumps(land, separators=(",", ":")), encoding="utf-8")
    print(f"{LAND_OUT.relative_to(config.ROOT)}: {len(land['geometry']['coordinates'])} polygons, "
          f"{LAND_OUT.stat().st_size / 1024:.0f} KB")
    preview_image(land, args.refresh)
    print(f"{IMAGE_OUT.relative_to(config.ROOT)}: 1200 x 630")
    return 0


if __name__ == "__main__":
    sys.exit(main())
