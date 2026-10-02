# -*- coding: utf-8 -*-
"""把 A/B/C/D 四批的 _rows_*.csv 与 _receipts_*.json 合并成两个交付表。
只做拼接与列名映射，不改动任何数值。"""
import csv, json, os, glob

BASE = r'C:\Users\13349\Desktop\小挑\补充数据_20261002\11_经济边界'
SRC = os.path.join(BASE, 'sources')
TAB = os.path.join(BASE, 'tables')
HDR = ["类别", "对象", "参数", "数值", "单位", "年份",
       "数据性质(合同/监审/招标/报道/规划)", "来源文件", "source_url"]

os.makedirs(TAB, exist_ok=True)

# ---- 1) 合并数值表 ----
rows, seen = [], set()
for f in ['_rows_A.csv', '_rows_B.csv', '_rows_C.csv', '_rows_D.csv']:
    p = os.path.join(SRC, f)
    if not os.path.exists(p):
        print('skip (missing):', f); continue
    with open(p, encoding='utf-8-sig', newline='') as fh:
        r = list(csv.reader(fh))
    body = [x for x in r[1:] if any(c.strip() for c in x)]
    print(f, '->', len(body), 'rows')
    for x in body:
        key = tuple(x)
        if key in seen:
            continue
        seen.add(key)
        rows.append(x)

out = os.path.join(TAB, 'economic_boundary_extracted.csv')
with open(out, 'w', encoding='utf-8-sig', newline='') as fh:
    w = csv.writer(fh)
    w.writerow(HDR)
    w.writerows(rows)
print('WROTE', out, len(rows), 'rows')

# ---- 2) 合并七列登记表 ----
recs = []
for f in sorted(glob.glob(os.path.join(SRC, '_receipts_*.json'))):
    recs += json.load(open(f, encoding='utf-8'))


def norm_loc(v):
    """统一成 'sources/文件名' 相对路径（原包惯例），并把同目录文件核实存在。"""
    if not v:
        return ''
    name = os.path.basename(v.replace('\\', '/'))
    if os.path.exists(os.path.join(SRC, name)):
        return 'sources/' + name
    return v  # 指向包外的件（如 03_规划与环评）保持原样


for r in recs:
    r['local_path'] = norm_loc(r.get('local_path'))
reg = os.path.join(BASE, '来源与适用范围_补充.csv')
with open(reg, 'w', encoding='utf-8-sig', newline='') as fh:
    w = csv.writer(fh)
    w.writerow(["数据类", "状态", "时间范围", "位置", "来源", "本次可核验内容", "限制"])
    for r in recs:
        w.writerow([
            r.get('data_class', ''),
            r.get('status', ''),
            r.get('time_range', ''),
            r.get('local_path') or '',
            r.get('url', ''),
            r.get('summary', ''),
            r.get('limits', ''),
        ])
print('WROTE', reg, len(recs), 'rows')

# ---- 3) 合并 receipts.json ----
allr = os.path.join(BASE, 'receipts.json')
json.dump(recs, open(allr, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print('WROTE', allr)
