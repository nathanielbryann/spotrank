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
