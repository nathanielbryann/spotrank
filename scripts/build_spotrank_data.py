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
    out = weighted / minutes
    out = out[minutes >= 20 * 60]  # drop days with less than 20 h of prices
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


def fetch(client, area, start, end, retries=3):
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
    ap.add_argument("--start", default="2024-01-01", help="first delivery day, YYYY-MM-DD")
    ap.add_argument("--end", default="yesterday", help="last delivery day: YYYY-MM-DD, yesterday (default), today or tomorrow")
    ap.add_argument("--update", action="store_true",
                    help="keep the days already in --out and only fetch from 7 days before its last day")
    ap.add_argument("--italy", choices=["NORD", "MEAN"], default="NORD")
    ap.add_argument("--zones", default=None, help="comma-separated subset, e.g. SE3,FI,DE-LU")
    ap.add_argument("--out", default=os.path.join(HERE, "spotrank_data.json"))
    ap.add_argument("--no-csv", action="store_true", help="don't write the CSV copy")
    ap.add_argument("--key", default=None, help="ENTSO-E API key (default: ENTSOE_API_KEY env var)")
    args = ap.parse_args()

    today = pd.Timestamp.now(tz=TZ).normalize().tz_localize(None)
    first_day = pd.Timestamp(args.start)
    end_day = parse_day(args.end, today)
    zones = [z.strip() for z in args.zones.split(",")] if args.zones else list(ZONES)
    unknown = [z for z in zones if z not in ZONES]
    if unknown:
        sys.exit(f"Unknown zone(s): {unknown}. Valid: {list(ZONES)}")

    existing = load_existing(args.out) if args.update else None
    fetch_from = first_day
    if existing is not None and len(existing):
        last = pd.Timestamp(existing.dropna(how="all").index.max())
        fetch_from = max(first_day, last - pd.Timedelta(days=7))  # re-fetch a week to pick up any revisions
        print(f"Updating {os.path.basename(args.out)}: it ends {last.date()}, fetching from {fetch_from.date()}")
    if fetch_from > end_day:
        print("Nothing to fetch.")
        return

    key = resolve_key(args.key)
    if not key:
        sys.exit("No API key. Paste it into api_key.txt, set ENTSOE_API_KEY, or pass --key.")
    from entsoe import EntsoePandasClient
    client = EntsoePandasClient(api_key=key)

    start = pd.Timestamp(fetch_from, tz=TZ)
    end = pd.Timestamp(end_day, tz=TZ) + pd.Timedelta(days=1)  # exclusive end
    print(f"Fetching {len(zones)} zones, {fetch_from.date()} to {end_day.date()} (CET delivery days)")
    try:
        cols = fetch_all(client, zones, start, end, args.italy)
    except KeyRejected as e:
        sys.exit(f"{e}\nCheck the key in api_key.txt (or create a new one on the ENTSO-E Transparency Platform) and run again.")

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


def fetch_all(client, zones, start, end, italy):
    cols = {}
    for z in zones:
        if z == "IT" and italy == "MEAN":
            parts = []
            for a in ITALY_ZONES:
                s = fetch(client, a, start, end)
                if s is not None:
                    parts.append(daily_baseload(s).rename(a))
            cols[z] = pd.concat(parts, axis=1).mean(axis=1) if parts else pd.Series(dtype=float)
        else:
            s = fetch(client, ZONES[z], start, end)
            cols[z] = daily_baseload(s) if s is not None else pd.Series(dtype=float)
        print(f"  {z:6s} {len(cols[z]):5d} days")
    return cols


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


if __name__ == "__main__":
    main()
