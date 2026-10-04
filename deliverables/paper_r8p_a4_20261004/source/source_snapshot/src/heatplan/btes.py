from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def simulate_btes_long_term(specification: dict[str, Any]) -> pd.DataFrame:
    """Lumped multi-year borehole thermal energy storage screening model.

    The calculation is intentionally conservative and is used before a detailed
    TRNSYS/FE model. It represents top, side and bottom losses separately,
    enforces minimum/maximum storage temperature and reports delivered energy,
    unmet extraction and annual recovery efficiency.
    """
    count = int(specification["borehole_count"])
    depth = float(specification["borehole_depth_m"])
    spacing = float(specification["borehole_spacing_m"])
    years = int(specification.get("simulation_years", 10))
    if count <= 0 or depth <= 0 or spacing <= 0 or years <= 0:
        raise ValueError("BTES 井数、深度、间距和模拟年数必须为正")
    footprint = count * spacing**2
    radius = np.sqrt(footprint / np.pi)
    volume = footprint * depth
    top_area = footprint
    bottom_area = footprint
    side_area = 2.0 * np.pi * radius * depth
    volumetric_capacity = float(specification.get("volumetric_heat_capacity_mj_per_m3k", 2.2))
    thermal_capacity_kwh_per_k = volume * volumetric_capacity / 3.6
    u_top = float(specification.get("top_u_w_per_m2k", 0.18))
    u_side = float(specification.get("side_u_w_per_m2k", 0.08))
    u_bottom = float(specification.get("bottom_u_w_per_m2k", 0.05))
    ua_kw_per_k = (u_top * top_area + u_side * side_area + u_bottom * bottom_area) / 1000.0
    ground_temperature = float(specification.get("undisturbed_ground_temperature_c", 10.0))
    temperature = float(specification.get("initial_temperature_c", ground_temperature))
    minimum_temperature = float(specification.get("minimum_temperature_c", ground_temperature - 1.0))
    maximum_temperature = float(specification.get("maximum_temperature_c", 65.0))
    charge_efficiency = float(specification.get("charge_efficiency", 0.95))
    discharge_efficiency = float(specification.get("discharge_efficiency", 0.92))
    monthly_charge = np.asarray(specification.get("monthly_charge_kwh", [0.0] * 12), dtype=float)
    monthly_demand = np.asarray(specification.get("monthly_discharge_demand_kwh", [0.0] * 12), dtype=float)
    if len(monthly_charge) != 12 or len(monthly_demand) != 12:
        raise ValueError("BTES 月度充热和取热序列必须各含 12 个数")

    rows: list[dict[str, Any]] = []
    for year in range(1, years + 1):
        charged = delivered = losses = unmet = 0.0
        start_temperature = temperature
        for month in range(12):
            hours = 24.0 * [31, 28.25, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month]
            requested_charge = max(0.0, monthly_charge[month])
            headroom = max(0.0, (maximum_temperature - temperature) * thermal_capacity_kwh_per_k)
            accepted = min(requested_charge * charge_efficiency, headroom)
            temperature += accepted / thermal_capacity_kwh_per_k
            charged += requested_charge
            loss = max(0.0, ua_kw_per_k * (temperature - ground_temperature) * hours)
            available_above_ground = max(0.0, (temperature - minimum_temperature) * thermal_capacity_kwh_per_k)
            loss = min(loss, available_above_ground)
            temperature -= loss / thermal_capacity_kwh_per_k
            losses += loss
            demand = max(0.0, monthly_demand[month])
            extractable = max(0.0, (temperature - minimum_temperature) * thermal_capacity_kwh_per_k)
            extracted = min(demand / max(discharge_efficiency, 1e-9), extractable)
            supplied = extracted * discharge_efficiency
            temperature -= extracted / thermal_capacity_kwh_per_k
            delivered += supplied
            unmet += demand - supplied
        rows.append({
            "year": year,
            "start_temperature_c": start_temperature,
            "end_temperature_c": temperature,
            "charged_input_kwh": charged,
            "delivered_heat_kwh": delivered,
            "thermal_loss_kwh": losses,
            "unmet_extraction_kwh": unmet,
            "recovery_efficiency": delivered / max(charged, 1e-9),
            "loss_fraction": losses / max(charged * charge_efficiency, 1e-9),
            "thermal_capacity_kwh_per_k": thermal_capacity_kwh_per_k,
            "footprint_m2": footprint,
            "equivalent_radius_m": radius,
        })
    return pd.DataFrame(rows)


def btes_go_no_go(result: pd.DataFrame, specification: dict[str, Any]) -> dict[str, Any]:
    final = result.iloc[-1]
    min_efficiency = float(specification.get("minimum_final_recovery_efficiency", 0.70))
    max_unmet_rate = float(specification.get("maximum_unmet_extraction_rate", 0.02))
    unmet_rate = float(final["unmet_extraction_kwh"]) / max(
        float(final["delivered_heat_kwh"] + final["unmet_extraction_kwh"]), 1e-9
    )
    reasons = []
    if float(final["recovery_efficiency"]) < min_efficiency:
        reasons.append("末年回收效率不足")
    if unmet_rate > max_unmet_rate:
        reasons.append("末年取热缺口过高")
    if float(final["end_temperature_c"]) <= float(specification.get("minimum_temperature_c", 9.0)):
        reasons.append("储热体长期温度接近下限")
    return {
        "status": "pass" if not reasons else "fail",
        "final_recovery_efficiency": float(final["recovery_efficiency"]),
        "final_unmet_rate": unmet_rate,
        "final_temperature_c": float(final["end_temperature_c"]),
        "reason": "; ".join(reasons),
    }

