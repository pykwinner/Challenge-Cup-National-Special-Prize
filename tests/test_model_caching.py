from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from unittest.mock import patch
import unittest

import numpy as np

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


class LegacyReferenceModel(HeatPlanningModel):
    """Pre-cache lookup behaviour retained as an independent test oracle."""

    def _cop(self, source, grade, t):
        return self._compute_cop_uncached(source, grade, t)

    def _electric_load_row(self, t: int, margin: float = 1.0) -> dict[int, float]:
        row: dict[int, float] = {}

        for (source_name, grade_name, time), index in self.variables[
            "source_heat"
        ].items():
            if time != t:
                continue
            source = self.sources[source_name]
            source_type = source.get("type")
            if source_type == "heat_pump":
                row[index] = margin / self._cop(
                    source, self.grades[grade_name], t
                )
            elif source_type == "electric_boiler":
                row[index] = margin / float(source.get("efficiency", 0.98))

        for (source_name, time), index in self.variables.get(
            "cooling_output", {}
        ).items():
            if time == t:
                row[index] = margin / self._cooling_eer(source_name, t)

        for pipe_name, pipe in self.pipes.items():
            pumping_ratio = float(
                pipe.get("pumping_electricity_kwh_per_kwh_heat", 0.0)
            )
            for category in ("pipe_flow_forward", "pipe_flow_reverse"):
                for (asset, _grade_name, time), index in self.variables.get(
                    category, {}
                ).items():
                    if asset == pipe_name and time == t:
                        row[index] = margin * pumping_ratio

            retrofit = pipe.get("retrofit")
            if not retrofit:
                continue
            improved_ratio = float(
                retrofit.get(
                    "pumping_electricity_kwh_per_kwh_heat", pumping_ratio
                )
            )
            reduction = max(0.0, pumping_ratio - improved_ratio)
            for category in (
                "pipe_retrofit_flow_forward",
                "pipe_retrofit_flow_reverse",
            ):
                for (asset, _grade_name, time), index in self.variables.get(
                    category, {}
                ).items():
                    if asset == pipe_name and time == t:
                        row[index] = -margin * reduction

        return row


class ModelCachingTest(unittest.TestCase):
    @staticmethod
    def _load_two_rows(config_name: str, timeseries_name: str):
        config, frame = load_inputs(
            PUBLIC_INPUTS / config_name,
            PUBLIC_INPUTS / timeseries_name,
        )
        return config, frame.iloc[:2].copy()

    def _assert_numeric_tree_close(
        self,
        actual,
        expected,
        *,
        path: str,
    ) -> None:
        if isinstance(actual, Mapping) and isinstance(expected, Mapping):
            self.assertEqual(
                set(actual),
                set(expected),
                msg=f"key mismatch at {path}",
            )
            for key in actual:
                self._assert_numeric_tree_close(
                    actual[key],
                    expected[key],
                    path=f"{path}[{key!r}]",
                )
            return
        np.testing.assert_allclose(
            float(actual),
            float(expected),
            rtol=1e-12,
            atol=1e-9,
            err_msg=f"numeric mismatch at {path}",
        )

    def _assert_build_structures_equal(
        self,
        cached: HeatPlanningModel,
        legacy: LegacyReferenceModel,
    ) -> None:
        self.assertEqual(set(cached.variables), set(legacy.variables))
        for category in cached.variables:
            self.assertEqual(
                dict(cached.variables[category]),
                dict(legacy.variables[category]),
                msg=f"variable-index mismatch in {category}",
            )

        cached_problem = cached.problem
        legacy_problem = legacy.problem
        self.assertEqual(cached_problem.names, legacy_problem.names)
        self.assertEqual(cached_problem.integrality, legacy_problem.integrality)
        np.testing.assert_allclose(
            cached_problem.lower,
            legacy_problem.lower,
            rtol=1e-12,
            atol=1e-9,
        )
        np.testing.assert_allclose(
            cached_problem.upper,
            legacy_problem.upper,
            rtol=1e-12,
            atol=1e-9,
        )
        np.testing.assert_allclose(
            cached_problem.objective,
            legacy_problem.objective,
            rtol=1e-12,
            atol=1e-9,
        )

        self.assertEqual(len(cached_problem.rows), len(legacy_problem.rows))
        for row_number, (actual, expected) in enumerate(
            zip(cached_problem.rows, legacy_problem.rows, strict=True)
        ):
            self._assert_numeric_tree_close(
                actual,
                expected,
                path=f"constraint_rows[{row_number}]",
            )
        np.testing.assert_allclose(
            cached_problem.row_lower,
            legacy_problem.row_lower,
            rtol=1e-12,
            atol=1e-9,
        )
        np.testing.assert_allclose(
            cached_problem.row_upper,
            legacy_problem.row_upper,
            rtol=1e-12,
            atol=1e-9,
        )

        for attribute in (
            "metric_coefficients",
            "metric_constants",
            "component_coefficients",
            "scenario_cost_coefficients",
            "scenario_metric_coefficients",
            "scenario_metric_constants",
        ):
            self._assert_numeric_tree_close(
                getattr(cached, attribute),
                getattr(legacy, attribute),
                path=attribute,
            )

    def test_wuhan_joint_registry_matches_legacy_scan_and_is_copy_safe(self):
        config, frame = self._load_two_rows(*PUBLIC_CASES[2])
        cached = HeatPlanningModel(config, frame)
        legacy = LegacyReferenceModel(config, frame)
        cached.build()
        legacy.build()

        for t in cached.times:
            actual = cached._electric_load_row(t)
            expected = legacy._electric_load_row(t)
            self._assert_numeric_tree_close(
                actual, expected, path=f"wuhan_electric_load[{t}]"
            )

        row = cached._electric_load_row(0)
        low = cached.grades["low"]

        heat_pump = cached.sources["district_a_air_hp"]
        heat_pump_index = cached.variables["source_heat"][
            (heat_pump["name"], "low", 0)
        ]
        self.assertAlmostEqual(
            row[heat_pump_index],
            1.0 / cached._compute_cop_uncached(heat_pump, low, 0),
            places=12,
        )

        boiler = cached.sources["district_a_emergency_eb"]
        boiler_index = cached.variables["source_heat"][(boiler["name"], "low", 0)]
        self.assertAlmostEqual(
            row[boiler_index],
            1.0 / float(boiler["efficiency"]),
            places=12,
        )

        cooling_index = cached.variables["cooling_output"][
            ("district_a_ground_hp", 0)
        ]
        self.assertAlmostEqual(
            row[cooling_index],
            1.0 / cached._cooling_eer("district_a_ground_hp", 0),
            places=12,
        )

        pipe = cached.pipes["interconnector_ab"]
        pumping_ratio = float(pipe["pumping_electricity_kwh_per_kwh_heat"])
        forward = cached.variables["pipe_flow_forward"][
            ("interconnector_ab", "low", 0)
        ]
        reverse = cached.variables["pipe_flow_reverse"][
            ("interconnector_ab", "low", 0)
        ]
        self.assertAlmostEqual(row[forward], pumping_ratio, places=12)
        self.assertAlmostEqual(row[reverse], pumping_ratio, places=12)

        margin = 1.137
        margin_row = cached._electric_load_row(0, margin=margin)
        self.assertEqual(set(row), set(margin_row))
        for index, coefficient in row.items():
            self.assertAlmostEqual(
                margin_row[index], margin * coefficient, places=12
            )

        row[heat_pump_index] = -999.0
        row[-1] = 123.0
        fresh_row = cached._electric_load_row(0)
        self.assertNotEqual(fresh_row[heat_pump_index], -999.0)
        self.assertNotIn(-1, fresh_row)

    def test_urumqi_retrofit_pump_coefficients_are_signed_correctly(self):
        config, frame = self._load_two_rows(*PUBLIC_CASES[0])
        model = HeatPlanningModel(config, frame)
        model.build()

        for t in model.times:
            row = model._electric_load_row(t)
            for grade_name in model.grades:
                forward = model.variables["pipe_flow_forward"][
                    ("trunk_a", grade_name, t)
                ]
                retrofit = model.variables["pipe_retrofit_flow_forward"][
                    ("trunk_a", grade_name, t)
                ]
                self.assertAlmostEqual(row[forward], 0.008, places=12)
                self.assertAlmostEqual(row[retrofit], -0.003, places=12)

    def test_cop_cache_keys_isolate_source_grade_and_time(self):
        config, frame = self._load_two_rows(*PUBLIC_CASES[2])
        model = HeatPlanningModel(config, frame)
        source_a = model.sources["district_a_air_hp"]
        source_b = model.sources["district_a_ground_hp"]
        low = model.grades["low"]
        high = model.grades["high"]
        calls: list[tuple[str, str, int]] = []

        def fake_uncached(source, grade, t):
            calls.append((source["name"], grade["name"], t))
            return float(len(calls))

        with patch.object(
            model,
            "_compute_cop_uncached",
            side_effect=fake_uncached,
        ) as uncached:
            first = model._cop(source_a, low, 0)
            self.assertEqual(model._cop(source_a, low, 0), first)
            different_source = model._cop(source_b, low, 0)
            different_grade = model._cop(source_a, high, 0)
            different_time = model._cop(source_a, low, 1)

        self.assertEqual(first, 1.0)
        self.assertEqual(different_source, 2.0)
        self.assertEqual(different_grade, 3.0)
        self.assertEqual(different_time, 4.0)
        self.assertEqual(uncached.call_count, 4)
        self.assertEqual(
            calls,
            [
                ("district_a_air_hp", "low", 0),
                ("district_a_ground_hp", "low", 0),
                ("district_a_air_hp", "high", 0),
                ("district_a_air_hp", "low", 1),
            ],
        )

    def test_public_two_row_builds_match_legacy_reference_structure(self):
        for config_name, timeseries_name in PUBLIC_CASES:
            with self.subTest(config=config_name):
                config, frame = self._load_two_rows(config_name, timeseries_name)
                cached = HeatPlanningModel(config, frame)
                legacy = LegacyReferenceModel(config, frame)
                cached.build()
                legacy.build()
                self._assert_build_structures_equal(cached, legacy)


if __name__ == "__main__":
    unittest.main()
