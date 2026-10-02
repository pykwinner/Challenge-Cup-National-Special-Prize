from copy import deepcopy

import pandas as pd
import pytest

from heatplan.chronology import explicit_annual_scope_audit
from heatplan.heating_season import _season_config
from heatplan.model import HeatPlanningModel


def test_service_year_requires_exact_complete_aware_time_axis():
    scope = {"start_inclusive": "2023-07-01T00:00:00+08:00", "end_exclusive": "2024-07-01T00:00:00+08:00", "timezone": "Asia/Shanghai", "expected_hours": 8784}
    c = {"planning": {"annual_scope": scope, "time_step_hours": 1}}
    f = pd.DataFrame({"timestamp": pd.date_range(scope["start_inclusive"], periods=8784, freq="h").tz_convert("UTC")})
    assert explicit_annual_scope_audit(c, f)["is_explicit_service_year_aligned"]
    for bad in (f.iloc[:-1], pd.concat([f.iloc[:-1],f.iloc[[-2]]]), f.assign(timestamp=f.timestamp.dt.tz_localize(None))):
        with pytest.raises(ValueError):
            explicit_annual_scope_audit(c, bad)
    wrong = deepcopy(c)
    wrong["planning"]["annual_scope"]["expected_hours"] = 8760
    with pytest.raises(ValueError, match="expected_hours"):
        explicit_annual_scope_audit(wrong, f)


def test_winter_defers_per_source_annual_ground_ratio_but_retains_physical_ledger():
    c = {"planning": {"discount_rate": .05, "asset_lifetime_years": 20, "time_step_hours": 1, "representative_day_weight": 1, "max_unserved_rate": 0},
         "grades": [{"name": "low", "temperature_c": 45}],
         "nodes": [{"name": "n", "demand_columns": {"low": "heat"}}],
         "sources": [{"name": "hp", "node": "n", "type": "heat_pump", "resource_kind": "renewable", "allowed_grades": ["low"], "existing_capacity_kw": 100, "max_new_capacity_kw": 0, "source_temp_c": 10, "cop_min": 2, "cop_max": 2}],
         "pipes": [], "storages": [],
         "multi_energy_services": {"cooling": {"enabled": True, "demand_columns": {"n": "cool"}, "sources": {"hp": {"eer": 2, "ground_thermal_balance": {"minimum_extraction_to_rejection_ratio": .8, "maximum_extraction_to_rejection_ratio": 1.25}}}}}}
    f = pd.DataFrame({"time": [0,1], "heat": [10.,10.], "cool": [0.,0.], "outdoor_temp": 0., "electricity_price": .5, "grid_emission_factor": 1.})
    with pytest.raises(RuntimeError, match="未得到可行解"):
        HeatPlanningModel(c, f).solve()
    winter, audit = _season_config(c, False)
    assert "cooling.sources.hp.ground_thermal_balance" in audit["deferred_annual_constraints"]
    assert "enforce" not in c["multi_energy_services"]["cooling"]["sources"]["hp"]["ground_thermal_balance"]
    a = HeatPlanningModel(winter, f).solve()
    assert a.summary["ground_heat_extraction_kwh_per_year"] == pytest.approx(10)
    assert a.summary["ground_heat_rejection_kwh_per_year"] == pytest.approx(0)
