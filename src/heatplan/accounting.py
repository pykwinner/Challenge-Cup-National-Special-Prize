"""Shared cash-cost definitions for heat regulation and project reporting.

Penalty, unserved-energy, exergy and CVaR terms are optimization preferences,
not invoices. Keep this explicit allow-list shared by the single-stage model,
joint-stage constraints and finance export.
"""
from __future__ import annotations

from typing import Any


HEATING_CASH_COMPONENTS = (
    "operating_cost",
    "carbon_cost",
    "fixed_om_cost",
    "startup_shutdown_cost",
    "biomass_fuel_cost",
)


def heating_cost_allocation_fraction(
    config: dict[str, Any], category: str, asset: str, option: str = "new",
) -> float:
    """Return the same category/asset allocation used by investment recording."""
    specification = config["planning"].get("heating_cost_allocation_fraction", {}).get(category, 1.0)
    lookup = f"{asset}:{option}" if category == "pipe_choice" else asset
    value = specification.get(lookup, specification.get("default", 1.0)) if isinstance(specification, dict) else specification
    fraction = float(value)
    if not 0.0 <= fraction <= 1.0:
        raise ValueError(f"{category}/{lookup} 的供热成本分摊比例必须位于 [0,1]")
    return fraction
