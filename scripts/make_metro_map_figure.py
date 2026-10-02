# -*- coding: utf-8 -*-
"""热能换乘站：把源网荷储协同决策画成一张城市轨道交通线路图。

数据源（车票数字全部实取，无人工录入）：
  docs/implementation_20261002_followup/parallel_stage_03/investment/run_03/
    decision_table.csv                  —— 四方案 capex / 窗口电费 / 碳排 / 电量
    compromise/effective_protocol.json  —— 窗口与身份声明
    manifest.json                       —— 审计规模

视觉：配色与字体照现稿（暖米黄底、深青主色、炉火橙、管道蓝灰、橄榄绿、Noto Sans SC）。
口径：rc6 研究候选；公开证据 + 冻结假设设计轴，不含具体设备性能准入；
      窗口 2880 小时，武汉为程序验证情景，不代表实测工程效果。
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, FancyBboxPatch, Rectangle

ROOT = Path(__file__).resolve().parents[1]
INV = (ROOT / "docs" / "implementation_20261002_followup" / "parallel_stage_03"
       / "investment" / "run_03")
OUT = ROOT / "outputs" / "figures"
OUT.mkdir(parents=True, exist_ok=True)

plt.rcParams["font.sans-serif"] = ["Noto Sans SC", "Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

BG = "#FBF7EE"          # 暖米黄底（现稿内页）
CARD = "#FFFFFF"        # 站点与卡片底色
INK = "#173F42"         # 深青正文
MUTED = "#7C8F91"       # 灰青说明
C_LOAD = "#1E6B6E"      # 荷：现稿主色深青
C_SRC = "#EF8F3C"       # 源：现稿封面炉火橙
C_NET = "#6E9BB0"       # 网：现稿封面管道蓝灰
C_STO = "#7FA05C"       # 储：现稿内页橄榄绿
COL = {"load": C_LOAD, "src": C_SRC, "net": C_NET, "sto": C_STO}
WID = {"load": 3.6, "src": 3.2, "net": 2.9, "sto": 2.9}
NAME = {"load": "荷线", "src": "源线", "net": "网线", "sto": "储线"}

CORR = {"load": 5.42, "src": 5.58, "net": 5.74, "sto": 5.90}
CORR_TOP, CORR_BOT = 5.30, 6.02
ENDX = 15.20


def load():
    plans = {r["decision"]: r for r in csv.DictReader(
        open(INV / "decision_table.csv", encoding="utf-8-sig"))}
    proto = json.load(open(INV / "compromise" / "effective_protocol.json",
                           encoding="utf-8"))
    mani = json.load(open(INV / "manifest.json", encoding="utf-8"))
    return plans, proto, mani


def main():
    plans, proto, mani = load()
    comp, base = plans["compromise"], plans["baseline"]

    fig = plt.figure(figsize=(17.0, 9.2))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 17.0)
    ax.set_ylim(0, 9.2)
    ax.axis("off")
    ax.add_patch(Rectangle((0, 0), 17.0, 9.2, facecolor=BG, edgecolor="none"))

    # ---------------------------------------------------------- 地理底纹
    for cx, cy, w, h in ((2.5, 8.36, 1.9, 0.46), (12.4, 7.42, 2.3, 0.56)):
        ax.add_patch(FancyBboxPatch(
            (cx - w / 2, cy - h / 2), w, h,
            boxstyle="round,pad=0.05,rounding_size=0.25",
            facecolor="#CBDDC0", edgecolor="none", alpha=0.55, zorder=0))

    # ---------------------------------------------------------- 线路
    paths = {
        "load": [(0.70, CORR["load"]), (ENDX, CORR["load"])],
        "src": [(0.70, 3.60), (2.30, 3.60), (4.20, CORR["src"]), (ENDX, CORR["src"])],
        "net": [(0.70, 7.30), (3.10, 7.30), (5.00, CORR["net"]), (ENDX, CORR["net"])],
        "sto": [(0.70, 8.05), (3.90, 8.05), (5.90, CORR["sto"]), (ENDX, CORR["sto"])],
    }
    for k, pts in paths.items():
        ax.plot([p[0] for p in pts], [p[1] for p in pts], color=COL[k],
                lw=WID[k], solid_capstyle="round", solid_joinstyle="round",
                zorder=3)

    # 线路号牌 + 名称（左端）
    for k in ("sto", "net", "load", "src"):
        y0 = paths[k][0][1]
        ax.add_patch(FancyBboxPatch(
            (0.30, y0 - 0.115), 0.30, 0.23,
            boxstyle="round,pad=0.02,rounding_size=0.05",
            facecolor=COL[k], edgecolor="none", zorder=6))
        ax.text(0.45, y0, NAME[k][0], fontsize=8.0, color="white",
                ha="center", va="center", zorder=7, fontweight="bold")
        ax.text(0.66, y0, NAME[k], fontsize=8.0, color=COL[k],
                fontweight="bold", ha="left", va="center", zorder=7)

    # ---------------------------------------------------------- 普通站
    def stop(x, y, lab, side):
        ax.add_patch(Circle((x, y), 0.062, facecolor=CARD,
                            edgecolor=INK, lw=1.1, zorder=6))
        ax.text(x, y + 0.19 * side, lab, fontsize=6.6, color="#3D5557",
                ha="center", va="bottom" if side > 0 else "top", zorder=7)

    stops = {
        "load": [(1.30, 5.42, "围护传热 H", -1), (1.95, 5.42, "太阳得热 G", -1),
                 (3.85, 5.42, "内部得热 Q", -1), (4.55, 5.42, "占用日程", -1)],
        "src": [(1.15, 3.60, "热泵机组", 1), (1.85, 3.60, "电加热兜底", 1)],
        "net": [(1.45, 7.30, "管段容量", 1), (2.35, 7.30, "供回水温", 1)],
        "sto": [(1.75, 8.05, "蓄热容量", -1), (2.80, 8.05, "充放热效率", -1),
                (3.75, 8.05, "初末库存", -1)],
    }
    for lst in stops.values():
        for x, y, lab, side in lst:
            stop(x, y, lab, side)

    # 走廊内四线共站
    for x, lab in ((7.60, "节点热平衡"), (8.40, "温位准入"),
                   (10.30, "设备上界"), (12.60, "碳帽采样"),
                   (14.10, "词典序端点")):
        ax.add_patch(FancyBboxPatch(
            (x - 0.062, CORR_TOP - 0.02), 0.124, CORR_BOT - CORR_TOP + 0.04,
            boxstyle="round,pad=0.008,rounding_size=0.05",
            facecolor=CARD, edgecolor="#4A5C5E", lw=1.0, zorder=6))
        ax.text(x, CORR_BOT + 0.18, lab, fontsize=6.8, color="#3D5557",
                ha="center", va="bottom", zorder=7)

    # ---------------------------------------------------------- 换乘站
    inter = [
        (2.70, "①", "建筑 1R1C 动态模型", "逐时室温递推 · 舒适上下限", "solo"),
        (6.20, "②", "源—网—荷—储协同", "同一服务平衡 · 不重复计热", "above"),
        (9.20, "③", "共享容量与冷热互斥", "配电上限 · 冷热不同开", "below"),
        (11.60, "④", "初末状态与逐时守恒", "首末闭环 · 残差 ~1e-14", "above"),
        (13.80, "⑤", "成本—碳排 ε 约束", "词典序端点 · 非支配筛选", "below"),
    ]
    for x, num, title, sub, where in inter:
        if where == "solo":
            y = CORR["load"]
            ax.add_patch(Circle((x, y), 0.155, facecolor=CARD,
                                edgecolor=C_LOAD, lw=1.9, zorder=6))
            ax.text(x, y + 0.44, f"{num} {title}", fontsize=8.0, color=INK,
                    ha="center", va="bottom", fontweight="bold", zorder=7)
            ax.text(x, y + 0.26, sub, fontsize=6.6, color=MUTED,
                    ha="center", va="bottom", zorder=7)
        else:
            ax.add_patch(FancyBboxPatch(
                (x - 0.085, CORR_TOP - 0.09), 0.17, CORR_BOT - CORR_TOP + 0.18,
                boxstyle="round,pad=0.012,rounding_size=0.08",
                facecolor=CARD, edgecolor="#4A5C5E", lw=1.3, zorder=6))
            up = where == "above"
            yy = CORR_BOT + 0.46 if up else CORR["load"] - 0.24
            ax.text(x, yy, f"{num} {title}", fontsize=8.2, color=INK,
                    ha="center", va="bottom" if up else "top",
                    fontweight="bold", zorder=7)
            ax.text(x, yy + (0.23 if up else -0.23), sub, fontsize=6.6,
                    color=MUTED, ha="center",
                    va="bottom" if up else "top", zorder=7)

    # ---------------------------------------------------------- 终点站
    ax.add_patch(Circle((ENDX, 5.66), 0.50, facecolor=C_SRC, edgecolor="none",
                        alpha=0.13, zorder=4))
    ax.add_patch(Circle((ENDX, 5.66), 0.31, facecolor=C_SRC,
                        edgecolor=CARD, lw=2.0, zorder=6))
    ax.text(ENDX, 5.66, "终", fontsize=9.0, color="white", ha="center",
            va="center", zorder=7, fontweight="bold")
    ax.text(ENDX, 6.28, "推荐方案到站", fontsize=8.2, color=C_SRC,
            ha="center", va="bottom", fontweight="bold", zorder=7)

    # ---------------------------------------------------------- 标题
    ax.text(0.62, 8.84, "热能换乘站", fontsize=23, color=INK,
            fontweight="bold", va="center")
    ax.text(0.70, 8.48, "从 8784 小时需求到一套可投资方案　·　"
                        "别人回答「要建多大」，我们回答「什么热源、何时运行、"
                        "如何满足每栋楼」",
            fontsize=9.2, color="#5C6E70", va="center")

    # ---------------------------------------------------------- 方案车票（横排）
    tx, ty, tw, th = 6.55, 2.42, 9.75, 2.10
    ax.add_patch(FancyBboxPatch(
        (tx, ty), tw, th, boxstyle="round,pad=0.06,rounding_size=0.14",
        facecolor=CARD, edgecolor="#D8E3DC", lw=1.3, zorder=5))
    ax.text(tx + 0.26, ty + th - 0.31, "方案车票", fontsize=10.5, color=INK,
            fontweight="bold", va="center", zorder=9)
    ax.text(tx + tw - 0.26, ty + th - 0.31, "compromise", fontsize=8.6,
            color=C_SRC, va="center", ha="right", zorder=9)
    ax.plot([tx + 0.26, tx + tw - 0.26], [ty + th - 0.58] * 2,
            color="#E4DCC9", lw=1.0, zorder=9)

    rows = [
        ("投资额", f"{float(comp['capex_cny']):,.0f} 元",
         f"基准 {float(base['capex_cny']):,.0f} 元", C_SRC),
        ("窗口电费", f"{float(comp['window_energy_bill_cny']):,.2f} 元",
         f"基准 {float(base['window_energy_bill_cny']):,.0f} 元", C_LOAD),
        ("窗口碳排", f"{float(comp['window_co2_kg']):,.1f} kgCO2",
         f"基准 {float(base['window_co2_kg']):,.0f} kg", C_STO),
        ("窗口电量", f"{float(comp['electricity_kwh']):,.1f} kWh",
         f"基准 {float(base['electricity_kwh']):,.0f} kWh", C_NET),
        ("设备配置", f"{int(comp['hp_units'])} 台热泵",
         f"电加热 {float(comp['resistance_capacity_kw']):.1f} kW", C_SRC),
    ]
    colw = (tw - 0.52) / len(rows)
    for i, (lab, val, sub, col) in enumerate(rows):
        x = tx + 0.30 + i * colw
        ax.plot([x, x], [ty + 0.32, ty + th - 0.80], color=col, lw=2.8,
                solid_capstyle="round", zorder=9)
        ax.text(x + 0.16, ty + th - 0.82, lab, fontsize=7.8, color=MUTED,
                va="center", zorder=9)
        ax.text(x + 0.16, ty + th - 1.14, val, fontsize=11.6, color=INK,
                va="center", fontweight="bold", zorder=9)
        ax.text(x + 0.16, ty + th - 1.42, sub, fontsize=7.0, color=MUTED,
                va="center", zorder=9)
    ax.text(tx + 0.30, ty + 0.16, "窗口 2880 h · 研究候选 · 非工程报价",
            fontsize=7.2, color=MUTED, va="center", zorder=9)

    # ---------------------------------------------------------- 线路图例
    lx, ly = 1.05, 3.00
    ax.text(lx, ly + 0.30, "线路", fontsize=8.2, color=INK, va="center",
            fontweight="bold")
    for i, k in enumerate(("load", "src", "net", "sto")):
        yy = ly - i * 0.27
        ax.plot([lx, lx + 0.40], [yy, yy], color=COL[k], lw=WID[k],
                solid_capstyle="round")
        ax.text(lx + 0.52, yy,
                f"{NAME[k]}　{['楼宇热需求、室温、舒适边界', '热泵、锅炉、余热',
                              '管网容量、源温级、配电', '蓄热：低谷热搬到高峰'][i]}",
                fontsize=7.0, color="#3D5557", va="center")
    ax.text(lx, ly - 1.22, "线宽 = 热量规模（大 / 中 / 小）", fontsize=7.0,
            color=MUTED, va="center")

    # ---------------------------------------------------------- 证据轨道
    ax.plot([0.70, 16.30], [1.42, 1.42], color="#D9D2C0", lw=1.0, zorder=1)
    ax.text(0.72, 1.42, "证据轨道", fontsize=8.0, color=C_SRC, va="center",
            fontweight="bold", zorder=9)
    win = (proto["window_start_inclusive"][:10] + " 至 "
           + proto["window_end_exclusive"][:10])
    track = [
        ("run", "investment/run_03"),
        ("天气窗口", f"{win} · {mani['hours']} h"),
        ("求解状态", "HiGHS 最优 · gap 5.0e-12"),
        ("独立审计", f"{mani['independently_audited_plans']} 方案 · "
                     f"{mani['epsilon_points']} 个 ε 点"),
        ("数据边界", "公开证据 + 冻结假设设计轴"),
    ]
    x = 1.86
    for k, v in track:
        ax.text(x, 1.62, k, fontsize=7.0, color=MUTED, va="center", zorder=9)
        ax.text(x, 1.24, v, fontsize=7.6, color=INK, va="center", zorder=9)
        x += 2.92

    ax.text(0.70, 0.62,
            "数据：docs/implementation_20261002_followup/parallel_stage_03/"
            "investment/run_03（rc6 研究候选）。公开证据 + 冻结假设设计轴，"
            "不含具体设备性能准入；武汉情景为程序验证情景，不代表实测工程效果。",
            fontsize=6.8, color="#9AA79F", va="center")

    png = OUT / "metro_map_thermal_interchange.png"
    tmp = png.with_name(png.stem + "_tmp" + png.suffix)
    fig.savefig(tmp, dpi=200, facecolor=BG)
    tmp.replace(png)
    print("saved ->", png)


if __name__ == "__main__":
    main()
