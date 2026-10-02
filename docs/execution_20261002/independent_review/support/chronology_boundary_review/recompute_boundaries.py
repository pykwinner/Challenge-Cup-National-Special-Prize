"""Read-only synthetic audit; deliberately never builds or solves an MILP."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT / "src"))
from heatplan.representative import build_multiyear_representative_days
from heatplan.building_thermal import validate_building_thermal_inputs
from heatplan.chronology import (
    chronology_quality_audit, prepare_full_year_timeseries,
    _maximum_grouped_ramp,
)
from heatplan.scenarios import _representative_hour_adjacency
from heatplan.model import HeatPlanningModel

FILES = ["representative.py", "scenarios.py", "chronology.py", "building_thermal.py", "model.py"]
def hashes():
    return {str(Path("src/heatplan") / f): sha256((ROOT / "src/heatplan" / f).read_bytes()).hexdigest() for f in FILES}

before = hashes()
config = {
    "planning": {"discount_rate": .05, "asset_lifetime_years": 20,
                 "time_step_hours": 1, "representative_day_weight": 1},
    "grades": [{"name": "low", "temperature_c": 45}],
    "nodes": [{"name": "n", "demand_columns": {"low": "heat"}}],
    "sources": [], "pipes": [], "storages": [],
}
def frame(times):
    return pd.DataFrame({"time": times.astype(str), "timestamp": times.astype(str),
                         "outdoor_temp": 0., "heat": 1., "electricity_price": .5,
                         "grid_emission_factor": 1.})

def attempt(f, c=config):
    try:
        a = build_multiyear_representative_days(c, {"2023": f}, n_clusters=1)
        return {"accepted": True, "rows": len(f), "weight_days": float(
            a.calibration.timeseries.groupby("representative_day").period_weight.first().sum()),
            "candidate_first_time": str(a.candidate_timeseries.time.iloc[0])}
    except Exception as error:
        return {"accepted": False, "error": str(error)}

base = frame(pd.date_range("2023-01-01", periods=48, freq="h", tz="UTC"))
gap = frame(pd.date_range("2023-01-01", periods=24, freq="h", tz="UTC").append(
            pd.date_range("2023-01-03", periods=24, freq="h", tz="UTC")))
shift = frame(pd.date_range("2023-01-01 06:00", periods=48, freq="h", tz="UTC"))
rc = deepcopy(config)
rc["building_thermal"] = {"enabled": True, "buildings": [{"name": "b", "node": "n",
    "heat_grade": "low", "area_m2": 100, "heat_loss_kw_per_c": 1,
    "thermal_capacity_kwh_per_c": 3, "initial_temperature_c": 20,
    "min_temperature_c": 18, "max_temperature_c": 24, "max_cooling_kw": 0}]}

# Two complete observed days, separated by two years. No observed edge joins them.
cross = frame(pd.date_range("2020-01-01", periods=24, freq="h", tz="UTC").append(
              pd.date_range("2022-01-01", periods=24, freq="h", tz="UTC")))
cross["heat"] = np.repeat([0., 100.], 24)
cross["source_weather_year"] = np.repeat(["2020", "2022"], 24)
new_mask, mode = _representative_hour_adjacency(cross)
observed_ramp = float(np.abs(np.diff(cross.heat.to_numpy()))[new_mask].max())

rep = frame(pd.date_range("2023-01-01", periods=24, freq="h", tz="UTC"))
rep["heat"] = range(24)
rep["boundary_group"] = "d0"
model = HeatPlanningModel(config, rep)  # Constructor only; no model.build/solve.
two_day = base.copy()
two_day["boundary_group"] = np.repeat(["day0", "day1"], 24)
two_day_model = HeatPlanningModel(config, two_day)

numeric = base.copy()
numeric["time"] = np.arange(48)
numeric_mask, numeric_mode = _representative_hour_adjacency(numeric)
uniform2 = frame(pd.date_range("2023-01-01", periods=24, freq="2h", tz="UTC"))

report = {
    "review_date_local": "2026-10-02",
    "identity": "Synthetic analytical fixtures, not measured data or an engineering experiment",
    "no_core_edits_no_milp_build_or_solve": True,
    "source_sha256_before": before,
    "partial_24h_claimed_annual": attempt(base.iloc[:24]),
    "missing_day_with_24h_blocks": attempt(gap),
    "shifted_06h_blocks": attempt(shift),
    "original_rc_input_valid": validate_building_thermal_inputs(rc, base) is not None,
    "rc_builder_interface": attempt(base, rc),
    "cross_year_ramp": {"coverage_helper_reported": _maximum_grouped_ramp(cross, "heat"),
                        "selection_adjacency_mode": mode,
                        "selection_valid_edges": int(new_mask.sum()),
                        "observed_contiguous_edge_max": observed_ramp},
    "representative_cyclic_wrap": {
        "coverage_reported_ramp": _maximum_grouped_ramp(rep, "heat"),
        "model_first_predecessor": model.time_predecessor[0],
        "model_wrap_ramp": abs(float(rep.heat.iloc[0] - rep.heat.iloc[model.time_predecessor[0]])),
        "note": "Artificial daily cycle edge; do not label it an observed chronological ramp"},
    "daily_state_partition": {
        "day0_first_predecessor": two_day_model.time_predecessor[0],
        "day1_first_predecessor": two_day_model.time_predecessor[24],
        "groups": two_day_model.boundary_groups,
        "lossless_cyclic_storage_derivation": "Summing each day's SOC equations gives eta_ch*sum(charge)*dt = sum(discharge)*dt/eta_dis separately for each day. Day weights do not link days."},
    "integer_time_with_real_timestamp": {
        "selection_mode": numeric_mode, "reported_valid_edges": int(numeric_mask.sum()),
        "midnight_edge_23_to_24_included": bool(numeric_mask[23]),
        "independent_timestamp_expected_valid_edges": 47},
    "uniform_2h_helper_accepted": len(prepare_full_year_timeseries(uniform2)) == len(uniform2),
    "uniform_2h_quality_valid_for_dt1": chronology_quality_audit(uniform2, expected_step_hours=1)["chronology_valid"],
    "missing_day_quality_valid": chronology_quality_audit(gap, expected_step_hours=1)["chronology_valid"],
}
report["source_sha256_after"] = hashes()
report["source_unchanged_during_recompute"] = report["source_sha256_after"] == before
output = Path(__file__).parent / "fixture_results.json"
if output.exists():
    previous = json.loads(output.read_text(encoding="utf-8"))
    previous_id = sha256(json.dumps(previous["source_sha256_before"], sort_keys=True).encode()).hexdigest()[:12]
    (output.parent / f"fixture_results_{previous_id}.json").write_text(
        json.dumps(previous, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
