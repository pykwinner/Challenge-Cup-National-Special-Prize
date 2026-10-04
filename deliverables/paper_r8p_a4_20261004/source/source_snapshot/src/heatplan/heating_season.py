"""Explicit cross-year service-period replay; never an annual certificate."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any

import numpy as np
import pandas as pd

from .chronology import (
    AnnualReplayArtifacts, AnnualReplayPreflight, FixedInvestmentPlanArtifacts, adapt_config_to_chronology_scenarios,
    annual_replay_acceptance_audit, chronology_quality_audit, chronology_required_columns,
    prepare_fixed_investment_plan, prepare_full_year_timeseries,
    replay_prepared_fixed_investment_plan, seasonal_storage_postcheck,
)


@dataclass
class HeatingSeasonReplayArtifacts:
    result: AnnualReplayArtifacts
    scope_audit: dict[str, Any]
    assumptions: dict[str, Any]
    storage_audit: pd.DataFrame
    certificate: dict[str, Any]
    period_summary: dict[str, Any]


def _scope_boundary(value: str, timezone: str | None) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp):
        raise ValueError("service-period boundaries must be valid timestamps")
    if timestamp.tzinfo is None:
        if timezone is None:
            raise ValueError("naive scope boundaries require an explicit timezone")
        timestamp = timestamp.tz_localize(timezone, ambiguous="raise", nonexistent="raise")
    return timestamp.tz_convert("UTC")


def prepare_heating_season_timeseries(
    config: dict[str, Any], frame: pd.DataFrame, scope: dict[str, Any], *,
    timestamp_column: str = "timestamp", input_timezone: str | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Select [start,end) and require exact timestamps, not merely row counts.

    Naive input must identify its actual timezone. LST is not an IANA timezone
    and must be converted upstream using a documented geographical convention.
    """
    start = _scope_boundary(scope["start_inclusive"], scope.get("timezone"))
    end = _scope_boundary(scope["end_exclusive"], scope.get("timezone"))
    dt = float(config.get("planning", {}).get("time_step_hours", 1.0))
    duration = (end - start).total_seconds() / 3600
    if not np.isfinite(dt) or dt <= 0 or duration <= 0:
        raise ValueError("service period must contain an integral number of positive time steps")
    step = pd.Timedelta(hours=dt)
    if step.value <= 0 or (end - start).value % step.value != 0:
        raise ValueError("service period must contain an integral number of positive time steps")
    if "expected_hours" in scope and not np.isclose(float(scope["expected_hours"]), duration, rtol=0., atol=1e-9):
        raise ValueError("expected_hours disagrees with the exact service-period boundaries")
    if timestamp_column not in frame:
        raise ValueError(f"missing timestamp column: {timestamp_column}")
    parsed = [pd.Timestamp(value) for value in frame[timestamp_column]]
    if any(pd.isna(value) for value in parsed):
        raise ValueError("invalid timestamp in service-period source")
    naive = [value.tzinfo is None for value in parsed]
    if any(naive) and not all(naive):
        raise ValueError("mixed naive and timezone-aware timestamps are ambiguous")
    if any(naive):
        if input_timezone is None:
            raise ValueError("naive input timestamps require input_timezone; LST must be converted upstream")
        timestamps = pd.DatetimeIndex(parsed).tz_localize(input_timezone, ambiguous="raise", nonexistent="raise").tz_convert("UTC")
    else:
        timestamps = pd.DatetimeIndex(pd.to_datetime(parsed, utc=True))
    selected = frame.loc[(timestamps >= start) & (timestamps < end)].copy()
    selected[timestamp_column] = timestamps[(timestamps >= start) & (timestamps < end)]
    selected = selected.sort_values(timestamp_column).reset_index(drop=True)
    expected = pd.date_range(start, end, freq=step, inclusive="left")
    observed = pd.DatetimeIndex(selected[timestamp_column])
    if observed.has_duplicates or not observed.equals(expected):
        raise ValueError("service-period timestamps do not exactly match [start_inclusive,end_exclusive): missing, duplicate or misaligned hours")
    source_scenarios = selected["scenario"].dropna().astype(str).unique().tolist() if "scenario" in selected else []
    if len(source_scenarios) > 1:
        raise ValueError("a physical service-period chronology must have one operating scenario")
    operating_scenario = str(scope.get("operating_scenario", source_scenarios[0] if source_scenarios else "heating_season"))
    thermal = config.get("building_thermal", {})
    covered = {(str(b["node"]), str(b["heat_grade"])) for b in thermal.get("buildings", [])
               if thermal.get("enabled", False)}
    for node in config.get("nodes", []):
        multipliers = node.get("user_behavior", {}).get("scenario_multipliers", {})
        # Old behavior multipliers do not act on RC-covered service. Empty
        # legacy grades also cannot change that period's delivered service.
        # Keep explicit scenario mapping mandatory for any active fixed load.
        active_fixed_load = any(
            (str(node["name"]), str(grade)) not in covered
            and (column not in selected or bool(pd.to_numeric(selected[column], errors="raise").ne(0.).any()))
            for grade, column in node.get("demand_columns", {}).items()
        )
        if active_fixed_load and any(float(value) != 1.0 for value in multipliers.values()) and operating_scenario not in multipliers:
            raise ValueError(f"operating_scenario must explicitly map user_behavior for node {node['name']}; choose from {sorted(multipliers)}")
    quality = chronology_quality_audit(
        selected, timestamp_column=timestamp_column, expected_step_hours=dt,
        required_columns=chronology_required_columns(config, timestamp_column),
        nonnegative_columns=[str(column) for node in config["nodes"] for column in node.get("demand_columns", {}).values()]
        + [str(column) for column in config.get("multi_energy_services", {}).get("cooling", {}).get("demand_columns", {}).values()],
    )
    if not quality["chronology_valid"]:
        raise ValueError("service-period quality audit failed: " + ", ".join(quality["issues"]))
    quality.update({"scope_id": scope.get("scope_id", scope.get("profile", "heating_season")),
                    "start_inclusive": start.isoformat(), "end_exclusive": end.isoformat(),
                    "exact_scope_match": True, "input_timezone": input_timezone or "explicit_in_input",
                    "scope_type": "heating_season", "expected_rows": len(expected),
                    "operating_scenario": operating_scenario})
    return prepare_full_year_timeseries(selected, timestamp_column, scenario=operating_scenario), quality


def _season_config(config: dict[str, Any], allow_cyclic_state_assumptions: bool) -> tuple[dict, dict]:
    fixed = deepcopy(config)
    cyclic = []
    if any(bool(source.get("unit_commitment", False)) for source in fixed.get("sources", [])):
        cyclic.append("unit_commitment")
    covered={(str(b["node"]),str(b["heat_grade"])) for b in fixed.get("building_thermal",{}).get("buildings",[]) if fixed.get("building_thermal",{}).get("enabled",False)}
    if any(any(bool(spec) and (str(node["name"]),str(grade)) not in covered
               for grade, spec in (node.get("thermal_inertia") or {}).items())
           for node in fixed.get("nodes", [])):
        cyclic.append("building_thermal_state")
    if fixed.get("building_thermal",{}).get("enabled",False):
        thermal=fixed["building_thermal"]
        for building in thermal["buildings"]:
            if building.get("boundary_mode",thermal.get("boundary_mode","fixed")) != "fixed":
                raise ValueError("heating-season RC requires explicit fixed initial/final state, not cyclic representative-period assumptions")
    if fixed.get("biomass_supply_chains"):
        cyclic.append("biomass_inventory")
    microgrid = fixed.get("microgrid_islanding", {})
    if float(microgrid.get("existing_battery_energy_kwh", 0.0)) + float(microgrid.get("max_new_battery_energy_kwh", 0.0)) > 0:
        cyclic.append("microgrid_battery")
    if cyclic and not allow_cyclic_state_assumptions:
        raise ValueError("initial-state handoff is not yet supported for " + ", ".join(cyclic)
                         + "; explicitly allow documented cyclic-state assumptions or use a supported configuration")
    for storage in fixed.get("storages", []):
        capacity = float(storage.get("existing_capacity_kwh", 0.0)) + float(storage.get("max_new_capacity_kwh", 0.0))
        if capacity > 0 and not all(key in storage for key in ("initial_soc_fraction", "final_soc_min_fraction")):
            raise ValueError(f"seasonal storage {storage['name']} requires explicit initial_soc_fraction and final_soc_min_fraction")
        storage["boundary_mode"] = "fixed"
        if capacity <= 0:
            storage["initial_soc_fraction"] = storage["final_soc_min_fraction"] = 0.0
    # These are annual economic/resource tests, not winter operating tests.
    # Keep them in investment planning, defer their service-year verification
    # to an independent annual replay instead of silently annualizing winter.
    deferred = {}
    if "fixed_investment_accounting" in fixed:
        deferred["fixed_investment_accounting"] = fixed.pop("fixed_investment_accounting")
    planning = fixed.setdefault("planning", {})
    if "annual_scope" in planning:
        deferred["planning.annual_scope"]=planning.pop("annual_scope")
    if "emissions_cap_kg_per_year" in planning:
        deferred["planning.emissions_cap_kg_per_year"] = planning.pop("emissions_cap_kg_per_year")
    policy = fixed.setdefault("policy", {})
    for key in ("annualized_investment_budget_yuan", "public_subsidy_budget_yuan_per_year",
                "maximum_levelized_heat_cost_yuan_per_kwh", "legacy_annualized_capex_yuan",
                "maximum_average_household_heat_bill_yuan_per_year"):
        if key in policy:
            deferred[f"policy.{key}"] = policy.pop(key)
    cooling = fixed.get("multi_energy_services", {}).get("cooling", {})
    if "ground_source_annual_balance" in cooling:
        deferred["cooling.ground_source_annual_balance"] = cooling.pop("ground_source_annual_balance")
    for source_name, specification in cooling.get("sources", {}).items():
        if not isinstance(specification, dict):
            continue
        balance = specification.get("ground_thermal_balance")
        if balance and bool(balance.get("enforce", True)):
            deferred[f"cooling.sources.{source_name}.ground_thermal_balance"] = deepcopy(balance)
            balance["enforce"] = False
    for chain in fixed.get("biomass_supply_chains", []):
        if "annual_supply_limit_kg" in chain:
            deferred[f"biomass.{chain['name']}.annual_supply_limit_kg"] = chain.pop("annual_supply_limit_kg")
    return fixed, {"cyclic_state_assumptions": cyclic,
                   "thermal_storage_boundary": "explicit_fixed_initial_and_final",
                   "deferred_annual_constraints": deferred,
                   "annual_supplement_required": True,
                   "scope_note": "Service-period operating feasibility only; annual economics/resource balance require separate annual replay."}


def run_fixed_investment_heating_season_replay(
    config: dict[str, Any], representative_timeseries: pd.DataFrame,
    chronological_source: pd.DataFrame, scope: dict[str, Any], *,
    timestamp_column: str = "timestamp", input_timezone: str | None = None,
    planning_time_limit: float = 180.0, replay_time_limit: float = 1800.0,
    planning_mip_gap: float | None = None, replay_mip_gap: float | None = None,
    allow_cyclic_state_assumptions: bool = False,
    fixed_plan: FixedInvestmentPlanArtifacts | None = None,
) -> HeatingSeasonReplayArtifacts:
    """Plan on the supplied planning horizon and validate only the stated season."""
    chronology, scope_audit = prepare_heating_season_timeseries(
        config, chronological_source, scope, timestamp_column=timestamp_column, input_timezone=input_timezone,
    )
    # Reject unsupported state assumptions before a potentially expensive solve.
    _season_config(config, allow_cyclic_state_assumptions)
    plan = fixed_plan if fixed_plan is not None else prepare_fixed_investment_plan(
        config,representative_timeseries,planning_time_limit=planning_time_limit,planning_mip_gap=planning_mip_gap)
    fixed_config, assumptions = _season_config(plan.fixed_config, allow_cyclic_state_assumptions)
    plan = replace(plan, fixed_config=fixed_config)
    _, scenario_audit = adapt_config_to_chronology_scenarios(fixed_config, chronology)
    preflight = AnnualReplayPreflight(
        chronological_timeseries=chronology, chronology_audit=scope_audit,
        representative_coverage_audit=pd.DataFrame(columns=["coverage_status"]),
        representative_recommendations=pd.DataFrame(), scenario_scope_audit=scenario_audit,
    )
    result = replay_prepared_fixed_investment_plan(
        config, representative_timeseries, chronology, plan,
        timestamp_column=timestamp_column, replay_time_limit=replay_time_limit,
        replay_mip_gap=replay_mip_gap, require_full_year=False, preflight=preflight,
    )
    # Annual planning totals and seasonal totals are intentionally not compared.
    result.kpi_comparison = pd.DataFrame()
    storage = seasonal_storage_postcheck(result.fixed_config, result.replay.dispatch, expected_timeseries=chronology)
    common = annual_replay_acceptance_audit(
        result.fixed_config, chronology_audit=scope_audit,
        representative_coverage_audit=preflight.representative_coverage_audit,
        investment_freeze_audit=result.investment_freeze_audit,
        fixed_investment_readiness_audit=result.fixed_investment_readiness_audit,
        planning_summary=result.planning.summary, replay_summary=result.replay.summary,
        storage_boundary_audit=storage,
    )
    gates = [gate for gate in common["gates"] if gate["gate"] not in {"continuous_full_calendar_year", "continuous_full_service_year", "representative_period_coverage"}]
    for gate in gates:
        gate["gate"] = gate["gate"].replace("annual_", "period_")
        gate["requirement"] = gate["requirement"].replace("全年", "本服务期")
    gates.insert(0, {"gate": "exact_service_period", "passed": bool(scope_audit["exact_scope_match"]),
                     "observed": scope_audit, "requirement": "exact timezone-aware half-open service window"})
    failed = [gate["gate"] for gate in gates if not gate["passed"]]
    certificate = {"schema": "heatplan.heating_season_certificate.v1", "scope_type": "heating_season",
                   "certification_pass": not failed, "gate_count": len(gates),
                   "passed_gate_count": len(gates) - len(failed), "failed_gate_count": len(failed),
                   "failed_gates": failed, "gates": gates, "assumptions": assumptions,
                   "annual_replay_certified": False}
    summary = {"scope": scope_audit, "heating_season_certified": not failed,
               "annual_replay_certified": False, "assumptions": assumptions,
               "solver_status": result.replay.summary["solver_status"],
               "unserved_rate": result.replay.summary["unserved_rate"],
               "numerical_audit_passed": result.replay.summary.get("numerical_audit_passed", False)}
    for key in ("demand_kwh_per_year", "effective_heat_demand_kwh_per_year", "unserved_kwh_per_year",
                "emissions_kg_per_year", "total_service_emissions_kg_per_year", "electricity_kwh_per_year",
                "heating_cash_operating_cost_yuan_per_year", "cooling_demand_kwh_per_year"):
        if key in result.replay.summary:
            summary[key.replace("_per_year", "_over_period")] = result.replay.summary[key]
    return HeatingSeasonReplayArtifacts(result, scope_audit, assumptions, storage, certificate, summary)
