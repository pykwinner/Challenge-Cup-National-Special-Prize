from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from heatplan.chronology import (
    load_fixed_investment_plan_checkpoint,
    prepare_fixed_investment_plan,
    replay_prepared_fixed_investment_plan,
    save_fixed_investment_plan_checkpoint,
)
from heatplan.multiyear import audit_fixed_plan_across_years
from heatplan.multiyear_replay_cli import write_multiyear_replay_rollup
from heatplan.risk_scan_cli import empirical_weighted_cvar
from heatplan.representative import (
    adapt_config_to_weather_year_scenarios,
    build_multiyear_representative_days,
    build_multiyear_stochastic_representative_bundle,
)


class MultiYearReadinessTest(unittest.TestCase):
    @staticmethod
    def _config() -> dict:
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
                    "existing_capacity_kw": 10.0,
                    "max_new_capacity_kw": 0.0,
                    "capex_yuan_per_kw": 0.0,
                    "variable_cost_yuan_per_kwh_heat": 0.0,
                    "emission_kg_per_kwh_heat": 0.2,
                }
            ],
            "pipes": [],
            "storages": [],
        }

    @staticmethod
    def _representative() -> pd.DataFrame:
        return pd.DataFrame(
            {
                "time": [0],
                "heat_demand": [5.0],
                "electricity_price": [0.6],
                "grid_emission_factor": [0.5],
            }
        )

    @staticmethod
    def _annual(year: int, demand: float) -> pd.DataFrame:
        rows = 8784 if year % 4 == 0 else 8760
        return pd.DataFrame(
            {
                "time": pd.date_range(
                    f"{year}-01-01", periods=rows, freq="h"
                ),
                "heat_demand": demand,
                "electricity_price": 0.6,
                "grid_emission_factor": 0.5,
            }
        )

    def test_prepare_fixed_plan_freezes_candidates_once(self):
        config = self._config()
        plan = prepare_fixed_investment_plan(
            config, self._representative(), planning_time_limit=30.0
        )

        self.assertEqual(plan.planning.summary["solver_status"], 0)
        self.assertEqual(
            plan.fixed_config["sources"][0]["max_new_capacity_kw"], 0.0
        )
        self.assertTrue(plan.investment_freeze_audit["freeze_ok"].all())

        replay = replay_prepared_fixed_investment_plan(
            config,
            self._representative(),
            pd.DataFrame(
                {
                    "time": pd.date_range(
                        "2024-01-01", periods=2, freq="h"
                    ),
                    "heat_demand": [5.0, 6.0],
                    "electricity_price": 0.6,
                    "grid_emission_factor": 0.5,
                }
            ),
            plan,
            timestamp_column="time",
            replay_time_limit=30.0,
            require_full_year=False,
        )
        self.assertIs(replay.planning, plan.planning)
        self.assertEqual(replay.replay.summary["solver_status"], 0)

    def test_fixed_plan_checkpoint_round_trip_and_input_fingerprints(self):
        config = self._config()
        representative = self._representative()
        plan = prepare_fixed_investment_plan(
            config, representative, planning_time_limit=30.0
        )
        with tempfile.TemporaryDirectory() as temporary:
            checkpoint = Path(temporary)
            manifest = save_fixed_investment_plan_checkpoint(
                checkpoint,
                plan,
                source_config=config,
                representative_timeseries=representative,
            )
            self.assertTrue(manifest.is_file())
            loaded = load_fixed_investment_plan_checkpoint(
                checkpoint,
                expected_config=config,
                expected_representative_timeseries=representative,
            )
            self.assertEqual(loaded.planning.summary["solver_status"], 0)
            self.assertAlmostEqual(
                loaded.planning.summary["objective_yuan_per_year"],
                plan.planning.summary["objective_yuan_per_year"],
            )
            self.assertTrue(
                loaded.investment_freeze_audit["freeze_ok"].all()
            )
            replay = replay_prepared_fixed_investment_plan(
                config,
                representative,
                pd.DataFrame(
                    {
                        "time": pd.date_range(
                            "2024-01-01", periods=2, freq="h"
                        ),
                        "heat_demand": [5.0, 6.0],
                        "electricity_price": 0.6,
                        "grid_emission_factor": 0.5,
                    }
                ),
                loaded,
                timestamp_column="time",
                replay_time_limit=30.0,
                require_full_year=False,
            )
            self.assertEqual(replay.replay.summary["solver_status"], 0)

            changed = representative.copy()
            changed.loc[0, "heat_demand"] = 5.1
            with self.assertRaisesRegex(ValueError, "代表时序不匹配"):
                load_fixed_investment_plan_checkpoint(
                    checkpoint,
                    expected_config=config,
                    expected_representative_timeseries=changed,
                )

    def test_formal_replay_rollup_hashes_certificates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            certificate = (
                root
                / "scenario_test"
                / "2024"
                / "annual_replay_acceptance_certificate.json"
            )
            certificate.parent.mkdir(parents=True)
            certificate.write_text(
                json.dumps(
                    {
                        "certification_pass": True,
                        "failed_gates": [],
                    }
                ),
                encoding="utf-8",
            )
            (root / "scenario_test" / "planning_model_profile.json").write_text(
                json.dumps({"solve_seconds": 12.5}), encoding="utf-8"
            )
            rows = [
                {
                    "profile": "scenario_test",
                    "weather_year": "2024",
                    "chronological_rows": 8784,
                    "replay_solver_status": 0,
                    "replay_mip_gap": 0.001,
                    "replay_solve_seconds": 20.0,
                    "heat_unserved_rate": 0.0,
                    "cooling_unserved_rate": 0.0,
                    "certification_pass": True,
                }
            ]
            rollup = write_multiyear_replay_rollup(root, rows)

            self.assertTrue(rollup["acceptance_pass"])
            self.assertEqual(rollup["chronological_hours_verified"], 8784)
            self.assertAlmostEqual(
                rollup["planning_solve_seconds_avoided_by_checkpoint"], 12.5
            )
            self.assertEqual(len(rollup["certificates"][0]["sha256"]), 64)

    def test_empirical_cvar_matches_worst_of_five_equal_weather_years(self):
        statistics = empirical_weighted_cvar(
            [(1.0, 0.2), (2.0, 0.2), (3.0, 0.2), (4.0, 0.2), (5.0, 0.2)],
            0.8,
        )

        self.assertAlmostEqual(
            statistics["expected_operating_risk_cost_yuan"], 3.0
        )
        self.assertAlmostEqual(
            statistics["cvar_operating_risk_cost_yuan"], 5.0
        )
        self.assertAlmostEqual(
            statistics["worst_operating_risk_cost_yuan"], 5.0
        )

    def test_one_plan_is_screened_against_normal_and_leap_years(self):
        artifacts = audit_fixed_plan_across_years(
            self._config(),
            self._representative(),
            {
                "2021": self._annual(2021, 5.0),
                "2024": self._annual(2024, 6.0),
            },
            timestamp_column="time",
            planning_time_limit=30.0,
        )

        audit = artifacts.year_audit.set_index("year_label")
        self.assertEqual(int(audit.loc["2021", "rows"]), 8760)
        self.assertEqual(int(audit.loc["2024", "rows"]), 8784)
        self.assertTrue(bool(audit["readiness_pass"].all()))
        self.assertTrue(bool(audit.loc["2024", "selected_extreme_year"]))
        self.assertIn(
            "peak_heat_demand",
            audit.loc["2024", "dominant_risk_reasons"],
        )
        self.assertEqual(
            artifacts.fixed_plan.planning.summary["solver_status"], 0
        )

    def test_multiyear_representative_set_preserves_extreme_and_mean_energy(self):
        def two_days(year: int, first: float, second: float) -> pd.DataFrame:
            return pd.DataFrame(
                {
                    "time": pd.date_range(
                        f"{year}-01-01", periods=48, freq="h"
                    ),
                    "heat_demand": [first] * 24 + [second] * 24,
                    "outdoor_temp": [5.0] * 24 + [-10.0] * 24,
                    "electricity_price": 0.6,
                    "grid_emission_factor": 0.5,
                }
            )

        artifacts = build_multiyear_representative_days(
            self._config(),
            {
                "2023": two_days(2023, 1.0, 3.0),
                "2024": two_days(2024, 2.0, 9.0),
            },
            n_clusters=2,
        )
        result = artifacts.calibration.timeseries
        weights = result.groupby("representative_day")["period_weight"].first()
        weighted_energy = sum(
            float(group["heat_demand"].sum()) * float(weights.loc[day])
            for day, group in result.groupby("representative_day")
        )

        self.assertAlmostEqual(float(weights.sum()), 2.0, places=7)
        self.assertAlmostEqual(float(result["heat_demand"].max()), 9.0)
        diagnostic = artifacts.calibration.diagnostics.set_index("column").loc[
            "heat_demand"
        ]
        self.assertAlmostEqual(
            weighted_energy,
            float(diagnostic["calibrated_representative_total"]),
            places=6,
        )
        self.assertAlmostEqual(float(diagnostic["chronology_total"]), 180.0)
        self.assertLessEqual(
            abs(float(diagnostic["calibrated_relative_bias"])),
            abs(float(diagnostic["original_relative_bias"])) + 1e-10,
        )
        extreme_years = set(
            result.loc[result["is_extreme_day"], "source_weather_year"]
        )
        self.assertIn("2024", extreme_years)

    def test_stochastic_bundle_preserves_each_year_as_independent_scenario(self):
        def two_days(year: int, first: float, second: float) -> pd.DataFrame:
            return pd.DataFrame(
                {
                    "time": pd.date_range(
                        f"{year}-01-01", periods=48, freq="h"
                    ),
                    "heat_demand": [first] * 24 + [second] * 24,
                    "outdoor_temp": [5.0] * 24 + [-10.0] * 24,
                    "electricity_price": 0.6,
                    "grid_emission_factor": 0.5,
                }
            )

        artifacts = build_multiyear_stochastic_representative_bundle(
            self._config(),
            {
                2023: two_days(2023, 1.0, 3.0),
                2024: two_days(2024, 2.0, 9.0),
            },
            scenario_probabilities={2023: 0.25, 2024: 0.75},
            n_clusters_per_year=1,
        )
        bundle = artifacts.scenario_bundle
        self.assertEqual(
            set(bundle["scenario"]),
            {"weather_year_2023", "weather_year_2024"},
        )
        self.assertFalse(bundle["time"].astype(str).duplicated().any())
        self.assertTrue(
            bundle.groupby("boundary_group")["scenario"].nunique().eq(1).all()
        )
        year_weights = (
            bundle.groupby(["scenario", "representative_day"])[
                "period_weight"
            ]
            .first()
            .groupby(level=0)
            .sum()
        )
        self.assertAlmostEqual(year_weights["weather_year_2023"], 2.0)
        self.assertAlmostEqual(year_weights["weather_year_2024"], 2.0)

        row_weights = (
            bundle["period_weight"].astype(float)
            * bundle["scenario_probability"].astype(float)
        )
        expected_energy = float((bundle["heat_demand"] * row_weights).sum())
        self.assertAlmostEqual(expected_energy, 222.0, places=5)
        self.assertEqual(
            artifacts.scenario_probabilities,
            {"weather_year_2023": 0.25, "weather_year_2024": 0.75},
        )

    def test_stochastic_bundle_rejects_invalid_year_probabilities(self):
        annual = {
            "2023": pd.DataFrame(
                {
                    "time": pd.date_range("2023-01-01", periods=24, freq="h"),
                    "heat_demand": 1.0,
                    "outdoor_temp": 0.0,
                    "electricity_price": 0.6,
                    "grid_emission_factor": 0.5,
                }
            )
        }
        with self.assertRaisesRegex(ValueError, "概率之和"):
            build_multiyear_stochastic_representative_bundle(
                self._config(),
                annual,
                scenario_probabilities={"2023": 0.9},
                n_clusters_per_year=1,
            )

    def test_base_policy_scope_expands_only_to_weather_year_scenarios(self):
        config = self._config()
        config["policy"] = {"transition_target_scenarios": ["base"]}
        bundle = pd.DataFrame(
            {
                "scenario": ["weather_year_2023", "weather_year_2024"]
            }
        )
        adapted, audit = adapt_config_to_weather_year_scenarios(config, bundle)
        self.assertEqual(
            adapted["policy"]["transition_target_scenarios"],
            ["weather_year_2023", "weather_year_2024"],
        )
        self.assertTrue(audit["mapping_applied"])
        self.assertEqual(
            config["policy"]["transition_target_scenarios"], ["base"]
        )

        config["policy"]["transition_target_scenarios"] = ["cold_wave"]
        with self.assertRaisesRegex(ValueError, "无法安全映射"):
            adapt_config_to_weather_year_scenarios(config, bundle)


if __name__ == "__main__":
    unittest.main()
