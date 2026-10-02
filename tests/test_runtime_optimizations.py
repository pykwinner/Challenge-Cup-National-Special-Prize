from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from heatplan.linear import LinearMILP
from heatplan.model import HeatPlanningModel


class RuntimeOptimizationTest(unittest.TestCase):
    def test_sparse_matrix_compilation_is_cached_and_invalidated_safely(self):
        problem = LinearMILP()
        first = problem.add_var("first")
        second = problem.add_var("second")
        problem.add_constraint({second: 2.0, first: -1.0}, lb=3.0)

        compiled = problem.constraint_matrix()
        np.testing.assert_allclose(
            compiled.toarray(),
            np.asarray([[-1.0, 2.0]]),
        )
        self.assertFalse(problem.last_matrix_cache_hit)
        self.assertIs(problem.constraint_matrix(), compiled)
        self.assertTrue(problem.last_matrix_cache_hit)

        problem.add_constraint({first: 4.0}, ub=5.0)
        rebuilt = problem.constraint_matrix()
        self.assertIsNot(rebuilt, compiled)
        self.assertFalse(problem.last_matrix_cache_hit)
        np.testing.assert_allclose(
            rebuilt.toarray(),
            np.asarray([[-1.0, 2.0], [4.0, 0.0]]),
        )

    @staticmethod
    def _make_model() -> tuple[dict, HeatPlanningModel]:
        config = {
            "planning": {
                "discount_rate": 0.05,
                "asset_lifetime_years": 20,
                "time_step_hours": 2.0,
            },
            "grades": [],
            "nodes": [
                {
                    "name": "node_a",
                    "demand_columns": {
                        "low": "load_a",
                        "high": "load_a_high",
                    },
                    "intermittent_heating": {
                        "schedule_column": "occupancy_a",
                        "setback_fraction": 0.20,
                    },
                    "user_behavior": {
                        "multiplier_column": "behavior_a",
                        "scenario_multipliers": {"stress": 1.50},
                    },
                },
                {
                    "name": "node_b",
                    "demand_columns": {"low": "load_b"},
                    "intermittent_heating": {
                        "schedule_column": "occupancy_b",
                        "setback_fraction": 0.40,
                    },
                },
            ],
            "sources": [],
            "pipes": [],
            "storages": [],
        }
        frame = pd.DataFrame(
            {
                "scenario": ["normal", "stress"],
                "scenario_probability": [0.75, 0.25],
                "period_weight": [3.0, 5.0],
                "load_a": [10.0, 20.0],
                "load_a_high": [4.0, 8.0],
                "load_b": [30.0, 40.0],
                "occupancy_a": [0.25, 0.50],
                "occupancy_b": [0.80, 0.90],
                "behavior_a": [1.10, 0.90],
            }
        )
        return config, HeatPlanningModel(config, frame)

    def test_time_accounting_preserves_expected_physical_and_event_weights(self):
        _, model = self._make_model()
        index = model.problem.add_var("accounting_probe")

        # Operation rates include dt in both expected and scenario-physical
        # accounting; event rates (for starts/stops) intentionally do not.
        model._record_time_component("operating_cost", index, 2.0, 0)
        model._record_time_component(
            "operating_cost", index, 3.0, 0, event=True
        )

        expected_component = 2.0 * (2.0 * 3.0 * 0.75) + 3.0 * (
            3.0 * 0.75
        )
        physical_component = 2.0 * (2.0 * 3.0) + 3.0 * 3.0
        self.assertAlmostEqual(
            model.component_coefficients["operating_cost"][index],
            expected_component,
        )
        self.assertAlmostEqual(
            model.problem.objective[index], expected_component
        )
        self.assertAlmostEqual(
            model.scenario_cost_coefficients["normal"][index],
            physical_component,
        )
        self.assertNotIn("stress", model.scenario_cost_coefficients)

        # Repeated writes to one metric/index must accumulate in both views.
        model._record_time_metric("electricity_kwh", index, 4.0, 1)
        model._record_time_metric("electricity_kwh", index, 5.0, 1)
        expected_metric = (4.0 + 5.0) * (2.0 * 5.0 * 0.25)
        physical_metric = (4.0 + 5.0) * (2.0 * 5.0)
        self.assertAlmostEqual(
            model.metric_coefficients["electricity_kwh"][index],
            expected_metric,
        )
        self.assertAlmostEqual(
            model.scenario_metric_coefficients["stress"][
                "electricity_kwh"
            ][index],
            physical_metric,
        )
        # Metrics are reporting coefficients and do not alter the objective.
        self.assertAlmostEqual(
            model.problem.objective[index], expected_component
        )

    def test_demand_cache_hits_same_key_and_isolates_node_column_and_time(self):
        _, model = self._make_model()
        node_a = model.nodes["node_a"]
        node_b = model.nodes["node_b"]
        calls: list[tuple[str, str, int]] = []

        def fake_uncached(node, column, t):
            calls.append((node["name"], column, t))
            return float(len(calls))

        with patch.object(
            model,
            "_compute_demand_uncached",
            side_effect=fake_uncached,
        ) as uncached:
            first = model._demand_value(node_a, "load_a", 0)
            self.assertEqual(model._demand_value(node_a, "load_a", 0), first)
            different_node = model._demand_value(node_b, "load_b", 0)
            different_column = model._demand_value(
                node_a, "load_a_high", 0
            )
            different_time = model._demand_value(node_a, "load_a", 1)

        self.assertEqual(first, 1.0)
        self.assertEqual(different_node, 2.0)
        self.assertEqual(different_column, 3.0)
        self.assertEqual(different_time, 4.0)
        self.assertEqual(uncached.call_count, 4)
        self.assertEqual(
            calls,
            [
                ("node_a", "load_a", 0),
                ("node_b", "load_b", 0),
                ("node_a", "load_a_high", 0),
                ("node_a", "load_a", 1),
            ],
        )

    def test_caller_config_mutation_does_not_change_lazy_model_inputs(self):
        config, model = self._make_model()
        caller_node = config["nodes"][0]
        model_node = model.nodes["node_a"]
        expected = model._compute_demand_uncached(model_node, "load_a", 1)

        self.assertIsNot(model.config, config)
        self.assertIsNot(model_node, caller_node)
        caller_node["intermittent_heating"]["setback_fraction"] = 1.0
        caller_node["user_behavior"]["multiplier_column"] = "load_b"
        config["planning"]["time_step_hours"] = 99.0

        self.assertEqual(
            model.nodes["node_a"]["intermittent_heating"][
                "setback_fraction"
            ],
            0.20,
        )
        self.assertEqual(
            model.nodes["node_a"]["user_behavior"]["multiplier_column"],
            "behavior_a",
        )
        self.assertEqual(model.config["planning"]["time_step_hours"], 2.0)
        self.assertEqual(model.dt, 2.0)
        self.assertAlmostEqual(
            model._demand_value(model.nodes["node_a"], "load_a", 1),
            expected,
        )


if __name__ == "__main__":
    unittest.main()
