"""Analytical design fixtures; these numbers are not field/network observations."""
from copy import deepcopy
import importlib.util
import math
from pathlib import Path

import pandas as pd
import pytest

from heatplan.network_feedback import (mode_physics, mode_violations,
    build_feedback_model, solve_with_network_feedback, FeedbackFailure)

spec = importlib.util.spec_from_file_location("network_case", Path(__file__).resolve().parents[1] / "scripts/run_network_feedback_reference.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def one_branch(demand=1.):
    config = runner.reference_config()
    config["network_feedback"]["branches"] = config["network_feedback"]["branches"][1:]
    config["nodes"] = [n for n in config["nodes"] if n["name"] != "near"]
    frame = pd.DataFrame({"time": ["2024-01-01T00:00:00+08:00"], "far_heat_kw": [demand],
                          "electricity_price": [1.], "grid_emission_factor": [.5]})
    return config, frame


def test_two_leg_enthalpy_and_zero_loss_exact():
    c, _ = one_branch()
    branch = c["network_feedback"]["branches"][0]
    mode = branch["operating_modes"][-1]
    result = mode_physics(branch, mode, c["network_feedback"]["water"])
    cp_m = 4.18 * mode["mass_flow_kg_per_s"]
    losses = cp_m * ((65 - result["far_supply_c"]) + (40 - result["plant_return_c"]))
    assert result["heat_loss_kw"] == pytest.approx(losses)
    branch["heat_loss_w_per_mk_per_leg"] = 0.
    result = mode_physics(branch, mode, c["network_feedback"]["water"])
    assert result["far_supply_c"] == 65
    assert result["plant_return_c"] == 40
    assert result["delivery_efficiency"] == 1


def test_feedback_matches_enumerated_valid_mode_optimum_and_closes_ledger():
    c, frame = one_branch(1.)
    branch = c["network_feedback"]["branches"][0]
    branch["delta_t_penalty_yuan_per_c_on_hour"] = 0
    model, catalog, _ = build_feedback_model(c, frame)
    candidates = []
    for entry in catalog:
        if not entry["engineering_violations"] and entry["delivered_heat_kw"] >= 1:
            candidates.append((entry["source_heat_kw"] + entry["pump_kw"]) / entry["delivered_heat_kw"])
    result = solve_with_network_feedback(c, frame)
    assert result.audit["passed"]
    assert len(result.history) > 1
    assert result.history[0]["status"] == "rejected"
    assert result.solution.summary["objective_yuan_per_year"] == pytest.approx(min(candidates))
    assert result.audit["source_heat_kwh"] == pytest.approx(result.audit["delivered_heat_kwh"] + result.audit["heat_loss_kwh"])


def test_large_soft_penalty_never_relaxes_far_supply_temperature():
    c, frame = one_branch(.3)
    branch = c["network_feedback"]["branches"][0]
    branch.update(target_customer_delta_t_c=7., delta_t_soft_band_c=0., delta_t_penalty_yuan_per_c_on_hour=100.)
    result = solve_with_network_feedback(c, frame)
    assert any("far_supply_c" in e["engineering_violations"] for e in result.catalog)
    assert result.history[0]["status"] == "rejected"
    assert result.audit["minimum_active_far_supply_c"] >= 55 - 1e-6
    assert result.audit["soft_penalty_yuan"] > 0


def test_all_invalid_modes_fail_with_preserved_cut_trace():
    c, frame = one_branch(.3)
    c["network_feedback"]["branches"][0]["minimum_far_supply_c"] = 64.
    with pytest.raises(FeedbackFailure) as caught:
        solve_with_network_feedback(c, frame)
    assert caught.value.history[0]["added_mode_cuts"]
    assert caught.value.history[-1]["status"] == "failed"
    assert caught.value.history[-1]["solver_evidence"]["solver_status"] == 2


def test_iteration_limit_does_not_report_engineering_acceptance():
    c, frame = one_branch()
    with pytest.raises(FeedbackFailure, match="iteration limit"):
        solve_with_network_feedback(c, frame, max_rounds=1)


@pytest.mark.parametrize("mutation", ["nan", "zero_mass", "transition", "rc", "heatpump", "shared_pump", "duplicate_branch", "source_temperature"])
def test_unsupported_domains_fail_before_solve(mutation):
    c, frame = one_branch()
    s = c["network_feedback"]
    branch = s["branches"][0]
    if mutation == "nan": branch["inner_diameter_m"] = math.nan
    if mutation == "zero_mass": branch["operating_modes"][0]["mass_flow_kg_per_s"] = 0
    if mutation == "transition": branch["operating_modes"][0]["mass_flow_kg_per_s"] = .02
    if mutation == "rc": c["building_thermal"] = {"enabled": True}
    if mutation == "heatpump": c["sources"][0]["type"] = "heat_pump"
    if mutation == "shared_pump": s["independent_branch_pumps"] = False
    if mutation == "duplicate_branch": s["branches"].append(deepcopy(branch))
    if mutation == "source_temperature": c["sources"][0]["supply_temp_c"] = 60
    with pytest.raises(ValueError): build_feedback_model(c, frame)
