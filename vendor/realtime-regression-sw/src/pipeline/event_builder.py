"""Write the aligned lookback window as a training-schema event CSV."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Sequence

import pandas as pd

logger = logging.getLogger(__name__)


def build_event_csv(
    aligned: pd.DataFrame,
    t_end: pd.Timestamp,
    out_dir: Path,
    input_variables: Sequence[str],
) -> Path:
    """Persist the aligned window to `{out_dir}/{t_end:%Y%m%d%H%M%S}.csv`.

    The CSV schema matches the training event format: `datetime` + 21 solar
    wind parameters + `ap30` + `hp30`. Column order matches the training
    `input_variables` list so downstream consumers can slice by position.

    Args:
        aligned: Aligned window from `pipeline.align.align`.
        t_end: Anchor timestamp used to name the file.
        out_dir: Destination directory (created if missing).
        input_variables: Ordered list of SW + ap30 variables from base.yaml.

    Returns:
        Path to the written CSV.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    expected_cols = ["datetime", *input_variables, "hp30"]
    missing = [col for col in expected_cols if col not in aligned.columns]
    if missing:
        raise ValueError(f"Aligned frame is missing columns required for CSV: {missing}")

    ordered = aligned[expected_cols].copy()
    ordered["datetime"] = pd.to_datetime(ordered["datetime"]).dt.strftime("%Y-%m-%d %H:%M:%S")

    filename = f"{t_end.strftime('%Y%m%d%H%M%S')}.csv"
    out_path = out_dir / filename
    ordered.to_csv(out_path, index=False)
    logger.info("Wrote event CSV %s (%d rows, %d cols)",
                out_path, len(ordered), len(ordered.columns))
    return out_path
