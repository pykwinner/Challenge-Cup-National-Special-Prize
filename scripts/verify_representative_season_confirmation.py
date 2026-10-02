"""Independently check saved finite-policy trajectories and weight decisions.

Uses a full convolution RC solution, not the producer's step recurrence, and
reconstructs bills, representative estimates and selection from saved powers.
This verifies numerical identity, not local empirical validation or superiority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import wasserstein_distance
import yaml

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def audit(run_dir):
    failures, checks = [], []

    def check(name, passed):
        checks.append(name)
        if not bool(passed):
            failures.append(name)

    def close(name, actual, expected, atol=1e-7):
        check(name, np.allclose(actual, expected, rtol=1e-9, atol=atol, equal_nan=False))

    def nullable_close(name, actual, expected):
        check(name, actual is None) if expected is None else close(name, actual, expected)

    protocol = read(run_dir / "protocol.json")
    for name, digest in read(run_dir / "protocol_lock.json").items():
        check("protocol_hash:" + name, sha(run_dir / name) == digest)
    for name, digest in protocol["source_sha256"].items():
        source_path = Path(name)
        check("source_hash:" + name, sha(source_path if source_path.is_absolute() else ROOT / source_path) == digest)
    for name, digest in protocol["code_sha256"].items():
        check("producer_hash:" + name, sha(ROOT / name) == digest)
    for name, digest in read(run_dir / "manifest.json")["files"].items():
        check("output_hash:" + name, sha(run_dir / name.replace("\\", "/")) == digest)
    config = yaml.safe_load((ROOT / "configs/evidence_case_20261002/wuhan_bestest600_rc_tou.yaml").read_text(encoding="utf-8"))
    source = pd.read_csv(ROOT / "data/derived/evidence_case_20261002/wuhan_2024_2025_hourly_tou.csv")
    times = pd.DatetimeIndex(pd.to_datetime(source.time, utc=True))
    check("whole_2880h_chronology", len(source) == 2880 and
          (times[1:] - times[:-1] == pd.Timedelta(hours=1)).all())
    building = config["building_thermal"]["buildings"][0]
    n = len(source)
    h, c = building["heat_loss_kw_per_c"], building["thermal_capacity_kwh_per_c"]
    alpha = np.exp(-h / c)
    beta = -np.expm1(-h / c) / h
    gains = source[building["internal_gain_column"]].to_numpy() + source[building["solar_gain_column"]].to_numpy()
    outdoor, price, factor = (source[key].to_numpy() for key in ["outdoor_temp", "electricity_price", "grid_emission_factor"])
    daily, totals, state_errors = {}, {}, {}
    measures = ["energy_kwh", "bill_yuan", "carbon_kg"]
    saved_daily = pd.read_csv(run_dir / "daily_totals.csv")
    saved_totals = pd.read_csv(run_dir / "policy_season_totals.csv").set_index("policy_delta_c")
    for delta in protocol["policies_delta_c"]:
        trajectory = pd.read_csv(run_dir / f"policies/delta_{delta}/trajectory.csv")
        prefix = f"policy_{delta}:"
        check(prefix + "calendar", len(trajectory) == n and
              pd.DatetimeIndex(pd.to_datetime(trajectory.time, utc=True)).equals(times))
        check(prefix + "day_identity", np.array_equal(trajectory.original_day_index, np.arange(n) // 24))
        heat, cool = trajectory.heat_kw.to_numpy(), trajectory.cool_kw.to_numpy()
        check(prefix + "nonnegative_exclusive_capacity", np.isfinite(heat + cool).all() and
              (heat >= -1e-9).all() and (cool >= -1e-9).all() and
              (np.minimum(heat, cool) <= 1e-9).all() and
              (heat <= building["max_heat_kw"] + 1e-9).all() and
              (cool <= building["max_cooling_kw"] + 1e-9).all() and
              (heat + cool <= config["sources"][0]["existing_capacity_kw"] + 1e-9).all())
        forcing = (1 - alpha) * outdoor + beta * (gains + heat - cool)
        ends = alpha ** np.arange(1, n + 1) * 20 + np.convolve(forcing, alpha ** np.arange(n))[:n]
        starts = np.r_[20, ends[:-1]]
        state_errors[str(delta)] = float(np.max(np.abs(trajectory.T_end - ends)))
        close(prefix + "convolution_temperature", trajectory.T_end, ends)
        close(prefix + "inherited_state", trajectory.T_start, starts)
        check(prefix + "comfort", (ends >= 20 - 1e-7).all() and (ends <= 27 + 1e-7).all())
        close(prefix + "terminal", ends[-1], 20)
        passive = alpha * starts + (1 - alpha) * outdoor + beta * gains
        targets = np.where(source.tariff_bucket == "valley", 20 + delta, 20.).astype(float)
        targets[-1] = 20
        required = (np.clip(passive, targets, 27) - passive) / beta
        required[-1] = (20 - passive[-1]) / beta
        close(prefix + "frozen_controller", heat - cool, required)
        energy = heat + cool
        values = np.column_stack((energy, energy * price, energy * factor))
        for k, name in enumerate(measures):
            close(prefix + name, trajectory[name], values[:, k])
        daily[delta] = values.reshape(-1, 24, 3).sum(axis=1)
        totals[delta] = values.sum(axis=0)
        observed_days = saved_daily[saved_daily.policy_delta_c == delta].sort_values("original_day_index")
        close(prefix + "daily_accounting", observed_days[measures], daily[delta])
        close(prefix + "season_accounting", saved_totals.loc[delta, measures], totals[delta])
        close(prefix + "peak_import", saved_totals.loc[delta, "peak_import_kw"], energy.max())
    selected = pd.read_csv(run_dir / "selected_days.csv")
    days = selected.original_day_index.to_numpy(dtype=int)
    selected_hours = pd.read_csv(run_dir / "selected_actual_hours.csv")
    indexes = (days[:, None] * 24 + np.arange(24)).ravel()
    check("actual_day_timestamps", pd.DatetimeIndex(pd.to_datetime(selected_hours.time, utc=True)).equals(times[indexes]))
    for name in ["outdoor_temp", "electricity_price", "grid_emission_factor", protocol["calibration_column"]]:
        close("actual_day_values:" + name, selected_hours[name], source.iloc[indexes][name])
    reports = read(run_dir / "weight_reports.json")
    weights_table = pd.read_csv(run_dir / "selected_day_weights.csv")
    candidates = pd.read_csv(run_dir / "policy_candidates.csv")
    selections = read(run_dir / "selections.json")
    reference = source[protocol["calibration_column"]].to_numpy()
    profiles = reference.reshape(-1, 24)[days]
    cap_atol, cap_rtol = protocol["cap_absolute_tolerance"], protocol["cap_relative_tolerance"]

    def fits(value, cap):
        return value <= cap + cap_atol + cap_rtol * max(abs(value), abs(cap), 1)

    def pick(costs):
        if not costs:
            return None
        minimum = min(costs.values())
        return min(delta for delta, value in costs.items() if value <= minimum + 1e-8)

    qualified, exceeded, empty = 0, 0, 0
    for method in protocol["methods"]:
        report = reports[method]
        weights = None
        if report["status"] == "ok":
            weights = weights_table[weights_table.method == method].set_index("original_day_index").loc[days].weight_days.to_numpy()
            check(method + ":weight_floor", (weights >= selected.lower_bound.to_numpy() - 1e-8).all())
            close(method + ":day_conservation", weights.sum(), n / 24)
            relative_error = (profiles.sum(axis=1) @ weights - reference.sum()) / reference.sum()
            close(method + ":reference_energy_error", report["relative_reference_energy_error"], relative_error)
            close(method + ":reference_W1", report["normalized_reference_W1"],
                  wasserstein_distance(reference, profiles.ravel(), v_weights=np.repeat(weights, 24)) / reference.mean())
            if method == "hard_energy_minimum_W1":
                close(method + ":hard_reference_energy", relative_error, 0, atol=1e-8)
        for multiplier in protocol["budget_multipliers"]:
            cap = totals[0][[0, 2]] * multiplier
            actual_ok = {delta: fits(v[0], cap[0]) and fits(v[2], cap[1]) for delta, v in totals.items()}
            estimated = {delta: daily[delta][days].T @ weights for delta in totals} if weights is not None else {}
            estimate_ok = {delta: fits(v[0], cap[0]) and fits(v[2], cap[1]) for delta, v in estimated.items()}
            chosen = pick({delta: v[1] for delta, v in estimated.items() if estimate_ok[delta]})
            oracle = pick({delta: v[1] for delta, v in totals.items() if actual_ok[delta]})
            subset = candidates[(candidates.method == method) & np.isclose(candidates.budget_multiplier, multiplier)].set_index("policy_delta_c")
            check(f"{method}:{multiplier}:all_candidates", set(subset.index) == set(totals) and len(subset) == 8)
            for delta, v in totals.items():
                row = subset.loc[delta]
                close(f"candidate:{method}:{multiplier}:{delta}:actual", row[["actual_" + m for m in measures]].to_numpy(dtype=float), v)
                check(f"candidate:{method}:{multiplier}:{delta}:feasibility", bool(row.actual_feasible) == actual_ok[delta] and bool(row.estimated_feasible) == estimate_ok.get(delta, False))
                if weights is not None:
                    close(f"candidate:{method}:{multiplier}:{delta}:estimated", row[["estimated_" + m for m in measures]].to_numpy(dtype=float), estimated[delta])
            match = [r for r in selections if r["method"] == method and r["budget_multiplier"] == multiplier]
            check(f"selection:{method}:{multiplier}:unique", len(match) == 1)
            row = match[0]
            check(f"selection:{method}:{multiplier}:decisions", row["selected_policy_delta_c"] == chosen and row["oracle_policy_delta_c"] == oracle)
            feasible = chosen is not None and actual_ok[chosen]
            check(f"selection:{method}:{multiplier}:actual_qualification", row["selected_policy_actual_feasible"] == feasible)
            regret = float(totals[chosen][1] - totals[oracle][1]) if feasible else None
            nullable_close(f"selection:{method}:{multiplier}:regret", row["actual_qualified_regret_yuan"], regret)
            for field, position, bound in [("actual_energy_cap_excess_kwh", 0, cap[0]), ("actual_carbon_cap_excess_kg", 2, cap[1])]:
                nullable_close(f"selection:{method}:{multiplier}:{field}", row[field], max(0, float(totals[chosen][position] - bound)) if chosen is not None else None)
            qualified += int(feasible)
            exceeded += int(chosen is not None and not feasible)
            empty += int(chosen is None)
    return {"numerical_audit_passed": not failures, "checks": len(checks), "failures": failures,
            "maximum_convolution_temperature_error_c": max(state_errors.values()),
            "actual_qualified_selections": qualified, "actual_cap_exceeded_selections": exceeded,
            "no_estimated_feasible_candidate": empty, "empirical_validation": False,
            "audit_method": "convolution RC, raw-power accounts, independent weighted estimates and finite-library enumeration",
            "auditor_sha256": sha(Path(__file__)), "producer_manifest_sha256": sha(run_dir / "manifest.json")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        parser.error("choose a new output; prior audit evidence is retained")
    report = audit(args.run)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["numerical_audit_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
