# -*- coding: utf-8 -*-
"""从本轮复算的 dispatch.csv 生成三张评分点名图：

  1. 负荷持续曲线（三城）
  2. 源荷匹配热力图（三城）
  3. 系统能流图（三城，季节能量按来源类别）

全部数值直接取自本轮求解输出与已核输入序列，不作任何平滑或估计。
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
from matplotlib import font_manager

FONT_DIR = pathlib.Path(__file__).resolve().parents[1] / "source" / "build-fonts"

CITY = {
    "scenario1_urumqi": ("乌鲁木齐", "#2f6fb5"),
    "scenario2_guyuan": ("固原", "#2e8b57"),
    "scenario3_wuhan": ("武汉", "#8a4fbf"),
}


def setup_font():
    for name in ("NotoSansSC-Regular.ttf", "NotoSerifSC-Regular.ttf"):
        p = FONT_DIR / name
        if p.exists():
            font_manager.fontManager.addfont(str(p))
    plt.rcParams["font.family"] = "Noto Sans SC"
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["figure.dpi"] = 130
    plt.rcParams["savefig.bbox"] = "tight"


def load(profile_dir: pathlib.Path, csv_path: pathlib.Path, config_path: pathlib.Path):
    disp = pd.read_csv(profile_dir / "dispatch.csv")
    frame = pd.read_csv(csv_path)
    import yaml
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    cols = [c for node in cfg["nodes"] for c in node.get("demand_columns", {}).values()]
    demand = frame[cols].sum(axis=1).to_numpy(dtype=float)
    disp["time"] = pd.to_datetime(disp["time"])
    return disp, demand, frame


def pivot(disp, category):
    g = disp[disp["category"] == category].groupby("time")["value"].sum()
    return g


def fig_load_duration(series, out):
    n = len(series)
    fig, axes = plt.subplots(1, n, figsize=(5.0 * n, 4.2), squeeze=False)
    axes = axes[0]
    for ax, (profile, d) in zip(axes, series.items()):
        city, color = CITY[profile]
        load = np.sort(d["demand"])[::-1]
        ax.plot(np.arange(len(load)) / 24.0, load / 1000.0, color=color, lw=2)
        ax.fill_between(np.arange(len(load)) / 24.0, load / 1000.0, color=color, alpha=0.15)
        peak = load[0]
        eqh = load.sum() / peak / len(load) * len(load) / 24.0
        ax.set_title(f"{city}·负荷持续曲线（{len(load)} h）", fontsize=12)
        ax.set_xlabel("累计天数（d）")
        ax.set_ylabel("热负荷（MW）")
        ax.grid(alpha=0.3, ls=":")
        ax.text(0.97, 0.95,
                f"峰值 {peak:.0f} kW\n等效满负荷 {load.sum()/peak:.0f} h",
                transform=ax.transAxes, ha="right", va="top", fontsize=9,
                bbox=dict(fc="white", ec="#cccccc", alpha=0.9))
    if n > 1:
        fig.suptitle("三城主算例供暖季负荷持续曲线", fontsize=13, y=1.02)
    fig.savefig(out)
    plt.close(fig)


def fig_match_heatmap(series, out):
    nprof = len(series)
    fig, axes = plt.subplots(1, nprof, figsize=(5.0 * nprof, 4.6), squeeze=False)
    axes = axes[0]
    for ax, (profile, d) in zip(axes, series.items()):
        city, color = CITY[profile]
        nh = len(d["demand"])
        days = nh // 24
        rate = np.full(nh, np.nan)
        m = d["demand"] > 1e-9
        rate[m] = (d["supply"][m] + d["storage_net"][m] + d["unserved"][m]) / d["demand"][m] * 100.0
        grid = rate[: days * 24].reshape(days, 24)
        from matplotlib.colors import TwoSlopeNorm
        im = ax.imshow(grid, aspect="auto", cmap="RdYlGn",
                       norm=TwoSlopeNorm(vcenter=100.0, vmin=95.0, vmax=110.0),
                       extent=[0, 24, days, 0], interpolation="nearest")
        ax.set_title(f"{city}·源荷匹配比热力图", fontsize=12)
        ax.set_xlabel("小时")
        ax.set_ylabel("供暖季日期序号")
        cb = fig.colorbar(im, ax=ax, pad=0.02)
        cb.set_label("可供热量/需求热量（%）", fontsize=9)
        valid = grid[np.isfinite(grid)]
        ax.text(0.02, 0.02,
                "季节均值 {:.2f}%\n最低 {:.2f}%  低于100%的小时 {}".format(
                    valid.mean(), valid.min(), int((valid < 100).sum())),
                transform=ax.transAxes, fontsize=8.5, va="bottom",
                bbox=dict(fc="white", ec="#cccccc", alpha=0.9))
    if nprof > 1:
        fig.suptitle("三城主算例逐时源荷匹配比（可供热量 ÷ 需求热量）", fontsize=12, y=1.03)
    fig.savefig(out)
    plt.close(fig)


def fig_energy_flow(series, out):
    n = len(series)
    fig, axes = plt.subplots(1, n, figsize=(5.0 * n, 4.2), squeeze=False)
    axes = axes[0]
    for ax, (profile, d) in zip(axes, series.items()):
        city, color = CITY[profile]
        parts = {k: float(np.sum(v)) for k, v in d["by_source"].items() if np.sum(v) > 1e-6}
        if not parts:
            parts = {"无热源出力": 0.0}
        labels = list(parts.keys())
        vals = np.array([parts[k] for k in labels]) / 1000.0
        ypos = np.arange(len(labels))[::-1]
        ax.barh(ypos, vals, color=color, alpha=0.85, height=0.6)
        for y, v in zip(ypos, vals):
            ax.text(v * 1.01, y, f"{v:,.1f}", va="center", fontsize=9)
        ax.set_yticks(ypos)
        ax.set_yticklabels(labels, fontsize=9)
        ax.set_xlabel("季节供热量（MWh）")
        ax.set_title(f"{city}·热源结构", fontsize=12)
        ax.grid(axis="x", alpha=0.3, ls=":")
        ax.set_xlim(0, max(vals) * 1.18 if vals.max() > 0 else 1)
        ax.text(0.98, 0.05,
                f"需求 {d['demand'].sum()/1000:,.0f} MWh\n失供 {d['unserved'].sum()/1000:,.2f} MWh",
                transform=ax.transAxes, ha="right", va="bottom", fontsize=8.5,
                bbox=dict(fc="white", ec="#cccccc", alpha=0.9))
    if n > 1:
        fig.suptitle("三城主算例季节热量来源结构", fontsize=13, y=1.02)
    fig.savefig(out)
    plt.close(fig)


ASSET_CN = {
    "industrial_waste_heat": "工业余热直供",
    "central_waste_source_hp": "余热源热泵",
    "distributed_air_hp_b": "分布式空气源热泵",
    "electric_backup_boiler": "电备用锅炉",
    "district_a_emergency_eb": "区域应急电锅炉",
    "existing_chp": "既有热电联产",
    "gas_peak_boiler": "燃气调峰锅炉",
    "central_low_temp_tank": "集中低温储热",
    "district_a_pcm": "相变储热",
}


def collect(root: pathlib.Path, cfg_root: pathlib.Path, inputs: pathlib.Path):
    out = {}
    for profile in CITY:
        pdir = root / profile
        if not (pdir / "dispatch.csv").exists():
            continue
        csv = inputs / f"{profile}_heating_season.csv"
        cfg = cfg_root / f"{profile}.yaml"
        disp, demand, frame = load(pdir, csv, cfg)
        supply = pivot(disp, "source_heat")
        charge = pivot(disp, "storage_charge")
        discharge = pivot(disp, "storage_discharge")
        unserved = pivot(disp, "unserved")
        idx = pd.to_datetime(frame["timestamp"]) if "timestamp" in frame else None
        n = len(demand)
        def arr(s):
            if idx is None:
                return np.zeros(n)
            return s.reindex(idx).fillna(0.0).to_numpy(dtype=float)[:n]
        sup = arr(supply)
        net = arr(discharge) - arr(charge)
        uns = arr(unserved)
        sup = np.pad(sup, (0, max(0, n - len(sup))))[:n]
        net = np.pad(net, (0, max(0, n - len(net))))[:n]
        uns = np.pad(uns, (0, max(0, n - len(uns))))[:n]
        by_source = {}
        for asset, g in disp[disp["category"] == "source_heat"].groupby("asset"):
            v = g.groupby("time")["value"].sum()
            by_source[ASSET_CN.get(asset, asset)] = arr(v)[:n]
        out[profile] = {"demand": demand, "supply": sup, "storage_net": net,
                        "unserved": uns, "by_source": by_source}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="unified_cli 输出根目录")
    ap.add_argument("--out", required=True)
    ap.add_argument("--legacy", required=True, help="legacy_three_city_season 目录")
    args = ap.parse_args()

    setup_font()
    root = pathlib.Path(args.root)
    legacy = pathlib.Path(args.legacy)
    series = collect(root, legacy / "configs", legacy)
    if not series:
        raise SystemExit("未找到 dispatch.csv")
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    fig_load_duration(series, out / "fig_load_duration.png")
    fig_match_heatmap(series, out / "fig_source_load_heatmap.png")
    fig_energy_flow(series, out / "fig_energy_flow.png")

    report = {}
    for profile, d in series.items():
        m = d["demand"] > 1e-9
        rate = (d["supply"][m] + d["storage_net"][m] + d["unserved"][m]) / d["demand"][m] * 100.0
        report[profile] = {
            "hours": int(len(d["demand"])),
            "demand_MWh": round(float(d["demand"].sum()) / 1000.0, 2),
            "supply_MWh": round(float(d["supply"].sum()) / 1000.0, 2),
            "unserved_MWh": round(float(d["unserved"].sum()) / 1000.0, 4),
            "match_rate_mean_pct": round(float(rate.mean()), 4),
            "match_rate_min_pct": round(float(rate.min()), 4),
            "peak_load_kW": round(float(d["demand"].max()), 2),
            "source_mix_MWh": {k: round(float(np.sum(v)) / 1000.0, 2) for k, v in d["by_source"].items()},
        }
    (out / "figure_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
