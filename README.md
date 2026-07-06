# ap-prediction

Public dashboard for 12-hour ap30 geomagnetic index forecasts.

- Deployed site: https://sites.njit.edu/ap-prediction/
  (also at https://njit-research.github.io/ap-prediction/)
- Inference engine + model weights: bundled in-tree under `vendor/realtime-regression-sw/`
- Forecast every 10 min, three attempts per 30-min anchor (cron `8,18,28,38,48,58 * * * *`,
  a backup against transient upstream outages)
- Architecture details: [docs/architecture.md](docs/architecture.md)

## How it works

1. `.github/workflows/forecast.yml` runs every 10 min.
2. It checks out this repo — the inference engine and the model checkpoint
   (`model_best.pth` + `table_stats.pkl`) are committed in-tree — and runs
   `scripts/run_realtime.py`. If an upstream feed is unreachable the run exits
   with a "data gap" warning (exit 2) instead of failing hard.
3. `scripts/update_site_data.py` copies the newest forecast JSON into
   `site/data/latest.json`, refreshes `site/data/status.json`, and appends to
   the past-forecast archives (`forecast_history.json` / `.csv`).
4. The `site/` directory is published as a GitHub Pages artifact.
5. `site/index.html` fetches `data/latest.json` (+ `forecast_history.json`) and
   renders a Chart.js plot of the 12-hour forecast, the observed history, and the
   green past-forecast line, with a `forecast_history.csv` download link.

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
