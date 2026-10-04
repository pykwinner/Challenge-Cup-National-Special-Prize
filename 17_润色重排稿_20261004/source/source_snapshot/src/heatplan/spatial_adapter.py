"""Map geometry-reference paths to a single, shared candidate corridor graph.

This dependency-free interface supplies geometry, identity and directed path
incidence only. It does not certify heating assets, excavation rights, pipe
performance or cost. Thermal model admission remains a separate step.
"""
from __future__ import annotations

from collections import Counter, defaultdict, deque
from copy import deepcopy
import hashlib
import json
from math import hypot, isfinite


PHYSICAL_FIELDS = (
    "actual_pipe_length_m", "diameter_mm", "capacity_kw", "delivery_efficiency",
    "mass_flow_kg_s", "pump_power_kw", "capex_total_yuan", "capex_yuan_per_m",
)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _xy(value):
    if len(value) != 2 or any(not isfinite(float(v)) for v in value):
        raise ValueError("Geometry requires two finite metric coordinates")
    return [float(v) for v in value]


def _close(a, b):
    return abs(a - b) <= max(1e-5, abs(a) * 1e-9, abs(b) * 1e-9)


def _edge_key(u, v, key):
    return (*sorted((u, v)), key)


def adapt_reference_network(reference_network, buildings, snaps, routes, *,
                            namespace, geometry_provenance, max_snap_distance_m=150.0):
    """Return unique candidate edges and ordered, signed path-to-edge mappings.

    ``reference_network`` uses ReferenceNetwork's exported nodes/edges schema.
    Each road edge may include ``grade_signature`` and ``source_record``.
    Equal endpoint identities and grade context identify one geometric road
    corridor, including duplicate OSM representations. Geometric crossings and
    distinct native node IDs are never merged. Snap nodes are kept explicitly.
    IDs are stable for the same frozen source versions and batch of snap points;
    a changed split topology is a new geometry version, not a persistent asset.
    Only edges used by successful reference paths enter the candidate union.
    """
    if not isinstance(namespace, str) or not namespace.strip():
        raise ValueError("A nonempty case namespace is required")
    if not isinstance(geometry_provenance, dict) or not geometry_provenance:
        raise ValueError("Geometry provenance is required")
    if not isfinite(max_snap_distance_m) or max_snap_distance_m < 0:
        raise ValueError("Invalid snap threshold")
    provenance = deepcopy(geometry_provenance)
    manifest_id = _digest(provenance)
    nodes = {}
    for node in reference_network["nodes"]:
        nid = node["node_id"]
        if nid in nodes:
            raise ValueError("Duplicate reference node identity")
        nodes[nid] = {"node_id": nid, "xy_m": _xy([node["x_m"], node["y_m"]]),
                      "node_kind": "native_OSM" if nid.startswith("n") else "reference_split",
                      "native_node_identity": nid if nid.startswith("n") else None}
    raw_edges = {}
    adjacency = defaultdict(set)
    for edge in reference_network["edges"]:
        u, v = edge["u"], edge["v"]
        if u not in nodes or v not in nodes or u == v or edge.get("kind") != "road":
            raise ValueError("Invalid road edge endpoints or kind")
        length = float(edge["length_m"])
        if not isfinite(length) or length <= 0:
            raise ValueError("Road length must be finite and positive")
        a, b = nodes[u]["xy_m"], nodes[v]["xy_m"]
        if not _close(length, hypot(b[0] - a[0], b[1] - a[1])):
            raise ValueError("Road edge geometry and length disagree")
        key = _edge_key(u, v, edge["edge_key"])
        if key in raw_edges:
            raise ValueError("Duplicate exported road edge")
        raw_edges[key] = edge
        adjacency[u].add(v); adjacency[v].add(u)
    by_building = {b["osm_id"]: b for b in buildings}
    by_snap = {s["osm_id"]: s for s in snaps}
    if len(by_building) != len(buildings) or len(by_snap) != len(snaps):
        raise ValueError("Duplicate building or snap identity")
    if set(by_building) != set(by_snap):
        raise ValueError("Every frozen building needs a snap outcome")
    terminals = {}
    for bid, snap in by_snap.items():
        _xy(snap["point_xy_m"])
        if snap["status"] == "snapped":
            distance = float(snap["distance_m"])
            if not isfinite(distance) or distance < 0 or distance > max_snap_distance_m:
                raise ValueError("Successful snap violates the frozen distance threshold")
            nid = snap["snap_node"]
            if nid not in nodes:
                raise ValueError("Snap node absent from native/split graph")
            p, q = _xy(snap["point_xy_m"]), nodes[nid]["xy_m"]
            if not _close(distance, hypot(p[0] - q[0], p[1] - q[1])):
                raise ValueError("Snap connector geometry and length disagree")
            terminals[bid] = nid if distance <= 1e-8 else f"building:{namespace}:{bid}"
            if terminals[bid] != nid:
                nodes[terminals[bid]] = {"node_id": terminals[bid], "xy_m": p,
                                       "node_kind": "building_centroid_reference", "native_node_identity": None}
        elif snap["status"] == "snap_distance_exceeded":
            distance = float(snap["distance_m"])
            if not isfinite(distance) or distance <= max_snap_distance_m:
                raise ValueError("Failed snap does not exceed its declared threshold")
        elif snap["status"] != "no_eligible_road_segments":
            raise ValueError("Unknown snap status")

    candidates, paths, failures = {}, [], []
    seen_paths = set()

    def candidate(u, v, length, kind, grade, source_record):
        left, right = sorted((u, v))
        basis = {"namespace": namespace, "geometry_manifest_id": manifest_id,
                 "kind": kind, "endpoints": [left, right],
                 "grade_signature": list(grade)}
        cid = f"corridor:{namespace}:{_digest(basis)}"
        geometry = [nodes[left]["xy_m"], nodes[right]["xy_m"]]
        if cid not in candidates:
            candidates[cid] = {
                "name": cid, "identity": cid, "identity_details": basis,
                "from_node": left, "to_node": right, "route_length_m": float(length),
                "kind": kind, "bidirectional": True, "geometry_xy_m": geometry,
                "geometry_provenance": {
                    "manifest_id": manifest_id, "metric_epsg": provenance.get("metric_epsg"),
                    "source_kind": "native_OSM_road_corridor" if kind == "road" and reference_network.get("native")
                                   else "reference_centroid_to_road" if kind == "building_reference_connector"
                                   else "CSV_geometry_reference",
                    "source_records": [], "geometry_only": True,
                    "grade_context_is_not_verified_elevation": True,
                },
                "path_ids": [], "directions_used": [], "traversal_count": 0, "use_count": 0,
                "engineering_admitted": False, "connection_allowed": None,
                "actual_heating_asset_identity": None,
                "physical_parameters": {field: None for field in PHYSICAL_FIELDS},
            }
        edge = candidates[cid]
        if not _close(edge["route_length_m"], length) or edge["geometry_xy_m"] != geometry:
            raise ValueError("A shared corridor identity has conflicting geometry")
        record = deepcopy(source_record)
        if record not in edge["geometry_provenance"]["source_records"]:
            edge["geometry_provenance"]["source_records"].append(record)
        return {"edge_identity": cid, "direction": 1 if (u, v) == (left, right) else -1}

    def connector(bid, outward):
        snap = by_snap[bid]
        if terminals[bid] == snap["snap_node"]:
            return []
        nid = terminals[bid]
        nodes[nid] = {"node_id": nid, "xy_m": _xy(snap["point_xy_m"]),
                      "node_kind": "building_centroid_reference", "native_node_identity": None}
        u, v = (nid, snap["snap_node"]) if outward else (snap["snap_node"], nid)
        return [candidate(u, v, float(snap["distance_m"]), "building_reference_connector", (),
                          {"building_osm_id": bid, "snap_node": snap["snap_node"],
                           "entrance_verified": False, "excavation_right_verified": False})]

    def connected(u, v):
        seen, queue = {u}, deque([u])
        while queue:
            current = queue.popleft()
            if current == v:
                return True
            for nxt in sorted(adjacency[current] - seen):
                seen.add(nxt); queue.append(nxt)
        return False

    for route in sorted(routes, key=lambda r: (r["anchor_osm_id"], r["target_osm_id"])):
        anchor, target = route["anchor_osm_id"], route["target_osm_id"]
        if anchor == target or anchor not in by_building or target not in by_building:
            raise ValueError("Reference path requires two known, distinct buildings")
        pid = f"path:{namespace}:{anchor}:{target}"
        if pid in seen_paths:
            raise ValueError("Duplicate directed reference path")
        seen_paths.add(pid)
        common = {"path_id": pid, "anchor_osm_id": anchor, "target_osm_id": target,
                  "status": route["status"], "anchor_is_not_heat_source": True}
        both_snapped = anchor in terminals and target in terminals
        if route["status"] != "routed":
            if route["status"] == "snap_not_available" and not both_snapped:
                pass
            elif route["status"] == "no_path" and both_snapped and not connected(
                    by_snap[anchor]["snap_node"], by_snap[target]["snap_node"]):
                pass
            else:
                raise ValueError("Failure status conflicts with captured topology or snaps")
            failures.append({**common, "anchor_snap_status": by_snap[anchor]["status"],
                             "target_snap_status": by_snap[target]["status"],
                             "anchor_snap_distance_m": by_snap[anchor].get("distance_m"),
                             "target_snap_distance_m": by_snap[target].get("distance_m"),
                             "traversals": [], "route_length_m": None})
            continue
        if not both_snapped:
            raise ValueError("Successful route contains a failed snap")
        path_nodes = route["node_ids"]
        if not path_nodes or path_nodes[0] != by_snap[anchor]["snap_node"] or path_nodes[-1] != by_snap[target]["snap_node"]:
            raise ValueError("Path endpoints do not match building snap nodes")
        if len(route["edges"]) != len(path_nodes) - 1 or len(set(path_nodes)) != len(path_nodes):
            raise ValueError("Path contains missing edges or a cycle")
        traversals = connector(anchor, True)
        road_length = 0.0
        for (u, v), step in zip(zip(path_nodes, path_nodes[1:]), route["edges"]):
            if (step["u"], step["v"]) != (u, v):
                raise ValueError("Route edge order disagrees with its node path")
            edge = raw_edges.get(_edge_key(u, v, step["edge_key"]))
            if edge is None or edge["segment_id"] != step["segment_id"] or edge["osm_id"] != step["osm_id"] or not _close(float(edge["length_m"]), float(step["length_m"])):
                raise ValueError("Route references an absent or altered native road edge")
            grade = edge.get("grade_signature", ["unknown", "unknown", "unknown"])
            record = {"osm_way_id": edge["osm_id"], "original_segment_id": edge["segment_id"],
                      "split_edge_key": edge["edge_key"], **deepcopy(edge.get("source_record", {}))}
            traversals.append(candidate(u, v, float(edge["length_m"]), "road", grade, record))
            road_length += float(edge["length_m"])
        traversals += connector(target, False)
        total = sum(candidates[step["edge_identity"]]["route_length_m"] for step in traversals)
        if not _close(road_length, float(route["road_length_m"])) or not _close(total, float(route["total_reference_length_m"])):
            raise ValueError("Mapped path length differs from captured full reference route")
        if len({s["edge_identity"] for s in traversals}) != len(traversals):
            raise ValueError("A path traverses the same candidate corridor twice")
        paths.append({**common, "from_node": terminals[anchor], "to_node": terminals[target],
                      "road_length_m": road_length, "route_length_m": total,
                      "traversals": traversals})
        for step in traversals:
            edge = candidates[step["edge_identity"]]
            edge["path_ids"].append(pid); edge["directions_used"].append(step["direction"])
            edge["traversal_count"] += 1
    for edge in candidates.values():
        edge["path_ids"] = sorted(set(edge["path_ids"]))
        edge["use_count"] = len(edge["path_ids"])
        edge["directions_used"] = sorted(set(edge["directions_used"]))
        edge["geometry_provenance"]["source_records"].sort(key=_digest)
        edge["physical_edge_id"] = edge["identity"]
        edge["route_length_basis"] = ("projected_native_road_segment" if edge["kind"] == "road" and reference_network.get("native")
                                      else "projected_reference_connector" if edge["kind"] == "building_reference_connector"
                                      else "projected_CSV_reference_segment")
        edge["route_provenance"] = deepcopy(edge["geometry_provenance"])
    used_nodes = {n for e in candidates.values() for n in (e["from_node"], e["to_node"])}
    used_nodes.update(terminals.values())
    used_nodes.update(s["snap_node"] for s in by_snap.values() if s["status"] == "snapped")
    result = {
        "schema": "shared_candidate_corridors_v1", "namespace": namespace,
        "scope": "geometry_reference_only_not_actual_heating_network",
        "eligible_for_engineering_solver_input": False,
        "source_capacity_kw": None, "costs_verified": False,
        "road_direction_policy": "bidirectional_candidate_corridor; traffic oneway is not thermal flow direction",
        "max_snap_distance_m": max_snap_distance_m,
        "geometry_manifest_id": manifest_id, "geometry_provenance": provenance,
        "nodes": [nodes[n] for n in sorted(used_nodes)],
        "buildings": [{"osm_id": bid, "snap_status": by_snap[bid]["status"],
                       "terminal_reference_node": terminals.get(bid),
                       "snap_distance_m": by_snap[bid].get("distance_m"),
                       "confirmed_heating_use": None, "actual_heated_area_m2": None,
                       "heat_loss_kw_per_c": None, "thermal_capacity_kwh_per_c": None,
                       "demand_series": None} for bid in sorted(by_building)],
        "candidate_edges": [candidates[cid] for cid in sorted(candidates)],
        "candidate_rules": [{"pipe": cid, "route_length_m": candidates[cid]["route_length_m"],
                                  "route_length_basis": candidates[cid]["route_length_basis"],
                                  "route_provenance": deepcopy(candidates[cid]["route_provenance"]),
                                  "physical_edge_id": cid,
                                  "connection_allowed": None}
                            for cid in sorted(candidates)],
        "paths": paths, "failed_paths": failures,
    }
    result["validation"] = validate_candidate_network(result)
    return result


def validate_candidate_network(data):
    """Independently recompute signed incidence, union length and path totals."""
    if data["geometry_manifest_id"] != _digest(data["geometry_provenance"]):
        raise ValueError("Geometry manifest identity does not match its provenance")
    edges = {e["identity"]: e for e in data["candidate_edges"]}
    nodes = {n["node_id"]: n for n in data["nodes"]}
    if len(edges) != len(data["candidate_edges"]) or len(nodes) != len(data["nodes"]):
        raise ValueError("Duplicate candidate or node identity")
    rules = {r["pipe"]: r for r in data["candidate_rules"]}
    if len(rules) != len(data["candidate_rules"]) or set(rules) != set(edges):
        raise ValueError("Candidate rules must cover each unique edge exactly once")
    usage, directions, seen_paths = defaultdict(set), defaultdict(set), set()
    counts = Counter()
    road_sum = path_sum = 0.0
    for edge in edges.values():
        u, v, length = edge["from_node"], edge["to_node"], float(edge["route_length_m"])
        if u >= v or u not in nodes or v not in nodes or not edge["bidirectional"]:
            raise ValueError("Candidate requires canonical endpoints and bidirectional incidence")
        if not isfinite(length) or length <= 0 or edge["name"] != edge["identity"]:
            raise ValueError("Invalid candidate identity or length")
        basis = edge["identity_details"]
        expected = f"corridor:{data['namespace']}:{_digest(basis)}"
        if (edge["identity"] != expected or basis["namespace"] != data["namespace"]
                or basis["geometry_manifest_id"] != data["geometry_manifest_id"]
                or basis["endpoints"] != [u, v] or basis["kind"] != edge["kind"]):
            raise ValueError("Candidate identity does not match its frozen geometry basis")
        rule = rules[edge["identity"]]
        if (edge["physical_edge_id"] != edge["identity"] or rule["physical_edge_id"] != edge["identity"]
                or not _close(float(rule["route_length_m"]), length)
                or rule["route_length_basis"] != edge["route_length_basis"]
                or not rule["route_provenance"] or rule["route_provenance"] != edge["route_provenance"]):
            raise ValueError("Candidate rule geometry fields disagree with the unique edge")
        geometry = [_xy(p) for p in edge["geometry_xy_m"]]
        if geometry != [nodes[u]["xy_m"], nodes[v]["xy_m"]]:
            raise ValueError("Candidate geometry endpoints differ from its nodes")
        if not _close(length, sum(hypot(b[0]-a[0], b[1]-a[1]) for a, b in zip(geometry, geometry[1:]))):
            raise ValueError("Candidate length differs from geometry")
    for path in data["paths"]:
        pid = path["path_id"]
        if pid in seen_paths or path["status"] != "routed":
            raise ValueError("Duplicate or unsuccessful active path")
        seen_paths.add(pid)
        cursor, total, road = path["from_node"], 0.0, 0.0
        for step in path["traversals"]:
            edge = edges.get(step["edge_identity"])
            if edge is None or type(step["direction"]) is not int or step["direction"] not in {-1, 1}:
                raise ValueError("Unknown candidate edge or traversal direction")
            u, v = edge["from_node"], edge["to_node"]
            if step["direction"] == -1:
                u, v = v, u
            if cursor != u or pid in usage[edge["identity"]]:
                raise ValueError("Broken path incidence or repeated candidate")
            cursor = v; total += edge["route_length_m"]
            road += edge["route_length_m"] if edge["kind"] == "road" else 0
            usage[edge["identity"]].add(pid); directions[edge["identity"]].add(step["direction"])
            counts[edge["identity"]] += 1
        if cursor != path["to_node"] or not _close(total, path["route_length_m"]) or not _close(road, path["road_length_m"]):
            raise ValueError("Path endpoints or reconstructed length disagree")
        path_sum += total; road_sum += road
    for failure in data["failed_paths"]:
        if failure["path_id"] in seen_paths or failure["traversals"] or failure["route_length_m"] is not None:
            raise ValueError("Failed reference path must not receive candidate traversals")
        seen_paths.add(failure["path_id"])
    for cid, edge in edges.items():
        if not usage[cid] or sorted(usage[cid]) != edge["path_ids"] or len(usage[cid]) != edge["use_count"] or counts[cid] != edge["traversal_count"] or sorted(directions[cid]) != edge["directions_used"]:
            raise ValueError("Shared candidate usage ledger disagrees with path incidence")
    unique_road = sum(e["route_length_m"] for e in edges.values() if e["kind"] == "road")
    unique_connectors = sum(e["route_length_m"] for e in edges.values() if e["kind"] == "building_reference_connector")
    return {"passed": True, "unique_candidate_edges": len(edges),
            "unique_road_edges": sum(e["kind"] == "road" for e in edges.values()),
            "unique_reference_connectors": sum(e["kind"] == "building_reference_connector" for e in edges.values()),
            "shared_road_edges": sum(e["kind"] == "road" and e["use_count"] > 1 for e in edges.values()),
            "both_directions_used_edges": sum(len(e["directions_used"]) == 2 for e in edges.values()),
            "successful_paths": len(data["paths"]), "failed_paths": len(data["failed_paths"]),
            "unique_road_length_m": unique_road, "unique_reference_connector_length_m": unique_connectors,
            "unique_candidate_route_length_m": unique_road + unique_connectors,
            "sum_individual_road_route_length_m": road_sum,
            "sum_individual_route_length_m": path_sum,
            "repeated_length_if_paths_built_independently_m": path_sum - unique_road - unique_connectors,
            "interpretation": "geometric reuse only; no verified construction savings or heat-pipe capacity"}
