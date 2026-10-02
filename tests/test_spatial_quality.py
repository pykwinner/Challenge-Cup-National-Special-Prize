from copy import deepcopy
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from heatplan.io import load_config
from heatplan.model import HeatPlanningModel
from heatplan.spatial_quality import (
    SpatialQualityError, aggregate_delivered_power, build_hierarchy,
    heat_pump_conversion, preprocess_spatial_quality, validate_conversion_dispatch,
    validate_source_grades,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def case():
    config = {
        "planning": {"discount_rate": 0.05, "asset_lifetime_years": 20,
                     "time_step_hours": 1, "max_unserved_rate": 0,
                     "firm_capacity_reserve_margin": 0},
        "grades": [{"name": "low", "temperature_c": 45}, {"name": "high", "temperature_c": 75}],
        "nodes": [{"name": "a", "demand_columns": {}},
                  {"name": "b", "demand_columns": {"low": "demand"}}],
        "sources": [{"name": "heat", "node": "a", "type": "direct_heat",
                     "allowed_grades": ["low"], "supply_temp_c": 50,
                     "existing_capacity_kw": 50, "max_new_capacity_kw": 0,
                     "variable_cost_yuan_per_kwh_heat": 0.1}],
        "pipes": [{"name": "ab", "from": "a", "to": "b", "existing_capacity_kw": 0,
                   "delivery_efficiency": 1, "diameter_options": [
                       {"name": "small", "capacity_kw": 20, "capex_total_yuan": 99}]}],
        "storages": [],
    }
    spatial = {
        "schema_version": 1, "coordinate_system": "local_cartesian_m",
        "systems": [{"name": "city"}],
        "districts": [{"name": "west", "system": "city"}, {"name": "east", "system": "city"}],
        "nodes": [{"name": "a", "district": "west", "x_m": 0, "y_m": 0},
                  {"name": "b", "district": "east", "x_m": 300, "y_m": 400}],
        "candidate_rules": [{"pipe": "ab", "route_factor": 1.2, "max_route_length_m": 600,
                             "connection_allowed": True, "cost_mode": "per_m",
                             "option_costs": [{"name": "small", "yuan_per_m": 2, "fixed_yuan": 100}]}],
    }
    buildings = [
        {"name": "home", "node": "a", "heat_grade": "low", "district_id": "west",
         "area_m2": 100, "heat_loss_kw_per_c": 0.1, "thermal_capacity_kwh_per_c": 7,
         "occupancy_column": "home_occupancy", "min_temperature_c": 18},
        {"name": "office", "node": "a", "heat_grade": "low", "district_id": "west",
         "area_m2": 200, "heat_loss_kw_per_c": 0.4, "thermal_capacity_kwh_per_c": 4,
         "occupancy_column": "office_occupancy", "min_temperature_c": 20},
        {"name": "lab", "node": "b", "heat_grade": "high", "district_id": "east",
         "area_m2": 50, "heat_loss_kw_per_c": 0.2, "thermal_capacity_kwh_per_c": 3},
    ]
    frame = pd.DataFrame({"time": ["2026-01-01T00:00:00Z"], "demand": [10.0],
                          "electricity_price": [0.5], "grid_emission_factor": [0.4], "outdoor_temp": [0.0]})
    return config, spatial, buildings, frame


def test_ownership_and_physical_properties_survive_aggregation(case):
    config, spatial, buildings, _ = case
    hierarchy = build_hierarchy(config, spatial, buildings)
    local = hierarchy.node_grade[("a", "low")]
    assert local["area_m2"] == 300
    assert local["heat_loss_kw_per_c"] == pytest.approx(0.5)
    assert local["thermal_capacity_kwh_per_c"] == 11
    assert local["members"]["home"] == buildings[0]
    assert local["members"]["office"] == buildings[1]
    # C/H = 70 h versus 10 h: aggregation must not replace these by one state.
    assert local["members"]["home"]["thermal_capacity_kwh_per_c"] / local["members"]["home"]["heat_loss_kw_per_c"] == 70
    assert local["members"]["office"]["min_temperature_c"] == 20
    assert hierarchy.system_grade[("city", "low")]["building_names"] == ["home", "office"]
    assert hierarchy.system_grade[("city", "high")]["area_m2"] == 50
    hierarchy.buildings["home"]["area_m2"] = 999
    assert buildings[0]["area_m2"] == 100


@pytest.mark.parametrize("failure", ["duplicate_building", "duplicate_id", "duplicate_node", "unknown_node", "unknown_grade", "unknown_district", "unknown_system", "contradictory_owner", "missing_node", "invalid_property"])
def test_invalid_or_ambiguous_ownership_rejected(case, failure):
    config, spatial, buildings, _ = case
    if failure == "duplicate_building":
        buildings.append(deepcopy(buildings[0]))
    elif failure == "duplicate_id":
        buildings[0]["building_id"] = buildings[1]["building_id"] = "same"
    elif failure == "duplicate_node":
        spatial["nodes"].append(deepcopy(spatial["nodes"][0]))
    elif failure == "unknown_node":
        buildings[0]["node"] = "missing"
    elif failure == "unknown_grade":
        buildings[0]["heat_grade"] = "missing"
    elif failure == "unknown_district":
        spatial["nodes"][0]["district"] = "missing"
    elif failure == "unknown_system":
        spatial["districts"][0]["system"] = "missing"
    elif failure == "contradictory_owner":
        buildings[0]["district_id"] = "east"
    elif failure == "missing_node":
        spatial["nodes"].pop()
    else:
        buildings[0]["heat_loss_kw_per_c"] = float("nan")
    with pytest.raises(SpatialQualityError):
        build_hierarchy(config, spatial, buildings)


def test_layered_power_conservation_and_grade_separation(case):
    config, spatial, buildings, _ = case
    hierarchy = build_hierarchy(config, spatial, buildings)
    result = aggregate_delivered_power(hierarchy, {"home": [1, 2], "office": [3, 5], "lab": [7, 11]},
                                       fixed_service_power_kw={("b", "low"): [13, 17]})
    np.testing.assert_array_equal(result.node_grade_kw[("a", "low")], [4, 7])
    np.testing.assert_array_equal(result.district_grade_kw[("west", "low")], [4, 7])
    np.testing.assert_array_equal(result.system_grade_kw[("city", "low")], [17, 24])
    np.testing.assert_array_equal(result.system_grade_kw[("city", "high")], [7, 11])
    for layer in (result.node_grade_kw, result.district_grade_kw, result.system_grade_kw):
        np.testing.assert_array_equal(sum(layer.values()), [24, 35])


def test_explicit_route_length_drives_cost_without_route_factor_override(case):
    config, spatial, buildings, frame = case
    rule = spatial["candidate_rules"][0]
    rule.update(route_length_m=575.0, route_length_basis="surveyed_polyline",
                route_provenance={"source_id": "analytical_test"}, physical_edge_id="edge:1",
                route_factor=100.0)
    result = preprocess_spatial_quality(config, spatial, buildings=buildings, timeseries=frame)
    pipe = result.config["pipes"][0]
    assert pipe["length_m"] == 575
    assert pipe["diameter_options"][0]["capex_total_yuan"] == 1250
    assert pipe["physical_edge_id"] == "edge:1"
    # The source config and rule are not overwritten.
    assert config["pipes"][0]["diameter_options"][0]["capex_total_yuan"] == 99
    assert rule["route_factor"] == 100


@pytest.mark.parametrize("failure", ["missing_basis", "missing_provenance", "too_short", "duplicate_edge"])
def test_route_provenance_and_unique_physical_edge_identity_are_required(case, failure):
    config, spatial, buildings, frame = case
    rule = spatial["candidate_rules"][0]
    rule.update(route_length_m=550., route_length_basis="test_geometry",
                route_provenance="analytical_test", physical_edge_id="one")
    if failure == "missing_basis": rule.pop("route_length_basis")
    if failure == "missing_provenance": rule.pop("route_provenance")
    if failure == "too_short": rule["route_length_m"] = 499
    if failure == "duplicate_edge":
        config["pipes"].append({**deepcopy(config["pipes"][0]), "name": "second"})
        spatial["candidate_rules"].append({**deepcopy(rule), "pipe": "second"})
    with pytest.raises(SpatialQualityError):
        preprocess_spatial_quality(config, spatial, buildings=buildings, timeseries=frame)


def test_coincident_coordinates_do_not_divide_by_zero_for_explicit_route(case):
    config, spatial, buildings, frame = case
    spatial["nodes"][1].update(x_m=0, y_m=0)
    rule = spatial["candidate_rules"][0]
    rule.pop("route_factor")
    rule.update(route_length_m=10., route_length_basis="test_loop",
                route_provenance="analytical_test")
    result = preprocess_spatial_quality(config, spatial, buildings=buildings, timeseries=frame)
    assert result.config["pipes"][0]["length_m"] == 10


def test_multiple_nodes_can_share_district_without_losing_ownership(case):
    config, spatial, buildings, _ = case
    spatial["nodes"][1]["district"] = "west"
    buildings[2].update(district_id="west", heat_grade="low")
    hierarchy = build_hierarchy(config, spatial, buildings)
    assert hierarchy.district_grade[("west", "low")]["area_m2"] == 350
    result = aggregate_delivered_power(hierarchy, {"home": [1], "office": [3], "lab": [7]})
    assert len(result.node_grade_kw) == 2
    assert len(result.district_grade_kw) == 1
    np.testing.assert_array_equal(result.district_grade_kw[("west", "low")], [11])


def test_fixed_services_cannot_name_unregistered_nodes_or_grades(case):
    config, spatial, buildings, _ = case
    hierarchy = build_hierarchy(config, spatial, buildings)
    powers = {name: [1] for name in hierarchy.buildings}
    with pytest.raises(SpatialQualityError, match="unknown fixed service"):
        aggregate_delivered_power(hierarchy, powers, fixed_service_power_kw={("missing", "low"): [2]})


@pytest.mark.parametrize("values", [
    {"home": [1], "office": [2]},
    {"home": [1], "office": [2, 3], "lab": [1]},
    {"home": [-1], "office": [2], "lab": [1]},
    {"home": [float("inf")], "office": [2], "lab": [1]},
])
def test_incomplete_or_invalid_power_cannot_silently_aggregate(case, values):
    config, spatial, buildings, _ = case
    with pytest.raises(SpatialQualityError):
        aggregate_delivered_power(build_hierarchy(config, spatial, buildings), values)


def test_route_distance_controls_real_cost_and_does_not_mutate_input(case):
    config, spatial, buildings, frame = case
    original = deepcopy(config)
    result = preprocess_spatial_quality(config, spatial, buildings=buildings, timeseries=frame)
    assert config == original
    assert result.config["pipes"][0]["length_m"] == 600
    assert result.config["pipes"][0]["diameter_options"][0]["capex_total_yuan"] == 1300
    model = HeatPlanningModel(result.config, frame)
    model.build()
    index = model.variables["pipe_choice"][("ab", "small")]
    assert model.problem.objective[index] == pytest.approx(1300 * model.crf)
    # Repricing overwrites the option total rather than repeatedly adding route cost.
    repeated = preprocess_spatial_quality(result.config, spatial, buildings=buildings, timeseries=frame)
    assert repeated.config == result.config


def test_preserve_cost_mode_keeps_original_engineering_estimate(case):
    config, spatial, _, frame = case
    spatial["candidate_rules"][0]["cost_mode"] = "preserve"
    result = preprocess_spatial_quality(config, spatial, timeseries=frame)
    assert result.config["pipes"][0]["length_m"] == 600
    assert result.config["pipes"][0]["diameter_options"][0]["capex_total_yuan"] == 99


def test_distance_cutoff_changes_tiny_model_feasibility(case):
    config, spatial, _, frame = case
    accepted = preprocess_spatial_quality(config, spatial, timeseries=frame)
    connected = HeatPlanningModel(accepted.config, frame)
    connected.build()
    yes = connected.problem.solve(time_limit=5)
    assert yes.status == 0
    assert yes.x[connected.variables["pipe_flow_forward"][("ab", "low", 0)]] == pytest.approx(10)
    spatial["candidate_rules"][0]["max_route_length_m"] = 599
    rejected = preprocess_spatial_quality(config, spatial, timeseries=frame)
    assert rejected.candidate_report[0]["reasons"] == ["exceeds_max_route_length"]
    assert rejected.config["pipes"] == []
    disconnected = HeatPlanningModel(rejected.config, frame)
    disconnected.build()
    assert not disconnected.variables["pipe_flow_forward"]
    assert disconnected.problem.solve(time_limit=5).status == 2


@pytest.mark.parametrize("mode", ["access", "cross_system"])
def test_connection_permission_affects_candidate_feasibility(case, mode):
    config, spatial, _, frame = case
    if mode == "access":
        spatial["candidate_rules"][0]["connection_allowed"] = False
    else:
        spatial["systems"].append({"name": "other"})
        spatial["districts"][1]["system"] = "other"
    assert not preprocess_spatial_quality(config, spatial, timeseries=frame).config["pipes"]
    if mode == "cross_system":
        spatial["candidate_rules"][0]["allow_cross_system"] = True
        assert len(preprocess_spatial_quality(config, spatial, timeseries=frame).config["pipes"]) == 1


@pytest.mark.parametrize("protected", ["installed", "n_minus_1"])
def test_existing_or_required_assets_are_not_silently_dropped(case, protected):
    config, spatial, _, frame = case
    if protected == "installed":
        config["pipes"][0]["existing_capacity_kw"] = 5
    else:
        config["planning"]["n_minus_1_pipe_names"] = ["ab"]
    spatial["candidate_rules"][0]["connection_allowed"] = False
    with pytest.raises(SpatialQualityError, match="installed asset or required N-1"):
        preprocess_spatial_quality(config, spatial, timeseries=frame)


@pytest.mark.parametrize("failure", ["coordinate_unit", "missing_rule", "duplicate_rule", "factor", "negative_cost", "unknown_option", "pipe_grade_change", "pipe_grade_limit"])
def test_incomplete_geometry_or_ignored_core_semantics_rejected(case, failure):
    config, spatial, _, frame = case
    rule = spatial["candidate_rules"][0]
    if failure == "coordinate_unit":
        spatial["coordinate_system"] = "lat_lon"
    elif failure == "missing_rule":
        spatial["candidate_rules"] = []
    elif failure == "duplicate_rule":
        spatial["candidate_rules"].append(deepcopy(rule))
    elif failure == "factor":
        rule["route_factor"] = 0.9
    elif failure == "negative_cost":
        rule["option_costs"][0]["yuan_per_m"] = -1
    elif failure == "unknown_option":
        rule["option_costs"][0]["name"] = "missing"
    elif failure == "pipe_grade_change":
        config["pipes"][0].update(from_grade="low", to_grade="high")
    else:
        config["pipes"][0]["allowed_grades"] = ["low"]
    with pytest.raises(SpatialQualityError):
        preprocess_spatial_quality(config, spatial, timeseries=frame)


def test_low_temperature_direct_heat_rejected(case):
    config, spatial, _, frame = case
    config["sources"][0]["allowed_grades"] = ["high"]
    with pytest.raises(SpatialQualityError, match="low-temperature direct supply"):
        preprocess_spatial_quality(config, spatial, timeseries=frame)


def test_direct_temperature_must_be_declared(case):
    config, _, _, _ = case
    del config["sources"][0]["supply_temp_c"]
    with pytest.raises(SpatialQualityError, match="supply_temp_c"):
        validate_source_grades(config)


def test_heat_pump_high_grade_requires_positive_electricity_and_conserved_resource_heat():
    source = {"type": "heat_pump", "source_temp_c": 10, "carnot_efficiency": 0.45,
              "cop_min": 1.2, "cop_max": 6}
    conversion = heat_pump_conversion(source, 75)
    expected_cop = 0.45 * (75 + 273.15) / 65
    assert conversion["cop"] == pytest.approx(expected_cop)
    electricity = 100 / expected_cop
    result = validate_conversion_dispatch(100, electricity, expected_cop,
                                          resource_input_kw=100 - electricity)
    assert result["electricity_input_kw"] > 0
    with pytest.raises(SpatialQualityError, match="not free"):
        validate_conversion_dispatch(100, 0, expected_cop)
    with pytest.raises(SpatialQualityError, match="resource heat balance"):
        validate_conversion_dispatch(100, electricity, expected_cop, resource_input_kw=100)
    with pytest.raises(SpatialQualityError, match="type=heat_pump"):
        heat_pump_conversion({"type": "direct_heat"}, 75)


def test_high_grade_heat_pump_enters_core_electric_load_balance(case):
    config, spatial, _, frame = case
    config["sources"][0].update(type="heat_pump", source_temp_c=10, allowed_grades=["high"])
    config["nodes"][1]["demand_columns"] = {"high": "demand"}
    preprocessed = preprocess_spatial_quality(config, spatial, timeseries=frame)
    model = HeatPlanningModel(preprocessed.config, frame)
    model.build()
    heat = model.variables["source_heat"][("heat", "high", 0)]
    cop = 0.45 * (75 + 273.15) / (75 - 10)
    assert model._electric_load_terms[0][heat] == pytest.approx(1 / cop)
    result = model.problem.solve(time_limit=5)
    assert result.status == 0
    assert result.x[heat] == pytest.approx(10)
    assert result.x[heat] * model._electric_load_terms[0][heat] == pytest.approx(10 / cop)


@pytest.mark.parametrize("updates", [{"cop_min": 1}, {"cop_max": float("inf")}, {"carnot_efficiency": 0}, {"cop_min": 7, "cop_max": 6}])
def test_invalid_heat_pump_cop_rejected(updates):
    source = {"type": "heat_pump", **updates}
    with pytest.raises(SpatialQualityError):
        heat_pump_conversion(source, 75)


def test_dynamic_heat_pump_needs_real_columns_and_rejects_invalid_humidity(case):
    config, _, _, frame = case
    config["sources"][0].update(type="heat_pump", source_temp_column="outdoor_temp", relative_humidity_column="rh")
    with pytest.raises(SpatialQualityError, match="requires timeseries column"):
        validate_source_grades(config)
    frame["rh"] = 90  # core expects a fraction; do not silently interpret percent.
    with pytest.raises(SpatialQualityError, match="humidity"):
        validate_source_grades(config, frame)


def test_s3_pack_changes_capex_and_matches_existing_cop():
    config = load_config(ROOT / "data/public_scenario_inputs/scenario3_wuhan_joint_public.yaml")
    spatial = yaml.safe_load((ROOT / "data/competition_spatial/s3_spatial.yaml").read_text(encoding="utf-8"))
    # Synthetic two-hour cold/humid checks, not a seasonal replay.
    frame = pd.read_csv(ROOT / "data/scenario3_timeseries.csv").iloc[:2].copy()
    frame["outdoor_temp"] = [-1.0, 9.0]
    frame["relative_humidity"] = [0.95, 0.60]
    result = preprocess_spatial_quality(config, spatial, timeseries=frame)
    route = np.hypot(900, 1000) * 1.08
    assert result.config["pipes"][0]["length_m"] == pytest.approx(route)
    options = {o["name"]: o for o in result.config["pipes"][0]["diameter_options"]}
    assert options["DN80"]["capex_total_yuan"] == pytest.approx(route * 200)
    assert options["DN100"]["capex_total_yuan"] == pytest.approx(route * 300 + 5000)
    model = HeatPlanningModel(result.config, frame)
    for source in config["sources"]:
        if source["type"] != "heat_pump":
            continue
        for t in range(len(frame)):
            conversion = heat_pump_conversion(source, 45,
                source_temperature_c=frame[source["source_temp_column"]].iloc[t] if source.get("source_temp_column") else None,
                relative_humidity=frame[source["relative_humidity_column"]].iloc[t] if source.get("relative_humidity_column") else None)
            assert conversion["cop"] == pytest.approx(model._cop(source, model.grades["low"], t))


def test_c1_building_interface_uses_config_records(case):
    config, spatial, buildings, _ = case
    config["building_thermal"] = {"enabled": True, "buildings": buildings}
    result = build_hierarchy(config, spatial)
    assert result.buildings == {row["name"]: row for row in buildings}


@pytest.mark.parametrize("configuration,timeseries", [
    ("scenario1_existing_network.yaml", "scenario1_existing_network_timeseries.csv"),
    ("scenario2_distributed_clean.yaml", "scenario2_timeseries.csv"),
])
def test_existing_s1_s2_source_quality_remains_compatible(configuration, timeseries):
    config = load_config(ROOT / "data" / configuration)
    frame = pd.read_csv(ROOT / "data" / timeseries).iloc[:2]
    assert validate_source_grades(config, frame)
