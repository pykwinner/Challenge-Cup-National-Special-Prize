"""Explicit analytic fixtures test audit rejection; they are not observations."""
import importlib.util
from pathlib import Path

import pandas as pd
import pytest

spec = importlib.util.spec_from_file_location("weather_archive", Path(__file__).parents[1]/"scripts/verify_original_weather_archive.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def fixture_data():
    time = pd.date_range("2023-01-01", "2024-01-01", inclusive="left", freq="h")
    keys = time.strftime("%Y%m%d%H")
    frame = pd.DataFrame({"time": time})
    payload = {"header": {"time_standard": "LST", "fill_value": -999},
               "parameters": {}, "properties": {"parameter": {}}}
    for i, (key, (column, unit)) in enumerate(audit.FIELDS.items()):
        frame[column] = float(i)
        payload["parameters"][key] = {"units": unit}
        payload["properties"]["parameter"][key] = dict.fromkeys(keys, float(i))
    return frame, payload


def test_one_bad_hour_remains_a_reported_mismatch():
    frame, payload = fixture_data()
    frame.loc[100, "outdoor_temp"] = .5
    result = audit.compare(frame, payload, 2023)
    assert result["all_values_match"] is False
    assert result["fields"]["T2M"]["mismatched_rows"] == 1
    assert result["fields"]["T2M"]["maximum_absolute_difference"] == .5
    assert len(frame) == 8760


@pytest.mark.parametrize("problem", ["wrong_clock", "wrong_unit", "missing_marker", "shifted_archive"])
def test_invalid_semantics_rejected_before_source_approval(problem):
    frame, payload = fixture_data()
    if problem == "wrong_clock":
        payload["header"]["time_standard"] = "UTC"
    elif problem == "wrong_unit":
        payload["parameters"]["ALLSKY_SFC_SW_DWN"]["units"] = "kWh/m^2"
    elif problem == "missing_marker":
        payload["properties"]["parameter"]["T2M"]["2023010100"] = -999
    else:
        frame["time"] += pd.Timedelta(hours=1)
    with pytest.raises(ValueError):
        audit.compare(frame, payload, 2023)
