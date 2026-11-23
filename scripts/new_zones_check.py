"""One-off check of the sources for the map-only zones: Ireland (SEM) on ENTSO-E, Great Britain from
LCCC's IMRP (GB day-ahead hourly price), and the ECB's GBP rate. Writes diagnostics/new_zones_check.md.

    python scripts/new_zones_check.py
"""
import json
import os
import sys
import traceback
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_spotrank_data as b  # noqa: E402

OUT = os.path.join(HERE, "..", "diagnostics", "new_zones_check.md")
LCCC = "https://dp.lowcarboncontracts.uk"
lines = [f"# New zones check\n\nChecked {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC.\n"]


def say(s=""):
    print(s)
    lines.append(s)


def get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "spotrank (github.com/nathanielbryann/spotrank)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), dict(r.headers)


def section(title, fn):
    say(f"\n## {title}\n")
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        say(f"ERROR {type(e).__name__}: {e}")
        say("```\n" + traceback.format_exc()[-1500:] + "\n```")


def entsoe_ie():
    from entsoe import EntsoePandasClient
    c = EntsoePandasClient(api_key=b.resolve_key(None), timeout=90, retry_count=2, retry_delay=5)
    today = pd.Timestamp.now(tz=b.TZ).normalize()
    for label, start, end in [("first days of 2021", pd.Timestamp("2021-01-01", tz=b.TZ), pd.Timestamp("2021-01-04", tz=b.TZ)),
                              ("last 3 days and tomorrow", today - pd.Timedelta(days=3), today + pd.Timedelta(days=2))]:
        s = c.query_day_ahead_prices("IE_SEM", start=start, end=end)
        step = s.index.to_series().diff().median() if len(s) > 1 else None
        say(f"Prices, {label}: {len(s)} points, {s.index.min()} to {s.index.max()}, step {step}")
        say("Daily baseload: " + ", ".join(f"{d}: {v:.2f}" for d, v in b.daily_baseload(s, prices=True).items()))
    d0, d1 = today - pd.Timedelta(days=2), today - pd.Timedelta(days=1)
    for name, fn in [("load forecast", lambda: c.query_load_forecast("IE_SEM", start=d0, end=d1)),
                     ("wind/solar forecast", lambda: c.query_wind_and_solar_forecast("IE_SEM", start=d0, end=d1)),
                     ("generation", lambda: c.query_generation("IE_SEM", start=d0, end=d1)),
                     ("installed capacity", lambda: c.query_installed_generation_capacity("IE_SEM", start=pd.Timestamp("2026-01-01", tz=b.TZ), end=pd.Timestamp("2026-12-31", tz=b.TZ)))]:
        try:
            df = fn()
            cols = list(df.columns)[:12] if hasattr(df, "columns") else ["(series)"]
            say(f"{name}: {len(df)} rows; columns {cols}")
        except Exception as e:  # noqa: BLE001
            say(f"{name}: {type(e).__name__}: {e}")


def lccc():
    raw, _ = get(f"{LCCC}/api/3/action/package_show?id=imrp-actuals")
    pkg = json.loads(raw)["result"]
    say(f"Dataset metadata_modified {pkg.get('metadata_modified')}, {len(pkg.get('resources', []))} resources")
    for r in pkg.get("resources", []):
        say(f"- {r.get('id')} | {r.get('name')} | {r.get('format')} | datastore {r.get('datastore_active')} | "
            f"created {r.get('created')} | modified {r.get('last_modified') or r.get('metadata_modified')} | {r.get('url')}")
    for r in pkg.get("resources", []):
        if r.get("format") == "CSV" and "definition" not in (r.get("name") or "").lower() + (r.get("url") or ""):
            raw, hdr = get(r["url"], timeout=120)
            rows = raw.decode("utf-8-sig").splitlines()
            say(f"\nCSV {r['id']}: {len(raw) / 1e6:.1f} MB, {len(rows) - 1} rows, Last-Modified {hdr.get('Last-Modified')}; header {rows[0]}")
            df = pd.read_csv(__import__('io').StringIO(raw.decode('utf-8-sig')))
            dcol = next(c for c in df.columns if 'date' in c.lower())
            df[dcol] = pd.to_datetime(df[dcol].astype(str).str[:10])
            last = df[dcol].max()
            say(f"First date {df[dcol].min().date()}, last date {last.date()}; periods on the last 4 dates: "
                + ", ".join(f"{d.date()}: {n}" for d, n in df.groupby(dcol).size().tail(4).items()))
            for d in sorted(df[dcol].unique())[-3:]:
                sub = df[df[dcol] == d]
                say(f"  {pd.Timestamp(d).date()}: " + " ".join(f"{int(p)}={v}" for p, v in zip(sub.iloc[:, 1], sub.iloc[:, 2])))
    for r in pkg.get("resources", []):
        if not r.get("datastore_active"):
            continue
        rid = r["id"]
        raw, _ = get(f"{LCCC}/api/3/action/datastore_search?resource_id={rid}&limit=1")
        res = json.loads(raw)["result"]
        fields = [f["id"] for f in res.get("fields", [])]
        say(f"\nResource {rid}: {res.get('total')} rows; fields {fields}")
        date_f = next((f for f in fields if "date" in f.lower()), None)
        if date_f:
            q = urllib.parse.urlencode({"resource_id": rid, "limit": 30, "sort": f'"{date_f}" desc'})
            try:
                raw, _ = get(f"{LCCC}/api/3/action/datastore_search?{q}")
                recs = json.loads(raw)["result"]["records"]
                say("Latest rows: " + "; ".join(json.dumps({k: v for k, v in x.items() if k != "_id"}) for x in recs[:30]))
            except Exception as e:  # noqa: BLE001
                say(f"sorted search failed: {e}")
            q = urllib.parse.urlencode({"resource_id": rid, "limit": 3, "sort": f'"{date_f}" asc'})
            try:
                raw, _ = get(f"{LCCC}/api/3/action/datastore_search?{q}")
                say("First rows: " + "; ".join(json.dumps({k: v for k, v in x.items() if k != "_id"}) for x in json.loads(raw)["result"]["records"]))
            except Exception as e:  # noqa: BLE001
                say(f"first rows failed: {e}")


def ecb():
    raw, _ = get("https://data-api.ecb.europa.eu/service/data/EXR/D.GBP.EUR.SP00.A?lastNObservations=5&format=csvdata")
    rows = raw.decode("utf-8").splitlines()
    head = rows[0].split(",")
    iT, iV = head.index("TIME_PERIOD"), head.index("OBS_VALUE")
    say("ECB EUR/GBP (GBP per EUR): " + ", ".join(f"{r.split(',')[iT]}: {r.split(',')[iV]}" for r in rows[1:]))


section("Ireland (SEM) on ENTSO-E", entsoe_ie)
section("Great Britain: LCCC IMRP actuals", lccc)
section("ECB exchange rate", ecb)
os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
