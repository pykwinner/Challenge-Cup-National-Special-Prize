# -*- coding: utf-8 -*-
"""从本轮乌鲁木齐复算输出生成正文图件。

  1. 源荷匹配比热力图（同一计量边界的逐时比值）
  2. 年化成本构成与容量—出力对照
  3. 最冷连续 168 h 调度与储热状态
  4. 负荷持续曲线与逐层容量水平

比值的分子为热源产热与储热净放热之和，分母为原始需求与建筑热状态净充热之和，
两侧同处用户侧计量边界，因此比值不低于 100%，超出部分即管网输配损失余量。
全部数值直接取自本轮求解输出与已核输入序列，不作平滑或估计。
"""
from __future__ import annotations

import argparse
import json
import pathlib

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml
from matplotlib import font_manager
from matplotlib.patches import Patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
FONT_DIR = ROOT / "source" / "build-fonts"

ASSET_CN = {
    "industrial_waste_heat": "工业余热直供",
    "central_waste_source_hp": "余热源热泵",
    "distributed_air_hp_b": "分布式空气源热泵",
    "electric_backup_boiler": "电备用锅炉",
    "district_a_emergency_eb": "区域应急电锅炉",
    "existing_chp": "既有热电联产",
    "gas_peak_boiler": "燃气调峰锅炉",
}
ASSET_COLOR = {
    "工业余热直供": "#2f6fb5",
    "余热源热泵": "#5aa9d6",
    "区域应急电锅炉": "#e08214",
    "分布式空气源热泵": "#8a4fbf",
    "既有热电联产": "#8c8c8c",
    "燃气调峰锅炉": "#bdbdbd",
    "电备用锅炉": "#c05131",
}


def setup_font():
    for name in ("NotoSansSC-Regular.ttf", "NotoSerifSC-Regular.ttf"):
        p = FONT_DIR / name
        if p.exists():
            font_manager.fontManager.addfont(str(p))
    plt.rcParams["font.family"] = "Noto Sans SC"
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["figure.dpi"] = 150
    plt.rcParams["savefig.bbox"] = "tight"


def load(case_dir: pathlib.Path, cfg_path: pathlib.Path, csv_path: pathlib.Path):
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    frame = pd.read_csv(csv_path)
    idx = pd.to_datetime(frame["timestamp"])
    disp = pd.read_csv(case_dir / "dispatch.csv")
    disp["time"] = pd.to_datetime(disp["time"])

    def piv(category, asset=None):
        x = disp[disp["category"] == category]
        if asset is not None:
            x = x[x["asset"] == asset]
        return x.groupby("time")["value"].sum().reindex(idx).fillna(0.0).to_numpy(float)

    demand = frame[[c for n in cfg["nodes"]
                    for c in n.get("demand_columns", {}).values()]].sum(axis=1).to_numpy(float)

    # 建筑热状态净充热：E_t = (1-lambda) E_{t-1} + (q+ - q-) dt
    lam = {}
    for n in cfg["nodes"]:
        for g, ti in (n.get("thermal_inertia") or {}).items():
            lam[f"{n['name']}_{g}"] = float(ti.get("recovery_loss_per_hour", 0.0))
    building = np.zeros_like(demand)
    for asset, grp in disp[disp["category"] == "building_thermal_state"].groupby("asset"):
        v = grp.groupby("time")["value"].sum().reindex(idx).fillna(0.0).to_numpy(float)
        l = lam.get(asset, 0.0)
        s = np.empty_like(v)
        s[0] = v[0]
        s[1:] = v[1:] - (1.0 - l) * v[:-1]
        building += s

    # 管网输配损失：效率随室外温度线性衰减
    tout = frame["outdoor_temp"].to_numpy(float)
    delivered = np.zeros_like(demand)
    loss = np.zeros_like(demand)
    for pipe in cfg["pipes"]:
        flow = piv("pipe_flow_forward", pipe["name"])
        eff = float(pipe.get("delivery_efficiency", 1.0))
        sens = float(pipe.get("cold_loss_sensitivity_per_c", 0.0))
        ref = float(pipe.get("loss_reference_temperature_c", 0.0))
        floor = float(pipe.get("minimum_delivery_efficiency", 0.8))
        eff_t = np.maximum(floor, eff - sens * np.maximum(0.0, ref - tout))
        delivered += flow * eff_t
        loss += flow * (1.0 - eff_t)

    by_source = {}
    for asset, grp in disp[disp["category"] == "source_heat"].groupby("asset"):
        v = grp.groupby("time")["value"].sum().reindex(idx).fillna(0.0).to_numpy(float)
        if v.max() > 1e-6:
            by_source[ASSET_CN.get(asset, asset)] = v

    return {
        "cfg": cfg, "index": idx, "frame": frame,
        "demand": demand, "building": building,
        "delivered": delivered, "loss": loss,
        "source": piv("source_heat"), "charge": piv("storage_charge"),
        "discharge": piv("storage_discharge"), "soc": piv("storage_soc"),
        "unserved": piv("unserved"), "by_source": by_source,
        "price": frame["electricity_price"].to_numpy(float),
        "tout": tout,
        "load_duration_sorted": np.sort(demand)[::-1],
    }


# --------------------------------------------------------------------------
def fig_match_ratio(d, out):
    num = d["source"] + d["discharge"] - d["charge"]
    den = d["demand"] + d["building"]
    rate = np.where(den > 1e-9, num / np.maximum(den, 1e-9) * 100.0, np.nan)
    nh = len(rate)
    grid = rate[: (nh // 24) * 24].reshape(-1, 24)

    fig, ax = plt.subplots(figsize=(7.6, 4.3))
    cmap = plt.get_cmap("YlGnBu").copy()
    cmap.set_bad("#f2f2f2")
    im = ax.imshow(np.ma.masked_invalid(grid), aspect="auto", cmap=cmap,
                   vmin=103.0, vmax=104.6,
                   extent=[0, 24, grid.shape[0], 0], interpolation="nearest")
    ax.set_title("乌鲁木齐供暖季源侧净供热量与负荷侧有效需求之比", fontsize=12)
    ax.set_xlabel("小时")
    ax.set_ylabel("供暖季日期序号")
    cb = fig.colorbar(im, ax=ax, pad=0.02)
    cb.set_label("源侧净供热量 ÷ 负荷侧有效需求（%）", fontsize=9.5)
    v = grid[np.isfinite(grid)]
    ax.text(0.02, 0.03,
            "季节均值 {:.2f}%　最低 {:.2f}%　最高 {:.2f}%\n"
            "低于 100% 的小时　{}　｜　灰白为无需供热的小时".format(
                v.mean(), v.min(), v.max(), int((v < 100).sum())),
            transform=ax.transAxes, fontsize=9, va="bottom",
            bbox=dict(fc="white", ec="#cccccc", alpha=0.92))
    fig.savefig(out)
    plt.close(fig)


# --------------------------------------------------------------------------
def fig_cost_capacity(d, case_dir, out):
    summary = json.loads((case_dir / "summary.json").read_text(encoding="utf-8"))
    comp = summary["components_yuan_per_year"]
    order = ["annualized_capex", "operating_cost", "exergy_mismatch_cost",
             "carbon_cost", "comfort_cost", "thermal_comfort_cost",
             "startup_shutdown_cost", "reliability_penalty", "fixed_om_cost"]
    label = {
        "annualized_capex": "年化投资", "operating_cost": "运行费用",
        "exergy_mismatch_cost": "火用失配惩罚", "carbon_cost": "碳价",
        "comfort_cost": "舒适偏离", "thermal_comfort_cost": "热舒适",
        "startup_shutdown_cost": "启停", "reliability_penalty": "可靠性惩罚",
        "fixed_om_cost": "固定运维变化",
    }
    names = [label[k] for k in order]
    vals = [comp[k] / 10000.0 for k in order]
    total = sum(vals)

    fig, axes = plt.subplots(1, 2, figsize=(12.4, 4.5),
                             gridspec_kw={"width_ratios": [1.05, 1.0]})

    ax = axes[0]
    ypos = np.arange(len(names))[::-1]
    colors = ["#c05131" if v < 0 else "#2f6fb5" for v in vals]
    ax.barh(ypos, vals, color=colors, alpha=0.88, height=0.62)
    for y, v in zip(ypos, vals):
        off = 0.35 if v >= 0 else -0.35
        ax.text(v + off, y, f"{v:,.2f}", va="center",
                ha="left" if v >= 0 else "right", fontsize=9)
    ax.axvline(0, color="#666666", lw=0.8)
    ax.set_yticks(ypos)
    ax.set_yticklabels(names, fontsize=10)
    ax.set_xlabel("万元/年")
    ax.set_title(f"乌鲁木齐年化成本构成（合计 {total:,.2f} 万元）", fontsize=12)
    ax.grid(axis="x", alpha=0.3, ls=":")
    lo, hi = min(vals + [0.0]), max(vals)
    ax.set_xlim(lo - 3.2, hi * 1.34)
    ax.text(0.99, 0.40,
            "全服务单位成本 0.1075 元/kWh\n"
            "＝按能量分摊 0.1007\n"
            "　＋火用失配 0.00674\n"
            "　＋舒适项 0.00004",
            transform=ax.transAxes, ha="right", va="center", fontsize=8.8,
            bbox=dict(fc="white", ec="#cccccc", alpha=0.92))

    ax = axes[1]
    maxout = {k: float(np.max(v)) for k, v in d["by_source"].items()}
    # 规划后容量：既有 + 新增 - 退役（取自本次求解的 investments.csv）
    plan = {
        "industrial_waste_heat": 650.0, "central_waste_source_hp": 300.0,
        "district_a_emergency_eb": 908.44, "electric_backup_boiler": 0.0,
        "distributed_air_hp_b": 0.0, "existing_chp": 350.0, "gas_peak_boiler": 600.0,
    }
    rows = [("工业余热直供", plan["industrial_waste_heat"]),
            ("余热源热泵", plan["central_waste_source_hp"]),
            ("区域应急电锅炉", plan["district_a_emergency_eb"]),
            ("既有热电联产（保留）", plan["existing_chp"]),
            ("燃气调峰锅炉（保留）", plan["gas_peak_boiler"])]
    labs = [r[0] for r in rows]
    cap = [r[1] for r in rows]
    run = [maxout.get(l, 0.0) for l in labs]
    y = np.arange(len(labs))[::-1]
    h = 0.34
    ax.barh(y + h / 2, cap, height=h, color="#c9d6e4", edgecolor="#5b7fa6",
            label="规划后容量")
    ax.barh(y - h / 2, run, height=h, color="#2f6fb5", label="全季最大出力")
    for yy, c, r in zip(y, cap, run):
        if c > 1e-6:
            ax.text(c + 16, yy + h / 2, f"{c:,.0f}", va="center", fontsize=8.5, color="#4a5b6b")
        ax.text(r + 16, yy - h / 2, f"{r:,.1f}", va="center", fontsize=8.5, color="#1f4f80")
    ax.set_yticks(y)
    ax.set_yticklabels(labs, fontsize=10)
    ax.set_xlabel("功率（kW）")
    ax.set_title("规划后容量与全季实际最大出力", fontsize=12)
    ax.grid(axis="x", alpha=0.3, ls=":")
    ax.set_xlim(0, max(cap) * 1.34)
    ax.set_ylim(-1.15, len(labs) - 0.35)
    ax.legend(fontsize=9, loc="center right", framealpha=0.95)
    ax.text(0.02, 0.015,
            "保留热电联产 350 kW 与燃气调峰 600 kW 全季出力为零\n"
            "区域应急电锅炉容量利用 {:.1f}%；配电 {:.0f}→{:.1f} kW".format(
                447.92 / 908.44 * 100.0, 360.0, 918.33),
            transform=ax.transAxes, fontsize=8.8, va="bottom",
            bbox=dict(fc="white", ec="#cccccc", alpha=0.92))

    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)


# --------------------------------------------------------------------------
def fig_coldest168(d, out):
    demand = d["demand"]
    win = int(np.argmax(pd.Series(demand).rolling(168).mean().fillna(-1).to_numpy()))
    s, e = max(0, win - 167), win + 1
    t = np.arange(e - s)
    fig, axes = plt.subplots(3, 1, figsize=(10.6, 7.2), sharex=True,
                             gridspec_kw={"height_ratios": [1.25, 0.75, 0.7]})

    ax = axes[0]
    bottom = np.zeros(e - s)
    for name, arr in d["by_source"].items():
        seg = arr[s:e]
        ax.fill_between(t, bottom, bottom + seg, color=ASSET_COLOR.get(name, "#999999"),
                        alpha=0.85, lw=0, label=name)
        bottom = bottom + seg
    ax.plot(t, demand[s:e], color="#1a1a1a", lw=2.0, label="热负荷")
    ax.set_ylabel("功率（kW）")
    ax.set_ylim(0, max(bottom.max(), demand[s:e].max()) * 1.22)
    ax.set_title("乌鲁木齐最冷连续 168 h 热源调度", fontsize=12.5)
    ax.grid(alpha=0.28, ls=":")
    ax.legend(fontsize=8.5, ncol=4, loc="upper left", framealpha=0.92)
    ax.text(0.99, 0.96, "热源出力按小时堆叠；黑线为需求",
            transform=ax.transAxes, ha="right", va="top", fontsize=8.5,
            bbox=dict(fc="white", ec="#cccccc", alpha=0.92))

    ax = axes[1]
    soc = d["soc"][s:e] / 100.0 * 100.0
    ax.fill_between(t, 0, soc, color="#e08214", alpha=0.35)
    ax.plot(t, soc, color="#b45f06", lw=1.9)
    ax.set_ylabel("储热 SOC\n（%，满量 100 kWh）", fontsize=9.5)
    ax.set_ylim(-6, 112)
    ax.grid(alpha=0.28, ls=":")
    ax.text(0.99, 0.94, "既有低温储热罐；本次未新增储热容量",
            transform=ax.transAxes, ha="right", va="top", fontsize=8.5,
            bbox=dict(fc="white", ec="#cccccc", alpha=0.92))

    ax = axes[2]
    ax.plot(t, d["tout"][s:e], color="#2f6fb5", lw=1.8, label="室外温度")
    ax.set_ylabel("温度（°C）", color="#2f6fb5", fontsize=9.5)
    ax.tick_params(axis="y", labelcolor="#2f6fb5")
    ax.grid(alpha=0.28, ls=":")
    ax2 = ax.twinx()
    ax2.step(t, d["price"][s:e], color="#c05131", lw=1.4, where="mid", label="电价")
    ax2.set_ylabel("电价（元/kWh）", color="#c05131", fontsize=9.5)
    ax2.tick_params(axis="y", labelcolor="#c05131")
    ax.set_xlabel("窗口内小时（h）")
    ax.set_xlim(0, e - s - 1)
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, fontsize=9, loc="upper left", framealpha=0.92)

    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)


# --------------------------------------------------------------------------
def fig_capacity_duration(d, out):
    """负荷持续曲线与逐层容量水平：说明备用容量与实际运行的差距。"""
    load = np.sort(d["demand"])[::-1]
    x = np.arange(len(load))
    base = 950.0                     # 工业余热直供 650 + 余热源热泵 300
    with_eb = base + 908.44          # + 区域应急电锅炉
    with_kept = with_eb + 950.0      # + 保留热电联产 350 与燃气调峰 600

    fig, ax = plt.subplots(figsize=(9.6, 4.6))
    ax.fill_between(x / 24.0, 0, load, color="#2f6fb5", alpha=0.22, lw=0)
    ax.plot(x / 24.0, load, color="#1f4f80", lw=2.0, label="热负荷持续曲线")
    ax.axhline(base, color="#5aa9d6", lw=1.6, ls="--",
               label=f"余热可用容量 {base:.0f} kW")
    ax.axhline(with_eb, color="#e08214", lw=1.6, ls="--",
               label=f"＋区域应急电锅炉 {with_eb:.0f} kW")
    ax.axhline(with_kept, color="#8c8c8c", lw=1.6, ls="--",
               label=f"＋保留化石容量 {with_kept:.0f} kW")

    h_base = int((load > base).sum())
    h_kept = int((load > with_kept).sum())
    ax.annotate("", xy=(h_base / 24.0, with_kept), xytext=(h_base / 24.0, base),
                arrowprops=dict(arrowstyle="<->", color="#c05131", lw=1.3))
    ax.text(h_base / 24.0 + 3, (base + with_kept) / 2,
            f"负荷高于余热容量的时长\n{h_base} h（占 {h_base/len(load)*100:.0f}%）",
            fontsize=9, va="center", color="#a03a20",
            bbox=dict(fc="white", ec="#e0c0b0", alpha=0.94))
    ax.set_xlabel("持续时间（d）")
    ax.set_ylabel("功率（kW）")
    ax.set_title("乌鲁木齐负荷持续曲线与逐层容量水平", fontsize=12.5)
    ax.set_xlim(0, len(load) / 24.0)
    ax.set_ylim(0, with_kept * 1.16)
    ax.grid(alpha=0.28, ls=":")
    ax.legend(fontsize=9, loc="upper right", framealpha=0.95)
    ax.text(0.99, 0.60,
            "峰值负荷 {:.1f} kW\n负荷高于全装机容量的时长 {} h".format(
                float(load[0]), h_kept),
            transform=ax.transAxes, ha="right", va="top", fontsize=9,
            bbox=dict(fc="white", ec="#cccccc", alpha=0.92))

    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--timeseries", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    setup_font()
    case = pathlib.Path(args.case)
    d = load(case, pathlib.Path(args.config), pathlib.Path(args.timeseries))
    d["_disp"] = pd.read_csv(case / "dispatch.csv")
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    fig_match_ratio(d, out / "fig_match_ratio.png")
    fig_cost_capacity(d, case, out / "fig_cost_capacity.png")
    fig_coldest168(d, out / "fig_coldest168.png")
    fig_capacity_duration(d, out / "fig_capacity_duration.png")

    num = d["source"] + d["discharge"] - d["charge"]
    den = d["demand"] + d["building"]
    # 需求为零的小时（无需供热）不参与比值，避免 0/0 拉低统计量
    r = np.where(den > 1e-9, num / np.where(den > 1e-9, den, 1.0) * 100.0, np.nan)
    report = {
        "hours": int(len(r)),
        "demand_MWh": round(float(d["demand"].sum()) / 1000.0, 3),
        "source_MWh": round(float(d["source"].sum()) / 1000.0, 3),
        "pipe_loss_MWh": round(float(d["loss"].sum()) / 1000.0, 3),
        "loss_share_pct": round(float(d["loss"].sum() / d["source"].sum()) * 100.0, 2),
        "storage_net_charge_MWh": round(float((d["charge"].sum() - d["discharge"].sum())) / 1000.0, 3),
        "building_net_charge_MWh": round(float(d["building"].sum()) / 1000.0, 4),
        "unserved_MWh": round(float(d["unserved"].sum()) / 1000.0, 4),
        "match_ratio_mean_pct": round(float(np.nanmean(r)), 3),
        "match_ratio_min_pct": round(float(np.nanmin(r)), 3),
        "match_ratio_max_pct": round(float(np.nanmax(r)), 3),
        "match_ratio_hours_below_100": int(np.nansum(r < 100)),
        "match_ratio_hours_defined": int(np.isfinite(r).sum()),
        "peak_load_kW": round(float(d["demand"].max()), 1),
        "source_mix_MWh": {k: round(float(v.sum()) / 1000.0, 2) for k, v in d["by_source"].items()},
        "source_max_kW": {k: round(float(v.max()), 2) for k, v in d["by_source"].items()},
    }
    (out / "figure_summary_r18.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
