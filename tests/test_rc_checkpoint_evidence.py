"""RC checkpoint evidence regressions with real <=5s four-hour solves."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import shutil
from time import perf_counter

import numpy as np
import pandas as pd
import pytest

from heatplan import chronology
from heatplan.building_audit import building_audit_identity
from heatplan.model import HeatPlanningModel


def strict_json(path):
    def reject(token):
        raise AssertionError(f"nonstandard JSON token: {token}")
    return json.loads(path.read_text(encoding="utf-8"), parse_constant=reject)


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")


@pytest.fixture(scope="session")
def bundles(tmp_path_factory):
    config = {
        "planning": {"discount_rate": .05, "asset_lifetime_years": 20, "time_step_hours": 1,
                     "representative_day_weight": 1, "max_unserved_rate": 0, "mip_gap": 0,
                     "design_outdoor_temperature_c": -5},
        "grades": [{"name": "low", "temperature_c": 45}],
        "nodes": [{"name": "n", "demand_columns": {"low": "old_heat"}}],
        "sources": [{"name": "hp", "node": "n", "type": "heat_pump", "resource_kind": "renewable",
                     "allowed_grades": ["low"], "existing_capacity_kw": 100, "max_new_capacity_kw": 0,
                     "cop_min": 2, "cop_max": 2, "source_temp_c": 10, "carnot_efficiency": .4}],
        "pipes": [], "storages": [],
        "building_thermal": {"enabled": True, "buildings": [{
            "name": "b", "node": "n", "heat_grade": "low", "area_m2": 100,
            "heat_loss_kw_per_c": 1, "thermal_capacity_kwh_per_c": 3,
            "initial_temperature_c": 20, "final_temperature_c": 20,
            "min_temperature_c": 20, "max_temperature_c": 24, "max_cooling_kw": 100}]},
        "multi_energy_services": {"cooling": {"enabled": True, "demand_columns": {"n": "old_cool"},
            "sources": {"hp": {"eer": 2, "eer_min": 2, "eer_max": 2}}, "max_unserved_rate": 0}},
    }
    frame = pd.DataFrame({"time": pd.date_range("2023-01-01", periods=4, freq="h", tz="UTC").astype(str),
                          "old_heat": 999., "old_cool": 777., "outdoor_temp": 0.,
                          "electricity_price": .5, "grid_emission_factor": 1.})
    frame["timestamp"] = frame.time
    config["building_thermal"]["buildings"][0]["max_temperature_c"] = 20.
    config["building_thermal"]["buildings"].append(dict(config["building_thermal"]["buildings"][0],
        name="b2", heat_loss_kw_per_c=2., thermal_capacity_kwh_per_c=7., area_m2=200.))
    config["grades"].append({"name": "high", "temperature_c": 70.})
    config["nodes"][0]["demand_columns"]["high"] = "dhw"
    config["sources"][0]["allowed_grades"].append("high")
    frame["dhw"] = 2.
    frame["outdoor_temp"] = [0., 0., 30., 30.]
    started = perf_counter()
    plan = chronology.prepare_fixed_investment_plan(config, frame, planning_time_limit=5., planning_mip_gap=0.)
    rc_seconds = perf_counter() - started
    rc_root = tmp_path_factory.mktemp("rc_v2")
    chronology.save_fixed_investment_plan_checkpoint(rc_root, plan, source_config=config, representative_timeseries=frame)

    legacy_config, legacy_frame = deepcopy(config), frame.copy()
    legacy_config.pop("building_thermal")
    legacy_config.pop("multi_energy_services")
    legacy_config["grades"] = legacy_config["grades"][:1]
    legacy_config["nodes"][0]["demand_columns"] = {"low": "old_heat"}
    legacy_config["sources"][0]["allowed_grades"] = ["low"]
    legacy_frame["old_heat"] = 20.
    started = perf_counter()
    legacy_plan = chronology.prepare_fixed_investment_plan(legacy_config, legacy_frame, planning_time_limit=5., planning_mip_gap=0.)
    legacy_seconds = perf_counter() - started
    legacy_root = tmp_path_factory.mktemp("legacy_v1")
    chronology.save_fixed_investment_plan_checkpoint(legacy_root, legacy_plan,
        source_config=legacy_config, representative_timeseries=legacy_frame)
    # Simulate an actual historical v1 LP summary, then bind its changed bytes.
    legacy_summary_path = legacy_root / "representative_planning/summary.json"
    legacy_summary = strict_json(legacy_summary_path)
    legacy_summary["mip_gap"] = float("nan")
    legacy_summary_path.write_text(json.dumps(legacy_summary, allow_nan=True), encoding="utf-8")
    rehash(legacy_root, "planning_summary")
    write_json(tmp_path_factory.mktemp("fixture_evidence") / "solver_fixture_evidence.json", {
        "scope": "two real four-hour fixtures, no annual solve",
        "RC_solver_status": plan.planning.summary["solver_status"], "RC_wall_seconds": rc_seconds,
        "legacy_solver_status": legacy_plan.planning.summary["solver_status"], "legacy_wall_seconds": legacy_seconds,
        "per_solve_time_limit_seconds": 5., "RC_buildings": 2,
        "RC_actual_heat_kwh": plan.planning.summary["building_heat_delivered_kwh_per_year"],
        "RC_actual_cooling_kwh": plan.planning.summary["building_cooling_delivered_kwh_per_year"],
        "RC_total_heat_with_DHW_kwh": plan.planning.summary["demand_kwh_per_year"],
        "integer_variables": plan.planning_model_profile["integer_variables"],
    })
    return {"rc": (config, frame, plan, rc_root), "legacy": (legacy_config, legacy_frame, legacy_plan, legacy_root)}


def checkpoint_copy(bundle, tmp_path):
    return Path(shutil.copytree(bundle[3], tmp_path / "checkpoint"))


def load(root, bundle, **kwargs):
    return chronology.load_fixed_investment_plan_checkpoint(root, expected_config=bundle[0],
        expected_representative_timeseries=bundle[1], **kwargs)


def rehash(root, label):
    path = root / "fixed_plan_checkpoint_manifest.json"
    manifest = strict_json(path)
    entry = manifest["files"][label]
    entry["sha256"] = sha256((root / entry["path"]).read_bytes()).hexdigest()
    write_json(path, manifest)


def test_rc_roundtrip_has_full_hashed_evidence_and_fresh_audit(bundles):
    bundle = bundles["rc"]
    restored = load(bundle[3], bundle)
    manifest = strict_json(bundle[3] / "fixed_plan_checkpoint_manifest.json")
    assert manifest["schema"] == chronology._FIXED_PLAN_RC_CHECKPOINT_SCHEMA
    assert set(chronology._FIXED_PLAN_RC_CHECKPOINT_FILES).issubset(manifest["files"])
    for entry in manifest["files"].values():
        assert sha256((bundle[3] / entry["path"]).read_bytes()).hexdigest() == entry["sha256"]
    assert len(restored.planning.building_trajectory) == 8
    assert len(restored.planning.building_comfort) == 2
    assert restored.planning.building_independent_audit["passed"] is True
    normalized = HeatPlanningModel(bundle[0], bundle[1])
    assert restored.planning.building_input_identity == building_audit_identity(normalized.config, normalized.ts)
    assert restored.planning.summary["building_heat_delivered_kwh_per_year"] == pytest.approx(120.)
    assert restored.planning.summary["building_cooling_delivered_kwh_per_year"] == pytest.approx(60.)
    assert restored.planning.summary["demand_kwh_per_year"] == pytest.approx(128.)


@pytest.mark.parametrize("missing", ["config", "frame", "both"])
def test_rc_requires_real_external_config_and_frame(bundles, missing):
    config, frame, _, root = bundles["rc"]
    with pytest.raises(ValueError):
        chronology.load_fixed_investment_plan_checkpoint(root,
            expected_config=None if missing in {"config", "both"} else config,
            expected_representative_timeseries=None if missing in {"frame", "both"} else frame)


def test_summary_boolean_is_replaced_by_fresh_recomputation(bundles, tmp_path):
    root = checkpoint_copy(bundles["rc"], tmp_path)
    path = root / "representative_planning/summary.json"
    summary = strict_json(path)
    summary["independent_building_audit_passed"] = False
    summary["independent_building_audit_metrics"] = {"forged": 999}
    write_json(path, summary)
    rehash(root, "planning_summary")
    restored = load(root, bundles["rc"])
    assert restored.planning.summary["independent_building_audit_passed"] is True
    assert "forged" not in restored.planning.summary["independent_building_audit_metrics"]


@pytest.mark.parametrize("tamper", ["delete_row", "remove_heat", "capacity", "duplicate_row"])
@pytest.mark.parametrize("update_hash", [False, True])
def test_tampered_trajectory_rejected_even_if_manifest_hash_is_updated(bundles, tmp_path, tamper, update_hash):
    root = checkpoint_copy(bundles["rc"], tmp_path)
    path = root / "representative_planning/building_trajectory.csv"
    trajectory = pd.read_csv(path)
    if tamper == "delete_row":
        trajectory = trajectory.iloc[:-1]
    elif tamper == "duplicate_row":
        trajectory.iloc[-1] = trajectory.iloc[0]
    elif tamper == "remove_heat":
        trajectory.heat_kw = trajectory.heat_kwh = 0.
        trajectory.rc_residual_c = 0.
    else:
        trajectory.thermal_capacity_kwh_per_c *= 2
    trajectory.to_csv(path, index=False)
    if update_hash:
        rehash(root, "planning_building_trajectory")
    with pytest.raises(ValueError):
        load(root, bundles["rc"])


def test_missing_trajectory_rejected(bundles, tmp_path):
    root = checkpoint_copy(bundles["rc"], tmp_path)
    (root / "representative_planning/building_trajectory.csv").unlink()
    with pytest.raises(ValueError):
        load(root, bundles["rc"])


@pytest.mark.parametrize("label", ["planning_summary", "planning_dispatch", "fixed_config", "planning_building_trajectory",
                                   "planning_building_comfort", "planning_building_independent_audit", "planning_building_input_identity"])
def test_missing_mandatory_file_hash_fails_closed(bundles, tmp_path, label):
    root = checkpoint_copy(bundles["rc"], tmp_path)
    path = root / "fixed_plan_checkpoint_manifest.json"
    manifest = strict_json(path)
    manifest["files"][label].pop("sha256")
    write_json(path, manifest)
    with pytest.raises(ValueError, match="必需文件哈希"):
        load(root, bundles["rc"])


@pytest.mark.parametrize("key", ["source_config_sha256", "representative_timeseries_sha256"])
def test_missing_mandatory_input_fingerprint_fails_closed(bundles, tmp_path, key):
    root = checkpoint_copy(bundles["rc"], tmp_path)
    path = root / "fixed_plan_checkpoint_manifest.json"
    manifest = strict_json(path)
    manifest.pop(key)
    write_json(path, manifest)
    with pytest.raises(ValueError, match="必需输入指纹"):
        load(root, bundles["rc"])


@pytest.mark.parametrize("label", ["report", "comfort", "identity", "summary_energy"])
def test_rehashed_forged_evidence_is_semantically_rejected(bundles, tmp_path, label):
    root = checkpoint_copy(bundles["rc"], tmp_path)
    if label == "comfort":
        path = root / "representative_planning/building_comfort.csv"
        data = pd.read_csv(path)
        data.loc[0, "heat_kwh"] = 999.
        data.to_csv(path, index=False)
        file_label = "planning_building_comfort"
    else:
        filename, file_label = {"report": ("building_independent_audit.json", "planning_building_independent_audit"),
                                "identity": ("building_input_identity.json", "planning_building_input_identity"),
                                "summary_energy": ("summary.json", "planning_summary")}[label]
        path = root / "representative_planning" / filename
        data = strict_json(path)
        if label == "report":
            data["metrics"]["heat_kwh"] = 999.
        elif label == "identity":
            data["frame_sha256"] = "0" * 64
        else:
            data["building_heat_delivered_kwh_per_year"] = 999.
        write_json(path, data)
    rehash(root, file_label)
    with pytest.raises(ValueError):
        load(root, bundles["rc"])


def test_old_rc_v1_missing_evidence_is_not_certified(bundles, tmp_path):
    root = checkpoint_copy(bundles["rc"], tmp_path)
    path = root / "fixed_plan_checkpoint_manifest.json"
    manifest = strict_json(path)
    manifest["schema"] = chronology._FIXED_PLAN_CHECKPOINT_SCHEMA
    manifest.pop("building_thermal_enabled")
    for label in chronology._FIXED_PLAN_RC_CHECKPOINT_FILES:
        manifest["files"].pop(label)
    write_json(path, manifest)
    with pytest.raises(ValueError, match="新版证据"):
        load(root, bundles["rc"])


def test_original_non_rc_v1_is_readable(bundles):
    bundle = bundles["legacy"]
    restored = load(bundle[3], bundle)
    assert restored.planning.building_trajectory is None
    assert restored.planning.summary["demand_kwh_per_year"] == pytest.approx(80.)


@pytest.mark.parametrize("manifest", [{}, {"schema": "heatplan.fixed_investment_checkpoint.v1", "files": {}}])
def test_empty_or_partial_manifest_never_bypasses_legacy_hashes(bundles, tmp_path, manifest):
    root = checkpoint_copy(bundles["legacy"], tmp_path)
    write_json(root / "fixed_plan_checkpoint_manifest.json", manifest)
    with pytest.raises(ValueError):
        load(root, bundles["legacy"])


def test_missing_legacy_mandatory_hash_is_rejected(bundles, tmp_path):
    root = checkpoint_copy(bundles["legacy"], tmp_path)
    path = root / "fixed_plan_checkpoint_manifest.json"
    manifest = strict_json(path)
    manifest["files"]["planning_summary"].pop("sha256")
    write_json(path, manifest)
    with pytest.raises(ValueError, match="必需文件哈希"):
        load(root, bundles["legacy"])


def test_lp_gap_nan_writes_null_and_no_other_nonfinite_is_hidden(bundles, tmp_path):
    bundle = bundles["rc"]
    summary_path = bundle[3] / "representative_planning/summary.json"
    assert strict_json(summary_path)["mip_gap"] is None
    assert "NaN" not in summary_path.read_text(encoding="utf-8")
    damaged = deepcopy(bundle[2])
    damaged.planning.summary["building_heat_delivered_kwh_per_year"] = float("nan")
    with pytest.raises(ValueError, match="非有限数"):
        chronology.save_fixed_investment_plan_checkpoint(tmp_path / "bad_summary", damaged,
            source_config=bundle[0], representative_timeseries=bundle[1])
    config = deepcopy(bundle[0])
    config["building_thermal"]["buildings"][0]["heat_loss_kw_per_c"] = float("inf")
    with pytest.raises(ValueError, match="非有限数"):
        chronology.save_fixed_investment_plan_checkpoint(tmp_path / "bad_config", bundle[2],
            source_config=config, representative_timeseries=bundle[1])
    frame = bundle[1].copy()
    frame.loc[0, "outdoor_temp"] = float("nan")
    with pytest.raises(ValueError, match="非有限数"):
        chronology.save_fixed_investment_plan_checkpoint(tmp_path / "bad_frame", bundle[2],
            source_config=bundle[0], representative_timeseries=frame)


def test_nonfinite_milp_gap_is_not_silently_null(bundles, tmp_path):
    bundle = bundles["rc"]
    damaged = deepcopy(bundle[2])
    damaged.planning_model_profile["integer_variables"] = 1
    with pytest.raises(ValueError, match="非有限数"):
        chronology.save_fixed_investment_plan_checkpoint(tmp_path, damaged,
            source_config=bundle[0], representative_timeseries=bundle[1])
