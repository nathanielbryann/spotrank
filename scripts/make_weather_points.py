"""Pick up to 8 spread-out points inside each game zone, for the weather clues (scripts/build_clues.py).

Run once (and again if zones change); writes scripts/weather_points.json. Zone shapes: entsoe-py's GeoJSON.
    pip install "geopandas==0.14.4" shapely entsoe-py
    python scripts/make_weather_points.py
"""
import json
import os

import entsoe
import geopandas as gpd
import numpy as np
from shapely.geometry import Point
from shapely.ops import unary_union

from build_spotrank_data import ZONES

GEO = os.path.join(os.path.dirname(entsoe.__file__), "geo", "geojson")
N = 8


def points_for(geom, n=N):
    lon0, lat0, lon1, lat1 = geom.bounds
    for step in (0.5, 0.35, 0.25, 0.15, 0.1, 0.05):
        xs, ys = np.arange(lon0 + step / 2, lon1, step), np.arange(lat0 + step / 2, lat1, step)
        inside = [(x, y) for x in xs for y in ys if geom.contains(Point(x, y))]
        if len(inside) >= 3 * n:
            break
    rp = geom.representative_point()
    chosen = [(rp.x, rp.y)]
    pts = np.array(inside) if inside else np.array(chosen)
    while len(chosen) < min(n, len(pts) + 1):   # farthest-point sampling (lat-corrected distance)
        c = np.array(chosen)
        dx = (pts[:, None, 0] - c[None, :, 0]) * np.cos(np.radians(pts[:, None, 1]))
        d = np.sqrt(dx ** 2 + (pts[:, None, 1] - c[None, :, 1]) ** 2).min(axis=1)
        k = int(d.argmax())
        if d[k] < 1e-9:
            break
        chosen.append(tuple(pts[k]))
    return [[round(y, 3), round(x, 3)] for x, y in chosen]   # [lat, lon]


out = {}
for z, code in ZONES.items():
    g = gpd.read_file(os.path.join(GEO, code + ".geojson")).to_crs(4326)
    geom = unary_union(list(g.geometry))
    out[z] = points_for(geom)
path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "weather_points.json")
with open(path, "w") as f:
    json.dump({"note": "Up to 8 spread-out points [lat, lon] inside each zone (entsoe-py zone shapes), for weather clues.", "points": out}, f, indent=0)
print({z: len(p) for z, p in out.items()})
