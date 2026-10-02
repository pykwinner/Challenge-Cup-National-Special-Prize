"""Steady heat-balance oracle and export mutations; no optimization is run."""
import csv
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_investment_reference.py"
SPEC = importlib.util.spec_from_file_location("independent_investment_audit", SCRIPT)
AUDITOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDITOR)


def write_json(path, value):
    path.write_text(json.dumps(value, allow_nan=False), encoding="utf-8")


def write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def case(tmp_path):
    """T=20 is an analytic equilibrium for each forcing, independent of the RC step."""
    protocol = {
        "window_start_inclusive": "2024-11-16T00:00:00+08:00",
        "window_end_exclusive": "2024-11-16T04:00:00+08:00",
        "physics": {"H_kw_per_K": .2, "C_kwh_per_K": 2., "initial_c": 20.,
                    "terminal_c": 20., "min_c": 20., "max_c": 27., "dt_hours": 1.},
        "design": {"eta_resistance": 1., "eer_cooling": 2., "max_resistance_kw": 6.,
                   "max_cooling_kw": 2., "max_grid_kw": 4.,
                   "resistance_capex_cny_per_kw": 100., "cooling_package_capex_cny": 1000.,
                   "hp_package_capex_cny": 17800.,
                   "resistance_fixed_om_cny_per_kw_window": 5.,
                   "hp_fixed_om_cny_per_unit_window": 20.,
                   "cooling_fixed_om_cny_per_window": 10.,
                   "repeat_window_factor": 2., "hp_unit_candidates": [1]},
        "bound_scope": {"kind": "free_registered_candidates", "hp_unit_candidates": [1], "fixed_resistance_kw": None},
    }
    inputs, dispatch = [], []
    # H*(20-Tout) = [2, 1, -2, 0] kW, met by [R+HP-C].
    outside, qr, qhp, qc = [10, 15, 30, 20], [1, 0, 0, 0], [1, 1, 0, 0], [0, 0, 2, 0]
    power, prices, carbon = [1.5, .5, 1., 0.], [.6, 1., .4, .6], [.5, .6, .7, .8]
    for index in range(4):
        stamp = f"2024-11-16T0{index}:00:00+08:00"
        inputs.append({"time": stamp, "outdoor_temp": outside[index],
                       "electricity_price": prices[index], "grid_emission_factor": carbon[index],
                       "internal_gain_kw": 0., "solar_gain_kw": 0., "cop_hp": 2.,
                       "capacity_hp_per_unit_kw": 8.})
        dispatch.append({"time": stamp, "q_resistance_kw": qr[index], "q_hp_kw": qhp[index],
                         "q_cooling_kw": qc[index], "electricity_kw": power[index],
                         "temperature_end_c": 20.})
    investments = {"hp_units": 1, "resistance_capacity_kw": 1., "cooling_capacity_kw": 2.}
    # Manual oracle: energy=3; bill=.9+.5+.4; CO2=.75+.3+.7.
    summary = {"capex_cny": 18900., "window_energy_bill_cny": 1.8,
               "window_fixed_om_cny": 35., "window_co2_kg": 1.75, "electricity_kwh": 3.,
               "comparison_cost_cny": 18973.6, "requested_epsilon_co2_kg": None,
               "solver_stages": [{"success": True, "status": 0, "mode": "cost",
                                  "epsilon_co2_kg": None, "constraint_max_violation": 0.,
                                  "seconds": .01, "primal_objective": 113.6,
                                  "dual_objective": 113.6, "primal_dual_gap": 0.,
                                  "objective_constant": 18860.},
                                 {"success": True, "status": 0, "mode": "carbon",
                                  "epsilon_co2_kg": None, "constraint_max_violation": 0.,
                                  "seconds": .01, "primal_objective": 1.75,
                                  "dual_objective": 1.75, "primal_dual_gap": 0.,
                                  "objective_constant": 0.}]}
    plan = tmp_path / "plan"
    plan.mkdir()
    paths = {"plan": plan, "input": tmp_path / "input.csv", "protocol": tmp_path / "protocol.json"}
    result = {**paths, "inputs": inputs, "dispatch": dispatch, "investments": investments,
              "summary": summary, "protocol_value": protocol}
    sync_enum(result)
    save_case(result)
    return result


def save_case(case):
    write_csv(case["input"], case["inputs"])
    write_csv(case["plan"] / "dispatch.csv", case["dispatch"])
    write_json(case["protocol"], case["protocol_value"])
    write_json(case["plan"] / "investments.json", case["investments"])
    write_json(case["plan"] / "summary.json", case["summary"])
    write_json(case["plan"] / "enumeration.json", case["enumeration"])


def sync_enum(case):
    """Synthetic status fixture, not an assertion that the oracle was optimized."""
    summary = case["summary"]
    first = summary["solver_stages"][0]
    summary["mode"] = first["mode"]
    summary["bound_scope"] = deepcopy(case["protocol_value"]["bound_scope"])
    summary["integer_enumeration_complete"] = True
    summary["global_primary_lower_bound"] = first["dual_objective"] + first["objective_constant"]
    value = summary["comparison_cost_cny"] if first["mode"] == "cost" else summary["window_co2_kg"]
    summary["global_primary_gap"] = max(0., value-summary["global_primary_lower_bound"])
    case["enumeration"] = [{"success": True, **case["investments"], "solver_stages": deepcopy(summary["solver_stages"])}]


def audit(case):
    return AUDITOR.audit_plan(case["plan"], case["input"], case["protocol"])


def test_equilibrium_oracle_passes_with_legitimate_zero_readings(case):
    result = audit(case)
    assert result["passed"], result["errors"]
    assert result["recomputed"]["electricity_kwh"] == pytest.approx(3.)
    assert result["recomputed"]["window_energy_bill_cny"] == pytest.approx(1.8)
    assert result["recomputed"]["comparison_cost_cny"] == pytest.approx(18973.6)
    assert not result["real_source_closure_asserted"]
    assert not result["field_or_device_validation"]


@pytest.mark.parametrize("field", AUDITOR.ACCOUNT_KEYS)
def test_each_summary_ledger_is_independently_recomputed(case, field):
    case["summary"][field] += 1
    save_case(case)
    result = audit(case)
    assert not result["passed"]
    assert result["checks"][f"ledger_{field}"] is False


def test_changed_meter_power_cannot_hide_behind_correct_summary(case):
    case["dispatch"][0]["electricity_kw"] += .2
    save_case(case)
    result = audit(case)
    assert not result["passed"]
    assert result["checks"]["electricity_balance"] is False
    assert result["checks"]["ledger_electricity_kwh"] is True


def test_nonsensical_resistance_investment_is_rejected(case):
    case["investments"]["resistance_capacity_kw"] = .5
    save_case(case)
    result = audit(case)
    assert not result["passed"]
    assert result["checks"]["hourly_capacity"] is False


def test_hp_availability_is_checked_hour_by_hour(case):
    case["inputs"][0]["capacity_hp_per_unit_kw"] = .5
    save_case(case)
    result = audit(case)
    assert not result["passed"]
    assert result["checks"]["hourly_capacity"] is False


def test_fixed_cooling_package_cannot_be_removed_to_make_costs_cheaper(case):
    case["investments"]["cooling_capacity_kw"] = 0.
    case["summary"]["capex_cny"] -= 1000
    case["summary"]["window_fixed_om_cny"] -= 10
    case["summary"]["comparison_cost_cny"] -= 1020
    save_case(case)
    result = audit(case)
    assert not result["passed"]
    assert result["checks"]["fixed_cooling_investment"] is False
    assert result["checks"]["ledger_capex_cny"] is False


def test_finite_grid_limit_is_not_an_ideal_unbounded_supply(case):
    case["protocol_value"]["design"]["max_grid_kw"] = 1.4
    save_case(case)
    result = audit(case)
    assert not result["passed"]
    assert result["checks"]["grid_capacity"] is False


def test_simultaneous_heat_cooling_fails_despite_correct_net_rc_and_accounts(case):
    # A paid but physically prohibited 1kW heat/1kW cold circulation: net heat=0.
    case["dispatch"][3].update(q_hp_kw=1., q_cooling_kw=1., electricity_kw=1.)
    case["summary"]["electricity_kwh"] += 1.
    case["summary"]["window_energy_bill_cny"] += .6
    case["summary"]["window_co2_kg"] += .8
    case["summary"]["comparison_cost_cny"] += 1.2
    stage = case["summary"]["solver_stages"][0]
    stage["primal_objective"] += 1.2
    stage["dual_objective"] += 1.2
    save_case(case)
    result = audit(case)
    assert not result["passed"]
    assert result["checks"]["rc_dynamics"] is True
    assert result["checks"]["heat_cooling_exclusive"] is False
    assert all(result["checks"][f"ledger_{field}"] for field in AUDITOR.ACCOUNT_KEYS)


@pytest.mark.parametrize("temperature,check", [(19., "comfort"), (20.01, "rc_dynamics")])
def test_temperature_export_mutations_are_detected(case, temperature, check):
    case["dispatch"][0]["temperature_end_c"] = temperature
    save_case(case)
    result = audit(case)
    assert not result["passed"]
    assert result["checks"][check] is False


def test_free_terminal_heat_cannot_be_exported_as_closed_window(case):
    case["dispatch"][-1]["temperature_end_c"] = 20.1
    save_case(case)
    result = audit(case)
    assert not result["passed"]
    assert result["checks"]["terminal"] is False


def test_deleted_hour_fails_even_when_deleted_from_both_exports(case):
    del case["inputs"][1]
    del case["dispatch"][1]
    save_case(case)
    result = audit(case)
    assert not result["passed"]
    assert result["checks"]["input_registered_chronology"] is False
    assert result["checks"]["dispatch_registered_chronology"] is False


def test_reordered_and_duplicate_dispatch_timestamps_fail(case):
    case["dispatch"][1]["time"] = case["dispatch"][0]["time"]
    save_case(case)
    result = audit(case)
    assert not result["passed"]
    assert result["checks"]["dispatch_registered_chronology"] is False


@pytest.mark.parametrize("field,value", [("cop_hp", ""), ("cop_hp", "nan"),
                                         ("cop_hp", 0), ("electricity_price", "inf")])
def test_missing_or_undefined_input_cannot_be_zero_imputed(case, field, value):
    case["inputs"][0][field] = value
    save_case(case)
    result = audit(case)
    assert not result["passed"]
    assert result["errors"]


def test_timezone_cannot_be_silently_relabelled(case):
    case["inputs"][0]["time"] = "2024-11-16T00:00:00"
    save_case(case)
    result = audit(case)
    assert not result["passed"]
    assert "+08:00" in result["errors"][0]


@pytest.mark.parametrize("units", [1.5, True, 3])
def test_unit_investment_is_integer_and_in_registered_enumeration(case, units):
    case["investments"]["hp_units"] = units
    save_case(case)
    assert not audit(case)["passed"]


def test_hard_epsilon_is_checked_from_energy_not_summary_claim(case):
    case["summary"]["requested_epsilon_co2_kg"] = 1.7
    save_case(case)
    result = audit(case)
    assert not result["passed"]
    assert result["checks"]["carbon_epsilon"] is False


@pytest.mark.parametrize("field,value", [("status", 1), ("success", False),
                                         ("primal_dual_gap", 1.),
                                         ("dual_objective", 120.),
                                         ("constraint_max_violation", .01)])
def test_solver_failure_or_inconsistent_certificate_is_retained(case, field, value):
    case["summary"]["solver_stages"][0][field] = value
    save_case(case)
    result = audit(case)
    assert not result["passed"]
    assert result["checks"]["solver_stage_0"] is False


def test_missing_registered_window_does_not_claim_complete_season(case):
    del case["protocol_value"]["window_end_exclusive"]
    save_case(case)
    assert not audit(case)["passed"]


def test_carbon_objective_is_per_window_and_does_not_multiply_repeat_factor(case):
    stage = case["summary"]["solver_stages"][0]
    stage.update(mode="carbon", primal_objective=1.75, dual_objective=1.75, objective_constant=0.)
    case["summary"]["solver_stages"][-1].update(mode="cost", primal_objective=113.6, dual_objective=113.6, objective_constant=18860.)
    sync_enum(case)
    save_case(case)
    assert audit(case)["passed"]


def test_fabricated_zero_gap_cannot_replace_independent_final_objective(case):
    stage = case["summary"]["solver_stages"][-1]
    stage["primal_objective"] += 10.
    stage["dual_objective"] += 10.
    save_case(case)
    result = audit(case)
    assert not result["passed"]
    assert result["checks"]["solver_stage_1"] is True
    assert result["checks"]["final_stage_objective"] is False


def test_fixed_cost_constant_is_not_hidden_in_variable_lp_objective(case):
    case["summary"]["solver_stages"][0]["objective_constant"] -= 1000.
    save_case(case)
    result = audit(case)
    assert not result["passed"]
    assert result["checks"]["solver_stage_0_constant"] is False


def test_lexicographic_primary_must_hold_for_the_final_dispatch(case):
    first = case["summary"]["solver_stages"][0]
    first.update(primal_objective=100., dual_objective=100.)
    save_case(case)
    result = audit(case)
    assert not result["passed"]
    assert result["checks"]["primary_lexicographic_bound"] is False
    assert result["checks"]["final_stage_objective"] is True


def test_scope_summary_cannot_expand_the_registered_candidates(case):
    case["summary"]["bound_scope"]["hp_unit_candidates"] = [0, 1, 2]
    save_case(case)
    result = audit(case)
    assert not result["passed"]
    assert result["checks"]["bound_scope_matches_protocol"] is False


def test_omitted_candidate_and_duplicate_candidate_are_rejected(case):
    case["protocol_value"]["design"]["hp_unit_candidates"] = [0, 1]
    case["protocol_value"]["bound_scope"]["hp_unit_candidates"] = [0, 1]
    case["summary"]["bound_scope"]["hp_unit_candidates"] = [0, 1]
    save_case(case)
    assert audit(case)["checks"]["enumeration_exact_scope"] is False
    case["enumeration"].append(deepcopy(case["enumeration"][0]))
    save_case(case)
    assert audit(case)["checks"]["enumeration_exact_scope"] is False


def test_fixed_scope_requires_and_preserves_all_three_investments(case):
    frozen = deepcopy(case["investments"])
    case["protocol_value"]["fixed_investment"] = frozen
    case["protocol_value"]["bound_scope"] = {"kind": "fixed_investment", "hp_unit_candidates": [1], "fixed_resistance_kw": 1.}
    sync_enum(case)
    save_case(case)
    assert audit(case)["passed"]
    case["investments"]["resistance_capacity_kw"] = 1.1
    save_case(case)
    result = audit(case)
    assert not result["passed"]
    assert result["checks"]["fixed_investment_preserved"] is False


@pytest.mark.parametrize("field", ["global_primary_lower_bound", "global_primary_gap"])
def test_global_bound_and_gap_are_recomputed(case, field):
    case["summary"][field] += 1.
    save_case(case)
    assert audit(case)["checks"][field] is False


def test_secondary_status2_does_not_count_as_primary_infeasibility(case):
    record = case["enumeration"][0]
    record["success"] = False
    record["solver_stages"][1] = {"success": False, "status": 2, "mode": "carbon"}
    save_case(case)
    result = audit(case)
    assert not result["passed"]
    assert result["checks"]["integer_enumeration_complete"] is False
    # Its successful primary still contributes the dual lower bound.
    assert result["bound_evidence"]["global_primary_lower_bound"] == pytest.approx(18973.6)


def test_primary_timeout_leaves_global_bound_unresolved(case):
    case["enumeration"][0]["solver_stages"][0] = {"success": False, "status": 1, "mode": "cost"}
    save_case(case)
    result = audit(case)
    assert not result["passed"]
    assert result["bound_evidence"]["global_primary_lower_bound"] is None


def test_actual_lp_9000_hp_is_cheaper_but_no_hp_baseline_has_only_its_own_bound(case):
    """Solve a small synthetic RC investment LP; these are not field observations.

    Twenty-four identical cold hours and F=1000 are a deliberate regression
    condition, not a lifetime/forecast. Both branches have the same comfort,
    terminal state, finite backup/grid and paid common cooling package.
    """
    import math
    from time import perf_counter
    import numpy as np
    from scipy.optimize import linprog

    pp = case["protocol_value"]
    pp["window_end_exclusive"] = "2024-11-17T00:00:00+08:00"
    pp["design"].update(hp_package_capex_cny=9000., repeat_window_factor=1000., hp_unit_candidates=[0])
    pp["bound_scope"] = {"kind": "baseline_no_HP", "hp_unit_candidates": [0], "fixed_resistance_kw": None}
    case["inputs"] = [{**case["inputs"][0], "time": f"2024-11-16T{hour:02d}:00:00+08:00",
                       "outdoor_temp": 10., "electricity_price": 1., "grid_emission_factor": .5}
                      for hour in range(24)]
    n, m = 24, 73
    ir, ih, it = np.arange(1, 25), np.arange(25, 49), np.arange(49, 73)
    a, b = math.exp(-.2/2), -math.expm1(-.2/2)/.2
    eq, rhs = np.zeros((n+1, m)), np.full(n+1, (1-a)*10.)
    for t in range(n):
        eq[t, it[t]], eq[t, ir[t]], eq[t, ih[t]] = 1., -b, -b
        if t:
            eq[t, it[t-1]] = -a
    rhs[0] += a*20.
    eq[-1, it[-1]], rhs[-1] = 1., 20.
    ub, limits = np.zeros((2*n, m)), np.r_[np.zeros(n), np.full(n, 4.)]
    for t in range(n):
        ub[t, ir[t]], ub[t, 0] = 1., -1.
        ub[n+t, ir[t]], ub[n+t, ih[t]] = 1., .5
    cost, carbon = np.zeros(m), np.zeros(m)
    cost[0], cost[ir], cost[ih] = 5100., 1000., 500.
    carbon[ir], carbon[ih] = .5, .25

    def branch(units):
        bounds = [(0., 6.)] + [(0., None)]*n + [(0., 8.*units)]*n + [(20., 27.)]*n
        constant = units*9000. + 1000. + 1000.*(units*20.+10.)
        stages = []
        selected = None
        au, bu = ub, limits
        for mode, objective in (("cost", cost), ("carbon", carbon)):
            started = perf_counter()
            fit = linprog(objective, A_ub=au, b_ub=bu, A_eq=eq, b_eq=rhs,
                          bounds=bounds, method="highs")
            assert fit.success
            dual = float(fit.ineqlin.marginals @ bu + fit.eqlin.marginals @ rhs)
            for bound, lower, upper in zip(bounds, fit.lower.marginals, fit.upper.marginals):
                dual += bound[0]*lower
                if bound[1] is not None:
                    dual += bound[1]*upper
            stages.append({"success": True, "status": 0, "mode": mode, "epsilon_co2_kg": None,
                           "seconds": perf_counter()-started,
                           "constraint_max_violation": max(0., float(np.max(au@fit.x-bu)), float(np.max(np.abs(eq@fit.x-rhs)))),
                           "primal_objective": float(fit.fun), "dual_objective": dual,
                           "primal_dual_gap": float(fit.fun-dual),
                           "objective_constant": constant if mode == "cost" else 0.})
            if mode == "cost":
                au = np.vstack([ub, cost])
                bu = np.r_[limits, fit.fun+1e-7+abs(fit.fun)*1e-9]
            selected = fit.x
        rows = [{"time": case["inputs"][t]["time"], "q_resistance_kw": float(selected[ir[t]]),
                 "q_hp_kw": float(selected[ih[t]]), "q_cooling_kw": 0.,
                 "electricity_kw": float(selected[ir[t]]+.5*selected[ih[t]]),
                 "temperature_end_c": float(selected[it[t]])} for t in range(n)]
        r = float(selected[0])
        energy = sum(row["electricity_kw"] for row in rows)
        capex, om = units*9000.+r*100.+1000., units*20.+r*5.+10.
        summary = {"capex_cny": capex, "window_energy_bill_cny": energy, "window_fixed_om_cny": om,
                   "window_co2_kg": energy*.5, "electricity_kwh": energy,
                   "comparison_cost_cny": capex+1000.*(energy+om), "solver_stages": stages,
                   "requested_epsilon_co2_kg": None, "mode": "cost"}
        return rows, {"hp_units": units, "resistance_capacity_kw": r, "cooling_capacity_kw": 2.}, summary

    rows0, inv0, sum0 = branch(0)
    rows1, inv1, sum1 = branch(1)
    assert sum1["comparison_cost_cny"] < sum0["comparison_cost_cny"]
    case.update(dispatch=rows0, investments=inv0, summary=sum0)
    sync_enum(case)
    save_case(case)
    base_audit = audit(case)
    assert base_audit["passed"], base_audit["errors"]
    assert base_audit["bound_evidence"]["bound_scope"]["hp_unit_candidates"] == [0]

    # Merely relabeling the same no-HP solve as a free [0,1] optimum is invalid.
    pp["design"]["hp_unit_candidates"] = [0, 1]
    pp["bound_scope"] = {"kind": "free_registered_candidates", "hp_unit_candidates": [0, 1], "fixed_resistance_kw": None}
    case["summary"]["bound_scope"] = deepcopy(pp["bound_scope"])
    save_case(case)
    assert audit(case)["checks"]["enumeration_exact_scope"] is False

    # Failed secondary for the cheaper n=1 primary must not hide that bound.
    one_record = {"success": False, **inv1, "solver_stages": deepcopy(sum1["solver_stages"])}
    one_record["solver_stages"][1] = {"success": False, "status": 2, "mode": "carbon"}
    case["enumeration"].append(one_record)
    save_case(case)
    failed = audit(case)
    expected = sum1["solver_stages"][0]["dual_objective"]+sum1["solver_stages"][0]["objective_constant"]
    assert not failed["passed"]
    assert failed["checks"]["integer_enumeration_complete"] is False
    assert failed["checks"]["global_primary_lower_bound"] is False
    assert failed["bound_evidence"]["global_primary_lower_bound"] == pytest.approx(expected)


def test_cli_reads_without_creating_audit_artifacts(case, capsys):
    before = {path: path.read_bytes() for path in case["input"].parent.rglob("*") if path.is_file()}
    status = AUDITOR.main(["--root", str(case["input"].parent)])
    result = json.loads(capsys.readouterr().out)
    assert status == 0 and result["passed"]
    after = {path: path.read_bytes() for path in case["input"].parent.rglob("*") if path.is_file()}
    assert before == after
