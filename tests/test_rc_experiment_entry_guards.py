"""Ensure the legacy load-shift guard protects the actual ablation entry."""
from types import SimpleNamespace

import pandas as pd
import pytest

from heatplan import experiments


def test_rc_legacy_ablation_is_rejected_before_any_solver_call(monkeypatch):
    c = {"building_thermal": {"enabled": True}, "planning": {}}
    monkeypatch.setattr(experiments, "load_inputs", lambda *a: (c, pd.DataFrame()))
    monkeypatch.setattr(experiments, "HeatPlanningModel", lambda *a: pytest.fail("guard must precede solve"))
    with pytest.raises(ValueError, match="does not disable RC"):
        experiments.ablation_study("config", "series")


def test_rc_carbon_price_scan_is_not_mistaken_for_legacy_ablation(monkeypatch):
    c = {"building_thermal": {"enabled": True}, "planning": {}}
    monkeypatch.setattr(experiments, "load_inputs", lambda *a: (c, pd.DataFrame()))
    prices = []
    def fake_model(config, _):
        prices.append(config["planning"]["carbon_price_yuan_per_kg"])
        return SimpleNamespace(solve=lambda: SimpleNamespace(summary={"objective_yuan_per_year": 1.0}))
    monkeypatch.setattr(experiments, "HeatPlanningModel", fake_model)
    result = experiments.carbon_price_scan("config", "series", [0., .5])
    assert prices == [0., .5]
    assert len(result) == 2
    assert c["planning"] == {}
