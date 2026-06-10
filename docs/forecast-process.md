# Forecast Generation Process / 예보 생성 과정

How each `ap30` forecast is produced end-to-end, including how missing or late
upstream data is handled. This is the canonical reference for the runtime
behaviour; see [architecture.md](architecture.md) for the overall system design.

각 `ap30` 예보가 어떻게 생성되는지를 처음부터 끝까지 설명하며, 업스트림 데이터가
결측되거나 늦게 들어올 때의 처리까지 포함한다. 런타임 동작의 기준 문서이며, 전체
시스템 설계는 [architecture.md](architecture.md)를 참고한다.

## At a glance / 한눈에 보기

```
trigger (cron, 3 attempts/anchor)
   → fetch NOAA + GFZ           ── fetch fails ─┐
   → aggregate + align (impute) ── unfillable ──┤
   → model + MCD uncertainty                    │
   → write forecast                             │
   → classify: ok / imputed / failed ◄──────────┘ (failed)
   → publish (don't-downgrade) + deploy page
```

## 1. Trigger / 트리거

- A GitHub Actions schedule runs the pipeline every 10 minutes:
  `cron: '8,18,28,38,48,58 * * * *'`.
- Each 30-minute anchor gets **three attempts**:
  - anchor `:00` → runs at `:08`, `:18`, `:28`
  - anchor `:30` → runs at `:38`, `:48`, `:58`
- A run can also be started manually (Actions → **Forecast** → **Run workflow**).
- **Caveat:** GitHub schedules are best-effort and may be delayed or dropped
  under load, so not all three attempts always fire.

- GitHub Actions 스케줄이 10분마다 파이프라인을 실행한다
  (`cron: '8,18,28,38,48,58 * * * *'`).
- 각 30분 anchor는 **3회 시도**한다: anchor `:00` → `:08`/`:18`/`:28`,
  anchor `:30` → `:38`/`:48`/`:58`.
- 수동 실행도 가능하다(Actions → **Forecast** → **Run workflow**).
- **주의:** GitHub 예약 실행은 best-effort라 부하 시 지연·드롭될 수 있어 3회가
  항상 다 도는 것은 아니다.

## 2. Data collection / 데이터 수집

The inference CLI (`run_realtime.py`) computes the **anchor**
`t_end = floor(now − 2 min, 30 min)` (all times **UTC**) and fetches the live
feeds, each with up to 3 retries and a 30-second timeout:

- **NOAA SWPC** — solar wind plasma + interplanetary magnetic field.
- **GFZ Potsdam** — Hp30 / ap30 geomagnetic nowcast.

If a feed cannot be retrieved after its retries, the run exits with code **2**
("data gap") and goes to the failure path in step 6.

추론 CLI(`run_realtime.py`)는 **anchor** `t_end = floor(now − 2분, 30분)`
(모든 시각 **UTC**)를 계산하고, 각 피드를 최대 3회 재시도·30초 타임아웃으로 수집한다:
**NOAA SWPC**(태양풍 + 행성간 자기장), **GFZ Potsdam**(Hp30/ap30 지자기 nowcast).
재시도 후에도 받지 못하면 코드 **2**("data gap")로 종료하여 6단계 실패 처리로 간다.

## 3. Preprocessing and missing-data handling / 전처리와 결측 처리

1. Resample the 1-minute feed to 30-minute bins and align onto the 24-step
   (12-hour) input window ending at the anchor.
2. **Impute missing values ("always emit" policy):**
   - **Forward-fill** — carry the last available value forward (up to 48 steps),
     which covers gaps at the recent/tail end of the window.
   - **Linear interpolation** (both directions) for interior gaps.
   - Proceed unless a single variable is **almost entirely** missing
     (`max_gap_fraction = 0.9`); the most-recent steps are **not** required to be
     real (`require_recent_steps_present = 0`).
   - As a last resort, the anchor may roll back by 30 minutes (up to 2 times) if
     the current window cannot be filled at all.
3. If, after imputation, a usable window still cannot be built, the run exits
   with code **2** (failure path).

The fraction of cells that had to be imputed is recorded as
`missing_data_filled_fraction` and drives the status in step 5.

1. 1분 피드를 30분 bin으로 재집계하고, anchor에서 끝나는 24-step(12시간) 입력창에
   정렬한다.
2. **결측값 보간("always emit" 정책):**
   - **forward-fill** — 직전 값을 앞으로 끌어옴(최대 48스텝). 최근/꼬리 결측을 덮음.
   - 내부 구멍은 **선형 보간**(양방향).
   - 한 변수가 **거의 전부** 결측이 아닌 한 진행(`max_gap_fraction = 0.9`). 최근
     스텝이 실측일 것을 **요구하지 않음**(`require_recent_steps_present = 0`).
   - 현재 창을 도저히 못 채우면 최후 수단으로 anchor를 30분씩(최대 2회) 롤백.
3. 보간 후에도 사용 가능한 창을 못 만들면 코드 **2**로 종료(실패 처리).

보간된 셀 비율은 `missing_data_filled_fraction`으로 기록되어 5단계 상태 판정에 쓰인다.

## 4. Inference / 추론

Normalize the window with the training statistics, run the model
(GNN + PatchTST), compute a Monte Carlo Dropout (MCD) uncertainty interval
(±2σ), denormalize, and write the 24-step forecast (JSON + CSV). The run exits
with code **0**.

학습 통계로 입력창을 정규화하고, 모델(GNN + PatchTST)을 실행하며, Monte Carlo
Dropout(MCD) 불확실성 구간(±2σ)을 계산한 뒤 역정규화하여 24-step 예보(JSON + CSV)를
작성한다. 코드 **0**으로 종료한다.

## 5. Status classification / 상태 판정

Each run is classified and the result is recorded with the forecast.
각 실행은 아래와 같이 분류되어 예보와 함께 기록된다.

| Condition / 조건 | Status |
|---|---|
| exit 0 and `missing_data_filled_fraction` ≤ 0.05 | **ok** (normal / 정상) |
| exit 0 and `missing_data_filled_fraction` > 0.05 | **imputed** (보간 예보) |
| non-zero exit (fetch failure / unfillable) | **failed** (예보 실패) |

## 6. Publishing and the retry rule / 발행과 재시도 규칙

`update_site_data.py` records the run:

- **Success (ok / imputed):** write `site/data/latest.json` (with the observed
  history embedded), set `status.json = "ok"`, and append the forecast to the
  per-anchor archives `forecast_history.json` / `.csv` (each carrying a `status`
  field/column).
- **Failure:** **keep the previous `latest.json`** (the page continues to show
  the last good forecast), set `status.json = "warn"` (data gap) or `"error"`,
  and record a **`failed`** marker for the current anchor in the archives.

**Don't-downgrade rule.** Because each anchor is attempted up to three times, a
later attempt only overwrites the stored record for that anchor when its status
is the **same or better** (`ok` > `imputed` > `failed`). So a later transient
failure never clobbers an earlier good forecast, and an `imputed` result is
upgraded to `ok` if a later attempt gets clean data.

`update_site_data.py`가 실행 결과를 기록한다:

- **성공(ok / imputed):** `site/data/latest.json` 작성(관측 history 포함),
  `status.json = "ok"`, 그리고 per-anchor 아카이브 `forecast_history.json` /
  `.csv`에 예보를 추가(각각 `status` 필드/컬럼 포함).
- **실패:** **직전 `latest.json` 유지**(마지막 정상 예보를 계속 표시),
  `status.json = "warn"`(데이터 공백) 또는 `"error"`, 아카이브에 현재 anchor를
  **`failed`** 마커로 기록.

**don't-downgrade 규칙.** 각 anchor는 최대 3회 시도되므로, 나중 시도는 그 status가
**같거나 높을 때만**(`ok` > `imputed` > `failed`) 기존 기록을 덮어쓴다. 즉 나중의
일시적 실패가 앞선 정상 예보를 덮어쓰지 않고, `imputed`는 이후 깨끗한 데이터가
들어오면 `ok`로 업그레이드된다.

## 7. Page display / 페이지 표시

The job itself always succeeds (failures are handled in software), so the static
site is re-deployed every run. The page shows a status **banner** — green
(current), yellow (data gap / stale / heavily imputed), or red (error) — and the
**plot**: observed ap30 history, the current 12-hour forecast with its MCD
uncertainty band, and a vertical "now" divider.

잡 자체는 항상 성공 처리되므로(실패는 소프트웨어에서 처리) 매 실행마다 정적 사이트가
재배포된다. 페이지는 상태 **배너**(초록=현행, 노랑=데이터 공백/오래됨/보간 많음,
빨강=에러)와 **플롯**(관측 ap30 history, 현재 12시간 예보 + MCD 불확실성 밴드,
세로 "now" 구분선)을 보여준다.

## When upstream data is missing — summary / 데이터 결측 시 — 요약

- **Partial gap** (some data fetched): missing values are imputed (forward-fill
  + interpolation) and a forecast is still produced → status **imputed**.
- **Total failure** (feed unreachable, or window unfillable): no forecast is
  produced → exit 2 → the last good forecast is kept on the page (yellow banner)
  and the anchor is recorded as **failed**.
- **Retries**: the same anchor is attempted three times, 10 minutes apart; if
  data returns on a later attempt the slot is filled with `ok` / `imputed` and
  the `failed` marker is overwritten (don't-downgrade).
- **Limit**: if an upstream feed stays unavailable across all attempts, that
  anchor remains `failed` and the previous forecast keeps showing until data
  returns.

- **부분 결측**(일부라도 수집): 결측값을 보간(forward-fill + 보간)해 **예보를 그대로
  생성** → status **imputed**.
- **완전 실패**(피드 unreachable, 또는 창을 못 채움): 예보 불가 → exit 2 → 직전
  정상 예보를 유지(노란 배너)하고 해당 anchor를 **failed**로 기록.
- **재시도**: 같은 anchor를 10분 간격 3회 시도. 나중 시도에 데이터가 돌아오면
  `ok`/`imputed`로 채워지고 `failed` 마커를 덮어씀(don't-downgrade).
- **한계**: 업스트림이 모든 시도에서 계속 불가면 그 anchor는 `failed`로 남고,
  데이터가 돌아올 때까지 직전 예보가 계속 표시된다.

## Notes / 참고

- All times — anchor and data timestamps — are **UTC**.
- This repository runs its own pipeline and accumulates its own data
  independently; a second deployment running the same code will generally be at
  a different anchor at any given moment (independent, best-effort scheduling),
  so the two can show different values even though the code and model are
  identical.

- 모든 시각(anchor·데이터 타임스탬프)은 **UTC**다.
- 이 레포는 자체 파이프라인으로 자기 데이터를 독립적으로 누적한다. 같은 코드를 도는
  다른 배포본도 임의 시점에 보통 서로 다른 anchor에 있으므로(독립·best-effort
  스케줄), 코드·모델이 동일해도 두 곳의 표시값이 다를 수 있다.
