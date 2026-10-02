import unittest
from unittest.mock import patch

import pandas as pd

from heatplan.finance import break_even_tariff, evaluate_project_finance


class FinanceOptimizationTest(unittest.TestCase):
    @staticmethod
    def _profile() -> pd.DataFrame:
        return pd.DataFrame(
            {
                "year": [2026, 2027, 2028],
                "demand_kwh": [100.0, 100.0, 100.0],
                "operating_cost_yuan": [0.0, 0.0, 0.0],
                "gross_capex_yuan": [100.0, 0.0, 0.0],
            }
        )

    def test_break_even_tariff_converges_without_diagnostics(self):
        finance = {
            "discount_rate": 0.0,
            "collection_rate": 1.0,
            "tariff_search_upper_yuan_per_kwh": 1.0,
        }
        with (
            patch(
                "heatplan.finance.evaluate_project_finance",
                wraps=evaluate_project_finance,
            ) as evaluate_mock,
            patch("heatplan.finance._irr", side_effect=AssertionError("IRR should be skipped")),
            patch("heatplan.finance._payback", side_effect=AssertionError("payback should be skipped")),
        ):
            tariff = break_even_tariff(self._profile(), finance)
        self.assertGreaterEqual(tariff, 1.0 / 3.0)
        self.assertLessEqual(tariff - 1.0 / 3.0, 1e-6)
        self.assertLess(evaluate_mock.call_count, 30)
        self.assertTrue(all(call.kwargs["include_diagnostics"] is False for call in evaluate_mock.call_args_list))

    def test_zero_tariff_is_returned_when_target_is_already_met(self):
        profile = self._profile()
        profile["gross_capex_yuan"] = 0.0
        self.assertEqual(break_even_tariff(profile, {"discount_rate": 0.0}), 0.0)

    def test_unreachable_target_raises_instead_of_returning_upper_bound(self):
        profile = self._profile()
        profile["gross_capex_yuan"] = [1_000.0, 0.0, 0.0]
        finance = {
            "discount_rate": 0.0,
            "tariff_search_upper_yuan_per_kwh": 1.0,
        }
        with self.assertRaisesRegex(ValueError, "not reachable"):
            break_even_tariff(profile, finance)

    def test_invalid_upper_bound_is_rejected(self):
        for upper in (0.0, -1.0, float("nan"), float("inf"), "not-a-number"):
            with self.subTest(upper=upper), self.assertRaisesRegex(ValueError, "upper"):
                break_even_tariff(
                    self._profile(),
                    {"tariff_search_upper_yuan_per_kwh": upper},
                )

    def test_invalid_tolerance_is_rejected(self):
        for tolerance in (0.0, -1e-6, float("nan"), float("inf"), "not-a-number"):
            with self.subTest(tolerance=tolerance), self.assertRaisesRegex(ValueError, "tolerance"):
                break_even_tariff(self._profile(), {}, tolerance=tolerance)

    def test_default_finance_evaluation_keeps_full_diagnostics(self):
        profile = self._profile()
        profile.loc[0, "demand_kwh"] = 0.0
        artifacts = evaluate_project_finance(
            profile,
            {
                "tariff_yuan_per_kwh": 1.0,
                "discount_rate": 0.0,
            },
        )
        for metric in (
            "project_irr",
            "equity_irr",
            "project_payback_years",
            "equity_payback_years",
        ):
            with self.subTest(metric=metric):
                self.assertIsNotNone(artifacts.metrics[metric])


if __name__ == "__main__":
    unittest.main()
