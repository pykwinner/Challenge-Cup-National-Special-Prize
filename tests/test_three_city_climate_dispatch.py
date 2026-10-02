"""Calendar safety and independently reconstructed continuous-policy checks."""
import importlib.util
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("climate_dispatch", ROOT / "scripts/run_three_city_climate_dispatch.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def inputs():
    config, prices = module.load_inputs(ROOT / module.CONFIG, ROOT / module.SERIES)
    climate = pd.read_csv(ROOT / "data/derived/three_city_common_reference_20261002/wuhan_hourly.csv")
    return config, climate, prices


def test_hourly_join_rejects_offset_and_existing_economics():
    _, climate, prices = inputs()
    shifted = prices.copy()
    shifted["time"] = pd.to_datetime(shifted.time) + pd.Timedelta(hours=1)
    with pytest.raises(ValueError, match="calendar mismatch"):
        module.join_common_economics(climate, shifted)
    climate["electricity_price"] = 1
    with pytest.raises(ValueError, match="pre-existing"):
        module.join_common_economics(climate, prices)


def test_full_continuous_policy_and_corrupted_power():
    config, climate, prices = inputs()
    frame = module.join_common_economics(climate, prices)
    trajectory = module.rollout(config, frame, 0)
    audit = module.independent_metrics(config, frame, trajectory)
    assert audit["passed"]
    assert audit["maximum_temperature_residual_c"] < 1e-10
    changed = trajectory.copy()
    changed.loc[100, "heat_kw"] += .1
    assert not module.independent_metrics(config, frame, changed)["passed"]
    assert not module.independent_metrics(config, frame, trajectory, cap=audit["carbon_kg"] - 1)["passed"]


def test_same_hours_in_wrong_order_rejected():
    config, climate, prices = inputs()
    frame = module.join_common_economics(climate, prices)
    trajectory = module.rollout(config, frame, 0)
    trajectory.iloc[[2, 3]] = trajectory.iloc[[3, 2]].to_numpy()
    with pytest.raises(ValueError, match="trajectory calendar"):
        module.independent_metrics(config, frame, trajectory)
