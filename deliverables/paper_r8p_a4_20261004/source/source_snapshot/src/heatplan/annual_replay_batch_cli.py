from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from time import perf_counter
from typing import Any, Iterable
from uuid import uuid4

import pandas as pd
import yaml

from .io import load_config
from .provenance import (
    algorithm_provenance, atomic_write_json, canonical_sha256, file_sha256,
)


_COMPLETION_FILE = "batch_completion_manifest.json"
_COMPLETION_SCHEMA = "annual-replay-completion-v1"


def _required_outputs(preflight_only: bool, estimate_model_size: bool) -> set[str]:
    names = {
        "annual_replay_preflight_summary.json", "chronology_quality_audit.json",
        "representative_coverage_audit.csv", "representative_coverage_recommendations.csv",
        "scenario_scope_audit.json",
    }
    if estimate_model_size:
        names.add("annual_replay_model_size_estimate.json")
    if not preflight_only:
        names.update({
            "annual_replay_summary.json", "annual_replay_acceptance_certificate.json",
            "chronological_timeseries.csv", "investment_freeze_audit.csv",
            "annual_replay_kpi_comparison.csv", "model_execution_profile.json",
            "fixed_investment_readiness_audit.json", "seasonal_storage_postcheck.csv",
            "fixed_investment_config.yaml",
        })
        for directory in ("representative_planning", "fixed_investment_annual_replay"):
            names.update(f"{directory}/{name}" for name in (
                "summary.json", "investments.csv", "dispatch.csv",
            ))
    return names


def _output_hashes(profile_output: Path) -> dict[str, str]:
    return {
        path.relative_to(profile_output).as_posix(): file_sha256(path)
        for path in sorted(profile_output.rglob("*"))
        if path.is_file() and path.relative_to(profile_output).as_posix() != _COMPLETION_FILE
    }


def _publish_completion(
    profile_output: Path, *, fingerprint: str, preflight_only: bool,
    estimate_model_size: bool,
) -> bool:
    outcome = _existing_outcome(profile_output, preflight_only=preflight_only)
    if outcome is None or outcome[0] not in {"success", "preflight_ready"}:
        return False
    hashes = _output_hashes(profile_output)
    if not _required_outputs(preflight_only, estimate_model_size).issubset(hashes):
        return False
    evidence = (
        "annual_replay_preflight_summary.json" if preflight_only
        else "annual_replay_acceptance_certificate.json"
    )
    atomic_write_json(profile_output / _COMPLETION_FILE, {
        "schema": _COMPLETION_SCHEMA, "fingerprint": fingerprint,
        "status": outcome[0], "preflight_only": preflight_only,
        "estimate_model_size": estimate_model_size,
        "certificate_path": None if preflight_only else evidence,
        "certificate_sha256": None if preflight_only else hashes[evidence],
        "evidence_path": evidence, "evidence_sha256": hashes[evidence],
        "outputs_sha256": hashes,
    })
    return True


def _completion_matches(
    profile_output: Path, *, fingerprint: str, preflight_only: bool,
    estimate_model_size: bool,
) -> bool:
    marker = _read_json(profile_output / _COMPLETION_FILE)
    if not marker or any((
        marker.get("schema") != _COMPLETION_SCHEMA,
        marker.get("fingerprint") != fingerprint,
        marker.get("preflight_only") is not preflight_only,
        marker.get("estimate_model_size") is not estimate_model_size,
    )):
        return False
    try:
        hashes = _output_hashes(profile_output)
    except OSError:
        return False
    if hashes != marker.get("outputs_sha256") or not _required_outputs(
        preflight_only, estimate_model_size,
    ).issubset(hashes):
        return False
    evidence = ("annual_replay_preflight_summary.json" if preflight_only
                else "annual_replay_acceptance_certificate.json")
    if (marker.get("evidence_path") != evidence
            or marker.get("evidence_sha256") != hashes[evidence]):
        return False
    if not preflight_only and (
        marker.get("certificate_path") != evidence
        or marker.get("certificate_sha256") != hashes[evidence]
    ):
        return False
    outcome = _existing_outcome(profile_output, preflight_only=preflight_only)
    return outcome is not None and outcome[0] == marker.get("status") and (
        outcome[0] in {"success", "preflight_ready"}
    )


def _archive_prior_attempt(profile_output: Path) -> None:
    """Preserve the old attempt outside the fresh output tree, including failures."""
    root = profile_output.parent.resolve()
    if profile_output.is_symlink() or not profile_output.resolve().is_relative_to(root):
        raise ValueError("场景输出目录必须位于指定输出根目录中")
    if profile_output.exists() and any(profile_output.iterdir()):
        destination = (profile_output.parent / ".batch_history" /
                       profile_output.name / uuid4().hex)
        if not destination.resolve().is_relative_to(root):
            raise ValueError("历史输出目录必须位于指定输出根目录中")
        destination.parent.mkdir(parents=True, exist_ok=True)
        profile_output.replace(destination)
    profile_output.mkdir(parents=True, exist_ok=True)


def _resolve(base: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _run_fingerprint(
    *,
    config_path: Path,
    representative_path: Path,
    annual_path: Path,
    timestamp_column: str,
    planning_time_limit: float,
    replay_time_limit: float,
    planning_mip_gap: float | None,
    replay_mip_gap: float | None,
    preflight_only: bool,
    estimate_model_size: bool = False,
    wall_time_limit: float | None = None,
) -> str:
    settings = {
        "timestamp_column": timestamp_column,
        "planning_time_limit": planning_time_limit,
        "replay_time_limit": replay_time_limit,
        "planning_mip_gap": planning_mip_gap,
        "replay_mip_gap": replay_mip_gap,
        "preflight_only": preflight_only,
        "estimate_model_size": estimate_model_size,
        "wall_time_limit": wall_time_limit,
    }
    return canonical_sha256({
        "schema": "annual-replay-input-v2",
        "resolved_config": load_config(config_path),
        "representative_sha256": file_sha256(representative_path),
        "annual_sha256": file_sha256(annual_path),
        "settings": settings,
        "algorithm": algorithm_provenance(),
    })


def _write_batch_summary(output_root: Path, rows: list[dict[str, Any]]) -> None:
    frame = pd.DataFrame(rows)
    frame.to_csv(
        output_root / "annual_replay_batch_summary.csv",
        index=False,
        encoding="utf-8-sig",
    )
    (output_root / "annual_replay_batch_summary.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _existing_outcome(
    profile_output: Path,
    *,
    preflight_only: bool,
) -> tuple[str, dict[str, Any]] | None:
    if preflight_only:
        summary = _read_json(profile_output / "annual_replay_preflight_summary.json")
        if summary is not None and (
            summary.get("chronology_valid") is True
            and summary.get("is_full_year_horizon") is True
            and summary.get("chronology_ready_for_formal_replay") is True
        ):
            return "preflight_ready", summary
        return None
    summary = _read_json(profile_output / "annual_replay_summary.json")
    if summary is not None:
        certificate = _read_json(profile_output / "annual_replay_acceptance_certificate.json")
        status = (
            "success"
            if (summary.get("annual_replay_certified") is True
                and certificate is not None
                and certificate.get("certification_pass") is True)
            else "acceptance_failed"
        )
        return status, summary
    failure = _read_json(profile_output / "annual_replay_failure_summary.json")
    if failure is not None:
        return str(failure.get("status", "failed")), failure
    return None


def _profile_record(
    *,
    name: str,
    profile_output: Path,
    return_code: int,
    elapsed_seconds: float,
    resumed: bool,
    preflight_only: bool,
) -> dict[str, Any]:
    outcome = _existing_outcome(profile_output, preflight_only=preflight_only)
    status, summary = outcome if outcome is not None else ("error", {})
    if return_code != 0 and status in {"success", "preflight_ready"}:
        status = "error"
    record: dict[str, Any] = {
        "profile": name,
        "status": status,
        "return_code": int(return_code),
        "resumed": bool(resumed),
        "elapsed_seconds": float(elapsed_seconds),
        "output": str(profile_output),
    }
    for key in (
        "chronological_rows",
        "is_full_year_horizon",
        "representative_series_review_count",
        "planning_solver_status",
        "replay_solver_status",
        "planning_unserved_rate",
        "replay_unserved_rate",
        "replay_build_seconds",
        "replay_solve_seconds",
        "replay_variables",
        "replay_integer_variables",
        "replay_constraints",
        "replay_matrix_nonzeros",
        "firm_heat_capacity_gap_kw",
        "firm_electric_capacity_gap_kw",
        "preflight_status",
        "message",
    ):
        if key in summary:
            record[key] = summary[key]
    return record


def run_batch_replay(
    manifest_path: str | Path,
    output_root: str | Path,
    *,
    selected_profiles: Iterable[str] | None = None,
    planning_time_limit: float = 180.0,
    replay_time_limit: float = 1800.0,
    planning_mip_gap: float | None = None,
    replay_mip_gap: float | None = None,
    preflight_only: bool = False,
    estimate_model_size: bool = False,
    resume: bool = True,
    wall_time_limit: float | None = None,
) -> pd.DataFrame:
    """Run isolated annual replays from a manifest and preserve every outcome."""

    manifest_path = Path(manifest_path).resolve()
    output_root = Path(output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    profiles = manifest.get("profiles", [])
    if not profiles:
        raise ValueError("批量全年回放清单至少需要一个 profile")
    chosen = set(selected_profiles or ())
    known = {str(profile["name"]) for profile in profiles}
    unknown = chosen - known
    if unknown:
        raise ValueError(f"清单中不存在这些 profile: {', '.join(sorted(unknown))}")
    if planning_time_limit <= 0 or replay_time_limit <= 0:
        raise ValueError("求解时间上限必须大于 0")
    if wall_time_limit is not None and wall_time_limit <= 0:
        raise ValueError("场景墙钟时间上限必须大于 0")
    effective_wall_limit = float(
        wall_time_limit
        if wall_time_limit is not None
        else planning_time_limit + replay_time_limit + 300.0
    )

    rows: list[dict[str, Any]] = []
    for profile in profiles:
        name = str(profile["name"])
        if chosen and name not in chosen:
            continue
        if name in {"", ".", "..", ".batch_history"} or Path(name).name != name:
            raise ValueError("profile 名称必须是单个目录名")
        profile_output = output_root / name
        profile_output.mkdir(parents=True, exist_ok=True)
        config_path = _resolve(manifest_path.parent, str(profile["config"]))
        representative_path = _resolve(
            manifest_path.parent, str(profile["representative_timeseries"])
        )
        annual_path = _resolve(
            manifest_path.parent, str(profile["annual_timeseries"])
        )
        timestamp_column = str(profile.get("timestamp_column", "timestamp"))
        fingerprint_arguments = dict(
            config_path=config_path,
            representative_path=representative_path,
            annual_path=annual_path,
            timestamp_column=timestamp_column,
            planning_time_limit=planning_time_limit,
            replay_time_limit=replay_time_limit,
            planning_mip_gap=planning_mip_gap,
            replay_mip_gap=replay_mip_gap,
            preflight_only=preflight_only,
            estimate_model_size=estimate_model_size,
            wall_time_limit=wall_time_limit,
        )
        fingerprint = _run_fingerprint(**fingerprint_arguments)
        prior_run = _read_json(profile_output / "batch_run_configuration.json")
        prior_outcome = _existing_outcome(
            profile_output, preflight_only=preflight_only
        )
        can_resume = bool(
            resume
            and prior_run is not None
            and prior_run.get("fingerprint") == fingerprint
            and prior_outcome is not None
            and prior_outcome[0] in {"success", "preflight_ready"}
            and _completion_matches(
                profile_output, fingerprint=fingerprint,
                preflight_only=preflight_only, estimate_model_size=estimate_model_size,
            )
        )
        if can_resume:
            record = _profile_record(
                name=name,
                profile_output=profile_output,
                return_code=0,
                elapsed_seconds=0.0,
                resumed=True,
                preflight_only=preflight_only,
            )
            rows.append(record)
            _write_batch_summary(output_root, rows)
            continue

        # A new attempt must never share summaries or partially written evidence
        # with an older attempt. History remains available for inspection.
        _archive_prior_attempt(profile_output)
        command = [
            sys.executable,
            "-m",
            "heatplan.annual_replay_cli",
            "--config",
            str(config_path),
            "--representative-timeseries",
            str(representative_path),
            "--annual-timeseries",
            str(annual_path),
            "--timestamp-column",
            timestamp_column,
            "--output",
            str(profile_output),
            "--planning-time-limit",
            str(planning_time_limit),
            "--replay-time-limit",
            str(replay_time_limit),
        ]
        if planning_mip_gap is not None:
            command.extend(["--planning-mip-gap", str(planning_mip_gap)])
        if replay_mip_gap is not None:
            command.extend(["--replay-mip-gap", str(replay_mip_gap)])
        if preflight_only:
            command.append("--preflight-only")
        if estimate_model_size:
            command.append("--estimate-model-size")
        run_configuration = {
            "profile": name,
            "fingerprint": fingerprint,
            "config": str(config_path),
            "representative_timeseries": str(representative_path),
            "annual_timeseries": str(annual_path),
            "timestamp_column": timestamp_column,
            "planning_time_limit": planning_time_limit,
            "replay_time_limit": replay_time_limit,
            "planning_mip_gap": planning_mip_gap,
            "replay_mip_gap": replay_mip_gap,
            "preflight_only": preflight_only,
            "estimate_model_size": estimate_model_size,
            "wall_time_limit": wall_time_limit,
            "algorithm": algorithm_provenance(),
            "command": command,
        }
        atomic_write_json(profile_output / "batch_run_configuration.json", run_configuration)
        started = perf_counter()
        environment = os.environ.copy()
        source_root = str(Path(__file__).resolve().parents[1])
        environment["PYTHONPATH"] = os.pathsep.join(
            value
            for value in (source_root, environment.get("PYTHONPATH", ""))
            if value
        )
        try:
            completed = subprocess.run(
                command,
                cwd=str(manifest_path.parent),
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=effective_wall_limit,
            )
        except subprocess.TimeoutExpired as exc:
            elapsed_seconds = perf_counter() - started
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
            (profile_output / "batch.stdout.log").write_text(
                stdout, encoding="utf-8"
            )
            (profile_output / "batch.stderr.log").write_text(
                stderr, encoding="utf-8"
            )
            timeout_summary = {
                "status": "wall_time_limit_exceeded",
                "profile": name,
                "wall_time_limit_seconds": effective_wall_limit,
                "elapsed_seconds": elapsed_seconds,
                "message": "场景进程超过外部墙钟时间上限并已终止",
            }
            (profile_output / "annual_replay_batch_timeout_summary.json").write_text(
                json.dumps(timeout_summary, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            record = {
                "profile": name,
                "status": "wall_time_limit_exceeded",
                "return_code": -1,
                "resumed": False,
                "elapsed_seconds": elapsed_seconds,
                "output": str(profile_output),
                "message": timeout_summary["message"],
            }
            rows.append(record)
            _write_batch_summary(output_root, rows)
            continue
        elapsed_seconds = perf_counter() - started
        (profile_output / "batch.stdout.log").write_text(
            completed.stdout, encoding="utf-8"
        )
        (profile_output / "batch.stderr.log").write_text(
            completed.stderr, encoding="utf-8"
        )
        record = _profile_record(
            name=name,
            profile_output=profile_output,
            return_code=completed.returncode,
            elapsed_seconds=elapsed_seconds,
            resumed=False,
            preflight_only=preflight_only,
        )
        if record["status"] in {"success", "preflight_ready"}:
            try:
                inputs_unchanged = _run_fingerprint(**fingerprint_arguments) == fingerprint
            except (OSError, ValueError, AttributeError, yaml.YAMLError):
                inputs_unchanged = False
            if not inputs_unchanged:
                record["status"] = "inputs_changed_during_run"
                record["message"] = "配置、数据、源码或软件环境在运行期间发生变化，未发布完成标记"
            elif not _publish_completion(
                profile_output, fingerprint=fingerprint,
                preflight_only=preflight_only, estimate_model_size=estimate_model_size,
            ):
                record["status"] = "incomplete_outputs"
                record["message"] = "缺少本次运行的必要产物，未发布完成标记"
        rows.append(record)
        _write_batch_summary(output_root, rows)
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="可断点续跑的三场景固定投资全年回放"
    )
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--profiles",
        nargs="*",
        help="只运行指定 profile；默认运行清单中的全部场景",
    )
    parser.add_argument("--planning-time-limit", type=float, default=180.0)
    parser.add_argument("--replay-time-limit", type=float, default=1800.0)
    parser.add_argument("--planning-mip-gap", type=float)
    parser.add_argument("--replay-mip-gap", type=float)
    parser.add_argument(
        "--wall-time-limit",
        type=float,
        help="每个场景的外部墙钟上限；默认等于两段求解上限之和再加300秒",
    )
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--estimate-model-size", action="store_true")
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="忽略相同输入与参数的既有成功结果并重新运行",
    )
    args = parser.parse_args()
    result = run_batch_replay(
        args.manifest,
        args.output,
        selected_profiles=args.profiles,
        planning_time_limit=args.planning_time_limit,
        replay_time_limit=args.replay_time_limit,
        planning_mip_gap=args.planning_mip_gap,
        replay_mip_gap=args.replay_mip_gap,
        preflight_only=args.preflight_only,
        estimate_model_size=args.estimate_model_size,
        resume=not args.no_resume,
        wall_time_limit=args.wall_time_limit,
    )
    print(result.to_string(index=False))
    accepted = {"success", "preflight_ready"}
    if not set(result["status"]).issubset(accepted):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
