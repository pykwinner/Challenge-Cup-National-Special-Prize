from pathlib import Path
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import csv, hashlib, json, math, urllib.request, sys
from datetime import datetime, timezone
from shapely import wkt

OUT=Path(__file__).resolve().parent
IN=Path('D:/competion/xiaotiao/datasets/osm')
csv.field_size_limit(20000000)
sys.stdout.reconfigure(encoding='utf-8')
a=json.loads((OUT/'audit.json').read_text(encoding='utf-8'))
byfile={x['file']:x for x in a['files']}
cities={'guyuan':'固原','urumqi':'乌鲁木齐','wuhan':'武汉'}

def load(path):
    with path.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))

for city in cities:
    b=load(IN/f'{city}_buildings.csv');bm={r['osm_id']:r for r in b}
    matched={};matched_sources=defaultdict(list)
    for layer in ['landuse','pois','water']:
        for row in load(IN/f'{city}_{layer}.csv'):
            if row['osm_id'] not in bm:continue
            g=wkt.loads(row['geometry'])
            if g.geom_type=='Polygon':
                matched[row['osm_id']]=g;matched_sources[row['osm_id']].append(layer)
    br=byfile[f'{city}_buildings.csv']['building_audit']
    br['footprint_polygon_recoverable_by_same_id_from_other_supplied_polygon_layers']={
        'building_ids':len(matched),'share_of_building_rows':len(matched)/len(b),
        'invalid_joined_polygons':sum(not g.is_valid for g in matched.values()),
        'matching_layers':dict(Counter('/'.join(sorted(v)) for v in matched_sources.values())),
        'not_complete_building_geometry_and_not_an_independent_survey':True}
    br['source_label_present_but_estimate_missing_records']=[{'csv_row':i,'record':r} for i,r in enumerate(b,2) if r['est_floor_area_src'] and not r['est_floor_area_m2']]
    br['generic_building_yes_count']=sum(r['building']=='yes' for r in b)
    br['non_heated_or_structure_only_tags_require_screening']={k:v for k,v in Counter(r['building'] for r in b).items() if k in {'dam','roof','greenhouse','ruins','construction','storage_tank','carport','bridge','water_tower','kiln','pavilion','garage'}}
    pr=byfile[f'{city}_power.csv'];power=load(IN/f'{city}_power.csv')
    pr['geometry']['closed_LineString_count_by_power_tag']=dict(Counter(r['power'] for r in power if wkt.loads(r['geometry']).is_closed))
    pr['power_audit']={'all_exported_ids_are_ways':True,'node_and_relation_features_not_present':True,
        'plant_generator_substation_are_candidates_not_available_heat_or_grid_capacity':True,
        'closed_lines_can_represent_facility_boundaries_not_transmission_routes':True,
        'voltage_multivalue_is_valid_tag_syntax_not_necessarily_corruption':True,
        'missing':['plant:output:electricity','plant:output:heat','generator output','source temperature/flow','connection capacity','transformer capacity','operating state','hourly availability','permits/tariffs']}

# A bounded primary-API spot check does not replace the missing original export receipt.
def spot(oid):
    url=f'https://api.openstreetmap.org/api/0.6/way/{oid}.json'
    result={'url':url,'typed_osm_id':'w'+oid,'reviewed_at_utc':datetime.now(timezone.utc).isoformat(),
            'scope':'current individual object identity/tag spot check only; original CSV snapshot date remains unknown',
            'prior_web_tool_attempt':'not_accessible_via_web_tool'}
    try:
        existing=OUT/f'evidence/osm_way_{oid}_current.json'
        if existing.exists():
            data=existing.read_bytes();result['http_status']=200
            result['local_prior_successful_response_reused']=True
        else:
            req=urllib.request.Request(url,headers={'User-Agent':'Codex-readonly-OSM-audit/1.0'})
            with urllib.request.urlopen(req,timeout=15) as response:
                data=response.read();result['http_status']=response.status
        path=OUT/f'evidence/osm_way_{oid}_current.json';path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
        obj=json.loads(data)['elements'][0]
        result.update({'status':'retrieved','local_file':path.relative_to(OUT).as_posix(),
                       'sha256':hashlib.sha256(data).hexdigest(),'object_version':obj.get('version'),
                       'object_timestamp':obj.get('timestamp'),'node_refs_present':bool(obj.get('nodes')),
                       'tags':obj.get('tags',{}),'closed_way_by_node_ids':bool(obj.get('nodes')) and obj['nodes'][0]==obj['nodes'][-1]})
    except Exception as e:result.update({'status':'failed','error':type(e).__name__+': '+str(e)[:300]})
    return result

with ThreadPoolExecutor(max_workers=2) as pool:a['bounded_current_OSM_API_spot_checks']=list(pool.map(spot,['428700030','37407276']))
for check in a['bounded_current_OSM_API_spot_checks']:
    if check['status']=='retrieved':
        for f in a['files']:
            if f['file'] in {'guyuan_buildings.csv','guyuan_water.csv','urumqi_power.csv'}:
                f['osm_ids']['bounded_current_individual_spot_checks']=a['bounded_current_OSM_API_spot_checks']

a['summary']={
    'boundary_vertex_rows':sum(x['data_rows'] for x in a['files'] if x['file'].endswith('_boundary.csv')),
    'non_boundary_feature_rows':sum(x['data_rows'] for x in a['files'] if not x['file'].endswith('_boundary.csv')),
    'OSM_id_duplicate_extra_rows_within_files':sum(x['osm_ids'].get('duplicate_id_extra_rows',0) for x in a['files']),
    'invalid_WKT_polygons':sum(x['geometry']['invalid_geometry_count'] for x in a['files']),
    'WKT_parse_errors':sum(len(x['geometry']['WKT_parse_errors']) for x in a['files']),
    'cross_boundary_features':sum(x['coverage_against_supplied_city_boundary']['counts'].get('intersects_but_not_wholly_inside',0) for x in a['files']),
    'wholly_outside_features_or_centroids':sum(x['coverage_against_supplied_city_boundary']['counts'].get('wholly_outside',0) for x in a['files']),
    'source_labeled_missing_floor_estimates':sum(len(x.get('building_audit',{}).get('source_label_present_but_estimate_missing_records',[])) for x in a['files']),
    'footprint_polygons_available_in_building_csv':0}
a['osm_originals_unchanged']=all(hashlib.sha256((IN/x['file']).read_bytes()).hexdigest()==x['sha256'] for x in a['files'])
assert a['osm_originals_unchanged']
(OUT/'audit.json').write_text(json.dumps(a,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

md='''# 三城市OSM数据完整性审计（2026-10-02）

**OSM空间背景数据已经增加，实际供热工程数据仍不齐。** 三城各8类CSV，共24个文件、271,975数据行，其中13,748行是边界顶点，258,227行是图层要素记录，不能把所有行都称作独立设施。逐文件原件SHA256、完整字段、空值、ID、WKT和覆盖统计见[audit.json](audit.json)。原CSV读取前后哈希一致；本轮不改CSV、不审BDG2大表、不运行优化。

新增的是城市边界几何、建筑位置与部分形态估计、道路走廊、用地/水体/铁路约束和POI/电力设施线索。道路不能称为实际热管网，估算楼面不能称为实测面积，电力/水体设施不能称为可用热源或接入容量。

## 逐文件结果

“点*”为从CSV坐标建立的质心点，用于坐标/覆盖检查，未提供建筑轮廓。“边界”行数是顶点数，ID为本地polygon/ring/vertex索引，不是OSM实体ID。ID核查保留n/w/r类型前缀。

| 城市/文件 | 数据行 | 唯一OSM ID | 保存的几何 | 无效几何 | 跨界 | 完全界外 |
|---|---:|---:|---|---:|---:|---:|
'''
for x in a['files']:
    city,layer=x['file'].split('_',1)
    if layer=='boundary.csv':geom='顶点→'+a['city_boundaries'][city]['reconstructed_type'];uid='未提供'
    elif layer=='buildings.csv':geom='点*';uid=str(x['osm_ids']['unique_osm_ids'])
    else:geom='/'.join(f'{k}:{v}' for k,v in x['geometry']['types'].items());uid=str(x['osm_ids']['unique_osm_ids'])
    c=x['coverage_against_supplied_city_boundary']['counts']
    md+=f"| {cities[city]}/{layer} | {x['data_rows']:,} | {uid} | {geom} | {x['geometry']['invalid_geometry_count']} | {c.get('intersects_but_not_wholly_inside',0)} | {c.get('wholly_outside',0)} |\n"
md+='''
21个有osm_id的文件均没有文件内重复ID、完全重复行或不合格式的类型前缀ID。三城之间没有共享的带类型OSM ID。跨图层同ID要素须按实体关联，不能重复计算为独立建筑/热源：固原197、乌鲁木齐572、武汉1527个实体出现在两个图层。其同ID复用可来自多标签；固原66个building=dam同时在水层，不能并入住宅供热面积。武汉建筑有1对相同质心、POI有2对等价几何但ID不同，需要对象复核，不能直接删去。

武汉`wuhan_pois.csv`有4个自交多边形：w404679832（CSV第7221行）、w733293308（7868）、w880467445（8569）、w880511721（8581）。已在JSON逐项记录，不在本轮修复。这4项未参与有效几何覆盖判定。其余保存的WKT均可解析并通过二维有效性检查；建筑仅质心通过点检查，不代表其未保存的轮廓有效。

## 建筑面积和用途仍不完整

| 城市 | 建筑层记录 | 有levels | 有height_m | 已填估算楼面 | 估算楼面缺失 | 填充率 | 估算来源 |
|---|---:|---:|---:|---:|---:|---:|---|
'''
for city,name in cities.items():
    b=byfile[f'{city}_buildings.csv']['building_audit'];src=b['estimate_source_counts']
    md+=f"| {name} | {b['row_count']:,} | {b['levels_nonempty']:,} | {b['height_nonempty']:,} | {b['estimated_floor_area_nonempty']:,} | {b['estimated_floor_area_missing']:,} | {100*b['floor_area_completeness_fraction']:.2f}% | levels:{src.get('levels',0)}；height:{src.get('height',0)} |\n"
md+='''
三城建筑CSV均没有geometry、原始轮廓顶点或OSM node refs，只有质心、footprint_area_m2和perimeter_m；原占地计算法/投影/面积修正/轮廓有效性无法全量复算。部分同ID多边形可从POI/用地/水层回找，不能因此宣称完整建筑轮廓已取得。已填楼面在显示舍入容差内均符合“占地×估算层数”，但这不是不动产权面积、实际受热面积或现场测量。

高度来源的有效数字样本中，乌鲁木齐21条、武汉8917条与Python `round(height_m/3)`的结果吻合。这只是由数据推断的3m层高/取整规律，没有找到导出脚本；真实层高、屋顶/地下/架空层及功能不能据此确定。有5条虽然写了估算来源，却未填估算值（乌鲁木齐1、武汉4），具体原行已记录。OSM [building:levels定义](https://wiki.openstreetmap.org/wiki/Key:building:levels)是地上外立面层数，排除屋顶和地下层；其常见渲染假设不能认证实际楼面或供热面积。

`building=yes`未给具体用途，且存在dam/roof/greenhouse/ruins/construction/storage_tank等结构或未必采暖类型。建筑CSV不含朝向/围护构造、U值、热容、通风渗透、热计量、室温、占用及服务日历，H/C和2R2C不能直接由面积表补齐。用途分类、热服务边界和资产实况须另核。

## CRS、城市范围及连通性

文件没有CRS声明/SRID、来源时间或查询条件。坐标列/几何x、y与经度、纬度范围一致，结合[OSM原生数据模型](https://wiki.openstreetmap.org/wiki/Elements)可将WGS84/EPSG:4326作为待确认的导出CRS，不能把缺失元数据写成已确认。平方米/米字段的原投影或地球模型未知。本轮按平均球半径做面积和Haversine长度数量级复核，未把度直接当米，也未修改原数值；这不能证明原处理算法和法定测量精度。

| 城市 | 重建边界 | 外环/内环 | 边界经纬度范围（西/南/东/北） | 球面近似面积km² |
|---|---|---|---|---:|
'''
for city,name in cities.items():
    b=a['city_boundaries'][city];roles=Counter(r['role'] for r in b['rings'])
    md+=f"| {name} | {b['reconstructed_type']}有效 | {roles.get('outer',0)}/{roles.get('inner',0)} | {'/'.join(f'{v:.7f}' for v in b['bounds_lonlat'])} | {b['approx_spherical_area_km2']:.1f} |\n"
md+='''
各环原件已闭合，polygon/ring/vertex键无重复，武汉3个内环得到保留。但均缺boundary relation ID、admin_level、来源日期和官方行政边界核对；有效闭合不能证明法定边界、完整城市覆盖或项目服务范围。乌鲁木齐范围延及达坂城等地区，与此前中心城区供热规划范围不同，必须按项目筛选。

相对于所给边界，共有1188条有效线/面跨界、9条要素/质心完全界外（固原建筑1，武汉建筑2、POI6）。电力线延伸最远，武汉power的西端达到111.2543204°，不是所有要素均已被裁成市内片段。跨界可能是完整way保留造成，未取得查询回执，原因不能确定。跨界线全长/多边形全面积不能计入纯市内总量。建筑只按质心核覆盖，无法判定全部轮廓是否跨界；映射覆盖率没有完整权威建筑母表作分母，不能以“文件有很多行”证明城市建筑已全部采集。

本轮建立无向几何诊断图，两种处理均没有人为吸附、交叉点切分或竖向判断：

| 城市 | 只连接way端点的连通分量 | 连每个相邻顶点的分量 | 顶点图最大分量节点占比 |
|---|---:|---:|---:|
'''
for city,name in cities.items():
    r=byfile[f'{city}_roads.csv']['road_connectivity']
    md+=f"| {name} | {r['endpoint_only_graph']['connected_components']:,} | {r['all_vertex_exact_coordinate_graph']['connected_components']:,} | {100*r['all_vertex_exact_coordinate_graph']['largest_component_node_share']:.2f}% |\n"
md+='''
端点图会漏掉在way中部共用的顶点；顶点坐标图又可能误合并不同OSM节点或不同高度道路。道路包含施工、拟建、步行、台阶、raceway等类型，不全是现成可施工走廊。没有原始node_id/ref、from/to、bridge/tunnel/layer/access、转向限制/原始relation，oneway空白也不能统一当False。导入前需恢复原始拓扑、确定网络规则、筛选现状道路与空间约束、核准可施工路径；这些结果不认证道路路由，更不认证热网。

电力层全部是way LineString，包含plant/substation/generator等闭合设施边界，其length_m可能是厂区轮廓周长，不全是输电线路长度。电压字段有分号多值，不能简单float转换后当损坏数据。OSM [power=plant定义](https://wiki.openstreetmap.org/wiki/Tag:power=plant)是设施身份，热/电输出有单独标签；本CSV没提供容量、运行状态、COP、余热温度流量或逐时可用率。水层包含水面、湿地和坝，未提供取排水许可、流量/水温及热资源保证。

## 来源和评分关联

所查`datasets`目录及ZIP只有34个CSV（其中BDG2不在本次表内容审计范围），没有README、原始OSM XML/JSON/PBF、下载脚本、query/bbox、Overpass服务器/日期、快照/element版本、CRS或估算说明。当前V10文本/脚本搜索仅发现OSM许可索引，未发现这24文件的下载/转换链。本轮两个当前OSM API对象的有限核查结果（取得或失败均记录）不能替代当时的完整下载回执、证明全部实体仍当前或认证历史覆盖。

两条原生OSM API个例读取成功，原响应保存在本审计目录`evidence/`。w428700030同时带building=dam和waterway=dam，支持同实体多图层解释。w37407276是闭合厂区way，现有标签列plant:output:electricity=660 MW；这是社区地图标签中的名义电功率，未进入原CSV，也不是供热能力或逐时可用余热。未把该个例数字补入正式模型。

| 评分项（沿用原报告权重） | 新增支撑 | 仍缺的对应证据 |
|---|---|---|
'''
for x in a['scoring_association']:
    md+=f"| {x['criterion']}{x['weight']} | {x['added']} | {x['missing']} |\n"
md+='''
以上是证据与评分项的关联，没有给出增分数或把地图数据等同已完成实验。最关键下一批材料是实际热管网竣工GIS和水力/造价、具体建筑受热面积/围护与动态观测、设备设计验收功率/性能及余热时序、供电接入容量和费用、水地资源许可，以及可追溯的OSM导出方法和资产映射。
'''
# Keep manually summarized totals tied to independent recomputed source controls.
assert a['summary']['boundary_vertex_rows']==13748 and a['summary']['non_boundary_feature_rows']==258227
assert a['summary']['cross_boundary_features']==1188 and a['summary']['wholly_outside_features_or_centroids']==9
assert len(a['cross_layer_overlapping_ids'])==197+572+1527
(OUT/'README.md').write_text(md,encoding='utf-8')
print(json.dumps({'rows':a['total_data_rows'],'summary':a['summary'],'spot_check_statuses':[s['status'] for s in a['bounded_current_OSM_API_spot_checks']],
                  'partial_footprint_join_counts':{c:byfile[f'{c}_buildings.csv']['building_audit']['footprint_polygon_recoverable_by_same_id_from_other_supplied_polygon_layers']['building_ids'] for c in cities}},ensure_ascii=False))
