"""公开原型数值复现：精确处理后输入复算，可选完整原件 SHA 检查。

Default mode inherits the recorded source review; it does not revalidate PDFs.
This entry accepts only the pinned reference admission and its exact inputs.
It runs one baseline with existing assets, not an investment/algorithm contest.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import platform
import shutil
import sys
from time import perf_counter

import numpy as np
import pandas as pd
import scipy
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from heatplan.building_audit import audit_building_trajectory
from heatplan.io import load_inputs


ADMISSION_PATH = "docs/implementation_20261002/data/admission_tou.json"
ADMISSION_SHA256 = "1fef22f34d8cd931cbaebbaedc2c1524dc5f899a958f229e34bdf05e2bf3028f"
REUSED_RUNNER_SHA256 = "ccb81b5a8d0eb348bcd5aa1216d745614988fa3bedc926e1e2ba0cd93532a26c"
PROCESSED_ROLES = ("rc_config", "timeseries", "field_provenance", "monthly_tariff", "tariff_rules", "base_admission")
DEFAULT_SOURCE_STATEMENT = "原始来源沿用既有审核，本次没有重新核验全部原件"
STRICT_SOURCE_STATEMENT = "本次核对完整准入表登记文件的 SHA；原件内容审核沿用既有记录，本次没有重新逐页审核"
GAP_TARGET = 0.0001


def filesystem_path(path):
    """Keep nested Windows evidence bundles usable beyond the legacy 260 limit."""
    absolute = Path(path).resolve()
    name = str(absolute)
    if sys.platform == "win32" and not name.startswith("\\\\?\\"):
        name = "\\\\?\\UNC\\" + name[2:] if name.startswith("\\\\") else "\\\\?\\" + name
        return Path(name)
    return absolute


def sha(path):
    return hashlib.sha256(filesystem_path(path).read_bytes()).hexdigest()


def save(path, value):
    filesystem_path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


class ReferenceRejected(ValueError):
    def __init__(self, message, report):
        super().__init__(message)
        self.report = report


def _under(root, relative):
    root = filesystem_path(root)
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("admission path escapes the selected input root")
    return path


def _leaves(value, prefix=""):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _leaves(child, f"{prefix}.{key}" if prefix else str(key))
    elif isinstance(value, list):
        if not value:
            yield prefix
        for number, child in enumerate(value):
            yield from _leaves(child, f"{prefix}[{number}]")
    else:
        yield prefix


def check_chronology(frame, config, scope):
    """Require the exact registered aware hour sequence, not just its row count."""
    if len(frame) != 2880 or scope["hours"] != 2880 or config["planning"]["time_step_hours"] != 1:
        raise ValueError("locked reference must be exactly 2880 one-hour intervals")
    stamps = pd.DatetimeIndex(pd.to_datetime(frame["time"], errors="raise"))
    if stamps.tz is None or stamps.hasnans or not stamps.is_unique:
        raise ValueError("reference hours must be unique, complete and timezone-aware")
    expected = pd.date_range(scope["start_inclusive"], scope["end_exclusive"], freq="h", inclusive="left")
    if not stamps.tz_convert("UTC").equals(expected.tz_convert("UTC")):
        raise ValueError("reference chronology differs from the locked full window")
    for column in ("period_weight", "scenario_probability", "heating_service"):
        if column not in frame or not np.allclose(frame[column], 1, rtol=0, atol=1e-12):
            raise ValueError(f"full chronology requires unit {column}")
    for column in ("scenario", "boundary_group"):
        if column not in frame or frame[column].isna().any() or frame[column].nunique() != 1:
            raise ValueError(f"full chronology requires a single complete {column}")
    return {"passed": True, **scope, "interval_convention": "[timestamp,timestamp+1h)",
            "unique_ordered_hour_sequence_checked": True, "unit_weights_checked": True}


def check_field_provenance(config, frame, fields, admission):
    paths = admission["input_paths"]
    expected = {(Path(paths["rc_config"]).name, name) for name in _leaves(config)}
    expected |= {(Path(paths["timeseries"]).name, name) for name in frame.columns}
    required = {"artifact", "field", "source_ids", "formula_or_locator", "identity_and_scope"}
    if not required.issubset(fields.columns):
        raise ValueError("field provenance is missing required columns")
    if len(fields) != len(expected) or set(zip(fields.artifact, fields.field)) != expected:
        raise ValueError("field provenance must cover every config leaf/CSV column exactly once")
    for row in fields.itertuples():
        if (not str(row.source_ids).strip() or set(str(row.source_ids).split(";")) - set(admission["sources"])
                or not str(row.formula_or_locator).strip() or not str(row.identity_and_scope).strip()):
            raise ValueError("field provenance has unknown or empty source/scope")
    return {"passed": True, "covered_rows": len(fields), "all_config_leaves_and_timeseries_columns": True,
            "review_identity": "locked field provenance inherited; original content not newly reviewed"}


def inspect_reference(input_root, *, verify_original_sources=False):
    """Real file checks scoped to this root; no fallback to project PDFs or network."""
    root = filesystem_path(input_root)
    admission_path = _under(root, ADMISSION_PATH)
    report = {"passed": False, "input_root": str(root), "mode": "original_hash_verification" if verify_original_sources else "processed_input_numerical_reproduction",
              "original_source_hashes_verified_this_run": False, "original_content_review_repeated": False,
              "source_review_inherited": True, "source_statement": DEFAULT_SOURCE_STATEMENT,
              "network_download_performed": False, "checks": [], "failed_files": []}
    if not admission_path.is_file() or sha(admission_path) != ADMISSION_SHA256:
        report["failed_files"] = [{"path": ADMISSION_PATH, "expected_sha256": ADMISSION_SHA256,
                                   "status": "missing" if not admission_path.is_file() else "sha256_mismatch",
                                   "acquisition_hint": "取得既有审核版本的 admission_tou.json；不能修改其哈希或准入范围接纳新参数"}]
        raise ReferenceRejected("locked admission_tou.json is missing or changed", report)
    admission = json.loads(admission_path.read_text(encoding="utf-8"))
    if not all(admission.get(flag) is True for flag in ("physical_reference_admitted", "rc_reference_admitted", "conditional_operating_tariff_reference_admitted")):
        raise ReferenceRejected("reference has not passed the recorded admission scope", report)
    report.update(admission_sha256=ADMISSION_SHA256, case_id=admission["case_id"],
                  admission_scope=admission["admission_scope"], economic_scope=admission["economic_scope"],
                  excluded_economic_fields=admission["excluded_economic_fields"],
                  full_economics_admitted=False, account_eligibility_verified=False,
                  grid_connection_feasibility_verified=False, external_building_validation_passed=False,
                  registered_original_review_claims=admission["checks"],
                  registered_claims_are_inherited_not_current_verification=True)
    numerical_paths = {admission["input_paths"][role] for role in PROCESSED_ROLES}
    if not numerical_paths.issubset(admission["input_sha256"]):
        raise ReferenceRejected("locked admission does not cover necessary processed inputs", report)
    # The base admission is small metadata, not an original PDF. Read it only
    # after validating its locked hash, to recover legacy-source acquisition URLs.
    sources = dict(admission["sources"])
    base_name = admission["input_paths"]["base_admission"]
    base_path = _under(root, base_name)
    if base_path.is_file() and sha(base_path) == admission["input_sha256"][base_name]:
        base = json.loads(base_path.read_text(encoding="utf-8"))
        sources = {**base.get("sources", {}), **sources}
    source_paths = {value["path"]: {"source_id": key, **value} for key, value in sources.items() if value.get("path")}
    for name, digest in admission["input_sha256"].items():
        path = _under(root, name)
        required = name in numerical_paths or verify_original_sources
        source = source_paths.get(name, {})
        row = {"path": name, "expected_sha256": digest, "required_this_run": required,
               "checked_this_run": required, "source_id": source.get("source_id"),
               "source_identity": source.get("identity"), "acquisition_url": source.get("url") or None,
               "acquisition_hint": "沿用登记来源获取相同版本并核对SHA；本入口不下载或重新生成原件" if source else "从相同版本工程取得该登记文件；本入口不以新文件替代既有准入"}
        if not path.is_file():
            row["status"] = "missing" if required else "missing_not_required_for_numerical_reproduction"
        elif required:
            row["actual_sha256"] = sha(path)
            row["status"] = "sha256_match" if row["actual_sha256"] == digest else "sha256_mismatch"
        else:
            row["status"] = "present_not_reverified_this_run"
        report["checks"].append(row)
        if required and row["status"] != "sha256_match":
            report["failed_files"].append(row)
    report["source_registry"] = sources
    report["processed_input_paths"] = admission["input_paths"]
    if report["failed_files"]:
        raise ReferenceRejected("required locked files missing or changed; see source_identity.json for exact files/URLs", report)
    config, frame = load_inputs(_under(root, admission["input_paths"]["rc_config"]),
                                _under(root, admission["input_paths"]["timeseries"]))
    fields = pd.read_csv(_under(root, admission["input_paths"]["field_provenance"]), keep_default_na=False)
    report["chronology"] = check_chronology(frame, config, admission["time_scope"])
    report["field_provenance"] = check_field_provenance(config, frame, fields, admission)
    if config.get("storages") or config.get("pipes") or any(float(s.get("max_new_capacity_kw", 0)) != 0 or float(s.get("max_retire_capacity_kw", 0)) != 0 for s in config["sources"]):
        raise ReferenceRejected("locked reference is existing ideal assets without storage/network/investment", report)
    report["passed"] = True
    if verify_original_sources:
        report["original_source_hashes_verified_this_run"] = True
        report["source_statement"] = STRICT_SOURCE_STATEMENT
    return admission, config, frame, report


def _runner():
    path = ROOT / "scripts/run_evidence_case_experiments.py"
    if sha(path) != REUSED_RUNNER_SHA256:
        raise ValueError("reused run_one source changed from registered portable baseline")
    spec = spec_from_file_location("_portable_reference_original_runner", path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def audit_saved_baseline(destination, config, frame):
    """Audit saved CSV outputs without solving; ledger limited to this ideal case."""
    destination = filesystem_path(destination)
    raw_path = destination / "model_summary_raw.json"
    if not raw_path.is_file():
        raw_path = destination / "summary.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    identity = json.loads((destination / "input_identity.json").read_text(encoding="utf-8"))
    trajectory = pd.read_csv(destination / "building_trajectory.csv")
    dispatch = pd.read_csv(destination / "dispatch.csv")
    building = audit_building_trajectory(config, frame, trajectory, dispatch, raw,
                                         require_continuous_fixed=True, expected_input_identity=identity)
    ordered = trajectory.sort_values("time_index")
    if len(ordered) != len(frame) or not np.array_equal(ordered.time_index, np.arange(len(frame))):
        raise ValueError("one building trajectory row per full input hour is required")
    if config["sources"][0]["efficiency"] != 1 or config["multi_energy_services"]["cooling"]["sources"]["bestest_ideal_air_system"]["eer"] != 1:
        raise ValueError("independent ledger is only for the exact admitted ideal unit-efficiency reference")
    electric = ordered.heat_kw.to_numpy(dtype=float) + ordered.cooling_kw.to_numpy(dtype=float)
    energy = float(electric.sum())
    bill = float(electric @ frame.electricity_price.to_numpy(dtype=float))
    carbon = float(electric @ frame.grid_emission_factor.to_numpy(dtype=float))
    reported = {"electricity_kwh": float(raw["electricity_kwh_per_year"]) + float(raw["cooling_electricity_kwh_per_year"]),
                "electricity_energy_charge_yuan": float(raw["objective_value"]),
                "fixed_factor_reference_carbon_kg": float(raw["total_service_emissions_kg_per_year"])}
    recomputed = {"electricity_kwh": energy, "electricity_energy_charge_yuan": bill,
                  "fixed_factor_reference_carbon_kg": carbon}
    checks = {key: {"recomputed": value, "reported": reported[key], "signed_residual": value - reported[key],
                    "absolute_tolerance": 1e-5, "relative_tolerance": 1e-9,
                    "passed": bool(abs(value - reported[key]) <= 1e-5 + 1e-9 * abs(value))}
              for key, value in recomputed.items()}
    passed = bool(building["passed"] and all(check["passed"] for check in checks.values()))
    return {"passed": passed, "independent_saved_building_audit": building, "independent_ledger_checks": checks,
            "hour_count": len(frame), "ledger_scope": "admitted ideal unit-efficiency heat/cooling, published energy charges, fixed factor only",
            "metrics": {**recomputed, "heat_delivered_kwh": float(ordered.heat_kw.sum()),
                        "cooling_delivered_kwh": float(ordered.cooling_kw.sum()),
                        "min_temperature_c": float(ordered.temperature_end_c.min()),
                        "max_temperature_c": float(ordered.temperature_end_c.max())},
            "full_economics_validated": False, "storage_SOC_applicable": False}


def reproduce(input_root, output, *, verify_original_sources=False, check_only=False, time_limit=120.0):
    output, started = filesystem_path(output), perf_counter()
    if output.exists():
        raise ValueError("output exists; choose a fresh directory to preserve prior evidence")
    if not np.isfinite(time_limit) or time_limit <= 0:
        raise ValueError("solver time limit must be positive and finite")
    output.mkdir(parents=True)
    frozen_code = None
    try:
        admission, _, _, source = inspect_reference(input_root, verify_original_sources=verify_original_sources)
        save(output / "source_identity.json", source)
        bundle = output / "input_bundle"
        names = [ADMISSION_PATH] + [admission["input_paths"][role] for role in PROCESSED_ROLES]
        for name in names:
            destination = _under(bundle, name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(_under(input_root, name), destination)
        # Check the copies used for numerical execution; no config overrides.
        _, config, frame, _ = inspect_reference(bundle)
        runner = _runner()
        frozen_code = {**runner.code_identity(), "scripts/reproduce_admitted_reference.py": sha(Path(__file__))}
        environment = {"python": platform.python_version(), "python_executable": sys.executable,
                       "platform": platform.platform(), "numpy": np.__version__, "pandas": pd.__version__,
                       "scipy": scipy.__version__, "PyYAML": yaml.__version__}
        save(output / "environment.json", environment)
        protocol = {"frozen_utc": datetime.now(timezone.utc).isoformat(), "name": "公开原型数值复现",
                    "case_id": admission["case_id"], "source_statement": source["source_statement"],
                    "admission_sha256": ADMISSION_SHA256, "chronology": source["chronology"],
                    "code_sha256": frozen_code, "check_only": bool(check_only),
                    "experiments": [] if check_only else ["baseline_complete_2880h_existing_assets"],
                    "solver_time_limit_seconds": time_limit, "solver_gap_target": GAP_TARGET,
                    "free_investment_case": False, "full_economics_admitted": False,
                    "algorithm_advantage_claimed": False, "empirical_or_engineering_case_claimed": False,
                    "storage_SOC_applicable": False, "source_download_required_for_default": False}
        save(output / "protocol.json", protocol)
        metrics = None
        if not check_only:
            result, audit = runner.run_one("baseline", config, frame, output, time_limit, GAP_TARGET)
            destination = output / "baseline"
            independent = audit_saved_baseline(destination, config, frame)
            save(destination / "independent_saved_output_audit.json", independent)
            if not independent["passed"]:
                raise ValueError("saved full-hour outputs failed independent numerical audit")
            # Retain raw arithmetic evidence, then present missing/excluded
            # financial quantities as null rather than zero economic benefit.
            shutil.copyfile(destination / "summary.json", destination / "model_summary_raw.json")
            metrics = independent["metrics"]
            summary = {"name": "公开原型数值复现", "case_id": admission["case_id"], "hours": len(frame),
                       "source_statement": source["source_statement"], "metrics_for_this_2880h_window": metrics,
                       "numerical_audit_passed": bool(audit["passed"] and independent["passed"]),
                       "solver_status": result.summary["solver_status"], "solver_gap": result.summary.get("mip_gap"),
                       "solution_quality": result.summary["solution_quality"],
                       "actual_solver_scope": result.summary["solution_quality"]["scope"],
                       "economic_scope": admission["economic_scope"],
                       "excluded_financial_quantities": {key: {"value": None, "status": "not_admitted_not_evaluated"}
                                                        for key in ("CAPEX", "installation", "fixed_OM", "variable_OM", "lifetime_cash_flow", "net_benefit", "payback")},
                       "raw_model_summary_notice": "model_summary_raw.json保留求解器账本；禁用投资/OM等构造系数中的0不代表真实费用缺失项为0或收益证明",
                       "storage_SOC": None, "storage_SOC_reason": "locked reference has no storage",
                       "investment_improvement_claimed": False, "algorithm_advantage_claimed": False,
                       "external_building_validation_passed": False, "full_economics_admitted": False}
            save(destination / "summary.json", runner.jsonable(summary))
        stable_code = frozen_code == {**runner.code_identity(), "scripts/reproduce_admitted_reference.py": sha(Path(__file__))}
        inspect_reference(input_root, verify_original_sources=verify_original_sources)
        inspect_reference(bundle)
        if not stable_code:
            raise ValueError("model/audit code changed during reproduction")
        manifest = {"passed": True, "status": "input_checks_only" if check_only else "baseline_numerical_reproduction_passed",
                    "name": "公开原型数值复现", "source_statement": source["source_statement"],
                    "original_source_hashes_verified_this_run": source["original_source_hashes_verified_this_run"],
                    "full_2880h_solver_runs": 0 if check_only else 1, "actual_solver_scope": None if check_only else summary["actual_solver_scope"],
                    "full_economics_admitted": False, "algorithm_advantage_claimed": False,
                    "code_stable_during_run": stable_code, "wall_seconds": perf_counter() - started,
                    "files_sha256": {str(file.relative_to(output)).replace("\\", "/"): sha(file)
                                     for file in sorted(output.rglob("*")) if file.is_file()}}
        save(output / "manifest.json", manifest)
        return {"passed": True, "output": str(output), "status": manifest["status"], "metrics": metrics}
    except Exception as error:
        if isinstance(error, ReferenceRejected):
            save(output / "source_identity.json", error.report)
        save(output / "failure.json", {"passed": False, "type": type(error).__name__, "reason": str(error),
                                       "original_source_hashes_verified_this_run": False, "code_before": frozen_code})
        save(output / "manifest.json", {"passed": False, "status": "failed", "name": "公开原型数值复现",
                                        "failure_type": type(error).__name__, "reason": str(error),
                                        "wall_seconds": perf_counter() - started,
                                        "files_sha256": {str(file.relative_to(output)).replace("\\", "/"): sha(file)
                                                         for file in sorted(output.rglob("*")) if file.is_file()}})
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=Path, default=ROOT, help="已准入文件的相对路径根目录；不从其他目录补找原件")
    parser.add_argument("--out", type=Path, required=True, help="新的证据输出目录")
    parser.add_argument("--verify-original-sources", action="store_true", help="核对完整准入表所有登记SHA；缺原件明确失败并列出获取入口")
    parser.add_argument("--check-only", action="store_true", help="仅核输入/时间轴/溯源，保存证据，不求解")
    parser.add_argument("--time-limit", type=float, default=120.0, help="一次基线求解预算秒数；不修改物理/经济参数")
    args = parser.parse_args(argv)
    try:
        result = reproduce(args.input_root, args.out, verify_original_sources=args.verify_original_sources,
                           check_only=args.check_only, time_limit=args.time_limit)
    except Exception as error:
        print(json.dumps({"passed": False, "reason": str(error), "output": str(args.out)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
