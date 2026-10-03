"""乌鲁木齐工业余热共同预算额度扫描（只读原配置，另建派生配置）。

动机：`data/example_city.yaml` 的 `industrial_waste_heat`（直接换热）与
`central_waste_source_hp`（余热源热泵）共用同一套输入序列
（`waste_heat_availability` 与 `waste_source_temp`，同节点 plant、同
resource_kind），但主算例没有启用 `shared_heat_resources` 共同预算，
两条路径可以各自独立取用同一股余热。

仓库没有资源侧可用热功率（kW）台账，因此不虚构该物理量，改为按模型自身
的解定基准：先用一个足够大的额度求解得到「无约束上游取热」，再以该峰值的
若干档作为额度下限做单因素扫描，回答「原结果是否依赖重复取热」。

口径声明：
- `transfer_efficiency` 取 1.0（无损），是使约束最弱的取值。若在该取值下
  结果已随额度变化，则变化是真实的，不是效率假设造成的。
- 热泵上游取热按模型定义 Q_HP*(1-1/COP)。
- 本扫描是机制压力测试，额度是研究设计参数，不是已核验的城市资源量。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
BASE_CONFIG = ROOT / "data" / "public_scenario_inputs" / "scenario1_urumqi_deterministic_public.yaml"
REP = ROOT / "data" / "public_scenario_inputs" / "scenario1_urumqi_representative_days_2023.csv"
ANNUAL = ROOT / "data" / "public_scenario_inputs" / "scenario1_urumqi_full_year_2023.csv"

POOL_NAME = "plant_industrial_waste_stream"
POOL_MEMBERS = ["industrial_waste_heat", "central_waste_source_hp"]


def write_variant(tag: str, available_heat_kw: float | None) -> Path:
    """派生配置：只加 shared_heat_resources 一节，其余全部继承原配置。"""
    body: dict = {"extends": str(BASE_CONFIG).replace("\\", "/")}
    if available_heat_kw is not None:
        body["shared_heat_resources"] = [
            {
                "name": POOL_NAME,
                "source_names": list(POOL_MEMBERS),
                "transfer_efficiency": 1.0,
                "available_heat_kw": available_heat_kw,
            }
        ]
    path = HERE / f"config_{tag}.yaml"
    path.write_text(yaml.safe_dump(body, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def write_manifest(tag: str, config: Path) -> Path:
    rel = lambda p: __import__("os").path.relpath(p, HERE).replace("\\", "/")
    manifest = {
        "profiles": [
            {
                "name": f"urumqi_pool_{tag}",
                "config": rel(config),
                "representative_timeseries": rel(REP),
                "annual_timeseries": rel(ANNUAL),
                "timestamp_column": "time",
            }
        ]
    }
    path = HERE / f"manifest_{tag}.yaml"
    path.write_text(yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def run(tag: str, available_heat_kw: float | None) -> dict:
    config = write_variant(tag, available_heat_kw)
    manifest = write_manifest(tag, config)
    out = HERE / f"run_{tag}"
    cmd = [
        sys.executable, "-m", "heatplan.annual_replay_batch_cli",
        "--manifest", str(manifest), "--output", str(out),
    ]
    env = {"PYTHONPATH": str(ROOT / "src"), **dict(__import__("os").environ)}
    proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    (HERE / f"log_{tag}.txt").write_text((proc.stdout or "") + (proc.stderr or ""), encoding="utf-8")

    record = {"tag": tag, "available_heat_kw": available_heat_kw, "return_code": proc.returncode}
    profile_dir = out / f"urumqi_pool_{tag}"
    summ = profile_dir / "annual_replay_summary.json"
    if summ.exists():
        d = json.loads(summ.read_text(encoding="utf-8"))
        record.update({
            "planning_solver_status": d.get("planning_solver_status"),
            "replay_solver_status": d.get("replay_solver_status"),
            "planning_solve_seconds": d.get("planning_solve_seconds"),
            "replay_solve_seconds": d.get("replay_solve_seconds"),
            "replay_unserved_rate": d.get("replay_unserved_rate"),
            "annual_replay_certified": d.get("annual_replay_certified"),
            "acceptance_failed_gates": d.get("acceptance_failed_gates"),
        })
        plan = profile_dir / "representative_planning" / "summary.json"
        rep = profile_dir / "fixed_investment_annual_replay" / "summary.json"
        for label, p in (("planning", plan), ("replay", rep)):
            if p.exists():
                s = json.loads(p.read_text(encoding="utf-8"))
                record[f"{label}_objective"] = s.get("objective_value")
                record[f"{label}_dual_bound"] = s.get("mip_dual_bound")
                record[f"{label}_gap"] = s.get("mip_gap")
                record[f"{label}_emissions_kg"] = s.get("emissions_kg_per_year")
                record[f"{label}_electricity_kwh"] = s.get("electricity_kwh_per_year")
                if "shared_heat_resource_max_overdraw_kw" in s:
                    record[f"{label}_pool_max_overdraw_kw"] = s["shared_heat_resource_max_overdraw_kw"]
    peak = peak_pool_use(profile_dir)
    if peak is not None:
        record["observed_peak_upstream_kw"] = peak
    (HERE / f"result_{tag}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


def peak_pool_use(profile_dir: Path) -> float | None:
    """从调度表读上游取热峰值（模型导出的 shared_heat_resource_use）。"""
    import csv
    for stage in ("fixed_investment_annual_replay", "representative_planning"):
        p = profile_dir / stage / "dispatch.csv"
        if not p.exists():
            continue
        best = None
        with p.open(encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                if row.get("category") == "shared_heat_resource_use":
                    v = float(row["value"])
                    best = v if best is None else max(best, v)
        if best is not None:
            return best
    return None


def main() -> None:
    results = []
    unbounded = run("unbounded", 1_000_000.0)
    results.append(unbounded)
    peak = unbounded.get("observed_peak_upstream_kw")
    print(f"无约束上游取热峰值 = {peak} kW")
    if peak:
        for fraction in (1.0, 0.85, 0.7, 0.55, 0.4):
            tag = f"a{int(round(fraction * 100))}"
            results.append(run(tag, peak * fraction))
        # 一个绝对下限参照：直接换热设备的既有铭牌容量
        results.append(run("a650", 650.0))
    (HERE / "sweep_results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
