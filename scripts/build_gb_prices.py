"""Add Great Britain's day-ahead prices to the data-only file (All zones map and ranking, Market data).

GB left the ENTSO-E Transparency Platform in June 2021. Its price comes from:
- NESO's open data portal: the N2EX GB day-ahead hourly auction price (Nord Pool), NESO Open Data
  Licence. Published the day before delivery, on a five-year rolling window (from 20 Sep 2021 when
  this was written). Used for every day it has.
- Before that, and for any day NESO is missing: the Low Carbon Contracts Company's Intermittent
  Market Reference Price (IMRP), the GB day-ahead hourly price as a weighted average of the EPEX SPOT
  and N2EX auctions (UK Open Government Licence). LCCC publishes a few days after delivery.
Prices are in GBP; each day is converted at the ECB euro reference rate (latest on or before the day).
A day is the simple mean of its hourly prices on the GB (UK local) delivery day, kept only when at
least 20 hours have a price.

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
NESO = "https://api.neso.energy/api/3/action"
NESO_DATASET = "day-ahead-power-exchange-prices-nordpool"
NESO_FALLBACK = "4f27eea5-7038-4f73-9740-e3e4ad47c26a"
LCCC = "https://dp.lowcarboncontracts.uk"
DATASET = "imrp-actuals"
FALLBACK_RESOURCE = "d0e58993-d8b2-4ab5-90e0-518b1e7b2f1e"
ECB = "https://data-api.ecb.europa.eu/service/data/EXR/D.GBP.EUR.SP00.A"
MIN_HOURS = 20
PAGE = 5000
NESO_FROM = "2021-09-21"   # first full day in NESO's data when this was written; later days stay NESO's


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


def neso_resource():
    try:
        pkg = json.loads(get(f"{NESO}/package_show?id={NESO_DATASET}"))["result"]
        for r in pkg.get("resources", []):
            if r.get("datastore_active"):
                return r["id"]
    except Exception as e:  # noqa: BLE001
        print(f"NESO dataset lookup failed ({e}); using the known table")
    return NESO_FALLBACK


def n2ex(since):
    """{day: [hourly £/MWh]} from NESO (N2EX day-ahead), newest first in pages, back to `since`."""
    rid, out, offset = neso_resource(), {}, 0
    while True:
        q = urllib.parse.urlencode({"resource_id": rid, "limit": PAGE, "offset": offset, "sort": '"Date" desc'})
        recs = json.loads(get(f"{NESO}/datastore_search?{q}"))["result"]["records"]
        if not recs:
            break
        oldest = None
        for x in recs:
            d = str(x.get("Date", ""))[:10]
            try:
                v = float(x["Price"])
            except (KeyError, TypeError, ValueError):
                continue
            oldest = d if oldest is None or d < oldest else oldest
            if d >= since:
                out.setdefault(d, []).append(v)
        if oldest is None or oldest < since or len(recs) < PAGE:
            break
        offset += PAGE
    return out


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
    print(f"GB prices since {since}")

    try:
        neso = {d: v for d, v in n2ex(since).items() if len(v) >= MIN_HOURS}
    except Exception as e:  # noqa: BLE001
        print(f"NESO failed ({e}); falling back to LCCC")
        neso = {}
    # LCCC for the days NESO doesn't have: before its first full day, or gaps in the window
    first = min(neso) if neso else None
    window = [(date.fromisoformat(since) + timedelta(days=k)).isoformat()
              for k in range((date.today() - date.fromisoformat(since)).days + 1)]
    lccc_needed = [d for d in window if d not in neso and (first is None or d < first or d <= (date.today() - timedelta(days=4)).isoformat())]
    lccc = {}
    if lccc_needed:
        try:
            lccc = {d: v for d, v in imrp(min(lccc_needed)).items() if d not in neso and len(v) >= MIN_HOURS}
        except Exception as e:  # noqa: BLE001
            print(f"LCCC failed ({e})")
    hourly = {**lccc, **neso}
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

    daily = {d: round(sum(v) / len(v) / rate_on(d), 2) for d, v in hourly.items()}
    short = []
    index = {d: i for i, (d, _) in enumerate(days)}
    added = changed = 0
    for d, v in daily.items():
        if d not in index:   # past the file's last day (the other zones aren't there yet): skip for now
            continue
        row = days[index[d]][1]
        if d in lccc and d >= NESO_FROM and row[gi] is not None:   # NESO down: fill gaps only, keep its prices
            continue
        if row[gi] is None:
            added += 1
        elif abs(row[gi] - v) > 0.005:
            changed += 1
        row[gi] = v
    data.setdefault("meta", {})["gb"] = {
        "source": "NESO open data: N2EX GB day-ahead hourly price (Nord Pool), from 21 Sep 2021; before that "
                  "LCCC's Intermittent Market Reference Price (EPEX SPOT and N2EX day-ahead)",
        "fx": "ECB euro reference rate (GBP per EUR), latest on or before the day",
        "metric": "Simple mean of the hourly prices on the GB delivery day (UK local time), at least 20 hours",
        "last_day": max(daily) if daily else None,
    }
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))
    print(f"GB: {len(daily)} days ({len(neso)} from NESO, {len(lccc)} from LCCC; last {max(daily)}), {added} added, {changed} revised"
          + (f"; fewer than {MIN_HOURS} hours, left out: {', '.join(short)}" if short else ""))


if __name__ == "__main__":
    main()
