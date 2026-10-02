# -*- coding: utf-8 -*-
"""
datasets/osm 的换算方法——**反推重建版脚本**（2026-10-02）

背景
----
`datasets/osm/` 内没有任何转换脚本、坐标系声明或下载记录。本节用"表里已有的
geometry（WKT）与其对应的 area_m2 / length_m"做反推，把原转换方法复原出来并验证。

反推结论（已验证）
------------------
1. 坐标系：**WGS84 / EPSG:4326**（经纬度十进制度；球面公式仅在度制经纬度下成立）
2. 地球模型：**球体，半径 R = 6378137.0 m**（即 WGS84 长半轴）
   - 判据：先用 R=6371008.8（平均半径）复算，面积比值恒为 1.0022、长度比值恒为 1.0011，
     离散度近零 ⟹ 系统性换半径，而非随机误差；sqrt(1.0022)=1.0011=6378137/6371008.8
   - 换成 R=6378137 后，面积/长度比值回到 **0.999997 ~ 1.000002**
3. 面积：**球面多边形公式**（纬度正弦加权的 shoelace），非投影平面法
4. 长度/周长：**大圆（haversine）折线求和**
5. 建筑层只抽 `way`（无 `relation`），且**未保留 geometry**

复算一致度（实测）
------------------
| 图层 | 样本 | 中位比值（表值/复算值，R=6378137） |
|---|---|---|
| 乌鲁木齐 landuse / roads | 600 / 600 | 0.999999 / 0.999997 |
| 武汉 landuse / roads | 600 / 600 | 1.000000 / 0.999998 |
| 固原 landuse / roads | 600 / 600 | 0.999999 / 1.000002 |
| 固原两栋最大建筑（Overpass 取回几何直接核） | 2 | 面积 1.00001 / 0.99999，周长 1.00002 / 0.99999 |

用法
----
    python reconstruct_conversion.py "POLYGON((87.60 43.93, ...))"          # 面积+周长
    python reconstruct_conversion.py --check ../osm/urumqi_landuse.csv      # 整表复算比对

限制
----
- 这是**方法复原**，不是原始脚本；原始下载时间/查询语句无法从包内恢复。
- `*_buildings.csv` **未保留 geometry**，故其 `footprint_area_m2` 只能抽样直核（已核 2 栋通过），
  无法全表复核。
- 反推基于当前 Overpass 快照；原抽取时刻的 OSM 数据可能已变动。
"""

import csv
import math
import re
import sys

try:  # 控制台可能是 GBK，强制 UTF-8 输出
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

R = 6378137.0  # WGS84 长半轴；反推所得


# ---------- WKT 解析（只处理 POLYGON / MULTIPOLYGON / LINESTRING / MULTILINESTRING） ----------

def _coords(blob):
    pts = []
    for tok in blob.split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            x, y = tok.split()[:2]
            pts.append((float(x), float(y)))
        except ValueError:
            pass
    return pts


def wkt_rings(wkt):
    """提取所有闭合环（外环+内环）"""
    return [p for p in (_coords(g) for g in re.findall(r"\(([^()]+)\)", wkt)) if len(p) >= 3]


def wkt_lines(wkt):
    """提取所有折线"""
    return [p for p in (_coords(g) for g in re.findall(r"\(([^()]+)\)", wkt)) if len(p) >= 2]


# ---------- 几何量 ----------

def spherical_area(pts, r=R):
    """球面多边形面积（纬度正弦加权 shoelace）。带洞几何应"外环 - 内环"，见 feature_area。"""
    if len(pts) < 3:
        return 0.0
    if pts[0] != pts[-1]:
        pts = pts + [pts[0]]
    s = 0.0
    for (x1, y1), (x2, y2) in zip(pts[:-1], pts[1:]):
        s += (math.radians(x2) - math.radians(x1)) * (
            2 + math.sin(math.radians(y1)) + math.sin(math.radians(y2))
        )
    return abs(s * r * r / 2.0)


def haversine(a, b, r=R):
    lon1, lat1 = a
    lon2, lat2 = b
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


def polyline_length(pts, r=R):
    return sum(haversine(a, b, r) for a, b in zip(pts[:-1], pts[1:]))


def feature_area(wkt, r=R):
    """多边形/多多边形面积：按环面积降序，偶数环为洞（外环减洞环）"""
    rings = wkt_rings(wkt)
    if not rings:
        return 0.0
    areas = sorted((spherical_area(p, r) for p in rings), reverse=True)
    return sum(areas[0::2]) - sum(areas[1::2])


def feature_length(wkt, r=R):
    return sum(polyline_length(p, r) for p in wkt_lines(wkt))


def centroid(pts):
    """顶点平均（近似质心，非面积质心）"""
    if not pts:
        return None
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


# ---------- 整表比对 ----------

def check(path, valcol=None, kind=None, limit=2000):
    with open(path, encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    if valcol is None:
        valcol = "area_m2" if "area_m2" in rows[0] else "length_m"
    if kind is None:
        kind = "area" if valcol == "area_m2" else "len"
    fn = feature_area if kind == "area" else feature_length
    ratios = []
    for r in rows[:limit]:
        g = (r.get("geometry") or "").strip()
        v = (r.get(valcol) or "").strip()
        if not g or not v:
            continue
        try:
            v = float(v)
        except ValueError:
            continue
        mine = fn(g)
        if mine > 0:
            ratios.append(v / mine)
    if not ratios:
        print(f"{path}: 无可用样本（该图层可能未保留 geometry）")
        return
    ratios.sort()
    med = ratios[len(ratios) // 2]
    verdict = "一致" if abs(med - 1) < 0.002 else "不一致"
    print(f"{path}\n  n={len(ratios)}  中位比值={med:.6f}  "
          f"5%={ratios[int(len(ratios)*.05)]:.6f}  95%={ratios[int(len(ratios)*.95)]:.6f}  "
          f"[{verdict}]")


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--check":
        check(sys.argv[2])
    elif len(sys.argv) > 1:
        w = sys.argv[1]
        print(f"面积 = {feature_area(w):.2f} m2")
        print(f"周长/长度 = {feature_length(w):.2f} m")
        rs = wkt_rings(w)
        if rs:
            print(f"质心 = {centroid(rs[0])}")
    else:
        print(__doc__)
