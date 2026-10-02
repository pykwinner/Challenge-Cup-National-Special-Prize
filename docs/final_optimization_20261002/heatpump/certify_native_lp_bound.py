"""Exact-rational finite-box Lagrangian lower bound for the encoded binary64 LP.

Legacy SciPy dual_objective/lower_bound columns remain numeric estimates. This
sidecar does not upgrade the incumbent's numerical feasibility or field physics.
All binary64 LP coefficients are interpreted as exact rational numbers here.
"""
from fractions import Fraction
import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd


def require(ok,message):
    if not ok:raise ValueError(message)


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def F(value):return Fraction.from_float(float(value))


def certify_case(driver,dispatch,dual,p):
    f=p['physics'];d=p['design'];n=len(driver);dt=p['dt_hours']
    require(len(dispatch)==n,'incomplete native LP')
    require(len(dual['equality'])==len(dual['grid_inequality'])==n,'certificate size')
    # Reproduce the frozen producer's binary64 coefficient evaluations exactly.
    a=math.exp(-f['H_kw_per_K']*dt/f['C_kwh_per_K']);b=(1-a)/f['H_kw_per_K']
    gain=(driver.internal_gain_kw+driver.solar_gain_kw).to_numpy()*d['prototype_copies']
    rhs=(1-a)*driver.outdoor_temp.to_numpy()+b*gain;rhs[0]+=a*f['initial_c']
    coef=dispatch.hp_electric_per_heat.to_numpy(float);capacity=dispatch.available_hp_heat_kw.to_numpy(float)
    price=driver.electricity_price.to_numpy(float)*dt
    cost_h=price*coef;cost_r=price/d['resistance_eta'];cost_c=price/d['cooling_eer']
    y=[F(v) for v in dual['equality']];z=[F(min(float(v),0.)) for v in dual['grid_inequality']]
    require(np.isfinite(np.concatenate([rhs,coef,capacity,cost_h,cost_r,cost_c])).all(),'nonfinite native coefficient')
    af,bf,rf,cf=F(a),F(b),F(1/d['resistance_eta']),F(1/d['cooling_eer'])
    constant=sum((y[i]*F(rhs[i])+z[i]*F(d['grid_limit_kw']) for i in range(n)),Fraction(0))
    minimum=Fraction(0)
    for i in range(n):
        residuals=[F(cost_h[i])+bf*y[i]-F(coef[i])*z[i],
                   F(cost_r[i])+bf*y[i]-rf*z[i],
                   F(cost_c[i])-bf*y[i]-cf*z[i],
                   -y[i]+(af*y[i+1] if i+1<n else Fraction(0))]
        lower=[Fraction(0)]*3+[F(f['terminal_c'] if i==n-1 else f['min_c'])]
        upper=[F(capacity[i]),F(d['resistance_heat_limit_kw']),F(d['cooling_limit_kw']),F(f['terminal_c'] if i==n-1 else f['max_c'])]
        minimum+=sum((min(r*lo,r*hi) for r,lo,hi in zip(residuals,lower,upper)),Fraction(0))
    exact=constant+minimum
    floor=math.nextafter(float(exact),-math.inf)
    require(F(floor)<=exact,'floating serialization did not round bound down')
    return dict(strict_encoded_binary64_lp_lower_bound_cny=floor,
        exact_rational_lower_bound_numerator=str(exact.numerator),exact_rational_lower_bound_denominator=str(exact.denominator),
        arithmetic='Fraction exact rational arithmetic on binary64 LP coefficients; displayed bound rounded downward to binary64',
        formula='z=min(z,0);g=c-Aeq.T*y-Aub.T*z;LB=y.T*beq+z.T*bub+sum_j min(g_j*lb_j,g_j*ub_j)',
        lower_upper_marginals_used=False,
        scope='valid weak-dual bound for encoded finite binary64 electricity-energy-charge LP only; not a bound on carbon,capital/full bills,or physical reality',
        incumbent_scope='solver incumbent remains feasible/optimal only within the independently checked numeric tolerances; no exact feasible upper bound or exact/lexicographic optimum asserted')


def certify_run(out):
    out=Path(out);p=json.loads((out/'protocol.json').read_text(encoding='utf8'))
    numeric=json.loads((out/'independent_optimization_audit.json').read_text(encoding='utf8'));require(numeric['passed'],'numeric audit must precede rational bound')
    manifest=json.loads((out/'manifest.json').read_text(encoding='utf8'))
    for group,parent in [('input_sha256',out/'inputs'),('code_sha256',out/'frozen_code'),('result_sha256',out)]:
        for name,value in manifest[group].items():require(digest(parent/name)==value,'native LP source changed: '+name)
    result={}
    for city in p['cities']:
        driver=pd.read_csv(out/'inputs'/(city+'_frozen_driver.csv'),float_precision='round_trip')
        for choice in p['design']['module_candidates']:
            if choice['units']==0:continue
            name=city+'__'+choice['case_id'];case=out/name
            dispatch=pd.read_csv(case/'dispatch.csv',float_precision='round_trip',keep_default_na=False)
            dual=json.loads((case/'dual_certificate.json').read_text(encoding='utf8'));solver=json.loads((case/'solver.json').read_text(encoding='utf8'))
            bound=certify_case(driver,dispatch,dual,p)
            bound.update(legacy_dual_value_numeric_estimate_cny=solver['dual_objective_cny'],
                legacy_comparison_column='energy_charge_dual_lower_bound_cny is a legacy numeric dual estimate, not the new strict bound',
                numerical_incumbent_energy_charge_cny=solver['primal_objective_cny'],
                numerical_incumbent_minus_strict_bound_cny=solver['primal_objective_cny']-bound['strict_encoded_binary64_lp_lower_bound_cny'])
            result[name]=bound
    require(len(result)==6,'native-bound coverage')
    return dict(passed=True,cases_checked=6,original_frozen_LP_files_modified=False,original_dual_estimates_retained=True,
        strict_bound_interpretation='exact arithmetic weak duality for encoded binary64 finite LP; numerical incumbent remains tolerance-feasible',
        numeric_audit_sha256=digest(out/'independent_optimization_audit.json'),manifest_sha256=digest(out/'manifest.json'),cases=result)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('run',type=Path);parser.add_argument('--out',type=Path)
    args=parser.parse_args();target=args.out or args.run/'native_lp_bound_sidecar.json';require(not target.exists(),'sidecar exists; frozen evidence cannot be overwritten')
    try:result=certify_run(args.run)
    except Exception as error:result=dict(passed=False,error=f'{type(error).__name__}: {error}')
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8')
    print(json.dumps({k:v for k,v in result.items() if k!='cases'},ensure_ascii=False));return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
