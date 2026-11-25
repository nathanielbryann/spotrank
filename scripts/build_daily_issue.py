"""European Spot Price Daily: one issue per day with today's map, the month and the year so far, and findings.

Everything comes from the site's own data files (no new fetches): day-ahead baseload prices for the
game's 33 zones and the 13 map-only zones, ENTSO-E's day-ahead load, wind and solar forecasts, and
regional averages. The findings are rule-based: fixed sentence patterns filled with the day's
numbers, so every sentence can be checked against the data.

    python scripts/build_daily_issue.py                       # today (Helsinki)
    python scripts/build_daily_issue.py --date 2026-11-24 --out /tmp/daily
    python scripts/build_daily_issue.py --pdf                 # also print the PDF with headless Chrome
    python scripts/build_daily_issue.py --pdf --skip-if-done  # the daily run: build once, again only if more zones arrive

Writes <out>/<date>/index.html (self-contained), <out>/<date>/issue.json (what the issue covers) and, with --pdf,
<out>/<date>/european-spot-price-daily-<date>.pdf; then rewrites the archive page <out>/index.html.
"""
import argparse
import html
import json
import math
import os
import re
import shutil
import statistics as st
import subprocess
import tempfile
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(HERE, "..", "site")
DATA = os.path.join(SITE, "data")
SITE_URL = "https://nathanielbryann.github.io/spotrank/"
RAMP9 = ["#1a9850", "#66bd63", "#a6d96a", "#d9ef8b", "#ffffbf", "#fee08b", "#fdae61", "#f46d43", "#d73027"]
TINY = {"ME", "XK", "MK", "AL"}
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
MONS = [m[:3] for m in MONTHS]
WDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
# neighbours worth watching: (a, b) pairs whose gap says something about the grid between them
PAIRS = [("DE-LU", "FR"), ("SE3", "FI"), ("NO2", "DE-LU"), ("IE", "GB"), ("ES", "FR"), ("PL", "DE-LU"),
         ("IT", "CH"), ("DK1", "DE-LU"), ("EE", "FI"), ("NL", "GB"), ("HU", "RO"), ("GR", "BG"), ("SE4", "DK2")]
PARTIAL = 0.9
# regional averages: each country counts once (a country with several zones as the average of its zones),
# so Norway's five zones or Italy's seven don't outweigh their neighbours
with open(os.path.join(HERE, "regions.json"), encoding="utf-8") as _f:
    REGIONS = {k: v for k, v in json.load(_f).items() if not k.startswith("_")}   # an average on fewer than 90% of the period's days gets an asterisk


# ---------------------------------------------------------------- data
def load_json(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return json.load(f)


def load_prices():
    ds, ex = load_json("spotrank_data.json"), load_json("spotrank_data_extra.json")
    zones = ds["zones"] + ex["zones"]
    xb, empty = dict(ex["days"]), [None] * len(ex["zones"])
    out = {}
    for d, v in ds["days"]:
        row = v + xb.get(d, empty)
        out[d] = {z: p for z, p in zip(zones, row) if p is not None}
    return zones, len(ds["zones"]), out


def load_drivers():
    out = {}
    for name in ("spotrank_drivers.json", "spotrank_drivers_extra.json"):
        try:
            d = load_json(name)
        except FileNotFoundError:
            continue
        idx = {dd: i for i, dd in enumerate(d["dates"])}
        for z, s in d["series"].items():
            out[z] = (idx, s)
    return out


def zone_names():
    """Names from the page's ZONE_META, so the issue and the site agree."""
    with open(os.path.join(SITE, "index.html"), encoding="utf-8") as f:
        src = f.read()
    block = src[src.index("const ZONE_META = {"):src.index("};", src.index("const ZONE_META = {"))]
    out = {}
    for m in re.finditer(r"(?:'([^']+)'|([A-Z0-9]+)):\{c:'([^']*)'(?:,a:'([^']*)')?", block):
        out[m.group(1) or m.group(2)] = (m.group(3), m.group(4) or "")
    return out


NAMES = {}
SHARED = {"Norway", "Sweden", "Denmark", "Italy"}


def is_italian(z):
    return z == "IT" or z.startswith("IT-")


def it_bench(d):
    """The same values with Italy North (IT) alone standing for Italy: the month and year rankings use it as the benchmark."""
    return {z: v for z, v in d.items() if not is_italian(z) or z == "IT"}


def join_and(xs):
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


def italy_note(shown, moves, up):
    """A line for the Italian zones left out of Biggest moves that moved as much as the zones listed."""
    it = [(z, v) for z, v in shown if is_italian(z)]
    if not it or not shown:
        return None
    z0, v0 = it[0]
    cut = shown[-1][1]
    left = sorted(((z, v) for z, v in moves.items() if is_italian(z) and z != z0 and (v >= cut if up else v <= cut)),
                  key=lambda kv: -kv[1] if up else kv[1])
    if not left:
        return None
    area = lambda z: label(z).replace("Italy ", "")   # noqa: E731
    verb = "rose" if up else "fell"
    if all(round(v) == round(v0) for _, v in left):
        return f"Italy {join_and([area(z) for z, _ in left])} also {verb} {eur(abs(v0))}, the same as Italy {area(z0)}."
    return f"Other Italian zones that {verb} as much: " + join_and([f"{area(z)} {signed(v)}" for z, v in left]) + "."


def one_italian(items, n):
    """The first n of a sorted list, keeping only the first Italian zone: Italy's zones often move together and would fill the list."""
    out, seen = [], False
    for z, v in items:
        if is_italian(z):
            if seen:
                continue
            seen = True
        out.append((z, v))
        if len(out) == n:
            break
    return out


def label(z):
    """How a zone is called in a sentence: Finland, Norway NO4, Italy North, Germany–Luxembourg."""
    c, a = NAMES.get(z, (z, ""))
    if z == "IT":
        return "Italy North"
    if z.startswith("IT-"):
        return "Italy " + a
    if c in SHARED:
        return c + " " + z
    return c


def short_name(z):
    """The name beside a zone code in a table: Norway · North, Italy · Sicily, Finland."""
    c, a = NAMES.get(z, (z, ""))
    if z == "IT":
        return "Italy · North"
    if z.startswith("IT-") or c in SHARED:
        return c + " · " + a.split(" · ")[0] if a else c
    return c


def eur(v, dp=0):
    s = f"{abs(v):,.{dp}f}"
    return ("−" if v < 0 else "") + "€" + s


def signed(v, dp=0):
    return ("+" if v >= 0 else "−") + "€" + f"{abs(v):,.{dp}f}"


def pct(v):
    return ("+" if v >= 0 else "−") + f"{abs(v):.0f}%"


def nice_day(d, year=False):
    x = date.fromisoformat(d)
    return f"{x.day} {MONS[x.month - 1]}" + (f" {x.year}" if year else "")


def dates_between(a, b):
    x, y = date.fromisoformat(a), date.fromisoformat(b)
    return [(x + timedelta(days=k)).isoformat() for k in range((y - x).days + 1)]


def period_avg(P, zones, a, b):
    """{zone: (avg, n, partial)} over a..b inclusive."""
    days = dates_between(a, b)
    out = {}
    for z in zones:
        vals = [P[d][z] for d in days if d in P and z in P[d]]
        if vals:
            out[z] = (sum(vals) / len(vals), len(vals), len(vals) < PARTIAL * len(days))
    return out


# where each region's average sits on the maps: open sea or grey land next to the region (viewBox units)
REGION_POS = {"North": (325, 225), "West": (74, 565), "East": (866, 575), "South": (356, 888)}


def region_zone_lines(region):
    """A region's zones in two short lines: NO1–5 · SE1–4 · FI / DK1–2 · EE · LV · LT."""
    zs, out, i = REGIONS[region], [], 0
    it = [z for z in zs if z == "IT" or z.startswith("IT-")]
    while i < len(zs):
        z = zs[i]
        if z in it:
            if z == it[0]:
                out.append(f"IT ({len(it)})" if len(it) > 1 else "IT")
            i += 1
            continue
        m = re.fullmatch(r"([A-Z]+)(\d)", z)
        j = i
        if m:
            while j + 1 < len(zs) and re.fullmatch(m.group(1) + str(int(zs[j][-1]) + 1), zs[j + 1]):
                j += 1
        out.append(z if j == i else f"{z}–{zs[j][-1]}")
        i = j + 1
    if len(out) < 2:
        return [" · ".join(out)]
    k = min(range(1, len(out)), key=lambda k: max(len(" · ".join(out[:k])), len(" · ".join(out[k:]))))   # the most even split
    return [" · ".join(out[:k]), " · ".join(out[k:])]


def region_avg(vals, region):
    """(average, countries counted, countries in the region) for one day's or one period's zone values."""
    by = {}
    for z in REGIONS[region]:
        by.setdefault(NAMES.get(z, (z, ""))[0], []).append(z)
    per = [st.mean(vals[z] for z in zs if z in vals) for zs in by.values() if any(z in vals for z in zs)]
    return (st.mean(per) if per else None), len(per), len(by)


def europe_avg(vals):
    """(average, countries counted) over every zone in the regions, each country counted once like the regional averages."""
    by = {}
    for r in REGIONS:
        for z in REGIONS[r]:
            by.setdefault(NAMES.get(z, (z, ""))[0], []).append(z)
    per = [st.mean(vals[z] for z in zs if z in vals) for zs in by.values() if any(z in vals for z in zs)]
    return (st.mean(per) if per else None), len(per)


# ---------------------------------------------------------------- findings
def finding_records(P, D, today):
    """The zone whose price today is its highest or lowest for the longest time."""
    best = None
    days = sorted(d for d in P if d < D)
    for z, v in today.items():
        for kind in ("high", "low"):
            since = None
            for d in reversed(days):
                p = P[d].get(z)
                if p is None:
                    continue
                if (kind == "high" and p >= v) or (kind == "low" and p <= v):
                    since = d
                    break
            have = [d for d in days if z in P[d]]
            if not have:
                continue
            span = (date.fromisoformat(D) - date.fromisoformat(since or have[0])).days
            if since is None:
                span += 10000   # a record for the whole history beats any "since"
            if span >= 45 and (best is None or span > best[0]):
                best = (span, z, kind, v, since, have[0])
    if not best:
        return None
    span, z, kind, v, since, first = best
    word = "highest" if kind == "high" else "lowest"
    if since is None:
        return f"{label(z)}'s {eur(v)} is its {word} daily price since our records start ({nice_day(first, True)})."
    return f"{label(z)}'s {eur(v)} is its {word} daily price since {nice_day(since, True)}."


def streak(P, D, pick):
    """How many days in a row (to D) the same zone was the cheapest (pick=min) or most expensive (pick=max)."""
    zt = pick(P[D], key=P[D].get)
    n, d = 0, date.fromisoformat(D)
    while d.isoformat() in P and P[d.isoformat()] and pick(P[d.isoformat()], key=P[d.isoformat()].get) == zt:
        n += 1
        d -= timedelta(days=1)
    return zt, n


def finding_groups(today):
    """Zones that cleared at exactly the same baseload price: they were coupled with no congestion between them."""
    by = {}
    for z, v in today.items():
        by.setdefault(round(v, 2), []).append(z)
    groups = sorted((g for g in by.values() if len(g) >= 3), key=len, reverse=True)
    if not groups:
        return None
    g = sorted(groups[0])
    names = ", ".join(f"{label(z)} ({z})" for z in g[:-1]) + f" and {label(g[-1])} ({g[-1]})"
    return f"{names} all cleared at exactly {eur(today[g[0]], 2)}: the grid between them wasn't congested all day."


def finding_gap(P, D, today):
    """The neighbour pair whose price gap today is most unusual against the past 30 days."""
    best = None
    past = [d for d in dates_between((date.fromisoformat(D) - timedelta(days=30)).isoformat(), (date.fromisoformat(D) - timedelta(days=1)).isoformat()) if d in P]
    for a, b in PAIRS:
        if a not in today or b not in today:
            continue
        gaps = [P[d][a] - P[d][b] for d in past if a in P[d] and b in P[d]]
        if len(gaps) < 15:
            continue
        g, m, s = today[a] - today[b], st.mean(gaps), st.pstdev(gaps) or 1
        z = (g - m) / s
        if abs(g) >= 15 and (best is None or abs(z) > abs(best[0])):
            best = (z, a, b, g, m)
    if not best or abs(best[0]) < 1.5:
        return None
    z, a, b, g, m = best
    cheaper, pricier = (a, b) if g < 0 else (b, a)
    usual = (f"over the past 30 days it was {eur(abs(m))} cheaper on average" if (m < 0) == (g < 0)
             else f"over the past 30 days it was {eur(abs(m))} more expensive on average")
    return f"{label(cheaper)} was {eur(abs(g))} cheaper than {label(pricier)} today; {usual}."


def driver_note(drivers, z, D):
    """Wind and demand forecasts for a zone today against their 30-day average, when they stand out."""
    if z not in drivers:
        return None
    idx, s = drivers[z]
    if D not in idx:
        return None
    i = idx[D]
    past = [idx[d] for d in dates_between((date.fromisoformat(D) - timedelta(days=30)).isoformat(), (date.fromisoformat(D) - timedelta(days=1)).isoformat()) if d in idx]
    bits = []
    for key, name in (("wind", "wind"), ("solar", "solar"), ("load", "demand")):
        ser = s.get(key)
        if not ser or ser[i] is None:
            continue
        hist = [ser[k] for k in past if ser[k] is not None]
        if len(hist) < 15:
            continue
        m = st.mean(hist)
        if key == "solar" and m < 300:   # winter solar: too small to matter
            continue
        if m <= 0:
            continue
        ch = (ser[i] - m) / m * 100
        if (key == "load" and abs(ch) >= 7) or (key != "load" and abs(ch) >= 35 and max(ser[i], m) >= 300):
            gw = ser[i] / 1000
            bits.append(f"{name} forecast {gw:.1f} GW, {abs(ch):.0f}% {'above' if ch > 0 else 'below'} its 30-day average")
    return "; ".join(bits) if bits else None


def fuel_line(D):
    try:
        f = load_json("fuel_prices.json")
    except FileNotFoundError:
        return None
    start = date.fromisoformat(f["start"])
    out = []
    for key, name, unit in (("ttf", "Gas (TTF)", "/MWh"), ("eua", "CO₂ (EUA)", "/t")):
        ser = f.get(key) or []
        last = next(((k, v) for k, v in reversed(list(enumerate(ser))) if v is not None and start + timedelta(days=k) <= date.fromisoformat(D)), None)
        if not last:
            continue
        k, v = last
        wk = next((ser[j] for j in range(k - 7, max(-1, k - 12), -1) if 0 <= j < len(ser) and ser[j] is not None), None)
        ch = f", {pct((v - wk) / wk * 100)} on the week" if wk else ""
        out.append((name, f"{eur(v, 1)}{unit}{ch}", (start + timedelta(days=k)).isoformat()))
    if not out:
        return None
    asof = min(x[2] for x in out)
    return "; ".join(f"{n} {t}" for n, t, _ in out) + f" (latest in the fuel file, {nice_day(asof)})."


# ---------------------------------------------------------------- map
def nice_step(rng, target):
    raw = rng / target
    p = 10 ** math.floor(math.log10(raw))
    f = raw / p
    return (1 if f < 1.5 else 2 if f < 3 else 5 if f < 7 else 10) * p


def colour_scale(vals):
    lo, hi = min(vals), max(vals)
    step = nice_step((hi - lo) or 1, 7)
    b0 = math.floor(lo / step) * step
    nb = math.ceil((hi - b0) / step) or 1
    if nb > 9:
        step = nice_step((hi - lo) or 1, 5)
        b0 = math.floor(lo / step) * step
        nb = math.ceil((hi - b0) / step) or 1
    nb = max(1, min(9, nb))
    ramp = [RAMP9[(5 if nb == 1 else 1 + math.floor(k * 8 / (nb - 1) + 0.5)) - 1] for k in range(nb)]
    bucket = lambda v: max(0, min(nb - 1, math.floor((v - b0) / step)))   # noqa: E731
    return ramp, bucket, b0, step, nb


def ink_for(hexc):
    n = int(hexc[1:], 16)
    c = []
    for v in (n >> 16, (n >> 8) & 255, n & 255):
        v /= 255
        c.append(v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4)
    return "#0f1722" if 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2] > 0.3 else "#ffffff"


# Labels moved off their zone's centre where neighbours crowd them (map units): DK1 up-left, DK2 down-right, SE4 up.
EU_POS = (14, 345)    # left edge and vertical centre of the Europe box: NO2's height, under Iceland, clear of Ireland and Britain
LABEL_AT = {"DK1": (455, 441), "DK2": (521, 481), "SE4": (547, 428)}


def map_svg(mapdata, values, partial, title, tip_suffix, regions=None, vs=None, europe=None):
    """A static map like the site's: coloured zones, a name and price label with a halo in the zone's colour."""
    vb = mapdata["viewBox"]
    ramp, bucket, b0, step, nb = colour_scale(list(values.values()))
    s = [f'<svg class="map" viewBox="0 0 {vb[2]} {vb[3]}" role="img" aria-label="{html.escape(title)}">']
    for c in mapdata["context"]:
        s.append(f'<path d="{c["d"]}" fill="#e3e8ed" stroke="#b9c2cc" stroke-width="0.6" vector-effect="non-scaling-stroke"/>')
    shapes = list(mapdata["zones"].items()) + list(mapdata.get("extra", {}).items())
    for z, g in shapes:
        v = values.get(z)
        fill = "#e3e8ed" if v is None else ramp[bucket(v)]
        t = f"{label(z)} ({z}): " + ("no price" if v is None else eur(v, 2) + tip_suffix + (" *" if z in partial else ""))
        s.append(f'<path d="{g["d"]}" fill="{fill}" stroke="#ffffff" stroke-width="1" vector-effect="non-scaling-stroke"><title>{html.escape(t)}</title></path>')
    if regions and mapdata.get("region_borders"):
        s.append(f'<path d="{mapdata["region_borders"]}" fill="none" stroke="#0f1722" stroke-width="2" stroke-opacity=".8" '
                 f'stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke" pointer-events="none"/>')
    for z, g in shapes:
        v = values.get(z)
        if v is None:
            continue
        fill = ramp[bucket(v)]
        ink = ink_for(fill)
        name = z.replace("IT-", "")
        x, y = LABEL_AT.get(z, (g["x"], g["y"]))
        s.append(f'<g class="lab{" tiny" if z in TINY else ""}" font-size="15" fill="{ink}" stroke="{fill}" stroke-width=".27em" '
                 f'stroke-linejoin="round" paint-order="stroke" text-anchor="middle" pointer-events="none">'
                 f'<text class="nm" x="{x}" y="{y}" dy="-.21em" font-size=".86em" opacity=".85">{html.escape(name)}</text>'
                 f'<text class="pr" x="{x}" y="{y}" dy=".86em" font-weight="700">{eur(v)}{"*" if z in partial else ""}</text></g>')
    for r, (avg, ch) in (regions or {}).items():
        if avg is None:
            continue
        x, y = REGION_POS[r]
        zl = region_zone_lines(r)
        top = 3.75 if ch else 2.95                       # box height before the zone lines (em)
        w = max(5.2, max(len(t) for t in zl) * 0.72 * 0.52 + 0.9)
        zt = "".join(f'<text y="{(top - 1.75 + 0.62 + 0.92 * k) / 0.72:.2f}em" font-size=".72em" font-weight="600" fill="#3b4653">{html.escape(t)}</text>'
                     for k, t in enumerate(zl))
        s.append(f'<g class="rlab" transform="translate({x} {y})" font-size="18" text-anchor="middle" pointer-events="none">'
                 f'<rect x="{-w / 2:.2f}em" y="-1.75em" width="{w:.2f}em" height="{top + 0.35 + 0.92 * len(zl):.2f}em" rx=".45em" fill="#ffffff" fill-opacity=".94" '
                 f'stroke="#0f1722" stroke-width="1.6" vector-effect="non-scaling-stroke"/>' + zt +
                 f'<text y="-1.06em" font-size=".8em" font-weight="700" letter-spacing=".1em" fill="#0f1722">{r.upper()}</text>'
                 f'<text y=".46em" font-size="1.4em" font-weight="700" fill="#0f1722">{eur(avg)}</text>'
                 + (f'<text y="1.88em" font-size=".85em" font-weight="600" fill="#3b4653">{ch}</text>' if ch else "") + "</g>")
    if europe and europe[0] is not None:                  # the European average: left edge, centred just above NO2's height, below Iceland
        avg, ch, n = europe
        sub = f"average of {n} countries"
        w = max(6.2, len(sub) * 0.7 * 0.53 + 1.4)
        h = 5.55 if ch else 4.6
        cx = w / 2
        rows = [(".75", "1.35", "700", "#c9d2dc", "EUROPE", ' letter-spacing=".12em"'),
                ("1.6", "2.95", "700", "#ffffff", eur(avg), "")]
        if ch:
            rows.append((".85", "4.0", "600", "#c9d2dc", ch, ""))
        rows.append((".7", f"{h - 0.6:.2f}", "500", "#9aa6b2", sub, ""))
        txt = "".join(f'<text x="{cx / float(f):.2f}em" y="{(float(y) - h / 2) / float(f):.2f}em" font-size="{f}em" font-weight="{wt}" fill="{c}"{ex}>{html.escape(t)}</text>'
                      for f, y, wt, c, t, ex in rows)
        ex, ey = EU_POS
        s.append(f'<g class="eu" transform="translate({ex} {ey})" font-size="22" text-anchor="middle" pointer-events="none">'
                 f'<rect y="{-h / 2:.2f}em" width="{w:.2f}em" height="{h:.2f}em" rx=".45em" fill="#0f1722"/>{txt}</g>')
    s.append("</svg>")
    legend = ['<div class="legend">']
    for k in range(nb):
        lo = b0 + k * step
        lab = f"{lo:g}" if k < nb - 1 else f"{lo:g}–{lo + step:g}"
        legend.append(f'<div><span style="background:{ramp[k]}"></span><em>{"€" if k == 0 else ""}{lab}</em></div>')
    legend.append("</div>")
    if regions:
        eu_chip = (f'<div class="eu"><b>EUROPE</b><strong>{eur(europe[0])}</strong><span>{europe[1] or ""}</span><i>average of {europe[2]} countries</i></div>'
                   if europe and europe[0] is not None else "")
        legend.append('<div class="regrow">' + eu_chip + "".join(
            f'<div><b>{r.upper()}</b><strong>{eur(avg)}</strong><span>{ch or ""}</span><i>{html.escape(" · ".join(region_zone_lines(r)))}</i></div>'
            for r, (avg, ch) in regions.items() if avg is not None) + '</div>')
        legend.append(f'<p class="cap">The dark box is the European average; thick lines mark the four regions. Each shows its average and the change against {html.escape(vs)}.</p>')
    return "".join(s) + "".join(legend)


# ---------------------------------------------------------------- issue
def ranked(vals, n=5, rev=False):
    return sorted(vals.items(), key=lambda kv: kv[1], reverse=rev)[:n]


def table(rows, head, fmt=lambda v: eur(v), extra=None):
    out = [f'<table><thead><tr><th colspan="2">{html.escape(head)}</th></tr></thead><tbody>']
    for z, v in rows:
        out.append(f'<tr><td><b>{html.escape(z)}</b> <span>{html.escape(short_name(z))}</span></td><td class="num">{fmt(v)}{extra(z) if extra else ""}</td></tr>')
    out.append("</tbody></table>")
    return "".join(out)


def build(D, out_dir, pdf, skip_done=False):
    """Write the issue for day D. With skip_done, an issue already built with at least as many zones is left alone."""
    global NAMES
    NAMES = zone_names()
    zones, n_game, P = load_prices()
    drivers = load_drivers()
    mapdata = load_json("zones-map.json")
    if D not in P:
        if skip_done:
            print(f"No prices for {D} yet: no issue today.")
            return False
        raise SystemExit(f"No prices for {D} in the data files.")
    P = {d: v for d, v in P.items() if d <= D}          # never anything after the issue's day
    today = P[D]
    meta_path = os.path.join(out_dir, D, "issue.json")
    if skip_done and os.path.exists(meta_path):
        with open(meta_path, encoding="utf-8") as f:
            done = json.load(f)
        if done.get("zones", 0) >= len(today) and (not pdf or done.get("pdf")):
            print(f"The issue for {D} is already built with {done['zones']} zones: left as it is.")
            return False
    Dd = date.fromisoformat(D)
    yday = (Dd - timedelta(days=1)).isoformat()
    prev = P.get(yday, {})

    # ---- today
    cheap, dear = ranked(today, 7), ranked(today, 7, rev=True)
    lo_z, hi_z = cheap[0][0], dear[0][0]
    spread = today[hi_z] - today[lo_z]
    past30 = [d for d in dates_between((Dd - timedelta(days=30)).isoformat(), yday) if d in P and len(P[d]) > 10]
    avg_spread = st.mean(max(P[d].values()) - min(P[d].values()) for d in past30) if past30 else None
    moves = {z: today[z] - prev[z] for z in today if z in prev}
    ups = one_italian(sorted(moves.items(), key=lambda kv: kv[1], reverse=True), 3)
    downs = one_italian(sorted(moves.items(), key=lambda kv: kv[1]), 3)
    it_notes = [n for n in (italy_note(ups, moves, True), italy_note(downs, moves, False)) if n]
    missing = [z for z in zones if z not in today]

    findings = []
    findings.append(f"Cheapest: {label(lo_z)} at {eur(today[lo_z])}/MWh. Most expensive: {label(hi_z)} at {eur(today[hi_z])}/MWh, "
                    f"a spread of {eur(spread)} across Europe" + (f" (30-day average {eur(avg_spread)})." if avg_spread else "."))
    if ups and ups[0][1] > 0:
        z, v = ups[0]
        line = f"Biggest rise since yesterday: {label(z)}, {signed(v)} to {eur(today[z])}"
        if downs and downs[0][1] < 0:
            z2, v2 = downs[0]
            line += f". Biggest fall: {label(z2)}, {signed(v2)} to {eur(today[z2])}"
        findings.append(line + ".")
    rec = finding_records(P, D, today)
    if rec:
        findings.append(rec)
    for pick, word in ((min, "cheapest"), (max, "most expensive")):
        z, n = streak(P, D, pick)
        if n >= 3:
            findings.append(f"{label(z)} is Europe's {word} zone for the {n}{'th' if 4 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')} day running.")
    grp = finding_groups(today)
    gap = finding_gap(P, D, today)
    why = []
    for z in dict.fromkeys([lo_z, hi_z] + [z for z, _ in ups[:1]] + [z for z, _ in downs[:1]]):
        note = driver_note(drivers, z, D)
        if note:
            why.append(f"{label(z)}: {note}.")
    fuel = None   # fuel_line(D): left out until the fuel file is updated automatically

    # ---- month and year so far
    m0 = Dd.replace(day=1).isoformat()
    mon = period_avg(P, zones, m0, D)
    lm_end = (Dd.replace(day=1) - timedelta(days=1))
    lmon = period_avg(P, zones, lm_end.replace(day=1).isoformat(), lm_end.isoformat())
    ly_m = period_avg(P, zones, Dd.replace(year=Dd.year - 1, day=1).isoformat(), Dd.replace(year=Dd.year - 1).isoformat())
    y0 = Dd.replace(month=1, day=1).isoformat()
    yr = period_avg(P, zones, y0, D)
    ly = period_avg(P, zones, Dd.replace(year=Dd.year - 1, month=1, day=1).isoformat(), Dd.replace(year=Dd.year - 1).isoformat())
    monv = {z: v[0] for z, v in mon.items()}
    yrv = {z: v[0] for z, v in yr.items()}
    mon_part = {z for z, v in mon.items() if v[2]}
    yr_part = {z for z, v in yr.items() if v[2]}

    # the month and year rankings and findings use Italy North as Italy's benchmark (the maps keep every zone)
    monr, mon_part_r = it_bench(monv), mon_part
    yrr, yr_part_r = it_bench(yrv), yr_part
    mf = []
    mz = ranked(monr, 1)[0][0], ranked(monr, 1, rev=True)[0][0]
    mf.append(f"Cheapest so far: {label(mz[0])} at {eur(monr[mz[0]])}. Most expensive: {label(mz[1])} at {eur(monr[mz[1]])}.")
    vs_z = {z: monv[z] - lmon[z][0] for z in monv if z in lmon and not lmon[z][2]}
    vs = it_bench(vs_z)
    if vs:
        up = sum(1 for v in vs.values() if v > 0)
        zb = max(vs, key=lambda z: abs(vs[z]))
        mf.append(f"Against the whole of {MONTHS[lm_end.month - 1]}, prices are up in {up} of {len(vs)} zones. "
                  f"The biggest change: {label(zb)}, {signed(vs[zb])}.")
        now_r, prev_r = it_bench({z: monv[z] for z in vs_z}), it_bench({z: lmon[z][0] for z in vs_z})
        rk_now = {z: k for k, (z, _) in enumerate(sorted(now_r.items(), key=lambda kv: kv[1]))}
        rk_prev = {z: k for k, (z, _) in enumerate(sorted(prev_r.items(), key=lambda kv: kv[1]))}
        zr = max(vs, key=lambda z: abs(rk_now[z] - rk_prev[z]))
        dr = rk_now[zr] - rk_prev[zr]
        if abs(dr) >= 5:
            mf.append(f"Biggest move in the ranking: {label(zr)}, from {rk_prev[zr] + 1}{'st' if rk_prev[zr] == 0 else 'th'} cheapest in "
                      f"{MONTHS[lm_end.month - 1]} to {rk_now[zr] + 1}{'st' if rk_now[zr] == 0 else 'th'} now.")
    vy = [z for z in monv if z in ly_m and not ly_m[z][2]]
    if vy:                                   # the European average, each country once, as in the map's Europe box
        avg_now = europe_avg({z: monv[z] for z in vy})[0]
        avg_then = europe_avg({z: ly_m[z][0] for z in vy})[0]
        both = "" if len(vy) == len(monv) else " (zones with data both years)"
        mf.append(f"The European average{both} is {eur(avg_now)} against {eur(avg_then)} "
                  f"over the same days of {MONTHS[Dd.month - 1]} {Dd.year - 1} ({signed(avg_now - avg_then)}).")

    yf = []
    yz = ranked(yrr, 1)[0][0], ranked(yrr, 1, rev=True)[0][0]
    yf.append(f"Cheapest so far: {label(yz[0])} at {eur(yrr[yz[0]])}. Most expensive: {label(yz[1])} at {eur(yrr[yz[1]])}.")
    cy = it_bench({z: yrv[z] - ly[z][0] for z in yrv if z in ly and not ly[z][2] and not yr[z][2]})   # €/MWh against the same days last year
    if cy:
        up = sum(1 for v in cy.values() if v > 0)
        zu, zd = max(cy, key=cy.get), min(cy, key=cy.get)
        tail = (f"biggest fall: {label(zd)} ({signed(cy[zd])})" if cy[zd] < 0 else f"smallest rise: {label(zd)} ({signed(cy[zd])})")
        yf.append(f"Against the same days of {Dd.year - 1}, prices are up in {up} of {len(cy)} zones. "
                  f"Biggest rise: {label(zu)} ({signed(cy[zu])}); {tail}.")

    # ---- regions
    reg = {}
    for r in REGIONS:
        t, n_t, n_all = region_avg(today, r)
        yd = region_avg(prev, r)[0] if prev else None
        m = region_avg(monv, r)[0]
        lm = region_avg({z: v[0] for z, v in lmon.items()}, r)[0]
        yv = region_avg(yrv, r)[0]
        lyv = region_avg({z: v[0] for z, v in ly.items()}, r)[0]
        reg[r] = dict(t=t, yd=yd, m=m, lm=lm, y=yv, ly=lyv, n=n_t, of=n_all)
    eu = dict(t=europe_avg(today), yd=europe_avg(prev) if prev else (None, 0), m=europe_avg(monv), lm=europe_avg({z: v[0] for z, v in lmon.items()}),
              y=europe_avg(yrv), ly=europe_avg({z: v[0] for z, v in ly.items()}))

    def eu_label(now_key, then_key, mode):
        now, n = eu[now_key]
        then = eu[then_key][0]
        ch = None
        if now is not None and then is not None:
            ch = signed(now - then) if mode == "eur" else pct((now - then) / then * 100)
        return (now, ch, n)
    have = {r: v for r, v in reg.items() if v["t"] is not None}
    region_line = None
    if len(have) >= 2:
        rlo, rhi = min(have, key=lambda r: have[r]["t"]), max(have, key=lambda r: have[r]["t"])
        region_line = (f"By region, the {rlo} was cheapest today at {eur(have[rlo]['t'])}/MWh and the {rhi} most expensive at "
                       f"{eur(have[rhi]['t'])}/MWh (" + ", ".join(f"{r} {eur(v['t'])}" for r, v in have.items() if r not in (rlo, rhi)) + ").")

    def reg_labels(now_key, then_key, mode, vs):
        out = {}
        for r, v in reg.items():
            now, then = v[now_key], v[then_key]
            ch = None
            if now is not None and then is not None:
                ch = signed(now - then) if mode == "eur" else pct((now - then) / then * 100)
            out[r] = (now, ch)
        return out
    # ---- key findings: the strongest few
    key = findings[:1] + ([region_line] if region_line else []) + findings[1:2] + ([rec] if rec and rec not in findings[:2] else []) + ([gap] if gap else []) + ([grp] if grp and not gap else [])
    key = list(dict.fromkeys(key))[:5]

    # ---- html
    title = f"European Spot Price Daily · {WDAYS[Dd.weekday()]} {Dd.day} {MONTHS[Dd.month - 1]} {Dd.year}"
    day_map = map_svg(mapdata, today, set(), f"Day-ahead baseload prices on {nice_day(D, True)}", "/MWh",
                      reg_labels("t", "yd", "eur", "yesterday"), "yesterday", eu_label("t", "yd", "eur"))
    mon_map = map_svg(mapdata, monv, mon_part, f"Average prices, 1–{Dd.day} {MONTHS[Dd.month - 1]}", "/MWh average",
                      reg_labels("m", "lm", "eur", MONS[lm_end.month - 1]), "the whole of " + MONTHS[lm_end.month - 1], eu_label("m", "lm", "eur"))
    yr_map = map_svg(mapdata, yrv, yr_part, f"Average prices, 1 Jan–{nice_day(D)}", "/MWh average",
                     reg_labels("y", "ly", "eur", str(Dd.year - 1)), "the same days of " + str(Dd.year - 1), eu_label("y", "ly", "eur"))
    it_note = lambda vals: (f'<p class="tnote">IT (Italy North) is used as the benchmark for Italian prices in these rankings; '
                            f'the map shows all {sum(1 for z in vals if is_italian(z))} Italian zones.</p>' if "IT" in vals else "")   # noqa: E731
    star = lambda part: (lambda z: "*" if z in part else "")   # noqa: E731
    li = lambda xs: "".join(f"<li>{html.escape(x)}</li>" for x in xs)   # noqa: E731
    pdf_name = f"european-spot-price-daily-{D}.pdf"
    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Big+Shoulders+Display:wght@800&family=Instrument+Sans:wght@400;600;700&family=JetBrains+Mono:wght@500;700&display=swap" rel="stylesheet">
<style>
:root {{ --ink:#0f1722; --ink-2:#3b4653; --muted:#7b8694; --line:#dde3e9; --bg:#edf0f3; --surface:#fff; --accent:#4256d9;
  --f-display:"Big Shoulders Display","Arial Narrow",Impact,sans-serif; --f-body:"Instrument Sans","Segoe UI",system-ui,sans-serif;
  --f-mono:"JetBrains Mono",ui-monospace,Consolas,monospace; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--bg); color: var(--ink); font: 15px/1.5 var(--f-body); }}
main {{ max-width: 860px; margin: 0 auto; padding: 24px 16px 48px; }}
header {{ display: flex; flex-wrap: wrap; align-items: flex-end; justify-content: space-between; gap: 8px 16px; margin-bottom: 14px; }}
.wordmark {{ font-family: var(--f-display); font-weight: 800; font-size: 40px; line-height: .9; text-transform: uppercase; letter-spacing: .01em; }}
.wordmark span {{ color: var(--accent); }}
.date {{ font-family: var(--f-mono); font-size: 14px; color: var(--ink-2); }}
.actions {{ font-size: 13.5px; }}
.actions a {{ color: var(--accent); font-weight: 600; text-decoration: none; margin-left: 12px; }}
.spoiler {{ background: #fff7e0; border: 1px solid #f0d58a; border-radius: 10px; padding: 8px 12px; font-size: 13.5px; margin-bottom: 14px; }}
section {{ background: var(--surface); border-radius: 14px; padding: 18px 20px; margin-bottom: 14px; box-shadow: 0 1px 2px rgba(15,23,34,.06); }}
h2 {{ font-family: var(--f-display); font-weight: 800; text-transform: uppercase; letter-spacing: .03em; font-size: 22px; line-height: 1; margin: 0 0 10px; }}
h2 small {{ font-family: var(--f-body); font-weight: 600; text-transform: none; letter-spacing: 0; font-size: 14px; color: var(--muted); margin-left: 6px; }}
ul.find {{ margin: 0; padding-left: 18px; }} ul.find li {{ margin: 4px 0; }}
.key ul.find li {{ font-size: 15.5px; }}
.map {{ width: 100%; height: auto; display: block; }}
.legend {{ display: flex; gap: 2px; margin: 6px 0 2px; }}
.legend div {{ flex: 1; }} .legend span {{ display: block; height: 10px; border-radius: 3px; }}
.legend em {{ font-style: normal; font-family: var(--f-mono); font-size: 11px; color: var(--muted); }}
.grid {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; margin-top: 12px; align-items: start; }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
th {{ text-align: left; font-size: 12px; color: var(--muted); font-weight: 600; border-bottom: 1px solid var(--line); padding: 4px 0; }}
td {{ padding: 3px 0; border-bottom: 1px solid var(--line); }}
td span {{ color: var(--muted); }}
td:first-child {{ width: 100%; max-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}   /* long zone names cut with … instead of wrapping */
td.num {{ text-align: right; font-family: var(--f-mono); font-weight: 700; white-space: nowrap; }}
.tnote {{ font-size: 11.5px; color: var(--muted); margin: 5px 0 0; line-height: 1.4; }}
.cap {{ font-size: 12px; color: var(--muted); margin: 4px 0 0; }}
.regrow {{ display: none; grid-template-columns: repeat(2, 1fr); gap: 6px; margin-top: 8px; }}
.regrow div {{ border: 1.5px solid var(--ink); border-radius: 8px; padding: 4px 2px; text-align: center; line-height: 1.2; }}
.regrow b {{ display: block; font-size: 10.5px; letter-spacing: .1em; }} .regrow strong {{ display: block; font-size: 17px; }}
.regrow span {{ display: block; font-size: 11.5px; color: var(--ink-2); font-weight: 600; }}
.regrow i {{ display: block; font-style: normal; font-size: 10.5px; color: var(--muted); margin-top: 2px; padding: 0 4px; }}
.regrow div.eu {{ grid-column: 1 / -1; background: var(--ink); border-color: var(--ink); color: #fff; }}
.regrow div.eu span {{ color: #c9d2dc; }} .regrow div.eu i {{ color: #9aa6b2; }}
.why {{ margin-top: 10px; font-size: 13.5px; color: var(--ink-2); }}
.note {{ font-size: 12px; color: var(--muted); margin-top: 8px; }}
footer {{ font-size: 12px; color: var(--muted); line-height: 1.55; }}
footer a {{ color: var(--muted); }}
.map.small .nm, .map.small .tiny {{ display: none; }}
@media (max-width: 640px) {{ .grid {{ grid-template-columns: 1fr; }} .regrow {{ display: grid; }} svg.map .rlab, svg.map .eu {{ display: none; }} .wordmark {{ font-size: 32px; }} section {{ padding: 14px; }} }}
@page {{ size: A4; margin: 11mm; }}
@media print {{ body {{ background: #fff; font-size: 11.5px; }} main {{ max-width: none; padding: 0; }} .actions, .spoiler {{ display: none; }}
  header {{ margin-bottom: 8px; }} .wordmark {{ font-size: 32px; }}
  section {{ box-shadow: none; border: 1px solid var(--line); break-inside: avoid; page-break-inside: avoid; padding: 10px 14px; margin-bottom: 8px; }}
  h2 {{ font-size: 18px; margin-bottom: 6px; }} .key ul.find li {{ font-size: 12.5px; }} ul.find li {{ margin: 2px 0; }}
  .map {{ max-height: 100mm; width: auto; margin: 0 auto; }}
  svg.map g.lab {{ font-size: 21px; }} svg.map g.rlab {{ font-size: 16.5px; }} svg.map g.eu {{ font-size: 20.5px; }} svg.map .tiny {{ display: none; }}   /* the printed map is ~440 px wide: 1000 units */ .legend {{ max-width: 120mm; margin: 4px auto 0; }}
  .grid {{ margin-top: 6px; gap: 10px; }} table {{ font-size: 11px; }} td {{ padding: 2px 0; }} .why {{ font-size: 11px; margin-top: 6px; }} .tnote {{ font-size: 10px; margin-top: 3px; }}
  section.today, section.month {{ break-after: page; page-break-after: always; }} }}
</style>
</head>
<body>
<main>
<header>
  <div><div class="wordmark">European Spot Price <span>Daily</span></div><div class="date">{html.escape(WDAYS[Dd.weekday()])} {Dd.day} {MONTHS[Dd.month - 1]} {Dd.year} · day-ahead baseload prices, {len(today)} bidding zones, {len({NAMES.get(z, (z, ''))[0] for z in today})} countries</div></div>
  <div class="actions"><a href="{pdf_name}">Download PDF</a><a href="../">All issues</a><a href="{SITE_URL}">Play today's puzzle</a></div>
</header>
<div class="spoiler">This issue shows today's prices, so it gives away today's Spotrank puzzle. <a href="{SITE_URL}">Play it first</a>.</div>

<section class="key"><h2>Key findings</h2><ul class="find">{li(key)}</ul></section>


<section class="today"><h2>Today <small>{nice_day(D, True)}, €/MWh</small></h2>
{day_map}
<div class="grid">
{table(cheap, "Cheapest")}
{table(dear, "Most expensive")}
<div>{table(ups + downs, "Biggest moves since yesterday", fmt=lambda v: signed(v))}{"".join(f'<p class="tnote">{html.escape(n)}</p>' for n in it_notes)}</div>
</div>
<ul class="find" style="margin-top:12px">{li([x for x in findings[2:] + ([grp] if grp else []) + ([gap] if gap else []) if x not in key])}</ul>
{('<div class="why"><b>Forecasts that stand out</b> (ENTSO-E day-ahead): ' + html.escape(' '.join(why)) + '</div>') if why else ''}
{('<div class="why"><b>Fuel</b>: ' + html.escape(fuel) + '</div>') if fuel else ''}
{('<p class="note">No price today for ' + ', '.join(missing) + '.</p>') if missing else ''}
</section>

<section class="month"><h2>{MONTHS[Dd.month - 1]} so far <small>1–{Dd.day} {MONTHS[Dd.month - 1]}, average €/MWh</small></h2>
{mon_map}
<div class="grid">
{table(ranked(monr, 10), "Cheapest", extra=star(mon_part_r))}
{table(ranked(monr, 10, rev=True), "Most expensive", extra=star(mon_part_r))}
{table(sorted(vs.items(), key=lambda kv: -abs(kv[1]))[:10] if vs else [], "Change against " + MONTHS[lm_end.month - 1], fmt=lambda v: signed(v))}
</div>
{it_note(monv)}
<ul class="find" style="margin-top:12px">{li(mf)}</ul>
</section>

<section><h2>{Dd.year} so far <small>1 Jan–{nice_day(D)}, average €/MWh</small></h2>
{yr_map}
<div class="grid">
{table(ranked(yrr, 10), "Cheapest", extra=star(yr_part_r))}
{table(ranked(yrr, 10, rev=True), "Most expensive", extra=star(yr_part_r))}
{table(sorted(cy.items(), key=lambda kv: -abs(kv[1]))[:10] if cy else [], "Change against " + str(Dd.year - 1) + " (same days)", fmt=lambda v: signed(v))}
</div>
{it_note(yrv)}
<ul class="find" style="margin-top:12px">{li(yf)}</ul>
</section>

<footer>
<p><b>Method.</b> A day's price is the time-weighted average of the day-ahead prices over the CET delivery day (Great Britain: the UK day), kept when at least 20 hours have a price.
Month and year figures average the daily prices; an asterisk marks an average on fewer than 90% of the period's days. Regional and European averages count each country once (a country with several zones as the average of its zones). Findings are generated by fixed rules from the same data.</p>
<p><b>Sources.</b> ENTSO-E Transparency Platform (prices, load, wind and solar forecasts); Great Britain: N2EX day-ahead price from NESO's open data (before 21 Sep 2021: LCCC's reference price), converted at the ECB euro reference rate;
Borders: entsoe-py, Natural Earth.</p>
</footer>
</main>
<script>
// label sizes in screen pixels whatever the map's width (the SVG scales); labels thin out on narrow screens
function fitLabels() {{
  document.querySelectorAll('svg.map').forEach(svg => {{
    const w = svg.getBoundingClientRect().width || 760, u = svg.viewBox.baseVal.width / w, small = w < 560;
    svg.classList.toggle('small', small);
    svg.querySelectorAll('g.lab').forEach(g => {{ g.setAttribute('font-size', ((small ? 9 : 11) * u).toFixed(2)); g.querySelector('.pr').setAttribute('dy', small ? '.33em' : '.86em'); }});
    svg.querySelectorAll('g.rlab').forEach(g => g.setAttribute('font-size', (13 * u).toFixed(2)));
    svg.querySelectorAll('g.eu').forEach(g => g.setAttribute('font-size', (16 * u).toFixed(2)));
  }});
}}
fitLabels(); addEventListener('resize', fitLabels); addEventListener('beforeprint', fitLabels);
</script>
</body>
</html>
"""
    target = os.path.join(out_dir, D)
    os.makedirs(target, exist_ok=True)
    page = os.path.join(target, "index.html")
    with open(page, "w", encoding="utf-8") as f:
        f.write(doc)
    print(f"wrote {page} ({len(doc) // 1024} KB)")
    if pdf:
        chrome = next((c for c in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser") if shutil.which(c)), None)
        if not chrome:
            raise SystemExit("No Chrome found for the PDF.")
        out = os.path.join(target, pdf_name)
        # Aim for 3 pages (today, month, year): a long day (wrapped date, many findings) can push Today onto page 2,
        # so print again with a smaller map until it fits.
        for mh in (100, 92, 84, 76):
            with tempfile.TemporaryDirectory() as tmp:
                copy = os.path.join(tmp, "issue.html")
                with open(copy, "w", encoding="utf-8") as f:
                    f.write(doc.replace("</head>", f"<style>@media print {{ .map {{ max-height: {mh}mm !important; }} }}</style>\n</head>", 1))
                subprocess.run([chrome, "--headless=new", "--no-sandbox", "--disable-gpu", f"--user-data-dir={tmp}/profile", "--no-pdf-header-footer",
                                "--virtual-time-budget=8000", "--window-size=900,1200", f"--print-to-pdf={out}", "file://" + copy],
                               check=True, capture_output=True, timeout=180)
            with open(out, "rb") as f:
                pages = len(re.findall(rb"/Type\s*/Page(?![s\w])", f.read()))
            if pages <= 3:
                break
        print(f"wrote {out} ({os.path.getsize(out) // 1024} KB, {pages} pages, map {mh} mm)")
    meta = dict(date=D, zones=len(today), countries=len({NAMES.get(z, (z, ""))[0] for z in today}),
                pdf=pdf_name if pdf else None, headline=key[0] if key else "",
                built=datetime.now(ZoneInfo("Europe/Helsinki")).isoformat(timespec="minutes"))
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    return True


def write_archive(out_dir):
    """The archive page: every issue, newest first, with its first finding and links to the page and the PDF."""
    issues = []
    for d in sorted(os.listdir(out_dir), reverse=True) if os.path.isdir(out_dir) else []:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) or not os.path.exists(os.path.join(out_dir, d, "index.html")):
            continue
        meta = {}
        mp = os.path.join(out_dir, d, "issue.json")
        if os.path.exists(mp):
            with open(mp, encoding="utf-8") as f:
                meta = json.load(f)
        issues.append((d, meta))

    def when(d, short=False):
        x = date.fromisoformat(d)
        return (f"{WDAYS[x.weekday()][:3]} {x.day} {MONS[x.month - 1]} {x.year}" if short else f"{WDAYS[x.weekday()]} {x.day} {MONTHS[x.month - 1]} {x.year}")

    def links(d, meta):
        pdf = meta.get("pdf")
        return f'<a href="{d}/">Read</a>' + (f'<a href="{d}/{html.escape(pdf)}">PDF</a>' if pdf and os.path.exists(os.path.join(out_dir, d, pdf)) else "")

    body = []
    if issues:
        d, meta = issues[0]
        body.append(f'<section class="latest"><div class="lbl">Latest issue</div><div class="when">{when(d)}</div>'
                    f'<p>{html.escape(meta.get("headline", ""))}</p><div class="links">{links(d, meta)}</div></section>')
        month = None
        rows = []
        for d, meta in issues:
            m = d[:7]
            if m != month:
                if rows:
                    rows.append("</ul>")
                x = date.fromisoformat(d)
                rows.append(f"<h2>{MONTHS[x.month - 1]} {x.year}</h2><ul>")
                month = m
            rows.append(f'<li><span class="d">{when(d, True)}</span><span class="h">{html.escape(meta.get("headline", ""))}</span>'
                        f'<span class="links">{links(d, meta)}</span></li>')
        rows.append("</ul>")
        body.append('<section class="all">' + "".join(rows) + "</section>")
    else:
        body.append("<section><p>No issues yet.</p></section>")
    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>European Spot Price Daily · All issues</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Big+Shoulders+Display:wght@800&family=Instrument+Sans:wght@400;600;700&family=JetBrains+Mono:wght@500;700&display=swap" rel="stylesheet">
<style>
:root {{ --ink:#0f1722; --ink-2:#3b4653; --muted:#7b8694; --line:#dde3e9; --bg:#edf0f3; --surface:#fff; --accent:#4256d9;
  --f-display:"Big Shoulders Display","Arial Narrow",Impact,sans-serif; --f-body:"Instrument Sans","Segoe UI",system-ui,sans-serif;
  --f-mono:"JetBrains Mono",ui-monospace,Consolas,monospace; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--bg); color: var(--ink); font: 15px/1.5 var(--f-body); }}
main {{ max-width: 860px; margin: 0 auto; padding: 24px 16px 48px; }}
.wordmark {{ font-family: var(--f-display); font-weight: 800; font-size: 40px; line-height: .9; text-transform: uppercase; }}
.wordmark span {{ color: var(--accent); }}
.sub {{ font-family: var(--f-mono); font-size: 14px; color: var(--ink-2); margin: 4px 0 16px; }}
section {{ background: var(--surface); border-radius: 14px; padding: 18px 20px; margin-bottom: 14px; box-shadow: 0 1px 2px rgba(15,23,34,.06); }}
.latest {{ background: var(--ink); color: #fff; }}
.latest .lbl {{ font-size: 12px; font-weight: 700; letter-spacing: .12em; text-transform: uppercase; color: #c9d2dc; }}
.latest .when {{ font-family: var(--f-display); font-weight: 800; font-size: 28px; text-transform: uppercase; line-height: 1.1; }}
.latest p {{ margin: 6px 0 10px; color: #dde3e9; }}
.latest a {{ color: #fff; background: var(--accent); border-radius: 8px; padding: 5px 14px; margin-right: 8px; font-weight: 600; text-decoration: none; }}
h2 {{ font-family: var(--f-display); font-weight: 800; text-transform: uppercase; letter-spacing: .03em; font-size: 20px; margin: 14px 0 6px; }}
h2:first-child {{ margin-top: 0; }}
ul {{ list-style: none; margin: 0; padding: 0; }}
li {{ display: grid; grid-template-columns: 9.5em 1fr auto; gap: 4px 12px; padding: 7px 0; border-bottom: 1px solid var(--line); align-items: baseline; }}
li .d {{ font-family: var(--f-mono); font-size: 13px; font-weight: 700; }}
li .h {{ font-size: 13.5px; color: var(--ink-2); }}
.all .links a {{ color: var(--accent); font-weight: 600; text-decoration: none; margin-left: 10px; font-size: 13.5px; }}
footer {{ font-size: 12px; color: var(--muted); }} footer a {{ color: var(--muted); }}
@media (max-width: 640px) {{ li {{ grid-template-columns: 1fr auto; }} li .h {{ grid-column: 1 / -1; grid-row: 2; }} }}
</style>
</head>
<body>
<main>
<div class="wordmark">European Spot Price <span>Daily</span></div>
<div class="sub">All issues · {len(issues)} so far · day-ahead baseload prices across Europe</div>
{"".join(body)}
<footer><p>A new issue each morning from the <a href="{SITE_URL}">Spotrank</a> data: ENTSO-E Transparency Platform, NESO (Great Britain) and the ECB euro reference rate.</p></footer>
</main>
</body>
</html>
"""
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(doc)
    print(f"wrote {os.path.join(out_dir, 'index.html')} ({len(issues)} issues)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", default=datetime.now(ZoneInfo("Europe/Helsinki")).date().isoformat())
    ap.add_argument("--out", default=os.path.join(SITE, "daily"))
    ap.add_argument("--pdf", action="store_true")
    ap.add_argument("--skip-if-done", action="store_true", help="leave an issue already built with as many zones alone")
    a = ap.parse_args()
    build(a.date, a.out, a.pdf, a.skip_if_done)
    write_archive(a.out)


if __name__ == "__main__":
    main()
