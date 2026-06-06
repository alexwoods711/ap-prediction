# Architecture

This document explains how `ap-prediction` works end-to-end: which pieces
exist, how data flows from the live upstream feeds to the browser, and how
the dashboard page connects to the main personal site at `www.eunsu.me`.

---

## 1. Overview

`ap-prediction` publishes a live 12-hour ap30 geomagnetic-index forecast
chart at `https://sites.njit.edu/ap-prediction/`. A GitHub Actions cron
re-runs the inference pipeline every 30 minutes, writes a fresh
`latest.json`, and deploys the updated static site to GitHub Pages.

**Design tenets**

- Single source of truth for the model: the sibling repository
  `realtime-regression-sw`. This repo pins a specific commit of it as a
  git submodule.
- The model weights (`model_best.pth`) and normalizer stats
  (`table_stats.pkl`) are versioned together as a GitHub Release asset
  pair, never checked into git.
- Everything the browser consumes is a single JSON file
  (`site/data/latest.json`). No backend API, no database, no server-side
  rendering. Just a static site.

---

## 2. Component map

Three GitHub repositories cooperate. Each one is public and independent.

```
┌─────────────────────────────────────────────────────────────────────────┐
│  github.com/njit-research/ap-prediction                           │
│    ├── scripts/run_realtime.py          ← inference CLI                 │
│    ├── src/, configs/                                                   │
│    └── Release: v0.1.0-assets                                           │
│        ├── model_best.pth               ← trained weights              │
│        └── table_stats.pkl              ← normalizer                   │
└──────────────────────┬──────────────────────────────────────────────────┘
                       │ git submodule (pinned commit)
                       │ gh release download (runtime)
                       ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  github.com/njit-research/ap-prediction          (this repo)              │
│    ├── .github/workflows/forecast.yml   ← cron + build + deploy         │
│    ├── vendor/realtime-regression-sw/   ← submodule                     │
│    ├── configs/realtime.ci.yaml         ← CI path overrides             │
│    ├── scripts/update_site_data.py      ← JSON post-process             │
│    ├── site/index.html                  ← page shell                   │
│    ├── site/main.js                     ← Chart.js renderer             │
│    └── site/data/                                                       │
│        ├── latest.json                  ← most recent forecast          │
│        └── status.json                  ← pipeline health               │
└──────────────────────┬──────────────────────────────────────────────────┘
                       │ actions/deploy-pages@v4 (artifact)
                       ▼
          sites.njit.edu/ap-prediction/       (served page)
          njit-research.github.io/ap-prediction/ (alias, auto-redirect)

┌─────────────────────────────────────────────────────────────────────────┐
│  github.com/eunsu-park/eunsu-park.github.io                             │
│    ├── _config.yml   (url: https://www.eunsu.me)                        │
│    ├── CNAME         (www.eunsu.me)                                     │
│    └── _includes/navigation.html   ← sidebar link to /ap-prediction     │
└──────────────────────┬──────────────────────────────────────────────────┘
                       ▼
          www.eunsu.me/                     (main CV site)
```

**Why three repos**

- `realtime-regression-sw` is the canonical model owner. Retraining bumps
  its release tag. Changes here must not casually break downstream
  consumers.
- `ap-prediction` is a *consumer*. It pins a submodule commit so the page
  never accidentally depends on the latest unstable model code.
- `eunsu-park.github.io` is a separate Jekyll CV site. It stays clean —
  no forecast auto-commits pollute its history, and a 30-min cron does
  not trigger its Jekyll rebuild.

---

## 3. Data flow

Every 30 minutes, one full cycle from upstream feed to browser happens:

```
┌──────────────────────┐   ┌──────────────────────┐   ┌──────────────────────┐
│ NOAA SWPC plasma     │   │ NOAA SWPC magnetic   │   │ GFZ Hp30/ap30        │
│ (1-min cadence)      │   │ (1-min cadence)      │   │ (30-min cadence)     │
└──────────┬───────────┘   └──────────┬───────────┘   └──────────┬───────────┘
           └──────────────┬──────────────┘                       │
                          ▼                                      ▼
                ┌─────────────────────────────────────────────────────┐
                │ realtime-regression-sw — run_realtime.py            │
                │                                                     │
                │  1. Fetch the three HTTP feeds (requests + retry)   │
                │  2. Aggregate 1-min → 30-min bins                   │
                │  3. Compute anchor t_end = floor(now - 2min, 30min) │
                │  4. Build the 96-row × 22-col event window          │
                │  5. Normalize with table_stats.pkl                  │
                │  6. Run model_best.pth (CPU, ~100ms)                │
                │  7. Denormalize, emit forecast 24 steps × ap30      │
                │  8. Write JSON + CSV to results/{YYYYMMDD}/         │
                └──────────────────────┬──────────────────────────────┘
                                       │
                                       ▼
                ┌─────────────────────────────────────────────────────┐
                │ ap-prediction — update_site_data.py                 │
                │                                                     │
                │  1. Locate newest JSON under vendor/.../results/    │
                │  2. Read it                                         │
                │  3. Locate the paired event CSV                     │
                │     (dataset/events/{anchor_stem}.csv)              │
                │  4. Extract last 96 rows of (datetime, ap30) →      │
                │     embed as "history" array in payload             │
                │  5. Write to site/data/latest.json                  │
                │  6. Refresh site/data/status.json                   │
                └──────────────────────┬──────────────────────────────┘
                                       │
                                       ▼ (git commit + push to main)
                                       │
                                       ▼ (actions/deploy-pages artifact)
                                       │
                                       ▼
                ┌─────────────────────────────────────────────────────┐
                │ Browser — site/main.js                              │
                │                                                     │
                │  1. fetch("./data/latest.json", {cache:"no-store"}) │
                │  2. fetch("./data/status.json", {cache:"no-store"}) │
                │  3. Populate metadata block (UTC + KST)             │
                │  4. Paint status banner based on status.json        │
                │  5. Render Chart.js: gray history + blue forecast   │
                │     + dashed bridge at the anchor                   │
                │  6. x-axis tick labels formatted in UTC             │
                └─────────────────────────────────────────────────────┘
```

### 3.1 Input

- **NOAA SWPC real-time solar wind** — plasma (density, speed, temp) and
  IMF magnetic field (Bx/By/Bz/Bt). 7-day rolling JSON; we use the last
  48 hours.
- **GFZ Potsdam Hp30/ap30 nowcast** — 30-min geomagnetic index observed
  values. Text file, published within minutes of each 30-min boundary.

### 3.2 Anchor computation

The "anchor time" `t_end` is the most recent completed 30-min boundary,
minus a 2-minute safety offset to let the publishers finish posting:

```
t_end = floor(now - 2min, to 30-min boundary)
```

Example: at 14:13 UTC → `t_end = 14:00 UTC`. At 14:45 UTC
→ `t_end = 14:30 UTC`.

If the final steps of the input window are NaN even after forward-fill,
`t_end` rolls back one 30-min step (up to 2 attempts). Beyond that, the
CLI exits with code 2 (`InsufficientDataError`).

### 3.3 Model I/O shape

| Tensor | Shape | Description |
|--------|-------|-------------|
| Input  | `(1, 96, 22)` | 1 batch × 96 timesteps (2 days × 30-min) × 22 vars |
| Output | `(1, 24, 1)`  | 1 batch × 24 timesteps (12 hours × 30-min) × 1 var (ap30) |

22 input variables: 21 solar-wind parameters (v/np/t ×avg/min/max,
Bx/By/Bz/Bt ×avg/min/max) + ap30.

The input ordering and normalization schema are **safety-critical
invariants**; see
[docs/realtime-regression-sw/runtime-invariants.md](https://github.com/njit-research/ap-prediction/blob/main/docs/realtime-regression-sw/runtime-invariants.md).

---

## 4. The GitHub Actions workflow

File: [.github/workflows/forecast.yml](../.github/workflows/forecast.yml)

### 4.1 Triggers

```yaml
on:
  schedule:
    - cron: '3,33 * * * *'     # every 30 min: :03 and :33 UTC
  workflow_dispatch:            # manual trigger from the UI
    inputs:
      now: {description: 'ISO8601 anchor override', required: false}
```

- **Cron** — fires at :03 and :33 UTC (= :03 and :33 KST, since minute is
  timezone-invariant). Offset chosen to dodge the hour-boundary
  congestion on GitHub's scheduler.
- **workflow_dispatch** — manual trigger with optional `now` parameter for
  replaying a specific anchor (debugging / backfill).

### 4.2 Concurrency

```yaml
concurrency:
  group: forecast
  cancel-in-progress: false
```

If the previous run is still going, queue the next one rather than
cancel it. Prevents the pipeline from eating its own tail under heavy
scheduler drift.

### 4.3 Permissions

```yaml
permissions:
  contents: write       # auto-commit site/data/*.json
  pages: write          # for actions/deploy-pages
  id-token: write       # OIDC token required by deploy-pages
```

### 4.4 Steps

| # | Step | Purpose |
|---|------|---------|
| 1 | `actions/checkout@v4` (with submodules) | Pull `ap-prediction` + the pinned `realtime-regression-sw` submodule |
| 2 | `actions/setup-python@v5` (3.12, pip cache) | Python runtime + speed up subsequent installs |
| 3 | `pip install torch --index-url .../cpu` | **CPU-only** PyTorch wheel (~200 MB instead of ~1.5 GB for CUDA) |
| 4 | `pip install -r vendor/realtime-regression-sw/requirements.txt` | numpy, pandas, pyarrow, omegaconf, pyyaml, requests, tqdm, matplotlib |
| 5 | `actions/cache@v4` keyed on `release-${ASSETS_TAG}` | Restore checkpoint + stats if the release tag hasn't changed |
| 6 | `gh release download ...` (on cache miss) | Pull `model_best.pth` + `table_stats.pkl` from the Release |
| 7 | `python scripts/run_realtime.py --config ../../configs/realtime.ci.yaml` | **Inference**. Captures real exit code via `set +e` and `$GITHUB_OUTPUT` |
| 8 | `python scripts/update_site_data.py --exit-code X` | Post-process: copy JSON, embed history, update status |
| 9 | `git commit -m "chore: update forecast data"` + `git push` | Persist `site/data/*.json` changes to `main` |
| 10 | Job summary | Append anchor + first-horizon ap30 to the Actions run summary |
| 11 | `actions/configure-pages@v5` | Signal to Pages: "we're deploying now" |
| 12 | `actions/upload-pages-artifact@v3 path:site` | Upload the `site/` tree as a Pages artifact |
| 13 | `actions/deploy-pages@v4` | Publish the artifact to the live site |

### 4.5 Failure handling

The workflow itself **never fails** on inference errors. Instead, the
failure state is recorded in `status.json` and rendered as a banner on
the page:

| Inference exit code | `status.json.status` | Page banner |
|---------------------|----------------------|-------------|
| `0` (success)       | `"ok"`               | Green: "Forecast is current." |
| `2` (InsufficientDataError) | `"warn"`     | Yellow: upstream data gap |
| other non-zero      | `"error"`            | Red: inference error |

When the run fails, `latest.json` is **not overwritten** — the page keeps
showing the last successful forecast with the warning banner on top.

---

## 5. Model asset delivery

### 5.1 Why not commit weights directly

- `model_best.pth` (~4.5 MB) + retraining churn would bloat git history
  over time.
- Weights must stay paired with the matching `table_stats.pkl`. Pairing
  them as **one GitHub Release** makes the coupling explicit and
  atomic.
- The CI cache (`actions/cache@v4`) downloads them once per release tag
  and reuses them across subsequent runs — zero cost on steady state.

### 5.2 Updating the checkpoint

This is the runbook for replacing `model_best.pth` and
`table_stats.pkl` with a newly trained pair. The whole operation is
driven by **one env-var bump in the workflow file** — no direct file
movement is needed.

#### Overview

```
①  Prepare the new matched pair
       ↓
②  Create a new Release in
   realtime-regression-sw
   (MUST be a new tag)
       ↓
③  Bump ASSETS_TAG in
   ap-prediction's workflow
       ↓
④  Commit + push
       ↓
⑤  Manually trigger the run,
   verify new checkpoint SHA
   on the page
```

#### Step ① — prepare the files

- Collect the new `model_best.pth` and `table_stats.pkl` from the
  retraining run. Any local path is fine — they only need to exist for
  upload.
- **They must be a matched pair.** Mismatched files (different
  training runs) cause silently miscalibrated forecasts; there is no
  runtime check that enforces the pairing. See
  [runtime-invariants.md §3](https://github.com/njit-research/ap-prediction/blob/main/docs/realtime-regression-sw/runtime-invariants.md#normalization-coupling).

#### Step ② — create a new Release

Open **https://github.com/njit-research/ap-prediction/releases/new**
and fill in:

| Field | Value |
|-------|-------|
| Tag | **A brand new tag**, e.g. `v0.2.0-assets`. Never reuse the old tag. |
| Target | `main` (or whichever commit the training code corresponds to) |
| Title | `v0.2.0 runtime assets` (any descriptive string) |
| Description | (optional) training data range, val-MAE, hyperparams |
| Attach binaries | Drag-drop `model_best.pth` and `table_stats.pkl` |

Click **Publish release**.

> ⚠️ **Never reuse the existing tag.** The CI cache key is
> `release-${ASSETS_TAG}` — overwriting assets under the same tag does
> not invalidate the cache, so the old files would keep being served
> indefinitely. Always create a new tag.

#### Step ③ — bump `ASSETS_TAG`

Edit `.github/workflows/forecast.yml` in this repo:

```yaml
env:
  ASSETS_TAG: v0.1.0-assets     # old → new
  REALTIME_REPO: njit-research/ap-prediction
```

becomes:

```yaml
env:
  ASSETS_TAG: v0.2.0-assets
  REALTIME_REPO: njit-research/ap-prediction
```

This is the only line that needs to change.

#### Step ④ — commit and push

```bash
cd ap-prediction
git add .github/workflows/forecast.yml
git commit -m "Bump ASSETS_TAG to v0.2.0-assets"
git push
```

#### Step ⑤ — manually trigger and verify

1. Go to **https://github.com/njit-research/ap-prediction/actions** →
   **Forecast** → **Run workflow**. Leave `now` empty; click
   **Run workflow**.
2. Wait 1–2 minutes. Because the cache key changed, the workflow will
   hit a cache miss and execute the `Download checkpoint + stats`
   step — confirm this in the run log.
3. Once the run is green, hard-refresh the deployed page
   (`Cmd+Shift+R` / `Ctrl+F5`):
   **https://sites.njit.edu/ap-prediction/**
4. Check the **"Checkpoint SHA"** field in the metadata block. It
   should now show the first 12 characters of the new `model_best.pth`
   SHA256 — different from the previous value.

#### Cache invalidation, explained

The workflow caches the `checkpoint/` directory using
`actions/cache@v4` with the key `release-${{ env.ASSETS_TAG }}`. The
cache is a key-value store, keyed on the literal string:

- `ASSETS_TAG=v0.1.0-assets` → key `release-v0.1.0-assets`
- `ASSETS_TAG=v0.2.0-assets` → key `release-v0.2.0-assets` (brand new,
  forces fresh download)

So **changing the tag string is the mechanism that invalidates the
cache.** You do not need to manually clear anything.

#### Rolling back

If the new model misbehaves, reverting is symmetric:

```bash
# Edit .github/workflows/forecast.yml, set ASSETS_TAG back to v0.1.0-assets
git add .github/workflows/forecast.yml
git commit -m "Revert ASSETS_TAG to v0.1.0-assets"
git push
# Manually trigger the workflow
```

Old releases remain available unless explicitly deleted, so rollback is
immediate. Keep the old Release around for at least a few forecast
cycles after a bump.

#### Code changes alongside the weights

If the retraining also changed the `realtime-regression-sw` source code
(new architecture, different variable order, etc.), advance the
submodule pin as well:

```bash
cd vendor/realtime-regression-sw
git fetch
git checkout <new-commit-or-tag>
cd ../..
git add vendor/realtime-regression-sw
git commit -m "Pin realtime-regression-sw to <new-ref>"
git push
```

If only the weights changed (same code), no submodule update is
needed.

#### Does the config need to change?

Short answer: **usually no**. The `configs/realtime.ci.yaml` file
describes the *model architecture* and the *runtime environment*, not
the trained weights themselves. So a simple "retrain on more data with
the same architecture" does **not** require touching the config.

Here is the full table, by config section:

| Section | Weights only (same architecture) | Architecture/profile change |
|---------|:-:|:-:|
| `profile.*`               | unchanged | **must update** |
| `experiment.name`         | unchanged | **must update** |
| `paths.*`                 | unchanged | unchanged |
| `sources.*`               | unchanged | unchanged |
| `window.lookback_steps`   | unchanged | **update if input length changes** |
| `window.forecast_steps`   | unchanged | **update if output length changes** |
| `window.boundary_offset_minutes` | unchanged | unchanged |
| `runtime.*`               | unchanged | unchanged |
| `analysis.*`              | unchanged | unchanged |
| `model_provenance.*`      | ⚠️ **recommended** (display-only) | **must update** |

**Why `model_provenance.*` is "recommended" even for same-architecture
retraining**

These three values (`val_loss_at_train`, `val_mae_at_train`,
`val_rmse_at_train`) are read from config at inference time and copied
into the output JSON's `model` block (see
[run_realtime.py:308](https://github.com/njit-research/ap-prediction/blob/main/scripts/run_realtime.py#L308)).
The page renders `val_mae_at_train` under "Val-MAE at train" in the
metadata block. If you ship new weights without updating these, the
page will display the **old** MAE alongside the **new** forecasts —
functionally fine but misleading for visitors.

**Concrete examples**

- *Retrained `in2d_out12h_gnn_transformer` with 6 more months of data*
  → no structural config change; optionally update
  `model_provenance.*` to the new training metrics.
- *Switched profile from `in2d_out12h_gnn_transformer` to
  `in12h_out12h_gnn_patchtst`* → must update `profile.*`,
  `experiment.name`, `window.lookback_steps` (96 → 24), and
  `model_provenance.*`.
- *Changed forecast horizon from 12h to 6h (new profile
  `in2d_out6h_gnn_transformer`)* → update `profile.*`,
  `experiment.name`, `window.forecast_steps` (24 → 12), and
  `model_provenance.*`.

**Atomicity**

When the architecture changes, commit the config change **together
with** the `ASSETS_TAG` bump in a single commit. Otherwise the
workflow will temporarily try to run new weights against the old
architecture (or vice versa) and crash.

#### Common pitfalls

| Pitfall | Symptom | Fix |
|---------|---------|-----|
| Reused old tag | New weights never take effect; Checkpoint SHA unchanged | Delete the reused-tag Release, recreate with a new tag, bump ASSETS_TAG again |
| Forgot to change ASSETS_TAG | Same symptom | Bump `ASSETS_TAG` to the new tag and push |
| Uploaded only the .pth, not the .pkl | Inference fails with stats-file not-found error | Edit the Release, attach the missing `table_stats.pkl` |
| Mismatched pair (different training runs) | Inference succeeds, but predictions look off (systematic bias) | Re-upload the correct matching pair as a new tag |
| Submodule advanced but ASSETS_TAG not bumped | Inference may crash if the new code expects different input features than the old weights | Align: bump ASSETS_TAG to a Release whose weights match the submodule's code |

---

## 6. GitHub Pages deployment

### 6.1 "Actions" source vs branch source

We use **Source: GitHub Actions** (not "Deploy from a branch"). This
means:

- No `gh-pages` branch exists. Publishing is done by uploading a Pages
  artifact (`actions/upload-pages-artifact@v3`) and then calling
  `actions/deploy-pages@v4`.
- Each run re-deploys the full `site/` directory. This keeps the build
  deterministic and means `main` branch history is not mixed with a
  parallel `gh-pages` history.

### 6.2 URL resolution

The repo name `ap-prediction` becomes the URL path:

`github.com/njit-research/ap-prediction` repo name (`ap-prediction`)
→ project Pages URL path (`/ap-prediction/`).

Because the user account has a user-page repo (`eunsu-park.github.io`)
with a custom domain (`CNAME = www.eunsu.me`), the custom domain is
**automatically inherited** by all project Pages. Therefore both of the
following URLs serve the same content:

- Primary: `https://sites.njit.edu/ap-prediction/`
- Alias: `https://njit-research.github.io/ap-prediction/` (301 redirects
  to the primary)

### 6.3 Cache behavior

- JSON files (`latest.json`, `status.json`) are fetched with
  `cache: "no-store"` in `main.js`, so browsers always request a fresh
  copy.
- HTML and JS files (`index.html`, `main.js`) use GitHub Pages' default
  cache headers. The browser may cache them aggressively — if the page
  visibly lags behind, a hard refresh (`Cmd+Shift+R` / `Ctrl+F5`)
  forces a fresh pull.

---

## 7. Homepage integration

The main site (`www.eunsu.me`) is a Jekyll blog in
`github.com/eunsu-park/eunsu-park.github.io`. Integration is **one
line** in `_includes/navigation.html`:

```html
<li><a href="{{ site.baseurl }}/ap-prediction">
  <i class="fas fa-chart-line"></i> AP Forecast
</a></li>
```

**How the link actually works**

1. Jekyll renders `{{ site.baseurl }}/ap-prediction` → `/ap-prediction`
   (since `baseurl` is empty in `_config.yml`).
2. Browser clicks on `<a href="/ap-prediction">` → navigates to
   `https://sites.njit.edu/ap-prediction`.
3. GitHub Pages receives the request for `/ap-prediction/` and serves
   the content from the `ap-prediction` project Pages artifact (i.e.
   the `site/` directory this repo publishes).

Nothing else is shared between the two sites — no CSS, no JavaScript,
no layout. They just happen to live under the same domain.

---

## 8. Files & responsibilities

### 8.1 In `ap-prediction`

| Path | Purpose |
|------|---------|
| [`.github/workflows/forecast.yml`](../.github/workflows/forecast.yml) | Cron-triggered build+deploy pipeline |
| [`configs/realtime.ci.yaml`](../configs/realtime.ci.yaml) | CI path overrides for `realtime-regression-sw` (checkpoint, stats, event_dir, results_dir all relative to submodule root) |
| [`scripts/update_site_data.py`](../scripts/update_site_data.py) | Post-process: read latest forecast JSON, embed 96-step observed history from the event CSV, write `site/data/latest.json` + `status.json` |
| [`site/index.html`](../site/index.html) | Static page shell. Inline CSS. Loads Chart.js v4 + date-fns adapter from jsDelivr CDN |
| [`site/main.js`](../site/main.js) | Fetches `latest.json` + `status.json`, fills metadata, paints banner, renders two-dataset chart (history gray + forecast blue) with bridge dashed line at anchor, UTC-formatted x-axis ticks, tooltips showing both UTC and KST |
| [`site/data/latest.json`](../site/data/latest.json) | Most recent forecast payload (auto-committed by the workflow) |
| [`site/data/status.json`](../site/data/status.json) | Pipeline health (auto-committed by the workflow) |
| [`vendor/realtime-regression-sw/`](../vendor/realtime-regression-sw) | Git submodule — pinned commit of the inference repo |

### 8.2 `latest.json` schema

```json
{
  "run_timestamp_utc":    "2026-04-25T00:00:07Z",
  "anchor_timestamp_utc": "2026-04-24T14:30:00Z",
  "model": {
    "profile":          "in2d_out12h_gnn_transformer",
    "checkpoint_path":  "./checkpoint/model_best.pth",
    "checkpoint_sha256":"d5d87bcbf905...",
    "val_loss_at_train": 0.2727,
    "val_mae_at_train":  0.3840,
    "val_rmse_at_train": 0.4960
  },
  "input": {
    "event_csv": "/.../dataset/events/20260424143000.csv",
    "sources": {
      "noaa_plasma_url": "...",
      "noaa_mag_url":    "...",
      "gfz_hpo_url":     "..."
    },
    "missing_data_filled_fraction": 0.017
  },
  "forecast": [                                // 24 entries = 12 hours
    {"horizon_steps":1, "horizon_minutes":30, "target_timestamp_utc":"...", "ap30":7.2},
    ...
  ],
  "history": [                                 // 96 entries = 48 hours (added by update_site_data.py)
    {"timestamp_utc":"...", "ap30":9.0},
    ...
  ]
}
```

### 8.3 `status.json` schema

```json
{
  "status":            "ok" | "warn" | "error",
  "last_success_utc":  "2026-04-25T00:00:07Z",
  "last_attempt_utc":  "2026-04-25T00:00:07Z",
  "last_error": null | {
    "code":    <int>,
    "message": "..."
  }
}
```

---

## 9. Cost and quota

- GitHub Actions Linux runner minutes are **unlimited and free for
  public repos**. Our 30-min cron uses ~720 minutes per month; cost is
  $0.
- GitHub Pages bandwidth: 100 GB/month soft limit per user. Our static
  site is a few hundred KB; nowhere near the limit.
- NOAA and GFZ feeds are unauthenticated public JSON/text; no API key
  or quota to worry about.

---

## 10. Known limitations

1. **Scheduler drift** — GitHub Actions cron is best-effort. A run
   scheduled for 14:33 UTC may actually start anywhere from 14:33 to
   15:00+. The anchor computation handles this gracefully by always
   aligning to the most recent 30-min boundary, but the "last updated"
   timestamp on the page reflects the actual run time, not the slot
   time.
2. **Public weights exposure** — `model_best.pth` is posted as a public
   Release asset. Anyone can download and reuse the weights. Acceptable
   for this project (academic/personal); if sensitivity ever changes,
   move the Release to a private repo and add a fine-grained PAT to the
   workflow.
3. **Single-point failure on stats-checkpoint pairing** — if the
   `ASSETS_TAG` env and the actual Release contents diverge (e.g. you
   upload a new `.pth` but forget to upload a matching `.pkl`), the
   model will silently produce miscalibrated outputs. There is no
   runtime check that the two match.
4. **No historical archive on the page** — `latest.json` is the only
   data the page shows. Past forecasts are not accessible from the UI
   (they still exist in git history of `site/data/latest.json`).

---

## 11. Extending the dashboard

Candidate next steps, in rough order of effort:

1. **MCD uncertainty band** — `run_realtime.py` already computes Monte
   Carlo Dropout samples (disabled in `configs/realtime.ci.yaml`
   `analysis.mcd.enable: false`). Enable it, propagate the `lower` /
   `upper` arrays into `latest.json`, and add a shaded band dataset in
   `main.js`. Minor Chart.js work.
2. **Historical accuracy view** — archive each run's `latest.json` to
   `site/data/history/YYYYMMDD.json`, plus a rolling `history.json`
   index. The page adds a secondary chart: "forecast-vs-realized MAE
   over the last 7 days".
3. **hp30 as a second target** — currently only ap30 is on the page.
   The model also has variants predicting hp30 directly. Add a second
   line to the chart with a toggle.
4. **Attention heatmap** — `plot_attention` exists in the sibling repo
   but emits PNG. For interactive use, serialize attention weights to
   JSON and render with a canvas heatmap library.

Each of these would be additive — none require restructuring the
current pipeline.
