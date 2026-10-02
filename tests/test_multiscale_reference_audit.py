"""Analytic equilibrium/decay oracles and deliberate artifact tampering.

No solver runs: equilibrium power follows H*(T-Tout)-gains directly; the free
decay oracle uses the continuous-time elapsed-time solution, not a step model.
"""
from copy import deepcopy
import importlib.util
import json
from math import exp
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("independent_multiscale_audit", ROOT / "scripts/audit_multiscale_reference.py")
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)
audit_case, verify_run, digest = AUDIT.audit_case, AUDIT.verify_run, AUDIT.digest


def equilibrium_case(n=4, members=("A", "B"), grid_cap=100., name="coupled_flexible", declared=False):
    stamps = pd.date_range("2024-11-16T00:00:00+08:00", periods=n, freq="h")
    frame = pd.DataFrame({"time": stamps, "outdoor_temp": -4.85 if declared else 10.,
                          "electricity_price": np.resize([.2, .8, 1., .4], n),
                          "grid_emission_factor": .5, "period_weight": 1., "scenario_probability": 1.})
    config = {"planning": {"time_step_hours": 1., "representative_day_weight": 1.},
              "nodes": [], "grades": [{"name": "ideal", "temperature_c": 27.}], "sources": [],
              "pipes": [], "storages": [], "demand_response": {"max_shift_fraction": 0.},
              "building_thermal": {"enabled": True, "boundary_mode": "fixed", "simultaneous_mode": "exclusive", "buildings": []},
              "multi_energy_services": {"cooling": {"enabled": True, "sources": {}, "demand_columns": {}}}}
    if grid_cap is not None:
        config["grid_connection"] = {"existing_capacity_kw": grid_cap, "max_new_capacity_kw": 0.,
                                     "firm_capacity_method": "portfolio_dispatch", "reserve_margin": 0.}
    trajectory, dispatch = [], []
    heat_electric, cool_electric = np.zeros(n), np.zeros(n)
    for member in members:
        node, building, source = f"node_{member}", f"variant_{member}", f"ideal_{member}"
        h, c = (.1043, .535576) if declared else (.1, .5)
        h *= 1. if member == "A" else 1.5
        c *= 1. if member == "A" else 4.
        gain = .2
        frame[f"gain_{member}"] = gain
        frame[f"solar_{member}"] = 0.
        spec = {"name": building, "node": node, "heat_grade": "ideal", "area_m2": 48.,
                "heat_loss_kw_per_c": h, "thermal_capacity_kwh_per_c": c, "min_temperature_c": 20.,
                "max_temperature_c": 20. if name == "coupled_all_fixed20" or name == f"coupled_{member}_fixed20" else 27.,
                "initial_temperature_c": 20., "final_temperature_c": 20.,
                "internal_gain_column": f"gain_{member}", "solar_gain_column": f"solar_{member}",
                "max_heat_kw": 1000., "max_cooling_kw": 1000.}
        config["building_thermal"]["buildings"].append(spec)
        config["nodes"].append({"name": node, "demand_columns": {"ideal": f"reference_{member}"}})
        eta = 1. if declared else (.8 if member == "A" else .9)
        eer = 1. if declared else (2. if member == "A" else 3.)
        config["sources"].append({"name": source, "node": node, "type": "electric_boiler", "efficiency": eta,
                                  "existing_capacity_kw": 1000., "max_new_capacity_kw": 0., "allowed_grades": ["ideal"]})
        config["multi_energy_services"]["cooling"]["sources"][source] = {"eer": eer, "eer_min": eer, "eer_max": eer, "capacity_equivalence": 1.}
        config["multi_energy_services"]["cooling"]["demand_columns"][node] = f"cooling_reference_{member}"
        net = h * (20. - frame.outdoor_temp.to_numpy()) - gain
        heat, cool = np.maximum(net, 0.), np.maximum(-net, 0.)
        heat_electric += heat / eta
        cool_electric += cool / eer
        for t in range(n):
            common = {"time_index": t, "time": stamps[t], "scenario": "base", "boundary_group": "base"}
            trajectory.append({**common, "building": building, "node": node, "heat_grade": "ideal",
                               "heat_kw": heat[t], "cooling_kw": cool[t],
                               "temperature_start_c": 20., "temperature_end_c": 20.,
                               "area_m2": 48., "heat_loss_kw_per_c": h, "thermal_capacity_kwh_per_c": c,
                               "outdoor_temperature_c": frame.outdoor_temp.iloc[t], "internal_gain_kw": gain,
                               "solar_gain_kw": 0., "dt_hours": 1.})
            for category, asset, grade, value, unit in (
                ("building_heat", building, "ideal", heat[t], "kW"),
                ("building_cooling", building, "cooling", cool[t], "kW"),
                ("building_temperature", building, "ideal", 20., "degC"),
                ("source_heat", source, "ideal", heat[t], "kW"),
                ("cooling_output", source, "cooling", cool[t], "kW")):
                dispatch.append({**common, "category": category, "asset": asset, "node": node,
                                 "grade": grade, "value": value, "unit": unit})
    electric = heat_electric + cool_electric
    bill = float(np.dot(electric, frame.electricity_price))
    co2 = float(np.dot(electric, frame.grid_emission_factor))
    summary = {"objective_yuan_per_year": bill, "objective_value": bill,
               "electricity_kwh_per_year": float(electric.sum()), "grid_electricity_kwh_per_year": float(electric.sum()),
               "total_service_emissions_kg_per_year": co2, "numerical_audit_passed": True,
               "components_yuan_per_year": {"operating_cost": float(np.dot(heat_electric, frame.electricity_price)),
                                            "cooling_operating_cost": float(np.dot(cool_electric, frame.electricity_price))}}
    return config, frame, pd.DataFrame(trajectory), pd.DataFrame(dispatch), summary


def rejected(parts, code=None):
    report = audit_case(*parts)
    assert report["passed"] is False, report
    if code:
        assert report["checks"][code]["passed"] is False, report
    json.dumps(report, allow_nan=False)
    return report


def test_two_building_equilibrium_is_independent_of_rc_coefficients_and_area():
    parts = equilibrium_case()
    report = audit_case(*parts)
    assert report["passed"], report
    assert report["metrics"]["max_rc_residual_c"] < 1e-12
    assert report["metrics"]["electricity_kwh"] == pytest.approx(4 * (.8 / .8 + 1.3 / .9))
    assert len(report["buildings"]) == 2
    assert len(report["node_balances"]) == 4
    assert report["metrics"]["outside_comfort_degree_hours"] == 0.
    json.dumps(report, allow_nan=False)


def test_analytic_elapsed_time_free_decay_oracle():
    c, f, tr, ds, summary = equilibrium_case(members=("A",), grid_cap=None)
    spec = c["building_thermal"]["buildings"][0]
    h, mass = spec["heat_loss_kw_per_c"], spec["thermal_capacity_kwh_per_c"]
    spec["min_temperature_c"] = 0.
    spec["final_temperature_c"] = 20 * exp(-h * len(f) / mass)
    f.outdoor_temp = f.gain_A = 0.
    tr.outdoor_temperature_c = tr.internal_gain_kw = tr.heat_kw = tr.cooling_kw = 0.
    tr.temperature_start_c = [20 * exp(-h * t / mass) for t in range(len(f))]
    tr.temperature_end_c = [20 * exp(-h * (t + 1) / mass) for t in range(len(f))]
    ds.loc[ds.category != "building_temperature", "value"] = 0.
    ds.loc[ds.category == "building_temperature", "value"] = tr.temperature_end_c.to_numpy()
    for key in ("objective_yuan_per_year", "objective_value", "electricity_kwh_per_year", "grid_electricity_kwh_per_year", "total_service_emissions_kg_per_year"):
        summary[key] = 0.
    summary["components_yuan_per_year"] = {"operating_cost": 0., "cooling_operating_cost": 0.}
    result = audit_case(c, f, tr, ds, summary)
    assert result["passed"], result
    assert result["buildings"][0]["final_end_c"] == pytest.approx(20 * exp(-.8))


@pytest.mark.parametrize("field", ["heat_kw", "cooling_kw", "temperature_end_c", "temperature_start_c"])
def test_single_hour_trajectory_tampering_is_rejected(field):
    parts = list(equilibrium_case())
    parts[2].loc[1, field] += .3
    rejected(parts)


@pytest.mark.parametrize("category", ["source_heat", "cooling_output", "building_heat", "building_temperature"])
def test_single_hour_dispatch_power_or_temperature_tampering_is_rejected(category):
    parts = list(equilibrium_case())
    index = parts[3].index[(parts[3].category == category) & (parts[3].time_index == 1)][0]
    parts[3].loc[index, "value"] += .3
    rejected(parts)


@pytest.mark.parametrize("export", [2, 3])
def test_forged_node_assignment_is_rejected(export):
    parts = list(equilibrium_case())
    parts[export].loc[0, "node"] = "node_B"
    rejected(parts, "trajectory_assignment" if export == 2 else "dispatch_assignment")


@pytest.mark.parametrize("tamper", ["none", "missing_building", "missing_hour", "duplicate_hour", "missing_dispatch"])
def test_missing_or_duplicated_trajectory_and_dispatch_fail_closed(tamper):
    parts = list(equilibrium_case())
    if tamper == "none":
        parts[2] = None
    elif tamper == "missing_building":
        parts[2] = parts[2][parts[2].building == "variant_A"]
    elif tamper == "missing_hour":
        parts[2] = parts[2].iloc[:-1]
    elif tamper == "duplicate_hour":
        parts[2] = pd.concat([parts[2], parts[2].iloc[[0]]])
    else:
        parts[3] = parts[3].iloc[:-1]
    rejected(parts)


def test_reconstructed_shared_grid_excess_is_rejected_without_exported_grid_field():
    parts = list(equilibrium_case(grid_cap=1.))
    rejected(parts, "shared_grid_limit")


def test_source_combined_heating_cooling_capacity_is_rejected():
    parts = list(equilibrium_case())
    parts[0]["sources"][0]["existing_capacity_kw"] = .1
    rejected(parts, "source_capacity")


@pytest.mark.parametrize("export", [2, 3])
def test_index_cannot_launder_clock_tampering(export):
    parts = list(equilibrium_case())
    parts[export]["time"] = parts[export]["time"].astype(object)
    parts[export].loc[0, "time"] = "2024-11-16T01:00:00+08:00"
    rejected(parts, "time_alignment")


@pytest.mark.parametrize("field", ["objective_yuan_per_year", "electricity_kwh_per_year",
                                 "grid_electricity_kwh_per_year", "total_service_emissions_kg_per_year"])
def test_summary_charge_energy_and_carbon_tampering_is_rejected(field):
    parts = list(equilibrium_case())
    parts[4][field] += 1.
    rejected(parts, "summary_accounting")


def test_summary_component_tampering_cannot_preserve_total_cost():
    parts = list(equilibrium_case())
    parts[4]["components_yuan_per_year"]["operating_cost"] += 1.
    parts[4]["components_yuan_per_year"]["cooling_operating_cost"] -= 1.
    rejected(parts, "summary_accounting")


@pytest.mark.parametrize("export,column", [(2, "heat_kw"), (2, "temperature_end_c"), (3, "value")])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), True])
def test_nonfinite_or_boolean_values_fail_closed(export, column, value):
    parts = list(equilibrium_case())
    parts[export][column] = parts[export][column].astype(object)
    parts[export].loc[0, column] = value
    rejected(parts, "valid_data")


def test_declared_source_efficiency_is_used_instead_of_assuming_ideal_one():
    parts = list(equilibrium_case())
    parts[0]["sources"][0]["efficiency"] = 1.
    rejected(parts, "summary_accounting")


def test_declared_hc_and_input_driver_tampering_are_not_hidden_by_zero_residual():
    for field in ("heat_loss_kw_per_c", "thermal_capacity_kwh_per_c"):
        parts = list(equilibrium_case())
        parts[2][field] += .1
        parts[2]["rc_residual_c"] = 0.
        rejected(parts, "trajectory_input_metadata")
    parts = list(equilibrium_case())
    parts[1].loc[0, "gain_A"] += .1
    rejected(parts, "rc_convolution")


def test_no_investment_is_enforced_even_when_existing_assets_remain_feasible():
    parts = list(equilibrium_case())
    parts[0]["sources"][0]["max_new_capacity_kw"] = 100.
    rejected(parts, "valid_data")


@pytest.fixture(scope="module")
def frozen_run(tmp_path_factory):
    run = tmp_path_factory.mktemp("multiscale_audit_frozen")
    cases = ["independent_A", "independent_B", "joint_unlimited", "coupled_flexible",
             "coupled_A_fixed20", "coupled_B_fixed20", "coupled_all_fixed20"]
    capacity = .1043 * 2.5 * 24.85
    protocol = {"schema_version": "multiscale_reference.v1", "cases": cases,
                "start_inclusive": "2024-11-16T00:00:00+08:00", "end_exclusive": "2025-03-16T00:00:00+08:00",
                "hours_per_building": 2880, "dt_hours": 1, "unit_physical_weights": True,
                "case_files": {}, "source_snapshots": {}, "reference_inputs": {},
                "shared_resource": {"capacity_kw": capacity}}
    comparison = []
    for name in cases:
        members = ("A",) if name == "independent_A" else ("B",) if name == "independent_B" else ("A", "B")
        parts = equilibrium_case(n=2880, members=members, name=name, declared=True,
                                 grid_cap=capacity if name.startswith("coupled_") else None)
        config, frame, tr, ds, summary = parts
        source = run / "input_cases" / name
        source.mkdir(parents=True)
        result = run / "results" / name
        result.mkdir(parents=True)
        for directory in (source, result):
            (directory / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
            frame.to_csv(directory / "input.csv", index=False)
        protocol["case_files"][name] = {p.relative_to(run).as_posix(): digest(p) for p in source.iterdir()}
        tr.to_csv(result / "building_trajectory.csv", index=False)
        ds.to_csv(result / "dispatch.csv", index=False)
        (result / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
        (result / "numerical_audit.json").write_text(json.dumps({"numerical_audit_passed": True}), encoding="utf-8")
        report = audit_case(*parts)
        assert report["passed"], report
        comparison.append({"name": name, "status": "passed", "audit": report})
    snapshot = run / "frozen_code" / "analytic_reference_input.csv"
    snapshot.parent.mkdir()
    snapshot.write_bytes((run / "input_cases/independent_A/input.csv").read_bytes())
    original = "tests/analytic_reference_input.csv"
    protocol["source_snapshots"][original] = {"path": snapshot.relative_to(run).as_posix(), "sha256": digest(snapshot)}
    protocol["reference_inputs"][original] = digest(snapshot)
    (run / "protocol.json").write_text(json.dumps(protocol), encoding="utf-8")
    (run / "protocol_lock.json").write_text(json.dumps({"protocol.json": digest(run / "protocol.json")}), encoding="utf-8")
    (run / "comparison.json").write_text(json.dumps(comparison), encoding="utf-8")
    (run / "manifest.json").write_text(json.dumps({"files": {p.relative_to(run).as_posix(): digest(p) for p in run.rglob("*") if p.is_file()}}), encoding="utf-8")
    return run


def test_disk_verifier_checks_complete_seven_case_run(frozen_run):
    report = verify_run(frozen_run)
    assert report["passed"], report
    assert len(report["cases"]) == 7
    assert all(case["passed"] for case in report["cases"])
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize("relative,code", [
    ("frozen_code/analytic_reference_input.csv", "source_snapshot_hashes"),
    ("input_cases/independent_A/input.csv", "frozen_input_hashes"),
    ("results/independent_A/dispatch.csv", "output_manifest_hashes"),
    ("protocol.json", "protocol_lock")])
def test_frozen_input_source_protocol_and_output_hash_tampering_rejected(frozen_run, relative, code):
    path = frozen_run / relative
    original = path.read_bytes()
    try:
        path.write_bytes(original + b"\n")
        report = verify_run(frozen_run)
        assert report["passed"] is False
        assert report["checks"][code]["passed"] is False
    finally:
        path.write_bytes(original)


def test_failure_record_cannot_be_laundered_by_successful_stale_artifacts(frozen_run):
    path = frozen_run / "results/independent_A/failure.json"
    try:
        path.write_text(json.dumps({"name": "independent_A", "status": "infeasible", "reason": "test retained failed attempt"}), encoding="utf-8")
        report = verify_run(frozen_run)
        assert report["passed"] is False
        case = next(case for case in report["cases"] if case["name"] == "independent_A")
        assert case["status"] == "infeasible" and case["passed"] is False
        assert case["failure"]["reason"] == "test retained failed attempt"
    finally:
        path.unlink()


def test_missing_solution_artifact_is_rejected_on_disk(frozen_run):
    path = frozen_run / "results/independent_A/building_trajectory.csv"
    original = path.read_bytes()
    try:
        path.unlink()
        report = verify_run(frozen_run)
        assert report["passed"] is False
        case = next(case for case in report["cases"] if case["name"] == "independent_A")
        assert case["passed"] is False
        assert "missing solution artifact" in case["reason"]
    finally:
        path.write_bytes(original)
