# -*- coding: utf-8 -*-
"""汇总生成本节抽取表 tables/heatpump_datasheet_extracted.csv。

输入：
  tables/_parts/*.csv                 各检索分组的分表
  sources/能效标识网/el_details*.jsonl  中国能效标识网备案明细（原始接口响应）
输出：
  tables/heatpump_datasheet_extracted.csv

规则：
  * 一行一个工况点
  * 备案行不按标准条文回填 源侧温度/出水温度（备案表不给），这两列留空
  * 按"物理内容"去重，避免同一工况点因来源重复而多行
"""
import csv
import glob
import json
import os

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
PARTS = os.path.join(ROOT, "tables", "_parts")
SRC_EL = os.path.join(ROOT, "sources", "能效标识网")
OUT = os.path.join(ROOT, "tables", "heatpump_datasheet_extracted.csv")

HDR = ["品牌", "型号", "机组类型", "额定制热量_kW", "源侧温度_C", "出水温度_C",
       "制热量_kW", "输入功率_kW", "COP",
       "数据性质(样本/检测报告/能效备案/招标门槛)", "适用城市", "年份",
       "来源文件", "source_url"]

NCOL = len(HDR)
EL_URL = "https://www.energylabel.com.cn/productFiling"


def pick(items, *names):
    """按多个候选名取第一个命中的值（备案接口字段名在不同类别下不一致）。"""
    for n in names:
        if n in items:
            return items[n]
    return ""


def load_parts():
    rows = []
    for p in sorted(glob.glob(os.path.join(PARTS, "*.csv"))):
        if os.path.basename(p).startswith("_tmp"):
            continue  # 只跳临时件；_local_ 等分表必须并入
        with open(p, encoding="utf-8-sig", newline="") as f:
            r = list(csv.reader(f))
        if not r:
            continue
        body = r[1:]
        for row in body:
            if len(row) < NCOL:
                row = row + [""] * (NCOL - len(row))
            rows.append(row[:NCOL])
        print(f"  part {os.path.basename(p)}: {len(body)} rows")
    return rows


def load_el():
    rows = []
    files = sorted(glob.glob(os.path.join(SRC_EL, "el_details*.jsonl")))
    for p in files:
        n = 0
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            code = r.get("productTypeCode")
            if code not in ("73", "74", "17", "12"):
                continue  # 72 类是冷水机组，绝大多数只备案制冷，不进供暖表
            it = r.get("items") or {}
            ptype = r.get("productType") or ""
            q = pick(it, "名义制热量(kW)", "名义制热量kW", "名义制热量(W)", "名义制热量W")
            pw = pick(it, "名义制热消耗功率(kW)", "名义制热消耗功率kW", "名义制热消耗功率(W)")
            coph = pick(it, "名义制热性能系数(COPh)(kW/kW)", "名义制热性能系数(COPh)(W/W)",
                        "制热性能系数（COP）kW/kW", "制热性能系数(COPH)(kW/kW)",
                        "制热性能系数（COP）W/W")
            copdh = pick(it, "低温制热性能系数(COPdh)(kW/kW)", "低温制热性能系数(COPdh)(W/W)")
            form = pick(it, "产品型式")
            brand = r.get("producerName") or ""
            model = (r.get("productModel") or "").strip()
            year = (r.get("announcementTime") or "")[:4]
            reg = r.get("registrationNumber") or ""
            f75 = f"el_details_{code}.jsonl（中国能效标识网 产品备案查询接口原始响应；备案号 {reg}）"
            typ_base = f"{ptype}/名义制热工况"
            if form:
                typ_base += f"（产品型式：{form}）"
            if q or pw or coph:
                rows.append([brand, model, typ_base, "", "", "", q, pw, coph,
                             "能效备案", "", year, f75, EL_URL])
                n += 1
            if copdh:
                typ2 = f"{ptype}/低温制热工况"
                if form:
                    typ2 += f"（产品型式：{form}）"
                rows.append([brand, model, typ2, "", "", "", "", "", copdh,
                             "能效备案", "", year, f75, EL_URL])
                n += 1
        print(f"  el {os.path.basename(p)}: {n} rows")
    return rows


QC_NOTE = ("【QC 警告：备案原表把 38 标成单位“W”，但型号 KFXRS-38II 名义为 38 kW，"
           "且 制热量/功率 比值与 COP 自洽 ⟹ 原表单位疑为笔误；本行按原表字面折算，"
           "引用前须回查备案原表】")


# EN14825 / Keymark 分环温点的口径警告
EN14825_NOTE = ("【QC 警告：本行来自 EN14825/Keymark 欧盟认证的分环温申报值，"
                "多为部分负荷工况（制热量随环温升高反而下降），"
                "不能当满负荷 Q(T) 曲线用；且为欧盟工况，与 GB/T 25127 名义工况不可直接比】")

# 来源是"二次转载页"而非厂家官方原件的域名
REPOST_DOMAINS = {
    "cardlott.cn": "【QC：来源为二次转载页 cardlott.cn，非清华同方官方原件，引用前须回查官方样本】",
}


def annotate_qc(rows):
    """给"备案原表单位疑似笔误"与"来源为二次转载"的行加 QC 标注（不改动任何数值）。"""
    n = 0
    for r in rows:
        try:
            q = float((r[6] or "").strip())
        except Exception:
            q = None
        if q is not None and 0 < q < 0.1:
            r[2] = r[2] + QC_NOTE
            n += 1
        u = (r[13] or "")
        for dom, note in REPOST_DOMAINS.items():
            if dom in u:
                r[2] = r[2] + note
                n += 1
                break
        # EN14825 分环温点多为"部分负荷申报值"，制热量随环温升高而下降，不能当满负荷曲线用
        if "EN14825" in r[12] or "Keymark" in r[12]:
            r[2] = r[2] + EN14825_NOTE
            n += 1
    print(f"  QC 标注行数: {n}")
    return rows


def keyof(row):
    """物理内容键：同一条物理数据不论来自哪份件都算重复。"""
    return tuple((x or "").strip() for x in
                 (row[0], row[1], row[6], row[7], row[8], row[9], row[2]))


def main():
    print("载入 parts ...")
    rows = load_parts()
    print("载入能效备案 ...")
    rows += load_el()
    rows = annotate_qc(rows)

    seen, out = set(), []
    for r in rows:
        k = keyof(r)
        if k in seen:
            continue
        seen.add(k)
        out.append(r)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(HDR)
        w.writerows(out)
    print(f"\nwrote {OUT}\n  rows={len(out)}  (去重前 {len(rows)})")

    import collections
    print("  数据性质:", collections.Counter(r[9] for r in out).most_common())
    print("  品牌数:", len({r[0] for r in out}))


if __name__ == "__main__":
    main()
