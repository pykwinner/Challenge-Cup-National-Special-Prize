"""Integer package investment with explicitly hypothetical performance/economics.

Public building/weather/tariff and FT8 historical installed price are evidence.
Every device performance value is a frozen research condition, not an FT8 curve.
This standalone LP/enumeration experiment neither imports nor edits heatplan.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from math import exp
from pathlib import Path
import shutil
import sys
from time import perf_counter

import numpy as np
import pandas as pd
from scipy.optimize import linprog
from scipy.sparse import coo_matrix, vstack

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / 'docs/implementation_20261002_followup/parallel_stage_03/investment/run_01'
SERIES = ROOT / 'data/derived/evidence_case_20261002/wuhan_2024_2025_hourly_tou.csv'
JOIN = ROOT / 'docs/implementation_20261002_followup/parallel_stage_02/data/main_case_source_join.json'


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def save(p, value):
    Path(p).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate(frame, protocol):
    required = ['outdoor_temp', 'electricity_price', 'grid_emission_factor', 'internal_gain_kw',
                'solar_gain_kw', 'cop_hp', 'capacity_hp_per_unit_kw']
    require(len(frame) > 0 and 'time' in frame, 'empty or missing time input')
    for key in required:
        require(key in frame and np.isfinite(frame[key].to_numpy(float)).all(), 'invalid input ' + key)
    require((frame.electricity_price > 0).all(), 'electricity prices must be positive')
    require((frame.grid_emission_factor > 0).all(), 'grid factors must be positive')
    require((frame.cop_hp >= 1).all() and (frame.capacity_hp_per_unit_kw > 0).all(), 'invalid design performance')
    f = protocol['physics']; d = protocol['design']
    for key in ['H_kw_per_K', 'C_kwh_per_K', 'dt_hours']:
        require(np.isfinite(f[key]) and f[key] > 0, 'invalid physics ' + key)
    for key in ['initial_c', 'terminal_c', 'min_c', 'max_c']:
        require(np.isfinite(f[key]), 'invalid temperature')
    require(f['min_c'] <= min(f['initial_c'], f['terminal_c']) <= max(f['initial_c'], f['terminal_c']) <= f['max_c'], 'invalid temperature bounds')
    positive = ['eta_resistance', 'eer_cooling', 'max_resistance_kw', 'max_cooling_kw', 'max_grid_kw',
                'resistance_capex_cny_per_kw', 'cooling_package_capex_cny', 'hp_package_capex_cny', 'repeat_window_factor']
    for key in positive:
        require(key in d and np.isfinite(d[key]) and d[key] > 0, 'missing/invalid design ' + key)
    require(d['eta_resistance'] <= 1, 'resistance efficiency exceeds unity')
    for key in ['resistance_fixed_om_cny_per_kw_window', 'hp_fixed_om_cny_per_unit_window', 'cooling_fixed_om_cny_per_window']:
        require(key in d and np.isfinite(d[key]) and d[key] >= 0, 'missing/invalid explicit OM ' + key)
    require(d['hp_unit_candidates'] and all(isinstance(n, int) and n >= 0 for n in d['hp_unit_candidates']), 'invalid integer packages')
    time = pd.DatetimeIndex(pd.to_datetime(frame.time))
    require(time.tz is not None and time.is_unique and time.is_monotonic_increasing, 'timezone/duplicate/order error')
    require(len(time) == 1 or np.all(np.diff(time.asi8) == int(f['dt_hours'] * 3.6e12)), 'chronology gap')
    if 'window_start_inclusive' in protocol:
        require(time[0] == pd.Timestamp(protocol['window_start_inclusive']), 'window start changed')
        require(time[-1] + pd.Timedelta(hours=f['dt_hours']) == pd.Timestamp(protocol['window_end_exclusive']), 'window end changed')


def make_lp(frame, protocol, hp_units, fixed_resistance_kw=None, epsilon=None, secondary_bound=None, mode='cost'):
    validate(frame, protocol)
    d = protocol['design']; f = protocol['physics']; n = len(frame)
    require(isinstance(hp_units, int) and hp_units >= 0, 'noninteger package count')
    require(mode in ('cost', 'carbon'), 'unknown objective')
    if epsilon is not None:
        require(np.isfinite(epsilon) and epsilon >= 0, 'invalid epsilon')
    if fixed_resistance_kw is not None:
        require(np.isfinite(fixed_resistance_kw) and 0 <= fixed_resistance_kw <= d['max_resistance_kw'], 'invalid fixed capacity')
    ir, ih, ic, it = 1 + np.arange(n), 1 + n + np.arange(n), 1 + 2*n + np.arange(n), 1 + 3*n + np.arange(n)
    m = 1 + 4*n; dt = f['dt_hours']; a = exp(-f['H_kw_per_K']*dt/f['C_kwh_per_K']); b = (1-a)/f['H_kw_per_K']
    electric = np.zeros((3, n)); electric[0] = 1/d['eta_resistance']; electric[1] = 1/frame.cop_hp; electric[2] = 1/d['eer_cooling']
    cost = np.zeros(m); carbon = np.zeros(m)
    cost[0] = d['resistance_capex_cny_per_kw'] + d['repeat_window_factor']*d['resistance_fixed_om_cny_per_kw_window']
    for idx, ratio in zip((ir, ih, ic), electric):
        cost[idx] = d['repeat_window_factor']*dt*frame.electricity_price.to_numpy()*ratio
        carbon[idx] = dt*frame.grid_emission_factor.to_numpy()*ratio
    constant = hp_units*d['hp_package_capex_cny'] + d['cooling_package_capex_cny'] + d['repeat_window_factor']*(hp_units*d['hp_fixed_om_cny_per_unit_window'] + d['cooling_fixed_om_cny_per_window'])
    eqrows=[]; eqcols=[]; eqvals=[]
    for t in range(n):
        for col, val in [(it[t], 1), (ir[t], -b), (ih[t], -b), (ic[t], b)]:
            eqrows.append(t); eqcols.append(col); eqvals.append(val)
        if t:
            eqrows.append(t); eqcols.append(it[t-1]); eqvals.append(-a)
    eqrows.append(n); eqcols.append(it[-1]); eqvals.append(1)
    Aeq=coo_matrix((eqvals,(eqrows,eqcols)),shape=(n+1,m)).tocsr()
    beq=np.r_[(1-a)*frame.outdoor_temp.to_numpy()+b*(frame.internal_gain_kw+frame.solar_gain_kw).to_numpy(), f['terminal_c']]
    beq[0]+=a*f['initial_c']
    rows=np.r_[np.arange(n),np.arange(n),n+np.arange(n),n+np.arange(n),n+np.arange(n)]
    cols=np.r_[ir,np.zeros(n,dtype=int),ir,ih,ic]
    vals=np.r_[np.ones(n),-np.ones(n),electric[0],electric[1],electric[2]]
    Aub=coo_matrix((vals,(rows,cols)),shape=(2*n,m)).tocsr()
    bub=np.r_[np.zeros(n), np.full(n,d['max_grid_kw'])]
    if epsilon is not None:
        Aub=vstack([Aub,coo_matrix(carbon.reshape(1,-1))]).tocsr(); bub=np.r_[bub,epsilon]
    if secondary_bound is not None:
        v = cost if secondary_bound['mode']=='cost' else carbon
        Aub=vstack([Aub,coo_matrix(v.reshape(1,-1))]).tocsr(); bub=np.r_[bub,secondary_bound['limit']]
    rbound=(0,d['max_resistance_kw']) if fixed_resistance_kw is None else (fixed_resistance_kw,fixed_resistance_kw)
    bounds=[rbound]+[(0,None)]*n+[(0,float(x)*hp_units) for x in frame.capacity_hp_per_unit_kw]+[(0,d['max_cooling_kw'])]*n+[(f['min_c'],f['max_c'])]*n
    meta={'cost_vector':cost,'carbon_vector':carbon,'cost_constant':constant,'indices':(ir,ih,ic,it),'electric_ratios':electric}
    return (cost if mode=='cost' else carbon),Aub,bub,Aeq,beq,bounds,meta


def stage(lp, mode, epsilon):
    c,au,bu,ae,be,bounds,meta=lp; start=perf_counter()
    r=linprog(c,A_ub=au,b_ub=bu,A_eq=ae,b_eq=be,bounds=bounds,method='highs',options={'time_limit':120.,'dual_feasibility_tolerance':1e-8,'primal_feasibility_tolerance':1e-8})
    record={'success':bool(r.success),'status':int(r.status),'message':str(r.message),'mode':mode,'epsilon_co2_kg':epsilon,'seconds':perf_counter()-start,'iterations':int(r.nit)}
    if r.success:
        dual=float(np.dot(r.ineqlin.marginals,bu)+np.dot(r.eqlin.marginals,be))
        for bound,lm,um in zip(bounds,r.lower.marginals,r.upper.marginals):
            if bound[0] is not None:dual+=float(bound[0]*lm)
            if bound[1] is not None:dual+=float(bound[1]*um)
        violation=max(0,float(np.max(au@r.x-bu)),float(np.max(np.abs(ae@r.x-be))))
        record.update(primal_objective=float(r.fun),dual_objective=dual,primal_dual_gap=float(r.fun-dual),constraint_max_violation=violation,objective_constant=meta['cost_constant'] if mode=='cost' else 0.)
    return r,record


def ledger(dispatch, protocol, hp_units, resistance_capacity_kw, frame):
    d=protocol['design']; dt=protocol['physics']['dt_hours']
    capex=hp_units*d['hp_package_capex_cny']+resistance_capacity_kw*d['resistance_capex_cny_per_kw']+d['cooling_package_capex_cny']
    om=hp_units*d['hp_fixed_om_cny_per_unit_window']+resistance_capacity_kw*d['resistance_fixed_om_cny_per_kw_window']+d['cooling_fixed_om_cny_per_window']
    bill=float(np.dot(dispatch.electricity_kw,frame.electricity_price)*dt)
    carbon=float(np.dot(dispatch.electricity_kw,frame.grid_emission_factor)*dt)
    return {'capex_cny':float(capex),'window_energy_bill_cny':bill,'window_fixed_om_cny':float(om),'window_co2_kg':carbon,'electricity_kwh':float(dispatch.electricity_kw.sum()*dt),'comparison_cost_cny':float(capex+d['repeat_window_factor']*(bill+om)),'hp_units':hp_units,'resistance_capacity_kw':float(resistance_capacity_kw),'cooling_capacity_kw':d['max_cooling_kw']}


def solve_candidate(frame,protocol,hp_units,mode='cost',epsilon=None,fixed_resistance_kw=None):
    lp=make_lp(frame,protocol,hp_units,fixed_resistance_kw,epsilon,mode=mode)
    primary,rec=stage(lp,mode,epsilon)
    if not primary.success:return {'success':False,'hp_units':hp_units,'solver_stages':[rec]}
    primary_value=float(primary.fun); tolerance=1e-7+abs(primary_value)*1e-9
    other='carbon' if mode=='cost' else 'cost'
    secondary_lp=make_lp(frame,protocol,hp_units,fixed_resistance_kw,epsilon,{'mode':mode,'limit':primary_value+tolerance},other)
    result,rec2=stage(secondary_lp,other,epsilon)
    if not result.success:return {'success':False,'hp_units':hp_units,'solver_stages':[rec,rec2]}
    ir,ih,ic,it=secondary_lp[-1]['indices']; x=result.x
    ratios=secondary_lp[-1]['electric_ratios']
    dispatch=pd.DataFrame({'time':frame.time,'q_resistance_kw':x[ir],'q_hp_kw':x[ih],'q_cooling_kw':x[ic],'electricity_kw':x[ir]*ratios[0]+x[ih]*ratios[1]+x[ic]*ratios[2],'temperature_end_c':x[it]})
    metrics=ledger(dispatch,protocol,hp_units,x[0],frame)
    return {'success':True,**metrics,'metrics':metrics,'dispatch':dispatch,'solver_stages':[rec,rec2],
            'primary_objective':primary_value+(lp[-1]['cost_constant'] if mode=='cost' else 0),'mode':mode,'requested_epsilon_co2_kg':epsilon}


def optimize(frame,protocol,mode='cost',epsilon=None,fixed=None):
    if fixed is not None:
        require(isinstance(fixed['hp_units'], (int, np.integer)) and not isinstance(fixed['hp_units'], bool), 'fixed package count must be integer')
    counts=protocol['design']['hp_unit_candidates'] if fixed is None else [int(fixed['hp_units'])]
    candidates=[solve_candidate(frame,protocol,k,mode,epsilon,None if fixed is None else fixed['resistance_capacity_kw']) for k in counts]
    valid=[p for p in candidates if p['success']]
    records=[{k:v for k,v in p.items() if k not in ('dispatch','metrics')} for p in candidates]
    if not valid:return {'success':False,'enumeration':records}
    minimum=min(p['primary_objective'] for p in valid); tolerance=1e-7+abs(minimum)*1e-9
    pool=[p for p in valid if p['primary_objective'] <= minimum+tolerance]
    result=min(pool,key=lambda p:p['window_co2_kg'] if mode=='cost' else p['comparison_cost_cny'])
    result['enumeration']=records
    result['integer_enumeration_complete']=all(all(s['status'] in (0,2) for s in p['solver_stages']) for p in candidates)
    result['global_primary_lower_bound']=minimum
    return result


def export_plan(directory,result,frame,protocol,epsilon=None):
    directory.mkdir(parents=True)
    save(directory/'effective_protocol.json',protocol)
    save(directory/'enumeration.json',result.get('enumeration',[]))
    require(result['success'] and result.get('integer_enumeration_complete',True),'plan not fully solved')
    result['dispatch'].to_csv(directory/'dispatch.csv',index=False,float_format='%.12g')
    save(directory/'investments.json',{'hp_units':result['hp_units'],'resistance_capacity_kw':result['resistance_capacity_kw'],'cooling_capacity_kw':result['cooling_capacity_kw']})
    summary={k:v for k,v in result.items() if k not in ('dispatch','metrics','enumeration')}
    summary['requested_epsilon_co2_kg']=epsilon
    summary['identity']='Conditional design simulation; FT8 performance not source-admitted; comparison cost is explicit repeated-window ledger, not full LCC or profit'
    save(directory/'summary.json',summary)
    print(json.dumps({'plan':str(directory),'hp_units':result['hp_units'],'resistance_kw':result['resistance_capacity_kw'],'comparison_cost_cny':result['comparison_cost_cny'],'co2_kg':result['window_co2_kg']}),flush=True)
    return {'plan':directory.name,**result['metrics'],'requested_epsilon_co2_kg':epsilon}


def default_protocol():
    return {'schema_version':1,'identity':'Public evidence + frozen hypothetical design axes; no exact FT device performance admission',
            'window_start_inclusive':'2024-11-16T00:00:00+08:00','window_end_exclusive':'2025-03-16T00:00:00+08:00',
            'physics':{'H_kw_per_K':.1043,'C_kwh_per_K':.535576,'initial_c':20.,'terminal_c':20.,'min_c':20.,'max_c':27.,'dt_hours':1.},
            'design':{'eta_resistance':.97,'eer_cooling':3.,'max_resistance_kw':6.,'max_cooling_kw':2.,'max_grid_kw':4.,
                      'resistance_capex_cny_per_kw':400.,'cooling_package_capex_cny':1500.,'hp_package_capex_cny':17800.,
                      'resistance_fixed_om_cny_per_kw_window':10.,'hp_fixed_om_cny_per_unit_window':80.,'cooling_fixed_om_cny_per_window':20.,
                      'repeat_window_factor':5.,'hp_unit_candidates':[0,1,2]},
            'performance_design':{'carnot_fraction':.35,'water_supply_reference_c':35.,'package_available_fraction':.75,
                                  'cop_formula':'theta*(273.15+35)/(35-Tout); theoretical design proxy only, not manufacturer table',
                                  'capacity_formula':'8kW nominal label * hypothetical available fraction; no real derating curve claim'},
            'epsilon_points':5,'sensitivity_design_cases':[{'carnot_fraction':.25,'repeat_window_factor':5.},{'carnot_fraction':.45,'repeat_window_factor':5.}],
            'repeat_factor_semantics':'Five repetitions of this exact 120-day operating window, no discount, escalation or new weather. A declared comparison weight, not physical life, five actual years, or full LCC.',
            'interval_physics':'Constant hourly weather/gains/controls; exact exponential 1R1C. Both start/end states bounded; trajectory monotone within each interval, so endpoint comfort implies intra-hour comfort in this hourly forcing model.',
            'service':'Complete heating/cooling with finite priced resistance and independent cooling design assets. Same comfort/gains/weather/electric limit/initial-terminal state; zero unmet heat/cold.',
            'asset_boundary':'New design reference package and hypothetical indoor compatible service. Historical FT price ends before indoor main pipe; indoor terminal, grid upgrades, taxes outside published tariff, reactive charge and full actual OM unresolved.',
            'unclaimed':['FT8 COP/derating/part-load/minimum modulation/defrost measurements','Actual Wuhan building/account/terminal/grid eligibility','Current local purchase quote','Reliability and N-1','Independent station weather validation','Full life-cycle economics/profit/IRR','Sub-hour weather disturbances and device ramping'],
            'all_om_are_declared_scenario_values':True,'no_unpriced_free_backup':True}


def prepare(out):
    require(not out.exists(),'choose a new output directory; prior runs never overwritten')
    out.mkdir(parents=True); bundle=out/'input_bundle'; bundle.mkdir()
    admission=ROOT/'docs/implementation_20261002/data/admission_tou.json'
    old=json.loads(admission.read_text(encoding='utf-8'))
    require(sha(SERIES)==old['input_sha256'][str(SERIES.relative_to(ROOT)).replace('\\','/')],'old input hash mismatch')
    join=json.loads(JOIN.read_text(encoding='utf-8')); contract=ROOT/join['sources']['original_contract']['path']
    require(sha(contract)==join['sources']['original_contract']['sha256'],'contract hash mismatch')
    protocol=default_protocol(); frame=pd.read_csv(SERIES)
    theta=protocol['performance_design']['carnot_fraction']; supply=protocol['performance_design']['water_supply_reference_c']
    require((frame.outdoor_temp<supply).all(),'design Carnot lift is not positive')
    frame['cop_hp']=theta*(supply+273.15)/(supply-frame.outdoor_temp)
    frame['capacity_hp_per_unit_kw']=8*protocol['performance_design']['package_available_fraction']
    frame.to_csv(bundle/'input.csv',index=False,float_format='%.12g')
    save(bundle/'protocol.json',protocol)
    for p in [SERIES,JOIN,admission,contract,ROOT/'docs/implementation_20261002/data/tou_account_and_hours.json',ROOT/'docs/implementation_20261002/data/monthly_tariff_verified.csv']:
        shutil.copyfile(p,bundle/p.name)
    sources={'series':{'copied_path':'wuhan_2024_2025_hourly_tou.csv','sha256':sha(SERIES),'identity':'Inherited admitted NASA hourly product, BESTEST derived fields, historical conditional tariff and average factor; current exact file hash verified, not every primary page re-reviewed'},
             'FT8_price':{'copied_path':contract.name,'sha256':sha(contract),'url':join['sources']['original_contract']['url'],'locators':'Original pp2,4,5,6,7,9; 8kW label/17800 tax-inclusive installed package/boundary/warranty only; no performance'},
             'previous_join':{'copied_path':JOIN.name,'sha256':sha(JOIN),'identity':'Prior blocked engineering join retained; new parametric route does not overturn its missing-field status'}}
    save(bundle/'source_registry.json',sources)
    provenance=[]
    for key in frame:
        provenance.append({'field':key,'identity':'hypothetical_performance_design' if key in ('cop_hp','capacity_hp_per_unit_kw') else 'inherited_public_reference_or_declared_derived_protocol','source_or_formula':protocol['performance_design']['cop_formula'] if key=='cop_hp' else protocol['performance_design']['capacity_formula'] if key=='capacity_hp_per_unit_kw' else 'Prior exact-hash TOU admission/field provenance; not field measurements','actual_FT8_performance':False})
    for key,val in protocol['design'].items():
        provenance.append({'field':'design.'+key,'identity':'historical_primary_package_price' if key=='hp_package_capex_cny' else 'declared_research_condition','source_or_formula':'Original contract PDF p6 row1: 17800 CNY per integer installed package' if key=='hp_package_capex_cny' else json.dumps(val),'actual_FT8_performance':False})
    pd.DataFrame(provenance).to_csv(bundle/'field_identity.csv',index=False)
    files={p.name:sha(p) for p in bundle.iterdir() if p.is_file()}
    save(bundle/'input_manifest.json',{'files':files,'source_status':'parameterised_simulation_ready; engineering_device_performance_not_admitted'})
    return frame,protocol,bundle


def run(out, input_bundle=None):
    code_start_sha256=sha(__file__)
    if input_bundle is None:frame,protocol,bundle=prepare(out)
    else:
        require(not out.exists(),'choose a new output directory')
        source=Path(input_bundle); lock=json.loads((source/'input_manifest.json').read_text(encoding='utf-8'))
        for name,digest in lock['files'].items():require(sha(source/name)==digest,'bundle altered: '+name)
        out.mkdir(parents=True); bundle=out/'input_bundle'; shutil.copytree(source,bundle)
        frame=pd.read_csv(bundle/'input.csv'); protocol=json.loads((bundle/'protocol.json').read_text(encoding='utf-8'))
    validate(frame,protocol)
    start=perf_counter(); points=[]
    # Baseline permits economical finite resistance sizing while forbidding HP.
    baseline_protocol=deepcopy(protocol); baseline_protocol['design']['hp_unit_candidates']=[0]
    baseline=optimize(frame,baseline_protocol)
    points.append(export_plan(out/'baseline',baseline,frame,protocol))
    cost=optimize(frame,protocol); points.append(export_plan(out/'cost_endpoint',cost,frame,protocol))
    carbon=optimize(frame,protocol,mode='carbon'); points.append(export_plan(out/'carbon_endpoint',carbon,frame,protocol))
    epsilon_values=np.linspace(carbon['window_co2_kg'],cost['window_co2_kg'],protocol['epsilon_points'])
    eps=[]
    for i,cap in enumerate(epsilon_values):
        result=optimize(frame,protocol,epsilon=float(cap)+1e-7)
        result['requested_epsilon_co2_kg']=float(cap)
        row=export_plan(out/f'epsilon_{i}',result,frame,protocol,float(cap)); points.append(row);eps.append(result)
    middle=len(eps)//2; compromise=eps[middle]
    points.append(export_plan(out/'compromise',compromise,frame,protocol,float(epsilon_values[middle])))
    chosen={'baseline':baseline,'low_cost':cost,'low_carbon':carbon,'compromise':compromise}
    decision=[]; replays=[]
    for name,result in chosen.items():
        decision.append({'decision':name,**result['metrics']})
        fixed={'hp_units':result['hp_units'],'resistance_capacity_kw':result['resistance_capacity_kw']}
        mode='carbon' if name=='low_carbon' else 'cost'; cap=float(epsilon_values[middle])+1e-7 if name=='compromise' else None
        replay=optimize(frame,protocol,mode=mode,epsilon=cap,fixed=fixed)
        row=export_plan(out/'fixed_replay'/name,replay,frame,protocol,cap)
        require(replay['hp_units']==fixed['hp_units'] and abs(replay['resistance_capacity_kw']-fixed['resistance_capacity_kw'])<1e-8,'capacity changed during replay')
        require(abs(replay['comparison_cost_cny']-result['comparison_cost_cny'])<=.01 and abs(replay['window_co2_kg']-result['window_co2_kg'])<=.01,'fixed replay objective mismatch')
        replays.append({'decision':name,'passed':True,'cost_difference':replay['comparison_cost_cny']-result['comparison_cost_cny'],'carbon_difference':replay['window_co2_kg']-result['window_co2_kg']})
    pd.DataFrame(decision).to_csv(out/'decision_table.csv',index=False)
    pd.DataFrame(points).to_csv(out/'epsilon_scan.csv',index=False)
    save(out/'fixed_replay_checks.json',replays)
    threshold=[]
    # Equal-service cost optimal conditional designs for each exact package count.
    c0=solve_candidate(frame,protocol,0); c1=solve_candidate(frame,protocol,1)
    op0=c0['window_energy_bill_cny']+c0['window_fixed_om_cny'];op1=c1['window_energy_bill_cny']+c1['window_fixed_om_cny']
    dc=c1['capex_cny']-c0['capex_cny']; saving=op0-op1
    threshold.append({'carnot_fraction':protocol['performance_design']['carnot_fraction'],'cost_resistance_kw':c0['resistance_capacity_kw'],'cost_hp_resistance_kw':c1['resistance_capacity_kw'],'additional_capex_cny':dc,'window_operating_saving_cny':saving,'repeat_factor_break_even':dc/saving if saving>0 else None,'maximum_hp_package_price_at_F5_cny':protocol['design']['hp_package_capex_cny']-(c1['comparison_cost_cny']-c0['comparison_cost_cny']),'identity':'Exact ledger crossing of the two fixed F5-optimal capacity/dispatch plans. The global investment threshold can differ because dispatch/capacity changes with F; four reoptimised design weights checked separately. Not lifetime/payback.'})
    for i,case in enumerate(protocol['sensitivity_design_cases']):
        pp=deepcopy(protocol); ff=frame.copy(); pp['design']['repeat_window_factor']=case['repeat_window_factor']; pp['performance_design']['carnot_fraction']=case['carnot_fraction']
        ff['cop_hp']=case['carnot_fraction']*(273.15+35)/(35-ff.outdoor_temp)
        a0=solve_candidate(ff,pp,0);a1=solve_candidate(ff,pp,1)
        ds=(a0['window_energy_bill_cny']+a0['window_fixed_om_cny'])-(a1['window_energy_bill_cny']+a1['window_fixed_om_cny'])
        threshold.append({'carnot_fraction':case['carnot_fraction'],'additional_capex_cny':a1['capex_cny']-a0['capex_cny'],'window_operating_saving_cny':ds,'repeat_factor_break_even':(a1['capex_cny']-a0['capex_cny'])/ds if ds>0 else None,'maximum_hp_package_price_at_F5_cny':17800-(a1['comparison_cost_cny']-a0['comparison_cost_cny']),'identity':'Independent hypothetical COP-design axis; not uncertainty bounds for FT8'})
        sub=out/'design_sensitivity'/f'theta_{i}';sub.mkdir(parents=True)
        ff.to_csv(sub/'input.csv',index=False,float_format='%.12g');save(sub/'protocol.json',pp)
        export_plan(sub/'resistance',a0,ff,pp);export_plan(sub/'hp_one',a1,ff,pp)
    pd.DataFrame(threshold).to_csv(out/'conditional_thresholds.csv',index=False)
    switches=[]
    for factor in [1.,5.,12.,20.]:
        pp=deepcopy(protocol);pp['design']['repeat_window_factor']=factor
        rr=optimize(frame,pp)
        switches.append({'repeat_window_factor':factor,**rr['metrics'],'identity':'Same weather/rates/performance repeated as a research condition; not actual future years'})
        export_plan(out/'repeat_factor_checks'/f'F{int(factor)}',rr,frame,pp)
    pd.DataFrame(switches).to_csv(out/'repeat_factor_decisions.csv',index=False)
    from audit_investment_reference import audit_plan
    audits=[]
    for summary in out.rglob('summary.json'):
        plan=summary.parent; inp=bundle/'input.csv'; prot=plan/'effective_protocol.json'
        if 'design_sensitivity' in plan.parts:inp=plan.parent/'input.csv'
        audit=audit_plan(plan,inp,prot);save(plan/'independent_audit.json',audit);audits.append({'plan':str(plan.relative_to(out)),**audit})
    require(all(x['passed'] for x in audits),'independent audit failed')
    require(sha(__file__)==code_start_sha256, 'runner code changed during solve')
    frozen_code=out/'frozen_code';frozen_code.mkdir()
    for code in [Path(__file__),Path(__file__).with_name('audit_investment_reference.py')]:
        shutil.copyfile(code,frozen_code/code.name)
    files={str(p.relative_to(out)).replace('\\','/'):sha(p) for p in out.rglob('*') if p.is_file()}
    manifest={'schema_version':1,'completed_utc':datetime.now(timezone.utc).isoformat(),'passed':True,'hours':len(frame),'decision_rows':4,'epsilon_points':len(eps),'independently_audited_plans':len(audits),'elapsed_seconds':perf_counter()-start,
              'script_sha256':sha(__file__),'audit_script_sha256':sha(Path(__file__).with_name('audit_investment_reference.py')),'files':files,'engineering_FT_performance_admitted':False,'parameterised_investment_experiment_complete':True,'source_identity':'Public prototype/weather/historical policy/installed price plus explicit frozen design axes','full_lcc_profit_claim':False,'core_model_not_used_or_modified':True}
    save(out/'independent_audits.json',audits);manifest['files']['independent_audits.json']=sha(out/'independent_audits.json');save(out/'manifest.json',manifest)
    print(json.dumps({k:v for k,v in manifest.items() if k!='files'},ensure_ascii=False),flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,default=DEFAULT_OUT);p.add_argument('--input-bundle',type=Path)
    a=p.parse_args();run(a.out,a.input_bundle)


if __name__=='__main__':
    main()
