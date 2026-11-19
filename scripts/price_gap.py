"""Tells a clues run whether tomorrow's day-ahead prices are missing, so that it fetches them again.

After 13:00 Brussels time tomorrow's prices should be out for every zone. If the afternoon update
missed some (ENTSO-E down or late), the next clues run (20:45 Helsinki, or a morning one) tries again.
Writes missing=true|false to $GITHUB_OUTPUT.

    python scripts/price_gap.py                                   # now
    python scripts/price_gap.py --now 2026-11-19T20:45:00+02:00   # test a moment
"""
import argparse
import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

BX = ZoneInfo("Europe/Brussels")
PRICES_OUT = 13   # hour (Brussels) after which tomorrow's prices should be published


def gaps(data, now):
    zones = [z if isinstance(z, str) else z.get("code") for z in data["zones"]]
    by_day = {row[0]: row[1] for row in data["days"]}
    out = {}
    if now.hour >= PRICES_OUT:
        day = (now.date() + timedelta(days=1)).isoformat()
        vals = by_day.get(day)
        gone = zones if vals is None else [z for z, v in zip(zones, vals) if v is None]
        if gone:
            out[day] = gone
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="site/data/spotrank_data.json")
    ap.add_argument("--now", help="ISO time to test with (default: now)")
    a = ap.parse_args()
    now = (datetime.fromisoformat(a.now) if a.now else datetime.now(BX)).astimezone(BX)
    with open(a.file, encoding="utf-8") as f:
        missing = gaps(json.load(f), now)
    if missing:
        for day, zs in missing.items():
            print(f"Prices for {day} missing for {len(zs)} zones: {', '.join(zs)}. Fetching prices again.")
    else:
        print("Tomorrow's prices are complete (or not due yet). No price fetch." if now.hour >= PRICES_OUT
              else f"Before {PRICES_OUT}:00 Brussels time, tomorrow's prices aren't due yet. No price fetch.")
    gh = os.environ.get("GITHUB_OUTPUT")
    if gh:
        with open(gh, "a", encoding="utf-8") as f:
            f.write(f"missing={'true' if missing else 'false'}\n")


if __name__ == "__main__":
    main()
