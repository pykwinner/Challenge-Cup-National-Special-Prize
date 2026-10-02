from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

import numpy as np
import pandas as pd

from .accounting import HEATING_CASH_COMPONENTS
from .io import load_inputs, validate_electricity_module_compatibility
from .linear import LinearMILP
from .physical_extensions import (
    EventStateBoundary, add_shared_heat_resources,
    add_monthly_electricity_tariff, extension_summary,
    add_resource_firm_portfolios, add_firm_resource_limits,
)
from .building_thermal import add_building_thermal_constraints, building_heat_reference, validate_building_thermal_inputs


@dataclass
class SolveArtifacts:
    result: Any
    values: np.ndarray
    summary: dict[str, Any]
    investments: pd.DataFrame
    dispatch: pd.DataFrame
    building_trajectory: pd.DataFrame | None = None
    building_comfort: pd.DataFrame | None = None


class HeatPlanningModel:
    """Small but complete source-network-load-storage MILP baseline."""

    def __init__(self, config: dict[str, Any], timeseries: pd.DataFrame):
        self.config = deepcopy(config)
        validate_electricity_module_compatibility(self.config)
        self.ts = timeseries.reset_index(drop=True).copy()
        self.problem = LinearMILP()
        self.variables: dict[str, dict[Any, int]] = defaultdict(dict)
        self.metric_coefficients: dict[str, dict[int, float]] = defaultdict(dict)
        self.metric_constants: dict[str, float] = defaultdict(float)
        self.component_coefficients: dict[str, dict[int, float]] = defaultdict(dict)
        self.scenario_cost_coefficients: dict[str, dict[int, float]] = defaultdict(dict)
        self.scenario_metric_coefficients: dict[str, dict[str, dict[int, float]]] = defaultdict(
            lambda: defaultdict(dict)
        )
        self.scenario_metric_constants: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        self._cop_cache: dict[tuple[str, str, int], float] = {}
        self._demand_cache: dict[tuple[str, str, int], float] = {}
        self._electric_load_terms: dict[int, dict[int, float]] = defaultdict(dict)
        self._finalized_electric_load_terms: dict[int, tuple[tuple[int, float], ...]] | None = None
        self._built = False

        self.grades = {x["name"]: x for x in self.config["grades"]}
        self.nodes = {x["name"]: x for x in self.config["nodes"]}
        self.sources = {x["name"]: x for x in self.config["sources"]}
        self.pipes = {x["name"]: x for x in self.config["pipes"]}
        self.storages = {x["name"]: x for x in self.config["storages"]}
        self.times = list(range(len(self.ts)))

        planning = self.config["planning"]
        rate = float(planning["discount_rate"])
        years = int(planning["asset_lifetime_years"])
        self.crf = rate * (1 + rate) ** years / ((1 + rate) ** years - 1)
        self.day_weight = float(planning.get("representative_day_weight", 1.0))
        self.dt = float(planning.get("time_step_hours", 1.0))
        if "scenario" not in self.ts.columns:
            self.ts["scenario"] = "base"
        self.ts["scenario"] = self.ts["scenario"].astype(str)
        self.scenarios = list(dict.fromkeys(self.ts["scenario"].tolist()))
        self.scenario_times = {
            scenario: self.ts.index[self.ts["scenario"] == scenario].tolist() for scenario in self.scenarios
        }
        if "scenario_probability" not in self.ts.columns:
            probability = 1.0 / len(self.scenarios)
            self.ts["scenario_probability"] = probability
        self.scenario_probabilities: dict[str, float] = {}
        for scenario, times in self.scenario_times.items():
            values = self.ts.loc[times, "scenario_probability"].astype(float)
            if values.nunique() != 1:
                raise ValueError(f"场景 {scenario} 的 scenario_probability 必须保持一致")
            self.scenario_probabilities[scenario] = float(values.iloc[0])
        probability_sum = sum(self.scenario_probabilities.values())
        if not np.isclose(probability_sum, 1.0, atol=1e-8):
            raise ValueError(f"场景概率之和必须为1，当前为 {probability_sum:.8f}")
        if "period_weight" not in self.ts.columns:
            self.ts["period_weight"] = self.day_weight
        # Scenario and representative-period weights are read for every cost
        # and KPI coefficient.  Freeze their scalar values once instead of
        # performing hundreds of thousands of pandas label lookups in build().
        # The operation order below matches the former helper implementation.
        self._scenario_by_time = tuple(self.ts["scenario"].astype(str).tolist())
        self._period_weight_by_time = tuple(
            float(value) for value in self.ts["period_weight"].tolist()
        )
        self._physical_weight_by_time = tuple(
            self.dt * weight for weight in self._period_weight_by_time
        )
        self._op_weight_by_time = tuple(
            self._physical_weight_by_time[t]
            * self.scenario_probabilities[self._scenario_by_time[t]]
            for t in self.times
        )
        self._event_weight_by_time = tuple(
            self._period_weight_by_time[t]
            * self.scenario_probabilities[self._scenario_by_time[t]]
            for t in self.times
        )
        self.boundary_groups: dict[str, list[int]] = {}
        self.time_boundary_group: dict[int, str] = {}
        boundary_column = "boundary_group" if "boundary_group" in self.ts.columns else "scenario"
        for group in dict.fromkeys(self.ts[boundary_column].astype(str).tolist()):
            self.boundary_groups[group] = self.ts.index[self.ts[boundary_column].astype(str) == group].tolist()
            for t in self.boundary_groups[group]:
                self.time_boundary_group[t] = group
        self.time_predecessor: dict[int, int] = {}
        for times in self.boundary_groups.values():
            for position, t in enumerate(times):
                self.time_predecessor[t] = times[position - 1]
        self.carbon_price = float(planning.get("carbon_price_yuan_per_kg", 0.0))
        self.ambient_reference_c = float(planning.get("ambient_reference_c", 0.0))
        self.policy = self.config.get("policy", {})
        # Model inputs are frozen after construction.  Per-column snapshots
        # preserve each dtype (including string/object columns) and avoid hot
        # pandas scalar lookups.  Read-only copies also make the cache contract
        # explicit if callers later mutate the public reporting frame ``ts``.
        self._timeseries_values: dict[str, np.ndarray] = {}
        for column in self.ts.columns:
            values = self.ts[column].to_numpy(copy=True)
            values.setflags(write=False)
            self._timeseries_values[str(column)] = values
        self.building_inputs = validate_building_thermal_inputs(
            self.config, self.ts, dt_hours=self.dt, boundary_groups=self.boundary_groups,
        )
        self.building_thermal = None
        self.building_reference = None
        self.rc_buildings_by_node_grade: dict[tuple[str, str], list[Any]] = defaultdict(list)
        if self.building_inputs is not None:
            self.building_reference = building_heat_reference(
                self.building_inputs,
                design_outdoor_temperature_c=self.config["planning"].get("design_outdoor_temperature_c"),
            )
            for building in self.building_inputs.buildings:
                self.rc_buildings_by_node_grade[building.node, building.heat_grade].append(building)
            for node_name, grade_name in self.rc_buildings_by_node_grade:
                node = self.nodes[node_name]
                if node.get("temperature_retrofit"):
                    raise ValueError("RC buildings require an explicit fixed heat grade; legacy temperature retrofit is not a delivered-heat allocation model")
                if float(node.get("heat_metering", {}).get("saving_fraction_at_full_coverage", 0.0)) != 0:
                    raise ValueError("RC buildings cannot also use fixed heat-metering saving fractions")

    @classmethod
    def from_files(cls, config_path: str | Path, timeseries_path: str | Path) -> "HeatPlanningModel":
        return cls(*load_inputs(config_path, timeseries_path))

    def _record_component(self, name: str, index: int, coefficient: float) -> None:
        self.component_coefficients[name][index] = self.component_coefficients[name].get(index, 0.0) + coefficient
        self.problem.add_to_objective(index, coefficient)

    def _subsidy_fraction(self, category: str, asset: str) -> float:
        specification = self.policy.get("capex_subsidy_fraction", {}).get(category, 0.0)
        if isinstance(specification, dict):
            value = specification.get(asset, specification.get("default", 0.0))
        else:
            value = specification
        fraction = float(value)
        if not 0.0 <= fraction <= 1.0:
            raise ValueError(f"{category}/{asset} 的补贴比例必须位于 [0,1]")
        return fraction

    def _record_investment_cost(self, index: int, category: str, asset: str, gross_cost: float) -> None:
        fraction = self._subsidy_fraction(category, asset)
        allocation_specification = self.config["planning"].get(
            "heating_cost_allocation_fraction", {}
        ).get(category, 1.0)
        if isinstance(allocation_specification, dict):
            allocation = float(
                allocation_specification.get(
                    asset, allocation_specification.get("default", 1.0)
                )
            )
        else:
            allocation = float(allocation_specification)
        if not 0.0 <= allocation <= 1.0:
            raise ValueError(f"{category}/{asset} 的供热成本分摊比例必须位于 [0,1]")
        annualized_gross = float(gross_cost) * self.crf
        annualized_net = annualized_gross * (1.0 - fraction)
        self._record_component("annualized_capex", index, annualized_net)
        self._record_metric(
            "regulated_annualized_capex_yuan_per_year",
            index,
            annualized_net * allocation,
        )
        self._record_metric(
            "shared_non_heating_capex_yuan_per_year",
            index,
            annualized_net * (1.0 - allocation),
        )
        self._record_metric("public_subsidy_yuan_per_year", index, annualized_gross * fraction)

    def _scenario(self, t: int) -> str:
        return self._scenario_by_time[t]

    def _period_weight(self, t: int) -> float:
        return self._period_weight_by_time[t]

    def _physical_weight(self, t: int) -> float:
        return self._physical_weight_by_time[t]

    def _op_weight(self, t: int) -> float:
        return self._op_weight_by_time[t]

    def _event_weight(self, t: int) -> float:
        return self._event_weight_by_time[t]

    def _record_time_component(self, name: str, index: int, rate: float, t: int, event: bool = False) -> None:
        expected_weight = (
            self._event_weight_by_time[t]
            if event
            else self._op_weight_by_time[t]
        )
        physical_weight = (
            self._period_weight_by_time[t]
            if event
            else self._physical_weight_by_time[t]
        )
        expected_coefficient = rate * expected_weight
        components = self.component_coefficients[name]
        components[index] = components.get(index, 0.0) + expected_coefficient
        self.problem.add_to_objective(index, expected_coefficient)
        scenario = self._scenario_by_time[t]
        coefs = self.scenario_cost_coefficients[scenario]
        coefs[index] = coefs.get(index, 0.0) + rate * physical_weight

    def _record_time_metric(self, name: str, index: int, rate: float, t: int) -> None:
        expected_coefficient = rate * self._op_weight_by_time[t]
        metrics = self.metric_coefficients[name]
        metrics[index] = metrics.get(index, 0.0) + expected_coefficient
        scenario = self._scenario_by_time[t]
        coefs = self.scenario_metric_coefficients[scenario][name]
        coefs[index] = (
            coefs.get(index, 0.0)
            + rate * self._physical_weight_by_time[t]
        )

    def _record_metric(self, name: str, index: int, coefficient: float) -> None:
        self.metric_coefficients[name][index] = self.metric_coefficients[name].get(index, 0.0) + coefficient

    @staticmethod
    def _add(coefs: dict[int, float], idx: int, value: float) -> None:
        coefs[idx] = coefs.get(idx, 0.0) + value

    @staticmethod
    def _clip_scalar(value: float, lower: float, upper: float) -> float:
        """Scalar equivalent of ``np.clip`` without NumPy dispatch overhead."""
        scalar = float(value)
        minimum = float(lower)
        maximum = float(upper)
        if minimum > maximum:
            return maximum
        if scalar < minimum:
            return minimum
        if scalar > maximum:
            return maximum
        # NaN compares false above and is returned unchanged, matching np.clip.
        return scalar

    def _exergy_factor(self, temperature_c: float) -> float:
        t0 = self.ambient_reference_c + 273.15
        t = temperature_c + 273.15
        return max(0.0, 1.0 - t0 / t)

    def _electricity_price(self, t: int) -> float:
        column = str(
            self.config["planning"].get("electricity_price_column", "electricity_price")
        )
        return float(self._ts_value(t, column))

    def _ts_value(self, t: int, column: str) -> Any:
        """Return one immutable model-input scalar without pandas indexing."""
        return self._timeseries_values[str(column)][int(t)]

    def _compute_cop_uncached(
        self,
        source: dict[str, Any],
        grade: dict[str, Any],
        t: int,
    ) -> float:
        source_temp = float(source.get("source_temp_c", 10.0))
        if source.get("source_temp_column"):
            source_temp = float(self._ts_value(t, source["source_temp_column"]))
        supply_k = float(grade["temperature_c"]) + 273.15
        source_k = source_temp + 273.15
        lift = max(supply_k - source_k, 4.0)
        raw = float(source.get("carnot_efficiency", 0.45)) * supply_k / lift
        band = source.get("defrost_temperature_band_c")
        if band and float(band[0]) <= source_temp <= float(band[1]):
            raw *= float(source.get("defrost_cop_multiplier", 1.0))
        humidity_column = source.get("relative_humidity_column")
        if humidity_column and humidity_column in self.ts.columns:
            humidity = float(self._ts_value(t, humidity_column))
            frost_threshold = float(source.get("frost_humidity_threshold", 0.75))
            if band and float(band[0]) <= source_temp <= float(band[1]) and humidity > frost_threshold:
                humidity_loss = float(source.get("humidity_defrost_loss_per_unit", 0.30))
                minimum = float(source.get("humidity_defrost_min_multiplier", 0.72))
                raw *= max(minimum, 1.0 - humidity_loss * (humidity - frost_threshold))
        return self._clip_scalar(
            raw, source.get("cop_min", 1.2), source.get("cop_max", 6.0)
        )

    def _cop(self, source: dict[str, Any], grade: dict[str, Any], t: int) -> float:
        key = (str(source["name"]), str(grade["name"]), int(t))
        if key not in self._cop_cache:
            self._cop_cache[key] = self._compute_cop_uncached(source, grade, t)
        return self._cop_cache[key]

    def _compute_demand_uncached(
        self,
        node: dict[str, Any],
        column: str,
        t: int,
    ) -> float:
        # RC reference demand is a weather/comfort steady-state benchmark,
        # never a second fixed service equality. Actual delivered heat is a
        # decision variable and is exported/settled separately.
        for grade, configured_column in node.get("demand_columns", {}).items():
            buildings = self.rc_buildings_by_node_grade.get((str(node["name"]), grade), [])
            if str(configured_column) == str(column) and buildings:
                return self.building_reference.node_grade_reference_kw[str(node["name"]), grade, t]
        demand = float(self._ts_value(t, column))
        intermittent = node.get("intermittent_heating", {})
        schedule_column = intermittent.get("schedule_column")
        if schedule_column:
            occupancy = self._clip_scalar(
                self._ts_value(t, schedule_column), 0.0, 1.0
            )
            setback = float(intermittent.get("setback_fraction", 0.35))
            demand *= setback + (1.0 - setback) * occupancy
        behavior = node.get("user_behavior", {})
        if behavior.get("multiplier_column"):
            demand *= float(self._ts_value(t, behavior["multiplier_column"]))
        scenario_multiplier = float(
            behavior.get("scenario_multipliers", {}).get(self._scenario(t), 1.0)
        )
        scenario_weight_column = behavior.get("scenario_multiplier_weight_column")
        if scenario_weight_column:
            scenario_weight = self._clip_scalar(
                self._ts_value(t, str(scenario_weight_column)), 0.0, 1.0
            )
            scenario_multiplier = 1.0 + (scenario_multiplier - 1.0) * scenario_weight
        demand *= scenario_multiplier
        return max(0.0, demand)

    def _demand_value(self, node: dict[str, Any], column: str, t: int) -> float:
        key = (str(node["name"]), str(column), int(t))
        if key not in self._demand_cache:
            self._demand_cache[key] = self._compute_demand_uncached(
                node, str(column), int(t)
            )
        return self._demand_cache[key]

    def _building_flex_firm_capacity(self) -> float:
        specification = self.config["planning"].get("firm_building_flexibility", {})
        if not specification or not bool(specification.get("enabled", False)):
            return 0.0
        duration = float(specification.get(
            "duration_hours",
            self.config["planning"].get("critical_peak_duration_hours", 1.0),
        ))
        if duration <= 0:
            raise ValueError("firm_building_flexibility.duration_hours 必须为正")
        availability = float(specification.get("availability_factor", 1.0))
        if not 0.0 <= availability <= 1.0:
            raise ValueError("firm_building_flexibility.availability_factor 必须位于 [0,1]")
        shift_fraction = float(self.config.get("demand_response", {}).get(
            "max_shift_fraction", 0.0
        ))
        total = 0.0
        for node in self.nodes.values():
            for grade_name, column in node.get("demand_columns", {}).items():
                if (str(node["name"]), grade_name) in self.rc_buildings_by_node_grade:
                    continue
                inertia = node.get("thermal_inertia", {}).get(grade_name)
                if not inertia or shift_fraction <= 0:
                    continue
                thermal_capacity = float(inertia["thermal_capacity_kwh_per_c"])
                occupied_deviation = float(inertia.get(
                    "occupied_max_temperature_deviation_c",
                    inertia.get("max_temperature_deviation_c", 1.0),
                ))
                energy_limited_power = thermal_capacity * occupied_deviation / duration
                peak_load = max(
                    (self._demand_value(node, column, t) for t in self.times),
                    default=0.0,
                )
                demand_response_power = shift_fraction * peak_load
                total += min(energy_limited_power, demand_response_power)
        return availability * total

    def design_peak_demand(self, node_name: str | None = None) -> float:
        """Common conservative capacity screen for legacy and RC services.

        RC buildings use the largest required temperature at the declared
        design outdoor temperature, without crediting unguaranteed gains or
        thermal inventory. Fixed services retain their coincident peak.
        """
        selected = [node for name,node in self.nodes.items() if node_name is None or name == node_name]
        fixed_peak = max((sum(self._demand_value(node,col,t) for node in selected for grade,col in node.get("demand_columns",{}).items() if (str(node["name"]),grade) not in self.rc_buildings_by_node_grade) for t in self.times), default=0.)
        if self.building_inputs is None:
            return float(fixed_peak)
        thermal_peak = sum(peak for (node, _grade), peak in self.building_reference.node_grade_design_peak_kw.items() if node_name is None or node == node_name)
        return float(fixed_peak+thermal_peak)

    def _source_availability(self, source: dict[str, Any], t: int) -> float:
        availability = float(source.get("availability", 1.0))
        if source.get("availability_column"):
            availability = float(
                self._ts_value(t, source["availability_column"])
            )
        if source.get("type") == "heat_pump" and source.get("source_temp_column"):
            source_temp = float(self._ts_value(t, source["source_temp_column"]))
            cutoff = source.get("cutoff_temperature_c")
            if cutoff is not None and source_temp < float(cutoff):
                return 0.0
            derating = source.get("low_temperature_derating")
            if derating:
                threshold = float(derating.get("threshold_c", -7.0))
                slope = float(derating.get("slope_per_c", 0.0))
                minimum = float(derating.get("minimum_factor", 0.0))
                factor = 1.0 - max(0.0, threshold - source_temp) * slope
                availability *= max(minimum, min(1.0, factor))
        return self._clip_scalar(availability, 0.0, 1.0)

    def _pipe_efficiency(self, pipe: dict[str, Any], t: int) -> float:
        return self._pipe_efficiency_from_spec(pipe, t)

    def _pipe_efficiency_from_spec(self, specification: dict[str, Any], t: int) -> float:
        efficiency = float(specification.get("delivery_efficiency", 1.0))
        sensitivity = float(specification.get("cold_loss_sensitivity_per_c", 0.0))
        if sensitivity > 0 and "outdoor_temp" in self.ts.columns:
            reference = float(specification.get("loss_reference_temperature_c", 0.0))
            minimum = float(specification.get("minimum_delivery_efficiency", 0.80))
            cold_delta = max(
                0.0, reference - float(self._ts_value(t, "outdoor_temp"))
            )
            efficiency = max(minimum, efficiency - sensitivity * cold_delta)
        return self._clip_scalar(efficiency, 0.0, 1.0)

    def _pipe_retrofit_efficiency(self, pipe: dict[str, Any], t: int) -> float:
        retrofit = pipe.get("retrofit", {})
        merged = dict(pipe)
        merged.update(retrofit)
        return self._pipe_efficiency_from_spec(merged, t)

    def _cooling_eer(self, source_name: str, t: int) -> float:
        cooling = self.config.get("multi_energy_services", {}).get("cooling", {})
        specification = cooling.get("sources", {}).get(source_name, {})
        if not isinstance(specification, dict):
            specification = {"eer": specification}
        raw = float(specification.get("eer", 3.5))
        if "outdoor_temp" in self.ts.columns:
            reference = float(specification.get("reference_outdoor_temp_c", 35.0))
            loss = float(specification.get("eer_loss_per_c_above_reference", 0.0))
            raw -= max(
                0.0, float(self._ts_value(t, "outdoor_temp")) - reference
            ) * loss
        return self._clip_scalar(
            raw,
            float(specification.get("eer_min", 1.5)),
            float(specification.get("eer_max", 8.0)),
        )

    def _record_electric_load(self, t: int, index: int, rate: float) -> None:
        """Register one physical electric-load coefficient for a time step.

        The registry is the shared source for electric-balance rows and the
        annual electricity KPI.  It is frozen on first read so a future load
        variable added too late in ``build()`` fails loudly instead of being
        silently omitted from grid and microgrid constraints.
        """
        if self._finalized_electric_load_terms is not None:
            raise RuntimeError("电负荷注册表已冻结，不能继续注册变量")
        coefficient = float(rate)
        if not np.isfinite(coefficient):
            raise ValueError(f"时段 {t} 的电负荷系数必须为有限值")
        row = self._electric_load_terms[int(t)]
        if index in row:
            raise RuntimeError(f"电负荷变量 {index} 在时段 {t} 被重复注册")
        row[index] = coefficient
        self._record_time_metric("electricity_kwh", index, coefficient, t)

    def _electric_load_row(self, t: int, margin: float = 1.0) -> dict[int, float]:
        if self._finalized_electric_load_terms is None:
            self._finalized_electric_load_terms = {
                time: tuple(row.items())
                for time, row in self._electric_load_terms.items()
            }
        # Always return a fresh dictionary.  Grid-capacity and microgrid blocks
        # append their own coefficients to this row and must never mutate the
        # cached physical-load registry or another constraint.
        return {
            index: margin * coefficient
            for index, coefficient in self._finalized_electric_load_terms.get(int(t), ())
        }

    def _source_cost_and_emission(self, source: dict[str, Any], grade: dict[str, Any], t: int) -> tuple[float, float]:
        if source.get("type") == "chp":
            return (
                float(source.get("variable_cost_yuan_per_kwh_heat", 0.0)),
                float(source.get("emission_kg_per_kwh_heat", 0.0)),
            )
        if source.get("type") == "heat_pump":
            cop = self._cop(source, grade, t)
            variable_cost = float(source.get("variable_om_yuan_per_kwh_heat", 0.0))
            variable_cost += self._electricity_price(t) / cop
            emission = float(self._ts_value(t, "grid_emission_factor")) / cop
            emission += float(source.get("direct_emission_kg_per_kwh_heat", 0.0))
            return variable_cost, emission
        if source.get("type") == "electric_boiler":
            efficiency = float(source.get("efficiency", 0.98))
            variable_cost = float(source.get("variable_om_yuan_per_kwh_heat", 0.0))
            variable_cost += self._electricity_price(t) / efficiency
            emission = (
                float(self._ts_value(t, "grid_emission_factor")) / efficiency
            )
            return variable_cost, emission
        return (
            float(source.get("variable_cost_yuan_per_kwh_heat", 0.0)),
            float(source.get("emission_kg_per_kwh_heat", 0.0)),
        )

    def _source_non_fossil_heat_fraction(
        self,
        source: dict[str, Any],
        grade: dict[str, Any],
        t: int,
    ) -> float:
        """Return the non-fossil share of one unit of useful source heat.

        Waste heat is intentionally kept in its own policy account.  For a
        renewable-source heat pump, only ambient/ground heat is counted by
        default; the compressor-electricity fraction remains in the power
        account.  Projects may override the fraction with audited evidence.
        """

        if source.get("resource_kind") != "renewable":
            return 0.0
        override = source.get("non_fossil_heat_fraction")
        if override is not None:
            return self._clip_scalar(override, 0.0, 1.0)
        if source.get("type") == "heat_pump":
            return max(0.0, 1.0 - 1.0 / self._cop(source, grade, t))
        return 1.0

    @staticmethod
    def _source_direct_fossil_emission(source: dict[str, Any]) -> float:
        if source.get("resource_kind") != "fossil":
            return 0.0
        return float(
            source.get(
                "direct_emission_kg_per_kwh_heat",
                source.get("emission_kg_per_kwh_heat", 0.0),
            )
        )

    def build(self) -> None:
        if self._built:
            return
        p = self.problem
        accounting = self.config.get("fixed_investment_accounting", {})
        if accounting:
            if accounting.get("scope") != "annual_selected_plan_charges":
                raise ValueError("unsupported fixed investment accounting scope")
            fixed_charge = p.add_var("fixed_annual_plan_charge", lb=1.0, ub=1.0)
            for name, amount in accounting.get("components", {}).items():
                if name not in {"annualized_capex", "fixed_om_cost"} or not np.isfinite(float(amount)):
                    raise ValueError("invalid fixed investment component")
                self._record_component(name, fixed_charge, float(amount))
            for name, amount in accounting.get("metrics", {}).items():
                if name not in {"regulated_annualized_capex_yuan_per_year", "shared_non_heating_capex_yuan_per_year", "public_subsidy_yuan_per_year"} or not np.isfinite(float(amount)):
                    raise ValueError("invalid fixed investment metric")
                self._record_metric(name, fixed_charge, float(amount))
        planning = self.config["planning"]
        cooling = self.config.get("multi_energy_services", {}).get("cooling", {})
        cooling_sources = cooling.get("sources", {}) if cooling else {}
        if cooling.get("enabled", False) and any(key in self.policy for key in ("emission_intensity_cap_kg_per_kwh", "minimum_emission_intensity_reduction_fraction")):
            raise ValueError("Joint cooling/heating requires total-service carbon constraints; legacy heat-only intensity policies lack a declared shared-electricity allocation")
        cooling_demand_columns = cooling.get("demand_columns", {}) if cooling else {}
        self.building_thermal = add_building_thermal_constraints(self)
        if self.building_thermal is not None:
            for node_name in self.building_thermal.covered_cooling_nodes - set(cooling_demand_columns):
                for t in self.times:
                    p.add_constraint({self.building_thermal.node_cooling[node_name,t]:1.0},lb=0.0,ub=0.0)

        # Heat-metering retrofit is a continuous coverage investment.  Its
        # demand-side saving remains linear because the untreated hourly load
        # is input data and multiplies only one coverage decision variable.
        for node_name, node in self.nodes.items():
            metering = node.get("heat_metering")
            if not metering:
                continue
            existing = float(metering.get("existing_coverage_fraction", 0.0))
            fixed_new = metering.get("fixed_new_coverage_fraction")
            if fixed_new is None:
                lower = 0.0
                upper = min(
                    float(metering.get("max_new_coverage_fraction", 0.0)),
                    max(0.0, 1.0 - existing),
                )
            else:
                lower = upper = float(fixed_new)
            if upper > 0.0 or fixed_new is not None:
                idx = p.add_var(
                    f"heat_metering_coverage[{node_name}]",
                    lb=lower,
                    ub=upper,
                )
                self.variables["heat_metering_coverage"][node_name] = idx
                self._record_investment_cost(
                    idx,
                    "heat_metering_coverage",
                    node_name,
                    float(metering.get("capex_yuan_per_coverage_fraction", 0.0)),
                )

        # Optional district-side temperature retrofit. A binary decision shifts
        # an eligible share of an existing high-temperature load to a lower
        # temperature grade, representing terminal and substation upgrades.
        for node_name, node in self.nodes.items():
            retrofit = node.get("temperature_retrofit")
            if not retrofit:
                continue
            source_grade = str(retrofit["source_grade"])
            target_grade = str(retrofit["target_grade"])
            if source_grade not in node.get("demand_columns", {}) or target_grade not in node.get("demand_columns", {}):
                raise ValueError(f"节点 {node_name} 的温度改造要求同时提供 {source_grade} 与 {target_grade} 负荷列")
            fixed_value = retrofit.get("fixed_value")
            lower = float(fixed_value) if fixed_value is not None else 0.0
            upper = float(fixed_value) if fixed_value is not None else 1.0
            idx = p.add_var(f"terminal_temperature_retrofit[{node_name}]", lb=lower, ub=upper, integer=True)
            self.variables["terminal_temperature_retrofit"][node_name] = idx
            self._record_investment_cost(
                idx, "terminal_temperature_retrofit", node_name, float(retrofit.get("capex_total_yuan", 0.0))
            )
            shifted_fraction = float(retrofit.get("eligible_fraction", 1.0))
            source_exergy = self._exergy_factor(float(self.grades[source_grade]["temperature_c"]))
            target_exergy = self._exergy_factor(float(self.grades[target_grade]["temperature_c"]))
            source_column = node["demand_columns"][source_grade]
            shifted_annual_heat = sum(
                self._demand_value(node, source_column, t) * shifted_fraction * self._op_weight(t)
                for t in self.times
            )
            self._record_metric(
                "demand_exergy_kwh",
                idx,
                shifted_annual_heat * (target_exergy - source_exergy),
            )
            self._record_metric("retrofitted_heat_kwh", idx, shifted_annual_heat)

        # Source investments and dispatch.
        for s_name, source in self.sources.items():
            max_new = float(source.get("max_new_capacity_kw", 0.0))
            existing_capacity = float(source.get("existing_capacity_kw", 0.0))
            maximum_capacity = existing_capacity + max_new
            heat_by_time: dict[int, list[int]] = {}
            minimum_electricity_by_time: dict[int, float] = {}
            if max_new > 0:
                idx = p.add_var(f"source_capacity[{s_name}]", ub=max_new)
                self.variables["source_capacity"][s_name] = idx
                self._record_investment_cost(
                    idx, "source_capacity", s_name, float(source.get("capex_yuan_per_kw", 0.0))
                )
            max_retire = min(float(source.get("max_retire_capacity_kw", 0.0)), existing_capacity)
            min_retire = min(
                float(source.get("minimum_retire_capacity_kw", 0.0)),
                max_retire,
            )
            if (
                bool(self.policy.get("enforce_small_coal_boiler_phaseout", False))
                and source.get("type") == "boiler"
                and source.get("resource_kind") == "fossil"
                and float(source.get("boiler_capacity_t_per_hour", float("inf")))
                <= float(
                    self.policy.get(
                        "small_coal_boiler_phaseout_max_t_per_hour", 10.0
                    )
                )
            ):
                min_retire = existing_capacity
            if max_retire > 0:
                retire_idx = p.add_var(
                    f"source_retirement[{s_name}]",
                    lb=min_retire,
                    ub=max_retire,
                )
                self.variables["source_retirement"][s_name] = retire_idx
                self._record_investment_cost(
                    retire_idx, "source_retirement", s_name,
                    float(source.get("decommissioning_cost_yuan_per_kw", 0.0)),
                )
                self._record_component(
                    "fixed_om_cost", retire_idx,
                    -float(source.get("fixed_om_yuan_per_kw_year", 0.0)),
                )

            allowed = list(source.get("allowed_grades", self.grades))
            for t in self.times:
                availability = self._source_availability(source, t)
                capacity_row: dict[int, float] = {}
                heat_by_time[t] = []
                for grade_name in allowed:
                    grade = self.grades[grade_name]
                    if source.get("type") != "heat_pump" and float(source.get("supply_temp_c", 999)) < float(grade["temperature_c"]):
                        continue
                    idx = p.add_var(f"source_heat[{s_name},{grade_name},{t}]")
                    self.variables["source_heat"][(s_name, grade_name, t)] = idx
                    self._add(capacity_row, idx, 1.0)
                    heat_by_time[t].append(idx)

                    var_cost, emission = self._source_cost_and_emission(source, grade, t)
                    self._record_time_component("operating_cost", idx, var_cost, t)
                    self._record_time_component("carbon_cost", idx, emission * self.carbon_price, t)
                    self._record_time_metric("emissions_kg", idx, emission, t)
                    self._record_time_metric("source_heat_kwh", idx, 1.0, t)
                    if source.get("resource_kind") in {"waste_heat", "renewable"}:
                        self._record_time_metric("clean_heat_kwh", idx, 1.0, t)
                    non_fossil_fraction = self._source_non_fossil_heat_fraction(
                        source, grade, t
                    )
                    if non_fossil_fraction > 0.0:
                        self._record_time_metric(
                            "non_fossil_heat_kwh",
                            idx,
                            non_fossil_fraction,
                            t,
                        )
                    if source.get("resource_kind") == "waste_heat":
                        recovered_fraction = 1.0
                        if source.get("type") == "heat_pump":
                            # The compressor electricity is not recovered waste heat.
                            recovered_fraction = 1.0 - 1.0 / self._cop(source, grade, t)
                        self._record_time_metric("waste_heat_kwh", idx, recovered_fraction, t)
                    if source.get("resource_kind") == "fossil":
                        self._record_time_metric("fossil_heat_kwh", idx, 1.0, t)
                        direct_emission = self._source_direct_fossil_emission(
                            source
                        )
                        if direct_emission > 0.0:
                            self._record_time_metric(
                                "direct_fossil_emissions_kg",
                                idx,
                                direct_emission,
                                t,
                            )
                    if source.get("type") == "heat_pump":
                        self._record_electric_load(
                            t, idx, 1.0 / self._cop(source, grade, t)
                        )
                    elif source.get("type") == "electric_boiler":
                        self._record_electric_load(
                            t,
                            idx,
                            1.0 / float(source.get("efficiency", 0.98)),
                        )

                    source_temp = float(source.get("supply_temp_c", grade["temperature_c"]))
                    if source.get("type") == "heat_pump":
                        source_temp = float(grade["temperature_c"])
                    ex_loss = max(self._exergy_factor(source_temp) - self._exergy_factor(float(grade["temperature_c"])), 0.0)
                    self._record_time_metric("exergy_loss_kwh", idx, ex_loss, t)
                    self._record_time_metric("source_exergy_kwh", idx, self._exergy_factor(source_temp), t)
                    self._record_time_component(
                        "exergy_mismatch_cost", idx,
                        ex_loss * float(planning.get("exergy_penalty_yuan_per_kwh", 0.0)), t,
                    )

                # A CHP may have a bounded condenser/bypass path for rejecting
                # heat when its minimum electricity output exceeds useful heat
                # demand. Rejected heat still consumes source capacity, fuel
                # and emissions and participates in the CHP heat-to-power
                # operating region; it is never counted as delivered service.
                if (
                    source.get("type") == "chp"
                    and bool(source.get("heat_rejection_enabled", False))
                ):
                    rejection_limit = float(
                        source.get("max_heat_rejection_kw", 0.0)
                    )
                    if rejection_limit > 0.0:
                        rejected = p.add_var(
                            f"chp_heat_rejection[{s_name},{t}]",
                            ub=rejection_limit,
                        )
                        self.variables["chp_heat_rejection"][(s_name, t)] = rejected
                        self._add(capacity_row, rejected, 1.0)
                        heat_by_time[t].append(rejected)
                        reference_grade = self.grades[allowed[0]]
                        var_cost, emission = self._source_cost_and_emission(
                            source, reference_grade, t
                        )
                        rejection_penalty = float(
                            source.get(
                                "heat_rejection_penalty_yuan_per_kwh", 0.0
                            )
                        )
                        self._record_time_component(
                            "operating_cost",
                            rejected,
                            var_cost + rejection_penalty,
                            t,
                        )
                        self._record_time_component(
                            "carbon_cost",
                            rejected,
                            emission * self.carbon_price,
                            t,
                        )
                        self._record_time_metric(
                            "emissions_kg", rejected, emission, t
                        )
                        self._record_time_metric(
                            "rejected_heat_kwh", rejected, 1.0, t
                        )
                        source_temp = float(
                            source.get(
                                "supply_temp_c",
                                reference_grade["temperature_c"],
                            )
                        )
                        rejected_exergy = self._exergy_factor(source_temp)
                        self._record_time_metric(
                            "source_exergy_kwh", rejected, rejected_exergy, t
                        )
                        self._record_time_metric(
                            "exergy_loss_kwh", rejected, rejected_exergy, t
                        )
                        direct_emission = self._source_direct_fossil_emission(
                            source
                        )
                        if direct_emission > 0.0:
                            self._record_time_metric(
                                "direct_fossil_emissions_kg",
                                rejected,
                                direct_emission,
                                t,
                            )

                # Optional cooling service shares the installed compressor and
                # heat-exchanger capacity with winter heating.  This closes the
                # annual asset-utilisation loop for hot-summer/cold-winter
                # districts instead of applying an assumed capex discount.
                cooling_spec = cooling_sources.get(s_name)
                cooling_column = cooling_demand_columns.get(str(source["node"]))
                if cooling_spec is not None and cooling_column:
                    if not isinstance(cooling_spec, dict):
                        cooling_spec = {"eer": cooling_spec}
                    cooling_idx = p.add_var(f"cooling_output[{s_name},{t}]")
                    self.variables["cooling_output"][(s_name, t)] = cooling_idx
                    self._add(
                        capacity_row,
                        cooling_idx,
                        float(cooling_spec.get("capacity_equivalence", 1.0)),
                    )
                    eer = self._cooling_eer(s_name, t)
                    electricity = 1.0 / eer
                    variable_cost = float(
                        cooling_spec.get("variable_om_yuan_per_kwh_cooling", 0.0)
                    ) + self._electricity_price(t) * electricity
                    emission = (
                        float(self._ts_value(t, "grid_emission_factor"))
                        * electricity
                        + float(cooling_spec.get("direct_emission_kg_per_kwh_cooling", 0.0))
                    )
                    self._record_time_component(
                        "cooling_operating_cost", cooling_idx, variable_cost, t
                    )
                    self._record_time_component(
                        "cooling_carbon_cost", cooling_idx,
                        emission * self.carbon_price, t,
                    )
                    self._record_time_metric(
                        "cooling_source_kwh", cooling_idx, 1.0, t
                    )
                    self._record_time_metric(
                        "cooling_electricity_kwh", cooling_idx, electricity, t
                    )
                    self._record_electric_load(t, cooling_idx, electricity)
                    self._record_time_metric(
                        "cooling_emissions_kg", cooling_idx, emission, t
                    )
                cap_idx = self.variables["source_capacity"].get(s_name)
                if cap_idx is not None:
                    self._add(capacity_row, cap_idx, -availability)
                retire_idx = self.variables["source_retirement"].get(s_name)
                if retire_idx is not None:
                    self._add(capacity_row, retire_idx, availability)
                p.add_constraint(capacity_row, ub=availability * existing_capacity)
                if source.get("type") == "chp":
                    legacy_ratio = float(source.get("electricity_to_heat_ratio", 0.0))
                    ratio_min = float(source.get("electricity_to_heat_ratio_min", legacy_ratio))
                    ratio_max = float(source.get("electricity_to_heat_ratio_max", legacy_ratio))
                    if ratio_min < 0 or ratio_max <= 0 or ratio_min > ratio_max:
                        raise ValueError(f"CHP {s_name} 的热电运行域参数无效")
                    electricity = p.add_var(f"chp_electricity_output[{s_name},{t}]")
                    self.variables["chp_electricity_output"][(s_name, t)] = electricity
                    upper = {electricity: 1.0, **{index: -ratio_max for index in heat_by_time[t]}}
                    lower = {electricity: 1.0, **{index: -ratio_min for index in heat_by_time[t]}}
                    p.add_constraint(upper, ub=0.0)
                    p.add_constraint(lower, lb=0.0)
                    if source.get("minimum_electricity_output_column"):
                        required = (
                            float(
                                self._ts_value(
                                    t,
                                    source["minimum_electricity_output_column"],
                                )
                            )
                            * availability
                        )
                        if source.get("unit_commitment", False):
                            minimum_electricity_by_time[t] = required
                        else:
                            p.add_constraint({electricity: 1.0}, lb=required)
                    export_multiplier = float(source.get("electricity_export_price_multiplier", 1.0))
                    self._record_time_component(
                        "operating_cost", electricity,
                        -self._electricity_price(t) * export_multiplier, t,
                    )
                    self._record_time_metric("chp_electricity_kwh", electricity, 1.0, t)

            # Optional unit commitment, startup/shutdown, and ramp constraints.
            # They are disabled unless unit_commitment=true in YAML.
            if source.get("unit_commitment", False) and maximum_capacity > 0:
                min_fraction = float(source.get("minimum_output_fraction", 0.0))
                ramp_up = float(source.get("ramp_up_fraction_per_hour", 1.0))
                ramp_down = float(source.get("ramp_down_fraction_per_hour", 1.0))
                cap_idx = self.variables["source_capacity"].get(s_name)
                for t in self.times:
                    on = p.add_var(f"source_on[{s_name},{t}]", ub=1.0, integer=True)
                    # Startup and shutdown are exact transition indicators once
                    # the adjacent on/off states are binary.  Keeping these two
                    # auxiliaries continuous removes two thirds of the unit-
                    # commitment integer variables without relaxing the model;
                    # the linking constraints below uniquely force them to 0/1.
                    startup = p.add_var(f"source_startup[{s_name},{t}]", ub=1.0)
                    shutdown = p.add_var(f"source_shutdown[{s_name},{t}]", ub=1.0)
                    self.variables["source_on"][(s_name, t)] = on
                    self.variables["source_startup"][(s_name, t)] = startup
                    self.variables["source_shutdown"][(s_name, t)] = shutdown
                    if t in minimum_electricity_by_time:
                        p.add_constraint(
                            {
                                self.variables["chp_electricity_output"][(s_name, t)]: 1.0,
                                on: -minimum_electricity_by_time[t],
                            },
                            lb=0.0,
                        )
                    self._record_time_component(
                        "startup_shutdown_cost", startup, float(source.get("startup_cost_yuan", 0.0)), t, event=True
                    )
                    self._record_time_component(
                        "startup_shutdown_cost", shutdown, float(source.get("shutdown_cost_yuan", 0.0)), t, event=True
                    )

                    upper_on = {idx: 1.0 for idx in heat_by_time[t]}
                    self._add(upper_on, on, -maximum_capacity)
                    p.add_constraint(upper_on, ub=0.0)

                    if min_fraction > 0:
                        lower_on = {idx: 1.0 for idx in heat_by_time[t]}
                        if cap_idx is not None:
                            self._add(lower_on, cap_idx, -min_fraction)
                        self._add(lower_on, on, -maximum_capacity)
                        p.add_constraint(lower_on, lb=min_fraction * existing_capacity - maximum_capacity)

                for t in self.times:
                    prev = self.time_predecessor[t]
                    on = self.variables["source_on"][(s_name, t)]
                    prev_on = self.variables["source_on"][(s_name, prev)]
                    startup = self.variables["source_startup"][(s_name, t)]
                    shutdown = self.variables["source_shutdown"][(s_name, t)]
                    # Exact three-row state-transition hull.  The two sum
                    # inequalities imply all four individual upper links
                    # (startup <= on, startup <= 1-prev, shutdown <= prev,
                    # shutdown <= 1-on) when combined with the equality.  This
                    # is the same tight relaxation with two fewer rows per hour.
                    # A one-row cyclic group has prev_on == on. Accumulate
                    # coefficients so its transition cancels exactly.
                    transition = {startup: 1.0, shutdown: -1.0, on: -1.0}
                    self._add(transition, prev_on, 1.0)
                    p.add_constraint(transition, lb=0.0, ub=0.0)
                    active_sum = {startup: 1.0, shutdown: 1.0, on: -1.0}
                    self._add(active_sum, prev_on, -1.0)
                    p.add_constraint(active_sum, ub=0.0)
                    inactive_sum = {startup: 1.0, shutdown: 1.0, on: 1.0}
                    self._add(inactive_sum, prev_on, 1.0)
                    p.add_constraint(inactive_sum, ub=2.0)

                    ramp_up_row = {idx: 1.0 for idx in heat_by_time[t]}
                    for idx in heat_by_time[prev]:
                        self._add(ramp_up_row, idx, -1.0)
                    if cap_idx is not None:
                        self._add(ramp_up_row, cap_idx, -ramp_up)
                    self._add(ramp_up_row, startup, -maximum_capacity)
                    p.add_constraint(ramp_up_row, ub=ramp_up * existing_capacity)

                    ramp_down_row = {idx: 1.0 for idx in heat_by_time[prev]}
                    for idx in heat_by_time[t]:
                        self._add(ramp_down_row, idx, -1.0)
                    if cap_idx is not None:
                        self._add(ramp_down_row, cap_idx, -ramp_down)
                    self._add(ramp_down_row, shutdown, -maximum_capacity)
                    p.add_constraint(ramp_down_row, ub=ramp_down * existing_capacity)

        # Node-level cooling balances.  Cooling is optional and intentionally
        # kept separate from heat-network flows: the first implementation
        # represents a shared central/building energy station, while avoiding
        # an unsupported assumption that every heating pipe can carry chilled
        # water.  Reliability is enforced both in expectation and per scenario.
        if cooling:
            unserved_penalty = float(
                cooling.get(
                    "unserved_penalty_yuan_per_kwh",
                    planning.get("unserved_penalty_yuan_per_kwh", 100.0),
                )
            )
            for node_name, demand_column in cooling_demand_columns.items():
                for t in self.times:
                    demand = max(
                        0.0, float(self._ts_value(t, str(demand_column)))
                    )
                    row: dict[int, float] = {}
                    for source_name, source in self.sources.items():
                        if source["node"] != node_name:
                            continue
                        cooling_idx = self.variables["cooling_output"].get(
                            (source_name, t)
                        )
                        if cooling_idx is not None:
                            self._add(row, cooling_idx, 1.0)
                    if self.building_thermal is not None and node_name in self.building_thermal.covered_cooling_nodes:
                        delivered = self.building_thermal.node_cooling[node_name,t]
                        self._add(row, delivered, -1.0)
                        p.add_constraint(row,lb=0.0,ub=0.0)
                        self._record_time_metric("cooling_demand_kwh",delivered,1.0,t)
                        self._record_time_metric("building_cooling_delivered_kwh",delivered,1.0,t)
                        continue
                    unserved = p.add_var(
                        f"cooling_unserved[{node_name},{t}]", ub=demand
                    )
                    self.variables["cooling_unserved"][(node_name, t)] = unserved
                    self._add(row, unserved, 1.0)
                    p.add_constraint(row, lb=demand, ub=demand)
                    self._record_time_component(
                        "cooling_comfort_cost", unserved, unserved_penalty, t
                    )
                    self._record_time_metric(
                        "cooling_unserved_kwh", unserved, 1.0, t
                    )
                    self.metric_constants["cooling_demand_kwh"] += (
                        demand * self._op_weight(t)
                    )
                    scenario = self._scenario(t)
                    self.scenario_metric_constants[scenario]["cooling_demand_kwh"] += (
                        demand * self._physical_weight(t)
                    )

            cooling_demand = self.metric_constants.get("cooling_demand_kwh", 0.0)
            maximum_cooling_unserved = float(
                cooling.get("max_unserved_rate", planning.get("max_unserved_rate", 0.0))
            ) * cooling_demand
            p.add_constraint(
                dict(self.metric_coefficients.get("cooling_unserved_kwh", {})),
                ub=maximum_cooling_unserved,
            )
            scenario_cooling_rate = float(
                cooling.get(
                    "max_scenario_unserved_rate",
                    cooling.get("max_unserved_rate", planning.get("max_scenario_unserved_rate", 0.0)),
                )
            )
            for scenario in self.scenarios:
                p.add_constraint(
                    dict(
                        self.scenario_metric_coefficients[scenario].get(
                            "cooling_unserved_kwh", {}
                        )
                    ),
                    ub=scenario_cooling_rate
                    * self.scenario_metric_constants[scenario].get(
                        "cooling_demand_kwh", 0.0
                    ),
                )

            # Large ground-source projects in Wuhan are evaluated on annual
            # heat extraction/rejection balance.  Applying the constraint per
            # stochastic scenario prevents a low-probability weather block
            # from hiding long-term ground-temperature drift.
            for source_name, raw_spec in cooling_sources.items():
                specification = raw_spec if isinstance(raw_spec, dict) else {}
                balance = specification.get("ground_thermal_balance")
                if not balance:
                    continue
                minimum = float(
                    balance.get("minimum_extraction_to_rejection_ratio", 0.8)
                )
                maximum = float(
                    balance.get("maximum_extraction_to_rejection_ratio", 1.25)
                )
                for scenario, scenario_times in self.scenario_times.items():
                    extraction: dict[int, float] = {}
                    rejection: dict[int, float] = {}
                    for t in scenario_times:
                        for grade_name, grade in self.grades.items():
                            heat_idx = self.variables["source_heat"].get(
                                (source_name, grade_name, t)
                            )
                            if heat_idx is not None:
                                rate = 1.0 - 1.0 / self._cop(
                                    self.sources[source_name], grade, t
                                )
                                self._add(
                                    extraction,
                                    heat_idx,
                                    rate * self._physical_weight(t),
                                )
                                self._record_time_metric(
                                    "ground_heat_extraction_kwh", heat_idx, rate, t
                                )
                        cooling_idx = self.variables["cooling_output"].get(
                            (source_name, t)
                        )
                        if cooling_idx is not None:
                            rate = 1.0 + 1.0 / self._cooling_eer(source_name, t)
                            self._add(
                                rejection,
                                cooling_idx,
                                rate * self._physical_weight(t),
                            )
                            self._record_time_metric(
                                "ground_heat_rejection_kwh", cooling_idx, rate, t
                            )
                    # Winter replay retains the physical ground-energy ledger,
                    # but the annual ratio is verified only over the service year.
                    if not bool(balance.get("enforce", True)):
                        continue
                    lower_row = dict(extraction)
                    upper_row = dict(extraction)
                    for index, coefficient in rejection.items():
                        self._add(lower_row, index, -minimum * coefficient)
                        self._add(upper_row, index, -maximum * coefficient)
                    p.add_constraint(lower_row, lb=0.0)
                    p.add_constraint(upper_row, ub=0.0)

        add_shared_heat_resources(self)

        # Biomass procurement, transport and inventory. Heat dispatch consumes
        # physical fuel; deliveries replenish a bounded store in every scenario.
        for chain in self.config.get("biomass_supply_chains", []):
            chain_name = str(chain["name"])
            source_names = [str(name) for name in chain.get("source_names", [])]
            unknown = set(source_names) - set(self.sources)
            if unknown:
                raise ValueError(f"生物质供应链 {chain_name} 引用了未知热源: {sorted(unknown)}")
            lhv = float(chain["lower_heating_value_kwh_per_kg"])
            efficiency = float(chain.get("conversion_efficiency", 0.85))
            if lhv <= 0 or not 0 < efficiency <= 1:
                raise ValueError(f"生物质供应链 {chain_name} 的热值或效率无效")
            existing_inventory = float(chain.get("existing_inventory_capacity_kg", 0.0))
            max_new_inventory = float(chain.get("max_new_inventory_capacity_kg", 0.0))
            inventory_cap_idx = None
            if max_new_inventory > 0:
                inventory_cap_idx = p.add_var(
                    f"biomass_inventory_capacity[{chain_name}]", ub=max_new_inventory
                )
                self.variables["biomass_inventory_capacity"][chain_name] = inventory_cap_idx
                self._record_investment_cost(
                    inventory_cap_idx, "biomass_inventory_capacity", chain_name,
                    float(chain.get("inventory_capex_yuan_per_kg", 0.0)),
                )
            delivery_limit = float(chain.get("delivery_capacity_kg_per_hour", np.inf))
            distance = float(chain.get("transport_distance_km", 0.0))
            fuel_cost = float(chain.get("procurement_cost_yuan_per_kg", 0.0))
            fuel_cost += float(chain.get("handling_cost_yuan_per_kg", 0.0))
            fuel_cost += float(chain.get("transport_cost_yuan_per_tonne_km", 0.0)) * distance / 1000.0
            fuel_emission = float(chain.get("upstream_emission_kg_per_kg", 0.0))
            fuel_emission += float(chain.get("transport_emission_kg_per_tonne_km", 0.0)) * distance / 1000.0
            # The collection/resource ceiling is a physical limit and therefore
            # has to hold in every scenario.  A probability-weighted aggregate
            # would let a low-probability disruption consume more fuel than the
            # county can actually supply in a year.
            annual_supply_rows: dict[str, dict[int, float]] = {
                scenario: {} for scenario in self.scenarios
            }
            for t in self.times:
                availability = 1.0
                if chain.get("delivery_availability_column"):
                    availability = float(
                        self._ts_value(t, chain["delivery_availability_column"])
                    )
                supply = p.add_var(
                    f"biomass_delivery[{chain_name},{t}]",
                    ub=max(0.0, delivery_limit * availability),
                )
                inventory = p.add_var(f"biomass_inventory[{chain_name},{t}]")
                self.variables["biomass_delivery"][(chain_name, t)] = supply
                self.variables["biomass_inventory"][(chain_name, t)] = inventory
                inventory_row = {inventory: 1.0}
                if inventory_cap_idx is not None:
                    inventory_row[inventory_cap_idx] = -1.0
                p.add_constraint(inventory_row, ub=existing_inventory)
                self._record_time_component("biomass_fuel_cost", supply, fuel_cost, t)
                self._record_time_component(
                    "carbon_cost", supply, fuel_emission * self.carbon_price, t
                )
                self._record_time_metric("emissions_kg", supply, fuel_emission, t)
                self._record_time_metric("biomass_fuel_kg", supply, 1.0, t)
                self._add(
                    annual_supply_rows[self._scenario(t)],
                    supply,
                    self._physical_weight(t),
                )
            inventory_indices = {t: self.variables["biomass_inventory"][(chain_name, t)] for t in self.times}
            inventory_boundary = EventStateBoundary(
                self, chain, label=f"biomass:{chain_name}", mode_key="boundary_mode",
                initial_key="initial_inventory_kg", minimum_key="terminal_inventory_min_kg",
                maximum_key="terminal_inventory_max_kg", existing_capacity=existing_inventory,
                capacity_index=inventory_cap_idx,
            )
            inventory_retention = float(chain.get("inventory_retention_per_hour", 1.0))
            if not np.isfinite(inventory_retention) or not 0 <= inventory_retention <= 1:
                raise ValueError("inventory_retention_per_hour must be in [0, 1]")
            for t in self.times:
                supply = self.variables["biomass_delivery"][(chain_name, t)]
                inventory = self.variables["biomass_inventory"][(chain_name, t)]
                balance = {
                    inventory: 1.0,
                    supply: -self.dt,
                }
                boundary_rhs = inventory_boundary.add_predecessor(
                    balance, t, inventory_indices, inventory_retention ** self.dt)
                for source_name in source_names:
                    for grade_name in self.grades:
                        heat_idx = self.variables["source_heat"].get((source_name, grade_name, t))
                        if heat_idx is not None:
                            self._add(balance, heat_idx, self.dt / (lhv * efficiency))
                            self._record_time_metric("biomass_heat_kwh", heat_idx, 1.0, t)
                p.add_constraint(balance, lb=boundary_rhs, ub=boundary_rhs)
            inventory_boundary.finish(inventory_indices)
            annual_limit = chain.get("annual_supply_limit_kg")
            if annual_limit is not None:
                for scenario, row in annual_supply_rows.items():
                    if row:
                        p.add_constraint(row, ub=float(annual_limit))

        # Pipe diameter investments and heat flows.
        for e_name, pipe in self.pipes.items():
            options = pipe.get("diameter_options", [])
            option_vars: list[int] = []
            for option in options:
                d_name = str(option["name"])
                idx = p.add_var(f"pipe_choice[{e_name},{d_name}]", ub=1.0, integer=True)
                self.variables["pipe_choice"][(e_name, d_name)] = idx
                option_vars.append(idx)
                self._record_investment_cost(
                    idx, "pipe_choice", f"{e_name}:{d_name}", float(option["capex_total_yuan"])
                )
            if option_vars:
                p.add_constraint({idx: 1.0 for idx in option_vars}, ub=1.0)

            retrofit = pipe.get("retrofit")
            retrofit_idx = None
            if retrofit:
                fixed_value = retrofit.get("fixed_value")
                lower = float(fixed_value) if fixed_value is not None else 0.0
                upper = float(fixed_value) if fixed_value is not None else 1.0
                retrofit_idx = p.add_var(
                    f"pipe_retrofit[{e_name}]", lb=lower, ub=upper, integer=True
                )
                self.variables["pipe_retrofit"][e_name] = retrofit_idx
                self._record_investment_cost(
                    retrofit_idx, "pipe_retrofit", e_name,
                    float(retrofit.get("capex_total_yuan", 0.0)),
                )

            maximum_pipe_capacity = float(pipe.get("existing_capacity_kw", 0.0))
            if options:
                maximum_pipe_capacity += max(float(option["capacity_kw"]) for option in options)
            bidirectional = bool(pipe.get("bidirectional", False))
            for t in self.times:
                cap_row: dict[int, float] = {}
                forward_row: dict[int, float] = {}
                reverse_row: dict[int, float] = {}
                for grade_name in self.grades:
                    forward = p.add_var(f"pipe_flow_forward[{e_name},{grade_name},{t}]")
                    self.variables["pipe_flow_forward"][(e_name, grade_name, t)] = forward
                    self._add(cap_row, forward, 1.0)
                    self._add(forward_row, forward, 1.0)
                    pumping_ratio = float(pipe.get("pumping_electricity_kwh_per_kwh_heat", 0.0))
                    pumping_cost = pumping_ratio * self._electricity_price(t)
                    grid_emission = float(
                        self._ts_value(t, "grid_emission_factor")
                    )
                    pumping_emission = pumping_ratio * grid_emission
                    self._record_time_component("operating_cost", forward, pumping_cost, t)
                    self._record_time_component("carbon_cost", forward, pumping_emission * self.carbon_price, t)
                    self._record_time_metric("emissions_kg", forward, pumping_emission, t)
                    self._record_electric_load(t, forward, pumping_ratio)
                    if retrofit_idx is not None:
                        activated = p.add_var(f"pipe_retrofit_flow_forward[{e_name},{grade_name},{t}]")
                        self.variables["pipe_retrofit_flow_forward"][(e_name, grade_name, t)] = activated
                        p.add_constraint({activated: 1.0, forward: -1.0}, ub=0.0)
                        p.add_constraint({activated: 1.0, retrofit_idx: -maximum_pipe_capacity}, ub=0.0)
                        p.add_constraint(
                            {activated: 1.0, forward: -1.0, retrofit_idx: -maximum_pipe_capacity},
                            lb=-maximum_pipe_capacity,
                        )
                        improved_ratio = float(retrofit.get(
                            "pumping_electricity_kwh_per_kwh_heat", pumping_ratio
                        ))
                        reduction = max(0.0, pumping_ratio - improved_ratio)
                        self._record_time_component(
                            "operating_cost", activated,
                            -reduction * self._electricity_price(t), t,
                        )
                        self._record_time_component(
                            "carbon_cost", activated,
                            -reduction * grid_emission * self.carbon_price, t,
                        )
                        self._record_time_metric(
                            "emissions_kg", activated,
                            -reduction * grid_emission, t,
                        )
                        self._record_electric_load(t, activated, -reduction)
                    if bidirectional:
                        reverse = p.add_var(f"pipe_flow_reverse[{e_name},{grade_name},{t}]")
                        self.variables["pipe_flow_reverse"][(e_name, grade_name, t)] = reverse
                        self._add(cap_row, reverse, 1.0)
                        self._add(reverse_row, reverse, 1.0)
                        self._record_time_component("operating_cost", reverse, pumping_cost, t)
                        self._record_time_component("carbon_cost", reverse, pumping_emission * self.carbon_price, t)
                        self._record_time_metric("emissions_kg", reverse, pumping_emission, t)
                        self._record_electric_load(t, reverse, pumping_ratio)
                        if retrofit_idx is not None:
                            activated_reverse = p.add_var(
                                f"pipe_retrofit_flow_reverse[{e_name},{grade_name},{t}]"
                            )
                            self.variables["pipe_retrofit_flow_reverse"][(e_name, grade_name, t)] = activated_reverse
                            p.add_constraint({activated_reverse: 1.0, reverse: -1.0}, ub=0.0)
                            p.add_constraint(
                                {activated_reverse: 1.0, retrofit_idx: -maximum_pipe_capacity}, ub=0.0
                            )
                            p.add_constraint(
                                {activated_reverse: 1.0, reverse: -1.0, retrofit_idx: -maximum_pipe_capacity},
                                lb=-maximum_pipe_capacity,
                            )
                            improved_ratio = float(retrofit.get(
                                "pumping_electricity_kwh_per_kwh_heat", pumping_ratio
                            ))
                            reduction = max(0.0, pumping_ratio - improved_ratio)
                            self._record_time_component(
                                "operating_cost", activated_reverse,
                                -reduction * self._electricity_price(t), t,
                            )
                            self._record_time_component(
                                "carbon_cost", activated_reverse,
                                -reduction * grid_emission * self.carbon_price, t,
                            )
                            self._record_time_metric(
                                "emissions_kg", activated_reverse,
                                -reduction * grid_emission, t,
                            )
                            self._record_electric_load(t, activated_reverse, -reduction)
                for option in options:
                    idx = self.variables["pipe_choice"][(e_name, str(option["name"]))]
                    self._add(cap_row, idx, -float(option["capacity_kw"]))
                p.add_constraint(cap_row, ub=float(pipe.get("existing_capacity_kw", 0.0)))
                if bidirectional:
                    direction = p.add_var(f"pipe_direction[{e_name},{t}]", ub=1.0, integer=True)
                    self.variables["pipe_direction"][(e_name, t)] = direction
                    self._add(forward_row, direction, -maximum_pipe_capacity)
                    self._add(reverse_row, direction, maximum_pipe_capacity)
                    p.add_constraint(forward_row, ub=0.0)
                    p.add_constraint(reverse_row, ub=maximum_pipe_capacity)

        # Storage investments and operation. Short-term storage can be cyclic;
        # seasonal storage can use fixed initial/final boundary conditions.
        for st_name, storage in self.storages.items():
            existing = float(storage.get("existing_capacity_kwh", 0.0))
            max_new = float(storage.get("max_new_capacity_kwh", 0.0))
            cap_idx = None
            if max_new > 0:
                cap_idx = p.add_var(f"storage_capacity[{st_name}]", ub=max_new)
                self.variables["storage_capacity"][st_name] = cap_idx
                self._record_investment_cost(
                    cap_idx, "storage_capacity", st_name, float(storage.get("capex_yuan_per_kwh", 0.0))
                )

            power_ratio = float(storage.get("power_to_energy_ratio_per_hour", 0.25))
            max_power = power_ratio * (existing + max_new)
            strict_storage_mode = bool(storage.get("strict_charge_discharge_mode", True))
            throughput_cost = float(
                storage.get("throughput_cost_yuan_per_kwh", 0.0)
            )
            for t in self.times:
                ch = p.add_var(f"storage_charge[{st_name},{t}]")
                dis = p.add_var(f"storage_discharge[{st_name},{t}]")
                soc = p.add_var(f"storage_soc[{st_name},{t}]")
                self.variables["storage_charge"][(st_name, t)] = ch
                self.variables["storage_discharge"][(st_name, t)] = dis
                self.variables["storage_soc"][(st_name, t)] = soc
                if throughput_cost > 0.0:
                    self._record_time_component(
                        "operating_cost", ch, throughput_cost, t
                    )
                    self._record_time_component(
                        "operating_cost", dis, throughput_cost, t
                    )

                soc_row = {soc: 1.0}
                ch_row = {ch: 1.0}
                dis_row = {dis: 1.0}
                if cap_idx is not None:
                    soc_row[cap_idx] = -1.0
                    ch_row[cap_idx] = -power_ratio
                    dis_row[cap_idx] = -power_ratio
                p.add_constraint(soc_row, ub=existing)
                p.add_constraint(ch_row, ub=power_ratio * existing)
                p.add_constraint(dis_row, ub=power_ratio * existing)
                if strict_storage_mode:
                    mode = p.add_var(f"storage_mode[{st_name},{t}]", ub=1.0, integer=True)
                    self.variables["storage_mode"][(st_name, t)] = mode
                    p.add_constraint({ch: 1.0, mode: -max_power}, ub=0.0)
                    p.add_constraint({dis: 1.0, mode: max_power}, ub=max_power)

            boundary_mode = str(storage.get("boundary_mode", "cyclic"))
            loss_factor = (1.0 - float(storage.get("self_loss_per_hour", 0.0))) ** self.dt
            eta_ch = float(storage.get("charge_efficiency", 0.95))
            eta_dis = float(storage.get("discharge_efficiency", 0.95))
            if boundary_mode not in {"cyclic", "fixed"}:
                raise ValueError(f"储热 {st_name} 的 boundary_mode 仅支持 cyclic 或 fixed")
            for t in self.times:
                row = {
                    self.variables["storage_soc"][(st_name, t)]: 1.0,
                    self.variables["storage_charge"][(st_name, t)]: -eta_ch * self.dt,
                    self.variables["storage_discharge"][(st_name, t)]: self.dt / eta_dis,
                }
                group_times = self.boundary_groups[self.time_boundary_group[t]]
                is_group_start = t == group_times[0]
                if boundary_mode == "cyclic" or not is_group_start:
                    prev = self.time_predecessor[t]
                    self._add(row, self.variables["storage_soc"][(st_name, prev)], -loss_factor)
                    p.add_constraint(row, lb=0.0, ub=0.0)
                else:
                    initial_fraction = float(storage.get("initial_soc_fraction", 0.5))
                    if cap_idx is not None:
                        row[cap_idx] = -loss_factor * initial_fraction
                    initial_energy = loss_factor * initial_fraction * existing
                    p.add_constraint(row, lb=initial_energy, ub=initial_energy)
            if boundary_mode == "fixed":
                final_fraction = float(storage.get("final_soc_min_fraction", 0.0))
                for group_times in self.boundary_groups.values():
                    final_row = {self.variables["storage_soc"][(st_name, group_times[-1])]: 1.0}
                    if cap_idx is not None:
                        final_row[cap_idx] = -final_fraction
                    p.add_constraint(final_row, lb=final_fraction * existing)

            # Capacity adequacy contribution of storage is limited by both
            # discharge power and the duration of a northern cold spell.
            critical_duration = float(planning.get("critical_peak_duration_hours", 0.0))
            if critical_duration > 0:
                firm = p.add_var(f"storage_firm_capacity[{st_name}]")
                self.variables["storage_firm_capacity"][st_name] = firm
                power_row = {firm: 1.0}
                energy_row = {firm: critical_duration}
                if cap_idx is not None:
                    power_row[cap_idx] = -power_ratio
                    energy_row[cap_idx] = -1.0
                p.add_constraint(power_row, ub=power_ratio * existing)
                p.add_constraint(energy_row, ub=existing)

        # Demand response and unmet heat.
        dr = self.config.get("demand_response", {})
        max_shift_fraction = float(dr.get("max_shift_fraction", 0.0))
        dr_penalty = float(dr.get("penalty_yuan_per_kwh", 0.0))
        response_window = max(1, int(dr.get("response_window_hours", len(self.times))))
        unmet_penalty = float(planning.get("unserved_heat_penalty_yuan_per_kwh", 100.0))
        for node_name, node in self.nodes.items():
            for grade_name, column in node.get("demand_columns", {}).items():
                if (node_name,grade_name) in self.rc_buildings_by_node_grade:
                    continue
                response_vars: dict[int, tuple[int, int]] = {}
                for t in self.times:
                    demand = self._demand_value(node, column, t)
                    maximum_demand = demand
                    retrofit = node.get("temperature_retrofit")
                    if retrofit and grade_name == str(retrofit["target_grade"]):
                        source_column = node["demand_columns"][str(retrofit["source_grade"])]
                        maximum_demand += (
                            float(retrofit.get("eligible_fraction", 1.0))
                            * self._demand_value(node, source_column, t)
                        )
                    unmet = p.add_var(f"unserved[{node_name},{grade_name},{t}]", ub=max(maximum_demand, 0.0))
                    self.variables["unserved"][(node_name, grade_name, t)] = unmet
                    self._record_time_component("reliability_penalty", unmet, unmet_penalty, t)
                    self._record_time_metric("unserved_kwh", unmet, 1.0, t)
                    self._record_time_metric("unserved_exergy_kwh", unmet, self._exergy_factor(float(self.grades[grade_name]["temperature_c"])), t)
                    if max_shift_fraction > 0 and demand > 0:
                        up = p.add_var(f"demand_up[{node_name},{grade_name},{t}]", ub=max_shift_fraction * demand)
                        down = p.add_var(f"demand_down[{node_name},{grade_name},{t}]", ub=max_shift_fraction * demand)
                        self.variables["demand_up"][(node_name, grade_name, t)] = up
                        self.variables["demand_down"][(node_name, grade_name, t)] = down
                        response_vars[t] = (up, down)
                        self._record_time_component("comfort_cost", up, dr_penalty, t)
                        self._record_time_component("comfort_cost", down, dr_penalty, t)
                        self._record_time_metric("shifted_heat_kwh", up, 1.0, t)
                for scenario_times in self.scenario_times.values():
                    for start in range(0, len(scenario_times), response_window):
                        conservation: dict[int, float] = {}
                        for t in scenario_times[start : start + response_window]:
                            if t in response_vars:
                                up, down = response_vars[t]
                                conservation[up] = 1.0
                                conservation[down] = -1.0
                        if conservation:
                            p.add_constraint(conservation, lb=0.0, ub=0.0)

                # Building thermal mass is modeled as virtual storage around the
                # nominal indoor-temperature trajectory.
                inertia = node.get("thermal_inertia", {}).get(grade_name)
                if inertia and response_vars:
                    thermal_capacity = float(inertia["thermal_capacity_kwh_per_c"])
                    occupied_max = float(inertia.get(
                        "occupied_max_temperature_deviation_c",
                        inertia.get("max_temperature_deviation_c", 1.0),
                    ))
                    unoccupied_max = float(inertia.get("unoccupied_max_temperature_deviation_c", occupied_max))
                    max_deviation = max(occupied_max, unoccupied_max)
                    state_limit = thermal_capacity * max_deviation
                    decay = 1.0 - float(inertia.get("recovery_loss_per_hour", 0.02))
                    comfort_penalty = float(inertia.get("comfort_penalty_yuan_per_degree_hour", 0.0))
                    for t in self.times:
                        state = p.add_var(
                            f"building_thermal_state[{node_name},{grade_name},{t}]",
                            lb=-state_limit,
                            ub=state_limit,
                        )
                        absolute = p.add_var(f"building_thermal_state_abs[{node_name},{grade_name},{t}]")
                        self.variables["building_thermal_state"][(node_name, grade_name, t)] = state
                        self.variables["building_thermal_state_abs"][(node_name, grade_name, t)] = absolute
                        p.add_constraint({absolute: 1.0, state: -1.0}, lb=0.0)
                        p.add_constraint({absolute: 1.0, state: 1.0}, lb=0.0)
                        occupancy_column = inertia.get(
                            "occupancy_column",
                            node.get("intermittent_heating", {}).get("schedule_column"),
                        )
                        occupancy = (
                            self._clip_scalar(
                                self._ts_value(t, occupancy_column),
                                0.0,
                                1.0,
                            )
                            if occupancy_column else 1.0
                        )
                        allowed_deviation = (
                            occupancy * occupied_max + (1.0 - occupancy) * unoccupied_max
                        )
                        p.add_constraint({absolute: 1.0}, ub=thermal_capacity * allowed_deviation)
                        unoccupied_penalty_weight = float(inertia.get("unoccupied_penalty_weight", 0.20))
                        comfort_weight = occupancy + (1.0 - occupancy) * unoccupied_penalty_weight
                        self._record_time_component(
                            "thermal_comfort_cost", absolute,
                            comfort_penalty * comfort_weight / thermal_capacity, t,
                        )
                        self._record_time_metric(
                            "temperature_deviation_degree_hours", absolute, 1.0 / thermal_capacity, t
                        )
                        self._record_time_metric(
                            "occupied_temperature_deviation_degree_hours", absolute,
                            occupancy / thermal_capacity, t,
                        )
                    for t in self.times:
                        prev = self.time_predecessor[t]
                        row = {
                            self.variables["building_thermal_state"][(node_name, grade_name, t)]: 1.0,
                        }
                        self._add(
                            row,
                            self.variables["building_thermal_state"][(node_name, grade_name, prev)],
                            -decay,
                        )
                        if t in response_vars:
                            up, down = response_vars[t]
                            row[up] = -1.0
                            row[down] = 1.0
                        p.add_constraint(row, lb=0.0, ub=0.0)

        # Node-grade-time heat balances.
        for node_name, node in self.nodes.items():
            for grade_name, grade in self.grades.items():
                demand_column = node.get("demand_columns", {}).get(grade_name)
                for t in self.times:
                    row: dict[int, float] = {}
                    for s_name, source in self.sources.items():
                        idx = self.variables["source_heat"].get((s_name, grade_name, t))
                        if idx is not None and source["node"] == node_name:
                            self._add(row, idx, 1.0)
                    for e_name, pipe in self.pipes.items():
                        forward = self.variables["pipe_flow_forward"][(e_name, grade_name, t)]
                        if pipe["from"] == node_name:
                            self._add(row, forward, -1.0)
                        if pipe["to"] == node_name:
                            self._add(row, forward, self._pipe_efficiency(pipe, t))
                            activated = self.variables["pipe_retrofit_flow_forward"].get(
                                (e_name, grade_name, t)
                            )
                            if activated is not None:
                                gain = self._pipe_retrofit_efficiency(pipe, t) - self._pipe_efficiency(pipe, t)
                                self._add(row, activated, max(0.0, gain))
                        reverse = self.variables["pipe_flow_reverse"].get((e_name, grade_name, t))
                        if reverse is not None:
                            if pipe["to"] == node_name:
                                self._add(row, reverse, -1.0)
                            if pipe["from"] == node_name:
                                self._add(row, reverse, self._pipe_efficiency(pipe, t))
                                activated_reverse = self.variables["pipe_retrofit_flow_reverse"].get(
                                    (e_name, grade_name, t)
                                )
                                if activated_reverse is not None:
                                    gain = self._pipe_retrofit_efficiency(pipe, t) - self._pipe_efficiency(pipe, t)
                                    self._add(row, activated_reverse, max(0.0, gain))
                    for st_name, storage in self.storages.items():
                        if storage["node"] == node_name and storage["grade"] == grade_name:
                            self._add(row, self.variables["storage_discharge"][(st_name, t)], 1.0)
                            self._add(row, self.variables["storage_charge"][(st_name, t)], -1.0)
                    unmet = self.variables["unserved"].get((node_name, grade_name, t))
                    if unmet is not None:
                        self._add(row, unmet, 1.0)
                    up = self.variables["demand_up"].get((node_name, grade_name, t))
                    down = self.variables["demand_down"].get((node_name, grade_name, t))
                    if up is not None:
                        self._add(row, up, -1.0)
                    if down is not None:
                        self._add(row, down, 1.0)
                    if self.building_thermal is not None and (node_name,grade_name) in self.building_thermal.covered_node_grades:
                        delivered = self.building_thermal.node_heat[node_name,grade_name,t]
                        self._add(row,delivered,-1.0)
                        p.add_constraint(row,lb=0.0,ub=0.0)
                        self._record_time_metric("demand_kwh",delivered,1.0,t)
                        self._record_time_metric("building_heat_delivered_kwh",delivered,1.0,t)
                        self._record_time_metric("demand_exergy_kwh",delivered,self._exergy_factor(float(grade["temperature_c"])),t)
                        reference = self._demand_value(node,demand_column,t) if demand_column else 0.0
                        self.metric_constants["building_reference_heat_kwh"] += reference*self._op_weight(t)
                        self.scenario_metric_constants[self._scenario(t)]["building_reference_heat_kwh"] += reference*self._physical_weight(t)
                        self._record_metering_reference(node_name, grade, reference, t)
                        continue
                    demand = self._demand_value(node, demand_column, t) if demand_column else 0.0
                    metering = node.get("heat_metering", {})
                    existing_metering = float(
                        metering.get("existing_coverage_fraction", 0.0)
                    )
                    saving_fraction = float(
                        metering.get("saving_fraction_at_full_coverage", 0.0)
                    )
                    existing_metering_saving = (
                        demand * existing_metering * saving_fraction
                    )
                    metering_idx = self.variables["heat_metering_coverage"].get(
                        node_name
                    )
                    if metering_idx is not None and demand > 0.0 and saving_fraction > 0.0:
                        self._add(row, metering_idx, demand * saving_fraction)
                    retrofit = node.get("temperature_retrofit")
                    retrofit_idx = self.variables["terminal_temperature_retrofit"].get(node_name)
                    if retrofit and retrofit_idx is not None:
                        source_grade = str(retrofit["source_grade"])
                        target_grade = str(retrofit["target_grade"])
                        source_column = node["demand_columns"][source_grade]
                        shift = (
                            float(retrofit.get("eligible_fraction", 1.0))
                            * self._demand_value(node, source_column, t)
                        )
                        if grade_name == source_grade:
                            self._add(row, retrofit_idx, shift)
                        elif grade_name == target_grade:
                            self._add(row, retrofit_idx, -shift)
                    effective_constant_demand = demand - existing_metering_saving
                    p.add_constraint(
                        row,
                        lb=effective_constant_demand,
                        ub=effective_constant_demand,
                    )
                    if demand > 0:
                        self._record_metric_constant_demand(node_name, grade, demand, t)
                        self._record_heat_metering_effect(
                            node_name,
                            grade,
                            demand,
                            t,
                            existing_metering,
                            saving_fraction,
                            metering_idx,
                        )

        self._add_heat_reliability_constraints(planning)

        # System-wide firm capacity adequacy for northern cold-wave reliability.
        reserve_margin = float(planning.get("firm_capacity_reserve_margin", 0.0))
        building_flex_firm_capacity = self._building_flex_firm_capacity()
        if reserve_margin > 0 or planning.get("n_minus_1_source_names") or planning.get("n_minus_1_pipe_names"):
            peak_demand = self.design_peak_demand()
            firm_row: dict[int, float] = {}
            existing_firm = 0.0
            source_firm: dict[str, tuple[float, int | None, int | None, float]] = {}
            for s_name, source in self.sources.items():
                credit = float(source.get("firm_capacity_credit", 1.0))
                worst_availability = float(source.get(
                    "firm_capacity_availability",
                    min(self._source_availability(source, t) for t in self.times),
                ))
                effective_credit = credit * worst_availability
                existing_firm += effective_credit * float(source.get("existing_capacity_kw", 0.0))
                cap_idx = self.variables["source_capacity"].get(s_name)
                if cap_idx is not None:
                    firm_row[cap_idx] = effective_credit
                retire_idx = self.variables["source_retirement"].get(s_name)
                if retire_idx is not None:
                    firm_row[retire_idx] = -effective_credit
                source_firm[s_name] = (
                    effective_credit * float(source.get("existing_capacity_kw", 0.0)),
                    cap_idx, retire_idx,
                    effective_credit,
                )
            for st_name, firm_idx in self.variables["storage_firm_capacity"].items():
                credit = float(self.storages[st_name].get("firm_capacity_credit", 1.0))
                firm_row[firm_idx] = credit
            firm_requirement = max(
                0.0,
                peak_demand * (1.0 + reserve_margin) - building_flex_firm_capacity,
            )
            p.add_constraint(firm_row, lb=firm_requirement - existing_firm)
            for outage_name in planning.get("n_minus_1_source_names", []):
                if outage_name not in source_firm:
                    raise ValueError(f"N-1关键热源不存在: {outage_name}")
                outage_existing, outage_cap_idx, outage_retire_idx, _ = source_firm[outage_name]
                outage_row = dict(firm_row)
                if outage_cap_idx is not None:
                    outage_row.pop(outage_cap_idx, None)
                if outage_retire_idx is not None:
                    outage_row.pop(outage_retire_idx, None)
                p.add_constraint(
                    outage_row,
                    lb=firm_requirement - (existing_firm - outage_existing),
                )

            add_resource_firm_portfolios(self, source_firm, firm_requirement)

            # Contingency network-flow feasibility for critical pipe outages.
            # Dispatch variables are scenario-specific, while all investment
            # variables remain shared with the nominal planning problem.
            pipe_reserve_margin = float(planning.get("n_minus_1_pipe_reserve_margin", reserve_margin))
            for outage_name in planning.get("n_minus_1_pipe_names", []):
                if outage_name not in self.pipes:
                    raise ValueError(f"N-1关键管道不存在: {outage_name}")

                contingency_source: dict[str, int] = {}
                for s_name, source in self.sources.items():
                    existing_contribution, cap_idx, retire_idx, effective_credit = source_firm[s_name]
                    q = p.add_var(f"contingency_source[{outage_name},{s_name}]")
                    contingency_source[s_name] = q
                    capacity_row = {q: 1.0}
                    if cap_idx is not None:
                        capacity_row[cap_idx] = -effective_credit
                    if retire_idx is not None:
                        capacity_row[retire_idx] = effective_credit
                    p.add_constraint(capacity_row, ub=existing_contribution)

                add_firm_resource_limits(self, contingency_source, f"pipe_outage:{outage_name}")
                contingency_flow: dict[tuple[str, str], int] = {}
                for e_name, pipe in self.pipes.items():
                    if e_name == outage_name:
                        continue
                    forward = p.add_var(f"contingency_flow_forward[{outage_name},{e_name}]")
                    contingency_flow[(e_name, "forward")] = forward
                    cap_row = {forward: 1.0}
                    if pipe.get("bidirectional", False):
                        reverse = p.add_var(f"contingency_flow_reverse[{outage_name},{e_name}]")
                        contingency_flow[(e_name, "reverse")] = reverse
                        cap_row[reverse] = 1.0
                    for option in pipe.get("diameter_options", []):
                        option_idx = self.variables["pipe_choice"][(e_name, str(option["name"]))]
                        cap_row[option_idx] = -float(option["capacity_kw"])
                    p.add_constraint(cap_row, ub=float(pipe.get("existing_capacity_kw", 0.0)))

                for node_name, node in self.nodes.items():
                    balance: dict[int, float] = {}
                    for s_name, source in self.sources.items():
                        if source["node"] == node_name:
                            self._add(balance, contingency_source[s_name], 1.0)
                    for st_name, firm_idx in self.variables["storage_firm_capacity"].items():
                        if self.storages[st_name]["node"] == node_name:
                            self._add(
                                balance,
                                firm_idx,
                                float(self.storages[st_name].get("firm_capacity_credit", 1.0)),
                            )
                    for e_name, pipe in self.pipes.items():
                        if e_name == outage_name:
                            continue
                        efficiency = min(self._pipe_efficiency(pipe, t) for t in self.times)
                        forward = contingency_flow[(e_name, "forward")]
                        if pipe["from"] == node_name:
                            self._add(balance, forward, -1.0)
                        if pipe["to"] == node_name:
                            self._add(balance, forward, efficiency)
                        reverse = contingency_flow.get((e_name, "reverse"))
                        if reverse is not None:
                            if pipe["to"] == node_name:
                                self._add(balance, reverse, -1.0)
                            if pipe["from"] == node_name:
                                self._add(balance, reverse, efficiency)
                    node_peak = self.design_peak_demand(node_name)
                    required = node_peak * (1.0 + pipe_reserve_margin)
                    p.add_constraint(balance, lb=required, ub=required)

        # Optional regional microgrid: local renewable, battery and dispatchable
        # backup capacities must support critical electric heating during islanding.
        microgrid = self.config.get("microgrid_islanding")
        microgrid_specs: dict[str, tuple[int | None, float, float]] = {}
        if microgrid:
            asset_fields = {
                "renewable": ("microgrid_renewable_capacity", "renewable_capacity"),
                "battery_power": ("microgrid_battery_power", "battery_power"),
                "battery_energy": ("microgrid_battery_energy", "battery_energy"),
                "backup": ("microgrid_backup_capacity", "backup_capacity"),
            }
            for asset, (category, prefix) in asset_fields.items():
                existing = float(microgrid.get(f"existing_{prefix}_kw", 0.0))
                max_new = float(microgrid.get(f"max_new_{prefix}_kw", 0.0))
                unit = "kWh" if asset == "battery_energy" else "kW"
                if asset == "battery_energy":
                    existing = float(microgrid.get("existing_battery_energy_kwh", 0.0))
                    max_new = float(microgrid.get("max_new_battery_energy_kwh", 0.0))
                idx = None
                if max_new > 0:
                    idx = p.add_var(f"{category}[system]", ub=max_new)
                    self.variables[category]["system"] = idx
                    capex_key = (
                        "battery_energy_capex_yuan_per_kwh"
                        if asset == "battery_energy" else f"{asset}_capex_yuan_per_kw"
                    )
                    self._record_investment_cost(
                        idx, category, "system", float(microgrid.get(capex_key, 0.0))
                    )
                microgrid_specs[asset] = (idx, existing, max_new)

            duration = float(microgrid.get("island_duration_hours", 0.0))
            power_idx, existing_power, _ = microgrid_specs["battery_power"]
            energy_idx, existing_energy, _ = microgrid_specs["battery_energy"]
            if duration > 0:
                duration_row: dict[int, float] = {}
                if power_idx is not None:
                    duration_row[power_idx] = duration
                if energy_idx is not None:
                    duration_row[energy_idx] = -1.0
                p.add_constraint(duration_row, ub=existing_energy - duration * existing_power)

            critical_fraction = float(microgrid.get("critical_electric_load_fraction", 1.0))
            renewable_firm = float(microgrid.get("renewable_firm_output_factor", 0.15))
            for t in self.times:
                security = {
                    idx: coefficient * critical_fraction
                    for idx, coefficient in self._electric_load_row(t).items()
                }
                existing_local = 0.0
                for asset, factor in (("renewable", renewable_firm), ("battery_power", 1.0), ("backup", 1.0)):
                    idx, existing, _ = microgrid_specs[asset]
                    existing_local += factor * existing
                    if idx is not None:
                        self._add(security, idx, -factor)
                p.add_constraint(security, ub=existing_local)

            # Capacity adequacy alone does not prove that an islanded microgrid
            # can operate hour by hour.  Explicit renewable, backup and battery
            # dispatch closes the electricity balance of heat pumps, electric
            # boilers and circulation pumps in every modeled period.
            renewable_factor_default = float(microgrid.get(
                "operational_renewable_output_factor", renewable_firm
            ))
            renewable_column = microgrid.get("renewable_availability_column")
            eta_charge = float(microgrid.get("battery_charge_efficiency", 0.95))
            eta_discharge = float(microgrid.get("battery_discharge_efficiency", 0.95))
            retention = (1.0 - float(microgrid.get("battery_self_loss_per_hour", 0.0))) ** self.dt
            renewable_cost = float(microgrid.get("renewable_variable_cost_yuan_per_kwh", 0.0))
            renewable_emission = float(microgrid.get("renewable_emission_kg_per_kwh", 0.0))
            backup_cost = float(microgrid.get("backup_variable_cost_yuan_per_kwh", 0.0))
            backup_emission = float(microgrid.get("backup_emission_kg_per_kwh", 0.0))
            battery_throughput_cost = float(microgrid.get(
                "battery_throughput_cost_yuan_per_kwh", 0.0
            ))
            strict_battery_mode = bool(microgrid.get("strict_battery_charge_discharge_mode", False))

            renewable_idx, existing_renewable, max_new_renewable = microgrid_specs["renewable"]
            battery_power_idx, existing_battery_power, max_new_battery_power = microgrid_specs["battery_power"]
            battery_energy_idx, existing_battery_energy, _ = microgrid_specs["battery_energy"]
            backup_idx, existing_backup, max_new_backup = microgrid_specs["backup"]
            maximum_battery_power = existing_battery_power + max_new_battery_power

            for t in self.times:
                factor = renewable_factor_default
                if renewable_column:
                    factor = float(self._ts_value(t, str(renewable_column)))
                factor = self._clip_scalar(factor, 0.0, 1.0)

                renewable_use = p.add_var(f"microgrid_renewable_use[{t}]")
                backup_generation = p.add_var(f"microgrid_backup_generation[{t}]")
                battery_charge = p.add_var(f"microgrid_battery_charge[{t}]")
                battery_discharge = p.add_var(f"microgrid_battery_discharge[{t}]")
                battery_soc = p.add_var(f"microgrid_battery_soc[{t}]")
                self.variables["microgrid_renewable_use"][t] = renewable_use
                self.variables["microgrid_backup_generation"][t] = backup_generation
                self.variables["microgrid_battery_charge"][t] = battery_charge
                self.variables["microgrid_battery_discharge"][t] = battery_discharge
                self.variables["microgrid_battery_soc"][t] = battery_soc

                renewable_limit = {renewable_use: 1.0}
                if renewable_idx is not None:
                    renewable_limit[renewable_idx] = -factor
                p.add_constraint(renewable_limit, ub=factor * existing_renewable)

                backup_limit = {backup_generation: 1.0}
                if backup_idx is not None:
                    backup_limit[backup_idx] = -1.0
                p.add_constraint(backup_limit, ub=existing_backup)

                charge_limit = {battery_charge: 1.0}
                discharge_limit = {battery_discharge: 1.0}
                soc_limit = {battery_soc: 1.0}
                if battery_power_idx is not None:
                    charge_limit[battery_power_idx] = -1.0
                    discharge_limit[battery_power_idx] = -1.0
                if battery_energy_idx is not None:
                    soc_limit[battery_energy_idx] = -1.0
                p.add_constraint(charge_limit, ub=existing_battery_power)
                p.add_constraint(discharge_limit, ub=existing_battery_power)
                p.add_constraint(soc_limit, ub=existing_battery_energy)
                if strict_battery_mode:
                    battery_mode = p.add_var(
                        f"microgrid_battery_mode[{t}]", ub=1.0, integer=True
                    )
                    self.variables["microgrid_battery_mode"][t] = battery_mode
                    p.add_constraint(
                        {battery_charge: 1.0, battery_mode: -maximum_battery_power}, ub=0.0
                    )
                    p.add_constraint(
                        {battery_discharge: 1.0, battery_mode: maximum_battery_power},
                        ub=maximum_battery_power,
                    )

                # No export is assumed.  Consequently local generation and
                # battery discharge cannot exceed heating demand plus charging.
                electric_balance = {
                    renewable_use: 1.0,
                    backup_generation: 1.0,
                    battery_discharge: 1.0,
                    battery_charge: -1.0,
                }
                for index, coefficient in self._electric_load_row(t).items():
                    self._add(electric_balance, index, -coefficient)
                    self._record_time_metric("grid_electricity_kwh", index, coefficient, t)
                p.add_constraint(electric_balance, ub=0.0)

                # The original source and pump accounting assumes grid power.
                # Add charging load, then replace that grid cost/emission with
                # the actual local supply mix through linear credits.
                grid_price = self._electricity_price(t)
                grid_emission = float(
                    self._ts_value(t, "grid_emission_factor")
                )
                self._record_time_component("operating_cost", battery_charge, grid_price, t)
                self._record_time_component(
                    "carbon_cost", battery_charge, grid_emission * self.carbon_price, t
                )
                self._record_time_metric("emissions_kg", battery_charge, grid_emission, t)
                self._record_time_metric("grid_electricity_kwh", battery_charge, 1.0, t)

                for index, local_cost, local_emission, metric_name in (
                    (renewable_use, renewable_cost, renewable_emission, "microgrid_renewable_electricity_kwh"),
                    (backup_generation, backup_cost, backup_emission, "microgrid_backup_electricity_kwh"),
                    (battery_discharge, battery_throughput_cost, 0.0, "microgrid_battery_discharge_kwh"),
                ):
                    self._record_time_component(
                        "operating_cost", index, local_cost - grid_price, t
                    )
                    self._record_time_component(
                        "carbon_cost", index,
                        (local_emission - grid_emission) * self.carbon_price, t,
                    )
                    self._record_time_metric(
                        "emissions_kg", index, local_emission - grid_emission, t
                    )
                    self._record_time_metric("grid_electricity_kwh", index, -1.0, t)
                    self._record_time_metric(metric_name, index, 1.0, t)
                self._record_time_metric(
                    "microgrid_battery_charge_kwh", battery_charge, 1.0, t
                )

            battery_boundary = EventStateBoundary(
                self, microgrid, label="microgrid:battery", mode_key="battery_boundary_mode",
                initial_key="battery_initial_soc_kwh", minimum_key="battery_terminal_soc_min_kwh",
                maximum_key="battery_terminal_soc_max_kwh", existing_capacity=existing_battery_energy,
                capacity_index=battery_energy_idx,
            )
            for t in self.times:
                battery_row = {
                    self.variables["microgrid_battery_soc"][t]: 1.0,
                    self.variables["microgrid_battery_charge"][t]: -eta_charge * self.dt,
                    self.variables["microgrid_battery_discharge"][t]: self.dt / eta_discharge,
                }
                boundary_rhs = battery_boundary.add_predecessor(
                    battery_row, t, self.variables["microgrid_battery_soc"], retention)
                p.add_constraint(battery_row, lb=boundary_rhs, ub=boundary_rhs)
            battery_boundary.finish(self.variables["microgrid_battery_soc"])

        # Local electricity connection adequacy for heat pumps and circulation
        # pumps. This prevents a heat-only optimum that cannot be connected to
        # the distribution grid without reinforcement.
        grid = self.config.get("grid_connection")
        if grid:
            existing_grid = float(grid.get("existing_capacity_kw", 0.0))
            max_new_grid = float(grid.get("max_new_capacity_kw", 0.0))
            grid_idx = None
            if max_new_grid > 0:
                grid_idx = p.add_var("grid_connection_capacity", ub=max_new_grid)
                self.variables["grid_connection_capacity"]["system"] = grid_idx
                self._record_investment_cost(
                    grid_idx, "grid_connection_capacity", "system", float(grid.get("capex_yuan_per_kw", 0.0))
                )
            margin = 1.0 + float(grid.get("reserve_margin", 0.0))
            for t in self.times:
                grid_availability = 1.0
                if grid.get("availability_column"):
                    grid_availability = self._clip_scalar(
                        self._ts_value(t, grid["availability_column"]),
                        0.0,
                        1.0,
                    )
                row = self._electric_load_row(t, margin=margin)
                if microgrid:
                    for category, sign in (
                        ("microgrid_battery_charge", 1.0),
                        ("microgrid_renewable_use", -1.0),
                        ("microgrid_backup_generation", -1.0),
                        ("microgrid_battery_discharge", -1.0),
                    ):
                        self._add(row, self.variables[category][t], margin * sign)
                if grid_idx is not None:
                    self._add(row, grid_idx, -grid_availability)
                p.add_constraint(row, ub=existing_grid * grid_availability)

            # Ensure that electrically supplied firm heat capacity is actually
            # deliverable through the grid connection in a contingency.  The
            # legacy method assumes every installed electric heat source runs
            # simultaneously.  ``portfolio_dispatch`` instead selects a
            # feasible firm-heat portfolio for the peak (and each configured
            # source outage), avoiding double-counting mutually substitutable
            # heat pumps and backup boilers.
            simultaneity = float(grid.get("firm_simultaneity", 1.0))
            existing_firm_local = 0.0
            firm_local_row: dict[int, float] = {}
            if microgrid:
                renewable_firm = float(microgrid.get("renewable_firm_output_factor", 0.15))
                for asset, factor in (("renewable", renewable_firm), ("battery_power", 1.0), ("backup", 1.0)):
                    idx, existing, _ = microgrid_specs[asset]
                    existing_firm_local += factor * existing
                    if idx is not None:
                        firm_local_row[idx] = -factor

            firm_method = str(grid.get("firm_capacity_method", "all_installed_electric"))
            if firm_method not in {"all_installed_electric", "portfolio_dispatch"}:
                raise ValueError(
                    "grid_connection.firm_capacity_method 仅支持 "
                    "all_installed_electric 或 portfolio_dispatch"
                )
            if firm_method == "portfolio_dispatch":
                peak_demand = self.design_peak_demand()
                required_firm_heat = max(
                    0.0,
                    peak_demand * (
                        1.0 + float(planning.get("firm_capacity_reserve_margin", 0.0))
                    ) - building_flex_firm_capacity,
                )
                portfolio_labels = ["base", *map(str, planning.get("n_minus_1_source_names", []))]
                self.firm_grid_portfolio_electric_coefficients: dict[str, dict[int, float]] = {}
                for label in portfolio_labels:
                    outage_name = None if label == "base" else label
                    heat_row: dict[int, float] = {}
                    electric_row: dict[int, float] = dict(firm_local_row)
                    for s_name, source in self.sources.items():
                        firm_dispatch = p.add_var(f"firm_source_dispatch[{label},{s_name}]")
                        self.variables["firm_source_dispatch"][(label, s_name)] = firm_dispatch
                        effective_credit = float(source.get("firm_capacity_credit", 1.0)) * float(
                            source.get(
                                "firm_capacity_availability",
                                min(self._source_availability(source, t) for t in self.times),
                            )
                        )
                        capacity_row = {firm_dispatch: 1.0}
                        cap_idx = self.variables["source_capacity"].get(s_name)
                        retire_idx = self.variables["source_retirement"].get(s_name)
                        if cap_idx is not None:
                            capacity_row[cap_idx] = -effective_credit
                        if retire_idx is not None:
                            capacity_row[retire_idx] = effective_credit
                        existing_capacity = effective_credit * float(
                            source.get("existing_capacity_kw", 0.0)
                        )
                        p.add_constraint(
                            capacity_row,
                            ub=0.0 if s_name == outage_name else existing_capacity,
                        )
                        if s_name == outage_name and cap_idx is not None:
                            # Remove the decision-dependent capacity term too.
                            p.add_constraint({firm_dispatch: 1.0}, ub=0.0)
                        heat_row[firm_dispatch] = 1.0
                        source_type = source.get("type")
                        if source_type == "heat_pump":
                            allowed_grades = source.get("allowed_grades", list(self.grades))
                            electric_ratio = max(
                                1.0 / self._cop(source, self.grades[grade_name], t)
                                for grade_name in allowed_grades
                                for t in self.times
                            )
                            electric_row[firm_dispatch] = simultaneity * electric_ratio
                        elif source_type == "electric_boiler":
                            electric_row[firm_dispatch] = (
                                simultaneity / float(source.get("efficiency", 0.98))
                            )
                    for st_name, firm_idx in self.variables["storage_firm_capacity"].items():
                        storage_dispatch = p.add_var(
                            f"firm_storage_dispatch[{label},{st_name}]"
                        )
                        self.variables["firm_storage_dispatch"][(label, st_name)] = storage_dispatch
                        storage_credit = float(
                            self.storages[st_name].get("firm_capacity_credit", 1.0)
                        )
                        p.add_constraint(
                            {storage_dispatch: 1.0, firm_idx: -storage_credit}, ub=0.0
                        )
                        heat_row[storage_dispatch] = 1.0
                    add_firm_resource_limits(self, {
                        name: self.variables["firm_source_dispatch"][(label, name)] for name in self.sources
                    }, f"grid_portfolio:{label}")
                    p.add_constraint(heat_row, lb=required_firm_heat)
                    if grid_idx is not None:
                        electric_row[grid_idx] = -1.0
                    p.add_constraint(
                        electric_row,
                        ub=existing_grid + existing_firm_local,
                    )
                    self.firm_grid_portfolio_electric_coefficients[label] = {
                        idx: coefficient
                        for idx, coefficient in electric_row.items()
                        if idx in self.variables["firm_source_dispatch"].values()
                    }
            else:
                firm_grid_row: dict[int, float] = dict(firm_local_row)
                existing_firm_electric_kw = 0.0
                for s_name, source in self.sources.items():
                    source_type = source.get("type")
                    if source_type not in {"heat_pump", "electric_boiler"}:
                        continue
                    credit = float(source.get("firm_capacity_credit", 1.0))
                    worst_availability = float(source.get(
                        "firm_capacity_availability",
                        min(self._source_availability(source, t) for t in self.times),
                    ))
                    if source_type == "heat_pump":
                        allowed_grades = source.get("allowed_grades", list(self.grades))
                        electric_ratio = max(
                            1.0 / self._cop(source, self.grades[grade_name], t)
                            for grade_name in allowed_grades
                            for t in self.times
                        )
                    else:
                        electric_ratio = 1.0 / float(source.get("efficiency", 0.98))
                    coefficient = simultaneity * credit * worst_availability * electric_ratio
                    existing_firm_electric_kw += coefficient * float(source.get("existing_capacity_kw", 0.0))
                    cap_idx = self.variables["source_capacity"].get(s_name)
                    if cap_idx is not None:
                        firm_grid_row[cap_idx] = coefficient
                if grid_idx is not None:
                    firm_grid_row[grid_idx] = -1.0
                p.add_constraint(
                    firm_grid_row,
                    ub=existing_grid + existing_firm_local - existing_firm_electric_kw,
                )

        # Allocate locally available wind/solar electricity to heat pumps,
        # electric boilers and circulation pumps. The credit replaces grid
        # electricity cost and emissions but cannot exceed either availability
        # or the actual electric heating load.
        renewable = self.config.get("renewable_electricity")
        if renewable:
            availability_column = str(renewable["availability_column"])
            renewable_price = float(renewable.get("price_yuan_per_kwh", 0.0))
            renewable_emission = float(renewable.get("emission_kg_per_kwh", 0.0))
            for t in self.times:
                available = max(
                    0.0, float(self._ts_value(t, availability_column))
                )
                use = p.add_var(f"renewable_electricity_use[{t}]", ub=available)
                self.variables["renewable_electricity_use"][t] = use
                load_row = {use: 1.0}
                for index, coefficient in self._electric_load_row(t).items():
                    self._add(load_row, index, -coefficient)
                p.add_constraint(load_row, ub=0.0)
                grid_price = self._electricity_price(t)
                grid_emission = float(
                    self._ts_value(t, "grid_emission_factor")
                )
                self._record_time_component(
                    "operating_cost", use, -(grid_price - renewable_price), t
                )
                self._record_time_component(
                    "carbon_cost", use,
                    -(grid_emission - renewable_emission) * self.carbon_price, t,
                )
                self._record_time_metric(
                    "emissions_kg", use, -(grid_emission - renewable_emission), t
                )
                self._record_time_metric("renewable_electricity_kwh", use, 1.0, t)
                weighted = available * self._op_weight(t)
                self.metric_constants["renewable_electricity_available_kwh"] += weighted
                scenario = self._scenario(t)
                self.scenario_metric_constants[scenario]["renewable_electricity_available_kwh"] += (
                    available * self._physical_weight(t)
                )

        # Grid electricity is the residual after local renewable allocation.
        # Microgrid-enabled cases record the fuller balance (including battery
        # and backup generation) above.
        if not microgrid:
            for t in self.times:
                for index, coefficient in self._electric_load_row(t).items():
                    self._record_time_metric("grid_electricity_kwh", index, coefficient, t)
                local_renewable = self.variables["renewable_electricity_use"].get(t)
                if local_renewable is not None:
                    self._record_time_metric("grid_electricity_kwh", local_renewable, -1.0, t)

        add_monthly_electricity_tariff(self)

        # Add risk aversion only after every scenario-level operating debit and
        # credit has been posted. In particular, renewable-electricity and
        # microgrid credits are created late in the build. Building the CVaR
        # epigraph earlier would optimize a gross-cost ledger while reporting
        # a different net-cost ledger in scenario_results.
        self._add_cvar_risk_objective(planning)

        # epsilon-constraint interface for cost-emission Pareto analysis.
        emissions_cap = planning.get("emissions_cap_kg_per_year")
        if emissions_cap is not None:
            emission_row, emission_constant = self.total_service_emissions_expression()
            p.add_constraint(emission_row, ub=float(emissions_cap) - emission_constant)

        self._add_policy_constraints()

        self._built = True

    def total_service_emissions_expression(self) -> tuple[dict[int, float], float]:
        """Return the common heat/cooling/system-electricity emissions ledger.

        The historical ``emissions_kg`` component also contains shared local
        electricity credits. It must be summed with cooling's gross emissions;
        it is not, by itself, an allocated net-heating carbon footprint.
        Call after build(), or after all energy postings during build().
        """
        row: dict[int, float] = {}
        constant = 0.0
        for metric in ("emissions_kg", "cooling_emissions_kg"):
            for index, coefficient in self.metric_coefficients.get(metric, {}).items():
                self._add(row, index, coefficient)
            constant += self.metric_constants.get(metric, 0.0)
        return row, float(constant)

    def _add_cvar_risk_objective(self, planning: dict[str, Any]) -> None:
        """Add CVaR on the complete net scenario operating-cost ledger."""

        risk = planning.get("risk", {})
        cvar_weight = float(risk.get("cvar_weight", 0.0))
        if cvar_weight <= 0.0 or len(self.scenarios) <= 1:
            return
        alpha = float(risk.get("cvar_alpha", 0.90))
        if not 0.0 < alpha < 1.0:
            raise ValueError("cvar_alpha 必须位于 (0, 1)")
        # Net scenario costs can be negative after revenue/energy credits.
        # Standard CVaR uses a free threshold, not CVaR of the positive part.
        eta = self.problem.add_var("scenario_cost_var", lb=-np.inf)
        self.variables["scenario_cost_var"]["system"] = eta
        self._record_component("cvar_risk_premium", eta, cvar_weight)
        for scenario in self.scenarios:
            excess = self.problem.add_var(
                f"scenario_cost_excess[{scenario}]"
            )
            self.variables["scenario_cost_excess"][scenario] = excess
            probability = self.scenario_probabilities[scenario]
            self._record_component(
                "cvar_risk_premium",
                excess,
                cvar_weight * probability / (1.0 - alpha),
            )
            row = dict(self.scenario_cost_coefficients[scenario])
            self._add(row, eta, -1.0)
            self._add(row, excess, -1.0)
            self.problem.add_constraint(row, ub=0.0)

    def _add_heat_reliability_constraints(
        self,
        planning: dict[str, Any],
    ) -> None:
        """Enforce annual and per-scenario hard heat-service limits."""
        max_unserved_rate = float(planning.get("max_unserved_rate", 0.0))
        maximum_unserved = max_unserved_rate * self.metric_constants.get(
            "demand_kwh", 0.0
        )
        self.problem.add_constraint(
            dict(self.metric_coefficients.get("unserved_kwh", {})),
            ub=maximum_unserved,
        )
        # An expected annual limit must not hide severe shortage in a
        # low-probability cold-wave block, so every scenario gets its own cap.
        scenario_unserved_rate = float(
            planning.get("max_scenario_unserved_rate", max_unserved_rate)
        )
        for scenario in self.scenarios:
            coefficients = dict(
                self.scenario_metric_coefficients[scenario].get(
                    "unserved_kwh", {}
                )
            )
            demand = self.scenario_metric_constants[scenario].get(
                "demand_kwh", 0.0
            )
            self.problem.add_constraint(
                coefficients,
                ub=scenario_unserved_rate * demand,
            )

    def _add_policy_constraints(self) -> None:
        """Add optional transition-policy constraints after all metrics exist."""
        p = self.problem
        policy = self.policy
        target_scenarios = set(
            policy.get("transition_target_scenarios", self.scenarios)
        )
        all_scenarios = set(self.scenarios)
        unknown_scenarios = target_scenarios - all_scenarios
        if unknown_scenarios:
            raise ValueError(
                "transition_target_scenarios 包含未知场景: "
                f"{sorted(unknown_scenarios)}"
            )

        def policy_metric(metric: str) -> dict[int, float]:
            if target_scenarios == all_scenarios:
                return dict(self.metric_coefficients.get(metric, {}))
            row: dict[int, float] = {}
            for scenario in target_scenarios:
                probability = self.scenario_probabilities[scenario]
                for idx, coefficient in self.scenario_metric_coefficients[
                    scenario
                ].get(metric, {}).items():
                    self._add(row, idx, probability * coefficient)
            return row

        def policy_metric_constant(metric: str) -> float:
            if target_scenarios == all_scenarios:
                return float(self.metric_constants.get(metric, 0.0))
            return sum(
                self.scenario_probabilities[scenario]
                * self.scenario_metric_constants[scenario].get(metric, 0.0)
                for scenario in target_scenarios
            )

        if target_scenarios == all_scenarios:
            demand = self.metric_constants.get("demand_kwh", 0.0)
        else:
            demand = sum(
                self.scenario_probabilities[scenario]
                * self.scenario_metric_constants[scenario].get(
                    "demand_kwh", 0.0
                )
                for scenario in target_scenarios
            )

        def share_constraint(metric: str, target: float, minimum: bool) -> None:
            if not 0.0 <= target <= 1.0:
                raise ValueError(f"{metric} 比例目标必须位于 [0,1]")
            row = policy_metric(metric)
            for idx, coefficient in policy_metric("source_heat_kwh").items():
                self._add(row, idx, -target * coefficient)
            if minimum:
                p.add_constraint(row, lb=0.0)
            else:
                p.add_constraint(row, ub=0.0)

        if "minimum_clean_heat_share" in policy:
            share_constraint(
                "clean_heat_kwh",
                float(policy["minimum_clean_heat_share"]),
                minimum=True,
            )
        if "minimum_waste_heat_share" in policy:
            share_constraint(
                "waste_heat_kwh",
                float(policy["minimum_waste_heat_share"]),
                minimum=True,
            )
        if "minimum_biomass_heat_share" in policy:
            share_constraint(
                "biomass_heat_kwh",
                float(policy["minimum_biomass_heat_share"]),
                minimum=True,
            )
        if "minimum_non_fossil_heat_share" in policy:
            share_constraint(
                "non_fossil_heat_kwh",
                float(policy["minimum_non_fossil_heat_share"]),
                minimum=True,
            )
        if "maximum_fossil_heat_share" in policy:
            share_constraint(
                "fossil_heat_kwh",
                float(policy["maximum_fossil_heat_share"]),
                minimum=False,
            )

        fossil_existing = sum(
            float(source.get("existing_capacity_kw", 0.0))
            for source in self.sources.values()
            if source.get("resource_kind") == "fossil"
        )
        fossil_capacity_row: dict[int, float] = {}
        for s_name, source in self.sources.items():
            if source.get("resource_kind") != "fossil":
                continue
            cap_idx = self.variables["source_capacity"].get(s_name)
            retire_idx = self.variables["source_retirement"].get(s_name)
            if cap_idx is not None:
                fossil_capacity_row[cap_idx] = 1.0
            if retire_idx is not None:
                fossil_capacity_row[retire_idx] = -1.0
        if "maximum_fossil_installed_capacity_kw" in policy:
            p.add_constraint(
                fossil_capacity_row,
                ub=float(policy["maximum_fossil_installed_capacity_kw"])
                - fossil_existing,
            )
        if "minimum_fossil_backup_capacity_kw" in policy:
            p.add_constraint(
                fossil_capacity_row,
                lb=float(policy["minimum_fossil_backup_capacity_kw"])
                - fossil_existing,
            )
        if "emission_intensity_cap_kg_per_kwh" in policy:
            intensity = float(policy["emission_intensity_cap_kg_per_kwh"])
            row = policy_metric("emissions_kg")
            for idx,coefficient in policy_metric("demand_kwh").items():
                self._add(row,idx,-intensity*coefficient)
            p.add_constraint(
                row,
                # Transition targets may exclude separately audited emergency events.
                ub=intensity * demand,
            )
        if "minimum_emission_intensity_reduction_fraction" in policy:
            baseline = float(
                policy["baseline_emission_intensity_kg_per_kwh"]
            )
            reduction = float(
                policy["minimum_emission_intensity_reduction_fraction"]
            )
            row = policy_metric("emissions_kg")
            for idx,coefficient in policy_metric("demand_kwh").items():
                self._add(row,idx,-baseline*(1.0-reduction)*coefficient)
            p.add_constraint(
                row,
                ub=baseline * (1.0 - reduction) * demand,
            )
        floor_area = float(policy.get("served_floor_area_m2", 0.0))
        if "maximum_direct_fossil_emission_intensity_kg_per_m2" in policy:
            p.add_constraint(
                policy_metric("direct_fossil_emissions_kg"),
                ub=float(
                    policy[
                        "maximum_direct_fossil_emission_intensity_kg_per_m2"
                    ]
                )
                * floor_area,
            )
        if "minimum_direct_fossil_emission_reduction_fraction" in policy:
            baseline = float(
                policy[
                    "baseline_direct_fossil_emission_intensity_kg_per_m2"
                ]
            )
            reduction = float(
                policy[
                    "minimum_direct_fossil_emission_reduction_fraction"
                ]
            )
            p.add_constraint(
                policy_metric("direct_fossil_emissions_kg"),
                ub=baseline * (1.0 - reduction) * floor_area,
            )
        if "minimum_retrofit_heat_share" in policy:
            target = float(policy["minimum_retrofit_heat_share"])
            if not 0.0 <= target <= 1.0:
                raise ValueError("minimum_retrofit_heat_share 必须位于 [0,1]")
            full_demand = self.metric_constants.get("demand_kwh", demand)
            row = dict(self.metric_coefficients.get("retrofitted_heat_kwh", {}))
            for idx,coefficient in self.metric_coefficients.get("demand_kwh",{}).items():
                self._add(row,idx,-target*coefficient)
            p.add_constraint(
                row,
                lb=target * full_demand,
            )
        if "minimum_heat_metering_coverage_fraction" in policy:
            target = float(policy["minimum_heat_metering_coverage_fraction"])
            row = policy_metric("metered_heat_reference_kwh")
            p.add_constraint(
                row,
                lb=target * policy_metric_constant("metering_service_reference_kwh")
                - policy_metric_constant("metered_heat_reference_kwh"),
            )
        if "maximum_occupied_temperature_deviation_degree_hours" in policy:
            p.add_constraint(
                policy_metric("occupied_temperature_deviation_degree_hours"),
                ub=float(
                    policy[
                        "maximum_occupied_temperature_deviation_degree_hours"
                    ]
                ),
            )
        if "minimum_renewable_electricity_absorption_rate" in policy:
            target = float(
                policy["minimum_renewable_electricity_absorption_rate"]
            )
            if not 0.0 <= target <= 1.0:
                raise ValueError(
                    "minimum_renewable_electricity_absorption_rate 必须位于 [0,1]"
                )
            if target_scenarios == all_scenarios:
                available = self.metric_constants.get(
                    "renewable_electricity_available_kwh", 0.0
                )
            else:
                available = sum(
                    self.scenario_probabilities[scenario]
                    * self.scenario_metric_constants[scenario].get(
                        "renewable_electricity_available_kwh", 0.0
                    )
                    for scenario in target_scenarios
                )
            p.add_constraint(
                policy_metric("renewable_electricity_kwh"),
                lb=target * available,
            )
        if "minimum_pipe_retrofit_count" in policy:
            p.add_constraint(
                {
                    idx: 1.0
                    for idx in self.variables["pipe_retrofit"].values()
                },
                lb=float(policy["minimum_pipe_retrofit_count"]),
            )
        if "annualized_investment_budget_yuan" in policy:
            p.add_constraint(
                dict(self.component_coefficients.get("annualized_capex", {})),
                ub=float(policy["annualized_investment_budget_yuan"]),
            )
        if "public_subsidy_budget_yuan_per_year" in policy:
            p.add_constraint(
                dict(
                    self.metric_coefficients.get(
                        "public_subsidy_yuan_per_year", {}
                    )
                ),
                ub=float(policy["public_subsidy_budget_yuan_per_year"]),
            )
        if "maximum_levelized_heat_cost_yuan_per_kwh" in policy:
            regulated_cost_row: dict[int, float] = {}
            for idx, coefficient in self.metric_coefficients.get(
                "regulated_annualized_capex_yuan_per_year", {}
            ).items():
                self._add(regulated_cost_row, idx, coefficient)
            for component in HEATING_CASH_COMPONENTS:
                for idx, coefficient in self.component_coefficients.get(
                    component, {}
                ).items():
                    self._add(regulated_cost_row, idx, coefficient)
            legacy_annualized_capex = float(
                policy.get("legacy_annualized_capex_yuan", 0.0)
            )
            target = float(policy["maximum_levelized_heat_cost_yuan_per_kwh"])
            for idx,coefficient in policy_metric("demand_kwh").items():
                self._add(regulated_cost_row,idx,-target*coefficient)
            p.add_constraint(
                regulated_cost_row,
                ub=target * demand
                - legacy_annualized_capex,
            )

        if "maximum_average_household_heat_bill_yuan_per_year" in policy:
            tariff = self.config.get("heat_tariff", {})
            households = float(tariff.get("household_count", 0.0))
            variable_tariff = float(tariff.get("variable_tariff_yuan_per_kwh", 0.0))
            fixed_charge = float(
                tariff.get("fixed_charge_yuan_per_household_year", 0.0)
            )
            # Affordability is evaluated at full planned service, so the model
            # cannot satisfy a bill cap by deliberately shedding heat.  The
            # tariff itself is an explicit scenario assumption, never inferred
            # from national policy wording.
            bill_row: dict[int, float] = {}
            for idx,coefficient in policy_metric("demand_kwh").items():
                self._add(bill_row,idx,variable_tariff*coefficient)
            for idx, coefficient in policy_metric(
                "metered_heat_savings_kwh"
            ).items():
                self._add(bill_row, idx, -variable_tariff * coefficient)
            bill_constant = variable_tariff * (
                demand - policy_metric_constant("metered_heat_savings_kwh")
            ) + fixed_charge * households
            if households <= 0.0:
                raise ValueError(
                    "居民热费可负担性约束需要正的 heat_tariff.household_count"
                )
            p.add_constraint(
                bill_row,
                ub=float(
                    policy[
                        "maximum_average_household_heat_bill_yuan_per_year"
                    ]
                )
                * households
                - bill_constant,
            )

    def _record_metric_constant_demand(self, node_name: str, grade: dict[str, Any], demand: float, t: int) -> None:
        # Constants are accumulated outside the variable coefficient maps.
        self.metric_constants["demand_kwh"] += demand * self._op_weight(t)
        self.metric_constants["metering_service_reference_kwh"] += demand * self._op_weight(t)
        self.metric_constants["demand_exergy_kwh"] += (
            demand * self._exergy_factor(float(grade["temperature_c"])) * self._op_weight(t)
        )
        scenario = self._scenario(t)
        self.scenario_metric_constants[scenario]["demand_kwh"] += demand * self._physical_weight(t)
        self.scenario_metric_constants[scenario]["metering_service_reference_kwh"] += demand * self._physical_weight(t)
        self.scenario_metric_constants[scenario]["demand_exergy_kwh"] += (
            demand * self._exergy_factor(float(grade["temperature_c"])) * self._physical_weight(t)
        )

    def _record_metering_reference(self, node_name: str, grade: dict, reference: float, t: int) -> None:
        """RC metering coverage uses fixed no-gain reference weights, not Q*coverage."""
        self.metric_constants["metering_service_reference_kwh"] += reference*self._op_weight(t)
        self.scenario_metric_constants[self._scenario(t)]["metering_service_reference_kwh"] += reference*self._physical_weight(t)
        self._record_heat_metering_effect(node_name, grade, reference, t,
            float(self.nodes[node_name].get("heat_metering",{}).get("existing_coverage_fraction",0.)),
            0., self.variables["heat_metering_coverage"].get(node_name))

    def _record_heat_metering_effect(
        self,
        node_name: str,
        grade: dict[str, Any],
        demand: float,
        t: int,
        existing_coverage: float,
        saving_fraction: float,
        coverage_index: int | None,
    ) -> None:
        """Record metering coverage, avoided heat and the exergy correction."""

        expected_weight = self._op_weight(t)
        physical_weight = self._physical_weight(t)
        scenario = self._scenario(t)
        existing_reference = demand * existing_coverage
        existing_saving = existing_reference * saving_fraction
        exergy_factor = self._exergy_factor(float(grade["temperature_c"]))
        self.metric_constants["metered_heat_reference_kwh"] += (
            existing_reference * expected_weight
        )
        self.metric_constants["metered_heat_savings_kwh"] += (
            existing_saving * expected_weight
        )
        self.metric_constants["demand_exergy_kwh"] -= (
            existing_saving * exergy_factor * expected_weight
        )
        self.scenario_metric_constants[scenario]["metered_heat_reference_kwh"] += (
            existing_reference * physical_weight
        )
        self.scenario_metric_constants[scenario]["metered_heat_savings_kwh"] += (
            existing_saving * physical_weight
        )
        self.scenario_metric_constants[scenario]["demand_exergy_kwh"] -= (
            existing_saving * exergy_factor * physical_weight
        )
        if coverage_index is None:
            return
        self._record_time_metric(
            "metered_heat_reference_kwh", coverage_index, demand, t
        )
        self._record_time_metric(
            "metered_heat_savings_kwh",
            coverage_index,
            demand * saving_fraction,
            t,
        )
        self._record_time_metric(
            "demand_exergy_kwh",
            coverage_index,
            -demand * saving_fraction * exergy_factor,
            t,
        )

    @staticmethod
    def _dot(coeffs: dict[int, float], values: np.ndarray) -> float:
        return float(sum(coef * values[idx] for idx, coef in coeffs.items()))

    def solve(
        self,
        mip_gap: float | None = None,
        time_limit: float | None = None,
        objective_mode: str = "total_cost",
    ) -> SolveArtifacts:
        self.build()
        planning = self.config["planning"]
        if objective_mode not in {"total_cost", "emissions"}:
            raise ValueError("objective_mode 仅支持 total_cost 或 emissions")
        original_objective = list(self.problem.objective)
        if objective_mode == "emissions":
            # A fixed epsilon times cost can reverse the carbon optimum when
            # cost scales change. Ties are handled by an explicit second solve.
            self.problem.objective = [0.0] * len(original_objective)
            emission_row, _ = self.total_service_emissions_expression()
            for idx, coefficient in emission_row.items():
                self.problem.objective[idx] = coefficient
        solve_started = perf_counter()
        try:
            result = self.problem.solve(
                mip_gap=float(mip_gap if mip_gap is not None else planning.get("mip_gap", 0.005)),
                time_limit=float(time_limit if time_limit is not None else planning.get("time_limit_seconds", 120.0)),
            )
        finally:
            self.problem.objective = original_objective
        solver_elapsed_seconds = perf_counter() - solve_started
        if result.x is None:
            raise RuntimeError(f"MILP 未得到可行解: status={result.status}, message={result.message}")
        values = np.asarray(result.x)
        summary = self._make_summary(result, values, objective_mode)
        summary["solver_elapsed_seconds"] = solver_elapsed_seconds
        summary["matrix_compile_seconds"] = float(
            self.problem.last_matrix_compile_seconds
        )
        summary["matrix_compile_cache_hit"] = bool(
            self.problem.last_matrix_cache_hit
        )
        summary.update(self.problem.audit_solution(values))
        emissions = float(summary["total_service_emissions_kg_per_year"])
        cap = planning.get("emissions_cap_kg_per_year")
        summary["emissions_cap_scope"] = "total_heating_cooling_and_shared_electricity"
        summary["emissions_cap_kg_per_year"] = float(cap) if cap is not None else None
        summary["emissions_cap_residual_kg"] = max(0.0, emissions-float(cap)) if cap is not None else 0.0
        summary["emissions_cap_pass"] = bool(cap is None or emissions <= float(cap) + 1e-6 + 1e-8*max(abs(emissions),abs(float(cap)),1.0))
        summary["legacy_emissions_component_semantics"] = "heating_ledger_plus_shared_electricity_credits; add_cooling_for_system_total"
        trajectory = comfort = None
        if self.building_thermal is not None:
            trajectory = self.building_thermal.export_trajectory(values)
            comfort = self.building_thermal.comfort_summary(values)
            simultaneous = bool(((trajectory.heat_kw > 1e-6)&(trajectory.cooling_kw > 1e-6)).any())
            summary.update({
                "building_model": "delivered_heat_cooling_exact_1R1C",
                "building_count": len(self.building_inputs.buildings),
                "building_comfort_state_passed": bool(comfort.building_physics_passed.all()) and not simultaneous,
                "building_no_simultaneous_heat_cooling_passed": not simultaneous,
                "building_max_rc_residual_c": float(comfort.max_rc_residual_c.max()),
                "building_under_degree_hours_end_sample": float(comfort.all_service_under_degree_hours_end_sample.sum()),
                "building_over_degree_hours_end_sample": float(comfort.all_service_over_degree_hours_end_sample.sum()),
                "building_reference_heat_kwh_per_year": float(self.metric_constants.get("building_reference_heat_kwh",0.)),
                "heat_service_metric_scope": "actual_RC_delivered_heat_plus_legacy_fixed_service; RC_comfort_certified_separately",
                "heat_unserved_metric_scope": "unserved_fixed_exogenous_service_only; no_RC_temperature_slack",
                "building_mode_formulation": self.building_inputs.simultaneous_mode,
                "building_mode_integer_variables": len(self.building_thermal.mode),
                "design_peak_heat_demand_kw": self.design_peak_demand(),
            })
        investments = self._make_investments(values)
        dispatch = self._make_dispatch(values)
        return SolveArtifacts(result, values, summary, investments, dispatch, trajectory, comfort)

    def solve_lexicographic(
        self, primary: str = "emissions", *, absolute_tolerance: float = 1e-7,
        relative_tolerance: float = 1e-8, mip_gap: float | None = None,
        time_limit: float | None = None,
    ) -> SolveArtifacts:
        """Two-stage endpoint with an explicit primary-objective allowance.

        Each stage uses the configured solve budget. A time-limited first
        stage only certifies a secondary solution within its incumbent bound,
        not a globally proven lexicographic optimum. The returned summary
        retains both stages' termination evidence. This instance keeps the
        primary bound, so use a fresh model for each independent endpoint.
        """
        if primary not in {"total_cost", "emissions"}:
            raise ValueError("primary must be total_cost or emissions")
        if not np.isfinite(absolute_tolerance) or not np.isfinite(relative_tolerance) or absolute_tolerance < 0 or relative_tolerance < 0:
            raise ValueError("lexicographic tolerances must be finite and nonnegative")
        first = self.solve(mip_gap=mip_gap, time_limit=time_limit, objective_mode=primary)
        if primary == "emissions":
            coefficients, constant = self.total_service_emissions_expression()
            value = float(first.summary["total_service_emissions_kg_per_year"])
            secondary = "total_cost"
        else:
            coefficients = {i:c for i,c in enumerate(self.problem.objective) if c}
            constant = 0.0
            value = self._dot(coefficients, first.values)
            secondary = "emissions"
        allowance = absolute_tolerance + relative_tolerance * abs(value)
        self.problem.add_constraint(coefficients, ub=value+allowance-constant)
        second = self.solve(mip_gap=mip_gap, time_limit=time_limit, objective_mode=secondary)
        second.summary.update({
            "endpoint_method": "two_stage_lexicographic_with_reported_tolerance",
            "primary_objective": primary, "primary_incumbent_value": value,
            "primary_objective_allowance": allowance,
            "primary_solver_status": first.summary["solver_status"],
            "primary_mip_gap": first.summary.get("mip_gap"),
            "primary_numerical_audit_passed": first.summary["numerical_audit_passed"],
            "primary_solver_elapsed_seconds": first.summary["solver_elapsed_seconds"],
            "endpoint_solver_elapsed_seconds": first.summary["solver_elapsed_seconds"]+second.summary["solver_elapsed_seconds"],
        })
        return second

    def _make_summary(self, result: Any, values: np.ndarray, objective_mode: str) -> dict[str, Any]:
        def physical_nonnegative(value: float, tolerance: float = 1e-9) -> float:
            parsed = float(value)
            return 0.0 if parsed <= tolerance else parsed

        components = {name: self._dot(coefs, values) for name, coefs in self.component_coefficients.items()}
        metrics = {name: self._dot(coefs, values) for name, coefs in self.metric_coefficients.items()}
        # Some KPIs contain both a fixed baseline and a decision-dependent
        # correction.  In particular, terminal temperature retrofit shifts
        # demand exergy from the high- to the low-temperature grade.  ``update``
        # used to overwrite that correction with the baseline constant.
        for name, value in self.metric_constants.items():
            metrics[name] = metrics.get(name, 0.0) + value
        demand = metrics.get("demand_kwh", 0.0)
        unserved = physical_nonnegative(metrics.get("unserved_kwh", 0.0))
        cooling_demand = metrics.get("cooling_demand_kwh", 0.0)
        cooling_unserved = physical_nonnegative(
            metrics.get("cooling_unserved_kwh", 0.0)
        )
        source_heat = metrics.get("source_heat_kwh", 0.0)
        source_exergy = metrics.get("source_exergy_kwh", 0.0)
        demand_exergy_served = max(0.0,metrics.get("demand_exergy_kwh", 0.0)-metrics.get("unserved_exergy_kwh",0.0))
        raw_exergy_efficiency = demand_exergy_served / max(source_exergy, 1e-9)
        exergy_residual = source_exergy - demand_exergy_served
        exergy_tolerance = max(1e-6, 1e-6 * max(source_exergy, demand_exergy_served, 1.0))
        exergy_accounting_valid = (
            -exergy_tolerance <= exergy_residual
            and -exergy_tolerance <= raw_exergy_efficiency <= 1.0 + exergy_tolerance
        )
        annualized_system_cost = float(sum(components.values()))
        regulated_annualized_capex = metrics.get(
            "regulated_annualized_capex_yuan_per_year",
            components.get("annualized_capex", 0.0),
        )
        heating_cash_operating_cost = sum(
            components.get(name, 0.0) for name in HEATING_CASH_COMPONENTS
        )
        incremental_regulated_cost = (
            regulated_annualized_capex + heating_cash_operating_cost
        )
        legacy_annualized_capex = float(self.policy.get("legacy_annualized_capex_yuan", 0.0))
        regulated_cost = incremental_regulated_cost + legacy_annualized_capex
        served_heat = max(0.0, demand - unserved)
        served_cooling = max(0.0, cooling_demand - cooling_unserved)
        served_energy = served_heat + served_cooling
        shared_capex = components.get("annualized_capex", 0.0)
        heat_capex_allocation = shared_capex * served_heat / max(served_energy, 1e-9)
        cooling_capex_allocation = shared_capex * served_cooling / max(served_energy, 1e-9)
        heating_variable_cost = heating_cash_operating_cost
        cooling_variable_cost = (
            components.get("cooling_operating_cost", 0.0)
            + components.get("cooling_carbon_cost", 0.0)
        )
        microgrid = self.config.get("microgrid_islanding", {})
        def installed_microgrid(category: str, existing_key: str) -> float:
            idx = self.variables[category].get("system")
            return float(microgrid.get(existing_key, 0.0)) + (float(values[idx]) if idx is not None else 0.0)
        grid = self.config.get("grid_connection", {})
        grid_capacity_idx = self.variables["grid_connection_capacity"].get("system")
        installed_grid_capacity = float(grid.get("existing_capacity_kw", 0.0)) + (
            float(values[grid_capacity_idx]) if grid_capacity_idx is not None else 0.0
        )
        portfolio_grid_requirement = max(
            (
                self._dot(coefficients, values)
                for coefficients in getattr(
                    self, "firm_grid_portfolio_electric_coefficients", {}
                ).values()
            ),
            default=0.0,
        )
        biomass_inventory_capacity = sum(
            float(chain.get("existing_inventory_capacity_kg", 0.0))
            + (
                float(values[self.variables["biomass_inventory_capacity"][str(chain["name"])]])
                if str(chain["name"]) in self.variables["biomass_inventory_capacity"] else 0.0
            )
            for chain in self.config.get("biomass_supply_chains", [])
        )
        served_floor_area = float(self.policy.get("served_floor_area_m2", 0.0))
        direct_fossil_emissions = metrics.get(
            "direct_fossil_emissions_kg", 0.0
        )
        direct_fossil_emission_intensity = (
            direct_fossil_emissions / served_floor_area
            if served_floor_area > 0.0
            else None
        )
        metering_savings = metrics.get("metered_heat_savings_kwh", 0.0)
        metered_reference_heat = metrics.get("metered_heat_reference_kwh", 0.0)
        effective_heat_demand = max(0.0, demand - metering_savings)
        heat_tariff = self.config.get("heat_tariff", {})
        tariff_households = float(heat_tariff.get("household_count", 0.0))
        variable_heat_tariff = float(
            heat_tariff.get("variable_tariff_yuan_per_kwh", 0.0)
        )
        fixed_heat_charge = float(
            heat_tariff.get("fixed_charge_yuan_per_household_year", 0.0)
        )
        billable_heat = effective_heat_demand
        indicative_heat_charge = (
            variable_heat_tariff * billable_heat
            + fixed_heat_charge * tariff_households
        )
        raw_mip_gap = getattr(result, "mip_gap", None)
        mip_gap = float(raw_mip_gap) if raw_mip_gap is not None else np.nan
        raw_mip_node_count = getattr(result, "mip_node_count", None)
        mip_node_count = (
            int(raw_mip_node_count) if raw_mip_node_count is not None else None
        )
        raw_mip_dual_bound = getattr(result, "mip_dual_bound", None)
        mip_dual_bound = (
            float(raw_mip_dual_bound)
            if raw_mip_dual_bound is not None
            else None
        )
        unit_commitment_start_events = 0.0
        unit_commitment_shutdown_events = 0.0
        expected_annual_start_events = 0.0
        expected_annual_shutdown_events = 0.0
        unit_commitment_transition_residual = 0.0
        unit_commitment_indicator_fractionality = 0.0
        for (source_name, t), on_index in self.variables[
            "source_on"
        ].items():
            previous = self.time_predecessor[t]
            previous_on_index = self.variables["source_on"][
                (source_name, previous)
            ]
            startup_index = self.variables["source_startup"][(source_name, t)]
            shutdown_index = self.variables["source_shutdown"][(source_name, t)]
            on_value = float(values[on_index])
            previous_on_value = float(values[previous_on_index])
            startup_value = float(values[startup_index])
            shutdown_value = float(values[shutdown_index])
            transition = on_value - previous_on_value
            unit_commitment_start_events += startup_value
            unit_commitment_shutdown_events += shutdown_value
            expected_annual_start_events += (
                startup_value * self._event_weight(t)
            )
            expected_annual_shutdown_events += (
                shutdown_value * self._event_weight(t)
            )
            unit_commitment_transition_residual = max(
                unit_commitment_transition_residual,
                abs(startup_value - max(0.0, transition)),
                abs(shutdown_value - max(0.0, -transition)),
            )
            for indicator in (on_value, startup_value, shutdown_value):
                unit_commitment_indicator_fractionality = max(
                    unit_commitment_indicator_fractionality,
                    min(abs(indicator), abs(1.0 - indicator)),
                )
        small_coal_boiler_existing_capacity = 0.0
        small_coal_boiler_retired_capacity = 0.0
        small_boiler_threshold = float(
            self.policy.get("small_coal_boiler_phaseout_max_t_per_hour", 10.0)
        )
        for source_name, source in self.sources.items():
            if (
                source.get("type") != "boiler"
                or source.get("resource_kind") != "fossil"
                or "boiler_capacity_t_per_hour" not in source
                or float(source["boiler_capacity_t_per_hour"])
                > small_boiler_threshold + 1e-9
            ):
                continue
            small_coal_boiler_existing_capacity += float(
                source.get("existing_capacity_kw", 0.0)
            )
            retire_idx = self.variables["source_retirement"].get(source_name)
            if retire_idx is not None:
                small_coal_boiler_retired_capacity += float(values[retire_idx])
        scenario_results: dict[str, dict[str, float]] = {}
        for scenario in self.scenarios:
            scenario_metrics = {
                name: self._dot(coefs, values)
                for name, coefs in self.scenario_metric_coefficients[scenario].items()
            }
            for name, value in self.scenario_metric_constants[scenario].items():
                scenario_metrics[name] = scenario_metrics.get(name, 0.0) + value
            scenario_demand = scenario_metrics.get("demand_kwh", 0.0)
            scenario_unserved = physical_nonnegative(
                scenario_metrics.get("unserved_kwh", 0.0)
            )
            scenario_cooling_demand = scenario_metrics.get("cooling_demand_kwh", 0.0)
            scenario_cooling_unserved = physical_nonnegative(
                scenario_metrics.get("cooling_unserved_kwh", 0.0)
            )
            scenario_source_heat = scenario_metrics.get(
                "source_heat_kwh", 0.0
            )
            scenario_metering_savings = scenario_metrics.get(
                "metered_heat_savings_kwh", 0.0
            )
            scenario_effective_demand = max(
                0.0, scenario_demand - scenario_metering_savings
            )
            scenario_results[scenario] = {
                "probability": self.scenario_probabilities[scenario],
                "operating_risk_cost_yuan": self._dot(self.scenario_cost_coefficients[scenario], values),
                "demand_kwh": scenario_demand,
                "unserved_kwh": scenario_unserved,
                "unserved_rate": scenario_unserved / max(scenario_demand, 1e-9),
                "effective_heat_demand_kwh": scenario_effective_demand,
                "metered_heat_savings_kwh": scenario_metering_savings,
                "heat_metering_coverage_fraction": scenario_metrics.get(
                    "metered_heat_reference_kwh", 0.0
                )
                / max(scenario_metrics.get("metering_service_reference_kwh", scenario_demand), 1e-9),
                "cooling_demand_kwh": scenario_cooling_demand,
                "cooling_unserved_kwh": scenario_cooling_unserved,
                "cooling_unserved_rate": scenario_cooling_unserved
                / max(scenario_cooling_demand, 1e-9),
                "cooling_electricity_kwh": scenario_metrics.get(
                    "cooling_electricity_kwh", 0.0
                ),
                "cooling_emissions_kg": scenario_metrics.get(
                    "cooling_emissions_kg", 0.0
                ),
                "emissions_kg": scenario_metrics.get("emissions_kg", 0.0),
                "clean_heat_share": scenario_metrics.get(
                    "clean_heat_kwh", 0.0
                )
                / max(scenario_source_heat, 1e-9),
                "non_fossil_heat_share": scenario_metrics.get(
                    "non_fossil_heat_kwh", 0.0
                )
                / max(scenario_source_heat, 1e-9),
                "waste_heat_share": scenario_metrics.get(
                    "waste_heat_kwh", 0.0
                )
                / max(scenario_source_heat, 1e-9),
                "fossil_heat_share": scenario_metrics.get(
                    "fossil_heat_kwh", 0.0
                )
                / max(scenario_source_heat, 1e-9),
                "direct_fossil_emissions_kg": scenario_metrics.get(
                    "direct_fossil_emissions_kg", 0.0
                ),
                "electricity_kwh": scenario_metrics.get("electricity_kwh", 0.0),
                "grid_electricity_kwh": scenario_metrics.get("grid_electricity_kwh", 0.0),
                "renewable_electricity_kwh": scenario_metrics.get(
                    "renewable_electricity_kwh", 0.0
                ),
                "microgrid_renewable_electricity_kwh": scenario_metrics.get(
                    "microgrid_renewable_electricity_kwh", 0.0
                ),
                "microgrid_backup_electricity_kwh": scenario_metrics.get(
                    "microgrid_backup_electricity_kwh", 0.0
                ),
                "microgrid_battery_charge_kwh": scenario_metrics.get(
                    "microgrid_battery_charge_kwh", 0.0
                ),
                "microgrid_battery_discharge_kwh": scenario_metrics.get(
                    "microgrid_battery_discharge_kwh", 0.0
                ),
            }
        return {
            "solver_status": int(result.status),
            "solver_message": str(result.message),
            "objective_mode": objective_mode,
            "objective_value": float(result.fun),
            "objective_yuan_per_year": annualized_system_cost,
            "mip_gap": mip_gap,
            "mip_node_count": mip_node_count,
            "mip_dual_bound": mip_dual_bound,
            "unit_commitment_binary_state_variables": len(
                self.variables["source_on"]
            ),
            "unit_commitment_continuous_transition_variables": (
                len(self.variables["source_startup"])
                + len(self.variables["source_shutdown"])
            ),
            "unit_commitment_start_events": unit_commitment_start_events,
            "unit_commitment_shutdown_events": unit_commitment_shutdown_events,
            "expected_annual_start_events": expected_annual_start_events,
            "expected_annual_shutdown_events": expected_annual_shutdown_events,
            "unit_commitment_max_transition_residual": (
                unit_commitment_transition_residual
            ),
            "unit_commitment_max_indicator_fractionality": (
                unit_commitment_indicator_fractionality
            ),
            "components_yuan_per_year": components,
            **extension_summary(self, values),
            **({"replay_service_policy": self.config["replay_service_policy"]}
               if "replay_service_policy" in self.config else {}),
            "emissions_kg_per_year": metrics.get("emissions_kg", 0.0),
            "cooling_emissions_kg_per_year": metrics.get("cooling_emissions_kg", 0.0),
            "total_service_emissions_kg_per_year": metrics.get("emissions_kg", 0.0)
            + metrics.get("cooling_emissions_kg", 0.0),
            "electricity_kwh_per_year": metrics.get("electricity_kwh", 0.0),
            "grid_electricity_kwh_per_year": metrics.get("grid_electricity_kwh", 0.0),
            "microgrid_renewable_electricity_kwh_per_year": metrics.get(
                "microgrid_renewable_electricity_kwh", 0.0
            ),
            "microgrid_backup_electricity_kwh_per_year": metrics.get(
                "microgrid_backup_electricity_kwh", 0.0
            ),
            "microgrid_battery_charge_kwh_per_year": metrics.get(
                "microgrid_battery_charge_kwh", 0.0
            ),
            "microgrid_battery_discharge_kwh_per_year": metrics.get(
                "microgrid_battery_discharge_kwh", 0.0
            ),
            "renewable_electricity_kwh_per_year": metrics.get("renewable_electricity_kwh", 0.0),
            "renewable_electricity_absorption_rate": metrics.get("renewable_electricity_kwh", 0.0)
            / max(metrics.get("renewable_electricity_available_kwh", 0.0), 1e-9),
            "chp_electricity_kwh_per_year": metrics.get("chp_electricity_kwh", 0.0),
            "demand_kwh_per_year": demand,
            "building_heat_delivered_kwh_per_year": metrics.get("building_heat_delivered_kwh", 0.0),
            "building_cooling_delivered_kwh_per_year": metrics.get("building_cooling_delivered_kwh", 0.0),
            "unserved_kwh_per_year": unserved,
            "unserved_rate": unserved / max(demand, 1e-9),
            "rejected_heat_kwh_per_year": metrics.get(
                "rejected_heat_kwh", 0.0
            ),
            "small_coal_boiler_existing_capacity_kw": (
                small_coal_boiler_existing_capacity
            ),
            "small_coal_boiler_retired_capacity_kw": (
                small_coal_boiler_retired_capacity
            ),
            "small_coal_boiler_remaining_capacity_kw": max(
                0.0,
                small_coal_boiler_existing_capacity
                - small_coal_boiler_retired_capacity,
            ),
            "effective_heat_demand_kwh_per_year": effective_heat_demand,
            "metered_heat_savings_kwh_per_year": metering_savings,
            "heat_metering_coverage_fraction": metered_reference_heat
            / max(metrics.get("metering_service_reference_kwh", demand), 1e-9),
            "heat_metering_coverage_scope": "fixed_no_gain_heat_reference_weighted",
            "heat_tariff_household_count": tariff_households,
            "indicative_heat_charge_yuan_per_year": indicative_heat_charge,
            "average_indicative_household_heat_bill_yuan_per_year": (
                indicative_heat_charge / tariff_households
                if tariff_households > 0.0
                else None
            ),
            "cooling_demand_kwh_per_year": cooling_demand,
            "cooling_unserved_kwh_per_year": cooling_unserved,
            "cooling_unserved_rate": cooling_unserved / max(cooling_demand, 1e-9),
            "cooling_electricity_kwh_per_year": metrics.get(
                "cooling_electricity_kwh", 0.0
            ),
            "ground_heat_extraction_kwh_per_year": metrics.get(
                "ground_heat_extraction_kwh", 0.0
            ),
            "ground_heat_rejection_kwh_per_year": metrics.get(
                "ground_heat_rejection_kwh", 0.0
            ),
            "ground_extraction_to_rejection_ratio": metrics.get(
                "ground_heat_extraction_kwh", 0.0
            ) / max(metrics.get("ground_heat_rejection_kwh", 0.0), 1e-9),
            "joint_service_levelized_cost_yuan_per_kwh": annualized_system_cost
            / max(served_energy, 1e-9),
            "energy_proportional_levelized_heat_cost_yuan_per_kwh": (
                heat_capex_allocation + heating_variable_cost
            ) / max(served_heat, 1e-9),
            "energy_proportional_levelized_cooling_cost_yuan_per_kwh": (
                cooling_capex_allocation + cooling_variable_cost
            ) / max(served_cooling, 1e-9),
            "clean_heat_share": metrics.get("clean_heat_kwh", 0.0) / max(source_heat, 1e-9),
            "non_fossil_heat_share": metrics.get("non_fossil_heat_kwh", 0.0)
            / max(source_heat, 1e-9),
            "waste_heat_share": metrics.get("waste_heat_kwh", 0.0) / max(source_heat, 1e-9),
            "fossil_heat_share": metrics.get("fossil_heat_kwh", 0.0) / max(source_heat, 1e-9),
            "emission_intensity_kg_per_kwh": metrics.get("emissions_kg", 0.0) / max(demand, 1e-9),
            "legacy_heat_emission_intensity_valid": not bool(self.config.get("multi_energy_services", {}).get("cooling", {}).get("enabled", False)),
            "total_service_emission_intensity_kg_per_kwh": (metrics.get("emissions_kg",0.)+metrics.get("cooling_emissions_kg",0.))/max(served_energy,1e-9),
            "direct_fossil_emissions_kg_per_year": direct_fossil_emissions,
            "served_floor_area_m2": served_floor_area,
            "direct_fossil_emission_intensity_kg_per_m2": direct_fossil_emission_intensity,
            "regulated_levelized_heat_cost_yuan_per_kwh": regulated_cost / max(demand, 1e-9),
            "stage_incremental_levelized_heat_cost_yuan_per_kwh": incremental_regulated_cost
            / max(demand, 1e-9),
            "legacy_annualized_capex_yuan_per_year": legacy_annualized_capex,
            "regulated_annualized_capex_yuan_per_year": regulated_annualized_capex,
            "heating_cash_operating_cost_yuan_per_year": heating_cash_operating_cost,
            "shared_non_heating_capex_yuan_per_year": metrics.get(
                "shared_non_heating_capex_yuan_per_year", 0.0
            ),
            "public_subsidy_yuan_per_year": metrics.get("public_subsidy_yuan_per_year", 0.0),
            "retrofitted_heat_share": metrics.get("retrofitted_heat_kwh", 0.0) / max(demand, 1e-9),
            "shifted_heat_kwh_per_year": metrics.get("shifted_heat_kwh", 0.0),
            "exergy_loss_kwh_per_year": metrics.get("exergy_loss_kwh", 0.0),
            # Preserve the historical bounded KPI for compatibility, but also
            # expose whether clipping hid an accounting inconsistency.
            "exergy_efficiency": self._clip_scalar(
                raw_exergy_efficiency, 0.0, 1.0
            ),
            "raw_exergy_efficiency": raw_exergy_efficiency,
            "exergy_accounting_valid": bool(exergy_accounting_valid),
            "exergy_balance_residual_kwh": exergy_residual,
            "temperature_deviation_degree_hours": metrics.get("temperature_deviation_degree_hours", 0.0),
            "occupied_temperature_deviation_degree_hours": metrics.get(
                "occupied_temperature_deviation_degree_hours", 0.0
            ),
            "biomass_fuel_kg_per_year": metrics.get("biomass_fuel_kg", 0.0),
            "biomass_heat_kwh_per_year": metrics.get("biomass_heat_kwh", 0.0),
            "grid_connection_capacity_kw": installed_grid_capacity,
            "firm_grid_capacity_method": str(
                grid.get("firm_capacity_method", "all_installed_electric")
            ) if grid else "not_applicable",
            "firm_portfolio_electric_requirement_kw": portfolio_grid_requirement,
            "building_flex_firm_capacity_kw": self._building_flex_firm_capacity(),
            "microgrid_island_design_enabled": bool(self.config.get("microgrid_islanding")),
            "biomass_inventory_capacity_kg": biomass_inventory_capacity,
            "microgrid_renewable_capacity_kw": installed_microgrid(
                "microgrid_renewable_capacity", "existing_renewable_capacity_kw"
            ),
            "microgrid_battery_power_kw": installed_microgrid(
                "microgrid_battery_power", "existing_battery_power_kw"
            ),
            "microgrid_battery_energy_kwh": installed_microgrid(
                "microgrid_battery_energy", "existing_battery_energy_kwh"
            ),
            "microgrid_backup_capacity_kw": installed_microgrid(
                "microgrid_backup_capacity", "existing_backup_capacity_kw"
            ),
            "worst_scenario_unserved_rate": max(
                (row["unserved_rate"] for row in scenario_results.values()), default=0.0
            ),
            "worst_scenario_cooling_unserved_rate": max(
                (
                    row["cooling_unserved_rate"]
                    for row in scenario_results.values()
                ),
                default=0.0,
            ),
            "scenario_results": scenario_results,
        }

    def _make_investments(self, values: np.ndarray) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        clean = lambda x: 0.0 if abs(float(x)) < 1e-8 else float(x)
        for name, idx in self.variables["source_capacity"].items():
            rows.append({"category": "source_capacity", "asset": name, "option": "new", "value": clean(values[idx]), "unit": "kW"})
        for name, idx in self.variables["source_retirement"].items():
            rows.append({"category": "source_retirement", "asset": name, "option": "decommission", "value": clean(values[idx]), "unit": "kW"})
        for name, idx in self.variables["storage_capacity"].items():
            rows.append({"category": "storage_capacity", "asset": name, "option": "new", "value": clean(values[idx]), "unit": "kWh"})
        for name, idx in self.variables["biomass_inventory_capacity"].items():
            rows.append({"category": "biomass_inventory_capacity", "asset": name, "option": "new", "value": clean(values[idx]), "unit": "kg"})
        for (name, option), idx in self.variables["pipe_choice"].items():
            rows.append({"category": "pipe_choice", "asset": name, "option": option, "value": clean(values[idx]), "unit": "binary"})
        for name, idx in self.variables["pipe_retrofit"].items():
            rows.append({"category": "pipe_retrofit", "asset": name, "option": "energy_efficiency", "value": clean(values[idx]), "unit": "binary"})
        for name, idx in self.variables["terminal_temperature_retrofit"].items():
            rows.append({"category": "terminal_temperature_retrofit", "asset": name, "option": "high_to_low", "value": clean(values[idx]), "unit": "binary"})
        for name, idx in self.variables["heat_metering_coverage"].items():
            rows.append({"category": "heat_metering_coverage", "asset": name, "option": "new", "value": clean(values[idx]), "unit": "fraction"})
        for name, idx in self.variables["grid_connection_capacity"].items():
            rows.append({"category": "grid_connection_capacity", "asset": name, "option": "new", "value": clean(values[idx]), "unit": "kW"})
        for category in (
            "microgrid_renewable_capacity", "microgrid_battery_power",
            "microgrid_battery_energy", "microgrid_backup_capacity",
        ):
            unit = "kWh" if category == "microgrid_battery_energy" else "kW"
            for name, idx in self.variables[category].items():
                rows.append({"category": category, "asset": name, "option": "new", "value": clean(values[idx]), "unit": unit})
        for name, idx in self.variables["storage_firm_capacity"].items():
            rows.append({"category": "storage_firm_capacity", "asset": name, "option": "derived", "value": clean(values[idx]), "unit": "kW"})
        return pd.DataFrame(rows, columns=["category", "asset", "option", "value", "unit"])

    def _make_dispatch(self, values: np.ndarray) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        clean = lambda x: 0.0 if abs(float(x)) < 1e-8 else float(x)
        spec = {
            "source_heat": ("kW", lambda key: (key[0], self.sources[key[0]]["node"], key[1], key[2])),
            "cooling_output": ("kW", lambda key: (key[0], self.sources[key[0]]["node"], "cooling", key[1])),
            "cooling_unserved": ("kW", lambda key: (f"{key[0]}_cooling", key[0], "cooling", key[1])),
            "pipe_flow_forward": ("kW", lambda key: (key[0], self.pipes[key[0]]["from"], key[1], key[2])),
            "pipe_flow_reverse": ("kW", lambda key: (key[0], self.pipes[key[0]]["to"], key[1], key[2])),
            "pipe_retrofit_flow_forward": ("kW", lambda key: (key[0], self.pipes[key[0]]["from"], key[1], key[2])),
            "pipe_retrofit_flow_reverse": ("kW", lambda key: (key[0], self.pipes[key[0]]["to"], key[1], key[2])),
            "storage_charge": ("kW", lambda key: (key[0], self.storages[key[0]]["node"], self.storages[key[0]]["grade"], key[1])),
            "storage_discharge": ("kW", lambda key: (key[0], self.storages[key[0]]["node"], self.storages[key[0]]["grade"], key[1])),
            "storage_soc": ("kWh", lambda key: (key[0], self.storages[key[0]]["node"], self.storages[key[0]]["grade"], key[1])),
            "demand_up": ("kW", lambda key: (f"{key[0]}_{key[1]}", key[0], key[1], key[2])),
            "demand_down": ("kW", lambda key: (f"{key[0]}_{key[1]}", key[0], key[1], key[2])),
            "unserved": ("kW", lambda key: (f"{key[0]}_{key[1]}", key[0], key[1], key[2])),
            "building_thermal_state": ("kWh", lambda key: (f"{key[0]}_{key[1]}", key[0], key[1], key[2])),
            "renewable_electricity_use": ("kW", lambda key: ("system", "system", "electricity", key)),
            "microgrid_renewable_use": ("kW", lambda key: ("system", "microgrid", "electricity", key)),
            "microgrid_backup_generation": ("kW", lambda key: ("system", "microgrid", "electricity", key)),
            "microgrid_battery_charge": ("kW", lambda key: ("system", "microgrid", "electricity", key)),
            "microgrid_battery_discharge": ("kW", lambda key: ("system", "microgrid", "electricity", key)),
            "microgrid_battery_soc": ("kWh", lambda key: ("system", "microgrid", "electricity", key)),
            "chp_electricity_output": ("kW", lambda key: (key[0], self.sources[key[0]]["node"], "electricity", key[1])),
            "chp_heat_rejection": ("kW", lambda key: (key[0], self.sources[key[0]]["node"], "rejected_heat", key[1])),
            "biomass_delivery": ("kg/h", lambda key: (key[0], "supply_chain", "fuel", key[1])),
            "biomass_inventory": ("kg", lambda key: (key[0], "supply_chain", "fuel", key[1])),
            "shared_heat_resource_use": ("kW", lambda key: (key[0], "resource_pool", "upstream_heat", key[1])),
        }
        if self.building_inputs is not None:
            buildings = {building.name:building for building in self.building_inputs.buildings}
            spec.update({
                "building_temperature":("degC",lambda key:(key[0],buildings[key[0]].node,buildings[key[0]].heat_grade,key[1])),
                "building_heat":("kW",lambda key:(key[0],buildings[key[0]].node,buildings[key[0]].heat_grade,key[1])),
                "building_cooling":("kW",lambda key:(key[0],buildings[key[0]].node,"cooling",key[1])),
            })
        for category, (unit, decoder) in spec.items():
            for key, idx in self.variables[category].items():
                asset, node, grade, t = decoder(key)
                rows.append({
                    "category": category,
                    "asset": asset,
                    "node": node,
                    "grade": grade,
                    "time_index": t,
                    "time": self._ts_value(t, "time"),
                    "scenario": self._scenario(t),
                    "boundary_group": self.time_boundary_group[t],
                    "value": clean(values[idx]),
                    "unit": unit,
                })
        return pd.DataFrame(rows)
