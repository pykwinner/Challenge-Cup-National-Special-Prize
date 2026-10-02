from __future__ import annotations

from pathlib import Path
import math
from typing import Any

import pandas as pd
import yaml

from .policy_alignment import validate_policy_config


def _deep_merge(base: Any, override: Any) -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        result = dict(base)
        for key, value in override.items():
            result[key] = _deep_merge(result[key], value) if key in result else value
        return result
    if isinstance(base, list) and isinstance(override, list):
        if all(isinstance(item, dict) and "name" in item for item in base + override):
            result = [dict(item) for item in base]
            positions = {str(item["name"]): index for index, item in enumerate(result)}
            for item in override:
                name = str(item["name"])
                if item.get("_delete", False):
                    if name in positions:
                        result.pop(positions[name])
                        positions = {str(row["name"]): index for index, row in enumerate(result)}
                    continue
                if name in positions:
                    result[positions[name]] = _deep_merge(result[positions[name]], item)
                else:
                    result.append(dict(item))
                    positions[name] = len(result) - 1
            return result
        return list(override)
    return override


def load_config(config_path: str | Path) -> dict[str, Any]:
    config_path = Path(config_path)
    with config_path.open("r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    parent = config.pop("extends", None)
    if parent:
        parent_path = (config_path.parent / str(parent)).resolve()
        config = _deep_merge(load_config(parent_path), config)
    return config


def load_inputs(config_path: str | Path, timeseries_path: str | Path) -> tuple[dict[str, Any], pd.DataFrame]:
    config_path = Path(config_path)
    timeseries_path = Path(timeseries_path)
    config = load_config(config_path)
    frame = pd.read_csv(timeseries_path)
    validate_inputs(config, frame)
    return config, frame


def validate_electricity_module_compatibility(config: dict[str, Any]) -> None:
    """Reject overlapping electricity ledgers until they share one balance."""
    if config.get("renewable_electricity") and config.get("microgrid_islanding"):
        raise ValueError(
            "暂不支持同时启用 renewable_electricity（独立绿电）和 "
            "microgrid_islanding（微电网）：两套模块尚未共用电力平衡，"
            "会重复抵扣购电费用与排放；请仅启用其中一套。"
        )


def validate_inputs(config: dict[str, Any], frame: pd.DataFrame) -> None:
    required_sections = {"planning", "grades", "nodes", "sources", "pipes", "storages"}
    missing = required_sections - set(config)
    if missing:
        raise ValueError(f"配置文件缺少章节: {sorted(missing)}")

    validate_electricity_module_compatibility(config)
    validate_policy_config(config)
    if config.get("building_thermal",{}).get("enabled",False):
        from .building_thermal import validate_building_thermal_inputs
        validate_building_thermal_inputs(config,frame)

    if "time" not in frame.columns:
        raise ValueError("时序数据必须含 time 列")
    if "scenario" in frame.columns:
        if frame[["scenario", "time"]].duplicated().any():
            raise ValueError("同一 scenario 内的 time 不允许重复")
    elif frame["time"].duplicated().any():
        raise ValueError("time 列不允许重复")

    if "scenario_probability" in frame.columns:
        if "scenario" not in frame.columns:
            raise ValueError("使用 scenario_probability 时必须提供 scenario 列")
        probabilities = frame.groupby("scenario", sort=False)["scenario_probability"].agg(["min", "max"])
        if not (probabilities["min"] == probabilities["max"]).all():
            raise ValueError("同一场景内的 scenario_probability 必须一致")
        if (probabilities["min"] <= 0).any():
            raise ValueError("scenario_probability 必须为正")
        if abs(float(probabilities["min"].sum()) - 1.0) > 1e-8:
            raise ValueError("各场景 scenario_probability 之和必须为 1")
    if "period_weight" in frame.columns and (frame["period_weight"].astype(float) <= 0).any():
        raise ValueError("period_weight 必须为正")

    names: dict[str, list[str]] = {}
    for section in ("grades", "nodes", "sources", "pipes", "storages"):
        values = [str(item["name"]) for item in config[section]]
        if len(values) != len(set(values)):
            raise ValueError(f"{section} 中存在重复 name")
        names[section] = values

    node_set = set(names["nodes"])
    grade_set = set(names["grades"])
    for source in config["sources"]:
        if source["node"] not in node_set:
            raise ValueError(f"热源 {source['name']} 引用了未知节点")
        unknown = set(source.get("allowed_grades", [])) - grade_set
        if unknown:
            raise ValueError(f"热源 {source['name']} 引用了未知品位 {sorted(unknown)}")
        for column_key in (
            "availability_column", "source_temp_column", "minimum_electricity_output_column",
            "relative_humidity_column",
        ):
            column = source.get(column_key)
            if column and column not in frame.columns:
                raise ValueError(f"热源 {source['name']} 需要时序列 {column}")

    for node in config["nodes"]:
        for grade, column in node.get("demand_columns", {}).items():
            if grade not in grade_set:
                raise ValueError(f"节点 {node['name']} 引用了未知品位 {grade}")
            if column not in frame.columns:
                raise ValueError(f"节点 {node['name']} 需要时序列 {column}")
        optional_columns = [
            node.get("intermittent_heating", {}).get("schedule_column"),
            node.get("user_behavior", {}).get("multiplier_column"),
            node.get("user_behavior", {}).get("scenario_multiplier_weight_column"),
        ]
        for column in filter(None, optional_columns):
            if column not in frame.columns:
                raise ValueError(f"节点 {node['name']} 需要时序列 {column}")
        metering = node.get("heat_metering", {})
        if metering:
            existing = float(metering.get("existing_coverage_fraction", 0.0))
            maximum_new = float(metering.get("max_new_coverage_fraction", 0.0))
            saving = float(metering.get("saving_fraction_at_full_coverage", 0.0))
            fixed_new = metering.get("fixed_new_coverage_fraction")
            if not all(math.isfinite(value) for value in (existing, maximum_new, saving)):
                raise ValueError(f"节点 {node['name']} 的计量参数必须为有限数值")
            if not 0.0 <= existing <= 1.0:
                raise ValueError(f"节点 {node['name']} 的既有计量覆盖率必须位于 [0,1]")
            if maximum_new < 0.0 or existing + maximum_new > 1.0 + 1e-9:
                raise ValueError(f"节点 {node['name']} 的新增计量覆盖率边界无效")
            if not 0.0 <= saving <= 1.0:
                raise ValueError(f"节点 {node['name']} 的计量节热率必须位于 [0,1]")
            if fixed_new is not None:
                fixed_new = float(fixed_new)
                if not math.isfinite(fixed_new):
                    raise ValueError(f"节点 {node['name']} 的固定新增计量覆盖率必须为有限数值")
                if fixed_new < 0.0 or existing + fixed_new > 1.0 + 1e-9:
                    raise ValueError(f"节点 {node['name']} 的固定新增计量覆盖率无效")
            metering_capex = float(
                metering.get("capex_yuan_per_coverage_fraction", 0.0)
            )
            if not math.isfinite(metering_capex) or metering_capex < 0.0:
                raise ValueError(f"节点 {node['name']} 的计量改造投资不得为负")

    heat_tariff = config.get("heat_tariff", {})
    if heat_tariff:
        for field in (
            "variable_tariff_yuan_per_kwh",
            "fixed_charge_yuan_per_household_year",
        ):
            value = float(heat_tariff.get(field, 0.0))
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"heat_tariff.{field} 不得为负")
        households = float(heat_tariff.get("household_count", 0.0))
        if not math.isfinite(households) or households < 0.0:
            raise ValueError("heat_tariff.household_count 不得为负")
        if (
            "maximum_average_household_heat_bill_yuan_per_year"
            in config.get("policy", {})
            and households <= 0.0
        ):
            raise ValueError("居民热费可负担性约束需要正的 heat_tariff.household_count")

    for pipe in config["pipes"]:
        if pipe["from"] not in node_set or pipe["to"] not in node_set:
            raise ValueError(f"管道 {pipe['name']} 引用了未知节点")

    for storage in config["storages"]:
        if storage["node"] not in node_set or storage["grade"] not in grade_set:
            raise ValueError(f"储热 {storage['name']} 的节点或品位不存在")

    for column in ("electricity_price", "grid_emission_factor"):
        if column not in frame.columns:
            raise ValueError(f"时序数据缺少 {column} 列")
    price_column = str(config["planning"].get("electricity_price_column", "electricity_price"))
    if price_column not in frame.columns:
        raise ValueError(f"规划配置需要电价时序列 {price_column}")
    renewable = config.get("renewable_electricity")
    if renewable:
        column = renewable.get("availability_column")
        if not column or column not in frame.columns:
            raise ValueError(f"可再生电力模块需要时序列 {column}")
    grid = config.get("grid_connection", {})
    if grid.get("availability_column") and grid["availability_column"] not in frame.columns:
        raise ValueError(f"电网可用率模块需要时序列 {grid['availability_column']}")
    microgrid = config.get("microgrid_islanding", {})
    renewable_column = microgrid.get("renewable_availability_column")
    if renewable_column and renewable_column not in frame.columns:
        raise ValueError(f"微网可再生电力模块需要时序列 {renewable_column}")
    for field in (
        "renewable_firm_output_factor",
        "operational_renewable_output_factor",
        "critical_electric_load_fraction",
    ):
        if field in microgrid and not 0.0 <= float(microgrid[field]) <= 1.0:
            raise ValueError(f"微网参数 {field} 必须位于 [0,1]")
    for field in (
        "battery_charge_efficiency",
        "battery_discharge_efficiency",
    ):
        if field in microgrid and not 0.0 < float(microgrid[field]) <= 1.0:
            raise ValueError(f"微网参数 {field} 必须位于 (0,1]")
    if not 0.0 <= float(microgrid.get("battery_self_loss_per_hour", 0.0)) < 1.0:
        raise ValueError("微网电池每小时自放电率必须位于 [0,1)")
    for chain in config.get("biomass_supply_chains", []):
        column = chain.get("delivery_availability_column")
        if column and column not in frame.columns:
            raise ValueError(f"生物质供应链 {chain['name']} 需要时序列 {column}")

    cooling = config.get("multi_energy_services", {}).get("cooling", {})
    if cooling:
        demand_columns = cooling.get("demand_columns", {})
        unknown_nodes = set(demand_columns) - node_set
        if unknown_nodes:
            raise ValueError(f"供冷负荷引用了未知节点: {sorted(unknown_nodes)}")
        for node, column in demand_columns.items():
            if column not in frame.columns:
                raise ValueError(f"供冷节点 {node} 需要时序列 {column}")
        unknown_sources = set(cooling.get("sources", {})) - set(names["sources"])
        if unknown_sources:
            raise ValueError(f"供冷模块引用了未知热源: {sorted(unknown_sources)}")
        for source_name, raw_spec in cooling.get("sources", {}).items():
            specification = raw_spec if isinstance(raw_spec, dict) else {"eer": raw_spec}
            if float(specification.get("eer", 3.5)) <= 0:
                raise ValueError(f"供冷设备 {source_name} 的 EER 必须为正")
            balance = specification.get("ground_thermal_balance", {})
            if balance:
                if "enforce" in balance and not isinstance(balance["enforce"], bool):
                    raise ValueError(f"供冷设备 {source_name} 的地源热平衡 enforce 必须为布尔值")
                minimum = float(balance.get("minimum_extraction_to_rejection_ratio", 0.8))
                maximum = float(balance.get("maximum_extraction_to_rejection_ratio", 1.25))
                if minimum <= 0 or maximum < minimum:
                    raise ValueError(f"供冷设备 {source_name} 的地源热平衡区间无效")
