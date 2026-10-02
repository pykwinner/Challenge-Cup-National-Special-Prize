"""Independent raw-CSV/native-response audit; does not import the spatial adapter."""
from collections import defaultdict
import csv
import hashlib
import importlib.util
import json
from math import hypot, isfinite
from pathlib import Path
import re
import sys
sys.dont_write_bytecode = True
HERE=Path(__file__).resolve().parent; ROOT=HERE.parents[2]
OUT=ROOT/'docs/execution_20261002/spatial_reference'; DATA=ROOT.parents[1]/'datasets/osm'
if importlib.util.find_spec('pyproj') is None: sys.path.append(str(OUT/'_runtime_packages'))
import networkx as nx
import numpy as np
from pyproj import Transformer, Geod
from shapely import wkt
from shapely.geometry import Point, Polygon, MultiPolygon, MultiPoint
from shapely.ops import transform

def load(p):return json.loads(p.read_text(encoding='utf-8'))
def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()
def rows(p):
    with p.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
issues=[];checks=0
def check(test,label,detail=None):
    global checks
    checks+=1
    if not test:issues.append({'check':label,'detail':detail})
def close(a,b):return abs(float(a)-float(b))<=1e-5+1e-9*abs(float(b))
def equal_num(a,b,label):check(close(a,b),label,{'actual':a,'expected':b})
BT={'yes','residential','apartments','house','detached','semidetached_house','terrace','office','school','university','hospital','commercial','retail','public','public_building','dormitory','hotel'}
RT={'residential','service','living_street','unclassified','tertiary','secondary','primary','tertiary_link','secondary_link','primary_link'}
CITIES={'guyuan':(106.2420,36.0090,32648),'urumqi':(87.6170,43.8250,32645),'wuhan':(114.3000,30.5900,32650)}
files=[ROOT/'src/heatplan/osm_reference.py',ROOT/'scripts/build_osm_reference_cases.py',OUT/'protocol.md']
for city in CITIES:
    for name in ['selection.json','native_intake.json','native_intake_receipts.json','snaps.json','routes.json','failures.json','reference_network.json','distance_error.json','connectivity.json','admission_report.json','result.json','model_interface.json']:
        files.append(OUT/city/name)
before={str(p.relative_to(ROOT)):sha(p) for p in files}
input_integrity=load(OUT/'input_integrity.json')
input_recomputed={p:sha(Path(p)) for p in input_integrity['before']}
for p,record in input_integrity['before'].items():check(input_recomputed[p]==record['sha256'],f'original input unchanged: {p}')
reports=[]
for city,(lon0,lat0,epsg) in CITIES.items():
    folder=OUT/city;s=load(folder/'selection.json');result=load(folder/'result.json')
    t=Transformer.from_crs(4326,epsg,always_xy=True);geod=Geod(ellps='WGS84');cx,cy=t.transform(lon0,lat0)
    rings=defaultdict(lambda:defaultdict(list))
    for r in rows(DATA/f'{city}_boundary.csv'):
        rings[r['polygon_id']][(r['ring_id'],r['role'])].append((int(r['vertex_id']),float(r['lon']),float(r['lat'])))
    polygons=[]
    for groups in rings.values():
        exterior=[];holes=[]
        for (_,role),values in groups.items():
            coordinates=[(a,b) for _,a,b in sorted(values)]
            if role=='outer':exterior=coordinates
            else:holes.append(coordinates)
        polygons.append(Polygon(exterior,holes))
    boundary=MultiPolygon(polygons);eligible=[]
    for r in rows(DATA/f'{city}_buildings.csv'):
        try:lon,lat,area=map(float,(r['centroid_lon'],r['centroid_lat'],r['footprint_area_m2']))
        except ValueError:continue
        if not re.fullmatch(r'w[1-9][0-9]*',r['osm_id']) or r['building'] not in BT or not all(map(isfinite,(lon,lat,area))) or area<=0:continue
        if not (-180<=lon<=180 and -90<=lat<=90):continue
        x,y=t.transform(lon,lat);d=hypot(x-cx,y-cy)
        if d<=1500 and boundary.covers(Point(lon,lat)):eligible.append((d,r['osm_id'],x,y,r))
    eligible.sort(key=lambda a:(a[0],a[1]));selected=eligible[:6]
    check([a[1] for a in selected]==[b['osm_id'] for b in s['buildings']],f'{city}: fixed raw building order')
    check(len(eligible)==s['candidate_buildings_count'],f'{city}: eligible building count')
    corridor=MultiPoint([(a[2],a[3]) for a in selected]).convex_hull.buffer(400)
    window=Point(cx,cy).buffer(1500);selected_roads=[]
    for r in rows(DATA/f'{city}_roads.csv'):
        if r['highway'] not in RT:continue
        g=wkt.loads(r['geometry'])
        if g.geom_type!='LineString' or g.is_empty or not g.is_valid:continue
        p=transform(t.transform,g)
        if p.intersects(window) and p.intersects(corridor):selected_roads.append(r['osm_id'])
    check(sorted(selected_roads)==[r['osm_id'] for r in s['roads']],f'{city}: fixed raw road order')
    for kind,expected in s['source_csv_sha256'].items():check(sha(DATA/f'{city}_{kind}.csv')==expected,f'{city}: selection source {kind}')
    rawways={};rawnodes={};receipt_count=0
    for receipt in load(folder/'native_intake_receipts.json'):
        if receipt['status']!='success':continue
        p=OUT/receipt['raw_file'];check(sha(p)==receipt['sha256'],f'{city}: API receipt checksum {p.name}')
        check(receipt['http_status']==200,f'{city}: recorded API HTTP status')
        kind='way' if '/ways.json?' in receipt['url'] else 'node'
        check(len(receipt['requested_ids'])<=(100 if kind=='way' else 500),f'{city}: bounded API request')
        for item in load(p)['elements']:
            if item['type']=='way':rawways[item['id']]=item
            elif item['type']=='node':rawnodes[item['id']]=item
        receipt_count+=1
    admitted=load(folder/'admission_report.json')['admitted_road_ids']
    check(set(rawways)=={int(r['osm_id'][1:]) for r in s['roads']},f'{city}: native way scope exactly fixed')
    refs={n for w in rawways.values() for n in w['nodes']}
    check(refs==set(rawnodes),f'{city}: all returned native node IDs exactly requested')
    occurrences=defaultdict(list)
    for wid in admitted:
        way=rawways[int(wid[1:])];tag=way.get('tags',{});grade=(tag.get('layer','0'),tag.get('bridge','no'),tag.get('tunnel','no'))
        for i,n in enumerate(way['nodes']):occurrences[n].append((grade,i in [0,len(way['nodes'])-1]))
    isolated={n for n,occ in occurrences.items() if len({v[0] for v in occ})>1 and not any(v[1] for v in occ)}
    graph=nx.MultiGraph();segments={};geod_lengths=[];projected_lengths=[];relative_errors=[];max_radius=0
    source_geometry_changes=0
    for wid in admitted:
        way=rawways[int(wid[1:])];tag=way.get('tags',{});grade=(tag.get('layer','0'),tag.get('bridge','no'),tag.get('tunnel','no'))
        coords=[(rawnodes[n]['lon'],rawnodes[n]['lat']) for n in way['nodes']];xy=[t.transform(*p) for p in coords]
        original=next(r['lonlat'] for r in s['roads'] if r['osm_id']==wid)
        if coords!=list(map(tuple,original)) and coords!=list(map(tuple,original[::-1])):source_geometry_changes+=1
        keys=[f'n{n}' if n not in isolated else f'n{n}|grade:{grade}' for n in way['nodes']]
        for key,p in zip(keys,xy):graph.add_node(key,xy=p)
        for i,(a,b) in enumerate(zip(xy,xy[1:])):
            length=hypot(b[0]-a[0],b[1]-a[1])
            if length<=1e-8:continue
            sid=f'{wid}:{i}';geo=geod.inv(*coords[i],*coords[i+1])[2]
            segments[sid]=(keys[i],keys[i+1],a,b,length)
            graph.add_edge(keys[i],keys[i+1],key=sid,length=length)
            geod_lengths.append(geo);projected_lengths.append(length);relative_errors.append(abs(length/geo-1))
            max_radius=max(max_radius,hypot(a[0]-cx,a[1]-cy),hypot(b[0]-cx,b[1]-cy))
    check(source_geometry_changes==result['geometry_changed_ways'],f'{city}: native geometry changes')
    snaps={r['osm_id']:r for r in load(folder/'snaps.json')};on_segment=defaultdict(list);snap_report=[]
    for building in s['buildings']:
        bid=building['osm_id'];q=np.array(t.transform(building['centroid_lon'],building['centroid_lat']))
        best=None
        for sid,(u,v,a,b,length) in segments.items():
            p=np.array(a);delta=np.array(b)-p;fraction=np.clip(np.dot(q-p,delta)/np.dot(delta,delta),0,1)
            projected=p+fraction*delta;d=float(np.linalg.norm(q-projected));choice=(d,sid,float(fraction),projected)
            if best is None or choice[:2]<best[:2]:best=choice
        d,sid,fraction,projected=best;record=snaps[bid]
        expected_status='snapped' if d<=150 else 'snap_distance_exceeded'
        check(record['status']==expected_status,f'{city}: snap status {bid}')
        equal_num(record['distance_m'],d,f'{city}: closest-segment distance {bid}')
        if expected_status=='snapped':
            check(record['segment_id']==sid,f'{city}: closest segment identity {bid}')
            equal_num(record['t'],fraction,f'{city}: segment parameter {bid}')
            on_segment[sid].append((fraction,record['snap_node'],tuple(projected)))
        snap_report.append({'osm_id':bid,'status':expected_status,'independent_distance_m':d})
    for sid,positions in on_segment.items():
        u,v,a,b,length=segments[sid];graph.remove_edge(u,v,key=sid)
        interior={round(f,12):(n,p) for f,n,p in positions if f>1e-10 and f<1-1e-10}
        for n,p in interior.values():graph.add_node(n,xy=p)
        chain=[(0.,u)]+[(f,n) for f,(n,p) in sorted(interior.items())]+[(1.,v)]
        for j,((f0,u0),(f1,v1)) in enumerate(zip(chain,chain[1:])):
            if (f1-f0)*length>1e-8:graph.add_edge(u0,v1,key=f'{sid}:{j}',length=(f1-f0)*length)
    exported=load(folder/'reference_network.json');export_nodes={n['node_id']:n for n in exported['nodes']}
    check(set(graph.nodes)==set(export_nodes),f'{city}: reconstructed graph node identities')
    check(graph.number_of_edges()==len(exported['edges']),f'{city}: reconstructed edge count')
    for edge in exported['edges']:
        expected=graph.get_edge_data(edge['u'],edge['v'],edge['edge_key'])
        check(expected is not None,f'{city}: exported edge exists in native graph {edge["edge_key"]}')
        if expected:equal_num(edge['length_m'],expected['length'],f'{city}: exported native edge length {edge["edge_key"]}')
    equal_num(sum(projected_lengths),sum(e[2]['length'] for e in graph.edges(data=True)),f'{city}: split total conserves road length')
    component_sizes=sorted(map(len,nx.connected_components(graph)),reverse=True)
    check(component_sizes==load(folder/'connectivity.json')['component_node_sizes'],f'{city}: component partition sizes')
    distance=load(folder/'distance_error.json')
    for field,value in [('sum_projected_m',sum(projected_lengths)),('sum_geodesic_m',sum(geod_lengths)),('max_relative_error_percent',max(relative_errors)*100),('max_absolute_error_m',max(abs(a-b) for a,b in zip(projected_lengths,geod_lengths))),('max_full_way_vertex_radius_m',max_radius)]:
        equal_num(distance[field],value,f'{city}: distance report {field}')
    route_report=[];routes=load(folder/'routes.json');anchor=s['anchor_osm_id']
    check([r['target_osm_id'] for r in routes]==[b['osm_id'] for b in s['buildings'][1:]],f'{city}: all fixed target objects retained')
    for r in routes:
        a,b=snaps[anchor],snaps[r['target_osm_id']]
        if a['status']!='snapped' or b['status']!='snapped':expected='snap_not_available';shortest=None
        elif not nx.has_path(graph,a['snap_node'],b['snap_node']):expected='no_path';shortest=None
        else:expected='routed';shortest=nx.dijkstra_path_length(graph,a['snap_node'],b['snap_node'],weight='length')
        check(r['status']==expected,f'{city}: route status {r["target_osm_id"]}')
        if shortest is not None:
            equal_num(r['road_length_m'],shortest,f'{city}: shortest road route {r["target_osm_id"]}')
            total=shortest+a['distance_m']+b['distance_m'];equal_num(r['total_reference_length_m'],total,f'{city}: route connectors and total')
            straight=np.linalg.norm(np.array(a['point_xy_m'])-np.array(b['point_xy_m']));equal_num(r['straight_line_m'],straight,f'{city}: direct building distance')
            equal_num(r['detour_ratio_total_over_straight'],total/straight,f'{city}: detour ratio')
        route_report.append({'target_osm_id':r['target_osm_id'],'status':expected,'independent_road_length_m':shortest})
    failure=load(folder/'failures.json')
    check([r for r in routes if r['status']!='routed']==failure['failed_routes'],f'{city}: route failures not removed')
    interface=load(folder/'model_interface.json');check(interface['eligible_for_engineering_solver_input'] is False,f'{city}: engineering admission off')
    for b in interface['buildings']:check(all(b[f] is None for f in ['confirmed_heating_use','actual_heated_area_m2','heat_loss_kw_per_c','thermal_capacity_kwh_per_c','demand_series']),f'{city}: building physical fields remain null')
    for r in interface['candidate_connections']:check(all(r[f] is None for f in ['actual_pipe_length_m','capacity_kw','diameter_mm','delivery_efficiency','capex_total_yuan']),f'{city}: pipe physical fields remain null')
    reports.append({'city':city,'fixed_building_ids_verified':[a[1] for a in selected],'native_ways':len(rawways),'native_nodes':len(rawnodes),
                    'segments_reconstructed':len(segments),'api_receipts_checked':receipt_count,'components':component_sizes,
                    'maximum_projection_error_percent':max(relative_errors)*100,'full_way_vertex_max_radius_m':max_radius,
                    'snaps':snap_report,'routes':route_report,'grade_conflict_refs':sorted(isolated)})
after={str(p.relative_to(ROOT)):sha(p) for p in files};check(before==after,'Reviewed spatial artifacts unchanged during recomputation')
report={'review_date_local':'2026-10-02','identity':'Actual original OSM CSV and captured native response recomputation; no synthetic observations',
        'independence':'Does not import osm_reference or build_osm_reference_cases; raw selection, projection, nearest segment and native graph/Dijkstra reconstructed separately',
        'geodesic_library_limit':'Recomputed from raw node coordinates using same installed pyproj.Geod library; not an independent geodesic implementation or certified survey',
        'code_and_artifact_sha256':before,'source_unchanged_during_recompute':before==after,'original_files_checked':len(input_recomputed),
        'original_sha256_recomputed':input_recomputed,'checks':checks,'issue_count':len(issues),'issues':issues,'cities':reports,
        'engineering_solve':False,'network_requests':False,'core_edits':False}
support=HERE/'support/spatial_recomputation';support.mkdir(exist_ok=True)
(support/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
print(json.dumps({'checks':checks,'issues':issues,'cities':reports},ensure_ascii=False,indent=2))
