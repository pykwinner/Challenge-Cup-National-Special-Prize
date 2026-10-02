from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from heatplan.scenarios import (
    calibrate_representative_day_weights,
    extreme_aware_typical_days,
)


class RepresentativeDayQualityTest(unittest.TestCase):
    def test_series_level_peak_and_ramp_days_are_retained_without_double_counting(self):
        days = 12
        timestamps = pd.date_range("2026-01-01", periods=days * 24, freq="h")
        frame = pd.DataFrame(
            {
                "time": timestamps.astype(str),
                "outdoor_temp": 5.0,
                "demand_a": 2.0,
                "demand_b": 3.0,
                "waste_heat_availability": 1.0,
                "worst_cop_score": 0.3,
            }
        )
        frame.loc[2 * 24 : 3 * 24 - 1, "outdoor_temp"] = -20.0
        frame.loc[7 * 24 + 12 : 8 * 24 - 1, "demand_a"] = 30.0
        frame.loc[8 * 24 + 8 : 9 * 24 - 1, "demand_b"] = 40.0
        frame.loc[9 * 24 : 10 * 24 - 1, "waste_heat_availability"] = 0.2
        frame.loc[10 * 24 + 5, "worst_cop_score"] = 0.8

        representative = extreme_aware_typical_days(
            frame,
            feature_columns=[
                "outdoor_temp",
                "demand_a",
                "demand_b",
                "waste_heat_availability",
            ],
            demand_columns=["demand_a", "demand_b"],
            n_clusters=2,
            random_state=7,
            anchor_score_columns=["worst_cop_score"],
        )
        selected = set(
            representative.groupby("representative_day")[
                "original_day_index"
            ].first()
        )
        self.assertTrue({2, 7, 8, 9, 10}.issubset(selected))
        weights = representative.groupby("representative_day")[
            "period_weight"
        ].first()
        self.assertAlmostEqual(float(weights.sum()), float(days), places=9)
        original_days = representative.groupby("representative_day")[
            "original_day_index"
        ].first()
        self.assertEqual(original_days.nunique(), len(original_days))
        day_seven = representative[
            representative["original_day_index"] == 7
        ].iloc[0]
        day_eight = representative[
            representative["original_day_index"] == 8
        ].iloc[0]
        self.assertTrue(bool(day_seven["is_extreme_day"]))
        self.assertTrue(bool(day_eight["is_extreme_day"]))
        self.assertIn("maximum_ramp::demand_a", day_seven["extreme_reasons"])
        self.assertIn("maximum_ramp::demand_b", day_eight["extreme_reasons"])
        day_ten = representative[
            representative["original_day_index"] == 10
        ].iloc[0]
        self.assertIn(
            "maximum_score::worst_cop_score", day_ten["extreme_reasons"]
        )

    def test_constrained_calibration_preserves_days_and_extreme_lower_bound(self):
        profiles = [10.0, 5.0, 0.0]
        blocks = []
        for day, value in enumerate(profiles):
            blocks.append(
                pd.DataFrame(
                    {
                        "representative_day": f"day_{day}",
                        "period_weight": 1.0,
                        "is_extreme_day": day == 0,
                        "scenario_probability": 1.0,
                        "heat_demand": value,
                    },
                    index=range(24),
                )
            )
        representative = pd.concat(blocks, ignore_index=True)
        chronology = pd.DataFrame(
            {
                "heat_demand": np.concatenate(
                    [np.full(24, 10.0), np.full(24, 2.5), np.zeros(24)]
                )
            }
        )

        result = calibrate_representative_day_weights(
            representative,
            chronology,
            ["heat_demand"],
            regularization=1e-8,
        )
        self.assertTrue((representative["period_weight"] == 1.0).all())
        self.assertAlmostEqual(
            float(result.weights["calibrated_weight"].sum()), 3.0, places=8
        )
        extreme = result.weights[result.weights["is_extreme_day"]]
        self.assertGreaterEqual(float(extreme["calibrated_weight"].min()), 1.0)
        diagnostic = result.diagnostics.iloc[0]
        self.assertLess(
            abs(float(diagnostic["calibrated_relative_bias"])),
            abs(float(diagnostic["original_relative_bias"])),
        )
        self.assertLess(abs(float(diagnostic["calibrated_relative_bias"])), 1e-5)
        self.assertFalse(result.summary["source_files_modified"])
        self.assertAlmostEqual(
            result.summary["original_weight_sum_days"],
            result.summary["calibrated_weight_sum_days"],
            places=8,
        )


if __name__ == "__main__":
    unittest.main()
