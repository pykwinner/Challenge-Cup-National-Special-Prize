"""Recompute real BDG2 outputs from locked CSVs without importing benchmark/core."""
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import sys
sys.dont_write_bytecode=True
import numpy as np
import pandas as pd
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
OUT=ROOT/'docs/execution_20261002/external_benchmark';DATA=ROOT.parents[1]/'datasets/BDG2'
AUDIT=ROOT/'docs/source_intake_20261002/datasets_batch_03'
def load(p):return json.loads(p.read_text(encoding='utf-8'))
def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()
issues=[];checks=0;numeric_checks=0
def check(test,label,details=None):
    global checks
    checks+=1
    if not test:issues.append({'check':label,'detail':details})
def compare(a,b,label):
    global numeric_checks
    numeric_checks+=1
    if a is None or b is None or isinstance(b,(bool,str)):
        check(a==b,label,{'reported':a,'recomputed':b})
    else:check(bool(np.isclose(float(a),float(b),rtol=2e-9,atol=1e-9)),label,{'reported':a,'recomputed':b})
lock=load(OUT/'protocol_lock.json');selection=load(OUT/'selection.json')
artifact_hashes={r['path']:sha(ROOT/r['path']) for r in lock['files']}
for r in lock['files']:check(artifact_hashes[r['path']]==r['sha256'],f'protocol lock {r["path"]}')
source_hashes={name:sha(DATA/name) for name in lock['sources']}
for name,r in lock['sources'].items():check(source_hashes[name]==r['sha256'],f'raw source hash {name}')
# Independently regenerate the stable-ID selection from the earlier full inventories.
seen=set();selected_ids=[];rejection_counts={}
for category in ['hotwater','steam','chilledwater']:
    inv=load(AUDIT/f'{category}_cleaned_channels.json');queues=defaultdict(list);reject2016=0
    for r in sorted(inv,key=lambda v:(v['site_id'],v['building_id'])):
        y=r['years']['2016'];area=r.get('sqm')
        good=y['coverage']>=.95 and y['weather_join_coverage']>=.95 and y['negative']==0 and y['positive']>0 and area is not None and np.isfinite(area) and area>0
        if good:queues[r['site_id']].append(r)
        else:reject2016+=1
    chosen=[]
    while len(chosen)<4:
        progress=False
        for site in sorted(queues):
            if len(chosen)>=4:break
            while queues[site]:
                r=queues[site].pop(0);y=r['years']['2017']
                if r['building_id'] in seen or y['coverage']<.95 or y['weather_join_coverage']<.95 or y['negative']>0:continue
                seen.add(r['building_id']);chosen.append(r['building_id']);progress=True;break
        if not progress:break
    selected_ids.extend((category,b) for b in chosen);rejection_counts[category]=reject2016
check(selected_ids==[(c['category'],c['building_id']) for c in selection['cases']],'independent stable sample selection')
metadata=pd.read_csv(DATA/'metadata.csv').set_index('building_id')
weather=pd.read_csv(DATA/'weather.csv',usecols=['timestamp','site_id','airTemperature'])
weather['timestamp']=pd.to_datetime(weather.timestamp)
check(not weather.duplicated(['site_id','timestamp']).any(),'weather source identity uniqueness')
meters={}
for category in ['hotwater','steam','chilledwater']:
    ids=[c['building_id'] for c in selection['cases'] if c['category']==category]
    m=pd.read_csv(DATA/f'{category}_cleaned.csv',usecols=['timestamp',*ids]);m['timestamp']=pd.to_datetime(m.timestamp)
    check(m.timestamp.is_unique and m.timestamp.is_monotonic_increasing,f'{category}: raw meter chronology')
    meters[category]=m.set_index('timestamp')
version_records={};reviewed_files=[]
for version in ['legacy','new_core']:
    folder=OUT/'versions'/version;vlock=load(folder/'version_lock.json');manifest=load(folder/'run_manifest.json')
    check(sha(ROOT/vlock['snapshot'])==vlock['core_sha256'],f'{version}: frozen core hash')
    check(vlock['protocol_lock_sha256']==sha(OUT/'protocol_lock.json'),f'{version}: protocol lock linkage')
    for r in manifest['files']:
        check(sha(ROOT/r['path'])==r['sha256'],f'{version}: run artifact hash {r["path"]}')
        reviewed_files.append(ROOT/r['path'])
    version_records[version]=load(folder/'results.json')
    expected=48 if version=='legacy' else 72
    check(len(version_records[version])==expected,f'{version}: no method records disappear')
    failures=[r for r in version_records[version] if r['status']!='ok']
    check(failures==load(folder/'failures.json'),f'{version}: failures retained in run outputs')

def w1_ecdf(reference,sample,weights):
    """Exact CDF integral, independent of scipy.stats.wasserstein_distance."""
    a=np.sort(reference);order=np.argsort(sample);b=sample[order];w=weights[order]
    grid=np.unique(np.r_[a,b]);left=grid[:-1]
    ca=np.searchsorted(a,left,side='right')/len(a)
    indices=np.searchsorted(b,left,side='right');mass=np.r_[0.,np.cumsum(w)]/w.sum()
    cb=mass[indices]
    return float(np.dot(np.diff(grid),np.abs(ca-cb)))

quality_results=[];recomputed=[];event_checks=0;failure_checks=[];calibration_feasibility=[]
for case in selection['cases']:
    category,bid,site=case['category'],case['building_id'],case['site_id']
    check(str(metadata.loc[bid,'site_id'])==site,f'{bid}: actual metadata site')
    temperature=weather[weather.site_id==site].set_index('timestamp').airTemperature
    meter=meters[category][bid]
    for year in [2016,2017]:
        folder=OUT/'cases'/case['case_id']/str(year)
        calendar=pd.date_range(f'{year}-01-01',f'{year+1}-01-01',freq='h',inclusive='left')
        q=meter.reindex(calendar).to_numpy(dtype=float);temp=temperature.reindex(calendar).to_numpy(dtype=float)
        finite=np.isfinite(q);nonnegative=finite&(q>=0);tf=np.isfinite(temp);joint=nonnegative&tf
        keep=np.all(joint.reshape(-1,24),axis=1);hm=np.repeat(keep,24)
        qr,tr,times=q[hm],temp[hm],calendar[hm];dates=times.strftime('%Y-%m-%d').to_numpy();n=int(keep.sum())
        original_peak=float(q[nonnegative].max()) if nonnegative.any() else None;ref_peak=float(qr.max()) if len(qr) else None
        quality={'calendar_hours':len(q),'calendar_days':len(q)//24,'finite_meter_hours':int(finite.sum()),'meter_coverage':float(finite.mean()),
                 'negative_meter_hours':int((finite&~nonnegative).sum()),'finite_temperature_hours':int(tf.sum()),'temperature_coverage':float(tf.mean()),
                 'joint_valid_hours':int(joint.sum()),'joint_valid_coverage':float(joint.mean()),'retained_days':n,'retained_hours':24*n,
                 'complete_day_coverage':n/(len(q)//24),'excluded_days':int((~keep).sum()),
                 'observed_energy_kwh_partial_year':float(q[nonnegative].sum()),'retained_scope_energy_kwh':float(qr.sum()),
                 'original_observed_peak_kw':original_peak,'retained_scope_peak_kw':ref_peak,
                 'original_peak_excluded_from_retained_scope':original_peak is not None and (ref_peak is None or original_peak>ref_peak)}
        saved_quality=load(folder/'coverage.json')
        for name,value in quality.items():compare(saved_quality[name],value,f'{bid}/{year}: quality {name}')
        saved_calendar=pd.read_csv(folder/'observed_calendar.csv');saved_time=pd.to_datetime(saved_calendar.time)
        check(np.array_equal(saved_time.to_numpy(),calendar.to_numpy()),f'{bid}/{year}: stored physical calendar')
        for name,value in [('energy_kwh',q),('outdoor_temp',temp),('load_kw',q)]:
            check(np.allclose(saved_calendar[name].to_numpy(),value,rtol=2e-9,atol=1e-9,equal_nan=True),f'{bid}/{year}: actual observations unchanged {name}')
        check(np.array_equal(saved_calendar.retained_complete_day.to_numpy(),hm),f'{bid}/{year}: stored no-fill complete-day mask')
        exclusions=[]
        for d in np.flatnonzero(~keep):
            ix=slice(24*d,24*d+24)
            exclusions.append({'date':calendar[d*24].strftime('%Y-%m-%d'),'missing_or_nonfinite_meter_hours':int((~finite[ix]).sum()),
                               'negative_meter_hours':int((finite[ix]&~nonnegative[ix]).sum()),'missing_or_nonfinite_temperature_hours':int((~tf[ix]).sum())})
        check(load(folder/'excluded_days.json')==exclusions,f'{bid}/{year}: every excluded day and reason retained')
        quality_results.append({'case_id':case['case_id'],'year':year,**quality})
        for version,records in version_records.items():
            for record in [r for r in records if r['case_id']==case['case_id'] and r['year']==year]:
                label=f'{version}/{bid}/{year}/{record["method"]}'
                check(record['observed_calendar_sha256']==sha(folder/'observed_calendar.csv'),f'{label}: input calendar hash')
                if record['status']!='ok':
                    reason_zero=record['method']=='extreme_calibrated' and float(qr.sum())==0
                    check(reason_zero,f'{label}: recorded failure independently explained by zero retained energy')
                    failure_checks.append({'case_id':case['case_id'],'year':year,'method':record['method'],'retained_energy_kwh':float(qr.sum()),'exception':record.get('exception'),'zero_energy_verified':reason_zero})
                    continue
                ds=[d['original_day_index'] for d in record['selected_days']];ws=np.array([d['weight_days'] for d in record['selected_days']],dtype=float)
                check(len(ds)==len(set(ds)) and np.isfinite(ws).all() and np.all(ws>0),f'{label}: unique positive finite days')
                compare(ws.sum(),n,f'{label}: conserved day count')
                for d,saved in zip(ds,record['selected_days']):check(saved['date']==dates[d*24],f'{label}: actual date mapping {d}')
                ix=np.concatenate([np.arange(d*24,(d+1)*24) for d in ds]);rp=qr[ix];rw=np.repeat(ws,24);m=len(ds)
                actual_days=pd.read_csv(folder/f'{version}_{record["method"]}_actual_days.csv')
                check(len(actual_days)==24*m,f'{label}: actual day CSV length')
                check(np.array_equal(pd.to_datetime(actual_days.time).to_numpy(),times[ix].to_numpy()),f'{label}: actual day CSV timestamps')
                check(np.allclose(actual_days.load_kw.to_numpy(),rp,rtol=2e-9,atol=1e-9),f'{label}: actual day CSV meter values')
                check(np.allclose(actual_days.period_weight.to_numpy(),rw,rtol=2e-9,atol=1e-9),f'{label}: actual day CSV weights')
                E=float(qr.sum());Er=float(np.dot(rp,rw));peak=float(qr.max());pk=float(rp.max());mean=float(qr.mean());w1=w1_ecdf(qr,rp,rw)
                chosen=set(ds);chosen_dates={dates[24*d] for d in ds};selected_hours=np.isin(dates,list(chosen_dates))
                cold_count=max(1,math.ceil(.05*n));dt=tr.reshape(-1,24).mean(axis=1);day_dates=dates[::24]
                cold=sorted(range(n),key=lambda d:(dt[d],day_dates[d]))[:cold_count]
                hot=sorted(range(n),key=lambda d:(-dt[d],day_dates[d]))[:cold_count]
                adj=np.diff(times.asi8)==3600000000000;jumps=np.abs(np.diff(qr));mid=dates[1:]!=dates[:-1]
                both=selected_hours[:-1]&selected_hours[1:];positive=np.flatnonzero(adj&(jumps>0));rank=sorted(positive,key=lambda e:(-jumps[e],e))
                tail=rank[:math.ceil(.01*len(rank))];mt=[e for e in tail if mid[e]]
                deficit=np.maximum(qr-pk,0)
                metric={'retained_days_n':n,'actual_representative_days_m':m,'representative_hours':24*m,'weight_sum_days':float(ws.sum()),'compression_n_over_m':n/m,'representative_day_fraction':m/n,
                        'retained_scope_energy_kwh':E,'weighted_representative_energy_kwh':Er,'retained_scope_energy_bias':(Er-E)/E if E>0 else None,'retained_scope_abs_energy_bias':abs(Er-E)/E if E>0 else None,
                        'retained_scope_peak_kw':peak,'representative_peak_kw':pk,'peak_retention_ratio':pk/peak if peak>0 else None,'maximum_peak_day_selected':bool(selected_hours[np.argmax(qr)]),
                        'ldc_wasserstein1_kw':w1,'ldc_w1_over_mean_load':w1/mean if mean>0 else None,
                        'cold_day_count':cold_count,'cold_day_hit_count':len(chosen.intersection(cold)),'cold_day_coverage':len(chosen.intersection(cold))/cold_count,
                        'hot_day_hit_count':len(chosen.intersection(hot)),'hot_day_coverage':len(chosen.intersection(hot))/cold_count,
                        'minimum_temperature_gap_c':float(tr[ix].min()-tr.min()),'absolute_minimum_temperature_day_selected':bool(selected_hours[np.argmin(tr)]),
                        'real_hour_edges':int(adj.sum()),'positive_ramp_edges':len(rank),
                        'maximum_real_ramp_kw_per_hour':float(jumps[rank[0]]) if rank else 0.,'maximum_selected_original_ramp_kw_per_hour':float(jumps[adj&both].max()) if rank and (adj&both).any() else 0.,
                        'maximum_real_ramp_retention_ratio':float(jumps[adj&both].max()/jumps[rank[0]]) if rank and (adj&both).any() else (0. if rank else None),
                        'maximum_real_ramp_both_days_selected':bool(both[rank[0]]) if rank else None,'maximum_real_ramp_crosses_midnight':bool(mid[rank[0]]) if rank else None,
                        'ramp_tail_event_count':len(tail),'ramp_tail_hit_count':sum(bool(both[e]) for e in tail),'ramp_tail_event_coverage':float(np.mean(both[tail])) if tail else None,
                        'midnight_tail_event_count':len(mt),'midnight_tail_event_coverage':float(np.mean(both[mt])) if mt else None,
                        'capacity_only_k_kw':pk,'capacity_only_deficit_hours':int((deficit>0).sum()),'capacity_only_deficit_hour_fraction':float((deficit>0).mean()),
                        'capacity_only_deficit_energy_kwh':float(deficit.sum()),'capacity_only_deficit_energy_fraction':float(deficit.sum()/E) if E>0 else None}
                for name,value in metric.items():compare(record['metrics'][name],value,f'{label}: {name}')
                expected_events=[{'start':str(times[e]),'end':str(times[e+1]),'ramp_kw_per_hour':float(jumps[e]),'crosses_midnight':bool(mid[e]),'both_endpoint_days_selected':bool(both[e])} for e in tail]
                saved_events=load(folder/f'{version}_{record["method"]}_ramp_events.json')
                check(len(expected_events)==len(saved_events),f'{label}: event list count')
                for a,b in zip(saved_events,expected_events):
                    for name,value in b.items():compare(a[name],value,f'{label}: event {event_checks} {name}')
                    event_checks+=1
                rawtimes=record['elapsed_seconds'];check(len(rawtimes)==3 and all(np.isfinite(rawtimes)) and all(v>=0 for v in rawtimes),f'{label}: three raw timings recorded')
                compare(record['elapsed_median_seconds'],float(np.median(rawtimes)),f'{label}: timing median')
                if record['method']=='ordinary':check(m==record['paired_actual_budget'],f'{label}: ordinary actual budget matches extreme')
                if record['method']=='extreme_calibrated':
                    base=next(r for r in records if r['case_id']==case['case_id'] and r['year']==year and r['method']=='extreme_aware')
                    check(ds==[d['original_day_index'] for d in base['selected_days']],f'{label}: calibration preserves actual days')
                    lower=np.array([1. if d['extreme_reasons'] else 1e-6 for d in record['selected_days']]);check(np.all(ws>=lower-1e-8),f'{label}: extreme lower bounds')
                    daily_energy=np.array([qr[d*24:d*24+24].sum() for d in ds]);remaining=n-lower.sum();offset=np.dot(daily_energy,lower)
                    lower_E=offset+remaining*daily_energy.min();upper_E=offset+remaining*daily_energy.max()
                    exact_feasible=bool(lower_E-1e-8<=E<=upper_E+1e-8)
                    calibration_feasibility.append({'case_id':case['case_id'],'year':year,'target_energy_kwh':E,'feasible_energy_interval_kwh':[float(lower_E),float(upper_E)],'exact_energy_match_feasible':exact_feasible,'actual_abs_energy_bias':metric['retained_scope_abs_energy_bias']})
                    for d in record['calibration_diagnostics']:
                        compare(d['chronology_total'],E,f'{label}: calibration target');compare(d['calibrated_representative_total'],Er,f'{label}: calibration total')
                    check(record['calibration_summary']['solver_success'],f'{label}: reported calibration success')
                recomputed.append({'core_label':version,'case_id':case['case_id'],'year':year,'method':record['method'],'selected_dates':[dates[24*d] for d in ds],'metrics':metric})
lookup={(r['core_label'],r['case_id'],r['year'],r['method']):r for r in recomputed}
comparison=[]
for case in selection['cases']:
    for year in [2016,2017]:
        old=lookup['legacy',case['case_id'],year,'extreme_aware'];new=lookup['new_core',case['case_id'],year,'extreme_aware']
        comparison.append({'case_id':case['case_id'],'year':year,'same_selected_dates':old['selected_dates']==new['selected_dates'],
                           'same_budget':old['metrics']['actual_representative_days_m']==new['metrics']['actual_representative_days_m'],
                           'old_max_ramp_both_days':old['metrics']['maximum_real_ramp_both_days_selected'],'new_max_ramp_both_days':new['metrics']['maximum_real_ramp_both_days_selected']})
summary=load(OUT/'summary.json');summary_metrics=['retained_scope_abs_energy_bias','peak_retention_ratio','cold_day_coverage','hot_day_coverage','ramp_tail_event_coverage','maximum_real_ramp_retention_ratio','ldc_w1_over_mean_load','capacity_only_deficit_energy_fraction','compression_n_over_m']
for row in summary['summaries']:
    records=[r for r in recomputed if r['core_label']==row['core_label'] and r['method']==row['method']]
    compare(row['successful_records'],len(records),f'{row["core_label"]}/{row["method"]}: summary success count')
    for key in summary_metrics:
        values=[r['metrics'][key] for r in records if r['metrics'][key] is not None]
        compare(row[f'median_{key}'],float(np.median(values)) if values else None,f'{row["core_label"]}/{row["method"]}: summary median {key}')
cal_pairs=[]
for r in recomputed:
    if r['method']!='extreme_calibrated':continue
    base=lookup['new_core',r['case_id'],r['year'],'extreme_aware']
    cal_pairs.append({'case_id':r['case_id'],'year':r['year'],'energy_bias_before':base['metrics']['retained_scope_abs_energy_bias'],'energy_bias_after':r['metrics']['retained_scope_abs_energy_bias'],
                      'w1_before':base['metrics']['ldc_w1_over_mean_load'],'w1_after':r['metrics']['ldc_w1_over_mean_load']})
sidecar=load(OUT/'posthoc_scope_sensitivity.json');side_results=[]
check(sidecar['protocol_lock_sha256']==sha(OUT/'protocol_lock.json'),'posthoc sidecar same original protocol lock')
check(sidecar['sidecar_script_sha256']==sha(ROOT/'scripts/bdg2_benchmark_diagnostics.py'),'posthoc separate script hash')
check(sidecar['selection_or_weights_changed'] is False,'posthoc explicitly keeps original selection and weights')
check(len(sidecar['records'])==120,'posthoc retains all 120 records including failure')
for row in sidecar['records']:
    case=next(c for c in selection['cases'] if c['case_id']==row['case_id']);year=row['year']
    q=meters[case['category']][case['building_id']]
    q=q[q.index.year==year].to_numpy(dtype=float);observed=q[np.isfinite(q)&(q>=0)]
    E=float(observed.sum());peak=float(observed.max());hours=8784 if year==2016 else 8760
    expected={'calendar_hours':hours,'all_valid_observed_hours':len(observed),'all_valid_observed_hour_coverage':len(observed)/hours,
              'all_valid_observed_energy_kwh_partial_year':E,'all_valid_observed_peak_kw':peak}
    original=next(r for r in version_records[row['core_label']] if r['case_id']==row['case_id'] and r['year']==year and r['method']==row['method'])
    check(row['method_status']==original['status'],'posthoc original failure/success status')
    if row['method_status']=='ok':
        metric=lookup[row['core_label'],row['case_id'],year,row['method']]['metrics'];k=metric['capacity_only_k_kw'];deficit=np.maximum(observed-k,0)
        expected.update(capacity_only_k_kw=k,full_observed_peak_retention_ratio=k/peak if peak>0 else None,
                        full_observed_deficit_hours=int((deficit>0).sum()),full_observed_deficit_hour_fraction=float((deficit>0).mean()),
                        full_observed_deficit_energy_kwh=float(deficit.sum()),full_observed_deficit_energy_fraction=float(deficit.sum()/E) if E>0 else None,
                        retained_scope_peak_retention_ratio=metric['peak_retention_ratio'],retained_scope_deficit_hours=metric['capacity_only_deficit_hours'],
                        retained_scope_deficit_hour_fraction=metric['capacity_only_deficit_hour_fraction'],retained_scope_deficit_energy_kwh=metric['capacity_only_deficit_energy_kwh'],
                        retained_scope_deficit_energy_fraction=metric['capacity_only_deficit_energy_fraction'])
    else:check(row['diagnostic_status']=='unavailable' and 'capacity_only_k_kw' not in row,'posthoc failed K stays unavailable')
    for name,value in expected.items():compare(row[name],value,f'posthoc/{row["core_label"]}/{row["case_id"]}/{year}/{row["method"]}: {name}')
    side_results.append({k:row[k] for k in ['core_label','case_id','year','method','method_status']}|expected)
diagnostic=load(OUT/'diagnostic_summary.json')
for row in diagnostic['scope_summaries']:
    group=[r for r in side_results if r['core_label']==row['core_label'] and r['method']==row['method'] and r['method_status']=='ok']
    for name,value in [('successful_records',len(group)),('positive_full_scope_deficit_cases',sum(r['full_observed_deficit_hours']>0 for r in group)),('positive_retained_scope_deficit_cases',sum(r['retained_scope_deficit_hours']>0 for r in group))]:compare(row[name],value,f'posthoc summary: {name}')
    for name in ['full_observed_peak_retention_ratio','full_observed_deficit_hour_fraction','full_observed_deficit_energy_fraction']:
        values=[r[name] for r in group if r[name] is not None];compare(row[f'median_{name}'],float(np.median(values)) if values else None,f'posthoc summary: median {name}')
plot=load(ROOT/'docs/execution_20261002/benchmark_summary_data.json')
common=sorted({(r['case_id'],r['year']) for r in recomputed if r['method']=='extreme_calibrated' and r['metrics']['retained_scope_energy_kwh']>0})
check([list(v) for v in common]==plot['common_case_years'],'summary chart uses same 23 positive-energy cases')
check(plot['input_sha256']==sha(OUT/'versions/new_core/results.json'),'summary chart actual frozen result hash')
for metric,methods in plot['display_percent_medians'].items():
    for method,value in methods.items():
        values=[lookup['new_core',c,y,method]['metrics'][metric]*100 for c,y in common]
        compare(value,float(np.median(values)),f'summary chart common-sample median {metric}/{method}')
for c,y in common:
    budgets=[lookup['new_core',c,y,m]['metrics']['actual_representative_days_m'] for m in ['ordinary','extreme_aware','extreme_calibrated']]
    check(len(set(budgets))==1,f'summary chart actual equal budget {c}/{y}')
for p in reviewed_files:check(sha(p)==next(r['sha256'] for v in ['legacy','new_core'] for r in load(OUT/'versions'/v/'run_manifest.json')['files'] if ROOT/r['path']==p),f'reviewed immutable file remains locked {p.name}')
result={'date_local':'2026-10-02','identity':'Independent actual BDG2 CSV recomputation, no benchmark adapter or core imported, no new algorithm run or model fit',
        'protocol_lock_sha256':sha(OUT/'protocol_lock.json'),'locked_artifact_sha256':artifact_hashes,'raw_source_sha256_recomputed':source_hashes,
        'checks':checks,'scalar_comparisons':numeric_checks,'event_records_checked':event_checks,'successful_records_recomputed':len(recomputed),'failed_records_checked':len(failure_checks),
        'issue_count':len(issues),'issues':issues,'selection_regenerated':selected_ids,'2016_quality_rejection_counts':rejection_counts,
        'quality_case_years':quality_results,'method_results':recomputed,'failure_explanations':failure_checks,'calibration_exact_feasibility':calibration_feasibility,
        'legacy_new_comparisons':comparison,'calibration_pairs':cal_pairs,'posthoc_records_recomputed':side_results,'summary_chart_data_verified':True,
        'w1_algorithm':'Independent integral of absolute empirical CDF difference; scipy Wasserstein not called',
        'timing_limit':'Verified saved times/median only; wall-clock speed not independently rerun',
        'no_model_fit_or_engineering_solve':True}
support=HERE/'support/external_recomputation';support.mkdir(exist_ok=True)
(support/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
print(json.dumps({'checks':checks,'scalar_comparisons':numeric_checks,'event_records_checked':event_checks,'records':len(recomputed),'failures':failure_checks,'issues':issues,
                  'new_legacy_changed_selections':sum(not c['same_selected_dates'] for c in comparison),'calibration_w1_worse_cases':sum(c['w1_after']>c['w1_before']+1e-10 for c in cal_pairs),
                  'infeasible_calibrated_energy_targets':sum(not c['exact_energy_match_feasible'] for c in calibration_feasibility)},ensure_ascii=False,indent=2))
