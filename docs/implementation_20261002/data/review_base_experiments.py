"""Read frozen E1/E2/E5 files and independently recompute season identities.

Does not import the solver/model or write into the frozen experiment directory.
"""
from pathlib import Path
import hashlib
import json

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "docs/implementation_20261002/data"
RUNS = ROOT / "docs/implementation_20261002/reference_experiments"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def local_path(path):
    return Path(path.replace("\\", "/"))


def fields(value, prefix=""):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from fields(child, f"{prefix}.{key}" if prefix else key)
    elif isinstance(value, list):
        if not value:
            yield prefix, value
        for index, child in enumerate(value):
            yield from fields(child, f"{prefix}[{index}]")
    else:
        yield prefix, value


def review(*, timeseries="data/derived/evidence_case_20261002/wuhan_2024_2025_hourly.csv",
           config_path="configs/evidence_case_20261002/wuhan_bestest600_rc.yaml",
           gate_file="admission.json", runs=RUNS, modes=("rc", "steady"),
           output_name="base_experiment_peer_review.json", variant="base"):
    source = pd.read_csv(ROOT / timeseries)
    clock = pd.to_datetime(source.time, utc=True)
    expected_clock = pd.date_range("2024-11-15T16:00Z", "2025-03-15T16:00Z", freq="h", inclusive="left")
    assert pd.DatetimeIndex(clock).equals(expected_clock)
    assert source.period_weight.eq(1).all() and source.scenario_probability.eq(1).all()
    base = yaml.safe_load((ROOT / config_path).read_text(encoding="utf-8"))
    H, C = .1043, .535576
    assert np.isclose(base["building_thermal"]["buildings"][0]["heat_loss_kw_per_c"], H)
    assert np.isclose(base["building_thermal"]["buildings"][0]["thermal_capacity_kwh_per_c"], C)
    gate = read(DATA / gate_file)
    gate_checks = {key: digest(ROOT / local_path(key)) == value for key, value in gate["input_sha256"].items()}
    stages, locked_checks, version_differences, controls = [], {}, {}, {}
    for mode in modes:
        directory = runs / mode
        protocol, manifest = read(directory / "protocol.json"), read(directory / "manifest.json")
        locked_checks[mode] = {
            "input_hashes": {key: digest(ROOT / local_path(key)) == value for key, value in protocol["input_sha256"].items()},
            "output_hashes": {key: digest(directory / local_path(key)) == value for key, value in manifest["files"].items()},
            "run_record_code_stable": manifest["code_stable_during_run"],
        }
        version_differences[mode] = {key: {"frozen": value, "current": digest(ROOT / local_path(key))}
                                    for key, value in protocol["code_sha256"].items() if digest(ROOT / local_path(key)) != value}
        for stage_dir in sorted(directory.iterdir()):
            if not stage_dir.is_dir() or not (stage_dir / "summary.json").is_file():
                continue
            summary = read(stage_dir / "summary.json")
            config = yaml.safe_load((stage_dir / "effective_config.yaml").read_text(encoding="utf-8"))
            dispatch = pd.read_csv(stage_dir / "dispatch.csv")
            heat_dispatch = dispatch.loc[dispatch.category.eq("source_heat")].sort_values("time_index")
            cool_dispatch = dispatch.loc[dispatch.category.eq("cooling_output")].sort_values("time_index")
            assert len(heat_dispatch) == len(cool_dispatch) == 2880
            assert np.array_equal(heat_dispatch.time_index, np.arange(2880))
            assert pd.DatetimeIndex(pd.to_datetime(heat_dispatch.time, utc=True)).equals(expected_clock)
            assert pd.DatetimeIndex(pd.to_datetime(cool_dispatch.time, utc=True)).equals(expected_clock)
            heat = heat_dispatch.value.to_numpy()
            cool = cool_dispatch.value.to_numpy()
            checks = {"dispatch_clock_matches_source": True, "finite_dispatch": bool(np.isfinite(heat).all() and np.isfinite(cool).all()),
                      "capacity_no_excess": bool(np.max(heat + cool) <= 1000 + 1e-8),
                      "nonnegative_service": bool(min(heat.min(), cool.min()) >= -1e-8)}
            record = {"mode": mode, "stage": stage_dir.name, "hours": 2880,
                      "heat_kwh": float(heat.sum()), "cooling_kwh": float(cool.sum()),
                      "electricity_kwh": float((heat + cool).sum()),
                      "reference_energy_charge_yuan": float(np.dot(heat + cool, source.electricity_price)),
                      "reference_average_factor_co2_kg": float(np.dot(heat + cool, source.grid_emission_factor)),
                      "peak_import_kw": float(np.max(heat + cool))}
            errors = {
                "heat_kwh": abs(record["heat_kwh"] - summary["building_heat_delivered_kwh_per_year"] if mode == "rc" else record["heat_kwh"] - summary["demand_kwh_per_year"]),
                "cooling_kwh": abs(record["cooling_kwh"] - summary["building_cooling_delivered_kwh_per_year"] if mode == "rc" else record["cooling_kwh"] - summary["cooling_demand_kwh_per_year"]),
                # The generic electricity total already includes cooling;
                # cooling_electricity is a component, not an additional total.
                "electricity_kwh": abs(record["electricity_kwh"] - summary["electricity_kwh_per_year"]),
                "charge_yuan": abs(record["reference_energy_charge_yuan"] - summary["objective_yuan_per_year"]),
                "co2_kg": abs(record["reference_average_factor_co2_kg"] - summary["total_service_emissions_kg_per_year"]),
            }
            checks["reported_season_metrics_recompute"] = max(errors.values()) < 1e-6
            record["summary_absolute_errors"] = errors
            if mode == "rc":
                tr = pd.read_csv(stage_dir / "building_trajectory.csv")
                assert len(tr) == 2880
                start, end = tr.temperature_start_c.to_numpy(), tr.temperature_end_c.to_numpy()
                dt = tr.dt_hours.to_numpy()
                a = np.exp(-H * dt / C)
                predicted = a * start + (1 - a) * tr.outdoor_temperature_c + (1 - a) / H * (heat - cool + tr.internal_gain_kw + tr.solar_gain_kw)
                residual = float(np.max(np.abs(predicted - end)))
                continuity = float(np.max(np.abs(end[:-1] - start[1:])))
                checks.update({"trajectory_clock_matches_source": pd.DatetimeIndex(pd.to_datetime(tr.time, utc=True)).equals(expected_clock),
                               "exact_rc_equation": residual < 1e-8, "state_continuity": continuity < 1e-8,
                               "initial_terminal_20c": max(abs(start[0] - 20), abs(end[-1] - 20)) < 1e-8,
                               "source_weather_gain_matches": bool(np.allclose(tr.outdoor_temperature_c, source.outdoor_temp, rtol=0, atol=1e-10) and np.allclose(tr.internal_gain_kw, .2) and np.allclose(tr.solar_gain_kw, 0)),
                               "trajectory_dispatch_heat_cool_match": bool(np.allclose(heat, tr.heat_kw, rtol=0, atol=1e-8) and np.allclose(cool, tr.cooling_kw, rtol=0, atol=1e-8)),
                               "hard_comfort": bool(np.all(start >= tr.min_temperature_c - 1e-8) and np.all(start <= tr.max_temperature_c + 1e-8) and np.all(end >= tr.min_temperature_c - 1e-8) and np.all(end <= tr.max_temperature_c + 1e-8)),
                               "heat_cool_exclusive": float(np.max(np.minimum(heat, cool))) < 1e-8})
                record.update({"rc_max_residual_c": residual, "continuity_max_residual_c": continuity,
                               "temperature_min_c": float(min(start.min(), end.min())), "temperature_max_c": float(max(start.max(), end.max()))})
            else:
                checks["steady_service_formulas"] = bool(np.allclose(heat, np.maximum(H * (20 - source.outdoor_temp) - .2, 0), rtol=0, atol=1e-8) and np.allclose(cool, np.maximum(H * (source.outdoor_temp - 27) + .2, 0), rtol=0, atol=1e-8))
            cap = summary.get("emissions_cap_kg_per_year")
            record["effective_emissions_cap_kg"] = cap
            checks["emission_cap"] = cap is None or record["reference_average_factor_co2_kg"] <= cap + 1e-6
            checks["solver_numerical_record"] = summary["solver_status"] == 0 and summary["numerical_audit_passed"]
            record["checks"] = {key: bool(value) for key, value in checks.items()}
            record["passed"] = all(record["checks"].values())
            stages.append(record)
        if mode == "rc":
            replay = yaml.safe_load((directory / "fixed_replay/effective_config.yaml").read_text(encoding="utf-8"))
            fixed = yaml.safe_load((directory / "fixed_lower_temperature/effective_config.yaml").read_text(encoding="utf-8"))
            left, right = dict(fields(replay)), dict(fields(fixed))
            changed = {key: {"before": left.get(key), "after": right.get(key)} for key in left.keys() | right.keys() if left.get(key) != right.get(key)}
            expected = {"building_thermal.buildings[0].max_temperature_c": {"before": 27., "after": 20.}}
            assert changed == expected
            controls["fixed20_changed_fields"] = changed
            caps = read(directory / "epsilon_caps.json")
            controls["frontier_range_degenerate"] = caps["tradeoff_range_degenerate"]
            controls["requested_epsilon_caps_kg"] = caps["caps"]
    control = next(row for row in stages if row["stage"] == "fixed_lower_temperature")
    baseline = next(row for row in stages if row["stage"] == "baseline" and row["mode"] == "rc")
    controls["fixed20_extra_electricity_kwh"] = control["electricity_kwh"] - baseline["electricity_kwh"]
    controls["fixed20_extra_electricity_percent"] = controls["fixed20_extra_electricity_kwh"] / baseline["electricity_kwh"] * 100
    controls["identity"] = "Fixing 20C tightens the upper bound from 27C; it is not a lower-heating-setpoint or observed-occupancy experiment"
    passed = all(gate_checks.values()) and all(row["passed"] for row in stages) and all(all(group[key].values()) for group in locked_checks.values() for key in ("input_hashes", "output_hashes"))
    version_scope = ("Results belong to their frozen b7f7 model version. Main reports subsequent change adds cooling-variable cost to shared electricity basic-cost accounting, without changing solver equations; no retrospective relabelling of old results as the current version." if variant == "base" else
                     "Independent TOU experiment results belong to the code hashes in their own protocol; current differences are recorded explicitly.")
    report = {"passed": passed, "variant": variant, "method": "Independent pandas/numpy reading of frozen CSVs; exact analytic 1R1C and direct energy/charge/CO2 sums; no solver/model imports or rerun",
              "source_admission_hash": digest(DATA / gate_file), "source_hash_checks": gate_checks,
              "locked_experiment_hash_checks": locked_checks, "current_code_differences": version_differences,
              "version_scope": version_scope,
              "quantity_scope": "All *_per_year fields here represent 2880-hour season sums; no annualization. Charge and fixed annual-average-factor CO2 only, no full economics, exergy performance, local building accuracy or BESTEST certification.",
              "stages": stages, "controls": controls}
    (DATA / output_name).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": passed, "stages": len(stages), "controls": controls, "current_code_differences": version_differences}, ensure_ascii=False, indent=2))
    if not passed:
        raise SystemExit(1)
    return report


if __name__ == "__main__":
    review()
