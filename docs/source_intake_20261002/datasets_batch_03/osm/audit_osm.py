from pathlib import Path
from collections import Counter, defaultdict
import csv, hashlib, json, math, re, sys, zipfile
from datetime import datetime, timezone
import numpy as np
import shapely
from shapely import wkt
from shapely.geometry import Point, Polygon, MultiPolygon
from shapely.prepared import prep
from shapely.validation import explain_validity

IN = Path('D:/competion/xiaotiao/datasets/osm')
OUT = Path(__file__).resolve().parent
csv.field_size_limit(20000000)
R = 6371008.8
CITIES = {'guyuan': '固原', 'urumqi': '乌鲁木齐', 'wuhan': '武汉'}

def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024*1024), b''): h.update(chunk)
    return h.hexdigest()

def load(path):
    with path.open(encoding='utf-8-sig', newline='') as f:
        d = csv.DictReader(f)
        return d.fieldnames, list(d)

def number(s):
    try:
        v = float(s)
        return v if math.isfinite(v) else None
    except (ValueError, TypeError): return None

def stats(values):
    values = [v for v in values if v is not None and math.isfinite(v)]
    if not values: return {'count': 0}
    a = np.array(values)
    return {'count': len(a), 'min': float(a.min()), 'p50': float(np.quantile(a, .5)),
            'p95': float(np.quantile(a, .95)), 'max': float(a.max()), 'sum': float(a.sum())}

def bounds(geoms):
    bb = [g.bounds for g in geoms if g is not None and not g.is_empty]
    if not bb: return None
    return [min(b[0] for b in bb), min(b[1] for b in bb), max(b[2] for b in bb), max(b[3] for b in bb)]

def sphere_line(coords):
    a = np.asarray(coords)[:, :2]
    lon, lat = np.radians(a[:,0]), np.radians(a[:,1])
    h = np.sin(np.diff(lat)/2)**2 + np.cos(lat[:-1])*np.cos(lat[1:])*np.sin(np.diff(lon)/2)**2
    return float((2*R*np.arcsin(np.sqrt(np.clip(h,0,1)))).sum())

def sphere_ring(coords):
    a = np.asarray(coords)[:, :2]
    lon, lat = np.radians(a[:,0]), np.radians(a[:,1])
    return abs(float((np.diff(lon)*(np.sin(lat[:-1])+np.sin(lat[1:]))).sum()))*R*R/2

def approximate_area(g):
    if g.geom_type == 'Polygon':
        return sphere_ring(g.exterior.coords) - sum(sphere_ring(x.coords) for x in g.interiors)
    if g.geom_type == 'MultiPolygon': return sum(approximate_area(x) for x in g.geoms)
    return None

def approximate_length(g):
    if g.geom_type in {'LineString','LinearRing'}: return sphere_line(g.coords)
    if g.geom_type == 'MultiLineString': return sum(sphere_line(x.coords) for x in g.geoms)
    return None

class Graph:
    def __init__(self):
        self.indices = {}; self.parent = []; self.size = []; self.degree = []; self.edges = 0; self.zero_edges = 0
    def index(self, coord):
        c = tuple(coord[:2])
        if c not in self.indices:
            n = len(self.parent); self.indices[c] = n; self.parent.append(n); self.size.append(1); self.degree.append(0)
        return self.indices[c]
    def root(self, n):
        while self.parent[n] != n:
            self.parent[n] = self.parent[self.parent[n]]; n = self.parent[n]
        return n
    def add(self, a, b):
        a, b = self.index(a), self.index(b)
        if a == b: self.zero_edges += 1; return
        self.edges += 1; self.degree[a] += 1; self.degree[b] += 1
        x, y = self.root(a), self.root(b)
        if x != y:
            if self.size[x] < self.size[y]: x, y = y, x
            self.parent[y] = x; self.size[x] += self.size[y]
    def summary(self):
        cc = Counter(self.root(i) for i in range(len(self.parent)))
        sizes = sorted(cc.values(), reverse=True)
        return {'nodes': len(self.parent), 'edges_with_multiplicity': self.edges, 'zero_length_coordinate_edges': self.zero_edges,
                'connected_components': len(cc), 'largest_component_nodes': sizes[0] if sizes else 0,
                'largest_component_node_share': sizes[0]/len(self.parent) if sizes else 0,
                'largest_10_component_sizes': sizes[:10], 'degree_1_nodes': sum(v==1 for v in self.degree),
                'degree_0_nodes': sum(v==0 for v in self.degree), 'degree_ge_3_nodes': sum(v>=3 for v in self.degree)}

def reconstruct_boundary(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[(row['polygon_id'], row['ring_id'], row['role'])].append(row)
    polygon_groups = defaultdict(lambda: {'outer': [], 'inner': []})
    details = []; errors = []
    for (pid, rid, role), rr in sorted(groups.items()):
        rr = sorted(rr, key=lambda r: int(r['vertex_id']))
        coords = [(float(r['lon']),float(r['lat'])) for r in rr]
        closed = len(coords)>1 and coords[0]==coords[-1]
        ring = {'polygon_id': pid, 'ring_id': rid, 'role': role, 'vertices': len(coords),
                'distinct_coordinates': len(set(coords)), 'closed_in_original': closed,
                'duplicate_vertex_ids': len(rr)-len({r['vertex_id'] for r in rr})}
        details.append(ring)
        if not closed: errors.append({'polygon_id':pid,'ring_id':rid,'error':'ring_not_closed_in_original'})
        if role not in {'outer','inner'}: errors.append({'polygon_id':pid,'ring_id':rid,'error':'unexpected_role'}); continue
        polygon_groups[pid][role].append(coords)
    pp = []
    for pid, rings in sorted(polygon_groups.items()):
        if len(rings['outer']) != 1:
            errors.append({'polygon_id':pid,'error':'outer_ring_count_not_one','count':len(rings['outer'])}); continue
        pp.append(Polygon(rings['outer'][0],rings['inner']))
    g = pp[0] if len(pp)==1 else MultiPolygon(pp)
    return g, {'rings': details, 'polygon_count': len(pp), 'original_structure_errors': errors,
               'reconstructed_type': g.geom_type, 'valid': bool(g.is_valid), 'validity_reason': explain_validity(g),
               'bounds_lonlat': list(g.bounds), 'approx_spherical_area_km2': approximate_area(g)/1e6,
               'area_method': 'sphere R=6371008.8m; lonlat spherical ring integration; QA only, not cadastral area',
               'boundary_osm_relation_id': None, 'admin_level': None, 'boundary_source_date': None,
               'authoritative_city_boundary_identity_verified': False}

files = sorted(IN.glob('*.csv'))
assert len(files)==24
before = {p.name: sha(p) for p in files}
boundary_geoms = {}; boundary_results = {}
for city in CITIES:
    _, rr = load(IN / f'{city}_boundary.csv')
    g, result = reconstruct_boundary(rr)
    boundary_geoms[city] = g; boundary_results[city] = result

file_results = []; cross_ids = defaultdict(list); file_ids = {}
for path in files:
    city, layer = path.stem.split('_',1)
    headers, rr = load(path)
    missing = Counter(); na_literal = Counter(); malformed = []
    ids = Counter(); rowhashes = Counter(); type_counts = Counter(); parse_errors = []; invalid = []
    geoms = []; coords_bad = []; empty = 0; nonfinite = 0; duplicates_geoms = Counter()
    approx_ratios = []; length_ratios = []; tag_counts = {}
    for col in headers:
        if col in {'building','est_floor_area_src','highway','power','railway','water','landuse','poi_type','poi_value','role','city'}:
            tag_counts[col] = dict(Counter(r.get(col,'') for r in rr))
    bg = boundary_geoms[city]; prepared = prep(bg) if bg.is_valid else None
    cov = Counter(); cov_examples = defaultdict(list)
    for n, row in enumerate(rr,2):
        if None in row or any(v is None for v in row.values()): malformed.append(n)
        for k in headers:
            v = row.get(k,'') or ''
            if not v.strip(): missing[k] += 1
            elif v.strip().lower() in {'nan','null','none','na','n/a'}: na_literal[k] += 1
        rowhashes[hashlib.sha256(json.dumps(row,sort_keys=True,ensure_ascii=False).encode()).hexdigest()] += 1
        oid = row.get('osm_id')
        if oid is not None: ids[oid] += 1
        g = None
        if layer=='boundary':
            x,y = number(row.get('lon')),number(row.get('lat'))
            if x is not None and y is not None: g = Point(x,y)
        elif 'geometry' in headers:
            if row['geometry'].strip():
                try: g = wkt.loads(row['geometry'])
                except Exception as e: parse_errors.append({'csv_row':n,'osm_id':oid,'error':str(e)[:200]})
        else:
            x,y = number(row.get('centroid_lon')),number(row.get('centroid_lat'))
            if x is not None and y is not None: g = Point(x,y)
        if g is not None:
            type_counts[g.geom_type] += 1
            if g.is_empty: empty += 1
            if not g.is_valid: invalid.append({'csv_row':n,'osm_id':oid,'reason':explain_validity(g)})
            arr = shapely.get_coordinates(g)
            if not np.isfinite(arr).all(): nonfinite += 1
            if len(arr) and ((np.abs(arr[:,0])>180).any() or (np.abs(arr[:,1])>90).any()):
                coords_bad.append({'csv_row':n,'osm_id':oid})
            if layer!='boundary': duplicates_geoms[hashlib.sha256(g.normalize().wkb).hexdigest()] += 1
            if prepared and layer!='boundary':
                if g.is_valid and not g.is_empty:
                    try:
                        case = 'wholly_inside_or_on_boundary' if prepared.covers(g) else ('intersects_but_not_wholly_inside' if prepared.intersects(g) else 'wholly_outside')
                    except Exception: case = 'topology_exception'
                else: case = 'invalid_or_empty_geometry_skipped'
                cov[case] += 1
                if case != 'wholly_inside_or_on_boundary' and len(cov_examples[case])<10:
                    cov_examples[case].append({'csv_row':n,'osm_id':oid,'bounds':list(g.bounds)})
            if layer not in {'buildings','boundary'} and g.is_valid and not g.is_empty:
                av = number(row.get('area_m2'))
                computed = approximate_area(g)
                if av is not None and av>0 and computed is not None and computed>0: approx_ratios.append(av/computed)
                lv = number(row.get('length_m')); computed_l = approximate_length(g)
                if lv is not None and lv>0 and computed_l is not None and computed_l>0: length_ratios.append(lv/computed_l)
        geoms.append(g)
    if layer=='boundary':
        ids_result = {'available':False,'unique_osm_ids':None,'reason':'Only polygon/ring/vertex indices; no boundary relation ID'}
    else:
        for oid in ids: cross_ids[(city,oid)].append(layer)
        file_ids[path.name] = set(ids)
        ids_result = {'available':True,'unique_osm_ids':len(ids),'duplicate_id_extra_rows':sum(v-1 for v in ids.values()),
                      'duplicate_id_count':sum(v>1 for v in ids.values()),
                      'duplicates':dict((k,v) for k,v in ids.items() if v>1),
                      'invalid_prefixed_ids':[k for k in ids if not re.fullmatch(r'[nwr][1-9]\d*',k)],
                      'type_prefix_counts':dict(Counter(k[:1] for k in ids)),
                      'identity_external_osm_api_checked':False}
    numeric = {}
    for k in headers:
        if k in {'length_m','area_m2','footprint_area_m2','perimeter_m','est_levels','est_floor_area_m2','height_m','levels','voltage','lanes','maxspeed'}:
            vals=[number(r[k]) for r in rr if r[k].strip()]
            numeric[k]={'summary':stats(vals),'nonempty_non_numeric_rows':sum(v is None for v in vals),
                        'nonempty_non_numeric_examples':list(dict.fromkeys(r[k] for r in rr if r[k].strip() and number(r[k]) is None))[:15],
                        'zero_rows':sum(v==0 for v in vals),'negative_rows':sum(v is not None and v<0 for v in vals)}
    result = {'file':path.name,'absolute_source_file':str(path),'bytes':path.stat().st_size,'sha256':before[path.name],
              'data_rows':len(rr),'fields':headers,'blank_counts':dict((k,missing[k]) for k in headers),
              'literal_missing_token_counts':dict(na_literal),'malformed_csv_rows':malformed,
              'exact_duplicate_row_extra_count':sum(v-1 for v in rowhashes.values()),
              'osm_ids':ids_result,'numeric_fields':numeric,'tag_distributions':tag_counts,
              'geometry':{'representation':'boundary vertices' if layer=='boundary' else ('centroid points only; building footprint geometry absent' if layer=='buildings' else 'WKT'),
                          'types':dict(type_counts),'parsed_or_derived_geometry_rows':sum(g is not None for g in geoms),
                          'missing_geometry_rows':sum(g is None for g in geoms),'WKT_parse_errors':parse_errors,
                          'invalid_geometry_count':len(invalid),'invalid_geometry_records':invalid,
                          'empty_geometries':empty,'nonfinite_coordinate_geometries':nonfinite,
                          'coordinate_range_error_records':coords_bad,'bounds_lonlat':bounds(geoms),
                          'equivalent_geometry_duplicate_extra_count':sum(v-1 for v in duplicates_geoms.values()),
                          'validity_is_only_2D_topological_not_real_world_accuracy':True,
                          'building_footprint_polygon_validity_tested':False if layer=='buildings' else None},
              'CRS':{'declared_in_file':False,'WKT_has_SRID':any((r.get('geometry') or '').startswith('SRID=') for r in rr),
                     'inferred_coordinate_order':'x=longitude, y=latitude in decimal degrees',
                     'candidate_CRS':'WGS84 / EPSG:4326 based on OSM conventions and coordinate ranges; not verified export metadata',
                     'projection_for_original_metric_area_length':'unknown'},
              'coverage_against_supplied_city_boundary':{'boundary_valid':bool(bg.is_valid),'counts':dict(cov),
                                                         'outside_or_crossing_examples':dict(cov_examples),
                                                         'building_coverage_uses_centroid_only':layer=='buildings',
                                                         'does_not_establish_city_mapping_completeness':True},
              'metric_QA':{'stored_area_over_approx_spherical_area_ratio':stats(approx_ratios),
                           'stored_length_over_haversine_polyline_length_ratio':stats(length_ratios),
                           'method_is_diagnostic_not_reprojection_or_source_formula_proof':True},
              'source_metadata':{'retrieval_time':None,'query_or_bbox':None,'OSM_snapshot_time':None,'element_versions':None,
                                 'source_urls':None,'licenses_in_file':None}}
    if layer=='boundary': result['boundary_reconstruction'] = boundary_results[city]
    if layer=='buildings':
        source = Counter(r['est_floor_area_src'] for r in rr)
        area_errors=[]; level_errors=[]; height_matches=Counter(); height_count=0; height_examples=[]
        for n,row in enumerate(rr,2):
            fp,l,fl = number(row['footprint_area_m2']),number(row['est_levels']),number(row['est_floor_area_m2'])
            lv,h = number(row['levels']),number(row['height_m'])
            if fp is not None and l is not None and fl is not None:
                error=fl-fp*l
                if abs(error)>.050001*abs(l)+.050001:
                    area_errors.append({'csv_row':n,'osm_id':row['osm_id'],'difference_m2':error,'est_source':row['est_floor_area_src']})
            if row['est_floor_area_src']=='levels' and l is not None and lv is not None and abs(l-lv)>1e-9:
                level_errors.append({'csv_row':n,'osm_id':row['osm_id'],'levels':lv,'est_levels':l})
            if row['est_floor_area_src']=='height' and h is not None and l is not None:
                height_count+=1
                for name,v in {'floor_h_div_3':max(1,math.floor(h/3)), 'ceil_h_div_3':max(1,math.ceil(h/3)),
                               'round_half_up_h_div_3':max(1,math.floor(h/3+.5)), 'python_round_h_div_3':max(1,round(h/3))}.items():
                    if abs(l-v)<1e-9: height_matches[name]+=1
                if len(height_examples)<12: height_examples.append({'osm_id':row['osm_id'],'height_m':h,'est_levels':l})
        result['building_audit']={'row_count':len(rr),'estimate_source_counts':dict(source),
            'levels_nonempty':len(rr)-missing['levels'],'height_nonempty':len(rr)-missing['height_m'],
            'estimated_floor_area_nonempty':len(rr)-missing['est_floor_area_m2'],
            'estimated_floor_area_missing':missing['est_floor_area_m2'],
            'floor_area_completeness_fraction':(len(rr)-missing['est_floor_area_m2'])/len(rr) if rr else 0,
            'footprint_polygon_not_supplied':True,'footprint_area_original_method_not_supplied':True,
            'floor_area_formula_checked':'est_floor_area_m2 ~= footprint_area_m2 * est_levels; tolerance accounts for two displayed 0.1m² rounded values',
            'floor_area_product_mismatches':area_errors,'tag_levels_vs_est_levels_mismatches':level_errors,
            'height_inference':{'height_sourced_rows_with_numeric_inputs':height_count,'candidate_3m_floor_rules_match_counts':dict(height_matches),
                                'examples':height_examples,'actual_export_script_or_formula_found':False,
                                'candidate_match_is_inference_not_verified_rule':True},
            'building_use_is_OSM_tag_not_confirmed_heated_use':True,
            'unknown_actual_parameters':['heated/gross/net area','original footprint vertices','surveyed levels','envelope U-values/areas','orientation','ventilation/infiltration','H/C or 2R2C parameters','measured heat/room temperature','occupancy/service calendars']}
    if layer=='roads':
        ep=Graph(); vp=Graph(); way_components=[]; line_rows=0
        for g in geoms:
            if g is None or g.is_empty: continue
            lines=[g] if g.geom_type=='LineString' else list(g.geoms) if g.geom_type=='MultiLineString' else []
            for line in lines:
                c=list(line.coords);line_rows+=1;ep.add(c[0],c[-1])
                for a,b in zip(c,c[1:]):vp.add(a,b)
        road_cc=Counter()
        for g in geoms:
            if g is not None and g.geom_type=='LineString' and len(g.coords):
                road_cc[vp.root(vp.indices[tuple(g.coords[0][:2])])]+=1
        result['road_connectivity']={'endpoint_only_graph':ep.summary(),'all_vertex_exact_coordinate_graph':vp.summary(),
            'all_vertex_graph_way_rows_per_component_largest_10':sorted(road_cc.values(),reverse=True)[:10],
            'all_vertex_graph_largest_component_way_row_share':max(road_cc.values())/sum(road_cc.values()) if road_cc else 0,
            'mode':'undirected exact-coordinate graph, raw vertices, no snapping, no inferred intersection split, no crossing connections',
            'OSM_node_ids_or_refs_present':False,'from_to_node_ids_present':False,
            'bridge_tunnel_layer_access_and_turn_relations_present':False,
            'edge_rows_are_whole_OSM_ways_not_topologically_split_edges':True,
            'limitations':['geometry cannot prove original OSM node identity','endpoint graph misses shared internal vertices',
                           'all-vertex coordinate graph may merge vertically separated features','missing turn/access/layer and clipping rules prevent authoritative road routing',
                           'road route is a possible corridor, not a district-heating pipe or excavation permit']}
    file_results.append(result)
    print(json.dumps({'file':path.name,'rows':len(rr),'invalid_geometries':len(invalid),'parse_errors':len(parse_errors)},ensure_ascii=True),flush=True)

cross = [{'city':CITIES[city],'osm_id':oid,'layers':layers} for (city,oid),layers in cross_ids.items() if len(layers)>1]
cross_summary = {CITIES[c]:dict(Counter('/'.join(sorted(x['layers'])) for x in cross if x['city']==CITIES[c])) for c in CITIES}
shared_cities=[]
for c1,c2 in [('guyuan','urumqi'),('guyuan','wuhan'),('urumqi','wuhan')]:
    a=set().union(*(s for fn,s in file_ids.items() if fn.startswith(c1+'_')))
    b=set().union(*(s for fn,s in file_ids.items() if fn.startswith(c2+'_')))
    shared_cities.append({'city1':CITIES[c1],'city2':CITIES[c2],'same_typed_osm_ids':len(a&b),'examples':sorted(a&b)[:20]})
source_search={'dataset_metadata_files_found':[],'dataset_download_scripts_found':[],
               'dataset_directory_file_inventory':[p.relative_to(IN.parent).as_posix() for p in IN.parent.rglob('*') if p.is_file() and 'BDG2' not in p.parts],
               'zip_non_csv_metadata_files':[],
               'source_scripts_search_scope':'datasets tree and current V10 text source; filename leads across workspace; no BDG2 rows read',
               'original_OSM_query_snapshot_and_reconstruction_method_verifiable':False}
z=IN.parent.parent/'datasets.zip'
if z.exists():
    with zipfile.ZipFile(z) as zz: source_search['zip_non_csv_metadata_files']=[x.filename for x in zz.infolist() if not x.filename.endswith('/') and not x.filename.lower().endswith('.csv')]
assert not source_search['zip_non_csv_metadata_files']

sources=[
 {'url':'https://wiki.openstreetmap.org/wiki/Elements','access_date':'2026-10-02','supports':'nodes/ways/relations have separate ID namespaces; native nodes use WGS84 latitude/longitude; original ways carry ordered node references'},
 {'url':'https://wiki.openstreetmap.org/wiki/Key:building:levels','access_date':'2026-10-02','supports':'above-ground facade levels exclude roof and underground levels; conventional 3m render assumption is not measured heated floor area'},
 {'url':'https://wiki.openstreetmap.org/wiki/Key:height','access_date':'2026-10-02','supports':'vertical height tag; source:height optional; height tag is not a thermal/capacity observation'},
 {'url':'https://wiki.openstreetmap.org/wiki/Tag:power=plant','access_date':'2026-10-02','supports':'plant is facility identity; separate plant:output:* attributes would describe output; CSV exports lack capacity and hourly availability'}]

score_map=[
 {'criterion':'模型架构','weight':20,'added':'候选道路走廊、用地/水/铁路约束及建筑/设施位置线索','missing':'真实热管网/源网荷储资产及工况；不能直接代替模型资产'},
 {'criterion':'动态仿真','weight':15,'added':'建筑位置和部分形态参考','missing':'H/C、2R2C、室内/热计量、服务日程；地图无动态验证'},
 {'criterion':'场景完整度','weight':15,'added':'三城8类空间层均有数据文件','missing':'官方边界与导出范围、OSM漏绘率及具体完整工程案例'},
 {'criterion':'多目标','weight':15,'added':'可为候选线路长度/空间冲突筛选提供输入','missing':'实际管道报价、容量、热损、价格与碳/收益边界；不能增加已求解目标结果证据'},
 {'criterion':'多尺度','weight':10,'added':'city—设施—建筑位置可用于设计空间映射','missing':'城市—片区—热站—楼宇真实归属/服务边界/资源约束及受控传递试验'},
 {'criterion':'分析可视化','weight':15,'added':'提供底图、POI与空间约束图层','missing':'有效范围标注/无效几何处理与服务供热结果映射；地图不能证明节能收益'},
 {'criterion':'代码质量','weight':10,'added':'24文件全量CSV/ID/几何/覆盖统计和原件哈希','missing':'下载回执、快照/CRS、原始OSM节点/版本、转换脚本及当前输入适配'}]
audit={'schema_version':1,'audit_date_local':'2026-10-02','input_directory':str(IN),'write_directory':str(OUT),
       'scope':'all 24 OSM CSV read-only; no BDG2 table audit; no optimization',
       'runtime':{'python':sys.version.split()[0],'shapely':shapely.__version__,'numpy':np.__version__,
                  'bundled_dependencies_used':True,'pyproj_available':False},
       'file_count':len(files),'total_data_rows':sum(r['data_rows'] for r in file_results),
       'files':file_results,'city_boundaries':boundary_results,'cross_layer_overlapping_ids':cross,
       'cross_layer_overlap_summary':cross_summary,'cross_city_id_overlap':shared_cities,
       'cross_layer_same_OSM_ID_can_be_normal_multitag_feature_not_independent_evidence':True,
       'provenance_search':source_search,'authoritative_field_semantics_sources':sources,
       'scoring_association':score_map,'points_awarded_or_invented':False,
       'missing_engineering_inputs':['approved/finished heating pipe GIS and endpoint mapping','pipe actual diameter/insulation/pressure/flow/pumps/capacity/cost',
                                     'building heated area, use, envelope and thermal parameters plus observed loads',
                                     'source equipment verified capacity/COP and hourly waste heat availability','water/geothermal extraction permits and measured flow/temperature',
                                     'electrical connection/transformer capacity and project tariffs','authoritative boundary and project service-area mapping',
                                     'OSM query, server, date/version, CRS and original element/node records; area/floor estimation script'],
       'actual_project_data_complete':False,'osm_originals_unchanged':all(sha(p)==before[p.name] for p in files),
       'solver_run':False,'original_csv_modified':False,'field_level_records_corrected':False}
assert audit['osm_originals_unchanged']
OUT.mkdir(parents=True,exist_ok=True)
(OUT/'audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'files':24,'rows':audit['total_data_rows'],'invalid_geometry_total':sum(r['geometry']['invalid_geometry_count'] for r in file_results),'originals_unchanged':True},ensure_ascii=True),flush=True)
