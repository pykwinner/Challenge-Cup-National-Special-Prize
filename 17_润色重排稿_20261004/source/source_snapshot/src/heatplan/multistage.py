from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
import yaml

from .accounting import HEATING_CASH_COMPONENTS, heating_cost_allocation_fraction
from .feasibility import apply_feasibility_exclusions, evaluate_feasibility, load_feasibility
from .finance import FinanceArtifacts, break_even_tariff, evaluate_project_finance
from .hydraulics import hydraulic_thermal_postcheck
from .io import load_inputs, validate_inputs
from .linear import LinearMILP
from .model import HeatPlanningModel
from .scenarios import build_correlated_northern_scenario_bundle


@dataclass
class JointMultiStageArtifacts:
    result: Any
    stage_summary: pd.DataFrame
    build_schedule: pd.DataFrame
    cumulative_investments: pd.DataFrame
    dispatch: pd.DataFrame
    finance: FinanceArtifacts | None
    hydraulic_postcheck: pd.DataFrame
    feasibility_report: pd.DataFrame | None
    summary: dict[str, Any] = field(default_factory=dict)


INVESTMENT_CATEGORIES = {
    "source_capacity",
    "source_retirement",
    "storage_capacity",
    "pipe_choice",
    "pipe_retrofit",
    "terminal_temperature_retrofit",
    "heat_metering_coverage",
    "grid_connection_capacity",
    "biomass_inventory_capacity",
    "microgrid_renewable_capacity",
    "microgrid_battery_power",
    "microgrid_battery_energy",
    "microgrid_backup_capacity",
}


def _stage_year(stage: dict[str, Any]) -> int:
    return int(stage["year"] if "year" in stage else stage["name"])


def _assert_complete_investment_registry(model: HeatPlanningModel) -> None:
    """Fail before stripping any investment cost that cannot be rebuilt."""
    registered = {
        index for category in INVESTMENT_CATEGORIES
        for index in model.variables.get(category, {}).values()
    }
    missing = set(model.component_coefficients.get("annualized_capex", {})) - registered
    if missing:
        names = ", ".join(model.problem.names[index] for index in sorted(missing))
        raise ValueError(f"联合多阶段投资类别登记不完整，无法承接投资费用: {names}")


def _apply_environment(frame: pd.DataFrame, demand_columns: list[str], stage: dict[str, Any]) -> pd.DataFrame:
    result = frame.copy()
    result[demand_columns] *= float(stage.get("demand_multiplier", 1.0))
    if "outdoor_temp" in result:
        result["outdoor_temp"] += float(stage.get("temperature_offset_c", 0.0))
    if "waste_heat_availability" in result:
        result["waste_heat_availability"] *= float(stage.get("waste_heat_multiplier", 1.0))
        result["waste_heat_availability"] = result["waste_heat_availability"].clip(0.0, 1.0)
    if "electricity_price" in result:
        result["electricity_price"] *= float(stage.get("electricity_price_multiplier", 1.0))
    if "grid_emission_factor" in result:
        result["grid_emission_factor"] *= float(stage.get("grid_emission_multiplier", 1.0))
    if "grid_availability" in result:
        result["grid_availability"] *= float(stage.get("grid_availability_multiplier", 1.0))
    if "renewable_power_available_kw" in result:
        result["renewable_power_available_kw"] *= float(stage.get("renewable_availability_multiplier", 1.0))
    if "chp_minimum_electricity_kw" in result:
        result["chp_minimum_electricity_kw"] *= float(stage.get("chp_minimum_electricity_multiplier", 1.0))
    return result


def _add_limits(config: dict[str, Any], cumulative: dict[str, Any]) -> None:
    sources = {item["name"]: item for item in config["sources"]}
    for name, amount in cumulative.get("sources_kw", {}).items():
        sources[name]["max_new_capacity_kw"] = float(sources[name].get("max_new_capacity_kw", 0.0)) + float(amount)
    storages = {item["name"]: item for item in config["storages"]}
    for name, amount in cumulative.get("storages_kwh", {}).items():
        storages[name]["max_new_capacity_kwh"] = float(storages[name].get("max_new_capacity_kwh", 0.0)) + float(amount)
    if "grid_connection_kw" in cumulative and config.get("grid_connection"):
        config["grid_connection"]["max_new_capacity_kw"] = float(
            config["grid_connection"].get("max_new_capacity_kw", 0.0)
        ) + float(cumulative["grid_connection_kw"])


def _merge_limits(cumulative: dict[str, Any], additions: dict[str, Any]) -> None:
    for group in ("sources_kw", "storages_kwh"):
        target = cumulative.setdefault(group, {})
        for name, amount in additions.get(group, {}).items():
            target[name] = float(target.get(name, 0.0)) + float(amount)
    if "grid_connection_kw" in additions:
        cumulative["grid_connection_kw"] = float(cumulative.get("grid_connection_kw", 0.0)) + float(
            additions["grid_connection_kw"]
        )


def _apply_existing_schedule(config: dict[str, Any], year: int) -> None:
    def scheduled_value(item: dict[str, Any], field: str) -> None:
        schedule = item.get(f"{field}_by_year", {})
        eligible = [(int(key), value) for key, value in schedule.items() if int(key) <= year]
        if eligible:
            item[field] = float(max(eligible, key=lambda pair: pair[0])[1])
        retirement = item.get("existing_retirement_year")
        if retirement is not None and year >= int(retirement):
            item[field] = 0.0

    for source in config["sources"]:
        scheduled_value(source, "existing_capacity_kw")
    for storage in config["storages"]:
        scheduled_value(storage, "existing_capacity_kwh")
    for pipe in config["pipes"]:
        scheduled_value(pipe, "existing_capacity_kw")
    if config.get("grid_connection"):
        scheduled_value(config["grid_connection"], "existing_capacity_kw")
    for node in config["nodes"]:
        if node.get("heat_metering"):
            scheduled_value(node["heat_metering"], "existing_coverage_fraction")


def _append_problem(master: LinearMILP, child: LinearMILP, prefix: str, objective_scale: float,
                    remove_objective: dict[int, float]) -> dict[int, int]:
    mapping: dict[int, int] = {}
    for index, name in enumerate(child.names):
        coefficient = (child.objective[index] - remove_objective.get(index, 0.0)) * objective_scale
        mapping[index] = master.add_var(
            f"{prefix}:{name}", lb=child.lower[index], ub=child.upper[index],
            integer=bool(child.integrality[index]), obj=coefficient,
        )
    for row, lower, upper in zip(child.rows, child.row_lower, child.row_upper):
        master.add_constraint({mapping[index]: value for index, value in row.items()}, lb=lower, ub=upper)
    return mapping


def _asset_key(category: str, raw_key: Any) -> tuple[str, str, str]:
    if category == "pipe_choice":
        asset, option = raw_key
        return category, str(asset), str(option)
    if category == "terminal_temperature_retrofit":
        return category, str(raw_key), "high_to_low"
    if category == "pipe_retrofit":
        return category, str(raw_key), "energy_efficiency"
    if category == "source_retirement":
        return category, str(raw_key), "decommission"
    return category, str(raw_key), "new"


def _asset_spec(config: dict[str, Any], key: tuple[str, str, str]) -> dict[str, Any]:
    category, asset, option = key
    if category == "heat_metering_coverage":
        node = next(row for row in config["nodes"] if str(row["name"]) == asset)
        item = node["heat_metering"]
        return {
            "gross_cost_per_unit": float(item.get("capex_yuan_per_coverage_fraction", 0.0)), "unit": "fraction",
            "lead_years": int(item.get("construction_lead_years", 0)),
            "lifetime_years": int(item.get("asset_lifetime_years", config["planning"]["asset_lifetime_years"])),
        }
    if category == "source_capacity":
        item = next(row for row in config["sources"] if str(row["name"]) == asset)
        return {
            "gross_cost_per_unit": float(item.get("capex_yuan_per_kw", 0.0)), "unit": "kW",
            "lead_years": int(item.get("construction_lead_years", 0)),
            "lifetime_years": int(item.get("asset_lifetime_years", config["planning"]["asset_lifetime_years"])),
        }
    if category == "biomass_inventory_capacity":
        item = next(row for row in config.get("biomass_supply_chains", []) if str(row["name"]) == asset)
        return {
            "gross_cost_per_unit": float(item.get("inventory_capex_yuan_per_kg", 0.0)), "unit": "kg",
            "lead_years": int(item.get("construction_lead_years", 0)),
            "lifetime_years": int(item.get("asset_lifetime_years", config["planning"]["asset_lifetime_years"])),
        }
    if category.startswith("microgrid_"):
        item = config["microgrid_islanding"]
        mapping = {
            "microgrid_renewable_capacity": ("renewable_capex_yuan_per_kw", "kW"),
            "microgrid_battery_power": ("battery_power_capex_yuan_per_kw", "kW"),
            "microgrid_battery_energy": ("battery_energy_capex_yuan_per_kwh", "kWh"),
            "microgrid_backup_capacity": ("backup_capex_yuan_per_kw", "kW"),
        }
        cost_key, unit = mapping[category]
        return {
            "gross_cost_per_unit": float(item.get(cost_key, 0.0)), "unit": unit,
            "lead_years": int(item.get("construction_lead_years", 0)),
            "lifetime_years": int(item.get("asset_lifetime_years", config["planning"]["asset_lifetime_years"])),
        }
    if category == "source_retirement":
        item = next(row for row in config["sources"] if str(row["name"]) == asset)
        return {
            "gross_cost_per_unit": float(item.get("decommissioning_cost_yuan_per_kw", 0.0)), "unit": "kW",
            "lead_years": int(item.get("decommissioning_lead_years", 0)),
            "lifetime_years": 1000,
        }
    if category == "storage_capacity":
        item = next(row for row in config["storages"] if str(row["name"]) == asset)
        return {
            "gross_cost_per_unit": float(item.get("capex_yuan_per_kwh", 0.0)), "unit": "kWh",
            "lead_years": int(item.get("construction_lead_years", 0)),
            "lifetime_years": int(item.get("asset_lifetime_years", config["planning"]["asset_lifetime_years"])),
        }
    if category == "pipe_choice":
        pipe = next(row for row in config["pipes"] if str(row["name"]) == asset)
        item = next(row for row in pipe["diameter_options"] if str(row["name"]) == option)
        return {
            "gross_cost_per_unit": float(item.get("capex_total_yuan", 0.0)), "unit": "binary",
            "lead_years": int(item.get("construction_lead_years", pipe.get("construction_lead_years", 0))),
            "lifetime_years": int(item.get("asset_lifetime_years", pipe.get(
                "asset_lifetime_years", config["planning"]["asset_lifetime_years"]
            ))),
        }
    if category == "pipe_retrofit":
        pipe = next(row for row in config["pipes"] if str(row["name"]) == asset)
        item = pipe["retrofit"]
        return {
            "gross_cost_per_unit": float(item.get("capex_total_yuan", 0.0)), "unit": "binary",
            "lead_years": int(item.get("construction_lead_years", 0)),
            "lifetime_years": int(item.get("asset_lifetime_years", config["planning"]["asset_lifetime_years"])),
        }
    if category == "terminal_temperature_retrofit":
        node = next(row for row in config["nodes"] if str(row["name"]) == asset)
        item = node["temperature_retrofit"]
        return {
            "gross_cost_per_unit": float(item.get("capex_total_yuan", 0.0)), "unit": "binary",
            "lead_years": int(item.get("construction_lead_years", 0)),
            "lifetime_years": int(item.get("asset_lifetime_years", config["planning"]["asset_lifetime_years"])),
        }
    if category == "grid_connection_capacity":
        item = config["grid_connection"]
        return {
            "gross_cost_per_unit": float(item.get("capex_yuan_per_kw", 0.0)), "unit": "kW",
            "lead_years": int(item.get("construction_lead_years", 0)),
            "lifetime_years": int(item.get("asset_lifetime_years", config["planning"]["asset_lifetime_years"])),
        }
    raise ValueError(f"未知投资资产: {key}")


def _subsidy(policy: dict[str, Any], key: tuple[str, str, str]) -> float:
    category, asset, option = key
    specification = policy.get("capex_subsidy_fraction", {}).get(category, 0.0)
    lookup = f"{asset}:{option}" if category == "pipe_choice" else asset
    value = specification.get(lookup, specification.get("default", 0.0)) if isinstance(specification, dict) else specification
    value = float(value)
    if not 0 <= value <= 1:
        raise ValueError(f"补贴比例不在 [0,1]: {key}")
    return value


def _present_value_factor(start_year: int, end_year: int, base_year: int, rate: float) -> float:
    return sum(1.0 / (1.0 + rate) ** (year - base_year) for year in range(start_year, end_year))


def _future_build_obligations(
    pathway: dict[str, Any], years: list[int], asset_specs: dict[tuple[str, str, str], dict[str, Any]],
    horizon_end: int,
) -> dict[tuple[int, tuple[str, str, str]], float]:
    """Explicitly committed quantities are the only permitted post-horizon builds.

    Each entry contains decision_year, category, asset, optional option and a
    positive value. It fixes the quantity rather than opening a speculative
    upper bound; uncommissioned works receive no residual credit.
    """
    obligations = {}
    for item in pathway.get("future_build_obligations", []):
        year = int(item["decision_year"])
        key = (str(item["category"]), str(item["asset"]), str(item.get("option", "new")))
        value = float(item["value"])
        if year not in years or key not in asset_specs:
            raise ValueError(f"未知远期建设义务年份或资产: {year}/{key}")
        if not np.isfinite(value) or value <= 0:
            raise ValueError("远期建设义务 value 必须为有限正数")
        if year + asset_specs[key]["lead_years"] < horizon_end:
            raise ValueError("future_build_obligations 仅用于终端结算点及以后投产的建设")
        pair = (years.index(year), key)
        if pair in obligations:
            raise ValueError(f"重复的远期建设义务: {year}/{key}")
        obligations[pair] = value
    return obligations


def solve_joint_multistage(
    config_path: str | Path,
    timeseries_path: str | Path,
    scenario_specs_path: str | Path,
    pathway_path: str | Path,
    feasibility_path: str | Path | None = None,
    time_limit: float = 300.0,
    mip_gap: float | None = None,
) -> JointMultiStageArtifacts:
    """Optimize construction vintages and representative stage operations.

    Operating years are [first_stage_year, horizon_end_year); H is a separate
    residual/debt settlement date. Residuals use the investor-funded capex and
    the bounded remaining-life fraction of commissioned assets. Post-horizon
    commissions are disabled except for fixed ``future_build_obligations``.
    Stage operations remain piecewise constant between the supplied stages.
    """
    base_config, base_frame = load_inputs(config_path, timeseries_path)
    feasibility_report = None
    if feasibility_path:
        feasibility = evaluate_feasibility(load_feasibility(feasibility_path))
        feasibility_report = feasibility.report
        base_config = apply_feasibility_exclusions(base_config, feasibility)
        if not feasibility.passed:
            failed_global = feasibility.report[
                (feasibility.report["status"] == "fail") & (feasibility.report["asset_refs"] == "")
            ]
            if not failed_global.empty:
                raise ValueError("存在未关联具体资产的强制工程准入失败，项目不得进入优化")

    scenario_spec = yaml.safe_load(Path(scenario_specs_path).read_text(encoding="utf-8"))
    pathway = yaml.safe_load(Path(pathway_path).read_text(encoding="utf-8"))
    stages = sorted(pathway["stages"], key=_stage_year)
    if not stages:
        raise ValueError("联合多阶段至少需要一个阶段")
    years = [_stage_year(stage) for stage in stages]
    base_year = years[0]
    horizon_end = int(pathway.get("horizon_end_year", years[-1] + int(pathway.get("last_stage_duration_years", 5))))
    rate = float(pathway.get("finance", {}).get("discount_rate", base_config["planning"]["discount_rate"]))
    if len(set(years)) != len(years) or horizon_end <= years[-1]:
        raise ValueError("阶段年份必须唯一，horizon_end_year 必须晚于最后阶段（经营年不含终点）")
    if not np.isfinite(rate) or rate < 0:
        raise ValueError("联合多阶段 discount_rate 必须为有限非负数")
    demand_columns = [column for node in base_config["nodes"] for column in node.get("demand_columns", {}).values()]

    stage_models: list[HeatPlanningModel] = []
    stage_configs: list[dict[str, Any]] = []
    cumulative_limits: dict[str, Any] = {}
    for stage in stages:
        _merge_limits(cumulative_limits, stage.get("additional_build_limits", {}))
        config = copy.deepcopy(base_config)
        _add_limits(config, cumulative_limits)
        _apply_existing_schedule(config, _stage_year(stage))
        policy = copy.deepcopy(stage.get("policy", {}))
        for financial_key in (
            "annualized_investment_budget_yuan", "public_subsidy_budget_yuan_per_year",
            "maximum_levelized_heat_cost_yuan_per_kwh", "legacy_annualized_capex_yuan",
        ):
            policy.pop(financial_key, None)
        config["policy"] = policy
        config["planning"]["risk"] = copy.deepcopy(stage.get("risk", scenario_spec.get("risk", {})))
        frame = _apply_environment(base_frame, demand_columns, stage)
        bundle = build_correlated_northern_scenario_bundle(
            frame, demand_columns, list(scenario_spec["scenarios"]),
            period_weight=float(scenario_spec.get("period_weight", 180.0)),
        )
        validate_inputs(config, bundle)
        model = HeatPlanningModel(config, bundle)
        model.build()
        _assert_complete_investment_registry(model)
        stage_models.append(model)
        stage_configs.append(config)

    master = LinearMILP()
    stage_mappings: list[dict[int, int]] = []
    for position, (stage, model) in enumerate(zip(stages, stage_models)):
        year = years[position]
        end = years[position + 1] if position + 1 < len(years) else horizon_end
        pv_factor = _present_value_factor(year, end, base_year, rate)
        stage_mappings.append(_append_problem(
            master, model.problem, str(year), pv_factor,
            remove_objective=dict(model.component_coefficients.get("annualized_capex", {})),
        ))

    stage_asset_variables: dict[tuple[int, tuple[str, str, str]], int] = {}
    all_assets: set[tuple[str, str, str]] = set()
    for position, model in enumerate(stage_models):
        for category in INVESTMENT_CATEGORIES:
            for raw_key, child_index in model.variables[category].items():
                key = _asset_key(category, raw_key)
                all_assets.add(key)
                stage_asset_variables[(position, key)] = stage_mappings[position][child_index]

    build_variables: dict[tuple[int, tuple[str, str, str]], int] = {}
    asset_specs = {key: _asset_spec(base_config, key) for key in all_assets}
    for key, spec in asset_specs.items():
        if spec["lead_years"] < 0 or spec["lifetime_years"] <= 0:
            raise ValueError(f"建设期必须非负、寿命必须为正: {key}")
        if not np.isfinite(spec["gross_cost_per_unit"]) or spec["gross_cost_per_unit"] < 0:
            raise ValueError(f"投资费用必须为有限非负数: {key}")
    obligations = _future_build_obligations(pathway, years, asset_specs, horizon_end)
    for key in sorted(all_assets):
        binary = asset_specs[key]["unit"] == "binary"
        maximum = max(
            master.upper[index] for (position, asset), index in stage_asset_variables.items() if asset == key
        )
        first_available = min(position for position, asset in stage_asset_variables if asset == key)
        for position, year in enumerate(years):
            if position < first_available:
                if (position, key) in obligations:
                    raise ValueError(f"远期建设义务早于资产可建设阶段: {year}/{key}")
                continue
            lower, upper = 0.0, (1.0 if binary else maximum)
            if year + asset_specs[key]["lead_years"] >= horizon_end:
                lower = obligations.get((position, key), 0.0)
                if lower > upper or (binary and lower not in (0.0, 1.0)):
                    raise ValueError(f"远期建设义务超过建设上限或不满足整数要求: {year}/{key}")
                upper = lower
            build_variables[(position, key)] = master.add_var(
                f"build[{year},{key[0]},{key[1]},{key[2]}]", lb=lower, ub=upper, integer=binary
            )
        if binary:
            master.add_constraint(
                {index: 1.0 for (position, asset), index in build_variables.items() if asset == key}, ub=1.0
            )

    # Commissioning, construction lead time and finite asset life.
    for (position, key), cumulative_index in stage_asset_variables.items():
        stage_year = years[position]
        spec = asset_specs[key]
        row = {cumulative_index: 1.0}
        for (build_position, asset), build_index in build_variables.items():
            if asset != key:
                continue
            decision_year = years[build_position]
            commission = decision_year + spec["lead_years"]
            retirement = commission + spec["lifetime_years"]
            if commission <= stage_year < retirement:
                row[build_index] = row.get(build_index, 0.0) - 1.0
        master.add_constraint(row, lb=0.0, ub=0.0)

    build_meta: dict[int, dict[str, Any]] = {}
    for (position, key), index in build_variables.items():
        year = years[position]
        stage = stages[position]
        spec = asset_specs[key]
        subsidy = _subsidy(stage.get("policy", {}), key)
        gross = spec["gross_cost_per_unit"]
        discount = 1.0 / (1.0 + rate) ** (year - base_year)
        private_capex = gross * (1.0 - subsidy)
        master.add_to_objective(index, private_capex * discount)
        commission = year + spec["lead_years"]
        retirement = commission + spec["lifetime_years"]
        remaining = max(0, retirement - horizon_end)
        salvage_fraction = min(1.0, remaining / spec["lifetime_years"])
        if commission >= horizon_end or key[0] == "source_retirement":
            salvage_fraction = 0.0
        # Only the investor-funded portion is recovered; subsidies cannot be
        # collected a second time through a gross-cost residual credit.
        salvage = private_capex * salvage_fraction
        master.add_to_objective(index, -salvage / (1.0 + rate) ** (horizon_end - base_year))
        build_meta[index] = {
            "decision_year": year, "commission_year": commission, "retirement_year": retirement,
            "category": key[0], "asset": key[1], "option": key[2], "unit": spec["unit"],
            "gross_cost_per_unit": gross, "subsidy_fraction": subsidy,
            "private_cost_per_unit": private_capex, "salvage_per_unit": salvage,
            "salvage_fraction": salvage_fraction,
            "heating_cost_allocation_fraction": heating_cost_allocation_fraction(base_config, *key),
            "future_build_obligation": (position, key) in obligations,
        }

    # Stage financial constraints use incremental builds and all active annuities.
    for position, (stage, model, mapping) in enumerate(zip(stages, stage_models, stage_mappings)):
        policy = stage.get("policy", {})
        stage_builds = {
            index: meta for index, meta in build_meta.items() if meta["decision_year"] == years[position]
        }
        if "annualized_investment_budget_yuan" in policy:
            master.add_constraint({
                index: meta["private_cost_per_unit"] * model.crf for index, meta in stage_builds.items()
            }, ub=float(policy["annualized_investment_budget_yuan"]))
        if "public_subsidy_budget_yuan_per_year" in policy:
            master.add_constraint({
                index: meta["gross_cost_per_unit"] * meta["subsidy_fraction"] * model.crf
                for index, meta in stage_builds.items()
            }, ub=float(policy["public_subsidy_budget_yuan_per_year"]))
        if "maximum_levelized_heat_cost_yuan_per_kwh" in policy:
            row: dict[int, float] = {}
            for component in HEATING_CASH_COMPONENTS:
                for child_index, coefficient in model.component_coefficients.get(component, {}).items():
                    row[mapping[child_index]] = row.get(mapping[child_index], 0.0) + coefficient
            for index, meta in build_meta.items():
                if meta["commission_year"] <= years[position] < meta["retirement_year"]:
                    row[index] = row.get(index, 0.0) + meta["private_cost_per_unit"] * model.crf * meta["heating_cost_allocation_fraction"]
            demand = model.metric_constants.get("demand_kwh", 0.0)
            legacy = float(policy.get("legacy_annualized_capex_yuan", 0.0))
            master.add_constraint(
                row, ub=float(policy["maximum_levelized_heat_cost_yuan_per_kwh"]) * demand - legacy
            )

    result = master.solve(
        mip_gap=float(
            mip_gap
            if mip_gap is not None
            else base_config["planning"].get("mip_gap", 0.005)
        ),
        time_limit=time_limit,
    )
    if result.x is None:
        raise RuntimeError(f"联合多阶段模型无可用解: {result.message}")
    values = np.asarray(result.x)
    joint_summary = {
        "solver_status": int(result.status),
        "solver_message": str(result.message),
        "objective_mode": "joint_multistage_present_value",
        "objective_value": float(result.fun),
        "joint_present_value_yuan": float(result.fun),
        "mip_gap": getattr(result, "mip_gap", None),
        "numerical_audit_scope": "joint_master",
        **master.audit_solution(values),
    }

    build_rows: list[dict[str, Any]] = []
    for index, meta in build_meta.items():
        amount = 0.0 if abs(values[index]) < 1e-8 else float(values[index])
        build_rows.append({
            **meta, "value": amount,
            "gross_capex_yuan": amount * meta["gross_cost_per_unit"],
            "public_subsidy_yuan": amount * meta["gross_cost_per_unit"] * meta["subsidy_fraction"],
            "private_capex_yuan": amount * meta["private_cost_per_unit"],
            "salvage_yuan": amount * meta["salvage_per_unit"],
        })
    build_columns = [
        "decision_year", "commission_year", "retirement_year", "category", "asset", "option", "unit",
        "gross_cost_per_unit", "subsidy_fraction", "private_cost_per_unit", "salvage_per_unit",
        "salvage_fraction", "heating_cost_allocation_fraction", "future_build_obligation",
        "value", "gross_capex_yuan", "public_subsidy_yuan", "private_capex_yuan", "salvage_yuan",
    ]
    build_schedule = pd.DataFrame(build_rows, columns=build_columns)

    summaries: list[dict[str, Any]] = []
    stage_cash_components: list[dict[str, float]] = []
    cumulative_rows: list[pd.DataFrame] = []
    dispatch_rows: list[pd.DataFrame] = []
    for position, (year, model, mapping) in enumerate(zip(years, stage_models, stage_mappings)):
        child_values = np.asarray([values[mapping[index]] for index in range(len(model.problem.names))])
        fake = SimpleNamespace(status=result.status, message=result.message, fun=float(result.fun), mip_gap=getattr(result, "mip_gap", np.nan))
        summary = model._make_summary(fake, child_values, "joint_multistage_total_cost")
        # Stage constraints are included in the complete master audit above.
        # This annualized stage ledger was not optimized independently: its
        # objective must not be compared to the master's discounted objective.
        summary["numerical_audit_scope"] = "stage_constraints_covered_by_joint_master"
        summary["numerical_objective_checked"] = False
        summary["numerical_objective_check_scope"] = "not_solved_independently; see joint summary"
        active = build_schedule[
            (build_schedule["commission_year"] <= year) & (build_schedule["retirement_year"] > year)
        ]
        annualized_private = float((active["private_capex_yuan"] * model.crf).sum())
        regulated_annualized = float((
            active["private_capex_yuan"] * active["heating_cost_allocation_fraction"] * model.crf
        ).sum())
        current_subsidy = build_schedule[build_schedule["decision_year"] == year]
        current_active = active[active["decision_year"] == year]
        current_private_annualized = float((
            current_active["private_capex_yuan"] * current_active["heating_cost_allocation_fraction"] * model.crf
        ).sum())
        external_legacy = float(stages[position].get("policy", {}).get("legacy_annualized_capex_yuan", 0.0))
        legacy_private_annualized = max(0.0, regulated_annualized - current_private_annualized) + external_legacy
        annualized_public = float((current_subsidy["public_subsidy_yuan"] * model.crf).sum())
        components = summary["components_yuan_per_year"]
        non_capex = sum(
            value for name, value in components.items() if name != "annualized_capex"
        )
        components["annualized_capex"] = annualized_private
        cash_components = {name: float(components.get(name, 0.0)) for name in HEATING_CASH_COMPONENTS}
        stage_cash_components.append(cash_components)
        heating_cash = sum(cash_components.values())
        summary["objective_yuan_per_year"] = non_capex + annualized_private
        summary["regulated_annualized_capex_yuan_per_year"] = regulated_annualized
        summary["shared_non_heating_capex_yuan_per_year"] = annualized_private - regulated_annualized
        summary["heating_cash_operating_cost_yuan_per_year"] = heating_cash
        summary["regulated_levelized_heat_cost_yuan_per_kwh"] = (
            heating_cash + regulated_annualized + external_legacy
        ) / max(summary["demand_kwh_per_year"], 1e-9)
        summary["stage_incremental_levelized_heat_cost_yuan_per_kwh"] = (
            heating_cash + current_private_annualized
        ) / max(summary["demand_kwh_per_year"], 1e-9)
        served_heat = max(0.0, summary["demand_kwh_per_year"] - summary["unserved_kwh_per_year"])
        served_cooling = max(0.0, summary["cooling_demand_kwh_per_year"] - summary["cooling_unserved_kwh_per_year"])
        served_energy = max(served_heat + served_cooling, 1e-9)
        summary["joint_service_levelized_cost_yuan_per_kwh"] = summary["objective_yuan_per_year"] / served_energy
        summary["energy_proportional_levelized_heat_cost_yuan_per_kwh"] = (
            annualized_private * served_heat / served_energy + heating_cash
        ) / max(served_heat, 1e-9)
        summary["energy_proportional_levelized_cooling_cost_yuan_per_kwh"] = (
            annualized_private * served_cooling / served_energy
            + components.get("cooling_operating_cost", 0.0) + components.get("cooling_carbon_cost", 0.0)
        ) / max(served_cooling, 1e-9)
        summary["legacy_annualized_capex_yuan_per_year"] = legacy_private_annualized
        summary["public_subsidy_yuan_per_year"] = annualized_public
        source_by_name = {str(item["name"]): item for item in stage_configs[position]["sources"]}
        existing_fossil = sum(
            float(item.get("existing_capacity_kw", 0.0))
            for item in source_by_name.values() if item.get("resource_kind") == "fossil"
        )
        active_source = active[active["category"] == "source_capacity"]
        new_fossil = sum(
            float(row["value"]) for _, row in active_source.iterrows()
            if source_by_name.get(str(row["asset"]), {}).get("resource_kind") == "fossil"
        )
        active_retirement = active[active["category"] == "source_retirement"]
        retired_fossil = sum(
            float(row["value"]) for _, row in active_retirement.iterrows()
            if source_by_name.get(str(row["asset"]), {}).get("resource_kind") == "fossil"
        )
        existing_waste = sum(
            float(item.get("existing_capacity_kw", 0.0))
            for item in source_by_name.values() if item.get("resource_kind") == "waste_heat"
        )
        new_waste = sum(
            float(row["value"]) for _, row in active_source.iterrows()
            if source_by_name.get(str(row["asset"]), {}).get("resource_kind") == "waste_heat"
        )
        summary["fossil_installed_capacity_kw"] = existing_fossil + new_fossil - retired_fossil
        summary["waste_heat_installed_capacity_kw"] = existing_waste + new_waste
        summary["retained_existing_backbone_capacity_kw"] = sum(
            float(item.get("existing_capacity_kw", 0.0)) for item in stage_configs[position]["pipes"]
        )
        summary["active_backbone_retrofit_count"] = float(
            active.loc[active["category"] == "pipe_retrofit", "value"].sum()
        )
        summary["active_storage_capacity_kwh"] = sum(
            float(item.get("existing_capacity_kwh", 0.0)) for item in stage_configs[position]["storages"]
        ) + float(active.loc[active["category"] == "storage_capacity", "value"].sum())
        summaries.append({
            "stage": year,
            **{key: value for key, value in summary.items() if not isinstance(value, dict)},
            **{f"component_{key}_yuan_per_year": value for key, value in components.items()},
        })
        investment = model._make_investments(child_values)
        investment.insert(0, "stage", year)
        cumulative_rows.append(investment)
        dispatch = model._make_dispatch(child_values)
        dispatch.insert(0, "stage", year)
        dispatch_rows.append(dispatch)

    stage_summary = pd.DataFrame(summaries)
    cumulative = pd.concat(cumulative_rows, ignore_index=True)
    dispatch = pd.concat(dispatch_rows, ignore_index=True)
    hydraulic = hydraulic_thermal_postcheck(stage_configs[-1], cumulative[cumulative["stage"] == years[-1]], dispatch[dispatch["stage"] == years[-1]])

    finance_artifacts = None
    if pathway.get("finance"):
        annual_rows = []
        for year in range(base_year, horizon_end):
            stage_position = max(index for index, stage_year in enumerate(years) if stage_year <= year)
            summary = summaries[stage_position]
            cash = stage_cash_components[stage_position]
            year_build = build_schedule[build_schedule["decision_year"] == year]
            annual_rows.append({
                "year": year,
                "is_terminal": False,
                "demand_kwh": summary["demand_kwh_per_year"],
                "operating_cost_yuan": sum(cash.values()),
                **{f"cash_{name}_yuan": value for name, value in cash.items()},
                "modeled_fixed_om_cost_yuan": cash["fixed_om_cost"],
                "gross_capex_yuan": float(year_build["gross_capex_yuan"].sum()),
                "subsidy_yuan": float(year_build["public_subsidy_yuan"].sum()),
                "residual_value_yuan": 0.0,
            })
        # H is a settlement instant, not an additional operating year.
        annual_rows.append({
            "year": horizon_end, "is_terminal": True, "demand_kwh": 0.0,
            "operating_cost_yuan": 0.0,
            **{f"cash_{name}_yuan": 0.0 for name in HEATING_CASH_COMPONENTS},
            "modeled_fixed_om_cost_yuan": 0.0,
            "gross_capex_yuan": 0.0, "subsidy_yuan": 0.0,
            "residual_value_yuan": float(build_schedule["salvage_yuan"].sum()),
        })
        annual_profile = pd.DataFrame(annual_rows)
        finance_spec = copy.deepcopy(pathway["finance"])
        finance_spec["break_even_tariff_yuan_per_kwh"] = break_even_tariff(annual_profile, finance_spec)
        finance_artifacts = evaluate_project_finance(annual_profile, finance_spec)
        finance_artifacts.metrics["break_even_tariff_yuan_per_kwh"] = finance_spec["break_even_tariff_yuan_per_kwh"]

    return JointMultiStageArtifacts(
        result=result, stage_summary=stage_summary, build_schedule=build_schedule,
        cumulative_investments=cumulative, dispatch=dispatch, finance=finance_artifacts,
        hydraulic_postcheck=hydraulic, feasibility_report=feasibility_report,
        summary=joint_summary,
    )
