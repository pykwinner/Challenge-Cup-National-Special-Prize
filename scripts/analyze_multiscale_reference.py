"""Analyze a completed frozen multiscale run, without importing model code.

All physical/accounting KPIs are rebuilt from hourly exported trajectories and
source dispatch. Raw solver bounds are used only after their cost identity is
checked. This script neither edits the raw run nor performs additional solves.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_RUN = ROOT / "docs/implementation_20261002_followup/parallel_stage_03/multiscale/run_01"
CASES = ("independent_A", "independent_B", "joint_unlimited", "coupled_flexible",
         "coupled_A_fixed20", "coupled_B_fixed20", "coupled_all_fixed20")
VALID = {"passed", "feasible_unproven"}
POWER_TOL = TEMP_TOL = 1e-6
COST_TOL = 1e-5
# This is a fixed post-solve analysis rule, not part of the frozen solve
# protocol or a preregistered confirmation. No physical parameters/cap are tuned.
WINDOW_RULE = ("Earliest hour with maximum positive independent-A+B excess over frozen K; "
               "if no positive excess, earliest coldest outdoor hour. Show the local "
               "calendar day and adjacent days (72 h), clamped at window endpoints.")


def require(value: bool, message: str) -> None:
    if not bool(value):
        raise ValueError(message)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def finite(value: Any) -> float | None:
    if isinstance(value, (bool, np.bool_)):
        return None
    try:
        x = float(value)
    except (ValueError, TypeError):
        return None
    return x if np.isfinite(x) else None


def clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, np.generic):
        return clean(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return None
    if isinstance(value, Path):
        return value.as_posix()
    return value


def save(path: Path, value: Any) -> None:
    path.write_text(json.dumps(clean(value), ensure_ascii=False, indent=2,
                               allow_nan=False) + "\n", encoding="utf-8")


def numbers(frame: pd.DataFrame, column: str) -> np.ndarray:
    require(column in frame, f"missing column: {column}")
    x = pd.to_numeric(frame[column], errors="raise").to_numpy(dtype=float)
    require(np.isfinite(x).all(), f"nonfinite column: {column}")
    return x


def objective_interval(summary: dict, bill: float, accepted: bool) -> dict:
    """Keep objective units and constants explicit; never infer bounds from gap."""
    quality = summary.get("solution_quality", {})
    stages = quality.get("solver_stages", summary.get("solver_stages", []))
    result = {"unit": "CNY_120_day_energy_charge", "lower_cny": None,
              "upper_cny": None, "raw_solver_lower": None, "raw_solver_upper": None,
              "solver_objective_constant": None, "cost_identity_checked": False,
              "quality": quality.get("quality", "missing_evidence"),
              "lower_provenance": "unavailable", "mip_gap": finite(summary.get("mip_gap")),
              "solver_status": summary.get("solver_status"),
              "gap_scaling_performed": False}
    if not accepted or len(stages) != 1:
        return result
    stage = stages[0]
    mode = stage.get("objective_mode", summary.get("objective_mode"))
    unit = stage.get("objective_unit")
    upper = finite(stage.get("objective_value", summary.get("objective_value")))
    lower = finite(stage.get("mip_dual_bound", summary.get("mip_dual_bound")))
    constant = finite(stage.get("solver_objective_constant", summary.get("solver_objective_constant")))
    result.update(raw_solver_lower=lower, raw_solver_upper=upper,
                  solver_objective_constant=constant, raw_objective_unit=unit,
                  raw_objective_mode=mode)
    checked = (mode == "total_cost" and unit == "yuan_model_objective" and upper is not None
               and constant is not None and abs(upper + constant - bill) <= COST_TOL
               and stage.get("numerical_objective_checked") is True
               and stage.get("feasible_incumbent_audited") is True)
    result["cost_identity_checked"] = checked
    if checked:
        result["upper_cny"] = upper + constant
        if lower is not None and lower <= upper + COST_TOL:
            result["lower_cny"] = min(lower, upper) + constant
            result["lower_provenance"] = "reported_solver_dual_bound_plus_constant"
        # Preserve a missing raw dual bound. An LP optimal-termination certificate
        # can still support a point interval, explicitly at solver tolerances.
        elif (stage.get("optimality_proven_within_solver_tolerance") is True
              and finite(stage.get("solver_integer_variables")) == 0):
            result["lower_cny"] = upper + constant
            result["lower_provenance"] = "continuous_LP_optimal_termination_certificate_at_solver_tolerances"
        result["solver_tolerance_limited"] = True
    return result


def power_metrics(power: np.ndarray, price: np.ndarray, ef: np.ndarray,
                  dt: float, cap: float) -> dict:
    excess = np.maximum(power - cap, 0.)
    return {"electricity_kwh": float(power.sum() * dt),
            "bill_cny": float(np.dot(power, price) * dt),
            "average_EF_carbon_kg": float(np.dot(power, ef) * dt),
            "peak_kw": float(power.max()),
            "common_grid_binding_hours": int(np.count_nonzero(np.abs(power - cap) <= POWER_TOL)),
            "common_grid_exceedance_hours": int(np.count_nonzero(power > cap + POWER_TOL)),
            "common_grid_max_excess_kw": float(excess.max()),
            "common_grid_sum_excess_kwh": float(excess.sum() * dt)}


def load_case(run: Path, record: dict, cap: float) -> dict:
    name, status = record["name"], record["status"]
    folder = run / "results" / name
    base = {"name": name, "status": status, "accepted_incumbent": status in VALID,
            "failure": read(folder / "failure.json") if (folder / "failure.json").exists() else None}
    if status not in VALID:
        return base
    cfg = yaml.safe_load((folder / "config.yaml").read_text(encoding="utf-8"))
    inp = pd.read_csv(folder / "input.csv")
    tr = pd.read_csv(folder / "building_trajectory.csv")
    ds = pd.read_csv(folder / "dispatch.csv")
    summary = read(folder / "summary.json")
    audit = read(folder / "independent_audit.json")
    require(audit.get("passed") is True, f"{name}: exported independent audit did not pass")
    n = len(inp)
    require(n == 2880, f"{name}: expected complete 2880 h")
    dt = float(cfg["planning"]["time_step_hours"])
    require(dt == 1 and np.all(numbers(inp, "period_weight") == 1)
            and np.all(numbers(inp, "scenario_probability") == 1), f"{name}: physical weights")
    time = pd.DatetimeIndex(pd.to_datetime(inp.time, utc=True)).tz_convert("Asia/Shanghai")
    require(np.all(np.diff(time.asi8) == 3600 * 10**9), f"{name}: hourly clock")
    price = numbers(inp, str(cfg["planning"].get("electricity_price_column", "electricity_price")))
    ef = numbers(inp, "grid_emission_factor")
    require(np.all(ef == ef[0]), f"{name}: expected fixed average EF, not marginal EF")
    valley = inp.tariff_bucket.astype(str).eq("valley").to_numpy()
    building_rows, building_series = [], {}
    power, heat_total, cool_total = np.zeros(n), np.zeros(n), np.zeros(n)
    require(len(tr) == n * len(cfg["building_thermal"]["buildings"]), f"{name}: trajectory size")
    for b in cfg["building_thermal"]["buildings"]:
        bn, node = b["name"], b["node"]
        rows = tr.loc[tr.building == bn].sort_values("time_index")
        require(len(rows) == n and np.array_equal(numbers(rows, "time_index"), np.arange(n)),
                f"{name}/{bn}: complete trajectory")
        require(np.array_equal(pd.to_datetime(rows.time, utc=True).array.asi8, time.asi8),
                f"{name}/{bn}: trajectory clock differs")
        heat, cool = numbers(rows, "heat_kw"), numbers(rows, "cooling_kw")
        start, end = numbers(rows, "temperature_start_c"), numbers(rows, "temperature_end_c")
        eh, ec = np.zeros(n), np.zeros(n)
        sources = [s for s in cfg["sources"] if s["node"] == node]
        require(len(sources) == 1, f"{name}/{bn}: expected one declared ideal source")
        for s in sources:
            eta = float(s["efficiency"])
            cs = cfg["multi_energy_services"]["cooling"]["sources"][s["name"]]
            eer = float(cs["eer"])
            require(eta == eer == 1, f"{name}/{bn}: declared ideal eta/EER=1")
            for category, grade, expected, divisor in (
                    ("source_heat", b["heat_grade"], heat, eta),
                    ("cooling_output", "cooling", cool, eer)):
                source_rows = ds.loc[(ds.category == category) & (ds.asset == s["name"])
                                     & (ds.grade == grade)].sort_values("time_index")
                require(len(source_rows) == n and
                        np.array_equal(numbers(source_rows, "time_index"), np.arange(n)),
                        f"{name}/{s['name']}/{category}: hourly dispatch incomplete")
                require((source_rows.node == node).all() and (source_rows.unit == "kW").all(),
                        f"{name}: dispatch node/unit mismatch")
                require(np.array_equal(pd.to_datetime(source_rows.time, utc=True).array.asi8, time.asi8),
                        f"{name}: dispatch clock mismatch")
                values = numbers(source_rows, "value")
                require(np.max(np.abs(values - expected)) <= POWER_TOL,
                        f"{name}/{bn}: source/building service mismatch")
                if category == "source_heat":
                    eh += values / divisor
                else:
                    ec += values / divisor
        bp = eh + ec
        require(bp.min() >= -POWER_TOL, f"{name}: negative electricity")
        power += bp
        heat_total += heat
        cool_total += cool
        metrics = power_metrics(bp, price, ef, dt, cap)
        metrics.pop("common_grid_binding_hours")
        metrics.pop("common_grid_exceedance_hours")
        metrics.pop("common_grid_max_excess_kw")
        metrics.pop("common_grid_sum_excess_kwh")
        energy = metrics["electricity_kwh"]
        building_rows.append({"name": name, "building": bn, "status": status, **metrics,
                              "heat_kwh": float(heat.sum() * dt), "cooling_kwh": float(cool.sum() * dt),
                              "valley_electricity_kwh": float(bp[valley].sum() * dt),
                              "valley_electricity_share": float(bp[valley].sum() * dt / energy) if energy > 0 else None,
                              "min_end_temperature_c": float(end.min()), "max_end_temperature_c": float(end.max()),
                              "mean_end_temperature_c": float(end.mean()),
                              "thermal_time_constant_h": float(b["thermal_capacity_kwh_per_c"] / b["heat_loss_kw_per_c"]),
                              "H_kw_per_c": float(b["heat_loss_kw_per_c"]), "C_kwh_per_c": float(b["thermal_capacity_kwh_per_c"]),
                              "initial_start_c": float(start[0]), "final_end_c": float(end[-1])})
        building_series[bn] = {"heat": heat, "cool": cool, "electric": bp,
                               "start_temperature": start, "end_temperature": end}
    metrics = power_metrics(power, price, ef, dt, cap)
    has_grid = bool(cfg.get("grid_connection"))
    actual_cap = float(cfg["grid_connection"]["existing_capacity_kw"]) if has_grid else None
    if has_grid:
        require(abs(actual_cap - cap) <= POWER_TOL, f"{name}: frozen cap differs")
        require(cfg["grid_connection"]["firm_capacity_method"] == "portfolio_dispatch", f"{name}: firm method")
        require(metrics["common_grid_max_excess_kw"] <= POWER_TOL, f"{name}: actual shared-grid violation")
    metrics.update(name=name, status=status, building_count=len(building_rows),
                   heat_kwh=float(heat_total.sum() * dt), cooling_kwh=float(cool_total.sum() * dt),
                   actual_grid_capacity_kw=actual_cap, comparison_common_capacity_kw=cap,
                   actual_grid_binding_hours=metrics["common_grid_binding_hours"] if has_grid else None,
                   grid_metric_scope="actual_shared_limit" if has_grid else "diagnostic_counterfactual_common_K",
                   average_EF_kg_per_kwh=float(ef[0]), physical_audit_passed=True,
                   bill_scope="2880h energy charge only; no annual scaling",
                   solution_quality=summary.get("solution_quality", {}).get("quality", "missing_evidence"))
    for key, raw_key in (("bill_cny", "objective_yuan_per_year"),
                         ("electricity_kwh", "electricity_kwh_per_year"),
                         ("average_EF_carbon_kg", "total_service_emissions_kg_per_year")):
        require(abs(metrics[key] - float(summary[raw_key])) <= COST_TOL,
                f"{name}: reconstructed {key} != exported summary")
    bounds = objective_interval(summary, metrics["bill_cny"], True)
    require(bounds["cost_identity_checked"], f"{name}: objective cannot be mapped to energy bill")
    metrics.update(objective_lower_cny=bounds["lower_cny"], objective_upper_cny=bounds["upper_cny"],
                   mip_gap=bounds["mip_gap"])
    return {**base, "config": cfg, "input": inp, "time": time, "price": price, "ef": ef,
            "valley": valley, "power": power, "building_series": building_series,
            "system": metrics, "buildings": building_rows, "bounds": bounds}


def separability(cases: dict) -> dict:
    need = ("independent_A", "independent_B", "joint_unlimited")
    if not all(cases[x]["accepted_incumbent"] for x in need):
        return {"available": False, "reason": "one or more independent/unlimited cases failed"}
    a, b, j = [cases[x] for x in need]
    union = a["config"]["building_thermal"]["buildings"] + b["config"]["building_thermal"]["buildings"]
    structural = union == j["config"]["building_thermal"]["buildings"]
    for key in ("sources", "nodes"):
        structural &= a["config"][key] + b["config"][key] == j["config"][key]
    for key in ("planning", "grades", "pipes", "storages", "demand_response"):
        structural &= a["config"][key] == b["config"][key] == j["config"][key]
    for key in ("enabled", "simultaneous_mode", "boundary_mode", "outdoor_temperature_column"):
        structural &= all(a["config"]["building_thermal"][key] == x["config"]["building_thermal"][key]
                          for x in (b, j))
    structural &= not any(x["config"].get("grid_connection") for x in (a, b, j))
    cool_a, cool_b, cool_j = [x["config"]["multi_energy_services"]["cooling"] for x in (a, b, j)]
    for key in ("sources", "demand_columns"):
        structural &= {**cool_a[key], **cool_b[key]} == cool_j[key]
    for key in set(cool_j) - {"sources", "demand_columns"}:
        structural &= cool_a[key] == cool_b[key] == cool_j[key]
    for independent in (a, b):
        require(independent["time"].equals(j["time"]), "independent/unlimited clock mismatch")
        for column in independent["input"].columns:
            require(column in j["input"] and independent["input"][column].equals(j["input"][column]),
                    f"independent/unlimited input differs: {column}")
    require(structural, "config no longer establishes separable independent/unlimited identity")
    lower_values = [x["bounds"]["lower_cny"] for x in (a, b)]
    lo = sum(lower_values) if all(x is not None for x in lower_values) else None
    up = a["bounds"]["upper_cny"] + b["bounds"]["upper_cny"]
    jlo, jup = j["bounds"]["lower_cny"], j["bounds"]["upper_cny"]
    intersection_lo = max([x for x in (lo, jlo) if x is not None], default=None)
    intersection_up = min(up, jup)
    overlap = (intersection_lo is None or intersection_lo <= intersection_up + COST_TOL)
    return {"available": True, "structural_separability_verified": True,
            "mathematical_optimum_identity": "independent_A optimum + independent_B optimum = joint_unlimited optimum",
            "independent_sum_lower_cny": lo, "independent_sum_upper_cny": up,
            "joint_unlimited_lower_cny": jlo, "joint_unlimited_upper_cny": jup,
            "intervals_consistent": overlap, "equivalent_optimum_intersection_lower_cny": intersection_lo,
            "equivalent_optimum_intersection_upper_cny": intersection_up,
            "incumbent_bill_difference_independent_minus_unlimited_cny": up - jup,
            "cost_tolerance_cny": COST_TOL,
            "interpretation": ("structural equivalence, solver intervals consistent; incumbents may differ"
                               if overlap else "structural equivalence but exported solver intervals inconsistent")}


def compare_cost(cases: dict, left: str, right: str, meaning: str) -> dict:
    """Difference right optimum minus left optimum; positive means left cheaper."""
    result = {"comparison": f"{right}_minus_{left}", "left": left, "right": right,
              "meaning": meaning, "unit": "CNY_120_day_energy_charge", "available": False,
              "bound_advantage_proven": False, "global_optimum_claimed": False}
    if not all(cases[n]["accepted_incumbent"] for n in (left, right)):
        result["reason"] = "failed/infeasible/missing case; no cost advantage inferred"
        return result
    x, y = cases[left], cases[right]
    xl, xu = x["bounds"]["lower_cny"], x["bounds"]["upper_cny"]
    yl, yu = y["bounds"]["lower_cny"], y["bounds"]["upper_cny"]
    low = yl - xu if yl is not None else None
    high = yu - xl if xl is not None else None
    certified = low is not None and low > COST_TOL
    result.update(available=True, left_lower_cny=xl, left_upper_cny=xu,
                  right_lower_cny=yl, right_upper_cny=yu,
                  incumbent_difference_cny=y["system"]["bill_cny"] - x["system"]["bill_cny"],
                  incumbent_difference_percent_of_left=100 * (y["system"]["bill_cny"] / x["system"]["bill_cny"] - 1),
                  optimum_difference_lower_cny=low, optimum_difference_upper_cny=high,
                  bound_advantage_proven=certified,
                  proof_rule="left audited feasible upper < right solver lower - cost tolerance",
                  left_quality=x["bounds"]["quality"], right_quality=y["bounds"]["quality"],
                  interpretation="strict cost advantage supported by nonoverlapping bounds at solver tolerances" if certified
                  else "strict cost advantage not established by available bounds; incumbent difference is descriptive")
    return result


def response_tables(cases: dict, cap: float) -> tuple[list, list]:
    changes, hourly = [], []
    if not cases["joint_unlimited"]["accepted_incumbent"]:
        return changes, hourly
    ref = cases["joint_unlimited"]
    for case in cases.values():
        if not case["accepted_incumbent"]:
            continue
        require(case["time"].equals(ref["time"]), f"{case['name']}: reference clock differs")
        bind = np.abs(case["power"] - cap) <= POWER_TOL
        ref_bind = np.abs(ref["power"] - cap) <= POWER_TOL
        cf_excess = np.zeros(len(bind), dtype=bool)
        if all(cases[n]["accepted_incumbent"] for n in ("independent_A", "independent_B")):
            cf_excess = cases["independent_A"]["power"] + cases["independent_B"]["power"] > cap + POWER_TOL
        for bn, series in case["building_series"].items():
            reference = ref["building_series"][bn]
            delta = {key: series[key] - reference[key] for key in
                     ("heat", "cool", "electric", "start_temperature", "end_temperature")}
            total_delta = series["electric"].sum() - reference["electric"].sum()
            changes.append({"name": case["name"], "building": bn, "reference": "joint_unlimited",
                            "mean_temperature_delta_c": float(delta["end_temperature"].mean()),
                            "max_absolute_temperature_delta_c": float(np.abs(delta["end_temperature"]).max()),
                            "temperature_changed_hours": int(np.count_nonzero(np.abs(delta["end_temperature"]) > TEMP_TOL)),
                            "heat_delta_kwh": float(delta["heat"].sum()), "cooling_delta_kwh": float(delta["cool"].sum()),
                            "electricity_delta_kwh": float(total_delta),
                            "bill_delta_cny": float(np.dot(delta["electric"], case["price"])),
                            "heat_changed_hours": int(np.count_nonzero(np.abs(delta["heat"]) > POWER_TOL)),
                            "cooling_changed_hours": int(np.count_nonzero(np.abs(delta["cool"]) > POWER_TOL)),
                            "absolute_heat_reallocation_kwh": float(np.abs(delta["heat"]).sum()),
                            "absolute_cooling_reallocation_kwh": float(np.abs(delta["cool"]).sum()),
                            "system_binding_hours": int(bind.sum()) if case["config"].get("grid_connection") else None,
                            "common_K_binding_hour_symmetric_difference_vs_unlimited": int(np.count_nonzero(bind != ref_bind)),
                            "binding_hours_also_independent_counterfactual_excess": int(np.count_nonzero(bind & cf_excess)),
                            "mean_electric_delta_at_actual_binding_kw": float(delta["electric"][bind].mean()) if bind.any() and case["config"].get("grid_connection") else None,
                            "mean_electric_delta_at_independent_excess_kw": float(delta["electric"][cf_excess].mean()) if cf_excess.any() else None})
            for i, stamp in enumerate(case["time"]):
                hourly.append({"name": case["name"], "building": bn, "time_index": i, "time": stamp.isoformat(),
                               "reference": "joint_unlimited", "temperature_start_c": float(series["start_temperature"][i]),
                               "temperature_end_c": float(series["end_temperature"][i]),
                               "temperature_end_delta_c": float(delta["end_temperature"][i]),
                               "heat_kw": float(series["heat"][i]), "heat_delta_kw": float(delta["heat"][i]),
                               "cooling_kw": float(series["cool"][i]), "cooling_delta_kw": float(delta["cool"][i]),
                               "electric_kw": float(series["electric"][i]), "electric_delta_kw": float(delta["electric"][i]),
                               "price_cny_per_kwh": float(case["price"][i]), "tariff_bucket": str(case["input"].tariff_bucket.iloc[i]),
                               "actual_system_grid_binding": bool(bind[i]) if case["config"].get("grid_connection") else None,
                               "independent_counterfactual_grid_excess_hour": bool(cf_excess[i])})
    return changes, hourly


def select_window(cases: dict, cap: float, fallback_input: pd.DataFrame) -> dict:
    time = pd.DatetimeIndex(pd.to_datetime(fallback_input.time, utc=True)).tz_convert("Asia/Shanghai")
    trigger = "earliest coldest outdoor hour (no positive independent excess available)"
    index = int(np.argmin(numbers(fallback_input, "outdoor_temp")))
    magnitude = None
    if all(cases[n]["accepted_incumbent"] for n in ("independent_A", "independent_B")):
        excess = cases["independent_A"]["power"] + cases["independent_B"]["power"] - cap
        if excess.max() > POWER_TOL:
            index = int(np.argmax(excess))
            magnitude = float(excess[index])
            trigger = "earliest maximum positive independent-A+B counterfactual grid excess"
    midnight_index = index - time[index].hour
    start = max(0, min(midnight_index - 24, len(time) - 72))
    return {"fixed_rule": WINDOW_RULE, "actual_trigger": trigger,
            "trigger_time": time[index].isoformat(), "trigger_time_index": index,
            "counterfactual_excess_at_trigger_kw": magnitude, "start_index": start,
            "end_index_exclusive": start + 72, "hours": 72,
            "start_inclusive": time[start].isoformat(), "end_exclusive": (time[start + 71] + pd.Timedelta(hours=1)).isoformat(),
            "selection_is_demonstration_not_outcome_validation": True,
            "selection_rule_in_frozen_solve_protocol": False,
            "selection_timing": "post-solve analysis; not preregistered confirmation or held-out selection",
            "capacity_or_parameters_retuned_after_results": False}


def plots(out: Path, cases: dict, systems: list, cap: float, window: dict, inp: pd.DataFrame) -> None:
    os.environ.setdefault("MPLCONFIGDIR", str(out / "matplotlib_cache"))
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "svg.fonttype": "none"})
    labels = {"independent_sum_counterfactual": "Independent sum\n(counterfactual)",
              "joint_unlimited": "Joint unlimited", "coupled_flexible": "Shared grid, flexible",
              "coupled_A_fixed20": "A fixed at 20 C", "coupled_B_fixed20": "B fixed at 20 C",
              "coupled_all_fixed20": "Both fixed at 20 C"}
    rows = [r for r in systems if r["name"] in labels]
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5), constrained_layout=True)
    for i, row in enumerate(rows):
        color = "#b06c2b" if row["name"] == "independent_sum_counterfactual" else "#2378a8" if row["name"] == "coupled_flexible" else "#72908d"
        hatch = "//" if row["status"] in ("feasible_unproven", "counterfactual") else None
        axes[0].barh(i, row["bill_cny"], color=color, alpha=.83, hatch=hatch)
        axes[1].barh(i, row["peak_kw"], color=color, alpha=.83, hatch=hatch)
        lo, up = row.get("objective_lower_cny"), row.get("objective_upper_cny")
        if lo is not None and up is not None:
            axes[0].plot([lo, up], [i, i], color="#272727", lw=2)
            axes[0].plot(lo, i, marker="|", color="#272727", markersize=12)
        axes[0].text(row["bill_cny"], i, f"  {row['bill_cny']:.2f}", va="center", fontsize=9)
        axes[1].text(row["peak_kw"], i, f"  {row['peak_kw']:.3f}", va="center", fontsize=9)
    for axis in axes:
        axis.set_yticks(np.arange(len(rows)), [labels[r["name"]] for r in rows])
        axis.invert_yaxis()
        axis.grid(axis="x", alpha=.2)
        axis.set_axisbelow(True)
        axis.spines[["top", "right"]].set_visible(False)
    if rows:
        axes[0].set_xlim(0, max(r["bill_cny"] for r in rows) * 1.17)
        axes[1].set_xlim(0, max(cap, max(r["peak_kw"] for r in rows)) * 1.17)
    axes[0].set(xlabel="120-day electricity energy charge (CNY)", title="Hourly reconstructed bill; line = cost bound interval")
    axes[1].axvline(cap, color="#b23636", ls="--", label=f"Frozen shared K = {cap:.6f} kW")
    axes[1].set(xlabel="Peak actual electric import (kW)", title="Peak from hourly source heat / eta + cooling / EER")
    axes[1].legend(loc="upper right", bbox_to_anchor=(1, 1.03), frameon=False, fontsize=8)
    axes[0].legend(handles=[Patch(facecolor="white", edgecolor="gray", hatch="//", label="Unproven incumbent or counterfactual")],
                   loc="upper right", bbox_to_anchor=(1, 1.03), frameon=False, fontsize=8)
    fig.suptitle("Two declared 48 m2 design variants | 2880 continuous hours | ideal existing devices", fontsize=13)
    for extension in ("png", "svg"):
        fig.savefig(out / f"system_bill_peak.{extension}", dpi=180)
    plt.close(fig)

    start, end = window["start_index"], window["end_index_exclusive"]
    time = pd.DatetimeIndex(pd.to_datetime(inp.time, utc=True)).tz_convert("Asia/Shanghai")
    x = time[start:end].to_pydatetime()
    t_end = (time[start:end] + pd.Timedelta(hours=1)).to_pydatetime()
    fig, axes = plt.subplots(3, 1, figsize=(13, 8.5), sharex=True, constrained_layout=True)
    styles = {"joint_unlimited": ("#80858b", "--", "Unlimited"),
              "coupled_flexible": ("#1675a6", "-", "Shared grid, flexible"),
              "coupled_all_fixed20": ("#388d6d", ":", "Both fixed at 20 C")}
    if all(cases[n]["accepted_incumbent"] for n in ("independent_A", "independent_B")):
        cf = cases["independent_A"]["power"] + cases["independent_B"]["power"]
        axes[0].step(x, cf[start:end], where="post", color="#ba7431", lw=1.6, label="Independent sum (counterfactual)")
        axes[0].fill_between(x, cap, cf[start:end], where=cf[start:end] > cap + POWER_TOL,
                             step="post", alpha=.18, color="#ba7431")
    for name, (color, ls, label) in styles.items():
        case = cases[name]
        if not case["accepted_incumbent"]:
            continue
        axes[0].step(x, case["power"][start:end], where="post", color=color, ls=ls, lw=1.7, label=label)
        for axis, bn in zip(axes[1:], ("variant_A", "variant_B")):
            axis.plot(t_end, case["building_series"][bn]["end_temperature"][start:end], color=color, ls=ls, lw=1.7, label=label)
    axes[0].axhline(cap, color="#b23636", ls="--", lw=1.4, label=f"Shared grid capacity {cap:.6f} kW")
    axes[0].set(ylabel="Electric import (kW)", title="System power: actual hourly source imports; orange shading = invalid counterfactual excess")
    axes[0].legend(loc="upper left", ncol=2, fontsize=8, frameon=False)
    for axis, bn in zip(axes[1:], ("variant_A", "variant_B")):
        axis.axhspan(20, 27, color="#dbe8dc", alpha=.25)
        axis.axhline(20, color="#7b877d", lw=.7)
        axis.axhline(27, color="#7b877d", lw=.7)
        axis.set(ylabel="End temperature (C)", title=f"{bn}: shared service contract 20-27 C; fixed20 is a stricter operating restriction", ylim=(19.7, 27.5))
    for axis in axes:
        axis.grid(alpha=.2)
        axis.spines[["top", "right"]].set_visible(False)
    axes[-1].xaxis.set_major_locator(mdates.HourLocator(byhour=[0, 12], tz=time.tz))
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%m-%d\n%H:%M", tz=time.tz))
    axes[-1].set_xlabel("Asia/Shanghai local time; temperatures at each hour end")
    fig.suptitle("Fixed 72-hour illustration (post-solve analysis) | " + window["start_inclusive"][:10] + " to " + window["end_exclusive"][:10], fontsize=13)
    for extension in ("png", "svg"):
        fig.savefig(out / f"representative_72h.{extension}", dpi=180)
    plt.close(fig)

    fig, axes = plt.subplots(2, 2, figsize=(13, 6.5), sharex=True, constrained_layout=True)
    for row, bn in enumerate(("variant_A", "variant_B")):
        for name, (color, ls, label) in styles.items():
            if not cases[name]["accepted_incumbent"]:
                continue
            series = cases[name]["building_series"][bn]
            axes[row, 0].step(x, series["heat"][start:end], where="post", color=color, ls=ls, label=label)
            axes[row, 1].step(x, series["cool"][start:end], where="post", color=color, ls=ls, label=label)
        axes[row, 0].set(title=f"{bn} delivered heating", ylabel="Heating (kW)")
        axes[row, 1].set(title=f"{bn} delivered cooling", ylabel="Cooling (kW)")
        axes[row, 1].set_ylim(bottom=0)
        available_cool = [float(cases[name]["building_series"][bn]["cool"][start:end].max())
                          for name in styles if cases[name]["accepted_incumbent"]]
        if not available_cool or max(available_cool) <= POWER_TOL:
            axes[row, 1].set_ylim(0, .05)
            axes[row, 1].text(.5, .7, "No cooling in this selected window", transform=axes[row, 1].transAxes,
                              ha="center", color="#586568", fontsize=10)
    for axis in axes.flat:
        axis.grid(alpha=.2)
        axis.spines[["top", "right"]].set_visible(False)
        axis.xaxis.set_major_locator(mdates.HourLocator(byhour=[0, 12], tz=time.tz))
        axis.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d\n%H:%M", tz=time.tz))
    axes[0, 0].legend(frameon=False, fontsize=8)
    fig.suptitle("Building response in the same selected 72 h | eta = EER = 1", fontsize=13)
    for extension in ("png", "svg"):
        fig.savefig(out / f"building_heat_cool_72h.{extension}", dpi=180)
    plt.close(fig)


def fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "未提供"
    return f"{0. if abs(value) < .5 * 10 ** (-digits) else value:.{digits}f}"


def markdown_table(rows: list, columns: list[tuple[str, str]], precision: int = 4) -> str:
    lines = ["| " + " | ".join(label.replace("|", "\\|") for _, label in columns) + " |",
             "| " + " | ".join("---" for _ in columns) + " |"]
    for row in rows:
        values = []
        for key, _ in columns:
            x = row.get(key)
            values.append(fmt(x, precision) if isinstance(x, (float, np.floating)) else "—" if x is None else str(x))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def report(out: Path, result: dict) -> None:
    systems = result["system_metrics"]
    buildings = result["building_metrics"]
    comparisons = result["cost_comparisons"]
    cf = result.get("independent_sum_counterfactual")
    eq = result["independent_unlimited_equivalence"]
    window = result["representative_window"]
    statuses = result["case_statuses"]
    failures = [r for r in statuses if r["status"] not in VALID]
    lines = ["# 两楼共享电网数值参考实验：独立结果分析", "",
             "本报告仅分析冻结后完成的 run_01，所有电量、电费、固定平均 EF 碳量、实际电网绑定与越限均由逐时原始出力重建。原始求解结果保持不变。", "",
             "A 使用 48 m² BESTEST600 衍生 1R1C 参考参数 H=0.1043 kW/℃、C=0.535576 kWh/℃；B 是明确的设计变体，H 为 A 的 1.5 倍、C 为 A 的 4 倍，面积仍为 48 m²。两者同时改变 H 和 C，结果只能归因于整体设计变体与调度，不能声称识别了单一热容效应。每楼内部增益 0.2 kW、太阳增益 0，已有理想设备 η=EER=1，无投资。", "",
             "共同服务为武汉参考天气下完整 2880 h、20–27℃、初末 20℃。共享电网容量先于求解按 (H_A+H_B)×(20−全窗最低室外温度) 冻结：K=" + fmt(result["shared_capacity_kw"], 7) + " kW。模型保留 portfolio_dispatch 静态 firm 约束；下面的实际绑定证据只来自逐时 Σ(heat/η+cooling/EER)，没有用静态 firm 变量等式充当运行绑定。", "",
             "## 求解质量与失败记录", "",
             markdown_table(statuses, [("name", "案例"), ("status", "状态"), ("quality", "求解质量"), ("reason", "失败说明")]), "",
             "passed 表示模型求解器在登记容差内证明最优且通过数值/物理/账目审计；feasible_unproven 表示通过审计的可行 incumbent，尚未证明全局最优。限时解、失败、不可行状态均保留。上下界仅针对相同 120 日电费目标，未把 MIP gap 换算成碳量或其他 KPI 的误差区间。", "",
             "## 系统逐时重建指标", "",
             markdown_table(systems, [("name", "案例"), ("status", "状态"), ("bill_cny", "电费/CNY"),
                 ("electricity_kwh", "电量/kWh"), ("average_EF_carbon_kg", "平均EF碳量/kg"),
                 ("peak_kw", "峰值/kW"), ("actual_grid_binding_hours", "实际grid绑定/h"),
                 ("common_grid_exceedance_hours", "相对共同K越限/h"),
                 ("common_grid_max_excess_kw", "最大超额/kW"), ("common_grid_sum_excess_kwh", "Σ超额/kWh")]), "",
             "独立单楼与 joint_unlimited 未施加共同 K，其“相对共同 K”列只作诊断；实际绑定列为空。拼接反事实则专门检验独立决策能否满足该共同资源。碳量使用固定平均 EF=" + fmt(result["average_EF_kg_per_kwh"], 4) + " kg/kWh，仅是参考核算；没有 marginal EF、逐时边际排放或减排因果解释。", ""]
    if cf:
        lines += [f"独立 A+B 拼接反事实：违反共同 grid {cf['common_grid_exceedance_hours']} h，最大超额 {fmt(cf['common_grid_max_excess_kw'], 6)} kW，Σ超额 {fmt(cf['common_grid_sum_excess_kwh'], 6)} kWh。" +
                  ("因此它在共同 grid 下不是系统可行解，不能以它的低电费宣称可实施系统方案。" if cf["common_grid_exceedance_hours"] > 0 else
                   "本次没有观察到独立拼接越限；不得宣称出现了共同资源冲突。"), ""]
    else:
        lines += ["独立 A/B 至少一个没有已审计可行结果，无法计算独立拼接反事实；未用其他案例代替。", ""]
    flexible = next((r for r in systems if r["name"] == "coupled_flexible"), None)
    if flexible:
        lines += [f"coupled_flexible 逐时实际电网绑定 {flexible['actual_grid_binding_hours']} h，峰值 {fmt(flexible['peak_kw'], 6)} kW。" +
                  ("实际共享上限被使用。" if flexible["actual_grid_binding_hours"] > 0 else
                   "本次未观察到实际共享上限绑定，不能仅凭存在 grid 约束声称调度受限。"), ""]
    lines += ["## 独立与 unlimited 的可分离一致性", ""]
    if eq.get("available"):
        lines += ["配置/设备/服务/逐时输入检查确认 joint_unlimited 是两个独立问题的可分离并集，故数学上的最优目标满足 f*_unlimited=f*_A+f*_B。两个分别求解的 incumbent 可以不同，数值一致性应检查界区间。", "",
                  f"独立最优和的区间为 [{fmt(eq['independent_sum_lower_cny'], 6)}, {fmt(eq['independent_sum_upper_cny'], 6)}] CNY；unlimited 区间为 [{fmt(eq['joint_unlimited_lower_cny'], 6)}, {fmt(eq['joint_unlimited_upper_cny'], 6)}] CNY。区间一致性：{eq['intervals_consistent']}；incumbent 电费差（独立和−unlimited）={fmt(eq['incumbent_bill_difference_independent_minus_unlimited_cny'], 6)} CNY。", ""]
    else:
        lines += ["一致性检查不可用：" + eq.get("reason", "证据缺失") + "。", ""]
    lines += ["## 共同 grid 成本与锁温消融", "",
              "差额统一定义为右侧案例减左侧案例。coupled−unlimited 的 incumbent 差只描述已输出可行解；最优差区间由 right lower−left upper 与 right upper−left lower 得到。灵活性费用优势必须满足 coupled_flexible 的已审计 upper < 消融的 solver lower−1e−5 CNY；只比较两个 incumbent 不构成严格优势证明。", "",
              markdown_table(comparisons, [("comparison", "差额"), ("incumbent_difference_cny", "incumbent差/CNY"),
                  ("optimum_difference_lower_cny", "最优差下界/CNY"), ("optimum_difference_upper_cny", "最优差上界/CNY"),
                  ("bound_advantage_proven", "严格费用优势由界支持")]), "",
              "锁 A、锁 B、全锁 20℃属于同一 20–27℃服务合同内更严格的运行限制，三者可行域不同；不是用同一可行域证明不同算法优劣。共享 grid 与锁温消融的理论可行域包含关系可给出弱不等式，但不能取代严格费用差的求解器界证据。", ""]
    grid_comparison = comparisons[0]
    if grid_comparison["available"]:
        lines += ["共享 grid 的已输出电费相对 unlimited 增加 " + fmt(grid_comparison["incumbent_difference_cny"], 6) +
                  " CNY（" + fmt(grid_comparison["incumbent_difference_percent_of_left"], 4) + "%，以 unlimited 为分母）。" +
                  ("其最优电费增加在登记求解器容差下也由目标上下界支持。" if grid_comparison["bound_advantage_proven"] else
                   "其最优电费是否严格增加尚未由现有界证明。"), ""]
    for comparison in comparisons:
        if comparison["available"]:
            lines += [f"- {comparison['comparison']}：incumbent 差={fmt(comparison['incumbent_difference_cny'], 6)} CNY；" +
                      (("unlimited 的低费用在求解器容差下由非重叠界支持。" if comparison["left"] == "joint_unlimited" else
                        "灵活调度的低费用在求解器容差下由非重叠界支持。") if comparison["bound_advantage_proven"] else
                       "现有界未能证明严格费用优势，保留不确定结论。")]
    lines.append("")
    ablations = [next(r for r in statuses if r["name"] == n) for n in
                 ("coupled_A_fixed20", "coupled_B_fixed20", "coupled_all_fixed20")]
    if all(r["status"] in VALID for r in ablations):
        lines += ["本次所有锁温消融都得到已审计可行解，未观察到消融不可行。完整解析 fixed20 witness 的最大负荷=" +
                  fmt(result["fixed20_witness_max_kw"], 7) + " kW ≤ K，协议设计即保留了固定20策略的可行性。", ""]
    elif failures:
        lines += ["存在失败或不可行记录，见质量表与原始 failure.json；求解失败不自动等于物理不可行。", ""]
    lines += ["## 从系统到建筑的响应", "",
              markdown_table(buildings, [("name", "案例"), ("building", "建筑"), ("electricity_kwh", "电量/kWh"),
                  ("bill_cny", "电费/CNY"), ("valley_electricity_share", "谷段电量占比"),
                  ("min_end_temperature_c", "最低末温/℃"), ("max_end_temperature_c", "最高末温/℃")]), "",
              "按 variant_A/B 对照 joint_unlimited 的每小时室温、heat/cool/electric 变化完整保存在 hourly_building_changes.csv。温度为小时末值，电热/电冷为小时内平均功率；电量与电费按 1 h 累计。谷段占比由输入 tariff_bucket=valley 定义，没有另设谷段阈值。建筑电费是各自 η/EER=1 的直接设备耗电账目分项，其和等于系统账目。", "",
              markdown_table(result["building_responses_vs_unlimited"], [("name", "案例"), ("building", "建筑"),
                  ("mean_temperature_delta_c", "平均末温变化/℃"), ("max_absolute_temperature_delta_c", "最大绝对温差/℃"),
                  ("heat_delta_kwh", "热量变化/kWh"), ("cooling_delta_kwh", "冷量变化/kWh"),
                  ("bill_delta_cny", "电费变化/CNY"), ("heat_changed_hours", "供热改变/h"),
                  ("system_binding_hours", "系统绑定/h"), ("binding_hours_also_independent_counterfactual_excess", "绑定与反事实越限重叠/h")]), "",
              "建筑变化可能包含不同 incumbent 的求解差异。仅当成本界与可行域证据同时支持时，才称为严格费用机制；没有把两楼总体差异拆解成纯 C 效应。绑定时段统计是系统绑定时两楼出力与 unlimited 的差异，不是把系统容量分摊成未经声明的建筑限额。", "",
              "## 固定的 72 h 展示规则（求解后分析）", "",
              "选择共同 grid 下独立拼接反事实最大正超额的最早小时及所在日；若没有正超额或独立结果缺失，使用全窗最冷室外小时的最早日期。展示该日及相邻日 72 h，靠端点时平移到可用窗口。规则固定于求解后分析脚本，未列入求解前冻结 protocol；不是预注册确认或留出选择。没有为图形或效果事后改 K、物理参数或实验案例。", "",
              "本次触发：" + window["actual_trigger"] + "；时刻 " + window["trigger_time"] +
              "；展示 " + window["start_inclusive"] + " 至 " + window["end_exclusive"] + "。所选片段仅作机制展示，全部指标与结论使用完整 2880 h。", "",
              "![系统费用与峰值对照](system_bill_peak.png)", "",
              "费用图的黑线为同单位目标区间，斜纹表示尚未证明最优的 incumbent 或反事实。电网 K 的虚线只在实际受限案例中构成约束，反事实超限不构成可行方案。", "",
              "![72小时电力、电网容量及两楼温度](representative_72h.png)", "",
              "![同一72小时各楼热冷出力](building_heat_cool_72h.png)", "",
              "所选 72 h 内没有冷量需求，冷量图据实显示为零。完整 2880 h 的 fixed20 消融存在保持 20℃所需的冷量，应以全窗表格和逐时文件为准。", "",
              "## 适用范围与文件证据", "",
              "这是公开原型、格点天气、条件适用分时电价和固定平均 EF 的数值参考实验。不是现场验证，不是武汉实测建筑或当地账户核实，不是 BESTEST 认证；B 也不是经过验证的 BESTEST900。理想已有设备、无投资、无生命周期成本，未补入未知费用为零。分时电价只覆盖参考账户的电量费，不能宣称当地用户真实账单收益、工程配网可行性或边际减排收益。", "",
              "分析独立读取逐时 raw；保留父运行 manifest.json 并在前后检查其文件哈希。analysis_manifest.json 只登记本分析新增文件与脚本 SHA，不改父 manifest。summary.json 为机器可读结论与质量证据；full_tables.csv 汇总系统/建筑/界对照，hourly_system.csv 与 hourly_building_changes.csv 保留全部小时。", ""]
    flex_response = [r for r in result["building_responses_vs_unlimited"] if r["name"] == "coupled_flexible"]
    if flex_response:
        insert_at = lines.index("## 固定的 72 h 展示规则（求解后分析）")
        commentary = []
        for row in flex_response:
            commentary += [row["building"] + " 在共享灵活调度的实际绑定小时，平均电力相对 unlimited 变化 " +
                           fmt(row["mean_electric_delta_at_actual_binding_kw"], 6) + " kW；在独立拼接越限小时，平均变化 " +
                           fmt(row["mean_electric_delta_at_independent_excess_kw"], 6) + " kW。"]
        lines[insert_at:insert_at] = commentary + [""]
    (out / "RESULTS.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--refresh-analysis", action="store_true",
                        help="Regenerate only this script's existing analysis after a presentation fix; raw run is never edited")
    args = parser.parse_args()
    run = args.run.resolve()
    require(run == ALLOWED_RUN.resolve(), "this task may write analysis only under the declared run_01")
    require((run / "manifest.json").is_file(), "completed run manifest required; do not analyze partial results")
    protocol = read(run / "protocol.json")
    require(sha(run / "protocol.json") == read(run / "protocol_lock.json")["protocol.json"], "protocol lock changed")
    manifest_sha = sha(run / "manifest.json")
    manifest = read(run / "manifest.json")
    require(manifest["requested_cases"] == manifest["attempted_cases"] == len(CASES), "run did not attempt all declared cases")
    records = read(run / "comparison.json")
    require(len(records) == len(CASES) and {r["name"] for r in records} == set(CASES), "incomplete/duplicated comparison")
    require(set(protocol["cases"]) == set(CASES), "protocol case set changed")
    raw_hashes = {name: sha(run / name) for name in manifest["files"]}
    require(raw_hashes == manifest["files"], "raw manifest integrity failed")
    cap = float(protocol["shared_resource"]["capacity_kw"])
    cases = {r["name"]: load_case(run, r, cap) for r in records}
    fallback = pd.read_csv(run / "input_cases/joint_unlimited/input.csv")
    expected_cap = .1043 * 2.5 * (20 - float(fallback.outdoor_temp.min()))
    require(abs(expected_cap - cap) <= POWER_TOL, "frozen K formula differs")
    out = run / "analysis"
    if out.exists():
        require(args.refresh_analysis and (out / "analysis_manifest.json").is_file(),
                "preserve existing analysis unless explicit --refresh-analysis is supplied")
        prior_analysis = read(out / "analysis_manifest.json")
        require(prior_analysis["source_manifest_sha256"] == manifest_sha,
                "do not refresh analysis against a different raw run")
    else:
        out.mkdir()
    systems = [cases[n]["system"] for n in CASES if cases[n]["accepted_incumbent"]]
    buildings = [b for n in CASES if cases[n]["accepted_incumbent"] for b in cases[n]["buildings"]]
    equivalence = separability(cases)
    cf = None
    if all(cases[n]["accepted_incumbent"] for n in ("independent_A", "independent_B")):
        a, b = cases["independent_A"], cases["independent_B"]
        require(a["time"].equals(b["time"]) and np.array_equal(a["price"], b["price"])
                and np.array_equal(a["ef"], b["ef"]), "independent chronology/accounting differs")
        cf = {"name": "independent_sum_counterfactual", "status": "counterfactual",
              **power_metrics(a["power"] + b["power"], a["price"], a["ef"], 1., cap),
              "actual_grid_capacity_kw": None, "actual_grid_binding_hours": None,
              "comparison_common_capacity_kw": cap, "building_count": 2,
              "grid_metric_scope": "independent_decisions_tested_against_common_K; not a system solution",
              "system_feasible_claimed": False,
              "objective_lower_cny": equivalence.get("independent_sum_lower_cny"),
              "objective_upper_cny": equivalence.get("independent_sum_upper_cny")}
        systems.append(cf)
    comparisons = [compare_cost(cases, "joint_unlimited", "coupled_flexible", "additional shared-grid operating cost")]
    comparisons += [compare_cost(cases, "coupled_flexible", n, "flexibility cost benefit versus stricter fixed20 operation")
                    for n in ("coupled_A_fixed20", "coupled_B_fixed20", "coupled_all_fixed20")]
    response, hourly_changes = response_tables(cases, cap)
    window = select_window(cases, cap, fallback)
    result = {"schema_version": "multiscale_reference.analysis.v1", "generated_utc": datetime.now(timezone.utc).isoformat(),
              "run": run.relative_to(ROOT).as_posix(), "shared_capacity_kw": cap,
              "fixed20_witness_max_kw": float(protocol["shared_resource"]["maximum_fixed20_feasible_witness_kw"]),
              "average_EF_kg_per_kwh": float(fallback.grid_emission_factor.iloc[0]),
              "case_statuses": [{"name": n, "status": cases[n]["status"],
                                  "quality": cases[n].get("bounds", {}).get("quality", "no accepted incumbent"),
                                  "reason": (cases[n].get("failure") or {}).get("reason")} for n in CASES],
              "case_objective_bounds": {n: cases[n].get("bounds") for n in CASES},
              "system_metrics": systems, "building_metrics": buildings,
              "independent_sum_counterfactual": cf, "independent_unlimited_equivalence": equivalence,
              "cost_comparisons": comparisons, "building_responses_vs_unlimited": response,
              "representative_window": window,
              "full_window_used_for_metrics": True, "static_firm_values_used_as_actual_binding": False,
              "global_optimality_claimed_for_time_limited_cases": False,
              "field_validation": False, "local_account_verified": False, "marginal_EF_used": False,
              "investment_analysis": False, "single_C_effect_identified": False,
              "fixed20_ablation_feasible_regions_differ": True,
              "limitations": ["public numerical prototype and declared H/C design variant", "gridded weather rather than building/site observations",
                              "conditional reference energy charges, no account eligibility verification", "existing ideal eta/EER=1 equipment, no investment/life-cycle economics",
                              "fixed average EF, no marginal emissions", "temperature locked at20 is a stricter feasible region within common20-27 contract",
                              "per-building incumbent trajectory differences may include solver approximation differences"]}
    save(out / "summary.json", result)
    tables = ([{"table_type": "system", **r} for r in systems]
              + [{"table_type": "building", **r} for r in buildings]
              + [{"table_type": "building_response_vs_unlimited", **r} for r in response]
              + [{"table_type": "cost_comparison", **r} for r in comparisons]
              + [{"table_type": "objective_bound", "name": n, **c["bounds"]} for n, c in cases.items() if c["accepted_incumbent"]]
              + [{"table_type": "case_status", **r} for r in result["case_statuses"]])
    pd.DataFrame(tables).to_csv(out / "full_tables.csv", index=False)
    pd.DataFrame(hourly_changes).to_csv(out / "hourly_building_changes.csv", index=False)
    hourly_system = []
    for n, case in cases.items():
        if not case["accepted_incumbent"]:
            continue
        for i, stamp in enumerate(case["time"]):
            hourly_system.append({"name": n, "time_index": i, "time": stamp.isoformat(),
                                  "power_kw": float(case["power"][i]), "common_K_kw": cap,
                                  "common_K_binding": bool(abs(case["power"][i] - cap) <= POWER_TOL),
                                  "common_K_excess_kw": max(0., float(case["power"][i] - cap)),
                                  "actual_shared_grid_constraint": bool(case["config"].get("grid_connection")),
                                  "outdoor_temperature_c": float(case["input"].outdoor_temp.iloc[i]),
                                  "price_cny_per_kwh": float(case["price"][i]), "tariff_bucket": str(case["input"].tariff_bucket.iloc[i])})
    if cf:
        a, b = cases["independent_A"], cases["independent_B"]
        for i, stamp in enumerate(a["time"]):
            power = float(a["power"][i] + b["power"][i])
            hourly_system.append({"name": cf["name"], "time_index": i, "time": stamp.isoformat(),
                                  "power_kw": power, "common_K_kw": cap,
                                  "common_K_binding": bool(abs(power - cap) <= POWER_TOL),
                                  "common_K_excess_kw": max(0., power - cap), "actual_shared_grid_constraint": False,
                                  "outdoor_temperature_c": float(a["input"].outdoor_temp.iloc[i]),
                                  "price_cny_per_kwh": float(a["price"][i]), "tariff_bucket": str(a["input"].tariff_bucket.iloc[i])})
    pd.DataFrame(hourly_system).to_csv(out / "hourly_system.csv", index=False)
    plots(out, cases, systems, cap, window, fallback)
    report(out, result)
    require(sha(run / "manifest.json") == manifest_sha and all(sha(run / n) == digest for n, digest in raw_hashes.items()),
            "raw result changed during analysis")
    save(out / "analysis_manifest.json", {"generated_utc": datetime.now(timezone.utc).isoformat(),
        "script": Path(__file__).relative_to(ROOT).as_posix(), "script_sha256": sha(Path(__file__)),
        "source_manifest_sha256": manifest_sha, "source_protocol_sha256": sha(run / "protocol.json"),
        "source_independent_verification_sha256": sha(run / "independent_verification.json")
            if (run / "independent_verification.json").is_file() else None,
        "source_raw_files_sha256": raw_hashes, "raw_files_unchanged_verified": True,
        "files": {p.relative_to(out).as_posix(): sha(p) for p in sorted(out.rglob("*"))
                  if p.is_file() and p.name != "analysis_manifest.json"}})
    print(json.dumps({"analysis": str(out), "cases": len(records), "accepted_incumbents": sum(c["accepted_incumbent"] for c in cases.values()),
                      "counterfactual_exceedance_hours": cf["common_grid_exceedance_hours"] if cf else None,
                      "strict_bound_supported_flexibility_advantages": sum(r["bound_advantage_proven"] for r in comparisons[1:])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
