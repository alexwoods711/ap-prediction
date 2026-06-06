"""Fetch and parse NOAA SWPC real-time solar wind JSON feeds.

The NOAA SWPC JSON format stores column headers in row 0 and the data rows in
row 1..N. Plasma and magnetic field are published as separate files at
~1-minute cadence.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from .._vendor.download import download_json

logger = logging.getLogger(__name__)


_PLASMA_RENAME = {
    "density": "np",
    "speed": "v",
    "temperature": "t",
}

_MAG_RENAME = {
    "bx_gsm": "bx",
    "by_gsm": "by",
    "bz_gsm": "bz",
    "bt": "bt",
}


def _rows_to_dataframe(rows: list) -> pd.DataFrame:
    """Convert NOAA header-first list-of-lists into a typed DataFrame."""
    if not rows or len(rows) < 2:
        raise ValueError("NOAA SWPC response did not contain data rows")

    header = [str(c).lower() for c in rows[0]]
    df = pd.DataFrame(rows[1:], columns=header)

    if "time_tag" not in df.columns:
        raise ValueError(f"NOAA response missing 'time_tag' column; got {header}")

    df["datetime"] = pd.to_datetime(df["time_tag"], utc=True).dt.tz_convert(None)
    df = df.drop(columns=["time_tag"])
    return df


def _numeric(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Coerce selected columns to float and treat NOAA sentinel strings as NaN."""
    for col in columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _cache_raw_json(payload: list, cache_dir: Optional[Path], filename: str) -> None:
    """Persist the raw JSON response for reproducibility if a cache dir is set."""
    if cache_dir is None:
        return
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_path = cache_dir / filename
    with out_path.open("w", encoding="utf-8") as fp:
        json.dump(payload, fp)
    logger.debug("Cached raw NOAA response → %s", out_path)


def fetch_plasma(url: str, timeout: int = 30, max_retries: int = 3,
                 cache_dir: Optional[Path] = None) -> pd.DataFrame:
    """Download the NOAA SWPC plasma JSON and return a typed DataFrame.

    Args:
        url: Full URL of plasma-*.json.
        timeout: Request timeout in seconds.
        max_retries: Max retry attempts.
        cache_dir: If given, raw JSON is cached under `cache_dir/plasma.json`.

    Returns:
        DataFrame with columns [datetime, np, v, t] sorted by datetime.
    """
    payload = download_json(url, timeout=timeout, max_retries=max_retries)
    if payload is None:
        raise RuntimeError(f"NOAA plasma download failed: {url}")

    _cache_raw_json(payload, cache_dir, "plasma.json")

    df = _rows_to_dataframe(payload)
    df = df.rename(columns=_PLASMA_RENAME)
    df = _numeric(df, ["np", "v", "t"])
    df = df[["datetime", "np", "v", "t"]].sort_values("datetime").reset_index(drop=True)
    return df


def fetch_mag(url: str, timeout: int = 30, max_retries: int = 3,
              cache_dir: Optional[Path] = None) -> pd.DataFrame:
    """Download the NOAA SWPC magnetic field JSON and return a typed DataFrame.

    Args:
        url: Full URL of mag-*.json.
        timeout: Request timeout in seconds.
        max_retries: Max retry attempts.
        cache_dir: If given, raw JSON is cached under `cache_dir/mag.json`.

    Returns:
        DataFrame with columns [datetime, bx, by, bz, bt] sorted by datetime.
    """
    payload = download_json(url, timeout=timeout, max_retries=max_retries)
    if payload is None:
        raise RuntimeError(f"NOAA mag download failed: {url}")

    _cache_raw_json(payload, cache_dir, "mag.json")

    df = _rows_to_dataframe(payload)
    df = df.rename(columns=_MAG_RENAME)
    df = _numeric(df, ["bx", "by", "bz", "bt"])
    df = df[["datetime", "bx", "by", "bz", "bt"]].sort_values("datetime").reset_index(drop=True)
    return df


def fetch_swpc(plasma_url: str, mag_url: str, timeout: int = 30,
               max_retries: int = 3,
               cache_dir: Optional[Path] = None) -> pd.DataFrame:
    """Fetch both plasma and mag feeds and join on datetime.

    Args:
        plasma_url: NOAA plasma JSON URL.
        mag_url: NOAA mag JSON URL.
        timeout: Request timeout per endpoint.
        max_retries: Max retries per endpoint.
        cache_dir: Optional directory to cache raw responses.

    Returns:
        Outer-joined DataFrame with columns [datetime, v, np, t, bx, by, bz, bt]
        sorted by datetime. Missing measurements are NaN.
    """
    plasma = fetch_plasma(plasma_url, timeout=timeout, max_retries=max_retries,
                          cache_dir=cache_dir)
    mag = fetch_mag(mag_url, timeout=timeout, max_retries=max_retries,
                    cache_dir=cache_dir)

    merged = plasma.merge(mag, on="datetime", how="outer")
    merged = merged.sort_values("datetime").reset_index(drop=True)

    ordered = ["datetime", "v", "np", "t", "bx", "by", "bz", "bt"]
    for col in ordered:
        if col not in merged.columns:
            merged[col] = np.nan
    merged = merged[ordered]

    logger.info("NOAA SWPC fetched: %d rows, %s → %s",
                len(merged), merged["datetime"].min(), merged["datetime"].max())
    return merged
