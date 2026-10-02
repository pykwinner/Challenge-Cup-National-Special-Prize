"""Shared corridor incidence tests, including independent manual length totals."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from heatplan.spatial_adapter import PHYSICAL_FIELDS, adapt_reference_network, validate_candidate_network


@pytest.fixture
def branched_case():
    coordinates = {"n1": [0, 0], "n2": [100, 0], "n3": [200, 0],
                   "n4": [100, 100], "n5": [500, 0], "n6": [600, 0]}
    definitions = [("w1:0", "w1", "n1", "n2"), ("w1:1", "w1", "n2", "n3"),
                   ("w2:0", "w2", "n2", "n4"), ("w3:0", "w3", "n5", "n6")]
    edges = [{"u": u, "v": v, "edge_key": key, "segment_id": key, "osm_id": wid,
              "kind": "road", "length_m": 100.0, "grade_signature": ["0", "no", "no"],
              "source_record": {"traffic_oneway": "yes", "way_version": 1}}
             for key, wid, u, v in definitions]
    graph = {"native": True, "nodes": [{"node_id": n, "x_m": xy[0], "y_m": xy[1]} for n, xy in coordinates.items()], "edges": edges}
    buildings = [{"osm_id": f"w10{i}", "building": "yes"} for i in range(1, 6)]
    snaps = [{"osm_id": "w101", "status": "snapped", "point_xy_m": [0, -10], "snap_node": "n1", "distance_m": 10.0},
             {"osm_id": "w102", "status": "snapped", "point_xy_m": [200, 20], "snap_node": "n3", "distance_m": 20.0},
             {"osm_id": "w103", "status": "snapped", "point_xy_m": [100, 130], "snap_node": "n4", "distance_m": 30.0},
             {"osm_id": "w104", "status": "snapped", "point_xy_m": [500, 0], "snap_node": "n5", "distance_m": 0.0},
             {"osm_id": "w105", "status": "snap_distance_exceeded", "point_xy_m": [100, 260], "distance_m": 160.0}]
    by_key = {e["edge_key"]: e for e in edges}

    def route(anchor, target, nodes, keys, total):
        steps = [{**by_key[key], "u": u, "v": v} for u, v, key in zip(nodes, nodes[1:], keys)]
        return {"anchor_osm_id": anchor, "target_osm_id": target, "status": "routed",
                "node_ids": nodes, "edges": steps, "road_length_m": 200.0, "total_reference_length_m": total}

    routes = [route("w101", "w102", ["n1", "n2", "n3"], ["w1:0", "w1:1"], 230.0),
              route("w101", "w103", ["n1", "n2", "n4"], ["w1:0", "w2:0"], 240.0),
              route("w102", "w101", ["n3", "n2", "n1"], ["w1:1", "w1:0"], 230.0),
              {"anchor_osm_id": "w101", "target_osm_id": "w104", "status": "no_path"},
              {"anchor_osm_id": "w101", "target_osm_id": "w105", "status": "snap_not_available"}]
    return graph, buildings, snaps, routes


def adapt(case, **kwargs):
    return adapt_reference_network(*case, namespace="manual_branch", geometry_provenance={
        "reference_kind": "analytic_test_fixture_not_real_OSM", "metric_epsg": None}, **kwargs)


def road_between(data, a, b):
    return next(e for e in data["candidate_edges"] if e["kind"] == "road" and {e["from_node"], e["to_node"]} == {a, b})


def test_shared_branch_counts_each_corridor_and_common_connector_once(branched_case):
    data = adapt(branched_case)
    # Independently: three 100m road spans and connectors of 10m,20m,30m.
    audit = data["validation"]
    assert audit["unique_road_edges"] == 3
    assert audit["unique_reference_connectors"] == 3
    assert audit["unique_candidate_route_length_m"] == pytest.approx(360)
    assert audit["sum_individual_route_length_m"] == pytest.approx(230 + 240 + 230)
    assert audit["repeated_length_if_paths_built_independently_m"] == pytest.approx(340)
    assert audit["shared_road_edges"] == 2
    assert road_between(data, "n1", "n2")["use_count"] == 3
    assert road_between(data, "n2", "n3")["use_count"] == 2
    assert road_between(data, "n2", "n4")["use_count"] == 1
    assert not any("w3" == r.get("osm_way_id") for e in data["candidate_edges"] for r in e["geometry_provenance"]["source_records"])


def test_reverse_path_reuses_ids_and_flips_every_direction_despite_traffic_oneway(branched_case):
    data = adapt(branched_case)
    paths = {(p["anchor_osm_id"], p["target_osm_id"]): p for p in data["paths"]}
    forward = paths[("w101", "w102")]["traversals"]
    reverse = paths[("w102", "w101")]["traversals"]
    assert reverse == [{"edge_identity": s["edge_identity"], "direction": -s["direction"]} for s in reversed(forward)]
    assert road_between(data, "n1", "n2")["directions_used"] == [-1, 1]
    assert data["validation"]["both_directions_used_edges"] == 4  # two roads plus two connectors
    assert all(e["bidirectional"] for e in data["candidate_edges"])


def test_duplicate_native_road_representation_shares_identity_and_preserves_aliases(branched_case):
    graph, buildings, snaps, routes = deepcopy(branched_case)
    alias = {**graph["edges"][0], "edge_key": "w9:0", "segment_id": "w9:0", "osm_id": "w9"}
    graph["edges"].append(alias)
    routes[2]["edges"][1].update(edge_key="w9:0", segment_id="w9:0", osm_id="w9")
    data = adapt((graph, buildings, snaps, routes))
    edge = road_between(data, "n1", "n2")
    assert {r["osm_way_id"] for r in edge["geometry_provenance"]["source_records"]} == {"w1", "w9"}
    assert data["validation"]["unique_candidate_route_length_m"] == pytest.approx(360)
    assert edge["use_count"] == 3


def test_different_grade_contexts_are_separate_corridors(branched_case):
    graph, buildings, snaps, routes = deepcopy(branched_case)
    alias = {**graph["edges"][0], "edge_key": "w9:0", "segment_id": "w9:0", "osm_id": "w9", "grade_signature": ["1", "yes", "no"]}
    graph["edges"].append(alias)
    routes[2]["edges"][1].update(edge_key="w9:0", segment_id="w9:0", osm_id="w9")
    data = adapt((graph, buildings, snaps, routes))
    assert data["validation"]["unique_road_edges"] == 4
    assert data["validation"]["unique_candidate_route_length_m"] == pytest.approx(460)


def test_disconnected_and_over_threshold_buildings_remain_failures(branched_case):
    data = adapt(branched_case)
    assert [(f["target_osm_id"], f["status"]) for f in data["failed_paths"]] == [("w104", "no_path"), ("w105", "snap_not_available")]
    assert data["failed_paths"][1]["target_snap_distance_m"] == 160
    assert all(f["route_length_m"] is None and not f["traversals"] for f in data["failed_paths"])
    assert not any("w105" in e["from_node"] or "w105" in e["to_node"] for e in data["candidate_edges"])
    assert {b["osm_id"] for b in data["buildings"]} == {f"w10{i}" for i in range(1, 6)}


def test_no_physics_cost_source_or_permission_is_manufactured(branched_case):
    data = adapt(branched_case)
    assert data["eligible_for_engineering_solver_input"] is False
    assert data["source_capacity_kw"] is None and data["costs_verified"] is False
    for edge in data["candidate_edges"]:
        assert all(edge["physical_parameters"][field] is None for field in PHYSICAL_FIELDS)
        assert edge["connection_allowed"] is None and edge["engineering_admitted"] is False
        assert edge["actual_heating_asset_identity"] is None
    for rule in data["candidate_rules"]:
        assert rule["connection_allowed"] is None
        assert rule["route_length_m"] > 0 and rule["route_length_basis"] and rule["route_provenance"]
        assert rule["physical_edge_id"] == rule["pipe"]


def test_order_stable_for_same_frozen_geometry_and_snap_batch(branched_case):
    a = adapt(branched_case)
    graph, buildings, snaps, routes = deepcopy(branched_case)
    graph["nodes"].reverse(); graph["edges"].reverse()
    b = adapt((graph, buildings[::-1], snaps[::-1], routes[::-1]))
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_candidate_rules_consume_real_edge_lengths_and_charge_shared_union_once(branched_case):
    from heatplan.spatial_quality import SpatialQualityError, preprocess_spatial_quality
    data = adapt(branched_case)
    # Pure analytic interface fixture. These physics/permissions/costs are not
    # written into any OSM artifact and do not grant real engineering admission.
    config = {"grades": [{"name": "low", "temperature_c": 45}], "sources": [],
              "nodes": [{"name": n["node_id"], "demand_columns": {}} for n in data["nodes"]],
              "pipes": [{"name": e["identity"], "from": e["from_node"], "to": e["to_node"],
                         "existing_capacity_kw": 0, "diameter_options": [{"name": "analytic", "capacity_kw": 1, "capex_total_yuan": 0}]}
                        for e in data["candidate_edges"]]}
    spatial = {"schema_version": 1, "coordinate_system": "local_cartesian_m",
               "systems": [{"name": "test"}], "districts": [{"name": "d", "system": "test"}],
               "nodes": [{"name": n["node_id"], "district": "d", "x_m": n["xy_m"][0], "y_m": n["xy_m"][1]} for n in data["nodes"]],
               "candidate_rules": deepcopy(data["candidate_rules"])}
    for rule in spatial["candidate_rules"]:
        rule.update(max_route_length_m=1000, route_factor=9, cost_mode="per_m",
                    option_costs=[{"name": "analytic", "yuan_per_m": 2, "fixed_yuan": 0}])
    with pytest.raises(SpatialQualityError, match="permissions"):
        preprocess_spatial_quality(config, spatial)
    for rule in spatial["candidate_rules"]:
        rule["connection_allowed"] = True  # test fixture only
    processed = preprocess_spatial_quality(config, spatial)
    assert sum(p["length_m"] for p in processed.config["pipes"]) == pytest.approx(360)
    assert sum(p["diameter_options"][0]["capex_total_yuan"] for p in processed.config["pipes"]) == pytest.approx(720)
    assert len(processed.config["pipes"]) == 6
    assert all(p["route_provenance"] for p in processed.config["pipes"])


@pytest.mark.parametrize("fault", ["false_disconnect", "false_threshold", "bad_snap", "altered_route", "duplicate_path", "missing_node"])
def test_invalid_reachability_or_identity_rejected(branched_case, fault):
    graph, buildings, snaps, routes = deepcopy(branched_case)
    if fault == "false_disconnect":
        routes[0] = {"anchor_osm_id": "w101", "target_osm_id": "w102", "status": "no_path"}
    elif fault == "false_threshold":
        snaps[4]["distance_m"] = 149
    elif fault == "bad_snap":
        snaps[0]["distance_m"] = 151
    elif fault == "altered_route":
        routes[0]["edges"][0]["length_m"] = 101
    elif fault == "duplicate_path":
        routes.append(deepcopy(routes[0]))
    else:
        graph["nodes"] = [n for n in graph["nodes"] if n["node_id"] != "n2"]
    with pytest.raises(ValueError):
        adapt((graph, buildings, snaps, routes))


@pytest.mark.parametrize("fault", ["direction", "length", "rule_length", "duplicate_edge", "usage", "failed_traversal"])
def test_independent_validator_rejects_corrupted_incidence_and_accounting(branched_case, fault):
    data = adapt(branched_case)
    if fault == "direction":
        data["paths"][0]["traversals"][0]["direction"] *= -1
    elif fault == "length":
        data["candidate_edges"][0]["route_length_m"] += 1
    elif fault == "rule_length":
        data["candidate_rules"][0]["route_length_m"] += 1
    elif fault == "duplicate_edge":
        data["candidate_edges"].append(deepcopy(data["candidate_edges"][0]))
    elif fault == "usage":
        data["candidate_edges"][0]["use_count"] += 1
    else:
        data["failed_paths"][0]["traversals"] = deepcopy(data["paths"][0]["traversals"])
    with pytest.raises(ValueError):
        validate_candidate_network(data)


ARTIFACTS = Path(__file__).resolve().parents[1] / "docs/implementation_20261002/spatial"


@pytest.mark.parametrize("city,success,failed,roads,shared", [("guyuan", 2, 3, 33, 1), ("urumqi", 5, 0, 24, 22), ("wuhan", 5, 0, 7, 4)])
def test_real_three_city_outputs_recompute_without_optional_geo_dependencies(city, success, failed, roads, shared):
    path = ARTIFACTS / city / "candidate_network.json"
    if not path.exists():
        pytest.skip("Captured geometry adapter outputs have not been built")
    data = json.loads(path.read_text(encoding="utf-8"))
    validation = validate_candidate_network(data)
    assert validation == data["validation"]
    assert validation["successful_paths"] == success and validation["failed_paths"] == failed
    assert validation["unique_road_edges"] == roads and validation["shared_road_edges"] == shared
    assert len(data["buildings"]) == 6 and data["max_snap_distance_m"] == 150
    assert data["reference_replay"]["unchanged_statuses"] is True
    if city == "guyuan":
        historical = json.loads((ARTIFACTS.parents[1] / "execution_20261002/spatial_reference/guyuan/snaps.json").read_text(encoding="utf-8"))
        frozen_failed_distances = sorted(s["distance_m"] for s in historical if s["status"] == "snap_distance_exceeded")
        actual = sorted(f["target_snap_distance_m"] for f in data["failed_paths"])
        assert actual == pytest.approx(frozen_failed_distances, abs=1e-5)
        assert min(actual) > 150
