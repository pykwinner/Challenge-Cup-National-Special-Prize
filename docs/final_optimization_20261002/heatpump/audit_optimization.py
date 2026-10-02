"""Independent fixed-asset LP primal/dual certificate and operating-ledger audit.

No producer/matrix-builder import. Dual stationarity is assembled directly from
the thermal recurrence, electricity coefficients and finite variable bounds.
"""
import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from audit_reference import check, close, digest, independently_lookup, audit_run


def audit_case(driver,dispatch,summary,solver,dual,p,points):
    f=p['physics'];d=p['design'];n=len(driver);dt=p['dt_hours'];tol=p['independent_audit_tolerances']
    physical=tol['physical_absolute'];money=tol['ledger_absolute']
    check(len(dispatch)==n==p['hours_per_city'],'incomplete optimized clock')
    check(dispatch.time.tolist()==driver.time.tolist(),'optimized time differs')
    check(dispatch.hour_index.tolist()==list(range(n)),'optimized index differs')
    t=pd.DatetimeIndex(pd.to_datetime(driver.time));ends=pd.DatetimeIndex(pd.to_datetime(dispatch.interval_end))
    check(t.is_unique and t.tz is not None and np.all(np.diff(t.asi8)==round(dt*3.6e12)), 'noncontinuous optimized time')
    check(t[0]==pd.Timestamp(p['window_start_inclusive']) and t[-1]+pd.Timedelta(hours=dt)==pd.Timestamp(p['window_end_exclusive']),'wrong optimized window')
    check(np.all(ends.asi8-t.asi8==round(dt*3.6e12)),'interval length')
    model=summary['model'];check(dispatch.model.eq(model).all() and dispatch.units.eq(1).all() and summary['units']==1,'asset identity')
    for output,input_ in [('outdoor_temp_c','outdoor_temp'),('relative_humidity_pct','relative_humidity_pct'),
                         ('electricity_price_cny_per_kwh','electricity_price'),('grid_factor_kg_per_kwh','grid_emission_factor'),('dt_hours','dt_hours')]:
        close(dispatch[output],driver[input_],output,physical)
    close(dispatch.leaving_water_c,d['leaving_water_c'],'leaving water',physical)
    q=[];power=[];capacity=[];coef=[];statuses=[]
    for i,row in enumerate(dispatch.to_dict('records')):
        try:
            qi,pi,corners=independently_lookup(points,model,float(driver.iloc[i].outdoor_temp),d['leaving_water_c']);status='admitted_grid'
        except ValueError:
            qi=pi=0.;corners=[];status='rejected_grid'
        check(row['map_status']==status,'LP map status differs')
        check((status=='rejected_grid')==bool(str(row['map_rejection'])),'LP rejection reason')
        close(np.asarray(json.loads(row['corners_json'])),np.asarray(corners),'LP printed corners',physical)
        cap=qi*d['delivered_capacity_factor'];q.append(qi);power.append(pi);capacity.append(cap);coef.append(pi/cap if cap else 0.);statuses.append(status)
    q=np.array(q);power=np.array(power);capacity=np.array(capacity);coef=np.array(coef)
    close(dispatch.catalogue_q_kw,q,'LP Q',physical);close(dispatch.catalogue_p_kw,power,'LP P',physical)
    close(dispatch.available_hp_heat_kw,capacity,'LP available capacity',physical);close(dispatch.hp_electric_per_heat,coef,'LP electrical coefficient',physical)
    close(dispatch.catalogue_cop_derived,np.divide(q,power,out=np.zeros(n),where=power>0),'LP derivedCOP',physical)
    hp=dispatch.hp_heat_kw.to_numpy();r=dispatch.resistance_heat_kw.to_numpy();cool=dispatch.cooling_kw.to_numpy();state=dispatch.temperature_end_c.to_numpy()
    x=np.concatenate([hp,r,cool,state]);check(np.isfinite(x).all(),'LP nonfinite primal')
    lb=np.concatenate([np.zeros(3*n),np.full(n,f['min_c'])]);ub=np.concatenate([capacity,np.full(n,d['resistance_heat_limit_kw']),np.full(n,d['cooling_limit_kw']),np.full(n,f['max_c'])])
    lb[-1]=ub[-1]=f['terminal_c'];check(np.min(x-lb)>=-physical and np.min(ub-x)>=-physical,'LP primal bound violation')
    check(np.max(np.minimum(hp+r,cool))<=physical,'simultaneous heat/cooling dominated dispatch')
    starts=np.concatenate([[f['initial_c']],state[:-1]]);close(dispatch.temperature_start_c,starts,'continuous LP state',physical)
    a=math.exp(-f['H_kw_per_K']*dt/f['C_kwh_per_K']);b=-math.expm1(-f['H_kw_per_K']*dt/f['C_kwh_per_K'])/f['H_kw_per_K']
    gain=(driver.internal_gain_kw.to_numpy()+driver.solar_gain_kw.to_numpy())*d['prototype_copies'];close(dispatch.gain_kw,gain,'LP gain',physical)
    recurrence=a*starts+(1-a)*driver.outdoor_temp.to_numpy()+b*(gain+hp+r-cool)
    rc_error=close(state,recurrence,'LP independent RC',physical)
    duty=np.divide(hp,capacity,out=np.zeros(n),where=capacity>0)
    close(dispatch.duty_fraction,duty,'LP duty',physical);check(np.min(duty)>=-physical and np.max(duty)<=1+physical,'LP duty bounds')
    hp_power=hp*coef;r_power=r/d['resistance_eta'];c_power=cool/d['cooling_eer'];grid=hp_power+r_power+c_power
    check(np.max(grid)<=d['grid_limit_kw']+physical and np.min(grid)>=-physical,'LP finite grid violation')
    for key,values in [('hp_power_kw',hp_power),('resistance_power_kw',r_power),('cooling_power_kw',c_power),('grid_power_kw',grid),
                       ('grid_energy_kwh',grid*dt),('energy_charge_cny',grid*dt*driver.electricity_price),('carbon_proxy_kg',grid*dt*driver.grid_emission_factor)]:
        close(dispatch[key],values,'LP '+key,money if key in ['energy_charge_cny','carbon_proxy_kg'] else physical)
    independently=dict(hours=n,units=1,electricity_kwh=float(grid.sum()*dt),energy_charge_cny=float(np.sum(grid*dt*driver.electricity_price)),
        carbon_proxy_kg=float(np.sum(grid*dt*driver.grid_emission_factor)),hp_heat_kwh=float(hp.sum()*dt),resistance_heat_kwh=float(r.sum()*dt),
        cooling_kwh=float(cool.sum()*dt),min_temperature_c=float(min(f['initial_c'],state.min())),max_temperature_c=float(max(f['initial_c'],state.max())),
        terminal_c=float(state[-1]),max_resistance_heat_kw=float(r.max()),max_cooling_kw=float(cool.max()),max_grid_power_kw=float(grid.max()),
        grid_rejected_hours=statuses.count('rejected_grid'))
    check(summary['status']=='optimal_fixed_asset_LP','unestablished optimality status')
    for key,value in independently.items():close(summary[key],value,'LP summary '+key,money)
    check(solver['status']==0 and solver['success'] is True and solver['objective_kind']=='electricity_energy_charge_cny_only','solver failure/objective scope')
    y=np.array(dual['equality']);z=np.array(dual['grid_inequality']);l=np.array(dual['lower_bounds']);u=np.array(dual['upper_bounds'])
    check(y.shape==z.shape==(n,) and l.shape==u.shape==(4*n,),'invalid dual sizes')
    check(all(np.isfinite(v).all() for v in [y,z,l,u]),'nonfinite certificate')
    check(np.max(z)<=physical and np.min(l)>=-physical and np.max(u)<=physical,'dual sign violation')
    price=driver.electricity_price.to_numpy()*dt
    c=np.concatenate([price*coef,price/d['resistance_eta'],price/d['cooling_eer'],np.zeros(n)])
    at_y=np.concatenate([-b*y,-b*y,b*y,y-np.concatenate([a*y[1:],[0.]])])
    at_z=np.concatenate([coef*z,z/d['resistance_eta'],z/d['cooling_eer'],np.zeros(n)])
    stationarity=close(c,at_y+at_z+l+u,'independent LP dual stationarity',tol['dual_stationarity_absolute'])
    eq_rhs=(1-a)*driver.outdoor_temp.to_numpy()+b*gain;eq_rhs[0]+=a*f['initial_c']
    dual_value=float(y@eq_rhs+z@np.full(n,d['grid_limit_kw'])+l@lb+u@ub)
    primal_value=independently['energy_charge_cny'];gap=primal_value-dual_value
    check(abs(gap)<=tol['certificate_gap_cny'],'independent primal/dual gap')
    close(solver['dual_objective_cny'],dual_value,'reported energy-charge-only dual bound',money)
    close(solver['primal_objective_cny'],primal_value,'reported primal',money)
    close(solver['absolute_primal_dual_gap_cny'],abs(gap),'reported gap',money)
    complementarity=max(float(np.max(np.abs(z*(d['grid_limit_kw']-grid)))),float(np.max(np.abs(l*(x-lb)))),float(np.max(np.abs(u*(ub-x)))))
    check(complementarity<=tol['certificate_gap_cny'],'complementarity violation')
    return dict(passed=True,hours=n,max_rc_error_c=rc_error,max_stationarity_error=stationarity,
        max_complementarity_error=complementarity,independent_energy_charge_lower_bound_cny=dual_value,
        independent_primal_energy_charge_cny=primal_value,primal_dual_gap_cny=gap,independent_summary=independently)


def audit_optimization(out,reference=None):
    out=Path(out);p=json.loads((out/'protocol.json').read_text(encoding='utf8'));reference=Path(reference) if reference else out/p['reference_run_relative']
    base_audit=audit_run(reference);check(base_audit['passed'],'reference independent audit failed')
    manifest=json.loads((out/'manifest.json').read_text(encoding='utf8'))
    for group,parent in [('input_sha256',out/'inputs'),('code_sha256',out/'frozen_code'),('result_sha256',out)]:
        for name,value in manifest[group].items():check(digest(parent/name)==value,'optimization frozen hash changed: '+name)
    check(manifest['expected_cases']==manifest['completed_cases']==6 and manifest['failures']==[],'LP incomplete/failures')
    check(json.loads((out/'failures.json').read_text(encoding='utf8'))==[],'LP failed cases')
    base=json.loads((reference/'protocol.json').read_text(encoding='utf8'))
    for key in ['physics','design','dt_hours','hours_per_city','window_start_inclusive','window_end_exclusive','cities']:
        check(p[key]==base[key],'LP/baseline service/assets changed: '+key)
    for name in ['protocol.json','admitted_heating_points.csv','independent_numeric_audit.json','manifest.json']:
        check(digest(out/'inputs'/('reference_'+name))==digest(reference/name),'reference identity changed '+name)
    g=pd.read_csv(out/'inputs/reference_admitted_heating_points.csv',float_precision='round_trip')
    points={(r.model,r.ambient_c,r.leaving_water_c):(r.q_kw,r.p_kw) for r in g.itertuples()}
    table=pd.read_csv(out/'optimization_comparison.csv',float_precision='round_trip');check(len(table)==6,'LP comparison incomplete')
    results={}
    for city in p['cities']:
        file=city+'_frozen_driver.csv';check(digest(out/'inputs'/file)==digest(reference/file),'optimized driver changed')
        driver=pd.read_csv(out/'inputs'/file,float_precision='round_trip')
        for choice in p['design']['module_candidates']:
            if choice['units']==0:continue
            name=city+'__'+choice['case_id'];folder=out/name
            read=lambda f:json.loads((folder/f).read_text(encoding='utf8'))
            dispatch=pd.read_csv(folder/'dispatch.csv',float_precision='round_trip',keep_default_na=False)
            summary=read('summary.json');check(summary['city']==city and summary['case_id']==choice['case_id'] and summary['model']==choice['model'],'LP case identity')
            result=audit_case(driver,dispatch,summary,read('solver.json'),read('dual_certificate.json'),p,points);results[name]=result
            original_summary=reference/name/'summary.json';frozen_summary=out/'inputs'/(name+'__thermostat_summary.json')
            check(digest(original_summary)==digest(frozen_summary),'baseline reference summary changed')
            thermostat=json.loads(frozen_summary.read_text(encoding='utf8'));row=table[(table.city==city)&(table.case_id==choice['case_id'])]
            check(len(row)==1,'LP comparison case missing/duplicated');row=row.iloc[0];values=result['independent_summary']
            for key,value in values.items():close(row[key],value,'LP comparison '+key,p['independent_audit_tolerances']['ledger_absolute'])
            saving=thermostat['energy_charge_cny']-values['energy_charge_cny'];check(saving>=-1e-6,'LP worse than same-assets feasible thermostat')
            close(row.thermostat_energy_charge_cny,thermostat['energy_charge_cny'],'comparison thermostat',1e-6)
            close(row.optimized_energy_charge_saving_cny,saving,'optimization saving',1e-6)
            close(row.optimized_energy_charge_saving_pct,100*saving/thermostat['energy_charge_cny'],'optimization saving percent',1e-6)
            close(row.optimized_electricity_change_kwh,values['electricity_kwh']-thermostat['electricity_kwh'],'electricity change',1e-6)
            close(row.optimized_carbon_proxy_change_kg,values['carbon_proxy_kg']-thermostat['carbon_proxy_kg'],'carbon KPI not objective bound',1e-6)
            close(row.energy_charge_dual_lower_bound_cny,result['independent_energy_charge_lower_bound_cny'],'comparison only energy-charge bound',1e-6)
    return dict(passed=True,cases_checked=len(results),hours_checked=sum(r['hours'] for r in results.values()),
        bound_scope='electricity_energy_charge_CNY_only, not carbon or capital/full bill',tolerances=p['independent_audit_tolerances'],
        reference_thermostat_audit_passed=True,same_assets_and_service_envelope=True,cases=results)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('run',type=Path);parser.add_argument('--reference',type=Path);parser.add_argument('--audit-out',type=Path)
    args=parser.parse_args();target=args.audit_out or args.run/'independent_optimization_audit.json';check(not target.exists(),'frozen audit exists')
    try:result=audit_optimization(args.run,args.reference)
    except Exception as error:result=dict(passed=False,error=f'{type(error).__name__}: {error}')
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8')
    print(json.dumps({k:v for k,v in result.items() if k!='cases'},ensure_ascii=False));return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
