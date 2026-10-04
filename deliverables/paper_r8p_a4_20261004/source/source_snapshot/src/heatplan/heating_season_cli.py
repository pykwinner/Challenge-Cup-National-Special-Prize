from __future__ import annotations

import argparse
from hashlib import sha256
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from .heating_season import prepare_heating_season_timeseries, run_fixed_investment_heating_season_replay
from .io import load_config
from .provenance import algorithm_provenance, atomic_write_json, canonical_sha256, file_sha256


def _json_diagnostics(value):
    """Unavailable numeric KPIs are JSON null, never nonstandard NaN/Infinity."""
    if isinstance(value, dict):
        return {key: _json_diagnostics(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_diagnostics(item) for item in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="固定投资跨年供暖季验证；不替代全年验收")
    parser.add_argument("--config", required=True)
    parser.add_argument("--representative-timeseries", required=True)
    parser.add_argument("--chronological-timeseries", required=True)
    parser.add_argument("--scope-file", required=True)
    parser.add_argument("--profile")
    parser.add_argument("--operating-scenario", help="显式映射用户行为情景系数；不自动使用缺省1")
    parser.add_argument("--input-timezone", help="无时区数据的真实时区；NASA LST须先在上游转换")
    parser.add_argument("--timestamp-column", default="timestamp")
    parser.add_argument("--output", required=True)
    parser.add_argument("--planning-time-limit", type=float, default=180.0)
    parser.add_argument("--replay-time-limit", type=float, default=1800.0)
    parser.add_argument("--allow-cyclic-state-assumptions", action="store_true",
                        help="明确接受尚无初始状态接口模块的循环边界，并写入证书假设")
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    def write(name, value):
        atomic_write_json(output / name, _json_diagnostics(value))
    # An interrupted new run must not retain an older passing certificate.
    write("heating_season_acceptance_certificate.json", {"certification_pass": False, "status": "in_progress"})
    write("heating_season_summary.json", {"heating_season_certified": False, "status": "in_progress"})
    try:
        config = load_config(args.config)
        scope_data = json.loads(Path(args.scope_file).read_text(encoding="utf-8"))
        scopes = scope_data.get("scenarios", [scope_data])
        selected = [scope for scope in scopes if args.profile is None or scope.get("profile") == args.profile]
        if len(selected) != 1:
            raise ValueError("--profile must select exactly one service-period scope")
        if args.operating_scenario:
            selected[0] = {**selected[0], "operating_scenario": args.operating_scenario}
        source = pd.read_csv(args.chronological_timeseries)
        chronology, audit = prepare_heating_season_timeseries(config, source, selected[0],
            timestamp_column=args.timestamp_column, input_timezone=args.input_timezone)
        write("heating_season_scope_audit.json", audit)
        chronology.to_csv(output / "chronological_timeseries.csv", index=False)
        if args.preflight_only:
            write("heating_season_summary.json", {"heating_season_certified": False, "status": "preflight_only", "scope": audit})
            print(json.dumps(audit, ensure_ascii=False, indent=2))
            return
        representative = pd.read_csv(args.representative_timeseries)
        identity = {"config": config, "scope": selected[0], "algorithm": algorithm_provenance(),
                    "representative_sha256": sha256(representative.to_csv(index=False).encode("utf-8")).hexdigest(),
                    "chronological_source_sha256": sha256(source.to_csv(index=False).encode("utf-8")).hexdigest(),
                    "input_timezone": args.input_timezone, "timestamp_column": args.timestamp_column,
                    "planning_time_limit": args.planning_time_limit, "replay_time_limit": args.replay_time_limit,
                    "allow_cyclic_state_assumptions": args.allow_cyclic_state_assumptions}
        write("run_identity.json", {"fingerprint": canonical_sha256(identity), "identity": identity})
        artifacts = run_fixed_investment_heating_season_replay(
            config, representative, source, selected[0],
            timestamp_column=args.timestamp_column, input_timezone=args.input_timezone,
            planning_time_limit=args.planning_time_limit, replay_time_limit=args.replay_time_limit,
            allow_cyclic_state_assumptions=args.allow_cyclic_state_assumptions,
        )
        artifacts.result.planning.investments.to_csv(output / "planned_investments.csv", index=False)
        artifacts.result.replay.dispatch.to_csv(output / "period_dispatch.csv", index=False)
        for field in ("building_trajectory", "building_comfort"):
            table = getattr(artifacts.result.replay, field, None)
            if table is not None:
                table.to_csv(output / f"{field}.csv", index=False)
        artifacts.result.investment_freeze_audit.to_csv(output / "investment_freeze_audit.csv", index=False)
        artifacts.storage_audit.to_csv(output / "storage_trajectory_audit.csv", index=False)
        (output / "fixed_investment_config.yaml").write_text(yaml.safe_dump(artifacts.result.fixed_config, allow_unicode=True, sort_keys=False), encoding="utf-8")
        write("planning_summary.json", artifacts.result.planning.summary)
        write("heating_season_summary.json", artifacts.period_summary)
        artifact_names = ("run_identity.json", "heating_season_scope_audit.json", "chronological_timeseries.csv",
                          "planned_investments.csv", "period_dispatch.csv", "investment_freeze_audit.csv",
                          "storage_trajectory_audit.csv", "fixed_investment_config.yaml", "planning_summary.json", "heating_season_summary.json")
        artifacts.certificate["run_identity_sha256"] = canonical_sha256(identity)
        artifacts.certificate["artifact_sha256"] = {name: file_sha256(output / name) for name in artifact_names}
        write("heating_season_acceptance_certificate.json", artifacts.certificate)
        print(json.dumps(artifacts.period_summary, ensure_ascii=False, indent=2))
        if not artifacts.certificate["certification_pass"]:
            raise SystemExit(1)
    except (ValueError, RuntimeError, OSError, KeyError, yaml.YAMLError) as exc:
        write("heating_season_failure.json", {"status": "failed", "message": str(exc)})
        write("heating_season_acceptance_certificate.json", {"certification_pass": False, "status": "failed", "message": str(exc)})
        write("heating_season_summary.json", {"heating_season_certified": False, "status": "failed", "message": str(exc)})
        parser.error(str(exc))


if __name__ == "__main__":
    main()
