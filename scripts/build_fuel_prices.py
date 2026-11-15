"""Fuel and CO2 prices for the Spotrank merit-order chart.

The prices live in data/fuel_prices.csv (date, ttf, eua, coal). New exports are
merged into it, and the site file site/data/fuel_prices.json is rebuilt from it.

    python scripts/build_fuel_prices.py --merge Fuel_and_Carbon_export.xlsx
    python scripts/build_fuel_prices.py            # rebuild the site file only

The export is the "Data Hub" layout: side-by-side blocks of Date | Value | Unit,
each under a "Market name" header (TTF spot, Coal ARA CIF Spot EUR/tonne,
CO2 EUA spot). Values from the new file replace existing ones on the same date;
the script lists every changed value so revisions are visible.

Running-cost assumptions for the chart are set in ASSUMPTIONS below and are
copied into the site file, so the page and this script always agree.
"""
import argparse
import csv
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CSV_PATH = os.path.join(ROOT, "data", "fuel_prices.csv")
OUT_PATH = os.path.join(ROOT, "site", "data", "fuel_prices.json")
SERIES = {   # key -> (words that identify the market name, expected unit)
    "ttf": (("ttf",), "EUR/MWh"),
    "eua": (("eua", "co2"), "EUR/tonne"),
    "coal": (("coal",), "EUR/tonne"),
}
LABELS = {"ttf": "TTF spot", "eua": "CO2 EUA spot", "coal": "Coal ARA CIF spot"}

# Running cost (short-run marginal cost) = fuel / efficiency + CO2 x emission factor / efficiency + variable O&M.
# Emission factors: IPCC 2006 defaults, t CO2 per MWh of fuel. Fixed costs in EUR/MWh of electricity.
ASSUMPTIONS = {
    "gas_ccgt": {"eff": 0.56, "ef": 0.202, "vom": 3},
    "gas_ocgt": {"eff": 0.37, "ef": 0.202, "vom": 5},
    "coal": {"eff": 0.40, "ef": 0.341, "vom": 4, "mwh_per_t": 6.978},
    "lignite": {"eff": 0.36, "ef": 0.364, "vom": 4, "fuel": 6},
    "oil": {"eff": 0.38, "ef": 0.279, "vom": 5, "fuel": 45},
    "fixed": {"solar": 0, "wind": 0, "hydro_ror": 0, "nuclear": 10, "biomass": 35, "other": 50},
}


def read_csv():
    data = {}
    if os.path.exists(CSV_PATH):
        with open(CSV_PATH, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                data[row["date"]] = {k: (float(row[k]) if row.get(k) not in (None, "") else None) for k in SERIES}
    return data


def write_csv(data):
    os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", *SERIES])
        for d in sorted(data):
            w.writerow([d, *["" if data[d][k] is None else f"{data[d][k]:.4f}".rstrip("0").rstrip(".") for k in SERIES]])


def read_export(path):
    """{key: {date: value}} from a Data Hub export (first sheet that has the blocks)."""
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    found, notes = {}, []
    for ws in wb.worksheets:
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            continue
        head = rows[0]
        for c, cell in enumerate(head):
            if str(cell).strip().lower() != "market name" or c + 1 >= len(head):
                continue
            name = str(head[c + 1] or "").lower()
            key = next((k for k, (words, _) in SERIES.items() if any(w in name for w in words)), None)
            if key is None or key in found:
                continue
            vals, units = {}, set()
            for r in rows[2:]:
                d, v = r[c] if c < len(r) else None, r[c + 1] if c + 1 < len(r) else None
                u = r[c + 2] if c + 2 < len(r) else None
                if d is None or v is None or not isinstance(v, (int, float)):
                    continue
                day = d.date() if isinstance(d, dt.datetime) else d if isinstance(d, dt.date) else None
                if day is None:
                    continue
                vals[day.isoformat()] = float(v)
                if u:
                    units.add(str(u).strip())
            bad = units - {SERIES[key][1]}
            if bad:
                notes.append(f"{LABELS[key]}: unexpected unit {sorted(bad)} (expected {SERIES[key][1]})")
            found[key] = vals
        if found:
            break
    missing = [LABELS[k] for k in SERIES if k not in found]
    if missing:
        notes.append("not found in the file: " + ", ".join(missing))
    return found, notes


def merge(data, found):
    added, changed = {k: 0 for k in SERIES}, []
    for k, vals in found.items():
        for d, v in vals.items():
            row = data.setdefault(d, {kk: None for kk in SERIES})
            old = row[k]
            if old is None:
                added[k] += 1
            elif abs(old - v) > 1e-6:
                changed.append((d, LABELS[k], old, v))
            row[k] = v
    return added, changed


def write_site(data):
    days = sorted(d for d in data if any(data[d][k] is not None for k in SERIES))
    if not days:
        sys.exit("No fuel prices to write.")
    first, last = dt.date.fromisoformat(days[0]), dt.date.fromisoformat(days[-1])
    full = [(first + dt.timedelta(days=i)).isoformat() for i in range((last - first).days + 1)]
    series = {k: [None if data.get(d, {}).get(k) is None else round(data[d][k], 3) for d in full] for k in SERIES}
    latest = {k: max((d for d in days if data[d][k] is not None), default=None) for k in SERIES}
    out = {
        "meta": {
            "series": {k: {"name": LABELS[k], "unit": SERIES[k][1].replace("EUR/tonne", "EUR/t")} for k in SERIES},
            "latest": latest,
            "assumptions": ASSUMPTIONS,
            "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        },
        "start": full[0],
        **series,
    }
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))
    return latest, len(full)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--merge", nargs="*", default=[], help="Data Hub export(s) (.xlsx) to merge in")
    args = ap.parse_args()
    data = read_csv()
    for path in args.merge:
        found, notes = read_export(path)
        added, changed = merge(data, found)
        print(f"{os.path.basename(path)}: " + ", ".join(f"{LABELS[k]} {len(found.get(k, {}))} days ({added[k]} new)" for k in SERIES))
        for n in notes:
            print("  note:", n)
        if changed:
            print(f"  {len(changed)} values changed from what was stored:")
            for d, name, old, new in changed[:40]:
                print(f"    {d} {name}: {old:g} -> {new:g}")
            if len(changed) > 40:
                print(f"    ... and {len(changed) - 40} more")
    if args.merge:
        write_csv(data)
    latest, n = write_site(data)
    print(f"wrote {OUT_PATH}: {n} days; latest " + ", ".join(f"{LABELS[k]} {latest[k]}" for k in SERIES))


if __name__ == "__main__":
    main()
