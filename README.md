# ap-prediction

Public dashboard for 12-hour ap30 geomagnetic index forecasts.

- Deployed site: https://sites.njit.edu/ap-prediction/
  (also at https://njit-research.github.io/ap-prediction/)
- Inference engine + model weights: bundled in-tree under `vendor/realtime-regression-sw/`
- Update cadence: every 30 min (cron `8,38 * * * *`)
- Architecture details: [docs/architecture.md](docs/architecture.md)

## How it works

1. `.github/workflows/forecast.yml` runs on a 30-min cron.
2. It checks out this repo — the inference engine and the model checkpoint
   (`model_best.pth` + `table_stats.pkl`) are committed in-tree — and runs
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
├── vendor/realtime-regression-sw/   inlined inference engine + committed checkpoint
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

### 1. Enable GitHub Pages

Settings → Pages → Build and deployment → Source: **GitHub Actions**.

That is the only setup step: the inference engine and the model checkpoint
(`model_best.pth` + `table_stats.pkl`, ~7.2 MB) are committed in-tree, so the
workflow runs from a plain checkout with no asset download.

### 2. Updating the model

Replace the matched checkpoint pair under
`vendor/realtime-regression-sw/checkpoint/` and commit:

```
cp <new>/model_best.pth  vendor/realtime-regression-sw/checkpoint/
cp <new>/table_stats.pkl vendor/realtime-regression-sw/checkpoint/
git add vendor/realtime-regression-sw/checkpoint/model_best.pth \
        vendor/realtime-regression-sw/checkpoint/table_stats.pkl
git commit -m "Update checkpoint to <training-run-id>"
```

Matched-pair invariant: the two files must come from the same training run.
See [docs/architecture.md](docs/architecture.md) §5 for details and for handling
engine-source / config changes alongside the weights.

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
