# -*- coding: utf-8 -*-
"""把分派代理产出的 *_agent_*.json 合并进 CSV。

合并规则（把关口）：
  一行只有在同时满足下面两条时才收：
    (1) 制热量_kW / 输入功率_kW / COP / 额定制热量_kW 里至少有一个能解析出数字；
    (2) 源侧温度_C / 出水温度_C 里至少有一个能解析出数字。
  否则该行无法在(环温,出水温度)平面上定位，丢弃并把丢弃原因记入 _not_found.md 的追加段。
"""
import csv, glob, json, os, re

HDR = "品牌,型号,机组类型,额定制热量_kW,源侧温度_C,出水温度_C,制热量_kW,输入功率_kW,COP,数据性质(样本/检测报告/能效备案/招标门槛),适用城市,年份,来源文件,source_url"
BASE = r"C:/Users/13349/Desktop/小挑/补充数据_20261002/13_热泵机组样本"
OUT = os.path.join(BASE, "tables/_parts/专业品牌.csv")
ALLOWED = {"样本", "检测报告", "能效备案", "招标门槛"}


def num(s):
    """抽取字符串里第一个数字（含量纲/比较符的前后文），没有则 None。"""
    if s is None:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", str(s))
    return m.group(0) if m else None


def norm_val(s):
    """数值列归一化。

    约定（按来源实际写法分三种，逐字尊重原文）：
      '41.5(16.0-50.0)'  -> '41.5'   标称值(变频区间)，取标称值
      '130(40～150)'     -> '130'    同上
      '1.96-6.55'        -> 原样保留  裸区间，没有标称值，不猜
      '-12' / '45.7'     -> 原样保留  单值
    """
    s = (s or "").strip()
    if not s:
        return ""
    m = re.match(r"^(-?\d+(?:\.\d+)?)\s*[（(]", s)          # 标称值(区间)
    if m:
        return m.group(1)
    nums = re.findall(r"-?\d+(?:\.\d+)?", s)
    if len(nums) >= 2 and re.search(r"[\d)]\s*[-~～]\s*[\d(]", s):
        return s                                            # 裸区间，原样留
    if len(nums) == 1:
        return nums[0]
    return s


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print("  !! 读不了", os.path.basename(path), e)
        return []


def main():
    with open(OUT, encoding="utf-8-sig", newline="") as f:
        mine = list(csv.reader(f))
    header, body = mine[0], mine[1:]
    seen = {(r[0], r[1], r[4], r[5], r[8]) for r in body}

    added, dropped = [], []
    for p in sorted(glob.glob(os.path.join(BASE, "_tmp_pg", "agent_*.json"))):
        rows = load(p)
        src = os.path.basename(p)
        print("%s: %d 行" % (src, len(rows)))
        for i, r in enumerate(rows):
            g = lambda k: (r.get(k) or "").strip()
            brand, model = g("品牌"), g("型号")
            if not brand:
                brand = "(招标未指定品牌)"
            if not model:
                model = "(原文未给型号代码)"
            hv = [g("制热量_kW"), g("输入功率_kW"), g("COP"), g("额定制热量_kW")]
            tv = [g("源侧温度_C"), g("出水温度_C")]
            # 把 "≥5.4（...）" 这类还原成纯数值；同时把原文附注挪进机组类型，保证不丢信息
            rated = g("额定制热量_kW")
            if rated and num(rated) and (rated != num(rated)):
                pass
            ok_val = any(num(v) for v in hv)
            ok_temp = any(num(v) for v in tv)
            nat = g("数据性质")
            if nat not in ALLOWED:
                dropped.append((brand, model, src + " #%d" % (i + 1), "数据性质非法: %s" % nat))
                continue
            if not ok_val:
                dropped.append((brand, model, src + " #%d" % (i + 1), "无任何制热量/功率/COP 数值"))
                continue
            if not ok_temp:
                dropped.append((brand, model, src + " #%d" % (i + 1), "无环温/出水温度，无法定位"))
                continue
            # 额定制热量列若混了长注释，只留数值，注释搬进机组类型
            note = ""
            if rated and num(rated):
                if rated.strip() != num(rated):
                    note = "（原文注明：%s）" % rated.strip()
                rated = num(rated)
            key = (brand, model, g("源侧温度_C"), g("出水温度_C"), g("COP"))
            if key in seen:
                dropped.append((brand, model, src + " #%d" % (i + 1), "与已有行重复"))
                continue
            seen.add(key)
            typ = g("机组类型") + note
            # 代理用 ⚠ 标记的存疑/矛盾项，必须带进 CSV，否则 schema 里没地方放
            n = g("note")
            if "⚠" in n:
                warn = n.split("⚠", 1)[1].split("。")[0].strip()
                typ += "【存疑：" + warn + "】"
            added.append([brand, model, typ, rated, g("源侧温度_C"), g("出水温度_C"),
                          norm_val(g("制热量_kW")), norm_val(g("输入功率_kW")),
                          norm_val(g("COP")), nat, g("适用城市"), g("年份"),
                          g("来源文件"), g("source_url")])

    with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(body + added)

    print("\n并入 %d 行，丢弃 %d 行。总行数 %d" % (len(added), len(dropped), len(body) + len(added)))
    with open(os.path.join(BASE, "_tmp_pg", "_dropped.txt"), "w", encoding="utf-8") as f:
        for b, m, s, why in dropped:
            f.write("%s | %s | %s | %s\n" % (b, m, s, why))
    print("丢弃明细 -> _tmp_pg/_dropped.txt")


if __name__ == "__main__":
    main()
