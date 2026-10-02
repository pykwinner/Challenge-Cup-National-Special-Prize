"""Failure-isolated climate dispatch with immutable inputs and atomic checkpoints.

This new entry point preserves the byte identity of the rc4 scripts/evidence.
It recomputes feedback baselines and uses their exact known-feasible bounds.
No representative-hour reduction, warm start, new acceleration, or historical
baseline reuse is enabled. Each solver stage receives 60s and zero MIP gap.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
from importlib import metadata
import json
from pathlib import Path
import platform
import sys
from time import perf_counter

import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_three_city_climate_dispatch as base
from heatplan.provenance import atomic_write_json
from run_evidence_case_experiments import jsonable

SCHEMA = "three_city_climate_resilient_v1"
CASES = ("baseline_delta0", "cost_endpoint", "carbon_endpoint", "midpoint_cap")
TASKS = ("prepare", *CASES)
TIME_LIMIT, GAP = 60., 0.


class IdentityFailure(RuntimeError):
    """A batch-wide input/protocol/code/environment identity changed."""


class StageFailure(Exception):
    """Retain the actual phase and original exception for a local failure."""

    def __init__(self, stage, error):
        super().__init__(str(error))
        self.stage, self.error = stage, error


def _phase(stage, function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except IdentityFailure:
        raise
    except Exception as error:  # Do not convert KeyboardInterrupt/SystemExit.
        raise StageFailure(stage, error) from error


def _write(path, value):
    path = Path(path)
    # tempfile appends a random suffix; deep retained evidence paths may cross
    # Win32 MAX_PATH even when their final JSON filename fits. Keep the shared
    # fsync/replace implementation, with an extended absolute path on Windows.
    if sys.platform == "win32":
        name = str(path.resolve())
        if not name.startswith("\\\\?\\"):
            name = "\\\\?\\UNC\\" + name[2:] if name.startswith("\\\\") else "\\\\?\\" + name
        path = Path(name)
    atomic_write_json(path, jsonable(value))


def _root_path(name):
    path = (base.ROOT / name).resolve()
    if not path.is_relative_to(base.ROOT.resolve()):
        raise IdentityFailure(f"identity path escapes project root: {name}")
    return path


def runtime_identity():
    identity = {"python": platform.python_version(), "implementation": platform.python_implementation(),
                "platform": platform.platform()}
    for name in ("numpy", "pandas", "scipy", "PyYAML"):
        identity[name] = metadata.version(name)
    return identity


def code_identity():
    codes = {name.replace("\\", "/"): digest for name, digest in base.code_identity().items()}
    for path in (Path(__file__), Path(base.__file__),
                 base.ROOT / "scripts/refine_three_city_climate_dispatch.py",
                 base.ROOT / "scripts/run_representative_season_confirmation.py",
                 base.ROOT / "src/heatplan/provenance.py"):
        codes[path.relative_to(base.ROOT).as_posix()] = base.sha(path)
    return codes


def _source_identity():
    """Bind all admission/preparation evidence, not just selected city CSVs."""
    manifest = base.read(base.ROOT / base.BASE / "manifest.json")
    admission = base.read(base.ROOT / base.ADMISSION)
    required = {}
    for mapping in (manifest["input_sha256"], manifest["code_sha256"],
                    manifest["artifact_sha256"], admission["input_sha256"]):
        for name, digest in mapping.items():
            path = _root_path(name)
            key = path.relative_to(base.ROOT).as_posix()
            if key in required and required[key] != digest:
                raise IdentityFailure(f"conflicting admitted source identity: {key}")
            required[key] = digest
    for name in (base.CONFIG, base.SERIES, base.ADMISSION, base.BASE / "manifest.json"):
        path = _root_path(name)
        key = path.relative_to(base.ROOT).as_posix()
        current = base.sha(path)
        if key in required and required[key] != current:
            raise IdentityFailure(f"admitted identity changed: {key}")
        required[key] = current
    for name, digest in required.items():
        if base.sha(_root_path(name)) != digest:
            raise IdentityFailure(f"admitted/prepared identity changed: {name}")
    return required


def _global_inputs():
    config, prices = base.load_inputs(base.ROOT / base.CONFIG, base.ROOT / base.SERIES)
    admitted = base.verify_admission(base.ROOT / base.ADMISSION, base.ROOT / base.CONFIG,
                                    base.ROOT / base.SERIES, rc=True)
    base.require(admitted.get("conditional_operating_tariff_reference_admitted") is True,
                 "conditional methodological tariff reference required")
    physics = base.read(base.ROOT / base.BASE / "protocol.json")["physical_reference"]
    base.require(physics["building_thermal"] == config["building_thermal"], "common physical service differs")
    base.chronology_check(prices, config, base.START, base.END)
    base.require(len(prices) == 2880 and (prices.scenario_probability == 1).all(), "invalid common calendar")
    return config, prices


def freeze(out, cities=base.CITIES):
    out = Path(out).resolve()
    cities = tuple(cities)
    base.require(cities and len(set(cities)) == len(cities) and set(cities) <= set(base.CITIES),
                 "cities must be a nonempty unique subset of the registered three cities")
    base.require(not out.exists(), "output exists; preserve prior evidence")
    _global_inputs()
    sources = _source_identity()
    protocol = {
        "schema": SCHEMA, "frozen_utc": datetime.now(timezone.utc).isoformat(), "cities": cities,
        "start_inclusive": base.START, "end_exclusive": base.END, "hours_per_city": 2880,
        "time_limit_per_stage_seconds": TIME_LIMIT, "mip_gap_target": GAP, "cases": CASES,
        "input_sha256": sources, "code_sha256": code_identity(), "runtime_identity": runtime_identity(),
        "baseline_reused_by_identity": False, "historical_evidence_relabelled_as_new": False,
        "baseline": "new complete chronological delta0 feedback rollout, independently audited",
        "endpoint": "existing run_one/two-stage solver; fresh model for each endpoint, each stage60s/gap0",
        "known_feasible_bound_gate": "primary incumbent<=new baseline KPI+1e-6; returned KPI<=baseline KPI+reported primary allowance+1e-6; allowance1e-7+1e-8*abs(primary)",
        "midpoint_rule": "only two accepted endpoints: half their actual carbon sum; applied allowance1e-7+1e-8*abs(cap)",
        "failure_rule": "stage/type/reason retained; local prepare/baseline failure skips its dependent cases; endpoint failure still attempts the other endpoint; missing accepted endpoint skips midpoint",
        "global_failure_rule": "any input/protocol/code/runtime identity failure aborts remaining work and invalidates all comparison claims",
        "checkpoint_rule": "atomic JSON before and after each task; no restart/overwrite of existing run",
        "scope": "same public prototype/ideal existing assets and common methodological Hubei tariff/EF; climate comparison only",
        "cost_scope": "120-day variable electricity bill only", "free_investment_variables": 0,
        "field_validation": False, "local_full_heating_seasons_claimed": False,
        "new_weight_candidate_evaluated": False, "acceleration_requested": False,
        "representative_hour_reduction": False, "source_originals_checked_at_freeze": True,
    }
    out.mkdir(parents=True)
    _write(out / "protocol.json", protocol)
    _write(out / "protocol_lock.json", {"protocol.json": base.sha(out / "protocol.json")})
    print(json.dumps({"frozen": True, "cities": cities, "protocol_sha256": base.sha(out / "protocol.json")}))
    return protocol


def verify_protocol(out):
    try:
        lock = base.read(out / "protocol_lock.json")
        if set(lock) != {"protocol.json"} or base.sha(out / "protocol.json") != lock["protocol.json"]:
            raise IdentityFailure("protocol lock mismatch")
        protocol = base.read(out / "protocol.json")
        if (protocol.get("schema") != SCHEMA or protocol.get("cases") != list(CASES)
                or protocol.get("time_limit_per_stage_seconds") != TIME_LIMIT
                or protocol.get("mip_gap_target") != GAP or protocol.get("hours_per_city") != 2880
                or protocol.get("start_inclusive") != base.START or protocol.get("end_exclusive") != base.END
                or protocol.get("baseline_reused_by_identity") is not False
                or protocol.get("acceleration_requested") is not False
                or protocol.get("representative_hour_reduction") is not False):
            raise IdentityFailure("frozen protocol does not satisfy resilient-v1 invariants")
        cities = protocol["cities"]
        if not cities or len(set(cities)) != len(cities) or not set(cities) <= set(base.CITIES):
            raise IdentityFailure("invalid frozen city selection")
        if protocol["code_sha256"] != code_identity():
            raise IdentityFailure("frozen code identity changed or incomplete")
        if protocol["runtime_identity"] != runtime_identity():
            raise IdentityFailure("frozen numerical runtime identity changed")
        if protocol["input_sha256"] != _source_identity():
            raise IdentityFailure("frozen input identity changed or incomplete")
        return protocol
    except IdentityFailure:
        raise
    except Exception as error:
        raise IdentityFailure(f"cannot verify frozen identities: {type(error).__name__}: {error}") from error


def prepare_city(city, config, prices, folder, protocol):
    folder.mkdir()
    climate = _phase("climate_read", pd.read_csv,
                     base.ROOT / f"data/derived/three_city_common_reference_20261002/{city}_hourly.csv")
    frame = _phase("common_economics_join", base.join_common_economics, climate, prices)
    _phase("city_chronology_validation", base.chronology_check, frame, config,
           protocol["start_inclusive"], protocol["end_exclusive"])
    _phase("city_input_validation", base.validate_inputs, config, frame)
    _phase("city_input_validation", base.require,
           len(frame) == protocol["hours_per_city"] and (frame.scenario_probability == 1).all(),
           "wrong city window/probability")
    _phase("chronology_export", frame.to_csv, folder / "chronology.csv", index=False)
    _phase("config_export", (folder / "common_config.yaml").write_text,
           yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return frame


def baseline_case(config, frame, folder, city):
    trajectory = _phase("baseline_rollout", base.rollout, config, frame, 0)
    _phase("baseline_export", trajectory.to_csv, folder / "baseline_delta0.csv", index=False)
    metrics = _phase("baseline_audit", base.independent_metrics, config, frame, trajectory)
    _write(folder / "baseline_audit.json", metrics)
    _phase("baseline_audit", base.require, metrics["passed"], "baseline independent state/ledger audit failed")
    return {"city": city, "case": "baseline_delta0", "origin": "new_feedback_rollout",
            "result_attribute": "predeclared_feasible_feedback_policy", **metrics}


def optimization_case(name, primary, config, frame, folder, city, baseline, endpoints):
    candidate, cap = deepcopy(config), None
    if name == "midpoint_cap":
        low, high = endpoints["carbon_endpoint"], endpoints["cost_endpoint"]
        _phase("midpoint_endpoint_order", base.require, low <= high + 1e-6 + abs(high) * 1e-8,
               "invalid accepted endpoint order")
        requested = (low + high) / 2
        cap = requested + 1e-7 + abs(requested) * 1e-8
        candidate["planning"]["emissions_cap_kg_per_year"] = cap
        _write(folder / "midpoint_cap_rule.json", {"requested_cap_kg": requested, "applied_cap_kg": cap})
    result, audit = _phase(f"{name}_solve", base.run_one, name, candidate, frame, folder,
                          TIME_LIMIT, GAP, primary=primary)
    trajectory = result.building_trajectory.rename(columns={"temperature_start_c": "T_start",
        "temperature_end_c": "T_end", "cooling_kw": "cool_kw"})
    metrics = _phase(f"{name}_independent_audit", base.independent_metrics, candidate, frame,
                     trajectory, cap=cap, summary=result.summary)
    if primary:
        key = "bill_yuan" if primary == "total_cost" else "carbon_kg"
        incumbent = float(result.summary["primary_incumbent_value"])
        allowance = float(result.summary["primary_objective_allowance"])
        metrics["checks"].update({
            "known_feasible_primary_bound": bool(np.isfinite(incumbent) and incumbent <= baseline[key] + 1e-6),
            "known_feasible_returned_bound": bool(metrics[key] <= baseline[key] + allowance + 1e-6),
            "declared_primary_allowance": bool(np.isfinite(allowance) and allowance >= 0 and
                np.isclose(allowance, 1e-7 + 1e-8 * abs(incumbent), rtol=0, atol=1e-12)),
        })
    metrics["passed"] = all(metrics["checks"].values())
    _write(folder / name / "independent_ledger_audit.json", metrics)
    _phase(f"{name}_independent_audit", base.require, metrics["passed"],
           "independent state/ledger/known feasible bound audit failed")
    return {"city": city, "case": name, "origin": "new_optimization",
            "result_attribute": "fixed_existing_assets_optimized_dispatch",
            "solution_quality": result.summary["solution_quality"], "solver_status": result.summary["solver_status"],
            "mip_gap": result.summary.get("mip_gap"), "wall_seconds": audit["wall_seconds"], **metrics}


def _state(cities):
    return {"schema": SCHEMA, "status": "running", "identity_verified": False,
            "comparison_valid": False, "cities": list(cities), "revision": 0,
            "rows": [], "failures": [], "global_failure": None,
            "tasks": [{"city": city, "case": case, "status": "pending"} for city in cities for case in TASKS]}


def _checkpoint(out, state):
    state["revision"] += 1
    state["updated_utc"] = datetime.now(timezone.utc).isoformat()
    _write(out / "checkpoint.json", state)


def _task(state, city, case):
    return next(task for task in state["tasks"] if task["city"] == city and task["case"] == case)


def _verify_run(out, state):
    """Self-consistent replacement protocol/lock is still a changed run."""
    try:
        expected = state["frozen_run_sha256"]
        for name, digest in expected.items():
            if base.sha(out / name) != digest:
                raise IdentityFailure(f"initial run identity changed: {name}")
        protocol = verify_protocol(out)
        for name, digest in expected.items():
            if base.sha(out / name) != digest:
                raise IdentityFailure(f"initial run identity changed during verification: {name}")
        return protocol
    except IdentityFailure:
        raise
    except Exception as error:
        raise IdentityFailure(f"cannot verify initial run identity: {type(error).__name__}: {error}") from error


def _failure(error, city, case, stage):
    if isinstance(error, StageFailure):
        stage, error = error.stage, error.error
    record = {"city": city, "case": case, "stage": stage, "status": "failed",
              "type": type(error).__name__, "reason": str(error),
              "failed_utc": datetime.now(timezone.utc).isoformat()}
    if getattr(error, "summary", None) is not None:
        record["solver_evidence"] = error.summary
    return record


def _execute(out, state, city, case, stage, function, *args):
    _verify_run(out, state)
    task = _task(state, city, case)
    task.update(status="running", stage=stage)
    _checkpoint(out, state)
    started = perf_counter()
    try:
        value = function(*args)
    except IdentityFailure:
        raise
    except Exception as error:
        _verify_run(out, state)  # An identity failure must never become a local skip.
        record = _failure(error, city, case, stage)
        record["wall_seconds"] = perf_counter() - started
        _write(out / "failure_records" / f"{city}_{case}.json", record)
        state["failures"].append(record)
        task.update(status="failed", stage=record["stage"], type=record["type"], reason=record["reason"])
        value = None
    else:
        _verify_run(out, state)
        task.update(status="accepted" if case != "prepare" else "completed", wall_seconds=perf_counter() - started)
        if case != "prepare":
            state["rows"].append(value)
    _checkpoint(out, state)
    print(json.dumps({"city": city, "case": case, "status": task["status"],
                      "accepted_rows": len(state["rows"]), "failures": len(state["failures"])}), flush=True)
    return value


def _skip(out, state, city, case, dependency, reason):
    task = _task(state, city, case)
    task.update(status="skipped", stage="dependency_gate", type="DependencyUnavailable",
                reason=reason, depends_on=dependency)
    record = {key: task[key] for key in ("city", "case", "status", "stage", "type", "reason", "depends_on")}
    state["failures"].append(record)
    _write(out / "failure_records" / f"{city}_{case}.json", record)
    _checkpoint(out, state)


def _finalize(out, state, started):
    _checkpoint(out, state)
    _write(out / "comparison.json", state["rows"])
    pd.json_normalize(state["rows"]).to_csv(out / "comparison.csv", index=False)
    _write(out / "failures.json", state["failures"])
    files = {path.relative_to(out).as_posix(): base.sha(path) for path in sorted(out.rglob("*"))
             if path.is_file() and path != out / "manifest.json"}
    if state["status"] == "completed":
        _verify_run(out, state)  # Export/hash operations are part of the identity boundary.
    complete = (state["status"] == "completed" and state["identity_verified"] and
                not state["failures"] and len(state["rows"]) == 4 * len(state["cities"]) and
                all(task["status"] in {"accepted", "completed"} for task in state["tasks"]))
    manifest = {"schema": SCHEMA, "all_requested_cases_passed": bool(complete),
        "comparison_valid": state["comparison_valid"], "identity_verified": state["identity_verified"],
        "status": state["status"], "accepted_rows": len(state["rows"]),
        "expected_rows": 4 * len(state["cities"]), "cities": state["cities"], "tasks": state["tasks"],
        "failures": state["failures"], "global_failure": state["global_failure"],
        "new_baseline_rows": sum(row["case"] == "baseline_delta0" for row in state["rows"]),
        "baseline_rows_reused": 0, "new_optimization_cases": sum(row["case"] != "baseline_delta0" for row in state["rows"]),
        "free_investment_variables": 0, "field_validation": False, "acceleration_requested": False,
        "wall_seconds": perf_counter() - started,
        "protocol_sha256": base.sha(out / "protocol.json") if (out / "protocol.json").is_file() else None,
        "frozen_run_sha256": state["frozen_run_sha256"], "files": files}
    _write(out / "manifest.json", manifest)
    return manifest


def run(out):
    out = Path(out).resolve()
    base.require(out.is_dir(), "freeze a new output directory first")
    # A partially completed or failed run is evidence too; never resume over it.
    base.require(set(path.name for path in out.iterdir()) == {"protocol.json", "protocol_lock.json"},
                 "run output exists or frozen directory contains extra files; preserve prior evidence")
    # Exclusive claim is outside the abort handler. A competing invocation must
    # never publish failure/checkpoint files into the active owner's directory.
    (out / "run_claim").mkdir()
    _write(out / "run_claim" / "claim.json", {"claimed_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "exclusive run ownership; retained even on interruption/failure"})
    started, state = perf_counter(), _state(base.CITIES)
    state["frozen_run_sha256"] = {}
    try:
        pinned = {name: base.sha(out / name) for name in ("protocol.json", "protocol_lock.json")}
        state["frozen_run_sha256"] = pinned
        protocol = _verify_run(out, state)
        state = _state(protocol["cities"])
        state["frozen_run_sha256"] = pinned
        config, prices = _global_inputs()
        _verify_run(out, state)
        state["identity_verified"] = True
        _checkpoint(out, state)
        (out / "results").mkdir()
        for city in protocol["cities"]:
            folder = out / "results" / city
            frame = _execute(out, state, city, "prepare", "city_prepare", prepare_city,
                             city, deepcopy(config), prices, folder, protocol)
            if frame is None:
                for case in CASES:
                    _skip(out, state, city, case, ["prepare"], "city preparation failed; no validated city chronology")
                continue
            baseline = _execute(out, state, city, "baseline_delta0", "baseline_rollout", baseline_case,
                                config, frame, folder, city)
            if baseline is None:
                for case in CASES[1:]:
                    _skip(out, state, city, case, ["baseline_delta0"], "baseline failed; no audited known-feasible bound")
                continue
            endpoints = {}
            for name, primary in (("cost_endpoint", "total_cost"), ("carbon_endpoint", "emissions"), ("midpoint_cap", None)):
                if name == "midpoint_cap" and len(endpoints) != 2:
                    _skip(out, state, city, name, ["cost_endpoint", "carbon_endpoint"], "missing accepted endpoint")
                    continue
                row = _execute(out, state, city, name, f"{name}_solve", optimization_case,
                               name, primary, config, frame, folder, city, baseline, endpoints)
                if row is not None and primary:
                    endpoints[name] = row["carbon_kg"]
        _verify_run(out, state)
        state.update(status="completed", identity_verified=True, comparison_valid=True)
        return _finalize(out, state, started)
    except Exception as error:
        state.update(status="aborted", identity_verified=False, comparison_valid=False)
        state["global_failure"] = _failure(error, None, "batch", "global_identity_or_execution")
        state["failures"].append(state["global_failure"])
        _write(out / "global_failure.json", state["global_failure"])
        for task in state["tasks"]:
            if task["status"] in {"pending", "running"}:
                _skip(out, state, task["city"], task["case"], ["global_identity"], "batch aborted; global identity/execution failed")
        _finalize(out, state, started)
        raise


def verify(out):
    out = Path(out).resolve()
    protocol = verify_protocol(out)
    if (out / "manifest.json").exists():
        manifest = base.read(out / "manifest.json")
        if not manifest["identity_verified"] or manifest["status"] != "completed":
            raise IdentityFailure("run is aborted/unverified; retained rows are not valid comparison evidence")
        files = manifest["files"]
        actual = {path.relative_to(out).as_posix() for path in out.rglob("*") if path.is_file() and path != out / "manifest.json"}
        if actual != set(files):
            raise IdentityFailure("run artifact inventory changed")
        for name, digest in files.items():
            path = (out / name).resolve()
            if not path.is_relative_to(out) or base.sha(path) != digest:
                raise IdentityFailure(f"run artifact changed: {name}")
        state = base.read(out / "checkpoint.json")
        _verify_run(out, state)
        expected = {"cities": state["cities"], "status": state["status"],
                    "tasks": state["tasks"], "failures": state["failures"], "global_failure": state["global_failure"],
                    "identity_verified": state["identity_verified"], "comparison_valid": state["comparison_valid"],
                    "accepted_rows": len(state["rows"]), "expected_rows": 4 * len(state["cities"]),
                    "new_baseline_rows": sum(row["case"] == "baseline_delta0" for row in state["rows"]),
                    "new_optimization_cases": sum(row["case"] != "baseline_delta0" for row in state["rows"]),
                    "baseline_rows_reused": 0, "frozen_run_sha256": state["frozen_run_sha256"],
                    "protocol_sha256": base.sha(out / "protocol.json"),
                    "all_requested_cases_passed": bool(not state["failures"] and len(state["rows"]) == 4 * len(state["cities"])
                        and all(task["status"] in {"accepted", "completed"} for task in state["tasks"]))}
        if (state["cities"] != protocol["cities"] or state["status"] != "completed"
                or not state["comparison_valid"] or state["global_failure"] is not None
                or any(manifest.get(key) != value for key, value in expected.items())
                or base.read(out / "comparison.json") != state["rows"]
                or base.read(out / "failures.json") != state["failures"]):
            raise IdentityFailure("manifest/checkpoint/protocol accounting mismatch")
    elif (out / "checkpoint.json").exists() or (out / "run_claim").exists():
        raise IdentityFailure("incomplete run: checkpoint exists without final manifest")
    return protocol


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("freeze", "run", "verify"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cities", nargs="+", choices=base.CITIES, default=base.CITIES,
                        help="prospectively select cities at freeze only; default all three")
    args = parser.parse_args()
    if args.action == "freeze":
        freeze(args.out, args.cities)
    elif args.action == "run":
        manifest = run(args.out)
        print(json.dumps({key: manifest[key] for key in ("status", "accepted_rows", "all_requested_cases_passed")}))
        return 0 if manifest["all_requested_cases_passed"] else 1
    else:
        verify(args.out)
        print(json.dumps({"hash_verification_passed": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
