"""Copy the latest realtime forecast JSON into the site data directory.

Runs after `vendor/realtime-regression-sw/scripts/run_realtime.py` inside the
GitHub Actions workflow. On success, the newest JSON under
`vendor/realtime-regression-sw/results/predictions/YYYYMMDD/` is copied to
`site/data/latest.json` and `site/data/status.json` is refreshed with
`status="ok"`. On failure (non-zero inference exit code), `latest.json` is
preserved as-is and `status.json` records the failure so the page can surface
a warning banner.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = REPO_ROOT / "vendor" / "realtime-regression-sw" / "results"
EVENTS_DIR = REPO_ROOT / "vendor" / "realtime-regression-sw" / "dataset" / "events"
SITE_DATA_DIR = REPO_ROOT / "site" / "data"
HISTORY_STEPS = 96  # 48 hours at 30-min cadence, matches the input window

# Past-forecast archives written alongside latest.json so the page can plot
# previously issued forecasts against observations (REFM-style) and offer a
# monthly CSV download.
FORECAST_HISTORY_JSON = SITE_DATA_DIR / "forecast_history.json"
FORECAST_HISTORY_CSV = SITE_DATA_DIR / "forecast_history.csv"
PLOT_HISTORY_HOURS = 48     # rolling window kept in the plot archive (JSON)
CSV_HISTORY_DAYS = 30       # rolling window kept in the monthly archive (CSV)
STEP_MINUTES = 30           # forecast cadence
# ap30 is a discrete index (scale min gap = 1); one decimal place recovers the
# nearest level and stays well below model error, while keeping the CSV compact.
AP_DECIMALS = 1


def _iso_now() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_status() -> dict:
    status_path = SITE_DATA_DIR / "status.json"
    if status_path.exists():
        with status_path.open("r", encoding="utf-8") as fp:
            return json.load(fp)
    return {
        "status": "unknown",
        "last_success_utc": None,
        "last_attempt_utc": None,
        "last_error": None,
    }


def _save_status(status: dict) -> None:
    SITE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    with (SITE_DATA_DIR / "status.json").open("w", encoding="utf-8") as fp:
        json.dump(status, fp, indent=2, ensure_ascii=False)


def _find_latest_prediction() -> Path | None:
    if not RESULTS_DIR.exists():
        return None
    candidates = sorted(RESULTS_DIR.rglob("*.json"))
    return candidates[-1] if candidates else None


def _locate_event_csv(data: dict) -> Path | None:
    """Find the event CSV referenced by the forecast JSON.

    Prefers the absolute path recorded in `input.event_csv`, falls back to
    `dataset/events/{anchor_stem}.csv` under the submodule.
    """
    recorded = data.get("input", {}).get("event_csv")
    if recorded:
        p = Path(recorded)
        if p.exists():
            return p
    anchor = data.get("anchor_timestamp_utc", "")
    if anchor:
        stem = anchor.replace("-", "").replace(":", "").replace("T", "").rstrip("Z")[:14]
        fallback = EVENTS_DIR / f"{stem}.csv"
        if fallback.exists():
            return fallback
    return None


def _load_history(event_csv: Path, steps: int = HISTORY_STEPS) -> list[dict]:
    """Return the trailing `steps` rows of the event CSV as (timestamp, ap30)."""
    import pandas as pd  # deferred import — only needed on success

    df = pd.read_csv(event_csv, parse_dates=["datetime"])
    tail = df.tail(steps)
    entries: list[dict] = []
    for _, row in tail.iterrows():
        value = row["ap30"]
        if pd.isna(value):
            continue
        ts = row["datetime"]
        entries.append({
            "timestamp_utc": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "ap30": float(value),
        })
    return entries


def _error_label(exit_code: int) -> tuple[str, str]:
    """Map realtime CLI exit code to a banner status + human message."""
    if exit_code == 0:
        return "ok", ""
    if exit_code == 2:
        return "warn", "InsufficientDataError — upstream data gap, waiting for next cycle."
    return "error", f"Inference exited with code {exit_code}."


def _parse_iso(value: str) -> datetime:
    """Parse a `YYYY-MM-DDTHH:MM:SSZ` timestamp into an aware UTC datetime."""
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _fmt_iso(dt: datetime) -> str:
    """Format an aware datetime as `YYYY-MM-DDTHH:MM:SSZ`."""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_float(value) -> float:
    """Parse a value to float, returning 0.0 on missing/invalid input."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _update_forecast_history(data: dict) -> None:
    """Append the run's first-frame (+30 min) forecast to the plot archive.

    Maintains `site/data/forecast_history.json` as a continuous 30-min grid over
    the last `PLOT_HISTORY_HOURS`, where each slot holds the first-horizon ap30
    the model predicted for that target time together with its MCD prediction
    interval (`lower`/`upper`). Slots without a recorded forecast are 0-filled; on
    reload a stored 0 is treated as empty (the regression model practically never
    emits exactly 0.0).

    Args:
        data: The forecast JSON payload (already loaded from the latest run).
    """
    forecast = data.get("forecast") or []
    if not forecast:
        return
    first = forecast[0]
    try:
        target = _parse_iso(first["target_timestamp_utc"])
    except (KeyError, ValueError):
        return

    # First-horizon MCD prediction interval, if the run produced one.
    mcd = (data.get("analysis") or {}).get("mcd") or {}

    def _bound(key: str):
        arr = mcd.get(key) or []
        return round(float(arr[0]), AP_DECIMALS) if arr else None

    known: dict[str, dict] = {}
    if FORECAST_HISTORY_JSON.exists():
        try:
            for entry in json.loads(FORECAST_HISTORY_JSON.read_text(encoding="utf-8")):
                value = float(entry.get("ap30", 0) or 0)
                if value:
                    known[entry["target_timestamp_utc"]] = {
                        "ap30": value,
                        "lower": entry.get("lower"),
                        "upper": entry.get("upper"),
                    }
        except (ValueError, KeyError):
            pass
    known[_fmt_iso(target)] = {
        "ap30": round(float(first["ap30"]), AP_DECIMALS),
        "lower": _bound("lower"),
        "upper": _bound("upper"),
    }

    step = timedelta(minutes=STEP_MINUTES)
    cursor = target - timedelta(hours=PLOT_HISTORY_HOURS)
    grid: list[dict] = []
    while cursor <= target:
        iso = _fmt_iso(cursor)
        rec = known.get(iso)
        if rec:
            grid.append({"target_timestamp_utc": iso, "ap30": rec["ap30"],
                         "lower": rec["lower"], "upper": rec["upper"]})
        else:
            grid.append({"target_timestamp_utc": iso, "ap30": 0,
                         "lower": 0, "upper": 0})
        cursor += step

    FORECAST_HISTORY_JSON.write_text(
        json.dumps(grid, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _update_forecast_csv(data: dict) -> None:
    """Append the run's forecast to the monthly wide-format CSV archive.

    Maintains `site/data/forecast_history.csv` as a rolling `CSV_HISTORY_DAYS`
    grid with one row per anchor and one ap30 column per horizon
    (`m_30 … m_720`, the lead time in minutes). Anchors not yet produced are
    0-filled and replaced by real values as forecasts run. Only ap30 is stored;
    each target time is recoverable from the anchor plus the column lead time.

    Args:
        data: The forecast JSON payload (already loaded from the latest run).
    """
    forecast = data.get("forecast") or []
    if not forecast:
        return
    try:
        anchor = _parse_iso(data["anchor_timestamp_utc"])
    except (KeyError, ValueError):
        return
    horizons = len(forecast)
    lead_cols = [f"m_{h * STEP_MINUTES}" for h in range(1, horizons + 1)]
    columns = ["anchor_timestamp_utc", *lead_cols]

    # Load existing rows into {anchor_iso: [ap30 per horizon]}; drop all-zero rows.
    known: dict[str, list[float]] = {}
    if FORECAST_HISTORY_CSV.exists():
        try:
            with FORECAST_HISTORY_CSV.open("r", encoding="utf-8", newline="") as fp:
                for row in csv.DictReader(fp):
                    values = [_safe_float(row.get(c)) for c in lead_cols]
                    if any(values):
                        known[row["anchor_timestamp_utc"]] = values
        except OSError:
            pass

    # Add the current run's row (ap30 per horizon, rounded to AP_DECIMALS).
    current = [0.0] * horizons
    for entry in forecast:
        h = int(entry["horizon_steps"])
        if 1 <= h <= horizons:
            current[h - 1] = round(float(entry["ap30"]), AP_DECIMALS)
    known[data["anchor_timestamp_utc"]] = current

    # Regenerate the rolling 30-day grid, 0-filling anchors with no forecast.
    step = timedelta(minutes=STEP_MINUTES)
    cursor = anchor - timedelta(days=CSV_HISTORY_DAYS)
    rows: list[dict] = []
    while cursor <= anchor:
        anchor_iso = _fmt_iso(cursor)
        values = known.get(anchor_iso, [0.0] * horizons)
        row = {"anchor_timestamp_utc": anchor_iso}
        for col, value in zip(lead_cols, values):
            row[col] = f"{value:.{AP_DECIMALS}f}"
        rows.append(row)
        cursor += step

    with FORECAST_HISTORY_CSV.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exit-code", type=int, required=True,
                        help="Exit code from run_realtime.py in the workflow.")
    args = parser.parse_args()

    now_iso = _iso_now()
    status = _load_status()
    status["last_attempt_utc"] = now_iso

    label, message = _error_label(args.exit_code)

    if args.exit_code == 0:
        latest = _find_latest_prediction()
        if latest is None:
            status["status"] = "error"
            status["last_error"] = {
                "code": 0,
                "message": "Inference reported success but no JSON output was found.",
            }
            _save_status(status)
            print("WARN: no prediction JSON located; status=error written.", file=sys.stderr)
            return 0

        with latest.open("r", encoding="utf-8") as fp:
            data = json.load(fp)

        event_csv = _locate_event_csv(data)
        if event_csv is not None:
            data["history"] = _load_history(event_csv)
        else:
            data["history"] = []
            print(f"WARN: event CSV not found; history omitted.", file=sys.stderr)

        SITE_DATA_DIR.mkdir(parents=True, exist_ok=True)
        dest = SITE_DATA_DIR / "latest.json"
        with dest.open("w", encoding="utf-8") as fp:
            json.dump(data, fp, indent=2, ensure_ascii=False)
        print(f"Wrote {dest} (forecast={len(data['forecast'])}, history={len(data['history'])})")

        # Past-forecast archives are a non-critical add-on: never let a failure
        # here break publishing of the primary latest.json / status.json.
        try:
            _update_forecast_history(data)
            _update_forecast_csv(data)
        except Exception as exc:  # noqa: BLE001 - defensive, archive is optional
            print(f"WARN: forecast archive update failed: {exc}", file=sys.stderr)

        status["status"] = "ok"
        status["last_success_utc"] = now_iso
        status["last_error"] = None
    else:
        status["status"] = label
        status["last_error"] = {"code": args.exit_code, "message": message}
        print(f"Inference failed (exit={args.exit_code}); preserving previous latest.json.",
              file=sys.stderr)

    _save_status(status)
    return 0


if __name__ == "__main__":
    sys.exit(main())
