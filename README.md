# Spotrank

A daily game: rank five European bidding zones by their day-ahead baseload price.

- `site/` is the website (published with GitHub Pages).
- `site/data/spotrank_data.json` holds daily baseload prices from January 2021 for 32 SDAC zones
  plus Switzerland (its own day-ahead auction, outside SDAC), from the
  [ENTSO-E Transparency Platform](https://transparency.entsoe.eu/).
- `.github/workflows/update-and-deploy.yml` fetches the newest prices, commits them, and republishes
  the site. `.github/workflows/daily-start.yml` starts it every day at 14:37 UTC. GitHub only runs a
  schedule for the person who last edited its cron line (edits through Claude's app connection don't
  count), so that small file is edited by hand on GitHub only. To start an update by hand: "Run
  workflow" on the Daily start page in the Actions tab, or push a change to `.github/update-now.txt`.
- `scripts/build_spotrank_data.py` is the price builder. It also runs locally:
  `python scripts/build_spotrank_data.py --update --out site/data/spotrank_data.json`

Daily baseload is the time-weighted average of all market time units in the CET delivery day,
which handles both hourly and 15-minute prices. Italy uses its North zone (IT-NORD).

After rebuilding the map (`scripts/build_map.py`), change `MAP_VERSION` in `site/index.html`:
browsers keep the map file cached, and a new version string makes them fetch the new one.

To extend the history (or after adding a zone), change `.github/history-request.txt`: the
**Backfill history** workflow fetches older prices, forecasts and generation in three parallel jobs
and redeploys the site.

## How the game picks puzzles and explains prices

- **Daily puzzle:** the day's own prices (from puzzle #5, 19 Nov 2026; known since the auction the day
  before; puzzles #1–4 used the previous day). Without them it falls back to an archive day. The
  briefing and merit order use the latest full day of actual generation (dated), since a day's own
  is only complete once it's over. The five zones are chosen to tell the day's story: a zone far
  from its usual rank (median of the previous 90 days), a pair of neighbouring zones split by
  congestion, a long-time high or low, or a negative price. The rest are filled at random, with rules
  for minimum price gaps and regional variety.
- **Market briefing:** shown before the first attempt, one card per zone with no prices: wind and
  solar against demand (vs the previous 30 days), demand vs a usual weekday or weekend day, the main
  power source and gas share (ENTSO-E actual generation), net imports or exports, hydro zones, and
  any congested border between neighbouring zones (named without saying which side was dearer).
- **Clues:** each miss unlocks a clue with numbers, weakest first: the price range of the five; then
  a record ("highest price since March 2025"), an unusual European rank, a negative price, or the two
  closest zones; then one zone's actual price.
- **Why these prices:** shown after each round. It uses the price archive (ranks, gaps between
  neighbouring zones, records) and `site/data/spotrank_drivers.json`, which holds daily averages of
  ENTSO-E's day-ahead forecasts of total load, wind and solar per zone. "Normal" means the average of
  the previous 30 days; demand is compared with the same day type (weekday or weekend). Each
  explanation that was used as a clue is marked with its clue number.
- **Map:** after the round, every zone is coloured by its price, with the five puzzle zones outlined.
  A Map | Ranking switch shows the sorted bar chart instead. Zone borders come from entsoe-py (MIT)
  and surrounding countries from Natural Earth (public domain); `scripts/build_map.py` rebuilds
  `site/data/zones-map.json`.
- **All zones** (after a round): choose what to show, **Day**, **Month** or **Year**, and how,
  **Map** or **Ranking**. Day opens on the puzzle's day (today) and has a dropdown back 10 days, never
  ahead (tomorrow's prices are tomorrow's puzzle); the merit order follows the day picked. Month and
  Year open on the current period so far (up to the puzzle's
  delivery day) and have a dropdown back to Jan 2021 / 2021 for full past months and years. The
  ranking always follows the choice. Hovering a zone shows the two previous months or years. An
  asterisk marks an average that rests on fewer than 90% of the period's days. The day, month or year
  picked resets on a new puzzle day; Day/Month/Year and Map/Ranking are remembered. The
  table view lists what's on screen.
- **Map-only zones** (All zones map and ranking, and Market data; never in the puzzle or the
  Forecast challenge): Italy's other zones (IT-CNOR, IT-CSUD, IT-SUD, IT-CALA, IT-SICI, IT-SARD),
  Ireland (IE: the SEM, Ireland and Northern Ireland, from ENTSO-E), the Western Balkans with a
  day-ahead market, all from ENTSO-E (Serbia RS: SEEPEX; Montenegro ME: BELEN, from Apr 2023; North
  Macedonia MK: MEMO, from May 2023; Albania AL and Kosovo XK: ALPEX, coupled, from Apr 2023 and Feb
  2024; Bosnia has no day-ahead market and stays grey) and Great Britain (GB). GB left
  the ENTSO-E platform in June 2021, so `scripts/build_gb_prices.py` takes the N2EX GB day-ahead
  hourly price from NESO's open data portal (dataset `day-ahead-power-exchange-prices-nordpool`, NESO
  Open Data Licence, published the day before delivery, five-year rolling window). Days before NESO's
  data starts (21 Sep 2021), and any day NESO is missing, use LCCC's Intermittent Market Reference
  Price (EPEX SPOT and N2EX day-ahead, UK Open Government Licence; published a few days late). Each GB
  delivery day (UK local time, at least 20 hours) is averaged and converted at the ECB euro reference
  rate (latest on or before the day). The ranking tags these zones "not in the puzzle"; clicking IE or
  an Italian zone on the map shows its merit order (GB has no generation data). GB and Ireland's map
  shapes come from Natural Earth 1:50m (`build_map.py --ne50`).
- **Prices page** (`#prices`): daily baseload history for up to six zones, a period picker (presets,
  dates, or drag on the overview strip) and the period average per zone, for the chart's zones and
  for all zones in a table. The average is the mean of the daily baseload prices in the period.
- **Market data page** (`#prices`, header button "Market data"): one period bar (presets, dates,
  drag strip) for five tabs, each section with a **Download CSV** button (semicolons and decimal
  commas when the browser uses decimal commas, so Excel opens it in columns):
  - **Prices:** price history for up to 6 zones and the period average of all zones.
  - **Generation:** generation mix; installed capacity by year with capacity factors.
  - **Merit order:** any zone and day, with "Play this day".
  - **Fuel & CO2:** TTF, EUA and coal, and gas vs coal running cost with the periods coal was cheaper
    and the lasting switches.
  - **Borders:** the price gap across any interconnected border, days split (daily averages €1 or
    more apart), and a table of all borders.
  - **Italy on Market data:** besides the game's IT (North zone), the page shows the other Italian
    bidding zones (IT-CNOR, IT-CSUD, IT-SUD, IT-CALA, IT-SICI, IT-SARD) and Italy as a whole (IT-ALL,
    generation mix and installed capacity only: no single day-ahead price, and ENTSO-E reports
    Italy's capacity only for the whole country). They live in `site/data/spotrank_data_extra.json`,
    `site/data/spotrank_drivers_extra.json` and `site/data/gen/`, which the puzzle never loads, so they
    are never in puzzles or clues (only in the All zones map and ranking). Builder: `--zones extra`,
    which also covers Ireland; GB is added by `scripts/build_gb_prices.py`.
- **Practice:** a random day, or any day from 2021 to yesterday.
- **Generation mix (Prices page):** for one zone over the period chosen in the price chart, a
  stacked chart of generation by technology (GW or % share) with demand (day-ahead forecast) and the
  zone's price underneath on the same time axis. Daily up to 400 days, then weekly, then monthly
  averages. Technologies under 2% of the zone's output in the period are grouped as "Other". Clicking
  a day opens its merit order.
- **Merit order:** below the map, for any zone you click (default: the dearest of the five), and on
  the Prices page for any zone and any past day (date picker with ‹ › day steps). Blocks
  are what each technology produced that day (ENTSO-E actual generation per production type, daily
  average), ordered by estimated running cost, with demand (day-ahead load forecast) and the day's
  price. Running cost = fuel ÷ efficiency + CO2 × emission factor ÷ efficiency + operating cost, using
  daily TTF, EUA and coal prices from `data/fuel_prices.csv`. Reservoir hydro is placed at the day's
  price (water value). Nuclear, biomass and "other" use fixed costs. The assumptions are in
  `scripts/build_fuel_prices.py`. Generation lives in `site/data/gen/<zone>.json`, one file per zone.
- **Players and leaderboard:** optional. A player picks a display name (no email or password); the
  browser keeps a private player code, which also moves the player to another device. Finished daily
  rounds are saved to a Supabase database (free plan, EU region) and shown in "Today among players"
  under the game and on the Leaderboard page (`#board`: today, this week, this month, all time;
  points 4/3/2/1/0 by tries), with first-try accuracy by region against all players. The page only
  calls the functions in `supabase/setup.sql`; the tables can't be read or changed directly. To
  remove a player, delete their row in Supabase (Table Editor → players); their results go with it.
  The daily workflow makes one small call so the free project isn't paused for inactivity.
- **Skill profile** (in Stats): first-attempt accuracy and average places off per region, built from
  the daily rounds saved in the player's browser. Regions are the UN's regions of Europe (M49): North
  (Nordics and Baltics), East (PL, CZ, SK, HU, RO, BG), South (IT, ES, PT, GR, HR, SI), West (DE-LU, NL,
  BE, FR, AT, CH). The same four group the zones in the Market data picker and "You vs everyone"; the
  puzzle builder keeps its finer regions for its mix rule.

- **Forecast challenge** (preview: open the site with `?fc=1`; `?fc=0` turns it off again; `FC_LIVE`
  in `site/index.html` turns it on for everyone). After the daily puzzle, the player ranks the same five
  zones by their daily baseload price for the next delivery day, once, before 11:00 Brussels time (the
  Swiss auction closes then and publishes its price from 11:10; the SDAC auction closes at 12:00). The zones start in today's order, the naive forecast. Points: one per zone
  in the right place, where a zone counts as right when its price is within €1 of the price that belongs
  in its place (so near-ties count either way). The card shows today's price, tomorrow's demand change and
  wind + solar share as far as published, and the latest fuel and CO2 prices. It opens only after the daily
  puzzle, because today's prices for the same zones would give the puzzle away. Database:
  `supabase/forecast-challenge.sql` (run once in the Supabase SQL Editor). The deadline is checked on the
  database's clock; the first forecast of a day fixes its five zones. Scores come from the `day_prices`
  table, which the daily workflow fills with `scripts/push_day_prices.py` using the `SUPABASE_SECRET_KEY`
  GitHub secret (the page's publishable key can't write it). Leaderboard page: Daily puzzle / Forecast
  switch. Stats: the player's forecasts.
- **Forecast clues.** The card's table shows, per zone: today's price, tomorrow's demand forecast against
  today's (ENTSO-E; usually all in by 10:30 CET), a weather guide for wind and sun (Open-Meteo, free
  for non-commercial use, CC BY 4.0: mean 100 m wind speed and total solar radiation over the CET day,
  averaged over up to 8 points per zone from `scripts/weather_points.json`, made by
  `scripts/make_weather_points.py`), and ENTSO-E's wind + solar forecast as a share of demand (most
  grid operators publish it after the 11:00 cut-off; they have until 18:00). `scripts/build_clues.py`
  writes `site/data/clues.json` and adds tomorrow's ENTSO-E forecasts to the drivers file; it never
  fetches prices (morning runs never do; see Missing prices), so Switzerland's 11:10 price can't reach the page before the cut-off.
  A wind or solar value of exactly 0 where the zone usually has well above 0 (Norway publishes 0 MW
  first) counts as not published yet.
  `.github/workflows/morning-clues.yml` starts a light "clues" run of the update workflow at 07:50,
  08:45 and 09:35 UTC (like `daily-start.yml`, its schedule only runs after Nathaniel has edited a cron
  line there himself); push a change to `.github/clues-now.txt` to start one by hand. The page fetches
  the clues again every 10 minutes while a forecast is open.
- **Missing prices.** A clues run after 13:00 Brussels time (the 20:45 Helsinki one) first runs
  `scripts/price_gap.py`: if tomorrow's prices are missing for any of the 33 zones (ENTSO-E down or late
  during the afternoon update), it fetches prices again and sends them to the leaderboard database.
  Morning clues runs are before 13:00, so they never fetch prices before the 11:00 cut-off.

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

### European Spot Price Daily

A daily newsletter page built from the same data: today's map, the month and the year so far, and
rule-based findings, as a web page and a 3-page A4 PDF.

- Issues: https://nathanielbryann.github.io/spotrank/daily/ (archive, newest first) and `daily/<date>/`.
- Built by `scripts/build_daily_issue.py --pdf --skip-if-done` in every run of **Update prices and deploy**.
  The first run of the day that has today's prices (normally the 09:45 morning run) builds the issue; later
  runs leave it alone unless more zones have arrived. The PDF is printed with the runner's Chrome.
- Maps show every zone, with the four regions of `scripts/regions.json` (their borders are drawn into
  `site/data/zones-map.json` by `scripts/build_map.py`) and a Europe box. Regional and European averages count
  each country once; a country with several zones counts as the average of its zones.
- Comparisons: today against yesterday, the month so far against the whole previous month, and the year so far
  against the whole previous year (each map says so above it).
- Today: in Biggest moves only one Italian zone is listed, with a note for the others that moved as much.
  Month and year rankings use Italy North (IT) as the benchmark for Italian prices.
- To build an issue by hand: `python scripts/build_daily_issue.py --date 2026-11-24 --pdf`.

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
