# -*- coding: utf-8 -*-
"""三城建筑存量空间分布六联图（a-f）。

数据源：data/external/osm_original/{city}_buildings.csv 与 _boundary.csv、_water.csv
许可：OpenStreetMap contributors, ODbL 1.0（众源数据，非官方台账）

口径：
  建筑密度（栋/km2）= 单元内建筑质心计数
  建筑占地密度（%）= 单元内建筑 footprint_area_m2 之和 / 单元面积
  单元 = 0.5 km x 0.5 km；网格按行政边界裁剪，界外不设色
  窗口 = WINDOW_MODE="quantile" 时取覆盖 90% 建筑点的方形区域，
         WINDOW_MODE="admin" 时取完整行政域（可看到城市轮廓）

已知限制：占地密度按质心归属，单体占地大于单元时该格可超 100%（已并入最高档），
  不代表真实地块覆盖率。
"""
from __future__ import annotations

import csv
import math
import os
import re
import sys
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.patheffects as patheffects
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import BoundaryNorm, ListedColormap

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "external" / "osm_original"
OUT = ROOT / "outputs" / "figures"
OUT.mkdir(parents=True, exist_ok=True)

CITIES = ["urumqi", "guyuan", "wuhan"]
CITY_CN = {"urumqi": "乌鲁木齐", "guyuan": "固原", "wuhan": "武汉"}

CELL_M = 500.0
KEEP_PCT = 90.0  # 窗口覆盖的建筑点比例（WINDOW_MODE="quantile" 时生效）
WINDOW_MODE = "quantile"  # quantile=裁到城区（看清密度） | admin=整幅行政域（看清轮廓）

# 分档（行内三城共用，可横向比较）
BINS_COUNT = [0, 10, 50, 150, 300, np.inf]  # 栋/km2
BINS_COVER = [0, 5, 15, 30, 50, np.inf]  # %
BIN_LABELS_COUNT = ["0–10", "10–50", "50–150", "150–300", "≥300"]
BIN_LABELS_COVER = ["0–5", "5–15", "15–30", "30–50", "≥50"]

# 色系（照现稿）：上排管道蓝灰（对应参考图 d 列），下排炉火橙（对应参考图 e 列）
CREAM = "#FBF7EE"        # 底色 暖米黄
INK = "#173F42"          # 正文深青黑
GREY = "#7C8F91"         # 辅助灰（浅）
LIGHT = "#D9D2C0"        # 浅底（暖灰）
EMBER = "#EF8F3C"        # 炉火橙 基准
EMBER_DEEP = "#C4652A"   # 炉火橙 深端
EMBER_DARK = "#8C3A14"   # 炉火橙 更深处
EMBER_LIGHT = "#F9D9BC"  # 炉火橙 浅端
STEEL = "#6E9BB0"        # 管道蓝灰 基准
STEEL_LIGHT = "#DDE6DE"  # 管道蓝灰 浅端
STEEL_DARK = "#33504F"   # 管道蓝灰 深端
STEEL_MID = "#A5C0C7"    # 管道蓝灰 浅-基准 中间插值


def _mix(c1, c2, t):
    """两个 hex 颜色之间线性插值。"""
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02X}" for x, y in zip(a, b))


def _ramp(stops, n):
    """在给定色标 stops 上等距取 n 个颜色（族内分段线性插值）。"""
    segs = len(stops) - 1
    out = []
    for i in range(n):
        p = i / (n - 1) * segs
        k = min(int(p), segs - 1)
        out.append(_mix(stops[k], stops[k + 1], p - k))
    return out


CMAP_COUNT = ListedColormap(_ramp([STEEL_LIGHT, STEEL, STEEL_DARK], 5))
CMAP_COVER = ListedColormap(_ramp([EMBER_LIGHT, EMBER, EMBER_DEEP, EMBER_DARK], 5))

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

plt.rcParams["axes.facecolor"] = CREAM
plt.rcParams["figure.facecolor"] = CREAM
plt.rcParams["savefig.facecolor"] = CREAM
plt.rcParams["text.color"] = INK
plt.rcParams["axes.labelcolor"] = INK
plt.rcParams["axes.edgecolor"] = INK
plt.rcParams["xtick.color"] = INK
plt.rcParams["ytick.color"] = INK


# ---------------------------------------------------------------- WKT 解析
_PAIR = re.compile(r"(-?\d+\.?\d*)\s+(-?\d+\.?\d*)")


def parse_wkt_polys(wkt: str):
    """POLYGON / MULTIPOLYGON -> [ (N,2) ndarray ]（外环与内环都返回）"""
    if not wkt:
        return []
    return [np.array([(float(a), float(b)) for a, b in _PAIR.findall(g)])
            for g in re.findall(r"\(([^()]*)\)", wkt)]


# ---------------------------------------------------------------- 数据装载
def load_buildings(city: str):
    with open(SRC / f"{city}_buildings.csv", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    lon = np.array([float(r["centroid_lon"]) for r in rows])
    lat = np.array([float(r["centroid_lat"]) for r in rows])
    area = np.array([float(r["footprint_area_m2"] or 0.0) for r in rows])
    return lon, lat, area


def load_boundary(city: str):
    """-> [ (N,2) ndarray, role ]，按 (polygon_id, ring_id) 分组的顶点序列"""
    with open(SRC / f"{city}_boundary.csv", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    rings: dict[tuple[str, str], list] = {}
    roles: dict[tuple[str, str], str] = {}
    for r in rows:
        k = (r["polygon_id"], r["ring_id"])
        rings.setdefault(k, []).append((float(r["lon"]), float(r["lat"])))
        roles[k] = r.get("role", "outer")
    return [(np.array(v), roles[k]) for k, v in rings.items()]


def load_water(city: str):
    with open(SRC / f"{city}_water.csv", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    return [p for p in (parse_wkt_polys(r.get("geometry", "")) for r in rows) for p in p]


def project(lon, lat, lon0, lat0, cos0):
    return (lon - lon0) * 111320.0 * cos0, (lat - lat0) * 110540.0


def points_in_ring(px, py, ring):
    """射线法，向量化：ring (N,2) -> 布尔数组"""
    inside = np.zeros(px.shape, dtype=bool)
    x1, y1 = ring[:, 0], ring[:, 1]
    x2, y2 = np.roll(x1, -1), np.roll(y1, -1)
    for i in range(len(ring)):
        if y1[i] == y2[i]:
            continue
        cond = (y1[i] > py) != (y2[i] > py)
        xin = (x2[i] - x1[i]) * (py - y1[i]) / (y2[i] - y1[i]) + x1[i]
        inside ^= cond & (px < xin)
    return inside


# ---------------------------------------------------------------- 单城聚合
class CityGrid:
    def __init__(self, city: str):
        self.city = city
        lon, lat, area = load_buildings(city)
        self.lon0, self.lat0 = float(np.median(lon)), float(np.median(lat))
        self.cos0 = math.cos(math.radians(self.lat0))
        x, y = project(lon, lat, self.lon0, self.lat0, self.cos0)
        self.x, self.y, self.area = x, y, area

        # 行政边界（投影后），用于窗口、网格裁剪与描边
        self.rings = []
        for ring, role in load_boundary(city):
            rx, ry = project(ring[:, 0], ring[:, 1], self.lon0, self.lat0, self.cos0)
            self.rings.append((np.column_stack([rx, ry]), role))

        # 方形窗口半边长
        if WINDOW_MODE == "admin":  # 整幅行政域：能看到完整城市轮廓
            pts = np.vstack([r for r, _ in self.rings])
            half = float(max(np.abs(pts[:, 0]).max(), np.abs(pts[:, 1]).max()))
        else:  # 覆盖 KEEP_PCT 建筑点：城区读得清，代价是切边
            half = float(max(np.percentile(np.abs(x), KEEP_PCT),
                             np.percentile(np.abs(y), KEEP_PCT)))
        self.half = half
        self.inside = (np.abs(x) <= half) & (np.abs(y) <= half)

        n = int(round(2 * half / CELL_M))
        self.n = n
        ix = np.clip(((x[self.inside] + half) / CELL_M).astype(int), 0, n - 1)
        iy = np.clip(((y[self.inside] + half) / CELL_M).astype(int), 0, n - 1)
        self.count = np.zeros((n, n))
        self.cov = np.zeros((n, n))
        np.add.at(self.count, (iy, ix), 1.0)
        np.add.at(self.cov, (iy, ix), self.area[self.inside])
        self.cov = self.cov / (CELL_M ** 2) * 100.0  # %
        self.count = self.count / (CELL_M ** 2 / 1e6)  # 栋/km2

        self.kept_pct = self.inside.sum() / len(x) * 100.0

        # 单元中心是否落在行政边界内（外环相加、内环相减）
        cx = -half + (np.arange(n) + 0.5) * CELL_M
        gx, gy = np.meshgrid(cx, cx)
        gx, gy = gx.ravel(), gy.ravel()
        mask = np.zeros(gx.shape, dtype=bool)
        for ring, role in self.rings:
            hit = points_in_ring(gx, gy, ring)
            mask = mask ^ hit if role == "outer" else mask & ~hit
        self.admin_mask = mask.reshape(n, n)

    def to_xy(self, rings):
        return [project(a[:, 0], a[:, 1], self.lon0, self.lat0, self.cos0)
                for a in rings if len(a)]


def classify(values, bins):
    return np.clip(np.digitize(values, bins[1:-1], right=False), 0, len(bins) - 2)


# ---------------------------------------------------------------- 绘图
def draw_legend(ax, cmap, labels, title, fs=8.5, ms=7.5):
    """独立图例轴：纵向排列，左文字、右色块（不遮挡地图）。

    色块用方形 marker：marker 尺寸以点为单位，不受轴纵横比拉伸。
    """
    ax.set_axis_off()
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    top, dy = 0.90, 0.115
    ax.text(0.0, top + 0.075, title, fontsize=fs, ha="left", va="bottom",
            color=INK)
    for i, lab in enumerate(labels):
        yy = top - i * dy
        ax.text(0.0, yy, lab, fontsize=fs, ha="left", va="center", color=INK)
        ax.plot(0.74, yy, marker="s", markersize=ms, linestyle="none",
                markerfacecolor=cmap(i), markeredgecolor=GREY,
                markeredgewidth=0.5, clip_on=False)


def draw_panel(ax, grid: CityGrid, values, bins, labels, cmap,
               letter, title_cn, unit):
    n, half = grid.n, grid.half
    cls = classify(values, bins)
    # 行政边界外不设色：设色块因此长出城市形状
    cls = np.ma.masked_array(cls, mask=~grid.admin_mask)

    ax.pcolormesh(np.linspace(-half, half, n + 1),
                  np.linspace(-half, half, n + 1),
                  cls, cmap=cmap,
                  norm=BoundaryNorm(np.arange(len(labels) + 1) - 0.5, len(labels)),
                  shading="flat", rasterized=True, zorder=2)

    # 水体（细线，压在网格之上，保证河流可见）
    for wx, wy in grid.to_xy(load_water(grid.city)):
        if len(wx) > 2:
            ax.plot(wx, wy, color=STEEL_MID, lw=0.22, alpha=0.8, zorder=4)

    # 行政边界（灰细线）
    for ring, _role in grid.rings:
        if len(ring) > 2:
            ax.plot(np.append(ring[:, 0], ring[0, 0]),
                    np.append(ring[:, 1], ring[0, 1]),
                    color="#3D5557", lw=0.85, zorder=5)

    m = half * 0.995
    ax.set_xlim(-m, m)
    ax.set_ylim(-m, m)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(False)

    # 面板字母 + 双行标题
    ax.text(-0.055, 1.055, letter, transform=ax.transAxes, fontsize=15,
            fontweight="bold", ha="left", va="bottom")
    ax.text(0.0, 1.055, f"{CITY_CN[grid.city]}·{title_cn}",
            transform=ax.transAxes, fontsize=10.5, ha="left", va="bottom",
            color=INK)
    ax.text(0.0, 1.018, unit, transform=ax.transAxes, fontsize=9,
            ha="left", va="bottom", color=GREY)

    # 比例尺（米黄描边，压在边界线上也读得清）
    halo = [patheffects.withStroke(linewidth=2.4, foreground=CREAM)]
    L = 20_000.0
    x1 = m - L - 0.05 * 2 * m
    y1 = -m + 0.045 * 2 * m
    ax.plot([x1, x1 + L], [y1, y1], color=INK, lw=1.6,
            solid_capstyle="butt", zorder=7, path_effects=halo)
    ax.text(x1 + L / 2, y1 + 0.016 * 2 * m, "20 km", ha="center", va="bottom",
            fontsize=8, color=INK, zorder=7, path_effects=halo)

    # 窗口覆盖率，供图注引用
    scope = "行政域" if WINDOW_MODE == "admin" else "窗口"
    ax.text(0.0, -0.028, f"{scope} {2*half/1000:.0f}×{2*half/1000:.0f} km，"
                         f"含 {grid.kept_pct:.0f}% 建筑点",
            transform=ax.transAxes, fontsize=7.5, color=GREY,
            ha="left", va="top")


def _save(fig, path, **kw):
    """先写临时文件再改名：避免目标被看图程序/索引器临时占用时写失败。"""
    tmp = path.with_name(path.stem + "_tmp" + path.suffix)
    fig.savefig(tmp, **kw)
    for i in range(12):
        try:
            os.replace(tmp, path)
            return
        except OSError:
            time.sleep(1.0)
    raise OSError(f"无法覆盖 {path}")


def main():
    global WINDOW_MODE
    if len(sys.argv) > 1 and sys.argv[1] in ("quantile", "admin"):
        WINDOW_MODE = sys.argv[1]
    tag = "" if WINDOW_MODE == "quantile" else "_adminextent"
    grids = {c: CityGrid(c) for c in CITIES}
    for c in CITIES:
        g = grids[c]
        print(f"{c:7s} 窗口 {2*g.half/1000:.0f}km 格 {g.n}x{g.n} "
              f"保留建筑 {g.kept_pct:.1f}% | 密度max {g.count.max():.0f} 栋/km2 "
              f"| 占地max {g.cov.max():.1f}%")

    fig = plt.figure(figsize=(14.0, 9.4))
    outer = fig.add_gridspec(2, 3, left=0.015, right=0.99, top=0.925,
                             bottom=0.075, wspace=0.06, hspace=0.32)
    letters = list("abcdef")

    for j, city in enumerate(CITIES):
        g = grids[city]
        for r, (vals, bins, labels, cmap, ltitle, mtitle, unit) in enumerate([
            (g.count, BINS_COUNT, BIN_LABELS_COUNT, CMAP_COUNT,
             "档位", "建筑密度", "（栋/km²）"),
            (g.cov, BINS_COVER, BIN_LABELS_COVER, CMAP_COVER,
             "档位", "建筑占地密度", "（%）"),
        ]):
            inner = outer[r, j].subgridspec(1, 2, width_ratios=[1.0, 0.30],
                                            wspace=0.02)
            ax = fig.add_subplot(inner[0, 0])
            lax = fig.add_subplot(inner[0, 1])
            draw_panel(ax, g, vals, bins, labels, cmap, letters[r * 3 + j],
                       mtitle, unit)
            draw_legend(lax, cmap, labels, ltitle)

    fig.text(0.015, 0.012,
             "数据：OpenStreetMap contributors（ODbL 1.0）众源数据；中国境内建筑由志愿者补绘，"
             "覆盖率不均，不等于实际栋数。单元 0.5 km×0.5 km，按建筑质心归属；"
             "占地密度按质心归属时单格可超 100%（已并入最高档）。",
             fontsize=7.5, color=GREY, ha="left", va="bottom")

    _save(fig, OUT / f"city_building_stock_six_panel{tag}.png", dpi=300,
      bbox_inches="tight", facecolor=CREAM)
    _save(fig, OUT / f"city_building_stock_six_panel{tag}.pdf",
      bbox_inches="tight", facecolor=CREAM)
    print("saved ->", OUT / f"city_building_stock_six_panel{tag}.png")


if __name__ == "__main__":
    main()
