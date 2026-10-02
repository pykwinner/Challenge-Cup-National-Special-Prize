from pathlib import Path
import copy
import tempfile
import unittest

import pandas as pd
import yaml

from heatplan.model import HeatPlanningModel
from heatplan.io import load_inputs
from heatplan.experiments import (
    epsilon_constraint_pareto,
    northern_cold_wave_stress_test,
    northern_resilience_stress_suite,
)
from heatplan.chronology import prepare_full_year_timeseries, seasonal_storage_postcheck
from heatplan.btes import btes_go_no_go, simulate_btes_long_term
from heatplan.feasibility import apply_feasibility_exclusions, evaluate_feasibility, load_feasibility
from heatplan.finance import break_even_tariff, evaluate_project_finance
from heatplan.hydraulics import hydraulic_thermal_postcheck
from heatplan.multistage import solve_joint_multistage
from heatplan.validation import monte_carlo_out_of_sample_replay
from heatplan.scenarios import build_correlated_northern_scenario_bundle
from heatplan.reporting import _configured_demand, plot_dispatch


ROOT = Path(__file__).resolve().parents[1]


class SmokeTest(unittest.TestCase):
    def test_chp_heat_rejection_preserves_minimum_electricity_operation(self):
        config = {
            "planning": {
                "discount_rate": 0.05,
                "asset_lifetime_years": 20,
                "time_step_hours": 1.0,
                "max_unserved_rate": 0.0,
                "max_scenario_unserved_rate": 0.0,
            },
            "grades": [{"name": "low", "temperature_c": 45.0}],
            "nodes": [
                {
                    "name": "plant",
                    "demand_columns": {"low": "heat_demand"},
                }
            ],
            "sources": [
                {
                    "name": "chp",
                    "node": "plant",
                    "type": "chp",
                    "resource_kind": "fossil",
                    "allowed_grades": ["low"],
                    "supply_temp_c": 90.0,
                    "existing_capacity_kw": 100.0,
                    "max_new_capacity_kw": 0.0,
                    "capex_yuan_per_kw": 0.0,
                    "variable_cost_yuan_per_kwh_heat": 0.2,
                    "emission_kg_per_kwh_heat": 0.2,
                    "electricity_to_heat_ratio_min": 0.2,
                    "electricity_to_heat_ratio_max": 0.5,
                    "minimum_electricity_output_column": "minimum_power",
                    "heat_rejection_enabled": True,
                    "max_heat_rejection_kw": 50.0,
                    "heat_rejection_penalty_yuan_per_kwh": 0.5,
                }
            ],
            "pipes": [],
            "storages": [],
        }
        frame = pd.DataFrame(
            {
                "time": [0],
                "heat_demand": [0.0],
                "minimum_power": [10.0],
                "electricity_price": [0.6],
                "grid_emission_factor": [0.5],
            }
        )

        artifacts = HeatPlanningModel(config, frame).solve(time_limit=30)
        rejected = artifacts.dispatch[
            artifacts.dispatch["category"] == "chp_heat_rejection"
        ]

        self.assertEqual(artifacts.summary["solver_status"], 0)
        self.assertGreaterEqual(float(rejected["value"].sum()), 20.0 - 1e-7)
        self.assertAlmostEqual(
            artifacts.summary["rejected_heat_kwh_per_year"],
            float(rejected["value"].sum()),
        )

    def test_example_is_feasible_and_balanced(self):
        model = HeatPlanningModel.from_files(ROOT / "data/example_city.yaml", ROOT / "data/example_timeseries.csv")
        artifacts = model.solve(time_limit=30)
        self.assertIn(artifacts.summary["solver_status"], (0, 1))
        self.assertLess(artifacts.summary["unserved_rate"], 1e-6)
        self.assertGreater(len(artifacts.dispatch), 0)
        self.assertTrue((artifacts.investments["value"] >= -1e-7).all())
        self.assertIn("exergy_accounting_valid", artifacts.summary)

    def test_dynamic_and_bidirectional_variables_are_built(self):
        model = HeatPlanningModel.from_files(ROOT / "data/example_city.yaml", ROOT / "data/example_timeseries.csv")
        model.build()
        self.assertIn(("gas_peak_boiler", 0), model.variables["source_on"])
        self.assertIn(("interconnector_ab", "high", 0), model.variables["pipe_flow_reverse"])
        self.assertTrue(model.variables["terminal_temperature_retrofit"])
        self.assertTrue(model.variables["grid_connection_capacity"])
        self.assertTrue(model.variables["storage_firm_capacity"])

    def test_unit_commitment_uses_exact_continuous_transition_indicators(self):
        model = HeatPlanningModel.from_files(
            ROOT / "data/example_city.yaml",
            ROOT / "data/example_timeseries.csv",
        )
        artifacts = model.solve(time_limit=30)
        source = "gas_peak_boiler"
        self.assertEqual(
            artifacts.summary["unit_commitment_binary_state_variables"],
            len(model.times),
        )
        self.assertEqual(
            artifacts.summary[
                "unit_commitment_continuous_transition_variables"
            ],
            2 * len(model.times),
        )
        self.assertLessEqual(
            artifacts.summary["unit_commitment_max_transition_residual"],
            1e-7,
        )
        self.assertLessEqual(
            artifacts.summary["unit_commitment_max_indicator_fractionality"],
            1e-7,
        )
        for t in model.times:
            on_index = model.variables["source_on"][(source, t)]
            startup_index = model.variables["source_startup"][(source, t)]
            shutdown_index = model.variables["source_shutdown"][(source, t)]
            self.assertEqual(model.problem.integrality[on_index], 1)
            self.assertEqual(model.problem.integrality[startup_index], 0)
            self.assertEqual(model.problem.integrality[shutdown_index], 0)

            previous = model.time_predecessor[t]
            previous_on_index = model.variables["source_on"][(source, previous)]
            transition = (
                artifacts.values[on_index]
                - artifacts.values[previous_on_index]
            )
            self.assertAlmostEqual(
                artifacts.values[startup_index],
                max(0.0, transition),
                places=7,
            )
            self.assertAlmostEqual(
                artifacts.values[shutdown_index],
                max(0.0, -transition),
                places=7,
            )

    def test_epsilon_frontier_respects_caps(self):
        frontier = epsilon_constraint_pareto(
            ROOT / "data/example_city.yaml", ROOT / "data/example_timeseries.csv", points=3
        )
        self.assertEqual(len(frontier), 3)
        self.assertTrue(
            (frontier["emissions_kg_per_year"] <= frontier["emissions_cap_kg_per_year"] * (1 + 2e-8)).all()
        )

    def test_northern_cold_wave_stress_runs_with_fixed_investment(self):
        result = northern_cold_wave_stress_test(
            ROOT / "data/example_city.yaml", ROOT / "data/example_timeseries.csv"
        )
        self.assertEqual(list(result["scenario"]), ["nominal", "compound_cold_wave"])
        self.assertGreater(result.loc[1, "demand_kwh_per_year"], result.loc[0, "demand_kwh_per_year"])

    def test_northern_resilience_suite_contains_n_minus_1_cases(self):
        result = northern_resilience_stress_suite(
            ROOT / "data/example_city.yaml", ROOT / "data/example_timeseries.csv"
        )
        self.assertTrue(result["scenario"].str.startswith("n_minus_1_source").any())
        self.assertTrue(result["scenario"].str.startswith("n_minus_1_pipe").any())
        self.assertTrue((result.loc[result["feasible"], "unserved_rate"] >= 0).all())
        self.assertTrue(result.loc[~result["feasible"], "unserved_rate"].isna().all())

    def test_correlated_scenario_bundle_keeps_shared_investment_and_boundaries(self):
        config, base = load_inputs(ROOT / "data/example_city.yaml", ROOT / "data/example_timeseries.csv")
        demand_columns = [column for node in config["nodes"] for column in node.get("demand_columns", {}).values()]
        bundle = build_correlated_northern_scenario_bundle(
            base,
            demand_columns,
            [
                {"name": "normal", "probability": 0.75},
                {
                    "name": "cold_wave",
                    "probability": 0.25,
                    "temperature_offset_c": -8,
                    "demand_multiplier": 1.15,
                    "waste_heat_multiplier": 0.80,
                },
            ],
            period_weight=180,
        )
        risk_config = copy.deepcopy(config)
        risk_config["planning"]["risk"] = {"cvar_alpha": 0.80, "cvar_weight": 0.05}
        model = HeatPlanningModel(risk_config, bundle)
        artifacts = model.solve(time_limit=60)
        self.assertEqual(set(artifacts.summary["scenario_results"]), {"normal", "cold_wave"})
        self.assertIn("cvar_risk_premium", artifacts.summary["components_yuan_per_year"])
        scenario_costs = [
            (
                float(item["operating_risk_cost_yuan"]),
                float(item["probability"]),
            )
            for item in artifacts.summary["scenario_results"].values()
        ]
        candidates = sorted({0.0, *(cost for cost, _ in scenario_costs)})
        empirical_cvar = min(
            eta
            + sum(
                probability * max(cost - eta, 0.0)
                for cost, probability in scenario_costs
            )
            / (1.0 - 0.80)
            for eta in candidates
        )
        reported_premium = artifacts.summary["components_yuan_per_year"][
            "cvar_risk_premium"
        ]
        self.assertAlmostEqual(
            reported_premium / 0.05,
            empirical_cvar,
            delta=max(1e-5, 1e-8 * abs(empirical_cvar)),
        )
        self.assertLess(artifacts.summary["worst_scenario_unserved_rate"], 1e-7)
        self.assertEqual(set(artifacts.dispatch["scenario"]), {"normal", "cold_wave"})

    def test_scenario_bundle_preserves_representative_day_weights_and_boundaries(self):
        config, base = load_inputs(ROOT / "data/example_city.yaml", ROOT / "data/example_timeseries.csv")
        demand_columns = [column for node in config["nodes"] for column in node.get("demand_columns", {}).values()]
        base = base.copy()
        base["period_weight"] = [10.0] * 12 + [20.0] * 12
        base["boundary_group"] = ["day_a"] * 12 + ["day_b"] * 12
        bundle = build_correlated_northern_scenario_bundle(
            base,
            demand_columns,
            [
                {"name": "normal", "probability": 0.75},
                {"name": "cold", "probability": 0.25},
            ],
            period_weight=180,
        )
        normal = bundle[bundle["scenario"] == "normal"]
        self.assertEqual(normal["period_weight"].tolist(), base["period_weight"].tolist())
        self.assertEqual(set(normal["boundary_group"]), {"normal::day_a", "normal::day_b"})

        priced = base.copy()
        priced["resident_heating_electricity_price"] = 0.25
        price_bundle = build_correlated_northern_scenario_bundle(
            priced,
            demand_columns,
            [{"name": "price_stress", "probability": 1.0, "electricity_price_multiplier": 1.2}],
            period_weight=180,
        )
        self.assertTrue(
            (price_bundle["resident_heating_electricity_price"] == 0.30).all()
        )

    def test_policy_constraints_and_subsidy_metrics_are_auditable(self):
        config, frame = load_inputs(ROOT / "data/example_city.yaml", ROOT / "data/example_timeseries.csv")
        config = copy.deepcopy(config)
        config["policy"] = {
            "minimum_clean_heat_share": 0.80,
            "minimum_waste_heat_share": 0.35,
            "maximum_fossil_heat_share": 0.20,
            "emission_intensity_cap_kg_per_kwh": 0.070,
            "minimum_retrofit_heat_share": 0.05,
            "maximum_levelized_heat_cost_yuan_per_kwh": 0.25,
            "public_subsidy_budget_yuan_per_year": 150000,
            "capex_subsidy_fraction": {"terminal_temperature_retrofit": 0.35},
        }
        config["planning"]["heating_cost_allocation_fraction"] = {
            "terminal_temperature_retrofit": 0.75,
        }
        artifacts = HeatPlanningModel(config, frame).solve(time_limit=60)
        summary = artifacts.summary
        self.assertGreaterEqual(summary["clean_heat_share"], 0.80 - 1e-7)
        self.assertGreaterEqual(summary["waste_heat_share"], 0.35 - 1e-7)
        self.assertLessEqual(summary["fossil_heat_share"], 0.20 + 1e-7)
        self.assertLessEqual(summary["emission_intensity_kg_per_kwh"], 0.070 + 1e-7)
        self.assertGreaterEqual(summary["retrofitted_heat_share"], 0.05 - 1e-7)
        self.assertLessEqual(summary["regulated_levelized_heat_cost_yuan_per_kwh"], 0.25 + 1e-7)
        self.assertGreater(summary["public_subsidy_yuan_per_year"], 0.0)
        self.assertGreater(summary["shared_non_heating_capex_yuan_per_year"], 0.0)
        self.assertLess(
            summary["regulated_annualized_capex_yuan_per_year"],
            summary["components_yuan_per_year"]["annualized_capex"],
        )
        self.assertTrue(summary["exergy_accounting_valid"])
        self.assertLessEqual(summary["raw_exergy_efficiency"], 1.0 + 1e-7)

    def test_engineering_gates_and_hydraulic_postcheck(self):
        config, frame = load_inputs(ROOT / "data/example_city.yaml", ROOT / "data/example_timeseries.csv")
        feasibility = evaluate_feasibility(load_feasibility(ROOT / "data/example_feasibility.yaml"))
        self.assertTrue(feasibility.passed)
        filtered = apply_feasibility_exclusions(config, feasibility)
        artifacts = HeatPlanningModel(filtered, frame).solve(time_limit=60)
        hydraulic = hydraulic_thermal_postcheck(filtered, artifacts.investments, artifacts.dispatch)
        self.assertFalse(hydraulic.empty)
        self.assertFalse((hydraulic["status"] == "fail").any())

    def test_finance_cashflow_and_break_even_tariff(self):
        profile = pd.DataFrame({
            "year": [2026, 2027, 2028, 2029],
            "demand_kwh": [1_000_000] * 4,
            "operating_cost_yuan": [100_000] * 4,
            "gross_capex_yuan": [300_000, 0, 0, 0],
            "subsidy_yuan": [30_000, 0, 0, 0],
            "residual_value_yuan": [0, 0, 0, 60_000],
        })
        finance = {"tariff_yuan_per_kwh": 0.20, "collection_rate": 0.98, "discount_rate": 0.06}
        artifacts = evaluate_project_finance(profile, finance)
        self.assertEqual(len(artifacts.cashflow), 4)
        tariff = break_even_tariff(profile, finance)
        self.assertGreater(tariff, 0.0)
        self.assertLess(tariff, 1.0)

    def test_continuous_chronology_and_storage_postcheck(self):
        _, frame = load_inputs(ROOT / "data/example_city.yaml", ROOT / "data/example_timeseries.csv")
        chronological = frame.iloc[:4].copy()
        chronological["timestamp"] = pd.date_range("2026-01-01", periods=4, freq="h")
        prepared = prepare_full_year_timeseries(chronological)
        self.assertEqual(prepared["boundary_group"].nunique(), 1)
        self.assertTrue((prepared["period_weight"] == 1.0).all())

    def test_joint_multistage_builds_once_and_reports_finance(self):
        artifacts = solve_joint_multistage(
            ROOT / "data/example_city.yaml",
            ROOT / "data/example_timeseries.csv",
            ROOT / "data/example_scenario_specs.yaml",
            ROOT / "data/example_policy_pathway.yaml",
            feasibility_path=ROOT / "data/example_feasibility.yaml",
            time_limit=180,
        )
        self.assertEqual(set(artifacts.stage_summary["stage"]), {2026, 2030, 2035})
        self.assertLess(artifacts.stage_summary["worst_scenario_unserved_rate"].max(), 1e-7)
        binary = artifacts.build_schedule[artifacts.build_schedule["unit"] == "binary"]
        self.assertTrue((binary.groupby(["category", "asset", "option"])["value"].sum() <= 1.0 + 1e-7).all())
        self.assertIsNotNone(artifacts.finance)

    def test_monte_carlo_out_of_sample_replay(self):
        config, frame = load_inputs(ROOT / "data/example_city.yaml", ROOT / "data/example_timeseries.csv")
        result = monte_carlo_out_of_sample_replay(config, frame, samples=3, time_limit=60)
        self.assertEqual(len(result), 3)
        self.assertTrue((result["unserved_rate"] >= 0).all())
        self.assertTrue((result["replay_service_policy"] == "diagnostic_relaxed_reliability").all())
        self.assertTrue((result["disturbance_evidence"] == "generated_research_stress_scenarios").all())

    def test_btes_multi_year_screening(self):
        import yaml
        specification = yaml.safe_load((ROOT / "data/example_btes.yaml").read_text(encoding="utf-8"))
        result = simulate_btes_long_term(specification)
        decision = btes_go_no_go(result, specification)
        self.assertEqual(len(result), 10)
        self.assertIn(decision["status"], {"pass", "fail"})
        self.assertTrue((result["thermal_loss_kwh"] >= 0).all())

    def test_first_scenario_profile_chp_renewables_and_backbone_retrofit(self):
        config, frame = load_inputs(
            ROOT / "data/scenario1_existing_network.yaml",
            ROOT / "data/scenario1_existing_network_timeseries.csv",
        )
        config = copy.deepcopy(config)
        config["policy"] = {"minimum_pipe_retrofit_count": 1}
        artifacts = HeatPlanningModel(config, frame).solve(time_limit=90)
        self.assertIn("existing_chp", {source["name"] for source in config["sources"]})
        self.assertGreater(artifacts.summary["chp_electricity_kwh_per_year"], 0.0)
        self.assertGreater(artifacts.summary["renewable_electricity_kwh_per_year"], 0.0)
        retrofit = artifacts.investments[
            (artifacts.investments["category"] == "pipe_retrofit")
            & (artifacts.investments["asset"] == "trunk_a")
        ]
        self.assertEqual(float(retrofit.iloc[0]["value"]), 1.0)
        chp_heat = artifacts.dispatch[
            (artifacts.dispatch["category"] == "source_heat")
            & (artifacts.dispatch["asset"] == "existing_chp")
        ].groupby("time_index")["value"].sum()
        chp_power = artifacts.dispatch[artifacts.dispatch["category"] == "chp_electricity_output"].set_index("time_index")["value"]
        active = chp_heat > 1e-7
        ratios = chp_power[active] / chp_heat[active]
        self.assertTrue((ratios >= 0.20 - 1e-7).all())
        self.assertTrue((ratios <= 0.45 + 1e-7).all())
        self.assertIn("source_retirement", set(artifacts.investments["category"]))

        # A positive minimum-electricity profile is an on-state floor, not an
        # unconditional must-run instruction.  With zero shoulder-season heat
        # demand and no storage sink, the CHP must be allowed to switch off.
        shoulder_config = copy.deepcopy(config)
        shoulder_config["policy"] = {}
        for storage in shoulder_config["storages"]:
            storage["existing_capacity_kwh"] = 0.0
            storage["max_new_capacity_kwh"] = 0.0
        shoulder_frame = frame.copy()
        for node in shoulder_config["nodes"]:
            for column in node.get("demand_columns", {}).values():
                shoulder_frame[column] = 0.0
        shoulder_model = HeatPlanningModel(shoulder_config, shoulder_frame)
        shoulder = shoulder_model.solve(time_limit=30)
        self.assertEqual(shoulder.summary["unserved_rate"], 0.0)
        chp_on = [
            shoulder.values[index]
            for (source, _), index in shoulder_model.variables["source_on"].items()
            if source == "existing_chp"
        ]
        self.assertTrue(all(value < 0.5 for value in chp_on))

    def test_second_scenario_biomass_inventory_and_microgrid_islanding(self):
        config, frame = load_inputs(
            ROOT / "data/scenario2_distributed_clean.yaml",
            ROOT / "data/scenario2_timeseries.csv",
        )
        specs = yaml.safe_load((ROOT / "data/scenario2_scenario_specs.yaml").read_text(encoding="utf-8"))
        demand_columns = [
            column for node in config["nodes"] for column in node.get("demand_columns", {}).values()
        ]
        bundle = build_correlated_northern_scenario_bundle(
            frame, demand_columns, specs["scenarios"], specs["period_weight"]
        )
        config["planning"]["risk"] = specs["risk"]
        model = HeatPlanningModel(config, bundle)
        artifacts = model.solve(time_limit=120)
        self.assertGreater(artifacts.summary["biomass_fuel_kg_per_year"], 0.0)
        components = artifacts.summary["components_yuan_per_year"]
        expected_cash_cost = sum(
            components.get(name, 0.0)
            for name in (
                "operating_cost", "carbon_cost", "fixed_om_cost",
                "startup_shutdown_cost", "biomass_fuel_cost",
            )
        )
        self.assertAlmostEqual(
            artifacts.summary["heating_cash_operating_cost_yuan_per_year"],
            expected_cash_cost,
            places=5,
        )
        self.assertGreaterEqual(
            artifacts.summary["heating_cash_operating_cost_yuan_per_year"] + 1e-7,
            components["biomass_fuel_cost"],
        )
        self.assertTrue(artifacts.summary["microgrid_island_design_enabled"])
        self.assertEqual(
            artifacts.summary["scenario_results"]["regional_grid_islanding"]["unserved_rate"], 0.0
        )
        island_metrics = artifacts.summary["scenario_results"]["regional_grid_islanding"]
        self.assertAlmostEqual(island_metrics["grid_electricity_kwh"], 0.0, places=5)
        self.assertGreater(
            island_metrics["microgrid_renewable_electricity_kwh"]
            + island_metrics["microgrid_backup_electricity_kwh"],
            0.0,
        )
        self.assertIn("microgrid_backup_capacity", set(artifacts.investments["category"]))
        self.assertIn("microgrid_battery_soc", set(artifacts.dispatch["category"]))
        maximum_soc = artifacts.summary["microgrid_battery_energy_kwh"]
        battery_soc = artifacts.dispatch[artifacts.dispatch["category"] == "microgrid_battery_soc"]
        self.assertTrue((battery_soc["value"] <= maximum_soc + 1e-6).all())
        charge = artifacts.dispatch[
            artifacts.dispatch["category"] == "microgrid_battery_charge"
        ].set_index("time_index")["value"]
        discharge = artifacts.dispatch[
            artifacts.dispatch["category"] == "microgrid_battery_discharge"
        ].set_index("time_index")["value"]
        self.assertTrue(((charge * discharge) <= 1e-7).all())
        heat_charge = artifacts.dispatch[
            artifacts.dispatch["category"] == "storage_charge"
        ].set_index(["asset", "time_index"])["value"]
        heat_discharge = artifacts.dispatch[
            artifacts.dispatch["category"] == "storage_discharge"
        ].set_index(["asset", "time_index"])["value"]
        self.assertTrue(((heat_charge * heat_discharge) <= 1e-7).all())
        deliveries = artifacts.dispatch[artifacts.dispatch["category"] == "biomass_delivery"]
        annual_limit = float(config["biomass_supply_chains"][0]["annual_supply_limit_kg"])
        for _, block in deliveries.groupby("scenario"):
            annual_delivery = float((block["value"] * specs["period_weight"]).sum())
            self.assertLessEqual(annual_delivery, annual_limit + 1e-5)

    def test_third_scenario_humid_defrost_intermittency_and_comfort(self):
        config, frame = load_inputs(
            ROOT / "data/scenario3_high_quality_newbuild.yaml",
            ROOT / "data/scenario3_timeseries.csv",
        )
        specs = yaml.safe_load((ROOT / "data/scenario3_scenario_specs.yaml").read_text(encoding="utf-8"))
        demand_columns = [
            column for node in config["nodes"] for column in node.get("demand_columns", {}).values()
        ]
        bundle = build_correlated_northern_scenario_bundle(
            frame, demand_columns, specs["scenarios"], specs["period_weight"]
        )
        config["planning"]["risk"] = specs["risk"]
        model = HeatPlanningModel(config, bundle)
        artifacts = model.solve(time_limit=120)
        self.assertEqual(artifacts.summary["firm_grid_capacity_method"], "portfolio_dispatch")
        self.assertGreater(artifacts.summary["firm_portfolio_electric_requirement_kw"], 0.0)
        self.assertGreaterEqual(
            artifacts.summary["grid_connection_capacity_kw"] + 1e-6,
            artifacts.summary["firm_portfolio_electric_requirement_kw"],
        )
        self.assertAlmostEqual(
            artifacts.summary["building_flex_firm_capacity_kw"], 82.75, places=6
        )
        self.assertNotIn(
            ("interconnector_ab", "DN150"), model.variables["pipe_choice"]
        )
        self.assertEqual(artifacts.summary["worst_scenario_unserved_rate"], 0.0)
        self.assertGreaterEqual(artifacts.summary["clean_heat_share"], 0.90)
        self.assertLessEqual(
            artifacts.summary["occupied_temperature_deviation_degree_hours"],
            artifacts.summary["temperature_deviation_degree_hours"] + 1e-8,
        )

    def test_wuhan_joint_heating_cooling_and_ground_balance(self):
        config, frame = load_inputs(
            ROOT / "data/scenario3_high_quality_newbuild.yaml",
            ROOT / "data/scenario3_timeseries.csv",
        )
        config = copy.deepcopy(config)
        config["policy"].pop("maximum_levelized_heat_cost_yuan_per_kwh", None)
        frame = frame.copy()
        frame["cooling_demand_a"] = 26.0
        frame["cooling_demand_b"] = 22.0
        balance = {
            "minimum_extraction_to_rejection_ratio": 0.8,
            "maximum_extraction_to_rejection_ratio": 1.25,
        }
        config["multi_energy_services"] = {
            "cooling": {
                "demand_columns": {
                    "district_a": "cooling_demand_a",
                    "district_b": "cooling_demand_b",
                },
                "sources": {
                    "district_a_ground_hp": {
                        "eer": 5.0,
                        "ground_thermal_balance": balance,
                    },
                    "district_b_ground_hp": {
                        "eer": 5.0,
                        "ground_thermal_balance": balance,
                    },
                },
                "max_unserved_rate": 0.0,
                "max_scenario_unserved_rate": 0.0,
            }
        }
        model = HeatPlanningModel(config, frame)
        artifacts = model.solve(time_limit=60)
        self.assertAlmostEqual(artifacts.summary["cooling_unserved_rate"], 0.0, places=7)
        self.assertGreater(artifacts.summary["cooling_demand_kwh_per_year"], 0.0)
        self.assertGreater(artifacts.summary["cooling_electricity_kwh_per_year"], 0.0)
        self.assertGreaterEqual(
            artifacts.summary["ground_extraction_to_rejection_ratio"], 0.8 - 1e-6
        )
        self.assertLessEqual(
            artifacts.summary["ground_extraction_to_rejection_ratio"], 1.25 + 1e-6
        )
        self.assertIn("cooling_output", set(artifacts.dispatch["category"]))
        self.assertGreater(
            artifacts.summary["joint_service_levelized_cost_yuan_per_kwh"], 0.0
        )

    def test_multiscenario_dispatch_plot_uses_configured_demand(self):
        config, frame = load_inputs(
            ROOT / "data/scenario3_high_quality_newbuild.yaml",
            ROOT / "data/scenario3_timeseries.csv",
        )
        demand = _configured_demand(config, frame, "normal_intermittent")
        raw_columns = [
            column for node in config["nodes"] for column in node.get("demand_columns", {}).values()
        ]
        self.assertLess(float(demand.sum()), float(frame[raw_columns].sum().sum()))
        dispatch = pd.DataFrame([
            {
                "category": "source_heat", "asset": "demo", "node": "district_a",
                "grade": "low", "time_index": index, "time": row["time"],
                "scenario": scenario, "value": float(demand.iloc[index]), "unit": "kW",
            }
            for scenario in ("normal_intermittent", "humid_cold_snap")
            for index, (_, row) in enumerate(frame.iterrows())
        ])
        timeseries = pd.concat([
            frame.assign(scenario="normal_intermittent"),
            frame.assign(scenario="humid_cold_snap"),
        ], ignore_index=True)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "dispatch.png"
            plot_dispatch(output, dispatch, config, timeseries)
            self.assertTrue(output.exists())
            self.assertGreater(output.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
