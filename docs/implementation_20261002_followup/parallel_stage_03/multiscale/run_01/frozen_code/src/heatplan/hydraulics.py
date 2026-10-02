from __future__ import annotations

import math
from typing import Any

import pandas as pd


def _friction_factor(reynolds: float, roughness_m: float, diameter_m: float) -> float:
    if reynolds <= 0:
        return 0.0
    if reynolds < 2300:
        return 64.0 / reynolds
    return 0.25 / math.log10(roughness_m / (3.7 * diameter_m) + 5.74 / reynolds**0.9) ** 2


def hydraulic_thermal_postcheck(
    config: dict[str, Any],
    investments: pd.DataFrame,
    dispatch: pd.DataFrame,
) -> pd.DataFrame:
    """Post-check selected pipes with Darcy-Weisbach and steady heat loss.

    This is deliberately a verification layer, not a replacement for a detailed
    hydraulic network simulator. It catches impossible velocities, excessive
    pressure drops, pumping power and thermal losses after the MILP chooses a
    topology.
    """
    checks = config.get("engineering_postcheck", {})
    rho = float(checks.get("water_density_kg_per_m3", 985.0))
    cp = float(checks.get("water_cp_kj_per_kgk", 4.18))
    viscosity = float(checks.get("dynamic_viscosity_pa_s", 0.00055))
    delta_t = float(checks.get("design_delta_t_c", 25.0))
    pump_efficiency = float(checks.get("pump_efficiency", 0.72))
    max_velocity = float(checks.get("max_velocity_m_per_s", 3.0))
    max_dp_per_m = float(checks.get("max_pressure_drop_pa_per_m", 300.0))
    max_loss_share = float(checks.get("max_thermal_loss_share", 0.08))

    flow = dispatch[dispatch["category"].isin(["pipe_flow_forward", "pipe_flow_reverse"])].copy()
    if flow.empty:
        return pd.DataFrame(columns=[
            "pipe", "selected_option", "peak_heat_kw", "velocity_m_per_s",
            "pressure_drop_pa_per_m", "pump_power_kw", "thermal_loss_share", "status", "reason",
        ])
    peak_by_pipe = (
        flow.groupby(["asset", "scenario", "time_index"], as_index=False)["value"].sum()
        .groupby("asset")["value"].max()
    )
    rows: list[dict[str, Any]] = []
    for pipe in config["pipes"]:
        name = str(pipe["name"])
        selected = investments[
            (investments["category"] == "pipe_choice")
            & (investments["asset"] == name)
            & (investments["value"] > 0.5)
        ]
        option = None
        if not selected.empty:
            option_name = str(selected.iloc[0]["option"])
            option = next(item for item in pipe.get("diameter_options", []) if str(item["name"]) == option_name)
        elif float(pipe.get("existing_capacity_kw", 0.0)) > 0:
            option_name = "existing"
            option = pipe.get("existing_hydraulic", {})
        else:
            continue
        retrofit_selected = investments[
            (investments["category"] == "pipe_retrofit")
            & (investments["asset"] == name)
            & (investments["value"] > 0.5)
        ]
        if not retrofit_selected.empty and pipe.get("retrofit"):
            option = {**option, **pipe["retrofit"]}
            option_name = f"{option_name}+retrofit"
        diameter_mm = float(option.get("diameter_mm", 0.0))
        length_m = float(option.get("length_m", pipe.get("length_m", 0.0)))
        heat_kw = float(peak_by_pipe.get(name, 0.0))
        if heat_kw <= 1e-6:
            rows.append({
                "pipe": name, "selected_option": option_name, "peak_heat_kw": heat_kw,
                "velocity_m_per_s": 0.0, "pressure_drop_pa_per_m": 0.0,
                "pump_power_kw": 0.0, "thermal_loss_share": math.nan,
                "status": "warning", "reason": "已建管线在求解时段无流量；应核实其 N-1/远期备用价值",
            })
            continue
        if diameter_mm <= 0 or length_m <= 0:
            rows.append({
                "pipe": name, "selected_option": option_name, "peak_heat_kw": heat_kw,
                "velocity_m_per_s": math.nan, "pressure_drop_pa_per_m": math.nan,
                "pump_power_kw": math.nan, "thermal_loss_share": math.nan,
                "status": "fail", "reason": "缺少 diameter_mm 或 length_m，无法完成工程复核",
            })
            continue
        diameter = diameter_mm / 1000.0
        mass_flow = heat_kw / max(cp * delta_t, 1e-9)
        volumetric_flow = mass_flow / rho
        area = math.pi * diameter**2 / 4.0
        velocity = volumetric_flow / max(area, 1e-12)
        reynolds = rho * velocity * diameter / max(viscosity, 1e-12)
        roughness = float(option.get("roughness_mm", pipe.get("roughness_mm", 0.1))) / 1000.0
        friction = _friction_factor(reynolds, roughness, diameter)
        dp_per_m = friction * rho * velocity**2 / (2.0 * diameter)
        pump_power = dp_per_m * length_m * volumetric_flow / max(pump_efficiency, 1e-9) / 1000.0
        u_value = float(option.get("heat_loss_w_per_mk", pipe.get("heat_loss_w_per_mk", 0.25)))
        mean_temp = float(option.get("mean_water_temp_c", pipe.get("mean_water_temp_c", 70.0)))
        ground_temp = float(option.get("ground_temp_c", pipe.get("ground_temp_c", 5.0)))
        thermal_loss_kw = u_value * length_m * max(mean_temp - ground_temp, 0.0) / 1000.0
        loss_share = thermal_loss_kw / max(heat_kw, 1e-9)
        reasons = []
        if velocity > max_velocity:
            reasons.append(f"流速 {velocity:.2f}>{max_velocity:.2f} m/s")
        if dp_per_m > max_dp_per_m:
            reasons.append(f"沿程压降 {dp_per_m:.0f}>{max_dp_per_m:.0f} Pa/m")
        if loss_share > max_loss_share:
            reasons.append(f"稳态热损失率 {loss_share:.1%}>{max_loss_share:.1%}")
        rows.append({
            "pipe": name, "selected_option": option_name, "peak_heat_kw": heat_kw,
            "velocity_m_per_s": velocity, "pressure_drop_pa_per_m": dp_per_m,
            "pump_power_kw": pump_power, "thermal_loss_share": loss_share,
            "status": "pass" if not reasons else "fail", "reason": "; ".join(reasons),
        })
    return pd.DataFrame(rows)
