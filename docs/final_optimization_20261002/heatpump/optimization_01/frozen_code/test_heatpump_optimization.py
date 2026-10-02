"""Fixed-asset LP and independent primal/dual certificate counterexamples."""
from copy import deepcopy
from pathlib import Path
import sys

import pytest

HERE=Path(__file__).resolve().parent;sys.path.insert(0,str(HERE))
from test_heatpump_reference import grid,small_inputs
import performance_map as pm
import run_reference as reference
import optimize_reference as producer
import audit_optimization as verifier


def inputs(outdoor):
    frame,p=small_inputs(outdoor)
    p['independent_audit_tolerances']=dict(physical_absolute=1e-8,ledger_absolute=1e-6,dual_stationarity_absolute=1e-8,certificate_gap_cny=1e-6)
    return frame,p


def points(grid):
    return {(r.model,r.ambient_c,r.leaving_water_c):(r.q_kw,r.p_kw) for r in grid.itertuples()}


def test_single_step_analytic_cost_and_exact_catalogue_capacity(grid):
    frame,p=inputs([-10.]);dispatch,summary,solver,dual=producer.solve(frame,p,pm.CatalogueMap(grid),'TCAV035BHE')
    heat=p['physics']['H_kw_per_K']*30-.2*p['design']['prototype_copies']
    assert dispatch.iloc[0].hp_heat_kw==pytest.approx(26.3)
    assert dispatch.iloc[0].resistance_heat_kw==pytest.approx(heat-26.3)
    assert summary['energy_charge_cny']==pytest.approx(.8*(12.1+heat-26.3))
    report=verifier.audit_case(frame,dispatch,summary,solver,dual,p,points(grid))
    assert report['passed'] and abs(report['primal_dual_gap_cny'])<=1e-6


def test_cost_lp_improves_thermostat_with_same_finite_assets_and_boundary(grid):
    frame,p=inputs([0.]*48);frame.loc[:11,'electricity_price']=.1;frame.loc[12:,'electricity_price']=1.5
    thermostat,baseline=reference.simulate(frame,p,pm.CatalogueMap(grid),'TCAV035BHE',1)
    dispatch,summary,solver,dual=producer.solve(frame,p,pm.CatalogueMap(grid),'TCAV035BHE')
    report=verifier.audit_case(frame,dispatch,summary,solver,dual,p,points(grid))
    assert report['passed'] and summary['energy_charge_cny']<baseline['energy_charge_cny']-1
    assert dispatch.temperature_end_c.max()>20.1 and dispatch.temperature_end_c.iloc[-1]==pytest.approx(20)
    assert dispatch.iloc[24].temperature_start_c==pytest.approx(dispatch.iloc[23].temperature_end_c)
    assert solver['objective_kind']=='electricity_energy_charge_cny_only'


def test_gap_refusal_uses_finite_backup_without_fabricated_hp(grid):
    frame,p=inputs([-22.]*3);dispatch,summary,solver,dual=producer.solve(frame,p,pm.CatalogueMap(grid),'TCAV035BHE')
    assert dispatch.map_status.eq('rejected_grid').all() and dispatch.hp_heat_kw.eq(0).all()
    assert verifier.audit_case(frame,dispatch,summary,solver,dual,p,points(grid))['passed']


def test_solver_infeasible_retains_status_without_objective_bound(grid):
    frame,p=inputs([-10.]);p['design']['grid_limit_kw']=.01
    dispatch,summary,solver,dual=producer.solve(frame,p,pm.CatalogueMap(grid),'TCAV035BHE')
    assert dispatch is summary is dual is None and not solver['success'] and solver['status']==2
    assert solver['dual_objective_cny'] is None and solver['primal_objective_cny'] is None


@pytest.mark.parametrize('field',['hp_heat_kw','temperature_end_c','energy_charge_cny','available_hp_heat_kw','grid_power_kw'])
def test_independent_lp_audit_rejects_primal_tampering(grid,field):
    frame,p=inputs([-10.]*4);dispatch,summary,solver,dual=producer.solve(frame,p,pm.CatalogueMap(grid),'TCAV035BHE')
    dispatch.loc[1,field]+=.1
    with pytest.raises(ValueError):verifier.audit_case(frame,dispatch,summary,solver,dual,p,points(grid))


@pytest.mark.parametrize('kind',['equality','grid_inequality','lower_bounds','upper_bounds'])
def test_independent_certificate_rejects_dual_tampering(grid,kind):
    frame,p=inputs([-10.]*4);dispatch,summary,solver,dual=producer.solve(frame,p,pm.CatalogueMap(grid),'TCAV035BHE')
    dual=deepcopy(dual);dual[kind][0]+=.1
    with pytest.raises(ValueError):verifier.audit_case(frame,dispatch,summary,solver,dual,p,points(grid))


def test_optimal_bound_cannot_be_assigned_to_carbon_kpi(grid):
    frame,p=inputs([-10.]);dispatch,summary,solver,dual=producer.solve(frame,p,pm.CatalogueMap(grid),'TCAV035BHE')
    solver['objective_kind']='carbon_proxy_kg'
    with pytest.raises(ValueError):verifier.audit_case(frame,dispatch,summary,solver,dual,p,points(grid))


def test_full_six_city_model_optimization_cases():
    if not (HERE/'optimization_01/manifest.json').exists():pytest.skip('full LP run not yet produced')
    report=verifier.audit_optimization(HERE/'optimization_01')
    assert report['passed'] and report['cases_checked']==6 and report['hours_checked']==17280
    assert report['same_assets_and_service_envelope'] and report['reference_thermostat_audit_passed']
