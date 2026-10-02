"""Read-only geometry reference adapter; supplies no heating physics or costs.

Original OSM node identity supplies connectivity; conflicting internal grades
are isolated by an additional conservative admission policy. Geometry
crossings are never inserted as junctions. Spatial reachability is not a right
to excavate, a building entrance, or a district-heating network certificate.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from math import hypot, isfinite
import re
from typing import Any

import networkx as nx
from pyproj import Geod, Transformer


@dataclass(frozen=True)
class MetricProjection:
    lon0: float
    lat0: float
    epsg: int

    def __post_init__(self):
        if not (-180 <= self.lon0 <= 180 and -80 < self.lat0 < 84):
            raise ValueError("UTM reference centre outside supported geographic range")
        expected = (32600 if self.lat0 >= 0 else 32700) + min(60, int((self.lon0+180)//6)+1)
        if self.epsg != expected:
            raise ValueError("EPSG must be the reference centre's WGS84 UTM zone")
        object.__setattr__(self, "forward", Transformer.from_crs(4326, self.epsg, always_xy=True))
        object.__setattr__(self, "inverse", Transformer.from_crs(self.epsg, 4326, always_xy=True))
        object.__setattr__(self, "geod", Geod(ellps="WGS84"))

    def xy(self, lon: float, lat: float) -> tuple[float, float]:
        if not (isfinite(lon) and isfinite(lat) and -180 <= lon <= 180 and -90 <= lat <= 90):
            raise ValueError("Invalid longitude/latitude")
        x,y = self.forward.transform(lon,lat)
        if not (isfinite(x) and isfinite(y)): raise ValueError("Projection failed")
        return float(x),float(y)

    def lonlat(self, x: float, y: float) -> tuple[float, float]:
        lon,lat = self.inverse.transform(x,y)
        return float(lon),float(lat)

    def geodesic_m(self, a, b) -> float:
        return float(self.geod.inv(a[0],a[1],b[0],b[1])[2])


@dataclass(frozen=True)
class RoadWay:
    osm_id: str
    lonlat: tuple[tuple[float,float], ...]
    tags: dict[str,str]
    node_refs: tuple[int,...] | None = None
    evidence_mode: str = "csv_geometry_only"

    def __post_init__(self):
        if not re.fullmatch(r"w[1-9][0-9]*", self.osm_id): raise ValueError("Invalid typed OSM way ID")
        if len(self.lonlat)<2: raise ValueError("Way requires at least two coordinates")
        if self.node_refs is not None and len(self.node_refs)!=len(self.lonlat):
            raise ValueError("Node references and coordinates must come from the same complete version")
        if self.node_refs is not None and any(not isinstance(n,int) or n<=0 for n in self.node_refs):
            raise ValueError("Invalid native node reference")


def grade_signature(tags: dict[str,str]):
    """Explicit bridge/tunnel/layer context, not an inferred crossing elevation."""
    return (tags.get("layer","0"), tags.get("bridge","no"), tags.get("tunnel","no"))


def closest_on_segment(point, a, b):
    dx,dy=b[0]-a[0],b[1]-a[1]
    denom=dx*dx+dy*dy
    if denom<=1e-20: return 0.0,tuple(a),hypot(point[0]-a[0],point[1]-a[1])
    t=max(0.0,min(1.0,((point[0]-a[0])*dx+(point[1]-a[1])*dy)/denom))
    q=(a[0]+t*dx,a[1]+t*dy)
    return t,q,hypot(point[0]-q[0],point[1]-q[1])


class ReferenceNetwork:
    def __init__(self, ways: list[RoadWay], projection: MetricProjection, native: bool):
        self.projection=projection;self.native=native
        self.graph=nx.MultiGraph();self.segments=[];self.snaps={};self.diagnostics=[]
        ways=sorted(ways,key=lambda w:w.osm_id)
        if len({w.osm_id for w in ways}) != len(ways): raise ValueError("Duplicate road way ID")
        if native and any(w.node_refs is None for w in ways):
            raise ValueError("Native mode cannot mix incomplete refs with current native coordinates")
        occurrences=defaultdict(list)
        for way in ways:
            if way.tags.get("access") in {"private","no","agricultural","forestry"}: continue
            if way.node_refs:
                for i,nid in enumerate(way.node_refs):
                    occurrences[nid].append((grade_signature(way.tags),i in {0,len(way.node_refs)-1},way.osm_id,way.lonlat[i]))
        isolated_refs=set()
        for nid,occ in sorted(occurrences.items()):
            sigs={x[0] for x in occ};coords={x[3] for x in occ}
            if len(coords)>1:
                raise ValueError(f"Native node {nid} has conflicting coordinates")
            if len(sigs)>1:
                if any(x[1] for x in occ):
                    self.diagnostics.append({"type":"native_shared_endpoint_grade_transition","node_ref":nid,"ways":[x[2] for x in occ]})
                else:
                    isolated_refs.add(nid)
                    self.diagnostics.append({"type":"native_shared_internal_grade_conflict_isolated","node_ref":nid,"ways":[x[2] for x in occ]})
        for way in ways:
            if way.tags.get("access") in {"private","no","agricultural","forestry"}:
                self.diagnostics.append({"type":"excluded_access","osm_id":way.osm_id,"access":way.tags["access"]});continue
            coords=[projection.xy(*p) for p in way.lonlat]
            sig=grade_signature(way.tags)
            keys=[]
            for i,(p,xy) in enumerate(zip(way.lonlat,coords)):
                if native:
                    nid=way.node_refs[i]
                    key=f"n{nid}" if nid not in isolated_refs else f"n{nid}|grade:{sig}"
                else:
                    key=f"geo:{float(p[0]).hex()},{float(p[1]).hex()}|grade:{sig}"
                self.graph.add_node(key,x_m=xy[0],y_m=xy[1],lon=p[0],lat=p[1])
                keys.append(key)
            for i,(p,q) in enumerate(zip(coords,coords[1:])):
                length=hypot(q[0]-p[0],q[1]-p[1])
                if length<=1e-8:
                    self.diagnostics.append({"type":"zero_length_segment_skipped","osm_id":way.osm_id,"segment_index":i});continue
                geodesic=projection.geodesic_m(way.lonlat[i],way.lonlat[i+1])
                segment={"segment_id":f"{way.osm_id}:{i}","osm_id":way.osm_id,"segment_index":i,
                         "u":keys[i],"v":keys[i+1],"a":p,"b":q,"length_m":length,"geodesic_m":geodesic,
                         "projection_abs_error_m":abs(length-geodesic),"projection_relative_error":abs(length/geodesic-1) if geodesic>0 else None,
                         "tags":dict(way.tags),"evidence_mode":way.evidence_mode}
                self.segments.append(segment)
                self.graph.add_edge(keys[i],keys[i+1],key=segment["segment_id"],length_m=length,kind="road",segment_id=segment["segment_id"],osm_id=way.osm_id)
        self.original_road_total_m=sum(s["length_m"] for s in self.segments)

    def snap_buildings(self, buildings: list[dict[str,Any]], max_distance_m: float = 150.0):
        if self.snaps: raise ValueError("Snap all objects in one batch; network is already split")
        if not isfinite(max_distance_m) or max_distance_m<0: raise ValueError("Invalid maximum snap distance")
        grouped=defaultdict(list)
        for building in sorted(buildings,key=lambda b:b["osm_id"]):
            bid=building["osm_id"]
            if bid in self.snaps: raise ValueError("Duplicate building osm_id")
            xy=self.projection.xy(float(building["centroid_lon"]),float(building["centroid_lat"]))
            best=None
            for s in self.segments:
                t,q,d=closest_on_segment(xy,s["a"],s["b"])
                candidate=(d,s["segment_id"],t,q,s)
                if best is None or candidate[:2]<best[:2]:best=candidate
            if best is None:
                self.snaps[bid]={"osm_id":bid,"status":"no_eligible_road_segments","point_xy_m":xy};continue
            d,_,t,q,s=best
            if d>max_distance_m:
                self.snaps[bid]={"osm_id":bid,"status":"snap_distance_exceeded","distance_m":d,
                                 "nearest_segment_id":s["segment_id"],"point_xy_m":xy};continue
            record={"osm_id":bid,"status":"snapped","distance_m":d,"segment_id":s["segment_id"],"osm_way_id":s["osm_id"],
                    "t":t,"point_xy_m":xy,"snap_xy_m":q,"reference_connector_is_not_entrance_or_right_of_way":True}
            self.snaps[bid]=record;grouped[s["segment_id"]].append(record)
        for s in self.segments:
            records=grouped.get(s["segment_id"],[])
            if not records:continue
            self.graph.remove_edge(s["u"],s["v"],key=s["segment_id"])
            interior={}
            for r in records:
                if r["t"]<=1e-10:r["snap_node"]=s["u"]
                elif r["t"]>=1-1e-10:r["snap_node"]=s["v"]
                else:
                    # Rounding only unifies practically identical positions on the same segment.
                    k=round(r["t"],12)
                    if k not in interior:interior[k]=f"snap:{s['segment_id']}:{k:.12f}"
                    r["snap_node"]=interior[k]
                    lon,lat=self.projection.lonlat(*r["snap_xy_m"])
                    self.graph.add_node(r["snap_node"],x_m=r["snap_xy_m"][0],y_m=r["snap_xy_m"][1],lon=lon,lat=lat)
            chain=[(0.0,s["u"])]+sorted((t,n) for t,n in interior.items())+[(1.0,s["v"])]
            for j,((t0,u),(t1,v)) in enumerate(zip(chain,chain[1:])):
                length=(t1-t0)*s["length_m"]
                if length>1e-8:self.graph.add_edge(u,v,key=f"{s['segment_id']}:{j}",length_m=length,kind="road",segment_id=s["segment_id"],osm_id=s["osm_id"])
        split_total=sum(d["length_m"] for _,_,d in self.graph.edges(data=True) if d["kind"]=="road")
        if abs(split_total-self.original_road_total_m)>max(1e-5,self.original_road_total_m*1e-10):
            raise RuntimeError("Segment splitting failed road length conservation")
        return list(self.snaps.values())

    def route(self, anchor_id: str, target_id: str):
        if anchor_id == target_id: raise ValueError("A reference route requires two distinct building objects")
        a=self.snaps.get(anchor_id);b=self.snaps.get(target_id)
        result={"anchor_osm_id":anchor_id,"target_osm_id":target_id,
                "anchor_is_not_heat_source":True,"route_is_not_heating_pipe_design":True}
        straight=hypot(a["point_xy_m"][0]-b["point_xy_m"][0],a["point_xy_m"][1]-b["point_xy_m"][1]) if a and b else None
        result["straight_line_m"]=straight
        if not a or not b or a["status"]!="snapped" or b["status"]!="snapped":
            return {**result,"status":"snap_not_available","anchor_snap_status":a["status"] if a else None,"target_snap_status":b["status"] if b else None}
        try:path=nx.shortest_path(self.graph,a["snap_node"],b["snap_node"],weight="length_m")
        except nx.NetworkXNoPath:return {**result,"status":"no_path","straight_line_m":straight}
        edges=[]
        for u,v in zip(path,path[1:]):
            key,d=min(self.graph.get_edge_data(u,v).items(),key=lambda kv:(kv[1]["length_m"],kv[0]))
            edges.append({"u":u,"v":v,"edge_key":key,**d})
        road=sum(d["length_m"] for d in edges)
        total=road+a["distance_m"]+b["distance_m"]
        coords=[(self.graph.nodes[n]["x_m"],self.graph.nodes[n]["y_m"]) for n in path]
        geom_total=sum(hypot(q[0]-p[0],q[1]-p[1]) for p,q in zip(coords,coords[1:]))
        if abs(geom_total-road)>max(1e-5,road*1e-9):raise RuntimeError("Path geometry/edge lengths disagree")
        if total+1e-5<straight:raise RuntimeError("Total route cannot be shorter than same-projection direct line")
        return {**result,"status":"routed","road_length_m":road,"anchor_connector_m":a["distance_m"],
                "target_connector_m":b["distance_m"],"total_reference_length_m":total,"straight_line_m":straight,
                "detour_ratio_total_over_straight":total/straight if straight>1e-8 else None,
                "node_ids":path,"edges":edges,"road_geometry_xy_m":coords,
                "geometry_full_xy_m":[a["point_xy_m"],*coords,b["point_xy_m"]]}

    def summary(self):
        cc=sorted((len(c) for c in nx.connected_components(self.graph)),reverse=True)
        return {"mode":"native_OSM_node_reference_with_conservative_grade_admission" if self.native else "geometry_reference_not_engineering_reachability",
                "nodes":self.graph.number_of_nodes(),"edges":self.graph.number_of_edges(),"connected_components":len(cc),
                "component_node_sizes":cc,"original_road_total_m":self.original_road_total_m,
                "road_total_after_split_m":sum(d["length_m"] for _,_,d in self.graph.edges(data=True)),
                "snap_status_counts":dict(__import__('collections').Counter(r["status"] for r in self.snaps.values())),
                "diagnostics":self.diagnostics,
                "unknown_access_way_ids":sorted({s["osm_id"] for s in self.segments if "access" not in s["tags"]}),
                "grade_unknown_way_ids":sorted({s["osm_id"] for s in self.segments if not any(k in s["tags"] for k in ("layer","bridge","tunnel"))}),
                "fallback_default_grade_is_not_verified_same_elevation":not self.native,
                "internal_shared_node_grade_conflict_isolation_is_extra_admission_policy":True,
                "not_identical_to_unfiltered_native_node_graph":bool([d for d in self.diagnostics if d["type"]=="native_shared_internal_grade_conflict_isolated"]),
                "not_a_heat_network_or_excavation_certificate":True}


def model_interface(buildings, routes):
    """Separate schema; never fabricates HeatPlanningModel physical inputs."""
    return {"schema":"osm_spatial_reference_v1","eligible_for_engineering_solver_input":False,
            "buildings":[{"reference_node_id":b["osm_id"],"longitude":b["centroid_lon"],"latitude":b["centroid_lat"],
                          "building_tag":b["building"],"confirmed_heating_use":None,"actual_heated_area_m2":None,
                          "heat_loss_kw_per_c":None,"thermal_capacity_kwh_per_c":None,"demand_series":None} for b in buildings],
            "candidate_connections":[{"from_reference":r["anchor_osm_id"],"to_reference":r["target_osm_id"],"status":r["status"],
                                      "reference_length_m":r.get("total_reference_length_m"),"actual_pipe_length_m":None,
                                      "capacity_kw":None,"diameter_mm":None,"delivery_efficiency":None,"capex_total_yuan":None} for r in routes],
            "source_capacity_kw":None,"costs_verified":False,
            "adapter_note":"Keep separate from spatial_quality preprocessing, which recalculates length as straight distance times route_factor. Requires explicit project mapping and physical input evidence before solver admission."}
