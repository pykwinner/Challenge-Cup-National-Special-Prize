"""Small analytic program checks only; none of these fixtures are observations."""
from pathlib import Path
import hashlib
import json
import sys
import types
import warnings

sys.dont_write_bytecode = True
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SUPPORT = HERE / 'support' / 'selection_and_weights'
SUPPORT.mkdir(parents=True, exist_ok=True)
source_path = ROOT / 'src/heatplan/scenarios.py'
source = source_path.read_text(encoding='utf-8')
source_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
if (SUPPORT / 'recomputation.json').exists():
    previous = json.loads((SUPPORT / 'recomputation.json').read_text(encoding='utf-8'))
    previous_dir = SUPPORT / previous['reviewed_source_sha256']
    previous_dir.mkdir(exist_ok=True)
    for name in ('recomputation.json', 'scenarios_reviewed_snapshot.py'):
        if (SUPPORT / name).exists():
            (previous_dir / name).write_bytes((SUPPORT / name).read_bytes())
(SUPPORT / 'scenarios_reviewed_snapshot.py').write_text(source, encoding='utf-8')
module = types.ModuleType('independent_review_scenarios')
module.__file__ = str(source_path)
sys.modules[module.__name__] = module
exec(compile(source, str(source_path), 'exec'), module.__dict__)

def days(values):
    return pd.DataFrame({'time': pd.date_range('2026-01-01', periods=24*len(values), freq='h').astype(str),
                         'demand': np.repeat(np.asarray(values,dtype=float),24)})

# Constant within every day; all demand changes occur at actual midnight.
midnight = days([0,40,100,20])
selected = module.extreme_aware_typical_days(midnight,['demand'],['demand'],n_clusters=1)
true_ramps = np.abs(np.diff(midnight['demand'].to_numpy()))
daily_ramps = np.abs(np.diff(midnight['demand'].to_numpy().reshape(-1,24),axis=1)).max(axis=1)
midnight_record = dict(real_hourly_maximum_ramp=float(true_ramps.max()),
                        daily_only_maximum_ramp=float(daily_ramps.max()),
                        actual_largest_transition_rows=[int(true_ramps.argmax()),int(true_ramps.argmax()+1)],
                        selected_original_days=selected.groupby('representative_day',sort=False)['original_day_index'].first().tolist(),
                        selected_reasons=selected.groupby('representative_day',sort=False)['extreme_reasons'].first().tolist(),
                        maximum_ramp_reason_retained=bool(selected['extreme_reasons'].str.contains('maximum_ramp').any()))

# Fixed-cluster squared distance exemplar differs from an unsquared-L2 medoid.
sample_days=days([0,0,0,4,10])
exemplars,counts,_=module.cluster_typical_days(sample_days,['demand'],n_clusters=1)
x=np.array([0.,0.,0.,4.,10.])
dist=np.abs(x[:,None]-x[None,:])
nearest_record=dict(selected_day_index=exemplars[0],selected_value=float(x[exemplars[0]]),
                    fixed_cluster_sum_squared_minimizer_indices=np.flatnonzero(np.isclose((dist**2).sum(axis=1),(dist**2).sum(axis=1).min())).tolist(),
                    unsquared_l2_medoid_indices=np.flatnonzero(np.isclose(dist.sum(axis=1),dist.sum(axis=1).min())).tolist(),
                    interpretation='KMeans cluster + nearest centroid actual day; squared-distance medoid of fixed cluster, not an independently optimized k-medoids partition.')

# A missing hour and an extra hour still leave a multiple of 24 rows.
bad_time=days([10,20])
timestamps=pd.date_range('2026-01-01',periods=49,freq='h').delete(12)
bad_time['time']=timestamps.astype(str)
try:
    module.extreme_aware_typical_days(bad_time,['demand'],['demand'],n_clusters=1)
    gap_record=dict(accepted=True)
except Exception as exc:
    gap_record=dict(accepted=False,error_type=type(exc).__name__,error=str(exc))
gap_record.update(rows=len(bad_time),maximum_hour_gap=float(np.diff(timestamps.asi8).max()/3.6e12),
                   first_24_rows_last_hour=str(timestamps[23]))

# Preserve old integer-label APIs while preferring a genuine timestamp column.
integer_time=days([0,100])
integer_time['timestamp']=pd.date_range('2026-01-01T00:00:00Z',periods=48,freq='h')
integer_time['time']=np.arange(48)
integer_result=module.extreme_aware_typical_days(integer_time,['demand'],['demand'],n_clusters=1)
timestamp_priority_record=dict(adjacency_mode=integer_result.attrs.get('ramp_adjacency_mode'),
                                genuine_timestamp_column_present=True,
                                maximum_ramp_reason_retained=bool(integer_result['extreme_reasons'].str.contains('maximum_ramp').any()))
integer_source=integer_time.drop(columns='timestamp').copy()
integer_source['source_time']=integer_source['time']
try:
    module.extreme_aware_typical_days(integer_source,['demand'],['demand'],n_clusters=1)
    integer_source_record=dict(accepted=True)
except Exception as exc:
    integer_source_record=dict(accepted=False,error_type=type(exc).__name__,error=str(exc))

# Entire constant-day feature set has fewer distinct profiles than n_clusters.
constant_result={}
with warnings.catch_warnings(record=True) as logged:
    warnings.simplefilter('always')
    try:
        module.cluster_typical_days(days([1,1,1,1]),['demand'],n_clusters=2)
        constant_result['accepted']=True
    except Exception as exc:
        constant_result.update(accepted=False,error_type=type(exc).__name__,error=str(exc))
    constant_result['warnings']=[str(w.message) for w in logged]

def representative():
    return pd.DataFrame({'representative_day':np.repeat(['A','B'],24),'period_weight':1.,
                          'is_extreme_day':False,'a':np.r_[np.full(24,10.),np.zeros(24)],
                          'b':np.r_[np.zeros(24),np.full(24,10.)]})

# Every retained day has a+b=240, so sum(w)=2 fixes total at480.
# The chronology target is a+b=240: no weights can exactly match both columns.
rep=representative()
rep_before=rep.copy(deep=True)
infeasible_target=pd.DataFrame({'a':np.full(48,2.5),'b':np.full(48,2.5)})
calibration=module.calibrate_representative_day_weights(rep,infeasible_target,['a','b'])
design=np.array([[240.,0.],[0.,240.]])
target=np.array([120.,120.])
constraint=np.vstack([design,np.ones(2)])
rhs=np.r_[target,2.]
infeasible_record=dict(analytic_match_feasible=False,
                        proof='Both target energies require w_A=w_B=0.5, contradicting hard sum(w)=2; retained a+b total is always480 versus target240.',
                        rank_design_and_sum=int(np.linalg.matrix_rank(constraint)),
                        rank_augmented=int(np.linalg.matrix_rank(np.column_stack([constraint,rhs]))),
                        returned_summary=calibration.summary,
                        weights=calibration.weights.to_dict(orient='records'),
                        diagnostics=calibration.diagnostics.to_dict(orient='records'),
                        original_frame_unchanged=bool(rep.equals(rep_before)))

feasible_target=pd.DataFrame({'a':np.full(48,1.5),'b':np.full(48,8.5)})
soft=module.calibrate_representative_day_weights(representative(),feasible_target,['a','b'])
soft_record=dict(exact_solution=[.3,1.7],returned_weights=soft.weights['calibrated_weight'].tolist(),
                  diagnostics=soft.diagnostics.to_dict(orient='records'),
                  explanation='Positive regularization makes exact energy matching a soft preference even when an exact feasible solution exists.')

result=dict(fixture_identity='synthetic analytic program checks, never empirical benchmark data',
              source_file='src/heatplan/scenarios.py',reviewed_source_sha256=source_sha,
              source_unchanged_at_end=hashlib.sha256(source_path.read_bytes()).hexdigest()==source_sha,
              no_engineering_solver=True,no_model_parameter_fit=True,
              midnight_ramp=midnight_record,nearest_centroid=nearest_record,
              missing_hour=gap_record,duplicate_profiles=constant_result,
              genuine_timestamp_priority=timestamp_priority_record,
              legacy_integer_source_time=integer_source_record,
              infeasible_energy_match=infeasible_record,feasible_soft_energy_match=soft_record)
(SUPPORT/'recomputation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
version_dir = SUPPORT / source_sha
version_dir.mkdir(exist_ok=True)
for name in ('recomputation.json', 'scenarios_reviewed_snapshot.py'):
    (version_dir / name).write_bytes((SUPPORT / name).read_bytes())
print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
