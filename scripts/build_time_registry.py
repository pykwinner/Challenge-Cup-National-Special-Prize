"""Register explicitly selected, hash-pinned experiment time axes read-only."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from heatplan.time_registry import describe_time_axis


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build_registry(root, spec):
    root = Path(root).resolve()
    rows = []
    seen = set()
    for entry in spec["entries"]:
        if not entry["id"] or entry["id"] in seen:
            raise ValueError("missing or duplicate registry ID")
        seen.add(entry["id"])
        path = (root / entry["path"]).resolve()
        if not path.is_relative_to(root):
            raise ValueError("time input path escapes project root")
        actual_sha = sha(path)
        if actual_sha != entry["sha256"]:
            raise ValueError("registered input identity changed: " + entry["id"])
        axis = describe_time_axis(pd.read_csv(path), kind=entry["kind"],
                                  dt_hours=entry["dt_hours"],
                                  time_column=entry.get("time_column", "time"),
                                  period_column=entry.get("period_column"),
                                  weight_column=entry.get("weight_column", "period_weight"),
                                  expected_start=entry.get("expected_start"),
                                  expected_end=entry.get("expected_end"))
        rows.append({"id": entry["id"], "path": entry["path"], "sha256": actual_sha,
                     "role": entry["role"], "axis": axis})
    if not rows:
        raise ValueError("registry has no explicitly registered inputs")
    groups = {}
    for row in rows:
        groups.setdefault(row["axis"]["axis_sha256"], []).append(row["id"])
    return {"schema": "heatplan.time_registry.v1", "passed": True,
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "scope": "explicitly selected inputs only; not a claim that all historical entry points use one registry",
            "entries": rows, "identical_time_axes": list(groups.values()),
            "source_content_review_repeated": False,
            "interpretation": "Time identity does not certify common data, weather, prices, assets, sources, or optimality."}


def render(report):
    lines = ["# 实验时间轴登记", "", "记录原始文件 SHA、逐时顺序、物理步长和核算权重。相同时间轴只证明时间可对齐，不能代替数据、设备、经济条件或来源审核。", "",
             "| 条目 | 类型 | 采样小时 | 加权核算小时 | 连续运行小时 | 用途 |",
             "|---|---|---:|---:|---:|---|"]
    for row in report["entries"]:
        axis = row["axis"]
        lines.append(f'| {row["id"]} | {axis["kind"]} | {axis["sampled_interval_hours"]:g} | {axis["accounting_weighted_hours"]:g} | {axis["continuous_horizon_hours"] if axis["continuous_horizon_hours"] is not None else "不成立"} | {row["role"]} |')
    lines.extend(["", "2880 h 表示已登记的共同 120 天窗口，不自动等于三城完整法定采暖期。代表日加权小时不是连续动态仿真时长；72 h 管网试验不能代替 2880 h 运行校核。", "", "此登记覆盖清单中的输入；旧冻结脚本保持原状。详细 UTC 起止、每块时间范围与状态连续性见 registry.json。", ""])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise ValueError("output exists; use a new directory to retain previous evidence")
    report = build_registry(args.root, json.loads(args.spec.read_text(encoding="utf-8")))
    report["spec_sha256"] = sha(args.spec)
    report["implementation_sha256"] = {"scripts/build_time_registry.py": sha(__file__),
        "src/heatplan/time_registry.py": sha(ROOT / "src/heatplan/time_registry.py")}
    args.out.mkdir(parents=True)
    (args.out / "registry.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    (args.out / "README.md").write_text(render(report), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "registered_inputs": len(report["entries"]),
                      "distinct_axes": len(report["identical_time_axes"])}))


if __name__ == "__main__":
    main()
