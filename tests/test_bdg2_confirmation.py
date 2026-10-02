"""Confirmation selection isolation and complete-day loss diagnostics."""
import importlib.util
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("bdg2_confirmation_test_module", ROOT / "scripts/bdg2_confirmation_benchmark.py")
b = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = b
spec.loader.exec_module(b)


def channel(building, site, coverage=1.0):
    return {"building_id": building, "site_id": site,
            "years": {"2017": {"coverage": coverage, "weather_join_coverage": coverage, "negative": 0}}}


def test_selects_new_sites_and_unique_buildings_without_algorithm_outcomes():
    inventories = {category: [channel(f"new_{category}_{i}", "New") for i in range(4)]
                   + [channel(f"old_{category}", "Old")] for category in b.base.CATEGORIES}
    result = b.select_confirmation(inventories, [{"building_id": "old_hotwater", "site_id": "Old"}])
    assert len(result["cases"]) == 9
    assert all(c["unseen_site"] and c["unseen_building"] for c in result["cases"])
    assert len({c["building_id"] for c in result["cases"]}) == 9


def test_bad_quality_and_cross_category_duplicate_are_not_replaced_by_result():
    inventories = {category: [channel("shared", "New"), channel(category+"_bad", "New", .5)]
                   for category in b.base.CATEGORIES}
    result = b.select_confirmation(inventories, [])
    assert len(result["cases"]) == 1
    assert any("development_or_confirmation_building_already_used" in d["reasons"] for d in result["decisions"])
    assert result["counts"]["steam"]["shortfall"] == 3


def test_full_day_filter_losses_include_meter_peak_and_energy_without_fill():
    times = pd.date_range("2017-01-01", periods=48, freq="h")
    load = np.ones(48)
    load[25] = 100
    temperatures = np.zeros(48)
    temperatures[26] = np.nan
    raw = pd.DataFrame({"time": times, "energy_kwh": load, "outdoor_temp": temperatures})
    full, retained, _, quality = b.base.prepare_year(raw, 2017)
    answer = b.observed_scope_audit(full, retained, {"days": [0]}, quality)
    assert answer["valid_meter_hours_excluded_by_complete_day_filter"] == 24
    assert answer["observed_energy_excluded_kwh"] == 123
    assert answer["original_observed_peak_retention_ratio"] == .01
    assert answer["original_peak_lost_by_complete_day_filter"]
    assert pd.isna(full.iloc[26]["outdoor_temp"])


def test_raw_scope_ramp_does_not_bridge_missing_meter_hours():
    raw = pd.DataFrame({"time": pd.date_range("2017-01-01", periods=48, freq="h"),
                        "energy_kwh": np.ones(48), "outdoor_temp": np.zeros(48)})
    raw.loc[23, "energy_kwh"] = np.nan
    raw.loc[24:, "energy_kwh"] = 10
    full, retained, _, quality = b.base.prepare_year(raw, 2017)
    answer = b.observed_scope_audit(full, retained, {"days": [0]}, quality)
    assert answer["maximum_original_valid_adjacent_ramp_kw_per_hour"] == 0
    assert answer["original_observed_ramp_retention_ratio"] is None
