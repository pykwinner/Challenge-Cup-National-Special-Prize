from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import yaml


@dataclass
class FeasibilityResult:
    report: pd.DataFrame
    eligible_assets: set[tuple[str, str]]
    blocked_assets: set[tuple[str, str]]

    @property
    def passed(self) -> bool:
        return bool((self.report["status"] != "fail").all())


REQUIRED_GATES = (
    "heat_source",
    "heat_network",
    "electric_grid",
    "customer_side",
    "finance",
    "construction_operation",
)


def load_feasibility(path: str | Path) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def evaluate_feasibility(specification: dict[str, Any]) -> FeasibilityResult:
    """Evaluate auditable engineering gates before optimization.

    Each check is explicit: ``passed`` records the engineering judgement,
    ``evidence`` provides the document or data reference, and ``asset_refs``
    identifies candidates that must be disabled when a mandatory check fails.
    A missing mandatory check/evidence is a failure rather than an optimistic
    default.
    """
    gates = specification.get("gates", {})
    rows: list[dict[str, Any]] = []
    eligible: set[tuple[str, str]] = set()
    blocked: set[tuple[str, str]] = set()
    for gate_name in REQUIRED_GATES:
        gate = gates.get(gate_name)
        if gate is None:
            rows.append({
                "gate": gate_name,
                "check": "gate_defined",
                "mandatory": True,
                "status": "fail",
                "evidence": "",
                "reason": "缺少必需工程准入门",
                "asset_refs": "",
            })
            continue
        for check in gate.get("checks", []):
            mandatory = bool(check.get("mandatory", True))
            passed = bool(check.get("passed", False))
            evidence = str(check.get("evidence", "")).strip()
            evidence_ok = bool(evidence) or not bool(check.get("evidence_required", True))
            status = "pass" if passed and evidence_ok else ("fail" if mandatory else "warning")
            refs: list[str] = []
            for ref in check.get("asset_refs", []):
                category = str(ref["category"])
                asset = str(ref["asset"])
                refs.append(f"{category}:{asset}")
                if status == "fail":
                    blocked.add((category, asset))
                elif status == "pass":
                    eligible.add((category, asset))
            reason = str(check.get("reason", ""))
            if passed and not evidence_ok:
                reason = reason or "判定通过但缺少可审计证据"
            rows.append({
                "gate": gate_name,
                "check": str(check.get("name", "unnamed_check")),
                "mandatory": mandatory,
                "status": status,
                "evidence": evidence,
                "reason": reason,
                "asset_refs": ";".join(refs),
            })
    return FeasibilityResult(pd.DataFrame(rows), eligible - blocked, blocked)


def apply_feasibility_exclusions(config: dict[str, Any], result: FeasibilityResult) -> dict[str, Any]:
    """Return a copy with failed candidate assets removed from the choice set."""
    filtered = copy.deepcopy(config)
    for category, asset in result.blocked_assets:
        if category == "source":
            match = next((item for item in filtered["sources"] if item["name"] == asset), None)
            if match is not None:
                match["max_new_capacity_kw"] = 0.0
        elif category == "storage":
            match = next((item for item in filtered["storages"] if item["name"] == asset), None)
            if match is not None:
                match["max_new_capacity_kwh"] = 0.0
        elif category == "pipe":
            match = next((item for item in filtered["pipes"] if item["name"] == asset), None)
            if match is not None:
                match["diameter_options"] = []
        elif category == "terminal_retrofit":
            match = next((item for item in filtered["nodes"] if item["name"] == asset), None)
            if match is not None and match.get("temperature_retrofit"):
                match["temperature_retrofit"]["fixed_value"] = 0.0
        elif category == "grid_connection" and asset == "system":
            if filtered.get("grid_connection"):
                filtered["grid_connection"]["max_new_capacity_kw"] = 0.0
        else:
            raise ValueError(f"未知工程准入资产引用: {category}:{asset}")
    return filtered

