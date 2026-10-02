"""Strict, read-only identities for continuous and representative time axes.

Weights multiply accounting intervals, never dynamic time steps.  This module
does not infer a heating season, link independent periods, or pad missing hours.
Existing frozen experiments are registered rather than rewritten.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

import numpy as np
import pandas as pd


def _aware(value: Any) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if pd.isna(stamp) or stamp.tzinfo is None:
        raise ValueError("time must contain explicit timezone-aware timestamps")
    return stamp.tz_convert("UTC")


def describe_time_axis(
    frame: pd.DataFrame, *, kind: str, dt_hours: float,
    time_column: str = "time", period_column: str | None = None,
    weight_column: str | None = "period_weight",
    expected_start: str | None = None, expected_end: str | None = None,
) -> dict:
    """Validate one scenario and return a canonical time-only SHA identity.

    ``continuous`` requires a complete ordered sequence and unit weights.
    ``representative`` requires explicit, contiguous, individually chronological
    blocks with constant positive weights. Its weighted hours are not a physical
    continuous horizon. All times denote interval starts, with half-open ends.
    """
    if kind not in {"continuous", "representative"}:
        raise ValueError("unknown time-axis kind")
    if isinstance(dt_hours, bool) or not math.isfinite(dt_hours) or dt_hours <= 0:
        raise ValueError("dt_hours must be finite and positive")
    step_ns = round(float(dt_hours) * 3_600_000_000_000)
    if step_ns <= 0 or not math.isclose(step_ns / 3_600_000_000_000, dt_hours,
                                      abs_tol=1e-15, rel_tol=0):
        raise ValueError("dt_hours cannot be represented as nanoseconds")
    if frame.empty or time_column not in frame:
        raise ValueError("empty or missing time input")
    stamps = pd.DatetimeIndex([_aware(value) for value in frame[time_column]])
    if not stamps.is_unique:
        raise ValueError("duplicate physical interval starts")
    if len(stamps) > 1 and (np.diff(np.sort(stamps.asi8)) < step_ns).any():
        raise ValueError("physical intervals overlap across time blocks")
    if "interval_end" in frame:
        ends = pd.DatetimeIndex([_aware(value) for value in frame.interval_end])
        if not np.equal(ends.asi8 - stamps.asi8, step_ns).all():
            raise ValueError("interval_end disagrees with the physical step")
    if "time_utc" in frame:
        utc_column = pd.DatetimeIndex([_aware(value) for value in frame.time_utc])
        if not np.equal(utc_column.asi8, stamps.asi8).all():
            raise ValueError("time_utc disagrees with local interval start")
    if "scenario" in frame and (frame.scenario.isna().any() or frame.scenario.nunique() != 1):
        raise ValueError("register scenarios separately")
    if "scenario_probability" in frame:
        probabilities = pd.to_numeric(frame.scenario_probability, errors="raise").to_numpy(float)
        if not np.isfinite(probabilities).all() or not np.equal(probabilities, 1.0).all():
            raise ValueError("this registry requires a single unit-probability scenario")
    if "dt_hours" in frame:
        steps = pd.to_numeric(frame.dt_hours, errors="raise").to_numpy(float)
        if not np.isfinite(steps).all() or not np.equal(steps, dt_hours).all():
            raise ValueError("row duration disagrees with registered physical step")
    weights = (pd.to_numeric(frame[weight_column], errors="raise").to_numpy(float)
               if weight_column and weight_column in frame else np.ones(len(frame)))
    if not np.isfinite(weights).all() or (weights <= 0).any():
        raise ValueError("non-finite or nonpositive accounting weight")
    if kind == "continuous":
        if not np.equal(weights, 1.0).all():
            raise ValueError("weighted representatives are not continuous operation")
        if not stamps.is_monotonic_increasing or (len(stamps) > 1 and
                not np.equal(np.diff(stamps.asi8), step_ns).all()):
            raise ValueError("continuous axis contains a gap, overlap or reordering")
        if "boundary_group" in frame and (frame.boundary_group.isna().any() or
                                           frame.boundary_group.nunique() != 1):
            raise ValueError("independent boundary groups cannot form one continuous axis")
        labels = ["continuous"] * len(frame)
    else:
        if not period_column or period_column not in frame or frame[period_column].isna().any():
            raise ValueError("representative axes require explicit complete period labels")
        if weight_column is None or weight_column not in frame:
            raise ValueError("representative axes require explicit accounting weights")
        labels = frame[period_column].astype(str).tolist()
        if any(not label.strip() for label in labels):
            raise ValueError("empty representative period label")
    blocks = []
    used = set()
    start = 0
    for stop in range(1, len(frame) + 1):
        if stop < len(frame) and labels[stop] == labels[start]:
            continue
        label = labels[start]
        if label in used:
            raise ValueError("representative period appears in disjoint blocks")
        used.add(label)
        sub = stamps[start:stop]
        if not sub.is_monotonic_increasing or (len(sub) > 1 and
                not np.equal(np.diff(sub.asi8), step_ns).all()):
            raise ValueError("missing or reordered physical interval within period")
        if not np.equal(weights[start:stop], weights[start]).all():
            raise ValueError("weights must be constant within a representative period")
        blocks.append({"period": label, "rows": stop - start,
                       "start_utc": sub[0].isoformat(),
                       "end_exclusive_utc": (sub[-1] + pd.Timedelta(step_ns, unit="ns")).isoformat(),
                       "accounting_weight": float(weights[start])})
        start = stop
    actual_start, actual_end = stamps.min(), stamps.max() + pd.Timedelta(step_ns, unit="ns")
    if expected_start is not None and actual_start != _aware(expected_start):
        raise ValueError("registered start disagrees with source axis")
    if expected_end is not None and actual_end != _aware(expected_end):
        raise ValueError("registered end disagrees with source axis")
    weighted_hours = math.fsum(float(value) * dt_hours for value in weights)
    if not math.isfinite(weighted_hours):
        raise ValueError("weighted duration overflow")
    payload = {"schema": "heatplan.time_axis.v1", "kind": kind,
               "utc_ns": [int(value) for value in stamps.asi8], "step_ns": step_ns,
               "blocks": blocks, "weights_hex": [float(value).hex() for value in weights]}
    identity = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                        allow_nan=False).encode("utf-8")).hexdigest()
    return {"schema": payload["schema"], "axis_sha256": identity, "kind": kind,
            "rows": len(frame), "physical_step_hours": float(dt_hours),
            "sampled_interval_hours": len(frame) * float(dt_hours),
            "accounting_weighted_hours": weighted_hours,
            "calendar_envelope_hours": float((actual_end - actual_start).total_seconds() / 3600),
            "continuous_horizon_hours": len(frame) * float(dt_hours) if kind == "continuous" else None,
            "start_utc": actual_start.isoformat(), "end_exclusive_utc": actual_end.isoformat(),
            "interval_convention": "[time,time+physical_step)",
            "state_continuity": "single continuous block" if kind == "continuous" else "not established between representative blocks",
            "full_year_claim": False, "complete_local_heating_season_claim": False,
            "blocks": blocks}


def require_same_continuous_axis(left: dict, right: dict) -> None:
    """Reject comparisons that silently align different physical intervals."""
    if (left.get("kind") != "continuous" or right.get("kind") != "continuous" or
            not left.get("axis_sha256") or left["axis_sha256"] != right.get("axis_sha256")):
        raise ValueError("comparison requires the same validated continuous time axis")
