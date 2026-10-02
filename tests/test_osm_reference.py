"""Optional spatial-reference tests; default project environments may skip pyproj.

Use a Python environment with the dependencies in requirements-spatial-reference.txt.
This module does not add the isolated dependency path or mutate any environment.
"""
import json
from math import hypot
from pathlib import Path

import pytest

pytest.importorskip("pyproj", reason="Optional geometry-reference dependency; no MILP dependency change")
pytest.importorskip("networkx")
from heatplan.osm_reference import MetricProjection, RoadWay, ReferenceNetwork, closest_on_segment, model_interface


@pytest.fixture
def projection():
    return MetricProjection(106.242, 36.009, 32648)


def make_way(projection, wid, xy, refs=None, **tags):
    origin = projection.xy(projection.lon0, projection.lat0)
    ll = tuple(projection.lonlat(origin[0]+x, origin[1]+y) for x, y in xy)
    return RoadWay(wid, ll, {"highway": "residential", **tags}, tuple(refs) if refs is not None else None)


def building(projection, bid, x, y):
    ox, oy = projection.xy(projection.lon0, projection.lat0)
    lon, lat = projection.lonlat(ox+x, oy+y)
    return {"osm_id": bid, "centroid_lon": lon, "centroid_lat": lat, "building": "yes"}


def test_utm_matches_reference_zone_and_independent_geodesic(projection):
    a = (106.242, 36.009); b = (106.25, 36.012)
    p = projection.xy(*a); q = projection.xy(*b)
    length = hypot(q[0]-p[0], q[1]-p[1]); geodesic = projection.geodesic_m(a, b)
    assert abs(length/geodesic-1) < .001
    assert projection.lonlat(*p) == pytest.approx(a, abs=1e-10)
    with pytest.raises(ValueError): MetricProjection(106.242, 36.009, 32650)
    with pytest.raises(ValueError): projection.xy(float("nan"), 36)


def test_native_shared_midway_vertex_is_real_junction(projection):
    a = make_way(projection, "w1", [(0, 0), (100, 0), (100, 100)], [1, 2, 3])
    b = make_way(projection, "w2", [(100, 0), (200, 0)], [2, 4])
    net = ReferenceNetwork([a, b], projection, True)
    net.snap_buildings([building(projection, "w101", 0, 0), building(projection, "w102", 200, 0)])
    result = net.route("w101", "w102")
    assert result["road_length_m"] == pytest.approx(200, abs=1e-5)
    assert "n2" in result["node_ids"]
    assert len(net.segments) == 3  # Intermediate point is retained, not end-to-end chord.


def test_bent_way_length_uses_all_intermediate_points(projection):
    way = make_way(projection, "w1", [(0, 0), (100, 0), (100, 100)], [1, 2, 3])
    net = ReferenceNetwork([way], projection, True)
    net.snap_buildings([building(projection, "w101", 0, 0), building(projection, "w102", 100, 100)])
    r = net.route("w101", "w102")
    assert r["road_length_m"] == pytest.approx(200, abs=1e-5)
    assert r["straight_line_m"] == pytest.approx(2**.5*100, abs=1e-5)


def test_geometric_crossing_without_shared_ref_is_not_junction(projection):
    a = make_way(projection, "w1", [(-100, 0), (100, 0)], [1, 2])
    b = make_way(projection, "w2", [(0, -100), (0, 100)], [3, 4], layer="1", bridge="yes")
    net = ReferenceNetwork([a, b], projection, True)
    assert net.summary()["connected_components"] == 2
    assert net.graph.number_of_nodes() == 4


def test_same_coordinate_different_native_ids_do_not_merge(projection):
    a = make_way(projection, "w1", [(0, 0), (100, 0)], [1, 2])
    b = make_way(projection, "w2", [(100, 0), (200, 0)], [3, 4])
    assert ReferenceNetwork([a, b], projection, True).summary()["connected_components"] == 2


def test_bridge_endpoint_transition_has_shared_identity(projection):
    a = make_way(projection, "w1", [(0, 0), (100, 0)], [1, 2])
    b = make_way(projection, "w2", [(100, 0), (200, 0)], [2, 3], layer="1", bridge="yes")
    net = ReferenceNetwork([a, b], projection, True)
    assert net.summary()["connected_components"] == 1
    assert net.diagnostics[0]["type"] == "native_shared_endpoint_grade_transition"


def test_internal_grade_conflict_is_explicit_extra_admission_policy(projection):
    a = make_way(projection, "w1", [(-100, 0), (0, 0), (100, 0)], [1, 2, 3])
    b = make_way(projection, "w2", [(0, -100), (0, 0), (0, 100)], [4, 2, 5], layer="1", bridge="yes")
    net = ReferenceNetwork([a, b], projection, True)
    assert net.summary()["connected_components"] == 2
    assert net.summary()["not_identical_to_unfiltered_native_node_graph"]
    assert net.diagnostics[0]["type"] == "native_shared_internal_grade_conflict_isolated"


def test_fallback_preserves_exact_float_coordinates(projection):
    a = RoadWay("w1", ((106., 36.), (106.001, 36.)), {"highway": "residential"})
    b = RoadWay("w2", ((106.001+1e-10, 36.), (106.002, 36.)), {"highway": "residential"})
    net = ReferenceNetwork([a, b], projection, False)
    assert net.summary()["connected_components"] == 2
    assert net.summary()["fallback_default_grade_is_not_verified_same_elevation"]
    assert net.summary()["grade_unknown_way_ids"] == ["w1", "w2"]


def test_fallback_conflicting_known_layers_stay_separate(projection):
    a = make_way(projection, "w1", [(0, 0), (100, 0)], layer="0")
    b = make_way(projection, "w2", [(100, 0), (200, 0)], layer="1")
    assert ReferenceNetwork([a, b], projection, False).summary()["connected_components"] == 2


def test_closest_segment_clamp_and_degenerate():
    assert closest_on_segment((-10, 5), (0, 0), (100, 0))[0] == 0
    assert closest_on_segment((110, 5), (0, 0), (100, 0))[0] == 1
    assert closest_on_segment((20, 10), (0, 0), (100, 0)) == (.2, (20., 0.), 10.)
    assert closest_on_segment((3, 4), (0, 0), (0, 0)) == (0., (0, 0), 5.)


def test_multiple_snaps_same_segment_conserve_length(projection):
    net = ReferenceNetwork([make_way(projection, "w1", [(0, 0), (100, 0)], [1, 2])], projection, True)
    net.snap_buildings([building(projection, "w101", 20, 10), building(projection, "w102", 80, 10)])
    assert net.graph.number_of_edges() == 3
    assert net.summary()["road_total_after_split_m"] == pytest.approx(100, abs=1e-5)
    r = net.route("w101", "w102")
    assert r["road_length_m"] == pytest.approx(60, abs=1e-5)
    assert r["total_reference_length_m"] == pytest.approx(80, abs=1e-5)
    assert r["total_reference_length_m"] >= r["straight_line_m"]


def test_duplicate_centroids_keep_two_objects(projection):
    net = ReferenceNetwork([make_way(projection, "w1", [(0, 0), (100, 0)], [1, 2])], projection, True)
    net.snap_buildings([building(projection, "w101", 50, 0), building(projection, "w102", 50, 0)])
    r = net.route("w101", "w102")
    assert len(net.snaps) == 2
    assert r["road_length_m"] == 0
    assert r["total_reference_length_m"] < 1e-5
    assert r["detour_ratio_total_over_straight"] is None
    assert all(d["length_m"] > 0 for _, _, d in net.graph.edges(data=True))


def test_150m_threshold_and_failure_no_fabricated_route(projection):
    net = ReferenceNetwork([make_way(projection, "w1", [(0, 0), (100, 0)], [1, 2])], projection, True)
    net.snap_buildings([building(projection, "w101", 20, 149), building(projection, "w102", 80, 151)])
    assert net.snaps["w101"]["status"] == "snapped"
    assert net.snaps["w102"]["status"] == "snap_distance_exceeded"
    r = net.route("w101", "w102")
    assert r["status"] == "snap_not_available"
    assert "total_reference_length_m" not in r


def test_no_path_does_not_return_straight_line_as_route(projection):
    net = ReferenceNetwork([make_way(projection, "w1", [(0, 0), (100, 0)], [1, 2]),
                            make_way(projection, "w2", [(0, 200), (100, 200)], [3, 4])], projection, True)
    net.snap_buildings([building(projection, "w101", 20, 0), building(projection, "w102", 80, 200)])
    r = net.route("w101", "w102")
    assert r["status"] == "no_path" and r["straight_line_m"] > 0
    assert "total_reference_length_m" not in r and "road_geometry_xy_m" not in r


def test_input_order_stability(projection):
    ways = [make_way(projection, "w1", [(0, 0), (100, 0)], [1, 2]), make_way(projection, "w2", [(100, 0), (200, 0)], [2, 3])]
    buildings = [building(projection, "w101", 20, 10), building(projection, "w102", 180, 10)]
    a = ReferenceNetwork(ways, projection, True); b = ReferenceNetwork(ways[::-1], projection, True)
    assert a.snap_buildings(buildings) == b.snap_buildings(buildings[::-1])
    assert a.route("w101", "w102") == b.route("w101", "w102")


def test_restricted_access_excluded_unknown_retained(projection):
    ways = [make_way(projection, "w1", [(0, 0), (100, 0)], [1, 2]), make_way(projection, "w2", [(100, 0), (200, 0)], [2, 3], access="private")]
    net = ReferenceNetwork(ways, projection, True)
    assert len(net.segments) == 1
    assert net.summary()["unknown_access_way_ids"] == ["w1"]
    assert net.diagnostics[0]["type"] == "excluded_access"


def test_identity_geometry_validation_and_duplicate_ids(projection):
    with pytest.raises(ValueError): RoadWay("w1", ((106, 36), (106.1, 36)), {}, (1,))
    with pytest.raises(ValueError): RoadWay("bad", ((106, 36), (106.1, 36)), {})
    way = make_way(projection, "w1", [(0, 0), (100, 0)], [1, 2])
    with pytest.raises(ValueError): ReferenceNetwork([way, way], projection, True)
    with pytest.raises(ValueError): ReferenceNetwork([make_way(projection, "w2", [(0, 0), (100, 0)])], projection, True)
    bad = make_way(projection, "w2", [(101, 0), (200, 0)], [2, 3])
    with pytest.raises(ValueError): ReferenceNetwork([way, bad], projection, True)


def test_same_object_route_rejected_and_second_snap_rejected(projection):
    net = ReferenceNetwork([make_way(projection, "w1", [(0, 0), (100, 0)], [1, 2])], projection, True)
    buildings = [building(projection, "w101", 20, 10)]
    net.snap_buildings(buildings)
    with pytest.raises(ValueError): net.route("w101", "w101")
    with pytest.raises(ValueError): net.snap_buildings(buildings)


def test_empty_road_intake_is_explicit_failure(projection):
    net = ReferenceNetwork([], projection, False)
    net.snap_buildings([building(projection, "w101", 20, 0), building(projection, "w102", 80, 0)])
    assert net.snaps["w101"]["status"] == "no_eligible_road_segments"
    assert net.route("w101", "w102")["status"] == "snap_not_available"


def test_physical_parameters_are_empty_and_solver_admission_false(projection):
    b = building(projection, "w101", 20, 0)
    interface = model_interface([b], [{"anchor_osm_id": "w101", "target_osm_id": "w102", "status": "no_path"}])
    assert interface["eligible_for_engineering_solver_input"] is False
    assert interface["source_capacity_kw"] is None
    for key in ("confirmed_heating_use", "actual_heated_area_m2", "heat_loss_kw_per_c", "thermal_capacity_kwh_per_c", "demand_series"):
        assert interface["buildings"][0][key] is None
    for key in ("capacity_kw", "diameter_mm", "delivery_efficiency", "capex_total_yuan", "actual_pipe_length_m"):
        assert interface["candidate_connections"][0][key] is None


ARTIFACTS = Path(__file__).resolve().parents[1] / "docs/execution_20261002/spatial_reference"


@pytest.mark.parametrize("city", ["guyuan", "urumqi", "wuhan"])
def test_captured_cases_preserve_frozen_objects_native_pairing_and_lengths(city):
    path = ARTIFACTS / city
    if not (path / "result.json").exists(): pytest.skip("Case artifacts have not been built in this checkout")
    read = lambda filename: json.loads((path / filename).read_text(encoding="utf-8"))
    selection = read("selection.json"); intake = read("native_intake.json"); snaps = read("snaps.json"); routes = read("routes.json")
    assert len(selection["buildings"]) == 6
    assert selection["selection_depends_on_route_success"] is False
    assert [b["osm_id"] for b in selection["buildings"]] == [b["osm_id"] for b in sorted(selection["buildings"], key=lambda b:(b["distance_to_fixed_centre_m"], b["osm_id"]))]
    assert {s["osm_id"] for s in snaps} == {b["osm_id"] for b in selection["buildings"]}
    if intake["all_ways_native_complete"]:
        assert all(a["status"] == "complete_current_native" for a in intake["native_way_audit"])
        assert all(len(a["node_versions"]) > 0 for a in intake["native_way_audit"])
    connectivity = read("connectivity.json")
    assert connectivity["original_road_total_m"] == pytest.approx(connectivity["road_total_after_split_m"], abs=1e-5)
    for r in routes:
        if r["status"] == "routed":
            assert sum(e["length_m"] for e in r["edges"]) == pytest.approx(r["road_length_m"], abs=1e-5)
            assert r["road_length_m"]+r["anchor_connector_m"]+r["target_connector_m"] == pytest.approx(r["total_reference_length_m"], abs=1e-5)
            assert r["total_reference_length_m"]+1e-5 >= r["straight_line_m"]
        else: assert "total_reference_length_m" not in r
    assert read("model_interface.json")["eligible_for_engineering_solver_input"] is False
    assert (path / "map.png").stat().st_size > 10000
    assert (path / "map.pdf").stat().st_size > 10000
