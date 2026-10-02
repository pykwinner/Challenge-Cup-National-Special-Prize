# -*- coding: utf-8 -*-
"""中国能效标识网 —— 按 (生产者, 型号) 抓取备案明细（制热量/输入功率/COPh/COPdh/HSPF/能效等级）。

输入：sources/能效标识网/el_records_list.json（由 el_harvest.py 生成）
输出：sources/能效标识网/el_details.jsonl（逐行追加，可中断续跑）
"""
import json
import os
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(BASE, ".."))
SRC = os.path.join(ROOT, "sources", "能效标识网")
LIST = os.path.join(SRC, "el_records_list.json")
OUT = os.path.join(SRC, "el_details.jsonl")

DETAIL = "https://www.energylabel.com.cn/admin-api/gateway/productRegistration/productDetailById"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
HDRS = ["-H", "Content-Type: application/json;charset=utf-8",
        "-H", "Referer: https://www.energylabel.com.cn/productFiling",
        "-H", "Origin: https://www.energylabel.com.cn"]

# 只抓这三类：低环温空气源热泵(2024/2019) + 水(地)源热泵 —— 与三城两温位直接对应
WANT_CODES = ["73", "17", "74", "12"]

BRANDS = ["纽恩泰", "海悟", "芬尼克兹", "中广电器", "生能", "阿尔普尔", "力诺瑞特", "格力",
          "美的", "海尔", "天加", "盾安", "同方", "克莱门特", "美意", "顿汉布什", "麦克维尔",
          "约克", "开利", "特灵", "国祥", "欧科", "瑞星", "华天成", "德富", "太阳雨",
          "四季沐歌", "迪森", "派沃", "申菱", "宏力", "贝莱特", "富尔达"]


def post(url, payload, timeout=45, tries=3):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last = None
    for i in range(tries):
        p = subprocess.run(["curl", "-sk", "--max-time", str(timeout), "-A", UA] + HDRS +
                           ["-X", "POST", "--data-binary", "@-", url],
                           input=body, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            return json.loads(p.stdout.decode("utf-8"))
        except Exception as e:
            last = e
            time.sleep(1.0 * (i + 1))
    return {"code": None, "err": str(last)}


def main():
    d = json.load(open(LIST, encoding="utf-8"))
    recs = d["records"]
    seen = set()
    if os.path.exists(OUT):
        for line in open(OUT, encoding="utf-8"):
            try:
                o = json.loads(line)
                seen.add((o.get("productId"), o.get("productTypeCode")))
            except Exception:
                pass
    todo = []
    for c in WANT_CODES:
        for it in recs.get(c, []):
            pn = it.get("producerName", "") or ""
            if not any(k in pn for k in BRANDS):
                continue
            key = (it.get("id"), c)
            if key in seen:
                continue
            todo.append((c, it))
    print("todo:", len(todo), "already:", len(seen), flush=True)
    f = open(OUT, "a", encoding="utf-8")
    n = 0
    for c, it in todo:
        r = post(DETAIL, {"productId": it["id"], "productTypeCode": c, "mark": 854, "isOld": 1})
        data = r.get("data")
        if isinstance(data, dict):
            rec = {"productId": it["id"], "productTypeCode": c,
                   "productType": data.get("productType"),
                   "productModel": data.get("productModel"),
                   "registrationNumber": data.get("registrationNumber"),
                   "producerName": data.get("producerName"),
                   "nxLever": data.get("nxLever"),
                   "announcementTime": data.get("announcementTime"),
                   "standard": data.get("standard"),
                   "items": {x.get("name"): x.get("value") for x in (data.get("list") or [])},
                   "list_raw": data.get("list")}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1
        if n % 50 == 0:
            f.flush()
            print(f"  done {n}/{len(todo)}", flush=True)
        time.sleep(0.12)
    f.flush()
    f.close()
    print("wrote", OUT, n)


if __name__ == "__main__":
    sys.exit(main())
