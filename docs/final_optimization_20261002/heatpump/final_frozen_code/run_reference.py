"""Standalone, continuous 2880h catalogue-conditioned operating ledger.

No heatplan/investment-runner imports. No TICA quotation or capital-price proxy.
All non-catalogue equipment/aggregation/part-load assumptions are explicit axes.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from math import exp
from pathlib import Path
import shutil
import sys

import numpy as np
import pandas as pd

from performance_map import CatalogueMap, MODELS, SOURCE_ID, SOURCE_URL, PDF_SHA256, extract_heating_grid, compare_user_csv, sha, require

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SUPPLEMENT = ROOT / '补充数据_20261002/13_热泵机组样本'
CITIES = ('urumqi', 'guyuan', 'wuhan')


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf8')


def protocol(reference):
    scale = 20
    return dict(schema_version=1, experiment_id='TICA_exact_catalogue_conditional_operation_v1',
        frozen_utc=datetime.now(timezone.utc).isoformat(),
        window_start_inclusive='2024-11-16T00:00:00+08:00', window_end_exclusive='2025-03-16T00:00:00+08:00',
        hours_per_city=2880, cities=list(CITIES), dt_hours=1,
        physics=dict(H_kw_per_K=reference['heat_loss_kw_per_c']*scale,
                     C_kwh_per_K=reference['inner_material_capacity_kwh_per_c']*scale,
                     initial_c=20., terminal_c=20., min_c=20., max_c=27.),
        design=dict(prototype_copies=scale, floor_area_m2=48*scale,
            aggregation_status='declared synthetic research scale: 20 identical public prototype copies, not20 real houses or a surveyed local building',
            module_candidates=[dict(case_id='resistance_only', model=None, units=0),
                               dict(case_id='TCAV035BHE_n1', model=MODELS[0], units=1),
                               dict(case_id='TCAV065BHE_n1', model=MODELS[1], units=1)],
            leaving_water_c=45., resistance_heat_limit_kw=90., cooling_limit_kw=80., grid_limit_kw=100.,
            resistance_eta=1., cooling_eer=3., delivered_capacity_factor=1.,
            part_load='hypothetical ideal hourly duty: heat=duty*catalogueQ, power=duty*catalogueP, 0<=duty<=1; no tested inverter/cycling curve',
            capacity_scope='single whole module; unit count integer and fixed; rated-water-flow catalogue condition, ideal exchanger',
            cooling_scope='separate hypothetical finite cooler, not a TICA cooling performance claim'),
        controller='continuous RC deadband thermostat: clip free next state to20..27; final step targets20; HP first then finite resistance; no daily reset',
        dynamics='T[t+1]=a*T[t]+(1-a)*Tout[t]+b*(gain[t]+qhp[t]+qr[t]-qc[t]); a=exp(-H*dt/C), b=(1-a)/H',
        monetary_scope='operating electricity energy charge only; sum(price_CNY_per_kWh*grid_kW*dt_h), no fixed/demand charge, tax adjustment, capex or maintenance',
        price_scope='published Hubei low-voltage industrial/commercial monthly TOU conditionally applied to all cities as common methodological axis; Wuhan geography matches Hubei but account eligibility/connection is not verified in any city; no actual local bill prediction',
        emissions_scope='same admitted Hubei2022 annual grid factor applied as common methodological axis; not hourly marginal carbon or other-city local factor',
        data_scope='NASA POWER gridded weather/reanalysis and published prototype/tariff; not site measurements or fitted local H/C',
        catalogue_scope='manufacturer-authored sample distributed by third-party; exact PDF SHA and all165 Q/P checked; grid wet bulb/flow detail/defrost integration unspecified',
        excluded=['TICA capital quotation','FT8 17800 CNY package price transfer','investment optimum','payback/annualization/repeated seasons',
                  'pump/fan standby beyond catalogue input boundary','pipe/exchanger losses','start-stop degradation','unprovided humidity/defrost corrections',
                  'hydraulic/latent load/network/field validation','procurement thresholds as performance'],
        claim='conditional operating comparison under explicit catalogue and ideal-duty assumptions, not realized equipment savings or final investment recommendation')


def validate_inputs(frame, p):
    needed = ['time','outdoor_temp','relative_humidity_pct','internal_gain_kw','solar_gain_kw','electricity_price','grid_emission_factor','dt_hours']
    require(all(x in frame for x in needed) and len(frame)>0, 'missing/empty hourly input')
    for x in needed[1:]:
        require(np.isfinite(frame[x].to_numpy(float)).all(), 'nonfinite '+x)
    require((frame.electricity_price>0).all() and (frame.grid_emission_factor>0).all(), 'nonpositive price/factor')
    require(((frame.relative_humidity_pct>=0)&(frame.relative_humidity_pct<=100)).all(), 'RH out of range')
    t = pd.DatetimeIndex(pd.to_datetime(frame.time))
    require(t.tz is not None and t.is_unique and t.is_monotonic_increasing, 'time zone/order/duplicate error')
    dt = p['dt_hours']
    require(np.all(frame.dt_hours.to_numpy(float)==dt), 'mixed dt')
    for key in ['period_weight','scenario_probability']:
        if key in frame:
            require(np.isfinite(frame[key].to_numpy(float)).all() and np.all(frame[key].to_numpy(float)==1), 'continuous single-scenario input requires unit '+key)
    require(len(t)==1 or np.all(np.diff(t.asi8)==round(dt*3.6e12)), 'chronology gap')
    require(len(t)==p['hours_per_city'] and t[0]==pd.Timestamp(p['window_start_inclusive']) and
            t[-1]+pd.Timedelta(hours=dt)==pd.Timestamp(p['window_end_exclusive']), 'wrong frozen window')
    f=p['physics']; d=p['design']
    for key in ['H_kw_per_K','C_kwh_per_K']:
        require(np.isfinite(f[key]) and f[key]>0, 'invalid physics')
    require(dt>0 and np.isfinite(dt), 'invalid dt')
    require(f['min_c']<=f['initial_c']<=f['max_c'] and f['min_c']<=f['terminal_c']<=f['max_c'], 'invalid initial/terminal')
    for key in ['resistance_heat_limit_kw','cooling_limit_kw','grid_limit_kw','cooling_eer','resistance_eta','delivered_capacity_factor']:
        require(np.isfinite(d[key]) and d[key]>0, 'invalid design '+key)
    require(d['resistance_eta']<=1 and d['delivered_capacity_factor']<=1, 'invalid efficiency/factor')


def simulate(frame, p, map_, model, units):
    validate_inputs(frame,p)
    require(type(units) is int and units in (0,1) and ((units==0 and model is None) or (units==1 and model in MODELS)), 'invalid whole-module choice')
    f=p['physics']; d=p['design']; dt=p['dt_hours']
    a=exp(-f['H_kw_per_K']*dt/f['C_kwh_per_K']); b=(1-a)/f['H_kw_per_K']
    state=f['initial_c']; records=[]
    for i, raw in enumerate(frame.to_dict('records')):
        gain=(raw['internal_gain_kw']+raw['solar_gain_kw'])*d['prototype_copies']
        free=a*state+(1-a)*raw['outdoor_temp']+b*gain
        target=f['terminal_c'] if i==len(frame)-1 else min(f['max_c'],max(f['min_c'],free))
        needed=(target-free)/b
        heat=max(needed,0.); cool=max(-needed,0.)
        q=p_input=cop=0.; corners=[]; rejection=''; status='no_module'
        if units:
            try:
                point=map_.query(model, raw['outdoor_temp'],d['leaving_water_c'])
                q=point.q_kw; p_input=point.p_kw; cop=point.cop; corners=point.corners; status='admitted_grid'
            except ValueError as error:
                status='rejected_grid'; rejection=str(error)
        capacity=units*q*d['delivered_capacity_factor']
        hp_heat=min(heat,capacity); duty=hp_heat/capacity if capacity>0 else 0.
        hp_power=duty*units*p_input
        resistance=heat-hp_heat; rpower=resistance/d['resistance_eta']; cpower=cool/d['cooling_eer']
        grid=hp_power+rpower+cpower
        next_state=a*state+(1-a)*raw['outdoor_temp']+b*(gain+hp_heat+resistance-cool)
        violations=[]
        for name,value,bound in [('resistance',resistance,d['resistance_heat_limit_kw']),('cooling',cool,d['cooling_limit_kw']),('grid',grid,d['grid_limit_kw'])]:
            if value>bound+1e-8: violations.append(name)
        row=dict(hour_index=i,time=raw['time'],interval_end=(pd.Timestamp(raw['time'])+pd.Timedelta(hours=dt)).isoformat(),
            outdoor_temp_c=raw['outdoor_temp'],relative_humidity_pct=raw['relative_humidity_pct'],
            electricity_price_cny_per_kwh=raw['electricity_price'],grid_factor_kg_per_kwh=raw['grid_emission_factor'],
            dt_hours=dt,model=model or 'none',units=units,leaving_water_c=d['leaving_water_c'],
            map_status=status,map_rejection=rejection,corners_json=json.dumps(corners,separators=(',',':')),
            catalogue_q_kw=q,catalogue_p_kw=p_input,catalogue_cop_derived=cop,available_hp_heat_kw=capacity,
            temperature_start_c=state,temperature_end_c=next_state,gain_kw=gain,target_end_c=target,
            heat_required_kw=heat,hp_heat_kw=hp_heat,resistance_heat_kw=resistance,cooling_kw=cool,duty_fraction=duty,
            hp_power_kw=hp_power,resistance_power_kw=rpower,cooling_power_kw=cpower,grid_power_kw=grid,
            grid_energy_kwh=grid*dt,energy_charge_cny=grid*dt*raw['electricity_price'],
            carbon_proxy_kg=grid*dt*raw['grid_emission_factor'],constraint_violations=';'.join(violations))
        records.append(row); state=next_state
    dispatch=pd.DataFrame(records)
    failed=dispatch.constraint_violations.ne('').any()
    summary=dict(status='failed_constraints' if failed else 'feasible',hours=len(dispatch),model=model,units=units,
        electricity_kwh=float(dispatch.grid_energy_kwh.sum()),energy_charge_cny=float(dispatch.energy_charge_cny.sum()),
        carbon_proxy_kg=float(dispatch.carbon_proxy_kg.sum()),hp_heat_kwh=float((dispatch.hp_heat_kw*dt).sum()),
        resistance_heat_kwh=float((dispatch.resistance_heat_kw*dt).sum()),cooling_kwh=float((dispatch.cooling_kw*dt).sum()),
        max_resistance_heat_kw=float(dispatch.resistance_heat_kw.max()),max_cooling_kw=float(dispatch.cooling_kw.max()),
        max_grid_power_kw=float(dispatch.grid_power_kw.max()),min_temperature_c=float(min(f['initial_c'],dispatch.temperature_end_c.min())),
        max_temperature_c=float(max(f['initial_c'],dispatch.temperature_end_c.max())),terminal_c=float(state),
        grid_rejected_hours=int(dispatch.map_status.eq('rejected_grid').sum()),
        grid_rejected_heating_hours=int((dispatch.map_status.eq('rejected_grid')&dispatch.heat_required_kw.gt(1e-9)).sum()),
        max_duty_fraction=float(dispatch.duty_fraction.max()),constraint_failure_hours=int(dispatch.constraint_violations.ne('').sum()))
    return dispatch,summary


def resolve_supplement(root, explicit=None):
    selected=Path(explicit).resolve() if explicit is not None else Path(root)/'补充数据_20261002/13_热泵机组样本'
    require(selected.is_dir(), 'No repository-local supplement. Use --source-dir with frozen input_bundle, or explicitly pass --supplement-dir for the original source directory; no cross-repository search is performed.')
    return selected


def freeze_inputs(out, source_dir=None, supplement_dir=None):
    bundle=out/'input_bundle'; bundle.mkdir()
    if source_dir:
        for p in Path(source_dir).iterdir():
            if p.is_file(): shutil.copy2(p,bundle/p.name)
    else:
        supplement=resolve_supplement(ROOT,supplement_dir)
        paths={
            'tica_24p_catalogue.pdf':supplement/'sources/大品牌/TICA_VForce变频模块机_EN.pdf',
            'user_heatpump_extraction_original.csv':supplement/'tables/heatpump_datasheet_extracted.csv',
            'physical_reference.json':ROOT/'docs/implementation_20261002_followup/parallel_stage_02/data/common_physical_reference.json',
            'weather_provenance.csv':ROOT/'docs/implementation_20261002_followup/parallel_stage_02/data/field_provenance.csv',
            'weather_manifest.json':ROOT/'docs/implementation_20261002_followup/parallel_stage_02/data/manifest.json',
            'weather_protocol.json':ROOT/'docs/implementation_20261002_followup/parallel_stage_02/data/protocol.json',
            'wuhan_tou_hourly.csv':ROOT/'data/derived/evidence_case_20261002/wuhan_2024_2025_hourly_tou.csv',
            'tariff_admission.json':ROOT/'docs/implementation_20261002/data/admission_tou.json',
            'tariff_account_and_hours.json':ROOT/'docs/implementation_20261002/data/tou_account_and_hours.json',
            'monthly_tariff_verified.csv':ROOT/'docs/implementation_20261002/data/monthly_tariff_verified.csv',
        }
        for city in CITIES:
            paths[city+'_hourly.csv']=ROOT/f'data/derived/three_city_common_reference_20261002/{city}_hourly.csv'
        for name,path in paths.items(): shutil.copy2(path,bundle/name)
        save(out/'original_input_paths.json',{name:str(path) for name,path in paths.items()})
    return bundle


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,default=HERE/'run_01');parser.add_argument('--source-dir',type=Path);parser.add_argument('--supplement-dir',type=Path)
    args=parser.parse_args(); out=args.out.resolve()
    require(not(args.source_dir and args.supplement_dir),'choose frozen source-dir or explicit supplement-dir, not both')
    require(not out.exists(), 'output already exists; frozen results cannot be replaced')
    out.mkdir(parents=True); bundle=freeze_inputs(out,args.source_dir,args.supplement_dir)
    grid=extract_heating_grid(bundle/'tica_24p_catalogue.pdf')
    comparison=compare_user_csv(grid,bundle/'user_heatpump_extraction_original.csv')
    grid.to_csv(out/'admitted_heating_points.csv',index=False,encoding='utf8')
    comparison.to_csv(out/'pdf_csv_point_check.csv',index=False,encoding='utf8')
    save(out/'source_admission.json', dict(status='admitted_exact_catalogue_copy_conditionally',source_id=SOURCE_ID,source_url=SOURCE_URL,
        source_sha256=PDF_SHA256,original_pdf_read_this_run=True,pdf_pages_visually_inspected_during_preparation=[14,15],all_q_p_points_checked=165,
        complete_points_by_model={MODELS[0]:83,MODELS[1]:82},pdf_csv_mismatches=0,
        manufacturer_endpoint_table_verified=False,
        primary_authorship='TICA-branded manufacturer catalogue, distributed copy; not independent laboratory/field report',
        official_family_url='https://global.tica.com/index/goods/pro/cid/23.html',
        official_family_page_scope='product family only; site text names TCAV035BE and TCAV065BHE, not exact version authentication of TCAV035BHE table',
        conspicuous_model_correction='READING CORRECTION: earlier task-summary wording concatenated model and point count as TCAV035BHE83/TCAV065BHE82. Original user CSV correctly names TCAV035BHE/TCAV065BHE, with83/82 grid points. This was our summary-reading ambiguity, not a user CSV model error. No suffix aliases accepted; original CSV not changed.',
        csv_nominal_missing_source_temperature='Four p14 nominal CSV rows have empty source-temperature columns; source notes supply DB/WB. They are documented separately, not silently joined into p15 grid.',
        nominal_heating_conditions=[dict(leaving_water_c=41,dry_bulb_c=-12,wet_bulb_c=-14,q_kw=[24,48],p_kw=[10.2,20.5]),
                                   dict(leaving_water_c=45,dry_bulb_c=7,wet_bulb_c=6,q_kw=[34,75],p_kw=[10.5,23.4])],
        p14_conditions='nominal records at rated water flow; single-module data;035 rated water flow5.76m3/h,06511.2m3/h; controller accessory separately purchased',
        p15_conditions='Heating Capacity Correction; water outlet temperature C, Ambient Temperature C, Capacity kW, Power kW; wet bulb/humidity/defrost integration and flow-versus-temperature details not stated',
        ambient_header48='kept as printed48 C, not corrected to45 C; current weather below35 C',
        capacity_loss_note='p14 says about6% installation-system loss should be considered; not a measured universal derate. Main axis uses explicit ideal delivered-capacity factor1.0.',
        rejection_policy=['different source/version/model/units','procurement thresholds','blank point or any required blank corner','outside printed axes','nominal humidity-specific point mixed into unspecified-humidity grid'],
        cop_kind='computed Q/P from printed kW columns; not a new measured COP and not interpolation of COP',
        excluded_catalogue='39-page marketing slides have no165-point Q/P grid; not merged'))
    p=protocol(json.loads((bundle/'physical_reference.json').read_text(encoding='utf8')));save(out/'protocol.json',p)
    map_=CatalogueMap(grid); tariff=pd.read_csv(bundle/'wuhan_tou_hourly.csv')
    summaries=[]; failures=[]
    for city in CITIES:
        frame=pd.read_csv(bundle/(city+'_hourly.csv'))
        require(frame.time.tolist()==tariff.time.tolist(), 'weather/tariff calendar mismatch')
        frame['electricity_price']=tariff.electricity_price;frame['grid_emission_factor']=tariff.grid_emission_factor
        frame.to_csv(out/(city+'_frozen_driver.csv'),index=False,encoding='utf8')
        for choice in p['design']['module_candidates']:
            case=out/(city+'__'+choice['case_id']);case.mkdir()
            try:
                dispatch,summary=simulate(frame,p,map_,choice['model'],choice['units'])
                dispatch.to_csv(case/'dispatch.csv',index=False,float_format='%.17g',encoding='utf8')
                summary.update(city=city,case_id=choice['case_id']);save(case/'summary.json',summary);summaries.append(summary)
                if summary['status']!='feasible':failures.append(dict(city=city,case_id=choice['case_id'],reason='constraint failure',summary=summary))
            except Exception as error:
                failure=dict(city=city,case_id=choice['case_id'],reason=f'{type(error).__name__}: {error}')
                save(case/'failure.json',failure);failures.append(failure)
    save(out/'failures.json',failures)
    table=pd.DataFrame(summaries)
    if len(table):
        rows=[]
        for row in table.to_dict('records'):
            baseline=table[(table.city==row['city'])&(table.units==0)]
            row['conditional_energy_charge_saving_cny']=None;row['conditional_electricity_saving_kwh']=None
            if len(baseline)==1 and row['status']=='feasible' and baseline.iloc[0]['status']=='feasible':
                row['conditional_energy_charge_saving_cny']=float(baseline.iloc[0].energy_charge_cny-row['energy_charge_cny'])
                row['conditional_electricity_saving_kwh']=float(baseline.iloc[0].electricity_kwh-row['electricity_kwh'])
            rows.append(row)
        pd.DataFrame(rows).to_csv(out/'operating_comparison.csv',index=False,encoding='utf8')
    frozen=out/'frozen_code';frozen.mkdir()
    for name in ['performance_map.py','run_reference.py','audit_reference.py','test_heatpump_reference.py','method_sources.json']:
        if (HERE/name).exists():shutil.copy2(HERE/name,frozen/name)
    save(out/'manifest.json',dict(created_utc=datetime.now(timezone.utc).isoformat(),case_count_expected=9,
        case_count_completed=len(summaries),failures=failures,python=sys.version,numpy=np.__version__,pandas=pd.__version__,
        input_sha256={x.name:sha(x) for x in bundle.iterdir() if x.is_file()},
        code_sha256={x.name:sha(x) for x in frozen.iterdir() if x.is_file()},
        result_sha256={str(x.relative_to(out)).replace('\\','/'):sha(x) for x in out.rglob('*') if x.is_file() and x.parent!=frozen and x.parent!=bundle}))
    print(json.dumps(dict(out=str(out),completed=len(summaries),failures=len(failures)),ensure_ascii=False))
    return 1 if failures else 0


if __name__=='__main__':
    raise SystemExit(main())
