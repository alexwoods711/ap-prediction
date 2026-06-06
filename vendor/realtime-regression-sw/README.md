# realtime-regression-sw

Real-time **ap30** geomagnetic index prediction from live solar-wind and
geomagnetic nowcast feeds.

---

## Overview

This is the on-demand inference engine. It downloads live data from NOAA SWPC
(solar wind) and GFZ Potsdam (Hp30/ap30 nowcast), preprocesses it into the same
30-minute event format used for training, and runs the trained model to produce
a 12-hour ap30 forecast.

- **Active model**: `in12h_out12h_gnn_patchtst` (GNN encoder + PatchTST temporal backbone)
- **Input**: 12-hour lookback (24 steps at 30-min cadence, 22 variables)
- **Output**: 24-step ap30 forecast (30 min → 12 hours ahead)
- **Execution**: On-demand CLI (single-run). Run manually when a forecast is needed.

---

## Architecture

```
NOAA SWPC plasma.json  ─┐
NOAA SWPC mag.json     ─┼─► fetch ─► aggregate(30-min) ─┐
GFZ Hp30/ap30 nowcast  ─┘                               ├─► align ─► event CSV ─► predict ─► results JSON/CSV
                                                         ┘
```

All bundled internal modules (downloader, normalizer, model code) live under
`src/_vendor/`. The engine has no runtime dependencies beyond the Python packages
listed in `requirements.txt`.

---

## Quickstart

```bash
conda activate ap
pip install -r requirements.txt

# Windows (default) — assumes workspace at D:/realtime/ with the standard layout
python scripts/run_realtime.py

# macOS / Linux — uses ~/realtime/... paths (see configs/realtime.mac.yaml)
python scripts/run_realtime.py --config configs/realtime.mac.yaml
```

Both configs share the same schema; only `paths.*` differ.

All profiles (24 input windows × 9 model architectures) are defined in
[`configs/profile/io/`](configs/profile/io/) and
[`configs/profile/model/`](configs/profile/model/); point `profile.io` and
`profile.model` in your runtime yaml to pick any combination.

Optional flags:

| Flag | Purpose |
|---|---|
| `--config PATH` | Override the config path (default `configs/realtime.yaml`) |
| `--now ISO8601`  | Pin the anchor time for reproducibility (e.g. `2026-04-19T12:00:00`) |
| `--dry-run`      | Use cached fixtures under `tests/fixtures/` instead of live network |
| `--device`       | `cpu`, `cuda`, or `mps` (overrides YAML) |
| `--verbose`      | Enable DEBUG-level logging |

---

## Configuration

Edit `configs/realtime.yaml` to change URLs, paths, window size, or the
missing-data policy. The checkpoint and training statistics paths default to the
OneDrive share used during training; point them at local copies if preferred.

Key keys:

```yaml
paths:
  checkpoint: "/path/to/model_best.pth"
  stats_file: "/path/to/table_stats.pkl"
sources:
  noaa_plasma_url: "https://services.swpc.noaa.gov/products/solar-wind/plasma-7-day.json"
  noaa_mag_url:    "https://services.swpc.noaa.gov/products/solar-wind/mag-7-day.json"
  gfz_hpo_url:     "https://www-app3.gfz-potsdam.de/kp_index/Hp30_ap30_nowcast.txt"
window:
  lookback_steps: 96
  forecast_steps: 12
```

---

## Data Sources

| Source | URL | Cadence | Purpose |
|---|---|---|---|
| NOAA SWPC plasma | https://services.swpc.noaa.gov/products/solar-wind/plasma-7-day.json | ~1 min | density, speed, temperature |
| NOAA SWPC mag    | https://services.swpc.noaa.gov/products/solar-wind/mag-7-day.json    | ~1 min | bx/by/bz/bt (GSM) |
| GFZ HPo nowcast  | https://www-app3.gfz-potsdam.de/kp_index/Hp30_ap30_nowcast.txt       | 30 min | Hp30, ap30 |

NOAA provides only the past 7 days of data; the pipeline cannot backtest further
into the past from this source.

---

## Output Schema

Each run produces a JSON and a CSV file in
`results/predictions/{YYYYMMDD}/{anchor_timestamp}.{json,csv}`.

```json
{
  "run_timestamp_utc": "2026-04-19T12:17:03Z",
  "anchor_timestamp_utc": "2026-04-19T12:00:00Z",
  "model": {
    "profile": "in2d_out6h_gnn_transformer",
    "checkpoint_path": "...",
    "checkpoint_sha256": "abcd1234...",
    "val_loss_at_train": 0.2178,
    "val_mae_at_train": 0.3532
  },
  "input": {
    "event_csv": "dataset/events/20260419120000.csv",
    "sources": { "noaa_plasma_url": "...", "noaa_mag_url": "...", "gfz_hpo_url": "..." },
    "missing_data_filled_fraction": 0.017
  },
  "forecast": [
    {"horizon_steps": 1, "horizon_minutes": 30, "target_timestamp_utc": "2026-04-19T12:30:00Z", "ap30": 7.2}
  ]
}
```

CSV columns: `horizon_steps, horizon_minutes, target_timestamp_utc, ap30_pred`.

---

## Model

The default profile `in2d_out6h_gnn_transformer` is a GNN encoder with a
Transformer temporal backbone, selected as the single best model across 99
trained configurations (lowest validation loss of 0.2178). The checkpoint and
the per-variable statistics file (`table_stats.pkl`) used at training time are
required for inference.

Checkpoint size: ~4.5 MB. CPU inference latency: ~100 ms per request.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `InsufficientDataError` | NOAA/GFZ outage or many recent NaNs | Retry later; check source URLs |
| `FileNotFoundError: table_stats.pkl` | Stats file path wrong | Update `paths.stats_file` in config |
| Unrealistic forecast values | Stats/model mismatch | Ensure stats file matches checkpoint training |
| SSL warnings | `urllib3` InsecureRequestWarning | Suppressed for JSOC compatibility — safe to ignore |

---

## License

MIT License. See [LICENSE](LICENSE).
