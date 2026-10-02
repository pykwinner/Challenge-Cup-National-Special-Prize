from pathlib import Path
from collections import Counter
import csv,hashlib,json

OUT=Path(__file__).resolve().parent
IN=Path('D:/competion/xiaotiao/datasets/osm')
csv.field_size_limit(20000000)
a=json.loads((OUT/'audit.json').read_text(encoding='utf-8'))
sha=lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
controls=[]
for f in a['files']:
    path=IN/f['file']
    with path.open(encoding='utf-8-sig',newline='') as fp:
        reader=csv.reader(fp);header=next(reader);rr=list(reader)
    assert header==f['fields'] and len(rr)==f['data_rows']
    assert all(len(r)==len(header) for r in rr)
    assert sha(path)==f['sha256']
    if 'osm_id' in header:
        ix=header.index('osm_id');ids=Counter(r[ix] for r in rr)
        assert len(ids)==f['osm_ids']['unique_osm_ids']
        assert sum(v-1 for v in ids.values())==f['osm_ids']['duplicate_id_extra_rows']
    if f['file'].endswith('_buildings.csv'):
        assert 'building_audit' in f
        ix=header.index('est_floor_area_m2');available=sum(bool(r[ix].strip()) for r in rr)
        assert available==f['building_audit']['estimated_floor_area_nonempty']
        assert f['building_audit']['estimated_floor_area_missing']==len(rr)-available
    if f['file'].endswith('_roads.csv'):
        assert 'road_connectivity' in f
        graph=f['road_connectivity']['all_vertex_exact_coordinate_graph']
        assert graph['nodes']>0 and graph['edges_with_multiplicity']>0 and graph['connected_components']>0
        assert f['metric_QA']['stored_length_over_haversine_polyline_length_ratio']['count']==len(rr)
    if f['file'].endswith(('_landuse.csv','_water.csv')):
        assert f['metric_QA']['stored_area_over_approx_spherical_area_ratio']['count']>0
    controls.append({'file':f['file'],'rows':len(rr),'fields':len(header),'input_sha256_matches':True})
assert sum(c['rows'] for c in controls)==a['total_data_rows']==271975
assert len(controls)==24
assert all(g['valid'] and not g['original_structure_errors'] for g in a['city_boundaries'].values())
assert sum(f['geometry']['invalid_geometry_count'] for f in a['files'])==4
for s in a['bounded_current_OSM_API_spot_checks']:
    if s['status']=='retrieved':assert sha(OUT/s['local_file'])==s['sha256']
report=(OUT/'README.md').read_text(encoding='utf-8')
assert '\ufffd' not in report and '1188' in report and '1321' not in report
assert all(f'{v:,}' in report for v in [3531,27194,56207,17758,15015])
verification={'audit_date_local':'2026-10-02','independent_csv_row_header_width_ID_count_controls':controls,
              'all_24_original_CSV_sha_unchanged':True,'building_audit_present_for_3_cities':True,
              'road_connectivity_present_for_3_cities':True,'metric_QA_branches_nonempty':True,
              'building_estimate_counts_reconciled':True,'boundary_rings_closed_valid':True,
              'four_invalid_Wuhan_POI_polygons_reported':True,'README_and_audit_totals_reconciled':True,
              'actual_project_completeness_certified':False,'optimization_run':False,
              'analysis_script_identities':{p.name:sha(p) for p in OUT.glob('*.py')}}
(OUT/'verification.json').write_text(json.dumps(verification,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
manifest={'audit_date_local':'2026-10-02','scope':str(OUT),'files':[{'file':p.relative_to(OUT).as_posix(),'bytes':p.stat().st_size,'sha256':sha(p)}
          for p in sorted(OUT.rglob('*')) if p.is_file() and p.name!='manifest.json'],'manifest_excludes_itself':True}
(OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'all_24_originals_unchanged':True,'data_rows':a['total_data_rows'],'building_branches':3,'road_branches':3,'QA_passed':True}))
