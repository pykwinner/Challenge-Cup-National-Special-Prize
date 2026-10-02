"""Plot only frozen measured-data benchmark outputs; do not rerun selection."""
from pathlib import Path
from hashlib import sha256
import json
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "external_benchmark/versions/new_core/results.json"
rows = json.loads(SOURCE.read_text(encoding="utf-8"))
methods = ["ordinary", "extreme_aware", "extreme_calibrated"]
by_method = {m: {(r["case_id"], r["year"]): r for r in rows if r["method"] == m and r["status"] == "ok"} for m in methods}
common = sorted(set.intersection(*(set(d) for d in by_method.values())))
assert len(common) == 23
assert all(len({by_method[m][c]["metrics"]["actual_representative_days_m"] for m in methods}) == 1 for c in common)
font = FontProperties(fname="C:/Windows/Fonts/msyh.ttc")
plt.rcParams["font.family"] = font.get_name()
plt.rcParams["axes.unicode_minus"] = False
labels = ["普通代表日", "极端日保护", "极端日＋权重校准"]
specs = [
    ("retained_scope_abs_energy_bias", "能量绝对偏差中位数（越小越好）", True),
    ("peak_retention_ratio", "峰值保留率中位数（越大越好）", False),
    ("maximum_real_ramp_retention_ratio", "最大真实爬坡保留率中位数（越大越好）", False),
    ("ldc_w1_over_mean_load", "持续曲线 W1／平均负荷中位数（越小越好）", False),
]
fig, axes = plt.subplots(2, 2, figsize=(13, 8.5))
fig.patch.set_facecolor("#f8fafc")
metrics = {}
for ax, (key, title, log) in zip(axes.flat, specs):
    values = [float(np.median([by_method[m][c]["metrics"][key] for c in common])) * 100 for m in methods]
    metrics[key] = dict(zip(methods, values))
    ax.bar(labels, values, color=["#64748b", "#087f8c", "#d88b20"], width=.58, zorder=3)
    ax.set_title(title, fontsize=12, loc="left", pad=12)
    ax.set_ylabel("%" + ("（对数轴）" if log else ""))
    if log:
        ax.set_yscale("log")
        ax.set_ylim(.0001, 12)
    else:
        ax.set_ylim(0, max(values) * 1.27)
    for i, value in enumerate(values):
        ax.text(i, value * (1.35 if log else 1.04), f"{value:.6f}%" if log and value < .01 else f"{value:.2f}%", ha="center", fontsize=11)
    ax.grid(axis="y", color="#e2e8f0", zorder=0)
    ax.spines[["top", "right"]].set_visible(False)
fig.suptitle("BDG2 真实计量基准：极值保护与能量校准", fontsize=19, x=.055, ha="left")
fig.text(.055, .925, "同一组23个正能量案例年度；逐例使用相同数量的实际代表日；中位数按案例等权", fontsize=11, color="#475569")
fig.text(.055, .065, "范围：仅保留完整观测日。原24例中1例保留负荷全零，比例无定义且校准失败，原记录保留。", fontsize=10)
fig.text(.055, .035, "13例的原始观测峰值位于被筛除的日子；全有效计量小时的事后范围诊断另表报告。此图不证明工程运行可靠性。", fontsize=10)
fig.subplots_adjust(left=.07, right=.98, top=.855, bottom=.16, hspace=.46, wspace=.25)
fig.savefig(HERE / "benchmark_summary.png", dpi=180, facecolor=fig.get_facecolor())
fig.savefig(HERE / "benchmark_summary.pdf", facecolor=fig.get_facecolor())
plt.close(fig)
(HERE / "benchmark_summary_data.json").write_text(json.dumps({
    "input": SOURCE.relative_to(HERE).as_posix(), "input_sha256": sha256(SOURCE.read_bytes()).hexdigest(),
    "common_case_years": common, "case_count": len(common), "same_actual_day_budget_each_case": True,
    "display_percent_medians": metrics, "scope": "Retained complete observed days; paired positive-energy cases only."
}, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
