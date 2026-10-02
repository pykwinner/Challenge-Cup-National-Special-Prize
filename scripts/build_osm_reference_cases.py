"""Freeze and build three small geometry-only OSM reference cases.

Read-only CSV/native OSM intake; no solver, physical assumptions, or Git actions.
Run with --offline to reproduce from the frozen selections and captured responses.
The optional isolated dependency directory is ignored and is not a deliverable.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from math import hypot, isfinite
from pathlib import Path
import re
import sys
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "docs/execution_20261002/spatial_reference"
# Prefer the user's installed environment. Never install or alter global packages.
if importlib.util.find_spec("pyproj") is None:
    sys.path.append(str(DEFAULT_OUT / "_runtime_packages"))
sys.path.insert(0, str(ROOT / "src"))
sys.dont_write_bytecode = True
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import networkx as nx
import numpy as np
from shapely import wkt
from shapely.geometry import Point, MultiPoint, Polygon, MultiPolygon, LineString, mapping
from shapely.ops import transform
from heatplan.osm_reference import MetricProjection, ReferenceNetwork, RoadWay, model_interface

CITIES = {"guyuan": ("固原", 106.2420, 36.0090, 32648),
          "urumqi": ("乌鲁木齐", 87.6170, 43.8250, 32645),
          "wuhan": ("武汉", 114.3000, 30.5900, 32650)}
BUILDING_TYPES = {"yes", "residential", "apartments", "house", "detached", "semidetached_house",
                  "terrace", "office", "school", "university", "hospital", "commercial", "retail",
                  "public", "public_building", "dormitory", "hotel"}
ROAD_TYPES = {"residential", "service", "living_street", "unclassified", "tertiary", "secondary", "primary",
              "tertiary_link", "secondary_link", "primary_link"}
PROTOCOL = {"window_radius_m": 1500.0, "corridor_buffer_m": 400.0, "building_count": 6,
            "max_snap_distance_m": 150.0, "building_types": sorted(BUILDING_TYPES), "road_types": sorted(ROAD_TYPES),
            "sort": "projected centre distance ascending, typed OSM ID lexicographic tie-break",
            "anchor": "first selected building; reference only, not heat source",
            "all_intermediate_road_vertices": True, "insert_geometry_crossings": False,
            "road_direction": "undirected candidate corridor; traffic oneway not a pipe direction",
            "way_batch_limit": 100, "node_batch_limit": 500, "request_attempt_limit": 3}


def utc():
    return datetime.now(timezone.utc).isoformat()


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024*1024), b""): h.update(block)
    return h.hexdigest()


def rows(path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        yield from csv.DictReader(f)


def status(out, stage, **details):
    p = out / "status.json"
    old = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    old.update(stage=stage, state="running", updated_utc=utc(), github_upload=False, milp_run=False, **details)
    dump(p, old)
    print(json.dumps({"stage": stage, **details}, ensure_ascii=False), flush=True)


def boundary(path):
    grouped = defaultdict(lambda: defaultdict(list))
    for r in rows(path):
        grouped[r["polygon_id"]][(r["ring_id"], r["role"])].append((int(r["vertex_id"]), float(r["lon"]), float(r["lat"])))
    polygons = []
    for rings in grouped.values():
        outer = []; holes = []
        for (_, role), points in rings.items():
            coords = [(a, b) for _, a, b in sorted(points)]
            if role == "outer": outer = coords
            elif role == "inner": holes.append(coords)
            else: raise ValueError(f"Unrecognized boundary ring role {role}")
        if not outer: raise ValueError("Boundary missing outer ring")
        polygons.append(Polygon(outer, holes))
    result = MultiPolygon(polygons)
    if not result.is_valid: raise ValueError("Invalid supplied boundary")
    return result


def finite_value(value):
    try:
        x = float(value)
        return x if isfinite(x) else None
    except (TypeError, ValueError): return None


def freeze(city, source, out):
    label, lon, lat, epsg = CITIES[city]
    p = out / city / "selection.json"
    source_hash = {kind: sha(source / f"{city}_{kind}.csv") for kind in ("boundary", "buildings", "roads")}
    if p.exists():
        selected = json.loads(p.read_text(encoding="utf-8"))
        if selected["source_csv_sha256"] != source_hash or selected["rules"] != PROTOCOL:
            raise ValueError("Frozen source/rules changed; use an explicitly new output directory")
        return selected
    projection = MetricProjection(lon, lat, epsg)
    centre = projection.xy(lon, lat)
    window = Point(centre).buffer(PROTOCOL["window_radius_m"])
    bound = boundary(source / f"{city}_boundary.csv")
    rejection = Counter(); eligible = []
    for r in rows(source / f"{city}_buildings.csv"):
        bid = r["osm_id"]
        coords = [finite_value(r[k]) for k in ("centroid_lon", "centroid_lat", "footprint_area_m2")]
        if not re.fullmatch(r"w[1-9][0-9]*", bid): rejection["invalid_way_id"] += 1; continue
        if None in coords or coords[2] <= 0: rejection["invalid_coordinate_or_footprint"] += 1; continue
        blon, blat, area = coords
        if not (-180 <= blon <= 180 and -90 <= blat <= 90): rejection["invalid_coordinate_or_footprint"] += 1; continue
        xy = projection.xy(blon, blat); d = hypot(xy[0]-centre[0], xy[1]-centre[1])
        if d > PROTOCOL["window_radius_m"]: rejection["outside_fixed_window"] += 1; continue
        if not bound.covers(Point(blon, blat)): rejection["outside_supplied_boundary"] += 1; continue
        if r["building"] not in BUILDING_TYPES: rejection["building_type_excluded"] += 1; continue
        eligible.append({"osm_id": bid, "building": r["building"], "name": r["name"], "centroid_lon": blon,
                         "centroid_lat": blat, "footprint_area_m2": area, "easting_m": xy[0], "northing_m": xy[1],
                         "distance_to_fixed_centre_m": d, "source_floor_area_not_admitted": r.get("est_floor_area_m2"),
                         "heating_use_confirmed": False, "generic_yes_use_unknown": r["building"] == "yes"})
    eligible.sort(key=lambda r: (r["distance_to_fixed_centre_m"], r["osm_id"]))
    buildings = eligible[:PROTOCOL["building_count"]]
    if not buildings: raise ValueError(f"No buildings admitted in fixed {city} window")
    corridor = MultiPoint([(b["easting_m"], b["northing_m"]) for b in buildings]).convex_hull.buffer(PROTOCOL["corridor_buffer_m"])
    selected_roads = []; background = []; road_rejection = Counter()
    for r in rows(source / f"{city}_roads.csv"):
        if r["highway"] not in ROAD_TYPES: road_rejection["road_type_excluded"] += 1; continue
        try:
            geom = wkt.loads(r["geometry"])
            if geom.geom_type != "LineString" or geom.is_empty or not geom.is_valid: raise ValueError("Invalid road")
            projected = transform(projection.forward.transform, geom)
        except Exception: road_rejection["invalid_geometry"] += 1; continue
        if not projected.intersects(window): road_rejection["outside_fixed_window"] += 1; continue
        record = {"osm_id": r["osm_id"], "highway": r["highway"], "name": r["name"], "oneway": r["oneway"],
                  "csv_length_m": finite_value(r["length_m"]), "lonlat": [list(x) for x in geom.coords],
                  "geometry_wkt_sha256": hashlib.sha256(r["geometry"].encode()).hexdigest(),
                  "csv_export_timestamp": None, "bridge_layer_tunnel_access_not_in_csv": True}
        background.append(record)
        if projected.intersects(corridor): selected_roads.append(record)
    selected_roads.sort(key=lambda r: r["osm_id"]); background.sort(key=lambda r: r["osm_id"])
    selected = {"city": city, "city_label": label, "frozen_utc": utc(), "selection_depends_on_route_success": False,
                "centre_lonlat": [lon, lat], "epsg": epsg, "rules": PROTOCOL, "source_csv_sha256": source_hash,
                "boundary_identity_officially_verified": False, "boundary_scope_note": "Supplied OSM CSV boundary; not a certified heat-planning boundary",
                "candidate_buildings_count": len(eligible), "building_rejections": dict(rejection),
                "road_rejections": dict(road_rejection), "buildings": buildings, "anchor_osm_id": buildings[0]["osm_id"],
                "corridor_xy_geometry": mapping(corridor), "roads": selected_roads, "background_roads": background}
    dump(p, selected)
    return selected


def fetch(out, city, kind, ids, offline, receipts):
    elements = {}; batch_size = 100 if kind == "ways" else 500
    for i in range(0, len(ids), batch_size):
        batch = ids[i:i+batch_size]
        url = f"https://api.openstreetmap.org/api/0.6/{kind}.json?{kind}=" + ",".join(map(str, batch))
        key = hashlib.sha256(url.encode()).hexdigest()[:16]
        dest = out / city / "native" / f"{kind}_{key}.json"
        meta_path = dest.with_suffix(".receipt.json")
        if dest.exists() and meta_path.exists():
            receipt = json.loads(meta_path.read_text(encoding="utf-8"))
            if receipt.get("url") != url or receipt.get("sha256") != sha(dest): raise ValueError("Cached native response checksum mismatch")
            data = json.loads(dest.read_bytes()); receipts.append({**receipt, "reused_cached_response": True})
        elif offline:
            receipts.append({"url": url, "utc": utc(), "status": "offline_cache_missing", "requested_ids": batch})
            continue
        else:
            data = None
            for attempt in range(1, 4):
                receipt = {"url": url, "utc": utc(), "attempt": attempt, "requested_ids": batch}
                try:
                    req = Request(url, headers={"User-Agent": "north-heat-spatial-reference/1.0 (read-only bounded research)", "Accept": "application/json"})
                    with urlopen(req, timeout=25) as response:
                        body = response.read(); receipt["http_status"] = response.status
                    parsed = json.loads(body)
                    if not isinstance(parsed.get("elements"), list): raise ValueError("Unexpected native response")
                    dest.parent.mkdir(parents=True, exist_ok=True); dest.write_bytes(body)
                    receipt.update(status="success", raw_file=str(dest.relative_to(out)).replace("\\", "/"), sha256=sha(dest), bytes=len(body))
                    dump(meta_path, receipt); receipts.append(receipt); data = parsed; break
                except Exception as error:
                    receipt.update(status="failed", error_type=type(error).__name__, error=str(error), http_status=getattr(error, "code", None))
                    receipts.append(receipt)
                    dump(out / city / "native" / f"{kind}_{key}.attempt{attempt}.failed.json", receipt)
                    if attempt < 3: time.sleep(1.0)
            if data is None: continue
        for element in data["elements"]:
            if element["type"] == kind[:-1]: elements[element["id"]] = element
    dump(out / city / "native_intake_receipts.json", receipts)
    return elements


def native_ways(selection, out, offline):
    city = selection["city"]; receipts = []
    ids = sorted(int(r["osm_id"][1:]) for r in selection["roads"])
    ways = fetch(out, city, "ways", ids, offline, receipts)
    node_ids = sorted({n for w in ways.values() for n in w.get("nodes", [])})
    nodes = fetch(out, city, "nodes", node_ids, offline, receipts)
    complete = {}; audit = []
    for raw in selection["roads"]:
        wid = int(raw["osm_id"][1:]); native = ways.get(wid)
        missing = [] if native else ["way"]
        if native: missing = [n for n in native.get("nodes", []) if n not in nodes]
        item = {"osm_id": raw["osm_id"], "csv_geometry_timestamp_unknown": True, "missing_native_refs": missing}
        if native and not missing and len(native.get("nodes", [])) >= 2:
            refs = tuple(native["nodes"]); coords = tuple((nodes[n]["lon"], nodes[n]["lat"]) for n in refs)
            tags = native.get("tags", {})
            csv_coords = tuple(map(tuple, raw["lonlat"]))
            exact = coords == csv_coords; reverse = coords == csv_coords[::-1]
            item.update(status="complete_current_native", way_version=native.get("version"), way_timestamp=native.get("timestamp"),
                        node_versions={str(n): nodes[n].get("version") for n in refs},
                        node_timestamps={str(n): nodes[n].get("timestamp") for n in refs}, tags=tags,
                        csv_equals_current_native_forward=exact, csv_equals_current_native_reverse=reverse,
                        geometry_changed_since_csv=not (exact or reverse), current_vertex_count=len(refs), csv_vertex_count=len(csv_coords),
                        geometry_refs_pairing="native way refs and coordinates from matching returned native node IDs; never refs attached to old WKT")
            complete[raw["osm_id"]] = RoadWay(raw["osm_id"], coords, tags, refs, "current_native_OSM_multiple_GETs")
        else: item["status"] = "native_intake_incomplete"
        audit.append(item)
    all_complete = len(complete) == len(selection["roads"]) and bool(selection["roads"])
    admitted = []; excluded = []
    for raw in selection["roads"]:
        native = complete.get(raw["osm_id"])
        # Avoid hybrid graphs: either every way has refs+native geometry or use all original WKT.
        if all_complete:
            way = native
        else:
            tags = {"highway": raw["highway"], "name": raw["name"], "oneway": raw["oneway"]}
            # New grade/access metadata cannot be certified for an old, changed geometry.
            if native and not next(a for a in audit if a["osm_id"] == raw["osm_id"])["geometry_changed_since_csv"]:
                tags.update(native.tags)
            way = RoadWay(raw["osm_id"], tuple(map(tuple, raw["lonlat"])), tags, None, "original_csv_geometry_fallback")
        if way.tags.get("highway") not in ROAD_TYPES:
            excluded.append({"osm_id": way.osm_id, "reason": "current_highway_type_not_admitted", "tags": way.tags}); continue
        if way.tags.get("access") in {"no", "private", "agricultural", "forestry"}:
            excluded.append({"osm_id": way.osm_id, "reason": "restricted_access", "tags": way.tags}); continue
        admitted.append(way)
    intake = {"requested_way_count": len(ids), "returned_way_count": len(ways), "requested_node_count": len(node_ids),
              "returned_node_count": len(nodes), "complete_way_count": len(complete), "all_ways_native_complete": all_complete,
              "native_api_is_not_transactional_snapshot": True,
              "source_time_note": "Original building/road CSV export time unknown. Current way and node versions/times preserved; requests are not a historical or atomic city snapshot.",
              "geometry_changed_way_count": sum(a.get("geometry_changed_since_csv", False) for a in audit),
              "selected_admitted_way_count": len(admitted), "excluded_ways": excluded,
              "native_way_audit": audit, "fallback_reason": None if all_complete else "one_or_more_way_or_node_requests_incomplete; whole graph uses original CSV geometry"}
    dump(out / city / "native_intake.json", intake)
    return admitted, all_complete, intake


def csv_output(path, records, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fields, extrasaction="ignore"); w.writeheader(); w.writerows(records)


def export_network(network, out):
    nodes = [{"node_id": n, **d} for n, d in sorted(network.graph.nodes(data=True))]
    edges = [{"u": u, "v": v, "edge_key": k, **d} for u, v, k, d in sorted(network.graph.edges(keys=True, data=True))]
    dump(out / "reference_network.json", {"units": "metres; coordinates also WGS84 lon/lat", "native": network.native, "nodes": nodes, "edges": edges})
    csv_output(out / "reference_nodes.csv", nodes, ["node_id", "lon", "lat", "x_m", "y_m"])
    csv_output(out / "reference_edges.csv", edges, ["u", "v", "edge_key", "osm_id", "segment_id", "length_m", "kind"])
    features = []
    for e in edges:
        a = network.graph.nodes[e["u"]]; b = network.graph.nodes[e["v"]]
        features.append({"type": "Feature", "properties": e, "geometry": {"type": "LineString", "coordinates": [[a["lon"], a["lat"]], [b["lon"], b["lat"]]]}})
    dump(out / "reference_edges.geojson", {"type": "FeatureCollection", "features": features})


def draw(selection, network, routes, output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    from matplotlib.font_manager import FontProperties
    from matplotlib.lines import Line2D
    from matplotlib.ticker import MaxNLocator
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    font = FontProperties(fname=str(font_path)) if font_path.exists() else FontProperties(family="DejaVu Sans")
    # Type 3 vector glyphs avoid CJK TTC subset text loss in some PDF renderers.
    plt.rcParams.update({"axes.unicode_minus": False, "font.size": 10, "pdf.fonttype": 3})
    projection = network.projection; cx, cy = projection.xy(*selection["centre_lonlat"])
    def relative(coords): return [(x-cx, y-cy) for x, y in coords]
    fig = plt.figure(figsize=(12.7, 8.0), layout="constrained")
    gs = fig.add_gridspec(1, 2, width_ratios=[3.0, 1.25]); ax = fig.add_subplot(gs[0, 0]); info = fig.add_subplot(gs[0, 1]); info.axis("off")
    background = [relative([projection.xy(*p) for p in r["lonlat"]]) for r in selection["background_roads"]]
    ax.add_collection(LineCollection(background, colors="#d9dee4", linewidths=0.65, zorder=1))
    admitted = [relative([s["a"], s["b"]]) for s in network.segments]
    ax.add_collection(LineCollection(admitted, colors="#8399b3", linewidths=1.15, zorder=2))
    colours = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#7B61A8"]
    handles = [Line2D([0], [0], color="#8399b3", lw=1.2, label="已准入参考道路"),
               Line2D([0], [0], color="#555555", ls="--", label="质心连接段")]
    if any(s["status"] == "snap_distance_exceeded" for s in network.snaps.values()):
        handles.append(Line2D([0], [0], marker="x", linestyle="None", color="#c62828", markersize=7, label="距道路>150 m，吸附失败"))
    for i, route in enumerate(routes):
        if route["status"] != "routed": continue
        colour = colours[i % len(colours)]; geom = relative(route["road_geometry_xy_m"])
        ax.plot(*zip(*geom), color=colour, lw=2.5, alpha=.83, zorder=4)
        a = network.snaps[route["anchor_osm_id"]]; b = network.snaps[route["target_osm_id"]]
        for snap in (a, b):
            ax.plot(*zip(*relative([snap["point_xy_m"], snap["snap_xy_m"]])), color="#555555", ls="--", lw=1, zorder=3)
        handles.append(Line2D([0], [0], color=colour, lw=2.5, label=f"1→{i+2}  {route['total_reference_length_m']:.0f} m"))
    # Fixed screen-space offsets only improve legibility; coordinates never move.
    offsets = {"guyuan": [(8, 12), (-27, 23), (14, 15), (-32, -18), (10, 15), (23, -22)],
               "urumqi": [(8, -20), (-20, -20), (12, -20), (-24, 14), (23, 5), (-15, 25)],
               "wuhan": [(12, 13), (-34, -5), (22, 17), (-22, -27), (-30, 13), (-5, 26)]}
    bounds = []
    for i, b in enumerate(selection["buildings"]):
        x, y = b["easting_m"]-cx, b["northing_m"]-cy; bounds.append((x, y))
        snap = network.snaps[b["osm_id"]]; failed = snap["status"] != "snapped"
        ax.scatter([x], [y], s=110 if i == 0 else 57, marker="*" if i == 0 else ("x" if failed else "o"),
                   c="#111827" if not failed else "#c62828", edgecolors="white" if not failed else None, linewidths=.8, zorder=7)
        ax.annotate(str(i+1), (x, y), xytext=offsets[selection["city"]][i], textcoords="offset points", fontsize=11, weight="bold", zorder=8,
                    arrowprops={"arrowstyle": "-", "color": "#6b7280", "lw": .7, "shrinkA": 2, "shrinkB": 4},
                    bbox={"boxstyle": "round,pad=.14", "fc": "white", "ec": "none", "alpha": .97})
    # Show the fixed intake corridor; complete ways are retained in exported data.
    corridor = selection["corridor_xy_geometry"]
    if corridor["type"] == "Polygon":
        outline = relative(corridor["coordinates"][0]); ax.plot(*zip(*outline), color="#b8aa8d", lw=1, ls=":", zorder=2)
    ax.set_xlim(min(x for x, y in bounds)-650, max(x for x, y in bounds)+650)
    ax.set_ylim(min(y for x, y in bounds)-650, max(y for x, y in bounds)+650)
    ax.set_aspect("equal"); ax.grid(alpha=.13); ax.xaxis.set_major_locator(MaxNLocator(5)); ax.yaxis.set_major_locator(MaxNLocator(5))
    ax.set_xlabel("相对固定中心的东向距离 / m", fontproperties=font); ax.set_ylabel("相对固定中心的北向距离 / m", fontproperties=font)
    ax.set_title(f"{selection['city_label']}｜固定六建筑空间参考案例", fontproperties=font, fontsize=15, pad=14)
    # Grid north is explicit because UTM grid north is not exactly geodetic north.
    ax.annotate("格网北", xy=(.94, .94), xytext=(.94, .81), xycoords="axes fraction", ha="center", fontproperties=font,
                arrowprops={"arrowstyle": "-|>", "color": "#333333", "lw": 1.2})
    xmin, xmax = ax.get_xlim(); ymin, ymax = ax.get_ylim(); sx = xmin+.05*(xmax-xmin); sy = ymin+.07*(ymax-ymin)
    ax.plot([sx, sx+200], [sy, sy], color="#333333", lw=3); ax.text(sx+100, sy+.02*(ymax-ymin), "200 m", ha="center", fontsize=9)
    ax.legend(handles=handles, loc="lower right", prop=font, fontsize=9, framealpha=.96)
    lines = ["对象与结果", "", f"EPSG:{selection['epsg']} · WGS84 UTM", f"道路准入：{len({s['osm_id'] for s in network.segments})} ways",
             f"连通分量：{network.summary()['connected_components']}", f"成功路线：{sum(r['status']=='routed' for r in routes)}/{len(routes)}",
             "", "编号  OSM建筑ID / 吸附距离"]
    for i, b in enumerate(selection["buildings"]):
        s = network.snaps[b["osm_id"]]
        d = f"{s['distance_m']:.1f} m" if "distance_m" in s else s["status"]
        lines.append(f"{i+1}  {b['osm_id']} / {d}")
    lines.extend(["", "★ 1 为参考锚点，非热源", "虚线仅连接建筑质心与道路", "未知用途/访问条件仍需核实", "当前道路与旧建筑导出异时", "", "拓扑：" + ("原生OSM共享node refs" if network.native else "CSV几何回退，工程可达未证实"),
                  "平面交叉不会自动设为路口", "", "不是实际供热管网设计", "无负荷/容量/效率/投资假设", "", "© OpenStreetMap contributors · ODbL"])
    info.text(.0, 1, "\n".join(lines), va="top", fontproperties=font, fontsize=10.3, linespacing=1.55)
    fig.savefig(output / "map.png", dpi=160, facecolor="white"); fig.savefig(output / "map.pdf", facecolor="white"); plt.close(fig)


def build(selection, out, offline, source):
    city = selection["city"]; dest = out / city; dest.mkdir(parents=True, exist_ok=True)
    ways, native, intake = native_ways(selection, out, offline)
    label, lon, lat, epsg = CITIES[city]; projection = MetricProjection(lon, lat, epsg)
    network = ReferenceNetwork(ways, projection, native)
    before_split_components = network.summary()["connected_components"]
    snaps = network.snap_buildings(selection["buildings"], PROTOCOL["max_snap_distance_m"])
    anchor = selection["anchor_osm_id"]
    routes = [network.route(anchor, b["osm_id"]) for b in selection["buildings"] if b["osm_id"] != anchor]
    dump(dest / "snaps.json", snaps); dump(dest / "routes.json", routes)
    failures = [r for r in routes if r["status"] != "routed"]
    dump(dest / "failures.json", {"failed_routes": failures, "failed_snaps": [s for s in snaps if s["status"] != "snapped"]})
    route_fields = ["anchor_osm_id", "target_osm_id", "status", "road_length_m", "anchor_connector_m", "target_connector_m",
                    "total_reference_length_m", "straight_line_m", "detour_ratio_total_over_straight"]
    csv_output(dest / "routes.csv", routes, route_fields)
    csv_output(dest / "snaps.csv", snaps, ["osm_id", "status", "distance_m", "osm_way_id", "segment_id", "t", "snap_node"])
    summary = network.summary()
    component_by_node = {n: i+1 for i, component in enumerate(sorted(nx.connected_components(network.graph), key=lambda c:(-len(c), min(c)))) for n in component}
    summary.update(components_before_snap_splitting=before_split_components,
                   building_component_ids={s["osm_id"]: component_by_node.get(s.get("snap_node")) for s in snaps})
    dump(dest / "connectivity.json", summary)
    centre = projection.xy(lon, lat)
    segments = [{k: s[k] for k in ("segment_id", "osm_id", "length_m", "geodesic_m", "projection_abs_error_m", "projection_relative_error")} for s in network.segments]
    relative = [s["projection_relative_error"] for s in segments if s["projection_relative_error"] is not None]
    bound = boundary(source / f"{city}_boundary.csv")
    window_outside = []; city_crossings = []; city_outside = []
    for way in ways:
        if any(hypot(*[q-z for q, z in zip(projection.xy(*p), centre)]) > PROTOCOL["window_radius_m"] for p in way.lonlat):
            window_outside.append(way.osm_id)
        geom = LineString(way.lonlat)
        if not bound.covers(geom):
            (city_crossings if geom.intersects(bound) else city_outside).append(way.osm_id)
    errors = {"projection": f"EPSG:{epsg}", "axis_order": "lon,lat -> easting_m,northing_m", "comparison": "each full-way segment vs independent WGS84 ellipsoid Geod.inv",
              "segment_count": len(segments), "max_absolute_error_m": max((s["projection_abs_error_m"] for s in segments), default=None),
              "max_relative_error_percent": max(relative)*100 if relative else None, "p95_relative_error_percent": float(np.percentile(relative, 95))*100 if relative else None,
              "max_full_way_vertex_radius_m": max((hypot(p[0]-centre[0], p[1]-centre[1]) for s in network.segments for p in (s["a"], s["b"])), default=None),
              "full_way_segments_not_clipped_to_window": True, "sum_projected_m": sum(s["length_m"] for s in segments),
              "sum_geodesic_m": sum(s["geodesic_m"] for s in segments), "not_certified_survey_accuracy": True,
              "way_ids_with_vertices_outside_fixed_window": window_outside, "way_ids_crossing_supplied_city_boundary": city_crossings,
              "way_ids_wholly_outside_supplied_city_boundary": city_outside,
              "vertex_occurrences_outside_fixed_window": sum(hypot(p[0]-centre[0],p[1]-centre[1]) > PROTOCOL["window_radius_m"] for way in ways for p in map(lambda ll: projection.xy(*ll), way.lonlat))}
    dump(dest / "distance_error.json", errors); csv_output(dest / "distance_error_segments.csv", segments, list(segments[0]) if segments else ["segment_id"])
    export_network(network, dest); dump(dest / "model_interface.json", model_interface(selection["buildings"], routes))
    admission = {"case_status": "spatial_reference_only", "eligible_for_milp": False, "selected_building_ids": [b["osm_id"] for b in selection["buildings"]],
                 "anchor_is_reference_not_source": True, "selected_road_ids": [r["osm_id"] for r in selection["roads"]],
                 "admitted_road_ids": [w.osm_id for w in ways], "excluded_ways": intake["excluded_ways"],
                 "generic_yes_building_count": sum(b["building"] == "yes" for b in selection["buildings"]),
                 "all_heating_uses_unconfirmed": True, "no_floor_area_to_heat_demand_conversion": True,
                 "unknown_access_way_ids": summary["unknown_access_way_ids"], "grade_unknown_way_ids": summary["grade_unknown_way_ids"],
                 "missing_before_engineering_admission": ["confirmed building footprints and heated use/area", "measured or design heat demand and thermal parameters", "actual heat source locations and capacity", "surveyed existing/allowed pipe corridors and crossings", "excavation and access rights", "pipe capacity/diameter/loss/cost with evidence", "explicit solver input mapping"],
                 "csv_current_native_time_difference_recorded": True, "not_official_heat_planning_boundary": True}
    dump(dest / "admission_report.json", admission)
    draw(selection, network, routes, dest)
    result = {"city": city, "city_label": label, "buildings": len(selection["buildings"]), "selected_road_ways": len(selection["roads"]),
              "admitted_road_ways": len(ways), "native_complete": native, "geometry_changed_ways": intake["geometry_changed_way_count"],
              "snapped_buildings": sum(s["status"] == "snapped" for s in snaps), "routes_success": len(routes)-len(failures), "routes_failed": len(failures),
              "connected_components": summary["connected_components"], "max_projection_relative_error_percent": errors["max_relative_error_percent"],
              "unknown_access_ways": len(summary["unknown_access_way_ids"]), "eligible_for_milp": False}
    dump(dest / "result.json", result)
    return result


def input_inventory(source):
    old = ROOT / "docs/source_intake_20261002/datasets_batch_03/osm"
    paths = sorted(source.glob("*.csv")) + sorted(p for p in old.rglob("*") if p.is_file())
    return {str(p.resolve()): {"bytes": p.stat().st_size, "sha256": sha(p)} for p in paths}


def write_readme(out, results, integrity):
    table = ["| 城市 | 建筑 | 准入道路way | 原生拓扑 | 吸附 | 路线成功/失败 | 分量 | 最大投影段误差 |", "|---|---:|---:|---|---:|---|---:|---:|"]
    for r in results:
        table.append(f"| {r['city_label']} | {r['buildings']} | {r['admitted_road_ways']} | {'完整refs+节点' if r['native_complete'] else '几何回退'} | {r['snapped_buildings']} | {r['routes_success']}/{r['routes_failed']} | {r['connected_components']} | {r['max_projection_relative_error_percent']:.5f}% |")
    text = """# 三城空间参考案例 · 2026-10-02

状态：空间参考实验版，未进入工程/MILP验收，不上传GitHub。固定选点与有限补取规则见 [protocol.md](protocol.md)。六个建筑由原CSV质心排序预先冻结，不根据路线成功重选。首个建筑仅作参考锚点，不是供热热源。

""" + "\n".join(table) + """

每城目录提供冻结selection.json、admission_report.json、native_intake.json、逐次请求及原始响应、connectivity.json、snaps/routes/failures、节点/边CSV与GeoJSON、逐段测地线误差、map.png/map.pdf和model_interface.json。完整道路way保留全部中间点和窗口外部分，不用道路两端直线代替。地图缩放展示建筑与候选走廊，完整way范围见distance_error.json。

原生拓扑只通过真实共享node ID连接，不自动增加二维交叉路口。同位置不同node ID不合并；不同层级内部共享node异常隔离，桥头真实端节点过渡另记。几何回退仅连相同原始坐标及未冲突层级签名，缺tag的默认0不表示同层已证实。无向道路只是候选供热走廊参考；oneway是交通方向，不能证明允许铺管。

建筑均来自旧CSV，导出时刻未知；道路/API节点是本次多次GET读取，保留各自version和timestamp，不声称原子城市快照。若旧/新几何改变，采用完整当前refs+coords配对，绝不把新refs挂在旧WKT上。未知access/建筑用途逐项保留，吸附线仅从建筑质心到道路，不是实测入口或合法支管；150m超距和失联均原样记录。

物理接口保持null：负荷、热损失、热惯性、热源容量、管径/容量、效率、投资均未推定。该schema不能直接输入现有模型；spatial_quality既有流程会用直线×route_factor覆盖长度，需显式映射并提供真实参数后才能接入。

数据：© [OpenStreetMap contributors](https://www.openstreetmap.org/copyright)，ODbL。有限补取依据 [OSM API 0.6](https://wiki.openstreetmap.org/wiki/API_v0.6)，层级/访问标签见 [layer](https://wiki.openstreetmap.org/wiki/Key:layer) / [access](https://wiki.openstreetmap.org/wiki/Key:access)。使用 [PROJ UTM](https://proj.org/en/stable/operations/projections/utm.html) 和 [pyproj Geod](https://pyproj4.github.io/pyproj/stable/api/geod.html) 进行独立逐段距离核查；误差表只评价本次投影与椭球测地线差值，不是测绘精度证明。

重跑：主工程Python加requirements-spatial-reference.txt中的可选pyproj、shapely、networkx、matplotlib。`python scripts/build_osm_reference_cases.py --offline`复用冻结选择和已保全API响应；缺响应会明确走几何回退。在线默认也优先使用已验证缓存，若需新的API时点请显式使用新的--output目录，不覆盖旧证据。_runtime_packages只为本次隔离可选依赖，不在交付清单内，不改变全局环境。

"""
    text += f"原始输入完整性：{len(integrity['before'])}个CSV/旧审核证据文件前后SHA256一致，变化数{len(integrity['changed_files'])}；详见input_integrity.json。测试结果另见verification.json。未运行：MILP、实际供热性能/费用实验、开挖许可核验，原因是本批次只有空间参考数据。\n"
    for r in results: text += f"\n## {r['city_label']}\n\n![{r['city_label']}地图]({r['city']}/map.png)\n"
    (out / "README.md").write_text(text, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT.parents[1] / "datasets/osm")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--freeze-only", action="store_true")
    args = parser.parse_args(); out = args.output.resolve(); source = args.source.resolve(); out.mkdir(parents=True, exist_ok=True)
    if source == out or source in out.parents: raise ValueError("Output must not be inside original data directory")
    baseline = input_inventory(source)
    dump(out / "input_hashes_before.json", {"captured_utc": utc(), "files": baseline})
    selections = [freeze(city, source, out) for city in CITIES]
    status(out, "selection_frozen", selections={s["city"]: {"buildings": [b["osm_id"] for b in s["buildings"]], "road_count": len(s["roads"])} for s in selections})
    if args.freeze_only: return
    results = []
    try:
        for selection in selections:
            status(out, "native_intake_and_routes", active_city=selection["city"], cities_completed=[r["city"] for r in results])
            results.append(build(selection, out, args.offline, source))
            dump(out / "results.json", {"state": "running", "cases": results})
            status(out, "city_map_complete", active_city=selection["city"], cities_completed=[r["city"] for r in results], latest_result=results[-1])
    finally:
        after = input_inventory(source); changed = [p for p in baseline if baseline[p] != after.get(p)]
        integrity = {"checked_utc": utc(), "before": baseline, "after": after, "changed_files": changed, "unchanged": not changed,
                     "source_csv_count": len(list(source.glob("*.csv")))}
        dump(out / "input_integrity.json", integrity)
        if changed: raise RuntimeError("Read-only original inputs changed during the run")
    dump(out / "results.json", {"state": "maps_and_routes_complete_awaiting_tests", "cases": results, "original_inputs_unchanged": True})
    write_readme(out, results, integrity)
    status(out, "maps_and_routes_complete_awaiting_tests", active_city=None, cities_completed=[r["city"] for r in results])


if __name__ == "__main__": main()
