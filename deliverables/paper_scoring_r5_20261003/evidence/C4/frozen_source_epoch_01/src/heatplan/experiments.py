from __future__ import annotations

import copy
from pathlib import Path

import numpy as np
import pandas as pd

from .io import load_inputs
from .model import HeatPlanningModel


def freeze_investment_plan(
    config: dict,
    nominal,
    *,
    relax_reliability: bool = True,
) -> dict:
    """Convert an optimized plan into fixed existing assets for replay.

    Stress tests historically relaxed the service constraint so that shortage
    became an observed output instead of making an event infeasible.  Formal
    annual replay can now retain the original reliability requirements by
    passing ``relax_reliability=False``.
    """
    fixed = copy.deepcopy(config)
    # A frozen asset has no *new* investment variable, but its selected annual
    # capital charge still belongs in annual replay and affordability tests.
    planning_summary = getattr(nominal, "summary", {})
    components = planning_summary.get("components_yuan_per_year", {})
    fixed["fixed_investment_accounting"] = {
        "scope": "annual_selected_plan_charges",
        "components": {name: float(components.get(name, 0.0)) for name in ("annualized_capex", "fixed_om_cost")},
        "metrics": {name: float(planning_summary.get(name, 0.0)) for name in (
            "regulated_annualized_capex_yuan_per_year", "shared_non_heating_capex_yuan_per_year", "public_subsidy_yuan_per_year")},
    }
    if not hasattr(nominal, "summary"):
        # Investment-only callers can still inspect installed assets. A model
        # replay must refuse these unknown charges instead of assuming zero.
        fixed["fixed_investment_accounting"] = {"scope": "unavailable_no_planning_summary"}
    for source in fixed["sources"]:
        row = nominal.investments[
            (nominal.investments["category"] == "source_capacity")
            & (nominal.investments["asset"] == source["name"])
        ]
        addition = float(row["value"].iloc[0]) if not row.empty else 0.0
        retired = nominal.investments[
            (nominal.investments["category"] == "source_retirement")
            & (nominal.investments["asset"] == source["name"])
        ]
        retirement = float(retired["value"].iloc[0]) if not retired.empty else 0.0
        source["existing_capacity_kw"] = float(source.get("existing_capacity_kw", 0.0)) + addition - retirement
        source["max_new_capacity_kw"] = 0.0
        source["max_retire_capacity_kw"] = 0.0

    for storage in fixed["storages"]:
        row = nominal.investments[
            (nominal.investments["category"] == "storage_capacity")
            & (nominal.investments["asset"] == storage["name"])
        ]
        addition = float(row["value"].iloc[0]) if not row.empty else 0.0
        storage["existing_capacity_kwh"] = float(storage.get("existing_capacity_kwh", 0.0)) + addition
        storage["max_new_capacity_kwh"] = 0.0

    for pipe in fixed["pipes"]:
        retrofit_row = nominal.investments[
            (nominal.investments["category"] == "pipe_retrofit")
            & (nominal.investments["asset"] == pipe["name"])
        ]
        if pipe.get("retrofit"):
            pipe["retrofit"]["fixed_value"] = (
                float(retrofit_row["value"].iloc[0]) if not retrofit_row.empty else 0.0
            )
            pipe["retrofit"]["capex_total_yuan"] = 0.0
        selected = nominal.investments[
            (nominal.investments["category"] == "pipe_choice")
            & (nominal.investments["asset"] == pipe["name"])
            & (nominal.investments["value"] > 0.5)
        ]
        if not selected.empty:
            option_name = str(selected["option"].iloc[0])
            option = next(x for x in pipe.get("diameter_options", []) if str(x["name"]) == option_name)
            pipe["existing_capacity_kw"] = float(pipe.get("existing_capacity_kw", 0.0)) + float(option["capacity_kw"])
        pipe["diameter_options"] = []

    for node in fixed["nodes"]:
        metering = node.get("heat_metering")
        if metering:
            row = nominal.investments[
                (nominal.investments["category"] == "heat_metering_coverage")
                & (nominal.investments["asset"] == node["name"])
            ]
            addition = float(row["value"].iloc[0]) if not row.empty else 0.0
            metering["existing_coverage_fraction"] = min(
                1.0,
                float(metering.get("existing_coverage_fraction", 0.0))
                + addition,
            )
            metering["max_new_coverage_fraction"] = 0.0
            metering.pop("fixed_new_coverage_fraction", None)
            metering["capex_yuan_per_coverage_fraction"] = 0.0
        retrofit = node.get("temperature_retrofit")
        if not retrofit:
            continue
        row = nominal.investments[
            (nominal.investments["category"] == "terminal_temperature_retrofit")
            & (nominal.investments["asset"] == node["name"])
        ]
        retrofit["fixed_value"] = float(row["value"].iloc[0]) if not row.empty else 0.0
        retrofit["capex_total_yuan"] = 0.0

    grid = fixed.get("grid_connection")
    if grid:
        row = nominal.investments[nominal.investments["category"] == "grid_connection_capacity"]
        addition = float(row["value"].iloc[0]) if not row.empty else 0.0
        grid["existing_capacity_kw"] = float(grid.get("existing_capacity_kw", 0.0)) + addition
        grid["max_new_capacity_kw"] = 0.0
    for chain in fixed.get("biomass_supply_chains", []):
        row = nominal.investments[
            (nominal.investments["category"] == "biomass_inventory_capacity")
            & (nominal.investments["asset"] == chain["name"])
        ]
        addition = float(row["value"].iloc[0]) if not row.empty else 0.0
        chain["existing_inventory_capacity_kg"] = float(
            chain.get("existing_inventory_capacity_kg", 0.0)
        ) + addition
        chain["max_new_inventory_capacity_kg"] = 0.0
    microgrid = fixed.get("microgrid_islanding")
    if microgrid:
        fields = {
            "microgrid_renewable_capacity": ("existing_renewable_capacity_kw", "max_new_renewable_capacity_kw"),
            "microgrid_battery_power": ("existing_battery_power_kw", "max_new_battery_power_kw"),
            "microgrid_battery_energy": ("existing_battery_energy_kwh", "max_new_battery_energy_kwh"),
            "microgrid_backup_capacity": ("existing_backup_capacity_kw", "max_new_backup_capacity_kw"),
        }
        for category, (existing_key, max_key) in fields.items():
            row = nominal.investments[nominal.investments["category"] == category]
            addition = float(row["value"].iloc[0]) if not row.empty else 0.0
            microgrid[existing_key] = float(microgrid.get(existing_key, 0.0)) + addition
            microgrid[max_key] = 0.0
    if relax_reliability:
        fixed["planning"]["max_unserved_rate"] = 1.0
        fixed["planning"]["max_scenario_unserved_rate"] = 1.0
        fixed["planning"]["firm_capacity_reserve_margin"] = 0.0
    return fixed


# Backward-compatible private alias retained for existing experiment helpers.
_freeze_investment_plan = freeze_investment_plan


def carry_forward_investments(config: dict, artifacts) -> dict:
    """Carry commissioned assets into the next rolling-planning stage.

    Previous additions become sunk existing assets; only unused candidate limits
    remain. Selected pipe options become existing capacity. A later stage may add
    fresh candidate limits explicitly through its pathway configuration.
    """
    carried = copy.deepcopy(config)
    for source in carried["sources"]:
        row = artifacts.investments[
            (artifacts.investments["category"] == "source_capacity")
            & (artifacts.investments["asset"] == source["name"])
        ]
        addition = float(row["value"].iloc[0]) if not row.empty else 0.0
        retired = artifacts.investments[
            (artifacts.investments["category"] == "source_retirement")
            & (artifacts.investments["asset"] == source["name"])
        ]
        retirement = float(retired["value"].iloc[0]) if not retired.empty else 0.0
        source["existing_capacity_kw"] = float(source.get("existing_capacity_kw", 0.0)) + addition - retirement
        source["max_new_capacity_kw"] = max(0.0, float(source.get("max_new_capacity_kw", 0.0)) - addition)
        source["max_retire_capacity_kw"] = max(
            0.0, float(source.get("max_retire_capacity_kw", 0.0)) - retirement
        )

    for storage in carried["storages"]:
        row = artifacts.investments[
            (artifacts.investments["category"] == "storage_capacity")
            & (artifacts.investments["asset"] == storage["name"])
        ]
        addition = float(row["value"].iloc[0]) if not row.empty else 0.0
        storage["existing_capacity_kwh"] = float(storage.get("existing_capacity_kwh", 0.0)) + addition
        storage["max_new_capacity_kwh"] = max(
            0.0, float(storage.get("max_new_capacity_kwh", 0.0)) - addition
        )

    for pipe in carried["pipes"]:
        retrofit_row = artifacts.investments[
            (artifacts.investments["category"] == "pipe_retrofit")
            & (artifacts.investments["asset"] == pipe["name"])
        ]
        if not retrofit_row.empty and float(retrofit_row["value"].iloc[0]) > 0.5 and pipe.get("retrofit"):
            pipe["retrofit"]["fixed_value"] = 1.0
            pipe["retrofit"]["capex_total_yuan"] = 0.0
        selected = artifacts.investments[
            (artifacts.investments["category"] == "pipe_choice")
            & (artifacts.investments["asset"] == pipe["name"])
            & (artifacts.investments["value"] > 0.5)
        ]
        if not selected.empty:
            option_name = str(selected["option"].iloc[0])
            option = next(x for x in pipe.get("diameter_options", []) if str(x["name"]) == option_name)
            pipe["existing_capacity_kw"] = float(pipe.get("existing_capacity_kw", 0.0)) + float(
                option["capacity_kw"]
            )
            pipe["diameter_options"] = []

    for node in carried["nodes"]:
        metering = node.get("heat_metering")
        if metering:
            row = artifacts.investments[
                (artifacts.investments["category"] == "heat_metering_coverage")
                & (artifacts.investments["asset"] == node["name"])
            ]
            if not row.empty:
                addition = float(row["value"].iloc[0])
                metering["existing_coverage_fraction"] = min(
                    1.0,
                    float(metering.get("existing_coverage_fraction", 0.0))
                    + addition,
                )
                metering["max_new_coverage_fraction"] = max(
                    0.0,
                    float(metering.get("max_new_coverage_fraction", 0.0))
                    - addition,
                )
        retrofit = node.get("temperature_retrofit")
        if not retrofit:
            continue
        row = artifacts.investments[
            (artifacts.investments["category"] == "terminal_temperature_retrofit")
            & (artifacts.investments["asset"] == node["name"])
        ]
        if not row.empty and float(row["value"].iloc[0]) > 0.5:
            retrofit["fixed_value"] = 1.0
            retrofit["capex_total_yuan"] = 0.0

    grid = carried.get("grid_connection")
    if grid:
        row = artifacts.investments[artifacts.investments["category"] == "grid_connection_capacity"]
        addition = float(row["value"].iloc[0]) if not row.empty else 0.0
        grid["existing_capacity_kw"] = float(grid.get("existing_capacity_kw", 0.0)) + addition
        grid["max_new_capacity_kw"] = max(0.0, float(grid.get("max_new_capacity_kw", 0.0)) - addition)

    for chain in carried.get("biomass_supply_chains", []):
        row = artifacts.investments[
            (artifacts.investments["category"] == "biomass_inventory_capacity")
            & (artifacts.investments["asset"] == chain["name"])
        ]
        if row.empty:
            continue
        addition = float(row["value"].iloc[0])
        chain["existing_inventory_capacity_kg"] = float(
            chain.get("existing_inventory_capacity_kg", 0.0)
        ) + addition
        chain["max_new_inventory_capacity_kg"] = max(
            0.0,
            float(chain.get("max_new_inventory_capacity_kg", 0.0)) - addition,
        )

    microgrid = carried.get("microgrid_islanding")
    if microgrid:
        fields = {
            "microgrid_renewable_capacity": (
                "existing_renewable_capacity_kw",
                "max_new_renewable_capacity_kw",
            ),
            "microgrid_battery_power": (
                "existing_battery_power_kw",
                "max_new_battery_power_kw",
            ),
            "microgrid_battery_energy": (
                "existing_battery_energy_kwh",
                "max_new_battery_energy_kwh",
            ),
            "microgrid_backup_capacity": (
                "existing_backup_capacity_kw",
                "max_new_backup_capacity_kw",
            ),
        }
        for category, (existing_key, max_key) in fields.items():
            row = artifacts.investments[artifacts.investments["category"] == category]
            if row.empty:
                continue
            addition = float(row["value"].iloc[0])
            microgrid[existing_key] = float(microgrid.get(existing_key, 0.0)) + addition
            microgrid[max_key] = max(0.0, float(microgrid.get(max_key, 0.0)) - addition)

    carried.pop("policy", None)
    carried["planning"].pop("risk", None)
    return carried


def carbon_price_scan(config_path: str | Path, timeseries_path: str | Path, prices: list[float]) -> pd.DataFrame:
    """A simple cost-carbon trade-off scan.

    For a publication-quality Pareto front, replace the carbon-price sweep with an
    epsilon-constraint: minimize cost under a sequence of explicit emission caps.
    """
    base_config, frame = load_inputs(config_path, timeseries_path)
    if base_config.get("building_thermal", {}).get("enabled", False):
        raise ValueError("Legacy no_demand_response ablation does not disable RC temperature flexibility; use an explicit RC control comparison")
    rows = []
    for price in prices:
        config = copy.deepcopy(base_config)
        config["planning"]["carbon_price_yuan_per_kg"] = float(price)
        summary = HeatPlanningModel(config, frame).solve().summary
        rows.append({"carbon_price_yuan_per_kg": price, **summary})
    return pd.json_normalize(rows)


def nondominated_cost_emission_points(
    frame: pd.DataFrame, *, cost_column: str = "objective_yuan_per_year",
    emission_column: str = "total_service_emissions_kg_per_year",
    absolute_tolerance: float = 1e-6, relative_tolerance: float = 1e-8,
) -> pd.DataFrame:
    """Stable tolerance-aware duplicate removal and two-objective minimization.

    The result is the nondominated subset of sampled feasible incumbents, not
    a claim that the complete continuous Pareto set has been enumerated.
    """
    if not np.isfinite(absolute_tolerance) or not np.isfinite(relative_tolerance) or absolute_tolerance < 0 or relative_tolerance < 0:
        raise ValueError("frontier tolerances must be finite and nonnegative")
    if frame.empty:
        return frame.copy()
    ordered = frame.sort_values([emission_column,cost_column], kind="stable").reset_index(drop=True)
    values = ordered[[cost_column,emission_column]].to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("frontier objectives must be finite")
    unique: list[int] = []
    for i, point in enumerate(values):
        duplicate = any(np.all(np.abs(point-values[j]) <= absolute_tolerance+relative_tolerance*np.maximum(np.abs(point),np.abs(values[j]))) for j in unique)
        if not duplicate:
            unique.append(i)
    kept = []
    for i in unique:
        dominated = False
        for j in unique:
            if i == j:
                continue
            tolerance = absolute_tolerance+relative_tolerance*np.maximum(np.abs(values[i]),np.abs(values[j]))
            if np.all(values[j] <= values[i]+tolerance) and np.any(values[j] < values[i]-tolerance):
                dominated = True
                break
        if not dominated:
            kept.append(i)
    return ordered.iloc[kept].reset_index(drop=True)


def epsilon_constraint_pareto(
    config_path: str | Path,
    timeseries_path: str | Path,
    points: int = 7,
) -> pd.DataFrame:
    """Sample a nondominated system cost/carbon frontier with explicit caps.

    The investment and operating model is unchanged across points. A hard reliability
    constraint prevents the emissions objective from creating artificial low-carbon
    solutions through heat curtailment.
    """
    if points < 2:
        raise ValueError("points 至少为 2")
    base_config, frame = load_inputs(config_path, timeseries_path)
    base_config = copy.deepcopy(base_config)
    base_config["planning"]["carbon_price_yuan_per_kg"] = 0.0
    base_config["planning"].pop("emissions_cap_kg_per_year", None)

    cost_optimum = HeatPlanningModel(copy.deepcopy(base_config), frame).solve_lexicographic(primary="total_cost").summary
    emission_optimum = HeatPlanningModel(copy.deepcopy(base_config), frame).solve_lexicographic(primary="emissions").summary
    emission_column = "total_service_emissions_kg_per_year"
    min_emissions = float(emission_optimum[emission_column])
    cost_optimum_emissions = float(cost_optimum[emission_column])
    if min_emissions > cost_optimum_emissions + 1e-6 + 1e-8*abs(cost_optimum_emissions):
        raise RuntimeError("Carbon endpoint exceeds cost endpoint; improve solve budgets before defining an epsilon range")
    caps = np.linspace(min_emissions, cost_optimum_emissions, points)

    rows = []
    for cap in caps:
        config = copy.deepcopy(base_config)
        # A tiny numerical allowance avoids rejecting the emissions-optimal endpoint.
        effective_cap = float(cap) + 1e-7 + abs(float(cap))*1e-8
        config["planning"]["emissions_cap_kg_per_year"] = effective_cap
        summary = HeatPlanningModel(config, frame).solve(objective_mode="total_cost").summary
        if not summary["numerical_audit_passed"] or not summary["emissions_cap_pass"]:
            raise RuntimeError("A sampled frontier incumbent failed independent numerical or carbon-cap checks")
        rows.append({"requested_emissions_cap_kg_per_year": float(cap), **summary})
    sampled = pd.json_normalize(rows)
    result = nondominated_cost_emission_points(sampled)
    result.attrs["all_sampled_points"] = sampled.to_dict(orient="records")
    result.attrs["cost_endpoint"] = cost_optimum
    result.attrs["carbon_endpoint"] = emission_optimum
    result["frontier_scope"] = "nondominated_subset_of_sampled_feasible_solutions"
    result["sampled_point_count"] = len(rows)
    result["removed_duplicate_or_dominated_count"] = len(rows)-len(result)
    result["marginal_abatement_cost_yuan_per_kg"] = np.nan
    for i in range(len(result) - 1):
        delta_cost = result.loc[i, "objective_yuan_per_year"] - result.loc[i + 1, "objective_yuan_per_year"]
        delta_emission = result.loc[i + 1, emission_column] - result.loc[i, emission_column]
        if delta_emission > 1e-6 + 1e-8*abs(result.loc[i + 1, emission_column]):
            result.loc[i, "marginal_abatement_cost_yuan_per_kg"] = delta_cost / delta_emission

    cost_span = result["objective_yuan_per_year"].max() - result["objective_yuan_per_year"].min()
    emission_span = result[emission_column].max() - result[emission_column].min()
    normalized_cost = (result["objective_yuan_per_year"] - result["objective_yuan_per_year"].min()) / max(cost_span, 1e-9)
    normalized_emission = (result[emission_column] - result[emission_column].min()) / max(emission_span, 1e-9)
    result["distance_to_ideal"] = np.sqrt(normalized_cost**2 + normalized_emission**2)
    result["recommended_compromise"] = False
    result.loc[result["distance_to_ideal"].idxmin(), "recommended_compromise"] = True
    result["recommendation_method"] = "closest_normalized_ideal_point"
    return result


def ablation_study(config_path: str | Path, timeseries_path: str | Path) -> pd.DataFrame:
    """Run four transparent ablations used by the report's contribution analysis."""
    base_config, frame = load_inputs(config_path, timeseries_path)
    variants = {
        "baseline": {},
        "no_exergy_penalty": {"exergy": False},
        "no_demand_response": {"dr": False},
        "no_new_storage": {"storage": False},
    }
    rows = []
    for name, switches in variants.items():
        config = copy.deepcopy(base_config)
        if switches.get("exergy") is False:
            config["planning"]["exergy_penalty_yuan_per_kwh"] = 0.0
        if switches.get("dr") is False:
            config["demand_response"]["max_shift_fraction"] = 0.0
        if switches.get("storage") is False:
            for storage in config["storages"]:
                storage["max_new_capacity_kwh"] = 0.0
        rows.append({"variant": name, **HeatPlanningModel(config, frame).solve().summary})
    return pd.json_normalize(rows)


def northern_cold_wave_stress_test(
    config_path: str | Path,
    timeseries_path: str | Path,
    temperature_drop_c: float = 8.0,
    demand_multiplier: float = 1.15,
    waste_heat_availability_multiplier: float = 0.85,
) -> pd.DataFrame:
    """Freeze the nominal investment plan and evaluate a compound northern cold wave.

    The stress combines colder outdoor air, higher heat load, and reduced industrial
    waste-heat availability. Dispatch may adapt, but no new capacity or pipe can be built.
    """
    config, frame = load_inputs(config_path, timeseries_path)
    if config.get("building_thermal", {}).get("enabled", False):
        raise ValueError("Legacy load-column stress is not an RC load perturbation; specify building H, outdoor temperature and gains explicitly")
    nominal_model = HeatPlanningModel(copy.deepcopy(config), frame)
    nominal = nominal_model.solve()
    fixed = _freeze_investment_plan(config, nominal)

    stressed = frame.copy()
    if "outdoor_temp" in stressed:
        stressed["outdoor_temp"] -= float(temperature_drop_c)
    demand_columns = [column for node in fixed["nodes"] for column in node.get("demand_columns", {}).values()]
    stressed[demand_columns] *= float(demand_multiplier)
    if "waste_heat_availability" in stressed:
        stressed["waste_heat_availability"] *= float(waste_heat_availability_multiplier)

    stressed_summary = HeatPlanningModel(fixed, stressed).solve().summary
    return pd.DataFrame([
        {"scenario": "nominal", **nominal.summary},
        {
            "scenario": "compound_cold_wave",
            "temperature_drop_c": temperature_drop_c,
            "demand_multiplier": demand_multiplier,
            "waste_heat_availability_multiplier": waste_heat_availability_multiplier,
            **stressed_summary,
        },
    ])


def northern_resilience_stress_suite(
    config_path: str | Path,
    timeseries_path: str | Path,
) -> pd.DataFrame:
    """Evaluate frozen investments under cold-wave and N-1 contingencies."""
    config, frame = load_inputs(config_path, timeseries_path)
    if config.get("building_thermal", {}).get("enabled", False):
        raise ValueError("Legacy load-column stress is not an RC load perturbation; use the RC competition runner")
    nominal = HeatPlanningModel(copy.deepcopy(config), frame).solve()
    fixed = _freeze_investment_plan(config, nominal)
    demand_columns = [column for node in fixed["nodes"] for column in node.get("demand_columns", {}).values()]

    def cold_wave_frame() -> pd.DataFrame:
        stressed = frame.copy()
        if "outdoor_temp" in stressed:
            stressed["outdoor_temp"] -= 8.0
        stressed[demand_columns] *= 1.15
        if "waste_heat_availability" in stressed:
            stressed["waste_heat_availability"] *= 0.85
        return stressed

    cases: list[tuple[str, dict, pd.DataFrame]] = [("nominal", fixed, frame.copy())]
    cases.append(("compound_cold_wave", copy.deepcopy(fixed), cold_wave_frame()))

    source_outage = copy.deepcopy(fixed)
    largest_source = max(
        source_outage["sources"],
        key=lambda x: float(x.get("existing_capacity_kw", 0.0)) * float(x.get("firm_capacity_credit", 1.0)),
    )
    largest_source["availability"] = 0.0
    largest_source.pop("availability_column", None)
    cases.append((f"n_minus_1_source__{largest_source['name']}", source_outage, cold_wave_frame()))

    pipe_outage = copy.deepcopy(fixed)
    active_pipes = [x for x in pipe_outage["pipes"] if float(x.get("existing_capacity_kw", 0.0)) > 0]
    if active_pipes:
        critical_pipe = max(active_pipes, key=lambda x: float(x.get("existing_capacity_kw", 0.0)))
        critical_pipe["existing_capacity_kw"] = 0.0
        cases.append((f"n_minus_1_pipe__{critical_pipe['name']}", pipe_outage, cold_wave_frame()))

    rows = []
    for name, scenario_config, scenario_frame in cases:
        try:
            summary = HeatPlanningModel(scenario_config, scenario_frame).solve().summary
            rows.append({"scenario": name, "feasible": True, **summary})
        except RuntimeError as exc:
            rows.append({
                "scenario": name,
                "feasible": False,
                "solver_message": str(exc),
                "unserved_rate": np.nan,
                "worst_scenario_unserved_rate": np.nan,
                "shortage_quantified": False,
            })
    return pd.json_normalize(rows)


def frozen_plan_scenario_replay(config: dict, base_frame: pd.DataFrame, scenario_bundle: pd.DataFrame) -> pd.DataFrame:
    """Replay a deterministic investment plan against an external scenario bundle.

    Infeasibility is reported explicitly because a frozen grid connection or
    topology may make a severe scenario impossible even when heat-curtailment
    variables are available. This distinction is more informative than silently
    dropping the scenario or allowing new investment during the stress test.
    """
    nominal = HeatPlanningModel(copy.deepcopy(config), base_frame).solve()
    fixed = _freeze_investment_plan(config, nominal)
    rows: list[dict] = []
    for scenario, block in scenario_bundle.groupby("scenario", sort=False):
        frame = block.copy().reset_index(drop=True)
        frame["scenario_probability"] = 1.0
        try:
            summary = HeatPlanningModel(copy.deepcopy(fixed), frame).solve().summary
            rows.append(
                {
                    "scenario": scenario,
                    "feasible": True,
                    "unserved_rate": summary["unserved_rate"],
                    "unserved_kwh_per_year": summary["unserved_kwh_per_year"],
                    "objective_yuan_per_year": summary["objective_yuan_per_year"],
                    "emissions_kg_per_year": summary["emissions_kg_per_year"],
                    "reason": "",
                }
            )
        except RuntimeError as exc:
            rows.append(
                {
                    "scenario": scenario,
                    "feasible": False,
                    "unserved_rate": np.nan,
                    "unserved_kwh_per_year": np.nan,
                    "objective_yuan_per_year": np.nan,
                    "emissions_kg_per_year": np.nan,
                    "reason": str(exc),
                }
            )
    return pd.DataFrame(rows)
