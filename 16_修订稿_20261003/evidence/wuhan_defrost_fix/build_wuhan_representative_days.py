"""武汉代表时段补充锚点日（供冷峰值日 + 除霜最不利日）。

背景
----
现用的 `scenario3_wuhan_representative_days_2023.csv` 只有 3 个锚点日
（2023-12-21 系统/负荷峰值、2023-12-22 最低温、2023-08-31 系统爬坡），
簇的 medoid 与权重能和 `cluster_typical_days` 逐位对上，但锚点规则与当前
`scenarios.extreme_aware_typical_days` 不同，说明它出自更早版本的锚点规则。

按当前生成器的锚点规则清点，缺失的锚点日有三个：

  2023-01-31  maximum_ramp::demand_a_low / demand_b_low（采暖负荷最大爬坡）
  2023-08-17  maximum_peak::cooling_demand_a / cooling_demand_b（供冷峰值）
  2023-11-05  minimum_availability::solar_heat_availability（太阳可用率最低）

本次只补会造成实际约束的锚点：
  - 2023-08-17：冻结方案的供冷上限 district_a=394.00 kW、district_b=350.53 kW，
    当日供冷需求 483.60 / 379.85 kW，全年有 34 h / 6 h 超限 —— 全年回放因此
    不可行（约束族消融：关掉供冷服务后同一模型即可行）。
  - 2023-12-19：除霜最不利小时所在日（T=-1.78 ℃、RH=0.977，落在除霜带
    [-2,7] ℃ 内），当前生成器也不锚定，需额外锚点列；缺它会使并网接入容量
    比全年可靠电力需求低 0.1058 kW，就绪审计拒绝放行。
  2023-01-31 与 2023-11-05 不补，理由是**可证不产生约束**：冻结配置中没有任何
  热源带 unit_commitment / 爬坡 / 最小开停参数；两台太阳能集热容量均为 0。

锚点语义
--------
沿用 `extreme_aware_typical_days` 的规则：新增锚点日从**它自己所属簇**的普通日
里扣掉一次出现，使代表时段总天数保持不变（本案例 365 天）。不这样做会让同一天
既作为独立锚点、又被原簇权重重复计入。

原始文件不修改。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
HERE = Path(__file__).resolve().parent

from heatplan.io import load_config  # noqa: E402
from heatplan.scenarios import cluster_typical_days  # noqa: E402

REP = ROOT / "data" / "public_scenario_inputs" / "scenario3_wuhan_representative_days_2023.csv"
ANNUAL = ROOT / "data" / "public_scenario_inputs" / "scenario3_wuhan_full_year_2023.csv"
CONFIG = ROOT / "data" / "public_scenario_inputs" / "scenario3_wuhan_joint_public.yaml"

ANCHORS = [
    ("2023-08-17", "cooling_peak_anchor", "供冷峰值日（当前生成器的 maximum_peak::cooling_demand_* 锚点）"),
    ("2023-01-31", "heating_ramp_anchor", "采暖负荷最大爬坡日（当前生成器的 maximum_ramp::demand_* 锚点）"),
    ("2023-12-19", "defrost_anchor", "除霜最不利日（生成器无此规则，需额外锚点列）"),
]
N_CLUSTERS = 6
RANDOM_STATE = 42


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cluster_labels(annual: pd.DataFrame, config: dict) -> tuple[list[int], list[int]]:
    demand = [c for node in config["nodes"] for c in node.get("demand_columns", {}).values()]
    service = list(demand) + [c for c in ("cooling_demand_a", "cooling_demand_b") if c in annual.columns]
    features = ["outdoor_temp", *service]
    for optional in ("relative_humidity", "solar_heat_availability",
                     "waste_heat_availability", "electricity_price"):
        if optional in annual.columns:
            features.append(optional)
    medoids, _weights, labels = cluster_typical_days(
        annual, list(dict.fromkeys(features)), N_CLUSTERS, RANDOM_STATE)
    return medoids, labels


def main() -> None:
    config = load_config(CONFIG)
    rep = pd.read_csv(REP)
    annual = pd.read_csv(ANNUAL)
    annual_no_ts = annual.drop(columns=[c for c in ("timestamp",) if c in annual.columns])

    medoids, labels = cluster_labels(annual_no_ts, config)
    medoid_dates = {i: annual_no_ts["time"].iloc[m * 24][:10] for i, m in enumerate(medoids)}

    # 现用文件里每个簇块的权重，以及它们对应的 medoid 日期
    block_weight = {}
    for name, group in rep.groupby("representative_day", sort=False):
        block_weight[name] = float(group["period_weight"].iloc[0])

    total_before = float(rep["period_weight"].sum())
    existing_days = set(rep["original_time"].astype(str).str[:10])
    patched = rep.copy()
    applied = []

    for day_text, label, why in ANCHORS:
        if day_text in existing_days:
            raise SystemExit(f"{day_text} 已在代表时段中，锚点定义有误")
        day_index = annual_no_ts.index[annual_no_ts["time"].astype(str).str.startswith(day_text)][0] // 24
        cluster = int(labels[day_index])
        host = medoid_dates[cluster]
        host_block = f"cluster_{cluster}"
        if host_block not in block_weight or host_block not in set(patched["representative_day"]):
            raise SystemExit(f"找不到 {host_block} 块，无法按原簇扣减权重")
        before = float(patched.loc[patched["representative_day"] == host_block, "period_weight"].iloc[0])
        patched.loc[patched["representative_day"] == host_block, "period_weight"] = before - 1.0

        rows = annual[annual["time"].astype(str).str.startswith(day_text)].copy()
        if len(rows) != 24:
            raise SystemExit(f"{day_text} 不是完整日（{len(rows)} 行）")
        source_time = rows["time"].astype(str).tolist()
        rows["time"] = [f"rep_{90 + len(applied):02d}_{v}" for v in source_time]
        rows["period_weight"] = 1.0
        rows["representative_day"] = label
        rows["is_extreme_day"] = True
        rows["boundary_group"] = label
        rows["timestamp"] = source_time
        rows["original_time"] = source_time
        patched = pd.concat([patched, rows], ignore_index=True)
        applied.append({
            "anchor_day": day_text, "label": label, "why": why,
            "host_cluster": host_block, "host_medoid_day": host,
            "host_weight": {"before": before, "after": before - 1.0},
        })

    total_after = float(patched["period_weight"].sum())
    if abs(total_after - total_before) > 1e-9:
        raise SystemExit(f"总权重改变了：{total_before} -> {total_after}")
    # 每个日期只能出现在一个块里；每天 24 行不是重复。
    per_day_blocks = (patched.assign(_day=patched["original_time"].astype(str).str[:10])
                      .groupby("_day")["representative_day"].nunique())
    dup = per_day_blocks[per_day_blocks > 1]
    if len(dup):
        raise SystemExit(f"同一日期出现在多个块中，锚点与簇块重叠：{dup.to_dict()}")
    if patched.groupby("representative_day").size().ne(24).any():
        raise SystemExit("存在非 24 小时的块")

    out = HERE / "scenario3_wuhan_representative_days_2023_anchored.csv"
    patched.to_csv(out, index=False)

    record = {
        "change": "武汉代表时段补充锚点日（供冷峰值 + 除霜最不利）",
        "generator": {
            "module": "heatplan.scenarios.extreme_aware_typical_days",
            "clustering": "heatplan.scenarios.cluster_typical_days",
            "n_clusters": N_CLUSTERS, "random_state": RANDOM_STATE,
        },
        "inputs": {
            "annual": {"path": str(ANNUAL.relative_to(ROOT)), "sha256": sha256(ANNUAL)},
            "original_representative": {"path": str(REP.relative_to(ROOT)), "sha256": sha256(REP)},
            "config": {"path": str(CONFIG.relative_to(ROOT)), "sha256": sha256(CONFIG)},
        },
        "anchors_applied": applied,
        "anchors_not_applied": [
            {"anchor_day": "2023-11-05", "reason": "minimum_availability::solar_heat_availability；两台太阳能集热容量均为 0，该锚点不产生约束"},
        ],
        "total_weight": {"before": total_before, "after": total_after},
        "days_before": int(rep["representative_day"].nunique()),
        "days_after": int(patched["representative_day"].nunique()),
        "original_file_untouched": str(REP),
        "output": str(out),
        "output_sha256": sha256(out),
    }
    (HERE / "anchored_representative_record.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(record, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
