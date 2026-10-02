"""Catalogue provenance/domain and independent continuous-ledger counterexamples."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import performance_map as pm
import run_reference as runner
import audit_reference as audit


@pytest.fixture(scope='module')
def grid():
    bundled=HERE/'run_01/input_bundle/tica_24p_catalogue.pdf'
    source=bundled if bundled.exists() else runner.SUPPLEMENT/'sources/大品牌/TICA_VForce变频模块机_EN.pdf'
    return pm.extract_heating_grid(source)


def test_all165_exact_pdf_cells_and_derived_cop(grid):
    map_=pm.CatalogueMap(grid)
    assert grid.groupby('model').size().to_dict()=={'TCAV035BHE':83,'TCAV065BHE':82}
    assert (grid.pdf_page==15).all() and (grid.evidence_kind=='manufacturer_catalogue_sample_not_measurement').all()
    for row in grid.itertuples():
        point=map_.query(row.model,row.ambient_c,row.leaving_water_c)
        assert (point.q_kw,point.p_kw)==(row.q_kw,row.p_kw)
        assert point.cop==pytest.approx(row.q_kw/row.p_kw,abs=1e-14)
        assert point.corners==((row.ambient_c,row.leaving_water_c,1.),)


def test_all165_user_rows_match_without_changing_original(grid):
    bundled=HERE/'run_01/input_bundle/user_heatpump_extraction_original.csv'
    source=bundled if bundled.exists() else runner.SUPPLEMENT/'tables/heatpump_datasheet_extracted.csv'
    before=pm.sha(source);report=pm.compare_user_csv(grid,source)
    assert len(report)==165 and report.q_p_exact_match.all() and report.old_cop_blank.all()
    assert pm.sha(source)==before


def test_bilinear_q_and_p_not_cop_interpolation(grid):
    point=pm.CatalogueMap(grid).query('TCAV035BHE',-17.5,47.5)
    assert point.q_kw==pytest.approx((19.3+22.6+19.2+21.8)/4)
    assert point.p_kw==pytest.approx((11.6+11.9+13.3+13.5)/4)
    assert point.cop==pytest.approx(point.q_kw/point.p_kw)
    wrong_cop=np.mean([19.3/11.6,22.6/11.9,19.2/13.3,21.8/13.5])
    assert abs(point.cop-wrong_cop)>1e-4
    assert len(point.corners)==4 and sum(x[2] for x in point.corners)==pytest.approx(1)


@pytest.mark.parametrize('model,x,y',[
    ('TCAV035BHE83',7,45),('TCAV065BHE82',7,45),('TCAV035BE',7,45),
    ('TCAV035BHE',-26.0000001,30),('TCAV035BHE',55.0000001,30),
    ('TCAV035BHE',0,29.9999999),('TCAV035BHE',0,55.0000001),
    ('TCAV035BHE',-26,45),('TCAV035BHE',-23,45),('TCAV035BHE',-23,42.5),
    ('TCAV035BHE',-1,55),('TCAV035BHE',-1,52.5),('TCAV065BHE',-20,50),
    ('TCAV065BHE',-17.5,50),('TCAV035BHE',float('nan'),45),('TCAV035BHE',7,float('inf'))])
def test_no_alias_extrapolation_or_gap_filling(grid,model,x,y):
    with pytest.raises(ValueError):pm.CatalogueMap(grid).query(model,x,y)


def test_48_is_the_printed_header_and_not45(grid):
    assert pm.CatalogueMap(grid).query('TCAV035BHE',48,30).q_kw==49
    p=pm.CatalogueMap(grid).query('TCAV035BHE',45,30)
    assert p.q_kw==pytest.approx(46.1+(49-46.1)*10/13)


@pytest.mark.parametrize('field,value',[('source_id','unverified'),('source_sha256','otherpdf'),('condition_id','other_wet_bulb'),
    ('model','TCAV035BHE83'),('q_unit','W'),('p_unit','kWh'),('q_kw',-1),('p_kw',0),('q_kw',float('nan')),('ambient_c',6)])
def test_cross_source_condition_units_and_invalid_values_rejected(grid,field,value):
    bad=grid.copy();bad.loc[0,field]=value
    with pytest.raises(ValueError):pm.CatalogueMap(bad)


def test_duplicate_and_missing_corner_rejected(grid):
    with pytest.raises(ValueError):pm.CatalogueMap(pd.concat([grid,grid.iloc[:1]],ignore_index=True))
    reduced=grid[~((grid.model=='TCAV035BHE')&(grid.ambient_c==-15)&(grid.leaving_water_c==45))]
    with pytest.raises(ValueError):pm.CatalogueMap(reduced).query('TCAV035BHE',-17.5,47.5)


def small_inputs(outdoor,dt=1.):
    n=len(outdoor);p=runner.protocol(dict(heat_loss_kw_per_c=.1043,inner_material_capacity_kwh_per_c=.535576))
    p['dt_hours']=dt;p['hours_per_city']=n;p['window_end_exclusive']=(pd.Timestamp(p['window_start_inclusive'])+pd.Timedelta(hours=n*dt)).isoformat()
    frame=pd.DataFrame(dict(time=pd.date_range(p['window_start_inclusive'],periods=n,freq=pd.Timedelta(hours=dt)).map(lambda x:x.isoformat()),
        outdoor_temp=outdoor,relative_humidity_pct=80.,internal_gain_kw=.2,solar_gain_kw=0.,electricity_price=.8,grid_emission_factor=.4,dt_hours=dt))
    return frame,p


def test_48h_continuity_same_service_finite_backup_and_ledger(grid):
    frame,p=small_inputs([-22.]*6+[5.]*6+[30.]*20+[0.]*16)
    points={(r.model,r.ambient_c,r.leaving_water_c):(r.q_kw,r.p_kw) for r in grid.itertuples()}
    baseline,base=runner.simulate(frame,p,pm.CatalogueMap(grid),None,0)
    dispatch,summary=runner.simulate(frame,p,pm.CatalogueMap(grid),'TCAV035BHE',1)
    report,_=audit.audit_case(frame,dispatch,summary,p,points,dict(model='TCAV035BHE',units=1))
    assert report['passed'] and summary['status']=='feasible' and summary['grid_rejected_hours']==6
    assert dispatch.iloc[0].hp_heat_kw==0 and dispatch.iloc[0].resistance_heat_kw>0
    assert dispatch.iloc[24].temperature_start_c==pytest.approx(dispatch.iloc[23].temperature_end_c)
    assert dispatch.iloc[24].temperature_start_c>20 # would fail if midnight reset
    assert np.allclose(dispatch.temperature_end_c,baseline.temperature_end_c,atol=1e-10)
    assert summary['terminal_c']==pytest.approx(20) and summary['cooling_kwh']>0
    assert summary['electricity_kwh']<base['electricity_kwh']


def test_halfhour_units_not_kw_as_kwh(grid):
    frame,p=small_inputs([-10.]*8,dt=.5)
    dispatch,summary=runner.simulate(frame,p,pm.CatalogueMap(grid),'TCAV035BHE',1)
    assert summary['electricity_kwh']==pytest.approx(dispatch.grid_power_kw.sum()*.5)
    assert summary['energy_charge_cny']==pytest.approx(summary['electricity_kwh']*.8)
    assert summary['carbon_proxy_kg']==pytest.approx(summary['electricity_kwh']*.4)


def test_constraints_fail_and_dispatch_is_retained(grid):
    frame,p=small_inputs([-40.]*3)
    dispatch,summary=runner.simulate(frame,p,pm.CatalogueMap(grid),'TCAV035BHE',1)
    assert len(dispatch)==3 and summary['status']=='failed_constraints'
    assert summary['constraint_failure_hours']==3 and (dispatch.grid_power_kw>p['design']['grid_limit_kw']).all()
    assert dispatch.map_status.eq('rejected_grid').all() and dispatch.hp_heat_kw.eq(0).all()


@pytest.mark.parametrize('field',['energy_charge_cny','hp_power_kw','temperature_start_c','catalogue_p_kw','duty_fraction','grid_power_kw'])
def test_independent_audit_detects_ledger_and_state_tampering(grid,field):
    frame,p=small_inputs([-10.]*6)
    dispatch,summary=runner.simulate(frame,p,pm.CatalogueMap(grid),'TCAV035BHE',1)
    points={(r.model,r.ambient_c,r.leaving_water_c):(r.q_kw,r.p_kw) for r in grid.itertuples()}
    dispatch.loc[1,field]+=.1
    with pytest.raises(ValueError):audit.audit_case(frame,dispatch,summary,p,points,dict(model='TCAV035BHE',units=1))


@pytest.mark.parametrize('units',[.5,1.,2,-1])
def test_noninteger_or_unregistered_package_choices_rejected(grid,units):
    frame,p=small_inputs([-10.]*3)
    with pytest.raises(ValueError):runner.simulate(frame,p,pm.CatalogueMap(grid),'TCAV035BHE',units)


def test_driver_gap_and_invalid_account_axis_rejected(grid):
    frame,p=small_inputs([-10.]*3);frame.loc[1,'time']=frame.loc[0,'time']
    with pytest.raises(ValueError):runner.simulate(frame,p,pm.CatalogueMap(grid),None,0)
    frame,p=small_inputs([-10.]*3);frame.loc[1,'electricity_price']=float('nan')
    with pytest.raises(ValueError):runner.simulate(frame,p,pm.CatalogueMap(grid),None,0)


@pytest.mark.parametrize('column,value',[('period_weight',2.),('scenario_probability',.5),('period_weight',float('nan')),('scenario_probability',float('inf'))])
def test_nonunit_weighted_input_rejected_before_simulation(grid,column,value):
    frame,p=small_inputs([-10.]*3);frame[column]=1.;frame.loc[1,column]=value
    with pytest.raises(ValueError):runner.simulate(frame,p,pm.CatalogueMap(grid),None,0)


@pytest.mark.parametrize('layout',['framework/north_heat_challenge','github_candidate_20261002_rc6'])
def test_repository_local_supplement_in_original_and_flat_layouts(tmp_path,layout):
    root=tmp_path/layout;local=root/'补充数据_20261002/13_热泵机组样本';local.mkdir(parents=True)
    assert runner.resolve_supplement(root)==local


def test_no_implicit_cross_repository_source_search(tmp_path):
    root=tmp_path/'framework/north_heat_challenge';root.mkdir(parents=True)
    old=tmp_path/'github_candidate_20261002_rc5/补充数据_20261002/13_热泵机组样本';old.mkdir(parents=True)
    with pytest.raises(ValueError):runner.resolve_supplement(root)
    assert runner.resolve_supplement(root,old)==old.resolve()


def test_frozen_bundle_portability_needs_no_project_source_lookup(tmp_path,monkeypatch):
    source=tmp_path/'source';source.mkdir();(source/'sentinel.txt').write_text('exact source bytes',encoding='utf8')
    out=tmp_path/'newrun';out.mkdir()
    monkeypatch.setattr(runner,'ROOT',tmp_path/'missing_project')
    bundle=runner.freeze_inputs(out,source_dir=source)
    assert (bundle/'sentinel.txt').read_text(encoding='utf8')=='exact source bytes'


def test_full_frozen_run_and_all_case_coverage():
    if not (HERE/'run_01/manifest.json').exists():pytest.skip('real run not yet produced')
    report=audit.audit_run(HERE/'run_01')
    assert report['passed'] and report['cases_checked']==9 and report['hours_checked']==25920
    assert report['all_three_city_same_service']
