from __future__ import annotations

import unittest

import pandas as pd

from heatplan.chronology import (
    annual_replay_acceptance_audit,
    annual_replay_preflight,
    chronology_quality_audit,
    estimate_annual_model_structure,
    fixed_investment_readiness_audit,
    representative_coverage_recommendations,
    representative_period_coverage_audit,
    run_fixed_investment_annual_replay,
    seasonal_storage_postcheck,
)
from heatplan.io import validate_inputs
from heatplan.model import HeatPlanningModel


class HeatMeteringAndAnnualReplayTest(unittest.TestCase):
    @staticmethod
    def _config(*, existing_source: float = 10.0, candidate_source: float = 0.0) -> dict:
        return {
            "planning": {
                "discount_rate": 0.05,
                "asset_lifetime_years": 20,
                "time_step_hours": 1.0,
                "max_unserved_rate": 0.0,
                "max_scenario_unserved_rate": 0.0,
                "firm_capacity_reserve_margin": 0.0,
            },
            "grades": [{"name": "low", "temperature_c": 45.0}],
            "nodes": [
                {
                    "name": "load",
                    "demand_columns": {"low": "heat_demand"},
                    "heat_metering": {
                        "existing_coverage_fraction": 0.20,
                        "max_new_coverage_fraction": 0.80,
                        "saving_fraction_at_full_coverage": 0.10,
                        "capex_yuan_per_coverage_fraction": 100.0,
                    },
                }
            ],
            "sources": [
                {
                    "name": "boiler",
                    "node": "load",
                    "type": "boiler",
                    "resource_kind": "fossil",
                    "allowed_grades": ["low"],
                    "supply_temp_c": 80.0,
                    "existing_capacity_kw": existing_source,
                    "max_new_capacity_kw": candidate_source,
                    "capex_yuan_per_kw": 1.0,
                    "variable_cost_yuan_per_kwh_heat": 0.0,
                    "emission_kg_per_kwh_heat": 0.2,
                }
            ],
            "pipes": [],
            "storages": [],
            "policy": {"minimum_heat_metering_coverage_fraction": 0.60},
            "heat_tariff": {
                "variable_tariff_yuan_per_kwh": 10.0,
                "fixed_charge_yuan_per_household_year": 0.0,
                "household_count": 1.0,
            },
        }

    @staticmethod
    def _frame(demand: float = 10.0) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "time": [0],
                "heat_demand": [demand],
                "electricity_price": [0.6],
                "grid_emission_factor": [0.5],
            }
        )

    def test_metering_coverage_reduces_heat_balance_and_reports_bill(self):
        config = self._config()
        config["policy"][
            "maximum_average_household_heat_bill_yuan_per_year"
        ] = 94.0
        artifacts = HeatPlanningModel(config, self._frame()).solve()

        metering = artifacts.investments[
            artifacts.investments["category"] == "heat_metering_coverage"
        ]
        source_heat = artifacts.dispatch[
            artifacts.dispatch["category"] == "source_heat"
        ]["value"].sum()
        self.assertAlmostEqual(float(metering["value"].iloc[0]), 0.40, places=6)
        self.assertAlmostEqual(
            artifacts.summary["heat_metering_coverage_fraction"], 0.60, places=6
        )
        self.assertAlmostEqual(
            artifacts.summary["metered_heat_savings_kwh_per_year"], 0.60, places=6
        )
        self.assertAlmostEqual(
            artifacts.summary["effective_heat_demand_kwh_per_year"], 9.40, places=6
        )
        self.assertAlmostEqual(source_heat, 9.40, places=6)
        self.assertAlmostEqual(
            artifacts.summary[
                "average_indicative_household_heat_bill_yuan_per_year"
            ],
            94.0,
            places=6,
        )
        self.assertTrue(artifacts.summary["exergy_accounting_valid"])

    def test_metering_input_validation_rejects_impossible_coverage(self):
        config = self._config()
        config["nodes"][0]["heat_metering"]["max_new_coverage_fraction"] = 0.90
        with self.assertRaisesRegex(ValueError, "新增计量覆盖率边界无效"):
            validate_inputs(config, self._frame())

    def test_representative_plan_is_frozen_before_chronological_replay(self):
        config = self._config(existing_source=0.0, candidate_source=10.0)
        config["policy"]["transition_target_scenarios"] = ["base"]
        representative = self._frame(demand=5.0)
        annual = pd.DataFrame(
            {
                "timestamp": ["2026-01-01 00:00", "2026-01-01 01:00"],
                "heat_demand": [4.0, 5.0],
                "electricity_price": [0.6, 0.6],
                "grid_emission_factor": [0.5, 0.5],
            }
        )

        result = run_fixed_investment_annual_replay(
            config,
            representative,
            annual,
            planning_time_limit=30.0,
            replay_time_limit=30.0,
            require_full_year=False,
        )

        self.assertTrue(bool(result.investment_freeze_audit["freeze_ok"].all()))
        fixed_source = result.fixed_config["sources"][0]
        self.assertGreater(fixed_source["existing_capacity_kw"], 0.0)
        self.assertEqual(fixed_source["max_new_capacity_kw"], 0.0)
        self.assertEqual(len(result.chronological_timeseries), 2)
        self.assertAlmostEqual(result.replay.summary["unserved_rate"], 0.0)
        self.assertNotIn(
            "source_capacity", set(result.replay.investments["category"])
        )
        self.assertGreater(result.planning_model_profile["variables"], 0)
        self.assertGreater(result.replay_model_profile["constraints"], 0)
        self.assertGreaterEqual(
            result.planning_model_profile["build_seconds"], 0.0
        )
        self.assertGreaterEqual(
            result.replay_model_profile["solve_seconds"], 0.0
        )
        self.assertTrue(result.scenario_scope_audit["mapping_applied"])
        self.assertEqual(
            result.fixed_config["policy"]["transition_target_scenarios"],
            ["full_year"],
        )

    def test_formal_replay_rejects_short_horizon_before_solving(self):
        config = self._config(existing_source=0.0, candidate_source=10.0)
        annual = pd.DataFrame(
            {
                "timestamp": ["2026-01-01 00:00", "2026-01-01 01:00"],
                "heat_demand": [4.0, 5.0],
                "electricity_price": [0.6, 0.6],
                "grid_emission_factor": [0.5, 0.5],
            }
        )
        with self.assertRaisesRegex(ValueError, "8760/8784"):
            run_fixed_investment_annual_replay(
                config,
                self._frame(demand=5.0),
                annual,
                planning_time_limit=30.0,
                replay_time_limit=30.0,
            )

    def test_full_year_quality_audit_and_preflight(self):
        config = self._config()
        timestamps = pd.date_range("2026-01-01", periods=8760, freq="h")
        annual = pd.DataFrame(
            {
                "timestamp": timestamps,
                "heat_demand": 5.0,
                "electricity_price": 0.6,
                "grid_emission_factor": 0.5,
            }
        )
        audit = chronology_quality_audit(
            annual,
            expected_step_hours=1.0,
            required_columns=list(annual.columns),
            nonnegative_columns=["heat_demand"],
        )
        self.assertTrue(audit["chronology_valid"])
        self.assertTrue(audit["is_full_year_horizon"])
        self.assertTrue(audit["is_calendar_year_aligned"])

        preflight = annual_replay_preflight(
            config,
            self._frame(demand=5.0),
            annual,
        )
        self.assertEqual(len(preflight.chronological_timeseries), 8760)
        self.assertTrue(preflight.chronology_audit["is_full_year_horizon"])

    def test_quality_audit_detects_gap_negative_demand_and_missing_value(self):
        annual = pd.DataFrame(
            {
                "timestamp": [
                    "2026-01-01 00:00",
                    "2026-01-01 02:00",
                    "2026-01-01 03:00",
                ],
                "heat_demand": [5.0, -1.0, None],
            }
        )
        audit = chronology_quality_audit(
            annual,
            expected_step_hours=1.0,
            required_columns=["timestamp", "heat_demand"],
            nonnegative_columns=["heat_demand"],
        )
        self.assertFalse(audit["chronology_valid"])
        self.assertIn("irregular_or_missing_intervals", audit["issues"])
        self.assertIn("missing_required_values", audit["issues"])
        self.assertIn("negative_demand_values", audit["issues"])

    def test_representative_coverage_audit_flags_missed_peak_and_cold_extreme(self):
        config = self._config()
        config["planning"]["representative_day_weight"] = 1.0
        representative = pd.DataFrame(
            {
                "time": [0, 1],
                "heat_demand": [5.0, 6.0],
                "outdoor_temp": [-5.0, -6.0],
                "electricity_price": [0.6, 0.6],
                "grid_emission_factor": [0.5, 0.5],
            }
        )
        chronology = pd.DataFrame(
            {
                "time": [0, 1, 2],
                "scenario": ["full_year"] * 3,
                "heat_demand": [5.0, 10.0, 5.0],
                "outdoor_temp": [-5.0, -12.0, -5.0],
                "electricity_price": [0.6, 0.6, 0.6],
                "grid_emission_factor": [0.5, 0.5, 0.5],
            }
        )
        audit = representative_period_coverage_audit(
            config, representative, chronology
        )
        demand = audit[audit["column"] == "heat_demand"].iloc[0]
        temperature = audit[audit["column"] == "outdoor_temp"].iloc[0]
        self.assertIn("demand_peak_under_captured", demand["findings"])
        self.assertIn("cold_extreme_under_captured", temperature["findings"])

        recommendations = representative_coverage_recommendations(audit)
        demand_action = recommendations[
            recommendations["column"] == "heat_demand"
        ].iloc[0]
        self.assertIn(
            "add_or_force_maximum_demand_day",
            demand_action["recommended_actions"],
        )
        self.assertIn(
            "recalibrate_weights_or_add_seasonal_day",
            demand_action["recommended_actions"],
        )
        self.assertAlmostEqual(
            demand_action["suggested_energy_weight_multiplier"],
            20.0 / 11.0,
            places=6,
        )
        self.assertFalse(bool(demand_action["automatic_application_allowed"]))

    def test_model_size_estimate_reports_structure_without_fake_solve_time(self):
        config = self._config()
        timestamps = pd.date_range("2026-01-01", periods=96, freq="h")
        annual = pd.DataFrame(
            {
                "timestamp": timestamps,
                "heat_demand": 5.0,
                "electricity_price": 0.6,
                "grid_emission_factor": 0.5,
            }
        )
        preflight = annual_replay_preflight(
            config,
            self._frame(demand=5.0),
            annual,
            require_full_year=False,
        )
        estimate = estimate_annual_model_structure(
            config,
            preflight.chronological_timeseries,
            pilot_hours=48,
        )
        projected = estimate["projected_full_horizon"]
        self.assertEqual(projected["time_steps"], 96)
        self.assertGreater(projected["variables"], 0)
        self.assertGreater(projected["constraints"], 0)
        self.assertGreater(projected["matrix_nonzeros"], 0)
        self.assertGreater(
            projected["conservative_python_build_memory_proxy_mb"], 0.0
        )
        self.assertIsNone(estimate["solve_time_estimate"])
        self.assertEqual(len(estimate["pilot_profiles"]), 2)
        with self.assertRaisesRegex(ValueError, "至少需要两个"):
            estimate_annual_model_structure(config, annual.iloc[:1])

    def test_fixed_investment_readiness_reports_firm_heat_shortfall(self):
        config = self._config(existing_source=5.0, candidate_source=0.0)
        config["planning"]["firm_capacity_reserve_margin"] = 0.20
        chronology = pd.DataFrame(
            {
                "time": [0, 1],
                "scenario": ["full_year", "full_year"],
                "scenario_probability": [1.0, 1.0],
                "period_weight": [1.0, 1.0],
                "boundary_group": ["full_year", "full_year"],
                "heat_demand": [5.0, 5.0],
                "electricity_price": [0.6, 0.6],
                "grid_emission_factor": [0.5, 0.5],
            }
        )
        audit = fixed_investment_readiness_audit(config, chronology)
        self.assertFalse(audit["readiness_pass"])
        self.assertFalse(audit["heat_firm_capacity_pass"])
        self.assertAlmostEqual(audit["firm_heat_capacity_gap_kw"], -1.0)

    def test_cyclic_storage_postcheck_uses_pre_step_boundary_state(self):
        config = {
            "planning": {"time_step_hours": 1.0},
            "storages": [
                {
                    "name": "tank",
                    "existing_capacity_kwh": 100.0,
                    "max_new_capacity_kwh": 0.0,
                    "boundary_mode": "cyclic",
                    "charge_efficiency": 1.0,
                    "discharge_efficiency": 1.0,
                    "self_loss_per_hour": 0.0,
                }
            ],
        }
        rows = []
        values = {
            "storage_soc": [60.0, 50.0],
            "storage_charge": [10.0, 0.0],
            "storage_discharge": [0.0, 10.0],
        }
        for category, series in values.items():
            for time_index, value in enumerate(series):
                rows.append(
                    {
                        "category": category,
                        "asset": "tank",
                        "time_index": time_index,
                        "scenario": "base",
                        "boundary_group": "day_1",
                        "value": value,
                    }
                )

        audit = seasonal_storage_postcheck(config, pd.DataFrame(rows))

        self.assertAlmostEqual(audit.loc[0, "start_soc_kwh"], 50.0)
        self.assertAlmostEqual(
            audit.loc[0, "first_period_end_soc_kwh"], 60.0
        )
        self.assertAlmostEqual(audit.loc[0, "end_soc_kwh"], 50.0)
        self.assertAlmostEqual(audit.loc[0, "soc_drift_kwh"], 0.0)
        self.assertAlmostEqual(
            audit.loc[0, "soc_balance_residual_kwh"], 0.0
        )
        self.assertTrue(bool(audit.loc[0, "boundary_condition_pass"]))
        self.assertTrue(
            bool(audit.loc[0, "charge_discharge_complementarity_pass"])
        )

    def test_storage_postcheck_rejects_simultaneous_charge_and_discharge(self):
        config = {
            "planning": {"time_step_hours": 1.0},
            "storages": [
                {
                    "name": "tank",
                    "existing_capacity_kwh": 100.0,
                    "boundary_mode": "cyclic",
                    "charge_efficiency": 1.0,
                    "discharge_efficiency": 1.0,
                    "self_loss_per_hour": 0.0,
                }
            ],
        }
        rows = [
            {
                "category": category,
                "asset": "tank",
                "time_index": 0,
                "scenario": "base",
                "boundary_group": "year",
                "value": value,
            }
            for category, value in (
                ("storage_soc", 50.0),
                ("storage_charge", 2.0),
                ("storage_discharge", 2.0),
            )
        ]

        audit = seasonal_storage_postcheck(config, pd.DataFrame(rows))

        self.assertTrue(bool(audit.loc[0, "boundary_condition_pass"]))
        self.assertAlmostEqual(
            audit.loc[0, "maximum_simultaneous_charge_discharge_kw"], 2.0
        )
        self.assertFalse(
            bool(audit.loc[0, "charge_discharge_complementarity_pass"])
        )

    def test_fixed_storage_postcheck_audits_initial_and_final_margins(self):
        config = {
            "planning": {"time_step_hours": 1.0},
            "storages": [
                {
                    "name": "seasonal",
                    "existing_capacity_kwh": 100.0,
                    "max_new_capacity_kwh": 0.0,
                    "boundary_mode": "fixed",
                    "initial_soc_fraction": 0.5,
                    "final_soc_min_fraction": 0.4,
                    "charge_efficiency": 1.0,
                    "discharge_efficiency": 1.0,
                    "self_loss_per_hour": 0.0,
                }
            ],
        }
        rows = []
        values = {
            "storage_soc": [55.0, 45.0],
            "storage_charge": [5.0, 0.0],
            "storage_discharge": [0.0, 10.0],
        }
        for category, series in values.items():
            for time_index, value in enumerate(series):
                rows.append(
                    {
                        "category": category,
                        "asset": "seasonal",
                        "time_index": time_index,
                        "scenario": "base",
                        "boundary_group": "year",
                        "value": value,
                    }
                )

        audit = seasonal_storage_postcheck(config, pd.DataFrame(rows))

        self.assertAlmostEqual(audit.loc[0, "start_soc_kwh"], 50.0)
        self.assertAlmostEqual(
            audit.loc[0, "final_soc_requirement_kwh"], 40.0
        )
        self.assertAlmostEqual(audit.loc[0, "final_soc_margin_kwh"], 5.0)
        self.assertTrue(bool(audit.loc[0, "boundary_condition_pass"]))

    def test_annual_replay_acceptance_certificate_exposes_failed_gate(self):
        config = {
            "planning": {
                "max_unserved_rate": 0.0,
                "max_scenario_unserved_rate": 0.0,
            },
            "policy": {"minimum_clean_heat_share": 0.5},
        }
        common = {
            "config": config,
            "chronology_audit": {
                "chronology_valid": True,
                "is_full_year_horizon": True,
                "is_calendar_year_aligned": True,
            },
            "representative_coverage_audit": pd.DataFrame(
                {"coverage_status": ["pass"]}
            ),
            "investment_freeze_audit": pd.DataFrame(
                {"freeze_ok": [True, True]}
            ),
            "fixed_investment_readiness_audit": {
                "readiness_pass": True,
                "firm_heat_capacity_gap_kw": 1.0,
                "grid": {"firm_electric_capacity_gap_kw": 0.0},
            },
            "planning_summary": {"solver_status": 0, "numerical_audit_passed": True, "numerical_objective_checked": True},
            "storage_boundary_audit": pd.DataFrame(),
        }
        replay_summary = {
            "solver_status": 0,
            "numerical_audit_passed": True,
            "numerical_objective_checked": True,
            "unserved_rate": 0.0,
            "worst_scenario_unserved_rate": 0.0,
            "clean_heat_share": 0.6,
        }

        passed = annual_replay_acceptance_audit(
            replay_summary=replay_summary, **common
        )
        self.assertTrue(passed["certification_pass"])

        replay_summary["unserved_rate"] = 0.01
        failed = annual_replay_acceptance_audit(
            replay_summary=replay_summary, **common
        )
        self.assertFalse(failed["certification_pass"])
        self.assertIn("annual_heat_reliability", failed["failed_gates"])


if __name__ == "__main__":
    unittest.main()
