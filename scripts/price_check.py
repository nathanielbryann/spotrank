"""Compare one zone's day-ahead prices on ENTSO-E with the Estonian TSO Elering's feed (EE, FI, LV, LT).

Run by .github/workflows/price-check.yml; the zone and day come from .github/price-check.txt
("zone: EE" and "day: 2026-11-17"). Writes diagnostics/price_check.md.
"""
import json
import os
import sys
import urllib.request

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_spotrank_data as b  # noqa: E402

req = dict(l.split(":", 1) for l in open(".github/price-check.txt").read().splitlines() if ":" in l)
zone, day = req.get("zone", "EE").strip(), req.get("day", "").strip()
start = pd.Timestamp(day, tz=b.TZ)
end = start + pd.Timedelta(days=1)
out = [f"# Price check: {zone}, delivery day {day}", "", f"Checked {pd.Timestamp.now(tz='UTC'):%Y-%m-%d %H:%M} UTC.", ""]

from entsoe import EntsoePandasClient  # noqa: E402
client = EntsoePandasClient(api_key=os.environ["ENTSOE_API_KEY"], timeout=90)
areas = [zone] + [z for z in ("FI", "LV", "LT") if z != zone]
ent = {}
for z in areas:
    try:
        s = client.query_day_ahead_prices(b.ZONES[z], start=start, end=end)
        s = s[~s.index.duplicated(keep="last")]
        ent[z] = s
        step = s.index.to_series().diff().median() if len(s) > 1 else None
        line = (f"ENTSO-E {z}: {len(s)} points ({s.index.min():%H:%M} to {s.index.max():%H:%M}, step {step}), "
                f"min {s.min():.2f}, max {s.max():.2f}, zeros {int((s == 0).sum())}")
        dbl = b.daily_baseload(s, prices=True)
        out.append(line + (f", daily baseload {dbl.iloc[0]:.2f}" if len(dbl) else ", no daily baseload (under 20 h of data)"))
    except Exception as e:  # noqa: BLE001
        out.append(f"ENTSO-E {z}: error {type(e).__name__}: {e}")

ele = {}
if zone in ("EE", "FI", "LV", "LT"):
    url = (f"https://dashboard.elering.ee/api/nps/price?start={start.tz_convert('UTC'):%Y-%m-%dT%H:%M:%S.000Z}"
           f"&end={(end - pd.Timedelta(seconds=1)).tz_convert('UTC'):%Y-%m-%dT%H:%M:%S.999Z}")
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "spotrank-check"}), timeout=60) as r:
            data = json.load(r)["data"]
        out.append("")
        for z in areas:
            pts = data.get(z.lower(), [])
            if not pts:
                out.append(f"Elering {z}: no data"); continue
            s = pd.Series([p["price"] for p in pts], index=pd.to_datetime([p["timestamp"] for p in pts], unit="s", utc=True))
            ele[z] = s
            out.append(f"Elering {z}: {len(s)} points, min {s.min():.2f}, max {s.max():.2f}, "
                       f"zeros {int((s == 0).sum())}, daily baseload {b.daily_baseload(s, prices=True).iloc[0]:.2f}")
    except Exception as e:  # noqa: BLE001
        out.append(f"Elering: error {type(e).__name__}: {e}")

if zone in ent:
    out += ["", f"## {zone} by hour (CET), ENTSO-E vs Elering", "", "| hour | ENTSO-E | Elering |", "|---|---:|---:|"]
    e1 = ent[zone].tz_convert(b.TZ).resample("60min").mean()
    e2 = ele[zone].tz_convert(b.TZ).resample("60min").mean() if zone in ele else None
    for t, v in e1.items():
        w = e2.get(t) if e2 is not None else None
        out.append(f"| {t:%H:%M} | {v:.2f} | {'' if w is None or pd.isna(w) else f'{w:.2f}'} |")
os.makedirs("diagnostics", exist_ok=True)
open("diagnostics/price_check.md", "w").write("\n".join(out) + "\n")
print("\n".join(out))
