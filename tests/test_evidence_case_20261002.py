"""Evidence gate tests: reject silent reinterpretation of original sources."""
from copy import deepcopy
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("evidence_case", ROOT / "scripts/prepare_evidence_case_20261002.py")
case = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(case)


@pytest.fixture(scope="module")
def frame():
    return case.build_frame()


def test_exact_admission_and_non_empirical_identity():
    report = case.verify_admission()
    assert report["physical_reference_admitted"] and report["rc_reference_admitted"]
    assert not report["full_economics_admitted"]
    assert not report["external_building_validation_passed"]
    assert report["active_field_coverage"]["missing"] == []


def test_raw_weather_clock_and_no_synthetic_weather(frame):
    assert len(frame) == 2880
    assert frame.time.iloc[0] == "2024-11-16T00:00:00+08:00"
    assert frame.time.iloc[-1] == "2025-03-15T23:00:00+08:00"
    assert frame.outdoor_temp.min() == -4.85
    assert frame.outdoor_temp.max() == 26.55
    assert frame.outdoor_temp.iloc[0] == 19.07
    assert frame.solar_irradiation_wh_m2.max() > 0
    assert frame.solar_gain_kw.eq(0).all()  # explicitly excluded pathway


@pytest.mark.parametrize("corruption", ["gap", "duplicate", "nan", "weight", "naive", "tariff"])
def test_reject_changed_physical_clock_or_scope(frame, corruption):
    damaged = frame.copy()
    if corruption == "gap":
        damaged = damaged.drop(index=25)
    elif corruption == "duplicate":
        damaged.loc[25, "time"] = damaged.loc[24, "time"]
    elif corruption == "nan":
        damaged.loc[25, "outdoor_temp"] = np.nan
    elif corruption == "weight":
        damaged.loc[25, "period_weight"] = 2.
    elif corruption == "naive":
        damaged["time"] = pd.to_datetime(damaged.time, utc=True).dt.tz_localize(None).astype(str)
    else:
        damaged.loc[25, "electricity_price"] = .7
    with pytest.raises(ValueError):
        case.validate_frame(damaged)


def test_source_lock_rejects_replaced_pdf(tmp_path):
    evidence = tmp_path / case.EVIDENCE
    evidence.mkdir(parents=True)
    (evidence / "bestest_6231.pdf").write_bytes(b"%PDF fabricated replacement")
    with pytest.raises(ValueError, match="Locked original"):
        case.verify_evidence(tmp_path)


@pytest.mark.parametrize("corruption", ["investment", "inheritance", "C", "heat_mapping"])
def test_new_unreviewed_branches_fail_gate(frame, corruption):
    config = deepcopy(case.build_config(True))
    if corruption == "investment":
        config["sources"][0]["max_new_capacity_kw"] = 10.
    elif corruption == "inheritance":
        config["extends"] = "../../data/example_city.yaml"
    elif corruption == "C":
        config["building_thermal"]["buildings"][0]["thermal_capacity_kwh_per_c"] = 753.
    else:
        config["nodes"][0]["demand_columns"]["ideal_zone_service"] = "unreviewed_load"
    with pytest.raises(ValueError, match="Config differs"):
        case.validate_case(config, frame, rc=True)


def test_provenance_has_unique_field_rows_and_known_sources():
    fields = pd.read_csv(ROOT / case.FIELD_PATH)
    assert not fields[["artifact", "field"]].duplicated().any()
    known = set(case.source_registry())
    for sources in fields.source_ids:
        assert set(sources.split(";")) <= known
    for mode in ("steady", "rc"):
        expected = {name for name, _ in case.leaves(case.build_config(mode == "rc"))}
        actual = set(fields.loc[fields.artifact.eq(f"wuhan_bestest600_{mode}.yaml"), "field"])
        assert actual == expected


def test_no_gain_screen_and_actual_steady_service_are_distinct(frame):
    assert frame.heat_reference_no_gain_kw.sum() > frame.steady_heat_demand_kw.sum()
    assert frame.steady_cooling_demand_kw.max() > 0  # ideal sensible cooling branch is required
    # 609033.6 + 936000 + 383040 J/K from the three prescribed interior layers.
    assert case.C == pytest.approx(.535576, abs=1e-12)
    assert case.H == pytest.approx(.1043, abs=1e-12)
    assert frame.steady_temperature_c.between(20, 27).all()


TOU_SPEC = importlib.util.spec_from_file_location("evidence_case_tou", ROOT / "docs/implementation_20261002/data/prepare_tou.py")
tou = importlib.util.module_from_spec(TOU_SPEC)
TOU_SPEC.loader.exec_module(tou)


def test_tou_separate_gate_preserves_base_and_conditional_scope():
    before = case.sha256(ROOT / case.DOC_DIR / "admission.json")
    report = tou.verify_admission()
    assert case.sha256(ROOT / case.DOC_DIR / "admission.json") == before
    assert report["physical_reference_admitted"] and report["rc_reference_admitted"]
    assert report["conditional_operating_tariff_reference_admitted"]
    assert not report["account_eligibility_verified"]
    assert not report["grid_connection_feasibility_verified"]
    assert not report["full_economics_admitted"]
    assert report["checks"]["physical_config_byte_identical_to_base"]
    assert report["hour_counts"] == {"flat": 960, "peak": 720, "sharp": 240, "valley": 960}
    assert report["month_hour_counts"] == {"2024-11": 360, "2024-12": 744, "2025-01": 744, "2025-02": 672, "2025-03": 360}


@pytest.mark.parametrize("stamp,expected", [
    ("2024-11-30T23:00+08:00", ("2024-11", "peak")),
    ("2024-12-01T00:00+08:00", ("2024-12", "valley")),
    ("2025-01-31T10:00Z", ("2025-01", "sharp")),
    ("2025-02-01T11:00Z", ("2025-02", "sharp")),
    ("2025-02-01T12:00+08:00", ("2025-02", "valley")),
    ("2025-03-01T14:00+08:00", ("2025-03", "flat")),
])
def test_tou_timezone_sharp_and_month_boundary(stamp, expected):
    assert tou.hour_bucket(stamp) == expected


@pytest.mark.parametrize("stamp", ["2025-04-01T00:00+08:00", "2025-02-01T00:00", "2025-02-01T06:30+08:00"])
def test_tou_no_missing_month_or_naive_clock_fallback(stamp):
    with pytest.raises(ValueError):
        tou.hour_bucket(stamp)


@pytest.mark.parametrize("damage", ["price", "timezone_shift", "bucket", "weather", "emission_factor", "two_part_fee", "investment"])
def test_tou_rejects_tariff_or_physical_scope_corruption(damage):
    modified = tou.build_frame()
    config = deepcopy(case.build_config(True))
    if damage == "price":
        modified.loc[0, "electricity_price"] *= .45  # prohibit multiplying complete final rates again
    elif damage == "timezone_shift":
        modified.loc[0, "time"] = "2024-11-16T00:00:00+00:00"
    elif damage == "bucket":
        modified.loc[0, "tariff_bucket"] = "flat"
    elif damage == "weather":
        modified.loc[0, "outdoor_temp"] += 1
    elif damage == "emission_factor":
        modified.loc[0, "grid_emission_factor"] = .5
    elif damage == "two_part_fee":
        config["planning"]["grid_basic_fee_yuan_per_kw_month"] = 42.
    else:
        config["sources"][0]["max_new_capacity_kw"] = 10.
    with pytest.raises(ValueError):
        tou.validate_case(config, modified)
