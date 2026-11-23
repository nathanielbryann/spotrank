"""Add Great Britain's day-ahead prices to the data-only file (All zones map and ranking, Market data).

GB left the ENTSO-E Transparency Platform in June 2021, so its price comes from the Low Carbon
Contracts Company: the Intermittent Market Reference Price (IMRP), the GB day-ahead hourly price,
a weighted average of the EPEX SPOT and N2EX day-ahead auctions (UK Open Government Licence).
It's in GBP, so each day is converted at the ECB euro reference rate (the latest on or before the day).

A day is the simple mean of its hourly prices on the GB (UK local) delivery day, kept only when at
least 20 hours have a price. LCCC publishes a few days after delivery, so GB trails the other zones.

    python scripts/build_gb_prices.py                    # last 21 days (and all history if GB is new)
    python scripts/build_gb_prices.py --from 2021-01-01  # backfill
"""
import argparse
import csv
import io
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "site", "data", "spotrank_data_extra.json")
LCCC = "https://dp.lowcarboncontracts.uk"
DATASET = "imrp-actuals"
FALLBACK_RESOURCE = "d0e58993-d8b2-4ab5-90e0-518b1e7b2f1e"
ECB = "https://data-api.ecb.europa.eu/service/data/EXR/D.GBP.EUR.SP00.A"
MIN_HOURS = 20
PAGE = 5000


def get(url, timeout=90):
    last = None
    for k in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "spotrank (github.com/nathanielbryann/spotrank)"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(5 * 2 ** k)
    raise RuntimeError(f"{url}: {last}")


def resource_id():
    """The IMRP actuals table that the portal's API can query (it was re-created once; find it each run)."""
    try:
        pkg = json.loads(get(f"{LCCC}/api/3/action/package_show?id={DATASET}"))["result"]
        for r in pkg.get("resources", []):
            if r.get("datastore_active"):
                return r["id"]
    except Exception as e:  # noqa: BLE001
        print(f"LCCC dataset lookup failed ({e}); using the known table")
    return FALLBACK_RESOURCE


def imrp(since):
    """{day: [hourly £/MWh]} from `since` to the latest published day, newest first in pages."""
    rid, out, offset = resource_id(), {}, 0
    while True:
        q = urllib.parse.urlencode({"resource_id": rid, "limit": PAGE, "offset": offset,
                                    "sort": '"IMRP_Date" desc, "Settlement_Period" asc'})
        recs = json.loads(get(f"{LCCC}/api/3/action/datastore_search?{q}"))["result"]["records"]
        if not recs:
            break
        oldest = None
        for x in recs:
            d = str(x.get("IMRP_Date", ""))[:10]
            try:
                v = float(x["IMRP_Amount"])
                int(x["Settlement_Period"])
            except (KeyError, TypeError, ValueError):
                continue
            oldest = d if oldest is None or d < oldest else oldest
            if d >= since:
                out.setdefault(d, []).append(v)
        if oldest is None or oldest < since or len(recs) < PAGE:
            break
        offset += PAGE
    return out


def ecb_rates(since):
    """{day: GBP per EUR}, the ECB reference rate on its publication days."""
    start = (date.fromisoformat(since) - timedelta(days=10)).isoformat()
    raw = get(f"{ECB}?startPeriod={start}&format=csvdata").decode("utf-8")
    rows = list(csv.DictReader(io.StringIO(raw)))
    return {r["TIME_PERIOD"]: float(r["OBS_VALUE"]) for r in rows if r.get("OBS_VALUE")}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--from", dest="since", default=None, help="first day to fetch (default: 21 days back, or all history if GB is new)")
    ap.add_argument("--start", default="2021-01-01", help="first day kept in the file")
    a = ap.parse_args()

    with open(a.out, encoding="utf-8") as f:
        data = json.load(f)
    zones, days = data["zones"], data["days"]
    if "GB" not in zones:
        zones.append("GB")
        for row in days:
            row[1].append(None)
    gi = zones.index("GB")
    have = [d for d, v in days if v[gi] is not None]
    since = a.since or ((date.today() - timedelta(days=21)).isoformat() if len(have) > 300 else a.start)
    since = max(since, a.start)
    print(f"GB prices from LCCC (IMRP) since {since}")

    hourly = imrp(since)
    rates = ecb_rates(since)
    rate_days = sorted(rates)
    if not hourly or not rate_days:
        sys.exit("No GB prices or no exchange rates came back; the file is unchanged.")

    def rate_on(d):   # latest ECB rate on or before the day; before the first one, the first one
        lo, hi = 0, len(rate_days)
        while lo < hi:
            mid = (lo + hi) // 2
            if rate_days[mid] <= d:
                lo = mid + 1
            else:
                hi = mid
        return rates[rate_days[lo - 1] if lo else rate_days[0]]

    daily = {d: round(sum(v) / len(v) / rate_on(d), 2) for d, v in hourly.items() if len(v) >= MIN_HOURS}
    short = sorted(d for d, v in hourly.items() if len(v) < MIN_HOURS)
    index = {d: i for i, (d, _) in enumerate(days)}
    added = changed = 0
    for d, v in daily.items():
        if d not in index:   # past the file's last day (the other zones aren't there yet): skip for now
            continue
        row = days[index[d]][1]
        if row[gi] is None:
            added += 1
        elif abs(row[gi] - v) > 0.005:
            changed += 1
        row[gi] = v
    data.setdefault("meta", {})["gb"] = {
        "source": "LCCC, Intermittent Market Reference Price (GB day-ahead hourly price, EPEX SPOT and N2EX)",
        "fx": "ECB euro reference rate (GBP per EUR), latest on or before the day",
        "metric": "Simple mean of the hourly prices on the GB delivery day (UK local time), at least 20 hours",
        "last_day": max(daily) if daily else None,
    }
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))
    print(f"GB: {len(daily)} days fetched (last {max(daily)}), {added} added, {changed} revised"
          + (f"; fewer than {MIN_HOURS} hours, left out: {', '.join(short)}" if short else ""))


if __name__ == "__main__":
    main()
