"""Build site/data/zones-map.json: simplified SVG paths for the Spotrank price map.

Bidding-zone borders come from the GeoJSON files shipped with entsoe-py (MIT licence).
Great Britain, Ireland (SEM: Ireland and Northern Ireland), Montenegro, North Macedonia, Albania,
Kosovo and Bosnia come from Natural Earth's 1:50m countries; the other surrounding countries that have
no price from Natural Earth's 1:110m countries (public domain), as bundled with geopandas 0.14.
"zones" are the game's zones, "extra" the map-only zones with a price (the other Italian zones, IE,
GB, RS, ME, MK, AL, XK), "context" grey land.

The 1:50m countries (more detail, for the coloured map-only zones and their neighbour Bosnia):
    curl -L -o /tmp/ne_50m_admin_0_countries.geojson \
      https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_50m_admin_0_countries.geojson

Run once; the output is static:
    pip install "geopandas==0.14.4" pyogrio shapely pyproj entsoe-py
    python scripts/build_map.py [--ne50 /tmp/ne_50m_admin_0_countries.geojson]
"""
import argparse
import json
import os

import entsoe
import geopandas as gpd
from shapely.geometry import box, LineString, MultiPolygon, Polygon
from shapely.ops import linemerge, polylabel, unary_union

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "site", "data", "zones-map.json")
GEO = os.path.join(os.path.dirname(entsoe.__file__), "geo", "geojson")

GAME = {"NO1": "NO_1", "NO2": "NO_2", "NO3": "NO_3", "NO4": "NO_4", "NO5": "NO_5",
        "SE1": "SE_1", "SE2": "SE_2", "SE3": "SE_3", "SE4": "SE_4", "FI": "FI",
        "DK1": "DK_1", "DK2": "DK_2", "EE": "EE", "LV": "LV", "LT": "LT", "PL": "PL",
        "DE-LU": "DE_LU", "NL": "NL", "BE": "BE", "FR": "FR", "AT": "AT", "CZ": "CZ",
        "SK": "SK", "HU": "HU", "SI": "SI", "HR": "HR", "RO": "RO", "BG": "BG", "GR": "GR",
        "IT": "IT_NORD", "ES": "ES", "PT": "PT", "CH": "CH"}
EXTRA = {"IT-CNOR": "IT_CNOR", "IT-CSUD": "IT_CSUD", "IT-SUD": "IT_SUD", "IT-CALA": "IT_CALA",
         "IT-SICI": "IT_SICI", "IT-SARD": "IT_SARD", "RS": "RS"}
NE50_EXTRA = {"ME": "Montenegro", "MK": "North Macedonia", "AL": "Albania", "XK": "Kosovo"}
NE50_CONTEXT = ["Bosnia and Herz."]
CONTEXT_ZONES = {}
NE_CONTEXT = ["Ukraine", "Belarus", "Moldova", "Russia", "Turkey", "Morocco", "Algeria", "Tunisia",
              "Iceland"]

CRS = "EPSG:3035"                          # ETRS89 Lambert Azimuthal Equal Area, Europe
BBOX_LL = (-11.5, 34.5, 33.5, 71.5)        # lon/lat window: Iberia to North Cape, Greece to the Baltics
W = 1000                                   # SVG viewBox width


def load_geojson(name):
    g = gpd.read_file(os.path.join(GEO, name + ".geojson"))
    return g.to_crs(CRS).union_all() if hasattr(g, "union_all") else g.to_crs(CRS).unary_union


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ne50", default="/tmp/ne_50m_admin_0_countries.geojson", help="Natural Earth 1:50m countries (GeoJSON)")
    a = ap.parse_args()
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

    def to_line_path(geom, tol_m):
        geom = geom.intersection(clip).simplify(tol_m)
        lines = [geom] if isinstance(geom, LineString) else [g for g in getattr(geom, "geoms", []) if isinstance(g, LineString)]
        parts = []
        for ln in lines:
            pts = [((x - minx) * scale, (maxy - y) * scale) for x, y in ln.coords]
            if len(pts) >= 2 and ln.length > tol_m:
                parts.append("M" + "L".join(f"{x:.1f},{y:.1f}" for x, y in pts))
        return "".join(parts)

    geoms = {}
    zones = {}
    for code, f in GAME.items():
        g = load_geojson(f)
        geoms[code] = g
        x, y = anchor(g)
        zones[code] = {"d": to_path(g, 2500), "x": x, "y": y}
    ne = gpd.read_file(gpd.datasets.get_path("naturalearth_lowres")).to_crs(CRS)
    ne50 = gpd.read_file(a.ne50).to_crs(CRS)
    name = "NAME" if "NAME" in ne50.columns else "name"
    kosovo = ne50[ne50[name] == "Kosovo"].geometry.iloc[0]
    extra = {}
    for code, f in EXTRA.items():
        g = load_geojson(f)
        if code == "RS":   # entsoe-py's Serbia leaves Kosovo out; trim the few border slivers that overlap
            g = g.difference(kosovo)
        geoms[code] = g
        x, y = anchor(g)
        extra[code] = {"d": to_path(g, 2500), "x": x, "y": y}
    for code, nm in NE50_EXTRA.items():
        g = ne50[ne50[name] == nm].geometry.iloc[0]
        geoms[code] = g
        x, y = anchor(g)
        extra[code] = {"d": to_path(g, 2500), "x": x, "y": y}
    uk = ne50[ne50[name] == "United Kingdom"].geometry.iloc[0]
    ie = ne50[ne50[name] == "Ireland"].geometry.iloc[0]
    parts = list(getattr(uk, "geoms", [uk]))
    ni = [p for p in parts if p.distance(ie) < 1000]           # Northern Ireland touches Ireland
    gb = MultiPolygon([p for p in parts if p not in ni]) if len(parts) - len(ni) > 1 else [p for p in parts if p not in ni][0]
    sem = ie.union(MultiPolygon(ni) if len(ni) > 1 else ni[0]).buffer(1).buffer(-1)   # close the border sliver
    for code, g in (("IE", sem), ("GB", gb)):
        geoms[code] = g
        x, y = anchor(g)
        extra[code] = {"d": to_path(g, 2500), "x": x, "y": y}
    context = [{"id": nm, "d": to_path(ne50[ne50[name] == nm].geometry.iloc[0], 2500)} for nm in NE50_CONTEXT]
    for code, f in CONTEXT_ZONES.items():
        context.append({"id": code, "d": to_path(load_geojson(f), 2500)})
    for name in NE_CONTEXT:
        row = ne[ne["name"] == name]
        if len(row):
            context.append({"id": name, "d": to_path(row.geometry.iloc[0], 4000)})
    # borders between the regions of scripts/regions.json: each region's outline where it runs along another
    # region (within 3 km, since the zone shapes come from two sources and don't meet exactly)
    with open(os.path.join(HERE, "regions.json"), encoding="utf-8") as fh:
        regions = {k: v for k, v in json.load(fh).items() if not k.startswith("_")}
    union = {r: unary_union([geoms[z].buffer(0) for z in zs if z in geoms]) for r, zs in regions.items()}
    names = list(union)
    border_lines = []
    for i, r1 in enumerate(names):
        for r2 in names[i + 1:]:
            seg = union[r1].boundary.intersection(union[r2].buffer(3000))
            if not seg.is_empty:
                border_lines.append(linemerge(seg) if seg.geom_type == "MultiLineString" else seg)
    region_borders = "".join(to_line_path(g, 2500) for g in border_lines)
    out = {
        "note": "Bidding-zone borders: entsoe-py (MIT). Other countries: Natural Earth (public domain). Projection: EPSG:3035.",
        "viewBox": [0, 0, W, H],
        "zones": zones,
        "extra": extra,
        "context": [c for c in context if c["d"]],
        "region_borders": region_borders,
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, separators=(",", ":"))
    print(f"wrote {OUT}: {os.path.getsize(OUT) / 1024:.0f} KB, viewBox {W}x{H}, {len(zones)} zones, {len(extra)} map-only zones, {len(out['context'])} context shapes")


if __name__ == "__main__":
    main()
