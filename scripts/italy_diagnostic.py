"""What ENTSO-E publishes for the Italian bidding zones (one-off check).

For each zone and for Italy as a whole: day-ahead prices, load forecast, wind and solar
forecast, actual generation by production type, and installed capacity, for a recent
month and for January 2021. Writes diagnostics/italy_availability.md.
"""
import os
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
from entsoe import EntsoePandasClient

TZ = "Europe/Brussels"
AREAS = ["IT_NORD", "IT_CNOR", "IT_CSUD", "IT_SUD", "IT_CALA", "IT_SICI", "IT_SARD", "IT"]
MONTHS = {"Oct 2026": ("2026-10-01", "2026-11-01"), "Jan 2021": ("2021-01-01", "2021-02-01")}
YEARS = [2021, 2026]


def client():
    return EntsoePandasClient(api_key=os.environ["ENTSOE_API_KEY"], timeout=90, retry_count=2, retry_delay=5)


def describe(df):
    if df is None:
        return "no data"
    if isinstance(df, pd.Series):
        return f"{df.notna().sum()} points"
    cols = df.columns
    if isinstance(cols, pd.MultiIndex):
        types = sorted({str(c[0]) for c in cols if str(c[-1]) != "Actual Consumption"})
    else:
        types = [str(c) for c in cols]
    return f"{len(df)} rows; {len(types)} types: " + ", ".join(types)


def run(job):
    area, what, label, a, b = job
    t0 = time.time()
    try:
        c = client()
        s, e = pd.Timestamp(a, tz=TZ), pd.Timestamp(b, tz=TZ)
        if what == "prices":
            out = c.query_day_ahead_prices(area, start=s, end=e)
        elif what == "load forecast":
            out = c.query_load_forecast(area, start=s, end=e)
        elif what == "wind/solar forecast":
            out = c.query_wind_and_solar_forecast(area, start=s, end=e)
        elif what == "generation":
            out = c.query_generation(area, start=s, end=e)
        else:
            out = c.query_installed_generation_capacity(area, start=s, end=e)
        res = describe(out)
    except Exception as ex:  # noqa: BLE001
        res = "no data" if type(ex).__name__ == "NoMatchingDataError" else f"error: {type(ex).__name__}: {str(ex)[:80]}"
    return area, what, label, res, round(time.time() - t0)


jobs = []
for area in AREAS:
    for label, (a, b) in MONTHS.items():
        for what in ("prices", "load forecast", "wind/solar forecast", "generation"):
            jobs.append((area, what, label, a, b))
    for y in YEARS:
        jobs.append((area, "capacity", str(y), f"{y}-01-01", f"{y}-12-31"))
with ThreadPoolExecutor(max_workers=16) as pool:
    results = list(pool.map(run, jobs))

os.makedirs("diagnostics", exist_ok=True)
lines = [f"# Italian zones on ENTSO-E ({pd.Timestamp.now(tz='UTC'):%Y-%m-%d %H:%M} UTC)", "",
         "| Area | Data | Period | Result | s |", "|---|---|---|---|---|"]
for area, what, label, res, sec in results:
    lines.append(f"| {area} | {what} | {label} | {res} | {sec} |")
open("diagnostics/italy_availability.md", "w").write("\n".join(lines) + "\n")
print("\n".join(lines))
