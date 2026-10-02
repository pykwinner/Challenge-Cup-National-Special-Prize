"""Fixed whole-module, perfect-foresight operating-cost LP; no capital optimization."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from math import exp
from pathlib import Path
import shutil
from time import perf_counter

import numpy as np
import pandas as pd
from scipy.optimize import linprog
from scipy.sparse import coo_matrix

from performance_map import CatalogueMap, sha, require
from run_reference import validate_inputs, save

HERE=Path(__file__).resolve().parent


def performance(frame,protocol,map_,model):
    d=protocol['design'];rows=[]
    for t in frame.outdoor_temp.to_numpy(float):
        try:
            r=map_.query(model,t,d['leaving_water_c'])
            rows.append((r.q_kw,r.p_kw,'admitted_grid','',json.dumps(r.corners,separators=(',',':'))))
        except ValueError as error:
            rows.append((0.,0.,'rejected_grid',str(error),'[]'))
    result=pd.DataFrame(rows,columns=['catalogue_q_kw','catalogue_p_kw','map_status','map_rejection','corners_json'])
    result['available_hp_heat_kw']=result.catalogue_q_kw*d['delivered_capacity_factor']
    result['hp_electric_per_heat']=np.divide(result.catalogue_p_kw,result.available_hp_heat_kw,
        out=np.zeros(len(result)),where=result.available_hp_heat_kw.to_numpy()>0)
    return result


def solve(frame,protocol,map_,model):
    validate_inputs(frame,protocol)
    f=protocol['physics'];d=protocol['design'];n=len(frame);dt=protocol['dt_hours']
    perf=performance(frame,protocol,map_,model);gain=(frame.internal_gain_kw+frame.solar_gain_kw).to_numpy()*d['prototype_copies']
    a=exp(-f['H_kw_per_K']*dt/f['C_kwh_per_K']);b=(1-a)/f['H_kw_per_K']
    coef=perf.hp_electric_per_heat.to_numpy();price=frame.electricity_price.to_numpy()*dt
    objective=np.concatenate([price*coef,price/d['resistance_eta'],price/d['cooling_eer'],np.zeros(n)])
    rows=[];cols=[];data=[]
    for i in range(n):
        for col,value in [(i,-b),(n+i,-b),(2*n+i,b),(3*n+i,1.)]:rows.append(i);cols.append(col);data.append(value)
        if i:rows.append(i);cols.append(3*n+i-1);data.append(-a)
    eq=coo_matrix((data,(rows,cols)),shape=(n,4*n)).tocsr()
    eq_rhs=(1-a)*frame.outdoor_temp.to_numpy()+b*gain;eq_rhs[0]+=a*f['initial_c']
    gr=np.tile(np.arange(n),3);gc=np.concatenate([np.arange(n),n+np.arange(n),2*n+np.arange(n)])
    gv=np.concatenate([coef,np.full(n,1/d['resistance_eta']),np.full(n,1/d['cooling_eer'])])
    grid=coo_matrix((gv,(gr,gc)),shape=(n,4*n)).tocsr();grid_rhs=np.full(n,d['grid_limit_kw'])
    bounds=[(0.,v) for v in perf.available_hp_heat_kw]+[(0.,d['resistance_heat_limit_kw'])]*n+[(0.,d['cooling_limit_kw'])]*n+[(f['min_c'],f['max_c'])]*n
    bounds[-1]=(f['terminal_c'],f['terminal_c'])
    started=perf_counter()
    result=linprog(objective,A_ub=grid,b_ub=grid_rhs,A_eq=eq,b_eq=eq_rhs,bounds=bounds,method='highs-ds',
        options={'primal_feasibility_tolerance':1e-9,'dual_feasibility_tolerance':1e-9})
    elapsed=perf_counter()-started
    solver=dict(status=int(result.status),success=bool(result.success),message=result.message,method='highs-ds',
                elapsed_seconds=elapsed,iterations=int(result.nit),objective_kind='electricity_energy_charge_cny_only',
                dual_objective_cny=None,primal_objective_cny=None,absolute_primal_dual_gap_cny=None)
    if result.status!=0 or not result.success or result.x is None:
        return None,None,solver,None
    x=result.x;qh=x[:n];qr=x[n:2*n];qc=x[2*n:3*n];state=x[3*n:]
    electricity=qh*coef+qr/d['resistance_eta']+qc/d['cooling_eer']
    dual=dict(equality=result.eqlin.marginals.tolist(),grid_inequality=result.ineqlin.marginals.tolist(),
              lower_bounds=result.lower.marginals.tolist(),upper_bounds=result.upper.marginals.tolist(),
              variable_order='hp_heat[n],resistance_heat[n],cooling[n],temperature_end[n]',
              sign_convention='SciPy marginal sensitivities: c=Aeq.T*y+Aub.T*z+lower+upper; z<=0,lower>=0,upper<=0')
    lb=np.array([v[0] for v in bounds]);ub=np.array([v[1] for v in bounds])
    dual_value=float(result.eqlin.marginals@eq_rhs+result.ineqlin.marginals@grid_rhs+result.lower.marginals@lb+result.upper.marginals@ub)
    primal=float(objective@x)
    solver.update(dual_objective_cny=dual_value,primal_objective_cny=primal,absolute_primal_dual_gap_cny=abs(primal-dual_value))
    dispatch=perf.copy()
    dispatch['hour_index']=np.arange(n);dispatch['time']=frame.time
    dispatch['interval_end']=(pd.to_datetime(frame.time)+pd.Timedelta(hours=dt)).map(lambda t:t.isoformat())
    for key,out in [('outdoor_temp','outdoor_temp_c'),('relative_humidity_pct','relative_humidity_pct'),
                    ('electricity_price','electricity_price_cny_per_kwh'),('grid_emission_factor','grid_factor_kg_per_kwh')]:dispatch[out]=frame[key]
    dispatch['dt_hours']=dt;dispatch['model']=model;dispatch['units']=1;dispatch['leaving_water_c']=d['leaving_water_c']
    dispatch['catalogue_cop_derived']=np.divide(dispatch.catalogue_q_kw,dispatch.catalogue_p_kw,out=np.zeros(n),where=dispatch.catalogue_p_kw.to_numpy()>0)
    dispatch['temperature_start_c']=np.concatenate([[f['initial_c']],state[:-1]]);dispatch['temperature_end_c']=state
    dispatch['gain_kw']=gain;dispatch['hp_heat_kw']=qh;dispatch['resistance_heat_kw']=qr;dispatch['cooling_kw']=qc
    dispatch['duty_fraction']=np.divide(qh,dispatch.available_hp_heat_kw,out=np.zeros(n),where=dispatch.available_hp_heat_kw.to_numpy()>0)
    dispatch['hp_power_kw']=qh*coef;dispatch['resistance_power_kw']=qr/d['resistance_eta'];dispatch['cooling_power_kw']=qc/d['cooling_eer']
    dispatch['grid_power_kw']=electricity;dispatch['grid_energy_kwh']=electricity*dt
    dispatch['energy_charge_cny']=electricity*dt*frame.electricity_price.to_numpy();dispatch['carbon_proxy_kg']=electricity*dt*frame.grid_emission_factor.to_numpy()
    summary=dict(status='optimal_fixed_asset_LP',model=model,units=1,hours=n,electricity_kwh=float(dispatch.grid_energy_kwh.sum()),
        energy_charge_cny=float(dispatch.energy_charge_cny.sum()),carbon_proxy_kg=float(dispatch.carbon_proxy_kg.sum()),
        hp_heat_kwh=float(qh.sum()*dt),resistance_heat_kwh=float(qr.sum()*dt),cooling_kwh=float(qc.sum()*dt),
        min_temperature_c=float(min(f['initial_c'],state.min())),max_temperature_c=float(max(f['initial_c'],state.max())),terminal_c=float(state[-1]),
        max_resistance_heat_kw=float(qr.max()),max_cooling_kw=float(qc.max()),max_grid_power_kw=float(electricity.max()),
        grid_rejected_hours=int(dispatch.map_status.eq('rejected_grid').sum()))
    return dispatch,summary,solver,dual


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--reference',type=Path,default=HERE/'run_01');parser.add_argument('--out',type=Path,default=HERE/'optimization_01')
    args=parser.parse_args();out=args.out.resolve();reference=args.reference.resolve()
    require(not out.exists(),'frozen optimization output cannot be overwritten');out.mkdir(parents=True)
    inputs=out/'inputs';inputs.mkdir()
    for name in ['protocol.json','admitted_heating_points.csv','independent_numeric_audit.json','manifest.json']:
        shutil.copy2(reference/name,inputs/('reference_'+name))
    p=deepcopy(json.loads((reference/'protocol.json').read_text(encoding='utf8')))
    p.update(experiment_id='TICA_fixed_whole_module_perfect_foresight_operating_LP_v1',frozen_utc=datetime.now(timezone.utc).isoformat(),
        reference_run_relative=str(Path('..')/reference.name),
        optimization='minimize electricity energy charge; fixed1 whole module and fixed90kW resistance/80kW separate cooler/100kW grid; perfect foresight; no investment variables or capital constants',
        service_comparison='same20..27C/continuousinitial-terminal20C and same assets; temperature trajectories may differ inside shared comfort envelope',
        solver_method='SciPy HiGHS dual simplex',solver_feasibility_tolerance=1e-9,
        independent_audit_tolerances=dict(physical_absolute=1e-8,ledger_absolute=1e-6,dual_stationarity_absolute=1e-8,certificate_gap_cny=1e-6),
        solver_bound_scope='dual lower bound for electricity energy-charge objective only; no cost bound asserted for carbon KPI, investment or complete bill',
        controller='LP full-window dispatch; exact continuous RC, common service envelope and fixed terminal; ideal-duty catalogue hypothesis remains')
    save(out/'protocol.json',p);map_=CatalogueMap(pd.read_csv(reference/'admitted_heating_points.csv',float_precision='round_trip'))
    summaries=[];failures=[]
    for city in p['cities']:
        shutil.copy2(reference/(city+'_frozen_driver.csv'),inputs/(city+'_frozen_driver.csv'))
        frame=pd.read_csv(inputs/(city+'_frozen_driver.csv'),float_precision='round_trip')
        for choice in p['design']['module_candidates']:
            if choice['units']==0:continue
            name=city+'__'+choice['case_id'];case=out/name;case.mkdir()
            shutil.copy2(reference/name/'summary.json',inputs/(name+'__thermostat_summary.json'))
            try:
                dispatch,summary,solver,dual=solve(frame,p,map_,choice['model']);save(case/'solver.json',solver)
                if dispatch is None:
                    failure=dict(case=name,reason='solver did not establish optimum',solver=solver);save(case/'failure.json',failure);failures.append(failure);continue
                dispatch.to_csv(case/'dispatch.csv',index=False,float_format='%.17g',encoding='utf8');save(case/'dual_certificate.json',dual)
                summary.update(city=city,case_id=choice['case_id']);save(case/'summary.json',summary)
                base=json.loads((inputs/(name+'__thermostat_summary.json')).read_text(encoding='utf8'))
                row=deepcopy(summary);row.update(thermostat_energy_charge_cny=base['energy_charge_cny'],
                    optimized_energy_charge_saving_cny=base['energy_charge_cny']-summary['energy_charge_cny'],
                    optimized_energy_charge_saving_pct=100*(base['energy_charge_cny']-summary['energy_charge_cny'])/base['energy_charge_cny'],
                    optimized_electricity_change_kwh=summary['electricity_kwh']-base['electricity_kwh'],
                    optimized_carbon_proxy_change_kg=summary['carbon_proxy_kg']-base['carbon_proxy_kg'],
                    energy_charge_dual_lower_bound_cny=solver['dual_objective_cny'])
                summaries.append(row)
            except Exception as error:
                failure=dict(case=name,reason=f'{type(error).__name__}: {error}');save(case/'failure.json',failure);failures.append(failure)
    save(out/'failures.json',failures);pd.DataFrame(summaries).to_csv(out/'optimization_comparison.csv',index=False,encoding='utf8')
    frozen=out/'frozen_code';frozen.mkdir()
    for name in ['performance_map.py','run_reference.py','optimize_reference.py','audit_reference.py','audit_optimization.py','test_heatpump_optimization.py','method_sources.json']:
        if (HERE/name).exists():shutil.copy2(HERE/name,frozen/name)
    save(out/'manifest.json',dict(expected_cases=6,completed_cases=len(summaries),failures=failures,
        input_sha256={x.name:sha(x) for x in inputs.iterdir()},code_sha256={x.name:sha(x) for x in frozen.iterdir()},
        result_sha256={str(x.relative_to(out)).replace('\\','/'):sha(x) for x in out.rglob('*') if x.is_file() and x.parent not in (inputs,frozen)}))
    print(json.dumps(dict(out=str(out),completed=len(summaries),failures=len(failures)),ensure_ascii=False));return 1 if failures else 0


if __name__=='__main__':raise SystemExit(main())
