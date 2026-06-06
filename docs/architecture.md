# Architecture / 시스템 구조

This document explains how `ap-prediction` works end-to-end: which pieces
exist, how data flows from the live upstream feeds to the browser, and how
the dashboard page connects to the main personal site at `www.eunsu.me`.

이 문서는 `ap-prediction`이 전체적으로 어떻게 동작하는지 설명합니다. 구성
요소, 실시간 피드에서 브라우저까지의 데이터 흐름, 대시보드 페이지가 개인
사이트(`www.eunsu.me`)와 어떻게 연결되는지 다룹니다.

---

## 1. Overview / 개요

`ap-prediction` publishes a live 12-hour ap30 geomagnetic-index forecast
chart at `https://sites.njit.edu/ap-prediction/`. A GitHub Actions cron
re-runs the inference pipeline every 30 minutes, writes a fresh
`latest.json`, and deploys the updated static site to GitHub Pages.

`ap-prediction`은 12시간 ap30 지자기 지수 예측 차트를
`https://sites.njit.edu/ap-prediction/`에 공개합니다. GitHub Actions cron이
30분마다 추론 파이프라인을 재실행하고 새 `latest.json`을 기록한 뒤,
업데이트된 정적 사이트를 GitHub Pages에 배포합니다.

**Design tenets / 설계 원칙**

- Single source of truth for the model: the sibling repository
  `realtime-regression-sw`. This repo pins a specific commit of it as a
  git submodule.
  모델은 `realtime-regression-sw` 레포가 단일 출처이며, 본 레포는 submodule로
  특정 커밋에 고정합니다.
- The model weights (`model_best.pth`) and normalizer stats
  (`table_stats.pkl`) are versioned together as a GitHub Release asset
  pair, never checked into git.
  모델 가중치와 정규화 통계는 GitHub Release 자산으로 쌍(pair)으로 관리되며,
  git에는 커밋되지 않습니다.
- Everything the browser consumes is a single JSON file
  (`site/data/latest.json`). No backend API, no database, no server-side
  rendering. Just a static site.
  브라우저는 단일 JSON 파일(`site/data/latest.json`)만 소비합니다. 백엔드 API,
  데이터베이스, 서버사이드 렌더링 없음 — 순수 정적 사이트입니다.

---

## 2. Component map / 구성 요소

Three GitHub repositories cooperate. Each one is public and independent.
세 개의 GitHub 레포지토리가 협력합니다. 모두 공개이며 독립적입니다.

```
┌─────────────────────────────────────────────────────────────────────────┐
│  github.com/njit-research/ap-prediction                           │
│    ├── scripts/run_realtime.py          ← inference CLI / 추론 CLI      │
│    ├── src/, configs/                                                   │
│    └── Release: v0.1.0-assets                                           │
│        ├── model_best.pth               ← trained weights / 학습 가중치 │
│        └── table_stats.pkl              ← normalizer / 정규화 통계      │
└──────────────────────┬──────────────────────────────────────────────────┘
                       │ git submodule (pinned commit)
                       │ gh release download (runtime)
                       ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  github.com/njit-research/ap-prediction          (this repo / 본 레포)     │
│    ├── .github/workflows/forecast.yml   ← cron + build + deploy         │
│    ├── vendor/realtime-regression-sw/   ← submodule                     │
│    ├── configs/realtime.ci.yaml         ← CI path overrides             │
│    ├── scripts/update_site_data.py      ← JSON post-process             │
│    ├── site/index.html                  ← page shell / 페이지 골격      │
│    ├── site/main.js                     ← Chart.js renderer             │
│    └── site/data/                                                       │
│        ├── latest.json                  ← most recent forecast          │
│        └── status.json                  ← pipeline health               │
└──────────────────────┬──────────────────────────────────────────────────┘
                       │ actions/deploy-pages@v4 (artifact)
                       ▼
          sites.njit.edu/ap-prediction/       (served page / 공개 URL)
          njit-research.github.io/ap-prediction/ (alias, auto-redirect)

┌─────────────────────────────────────────────────────────────────────────┐
│  github.com/eunsu-park/eunsu-park.github.io                             │
│    ├── _config.yml   (url: https://www.eunsu.me)                        │
│    ├── CNAME         (www.eunsu.me)                                     │
│    └── _includes/navigation.html   ← sidebar link to /ap-prediction     │
│                                      사이드바 링크                       │
└──────────────────────┬──────────────────────────────────────────────────┘
                       ▼
          www.eunsu.me/                     (main CV site / 메인 사이트)
```

**Why three repos / 왜 세 레포로 분리했나**

- `realtime-regression-sw` is the canonical model owner. Retraining bumps
  its release tag. Changes here must not casually break downstream
  consumers.
  `realtime-regression-sw`는 모델 소유자. 재학습은 릴리즈 태그 갱신으로 관리.
  변경이 하위 소비자에게 우발적 영향을 주지 않도록 격리.
- `ap-prediction` is a *consumer*. It pins a submodule commit so the page
  never accidentally depends on the latest unstable model code.
  `ap-prediction`은 소비자. submodule 커밋 고정으로 불안정한 최신 모델 코드에
  우발적 의존 방지.
- `eunsu-park.github.io` is a separate Jekyll CV site. It stays clean —
  no forecast auto-commits pollute its history, and a 30-min cron does
  not trigger its Jekyll rebuild.
  `eunsu-park.github.io`는 독립 Jekyll CV 사이트. 30분 주기의 forecast
  auto-commit이 Jekyll 리빌드를 유발하지 않도록 분리.

---

## 3. Data flow / 데이터 흐름

Every 30 minutes, one full cycle from upstream feed to browser happens:
30분마다 업스트림 피드에서 브라우저까지 한 사이클이 돕니다:

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

### 3.1 Input / 입력

- **NOAA SWPC real-time solar wind** — plasma (density, speed, temp) and
  IMF magnetic field (Bx/By/Bz/Bt). 7-day rolling JSON; we use the last
  48 hours.
- **GFZ Potsdam Hp30/ap30 nowcast** — 30-min geomagnetic index observed
  values. Text file, published within minutes of each 30-min boundary.

- **NOAA SWPC 실시간 태양풍** — plasma(밀도, 속도, 온도)과 IMF 자기장
  (Bx/By/Bz/Bt). 7일 롤링 JSON, 최근 48시간 사용.
- **GFZ 포츠담 Hp30/ap30 nowcast** — 30분 지자기 지수 관측값. 텍스트 파일,
  각 30분 경계 직후 발행.

### 3.2 Anchor computation / Anchor 계산

The "anchor time" `t_end` is the most recent completed 30-min boundary,
minus a 2-minute safety offset to let the publishers finish posting:

Anchor 시각 `t_end`은 가장 최근에 완료된 30분 경계에서 발행자가 게시를
마칠 수 있도록 2분의 안전 오프셋을 뺀 값입니다:

```
t_end = floor(now - 2min, to 30-min boundary)
```

Example / 예시: at 14:13 UTC → `t_end = 14:00 UTC`. At 14:45 UTC
→ `t_end = 14:30 UTC`.

If the final steps of the input window are NaN even after forward-fill,
`t_end` rolls back one 30-min step (up to 2 attempts). Beyond that, the
CLI exits with code 2 (`InsufficientDataError`).

입력 윈도우의 마지막 스텝이 forward-fill 후에도 NaN이면, `t_end`를 30분씩
rollback(최대 2회). 그 이상이면 CLI가 exit code 2 (`InsufficientDataError`)로
종료합니다.

### 3.3 Model I/O shape / 모델 입출력 shape

| Tensor | Shape | Description |
|--------|-------|-------------|
| Input  | `(1, 96, 22)` | 1 batch × 96 timesteps (2 days × 30-min) × 22 vars |
| Output | `(1, 24, 1)`  | 1 batch × 24 timesteps (12 hours × 30-min) × 1 var (ap30) |

22 input variables: 21 solar-wind parameters (v/np/t ×avg/min/max,
Bx/By/Bz/Bt ×avg/min/max) + ap30.

The input ordering and normalization schema are **safety-critical
invariants**; see
[docs/realtime-regression-sw/runtime-invariants.md](https://github.com/njit-research/ap-prediction/blob/main/docs/realtime-regression-sw/runtime-invariants.md).

입력 순서와 정규화 스키마는 **안전 불변식**입니다. 위 문서 참조.

---

## 4. The GitHub Actions workflow / GitHub Actions 워크플로

File: [.github/workflows/forecast.yml](../.github/workflows/forecast.yml)

### 4.1 Triggers / 트리거

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
  Cron은 UTC :03, :33에 발사. 분(minute)은 시간대 불변이므로 KST도 :03, :33.
  시간 경계 혼잡을 피하기 위한 오프셋.
- **workflow_dispatch** — manual trigger with optional `now` parameter for
  replaying a specific anchor (debugging / backfill).
  수동 트리거, `now`로 특정 anchor 재실행 가능.

### 4.2 Concurrency / 동시성

```yaml
concurrency:
  group: forecast
  cancel-in-progress: false
```

If the previous run is still going, queue the next one rather than
cancel it. Prevents the pipeline from eating its own tail under heavy
scheduler drift.
이전 실행이 진행 중이면 다음 것을 취소하지 않고 대기. 스케줄러 드리프트
하에서 파이프라인이 자신을 잠식하는 것을 방지.

### 4.3 Permissions / 권한

```yaml
permissions:
  contents: write       # auto-commit site/data/*.json
  pages: write          # for actions/deploy-pages
  id-token: write       # OIDC token required by deploy-pages
```

### 4.4 Steps / 단계

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

### 4.5 Failure handling / 실패 처리

The workflow itself **never fails** on inference errors. Instead, the
failure state is recorded in `status.json` and rendered as a banner on
the page:

추론 오류가 나도 워크플로 자체는 **절대 실패하지 않음**. 대신 `status.json`에
실패 상태를 기록하고 페이지 배너로 표출:

| Inference exit code | `status.json.status` | Page banner |
|---------------------|----------------------|-------------|
| `0` (success)       | `"ok"`               | Green: "Forecast is current." |
| `2` (InsufficientDataError) | `"warn"`     | Yellow: upstream data gap |
| other non-zero      | `"error"`            | Red: inference error |

When the run fails, `latest.json` is **not overwritten** — the page keeps
showing the last successful forecast with the warning banner on top.

실패 시 `latest.json`은 **덮어쓰지 않음**. 페이지는 마지막 성공 예측을 유지한
채 상단에 경고 배너만 표시.

---

## 5. Model asset delivery / 모델 자산 전달

### 5.1 Why not commit weights directly / 왜 가중치를 커밋하지 않나

- `model_best.pth` (~4.5 MB) + retraining churn would bloat git history
  over time.
- Weights must stay paired with the matching `table_stats.pkl`. Pairing
  them as **one GitHub Release** makes the coupling explicit and
  atomic.
- The CI cache (`actions/cache@v4`) downloads them once per release tag
  and reuses them across subsequent runs — zero cost on steady state.

- 재학습 시 git 히스토리가 비대해짐
- 가중치는 `table_stats.pkl`과 반드시 짝을 이뤄야 함. **하나의 Release**로
  묶으면 결합이 명시적·원자적
- CI 캐시가 릴리즈 태그당 한 번만 다운로드하고 이후 재사용 — 정상 상태에서
  추가 비용 0

### 5.2 Updating the checkpoint / 체크포인트 갱신 절차

This is the runbook for replacing `model_best.pth` and
`table_stats.pkl` with a newly trained pair. The whole operation is
driven by **one env-var bump in the workflow file** — no direct file
movement is needed.

재학습된 `model_best.pth` / `table_stats.pkl` 쌍으로 교체하는 런북입니다.
전체 작업은 **워크플로 파일의 env 값 한 줄 변경**으로 구동되며, 파일을 직접
이동/복사할 필요가 없습니다.

#### Overview / 개요

```
①  Prepare the new matched pair   .pth 와 .pkl 페어 준비
       ↓
②  Create a new Release in        realtime-regression-sw에 새 Release 생성
   realtime-regression-sw         (반드시 새 태그)
   (MUST be a new tag)
       ↓
③  Bump ASSETS_TAG in             ap-prediction workflow의
   ap-prediction's workflow       ASSETS_TAG 값 변경
       ↓
④  Commit + push                  커밋 + 푸시
       ↓
⑤  Manually trigger the run,      수동 실행 후 페이지에서
   verify new checkpoint SHA      Checkpoint SHA 확인
   on the page
```

#### Step ① — prepare the files / 파일 준비

- Collect the new `model_best.pth` and `table_stats.pkl` from the
  retraining run. Any local path is fine — they only need to exist for
  upload.
  재학습 실행에서 새 두 파일을 수집. 업로드 가능하면 아무 로컬 경로나 가능.
- **They must be a matched pair.** Mismatched files (different
  training runs) cause silently miscalibrated forecasts; there is no
  runtime check that enforces the pairing. See
  [runtime-invariants.md §3](https://github.com/njit-research/ap-prediction/blob/main/docs/realtime-regression-sw/runtime-invariants.md#normalization-coupling).
  **반드시 매칭 페어**여야 함. 불일치 시 조용히 miscalibrated 예측이 나오며,
  런타임 검증이 없음.

#### Step ② — create a new Release / 새 Release 생성

Open **https://github.com/njit-research/ap-prediction/releases/new**
and fill in:

| Field | Value |
|-------|-------|
| Tag | **A brand new tag**, e.g. `v0.2.0-assets`. Never reuse the old tag. |
| Target | `main` (or whichever commit the training code corresponds to) |
| Title | `v0.2.0 runtime assets` (any descriptive string) |
| Description | (optional) training data range, val-MAE, hyperparams |
| Attach binaries | Drag-drop `model_best.pth` and `table_stats.pkl` |

| 필드 | 값 |
|------|---|
| Tag | **새로운 태그** (예: `v0.2.0-assets`). 기존 태그 재사용 금지 |
| Target | `main` (또는 학습 코드 시점의 커밋) |
| Title | 자유롭게 (예: `v0.2.0 runtime assets`) |
| Description | (선택) 학습 데이터 범위, val-MAE, 하이퍼파라미터 |
| Attach binaries | `model_best.pth` + `table_stats.pkl` 드래그 드롭 |

Click **Publish release**.

> ⚠️ **Never reuse the existing tag.** The CI cache key is
> `release-${ASSETS_TAG}` — overwriting assets under the same tag does
> not invalidate the cache, so the old files would keep being served
> indefinitely. Always create a new tag.
>
> ⚠️ **기존 태그 재사용 절대 금지.** CI 캐시 키가 `release-${ASSETS_TAG}`라
> 같은 태그에 파일만 덮어써도 캐시가 갱신되지 않아 구 파일이 계속 사용됨.
> 반드시 새 태그 생성.

#### Step ③ — bump `ASSETS_TAG` / ASSETS_TAG 값 변경

Edit `.github/workflows/forecast.yml` in this repo:

```yaml
env:
  ASSETS_TAG: v0.1.0-assets     # old → new
  REALTIME_REPO: njit-research/ap-prediction
```

becomes / 변경 후:

```yaml
env:
  ASSETS_TAG: v0.2.0-assets
  REALTIME_REPO: njit-research/ap-prediction
```

This is the only line that needs to change.
변경이 필요한 유일한 라인입니다.

#### Step ④ — commit and push / 커밋 + 푸시

```bash
cd ap-prediction
git add .github/workflows/forecast.yml
git commit -m "Bump ASSETS_TAG to v0.2.0-assets"
git push
```

#### Step ⑤ — manually trigger and verify / 수동 실행 + 검증

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

단계별:
1. Actions → Forecast → **Run workflow** (`now` 빈 칸, **Run workflow** 확정)
2. 1–2분 대기. 캐시 키가 바뀌어 `Download checkpoint + stats` 단계가 실행되는지
   로그에서 확인.
3. 성공 후 페이지 하드 리프레시.
4. 메타데이터 블록의 **Checkpoint SHA**가 새 값으로 바뀌었는지 확인.

#### Cache invalidation, explained / 캐시 무효화 원리

The workflow caches the `checkpoint/` directory using
`actions/cache@v4` with the key `release-${{ env.ASSETS_TAG }}`. The
cache is a key-value store, keyed on the literal string:

워크플로는 `checkpoint/` 디렉토리를 `actions/cache@v4`로 캐싱하며, 키는
`release-${{ env.ASSETS_TAG }}` 문자열 그대로입니다:

- `ASSETS_TAG=v0.1.0-assets` → key `release-v0.1.0-assets`
- `ASSETS_TAG=v0.2.0-assets` → key `release-v0.2.0-assets` (brand new,
  forces fresh download)

So **changing the tag string is the mechanism that invalidates the
cache.** You do not need to manually clear anything.

**태그 문자열 변경 자체가 캐시 무효화의 트리거**이며, 별도 캐시 삭제 작업
불필요.

#### Rolling back / 롤백

If the new model misbehaves, reverting is symmetric:

문제 발생 시 롤백은 대칭적입니다:

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

Release를 삭제하지 않는 한 즉시 롤백 가능. 교체 후 몇 주기 동안은 구 Release를
유지 권장.

#### Code changes alongside the weights / 모델 코드도 바뀐 경우

If the retraining also changed the `realtime-regression-sw` source code
(new architecture, different variable order, etc.), advance the
submodule pin as well:

재학습 시 `realtime-regression-sw`의 코드(아키텍처, 변수 순서 등)도 함께
바뀌었다면 submodule pin도 이동시키세요:

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

가중치만 바뀐 경우(코드 동일)에는 submodule 갱신 불필요.

#### Does the config need to change? / Config는 수정해야 하나?

Short answer: **usually no**. The `configs/realtime.ci.yaml` file
describes the *model architecture* and the *runtime environment*, not
the trained weights themselves. So a simple "retrain on more data with
the same architecture" does **not** require touching the config.

짧은 답: **보통 불필요**. `configs/realtime.ci.yaml`은 **모델 아키텍처**와
**런타임 환경**을 기술하는 파일이지, 가중치 자체를 기술하지 않습니다. 따라서
동일 아키텍처로 데이터만 더 넣어서 재학습한 경우에는 config를 손댈 필요가
**없습니다**.

Here is the full table, by config section:
섹션별 전체 표:

| Section | Weights only (same architecture) | Architecture/profile change |
|---------|:-:|:-:|
| `profile.*`               | unchanged 그대로 | **must update 필수** |
| `experiment.name`         | unchanged 그대로 | **must update 필수** |
| `paths.*`                 | unchanged 그대로 | unchanged 그대로 |
| `sources.*`               | unchanged 그대로 | unchanged 그대로 |
| `window.lookback_steps`   | unchanged 그대로 | **update if input length changes 입력 길이 변경 시 필수** |
| `window.forecast_steps`   | unchanged 그대로 | **update if output length changes 출력 길이 변경 시 필수** |
| `window.boundary_offset_minutes` | unchanged 그대로 | unchanged 그대로 |
| `runtime.*`               | unchanged 그대로 | unchanged 그대로 |
| `analysis.*`              | unchanged 그대로 | unchanged 그대로 |
| `model_provenance.*`      | ⚠️ **recommended 권장** (display-only) | **must update 필수** |

**Why `model_provenance.*` is "recommended" even for same-architecture
retraining / 동일 아키텍처 재학습에서도 `model_provenance.*` 갱신이 권장되는
이유**

These three values (`val_loss_at_train`, `val_mae_at_train`,
`val_rmse_at_train`) are read from config at inference time and copied
into the output JSON's `model` block (see
[run_realtime.py:308](https://github.com/njit-research/ap-prediction/blob/main/scripts/run_realtime.py#L308)).
The page renders `val_mae_at_train` under "Val-MAE at train" in the
metadata block. If you ship new weights without updating these, the
page will display the **old** MAE alongside the **new** forecasts —
functionally fine but misleading for visitors.

이 세 값은 추론 시 config에서 읽혀 출력 JSON의 `model` 블록에 그대로 복사되고
([run_realtime.py:308](https://github.com/njit-research/ap-prediction/blob/main/scripts/run_realtime.py#L308)),
페이지의 "Val-MAE at train"에 표시됩니다. 새 가중치를 적용하면서 이 값을
갱신하지 않으면 **옛 MAE가 새 예측과 함께 표시**됨 — 기능 문제는 없지만 방문자
입장에서 오해 소지.

**Concrete examples / 구체적 예시**

- *Retrained `in2d_out12h_gnn_transformer` with 6 more months of data*
  → no structural config change; optionally update
  `model_provenance.*` to the new training metrics.
  동일 아키텍처 `in2d_out12h_gnn_transformer`에 데이터 6개월 추가해서 재학습
  → 구조 변경 없음. `model_provenance.*`만 선택적 갱신.
- *Switched profile from `in2d_out12h_gnn_transformer` to
  `in12h_out12h_gnn_patchtst`* → must update `profile.*`,
  `experiment.name`, `window.lookback_steps` (96 → 24), and
  `model_provenance.*`.
  프로파일을 `in2d_out12h_gnn_transformer` → `in12h_out12h_gnn_patchtst`로
  전환 → `profile.*`, `experiment.name`, `window.lookback_steps`(96 → 24),
  `model_provenance.*` 모두 갱신 필요.
- *Changed forecast horizon from 12h to 6h (new profile
  `in2d_out6h_gnn_transformer`)* → update `profile.*`,
  `experiment.name`, `window.forecast_steps` (24 → 12), and
  `model_provenance.*`.
  예측 horizon을 12h → 6h로 변경 → `profile.*`, `experiment.name`,
  `window.forecast_steps`(24 → 12), `model_provenance.*` 갱신.

**Atomicity / 원자성**

When the architecture changes, commit the config change **together
with** the `ASSETS_TAG` bump in a single commit. Otherwise the
workflow will temporarily try to run new weights against the old
architecture (or vice versa) and crash.

아키텍처가 바뀌는 경우에는 config 변경과 `ASSETS_TAG` 변경을 **같은 커밋에
원자적으로** 반영하세요. 그렇지 않으면 워크플로가 잠시 신·구 불일치 상태에서
크래시합니다.

#### Common pitfalls / 흔한 실수

| Pitfall | Symptom | Fix |
|---------|---------|-----|
| Reused old tag | New weights never take effect; Checkpoint SHA unchanged | Delete the reused-tag Release, recreate with a new tag, bump ASSETS_TAG again |
| Forgot to change ASSETS_TAG | Same symptom | Bump `ASSETS_TAG` to the new tag and push |
| Uploaded only the .pth, not the .pkl | Inference fails with stats-file not-found error | Edit the Release, attach the missing `table_stats.pkl` |
| Mismatched pair (different training runs) | Inference succeeds, but predictions look off (systematic bias) | Re-upload the correct matching pair as a new tag |
| Submodule advanced but ASSETS_TAG not bumped | Inference may crash if the new code expects different input features than the old weights | Align: bump ASSETS_TAG to a Release whose weights match the submodule's code |

| 실수 | 증상 | 해결 |
|------|------|-----|
| 기존 태그 재사용 | 새 가중치 반영 안 됨, Checkpoint SHA 불변 | 해당 Release 삭제, 새 태그로 재생성, ASSETS_TAG 다시 갱신 |
| ASSETS_TAG 변경 누락 | 동일 증상 | `ASSETS_TAG`를 새 태그로 변경 후 푸시 |
| .pth만 업로드, .pkl 누락 | stats-file not-found 에러로 추론 실패 | Release 편집, `table_stats.pkl` 추가 첨부 |
| 페어 불일치 (다른 학습 실행) | 추론 성공하지만 예측에 체계적 편향 | 올바른 페어로 새 태그 재업로드 |
| Submodule은 갱신했는데 ASSETS_TAG는 그대로 | 새 코드가 구 가중치와 입력 feature가 달라 추론 크래시 가능 | submodule과 일치하는 가중치를 가진 Release로 ASSETS_TAG 정렬 |

---

## 6. GitHub Pages deployment / GitHub Pages 배포

### 6.1 "Actions" source vs branch source / 배포 모드

We use **Source: GitHub Actions** (not "Deploy from a branch"). This
means:
**Source: GitHub Actions**를 사용. 의미:

- No `gh-pages` branch exists. Publishing is done by uploading a Pages
  artifact (`actions/upload-pages-artifact@v3`) and then calling
  `actions/deploy-pages@v4`.
  `gh-pages` 브랜치 없음. Pages 아티팩트 업로드 + `deploy-pages`로 배포.
- Each run re-deploys the full `site/` directory. This keeps the build
  deterministic and means `main` branch history is not mixed with a
  parallel `gh-pages` history.
  매 실행이 `site/` 전체를 재배포. 빌드가 결정적이며 `main` 히스토리가
  `gh-pages` 병행 히스토리와 섞이지 않음.

### 6.2 URL resolution / URL 작동 원리

The repo name `ap-prediction` becomes the URL path:

`github.com/njit-research/ap-prediction` repo name (`ap-prediction`)
→ project Pages URL path (`/ap-prediction/`).

레포 이름이 URL 경로가 됩니다.

Because the user account has a user-page repo (`eunsu-park.github.io`)
with a custom domain (`CNAME = www.eunsu.me`), the custom domain is
**automatically inherited** by all project Pages. Therefore both of the
following URLs serve the same content:

사용자 페이지 레포(`eunsu-park.github.io`)에 커스텀 도메인(`CNAME =
www.eunsu.me`)이 설정되어 있어, 모든 프로젝트 페이지가 자동으로 커스텀
도메인을 **상속**받습니다. 따라서 아래 두 URL이 동일 콘텐츠 제공:

- Primary: `https://sites.njit.edu/ap-prediction/`
- Alias: `https://njit-research.github.io/ap-prediction/` (301 redirects
  to the primary)

### 6.3 Cache behavior / 캐시 동작

- JSON files (`latest.json`, `status.json`) are fetched with
  `cache: "no-store"` in `main.js`, so browsers always request a fresh
  copy.
  JSON 파일은 `cache: "no-store"`로 항상 새로 요청.
- HTML and JS files (`index.html`, `main.js`) use GitHub Pages' default
  cache headers. The browser may cache them aggressively — if the page
  visibly lags behind, a hard refresh (`Cmd+Shift+R` / `Ctrl+F5`)
  forces a fresh pull.
  HTML/JS는 기본 캐시 헤더 사용. 강제 새로고침으로 최신화 가능.

---

## 7. Homepage integration / 메인 사이트 연동

The main site (`www.eunsu.me`) is a Jekyll blog in
`github.com/eunsu-park/eunsu-park.github.io`. Integration is **one
line** in `_includes/navigation.html`:

메인 사이트는 `eunsu-park/eunsu-park.github.io`의 Jekyll 블로그. 연동은
`_includes/navigation.html`에 **한 줄** 추가로 완료:

```html
<li><a href="{{ site.baseurl }}/ap-prediction">
  <i class="fas fa-chart-line"></i> AP Forecast
</a></li>
```

**How the link actually works / 링크 동작 원리**

1. Jekyll renders `{{ site.baseurl }}/ap-prediction` → `/ap-prediction`
   (since `baseurl` is empty in `_config.yml`).
   Jekyll이 `{{ site.baseurl }}/ap-prediction`을 `/ap-prediction`으로 렌더링.
2. Browser clicks on `<a href="/ap-prediction">` → navigates to
   `https://sites.njit.edu/ap-prediction`.
   브라우저가 `<a href="/ap-prediction">` 클릭 → `https://sites.njit.edu/ap-prediction`으로 이동.
3. GitHub Pages receives the request for `/ap-prediction/` and serves
   the content from the `ap-prediction` project Pages artifact (i.e.
   the `site/` directory this repo publishes).
   GitHub Pages가 `/ap-prediction/` 요청을 받아 본 레포의 project Pages 아티팩트에서 콘텐츠 제공.

Nothing else is shared between the two sites — no CSS, no JavaScript,
no layout. They just happen to live under the same domain.

두 사이트 간 CSS, JS, 레이아웃 공유 없음 — 같은 도메인 아래 경로만 공유.

---

## 8. Files & responsibilities / 파일과 책임

### 8.1 In `ap-prediction` / 본 레포

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

### 8.2 `latest.json` schema / 스키마

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

### 8.3 `status.json` schema / 스키마

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

## 9. Cost and quota / 비용과 할당량

- GitHub Actions Linux runner minutes are **unlimited and free for
  public repos**. Our 30-min cron uses ~720 minutes per month; cost is
  $0.
  공개 레포의 Linux 러너 분은 **무제한 무료**. 30분 주기 × 월 약 720분, 비용
  $0.
- GitHub Pages bandwidth: 100 GB/month soft limit per user. Our static
  site is a few hundred KB; nowhere near the limit.
  GitHub Pages 대역폭: 사용자당 월 100 GB soft limit. 정적 사이트가 수백 KB
  수준이라 한도와 무관.
- NOAA and GFZ feeds are unauthenticated public JSON/text; no API key
  or quota to worry about.
  NOAA와 GFZ 피드는 비인증 공개 JSON/텍스트; API 키나 할당량 걱정 없음.

---

## 10. Known limitations / 알려진 한계

1. **Scheduler drift** — GitHub Actions cron is best-effort. A run
   scheduled for 14:33 UTC may actually start anywhere from 14:33 to
   15:00+. The anchor computation handles this gracefully by always
   aligning to the most recent 30-min boundary, but the "last updated"
   timestamp on the page reflects the actual run time, not the slot
   time.
   GitHub Actions cron은 best-effort. 14:33 예정이 14:33~15:00 사이 언제든
   시작 가능. Anchor는 항상 최근 30분 경계 정렬로 대응하지만, 페이지의
   "last updated"는 실제 실행 시각을 반영.
2. **Public weights exposure** — `model_best.pth` is posted as a public
   Release asset. Anyone can download and reuse the weights. Acceptable
   for this project (academic/personal); if sensitivity ever changes,
   move the Release to a private repo and add a fine-grained PAT to the
   workflow.
   공개 가중치 노출 — `model_best.pth`는 공개 Release로 게시됨. 민감도가
   변경되면 private 레포 + fine-grained PAT로 전환 가능.
3. **Single-point failure on stats-checkpoint pairing** — if the
   `ASSETS_TAG` env and the actual Release contents diverge (e.g. you
   upload a new `.pth` but forget to upload a matching `.pkl`), the
   model will silently produce miscalibrated outputs. There is no
   runtime check that the two match.
   stats-checkpoint 페어 단일 실패점 — `ASSETS_TAG` env와 실제 Release 내용이
   어긋나면(예: 새 `.pth` 업로드 시 `.pkl`을 빠뜨림) 조용히 miscalibrated
   출력. 런타임 검증 없음.
4. **No historical archive on the page** — `latest.json` is the only
   data the page shows. Past forecasts are not accessible from the UI
   (they still exist in git history of `site/data/latest.json`).
   페이지 히스토리 아카이브 없음 — `latest.json`이 유일한 데이터. 과거 예측은
   UI에서 접근 불가(git 히스토리에는 존재).

---

## 11. Extending the dashboard / 대시보드 확장 가이드

Candidate next steps, in rough order of effort:
다음 확장 후보 (대략적 난이도 순):

1. **MCD uncertainty band** — `run_realtime.py` already computes Monte
   Carlo Dropout samples (disabled in `configs/realtime.ci.yaml`
   `analysis.mcd.enable: false`). Enable it, propagate the `lower` /
   `upper` arrays into `latest.json`, and add a shaded band dataset in
   `main.js`. Minor Chart.js work.
   MCD 불확실성 밴드 — 이미 계산 가능. 설정만 활성화하고 JSON 전파 후 음영대
   렌더링.
2. **Historical accuracy view** — archive each run's `latest.json` to
   `site/data/history/YYYYMMDD.json`, plus a rolling `history.json`
   index. The page adds a secondary chart: "forecast-vs-realized MAE
   over the last 7 days".
   과거 정확도 뷰 — 매 실행의 예측을 보관하고, 7일 롤링 MAE 차트 추가.
3. **hp30 as a second target** — currently only ap30 is on the page.
   The model also has variants predicting hp30 directly. Add a second
   line to the chart with a toggle.
   hp30 이중 타겟 — ap30만 표시 중. hp30 예측 라인 추가.
4. **Attention heatmap** — `plot_attention` exists in the sibling repo
   but emits PNG. For interactive use, serialize attention weights to
   JSON and render with a canvas heatmap library.
   Attention 히트맵 — 현재 PNG 생성. JSON 직렬화 후 인터랙티브 히트맵 렌더링.

Each of these would be additive — none require restructuring the
current pipeline.
모두 가산적 변경 — 현재 파이프라인 재구조화 불필요.
