# ap-prediction

Public dashboard for 12-hour ap30 geomagnetic index forecasts.

- Deployed site: https://sites.njit.edu/ap-prediction/
  (also at https://njit-research.github.io/ap-prediction/)
- Forecast model: [njit-research/ap-prediction](https://github.com/njit-research/ap-prediction)
- Update cadence: every 30 min (cron `8,38 * * * *`)
- Architecture details: [docs/architecture.md](docs/architecture.md)

## How it works

1. `.github/workflows/forecast.yml` runs on a 30-min cron.
2. It checks out this repo (with `realtime-regression-sw` pinned as a submodule),
   downloads the model checkpoint + normalization stats from the
   `realtime-regression-sw` GitHub Release, and runs
   `scripts/run_realtime.py`.
3. `scripts/update_site_data.py` copies the newest forecast JSON into
   `site/data/latest.json` and refreshes `site/data/status.json`.
4. The `site/` directory is published as a GitHub Pages artifact.
5. `site/index.html` fetches `data/latest.json` on load and renders a Chart.js
   line plot of the 24-step (12-hour) ap30 forecast.

## Repository layout

```
ap-prediction/
├── .github/workflows/forecast.yml   cron-triggered pipeline
├── vendor/realtime-regression-sw/   git submodule, inference code
├── configs/realtime.ci.yaml         CI path overrides
├── scripts/update_site_data.py      post-process inference output
├── site/
│   ├── index.html                   page shell
│   ├── main.js                      Chart.js render + metadata
│   └── data/
│       ├── latest.json              most recent forecast (committed each run)
│       └── status.json              pipeline status for the banner
└── README.md
```

## One-time setup

### 1. Upload runtime assets to the inference repo

The workflow downloads `model_best.pth` and `table_stats.pkl` from a GitHub
Release on `njit-research/ap-prediction`. Create the release once:

1. Open https://github.com/njit-research/ap-prediction/releases/new
2. Tag: `v0.1.0-assets` (new)
3. Target: `main`
4. Title: `v0.1.0 runtime assets`
5. Attach both files:
   - `model_best.pth` (~4.5 MB)
   - `table_stats.pkl` (<100 KB)
6. Publish.

Matched-pair invariant: the two files must come from the same training run.
When the model is retrained, create a new release (e.g. `v0.2.0-assets`) with
both files and update `env.ASSETS_TAG` in `forecast.yml`.

### 2. Enable GitHub Pages

Settings → Pages → Build and deployment → Source: **GitHub Actions**.

### 3. (Optional) Sync the submodule to a stable tag

```
cd vendor/realtime-regression-sw
git checkout <tag-or-sha>
cd ../..
git add vendor/realtime-regression-sw
git commit -m "Pin realtime-regression-sw to <tag-or-sha>"
```

## Trigger a run manually

Actions tab → "Forecast" workflow → "Run workflow".
Optionally provide an ISO8601 `now` to replay a specific anchor.

## Failure handling

`run_realtime.py` exit codes mapped by `scripts/update_site_data.py`:

- `0` → `status.json.status = "ok"`, `latest.json` updated
- `2` → `status.json.status = "warn"` (InsufficientDataError —
  upstream data gap), `latest.json` preserved
- other → `status.json.status = "error"`, `latest.json` preserved

The workflow itself always succeeds (the Actions badge stays green); the page
banner is the true health indicator.
