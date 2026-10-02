"""Small physical cases for investment, cash-ledger and terminal-date regressions."""
from __future__ import annotations

import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd
import yaml

from heatplan.finance import evaluate_project_finance
from heatplan.multistage import INVESTMENT_CATEGORIES, solve_joint_multistage


class MultiStageAccountingRegressionTest(unittest.TestCase):
    @staticmethod
    def _config() -> dict:
        return {
            "planning": {
                "discount_rate": 0.05, "asset_lifetime_years": 20,
                "time_step_hours": 1, "representative_day_weight": 1,
                "max_unserved_rate": 0, "max_scenario_unserved_rate": 0,
                "firm_capacity_reserve_margin": 0, "mip_gap": 0,
            },
            "grades": [{"name": "low", "temperature_c": 45}],
            "nodes": [{"name": "load", "demand_columns": {"low": "heat_demand"}}],
            "sources": [{
                "name": "boiler", "node": "load", "type": "boiler",
                "resource_kind": "fossil", "allowed_grades": ["low"], "supply_temp_c": 80,
                "existing_capacity_kw": 10, "max_new_capacity_kw": 1,
                "capex_yuan_per_kw": 1_000_000,
                "variable_cost_yuan_per_kwh_heat": 1, "emission_kg_per_kwh_heat": 0,
            }],
            "pipes": [], "storages": [],
        }

    def _solve(self, config: dict, pathway: dict, *, scenarios: list[dict] | None = None):
        frame = pd.DataFrame({
            "time": [0, 1], "heat_demand": [10, 10],
            "electricity_price": [0.6, 0.6], "grid_emission_factor": [0.5, 0.5],
        })
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, data in (
                ("config.yaml", config), ("pathway.yaml", pathway),
                ("scenarios.yaml", {
                    "period_weight": 1,
                    "scenarios": scenarios or [{"name": "base", "probability": 1}],
                }),
            ):
                (root / name).write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
            frame.to_csv(root / "timeseries.csv", index=False)
            return solve_joint_multistage(
                root / "config.yaml", root / "timeseries.csv", root / "scenarios.yaml",
                root / "pathway.yaml", time_limit=10, mip_gap=0,
            )

    @staticmethod
    def _add_meter(config: dict, *, cost: float = 1_000_000) -> dict:
        meter = {
            "existing_coverage_fraction": 0, "max_new_coverage_fraction": 1,
            "saving_fraction_at_full_coverage": 0.1,
            "capex_yuan_per_coverage_fraction": cost,
        }
        config["nodes"][0]["heat_metering"] = meter
        return meter

    def _biomass_config(self) -> dict:
        config = self._config()
        config["sources"][0].update(resource_kind="biomass", variable_cost_yuan_per_kwh_heat=0)
        config["biomass_supply_chains"] = [{
            "name": "fuel", "source_names": ["boiler"],
            "lower_heating_value_kwh_per_kg": 2, "conversion_efficiency": 1,
            "procurement_cost_yuan_per_kg": 4, "existing_inventory_capacity_kg": 20,
            "max_new_inventory_capacity_kg": 0, "delivery_capacity_kg_per_hour": 20,
            "annual_supply_limit_kg": 100,
        }]
        return config

    def _future_config(self) -> dict:
        config = self._config()
        config["sources"][0]["max_new_capacity_kw"] = 0
        future = copy.deepcopy(config["sources"][0])
        future.update(
            name="future", existing_capacity_kw=0, max_new_capacity_kw=1,
            capex_yuan_per_kw=100, variable_cost_yuan_per_kwh_heat=0,
            construction_lead_years=10, asset_lifetime_years=20,
        )
        config["sources"].append(future)
        return config

    def test_expensive_meter_is_not_a_free_unregistered_investment(self):
        config = self._config()
        self._add_meter(config)
        artifacts = self._solve(config, {
            "horizon_end_year": 2031, "stages": [{"year": 2026}, {"year": 2030}],
        })
        builds = artifacts.build_schedule.query("category == 'heat_metering_coverage'")
        self.assertEqual(len(builds), 2)
        self.assertAlmostEqual(float(builds.value.sum()), 0.0)
        self.assertAlmostEqual(float(artifacts.cumulative_investments.query(
            "category == 'heat_metering_coverage'"
        ).value.sum()), 0.0)

    def test_missing_investment_registration_fails_before_cost_is_removed(self):
        config = self._config()
        self._add_meter(config, cost=0)
        # Even a currently zero-cost category must have its physical carry-over
        # registered, so future cost/subsidy changes cannot silently drop it.
        with patch("heatplan.multistage.INVESTMENT_CATEGORIES", INVESTMENT_CATEGORIES - {"heat_metering_coverage"}):
            with self.assertRaisesRegex(ValueError, "投资类别登记不完整.*heat_metering"):
                self._solve(config, {"horizon_end_year": 2027, "stages": [{"year": 2026}]})

    def test_meter_batches_preserve_lead_life_subsidy_and_heat_allocation(self):
        config = self._config()
        meter = self._add_meter(config, cost=100)
        meter.update(construction_lead_years=2, asset_lifetime_years=5)
        config["planning"]["heating_cost_allocation_fraction"] = {"heat_metering_coverage": 0.25}
        artifacts = self._solve(config, {
            "horizon_end_year": 2036,
            "stages": [
                {"year": 2026},
                {"year": 2030, "policy": {
                    "minimum_heat_metering_coverage_fraction": 0.6,
                    "capex_subsidy_fraction": {"heat_metering_coverage": 0.5},
                }},
                {"year": 2034, "policy": {"minimum_heat_metering_coverage_fraction": 0.6}},
            ],
        })
        builds = artifacts.build_schedule.query("category == 'heat_metering_coverage'").set_index("decision_year")
        self.assertAlmostEqual(builds.loc[2026, "value"], 0.6)
        self.assertAlmostEqual(builds.loc[2030, "value"], 0.6)
        self.assertEqual(builds.loc[2034, "value"], 0)
        self.assertEqual(builds.loc[2026, "commission_year"], 2028)
        self.assertEqual(builds.loc[2026, "retirement_year"], 2033)
        self.assertAlmostEqual(builds.loc[2030, "gross_capex_yuan"], 60)
        self.assertAlmostEqual(builds.loc[2030, "public_subsidy_yuan"], 30)
        self.assertAlmostEqual(builds.loc[2030, "salvage_yuan"], 6)
        cumulative = artifacts.cumulative_investments.query("category == 'heat_metering_coverage'").set_index("stage")
        self.assertEqual(cumulative.loc[2026, "value"], 0)
        self.assertAlmostEqual(cumulative.loc[2030, "value"], 0.6)
        self.assertAlmostEqual(cumulative.loc[2034, "value"], 0.6)
        summaries = artifacts.stage_summary.set_index("stage")
        crf = 0.05 * 1.05 ** 20 / (1.05 ** 20 - 1)
        self.assertAlmostEqual(summaries.loc[2030, "regulated_annualized_capex_yuan_per_year"], 60 * crf * 0.25)
        self.assertAlmostEqual(summaries.loc[2034, "regulated_annualized_capex_yuan_per_year"], 30 * crf * 0.25)
        self.assertAlmostEqual(summaries.loc[2030, "legacy_annualized_capex_yuan_per_year"], 60 * crf * 0.25)

    def test_meter_purchase_respects_investment_budget(self):
        config = self._config()
        self._add_meter(config, cost=100)
        with self.assertRaisesRegex(RuntimeError, "无可用解"):
            self._solve(config, {
                "horizon_end_year": 2027,
                "stages": [{"year": 2026, "policy": {
                    "minimum_heat_metering_coverage_fraction": 1,
                    "annualized_investment_budget_yuan": 0,
                }}],
            })

    def test_heat_allocation_is_used_in_joint_price_cap_not_only_report(self):
        config = self._config()
        self._add_meter(config, cost=100)
        config["planning"]["heating_cost_allocation_fraction"] = {"heat_metering_coverage": 0.25}
        artifacts = self._solve(config, {
            "horizon_end_year": 2027,
            "stages": [{"year": 2026, "policy": {
                "minimum_heat_metering_coverage_fraction": 1,
                "maximum_levelized_heat_cost_yuan_per_kwh": 1.1,
            }}],
        })
        # 18 operating + about 2 allocated annuity fits 22 yuan; the full
        # eight-yuan annuity would incorrectly reject this feasible plan.
        self.assertLess(artifacts.stage_summary.iloc[0]["regulated_levelized_heat_cost_yuan_per_kwh"], 1.1)

    def test_biomass_cash_cost_enforces_the_joint_regulated_price_cap(self):
        with self.assertRaisesRegex(RuntimeError, "无可用解"):
            self._solve(self._biomass_config(), {
                "horizon_end_year": 2027,
                "stages": [{"year": 2026, "policy": {"maximum_levelized_heat_cost_yuan_per_kwh": 1}}],
            })

    def test_biomass_ledger_reconciles_report_finance_and_tariff(self):
        artifacts = self._solve(self._biomass_config(), {
            "horizon_end_year": 2027,
            "finance": {"discount_rate": 0, "tariff_yuan_per_kwh": 2},
            "stages": [{"year": 2026, "policy": {"maximum_levelized_heat_cost_yuan_per_kwh": 2}}],
        })
        summary = artifacts.stage_summary.iloc[0]
        self.assertAlmostEqual(summary["regulated_levelized_heat_cost_yuan_per_kwh"], 2)
        self.assertAlmostEqual(summary["heating_cash_operating_cost_yuan_per_year"], 40)
        self.assertAlmostEqual(summary["component_biomass_fuel_cost_yuan_per_year"], 40)
        cashflow = artifacts.finance.cashflow
        self.assertEqual(cashflow.operating_cost_yuan.tolist(), [40, 0])
        self.assertEqual(cashflow.cash_biomass_fuel_cost_yuan.tolist(), [40, 0])
        self.assertAlmostEqual(artifacts.finance.metrics["break_even_tariff_yuan_per_kwh"], 2, delta=1e-6)

    def test_risk_premium_stays_in_objective_but_out_of_cash_ledger(self):
        artifacts = self._solve(self._config(), {
            "horizon_end_year": 2027,
            "finance": {"discount_rate": 0, "tariff_yuan_per_kwh": 1},
            "stages": [{"year": 2026, "risk": {"cvar_alpha": 0.5, "cvar_weight": 1}}],
        }, scenarios=[{"name": "a", "probability": 0.5}, {"name": "b", "probability": 0.5}])
        summary = artifacts.stage_summary.iloc[0]
        self.assertAlmostEqual(summary["component_cvar_risk_premium_yuan_per_year"], 20)
        self.assertAlmostEqual(summary["objective_yuan_per_year"], 40)
        self.assertAlmostEqual(summary["regulated_levelized_heat_cost_yuan_per_kwh"], 1)
        self.assertEqual(artifacts.finance.cashflow.operating_cost_yuan.tolist(), [20, 0])

    def test_external_legacy_cost_remains_in_regulatory_report_and_constraint(self):
        pathway = {
            "horizon_end_year": 2027,
            "stages": [{"year": 2026, "policy": {
                "legacy_annualized_capex_yuan": 20,
                "maximum_levelized_heat_cost_yuan_per_kwh": 2,
            }}],
        }
        artifacts = self._solve(self._config(), pathway)
        self.assertAlmostEqual(artifacts.stage_summary.iloc[0]["regulated_levelized_heat_cost_yuan_per_kwh"], 2)
        pathway["stages"][0]["policy"]["maximum_levelized_heat_cost_yuan_per_kwh"] = 1
        with self.assertRaisesRegex(RuntimeError, "无可用解"):
            self._solve(self._config(), pathway)

    def test_never_commissioned_asset_cannot_be_built_for_salvage(self):
        artifacts = self._solve(self._future_config(), {
            "horizon_end_year": 2030,
            "finance": {"discount_rate": 0, "tariff_yuan_per_kwh": 2},
            "stages": [{"year": 2026}],
        })
        future = artifacts.build_schedule.query("asset == 'future'").iloc[0]
        self.assertEqual(future["commission_year"], 2036)
        self.assertEqual(future["value"], 0)
        self.assertEqual(future["salvage_per_unit"], 0)
        self.assertAlmostEqual(artifacts.result.fun, 80)
        cashflow = artifacts.finance.cashflow
        self.assertEqual(cashflow.loc[~cashflow.is_terminal, "year"].tolist(), list(range(2026, 2030)))
        self.assertEqual(cashflow.operating_cost_yuan.tolist(), [20, 20, 20, 20, 0])
        self.assertEqual(cashflow.revenue_yuan.tolist(), [40, 40, 40, 40, 0])
        self.assertAlmostEqual(artifacts.finance.metrics["project_npv_yuan"], 80)

    def test_explicit_future_obligation_fixes_quantity_without_residual_credit(self):
        artifacts = self._solve(self._future_config(), {
            "horizon_end_year": 2030,
            "future_build_obligations": [{
                "decision_year": 2026, "category": "source_capacity", "asset": "future", "value": 0.5,
            }],
            "stages": [{"year": 2026}],
        })
        future = artifacts.build_schedule.query("asset == 'future'").iloc[0]
        self.assertEqual(future["value"], 0.5)
        self.assertEqual(future["private_capex_yuan"], 50)
        self.assertEqual(future["salvage_yuan"], 0)
        self.assertTrue(future["future_build_obligation"])

    def test_subsidy_does_not_create_a_gross_cost_residual_windfall(self):
        config = self._future_config()
        config["sources"][-1]["construction_lead_years"] = 0
        artifacts = self._solve(config, {
            "horizon_end_year": 2030,
            "stages": [{"year": 2026, "policy": {"capex_subsidy_fraction": {"source_capacity": 1}}}],
        })
        future = artifacts.build_schedule.query("asset == 'future'").iloc[0]
        self.assertEqual(future["private_cost_per_unit"], 0)
        self.assertEqual(future["salvage_per_unit"], 0)
        self.assertLessEqual(future["salvage_yuan"], future["private_capex_yuan"])

    def test_decommissioning_expense_has_no_asset_residual_value(self):
        config = self._config()
        boiler = config["sources"][0]
        boiler.update(max_retire_capacity_kw=5, minimum_retire_capacity_kw=5, decommissioning_cost_yuan_per_kw=100)
        backup = copy.deepcopy(boiler)
        backup.update(name="backup", max_retire_capacity_kw=0, minimum_retire_capacity_kw=0)
        config["sources"].append(backup)
        artifacts = self._solve(config, {
            "horizon_end_year": 2030, "stages": [{"year": 2026}],
        })
        retirement = artifacts.build_schedule.query("category == 'source_retirement'").iloc[0]
        self.assertEqual(retirement["value"], 5)
        self.assertEqual(retirement["gross_capex_yuan"], 500)
        self.assertEqual(retirement["salvage_yuan"], 0)

    def test_no_candidate_assets_still_produces_finance_and_empty_schedule(self):
        config = self._config()
        config["sources"][0]["max_new_capacity_kw"] = 0
        artifacts = self._solve(config, {
            "horizon_end_year": 2027, "finance": {"tariff_yuan_per_kwh": 2},
            "stages": [{"year": 2026}],
        })
        self.assertTrue(artifacts.build_schedule.empty)
        self.assertEqual(artifacts.finance.metrics["operating_years"], 1)

    def test_terminal_year_must_follow_last_stage(self):
        with self.assertRaisesRegex(ValueError, "horizon_end_year"):
            self._solve(self._config(), {"horizon_end_year": 2026, "stages": [{"year": 2026}]})

    def test_joint_master_audit_checks_actual_discounted_objective(self):
        artifacts = self._solve(self._config(), {
            "horizon_end_year": 2030,
            "finance": {"discount_rate": 0, "tariff_yuan_per_kwh": 2},
            "stages": [{"year": 2026}, {"year": 2028}],
        })
        summary = artifacts.summary
        self.assertTrue(summary["numerical_audit_passed"])
        self.assertTrue(summary["numerical_objective_checked"])
        self.assertEqual(summary["numerical_audit_scope"], "joint_master")
        self.assertAlmostEqual(summary["objective_value"], 80)
        self.assertLessEqual(summary["numerical_objective_recompute_error"], 1e-8)
        self.assertTrue((artifacts.stage_summary.objective_yuan_per_year == 20).all())
        self.assertFalse(artifacts.stage_summary.numerical_objective_checked.any())
        self.assertTrue(artifacts.stage_summary.numerical_objective_check_scope.str.contains(
            "not_solved_independently"
        ).all())


class TerminalFinanceRegressionTest(unittest.TestCase):
    @staticmethod
    def _profile() -> pd.DataFrame:
        return pd.DataFrame({
            "year": [2026, 2027, 2028], "is_terminal": [False, False, True],
            "demand_kwh": [100, 100, 0], "operating_cost_yuan": [10, 10, 0],
            "gross_capex_yuan": [100, 0, 0], "residual_value_yuan": [0, 0, 60],
        })

    def test_terminal_has_no_revenue_om_or_operating_tax_and_clears_debt(self):
        artifacts = evaluate_project_finance(self._profile(), {
            "tariff_yuan_per_kwh": 1, "discount_rate": 0,
            "fixed_om_fraction_of_cumulative_capex": 0.1,
            "debt_fraction": 0.8, "debt_interest_rate": 0.1, "debt_term_years": 10,
        })
        frame = artifacts.cashflow
        terminal = frame.iloc[-1]
        for column in ("revenue_yuan", "operating_cost_yuan", "fixed_om_yuan", "income_tax_yuan"):
            self.assertEqual(terminal[column], 0)
        self.assertAlmostEqual(frame.principal_repayment_yuan.sum(), 80)
        payment = 80 * 0.1 / (1 - 1.1 ** -10)
        balance_after_first_payment = 80 - (payment - 8)
        self.assertAlmostEqual(terminal["debt_service_yuan"], balance_after_first_payment * 1.1)
        self.assertGreater(terminal["terminal_debt_repayment_yuan"], 0)
        self.assertAlmostEqual(terminal["debt_balance_yuan"], 0)
        self.assertTrue(pd.isna(terminal["debt_service_coverage_ratio"]))
        self.assertAlmostEqual(terminal["project_cashflow_yuan"], 60)
        self.assertAlmostEqual(artifacts.metrics["project_npv_yuan"], 120)
        self.assertEqual(artifacts.metrics["operating_years"], 2)

    def test_fixed_om_adjustment_is_not_added_twice_and_proxy_is_explicit(self):
        profile = self._profile()
        # Ten yuan of ordinary costs plus a -2 retirement-saving adjustment.
        profile["operating_cost_yuan"] = [8, 8, 0]
        profile["modeled_fixed_om_cost_yuan"] = [-2, -2, 0]
        specification = {"fixed_om_fraction_of_cumulative_capex": 0.1}
        frame = evaluate_project_finance(profile, specification).cashflow
        self.assertEqual(frame.fixed_om_yuan.tolist(), [10, 10, 0])
        self.assertEqual(frame.total_fixed_om_cost_yuan.tolist(), [8, 8, 0])
        self.assertEqual(frame.total_cash_operating_cost_yuan.tolist(), [18, 18, 0])
        specification["fixed_om_method"] = "modeled_only"
        frame = evaluate_project_finance(profile, specification).cashflow
        self.assertEqual(frame.total_cash_operating_cost_yuan.tolist(), [8, 8, 0])
        # A positive modeled fee is also already included and must not be
        # charged again simply because its explanatory detail is present.
        profile["operating_cost_yuan"] = [13, 13, 0]
        profile["modeled_fixed_om_cost_yuan"] = [3, 3, 0]
        frame = evaluate_project_finance(profile, specification).cashflow
        self.assertEqual(frame.total_cash_operating_cost_yuan.tolist(), [13, 13, 0])

    def test_terminal_rejects_accidental_additional_operating_year(self):
        profile = self._profile()
        profile.loc[2, "demand_kwh"] = 100
        with self.assertRaisesRegex(ValueError, "must not contain demand"):
            evaluate_project_finance(profile, {})

    def test_legacy_profiles_without_terminal_keep_all_operating_rows(self):
        profile = self._profile().drop(columns="is_terminal")
        profile.loc[2, "demand_kwh"] = 100
        artifacts = evaluate_project_finance(profile, {"tariff_yuan_per_kwh": 1})
        self.assertEqual(artifacts.cashflow.revenue_yuan.tolist(), [100, 100, 100])
        self.assertEqual(artifacts.metrics["operating_years"], 3)


if __name__ == "__main__":
    unittest.main()
