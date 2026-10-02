from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
QUALITY = ROOT / "outputs" / "v11_6_multiyear_representative_days" / "representative_quality_summary.csv"
BASELINE = ROOT / "outputs" / "v11_6_multiyear_readiness" / "multiyear_readiness_summary.csv"
ROBUST = ROOT / "outputs" / "v11_6_multiyear_readiness_robust" / "multiyear_readiness_summary.csv"
REPLAY = ROOT / "outputs" / "v11_6_multiyear_formal_replay" / "multiyear_replay_summary.csv"
OUT = ROOT / "outputs" / "v11_6_figures"

LABELS = {
    "scenario1_urumqi": "S1 乌鲁木齐",
    "scenario2_guyuan": "S2 固原",
    "scenario3_wuhan": "S3 武汉",
}
COLORS = {
    "scenario1_urumqi": "#2563EB",
    "scenario2_guyuan": "#0F9D7A",
    "scenario3_wuhan": "#7C3AED",
}


def main() -> None:
    for path in (QUALITY, BASELINE, ROBUST, REPLAY):
        if not path.exists():
            raise FileNotFoundError(path)
    quality = pd.read_csv(QUALITY)
    baseline = pd.read_csv(BASELINE).set_index("profile")
    robust = pd.read_csv(ROBUST).set_index("profile")
    replay = pd.read_csv(REPLAY)
    replay["case"] = replay.apply(
        lambda row: f"{LABELS[row['profile']]} {int(row['weather_year'])}", axis=1
    )

    plt.rcParams["font.sans-serif"] = [
        "Microsoft YaHei", "Noto Sans CJK SC", "SimHei", "Arial"
    ]
    plt.rcParams["axes.unicode_minus"] = False
    fig, axes = plt.subplots(2, 2, figsize=(15, 9), facecolor="#F5F7FA")
    fig.subplots_adjust(left=0.08, right=0.97, top=0.88, bottom=0.09, wspace=0.28, hspace=0.42)
    fig.suptitle("V11.6 多气象年鲁棒规划与极端年正式回放", fontsize=22, fontweight="bold", color="#17324D")
    fig.text(0.08, 0.915, "2020—2024 NASA POWER逐时气象 · 同一投资方案跨年审计 · 7个极端年8760/8784小时复算", fontsize=12.5, color="#64748B")

    profiles = quality["profile"].tolist()
    x = np.arange(len(profiles))
    ax = axes[0, 0]
    before = quality["review_count_before_across_years"].to_numpy()
    after = quality["review_count_after_across_years"].to_numpy()
    ax.bar(x - 0.18, before, width=0.36, color="#CBD5E1", label="2023单年代表集")
    ax.bar(x + 0.18, after, width=0.36, color="#0F9D7A", label="五年联合代表集")
    ax.set_xticks(x, [LABELS[p] for p in profiles])
    ax.set_ylabel("跨年覆盖告警数")
    ax.set_title("代表时段覆盖告警全部清零", loc="left", fontweight="bold")
    ax.legend(frameon=False, ncols=2, fontsize=10)
    for i, (b, a) in enumerate(zip(before, after)):
        ax.text(i - 0.18, b + 0.25, str(int(b)), ha="center", color="#475569")
        ax.text(i + 0.18, a + 0.25, str(int(a)), ha="center", color="#0F9D7A")

    ax = axes[0, 1]
    base_pass = baseline.loc[profiles, "readiness_pass_years"].to_numpy()
    robust_pass = robust.loc[profiles, "readiness_pass_years"].to_numpy()
    ax.barh(x + 0.18, base_pass, height=0.34, color="#CBD5E1", label="2023方案")
    ax.barh(x - 0.18, robust_pass, height=0.34, color=[COLORS[p] for p in profiles], label="五年鲁棒方案")
    ax.set_yticks(x, [LABELS[p] for p in profiles])
    ax.set_xlim(0, 5.5)
    ax.set_xlabel("通过容量预审的气象年数（共5年）")
    ax.set_title("15个场景×年份组合全部通过", loc="left", fontweight="bold")
    ax.legend(frameon=False, fontsize=10)
    for i, value in enumerate(robust_pass):
        ax.text(value + 0.08, i - 0.18, f"{int(value)}/5", va="center", color="#17324D")

    ax = axes[1, 0]
    ordered = replay.sort_values(["profile", "weather_year"]).reset_index(drop=True)
    y = np.arange(len(ordered))
    bars = ax.barh(y, ordered["replay_solve_seconds"], color=[COLORS[p] for p in ordered["profile"]], height=0.58)
    ax.set_yticks(y, ordered["case"])
    ax.invert_yaxis()
    ax.set_xscale("log")
    ax.set_xlabel("求解时间（秒，对数坐标）")
    ax.set_title("7个极端年全部取得验收证书", loc="left", fontweight="bold")
    for bar, value in zip(bars, ordered["replay_solve_seconds"]):
        ax.text(value * 1.05, bar.get_y() + bar.get_height() / 2, f"{value:.1f}s", va="center", fontsize=9.5)

    ax = axes[1, 1]
    for profile, block in replay.groupby("profile"):
        ax.scatter(
            block["levelized_heat_cost_yuan_per_kwh"],
            block["emissions_kg_per_year"] / 1000,
            s=72,
            color=COLORS[profile],
            label=LABELS[profile],
            edgecolor="white",
            linewidth=0.8,
        )
        for _, row in block.iterrows():
            ax.annotate(str(int(row["weather_year"])), (row["levelized_heat_cost_yuan_per_kwh"], row["emissions_kg_per_year"] / 1000), xytext=(5, 4), textcoords="offset points", fontsize=9)
    ax.set_xlabel("平准供热成本（元/kWh）")
    ax.set_ylabel("年度排放（tCO2e代理）")
    ax.set_title("跨年成本—排放结果保持可解释", loc="left", fontweight="bold")
    ax.legend(frameon=False, fontsize=9)

    for ax in axes.ravel():
        ax.set_facecolor("#FFFFFF")
        ax.grid(axis="x", alpha=0.16)
        for spine in ax.spines.values():
            spine.set_color("#D8E1E8")
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / "multiyear_robustness_dashboard.png", dpi=220, facecolor=fig.get_facecolor())
    fig.savefig(OUT / "multiyear_robustness_dashboard.svg", facecolor=fig.get_facecolor())
    plt.close(fig)


if __name__ == "__main__":
    main()
