"""Frozen, actual-day BDG2 benchmark; does not run HeatPlanningModel.

Run from the project root (Python 3.12, numpy/pandas/scipy/sklearn):
  python scripts/bdg2_external_benchmark.py freeze
  python scripts/bdg2_external_benchmark.py run --core-label legacy --core-file docs/execution_20261002/algorithm/scenarios_before.py
  python scripts/bdg2_external_benchmark.py run --core-label new_core --core-file src/heatplan/scenarios.py
  python scripts/bdg2_external_benchmark.py report
Every run verifies the protocol/sample/code/input lock, and uses a frozen core copy.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import platform
import shutil
import sys
from time import perf_counter
import traceback

import numpy as np
import pandas as pd
import scipy
from scipy.stats import wasserstein_distance
import sklearn
import threadpoolctl

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/execution_20261002/external_benchmark"
AUDIT = ROOT / "docs/source_intake_20261002/datasets_batch_03"
DATA = ROOT.parents[1] / "datasets/BDG2"
CATEGORIES = ("hotwater", "steam", "chilledwater")
SETTINGS = {"seed": 42, "base_k": 6, "repeats": 3, "threads": 1,
            "target_per_category": 4, "coverage_gate": 0.95,
            "features": ["load_kw", "outdoor_temp"], "demand_columns": ["load_kw"],
            "cold_hot_fraction": 0.05, "positive_ramp_fraction": 0.01}


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def utc():
    return datetime.now(timezone.utc).isoformat()


def status(phase, **fields):
    old = read_json(OUT / "status.json") if (OUT / "status.json").exists() else {}
    old.update(phase=phase, updated_utc=utc(), **fields)
    write_json(OUT / "status.json", old)


def quality_reasons(record, year, training=False):
    y = record["years"][str(year)]
    reasons = []
    if y["coverage"] < SETTINGS["coverage_gate"]:
        reasons.append("meter_coverage_below_95pct")
    if y["weather_join_coverage"] < SETTINGS["coverage_gate"]:
        reasons.append("joint_temperature_coverage_below_95pct")
    if y["negative"]:
        reasons.append("negative_meter_values")
    if training:
        if not y["positive"]:
            reasons.append("2016_no_positive_observation")
        area = record.get("sqm")
        if area is None or not np.isfinite(area) or area <= 0:
            reasons.append("invalid_metadata_floor_area")
    return reasons


def select_cases(inventories, target=4):
    """Stable IDs only; 2017 values beyond predeclared quality are ignored."""
    accepted, decisions, seen = [], [], set()
    counts = {}
    for category in CATEGORIES:
        qualified = sorted((r for r in inventories[category]
                            if not quality_reasons(r, 2016, training=True)),
                           key=lambda r: (r["site_id"], r["building_id"]))
        by_site = {}
        for r in qualified:
            by_site.setdefault(r["site_id"], []).append(r)
        count = 0
        round_number = 1
        while count < target:
            changed = False
            for site in sorted(by_site):
                if count >= target:
                    break
                queue = by_site[site]
                while queue:
                    r = queue.pop(0)
                    reasons = quality_reasons(r, 2017)
                    if r["building_id"] in seen:
                        reasons.append("building_already_selected_in_previous_category")
                    decisions.append({"category": category, "site_id": site,
                                      "building_id": r["building_id"], "round": round_number,
                                      "accepted": not reasons, "reasons": reasons,
                                      "quality_2016": r["years"]["2016"],
                                      "quality_2017": {k: r["years"]["2017"][k] for k in
                                                       ("rows", "finite", "coverage", "negative",
                                                        "weather_join_valid_count", "weather_join_coverage")}})
                    if reasons:
                        continue
                    case = {"case_id": f"{category}__{r['building_id']}", "category": category,
                            "building_id": r["building_id"], "site_id": site,
                            "sqm": r["sqm"], "primaryspaceusage": r.get("primaryspaceusage"),
                            "quality_2016": r["years"]["2016"],
                            "quality_2017": decisions[-1]["quality_2017"],
                            "selection_round": round_number}
                    accepted.append(case)
                    seen.add(r["building_id"])
                    count += 1
                    changed = True
                    break
            if not changed:
                break
            round_number += 1
        counts[category] = {"qualified_2016": len(qualified), "selected": count,
                            "distinct_sites": len({x["site_id"] for x in accepted
                                                   if x["category"] == category}),
                            "shortfall": max(0, target - count)}
    return {"settings": SETTINGS, "cases": accepted, "counts": counts, "decisions": decisions}


def environment():
    return {"python": sys.version, "executable": sys.executable,
            "platform": platform.platform(),
            "versions": {"numpy": np.__version__, "pandas": pd.__version__,
                         "scipy": scipy.__version__, "sklearn": sklearn.__version__,
                         "threadpoolctl": threadpoolctl.__version__},
            "thread_limit": SETTINGS["threads"], "random_state": SETTINGS["seed"],
            "threadpools": threadpoolctl.threadpool_info(),
            "project_git_commit": None, "code_identity": "SHA-256; project is not a Git checkout"}


def freeze():
    if (OUT / "protocol_lock.json").exists():
        verify_lock()
        print("Existing lock verified; sample not changed.")
        return
    status("freezing_sample", selection_frozen=False, algorithms_started=False)
    inventories = {c: read_json(AUDIT / f"{c}_cleaned_channels.json") for c in CATEGORIES}
    selection = select_cases(inventories, SETTINGS["target_per_category"])
    write_json(OUT / "selection.json", selection)
    write_json(OUT / "environment.json", environment())
    integrity = read_json(AUDIT / "bdg2_integrity.json")
    expected = {r["file"]: r["sha256"] for r in integrity["files"]}
    source_hashes = {}
    for name in ["metadata.csv", "weather.csv"] + [f"{c}_cleaned.csv" for c in CATEGORIES]:
        path = DATA / name
        actual = sha(path)
        if actual != expected[name]:
            raise ValueError(f"Source audit hash mismatch: {name}")
        source_hashes[name] = {"path": str(path), "bytes": path.stat().st_size, "sha256": actual}
    locked = [OUT / "protocol.md", Path(__file__), ROOT / "tests/test_bdg2_external_benchmark.py",
              OUT / "selection.json", OUT / "environment.json", AUDIT / "bdg2_integrity.json"]
    locked += [AUDIT / f"{c}_cleaned_channels.json" for c in CATEGORIES]
    lock = {"schema_version": 1, "frozen_utc": utc(), "settings": SETTINGS,
            "source_repository": "buds-lab/building-data-genome-project-2",
            "source_commit": "9b97ccbe90096aff42ed4fd6493bf7ae692d7118",
            "files": [{"path": str(p.relative_to(ROOT)), "sha256": sha(p)} for p in locked],
            "sources": source_hashes, "case_count": len(selection["cases"]),
            "core_policy": "Each version separately snapshotted and hashed before any method run"}
    write_json(OUT / "protocol_lock.json", lock)
    status("sample_frozen", selection_frozen=True, algorithms_started=False,
           protocol_sha256=sha(OUT / "protocol.md"), selection_sha256=sha(OUT / "selection.json"),
           protocol_lock_sha256=sha(OUT / "protocol_lock.json"), case_count=len(selection["cases"]),
           pending=["run legacy", "run separately frozen new_core when ready", "report and verify"])
    print(json.dumps({"case_count": len(selection["cases"]), "counts": selection["counts"],
                      "protocol_lock_sha256": sha(OUT / "protocol_lock.json")}, ensure_ascii=False))


def verify_lock():
    lock = read_json(OUT / "protocol_lock.json")
    if lock["settings"] != SETTINGS:
        raise ValueError("Settings do not match fixed protocol lock")
    for record in lock["files"]:
        if sha(ROOT / record["path"]) != record["sha256"]:
            raise ValueError(f"Locked artifact changed: {record['path']}")
    for name, record in lock["sources"].items():
        if sha(DATA / name) != record["sha256"]:
            raise ValueError(f"Locked input changed: {name}")
    saved = read_json(OUT / "environment.json")
    if saved["versions"] != environment()["versions"] or saved["python"] != sys.version:
        raise ValueError("Runtime differs from frozen environment")
    return lock


def prepare_year(raw, year):
    """Return real-calendar observations, complete days, exclusions, coverage."""
    frame = raw.copy()
    times = pd.DatetimeIndex(pd.to_datetime(frame["time"], errors="raise"))
    if times.has_duplicates or not times.is_monotonic_increasing:
        raise ValueError("Source timestamps must be unique and chronological")
    if (times != times.floor("h")).any():
        raise ValueError("Source timestamps must be aligned to clock hours")
    frame.index = times
    frame = frame.loc[frame.index.year == year].drop(columns="time")
    calendar = pd.date_range(f"{year}-01-01", f"{year+1}-01-01", freq="h", inclusive="left")
    full = frame.reindex(calendar)
    full.index.name = "time"
    meter = np.isfinite(full["energy_kwh"].to_numpy())
    nonnegative = meter & (full["energy_kwh"].to_numpy() >= 0)
    temp = np.isfinite(full["outdoor_temp"].to_numpy())
    full["load_kw"] = full["energy_kwh"] / 1.0
    keep, exclusions = [], []
    for date, day in full.groupby(full.index.normalize(), sort=True):
        valid = np.isfinite(day["energy_kwh"]) & (day["energy_kwh"] >= 0) & np.isfinite(day["outdoor_temp"])
        if len(day) == 24 and valid.all():
            keep.append(date)
        else:
            exclusions.append({"date": str(date.date()),
                               "missing_or_nonfinite_meter_hours": int((~np.isfinite(day["energy_kwh"])).sum()),
                               "negative_meter_hours": int((day["energy_kwh"] < 0).sum()),
                               "missing_or_nonfinite_temperature_hours": int((~np.isfinite(day["outdoor_temp"])).sum())})
    full["retained_complete_day"] = full.index.normalize().isin(keep)
    retained = full.loc[full["retained_complete_day"]].reset_index()
    retained["scenario"] = f"observed_{year}"
    retained["boundary_group"] = f"observed_{year}"
    retained["source_weather_year"] = year
    if len(retained) % 24:
        raise AssertionError("Invalid complete day adapter")
    valid_peak = float(full.loc[nonnegative, "load_kw"].max()) if nonnegative.any() else None
    retained_peak = float(retained["load_kw"].max()) if len(retained) else None
    quality = {"year": year, "calendar_hours": len(full), "calendar_days": len(full) // 24,
               "finite_meter_hours": int(meter.sum()), "meter_coverage": float(meter.mean()),
               "negative_meter_hours": int((meter & ~nonnegative).sum()),
               "finite_temperature_hours": int(temp.sum()), "temperature_coverage": float(temp.mean()),
               "joint_valid_hours": int((nonnegative & temp).sum()),
               "joint_valid_coverage": float((nonnegative & temp).mean()),
               "retained_days": len(keep), "retained_hours": len(retained),
               "complete_day_coverage": len(keep) / (len(full) // 24),
               "excluded_days": len(exclusions),
               "observed_energy_kwh_partial_year": float(full.loc[nonnegative, "energy_kwh"].sum()),
               "retained_scope_energy_kwh": float(retained["energy_kwh"].sum()),
               "original_observed_peak_kw": valid_peak, "retained_scope_peak_kw": retained_peak,
               "original_peak_excluded_from_retained_scope": valid_peak is not None and
                     (retained_peak is None or valid_peak > retained_peak),
               "unit_conversion": "hourly energy_kwh / 1 h = hourly mean load_kw; no missing-value fill"}
    return full.reset_index(), retained, exclusions, quality


def load_core(path, label):
    module_name = f"bdg2_frozen_core_{label}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def validate_representatives(days, weights, n_days, requested=None):
    days = [int(d) for d in days]
    weights = np.asarray(weights, dtype=float)
    if len(days) != len(weights) or not np.isfinite(weights).all() or (weights < 0).any():
        raise ValueError("Representative weights must be finite and nonnegative")
    if len(set(days)) != len(days) or any(d < 0 or d >= n_days for d in days):
        raise ValueError("Representatives must be unique actual source days")
    if not np.isclose(weights.sum(), n_days, rtol=0, atol=1e-8):
        raise ValueError("Representative weights do not sum to retained days")
    positive = [(d, float(w)) for d, w in zip(days, weights) if w > 0]
    if not positive:
        raise ValueError("No positive-weight actual representative day")
    if requested is not None and len(positive) != requested:
        raise ValueError(f"Actual positive-day budget {len(positive)} differs from requested {requested}")
    return [p[0] for p in positive], [p[1] for p in positive]


def execute_method(core, retained, method, k):
    """Time only the independent method invocation; no IO or metrics."""
    elapsed, outputs = [], []
    n_days = len(retained) // 24
    for _ in range(SETTINGS["repeats"]):
        with threadpoolctl.threadpool_limits(limits=SETTINGS["threads"]):
            started = perf_counter()
            try:
                if method == "extreme_aware":
                    reps = core.extreme_aware_typical_days(retained, SETTINGS["features"],
                           SETTINGS["demand_columns"], n_clusters=k, random_state=SETTINGS["seed"])
                else:
                    reps = core.cluster_typical_days(retained, SETTINGS["features"],
                           n_clusters=k, random_state=SETTINGS["seed"])
            except Exception as exc:
                exc.benchmark_elapsed_seconds = elapsed
                raise
            finally:
                elapsed.append(perf_counter() - started)
        reasons = {}
        if method == "extreme_aware":
            days, weights = [], []
            for d, group in reps.groupby("original_day_index", sort=True):
                if len(group) != 24 or group["period_weight"].nunique() != 1:
                    raise ValueError("Core output has malformed actual day blocks")
                source = retained.iloc[int(d)*24:(int(d)+1)*24]
                if not np.array_equal(group["load_kw"].to_numpy(), source["load_kw"].to_numpy()):
                    raise ValueError("Core output is not an unmodified actual source day")
                if not np.array_equal(pd.to_datetime(group["original_time"]).to_numpy(), source["time"].to_numpy()):
                    raise ValueError("Core source timestamp mapping changed")
                days.append(int(d))
                weights.append(float(group["period_weight"].iloc[0]))
                reasons[str(int(d))] = str(group["extreme_reasons"].iloc[0])
            days, weights = validate_representatives(days, weights, n_days)
        else:
            days, weights = validate_representatives(reps[0], reps[1], n_days, requested=k)
        outputs.append({"days": days, "weights": weights, "reasons": reasons})
    if any(o != outputs[0] for o in outputs[1:]):
        raise ValueError("Fixed-seed repetitions disagree")
    result = outputs[0]
    result.update(requested_k=k, actual_m=len(result["days"]), elapsed_seconds=elapsed,
                  elapsed_median_seconds=float(np.median(elapsed)))
    return result


def representative_frame(retained, selection):
    blocks = []
    for d, w in zip(selection["days"], selection["weights"]):
        block = retained.iloc[d*24:(d+1)*24].copy()
        block["representative_day"] = f"day_{d}"
        block["original_day_index"] = d
        block["period_weight"] = w
        block["is_extreme_day"] = bool(selection["reasons"].get(str(d), ""))
        blocks.append(block)
    return pd.concat(blocks, ignore_index=True)


def execute_calibration(core, retained, selection):
    representative = representative_frame(retained, selection)
    elapsed, outputs = [], []
    calibration = None
    for _ in range(SETTINGS["repeats"]):
        with threadpoolctl.threadpool_limits(limits=SETTINGS["threads"]):
            start = perf_counter()
            try:
                calibration = core.calibrate_representative_day_weights(
                    representative, retained, match_columns=["load_kw"], regularization=1e-4,
                    minimum_extreme_weight=1.0, minimum_day_weight=1e-6)
            except Exception as exc:
                exc.benchmark_elapsed_seconds = elapsed
                raise
            finally:
                elapsed.append(perf_counter()-start)
        frame = calibration.timeseries
        by_day = frame.groupby("original_day_index", sort=False)
        days = [int(d) for d in by_day.groups]
        weights = [float(g["period_weight"].iloc[0]) for _, g in by_day]
        days, weights = validate_representatives(days, weights, len(retained)//24,
                                                requested=len(selection["days"]))
        if days != selection["days"]:
            raise ValueError("Calibration changed selected actual days")
        for d, w in zip(days, weights):
            if selection["reasons"].get(str(d), "") and w < 1.0-1e-8:
                raise ValueError("Calibration violated extreme weight lower bound")
        outputs.append({"days": days, "weights": weights})
    if any(o != outputs[0] for o in outputs[1:]):
        raise ValueError("Fixed calibration repetitions disagree")
    result = outputs[0]
    result.update(reasons=selection["reasons"], requested_k=selection["requested_k"],
                  actual_m=len(result["days"]), elapsed_seconds=elapsed,
                  elapsed_median_seconds=float(np.median(elapsed)),
                  calibration_summary=calibration.summary,
                  calibration_diagnostics=calibration.diagnostics.to_dict(orient="records"),
                  calibration_weight_audit=calibration.weights.to_dict(orient="records"))
    return result


def compute_metrics(retained, days, weights):
    """Scope: retained complete days; ramps only original adjacent hour edges."""
    n = len(retained) // 24
    days, weights = validate_representatives(days, weights, n)
    indices = np.concatenate([np.arange(d*24, (d+1)*24) for d in days])
    load = retained["load_kw"].to_numpy(dtype=float)
    rep_load = load[indices]
    rep_weights = np.repeat(weights, 24)
    ref_energy = float(retained["energy_kwh"].sum())
    rep_energy = float(np.dot(rep_load, rep_weights))  # each interval is exactly 1 hour
    dates = pd.to_datetime(retained["time"]).dt.strftime("%Y-%m-%d").to_numpy()
    selected_dates = {dates[d*24] for d in days}
    selected_hours = np.isin(dates, sorted(selected_dates))
    mean_load = float(load.mean())
    peak = float(load.max())
    rep_peak = float(rep_load.max())
    metrics = {"retained_days_n": n, "actual_representative_days_m": len(days),
               "representative_hours": 24*len(days), "weight_sum_days": float(sum(weights)),
               "compression_n_over_m": n / len(days), "representative_day_fraction": len(days) / n,
               "retained_scope_energy_kwh": ref_energy, "weighted_representative_energy_kwh": rep_energy,
               "retained_scope_energy_bias": (rep_energy-ref_energy)/ref_energy if ref_energy > 0 else None,
               "retained_scope_abs_energy_bias": abs(rep_energy-ref_energy)/ref_energy if ref_energy > 0 else None,
               "retained_scope_peak_kw": peak, "representative_peak_kw": rep_peak,
               "peak_retention_ratio": rep_peak/peak if peak > 0 else None,
               "maximum_peak_day_selected": bool(selected_hours[int(np.argmax(load))]),
               "ldc_wasserstein1_kw": float(wasserstein_distance(load, rep_load, v_weights=rep_weights)),
               "ldc_w1_over_mean_load": float(wasserstein_distance(load, rep_load, v_weights=rep_weights))/mean_load if mean_load > 0 else None}
    temps = retained["outdoor_temp"].to_numpy(dtype=float)
    daily = pd.DataFrame({"date": dates, "temperature": temps}).groupby("date")["temperature"].mean()
    count = max(1, math.ceil(SETTINGS["cold_hot_fraction"]*n))
    cold = sorted(daily.index, key=lambda d: (daily[d], d))[:count]
    hot = sorted(daily.index, key=lambda d: (-daily[d], d))[:count]
    metrics.update(cold_day_count=count, cold_day_hit_count=len(selected_dates.intersection(cold)),
                   cold_day_coverage=len(selected_dates.intersection(cold))/count,
                   hot_day_hit_count=len(selected_dates.intersection(hot)),
                   hot_day_coverage=len(selected_dates.intersection(hot))/count,
                   minimum_temperature_gap_c=float(temps[indices].min()-temps.min()),
                   absolute_minimum_temperature_day_selected=bool(selected_hours[int(np.argmin(temps))]))
    times = pd.to_datetime(retained["time"]).to_numpy(dtype="datetime64[ns]")
    adjacent = np.diff(times) == np.timedelta64(1, "h")
    jumps = np.abs(np.diff(load))
    midnight = dates[1:] != dates[:-1]
    pair_selected = selected_hours[:-1] & selected_hours[1:]
    eligible = np.flatnonzero(adjacent & (jumps > 0))
    # Stable tie order: higher ramp, then earlier end timestamp (original chronological index).
    ranked = sorted(eligible.tolist(), key=lambda i: (-jumps[i], i))
    top = ranked[:math.ceil(SETTINGS["positive_ramp_fraction"]*len(ranked))]
    events = [{"start": str(pd.Timestamp(times[i])), "end": str(pd.Timestamp(times[i+1])),
               "ramp_kw_per_hour": float(jumps[i]), "crosses_midnight": bool(midnight[i]),
               "both_endpoint_days_selected": bool(pair_selected[i])} for i in top]
    null_reasons = {}
    if ranked:
        biggest = ranked[0]
        observed_max = float(jumps[biggest])
        kept_max = float(jumps[adjacent & pair_selected].max()) if (adjacent & pair_selected).any() else 0.0
        top_midnight = [i for i in top if midnight[i]]
        metrics.update(real_hour_edges=int(adjacent.sum()), positive_ramp_edges=len(ranked),
                       maximum_real_ramp_kw_per_hour=observed_max,
                       maximum_selected_original_ramp_kw_per_hour=kept_max,
                       maximum_real_ramp_retention_ratio=kept_max/observed_max,
                       maximum_real_ramp_both_days_selected=bool(pair_selected[biggest]),
                       maximum_real_ramp_crosses_midnight=bool(midnight[biggest]),
                       ramp_tail_event_count=len(top), ramp_tail_hit_count=sum(bool(pair_selected[i]) for i in top),
                       ramp_tail_event_coverage=float(np.mean(pair_selected[top])),
                       midnight_tail_event_count=len(top_midnight),
                       midnight_tail_event_coverage=float(np.mean(pair_selected[top_midnight])) if top_midnight else None)
        if not top_midnight:
            null_reasons["midnight_tail_event_coverage"] = "no midnight events in fixed top-1pct positive ramp set"
    else:
        metrics.update(real_hour_edges=int(adjacent.sum()), positive_ramp_edges=0,
                       maximum_real_ramp_kw_per_hour=0.0, maximum_selected_original_ramp_kw_per_hour=0.0,
                       maximum_real_ramp_retention_ratio=None, maximum_real_ramp_both_days_selected=None,
                       maximum_real_ramp_crosses_midnight=None, ramp_tail_event_count=0, ramp_tail_hit_count=0,
                       ramp_tail_event_coverage=None, midnight_tail_event_count=0, midnight_tail_event_coverage=None)
        null_reasons["ramp_metrics"] = "no positive true-adjacent load ramps"
    deficit = np.maximum(load-rep_peak, 0)
    metrics.update(capacity_only_k_kw=rep_peak,
                   capacity_only_deficit_hours=int((deficit > 0).sum()),
                   capacity_only_deficit_hour_fraction=float((deficit > 0).mean()),
                   capacity_only_deficit_energy_kwh=float(deficit.sum()),
                   capacity_only_deficit_energy_fraction=float(deficit.sum())/ref_energy if ref_energy > 0 else None)
    if ref_energy <= 0:
        null_reasons["energy_bias_and_deficit_energy_fraction"] = "zero retained observed energy"
    if mean_load <= 0:
        null_reasons["ldc_w1_over_mean_load"] = "zero retained mean load"
    if peak <= 0:
        null_reasons["peak_retention_ratio"] = "zero retained peak load"
    metrics["null_reasons"] = null_reasons
    return metrics, events


def run(core_file, label):
    if label not in ("legacy", "new_core"):
        raise ValueError("Core label must be legacy or new_core")
    started_all = perf_counter()
    status("verifying_inputs", current_core=label)
    verify_lock()
    version_dir = OUT / "versions" / label
    version_dir.mkdir(parents=True, exist_ok=True)
    snapshot = version_dir / "scenarios.py"
    source = Path(core_file).resolve()
    if snapshot.exists():
        if sha(source) != sha(snapshot):
            raise ValueError("Version label already frozen to a different core; cannot overwrite")
    else:
        shutil.copyfile(source, snapshot)
    version_lock = {"core_label": label, "source_path": str(source), "snapshot": str(snapshot.relative_to(ROOT)),
                    "core_sha256": sha(snapshot), "frozen_utc": utc(),
                    "protocol_lock_sha256": sha(OUT / "protocol_lock.json"),
                    "environment_sha256": sha(OUT / "environment.json")}
    version_lock_path = version_dir / "version_lock.json"
    if version_lock_path.exists():
        previous = read_json(version_lock_path)
        if previous["core_sha256"] != version_lock["core_sha256"]:
            raise ValueError("Frozen core hash differs")
    else:
        write_json(version_lock_path, version_lock)
    core = load_core(snapshot, label)
    cases = read_json(OUT / "selection.json")["cases"]
    io_started = perf_counter()
    metadata = pd.read_csv(DATA / "metadata.csv").set_index("building_id")
    weather = pd.read_csv(DATA / "weather.csv", usecols=["timestamp", "site_id", "airTemperature"])
    weather["timestamp"] = pd.to_datetime(weather["timestamp"])
    if weather.duplicated(["site_id", "timestamp"]).any():
        raise ValueError("Duplicate source site/weather timestamp")
    meters = {}
    for category in CATEGORIES:
        ids = [c["building_id"] for c in cases if c["category"] == category]
        meters[category] = pd.read_csv(DATA / f"{category}_cleaned.csv", usecols=["timestamp"]+ids)
        meters[category]["timestamp"] = pd.to_datetime(meters[category]["timestamp"])
    io_seconds = perf_counter()-io_started
    status("running_methods", algorithms_started=True, current_core=label, completed_case_years=0,
           frozen_core_sha256=sha(snapshot))
    results, qualities, failures = [], [], []
    for case in cases:
        meter = meters[case["category"]]
        if str(metadata.loc[case["building_id"], "site_id"]) != case["site_id"]:
            raise ValueError("Selection site does not match locked metadata")
        temp = weather.loc[weather["site_id"] == case["site_id"], ["timestamp", "airTemperature"]]
        joined = meter[["timestamp", case["building_id"]]].merge(temp, on="timestamp", how="left", validate="one_to_one", sort=False)
        raw = joined.rename(columns={"timestamp": "time", case["building_id"]: "energy_kwh", "airTemperature": "outdoor_temp"})
        for year in (2016, 2017):
            case_path = OUT / "cases" / case["case_id"] / str(year)
            case_path.mkdir(parents=True, exist_ok=True)
            full, retained, exclusions, quality = prepare_year(raw, year)
            quality.update(case_id=case["case_id"], category=case["category"], building_id=case["building_id"], site_id=case["site_id"])
            if quality["meter_coverage"] < SETTINGS["coverage_gate"] or quality["joint_valid_coverage"] < SETTINGS["coverage_gate"] or quality["negative_meter_hours"]:
                raise ValueError("Selected actual source coverage differs from frozen quality admission")
            qualities.append(quality)
            write_json(case_path / "coverage.json", quality)
            write_json(case_path / "excluded_days.json", exclusions)
            # These are unfilled observations and explicit calendars, not new cleaned source data.
            full.to_csv(case_path / "observed_calendar.csv", index=False, na_rep="")
            input_sha = sha(case_path / "observed_calendar.csv")
            budget = None
            extreme_selection = None
            methods = ("extreme_aware", "ordinary", "extreme_calibrated") if label == "new_core" else ("extreme_aware", "ordinary")
            for method in methods:
                k = SETTINGS["base_k"] if method == "extreme_aware" else budget
                record = {"case_id": case["case_id"], "category": case["category"], "site_id": case["site_id"],
                          "year": year, "core_label": label, "method": method, "requested_k": k,
                          "core_sha256": sha(snapshot), "observed_calendar_sha256": input_sha,
                          "input_retained_days": len(retained)//24}
                stage = "method"
                method_started = perf_counter()
                try:
                    if k is None:
                        raise ValueError("Extreme method failed; paired actual-day budget unavailable")
                    if method == "extreme_calibrated":
                        if extreme_selection is None:
                            raise ValueError("No uncalibrated extreme selection available")
                        run_result = execute_calibration(core, retained, extreme_selection)
                    else:
                        run_result = execute_method(core, retained, method, k)
                    if method == "extreme_aware":
                        budget = run_result["actual_m"]
                        extreme_selection = run_result
                    stage = "metrics"
                    metric_started = perf_counter()
                    metrics, events = compute_metrics(retained, run_result["days"], run_result["weights"])
                    metrics_elapsed = perf_counter()-metric_started
                    selected = []
                    for d, w in zip(run_result["days"], run_result["weights"]):
                        selected.append({"original_day_index": d, "date": str(retained.iloc[d*24]["time"].date()),
                                         "weight_days": w, "extreme_reasons": run_result["reasons"].get(str(d), "")})
                    record.update(status="ok", metrics=metrics, elapsed_seconds=run_result["elapsed_seconds"],
                                  elapsed_median_seconds=run_result["elapsed_median_seconds"],
                                  metrics_elapsed_seconds=metrics_elapsed, selected_days=selected,
                                  actual_m=run_result["actual_m"], paired_actual_budget=budget,
                                  budget_matched=True)
                    if method == "extreme_calibrated":
                        record.update(calibration_summary=run_result["calibration_summary"],
                                      calibration_diagnostics=run_result["calibration_diagnostics"],
                                      calibration_weight_audit=run_result["calibration_weight_audit"],
                                      timing_scope="weight-calibration invocation only; fixed selection reused")
                    rep_frames = []
                    for d, w in zip(run_result["days"], run_result["weights"]):
                        piece = retained.iloc[d*24:(d+1)*24].copy()
                        piece["original_day_index"] = d
                        piece["period_weight"] = w
                        rep_frames.append(piece)
                    pd.concat(rep_frames).to_csv(case_path / f"{label}_{method}_actual_days.csv", index=False)
                    write_json(case_path / f"{label}_{method}_ramp_events.json", events)
                except Exception as exc:
                    record.update(status="failed", failure_stage=stage, exception_type=type(exc).__name__,
                                  exception=str(exc), traceback=traceback.format_exc(), budget_matched=False,
                                  failure_total_elapsed_seconds=perf_counter()-method_started,
                                  elapsed_seconds=getattr(exc, "benchmark_elapsed_seconds", []))
                    failures.append(record)
                results.append(record)
                write_json(case_path / f"{label}_{method}_result.json", record)
            write_json(version_dir / "results.json", results)
            write_json(version_dir / "failures.json", failures)
            status("running_methods", current_core=label, completed_case_years=len(qualities),
                   total_case_years=2*len(cases), method_failures=len(failures))
            print(f"{label}: {case['case_id']} {year}: N={len(retained)//24}, M={budget}, failures={len(failures)}", flush=True)
    if sha(snapshot) != version_lock["core_sha256"]:
        raise ValueError("Frozen core changed during benchmark")
    rows = []
    for r in results:
        row = {k: v for k, v in r.items() if k not in ("metrics", "selected_days", "elapsed_seconds", "traceback",
                                                    "calibration_summary", "calibration_diagnostics", "calibration_weight_audit")}
        row.update({k: v for k, v in r.get("metrics", {}).items() if k != "null_reasons"})
        rows.append(row)
    pd.DataFrame(rows).to_csv(version_dir / "metrics.csv", index=False)
    pd.DataFrame(qualities).to_csv(OUT / "coverage.csv", index=False)
    write_json(version_dir / "run_manifest.json", {"completed_utc": utc(), "core_sha256": sha(snapshot),
               "protocol_lock_sha256": sha(OUT / "protocol_lock.json"), "method_records": len(results),
               "failed_method_records": len(failures), "case_years": len(qualities),
               "source_load_io_seconds": io_seconds, "total_elapsed_seconds": perf_counter()-started_all,
               "method_time_scope": "per independent invocation only; three raw runs and median",
               "files": [{"path": str(p.relative_to(ROOT)), "sha256": sha(p)} for p in sorted(version_dir.iterdir()) if p.is_file() and p.name != "run_manifest.json"]})
    completed = [p.name for p in (OUT / "versions").iterdir() if (p / "run_manifest.json").exists()]
    status("version_completed", completed_versions=completed, current_core=label,
           method_records=len(results), method_failures=len(failures),
           pending=["new_core if not completed", "report and verify"])


def report():
    verify_lock()
    versions = {}
    for folder in sorted((OUT / "versions").iterdir()):
        if (folder / "run_manifest.json").exists():
            manifest = read_json(folder / "run_manifest.json")
            for f in manifest["files"]:
                if sha(ROOT / f["path"]) != f["sha256"]:
                    raise ValueError(f"Run artifact hash mismatch: {f['path']}")
            versions[folder.name] = read_json(folder / "results.json")
    summaries, paired = [], []
    metric_keys = ["retained_scope_abs_energy_bias", "peak_retention_ratio", "cold_day_coverage",
                   "hot_day_coverage", "ramp_tail_event_coverage", "maximum_real_ramp_retention_ratio",
                   "ldc_w1_over_mean_load", "capacity_only_deficit_energy_fraction", "compression_n_over_m"]
    for version, records in versions.items():
        lookup = {(r["case_id"], r["year"], r["method"]): r for r in records}
        for method in sorted({r["method"] for r in records}):
            good = [r for r in records if r["method"] == method and r["status"] == "ok"]
            summary = {"core_label": version, "method": method, "successful_records": len(good),
                       "failed_records": sum(r["method"] == method and r["status"] != "ok" for r in records),
                       "median_method_seconds": float(np.median([r["elapsed_median_seconds"] for r in good])) if good else None}
            for key in metric_keys:
                vals = [r["metrics"][key] for r in good if r["metrics"][key] is not None]
                summary[f"median_{key}"] = float(np.median(vals)) if vals else None
            summaries.append(summary)
        for key, ext in lookup.items():
            if key[2] != "extreme_aware":
                continue
            ordinary = lookup.get((key[0], key[1], "ordinary"))
            if ext["status"] != "ok" or ordinary is None or ordinary["status"] != "ok":
                paired.append({"core_label": version, "case_id": key[0], "year": key[1], "paired_status": "failed"})
                continue
            pair = {"core_label": version, "case_id": key[0], "year": key[1], "paired_status": "ok",
                    "extreme_actual_m": ext["actual_m"], "ordinary_actual_m": ordinary["actual_m"],
                    "actual_budget_equal": ext["actual_m"] == ordinary["actual_m"]}
            for metric in metric_keys:
                a, b = ext["metrics"][metric], ordinary["metrics"][metric]
                pair[f"extreme_minus_ordinary_{metric}"] = a-b if a is not None and b is not None else None
            paired.append(pair)
    pd.DataFrame(summaries).to_csv(OUT / "summary.csv", index=False)
    pd.DataFrame(paired).to_csv(OUT / "paired_comparisons.csv", index=False)
    cross = []
    if "legacy" in versions and "new_core" in versions:
        old = {(r["case_id"], r["year"], r["method"]): r for r in versions["legacy"]}
        for new in versions["new_core"]:
            if new["method"] == "extreme_calibrated":
                continue
            previous = old[(new["case_id"], new["year"], new["method"])]
            row = {"case_id": new["case_id"], "year": new["year"], "method": new["method"],
                   "legacy_status": previous["status"], "new_core_status": new["status"]}
            if previous["status"] == new["status"] == "ok":
                row.update(legacy_m=previous["actual_m"], new_core_m=new["actual_m"],
                           actual_budget_equal=previous["actual_m"] == new["actual_m"],
                           legacy_max_ramp_both_days_selected=previous["metrics"]["maximum_real_ramp_both_days_selected"],
                           new_core_max_ramp_both_days_selected=new["metrics"]["maximum_real_ramp_both_days_selected"])
            cross.append(row)
        pd.DataFrame(cross).to_csv(OUT / "version_comparisons.csv", index=False)
    result = {"generated_utc": utc(), "protocol_lock_sha256": sha(OUT / "protocol_lock.json"),
              "summaries": summaries, "paired_comparisons": paired, "version_comparisons": cross,
              "interpretation": "Quality-screened, unweighted case-level medians; independent compression per year; no prediction or HeatPlan optimization"}
    write_json(OUT / "summary.json", result)
    status("results_reported", completed_versions=sorted(versions),
           successful_method_records=sum(r["status"] == "ok" for v in versions.values() for r in v),
           failed_method_records=sum(r["status"] != "ok" for v in versions.values() for r in v),
           pending=["write findings and limitations", "focused-test evidence and final delivery verification"])
    print(json.dumps(summaries, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("freeze", "run", "report"))
    parser.add_argument("--core-label", choices=("legacy", "new_core"))
    parser.add_argument("--core-file", type=Path)
    args = parser.parse_args()
    try:
        if args.phase == "freeze":
            freeze()
        elif args.phase == "run":
            if not args.core_file or not args.core_label:
                parser.error("run requires --core-file and --core-label")
            run(args.core_file, args.core_label)
        else:
            report()
    except Exception as exc:
        status("phase_failed", failure_type=type(exc).__name__, failure=str(exc))
        raise


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
