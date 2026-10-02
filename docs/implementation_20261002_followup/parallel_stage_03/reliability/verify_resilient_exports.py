"""Read-only export reconstruction for the new resilient entry point.

No solver/model construction is called. Reuse the established independent
convolution and source-zone verifier, and audit new-run provenance explicitly.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "scripts"))
import run_three_city_climate_dispatch_resilient as resilient
from verify_three_city_climate_dispatch import source_zone_checks


def audit(run, out):
    run, out = run.resolve(), out.resolve()
    resilient.base.require(not out.exists(), "audit output exists; preserve earlier evidence")
    protocol = resilient.verify(run)
    rows = resilient.base.read(run / "comparison.json")
    manifest = resilient.base.read(run / "manifest.json")
    checks, details = {}, []

    def check(name, value):
        checks[name] = bool(value)

    check("new_run_not_historical_reuse", manifest["baseline_rows_reused"] == 0)
    check("predeclared_city_row_inventory", len(rows) == len({(r["city"], r["case"]) for r in rows}) == 4 * len(protocol["cities"]))
    check("no_acceleration_requested", protocol["acceleration_requested"] is False and protocol["representative_hour_reduction"] is False)
    common, prices = resilient.base.load_inputs(ROOT / resilient.base.CONFIG, ROOT / resilient.base.SERIES)
    for city in protocol["cities"]:
        folder = run / "results" / city
        frame = pd.read_csv(folder / "chronology.csv")
        config = yaml.safe_load((folder / "common_config.yaml").read_text(encoding="utf-8"))
        climate = pd.read_csv(ROOT / f"data/derived/three_city_common_reference_20261002/{city}_hourly.csv")
        expected = resilient.base.join_common_economics(climate, prices)
        try:
            pd.testing.assert_frame_equal(frame, expected, check_dtype=False, rtol=1e-12, atol=1e-12)
            matched = True
        except AssertionError:
            matched = False
        check(city + "/source_archive_to_export", matched)
        check(city + "/config_archive_to_export", config == common)
        resilient.base.chronology_check(frame, config, protocol["start_inclusive"], protocol["end_exclusive"])
        check(city + "/complete_2880h", len(frame) == 2880 and np.all(frame.period_weight == 1))
        baseline = next(r for r in rows if r["city"] == city and r["case"] == "baseline_delta0")
        for row in (r for r in rows if r["city"] == city):
            name, extra, cap = row["case"], {}, None
            key = city + "/" + name
            if name == "baseline_delta0":
                trajectory = pd.read_csv(folder / "baseline_delta0.csv")
                extra["new_feedback_origin"] = row["origin"] == "new_feedback_rollout"
            else:
                destination = folder / name
                original = pd.read_csv(destination / "building_trajectory.csv")
                dispatch = pd.read_csv(destination / "dispatch.csv")
                summary = resilient.base.read(destination / "summary.json")
                extra.update(source_zone_checks(dispatch, original, frame))
                trajectory = original.rename(columns={"temperature_start_c": "T_start",
                    "temperature_end_c": "T_end", "cooling_kw": "cool_kw"})
                extra["new_optimization_origin"] = row["origin"] == "new_optimization"
                extra["zero_gap_requested"] = summary["solver_requested_mip_gap"] == 0.
                extra["budget_60_seconds"] = summary["solver_time_limit_seconds"] == 60.
                extra["no_matrix_cache_hit_reported"] = summary["matrix_compile_cache_hit"] is False
                corrupted = dispatch.copy()
                index = corrupted.index[corrupted.category == "source_heat"][100]
                corrupted.loc[index, "value"] += .1
                extra["source_zone_tamper_rejected"] = not all(source_zone_checks(corrupted, original, frame).values())
                if name == "midpoint_cap":
                    rule = resilient.base.read(folder / "midpoint_cap_rule.json")
                    cost = next(r for r in rows if r["city"] == city and r["case"] == "cost_endpoint")
                    carbon = next(r for r in rows if r["city"] == city and r["case"] == "carbon_endpoint")
                    requested = .5 * (cost["carbon_kg"] + carbon["carbon_kg"])
                    cap = rule["applied_cap_kg"]
                    extra["midpoint_from_accepted_actual_carbon"] = np.isclose(rule["requested_cap_kg"], requested, rtol=0, atol=1e-10)
                    extra["midpoint_fixed_allowance"] = np.isclose(cap, requested + 1e-7 + 1e-8 * abs(requested), rtol=0, atol=1e-10)
                else:
                    metric = "bill_yuan" if name == "cost_endpoint" else "carbon_kg"
                    anchor, allowance = summary["primary_incumbent_value"], summary["primary_objective_allowance"]
                    extra["known_feasible_primary_bound"] = anchor <= baseline[metric] + 1e-6
                    extra["known_feasible_returned_bound"] = row[metric] <= baseline[metric] + allowance + 1e-6
                    extra["declared_primary_allowance"] = np.isclose(allowance, 1e-7 + 1e-8 * abs(anchor), rtol=0, atol=1e-12)
                    extra["primary_zero_gap_requested"] = summary["primary_solver_requested_mip_gap"] == 0.
                    extra["primary_budget_60_seconds"] = summary["primary_solver_time_limit_seconds"] == 60.
                    extra["two_retained_solver_stages"] = len(summary["solver_stages"]) == 2
            independent = resilient.base.independent_metrics(config, frame, trajectory, cap=cap)
            extra["independent_continuous_states_and_ledger"] = independent["passed"]
            for metric in ("bill_yuan", "energy_kwh", "carbon_kg", "peak_import_kw"):
                extra["table_" + metric] = np.isclose(row[metric], independent[metric], rtol=1e-10, atol=1e-7)
            damaged = trajectory.copy()
            damaged.loc[100, "heat_kw"] += .1
            extra["power_to_state_tamper_rejected"] = not resilient.base.independent_metrics(config, frame, damaged)["passed"]
            checks.update({key + "/" + name: bool(value) for name, value in extra.items()})
            details.append({"city": city, "case": row["case"], "checks": extra,
                            "maximum_temperature_residual_c": independent["maximum_temperature_residual_c"]})
    report = {"checked_utc": datetime.now(timezone.utc).isoformat(), "passed": all(checks.values()),
              "check_count": len(checks), "failed_checks": [key for key, value in checks.items() if not value],
              "checks": checks, "cases": details, "source_zone_tamper_examples": 3 * len(protocol["cities"]),
              "power_to_state_tamper_examples": 4 * len(protocol["cities"]),
              "solver_or_model_build_called": False, "historical_result_relabelled_as_new": False,
              "manifest_sha256": resilient.base.sha(run / "manifest.json"),
              "script_sha256": resilient.base.sha(Path(__file__)),
              "verifier_dependency_sha256": {name: resilient.base.sha(ROOT / "scripts" / name) for name in
                  ("verify_three_city_climate_dispatch.py", "run_three_city_climate_dispatch.py")},
              "scope": "new saved complete-city dispatch exports under common public prototype/methodological tariff; no field engineering validation"}
    resilient._write(out, report)
    print({key: report[key] for key in ("passed", "check_count", "failed_checks")})
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(audit(args.run, args.out))
