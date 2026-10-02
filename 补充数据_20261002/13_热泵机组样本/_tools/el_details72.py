# -*- coding: utf-8 -*-
"""中国能效标识网 —— 并行抓取「72 冷水（热泵）机组 2024版」目标品牌备案明细。

输出：sources/能效标识网/el_details_72.jsonl
与 el_details.py 分开写不同文件，避免并发写冲突。
"""
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(BASE, ".."))
SRC = os.path.join(ROOT, "sources", "能效标识网")
LIST = os.path.join(SRC, "el_records_list.json")
OUT = os.path.join(SRC, "el_details_72.jsonl")

DETAIL = "https://www.energylabel.com.cn/admin-api/gateway/productRegistration/productDetailById"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

BRANDS = ["纽恩泰", "海悟", "芬尼克兹", "中广电器", "生能", "阿尔普尔", "力诺瑞特", "格力",
          "美的", "海尔", "天加", "盾安", "同方", "克莱门特", "美意", "顿汉布什", "麦克维尔",
          "约克", "开利", "特灵", "国祥", "欧科", "瑞星", "华天成", "德富", "太阳雨",
          "四季沐歌", "迪森", "派沃", "申菱", "宏力", "贝莱特", "富尔达"]

CODES = ["72", "11"]


def post(payload, timeout=45, tries=3):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    cmd = ["curl", "-sk", "--max-time", str(timeout), "-A", UA,
           "-H", "Content-Type: application/json;charset=utf-8",
           "-H", "Referer: https://www.energylabel.com.cn/productFiling",
           "-H", "Origin: https://www.energylabel.com.cn",
           "-X", "POST", "--data-binary", "@-", DETAIL]
    for i in range(tries):
        p = subprocess.run(cmd, input=body, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            return json.loads(p.stdout.decode("utf-8"))
        except Exception:
            time.sleep(0.8 * (i + 1))
    return {"code": None}


def work(job):
    c, it = job
    r = post({"productId": it["id"], "productTypeCode": c, "mark": 854, "isOld": 1})
    data = r.get("data")
    if not isinstance(data, dict):
        return None
    return {"productId": it["id"], "productTypeCode": c,
            "productType": data.get("productType"),
            "productModel": data.get("productModel"),
            "registrationNumber": data.get("registrationNumber"),
            "producerName": data.get("producerName"),
            "nxLever": data.get("nxLever"),
            "announcementTime": data.get("announcementTime"),
            "standard": data.get("standard"),
            "items": {x.get("name"): x.get("value") for x in (data.get("list") or [])}}


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
    for c in CODES:
        for it in recs.get(c, []):
            pn = it.get("producerName", "") or ""
            if any(k in pn for k in BRANDS) and (it.get("id"), c) not in seen:
                todo.append((c, it))
    print("todo:", len(todo), "already:", len(seen), flush=True)
    f = open(OUT, "a", encoding="utf-8")
    n = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        for res in ex.map(work, todo):
            if res:
                f.write(json.dumps(res, ensure_ascii=False) + "\n")
                n += 1
                if n % 100 == 0:
                    f.flush()
                    print(f"  done {n}/{len(todo)}", flush=True)
    f.flush()
    f.close()
    print("wrote", OUT, n)


if __name__ == "__main__":
    sys.exit(main())
