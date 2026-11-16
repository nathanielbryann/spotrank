"""Send recent daily baseload prices to the Spotrank database, so forecasts can be scored.

The nightly GitHub job runs this after the price update. It needs the Supabase secret key in the
environment variable SUPABASE_SECRET_KEY (a GitHub secret). Without it, it says so and does nothing.
The key is only sent to Supabase and never printed.

  python scripts/push_day_prices.py                 # the last 10 delivery days
  python scripts/push_day_prices.py --days 60
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

SUPABASE_URL = "https://ywzvupcjyuvftnpchrtr.supabase.co"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default="site/data/spotrank_data.json")
    ap.add_argument("--days", type=int, default=10, help="how many of the latest delivery days to send")
    ap.add_argument("--dry-run", action="store_true", help="show what would be sent, send nothing")
    a = ap.parse_args()

    with open(a.data, encoding="utf-8") as f:
        data = json.load(f)
    zones = data["zones"]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = []
    for day, vals in data["days"][-a.days:]:
        prices = {z: v for z, v in zip(zones, vals) if v is not None}
        if prices:
            rows.append({"price_date": day, "prices": prices, "updated_at": now})
    if not rows:
        print("No prices to send.")
        return 0
    span = f"{rows[0]['price_date']} to {rows[-1]['price_date']} ({len(rows)} days)"
    if a.dry_run:
        print("Would send", span)
        print(json.dumps(rows[-1])[:400])
        return 0

    key = os.environ.get("SUPABASE_SECRET_KEY", "").strip()
    if not key:
        print("::notice::SUPABASE_SECRET_KEY isn't set, so forecast prices weren't sent to the database.")
        return 0
    if not key.startswith("sb_secret_"):
        print("::warning::SUPABASE_SECRET_KEY doesn't start with sb_secret_. It needs the secret key from "
              "Supabase (Project Settings > API Keys > Secret keys), not the publishable key.")
        return 1
    req = urllib.request.Request(
        SUPABASE_URL + "/rest/v1/day_prices?on_conflict=price_date",
        data=json.dumps(rows).encode("utf-8"),
        method="POST",
        headers={
            "apikey": key,
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            print(f"Sent prices for {span}: HTTP {r.status}")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        print(f"::warning::The database refused the prices (HTTP {e.code}): {body}")
        return 1
    except urllib.error.URLError as e:
        print(f"::warning::Couldn't reach the database: {e.reason}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
