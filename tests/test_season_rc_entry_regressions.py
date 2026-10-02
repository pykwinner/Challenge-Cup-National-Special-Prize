"""Program fixtures are mathematical tests, not measured building datasets."""
from copy import deepcopy

import pytest

from heatplan.heating_season import _season_config, prepare_heating_season_timeseries
from test_audit_and_heating_season import config, frame, scope


def test_rc_service_ignores_inactive_legacy_behavior_but_dhw_still_requires_mapping():
    c, f = config(), frame()
    c["nodes"][0].update(user_behavior={"scenario_multipliers": {"stress": 1.5}},
                         demand_columns={"low": "heat", "high": "dhw"})
    c["building_thermal"] = {"enabled": True, "buildings": [{"name": "room", "node": "n", "heat_grade": "low"}]}
    f["dhw"] = 0.
    f["outdoor_temp"] = 0.
    original = deepcopy(c)
    selected, audit = prepare_heating_season_timeseries(c, f, scope())
    assert len(selected) == 4 and audit["exact_scope_match"]
    assert c == original
    f.loc[0, "dhw"] = 1.
    with pytest.raises(ValueError, match="operating_scenario"):
        prepare_heating_season_timeseries(c, f, scope())


def test_null_thermal_inertia_is_not_an_active_cyclic_state():
    c = config()
    c["nodes"][0]["thermal_inertia"] = {"low": None}
    _season_config(c, allow_cyclic_state_assumptions=False)
    c["nodes"][0]["thermal_inertia"]["low"] = {"thermal_capacity_kwh_per_c": 1.}
    with pytest.raises(ValueError, match="building_thermal_state"):
        _season_config(c, allow_cyclic_state_assumptions=False)
