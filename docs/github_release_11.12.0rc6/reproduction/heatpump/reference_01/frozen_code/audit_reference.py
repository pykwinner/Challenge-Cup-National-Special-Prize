"""Independent numeric audit. Does not import the producer or its lookup module.

Source-PDF cell admission is a separate report. This audit independently rebuilds
adjacent-axis Q/P interpolation, controller, exact RC dynamics and hourly ledger.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

TOL = 1e-8
MODELS = ('TCAV035BHE','TCAV065BHE')
AX = (-26.,-20.,-15.,-10.,-5.,0.,7.,10.,15.,20.,25.,30.,35.,48.,55.)
AY = (30.,35.,40.,45.,50.,55.)


def check(ok,message):
    if not ok: raise ValueError(message)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def close(actual,expected,label,tol=TOL):
    actual=np.asarray(actual,dtype=float);expected=np.asarray(expected,dtype=float)
    check(np.isfinite(actual).all() and np.isfinite(expected).all(),label+' nonfinite')
    error=float(np.max(np.abs(actual-expected))) if actual.size else 0.
    check(error<=tol, f'{label}: max error {error} exceeds {tol}')
    return error


def independently_lookup(points,model,x,y):
    check(model in MODELS,'unknown model')
    check(math.isfinite(x) and math.isfinite(y),'nonfinite map coordinate')
    check(AX[0]<=x<=AX[-1] and AY[0]<=y<=AY[-1],'extrapolation')
    weights=[]
    for value,axis in [(x,AX),(y,AY)]:
        hi=int(np.searchsorted(axis,value,side='left'))
        if axis[hi]==value: weights.append([(value,1.)])
        else:
            lo=hi-1; frac=(value-axis[lo])/(axis[hi]-axis[lo])
            weights.append([(axis[lo],1-frac),(axis[hi],frac)])
    selected=[];q=p=0.
    for a,wa in weights[0]:
        for b,wb in weights[1]:
            check((model,a,b) in points,'blank corner')
            qi,pi=points[(model,a,b)];q+=wa*wb*qi;p+=wa*wb*pi;selected.append([a,b,wa*wb])
    return q,p,selected


def audit_case(driver,dispatch,summary,protocol,points,choice):
    f=protocol['physics'];d=protocol['design'];dt=protocol['dt_hours'];n=len(driver)
    check(len(dispatch)==n==protocol['hours_per_city'],'wrong case length')
    check(dispatch.time.tolist()==driver.time.tolist(),'dispatch time changed')
    check(dispatch.hour_index.tolist()==list(range(n)),'hour order/index')
    time=pd.DatetimeIndex(pd.to_datetime(driver.time));ends=pd.DatetimeIndex(pd.to_datetime(dispatch.interval_end))
    check(time.tz is not None and time.is_unique and np.all(np.diff(time.asi8)==round(dt*3.6e12)), 'driver chronology')
    check(time[0]==pd.Timestamp(protocol['window_start_inclusive']) and time[-1]+pd.Timedelta(hours=dt)==pd.Timestamp(protocol['window_end_exclusive']),'window mismatch')
    check(np.all(ends.asi8-time.asi8==round(dt*3.6e12)),'interval end mismatch')
    model=choice['model'];units=choice['units']
    check(type(units) is int and units in (0,1) and ((units==0 and model is None) or (units==1 and model in MODELS)), 'noninteger/unknown equipment')
    check(dispatch.model.eq(model or 'none').all() and dispatch.units.eq(units).all(),'equipment changed')
    err={}
    for output,input_ in [('outdoor_temp_c','outdoor_temp'),('relative_humidity_pct','relative_humidity_pct'),
                         ('electricity_price_cny_per_kwh','electricity_price'),('grid_factor_kg_per_kwh','grid_emission_factor'),('dt_hours','dt_hours')]:
        err[output]=close(dispatch[output],driver[input_],output)
    gain=(driver.internal_gain_kw.to_numpy()+driver.solar_gain_kw.to_numpy())*d['prototype_copies']
    err['gain']=close(dispatch.gain_kw,gain,'gain scaling')
    err['water']=close(dispatch.leaving_water_c,d['leaving_water_c'],'leaving water')
    a=math.exp(-f['H_kw_per_K']*dt/f['C_kwh_per_K']);b=-math.expm1(-f['H_kw_per_K']*dt/f['C_kwh_per_K'])/f['H_kw_per_K']
    expected_state=f['initial_c'];max_rc_error=0.;rejected=0
    columns=['catalogue_q_kw','catalogue_p_kw','catalogue_cop_derived','available_hp_heat_kw','target_end_c','heat_required_kw',
             'hp_heat_kw','resistance_heat_kw','cooling_kw','duty_fraction','hp_power_kw','resistance_power_kw','cooling_power_kw','grid_power_kw']
    expected={x:[] for x in columns}
    starts=[];states=[];statuses=[]
    for i,r in enumerate(dispatch.to_dict('records')):
        starts.append(expected_state)
        free=a*expected_state+(1-a)*driver.iloc[i].outdoor_temp+b*gain[i]
        target=f['terminal_c'] if i==n-1 else float(np.clip(free,f['min_c'],f['max_c']))
        net=(target-free)/b;heat=max(net,0.);cool=max(-net,0.)
        q=p=cop=0.;corner=[];status='no_module'
        if units:
            try:
                q,p,corner=independently_lookup(points,model,float(driver.iloc[i].outdoor_temp),d['leaving_water_c'])
                cop=q/p;status='admitted_grid'
            except ValueError:
                rejected+=1;status='rejected_grid'
        check(r['map_status']==status,'map accept/reject differs')
        check((status=='rejected_grid')==bool(str(r.get('map_rejection',''))),'rejection reason missing/spurious')
        close(np.asarray(json.loads(r['corners_json'])),np.asarray(corner),'corner identity/weight')
        capacity=units*q*d['delivered_capacity_factor'];hp=min(heat,capacity);duty=hp/capacity if capacity else 0.
        qr=heat-hp;php=duty*units*p;pr=qr/d['resistance_eta'];pc=cool/d['cooling_eer'];grid=php+pr+pc
        values=[q,p,cop,capacity,target,heat,hp,qr,cool,duty,php,pr,pc,grid]
        for k,v in zip(columns,values):expected[k].append(v)
        check(qr<=d['resistance_heat_limit_kw']+TOL and cool<=d['cooling_limit_kw']+TOL and grid<=d['grid_limit_kw']+TOL,'finite capacity/grid limit violation')
        check(0<=duty<=1+TOL,'duty outside whole-module bounds')
        expected_state=a*expected_state+(1-a)*driver.iloc[i].outdoor_temp+b*(gain[i]+hp+qr-cool);states.append(expected_state);statuses.append(status)
        max_rc_error=max(max_rc_error,abs(r['temperature_end_c']-expected_state))
    for k in columns:err[k]=close(dispatch[k],expected[k],k)
    err['state_start']=close(dispatch.temperature_start_c,starts,'continuous state start')
    err['state_end']=close(dispatch.temperature_end_c,states,'independent continuous RC')
    check(np.min(states)>=f['min_c']-TOL and np.max(states)<=f['max_c']+TOL,'comfort')
    close(states[-1],f['terminal_c'],'terminal');check(dispatch.constraint_violations.eq('').all(),'producer constraint failure')
    power=np.array(expected['grid_power_kw']);energy=power*dt;bill=energy*driver.electricity_price;carbon=energy*driver.grid_emission_factor
    for name,values in [('grid_energy_kwh',energy),('energy_charge_cny',bill),('carbon_proxy_kg',carbon)]:err[name]=close(dispatch[name],values,name)
    independent=dict(hours=n,model=model,units=units,electricity_kwh=float(energy.sum()),energy_charge_cny=float(bill.sum()),carbon_proxy_kg=float(carbon.sum()),
        hp_heat_kwh=float(np.sum(expected['hp_heat_kw'])*dt),resistance_heat_kwh=float(np.sum(expected['resistance_heat_kw'])*dt),cooling_kwh=float(np.sum(expected['cooling_kw'])*dt),
        max_resistance_heat_kw=max(expected['resistance_heat_kw']),max_cooling_kw=max(expected['cooling_kw']),max_grid_power_kw=max(expected['grid_power_kw']),
        min_temperature_c=min(f['initial_c'],min(states)),max_temperature_c=max(f['initial_c'],max(states)),terminal_c=states[-1],
        grid_rejected_hours=rejected,grid_rejected_heating_hours=sum(s=='rejected_grid' and h>TOL for s,h in zip(statuses,expected['heat_required_kw'])),
        max_duty_fraction=max(expected['duty_fraction']),constraint_failure_hours=0)
    check(summary['status']=='feasible','summary not feasible')
    for k,v in independent.items():
        if isinstance(v,(int,float)):err['summary_'+k]=close(summary[k],v,'summary '+k)
        else:check(summary[k]==v,'summary '+k)
    return dict(passed=True,hours=n,max_absolute_error=max(err.values()),errors=err,independent_summary=independent), np.array(states)


def audit_run(out):
    out=Path(out);manifest=json.loads((out/'manifest.json').read_text(encoding='utf8'))
    for group,parent in [('input_sha256',out/'input_bundle'),('code_sha256',out/'frozen_code'),('result_sha256',out)]:
        for name,value in manifest[group].items():check(digest(parent/name)==value,'frozen file hash changed: '+name)
    p=json.loads((out/'protocol.json').read_text(encoding='utf8'));g=pd.read_csv(out/'admitted_heating_points.csv',float_precision='round_trip')
    check(g.groupby('model').size().to_dict()=={MODELS[0]:83,MODELS[1]:82},'wrong admitted point counts')
    check(not g.duplicated(['model','ambient_c','leaving_water_c']).any(),'duplicate grid')
    check(g.q_unit.eq('kW').all() and g.p_unit.eq('kW').all() and np.isfinite(g.q_kw).all() and np.isfinite(g.p_kw).all() and (g.q_kw>0).all() and (g.p_kw>0).all(),'invalid Q/P or units')
    check(g.source_sha256.eq(digest(out/'input_bundle/tica_24p_catalogue.pdf')).all(),'map source identity')
    close(g.cop_derived,g.q_kw/g.p_kw,'all165 derived COP')
    points={(r.model,r.ambient_c,r.leaving_water_c):(r.q_kw,r.p_kw) for r in g.itertuples()}
    check(manifest['case_count_expected']==9 and manifest['case_count_completed']==9 and manifest['failures']==[],'missing/failed cases retained')
    check(json.loads((out/'failures.json').read_text(encoding='utf8'))==[],'failure file nonempty')
    results={};table=pd.read_csv(out/'operating_comparison.csv',float_precision='round_trip');check(len(table)==9,'comparison coverage')
    tariff=pd.read_csv(out/'input_bundle/wuhan_tou_hourly.csv',float_precision='round_trip')
    for city in p['cities']:
        driver=pd.read_csv(out/(city+'_frozen_driver.csv'),float_precision='round_trip')
        original=pd.read_csv(out/'input_bundle'/(city+'_hourly.csv'),float_precision='round_trip')
        check(driver.time.tolist()==original.time.tolist()==tariff.time.tolist(),'original weather/tariff clock')
        for key in ['outdoor_temp','relative_humidity_pct','internal_gain_kw','solar_gain_kw','dt_hours','period_weight','scenario_probability']:
            close(driver[key],original[key],'driver '+key)
        close(driver.period_weight,1,'period weights');close(driver.scenario_probability,1,'scenario probabilities')
        close(driver.electricity_price,tariff.electricity_price,'frozen common tariff');close(driver.grid_emission_factor,tariff.grid_emission_factor,'frozen common factor')
        reference_states=None;baseline=None
        for choice in p['design']['module_candidates']:
            case=city+'__'+choice['case_id'];folder=out/case
            dispatch=pd.read_csv(folder/'dispatch.csv',float_precision='round_trip',keep_default_na=False)
            summary=json.loads((folder/'summary.json').read_text(encoding='utf8'))
            check(summary['city']==city and summary['case_id']==choice['case_id'],'case identity')
            result,states=audit_case(driver,dispatch,summary,p,points,choice);results[case]=result
            if reference_states is None:reference_states=states;baseline=result['independent_summary']
            else:close(states,reference_states,'same complete temperature service trajectory')
            row=table[(table.city==city)&(table.case_id==choice['case_id'])];check(len(row)==1,'missing/duplicate comparison row')
            for key,value in result['independent_summary'].items():
                if isinstance(value,(float,int)):close(row.iloc[0][key],value,'comparison '+key)
            close(row.iloc[0].conditional_energy_charge_saving_cny,baseline['energy_charge_cny']-summary['energy_charge_cny'],'comparison saving bill')
            close(row.iloc[0].conditional_electricity_saving_kwh,baseline['electricity_kwh']-summary['electricity_kwh'],'comparison saving energy')
    return dict(passed=True,audit_kind='independent_numeric_ledger_not_source_endpoint_authentication',tolerance_absolute=TOL,
        cases_checked=len(results),hours_checked=sum(x['hours'] for x in results.values()),all_three_city_same_service=True,
        source_cell_report='source_admission.json and pdf_csv_point_check.csv are separate PDF-source admission evidence',cases=results)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('run',type=Path);parser.add_argument('--audit-out',type=Path)
    args=parser.parse_args();target=args.audit_out or args.run/'independent_numeric_audit.json'
    check(not target.exists(),'existing audit cannot be overwritten')
    try:result=audit_run(args.run)
    except Exception as error:result=dict(passed=False,error=f'{type(error).__name__}: {error}')
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8')
    print(json.dumps({k:v for k,v in result.items() if k!='cases'},ensure_ascii=False));return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
