"""Reproducible C1/C3 case runner; success manifests are written last.

Representative-day incumbents are planning approximations. A service-year
certificate additionally requires fixed-investment chronological replay and
independent checking. This module does not implement Benders decomposition.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import sys
from time import perf_counter
import traceback
from typing import Any

import numpy as np
import pandas as pd
import scipy
import yaml

from .chronology import (explicit_annual_scope_audit, investment_freeze_audit,
                         model_structure_profile, seasonal_storage_postcheck,
                         representative_period_coverage_audit)
from .experiments import freeze_investment_plan, nondominated_cost_emission_points
from .building_audit import audit_building_trajectory, building_audit_identity
from .heating_season import _season_config, prepare_heating_season_timeseries
from .io import load_config, validate_inputs
from .model import HeatPlanningModel
from .spatial_quality import aggregate_delivered_power, preprocess_spatial_quality

CASE = Path("data/competition_20261001")
SPATIAL = Path("data/competition_spatial/s3_spatial.yaml")
OUTPUT = Path("outputs/competition_20261001/C4")
CODE_FILES = [f"src/heatplan/{name}.py" for name in (
    "competition_runner", "model", "io", "linear", "chronology", "heating_season",
    "building_thermal", "building_audit", "spatial_quality", "experiments", "accounting", "policy_alignment")]
CODE_FILES += ["scripts/run_competition_case.py"]


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, np.ndarray):
        return _jsonable(value.tolist())
    if isinstance(value, np.generic):
        return _jsonable(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return None
    if isinstance(value, (Path, pd.Timestamp)):
        return str(value)
    return value


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(_jsonable(value), ensure_ascii=False, indent=2,
                                    allow_nan=False), encoding="utf-8")
    os.replace(temporary, path)


def fingerprint(value: Any) -> str:
    encoded = json.dumps(_jsonable(value), sort_keys=True, ensure_ascii=False,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def code_identity(root: Path) -> dict:
    return {name: file_hash(root / name) for name in CODE_FILES}


def config_chain(path: Path) -> list[Path]:
    chain, seen = [], set()
    while True:
        path = path.resolve()
        if path in seen:
            raise ValueError("cyclic config inheritance")
        seen.add(path)
        chain.append(path)
        item = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not item.get("extends"):
            return chain
        path = path.parent / item["extends"]


def normalize_clock(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy().reset_index(drop=True)
    for key in ("time", "timestamp"):
        parsed = [pd.Timestamp(value) for value in result[key]]
        if any(pd.isna(value) or value.tzinfo is None for value in parsed):
            raise ValueError(f"{key} requires aware source timestamps")
        result[key] = pd.to_datetime(parsed, utc=True)
    if not result.time.equals(result.timestamp):
        raise ValueError("time and timestamp disagree")
    if result.time.duplicated().any() or not result.time.is_monotonic_increasing:
        raise ValueError("chronological source timestamps must be sorted and unique")
    return result


def representative_days(annual: pd.DataFrame, config: dict | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Monthly nearest-mean days + coldest/hottest days; deterministic weights.

    Assign every real day to a selected day in its own calendar month using
    standardized daily outdoor min/mean/max and solar mean. Real time labels,
    gains, occupancy, and tariffs are retained. Each selected day has its own
    fixed state boundary. This is an approximation, not a state-linked year.
    """
    annual = normalize_clock(annual)
    local = annual.time.dt.tz_convert("Asia/Shanghai")
    dates = local.dt.strftime("%Y-%m-%d")
    groups = {day: rows for day, rows in annual.groupby(dates, sort=True)}
    for day, rows in groups.items():
        if len(rows) != 24 or not (rows.time.diff().iloc[1:] == pd.Timedelta(hours=1)).all():
            raise ValueError(f"representative input has incomplete local day {day}")
    daily = pd.DataFrame({day: {
        "mean": float(rows.outdoor_temp.mean()), "min": float(rows.outdoor_temp.min()),
        "max": float(rows.outdoor_temp.max()),
        "solar": float(rows.solar_heat_availability.mean()),
    } for day, rows in groups.items()}).T.sort_index()
    references = {}
    if config is not None:
        for building in config["building_thermal"]["buildings"]:
            def values(column_key, scalar_key, default=0.):
                return (annual[building[column_key]].astype(float) if column_key in building
                        else pd.Series(float(building.get(scalar_key, default)), index=annual.index))
            h = float(building["heat_loss_kw_per_c"])
            gains = values("internal_gain_column", "internal_gain_kw") + values("solar_gain_column", "solar_gain_kw")
            references[building["name"] + "_heat"] = (h * (values("min_temperature_column", "min_temperature_c") - annual.outdoor_temp) - gains).clip(lower=0.)
            references[building["name"] + "_cool"] = (h * (annual.outdoor_temp - values("max_temperature_column", "max_temperature_c")) + gains).clip(lower=0.)
        for name, series in references.items():
            daily[name] = series.groupby(dates).sum()
    features = (daily - daily.mean()) / daily.std(ddof=0).replace(0, 1)
    selected = {str(daily["min"].idxmin()), str(daily["max"].idxmax())}
    # Include actual hourly extrema of gains and comfort limits as well as
    # weather. No synthetic day, naive timestamp or disconnected hour is made.
    extreme_columns = [c for c in annual if c.startswith(("internal_gain_", "solar_gain_",
                                                        "min_temperature_", "max_temperature_"))]
    for column in extreme_columns:
        for position in (annual[column].idxmin(), annual[column].idxmax()):
            selected.add(str(dates.loc[position]))
    # One bounded coverage repair follows the recorded 17-day coverage audit:
    # RC steady-reference peaks and maximum ramps were under-captured. These
    # references select stress days only; they never become fixed RC loads.
    for series in references.values():
        positions = [int(series.idxmax()), int(series.diff().abs().idxmax())]
        for position in positions:
            selected.add(str(dates.loc[position]))
            if position > 0 and dates.loc[position - 1] != dates.loc[position]:
                selected.add(str(dates.loc[position - 1]))
    months = daily.index.str[:7]
    day_type = pd.to_datetime(daily.index).dayofweek < 5
    strata = pd.Series([f"{month}_{int(workday)}" for month, workday in zip(months, day_type)], index=daily.index)
    # Second and final bounded repair: the 22-day audit showed a +16.5%
    # office cooling energy bias. Keep workdays/weekends separate, matching
    # C1's synthetic office occupancy calendar, when selecting and assigning.
    for stratum in sorted(set(strata)):
        data = features.loc[strata == stratum]
        selected.add(str(((data - data.mean()) ** 2).sum(axis=1).idxmin()))
    assignments = []
    for day in daily.index:
        candidates = sorted(s for s in selected if strata.loc[s] == strata.loc[day])
        distances = ((features.loc[candidates] - features.loc[day]) ** 2).sum(axis=1)
        chosen = day if day in selected else str(distances.idxmin())
        assignments.append({"actual_local_day": day, "representative_local_day": chosen,
                            "squared_standardized_distance": float(distances.loc[chosen])})
    assignment = pd.DataFrame(assignments)
    weights = assignment.representative_local_day.value_counts()
    pieces = []
    for day in sorted(selected):
        piece = groups[day].copy()
        piece["boundary_group"] = "representative_" + day
        piece["period_weight"] = int(weights[day])
        piece["scenario"] = "base"
        piece["scenario_probability"] = 1.0
        pieces.append(piece)
    result = pd.concat(pieces, ignore_index=True)
    if not np.isclose(result.period_weight.sum(), len(annual)):
        raise AssertionError("representative hour weights do not cover the service year")
    return result, assignment


def technology_config(config: dict, technology: str) -> tuple[dict, list[dict]]:
    config = deepcopy(config)
    changes = []
    if technology == "optimized":
        return config, changes
    if technology != "ashp_backup":
        raise ValueError("unknown technology")
    allowed = {"district_a_air_hp", "distributed_air_hp_b", "district_a_emergency_eb"}
    if not allowed.issubset({s["name"] for s in config["sources"]}):
        raise ValueError("ASHP+backup baseline is defined for the inherited S3 equipment only")
    for source in config["sources"]:
        if source["name"] not in allowed:
            if source.get("existing_capacity_kw", 0):
                raise ValueError("baseline cannot silently remove existing equipment")
            changes.append({"asset": source["name"], "field": "max_new_capacity_kw",
                            "before": source.get("max_new_capacity_kw", 0), "after": 0})
            source["max_new_capacity_kw"] = 0.0
    for storage in config["storages"]:
        if storage.get("existing_capacity_kwh", 0):
            raise ValueError("baseline cannot silently remove existing storage")
        changes.append({"asset": storage["name"], "field": "max_new_capacity_kwh",
                        "before": storage.get("max_new_capacity_kwh", 0), "after": 0})
        storage["max_new_capacity_kwh"] = 0.0
    return config, changes


def load_case(root: Path, technology: str = "optimized", mode: str = "exclusive") -> dict:
    path = root / CASE / "wuhan_rc_candidate.yaml"
    raw = load_config(path)
    annual_path = root / CASE / "wuhan_service_year_2023_2024_utc.csv"
    annual = normalize_clock(pd.read_csv(annual_path))
    scope = explicit_annual_scope_audit(raw, annual)
    if len(annual) != 8784:
        raise ValueError("C1 main case requires 8784 hours")
    config, changes = technology_config(raw, technology)
    config["building_thermal"]["simultaneous_mode"] = mode
    spatial = yaml.safe_load((root / SPATIAL).read_text(encoding="utf-8"))
    prepared = preprocess_spatial_quality(config, spatial, timeseries=annual)
    config = prepared.config  # Essential: the reports alone do not alter the model.
    validate_inputs(config, annual)
    representative, assignments = representative_days(annual, config)
    paths = config_chain(path) + [annual_path, root / SPATIAL, root / CASE / "service_scope.json"]
    identities = {str(p.relative_to(root)): file_hash(p) for p in paths}
    contract = {
        "building_thermal": {k: v for k, v in config["building_thermal"].items()
                             if k != "simultaneous_mode"},
        "annual_data_sha256": file_hash(annual_path),
        "storage_boundaries": [{k: s.get(k) for k in ("name", "boundary_mode",
                              "initial_soc_fraction", "final_soc_min_fraction")}
                              for s in config["storages"]],
    }
    return dict(config=config, raw_config=raw, annual=annual, representative=representative,
                assignments=assignments, spatial=prepared, source_identities=identities,
                annual_scope_audit=scope, technology=technology, technology_changes=changes,
                service_contract_sha256=fingerprint(contract),
                spatial_sha256=file_hash(root / SPATIAL),
                spatial_config_sha256=fingerprint(config))


def independent_rc_audit(config: dict, frame: pd.DataFrame, trajectory: pd.DataFrame) -> dict:
    """Recompute from raw input H/C, gains, comfort and exported actual powers.

    Does not call C2 state/residual/comfort helpers or trust exported residuals,
    temperatures at the start of a step, or exported input parameter columns.
    """
    required = {"building", "time_index", "time", "boundary_group", "temperature_end_c",
                "heat_kw", "cooling_kw"}
    if trajectory is None or required - set(trajectory):
        raise ValueError("missing required actual RC trajectory columns")
    frame = frame.reset_index(drop=True)
    section = config["building_thermal"]
    names = {b["name"] for b in section["buildings"]}
    if set(trajectory.building) != names or len(trajectory) != len(names) * len(frame):
        raise ValueError("trajectory does not exactly cover building/time pairs")
    dt = float(config["planning"]["time_step_hours"])
    temperature_tol, power_tol = 1e-6, 1e-6
    rows = []
    for building in section["buildings"]:
        data = trajectory[trajectory.building == building["name"]].sort_values("time_index")
        if data.time_index.tolist() != list(range(len(frame))):
            raise ValueError("duplicate/missing trajectory time_index")
        if not np.array_equal(pd.to_datetime(data.time, utc=True).to_numpy(),
                              pd.to_datetime(frame.time, utc=True).to_numpy()):
            raise ValueError("trajectory timestamps do not match input")
        if data.boundary_group.astype(str).tolist() != frame.boundary_group.astype(str).tolist():
            raise ValueError("trajectory boundaries do not match input")
        data = data.reset_index(drop=True)
        if not np.isfinite(data[["temperature_end_c", "heat_kw", "cooling_kw"]]).all().all():
            raise ValueError("nonfinite RC output")
        h, c = float(building["heat_loss_kw_per_c"]), float(building["thermal_capacity_kwh_per_c"])
        a = float(np.exp(-h * dt / c))
        b = float(-np.expm1(-h * dt / c) / h)
        def series(column_key, scalar_key, default=0.):
            return (frame[building[column_key]].to_numpy(float) if column_key in building
                    else np.full(len(frame), float(building.get(scalar_key, default))))
        gains = series("internal_gain_column", "internal_gain_kw") + series("solar_gain_column", "solar_gain_kw")
        minimum = series("min_temperature_column", "min_temperature_c")
        maximum = series("max_temperature_column", "max_temperature_c")
        outdoor = frame[section.get("outdoor_temperature_column", "outdoor_temp")].to_numpy(float)
        temp, heat, cool = (data[key].to_numpy(float) for key in ("temperature_end_c", "heat_kw", "cooling_kw"))
        for group, indices in frame.groupby("boundary_group", sort=False).groups.items():
            indices = list(indices)
            spec = {**building, **building.get("boundary_conditions", {}).get(str(group), {})}
            if spec.get("boundary_mode", section.get("boundary_mode", "fixed")) != "fixed":
                raise ValueError("competition audit requires declared fixed RC boundaries")
            initial = float(spec["initial_temperature_c"])
            previous = np.r_[initial, temp[indices[:-1]]]
            predicted = a * previous + b * (h * outdoor[indices] + heat[indices] - cool[indices] + gains[indices])
            residual = float(np.max(np.abs(temp[indices] - predicted)))
            comfort = float(max(0., np.max(minimum[indices] - temp[indices]), np.max(temp[indices] - maximum[indices])))
            lo = float(spec.get("final_temperature_c", spec.get("final_min_temperature_c", initial)))
            hi = float(spec.get("final_temperature_c", spec.get("final_max_temperature_c", np.inf)))
            terminal = float(max(0., lo - temp[indices[-1]], temp[indices[-1]] - hi))
            simultaneous = int(np.sum((heat[indices] > power_tol) & (cool[indices] > power_tol)))
            negative = float(max(0., -heat[indices].min(), -cool[indices].min()))
            rows.append(dict(building=building["name"], boundary_group=group,
                             max_rc_residual_c=residual, max_comfort_violation_c=comfort,
                             terminal_violation_c=terminal, simultaneous_hours=simultaneous,
                             negative_power_kw=negative,
                             passed=max(residual, comfort, terminal) <= temperature_tol
                             and simultaneous == 0 and negative <= power_tol))
    return {"passed": all(row["passed"] for row in rows), "checks": rows,
            "temperature_tolerance_c": temperature_tol, "power_tolerance_kw": power_tol,
            "scope": "independent_1R1C_recompute_and_end_sample_comfort_not_local_measurement_validation"}


def corrected_replay_cost(planning_summary: dict, replay_summary: dict, *, annual: bool) -> dict:
    components = replay_summary["components_yuan_per_year"]
    included_capex = float(components.get("annualized_capex", 0.))
    capital = float(planning_summary["components_yuan_per_year"].get("annualized_capex", 0.))
    expected = capital if annual else 0.
    if not np.isclose(included_capex, expected, atol=1e-5, rtol=1e-8):
        raise ValueError("fixed replay capital differs from frozen plan / declared period scope")
    operating_objective = float(replay_summary["objective_yuan_per_year"]) - included_capex
    cash_keys = ("operating_cost", "carbon_cost", "fixed_om_cost", "startup_shutdown_cost",
                 "biomass_fuel_cost", "cooling_operating_cost", "cooling_carbon_cost")
    cash = sum(float(components.get(key, 0.)) for key in cash_keys)
    return {"planned_annualized_capex_yuan": capital,
            "replay_period_operating_objective_yuan": operating_objective,
            "replay_period_cash_operating_yuan": cash,
            "capital_already_in_model_objective_yuan": included_capex,
            "full_service_year_total_objective_yuan": capital + operating_objective if annual else None,
            "full_service_year_total_cash_cost_yuan": capital + cash if annual else None,
            "objective_contains_penalties": True,
            "scope": "full_service_year" if annual else "period_operating_only_no_annual_total_claim"}


def verify_manifest(directory: Path) -> dict:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("complete") is not True:
        raise ValueError("cannot consume incomplete evidence")
    for relative, expected in manifest["files"].items():
        target = (directory / relative).resolve()
        if not target.is_relative_to(directory.resolve()) or file_hash(target) != expected:
            raise ValueError(f"evidence hash mismatch: {relative}")
    return manifest


@contextmanager
def solver_lock(root: Path):
    """Serializes this runner; C0 also coordinates other processes on this host."""
    path = root / OUTPUT / "solver.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.write(descriptor, json.dumps({"pid": os.getpid(), "utc": datetime.now(timezone.utc).isoformat()}).encode())
        os.close(descriptor)
        descriptor = None
        yield
    finally:
        if descriptor is not None:
            os.close(descriptor)
        path.unlink(missing_ok=True)


def _export_aggregation(case: dict, trajectory: pd.DataFrame, destination: Path) -> None:
    powers = {name: group.sort_values("time_index").heat_kw.to_numpy()
              for name, group in trajectory.groupby("building")}
    # Solver numerical noise within the declared power tolerance is recorded
    # in the raw trajectory/audit; aggregation API requires nonnegative values.
    powers = {name: np.maximum(values, 0.) for name, values in powers.items()}
    totals = aggregate_delivered_power(case["spatial"].hierarchy, powers)
    rows = []
    for level, mapping in (("node", totals.node_grade_kw), ("district", totals.district_grade_kw),
                           ("system", totals.system_grade_kw)):
        for (area, grade), values in mapping.items():
            rows.extend({"level": level, "area": area, "grade": grade, "time_index": i,
                         "actual_delivered_heat_kw": value} for i, value in enumerate(values))
    pd.DataFrame(rows).to_csv(destination / "delivered_heat_hierarchy.csv", index=False)


def run_solve(root: Path, destination: Path, case: dict, config: dict, frame: pd.DataFrame,
              *, scope: str, time_limit: float, mip_gap: float, objective: str = "total_cost",
              epsilon: float | None = None, parent: dict | None = None) -> dict:
    """One immutable run directory, including failures and full input identities."""
    destination.mkdir(parents=True, exist_ok=False)
    started = perf_counter()
    before = code_identity(root)
    metadata = dict(scope=scope, technology=case["technology"],
                    service_contract_sha256=case["service_contract_sha256"],
                    spatial_sha256=case["spatial_sha256"],
                    spatial_config_sha256=case["spatial_config_sha256"],
                    config_sha256=fingerprint(config), code_sha256=before,
                    source_sha256=case["source_identities"], hours=len(frame),
                    weighted_hours=float(frame.period_weight.sum()),
                    simultaneous_mode=config["building_thermal"]["simultaneous_mode"],
                    time_limit_seconds=time_limit, requested_mip_gap=mip_gap,
                    objective=objective, epsilon_kg=epsilon,
                    parent_investment_sha256=parent.get("investment_sha256") if parent else None,
                    started_utc=datetime.now(timezone.utc).isoformat(),
                    environment=dict(python=sys.version, scipy=scipy.__version__, numpy=np.__version__,
                                     pandas=pd.__version__, platform=platform.platform(), executable=sys.executable))
    write_json(destination / "run_record.json", {**metadata, "state": "running"})
    write_json(destination / "model_config.json", config)
    write_json(destination / "spatial_candidates.json", case["spatial"].candidate_report)
    write_json(destination / "source_grades.json", case["spatial"].source_grade_report)
    write_json(destination / "technology_changes.json", case["technology_changes"])
    if scope in ("representative", "frontier_representative"):
        coverage = representative_period_coverage_audit(config, frame, case["annual"])
        coverage.to_csv(destination / "representative_coverage.csv", index=False)
        case["assignments"].to_csv(destination / "day_assignments.csv", index=False)
    if scope == "season":
        write_json(destination / "season_scope_audit.json", case["season_scope_audit"])
        write_json(destination / "season_assumptions.json", case["season_assumptions"])
    frame.to_csv(destination / "model_timeseries.csv", index=False)
    model = artifacts = None
    try:
        validate_inputs(config, frame)
        build_start = perf_counter()
        model = HeatPlanningModel(config, frame)
        anchor = building_audit_identity(config, model.ts)
        anchor_dir = root / OUTPUT / "input_anchors"
        anchor_dir.mkdir(exist_ok=True)
        anchor_path = anchor_dir / (fingerprint({"destination": str(destination), **anchor}) + ".json")
        if anchor_path.exists():
            raise FileExistsError("pre-solve audit anchor already exists")
        write_json(anchor_path, anchor)
        metadata["c6_input_anchor_file"] = str(anchor_path.relative_to(root))
        metadata["c6_input_anchor_sha256"] = file_hash(anchor_path)
        model.build()
        build_seconds = perf_counter() - build_start
        if epsilon is not None:
            row, constant = model.total_service_emissions_expression()
            model.problem.add_constraint(row, ub=float(epsilon) - constant)
        profile = model_structure_profile(model, build_seconds=build_seconds)
        profile["binary_variables"] = sum(bool(integer and lo >= 0 and hi <= 1)
            for integer, lo, hi in zip(model.problem.integrality, model.problem.lower, model.problem.upper))
        write_json(destination / "model_profile.json", profile)
        solve_start = perf_counter()
        try:
            artifacts = model.solve(time_limit=time_limit, mip_gap=mip_gap, objective_mode=objective)
        finally:
            profile["solve_and_extract_seconds"] = perf_counter() - solve_start
            profile["matrix_compile_seconds"] = model.problem.last_matrix_compile_seconds
            profile["matrix_compile_cache_hit"] = model.problem.last_matrix_cache_hit
            write_json(destination / "model_profile.json", profile)
        export_start = perf_counter()
        artifacts.investments.to_csv(destination / "investments.csv", index=False)
        artifacts.dispatch.to_csv(destination / "dispatch.csv", index=False)
        artifacts.building_trajectory.to_csv(destination / "building_trajectory.csv", index=False)
        artifacts.building_comfort.to_csv(destination / "building_comfort.csv", index=False)
        np.savez_compressed(destination / "solution.npz", values=artifacts.values)
        pd.DataFrame({"variable": model.problem.names}).to_csv(destination / "variables.csv.gz", index=False)
        _export_aggregation(case, artifacts.building_trajectory, destination)
        profile["export_seconds"] = perf_counter() - export_start
        audit_start = perf_counter()
        rc = independent_rc_audit(config, frame, artifacts.building_trajectory)
        write_json(destination / "independent_rc_audit.json", rc)
        storage_config = freeze_investment_plan(config, artifacts, relax_reliability=False) if parent is None else config
        storage = seasonal_storage_postcheck(storage_config, artifacts.dispatch, expected_timeseries=frame)
        storage.to_csv(destination / "storage_postcheck.csv", index=False)
        summary = artifacts.summary
        # Explicit RC delivery fields let the independent auditor compare the
        # service ledger even when separate zero/nonzero fixed grades coexist.
        for service in ("heat", "cooling"):
            coefficients = model.metric_coefficients[f"building_{service}_delivered_kwh"]
            summary[f"building_{service}_delivered_kwh_per_year"] = float(sum(
                coefficient * artifacts.values[index] for index, coefficient in coefficients.items()))
        c6 = audit_building_trajectory(config, model.ts, artifacts.building_trajectory,
                                      artifacts.dispatch, summary,
                                      require_continuous_fixed=scope in ("annual", "season"),
                                      expected_input_identity=anchor)
        write_json(destination / "c6_building_audit.json", c6)
        summary["independent_building_audit_passed"] = c6["passed"]
        summary["independent_building_audit_metrics"] = c6["metrics"]
        summary["independent_building_audit_scope"] = c6["scope"]
        epsilon_passed = epsilon is None or float(summary["total_service_emissions_kg_per_year"]) <= epsilon + 1e-6 + 1e-8 * abs(epsilon)
        audit = dict(independent_rc_passed=rc["passed"], numerical_passed=bool(summary["numerical_audit_passed"]),
                     building_passed=bool(summary["building_comfort_state_passed"]), epsilon_passed=epsilon_passed,
                     c6_building_audit_passed=c6["passed"])
        # Retain the full storage table; core numerical rows enforce all SOC
        # transitions. The table's own explicit boolean flags also must pass.
        flags = [c for c in storage if c.endswith("passed") or c.endswith("_pass")]
        audit["storage_postcheck_passed"] = bool(all(storage[c].all() for c in flags))
        audit["code_stable_during_run"] = before == code_identity(root)
        if scope == "annual":
            write_json(destination / "annual_scope_audit.json", explicit_annual_scope_audit(config, frame))
        if parent:
            audit["new_investment_zero"] = bool(artifacts.investments.loc[
                ~artifacts.investments.category.isin(["pipe_retrofit", "terminal_temperature_retrofit"]),
                "value"].abs().max() <= 1e-6) if not artifacts.investments.empty else True
            cost = corrected_replay_cost(parent["planning_summary"], summary, annual=scope == "annual")
            write_json(destination / "corrected_cost.json", cost)
            summary["corrected_cost"] = cost
        profile["audit_seconds"] = perf_counter() - audit_start
        profile["wall_seconds"] = perf_counter() - started
        summary["runner_audit"] = audit
        summary["claim"] = ("strict_formulation_feasible_incumbent" if metadata["simultaneous_mode"] == "exclusive"
                            else "relaxed_formulation_incumbent_and_relaxation_bound; strict_model_gap_not_claimed")
        write_json(destination / "summary.json", summary)
        write_json(destination / "model_profile.json", profile)
        if not all(audit.values()):
            raise ValueError(f"incumbent failed acceptance audit: {audit}")
        if scope == "representative":
            fixed = freeze_investment_plan(config, artifacts, relax_reliability=False)
            # Carry the declared full-year scope from the actual C1 config.
            fixed["planning"]["annual_scope"] = deepcopy(case["config"]["planning"]["annual_scope"])
            if "fixed_investment_accounting" not in fixed:
                raise ValueError("C0 fixed annual investment accounting integration required")
            freeze = investment_freeze_audit(config, fixed, artifacts)
            freeze.to_csv(destination / "investment_freeze_audit.csv", index=False)
            if not freeze.freeze_ok.all():
                raise ValueError("investment freezing failed")
            investment_hash = fingerprint(artifacts.investments.to_dict("records"))
            plan = dict(fixed_config=fixed, planning_summary=summary, investment_sha256=investment_hash,
                        fixed_config_sha256=fingerprint(fixed), service_contract_sha256=case["service_contract_sha256"],
                        spatial_config_sha256=case["spatial_config_sha256"], source_sha256=case["source_identities"],
                        code_sha256=before, technology=case["technology"], spatial_sha256=case["spatial_sha256"])
            write_json(destination / "fixed_plan.json", plan)
            metadata["investment_sha256"] = investment_hash
        write_json(destination / "run_record.json", {**metadata, "state": "accepted",
                   "wall_seconds": perf_counter() - started, "solver_status": summary["solver_status"],
                   "mip_gap": summary["mip_gap"], "audit": audit})
        # This is deliberately the last write on successful execution.
        files = {str(p.relative_to(destination)): file_hash(p) for p in sorted(destination.rglob("*")) if p.is_file()}
        write_json(destination / "manifest.json", {**metadata, "complete": True, "files": files,
                   "formal_scope": scope, "independent_external_certificate": "pending_C6",
                   "is_full_annual_evidence": scope == "annual",
                   "is_strict_formulation": metadata["simultaneous_mode"] == "exclusive"})
        return summary
    except Exception as error:
        record = {**metadata, "state": "failed", "exception_type": type(error).__name__,
                  "message": str(error), "traceback": traceback.format_exc(), "wall_seconds": perf_counter() - started}
        status = re.search(r"status=(\d+)", str(error))
        record["solver_status"] = int(status.group(1)) if status else None
        record["mip_gap"] = None
        if artifacts is not None:
            record["solver_status"] = artifacts.summary.get("solver_status")
            record["mip_gap"] = artifacts.summary.get("mip_gap")
        write_json(destination / "failure.json", record)
        write_json(destination / "run_record.json", record)
        raise


def load_plan(directory: Path, root: Path, case: dict) -> dict:
    verify_manifest(directory)
    plan = json.loads((directory / "fixed_plan.json").read_text(encoding="utf-8"))
    for key in ("service_contract_sha256", "spatial_sha256", "spatial_config_sha256", "technology"):
        if plan[key] != case[key]:
            raise ValueError(f"planning/replay identity mismatch: {key}")
    if plan["source_sha256"] != case["source_identities"] or plan["code_sha256"] != code_identity(root):
        raise ValueError("source/code changed since planning; replan before formal replay")
    if fingerprint(plan["fixed_config"]) != plan["fixed_config_sha256"]:
        raise ValueError("fixed investment config hash mismatch")
    return plan


def compare_evidence(destination: Path, optimized_annual: Path, baseline_annual: Path,
                     optimized_season: Path, baseline_season: Path) -> dict:
    """Require matched service and per-technology annual/winter investment."""
    paths = {"optimized_annual": optimized_annual, "baseline_annual": baseline_annual,
             "optimized_season": optimized_season, "baseline_season": baseline_season}
    manifests = {name: verify_manifest(path) for name, path in paths.items()}
    anchor = manifests["optimized_annual"]
    for name, manifest in manifests.items():
        annual = name.endswith("annual")
        if manifest.get("formal_scope") != ("annual" if annual else "season"):
            raise ValueError("comparison requires actual annual and season evidence")
        if manifest.get("hours") != (8784 if annual else 2928):
            raise ValueError("comparison requires the complete declared C1 scopes")
        for key in ("service_contract_sha256", "spatial_sha256", "source_sha256", "code_sha256"):
            if manifest.get(key) != anchor.get(key):
                raise ValueError(f"comparison service/input/code mismatch: {key}")
        if manifest.get("technology") != ("optimized" if name.startswith("optimized") else "ashp_backup"):
            raise ValueError("comparison technology label does not match manifest")
    for technology in ("optimized", "baseline"):
        year, winter = manifests[technology + "_annual"], manifests[technology + "_season"]
        if not year.get("parent_investment_sha256") or year["parent_investment_sha256"] != winter.get("parent_investment_sha256"):
            raise ValueError("annual/winter investment identity mismatch")
    rows = []
    for label, path in paths.items():
        summary = json.loads((path / "summary.json").read_text(encoding="utf-8"))
        if not summary.get("runner_audit") or not all(summary["runner_audit"].values()):
            raise ValueError("comparison source did not pass all acceptance checks")
        cost = summary["corrected_cost"]
        rows.append({"case": label, "run_directory": str(path),
                     "investment_sha256": manifests[label]["parent_investment_sha256"],
                     "annual_total_cash_cost_yuan": cost["full_service_year_total_cash_cost_yuan"],
                     "annual_total_objective_yuan": cost["full_service_year_total_objective_yuan"],
                     "period_operating_objective_yuan": cost["replay_period_operating_objective_yuan"],
                     "planned_annualized_capex_yuan": cost["planned_annualized_capex_yuan"],
                     "period_emissions_kg": summary["total_service_emissions_kg_per_year"],
                     "actual_RC_heat_kwh": summary["building_heat_delivered_kwh_per_year"],
                     "actual_RC_cooling_kwh": summary["building_cooling_delivered_kwh_per_year"],
                     "solver_status": summary["solver_status"], "mip_gap": summary["mip_gap"],
                     "formulation": summary["building_mode_formulation"]})
    table = pd.DataFrame(rows).set_index("case")
    benefits = {}
    for field in ("annual_total_cash_cost_yuan", "annual_total_objective_yuan", "period_emissions_kg"):
        opt, base = float(table.loc["optimized_annual", field]), float(table.loc["baseline_annual", field])
        benefits[field] = {"optimized": opt, "baseline": base, "baseline_minus_optimized": base - opt,
                           "reduction_fraction": (base - opt) / base if base else None}
    report = {"matched_service_contract_sha256": anchor["service_contract_sha256"],
              "scope": "same_comfort_weather_H_C_and_boundary_contract; optimized_dispatch_per_technology",
              "annual_comparison": benefits, "winter_has_no_annual_total_cost_claim": True,
              "external_C6_full_system_certificate": "pending_separate_acceptance",
              "sources": {name: {"directory": str(path), "manifest_sha256": file_hash(path / "manifest.json")}
                          for name, path in paths.items()}}
    destination.mkdir(parents=True, exist_ok=False)
    table.to_csv(destination / "technology_comparison.csv")
    write_json(destination / "comparison.json", report)
    write_json(destination / "manifest.json", {"complete": True, "scope": "matched_service_comparison",
        "files": {p.name: file_hash(p) for p in destination.iterdir() if p.is_file()}, **report})
    return report


def run_frontier(root: Path, destination: Path, case: dict, config: dict, *, time_limit: float, mip_gap: float) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    rows, failures = [], []
    def point(name, objective="total_cost", cap=None):
        try:
            summary = run_solve(root, destination / name, case, config, case["representative"],
                                scope="frontier_representative", time_limit=time_limit,
                                mip_gap=mip_gap, objective=objective, epsilon=cap)
            rows.append({"run": name, "epsilon_kg": cap, **{key: summary[key] for key in (
                "objective_yuan_per_year", "total_service_emissions_kg_per_year", "solver_status", "mip_gap")}})
            return summary
        except Exception as error:
            failures.append({"run": name, "message": str(error)})
            return None
    cost = point("cost_endpoint")
    carbon = point("carbon_endpoint", "emissions")
    if cost and carbon:
        lo, hi = carbon["total_service_emissions_kg_per_year"], cost["total_service_emissions_kg_per_year"]
        if lo <= hi + 1e-6:
            for i, cap in enumerate(np.linspace(lo, hi, 5)):
                point(f"epsilon_{i + 1}", cap=float(cap + 1e-7 + abs(cap) * 1e-8))
        else:
            failures.append({"run": "epsilon_grid", "message": "incumbent endpoints inverted; no valid grid"})
    points = pd.DataFrame(rows)
    points.to_csv(destination / "all_points.csv", index=False)
    front = nondominated_cost_emission_points(points) if not points.empty else points
    front.to_csv(destination / "nondominated.csv", index=False)
    report = dict(requested_epsilon_points=5, actual_epsilon_points=sum(r["run"].startswith("epsilon_") for r in rows),
                  actual_nondominated_points=len(front), failures=failures,
                  scope="sampled_representative_incumbents_not_full_year_frontier",
                  endpoints="single_objective_incumbents; no_lexicographic_optimality_claim",
                  financial_objective="inherited_total_objective_including_carbon_and_exergy_preferences")
    write_json(destination / "frontier_report.json", report)
    if not failures:
        files = {str(p.relative_to(destination)): file_hash(p) for p in sorted(destination.rglob("*")) if p.is_file()}
        write_json(destination / "manifest.json", {"complete": True, "files": files, **report})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["prepare", "smoke", "plan", "annual", "season", "frontier", "compare"])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--technology", choices=["optimized", "ashp_backup"], default="optimized")
    parser.add_argument("--mode", choices=["exclusive", "relaxed"], default="exclusive")
    parser.add_argument("--time-limit", type=float, default=60.)
    parser.add_argument("--mip-gap", type=float, default=0.005)
    parser.add_argument("--hours", choices=[24, 48], type=int, default=24)
    parser.add_argument("--smoke-weather", choices=["cold", "hot"], default="cold")
    parser.add_argument("--plan-dir", type=Path)
    parser.add_argument("--optimized-annual", type=Path)
    parser.add_argument("--baseline-annual", type=Path)
    parser.add_argument("--optimized-season", type=Path)
    parser.add_argument("--baseline-season", type=Path)
    parser.add_argument("--allow-heavy", action="store_true", help="Only after C0 explicitly releases frozen core and solver queue")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if not args.run_id or Path(args.run_id).name != args.run_id or args.run_id in (".", ".."):
        parser.error("run-id must be a single directory name")
    if not np.isfinite(args.time_limit) or args.time_limit <= 0 or not 0 <= args.mip_gap <= 1:
        parser.error("finite positive time limit and gap in [0,1] required")
    if args.stage == "smoke" and args.time_limit > 60:
        parser.error("short diagnostics are limited to 60 seconds per solve")
    if args.stage in ("plan", "annual", "season", "frontier") and not args.allow_heavy:
        parser.error("wait for C0 core freeze / heavy solver release, then supply --allow-heavy")
    destination = root / OUTPUT / args.run_id
    try:
        if args.stage == "compare":
            sources = [args.optimized_annual, args.baseline_annual, args.optimized_season, args.baseline_season]
            if any(path is None for path in sources):
                parser.error("compare requires all four annual/season source directories")
            compare_evidence(destination, *(path.resolve() for path in sources))
            return 0
        case = load_case(root, args.technology, args.mode)
        config = deepcopy(case["config"])
        if args.stage == "prepare":
            destination.mkdir(parents=True, exist_ok=False)
            case["representative"].to_csv(destination / "representative.csv", index=False)
            case["assignments"].to_csv(destination / "day_assignments.csv", index=False)
            write_json(destination / "prepared_config.json", config)
            write_json(destination / "case_identity.json", {k: case[k] for k in (
                "source_identities", "annual_scope_audit", "technology_changes", "service_contract_sha256",
                "spatial_sha256", "spatial_config_sha256")})
            write_json(destination / "spatial_candidates.json", case["spatial"].candidate_report)
            representative_period_coverage_audit(config, case["representative"], case["annual"]).to_csv(
                destination / "representative_coverage.csv", index=False)
            write_json(destination / "manifest.json", {"complete": True, "scope": "input_preparation_only",
                "files": {p.name: file_hash(p) for p in destination.iterdir() if p.is_file()}})
            return 0
        if args.stage in ("smoke", "plan", "frontier"):
            config["planning"].pop("annual_scope", None)
            frame = case["representative"]
            scope = "representative"
            if args.stage == "smoke":
                annual = case["annual"]
                index = annual.outdoor_temp.idxmin() if args.smoke_weather == "cold" else annual.outdoor_temp.idxmax()
                start = int(index // 24 * 24)
                start = min(start, len(annual) - args.hours)
                frame = annual.iloc[start:start + args.hours].copy().reset_index(drop=True)
                frame["boundary_group"] = "short_diagnostic"
                # No policy or yearly ground ratio is silently relaxed. This
                # short window is intentionally not an annual certificate.
                scope = "short_diagnostic"
            with solver_lock(root):
                if args.stage == "frontier":
                    run_frontier(root, destination, case, config, time_limit=args.time_limit, mip_gap=args.mip_gap)
                else:
                    run_solve(root, destination, case, config, frame, scope=scope,
                              time_limit=args.time_limit, mip_gap=args.mip_gap)
        else:
            if args.plan_dir is None:
                parser.error("annual/season require --plan-dir pointing to completed representative plan")
            plan = load_plan(args.plan_dir.resolve(), root, case)
            config = deepcopy(plan["fixed_config"])
            frame = case["annual"]
            if args.stage == "season":
                service_scope = json.loads((root / CASE / "service_scope.json").read_text(encoding="utf-8"))
                frame, scope_audit = prepare_heating_season_timeseries(config, frame, service_scope)
                config, assumptions = _season_config(config, False)
                # Refuse the known pre-freeze seasonal bug, rather than deleting
                # per-source ground constraints locally and masking core drift.
                cooling = config.get("multi_energy_services", {}).get("cooling", {})
                if any(s.get("ground_thermal_balance") and s["ground_thermal_balance"].get("enforce", True)
                       for s in cooling.get("sources", {}).values()):
                    raise ValueError("C0 seasonal fix missing: per-source annual ground balance remains")
                case = {**case, "season_scope_audit": scope_audit, "season_assumptions": assumptions}
            with solver_lock(root):
                run_solve(root, destination, case, config, frame, scope=args.stage,
                          time_limit=args.time_limit, mip_gap=args.mip_gap, parent=plan)
        return 0
    except Exception as error:
        # Also preserve errors before model construction (identity, gate, IO).
        if not destination.exists():
            destination.mkdir(parents=True)
            write_json(destination / "failure.json", {"stage": args.stage, "exception_type": type(error).__name__,
                "message": str(error), "traceback": traceback.format_exc(), "complete": False})
        print(json.dumps({"stage": args.stage, "run_id": args.run_id, "error": str(error)}, ensure_ascii=True))
        return 1
