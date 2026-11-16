"""Build the Spotrank data file from ENTSO-E day-ahead prices.

Fetches SDAC day-ahead prices for every Spotrank zone from the ENTSO-E
Transparency Platform, computes the daily baseload average for each CET
delivery day, and writes:

  spotrank_data.json  - load this into the game ("Load data file...")
  spotrank_data.csv   - the same numbers as a wide table, for checking

Daily baseload = time-weighted mean of all market time units in the delivery
day, so it is correct for hourly data, 15-minute data, and days that mix both.

Easiest: paste your key into api_key.txt (same folder) and double-click
"Run Spotrank data (Mac).command" or "Run Spotrank data (Windows).bat".

Command line:
    python build_spotrank_data.py --start 2024-01-01 --end 2025-12-31

API key lookup order: --key, the ENTSOE_API_KEY environment variable,
api_key.txt next to this script, then a prompt in the terminal.

Options:
    --italy NORD   use the North zone (IT-NORD) as "Italy" (default)
    --italy MEAN   use the unweighted mean of the Italian bidding zones
    --zones SE3,FI,DE-LU   fetch only some zones (quick test)
"""
import argparse
import json
import os
import sys
import time

import pandas as pd

TZ = "Europe/Brussels"  # SDAC delivery days run on CET/CEST for every zone

# Spotrank zone code -> entsoe-py area code
ZONES = {
    "NO1": "NO_1", "NO2": "NO_2", "NO3": "NO_3", "NO4": "NO_4", "NO5": "NO_5",
    "SE1": "SE_1", "SE2": "SE_2", "SE3": "SE_3", "SE4": "SE_4", "FI": "FI",
    "DK1": "DK_1", "DK2": "DK_2", "EE": "EE", "LV": "LV", "LT": "LT", "PL": "PL",
    "DE-LU": "DE_LU", "NL": "NL", "BE": "BE", "FR": "FR", "AT": "AT", "CZ": "CZ",
    "SK": "SK", "HU": "HU", "SI": "SI", "HR": "HR", "RO": "RO", "BG": "BG", "GR": "GR",
    "IT": "IT_NORD", "ES": "ES", "PT": "PT",
    "CH": "CH",   # Switzerland: its own day-ahead auction, outside SDAC
}
ITALY_ZONES = ["IT_NORD", "IT_CNOR", "IT_CSUD", "IT_SUD", "IT_CALA", "IT_SICI", "IT_SARD"]


def daily_baseload(s: pd.Series) -> pd.Series:
    """Time-weighted daily mean on the CET delivery day.

    Each price applies from its timestamp until the next one, capped at
    60 minutes so a data gap is not filled with a stale price.
    """
    s = pd.to_numeric(s, errors="coerce").dropna()
    if s.empty:
        return s
    s = s[~s.index.duplicated(keep="last")].sort_index()
    s.index = s.index.tz_convert(TZ)
    nxt = s.index[1:].append(pd.DatetimeIndex([s.index[-1] + pd.Timedelta(minutes=60)]))
    dur = pd.Series((nxt - s.index).total_seconds() / 60.0, index=s.index).clip(upper=60)
    if len(s) > 1:  # last point: same length as the one before it
        dur.iloc[-1] = min(60.0, dur.iloc[-2])
    day = s.index.normalize()
    weighted = (s * dur).groupby(day).sum()
    minutes = dur.groupby(day).sum()
    nonzero = (s != 0).groupby(day).sum()
    out = weighted / minutes
    # drop days with less than 20 h of prices, and days where every price is exactly 0.00: that is a
    # placeholder before the real prices are in (EE, 17 Nov 2026), so the next run fetches the day again
    out = out[(minutes >= 20 * 60) & (nonzero > 0)]
    out.index = out.index.strftime("%Y-%m-%d")
    return out


HERE = os.path.dirname(os.path.abspath(__file__))
KEY_FILE = os.path.join(HERE, "api_key.txt")
KEY_PLACEHOLDER = "PASTE-YOUR-KEY-HERE"


def key_from_file(path=KEY_FILE):
    try:
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and line != KEY_PLACEHOLDER:
                    return line
    except FileNotFoundError:
        pass
    return None


def save_key(key, path=KEY_FILE):
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Your ENTSO-E API key is on the next line. Keep this file private.\n" + key + "\n")


def resolve_key(cli_key):
    key = cli_key or os.environ.get("ENTSOE_API_KEY") or key_from_file()
    if key:
        return key.strip()
    if not sys.stdin.isatty():
        return None
    print("No API key found in api_key.txt.")
    key = input("Paste your ENTSO-E API key and press Enter: ").strip()
    if key and input("Save it to api_key.txt for next time? [y/n]: ").strip().lower().startswith("y"):
        save_key(key)
        print("Saved to api_key.txt")
    return key or None


class KeyRejected(Exception):
    pass


def fetch(client, area, start, end, retries=2):
    for k in range(retries):
        try:
            return client.query_day_ahead_prices(area, start=start, end=end)
        except Exception as e:  # noqa: BLE001 - report and retry
            name = type(e).__name__
            if name == "NoMatchingDataError":
                return None
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status in (401, 403):
                raise KeyRejected(f"ENTSO-E rejected the API key (HTTP {status}).") from e
            if k == retries - 1:
                print(f"    {area}: failed after {retries} tries ({name}: {e})")
                return None
            time.sleep(3 * (k + 1))


def parse_day(s, today):
    """YYYY-MM-DD, or yesterday / today / tomorrow (CET)."""
    words = {"yesterday": -1, "today": 0, "tomorrow": 1}
    if s is None:
        s = "yesterday"
    if s.lower() in words:
        return today + pd.Timedelta(days=words[s.lower()])
    return pd.Timestamp(s)


def load_existing(path):
    """Existing output as a DataFrame (index = date strings), or None."""
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        old = json.load(f)
    return pd.DataFrame([r[1] for r in old["days"]], index=[r[0] for r in old["days"]],
                        columns=old["zones"], dtype=float)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", default="2021-01-01",
                    help="first delivery day kept in the files, YYYY-MM-DD (older days are dropped)")
    ap.add_argument("--fetch-from", default=None,
                    help="with --update: fetch prices from this day instead of a week before the last one (backfill)")
    ap.add_argument("--fetch-to", default=None, help="with --fetch-from: last day to fetch")
    ap.add_argument("--end", default="yesterday", help="last delivery day: YYYY-MM-DD, yesterday (default), today or tomorrow")
    ap.add_argument("--update", action="store_true",
                    help="keep the days already in --out and only fetch from 7 days before its last day")
    ap.add_argument("--italy", choices=["NORD", "MEAN"], default="NORD")
    ap.add_argument("--zones", default=None, help="comma-separated subset, e.g. SE3,FI,DE-LU")
    ap.add_argument("--out", default=os.path.join(HERE, "spotrank_data.json"))
    ap.add_argument("--no-csv", action="store_true", help="don't write the CSV copy")
    ap.add_argument("--time-budget", type=int, default=None, help="stop waiting for price zones after this many seconds")
    ap.add_argument("--workers", type=int, default=8, help="requests run in parallel (default 8)")
    ap.add_argument("--skip-prices", action="store_true", help="don't fetch prices (drivers only)")
    ap.add_argument("--drivers-out", default=None,
                    help="also fetch day-ahead load, wind and solar forecasts into this JSON file")
    ap.add_argument("--drivers-budget", type=int, default=900,
                    help="seconds to spend on drivers, including backfilling older months (default 900)")
    ap.add_argument("--gen-out", default=None,
                    help="also fetch actual generation by technology and installed capacity into this folder (one JSON per zone)")
    ap.add_argument("--gen-budget", type=int, default=600,
                    help="seconds to spend on generation, including backfilling older months (default 600)")
    ap.add_argument("--key", default=None, help="ENTSO-E API key (default: ENTSOE_API_KEY env var)")
    args = ap.parse_args()

    today = pd.Timestamp.now(tz=TZ).normalize().tz_localize(None)
    first_day = pd.Timestamp(args.start)
    end_day = parse_day(args.end, today)
    if args.zones == "extra":   # the data-only zones
        zones = list(EXTRA_ZONES)
    else:
        zones = [z.strip() for z in args.zones.split(",")] if args.zones else list(ZONES)
    unknown = [z for z in zones if z not in ZONES and z not in EXTRA_ZONES]
    if unknown:
        sys.exit(f"Unknown zone(s): {unknown}. Valid: {list(ZONES) + list(EXTRA_ZONES)}")

    key = resolve_key(args.key)
    if not key:
        sys.exit("No API key. Paste it into api_key.txt, set ENTSOE_API_KEY, or pass --key.")
    from entsoe import EntsoePandasClient

    def make_client():
        # 90 s per request so a stalled connection fails instead of hanging the run
        return EntsoePandasClient(api_key=key, timeout=90, retry_count=2, retry_delay=5)

    unfinished = False
    try:
        if not args.skip_prices:
            unfinished |= run_prices(args, make_client, zones, first_day, end_day)
        if args.drivers_out:
            unfinished |= run_drivers(args, make_client, zones, first_day, end_day, today)
        if args.gen_out:
            unfinished |= run_generation(args, make_client, zones, first_day, end_day, today)
    except KeyRejected as e:
        sys.exit(f"{e}\nCheck the key in api_key.txt (or create a new one on the ENTSO-E Transparency Platform) and run again.")
    if unfinished:  # don't wait for requests still running past a time budget
        sys.stdout.flush()
        os._exit(0)


def run_prices(args, make_client, zones, first_day, end_day):
    zones = [z for z in zones if z not in EXTRA_NO_PRICE]   # Italy as a whole has no day-ahead price
    existing = load_existing(args.out) if args.update else None
    fetch_from = first_day
    if existing is not None and len(existing):
        last = pd.Timestamp(existing.dropna(how="all").index.max())
        fetch_from = max(first_day, last - pd.Timedelta(days=7))  # re-fetch a week to pick up any revisions
        print(f"Updating {os.path.basename(args.out)}: it ends {last.date()}, fetching from {fetch_from.date()}")
    if args.fetch_from:   # backfill: days already in the file are kept unless the new fetch has them
        fetch_from = max(first_day, pd.Timestamp(args.fetch_from))
        if args.fetch_to:
            end_day = min(end_day, pd.Timestamp(args.fetch_to))
        print(f"Backfilling {fetch_from.date()} to {end_day.date()}")
    if fetch_from > end_day:
        print("Nothing to fetch.")
        return False
    start = pd.Timestamp(fetch_from, tz=TZ)
    end = pd.Timestamp(end_day, tz=TZ) + pd.Timedelta(days=1)  # exclusive end
    print(f"Fetching prices for {len(zones)} zones, {fetch_from.date()} to {end_day.date()} (CET delivery days)")
    cols, unfinished = fetch_all(make_client, zones, start, end, args.italy, budget_s=args.time_budget, workers=args.workers)

    days = pd.date_range(fetch_from, end_day, freq="D").strftime("%Y-%m-%d")
    new = pd.DataFrame({z: cols[z] for z in zones}).reindex(days)
    if existing is not None:
        all_zones = list(existing.columns) + [z for z in zones if z not in existing.columns]
        df = new.reindex(columns=all_zones).combine_first(existing.reindex(columns=all_zones))
        df = df.reindex(columns=all_zones)
    else:
        df = new
    df = df.sort_index().round(2)
    df = df.loc[df.index >= first_day.strftime("%Y-%m-%d")]
    while len(df) and df.iloc[-1].isna().all():  # e.g. tomorrow, before the auction results are out
        df = df.iloc[:-1]
    write_outputs(args, df, cols)
    return unfinished


def fetch_zone(make_client, z, start, end, italy):
    client = make_client()
    if z == "IT" and italy == "MEAN":
        parts = []
        for a in ITALY_ZONES:
            s = fetch(client, a, start, end)
            if s is not None:
                parts.append(daily_baseload(s).rename(a))
        return pd.concat(parts, axis=1).mean(axis=1) if parts else pd.Series(dtype=float)
    s = fetch(client, ZONES.get(z) or EXTRA_ZONES[z], start, end)
    return daily_baseload(s) if s is not None else pd.Series(dtype=float)


def fetch_all(make_client, zones, start, end, italy, budget_s=None, workers=8):
    """Fetch zones in parallel. ENTSO-E can take 10-40 s per request, so one-by-one is slow."""
    from concurrent.futures import ThreadPoolExecutor, wait, FIRST_EXCEPTION
    t0 = time.time()
    pool = ThreadPoolExecutor(max_workers=workers)
    futs = {pool.submit(fetch_zone, make_client, z, start, end, italy): z for z in zones}
    done, pending = wait(futs, timeout=budget_s, return_when=FIRST_EXCEPTION)
    for f in done:
        if isinstance(f.exception(), KeyRejected):
            pool.shutdown(wait=False, cancel_futures=True)
            raise f.exception()
    if pending:  # wait for the rest only if no exception stopped us early
        more, pending = wait(pending, timeout=None if budget_s is None else max(0, budget_s - (time.time() - t0)))
        done |= more
    cols = {}
    for f, z in futs.items():
        if f in done and f.exception() is None:
            cols[z] = f.result()
        else:
            if f in done:
                print(f"  {z:6s} failed: {f.exception()}")
            cols[z] = pd.Series(dtype=float)
    for z in zones:
        print(f"  {z:6s} {len(cols[z]):5d} days" + ("" if not any(futs[f] == z for f in pending) else "  (skipped: time budget used up, existing data kept)"))
    print(f"Fetched in {time.time() - t0:.0f} s")
    pool.shutdown(wait=False, cancel_futures=True)
    return cols, bool(pending)


def write_outputs(args, df, cols):
    df.index.name = "date"
    out_dir = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(out_dir, exist_ok=True)
    if not args.no_csv:
        df.to_csv(os.path.splitext(args.out)[0] + ".csv")
    out = {
        "meta": {
            "placeholder": False,
            "source": "ENTSO-E Transparency Platform, day-ahead prices (SDAC)",
            "source_short": "ENTSO-E",
            "metric": "Daily baseload average, time-weighted, CET delivery day",
            "unit": "EUR/MWh",
            "timezone": TZ,
            "italy": "IT_NORD" if args.italy == "NORD" else "MEAN",
            "italy_label": "North zone (IT-NORD)" if args.italy == "NORD" else "Mean of zones",
            "last_day": df.index[-1] if len(df) else None,
            "generated": pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds"),
        },
        "zones": list(df.columns),
        "days": [[d, [None if pd.isna(v) else float(v) for v in row]] for d, row in zip(df.index, df.to_numpy())],
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))
    print(f"\nDone: {len(df)} days x {len(df.columns)} zones, {int(df.notna().sum().sum())} prices, last day {out['meta']['last_day']}")
    print(f"  {os.path.abspath(args.out)}")
    empty = [z for z, s in cols.items() if s.empty]
    if empty:
        print(f"No new data returned for: {', '.join(empty)}")


# ---------------------------------------------------------------------------
# Drivers: day-ahead forecasts of load, wind and solar per bidding zone.
# These are what the day-ahead auction "sees", so they explain prices better
# than actuals. ENTSO-E serves them one month per request, so the history is
# backfilled a few months per run, newest first, until complete.
# ---------------------------------------------------------------------------
METRICS = ["load", "wind", "solar"]
DRIVER_AREA = dict(ZONES)  # Italy: North zone for drivers in both --italy modes
# Data-only zones for the Market data page (never in the game): the other Italian bidding zones,
# and Italy as a whole (forecasts, generation and capacity; it has no single day-ahead price).
EXTRA_ZONES = {"IT-CNOR": "IT_CNOR", "IT-CSUD": "IT_CSUD", "IT-SUD": "IT_SUD", "IT-CALA": "IT_CALA",
               "IT-SICI": "IT_SICI", "IT-SARD": "IT_SARD", "IT-ALL": "IT"}
EXTRA_NO_PRICE = {"IT-ALL"}
NO_CAPACITY = {"IT", "IT-CNOR", "IT-CSUD", "IT-SUD", "IT-CALA", "IT-SICI", "IT-SARD"}   # ENTSO-E reports capacity for Italy only as a whole
DRIVER_AREA.update(EXTRA_ZONES)
RECENT_DAYS = 35           # refetched every run (from the start of that month): the 30-day "normal" behind yesterday


def daily_frame(z, s_load, df_ws):
    out = {}
    if s_load is not None and len(s_load):
        col = "Forecasted Load" if "Forecasted Load" in getattr(s_load, "columns", []) else None
        series = s_load[col] if col else (s_load.iloc[:, 0] if hasattr(s_load, "columns") else s_load)
        out["load"] = daily_baseload(series)
    if df_ws is not None and len(df_ws):
        if isinstance(df_ws.columns, pd.MultiIndex):
            df_ws = df_ws.copy(); df_ws.columns = [" ".join(map(str, c)) for c in df_ws.columns]
        wind_cols = [c for c in df_ws.columns if "Wind" in str(c)]
        solar_cols = [c for c in df_ws.columns if "Solar" in str(c)]
        if wind_cols:
            out["wind"] = daily_baseload(df_ws[wind_cols].sum(axis=1, min_count=1))
        if solar_cols:
            out["solar"] = daily_baseload(df_ws[solar_cols].sum(axis=1, min_count=1))
    return out


def fetch_driver_range(make_client, z, start, end):
    client = make_client()
    area = DRIVER_AREA[z]
    res = {}
    for name, fn in (("load", client.query_load_forecast), ("ws", client.query_wind_and_solar_forecast)):
        for k in range(2):
            try:
                res[name] = fn(area, start=start, end=end)
                break
            except Exception as e:  # noqa: BLE001
                if type(e).__name__ == "NoMatchingDataError":
                    res[name] = None
                    break
                status = getattr(getattr(e, "response", None), "status_code", None)
                if status in (401, 403):
                    raise KeyRejected(f"ENTSO-E rejected the API key (HTTP {status}).") from e
                if k == 1:
                    raise
                time.sleep(5)
    return daily_frame(z, res.get("load"), res.get("ws"))


def load_drivers(path):
    if not path or not os.path.exists(path):
        return {m: pd.DataFrame(dtype=float) for m in METRICS}, {}
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    frames = {m: pd.DataFrame({z: d["series"][z][m] for z in d["series"]}, index=d["dates"], dtype=float) for m in METRICS}
    return frames, d.get("meta", {}).get("fetched_months", {})


def run_drivers(args, make_client, zones, first_day, end_day, today):
    from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
    t0 = time.time()
    frames, fetched = load_drivers(args.drivers_out)
    cutoff = (today - pd.Timedelta(days=RECENT_DAYS)).to_period("M")
    recent_from = max(first_day, cutoff.start_time)                   # whole months, so nothing falls between
    tasks = [(z, recent_from, end_day, None) for z in zones]          # recent window, every run
    months = pd.period_range(first_day.to_period("M"), cutoff, freq="M")
    for m in reversed(months):                                        # backfill, newest month first
        if m >= cutoff:
            continue
        for z in zones:
            if str(m) not in fetched.get(z, []):
                tasks.append((z, max(first_day, m.start_time), m.end_time.normalize(), str(m)))
    n_backfill = sum(1 for t in tasks if t[3])
    print(f"\nDrivers: {len(zones)} zones recent window from {recent_from.date()}, {n_backfill} zone-months left to backfill")

    results, done_months, failed = [], {}, 0
    pool = ThreadPoolExecutor(max_workers=args.workers)
    it = iter(tasks)
    running = {}

    def submit_next():
        for z, a, b, label in it:
            fut = pool.submit(fetch_driver_range, make_client, z, pd.Timestamp(a, tz=TZ),
                              pd.Timestamp(b, tz=TZ) + pd.Timedelta(days=1), )
            running[fut] = (z, label)
            return True
        return False

    for _ in range(args.workers):
        submit_next()
    timed_out = False
    while running:
        left = args.drivers_budget - (time.time() - t0)
        if left <= 0:
            timed_out = True
            break
        done, _ = wait(list(running), timeout=left, return_when=FIRST_COMPLETED)
        for f in done:
            z, label = running.pop(f)
            exc = f.exception()
            if isinstance(exc, KeyRejected):
                pool.shutdown(wait=False, cancel_futures=True)
                raise exc
            if exc is not None:
                failed += 1
            else:
                results.append((z, f.result()))
                if label:
                    done_months.setdefault(z, []).append(label)
            if time.time() - t0 < args.drivers_budget:
                submit_next()
    pool.shutdown(wait=False, cancel_futures=True)

    for z, daily in results:
        for m in METRICS:
            s = daily.get(m)
            if s is None or s.empty:
                continue
            fr = frames[m]
            if z not in fr.columns:
                fr[z] = float("nan")
            new_idx = fr.index.union(s.index)
            fr = fr.reindex(new_idx)
            fr.loc[s.index, z] = s.values
            frames[m] = fr
    for z, ms in done_months.items():
        fetched[z] = sorted(set(fetched.get(z, [])) | set(ms))
    remaining = n_backfill - sum(len(v) for v in done_months.values())
    write_drivers(args.drivers_out, frames, fetched, zones, first_day)
    print(f"Drivers: {len(results)} requests done, {failed} failed, {remaining} zone-months still to backfill "
          f"({time.time() - t0:.0f} s{', time budget used up' if timed_out else ''})")
    return timed_out


def write_drivers(path, frames, fetched, zones, first_day):
    idx = sorted(set().union(*[set(f.index) for f in frames.values()]))
    idx = [d for d in idx if d >= first_day.strftime("%Y-%m-%d")]
    all_zones = [z for z in zones] + [z for f in frames.values() for z in f.columns if z not in zones]
    all_zones = list(dict.fromkeys(all_zones))
    series = {}
    for z in all_zones:
        series[z] = {}
        for m in METRICS:
            col = frames[m][z] if z in frames[m].columns else pd.Series(dtype=float)
            vals = col.reindex(idx)
            series[z][m] = [None if pd.isna(v) else int(round(v)) for v in vals]
    out = {
        "meta": {
            "source": "ENTSO-E Transparency Platform: day-ahead total load forecast and day-ahead wind & solar generation forecast",
            "unit": "MW, daily average of the day-ahead forecast (CET delivery day)",
            "italy": "IT-NORD",
            "metrics": METRICS,
            "generated": pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds"),
            "fetched_months": fetched,
        },
        "dates": idx,
        "zones": all_zones,
        "series": series,
    }
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))
    print(f"  wrote {path}: {len(idx)} days")



# ---------------------------------------------------------------------------
# Generation: actual generation per production type (daily average MW) and
# installed capacity per year, for the merit-order chart. Like the drivers,
# ENTSO-E serves generation one month per request, so the history is backfilled
# newest month first over several runs. One file per zone keeps the page light:
# it loads only the zone you click.
# ---------------------------------------------------------------------------
GEN_GROUPS = {   # Spotrank technology group -> ENTSO-E production types (entsoe-py names)
    "solar": ["Solar"],
    "wind": ["Wind Onshore", "Wind Offshore"],
    "hydro_ror": ["Hydro Run-of-river and pondage", "Marine"],
    "nuclear": ["Nuclear"],
    "biomass": ["Biomass", "Waste", "Geothermal", "Other renewable"],
    "hydro_storage": ["Hydro Water Reservoir", "Hydro Pumped Storage"],
    "lignite": ["Fossil Brown coal/Lignite", "Fossil Oil shale", "Fossil Peat"],
    "coal": ["Fossil Hard coal", "Fossil Coal-derived gas"],
    "gas": ["Fossil Gas"],
    "oil": ["Fossil Oil"],
    "other": ["Other", "Energy storage"],
}
TYPE_GROUP = {t: g for g, ts in GEN_GROUPS.items() for t in ts}
GEN_RECENT_DAYS = 10   # refetched every run, from the start of that month: late publications and revisions


def _query(fn, *a, **kw):
    """One ENTSO-E call with a retry; None when there is no data."""
    for k in range(2):
        try:
            return fn(*a, **kw)
        except Exception as e:  # noqa: BLE001
            if type(e).__name__ == "NoMatchingDataError":
                return None
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status in (401, 403):
                raise KeyRejected(f"ENTSO-E rejected the API key (HTTP {status}).") from e
            if k == 1:
                raise
            time.sleep(5)


def gen_columns(df):
    """{production type: series of actual generation, MW} from query_generation output."""
    if df is None or len(df) == 0:
        return {}
    if isinstance(df, pd.Series):
        return {str(df.name): df}
    out = {}
    if isinstance(df.columns, pd.MultiIndex):
        for col in df.columns:
            if str(col[-1]) == "Actual Aggregated":   # skip "Actual Consumption" (pumping, storage charging)
                out[str(col[0])] = df[col]
    else:
        for c in df.columns:
            out[str(c)] = df[c]
    return out


def gen_daily(df):
    """Daily average MW per technology group. Each production type is averaged on its own
    resolution first, so 15-minute and hourly types in one zone add up correctly."""
    per_group = {}
    for t, s in gen_columns(df).items():
        d = daily_baseload(s)
        if not d.empty:
            per_group.setdefault(TYPE_GROUP.get(t, "other"), []).append(d)
    return {g: pd.concat(ds, axis=1).sum(axis=1, min_count=1) for g, ds in per_group.items()}


def capacity_groups(df):
    """Installed capacity (MW) per technology group from query_installed_generation_capacity."""
    if df is None or len(df) == 0:
        return {}
    row = df.iloc[-1] if isinstance(df, pd.DataFrame) else df
    if isinstance(row, pd.Series) and not isinstance(df, pd.DataFrame):
        row = pd.Series({str(df.name): float(df.iloc[-1])})
    out, seen = {}, set()
    for col, v in row.items():
        t = str(col[0]) if isinstance(col, tuple) else str(col)
        if isinstance(col, tuple) and str(col[-1]) == "Actual Consumption":
            continue
        if t in seen or pd.isna(v):
            continue
        seen.add(t)
        g = TYPE_GROUP.get(t, "other")
        out[g] = out.get(g, 0.0) + float(v)
    return {g: int(round(v)) for g, v in out.items() if v > 0}


def fetch_gen_month(make_client, z, start, end):
    client = make_client()
    return gen_daily(_query(client.query_generation, DRIVER_AREA[z], start=start, end=end))


def fetch_capacity(make_client, z, year):
    client = make_client()
    df = _query(client.query_installed_generation_capacity, DRIVER_AREA[z],
                start=pd.Timestamp(f"{year}-01-01", tz=TZ), end=pd.Timestamp(f"{year}-12-31", tz=TZ))
    return capacity_groups(df)


def load_gen_zone(path):
    if not os.path.exists(path):
        return {}, [], {}
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    series = {}
    if d.get("start") and d.get("days"):
        idx = pd.date_range(d["start"], periods=d["days"], freq="D").strftime("%Y-%m-%d")
        series = {g: pd.Series(v, index=idx, dtype=float) for g, v in d.get("gen", {}).items()}
    return series, d.get("fetched_months", []), d.get("cap", {})


def write_gen_zone(path, z, series, fetched, caps, first_day):
    idx = sorted(set().union(*[set(s.dropna().index) for s in series.values()])) if series else []
    idx = [d for d in idx if d >= first_day.strftime("%Y-%m-%d")]
    if idx:
        full = pd.date_range(idx[0], idx[-1], freq="D").strftime("%Y-%m-%d")
    else:
        full = pd.Index([])
    gen = {}
    for g in GEN_GROUPS:
        if g in series and series[g].reindex(full).notna().any():
            gen[g] = [None if pd.isna(v) else int(round(v)) for v in series[g].reindex(full)]
    out = {
        "zone": z, "area": DRIVER_AREA[z],
        "source": "ENTSO-E Transparency Platform: actual generation per production type (16.1.B&C) and installed capacity per production type (14.1.A)",
        "unit": "MW; generation is the daily average over the CET delivery day",
        "groups": {g: GEN_GROUPS[g] for g in GEN_GROUPS},
        "start": full[0] if len(full) else None, "days": len(full),
        "gen": gen, "cap": dict(sorted(caps.items())),
        "fetched_months": sorted(set(fetched)),
        "generated": pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds"),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))


def run_generation(args, make_client, zones, first_day, end_day, today):
    from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
    t0 = time.time()
    os.makedirs(args.gen_out, exist_ok=True)
    state = {z: load_gen_zone(os.path.join(args.gen_out, z + ".json")) for z in zones}
    last = min(end_day, today - pd.Timedelta(days=1))   # whole days only: today is still being published
    cutoff = (today - pd.Timedelta(days=GEN_RECENT_DAYS)).to_period("M")
    recent_from = max(first_day, cutoff.start_time)
    tasks = []
    for z in zones:   # installed capacity: each year once (a missing year is retried next run)
        if z in NO_CAPACITY:
            continue
        for y in range(first_day.year, today.year + 1):
            if str(y) not in state[z][2]:
                tasks.append(("cap", z, y, None))
    tasks += [("gen", z, (recent_from, last), None) for z in zones]
    for m in reversed(pd.period_range(first_day.to_period("M"), cutoff, freq="M")):
        if m >= cutoff:
            continue
        for z in zones:
            if str(m) not in state[z][1]:
                tasks.append(("gen", z, (max(first_day, m.start_time), m.end_time.normalize()), str(m)))
    n_back = sum(1 for t in tasks if t[0] == "gen" and t[3])
    print(f"\nGeneration: {len(zones)} zones from {recent_from.date()}, {n_back} zone-months left to backfill, "
          f"{sum(1 for t in tasks if t[0] == 'cap')} capacity years")

    pool = ThreadPoolExecutor(max_workers=args.workers)
    it = iter(tasks)
    running, failed, done = {}, 0, 0
    timed_out = False

    def submit_next():
        for kind, z, arg, label in it:
            if kind == "cap":
                fut = pool.submit(fetch_capacity, make_client, z, arg)
            else:
                a, b = arg
                fut = pool.submit(fetch_gen_month, make_client, z, pd.Timestamp(a, tz=TZ), pd.Timestamp(b, tz=TZ) + pd.Timedelta(days=1))
            running[fut] = (kind, z, arg, label)
            return True
        return False

    for _ in range(args.workers):
        submit_next()
    last_save = time.time()
    while running:
        left = args.gen_budget - (time.time() - t0)
        if left <= 0:
            timed_out = True
            break
        fin, _ = wait(list(running), timeout=left, return_when=FIRST_COMPLETED)
        for f in fin:
            kind, z, arg, label = running.pop(f)
            exc = f.exception()
            if isinstance(exc, KeyRejected):
                pool.shutdown(wait=False, cancel_futures=True)
                raise exc
            if exc is not None:
                failed += 1
                print(f"    {z} {kind} {label or arg}: {type(exc).__name__}: {str(exc)[:120]}")
            else:
                done += 1
                series, fetched, caps = state[z]
                if kind == "cap":
                    if f.result():
                        caps[str(arg)] = f.result()
                else:
                    for g, s in f.result().items():
                        cur = series.get(g, pd.Series(dtype=float))
                        cur = cur.reindex(cur.index.union(s.index))
                        cur.loc[s.index] = s.values
                        series[g] = cur
                    if label:
                        fetched.append(label)
            if time.time() - t0 < args.gen_budget:
                submit_next()
        if time.time() - last_save > 300:   # save progress every 5 minutes, so a cancelled run keeps it
            for z in zones:
                write_gen_zone(os.path.join(args.gen_out, z + ".json"), z, *state[z], first_day)
            last_save = time.time()
    pool.shutdown(wait=False, cancel_futures=True)
    for z in zones:
        write_gen_zone(os.path.join(args.gen_out, z + ".json"), z, *state[z], first_day)
    left_months = sum(1 for z in zones for m in pd.period_range(first_day.to_period("M"), cutoff, freq="M")
                      if m < cutoff and str(m) not in state[z][1])
    print(f"Generation: {done} requests done, {failed} failed, {left_months} zone-months still to backfill "
          f"({time.time() - t0:.0f} s{', time budget used up' if timed_out else ''})")
    return timed_out


if __name__ == "__main__":
    main()
