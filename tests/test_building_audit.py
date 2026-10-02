"""Analytic fixtures and deliberate export tampering; no large solver runs."""
from copy import deepcopy
import json
from math import exp, expm1

import numpy as np
import pandas as pd
import pytest

from heatplan.building_audit import audit_building_trajectory, building_audit_identity


def case():
    config = {
        "planning": {"time_step_hours": 1., "representative_day_weight": 1.},
        "nodes": [{"name": "n", "demand_columns": {"low": "reference"}}],
        "grades": [{"name": "low", "temperature_c": 45.}],
        "building_thermal": {"enabled": True, "buildings": [{
            "name": "b", "node": "n", "heat_grade": "low", "area_m2": 100.,
            "heat_loss_kw_per_c": 1., "thermal_capacity_kwh_per_c": 10.,
            "initial_temperature_c": 20., "final_temperature_c": 20.,
            "min_temperature_c": 18., "max_temperature_c": 24.,
        }]},
    }
    frame = pd.DataFrame({"time": pd.date_range("2023-01-01", periods=4, freq="h", tz="UTC"),
                          "outdoor_temp": 10., "reference": 999.})
    return config, frame


def trajectory_fixture(config, frame, *, heat=10., cool=0.):
    """Closed-form constant-forcing oracle over each group, independent of C2.

    Explicitly intended for these small constant-weather fixtures. Temperatures
    use elapsed-time closed form, rather than the auditor's one-step recurrence.
    """
    dt = config["planning"]["time_step_hours"]
    groups = frame.get("boundary_group", frame.get("scenario", pd.Series(["base"] * len(frame))))
    rows = []
    for spec in config["building_thermal"]["buildings"]:
        h, c = spec["heat_loss_kw_per_c"], spec["thermal_capacity_kwh_per_c"]
        a = exp(-h * dt / c)
        b = -expm1(-h * dt / c) / h
        mode = spec.get("boundary_mode", config["building_thermal"].get("boundary_mode", "fixed"))
        for group in groups.unique():
            ix = np.flatnonzero(groups.eq(group))
            boundary = dict(spec, **spec.get("boundary_conditions", {}).get(group, {}))
            reference = boundary["initial_temperature_c"]
            outside = frame.outdoor_temp.iloc[ix[0]]
            internal = spec.get("internal_gain_kw", 0.)
            solar = frame[spec["solar_gain_column"]].iloc[ix[0]] if "solar_gain_column" in spec else 0.
            forcing = outside + (heat - cool + internal + solar) / h
            start = reference if mode == "fixed" else forcing
            final_lo = boundary.get("final_temperature_c", boundary.get("final_min_temperature_c", reference)) if mode == "fixed" else None
            final_hi = boundary.get("final_temperature_c", boundary.get("final_max_temperature_c")) if mode == "fixed" else None
            for pos, t in enumerate(ix):
                lo = frame[spec["min_temperature_column"]].iloc[t] if "min_temperature_column" in spec else spec["min_temperature_c"]
                hi = frame[spec["max_temperature_column"]].iloc[t] if "max_temperature_column" in spec else spec["max_temperature_c"]
                begin = forcing + (start - forcing) * exp(-h * dt * pos / c)
                end = forcing + (start - forcing) * exp(-h * dt * (pos + 1) / c)
                prev_lo = prev_hi = reference
                if pos > 0 or mode == "cyclic":
                    prev_t = ix[pos - 1]
                    prev_lo = frame[spec["min_temperature_column"]].iloc[prev_t] if "min_temperature_column" in spec else spec["min_temperature_c"]
                    prev_hi = frame[spec["max_temperature_column"]].iloc[prev_t] if "max_temperature_column" in spec else spec["max_temperature_c"]
                heat_cap = max(0., (hi - prev_lo) / b + h * (prev_lo - outside) - internal - solar)
                cool_cap = max(0., (prev_hi - lo) / b + h * (outside - prev_hi) + internal + solar)
                heat_cap = min(heat_cap, spec.get("max_heat_kw", float("inf"))) if spec.get("heating_enabled", True) else 0.
                cool_cap = min(cool_cap, spec.get("max_cooling_kw", float("inf"))) if spec.get("cooling_enabled", True) else 0.
                service_column = spec.get("heating_service_column", config["building_thermal"].get("heating_service_column"))
                rows.append({
                    "building": spec["name"], "node": spec["node"], "heat_grade": spec["heat_grade"],
                    "time_index": int(t), "time": frame.time.iloc[t], "scenario": frame.scenario.iloc[t] if "scenario" in frame else "base",
                    "boundary_group": group, "boundary_mode": mode,
                    "period_weight": frame.period_weight.iloc[t] if "period_weight" in frame else config["planning"]["representative_day_weight"],
                    "scenario_probability": frame.scenario_probability.iloc[t] if "scenario_probability" in frame else 1.,
                    "dt_hours": dt, "area_m2": spec["area_m2"], "heat_loss_kw_per_c": h,
                    "thermal_capacity_kwh_per_c": c, "initial_temperature_reference_c": reference,
                    "final_min_temperature_c": final_lo, "final_max_temperature_c": final_hi,
                    "temperature_start_c": begin, "temperature_end_c": end,
                    "min_temperature_c": lo, "max_temperature_c": hi, "outdoor_temperature_c": outside,
                    "internal_gain_kw": internal, "solar_gain_kw": solar,
                    "heating_service": frame[service_column].iloc[t] if service_column else 1.,
                    "heat_kw": heat, "cooling_kw": cool, "heat_upper_kw": heat_cap, "cooling_upper_kw": cool_cap,
                    "heat_kwh": heat * dt, "cooling_kwh": cool * dt,
                    "under_temperature_c": max(0., lo - end), "over_temperature_c": max(0., end - hi),
                    "rc_residual_c": 0.,
                })
    return pd.DataFrame(rows)


def dispatch_fixture(tr):
    rows = []
    for row in tr.itertuples(index=False):
        for category, value, grade, unit in (
            ("building_temperature", row.temperature_end_c, row.heat_grade, "degC"),
            ("building_heat", row.heat_kw, row.heat_grade, "kW"),
            ("building_cooling", row.cooling_kw, "cooling", "kW"),
        ):
            rows.append({"category": category, "asset": row.building, "node": row.node, "grade": grade,
                         "time_index": row.time_index, "time": row.time, "scenario": row.scenario,
                         "boundary_group": row.boundary_group, "value": value, "unit": unit})
    return pd.DataFrame(rows)


def assert_failed(report, code=None):
    assert report["passed"] is False, report
    assert any(not check["passed"] for check in report["checks"].values())
    if code:
        assert report["checks"][code]["passed"] is False, report["checks"]
    json.dumps(report, allow_nan=False)


def test_equilibrium_actual_service_uses_neither_reference_nor_area_multiplier():
    c, f = case()
    tr = trajectory_fixture(c, f)
    summary = {"demand_kwh_per_year": 40., "cooling_demand_kwh_per_year": 0.,
               "building_reference_heat_kwh_per_year": 3996., "building_count": 1}
    report = audit_building_trajectory(c, f, tr, dispatch_fixture(tr), summary,
                                      expected_input_identity=building_audit_identity(c, f), require_continuous_fixed=True)
    assert report["passed"], report
    assert report["building_physics_passed"]
    assert report["metrics"]["heat_kwh"] == 40.
    assert report["metrics"]["max_rc_residual_c"] < 1e-12
    assert report["node_service_energy"][0]["kwh"] == 40.
    assert report["annual_certification"] is False
    json.dumps(report, allow_nan=False)


def test_closed_form_free_decay_and_terminal_inventory():
    c, f = case()
    s = c["building_thermal"]["buildings"][0]
    s.update(heat_loss_kw_per_c=2., min_temperature_c=0., final_min_temperature_c=0.)
    del s["final_temperature_c"]
    f.outdoor_temp = 0.
    tr = trajectory_fixture(c, f, heat=0.)
    report = audit_building_trajectory(c, f, tr)
    assert report["passed"], report
    assert report["building_groups"][0]["final_end_c"] == pytest.approx(20 * exp(-.8))
    assert report["building_groups"][0]["inventory_change_kwh"] == pytest.approx(200 * (exp(-.8) - 1))


@pytest.mark.parametrize("field", ["heat_loss_kw_per_c", "thermal_capacity_kwh_per_c", "area_m2",
    "initial_temperature_reference_c", "dt_hours", "outdoor_temperature_c", "internal_gain_kw",
    "solar_gain_kw", "min_temperature_c", "max_temperature_c", "heating_service", "period_weight", "scenario_probability"])
def test_forged_metadata_cannot_redefine_physics(field):
    c, f = case()
    tr = trajectory_fixture(c, f)
    tr.loc[0, field] += 1
    tr.rc_residual_c = 0.
    assert_failed(audit_building_trajectory(c, f, tr), "input_metadata")


@pytest.mark.parametrize("field", ["heat_kw", "cooling_kw", "temperature_end_c", "time_index", "heat_upper_kw",
    "cooling_upper_kw", "rc_residual_c", "heat_kwh", "cooling_kwh", "temperature_start_c"])
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_exports_fail_closed(field, bad):
    c, f = case()
    tr = trajectory_fixture(c, f)
    tr[field] = tr[field].astype(float)
    tr.loc[0, field] = bad
    assert_failed(audit_building_trajectory(c, f, tr), "invalid_data")


@pytest.mark.parametrize("tamper", ["delete", "duplicate", "replace_with_duplicate", "unknown_building", "drop_building"])
def test_exact_cartesian_row_coverage(tamper):
    c, f = case()
    c["building_thermal"]["buildings"].append(dict(c["building_thermal"]["buildings"][0], name="b2"))
    tr = trajectory_fixture(c, f)
    if tamper == "delete":
        tr = tr.iloc[:-1]
    elif tamper == "duplicate":
        tr = pd.concat([tr, tr.iloc[[0]]], ignore_index=True)
    elif tamper == "replace_with_duplicate":
        tr.iloc[-1] = tr.iloc[0]
    elif tamper == "drop_building":
        tr = tr[tr.building == "b"]
    else:
        tr.loc[0, "building"] = "ghost"
    assert_failed(audit_building_trajectory(c, f, tr), "trajectory_complete")


@pytest.mark.parametrize("index", [.5, -1., 100., True])
def test_index_never_rounded_rebased_or_boolean(index):
    c, f = case()
    tr = trajectory_fixture(c, f).astype({"time_index": object})
    tr.loc[0, "time_index"] = index
    assert_failed(audit_building_trajectory(c, f, tr), "invalid_data")


@pytest.mark.parametrize("field,value", [("time", "2023-02-01T00:00:00+00:00"), ("scenario", "forged"),
                                        ("boundary_group", "forged"), ("node", "other"), ("heat_grade", "high"),
                                        ("boundary_mode", "cyclic")])
def test_index_cannot_launder_clock_or_assignment(field, value):
    c, f = case()
    tr = trajectory_fixture(c, f)
    tr[field] = tr[field].astype(object)
    tr.loc[0, field] = value
    assert_failed(audit_building_trajectory(c, f, tr), "time_and_assignment")


def test_shuffled_rows_and_nondefault_frame_index_are_legal():
    c, f = case()
    tr = trajectory_fixture(c, f).sample(frac=1, random_state=4)
    f.index = [10, 11, 100, 200]
    assert audit_building_trajectory(c, f, tr)["passed"]


def test_removed_actual_heat_cannot_hide_behind_comfort_and_zero_residual():
    c, f = case()
    tr = trajectory_fixture(c, f)
    tr.heat_kw = tr.heat_kwh = tr.rc_residual_c = 0.
    assert_failed(audit_building_trajectory(c, f, tr), "exact_rc")


@pytest.mark.parametrize("mode", ["exclusive", "relaxed"])
def test_any_positive_heat_and_cooling_over_tolerance_fails_physics(mode):
    c, f = case()
    c["building_thermal"]["simultaneous_mode"] = mode
    tr = trajectory_fixture(c, f)
    tr.heat_kw += 2e-6
    tr.cooling_kw += 2e-6
    tr.heat_kwh = tr.heat_kw
    tr.cooling_kwh = tr.cooling_kw
    report = audit_building_trajectory(c, f, tr)
    assert_failed(report, "no_simultaneous_heat_cooling")
    assert not report["building_physics_passed"]
    assert report["checks"]["exact_rc"]["passed"]


def test_solver_roundoff_below_power_tolerance_is_accepted():
    c, f = case()
    tr = trajectory_fixture(c, f)
    tr.heat_kw += 1e-8
    tr.cooling_kw += 1e-8
    tr.heat_kwh = tr.heat_kw
    tr.cooling_kwh = tr.cooling_kw
    assert audit_building_trajectory(c, f, tr)["passed"]


@pytest.mark.parametrize("key,value", [("heating_enabled", False), ("max_heat_kw", 9.)])
def test_disabled_or_capped_supply_cannot_maintain_comfort(key, value):
    c, f = case()
    c["building_thermal"]["buildings"][0][key] = value
    tr = trajectory_fixture(c, f)
    assert_failed(audit_building_trajectory(c, f, tr), "delivered_power_bounds")


def test_negative_heat_cannot_be_used_as_cooling():
    c, f = case()
    tr = trajectory_fixture(c, f, heat=-1)
    assert_failed(audit_building_trajectory(c, f, tr), "delivered_power_bounds")


def test_terminal_and_reported_start_are_independently_checked():
    c, f = case()
    tr = trajectory_fixture(c, f, heat=0.)
    # Correct cooling physics still fails the frozen terminal requirement.
    assert_failed(audit_building_trajectory(c, f, tr), "terminal_state")
    tr = trajectory_fixture(c, f)
    tr.loc[1, "temperature_start_c"] = 21.
    assert_failed(audit_building_trajectory(c, f, tr), "state_continuity")


def test_independent_representative_days_may_gap_or_reuse_clock():
    c, f = case()
    s = c["building_thermal"]["buildings"][0]
    s.pop("final_temperature_c")
    s["boundary_conditions"] = {"second": {"initial_temperature_c": 21., "final_min_temperature_c": 18.}}
    f["boundary_group"] = ["first", "first", "second", "second"]
    f["time"] = [f.time.iloc[0], f.time.iloc[1], f.time.iloc[0], f.time.iloc[1]]
    tr = trajectory_fixture(c, f)
    report = audit_building_trajectory(c, f, tr)
    assert report["passed"], report
    assert report["building_groups"][1]["initial_start_c"] == 21.
    assert_failed(audit_building_trajectory(c, f, tr, require_continuous_fixed=True), "continuous_fixed")
    tr.loc[2, "temperature_start_c"] = tr.loc[1, "temperature_end_c"]
    assert_failed(audit_building_trajectory(c, f, tr), "state_continuity")


def test_cyclic_uses_own_final_state_not_reference_initial():
    c, f = case()
    s = c["building_thermal"]["buildings"][0]
    s.pop("final_temperature_c")
    s.update(boundary_mode="cyclic", initial_temperature_c=8.)
    tr = trajectory_fixture(c, f)
    assert audit_building_trajectory(c, f, tr)["passed"]
    assert_failed(audit_building_trajectory(c, f, tr, require_continuous_fixed=True), "continuous_fixed")
    tr.loc[0, "temperature_start_c"] = 8.
    assert_failed(audit_building_trajectory(c, f, tr), "state_continuity")


@pytest.mark.parametrize("bad", ["gap", "duplicate", "naive", "interleaved", "cross_scenario", "duplicate_building", "bad_h", "nan_gain"])
def test_invalid_trusted_inputs_fail_closed(bad):
    c, f = case()
    tr = trajectory_fixture(c, f)
    if bad == "gap":
        f.loc[2, "time"] += pd.Timedelta(hours=10)
    elif bad == "duplicate":
        f.loc[1, "time"] = f.loc[0, "time"]
    elif bad == "naive":
        f.time = f.time.dt.tz_localize(None)
    elif bad == "interleaved":
        f["boundary_group"] = ["a", "b", "a", "b"]
    elif bad == "cross_scenario":
        f["boundary_group"] = "same"
        f["scenario"] = ["x", "x", "y", "y"]
    elif bad == "duplicate_building":
        c["building_thermal"]["buildings"].append(deepcopy(c["building_thermal"]["buildings"][0]))
    elif bad == "bad_h":
        c["building_thermal"]["buildings"][0]["heat_loss_kw_per_c"] = 0
    else:
        c["building_thermal"]["buildings"][0]["internal_gain_kw"] = float("nan")
    assert_failed(audit_building_trajectory(c, f, tr), "invalid_data")


@pytest.mark.parametrize("tamper", ["capacity", "initial", "heat_loss", "remove_frame_end"])
def test_frozen_identity_detects_coordinated_input_result_changes(tamper):
    c, f = case()
    anchor = building_audit_identity(c, f)
    s = c["building_thermal"]["buildings"][0]
    if tamper == "capacity":
        s["thermal_capacity_kwh_per_c"] *= 2
    elif tamper == "initial":
        s.update(initial_temperature_c=21., final_min_temperature_c=18.)
        s.pop("final_temperature_c")
    elif tamper == "heat_loss":
        s["heat_loss_kw_per_c"] *= 2
        f.outdoor_temp = 15.
    else:
        f = f.iloc[:-1].copy()
    tr = trajectory_fixture(c, f)
    assert audit_building_trajectory(c, f, tr)["passed"]
    assert_failed(audit_building_trajectory(c, f, tr, expected_input_identity=anchor), "input_identity")


def test_weights_apply_to_service_energy_only_and_do_not_annualize_claim():
    c, f = case()
    f["period_weight"] = [2., 3., 4., 5.]
    f["scenario_probability"] = .25
    tr = trajectory_fixture(c, f)
    summary = {"demand_kwh_per_year": 35., "cooling_demand_kwh_per_year": 0.}
    report = audit_building_trajectory(c, f, tr, summary=summary)
    assert report["passed"], report
    assert report["metrics"]["heat_kwh"] == 40.
    assert report["metrics"]["expected_weighted_heat_kwh"] == 35.
    assert report["annual_certification"] is False
    assert_failed(audit_building_trajectory(c, f, tr, require_continuous_fixed=True), "continuous_fixed")
    summary["demand_kwh_per_year"] = 140.
    assert_failed(audit_building_trajectory(c, f, tr, summary=summary), "summary_actual_service_energy")


def test_cooling_gains_and_hourly_comfort_columns():
    c, f = case()
    f.outdoor_temp = 30.
    f["lo"], f["hi"], f["solar"], f["heating_service"] = 19., 21., 1., 0.
    c["building_thermal"]["buildings"][0].update(
        internal_gain_kw=2., solar_gain_column="solar", min_temperature_column="lo",
        max_temperature_column="hi", heating_service_column="heating_service")
    tr = trajectory_fixture(c, f, heat=0., cool=13.)
    report = audit_building_trajectory(c, f, tr)
    assert report["passed"], report
    assert report["metrics"]["cooling_kwh"] == 52.


@pytest.mark.parametrize("bad", ["delete", "duplicate", "value", "unit", "asset", "node", "grade", "time", "nan"])
def test_dispatch_tampering(bad):
    c, f = case()
    tr = trajectory_fixture(c, f)
    d = dispatch_fixture(tr)
    if bad == "delete":
        d = d.iloc[:-1]
    elif bad == "duplicate":
        d = pd.concat([d, d.iloc[[0]]], ignore_index=True)
    elif bad in {"value", "nan"}:
        d.loc[1, "value"] = 999. if bad == "value" else float("nan")
    elif bad == "time":
        d.loc[1, "time"] += pd.Timedelta(hours=1)
    else:
        d.loc[1, bad] = "forged"
    assert_failed(audit_building_trajectory(c, f, tr, dispatch=d))


def test_multibuilding_node_aggregation_and_optional_node_aliases_no_double_count():
    c, f = case()
    c["building_thermal"]["buildings"].append(dict(c["building_thermal"]["buildings"][0], name="b2"))
    tr = trajectory_fixture(c, f)
    d = dispatch_fixture(tr)
    node = d[(d.category == "building_heat") & (d.asset == "b")].copy()
    node["category"], node["asset"], node["value"] = "building_heat_by_node", "n_low", 20.
    d = pd.concat([d, node], ignore_index=True)
    summary = {"demand_kwh_per_year": 80., "cooling_demand_kwh_per_year": 0.}
    report = audit_building_trajectory(c, f, tr, d, summary)
    assert report["passed"], report
    assert report["node_service_energy"][0]["kwh"] == 80.
    d.loc[d.category == "building_heat_by_node", "value"] = 40.
    assert_failed(audit_building_trajectory(c, f, tr, d, summary), "dispatch_explicit_nodes")


def test_summary_reference_cannot_replace_actual_delivered_energy():
    c, f = case()
    tr = trajectory_fixture(c, f)
    summary = {"demand_kwh_per_year": 3996., "cooling_demand_kwh_per_year": 0.}
    assert_failed(audit_building_trajectory(c, f, tr, summary=summary), "summary_actual_service_energy")
    summary = {"building_reference_heat_kwh_per_year": 40.}
    assert_failed(audit_building_trajectory(c, f, tr, summary=summary), "summary_actual_service_available")


def test_mixed_dhw_requires_explicit_rc_actual_service_summary_fields():
    c, f = case()
    c["nodes"][0]["demand_columns"]["high"] = "dhw"
    c["grades"].append({"name": "high"})
    f["dhw"] = 2.
    tr = trajectory_fixture(c, f)
    summary = {"demand_kwh_per_year": 48., "cooling_demand_kwh_per_year": 0.}
    assert_failed(audit_building_trajectory(c, f, tr, summary=summary), "summary_actual_service_available")
    summary["building_heat_delivered_kwh_per_year"] = 40.
    assert audit_building_trajectory(c, f, tr, summary=summary)["passed"]


def test_csv_roundtrip_and_missing_nullable_bound_semantics(tmp_path):
    c, f = case()
    c["building_thermal"]["buildings"][0].pop("final_temperature_c")
    tr = trajectory_fixture(c, f)
    path = tmp_path / "building_trajectory.csv"
    tr.to_csv(path, index=False)
    reread = pd.read_csv(path)
    assert audit_building_trajectory(c, f, reread)["passed"]
    reread.loc[0, "final_min_temperature_c"] = float("nan")
    assert_failed(audit_building_trajectory(c, f, reread), "boundary_metadata")


def test_small_real_model_export_is_audited_without_calling_c2_for_recalculation():
    # Four hours, one fixed existing heat pump. This is compatibility evidence,
    # separate from the closed-form/tamper tests and never an annual certificate.
    from heatplan.model import HeatPlanningModel
    c, f = case()
    c["planning"].update(discount_rate=.05, asset_lifetime_years=20, max_unserved_rate=0.)
    c["sources"] = [{"name": "hp", "node": "n", "type": "heat_pump", "resource_kind": "renewable",
                      "allowed_grades": ["low"], "existing_capacity_kw": 100., "max_new_capacity_kw": 0.,
                      "cop_min": 2., "cop_max": 2., "source_temp_c": 10., "carnot_efficiency": .4}]
    c["pipes"], c["storages"] = [], []
    f["timestamp"], f["electricity_price"], f["grid_emission_factor"] = f.time, .5, 1.
    model = HeatPlanningModel(c, f)
    artifacts = model.solve(time_limit=10., mip_gap=0.)
    report = audit_building_trajectory(c, model.ts, artifacts.building_trajectory,
                                      artifacts.dispatch, artifacts.summary, require_continuous_fixed=True)
    assert report["passed"], report
    assert report["expected_rows"] == 4
    assert not report["annual_certification"]


def test_large_capacity_does_not_lose_equilibrium_power_bound_precision():
    c, f = case()
    c["building_thermal"]["buildings"][0].update(
        thermal_capacity_kwh_per_c=1e10, min_temperature_c=20., max_temperature_c=20.)
    tr = trajectory_fixture(c, f)
    assert tr.heat_upper_kw.eq(10.).all()
    assert audit_building_trajectory(c, f, tr)["passed"]


def test_half_hour_dt_and_equivalent_timezone_are_valid():
    c, f = case()
    c["planning"]["time_step_hours"] = .5
    f.time = pd.date_range("2023-01-01", periods=4, freq="30min", tz="UTC")
    tr = trajectory_fixture(c, f)
    tr.time = tr.time.dt.tz_convert("Asia/Shanghai")
    report = audit_building_trajectory(c, f, tr)
    assert report["passed"], report
    assert report["metrics"]["heat_kwh"] == 20.


@pytest.mark.parametrize("field", ["heat_upper_kw", "cooling_upper_kw", "heat_kwh", "cooling_kwh", "under_temperature_c",
                                   "over_temperature_c", "rc_residual_c", "final_min_temperature_c", "final_max_temperature_c"])
def test_derived_export_fields_are_not_trusted(field):
    c, f = case()
    tr = trajectory_fixture(c, f)
    tr.loc[0, field] += 1.
    assert_failed(audit_building_trajectory(c, f, tr))


def test_missing_or_duplicate_columns_cannot_pass():
    c, f = case()
    tr = trajectory_fixture(c, f)
    assert_failed(audit_building_trajectory(c, f, tr.drop(columns="heat_kw")), "invalid_data")
    assert_failed(audit_building_trajectory(c, f, pd.concat([tr, tr[["heat_kw"]]], axis=1)), "invalid_data")


def test_heat_to_cooling_switch_on_one_continuous_trajectory():
    c, f = case()
    # Maintain 20 C: winter requires +10 kW and summer requires -10 kW.
    winter = trajectory_fixture(c, f)
    summer_frame = f.copy()
    summer_frame.outdoor_temp = 30.
    summer = trajectory_fixture(c, summer_frame, heat=0., cool=10.)
    tr = pd.concat([winter.iloc[:2], summer.iloc[2:]], ignore_index=True)
    f.loc[2:, "outdoor_temp"] = 30.
    report = audit_building_trajectory(c, f, tr)
    assert report["passed"], report
    assert report["metrics"]["heat_kwh"] == report["metrics"]["cooling_kwh"] == 20.


def test_comfort_violation_cannot_be_hidden_by_zero_heating_service_indicator():
    c, f = case()
    c["building_thermal"]["heating_service_column"] = "service"
    f["service"] = 0.
    tr = trajectory_fixture(c, f, heat=0.)
    assert_failed(audit_building_trajectory(c, f, tr), "comfort_bounds")


@pytest.mark.parametrize("field", ["building_max_rc_residual_c", "building_under_degree_hours_end_sample",
                                   "building_over_degree_hours_end_sample"])
@pytest.mark.parametrize("bad", [999., float("nan"), float("inf")])
def test_summary_building_diagnostics_are_recomputed_and_finite(field, bad):
    c, f = case()
    tr = trajectory_fixture(c, f)
    summary = {"demand_kwh_per_year": 40., "cooling_demand_kwh_per_year": 0., field: bad}
    assert_failed(audit_building_trajectory(c, f, tr, summary=summary))


def test_overflowing_statistics_do_not_leave_building_physics_passed():
    c, f = case()
    f["period_weight"] = 1e308
    tr = trajectory_fixture(c, f)
    with np.errstate(over="ignore"):
        report = audit_building_trajectory(c, f, tr)
    assert_failed(report, "finite_recalculation")
    assert report["building_physics_passed"] is False
