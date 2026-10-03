from __future__ import annotations

import copy
from typing import Any

import numpy as np
import pandas as pd

from .experiments import _freeze_investment_plan
from .model import HeatPlanningModel
from .scenarios import build_correlated_northern_scenario_bundle


def monte_carlo_out_of_sample_replay(
    config: dict[str, Any],
    base_frame: pd.DataFrame,
    samples: int = 30,
    random_seed: int = 42,
    time_limit: float = 60.0,
) -> pd.DataFrame:
    """Freeze the optimized plan and replay correlated unseen disturbances."""
    if samples < 1:
        raise ValueError("samples 必须为正")
    nominal = HeatPlanningModel(copy.deepcopy(config), base_frame).solve(time_limit=time_limit)
    fixed = _freeze_investment_plan(config, nominal)
    demand_columns = [column for node in config["nodes"] for column in node.get("demand_columns", {}).values()]
    rng = np.random.default_rng(random_seed)
    specifications = []
    probabilities = np.full(samples, 1.0 / samples)
    for sample in range(samples):
        severity = float(rng.beta(2.0, 5.0))
        specifications.append({
            "name": f"oos_{sample:03d}",
            "probability": float(probabilities[sample]),
            "temperature_offset_c": -12.0 * severity,
            "demand_multiplier": 1.0 + 0.22 * severity + float(rng.normal(0.0, 0.015)),
            "waste_heat_multiplier": max(0.45, 1.0 - 0.45 * severity + float(rng.normal(0.0, 0.04))),
            "electricity_price_multiplier": 1.0 + 0.35 * severity,
            "grid_emission_multiplier": 1.0 + 0.20 * severity,
        })
    bundle = build_correlated_northern_scenario_bundle(
        base_frame, demand_columns, specifications,
        period_weight=float(config["planning"].get("representative_day_weight", 1.0)),
    )
    artifacts = HeatPlanningModel(fixed, bundle).solve(time_limit=time_limit)
    rows = []
    for scenario, metrics in artifacts.summary["scenario_results"].items():
        rows.append({"scenario": scenario, **metrics})
    result = pd.DataFrame(rows).sort_values("unserved_rate", ascending=False).reset_index(drop=True)
    result["unserved_rate_rank"] = np.arange(1, len(result) + 1)
    return result

