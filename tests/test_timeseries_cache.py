from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

from heatplan.io import load_inputs
from heatplan.model import HeatPlanningModel


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_INPUTS = ROOT / "data/public_scenario_inputs"
PUBLIC_CASES = (
    (
        "scenario1_urumqi_deterministic_public.yaml",
        "scenario1_urumqi_representative_days_2023.csv",
    ),
    (
        "scenario2_guyuan_evidence_public.yaml",
        "scenario2_guyuan_representative_days_2023.csv",
    ),
    (
        "scenario3_wuhan_joint_public.yaml",
        "scenario3_wuhan_representative_days_2023.csv",
    ),
)


class TimeseriesCacheTest(unittest.TestCase):
    @staticmethod
    def _load_two_rows(config_name: str, timeseries_name: str):
        config, frame = load_inputs(
            PUBLIC_INPUTS / config_name,
            PUBLIC_INPUTS / timeseries_name,
        )
        return config, frame.iloc[:2].copy()

    def test_public_case_precomputed_time_weights_match_dataframe_formulas(self):
        for config_name, timeseries_name in PUBLIC_CASES:
            with self.subTest(config=config_name):
                config, frame = self._load_two_rows(
                    config_name, timeseries_name
                )
                model = HeatPlanningModel(config, frame)

                if "scenario" in frame.columns:
                    expected_scenarios = frame["scenario"].astype(str).tolist()
                else:
                    expected_scenarios = ["base"] * len(frame)
                if "scenario_probability" in frame.columns:
                    expected_probabilities = frame[
                        "scenario_probability"
                    ].astype(float).tolist()
                else:
                    probability = 1.0 / len(dict.fromkeys(expected_scenarios))
                    expected_probabilities = [probability] * len(frame)
                if "period_weight" in frame.columns:
                    expected_period_weights = frame["period_weight"].astype(
                        float
                    ).tolist()
                else:
                    day_weight = float(
                        config["planning"].get(
                            "representative_day_weight", 1.0
                        )
                    )
                    expected_period_weights = [day_weight] * len(frame)
                dt = float(config["planning"].get("time_step_hours", 1.0))

                for t in model.times:
                    scenario = expected_scenarios[t]
                    period_weight = expected_period_weights[t]
                    probability = expected_probabilities[t]
                    self.assertEqual(model._scenario(t), scenario)
                    self.assertEqual(model._period_weight(t), period_weight)
                    np.testing.assert_allclose(
                        model._physical_weight(t),
                        dt * period_weight,
                        rtol=0.0,
                        atol=1e-15,
                    )
                    np.testing.assert_allclose(
                        model._op_weight(t),
                        dt * period_weight * probability,
                        rtol=0.0,
                        atol=1e-15,
                    )
                    np.testing.assert_allclose(
                        model._event_weight(t),
                        period_weight * probability,
                        rtol=0.0,
                        atol=1e-15,
                    )

    def test_timeseries_snapshot_covers_columns_is_read_only_and_isolated(self):
        config, frame = self._load_two_rows(*PUBLIC_CASES[0])
        model = HeatPlanningModel(config, frame)

        self.assertEqual(
            set(model._timeseries_values),
            {str(column) for column in model.ts.columns},
        )
        for column in model.ts.columns:
            cached = model._timeseries_values[str(column)]
            self.assertFalse(cached.flags.writeable, msg=str(column))
            pd.testing.assert_series_equal(
                pd.Series(cached),
                model.ts[column].reset_index(drop=True),
                check_names=False,
            )

        cached_time = model._ts_value(0, "time")
        cached_price = float(model._ts_value(0, "electricity_price"))
        with self.assertRaises(ValueError):
            model._timeseries_values["electricity_price"][0] = -1.0

        # Neither the caller-owned frame nor the public reporting frame is the
        # immutable model-input snapshot used by the hot-path scalar accessor.
        frame.loc[frame.index[0], "time"] = "caller_mutation"
        frame.loc[frame.index[0], "electricity_price"] = 999.0
        model.ts.loc[0, "time"] = "reporting_frame_mutation"
        model.ts.loc[0, "electricity_price"] = 888.0
        self.assertEqual(model._ts_value(0, "time"), cached_time)
        self.assertEqual(
            float(model._ts_value(0, "electricity_price")), cached_price
        )

    def test_wuhan_dynamic_columns_and_custom_tariff_use_frozen_values(self):
        config, frame = self._load_two_rows(*PUBLIC_CASES[2])
        custom_price_column = "test_custom_electricity_price"
        frame[custom_price_column] = (
            frame["electricity_price"].astype(float) + 0.12345
        )
        config["planning"]["electricity_price_column"] = custom_price_column

        node_demand_columns = {
            str(column)
            for node in config["nodes"]
            for column in node.get("demand_columns", {}).values()
        }
        occupancy_columns = {
            str(column)
            for node in config["nodes"]
            for column in (
                node.get("intermittent_heating", {}).get("schedule_column"),
                *(
                    inertia.get("occupancy_column")
                    for inertia in node.get("thermal_inertia", {}).values()
                ),
            )
            if column
        }
        behavior_columns = {
            str(column)
            for node in config["nodes"]
            for column in (
                node.get("user_behavior", {}).get("multiplier_column"),
                node.get("user_behavior", {}).get(
                    "scenario_multiplier_weight_column"
                ),
            )
            if column
        }
        source_temperature_columns = {
            str(source["source_temp_column"])
            for source in config["sources"]
            if source.get("source_temp_column")
        }
        source_availability_columns = {
            str(source["availability_column"])
            for source in config["sources"]
            if source.get("availability_column")
        }
        cooling_columns = {
            str(column)
            for column in config["multi_energy_services"]["cooling"]
            .get("demand_columns", {})
            .values()
        }

        for label, columns in (
            ("demand", node_demand_columns),
            ("occupancy", occupancy_columns),
            ("behavior", behavior_columns),
            ("source temperature", source_temperature_columns),
            ("source availability", source_availability_columns),
            ("cooling", cooling_columns),
        ):
            self.assertTrue(columns, msg=f"Wuhan case lacks {label} columns")

        model = HeatPlanningModel(config, frame)
        dynamic_columns = set().union(
            node_demand_columns,
            occupancy_columns,
            behavior_columns,
            source_temperature_columns,
            source_availability_columns,
            cooling_columns,
            {custom_price_column},
        )
        self.assertTrue(dynamic_columns.issubset(model._timeseries_values))

        for column in sorted(dynamic_columns):
            for t in model.times:
                actual = model._ts_value(t, column)
                expected = model.ts.iloc[t][column]
                if isinstance(expected, (int, float, np.number)):
                    np.testing.assert_allclose(
                        float(actual), float(expected), rtol=0.0, atol=0.0
                    )
                else:
                    self.assertEqual(actual, expected)
        for t in model.times:
            self.assertEqual(
                model._electricity_price(t),
                float(model.ts.iloc[t][custom_price_column]),
            )

    def test_add_policy_constraints_only_appends_configured_rows(self):
        config, frame = self._load_two_rows(*PUBLIC_CASES[0])
        config["policy"] = {}
        model = HeatPlanningModel(config, frame)
        model.build()

        original_rows = deepcopy(model.problem.rows)
        original_lower = list(model.problem.row_lower)
        original_upper = list(model.problem.row_upper)
        original_objective = list(model.problem.objective)
        model.policy = {
            "minimum_clean_heat_share": 0.50,
            "annualized_investment_budget_yuan": 123456789.0,
            "maximum_levelized_heat_cost_yuan_per_kwh": 2.0,
            "reference_note_without_constraint": "ignored metadata",
        }

        model._add_policy_constraints()

        self.assertEqual(len(model.problem.rows), len(original_rows) + 3)
        self.assertEqual(model.problem.rows[: len(original_rows)], original_rows)
        self.assertEqual(
            model.problem.row_lower[: len(original_lower)], original_lower
        )
        self.assertEqual(
            model.problem.row_upper[: len(original_upper)], original_upper
        )
        self.assertEqual(model.problem.objective, original_objective)
        np.testing.assert_allclose(
            model.problem.row_lower[-3:],
            [0.0, -np.inf, -np.inf],
            rtol=0.0,
            atol=0.0,
        )
        expected_lcoh_bound = (
            2.0 * model.metric_constants.get("demand_kwh", 0.0)
        )
        np.testing.assert_allclose(
            model.problem.row_upper[-3:],
            [np.inf, 123456789.0, expected_lcoh_bound],
            rtol=0.0,
            atol=1e-9,
        )

    def test_scalar_clip_matches_numpy_for_finite_and_nan_values(self):
        for value in (-np.inf, -1.0, 0.25, 2.0, np.inf, np.nan):
            with self.subTest(value=value):
                expected = float(np.clip(value, 0.0, 1.0))
                actual = HeatPlanningModel._clip_scalar(value, 0.0, 1.0)
                if np.isnan(expected):
                    self.assertTrue(np.isnan(actual))
                else:
                    self.assertEqual(actual, expected)
        self.assertEqual(
            HeatPlanningModel._clip_scalar(0.25, 2.0, 1.0),
            float(np.clip(0.25, 2.0, 1.0)),
        )


if __name__ == "__main__":
    unittest.main()
