"""Clues for the Forecast challenge, refreshed in the morning before the 11:00 CET cut-off.

--entsoe   tomorrow's day-ahead demand, wind and solar forecasts from ENTSO-E, as far as published, merged
           into site/data/spotrank_drivers.json. Never prices: Switzerland's auction result comes out at
           11:10 CET, and nothing price-related may reach the page before the cut-off.
--weather  a weather proxy for every zone from Open-Meteo (free for non-commercial use, CC BY 4.0):
           mean 100 m wind speed (m/s) and total solar radiation (kWh/m²) per CET day, averaged over up
           to 8 points inside the zone (scripts/weather_points.json), for today and the next two days.
           Written to site/data/clues.json. Most wind/solar forecasts on ENTSO-E come after the cut-off
           (grid operators have until 18:00), so this is the wind and sun clue for most zones.

    python scripts/build_clues.py --entsoe --weather
"""
import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_spotrank_data as b  # noqa: E402

OPEN_METEO = "https://api.open-meteo.com/v1/forecast"


def entsoe_tomorrow(drivers_path, key, workers):
    from entsoe import EntsoePandasClient

    def make_client():
        return EntsoePandasClient(api_key=key, timeout=60, retry_count=2, retry_delay=5)

    today = pd.Timestamp.now(tz=b.TZ).normalize()
    start, end = today + pd.Timedelta(days=1), today + pd.Timedelta(days=2)
    day = start.strftime("%Y-%m-%d")
    zones = list(b.ZONES)
    results, failed = {}, []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(b.fetch_driver_range, make_client, z, start, end): z for z in zones}
        for f, z in futs.items():
            try:
                results[z] = f.result(timeout=240)
            except b.KeyRejected:
                raise
            except Exception as e:  # noqa: BLE001
                failed.append(f"{z}: {type(e).__name__}")
    frames, fetched = b.load_drivers(drivers_path)
    counts = {m: 0 for m in b.METRICS}
    for z, daily in results.items():
        for m in b.METRICS:
            s = daily.get(m)
            if s is None or s.empty or day not in s.index:   # daily values are keyed "YYYY-MM-DD"
                continue
            v = float(s.loc[day])
            fr = frames[m]
            if z not in fr.columns:
                fr[z] = float("nan")
            if day not in fr.index:
                fr = fr.reindex(fr.index.union([day]))
            fr.loc[day, z] = v
            frames[m] = fr
            counts[m] += 1
    with open(drivers_path, encoding="utf-8") as f:
        old = json.load(f)
    b.write_drivers(drivers_path, frames, fetched, old.get("zones", zones), pd.Timestamp(old["dates"][0]))
    print(f"ENTSO-E for {day}: load {counts['load']}, wind {counts['wind']}, solar {counts['solar']} of {len(zones)} zones"
          + (f"; failed: {', '.join(failed)}" if failed else ""))
    return {"date": day, "zones": len(zones), **counts, "failed": len(failed)}


def weather(points, days):
    out = {d: {} for d in days}

    def one(z):
        pts = points[z]
        q = {
            "latitude": ",".join(str(p[0]) for p in pts), "longitude": ",".join(str(p[1]) for p in pts),
            "hourly": "wind_speed_100m,shortwave_radiation", "wind_speed_unit": "ms",
            "timezone": "Europe/Berlin", "start_date": days[0], "end_date": days[-1],
        }
        url = OPEN_METEO + "?" + urllib.parse.urlencode(q)
        for k in range(3):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "spotrank"}), timeout=40) as r:
                    data = json.load(r)
                break
            except Exception:  # noqa: BLE001
                if k == 2:
                    raise
                time.sleep(4)
        data = data if isinstance(data, list) else [data]
        per_day = {d: {"wind": [], "sun": []} for d in days}
        for loc in data:
            h = loc["hourly"]
            df = pd.DataFrame({"wind": h["wind_speed_100m"], "sun": h["shortwave_radiation"]}, index=pd.to_datetime(h["time"]))
            for d in days:
                part = df[df.index.strftime("%Y-%m-%d") == d]
                if len(part) >= 20:
                    if part["wind"].notna().sum() >= 20:
                        per_day[d]["wind"].append(part["wind"].mean())
                    if part["sun"].notna().sum() >= 20:
                        per_day[d]["sun"].append(part["sun"].sum() / 1000)   # W/m² hourly means -> kWh/m² per day
        return z, {d: {k: round(sum(v) / len(v), 2) for k, v in per_day[d].items() if v} for d in days}

    failed = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for fut in [pool.submit(one, z) for z in points]:
            try:
                z, res = fut.result(timeout=200)
                for d in days:
                    if res.get(d):
                        out[d][z] = res[d]
            except Exception as e:  # noqa: BLE001
                failed.append(type(e).__name__)
    print(f"Weather: {sum(len(v) for v in out.values())} zone-days for {days[0]} to {days[-1]}"
          + (f"; {len(failed)} zones failed" if failed else ""))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--entsoe", action="store_true", help="tomorrow's demand, wind and solar forecasts from ENTSO-E")
    ap.add_argument("--weather", action="store_true", help="wind and sun from Open-Meteo, today and the next two days")
    ap.add_argument("--drivers", default="site/data/spotrank_drivers.json")
    ap.add_argument("--out", default="site/data/clues.json")
    ap.add_argument("--workers", type=int, default=12)
    a = ap.parse_args()

    clues = {"meta": {}, "days": {}}
    if os.path.exists(a.out):
        with open(a.out, encoding="utf-8") as f:
            clues = json.load(f)
    now = pd.Timestamp.now(tz="UTC")
    if a.entsoe:
        key = b.resolve_key(None)
        if not key:
            print("::warning::No ENTSOE_API_KEY, so tomorrow's forecasts weren't fetched.")
        else:
            clues["meta"]["entsoe"] = {**entsoe_tomorrow(a.drivers, key, a.workers), "checked": now.isoformat(timespec="seconds")}
    if a.weather:
        with open(os.path.join(HERE, "weather_points.json"), encoding="utf-8") as f:
            points = json.load(f)["points"]
        today = pd.Timestamp.now(tz=b.TZ).normalize()
        days = [(today + pd.Timedelta(days=k)).strftime("%Y-%m-%d") for k in range(3)]
        got = weather(points, days)
        keep = (today - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
        clues["days"] = {d: v for d, v in clues.get("days", {}).items() if d >= keep}
        for d, v in got.items():
            if v:
                clues["days"][d] = v
        clues["meta"]["weather"] = {
            "checked": now.isoformat(timespec="seconds"),
            "source": "Open-Meteo.com, CC BY 4.0",
            "wind": "mean wind speed at 100 m, m/s, CET day",
            "sun": "total solar radiation (shortwave), kWh/m², CET day",
            "points": "up to 8 points inside each zone (scripts/weather_points.json)",
        }
    clues["meta"]["generated"] = now.isoformat(timespec="seconds")
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(clues, f, separators=(",", ":"))
    print("wrote", a.out)


if __name__ == "__main__":
    main()
