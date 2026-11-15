"""Check which zones already have TOMORROW's data on ENTSO-E right now.

For the Forecast challenge we need to know what a player could see before the
day-ahead auction (gate closure 12:00 CET): the day-ahead load forecast, the
day-ahead wind and solar forecast, and (as a control) whether tomorrow's prices
exist yet. Run in the morning; results go to diagnostics/.

    python scripts/forecast_diagnostic.py --out-dir diagnostics
"""
import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_spotrank_data as b  # noqa: E402


def hours_covered(obj):
    """Hours of tomorrow covered by a Series/DataFrame of forecasts or prices."""
    if obj is None or len(obj) == 0:
        return 0.0
    idx = obj.index.tz_convert(b.TZ)
    s = pd.Series(1, index=idx).sort_index()
    step = s.index.to_series().diff().median()
    step_h = (step.total_seconds() / 3600) if pd.notna(step) else 1.0
    return round(len(s) * step_h, 2)


def check_zone(make_client, z, start, end):
    client = make_client()
    area = b.DRIVER_AREA[z]
    out = {}
    for name, fn in (("load", client.query_load_forecast), ("ws", client.query_wind_and_solar_forecast),
                     ("price", client.query_day_ahead_prices)):
        t0 = time.time()
        try:
            res = fn(area, start=start, end=end)
            if name == "ws":
                cols = [str(c) for c in getattr(res, "columns", [])]
                wind = [c for c in cols if "Wind" in c]
                solar = [c for c in cols if "Solar" in c]
                out["wind_h"] = hours_covered(res[wind].dropna(how="all")) if wind else 0.0
                out["solar_h"] = hours_covered(res[solar].dropna(how="all")) if solar else 0.0
            else:
                out[name + "_h"] = hours_covered(res.dropna() if hasattr(res, "dropna") else res)
        except Exception as e:  # noqa: BLE001
            kind = type(e).__name__
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status in (401, 403):
                raise b.KeyRejected(f"ENTSO-E rejected the API key (HTTP {status}).") from e
            if name == "ws":
                out["wind_h"] = out["solar_h"] = 0.0 if kind == "NoMatchingDataError" else None
            else:
                out[name + "_h"] = 0.0 if kind == "NoMatchingDataError" else None
            if kind != "NoMatchingDataError":
                out.setdefault("errors", []).append(f"{name}: {kind}")
        out.setdefault("secs", {})[name] = round(time.time() - t0, 1)
    return z, out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", default=os.path.join(b.HERE, "..", "diagnostics"))
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--key", default=None)
    args = ap.parse_args()

    key = b.resolve_key(args.key)
    if not key:
        sys.exit("No API key.")
    from entsoe import EntsoePandasClient

    def make_client():
        return EntsoePandasClient(api_key=key, timeout=90, retry_count=2, retry_delay=5)

    now_utc = pd.Timestamp.now(tz="UTC")
    now_cet = now_utc.tz_convert(b.TZ)
    tomorrow = now_cet.normalize() + pd.Timedelta(days=1)
    start, end = tomorrow, tomorrow + pd.Timedelta(days=1)
    print(f"Run at {now_cet:%Y-%m-%d %H:%M} CET; checking delivery day {tomorrow:%Y-%m-%d}")

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        try:
            results = dict(pool.map(lambda z: check_zone(make_client, z, start, end), list(b.ZONES)))
        except b.KeyRejected as e:
            sys.exit(str(e))

    def full(h):
        return h is not None and h >= 23

    summary = {
        "load": sum(full(r.get("load_h")) for r in results.values()),
        "wind": sum(full(r.get("wind_h")) for r in results.values()),
        "solar": sum(full(r.get("solar_h")) for r in results.values()),
        "price": sum(full(r.get("price_h")) for r in results.values()),
        "errors": sum(1 for r in results.values() if r.get("errors")),
    }
    print(f"Zones with a full day for tomorrow (of {len(results)}): load {summary['load']}, wind {summary['wind']}, "
          f"solar {summary['solar']}, prices {summary['price']}; zones with errors: {summary['errors']}")

    os.makedirs(args.out_dir, exist_ok=True)
    stamp = now_cet.strftime("%Y-%m-%dT%H%M")
    with open(os.path.join(args.out_dir, f"forecast_availability_{stamp}CET.json"), "w", encoding="utf-8") as f:
        json.dump({"run_cet": now_cet.isoformat(timespec="minutes"), "delivery_day": f"{tomorrow:%Y-%m-%d}",
                   "summary": summary, "zones": results}, f, indent=1)
    md = os.path.join(args.out_dir, "forecast_availability.md")
    new = not os.path.exists(md)
    with open(md, "a", encoding="utf-8") as f:
        if new:
            f.write("# Forecast data availability\n\nZones (of 32) with a full day of data for the next delivery day, "
                    "checked at the time shown. Hours per zone are in the JSON files.\n\n"
                    "| Checked (CET) | Delivery day | Load forecast | Wind forecast | Solar forecast | Prices | Zones with errors |\n"
                    "|---|---|---|---|---|---|---|\n")
        f.write(f"| {now_cet:%Y-%m-%d %H:%M} | {tomorrow:%Y-%m-%d} | {summary['load']} | {summary['wind']} | "
                f"{summary['solar']} | {summary['price']} | {summary['errors']} |\n")
    for z, r in results.items():
        print(f"  {z:6s} load {r.get('load_h')}  wind {r.get('wind_h')}  solar {r.get('solar_h')}  price {r.get('price_h')}"
              + (f"  errors {r['errors']}" if r.get("errors") else ""))


if __name__ == "__main__":
    main()
