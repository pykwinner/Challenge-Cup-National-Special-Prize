from __future__ import annotations

import unittest
from pathlib import Path

import pandas as pd

from heatplan.io import validate_inputs
from heatplan.model import HeatPlanningModel
from heatplan.policy_alignment import (
    build_policy_action_register,
    build_policy_readiness_summary,
    evaluate_policy_alignment,
    load_policy_profile,
    validate_policy_config,
    validate_policy_profile,
)


ROOT = Path(__file__).resolve().parents[1]


class PolicyAlignmentTest(unittest.TestCase):
    @staticmethod
    def _config(policy: dict | None = None) -> dict:
        return {
            "planning": {
                "discount_rate": 0.05,
                "asset_lifetime_years": 20,
                "time_step_hours": 1.0,
                "max_unserved_rate": 0.0,
            },
            "grades": [{"name": "low", "temperature_c": 45.0}],
            "nodes": [
                {
                    "name": "load",
                    "demand_columns": {"low": "heat_demand"},
                }
            ],
            "sources": [
                {
                    "name": "fossil_boiler",
                    "node": "load",
                    "type": "boiler",
                    "resource_kind": "fossil",
                    "allowed_grades": ["low"],
                    "supply_temp_c": 80.0,
                    "existing_capacity_kw": 10.0,
                    "variable_cost_yuan_per_kwh_heat": 0.0,
                    "emission_kg_per_kwh_heat": 0.20,
                },
                {
                    "name": "renewable_boiler",
                    "node": "load",
                    "type": "boiler",
                    "resource_kind": "renewable",
                    "allowed_grades": ["low"],
                    "supply_temp_c": 80.0,
                    "existing_capacity_kw": 10.0,
                    "variable_cost_yuan_per_kwh_heat": 1.0,
                    "emission_kg_per_kwh_heat": 0.0,
                },
            ],
            "pipes": [],
            "storages": [],
            "policy": dict(policy or {}),
        }

    @staticmethod
    def _frame() -> pd.DataFrame:
        return pd.DataFrame(
            {
                "time": [0],
                "heat_demand": [10.0],
                "electricity_price": [0.6],
                "grid_emission_factor": [0.5],
            }
        )

    def test_non_fossil_share_and_direct_emission_policy_metrics(self):
        config = self._config(
            {
                "minimum_non_fossil_heat_share": 0.50,
                "served_floor_area_m2": 100.0,
                "maximum_direct_fossil_emission_intensity_kg_per_m2": 0.01,
            }
        )
        validate_inputs(config, self._frame())

        summary = HeatPlanningModel(config, self._frame()).solve().summary

        self.assertGreaterEqual(summary["non_fossil_heat_share"], 0.50 - 1e-8)
        self.assertLessEqual(
            summary["direct_fossil_emission_intensity_kg_per_m2"],
            0.01 + 1e-8,
        )
        self.assertAlmostEqual(summary["served_floor_area_m2"], 100.0)
        base = summary["scenario_results"]["base"]
        self.assertGreaterEqual(base["non_fossil_heat_share"], 0.50 - 1e-8)
        self.assertLessEqual(base["direct_fossil_emissions_kg"], 1.0 + 1e-8)

    def test_relative_emission_target_requires_audited_baseline_pair(self):
        with self.assertRaisesRegex(ValueError, "必须同时提供"):
            validate_policy_config(
                self._config(
                    {"minimum_emission_intensity_reduction_fraction": 0.17}
                )
            )

        config = self._config(
            {
                "baseline_emission_intensity_kg_per_kwh": 0.20,
                "minimum_emission_intensity_reduction_fraction": 0.50,
            }
        )
        summary = HeatPlanningModel(config, self._frame()).solve().summary
        self.assertLessEqual(summary["emission_intensity_kg_per_kwh"], 0.10 + 1e-8)

    def test_direct_emission_reduction_requires_positive_floor_area(self):
        with self.assertRaisesRegex(ValueError, "served_floor_area_m2"):
            validate_policy_config(
                self._config(
                    {
                        "served_floor_area_m2": 0.0,
                        "baseline_direct_fossil_emission_intensity_kg_per_m2": 0.02,
                        "minimum_direct_fossil_emission_reduction_fraction": 0.03,
                    }
                )
            )

    def test_source_policy_classification_fraction_is_bounded(self):
        config = self._config()
        config["sources"][1]["non_fossil_heat_fraction"] = 1.2
        with self.assertRaisesRegex(ValueError, "non_fossil_heat_fraction"):
            validate_policy_config(config)

    def test_small_coal_boiler_phaseout_requires_unit_inventory(self):
        config = self._config(
            {"enforce_small_coal_boiler_phaseout": True}
        )
        with self.assertRaisesRegex(ValueError, "boiler_capacity_t_per_hour"):
            validate_policy_config(config)

    def test_small_coal_boiler_phaseout_forces_full_retirement(self):
        config = self._config(
            {
                "enforce_small_coal_boiler_phaseout": True,
                "small_coal_boiler_phaseout_max_t_per_hour": 10.0,
            }
        )
        fossil = config["sources"][0]
        fossil["boiler_capacity_t_per_hour"] = 8.0
        fossil["max_retire_capacity_kw"] = 10.0
        summary = HeatPlanningModel(config, self._frame()).solve().summary
        self.assertAlmostEqual(
            summary["small_coal_boiler_retired_capacity_kw"], 10.0
        )
        self.assertAlmostEqual(
            summary["small_coal_boiler_remaining_capacity_kw"], 0.0
        )

    def test_national_profile_is_valid_and_never_claims_project_compliance(self):
        profile = load_policy_profile(
            ROOT
            / "data"
            / "policy_profiles"
            / "national_15th_fyp_reference_2026.yaml"
        )
        result = evaluate_policy_alignment(
            {
                "emission_intensity_kg_per_kwh": 0.08,
                "non_fossil_heat_share": 0.60,
                "direct_fossil_emission_intensity_kg_per_m2": 0.01,
            },
            profile,
        )
        self.assertFalse((result["status"] == "compliant").any())
        self.assertIn("national_reference_only", set(result["status"]))
        self.assertIn("needs_audited_local_baseline", set(result["status"]))
        metering = result[result["target_code"] == "HEAT_METERING_REFORM"].iloc[0]
        self.assertEqual(metering["status"], "mapped_to_model")
        self.assertEqual(
            metering["model_metric"], "heat_metering_coverage_fraction"
        )
        self.assertEqual(
            metering["source_verification_status"], "verified_official"
        )

    def test_national_profile_filters_policy_by_heating_scenario(self):
        profile = load_policy_profile(
            ROOT
            / "data"
            / "policy_profiles"
            / "national_15th_fyp_reference_2026.yaml"
        )
        result = evaluate_policy_alignment(
            {"worst_scenario_unserved_rate": 0.0},
            profile,
            scenario_id="scenario2_distributed_clean",
            as_of_date="2026-07-20",
        )
        chp = result[result["target_code"] == "CHP_DECOUPLING_AND_STORAGE"].iloc[0]
        distributed = result[
            result["target_code"] == "DISTRIBUTED_ENERGY_AND_GRID"
        ].iloc[0]
        boiler = result[
            result["target_code"] == "SMALL_COAL_BOILER_PHASEOUT"
        ].iloc[0]
        self.assertEqual(chp["status"], "not_applicable_to_scenario")
        self.assertEqual(distributed["status"], "mapped_to_model")
        self.assertEqual(boiler["status"], "needs_local_asset_inventory")

    def test_verified_official_source_requires_government_domain(self):
        profile = {
            "profile": {"id": "bad", "name": "bad", "period": "2030"},
            "sources": [
                {
                    "id": "bad_source",
                    "url": "https://example.com/policy",
                    "verification_status": "verified_official",
                }
            ],
            "targets": [
                {
                    "code": "BAD",
                    "name": "Bad",
                    "source_id": "bad_source",
                    "assessment_mode": "qualitative_mapping",
                    "comparison": "none",
                }
            ],
        }
        with self.assertRaisesRegex(ValueError, "政府官网"):
            validate_policy_profile(profile)

    def test_project_kpi_readiness_gate_prevents_premature_compliance(self):
        profile = {
            "profile": {"id": "local", "name": "local", "period": "2030"},
            "sources": [
                {
                    "id": "local_plan",
                    "url": "https://example.gov.cn/local-plan",
                    "verification_status": "verified_official",
                }
            ],
            "targets": [
                {
                    "code": "LOCAL_FOSSIL_CAP",
                    "name": "Local fossil share cap",
                    "source_id": "local_plan",
                    "assessment_mode": "project_kpi",
                    "comparison": "maximum",
                    "target_value": 0.20,
                    "unit": "share",
                    "model_metric": "fossil_heat_share",
                    "enforcement_readiness": "requires_local_baseline",
                }
            ],
        }
        result = evaluate_policy_alignment({"fossil_heat_share": 0.15}, profile)
        self.assertEqual(result.loc[0, "status"], "needs_audited_local_baseline")
        self.assertIsNone(result.loc[0, "compliant"])

    def test_policy_readiness_summary_and_action_register(self):
        profile = load_policy_profile(
            ROOT
            / "data"
            / "policy_profiles"
            / "national_15th_fyp_reference_2026.yaml"
        )
        result = evaluate_policy_alignment(
            {"worst_scenario_unserved_rate": 0.0},
            profile,
            scenario_id="scenario2_distributed_clean",
        )
        readiness = build_policy_readiness_summary(result)
        actions = build_policy_action_register(result)
        counts = dict(
            zip(readiness["readiness_group"], readiness["target_count"])
        )
        self.assertGreater(counts["model_ready"], 0)
        self.assertGreater(counts["model_ready_evidence_pending"], 0)
        self.assertGreater(counts["strategic_reference"], 0)
        self.assertGreater(counts["evidence_gap"], 0)
        self.assertIn("SMALL_COAL_BOILER_PHASEOUT", set(actions["target_code"]))

    def test_project_kpi_profile_can_issue_a_compliance_verdict(self):
        profile = {
            "profile": {"id": "local", "name": "local", "period": "2030"},
            "sources": [
                {
                    "id": "local_plan",
                    "url": "https://example.gov.cn/local-plan",
                }
            ],
            "targets": [
                {
                    "code": "LOCAL_FOSSIL_CAP",
                    "name": "Local fossil share cap",
                    "source_id": "local_plan",
                    "assessment_mode": "project_kpi",
                    "comparison": "maximum",
                    "target_value": 0.20,
                    "unit": "share",
                    "model_metric": "fossil_heat_share",
                }
            ],
        }
        validate_policy_profile(profile)
        result = evaluate_policy_alignment({"fossil_heat_share": 0.15}, profile)
        self.assertEqual(result.loc[0, "status"], "compliant")
        self.assertTrue(bool(result.loc[0, "compliant"]))
        self.assertAlmostEqual(float(result.loc[0, "gap_to_target"]), 0.05)


if __name__ == "__main__":
    unittest.main()
