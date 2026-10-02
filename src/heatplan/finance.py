from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class FinanceArtifacts:
    cashflow: pd.DataFrame
    metrics: dict[str, float | None]


def _npv(rate: float, values: np.ndarray) -> float:
    return float(sum(value / (1.0 + rate) ** year for year, value in enumerate(values)))


def _irr(values: np.ndarray) -> float | None:
    if not ((values < 0).any() and (values > 0).any()):
        return None
    low, high = -0.95, 5.0
    f_low, f_high = _npv(low, values), _npv(high, values)
    if f_low * f_high > 0:
        return None
    for _ in range(160):
        mid = (low + high) / 2.0
        f_mid = _npv(mid, values)
        if abs(f_mid) < 1e-7:
            return mid
        if f_low * f_mid <= 0:
            high, f_high = mid, f_mid
        else:
            low, f_low = mid, f_mid
    return (low + high) / 2.0


def _payback(values: np.ndarray) -> float | None:
    cumulative = 0.0
    previous = 0.0
    for year, value in enumerate(values):
        previous = cumulative
        cumulative += value
        if cumulative >= 0:
            if year == 0 or value == 0:
                return float(year)
            return (year - 1) + max(0.0, -previous / value)
    return None


def evaluate_project_finance(
    annual_profile: pd.DataFrame,
    finance: dict[str, Any],
    *,
    include_diagnostics: bool = True,
) -> FinanceArtifacts:
    """Build project and equity cashflows from an annual operating profile.

    Required columns are ``year``, ``demand_kwh`` and ``operating_cost_yuan``.
    Optional columns are gross capex, subsidy and residual value. A final row
    with ``is_terminal=True`` settles residual value and all remaining debt;
    it has no demand, operating costs, new investment, revenue or fixed O&M.
    Without that marker every row retains its historical operating-year meaning.

    ``operating_cost_yuan`` includes all modeled cash components, including any
    modeled fixed-O&M adjustment. ``fixed_om_method`` defaults to
    ``proxy_plus_model_adjustment``: the explicit capex-based proxy is added
    once, preserving legacy finance inputs. ``modeled_only`` disables that proxy.
    A modeled fixed-O&M detail column is informational and is never added twice.
    """
    frame = annual_profile.copy().sort_values("year").reset_index(drop=True)
    if frame.empty:
        raise ValueError("annual_profile must contain at least one year")
    for column in ("gross_capex_yuan", "subsidy_yuan", "residual_value_yuan"):
        if column not in frame:
            frame[column] = 0.0
    if "is_terminal" not in frame:
        frame["is_terminal"] = False
    if not frame["is_terminal"].isin([True, False]).all():
        raise ValueError("is_terminal must contain boolean values")
    terminal = frame["is_terminal"].astype(bool)
    frame["is_terminal"] = terminal
    terminal_index = int(frame.index[terminal][0]) if terminal.any() else None
    if terminal_index is not None:
        if terminal.sum() != 1 or terminal_index != len(frame) - 1:
            raise ValueError("a single terminal settlement row must be the last year")
        if len(frame) < 2 or not (frame["year"].diff().iloc[1:] == 1).all():
            raise ValueError("terminal settlement requires consecutive operating years followed by H")
        nonterminal_fields = [
            "demand_kwh", "operating_cost_yuan", "gross_capex_yuan", "subsidy_yuan",
            *[column for column in frame if column.startswith("cash_")],
        ]
        if "modeled_fixed_om_cost_yuan" in frame:
            nonterminal_fields.append("modeled_fixed_om_cost_yuan")
        if any(float(frame.loc[terminal_index, column]) != 0.0 for column in nonterminal_fields):
            raise ValueError("terminal settlement must not contain demand, operating costs or new investment")
    tariff = float(finance.get("tariff_yuan_per_kwh", 0.0))
    collection_rate = float(finance.get("collection_rate", 1.0))
    fixed_om_fraction = float(finance.get("fixed_om_fraction_of_cumulative_capex", 0.0))
    fixed_om_method = str(finance.get("fixed_om_method", "proxy_plus_model_adjustment"))
    if fixed_om_method not in {"proxy_plus_model_adjustment", "modeled_only"}:
        raise ValueError("fixed_om_method must be proxy_plus_model_adjustment or modeled_only")
    tax_rate = float(finance.get("income_tax_rate", 0.0))
    debt_fraction = float(finance.get("debt_fraction", 0.0))
    debt_rate = float(finance.get("debt_interest_rate", 0.0))
    debt_term = int(finance.get("debt_term_years", 0))
    discount_rate = float(finance.get("discount_rate", 0.06))

    frame["cumulative_gross_capex_yuan"] = frame["gross_capex_yuan"].cumsum()
    frame["revenue_yuan"] = frame["demand_kwh"] * tariff * collection_rate
    frame["fixed_om_method"] = fixed_om_method
    frame["fixed_om_yuan"] = (
        frame["cumulative_gross_capex_yuan"] * fixed_om_fraction
        if fixed_om_method == "proxy_plus_model_adjustment" else 0.0
    )
    frame.loc[terminal, "fixed_om_yuan"] = 0.0
    if "modeled_fixed_om_cost_yuan" not in frame:
        frame["modeled_fixed_om_cost_yuan"] = 0.0
    frame["total_fixed_om_cost_yuan"] = frame["fixed_om_yuan"] + frame["modeled_fixed_om_cost_yuan"]
    frame["total_cash_operating_cost_yuan"] = frame["operating_cost_yuan"] + frame["fixed_om_yuan"]
    frame["ebitda_yuan"] = frame["revenue_yuan"] - frame["total_cash_operating_cost_yuan"]
    frame["income_tax_yuan"] = frame["ebitda_yuan"].clip(lower=0.0) * tax_rate
    frame["project_cashflow_yuan"] = (
        frame["ebitda_yuan"] - frame["income_tax_yuan"] - frame["gross_capex_yuan"]
        + frame["subsidy_yuan"] + frame["residual_value_yuan"]
    )

    frame["debt_draw_yuan"] = frame["gross_capex_yuan"] * debt_fraction
    frame["debt_service_yuan"] = 0.0
    frame["interest_yuan"] = 0.0
    frame["principal_repayment_yuan"] = 0.0
    frame["terminal_debt_repayment_yuan"] = 0.0
    for index, draw in frame.loc[frame["debt_draw_yuan"] > 0, "debt_draw_yuan"].items():
        if debt_term <= 0:
            raise ValueError("debt_term_years must be positive when debt is drawn")
        payment = draw / debt_term if debt_rate == 0 else draw * debt_rate / (1.0 - (1.0 + debt_rate) ** -debt_term)
        balance = draw
        for pay_index in range(index + 1, min(index + debt_term + 1, len(frame))):
            interest = balance * debt_rate
            principal = min(payment - interest, balance)
            frame.loc[pay_index, "debt_service_yuan"] += interest + principal
            frame.loc[pay_index, "interest_yuan"] += interest
            frame.loc[pay_index, "principal_repayment_yuan"] += principal
            balance -= principal
        if terminal_index is not None and balance > 0.0:
            # The scheduled payment at H already includes the final year's
            # interest. Settle only the still-unpaid principal on that date.
            frame.loc[terminal_index, "terminal_debt_repayment_yuan"] += balance
            frame.loc[terminal_index, "principal_repayment_yuan"] += balance
            frame.loc[terminal_index, "debt_service_yuan"] += balance
    frame["debt_balance_yuan"] = (
        frame["debt_draw_yuan"].cumsum() - frame["principal_repayment_yuan"].cumsum()
    ).clip(lower=0.0)
    frame["equity_cashflow_yuan"] = (
        frame["project_cashflow_yuan"] + frame["debt_draw_yuan"] - frame["debt_service_yuan"]
    )
    frame["debt_service_coverage_ratio"] = np.where(
        (frame["debt_service_yuan"] > 0) & ~terminal,
        frame["ebitda_yuan"] / frame["debt_service_yuan"],
        np.nan,
    )
    project_values = frame["project_cashflow_yuan"].to_numpy(float)
    equity_values = frame["equity_cashflow_yuan"].to_numpy(float)
    debt_years = frame.loc[(frame["debt_service_yuan"] > 0) & ~terminal, "debt_service_coverage_ratio"]
    metrics: dict[str, float | None] = {
        "project_npv_yuan": _npv(discount_rate, project_values),
        "equity_npv_yuan": _npv(discount_rate, equity_values),
        "project_irr": _irr(project_values) if include_diagnostics else None,
        "equity_irr": _irr(equity_values) if include_diagnostics else None,
        "project_payback_years": _payback(project_values) if include_diagnostics else None,
        "equity_payback_years": _payback(equity_values) if include_diagnostics else None,
        "minimum_dscr": None if debt_years.empty else float(debt_years.min()),
        "tariff_yuan_per_kwh": tariff,
        "operating_years": int((~terminal).sum()),
        "terminal_debt_repayment_yuan": float(frame["terminal_debt_repayment_yuan"].sum()),
        "ending_debt_balance_yuan": float(frame["debt_balance_yuan"].iloc[-1]),
    }
    return FinanceArtifacts(frame, metrics)


def break_even_tariff(
    annual_profile: pd.DataFrame,
    finance: dict[str, Any],
    target_equity_npv_yuan: float = 0.0,
    tolerance: float = 1e-6,
) -> float:
    """Return the lowest tariff whose equity NPV reaches the target.

    The search assumes equity NPV is non-decreasing with tariff. Expensive IRR
    and payback diagnostics are intentionally omitted from search evaluations;
    callers still receive them from the default ``evaluate_project_finance``
    path used for the final project report.
    """
    try:
        high = float(finance.get("tariff_search_upper_yuan_per_kwh", 5.0))
    except (TypeError, ValueError) as exc:
        raise ValueError("tariff_search_upper_yuan_per_kwh must be a finite positive number") from exc
    try:
        tolerance = float(tolerance)
    except (TypeError, ValueError) as exc:
        raise ValueError("tolerance must be a finite positive number") from exc
    try:
        target = float(target_equity_npv_yuan)
    except (TypeError, ValueError) as exc:
        raise ValueError("target_equity_npv_yuan must be finite") from exc
    if not np.isfinite(high) or high <= 0.0:
        raise ValueError("tariff_search_upper_yuan_per_kwh must be a finite positive number")
    if not np.isfinite(tolerance) or tolerance <= 0.0:
        raise ValueError("tolerance must be a finite positive number")
    if not np.isfinite(target):
        raise ValueError("target_equity_npv_yuan must be finite")

    def equity_npv(tariff: float) -> float:
        trial = dict(finance)
        trial["tariff_yuan_per_kwh"] = tariff
        value = evaluate_project_finance(
            annual_profile,
            trial,
            include_diagnostics=False,
        ).metrics["equity_npv_yuan"]
        if value is None or not np.isfinite(value):
            raise ValueError("equity NPV must be finite during tariff search")
        return float(value)

    low = 0.0
    if equity_npv(low) >= target:
        return low
    high_npv = equity_npv(high)
    if high_npv < target:
        raise ValueError(
            "target equity NPV is not reachable within "
            f"tariff_search_upper_yuan_per_kwh={high:g} "
            f"(upper-bound equity NPV={high_npv:g}, target={target:g})"
        )

    for _ in range(100):
        if high - low <= tolerance:
            break
        mid = (low + high) / 2.0
        if equity_npv(mid) >= target:
            high = mid
        else:
            low = mid
    return high
