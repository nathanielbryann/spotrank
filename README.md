# Spotrank

A daily game: rank five European bidding zones by their day-ahead baseload price.

- `site/` is the website (published with GitHub Pages).
- `site/data/spotrank_data.json` holds daily baseload prices for 32 SDAC zones from the
  [ENTSO-E Transparency Platform](https://transparency.entsoe.eu/).
- `.github/workflows/update-and-deploy.yml` runs every day at 14:37 UTC. It fetches the newest
  prices, commits them, and republishes the site.
- `scripts/build_spotrank_data.py` is the price builder. It also runs locally:
  `python scripts/build_spotrank_data.py --update --out site/data/spotrank_data.json`

Daily baseload is the time-weighted average of all market time units in the CET delivery day,
which handles both hourly and 15-minute prices. Italy uses its North zone (IT-NORD).

## How the game picks puzzles and explains prices

- **Daily puzzle:** yesterday's prices. The five zones are chosen to tell the day's story: a zone far
  from its usual rank (median of the previous 90 days), a pair of neighbouring zones split by
  congestion, a long-time high or low, or a negative price. The rest are filled at random, with rules
  for minimum price gaps and regional variety.
- **Clues:** each miss unlocks a hint from the same analysis as the explanation panel, ordered from
  subtle to strong (hydro, demand, wind and solar, bottleneck, unusual rank, record, negative price).
  No prices are revealed unless a calm day has fewer than three story hints; then the price range or
  one zone's price fills the gap.
- **Why these prices:** shown after each round. It uses the price archive (ranks, gaps between
  neighbouring zones, records) and `site/data/spotrank_drivers.json`, which holds daily averages of
  ENTSO-E's day-ahead forecasts of total load, wind and solar per zone. "Normal" means the average of
  the previous 30 days; demand is compared with the same day type (weekday or weekend). Each
  explanation that was used as a clue is marked with its clue number.
- **Map:** after the round, every zone is coloured by its price, with the five puzzle zones outlined.
  A Map | Ranking switch shows the sorted bar chart instead. Zone borders come from entsoe-py (MIT)
  and surrounding countries from Natural Earth (public domain); `scripts/build_map.py` rebuilds
  `site/data/zones-map.json`.
- **Month tab:** a third view next to Map and Ranking. Every zone is coloured and labelled with its
  average so far in the delivery day's month (from the 1st up to the delivery day); hovering shows the
  two previous calendar months.
- **Prices page** (`#prices`): daily baseload history for up to six zones, a period picker (presets,
  dates, or drag on the overview strip) and the period average per zone, for the chart's zones and
  for all 32 zones in a table. The average is the mean of the daily baseload prices in the period.
- **Merit order:** below the map, for any zone you click (default: the dearest of the five), and on
  the Prices page for any zone and any past day (date picker with ‹ › day steps). Blocks
  are what each technology produced that day (ENTSO-E actual generation per production type, daily
  average), ordered by estimated running cost, with demand (day-ahead load forecast) and the day's
  price. Running cost = fuel ÷ efficiency + CO2 × emission factor ÷ efficiency + operating cost, using
  daily TTF, EUA and coal prices from `data/fuel_prices.csv`. Reservoir hydro is placed at the day's
  price (water value). Nuclear, biomass and "other" use fixed costs. The assumptions are in
  `scripts/build_fuel_prices.py`. Generation lives in `site/data/gen/<zone>.json`, one file per zone.
- **Skill profile** (in Stats): first-attempt accuracy and average places off per region, built from
  the daily rounds saved in the player's browser.

The drivers history is backfilled a few months per run, newest first. A run can be given more time
with **Run workflow**, using the "Minutes to spend on wind/solar/load data" field. Generation data works the same way
(10 minutes per daily run); the **Backfill generation data** workflow runs longer catch-ups and starts
whenever `.github/backfill-request.txt` changes.

### Fuel and CO2 prices

There is no API for these, so new prices are merged from the Data Hub export (the same layout every
time: blocks of Date | Value | Unit for TTF spot, Coal ARA CIF Spot EUR/tonne and CO2 EUA spot):

    python scripts/build_fuel_prices.py --merge Fuel_and_Carbon_export.xlsx

This updates `data/fuel_prices.csv`, lists any stored values that changed, and rebuilds
`site/data/fuel_prices.json`. Commit both files. Between updates the chart uses the latest
prices before the day and says which date they are from.

## One-time setup

1. **Create the repository.** On GitHub, click **New repository**, name it `spotrank`, set it to
   **Public** (GitHub Pages on a free account needs a public repository), and create it
   without a README.
2. **Upload the files.** On the new repository page, click **uploading an existing file**.
   In Finder, open the unzipped `spotrank` folder and press **Cmd+Shift+.** so the hidden
   `.github` folder shows. Select everything inside the folder (including `.github` and
   `.gitignore`), drag it onto the upload page, and click **Commit changes**.
   Check that the repository now shows a `.github` folder. If it doesn't, see "If .github is
   missing" below.
3. **Add your ENTSO-E key as a secret.** Go to **Settings, then Secrets and variables, then Actions**,
   click **New repository secret**, name it `ENTSOE_API_KEY`, paste your key, and save.
   The key is encrypted and is never shown in the code, the logs, or the website.
4. **Turn on Pages.** Go to **Settings, then Pages**. Under **Build and deployment**, set
   **Source** to **GitHub Actions**.
5. **Run it once.** Go to the **Actions** tab, pick **Update prices and deploy**, and click
   **Run workflow**. A green tick appears after about 2 minutes. The site address is shown
   in the run summary and under Settings, then Pages. It looks like
   `https://<your-username>.github.io/spotrank/`.

The run started by your first upload may fail with a red cross, because the secret and Pages
weren't set up yet. That's expected; step 5 is the one that counts.

### If .github is missing

On the repository page, click **Add file, then Create new file**. Type the name
`.github/workflows/update-and-deploy.yml` (the slashes create the folders), paste the contents of
that file from the zip, and click **Commit changes**.

## Day to day

Nothing to do. The workflow runs every day and keeps the repository active, so GitHub
doesn't pause the schedule. To update right away, use **Run workflow** in the Actions tab.
To change the page, edit `site/index.html` on GitHub; every commit redeploys the site.
