from __future__ import annotations

from copy import deepcopy

import numpy as np
import pandas as pd
import pytest

from heatplan.model import HeatPlanningModel
from heatplan.mpc import rolling_horizon_dispatch


def _inputs(rows=7, dt=1.0):
    config = {
        "planning": {
            "discount_rate": 0.05, "asset_lifetime_years": 20,
            "time_step_hours": dt, "representative_day_weight": 1.0,
            "max_unserved_rate": 0.0, "max_scenario_unserved_rate": 0.0,
        },
        "grades": [{"name": "low", "temperature_c": 45.0}],
        "nodes": [{"name": "n", "demand_columns": {"low": "demand"}}],
        "sources": [{
            "name": "source", "node": "n", "type": "boiler",
            "allowed_grades": ["low"], "supply_temp_c": 60.0,
            "existing_capacity_kw": 2.0, "max_new_capacity_kw": 0.0,
            "variable_cost_yuan_per_kwh_heat": 1.0,
            "emission_kg_per_kwh_heat": 0.0,
        }],
        "pipes": [], "storages": [],
    }
    frame = pd.DataFrame({
        "time": range(rows), "demand": [1.0] * rows,
        "electricity_price": [1.0] * rows,
        "grid_emission_factor": [1.0] * rows,
    })
    return config, frame


@pytest.mark.parametrize("rows,dt,horizon,step", [
    (1, 1.0, 1.0, 1.0), (7, 1.0, 2.0, 1.0),
    (7, 1.0, 3.0, 2.0), (7, 0.5, 1.0, 0.5),
    (7, 2.0, 4.0, 2.0), (8, 0.25, 1.0, 0.5),
])
def test_every_time_step_is_implemented_exactly_once_including_short_tail(rows, dt, horizon, step):
    config, frame = _inputs(rows, dt)
    output = rolling_horizon_dispatch(
        config, frame, horizon_hours=horizon, control_step_hours=step,
        fixed_investment_config=config, time_limit_per_horizon=5.0,
    )
    heat = output[output["category"] == "source_heat"]
    assert heat["global_time_index"].tolist() == list(range(rows))
    assert heat["time"].tolist() == list(range(rows))
    assert not output.duplicated(["category", "asset", "grade", "global_time_index"]).any()
    assert heat["value"].tolist() == pytest.approx([1.0] * rows)
    expected_starts = [(t // round(step / dt)) * round(step / dt) for t in range(rows)]
    assert heat["mpc_window_start"].tolist() == expected_starts


@pytest.mark.parametrize("dt,horizon,step,match", [
    (1.0, 2.0, 3.0, "不得大于"),
    (1.0, 1.5, 1.0, "正整数倍"),
    (2.0, 4.0, 1.0, "正整数倍"),
    (1.0, 2.0, 0.0, "正的有限小时数"),
    (1.0, np.inf, 1.0, "正的有限小时数"),
    (0.0, 2.0, 1.0, "time_step_hours"),
    (np.nan, 2.0, 1.0, "time_step_hours"),
])
def test_invalid_window_durations_fail_before_any_solve(monkeypatch, dt, horizon, step, match):
    def unexpected_solve(*args, **kwargs):
        pytest.fail("invalid MPC parameters reached the solver")
    monkeypatch.setattr(HeatPlanningModel, "solve", unexpected_solve)
    config, frame = _inputs(dt=dt)
    with pytest.raises(ValueError, match=match):
        rolling_horizon_dispatch(config, frame, horizon_hours=horizon, control_step_hours=step)


@pytest.mark.parametrize("module", ["unit_commitment", "thermal_inertia", "biomass", "battery", "battery_power", "demand_response"])
def test_unimplemented_inter_window_states_are_rejected_explicitly(module):
    config, frame = _inputs()
    if module == "unit_commitment":
        config["sources"][0][module] = True
    elif module == "thermal_inertia":
        config["nodes"][0][module] = {"low": {"thermal_capacity_kwh_per_c": 1.0}}
    elif module == "biomass":
        config["biomass_supply_chains"] = [{"name": "fuel"}]
    elif module == "battery":
        config["microgrid_islanding"] = {"existing_battery_energy_kwh": 1.0}
    elif module == "battery_power":
        config["microgrid_islanding"] = {"max_new_battery_power_kw": 1.0}
    else:
        config[module] = {"max_shift_fraction": 0.1}
    with pytest.raises(ValueError, match="尚未支持.*跨窗口状态传递"):
        rolling_horizon_dispatch(config, frame, fixed_investment_config=config)


@pytest.mark.parametrize("kind", ["scenario", "boundary_group", "period_weight", "default_weight", "empty"])
def test_representative_or_disconnected_trajectories_are_not_treated_as_continuous(kind):
    config, frame = _inputs()
    if kind in ("scenario", "boundary_group"):
        frame[kind] = ["a"] * 3 + ["b"] * 4
        match = "连续单场景"
    elif kind == "period_weight":
        frame[kind] = 2.0
        match = "必须为 1"
    elif kind == "default_weight":
        config["planning"]["representative_day_weight"] = 2.0
        match = "必须为 1"
    else:
        frame = frame.iloc[:0]
        match = "非空"
    with pytest.raises(ValueError, match=match):
        rolling_horizon_dispatch(config, frame, fixed_investment_config=config)


@pytest.mark.parametrize("control_steps", [1, 2])
def test_storage_state_is_conserved_across_windows_and_final_target_is_preserved(control_steps):
    config, frame = _inputs(rows=7, dt=0.5)
    config["storages"] = [{
        "name": "store", "node": "n", "grade": "low",
        "existing_capacity_kwh": 2.0, "max_new_capacity_kwh": 0.0,
        "power_to_energy_ratio_per_hour": 1.0,
        "charge_efficiency": 0.9, "discharge_efficiency": 0.8,
        "self_loss_per_hour": 0.05, "initial_soc_fraction": 0.8,
        "final_soc_min_fraction": 0.25,
    }]
    original = deepcopy(config)
    output = rolling_horizon_dispatch(
        config, frame, horizon_hours=1.5, control_step_hours=control_steps * 0.5,
        fixed_investment_config=config, time_limit_per_horizon=5.0,
    )
    storage = output[output["asset"] == "store"].pivot(
        index="global_time_index", columns="category", values="value"
    )
    previous = 1.6
    for _, row in storage.iterrows():
        expected = 0.95 ** 0.5 * previous + 0.9 * row["storage_charge"] * 0.5 - row["storage_discharge"] * 0.5 / 0.8
        assert row["storage_soc"] == pytest.approx(expected, abs=1e-7)
        previous = row["storage_soc"]
    assert previous >= 0.5 - 1e-7
    assert config == original


def test_planning_freeze_preserves_reliability_and_capacity_reserve(monkeypatch):
    config, frame = _inputs(rows=3)
    config["planning"]["firm_capacity_reserve_margin"] = 0.2
    config["sources"][0].update({
        "existing_capacity_kw": 0.0, "max_new_capacity_kw": 2.0,
        "capex_yuan_per_kw": 10.0,
    })
    observed = []
    original_solve = HeatPlanningModel.solve
    def record_solve(self, *args, **kwargs):
        observed.append(deepcopy(self.config))
        return original_solve(self, *args, **kwargs)
    monkeypatch.setattr(HeatPlanningModel, "solve", record_solve)
    output = rolling_horizon_dispatch(config, frame, horizon_hours=2, time_limit_per_horizon=5.0)
    assert len(observed) == 4
    for frozen in observed[1:]:
        assert frozen["planning"]["max_unserved_rate"] == 0.0
        assert frozen["planning"]["max_scenario_unserved_rate"] == 0.0
        assert frozen["planning"]["firm_capacity_reserve_margin"] == 0.2
        assert frozen["sources"][0]["max_new_capacity_kw"] == 0.0
        assert frozen["sources"][0]["existing_capacity_kw"] >= 1.2 - 1e-8
    assert output.loc[output["category"] == "unserved", "value"].sum() == pytest.approx(0.0)


def test_supplied_fixed_plan_cannot_reinvest_inside_each_window():
    config, frame = _inputs(rows=2)
    config["sources"][0]["max_new_capacity_kw"] = 1.0
    with pytest.raises(ValueError, match="尚有可变投资"):
        rolling_horizon_dispatch(config, frame, fixed_investment_config=config)


@pytest.mark.parametrize("offset_hours", [[0, 1, 3], [2, 1, 0], [0, 1, 1]])
def test_timestamp_gaps_reverse_order_and_duplicates_are_rejected(offset_hours):
    config, frame = _inputs(rows=3)
    frame["timestamp"] = pd.Timestamp("2026-01-01") + pd.to_timedelta(offset_hours, unit="h")
    with pytest.raises(ValueError, match="严格升序、无缺时或重时"):
        rolling_horizon_dispatch(config, frame, fixed_investment_config=config)


def test_invalid_timestamp_is_rejected():
    config, frame = _inputs(rows=3)
    frame["timestamp"] = ["2026-01-01 00:00", "invalid", "2026-01-01 02:00"]
    with pytest.raises(ValueError, match="有效时间戳"):
        rolling_horizon_dispatch(config, frame, fixed_investment_config=config)


def test_half_hour_timestamp_trajectory_preserves_order_and_short_tail():
    config, frame = _inputs(rows=5, dt=0.5)
    frame["timestamp"] = pd.date_range("2026-01-01", periods=5, freq="30min")
    original = frame.copy(deep=True)
    output = rolling_horizon_dispatch(
        config, frame, horizon_hours=1.0, control_step_hours=0.5,
        fixed_investment_config=config, time_limit_per_horizon=5.0,
    )
    heat = output[output["category"] == "source_heat"]
    assert heat["global_time_index"].tolist() == list(range(5))
    assert heat["value"].tolist() == pytest.approx([1.0] * 5)
    pd.testing.assert_frame_equal(frame, original)
