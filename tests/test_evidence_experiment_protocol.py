from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path

import pandas as pd
import pytest


SPEC = spec_from_file_location("evidence_runner", Path(__file__).resolve().parents[1] / "scripts/run_evidence_case_experiments.py")
runner = module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def test_admission_is_bound_to_actual_configuration_and_hourly_data(tmp_path):
    config, data, report = [tmp_path / name for name in ("case.yaml", "hours.csv", "admission.json")]
    config.write_text("test: true", encoding="utf-8")
    data.write_text("time,value\n", encoding="utf-8")
    admitted = {"physical_reference_admitted": True, "rc_reference_admitted": False,
                "input_sha256": {str(p): runner.sha(p) for p in (config, data)}}
    report.write_text(json.dumps(admitted), encoding="utf-8")
    assert runner.verify_admission(report, config, data, rc=False)
    with pytest.raises(ValueError, match="not passed"):
        runner.verify_admission(report, config, data, rc=True)
    data.write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="identity changed"):
        runner.verify_admission(report, config, data, rc=False)


@pytest.mark.parametrize("failure", ["gapped", "weighted", "naive", "wrong_step", "extra_scenario"])
def test_complete_season_cannot_be_inferred_from_row_count(failure):
    c = {"planning": {"time_step_hours": 1}}
    f = pd.DataFrame({"time": pd.date_range("2024-01-01T00:00:00+08:00", periods=3, freq="h")})
    start, end = "2024-01-01T00:00:00+08:00", "2024-01-01T03:00:00+08:00"
    assert runner.chronology_check(f, c, start, end)["passed"]
    if failure == "gapped": f.loc[1, "time"] += pd.Timedelta(hours=10)
    if failure == "weighted": f["period_weight"] = 2
    if failure == "naive": f["time"] = f["time"].dt.tz_localize(None)
    if failure == "wrong_step": c["planning"]["time_step_hours"] = .5
    if failure == "extra_scenario": f["scenario"] = ["a", "b", "a"]
    with pytest.raises(ValueError): runner.chronology_check(f, c, start, end)
