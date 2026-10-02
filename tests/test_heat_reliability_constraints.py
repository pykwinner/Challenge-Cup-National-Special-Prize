from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from heatplan.model import HeatPlanningModel


class HeatReliabilityConstraintTest(unittest.TestCase):
    @staticmethod
    def _make_model(
        demands: tuple[float, float],
    ) -> tuple[HeatPlanningModel, list[int], dict[str, float]]:
        planning = {
            "discount_rate": 0.05,
            "asset_lifetime_years": 20,
            "time_step_hours": 2.0,
            "max_unserved_rate": 0.10,
            "max_scenario_unserved_rate": 0.20,
        }
        config = {
            "planning": planning,
            "grades": [],
            "nodes": [],
            "sources": [],
            "pipes": [],
            "storages": [],
        }
        frame = pd.DataFrame(
            {
                "scenario": ["normal", "stress"],
                "scenario_probability": [0.75, 0.25],
                "period_weight": [3.0, 5.0],
            }
        )
        model = HeatPlanningModel(config, frame)
        grade = {"temperature_c": 60.0}
        unserved_indices: list[int] = []
        for t, demand in enumerate(demands):
            index = model.problem.add_var(
                f"unserved[test_node,high,{t}]",
                ub=max(demand, 0.0),
            )
            unserved_indices.append(index)
            model.variables["unserved"][("test_node", "high", t)] = index
            model._record_time_metric("unserved_kwh", index, 1.0, t)
            # build() records demand constants only for strictly positive load.
            if demand > 0.0:
                model._record_metric_constant_demand(
                    "test_node", grade, demand, t
                )
        return model, unserved_indices, planning

    def test_annual_cap_uses_expected_scenario_weights(self):
        model, (normal, stress), planning = self._make_model((100.0, 200.0))

        model._add_heat_reliability_constraints(planning)

        self.assertEqual(len(model.problem.rows), 3)
        self.assertEqual(
            model.problem.rows[0],
            {
                normal: 2.0 * 3.0 * 0.75,
                stress: 2.0 * 5.0 * 0.25,
            },
        )
        self.assertEqual(model.problem.row_lower[0], -np.inf)
        self.assertAlmostEqual(
            model.problem.row_upper[0],
            0.10
            * (
                100.0 * 2.0 * 3.0 * 0.75
                + 200.0 * 2.0 * 5.0 * 0.25
            ),
        )
        self.assertAlmostEqual(model.problem.row_upper[0], 95.0)

    def test_scenario_caps_use_physical_weights_without_probability(self):
        model, (normal, stress), planning = self._make_model((100.0, 200.0))

        model._add_heat_reliability_constraints(planning)

        self.assertEqual(model.scenarios, ["normal", "stress"])
        self.assertEqual(model.problem.rows[1], {normal: 2.0 * 3.0})
        self.assertEqual(model.problem.rows[2], {stress: 2.0 * 5.0})
        np.testing.assert_allclose(
            model.problem.row_lower[1:],
            [-np.inf, -np.inf],
            rtol=0.0,
            atol=0.0,
        )
        np.testing.assert_allclose(
            model.problem.row_upper[1:],
            [0.20 * 100.0 * 2.0 * 3.0, 0.20 * 200.0 * 2.0 * 5.0],
            rtol=0.0,
            atol=1e-12,
        )
        np.testing.assert_allclose(
            model.problem.row_upper[1:],
            [120.0, 400.0],
            rtol=0.0,
            atol=1e-12,
        )

    def test_zero_demand_adds_zero_caps_without_missing_metric_failure(self):
        model, (normal, stress), planning = self._make_model((0.0, 0.0))
        self.assertNotIn("demand_kwh", model.metric_constants)

        model._add_heat_reliability_constraints(planning)

        self.assertEqual(
            model.problem.rows,
            [
                {
                    normal: 2.0 * 3.0 * 0.75,
                    stress: 2.0 * 5.0 * 0.25,
                },
                {normal: 2.0 * 3.0},
                {stress: 2.0 * 5.0},
            ],
        )
        np.testing.assert_allclose(
            model.problem.row_lower,
            [-np.inf, -np.inf, -np.inf],
            rtol=0.0,
            atol=0.0,
        )
        np.testing.assert_allclose(
            model.problem.row_upper,
            [0.0, 0.0, 0.0],
            rtol=0.0,
            atol=0.0,
        )


if __name__ == "__main__":
    unittest.main()
