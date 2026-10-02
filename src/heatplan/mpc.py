from __future__ import annotations

import copy
import math
from typing import Any

import pandas as pd

from .experiments import freeze_investment_plan
from .io import validate_inputs
from .model import HeatPlanningModel


def _duration_steps(hours: float, dt: float, name: str) -> int:
    value = float(hours)
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError(f"MPC {name} 必须为正的有限小时数")
    steps = value / dt
    nearest = round(steps)
    if nearest < 1 or not math.isclose(steps, nearest, rel_tol=1e-9, abs_tol=1e-9):
        raise ValueError(f"MPC {name} 必须是 time_step_hours 的正整数倍")
    return nearest


def _validate_rolling_scope(config: dict[str, Any], frame: pd.DataFrame) -> None:
    """Fail explicitly for states which are not yet carried between windows."""
    unsupported = []
    if any(source.get("unit_commitment", False) for source in config["sources"]):
        unsupported.append("unit_commitment（机组启停）")
    if any(node.get("thermal_inertia") for node in config["nodes"]):
        unsupported.append("thermal_inertia（建筑热状态）")
    if config.get("biomass_supply_chains"):
        unsupported.append("biomass_supply_chains（燃料库存）")
    if float(config.get("demand_response", {}).get("max_shift_fraction", 0.0)) > 0.0:
        unsupported.append("demand_response（跨窗口移峰余额）")
    microgrid = config.get("microgrid_islanding", {})
    if any(float(microgrid.get(key, 0.0)) > 0.0 for key in (
        "existing_battery_energy_kwh", "max_new_battery_energy_kwh",
        "existing_battery_power_kw", "max_new_battery_power_kw",
    )):
        unsupported.append("microgrid battery（电池 SOC）")
    if unsupported:
        raise ValueError(
            "MPC 尚未支持以下模块的跨窗口状态传递，不能循环重置后继续计算："
            + "、".join(unsupported)
        )
    if frame.empty:
        raise ValueError("MPC 需要非空的连续逐时数据")
    if "timestamp" in frame:
        timestamps = pd.to_datetime(frame["timestamp"], errors="coerce", utc=True)
        if timestamps.isna().any():
            raise ValueError("MPC timestamp 必须全部为有效时间戳")
        dt = float(config["planning"].get("time_step_hours", 1.0))
        expected_interval = pd.Timedelta(hours=dt)
        if not timestamps.diff().iloc[1:].eq(expected_interval).all():
            raise ValueError(
                "MPC timestamp 必须按输入顺序严格升序、无缺时或重时，"
                "且每步间隔等于 time_step_hours；不会自动排序。"
            )
    for column in ("scenario", "boundary_group"):
        if column in frame and frame[column].nunique(dropna=False) != 1:
            raise ValueError(f"MPC 仅支持连续单场景、单状态组数据，{column} 不得有多个取值")
    weights = frame.get("period_weight")
    if weights is None:
        weights = [config["planning"].get("representative_day_weight", 1.0)]
    if any(not math.isclose(float(value), 1.0, abs_tol=1e-9) for value in weights):
        raise ValueError("MPC 需要实际连续时序，period_weight/representative_day_weight 必须为 1")
    validate_inputs(config, frame)


def rolling_horizon_dispatch(
    config: dict[str, Any],
    frame: pd.DataFrame,
    horizon_hours: float = 24,
    control_step_hours: float = 1,
    fixed_investment_config: dict[str, Any] | None = None,
    time_limit_per_horizon: float = 30.0,
) -> pd.DataFrame:
    """Planning-informed receding-horizon dispatch.

    If a fixed asset configuration is not supplied, the function first plans
    once over the available input and freezes those investments. Each horizon
    implements only its first control block and passes storage SOC forward.
    Durations are hours and must be whole multiples of time_step_hours. Input
    must be one continuous, unweighted trajectory. Heat-storage SOC is the only
    supported inter-window state; other stateful modules fail explicitly.
    This replays the supplied future data, rather than a forecast-error model.
    When timestamp is present, its order and spacing are validated without
    sorting. Without timestamps, time retains its label/index semantics: rows
    are executed in input order and actual calendar continuity is not verified.
    """
    effective_config = fixed_investment_config if fixed_investment_config is not None else config
    dt = float(effective_config["planning"].get("time_step_hours", 1.0))
    if not math.isfinite(dt) or dt <= 0.0:
        raise ValueError("MPC time_step_hours 必须为正的有限小时数")
    horizon_steps = _duration_steps(horizon_hours, dt, "horizon_hours")
    control_steps = _duration_steps(control_step_hours, dt, "control_step_hours")
    if control_steps > horizon_steps:
        raise ValueError("MPC control_step_hours 不得大于 horizon_hours")
    _validate_rolling_scope(effective_config, frame)
    if fixed_investment_config is None:
        planned = HeatPlanningModel(copy.deepcopy(config), frame).solve(time_limit=time_limit_per_horizon)
        fixed = freeze_investment_plan(config, planned, relax_reliability=False)
    else:
        fixed = copy.deepcopy(fixed_investment_config)
    storage_state = {
        item["name"]: float(item.get("initial_soc_fraction", 0.5)) for item in fixed["storages"]
    }
    if any(not math.isfinite(value) or not 0.0 <= value <= 1.0 for value in storage_state.values()):
        raise ValueError("MPC 储热 initial_soc_fraction 必须位于 [0,1]")
    implemented: list[pd.DataFrame] = []
    for start in range(0, len(frame), control_steps):
        stop = min(start + horizon_steps, len(frame))
        window = frame.iloc[start:stop].copy().reset_index(drop=True)
        executed_steps = min(control_steps, len(window))
        window_config = copy.deepcopy(fixed)
        for storage in window_config["storages"]:
            storage["boundary_mode"] = "fixed"
            storage["initial_soc_fraction"] = storage_state[storage["name"]]
            if stop < len(frame):
                storage["final_soc_min_fraction"] = 0.0
        model = HeatPlanningModel(window_config, window)
        model.build()
        unfrozen = [
            model.problem.names[index]
            for index in model.component_coefficients.get("annualized_capex", {})
            if model.problem.upper[index] > model.problem.lower[index]
        ]
        if unfrozen:
            raise ValueError("MPC fixed_investment_config 尚有可变投资：" + "、".join(unfrozen))
        artifacts = model.solve(time_limit=time_limit_per_horizon)
        first = artifacts.dispatch[artifacts.dispatch["time_index"] < executed_steps].copy()
        first["mpc_window_start"] = start
        first["global_time_index"] = first["time_index"] + start
        implemented.append(first)
        end_index = executed_steps - 1
        for storage in window_config["storages"]:
            row = artifacts.dispatch[
                (artifacts.dispatch["category"] == "storage_soc")
                & (artifacts.dispatch["asset"] == storage["name"])
                & (artifacts.dispatch["time_index"] == end_index)
            ]
            capacity = float(storage.get("existing_capacity_kwh", 0.0))
            if len(row) != 1:
                raise RuntimeError(f"MPC 缺少储热 {storage['name']} 的已执行末步 SOC")
            energy = float(row.iloc[0]["value"])
            if not math.isfinite(energy) or energy < -1e-7 or energy > capacity + 1e-7:
                raise RuntimeError(f"MPC 储热 {storage['name']} 的跨窗口 SOC 越界")
            storage_state[storage["name"]] = (
                min(1.0, max(0.0, energy / capacity)) if capacity > 0.0 else 0.0
            )
    return pd.concat(implemented, ignore_index=True) if implemented else pd.DataFrame()
