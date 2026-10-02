"""Replay frozen OSM samples and export shared geometry-only candidate edges.

No downloads, solver calls, source-data writes or global dependency changes.
Historical reference artifacts are verified before and after the run.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
REFERENCE_ROOT = ROOT / "docs/execution_20261002/spatial_reference"
OUTPUT_ROOT = ROOT / "docs/implementation_20261002/spatial"
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))
if importlib.util.find_spec("pyproj") is None:
    sys.path.append(str(REFERENCE_ROOT / "_runtime_packages"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from heatplan.osm_reference import MetricProjection, ReferenceNetwork, RoadWay, grade_signature
from heatplan.spatial_adapter import adapt_reference_network, validate_candidate_network

CITIES = ("guyuan", "urumqi", "wuhan")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def write_csv(path, records, fields):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(records)


def snapshot(reference):
    return {str(p.relative_to(reference)).replace("\\", "/"): sha(p)
            for p in sorted(reference.rglob("*")) if p.is_file()
            and not any(part in {"_runtime_packages", "__pycache__", ".pytest_cache"} for part in p.parts)}


def original_input_status(reference):
    baseline = read(reference / "input_hashes_before.json")["files"]
    verified, missing = {}, []
    for filename, expected in baseline.items():
        path = Path(filename)
        if not path.exists():
            missing.append(filename)
            continue
        actual = {"bytes": path.stat().st_size, "sha256": sha(path)}
        if actual != expected:
            raise ValueError(f"Original input no longer matches its frozen baseline: {path}")
        verified[filename] = actual
    return {"verified": verified, "missing": missing, "expected_count": len(baseline)}


def replay_city(reference, city):
    dest = reference / city
    selection = read(dest / "selection.json")
    intake = read(dest / "native_intake.json")
    if not intake["all_ways_native_complete"]:
        raise ValueError(f"{city}: complete frozen native-node evidence required; no hybrid or invented fallback")
    receipts = read(dest / "native_intake_receipts.json")
    elements, files = {"way": {}, "node": {}}, []
    for receipt in receipts:
        if receipt.get("status") != "success":
            raise ValueError("Frozen native receipt is not successful")
        raw = (reference / receipt["raw_file"]).resolve()
        if not raw.is_relative_to(reference.resolve()) or sha(raw) != receipt["sha256"]:
            raise ValueError("Frozen native response identity or checksum mismatch")
        files.append({"file": receipt["raw_file"], "sha256": receipt["sha256"],
                      "url": receipt["url"], "capture_utc": receipt["utc"]})
        for item in read(raw)["elements"]:
            kind = item["type"]
            if kind in elements:
                previous = elements[kind].get(item["id"])
                if previous is not None and previous != item:
                    raise ValueError("Conflicting native versions in frozen response batch")
                elements[kind][item["id"]] = item
    audit = {r["osm_id"]: r for r in intake["native_way_audit"]}
    excluded = {r["osm_id"] for r in intake["excluded_ways"]}
    ways = []
    for road in selection["roads"]:
        wid = road["osm_id"]
        if wid in excluded:
            continue
        item = elements["way"][int(wid[1:])]
        refs = tuple(item["nodes"])
        coords = tuple((elements["node"][n]["lon"], elements["node"][n]["lat"]) for n in refs)
        if item.get("version") != audit[wid]["way_version"] or item.get("timestamp") != audit[wid]["way_timestamp"]:
            raise ValueError("Native way version differs from frozen audit")
        for nid in refs:
            node = elements["node"][nid]
            if node.get("version") != audit[wid]["node_versions"][str(nid)] or node.get("timestamp") != audit[wid]["node_timestamps"][str(nid)]:
                raise ValueError("Native node version differs from frozen audit")
        ways.append(RoadWay(wid, coords, item.get("tags", {}), refs, "current_native_OSM_multiple_GETs"))
    projection = MetricProjection(*selection["centre_lonlat"], selection["epsg"])
    network = ReferenceNetwork(ways, projection, True)
    snaps = network.snap_buildings(selection["buildings"], selection["rules"]["max_snap_distance_m"])
    anchor = selection["anchor_osm_id"]
    routes = [network.route(anchor, b["osm_id"]) for b in selection["buildings"] if b["osm_id"] != anchor]
    old_routes = {(r["anchor_osm_id"], r["target_osm_id"]): r for r in read(dest / "routes.json")}
    if len(old_routes) != len(routes):
        raise ValueError("Frozen route count changed")
    for route in routes:
        previous = old_routes[(route["anchor_osm_id"], route["target_osm_id"])]
        if previous["status"] != route["status"]:
            raise ValueError("Reference reachability changed; requires a new sourced sample version")
        if route["status"] == "routed" and abs(previous["total_reference_length_m"] - route["total_reference_length_m"]) > 1e-5:
            raise ValueError("Frozen route length did not reproduce")
    old_snaps = {s["osm_id"]: s for s in read(dest / "snaps.json")}
    for snap in snaps:
        previous = old_snaps[snap["osm_id"]]
        if previous["status"] != snap["status"] or abs(previous.get("distance_m", 0) - snap.get("distance_m", 0)) > 1e-5:
            raise ValueError("Frozen snap status/distance changed")
    segments = {s["segment_id"]: s for s in network.segments}
    by_way = {w.osm_id: w for w in ways}
    graph_edges = []
    for u, v, key, attributes in network.graph.edges(keys=True, data=True):
        segment = segments[attributes["segment_id"]]
        way, idx = by_way[segment["osm_id"]], segment["segment_index"]
        native = elements["way"][int(way.osm_id[1:])]
        record = {"way_version": native["version"], "way_timestamp": native["timestamp"],
                  "original_segment_native_node_refs": list(way.node_refs[idx:idx+2]),
                  "native_node_versions": {str(n): elements["node"][n]["version"] for n in way.node_refs[idx:idx+2]},
                  "traffic_oneway": way.tags.get("oneway"), "access": way.tags.get("access"),
                  "excavation_permission_verified": False}
        graph_edges.append({"u": u, "v": v, "edge_key": key, **attributes,
                            "grade_signature": list(grade_signature(way.tags)), "source_record": record})
    reference_network = {"native": True,
                         "nodes": [{"node_id": nid, **attributes} for nid, attributes in network.graph.nodes(data=True)],
                         "edges": graph_edges}
    provenance = {"reference_kind": "frozen_OSM_native_node_corridor_reference",
                  "metric_epsg": selection["epsg"], "selection_frozen_utc": selection["frozen_utc"],
                  "selection_file": f"{city}/selection.json", "selection_sha256": sha(dest / "selection.json"),
                  "native_audit_file": f"{city}/native_intake.json", "native_audit_sha256": sha(dest / "native_intake.json"),
                  "native_response_files": files, "reference_root_project_path": str(reference.relative_to(ROOT)).replace("\\", "/"),
                  "native_API_is_not_atomic_snapshot": True,
                  "identity_valid_for": "same frozen native versions and snap batch; changed splits create a new geometry version",
                  "heat_source_location_verified": False, "physical_pipe_properties_verified": False}
    candidate = adapt_reference_network(reference_network, selection["buildings"], snaps, routes,
                                        namespace=city, geometry_provenance=provenance,
                                        max_snap_distance_m=selection["rules"]["max_snap_distance_m"])
    candidate["reference_replay"] = {"unchanged_statuses": True, "route_length_tolerance_m": 1e-5,
                                    "selected_buildings": len(selection["buildings"]),
                                    "native_road_ways": len(ways), "network_summary": network.summary()}
    return candidate


def export_city(output, city, candidate):
    dest = output / city
    dump(dest / "candidate_network.json", candidate)
    dump(dest / "candidate_rules.json", {"candidate_rules": candidate["candidate_rules"],
                                       "geometry_manifest_id": candidate["geometry_manifest_id"],
                                       "eligible_for_engineering_solver_input": False})
    dump(dest / "failures.json", candidate["failed_paths"])
    write_csv(dest / "candidate_edges.csv", candidate["candidate_edges"],
              ["identity", "from_node", "to_node", "kind", "route_length_m", "bidirectional", "use_count", "physical_edge_id", "route_length_basis", "engineering_admitted"])
    incidence = [{"path_id": p["path_id"], "order": i, **step}
                 for p in candidate["paths"] for i, step in enumerate(p["traversals"])]
    write_csv(dest / "path_edge_incidence.csv", incidence, ["path_id", "order", "edge_identity", "direction"])
    projection = MetricProjection(*read(REFERENCE_ROOT / city / "selection.json")["centre_lonlat"], candidate["geometry_provenance"]["metric_epsg"])
    dump(dest / "candidate_edges.geojson", {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {k: e[k] for k in ("identity", "kind", "route_length_m", "use_count", "physical_edge_id", "engineering_admitted")},
         "geometry": {"type": "LineString", "coordinates": [projection.lonlat(*xy) for xy in e["geometry_xy_m"]]}}
        for e in candidate["candidate_edges"]]})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT_ROOT)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(OUTPUT_ROOT.resolve()):
        raise ValueError("Output must stay within the owned implementation spatial directory")
    before = snapshot(REFERENCE_ROOT)
    original_before = original_input_status(REFERENCE_ROOT)
    started = time.perf_counter()
    cases = []
    for city in CITIES:
        candidate = replay_city(REFERENCE_ROOT, city)
        candidate["validation"] = validate_candidate_network(candidate)
        export_city(output, city, candidate)
        cases.append({"city": city, **candidate["validation"], "selected_buildings": len(candidate["buildings"]),
                      "native_road_ways": candidate["reference_replay"]["native_road_ways"]})
        print(json.dumps(cases[-1], ensure_ascii=False))
    after = snapshot(REFERENCE_ROOT)
    if before != after:
        raise RuntimeError("Historical geometry reference artifacts changed")
    original_after = original_input_status(REFERENCE_ROOT)
    if original_before != original_after:
        raise RuntimeError("Original inputs changed during geometry replay")
    result = {"status": "geometry_adapter_verified_engineering_admission_pending", "cases": cases,
              "historical_reference_files_checked": len(before), "historical_reference_unchanged": True,
              "original_input_files_verified": len(original_before["verified"]),
              "original_input_files_missing": original_before["missing"],
              "original_inputs_verified_unchanged": not original_before["missing"],
              "original_data_read_only": True, "new_network_requests": 0,
              "total_geometry_runtime_seconds": time.perf_counter() - started,
              "model_experiments_run": False, "github_upload": False}
    dump(output / "results.json", result)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    dump(output / "runs" / f"{stamp}.json", {**result, "run_utc": stamp,
         "code_sha256": {"src/heatplan/spatial_adapter.py": sha(ROOT / "src/heatplan/spatial_adapter.py"),
                         "scripts/build_shared_osm_candidates.py": sha(Path(__file__))},
         "historical_reference_sha256": before})
    print(f"Completed: {output}")


if __name__ == "__main__":
    main()
