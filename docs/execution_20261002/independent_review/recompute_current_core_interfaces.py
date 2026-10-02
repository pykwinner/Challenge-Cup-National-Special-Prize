"""Tiny synthetic interface construction, no solve or engineering experiment."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys
sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import numpy as np
import pandas as pd
from heatplan.representative import build_multiyear_representative_days, build_multiyear_stochastic_representative_bundle
from heatplan.building_thermal import validate_building_thermal_inputs
from heatplan.model import HeatPlanningModel
from heatplan.chronology import _maximum_grouped_ramp, prepare_full_year_timeseries

FILES = ['representative.py', 'scenarios.py', 'chronology.py', 'chronology_cli.py', 'building_thermal.py', 'model.py']
def hashes():
    return {f'src/heatplan/{f}': sha256((ROOT/'src/heatplan'/f).read_bytes()).hexdigest() for f in FILES}
before = hashes()
config = {'planning': {'discount_rate': .05, 'asset_lifetime_years': 20, 'time_step_hours': 1},
          'grades': [{'name': 'low', 'temperature_c': 45}],
          'nodes': [{'name': 'n', 'demand_columns': {'low': 'heat'}}],
          'sources': [], 'pipes': [], 'storages': [],
          'building_thermal': {'enabled': True, 'buildings': [{'name': 'b', 'node': 'n', 'heat_grade': 'low',
              'area_m2': 100, 'heat_loss_kw_per_c': 1, 'thermal_capacity_kwh_per_c': 3,
              'initial_temperature_c': 20, 'min_temperature_c': 18, 'max_temperature_c': 24, 'max_cooling_kw': 0}]}}
def frame(year):
    ts = pd.date_range(f'{year}-01-01', periods=48, freq='h', tz='UTC')
    return pd.DataFrame({'time': ts, 'timestamp': ts, 'heat': np.repeat([1.,2.],24),
                         'outdoor_temp': np.repeat([0.,-5.],24), 'electricity_price': .5, 'grid_emission_factor': 1.})
def inspect_rc(output):
    rc = validate_building_thermal_inputs(config, output)
    model = HeatPlanningModel(config, output)
    model.build()
    sizes = [len(v) for v in rc.boundary_groups.values()]
    within_steps = [pd.to_datetime(output.loc[list(v),'time'],utc=True).diff().dropna().dt.total_seconds().tolist()
                    for v in rc.boundary_groups.values()]
    assert all(s == 24 for s in sizes), f'Unexpected RC group lengths {sizes}'
    assert all(step == 3600 for steps in within_steps for step in steps), f'Unexpected internal steps {within_steps}'
    assert model._built, 'Model did not complete build'
    return {'accepted': True, 'rows': len(output), 'groups': sizes,
            'within_group_all_1h': True, 'model_build_completed': True,
            'variables': len(model.problem.names), 'constraint_rows': len(model.problem.rows),
            'scenario_probabilities': model.scenario_probabilities,
            'unique_representative_time_id': bool(output.representative_time_id.is_unique),
            'solve_called': False}
def capture(fn):
    try: return fn()
    except Exception as exc: return {'accepted': False, 'exception_type': type(exc).__name__, 'exception': str(exc)}
single = build_multiyear_representative_days(config, {'2020': frame(2020)}, n_clusters=1)
multi = build_multiyear_representative_days(config, {'2020': frame(2020), '2022': frame(2022)}, n_clusters=1)
bundle = build_multiyear_stochastic_representative_bundle(config, {'2020': frame(2020), '2022': frame(2022)},
    scenario_probabilities={'2020': .3, '2022': .7}, n_clusters_per_year=1)
cross = pd.concat([frame(2020).iloc[:24], frame(2022).iloc[:24]],ignore_index=True)
cross['heat'] = np.repeat([0.,100.],24)
cross['source_weather_year'] = np.repeat(['2020','2022'],24)
gap = pd.concat([frame(2020).iloc[:24], frame(2020).iloc[24:].assign(
    time=lambda x:x.time+pd.Timedelta(days=1),timestamp=lambda x:x.timestamp+pd.Timedelta(days=1))],ignore_index=True)
gap['heat'] = np.repeat([0.,100.],24)
continuous = frame(2020);continuous['heat'] = np.repeat([0.,100.],24)
twohour = frame(2020).iloc[:24].copy()
twohour['timestamp'] = pd.date_range('2020-01-01',periods=24,freq='2h',tz='UTC')
def prepare(step):
    out = prepare_full_year_timeseries(twohour,expected_step_hours=step)
    return {'accepted': True, 'rows': len(out), 'expected_step_hours': step}
legacy_config = deepcopy(config);legacy_config.pop('building_thermal')
integer = frame(2020).drop(columns='timestamp');integer['time'] = np.arange(len(integer))
legacy = build_multiyear_representative_days(legacy_config, {'diagnostic':integer}, n_clusters=1)
report = {'identity': 'Synthetic 24/48h interface fixtures; not measured observations or engineering experiments',
          'core_edits': False, 'engineering_solve': False, 'tiny_fixture_model_builds': True,
          'source_sha256_before': before,
          'rc_single_year': capture(lambda: inspect_rc(single.calibration.timeseries)),
          'rc_nonadjacent_two_years': capture(lambda: inspect_rc(multi.calibration.timeseries)),
          'rc_stochastic_year_bundle': capture(lambda: inspect_rc(bundle.scenario_bundle)),
          'rc_energy_target_scope': multi.calibration.summary.get('energy_target_scope'),
          'legacy_integer_time_mode': legacy.candidate_timeseries.attrs.get('ramp_adjacency_mode'),
          'observed_ramps': {'cross_year': _maximum_grouped_ramp(cross,'heat'),
                            'missing_day': _maximum_grouped_ramp(gap,'heat'),
                            'actual_midnight': _maximum_grouped_ramp(continuous,'heat')},
          'prepare_2h_with_dt1': capture(lambda: prepare(1)), 'prepare_2h_with_dt2': capture(lambda: prepare(2))}
report['source_sha256_after'] = hashes()
report['source_unchanged_during_recompute'] = before == report['source_sha256_after']
support = HERE/'support/core_interfaces';support.mkdir(exist_ok=True)
version = sha256(json.dumps(before,sort_keys=True).encode()).hexdigest()
text = json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
(support/f'{version}.json').write_text(text,encoding='utf-8')
(support/'latest.json').write_text(text,encoding='utf-8')
assert report['source_unchanged_during_recompute'], 'Source changed during fixture; do not use mixed-version result'
print(text)
