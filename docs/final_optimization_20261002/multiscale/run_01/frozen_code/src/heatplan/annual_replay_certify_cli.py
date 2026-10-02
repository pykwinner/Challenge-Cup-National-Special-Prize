from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from .chronology import (
    annual_replay_acceptance_audit,
    seasonal_storage_postcheck,
)


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def certify_annual_replay_output(output: str | Path) -> dict[str, Any]:
    """Rebuild postchecks and the certificate from an existing replay."""

    output = Path(output).resolve()
    fixed_config = yaml.safe_load(
        (output / "fixed_investment_config.yaml").read_text(encoding="utf-8")
    )
    planning_summary = _json(output / "representative_planning" / "summary.json")
    replay_summary = _json(
        output / "fixed_investment_annual_replay" / "summary.json"
    )
    dispatch = pd.read_csv(
        output / "fixed_investment_annual_replay" / "dispatch.csv"
    )
    coverage = pd.read_csv(output / "representative_coverage_audit.csv")
    freeze = pd.read_csv(output / "investment_freeze_audit.csv")
    readiness = _json(output / "fixed_investment_readiness_audit.json")
    chronology = _json(output / "chronology_quality_audit.json")
    chronological_timeseries = pd.read_csv(output / "chronological_timeseries.csv")
    storage = seasonal_storage_postcheck(fixed_config, dispatch, expected_timeseries=chronological_timeseries)
    storage.to_csv(output / "seasonal_storage_postcheck.csv", index=False)
    certificate = annual_replay_acceptance_audit(
        fixed_config,
        chronology_audit=chronology,
        representative_coverage_audit=coverage,
        investment_freeze_audit=freeze,
        fixed_investment_readiness_audit=readiness,
        planning_summary=planning_summary,
        replay_summary=replay_summary,
        storage_boundary_audit=storage,
    )
    (output / "annual_replay_acceptance_certificate.json").write_text(
        json.dumps(certificate, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    combined_path = output / "annual_replay_summary.json"
    combined = _json(combined_path)
    combined["annual_replay_certified"] = bool(
        certificate["certification_pass"]
    )
    combined["acceptance_failed_gates"] = certificate["failed_gates"]
    combined_path.write_text(
        json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return certificate


def main() -> None:
    parser = argparse.ArgumentParser(
        description="从既有全年回放结果重建储热后检查与验收证书"
    )
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    certificate = certify_annual_replay_output(args.output)
    print(json.dumps(certificate, ensure_ascii=False, indent=2))
    if not certificate["certification_pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
