"""Build site/data/zones-map.json: simplified SVG paths for the Spotrank price map.

Bidding-zone borders come from the GeoJSON files shipped with entsoe-py (MIT licence).
Surrounding countries that are not in the game (GB, IE, the western Balkans, ...) come from
Natural Earth's 1:110m countries (public domain), as bundled with geopandas 0.14.

Run once; the output is static:
    pip install "geopandas==0.14.4" pyogrio shapely pyproj entsoe-py
    python scripts/build_map.py
"""
import json
import os

import entsoe
import geopandas as gpd
from shapely.geometry import box, MultiPolygon, Polygon
from shapely.ops import polylabel

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "site", "data", "zones-map.json")
GEO = os.path.join(os.path.dirname(entsoe.__file__), "geo", "geojson")

GAME = {"NO1": "NO_1", "NO2": "NO_2", "NO3": "NO_3", "NO4": "NO_4", "NO5": "NO_5",
        "SE1": "SE_1", "SE2": "SE_2", "SE3": "SE_3", "SE4": "SE_4", "FI": "FI",
        "DK1": "DK_1", "DK2": "DK_2", "EE": "EE", "LV": "LV", "LT": "LT", "PL": "PL",
        "DE-LU": "DE_LU", "NL": "NL", "BE": "BE", "FR": "FR", "AT": "AT", "CZ": "CZ",
        "SK": "SK", "HU": "HU", "SI": "SI", "HR": "HR", "RO": "RO", "BG": "BG", "GR": "GR",
        "IT": "IT_NORD", "ES": "ES", "PT": "PT", "CH": "CH"}
CONTEXT_ZONES = {"IT-CNOR": "IT_CNOR", "IT-CSUD": "IT_CSUD", "IT-SUD": "IT_SUD", "IT-CALA": "IT_CALA",
                 "IT-SICI": "IT_SICI", "IT-SARD": "IT_SARD", "RS": "RS"}
NE_CONTEXT = ["United Kingdom", "Ireland", "Bosnia and Herz.", "Montenegro", "Albania", "North Macedonia",
              "Kosovo", "Ukraine", "Belarus", "Moldova", "Russia", "Turkey", "Morocco", "Algeria", "Tunisia",
              "Iceland"]

CRS = "EPSG:3035"                          # ETRS89 Lambert Azimuthal Equal Area, Europe
BBOX_LL = (-11.5, 34.5, 33.5, 71.5)        # lon/lat window: Iberia to North Cape, Greece to the Baltics
W = 1000                                   # SVG viewBox width


def load_geojson(name):
    g = gpd.read_file(os.path.join(GEO, name + ".geojson"))
    return g.to_crs(CRS).union_all() if hasattr(g, "union_all") else g.to_crs(CRS).unary_union


def main():
    clip = gpd.GeoSeries([box(*BBOX_LL)], crs="EPSG:4326").to_crs(CRS).iloc[0].envelope
    minx, miny, maxx, maxy = clip.bounds
    scale = W / (maxx - minx)
    H = round((maxy - miny) * scale)

    def to_path(geom, tol_m):
        geom = geom.intersection(clip).simplify(tol_m, preserve_topology=True)
        polys = [geom] if isinstance(geom, Polygon) else list(getattr(geom, "geoms", []))
        parts = []
        for poly in polys:
            if not isinstance(poly, Polygon) or poly.area < (tol_m * 3) ** 2:
                continue
            for ring in [poly.exterior] + list(poly.interiors):
                pts = [((x - minx) * scale, (maxy - y) * scale) for x, y in ring.coords]
                parts.append("M" + "L".join(f"{x:.1f},{y:.1f}" for x, y in pts[:-1]) + "Z")
        return "".join(parts)

    def anchor(geom):
        geom = geom.intersection(clip)
        polys = [geom] if isinstance(geom, Polygon) else list(getattr(geom, "geoms", []))
        big = max(polys, key=lambda p: p.area)
        pt = polylabel(big, tolerance=2000)
        return round((pt.x - minx) * scale, 1), round((maxy - pt.y) * scale, 1)

    zones = {}
    for code, f in GAME.items():
        g = load_geojson(f)
        x, y = anchor(g)
        zones[code] = {"d": to_path(g, 2500), "x": x, "y": y}
    context = []
    for code, f in CONTEXT_ZONES.items():
        context.append({"id": code, "d": to_path(load_geojson(f), 2500)})
    ne = gpd.read_file(gpd.datasets.get_path("naturalearth_lowres")).to_crs(CRS)
    for name in NE_CONTEXT:
        row = ne[ne["name"] == name]
        if len(row):
            context.append({"id": name, "d": to_path(row.geometry.iloc[0], 4000)})
    out = {
        "note": "Bidding-zone borders: entsoe-py (MIT). Other countries: Natural Earth (public domain). Projection: EPSG:3035.",
        "viewBox": [0, 0, W, H],
        "zones": zones,
        "context": [c for c in context if c["d"]],
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, separators=(",", ":"))
    print(f"wrote {OUT}: {os.path.getsize(OUT) / 1024:.0f} KB, viewBox {W}x{H}, {len(zones)} zones, {len(out['context'])} context shapes")


if __name__ == "__main__":
    main()
