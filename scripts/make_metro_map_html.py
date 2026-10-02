# -*- coding: utf-8 -*-
"""热能换乘站：源网荷储协同决策的城市轨道线路图（HTML/SVG 版）。

用 HTML+SVG 排版后调无头 Edge 截图，解决 matplotlib 手动布点做不出设计感的问题。
排版约定：全部文字按 **基线 y** 定位，不使用 dominant-baseline，避免基线错乱。

数据源（车票数字全部实取，无人工录入）：
  docs/implementation_20261002_followup/parallel_stage_03/investment/run_03/
    decision_table.csv / compromise/effective_protocol.json / manifest.json

口径：rc6 研究候选；公开证据 + 冻结假设设计轴，不含具体设备性能准入；
      窗口 2880 小时，武汉为程序验证情景，不代表实测工程效果。
"""
from __future__ import annotations

import csv
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INV = (ROOT / "docs" / "implementation_20261002_followup" / "parallel_stage_03"
       / "investment" / "run_03")
OUT = ROOT / "outputs" / "figures"
BUILD = OUT / "_svg_build"
BUILD.mkdir(parents=True, exist_ok=True)

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
W, H, SCALE = 1700, 1000, 2

C = {"load": "#1E6B6E", "src": "#EF8F3C", "net": "#6E9BB0", "sto": "#7FA05C"}
SW = {"load": 7.5, "src": 6.5, "net": 5.5, "sto": 5.5}
NAME = {"load": "荷线", "src": "源线", "net": "网线", "sto": "储线"}
DESC = {"load": "楼宇热需求、室温、舒适边界", "src": "热泵、锅炉、余热",
        "net": "管网容量、源温级、配电", "sto": "蓄热：低谷热搬到高峰"}

# ---- 几何：荷线为横贯主线（y=340），其余三线以 45° 折线穿透它
SPINE = 340
IX = [390, 620, 900, 1180, 1450]      # 五个换乘点，全部落在主线上
END = 1620
BOT = 620                              # 地图下边界

PATHS = {
    # 荷线：横贯主线（最粗，作参照轴）
    "load": [(70, SPINE), (END, SPINE)],
    # 源线：两次深 V 下穿主线（在 ② ⑤ 交叉）
    "src": [(70, 570), (390, 570), (620, SPINE), (840, 560), (1230, 560),
            (1450, SPINE), (1620, 510)],
    # 网线：左上起，下穿后在 ② ③ ⑤ 三处穿透，中间抬 Λ
    "net": [(70, 130), (370, 130), (620, SPINE), (760, 200), (900, SPINE),
            (1040, 200), (1310, 200), (1450, SPINE), (1620, 480)],
    # 储线：左下起，浅谷走线，在 ① ④ 两处穿透主线
    "sto": [(70, 620), (110, 620), (390, SPINE), (530, 480), (1040, 480),
            (1180, SPINE), (1330, 490), (1620, 490)],
}

# 普通站：(x, y, 标签, 标签基线偏移)；只放在各自线的水平段上，避免斜线穿字
STOPS = [
    (150, 340, "围护传热 H", -24), (255, 340, "太阳得热 G", 34),
    (530, 340, "内部得热 Q", -24), (770, 340, "舒适下限 20℃", 34),
    (1040, 340, "舒适上限 27℃", -24), (1330, 340, "热惯性 C", 34),
    (1510, 340, "设备上界", -24),
    (170, 570, "热泵机组", -24), (290, 570, "电加热兜底", 34),
    (960, 560, "余热接入", -24), (1100, 560, "温位准入", 34),
    (170, 130, "管段容量", 34), (300, 130, "供回水温", 34),
    (1120, 200, "沿程压降", -24), (1240, 200, "泵耗", 34),
    (700, 480, "蓄热容量", -24), (860, 480, "充放热效率", 34),
    (1010, 480, "初末库存", -24), (1560, 490, "冷热互斥", 34),
]

INTER = [
    (390, "1", "建筑 1R1C 动态模型", "逐时室温递推 · 舒适上下限", "up"),
    (620, "2", "源—网—荷—储协同", "同一服务平衡 · 不重复计热", "down"),
    (900, "3", "共享容量与冷热互斥", "配电上限 · 冷热不同开", "up"),
    (1180, "4", "初末状态与逐时守恒", "首末闭环 · 残差 ~1e-14", "down"),
    (1450, "5", "成本—碳排 ε 约束", "词典序端点 · 非支配筛选", "up"),
]


def poly(pts):
    return " ".join(f"{x},{y}" for x, y in pts)


def load():
    plans = {r["decision"]: r for r in csv.DictReader(
        open(INV / "decision_table.csv", encoding="utf-8-sig"))}
    proto = json.load(open(INV / "compromise" / "effective_protocol.json",
                           encoding="utf-8"))
    mani = json.load(open(INV / "manifest.json", encoding="utf-8"))
    return plans, proto, mani


def build_svg(plans, proto, mani):
    comp, base = plans["compromise"], plans["baseline"]
    p = []

    # ---------- 底纹（绿地，避开文字区）
    p.append('<g opacity=".45">'
             '<ellipse cx="200" cy="140" rx="130" ry="40" fill="#CBDDC0"/>'
             '</g>')

    # ---------- 线路
    for k in ("sto", "net", "src", "load"):
        p.append(f'<polyline points="{poly(PATHS[k])}" fill="none" '
                 f'stroke="{C[k]}" stroke-width="{SW[k]}" '
                 f'stroke-linecap="round" stroke-linejoin="round"/>')

    # ---------- 普通站
    for x, y, lab, dy in STOPS:
        p.append(f'<circle cx="{x}" cy="{y}" r="7" fill="#fff" '
                 f'stroke="#33504F" stroke-width="2.2"/>')
        p.append(f'<text x="{x}" y="{y+dy}" class="stop" '
                 f'text-anchor="middle">{lab}</text>')


    # ---------- 换乘站
    for x, num, title, sub, where in INTER:
        y = SPINE
        p.append(f'<circle cx="{x}" cy="{y}" r="24" fill="#fff" '
                 f'stroke="#33504F" stroke-width="3.0"/>')
        p.append(f'<circle cx="{x}" cy="{y}" r="8" fill="{C["load"]}"/>')
        if where == "up":
            p.append(f'<text x="{x}" y="{y-40}" class="ititle" '
                     f'text-anchor="middle">{num}&#160;{title}</text>')
            p.append(f'<text x="{x}" y="{y-20}" class="isub" '
                     f'text-anchor="middle">{sub}</text>')
        else:
            p.append(f'<text x="{x}" y="{y+56}" class="ititle" '
                     f'text-anchor="middle">{num}&#160;{title}</text>')
            p.append(f'<text x="{x}" y="{y+76}" class="isub" '
                     f'text-anchor="middle">{sub}</text>')

    # ---------- 终点站
    p.append(f'<circle cx="{END}" cy="400" r="34" fill="{C["src"]}" '
             f'opacity=".16"/>')
    p.append(f'<circle cx="{END}" cy="400" r="23" fill="{C["src"]}"/>')
    p.append(f'<text x="{END}" y="408" class="term" '
             f'text-anchor="middle">终</text>')
    p.append(f'<text x="{END}" y="300" class="ititle" text-anchor="middle" '
             f'fill="{C["src"]}">推荐方案到站</text>')

    # ---------- 线路图例（横向一行，置于地图下方）
    ly = 690
    for i, k in enumerate(("load", "src", "net", "sto")):
        lx = 70 + i * 400
        p.append(f'<line x1="{lx}" y1="{ly}" x2="{lx+34}" y2="{ly}" '
                 f'stroke="{C[k]}" stroke-width="{SW[k]}" '
                 f'stroke-linecap="round"/>')
        p.append(f'<text x="{lx+46}" y="{ly+5}" class="leg">'
                 f'{NAME[k]}&#160;&#160;{DESC[k]}</text>')
    p.append(f'<text x="70" y="722" class="legsub">'
             f'线宽 = 热量规模（大 / 中 / 小）；换乘站为四条线实际交叉处</text>')

    # ---------- 车票
    tx, ty, tw, th = 60, 750, 1580, 148
    p.append(f'<rect x="{tx}" y="{ty}" width="{tw}" height="{th}" rx="18" '
             f'fill="#fff" stroke="#DDE6DE" stroke-width="1.5"/>')
    p.append(f'<text x="{tx+30}" y="{ty+36}" class="cardtitle">方案车票</text>')
    p.append(f'<text x="{tx+tw-30}" y="{ty+36}" class="cardtag" '
             f'text-anchor="end">compromise</text>')
    p.append(f'<line x1="{tx+30}" y1="{ty+52}" x2="{tx+tw-30}" y2="{ty+52}" '
             f'stroke="#E8E1CE" stroke-width="1"/>')
    rows = [
        ("投资额", f"{float(comp['capex_cny']):,.0f} 元",
         f"基准 {float(base['capex_cny']):,.0f} 元", C["src"]),
        ("窗口电费", f"{float(comp['window_energy_bill_cny']):,.2f} 元",
         f"基准 {float(base['window_energy_bill_cny']):,.0f} 元", C["load"]),
        ("窗口碳排", f"{float(comp['window_co2_kg']):,.1f} kgCO2",
         f"基准 {float(base['window_co2_kg']):,.0f} kg", C["sto"]),
        ("窗口电量", f"{float(comp['electricity_kwh']):,.1f} kWh",
         f"基准 {float(base['electricity_kwh']):,.0f} kWh", C["net"]),
        ("设备配置", f"{int(comp['hp_units'])} 台热泵",
         f"电加热 {float(comp['resistance_capacity_kw']):,.1f} kW", C["src"]),
    ]
    cw = (tw - 60) / len(rows)
    for i, (lab, val, sub, col) in enumerate(rows):
        x = tx + 44 + i * cw
        p.append(f'<line x1="{x}" y1="{ty+72}" x2="{x}" y2="{ty+126}" '
                 f'stroke="{col}" stroke-width="3.6" stroke-linecap="round"/>')
        p.append(f'<text x="{x+18}" y="{ty+76}" class="clab">{lab}</text>')
        p.append(f'<text x="{x+18}" y="{ty+106}" class="cval">{val}</text>')
        p.append(f'<text x="{x+18}" y="{ty+128}" class="csub">{sub}</text>')

    # ---------- 证据轨道
    ey = 940
    p.append(f'<line x1="70" y1="{ey}" x2="1630" y2="{ey}" '
             f'stroke="#D9D2C0" stroke-width="1.4"/>')
    p.append(f'<text x="70" y="{ey+5}" class="evtag">证据轨道</text>')
    win = (proto["window_start_inclusive"][:10] + " 至 "
           + proto["window_end_exclusive"][:10])
    track = [("run", "investment/run_03"),
             ("天气窗口", f"{win} · {mani['hours']} h"),
             ("求解状态", "HiGHS 最优 · gap 5.0e-12"),
             ("独立审计", f"{mani['independently_audited_plans']} 方案 · "
                          f"{mani['epsilon_points']} 个 ε 点"),
             ("数据边界", "公开证据 + 冻结假设设计轴")]
    for i, (k, v) in enumerate(track):
        x = 200 + i * 290
        p.append(f'<text x="{x}" y="{ey-16}" class="evk">{k}</text>')
        p.append(f'<text x="{x}" y="{ey+26}" class="evv">{v}</text>')
    return "\n".join(p)


def build_html(svg):
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
  *{{margin:0;padding:0;box-sizing:border-box}}
  html,body{{width:{W}px;height:{H}px;overflow:hidden;background:#FBF7EE;
    font-family:"Noto Sans SC","Microsoft YaHei",sans-serif;
    -webkit-font-smoothing:antialiased}}
  text{{fill:#173F42;paint-order:stroke;stroke:#FBF7EE;stroke-width:4.5px;stroke-linejoin:round}}
  .h1{{font-size:38px;font-weight:900;letter-spacing:1px}}
  .h2{{font-size:15px;fill:#5C6E70}}
  .stop{{font-size:13px;fill:#3D5557}}
  .ititle{{font-size:16px;font-weight:700}}
  .isub{{font-size:12px;fill:#7C8F91}}
  .term{{font-size:17px;font-weight:700;fill:#fff;stroke:none}}
  .leg{{font-size:13px;fill:#3D5557}}
  .legsub{{font-size:12px;fill:#7C8F91}}
  .cardtitle{{font-size:17px;font-weight:700;stroke:none}}
  .cardtag{{font-size:13px;fill:#EF8F3C;stroke:none}}
  .clab{{font-size:12px;fill:#7C8F91;stroke:none}}
  .cval{{font-size:23px;font-weight:800;stroke:none}}
  .csub{{font-size:11.5px;fill:#9AA79F;stroke:none}}
  .evtag{{font-size:13px;font-weight:700;fill:#EF8F3C}}
  .evk{{font-size:11.5px;fill:#7C8F91;stroke:none}}
  .evv{{font-size:13px;stroke:none}}
  .foot{{font-size:11.5px;fill:#9AA79F;stroke:none}}
</style></head><body>
<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}"
     xmlns="http://www.w3.org/2000/svg">
  <rect width="{W}" height="{H}" fill="#FBF7EE"/>
  <text x="70" y="52" class="h1">热能换乘站</text>
  <text x="70" y="86" class="h2">从 8784 小时需求到一套可投资方案　·　别人回答「要建多大」，我们回答「什么热源、何时运行、如何满足每栋楼」</text>
{svg}
  <text x="70" y="986" class="foot">数据：docs/implementation_20261002_followup/parallel_stage_03/investment/run_03（rc6 研究候选）。公开证据 + 冻结假设设计轴，不含具体设备性能准入；武汉情景为程序验证情景，不代表实测工程效果。</text>
</svg></body></html>"""


def main():
    plans, proto, mani = load()
    html = build_html(build_svg(plans, proto, mani))
    hp = BUILD / "metro.html"
    hp.write_text(html, encoding="utf-8")

    png = OUT / "metro_map_thermal_interchange.png"
    subprocess.run([EDGE, "--headless", "--disable-gpu", "--hide-scrollbars",
                    f"--force-device-scale-factor={SCALE}",
                    f"--window-size={W},{H}",
                    f"--screenshot={png}", hp.as_uri()],
                   capture_output=True, timeout=180)
    print("saved ->", png, png.stat().st_size // 1024, "KB")


if __name__ == "__main__":
    main()
