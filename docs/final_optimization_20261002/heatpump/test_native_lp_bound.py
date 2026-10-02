"""Legacy dual tolerance counterexample and exact finite-box bound regression."""
from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path
import sys

import pandas as pd
import pytest

HERE=Path(__file__).resolve().parent;sys.path.insert(0,str(HERE))
import audit_optimization as numerical
import certify_native_lp_bound as exact


def real_case():
    out=HERE/'optimization_01';case=out/'wuhan__TCAV065BHE_n1'
    read=lambda p:json.loads(p.read_text(encoding='utf8'))
    p=read(out/'protocol.json');driver=pd.read_csv(out/'inputs/wuhan_frozen_driver.csv',float_precision='round_trip')
    dispatch=pd.read_csv(case/'dispatch.csv',float_precision='round_trip',keep_default_na=False)
    g=pd.read_csv(out/'inputs/reference_admitted_heating_points.csv',float_precision='round_trip')
    points={(r.model,r.ambient_c,r.leaving_water_c):(r.q_kw,r.p_kw) for r in g.itertuples()}
    return driver,dispatch,read(case/'summary.json'),read(case/'solver.json'),read(case/'dual_certificate.json'),p,points


def test_5e_minus9_terminal_dual_tolerance_is_not_strict_lower_bound():
    driver,dispatch,summary,solver,dual,p,points=real_case()
    altered=deepcopy(dual);altered['lower_bounds'][-1]+=5e-9
    report=deepcopy(solver);report['dual_objective_cny']+=20*5e-9
    report['absolute_primal_dual_gap_cny']=abs(report['primal_objective_cny']-report['dual_objective_cny'])
    assert numerical.audit_case(driver,dispatch,summary,report,altered,p,points)['passed']
    assert report['dual_objective_cny']>report['primal_objective_cny']
    original_bound=exact.certify_case(driver,dispatch,dual,p)
    new_bound=exact.certify_case(driver,dispatch,altered,p)
    assert new_bound==original_bound # lower/upper marginals are not used in weak-dual construction
    assert new_bound['strict_encoded_binary64_lp_lower_bound_cny']<report['dual_objective_cny']


def test_all_six_native_bounds_are_downward_serialized_and_original_estimates_retained():
    result=exact.certify_run(HERE/'optimization_01')
    assert result['passed'] and result['cases_checked']==6 and result['original_dual_estimates_retained']
    for case in result['cases'].values():
        bound=Fraction(int(case['exact_rational_lower_bound_numerator']),int(case['exact_rational_lower_bound_denominator']))
        assert Fraction.from_float(case['strict_encoded_binary64_lp_lower_bound_cny'])<=bound
        assert abs(case['numerical_incumbent_minus_strict_bound_cny'])<1e-6


def test_positive_grid_dual_is_projected_to_a_valid_nonpositive_dual():
    driver,dispatch,summary,solver,dual,p,points=real_case()
    altered=deepcopy(dual);altered['grid_inequality'][0]=1.
    projected=deepcopy(altered);projected['grid_inequality'][0]=0.
    assert exact.certify_case(driver,dispatch,altered,p)==exact.certify_case(driver,dispatch,projected,p)
