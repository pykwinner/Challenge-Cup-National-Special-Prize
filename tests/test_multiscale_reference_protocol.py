"""Protocol fairness checks; these fixtures do not claim measured buildings."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from run_multiscale_reference import input_reference, make_case


def test_two_nodes_use_distinct_declared_design_variants_and_one_shared_limit():
    ref, frame = input_reference()
    before = deepcopy(ref)
    k = .1043 * 2.5 * (20 - frame.outdoor_temp.min())
    config, data = make_case(ref, frame, "coupled_flexible", k)
    a, b = config["building_thermal"]["buildings"]
    assert a["node"] != b["node"]
    assert b["heat_loss_kw_per_c"] == a["heat_loss_kw_per_c"] * 1.5
    assert b["thermal_capacity_kwh_per_c"] == a["thermal_capacity_kwh_per_c"] * 4
    assert config["grid_connection"]["firm_capacity_method"] == "portfolio_dispatch"
    assert config["grid_connection"]["max_new_capacity_kw"] == 0
    assert ref == before
    assert np.array_equal(data.outdoor_temp, frame.outdoor_temp)
    assert np.array_equal(data.electricity_price, frame.electricity_price)
    assert all(x["min_temperature_c"] == 20 and x["max_temperature_c"] == 27 and
               x["initial_temperature_c"] == x["final_temperature_c"] == 20 for x in [a, b])


def test_counterfactual_removes_only_grid_and_single_lock_changes_only_operation():
    ref, frame = input_reference()
    coupled, data = make_case(ref, frame, "coupled_flexible", 6.4796375)
    unlimited, other = make_case(ref, frame, "joint_unlimited", 6.4796375)
    assert {k: v for k, v in coupled.items() if k != "grid_connection"} == unlimited
    assert data.equals(other)
    locked, _ = make_case(ref, frame, "coupled_B_fixed20", 6.4796375)
    expected = deepcopy(coupled)
    expected["building_thermal"]["buildings"][1]["max_temperature_c"] = 20.0
    assert locked == expected


def test_registered_shared_capacity_admits_full_fixed20_heat_and_cold_witness():
    ref, frame = input_reference()
    h = ref["building_thermal"]["buildings"][0]["heat_loss_kw_per_c"]
    k = h * 2.5 * max(20 - frame.outdoor_temp.min(), 0)
    combined = sum(np.abs(h * m * (20 - frame.outdoor_temp) - .2) for m in [1, 1.5])
    assert combined.max() <= k
    assert len(frame) == 2880
