# -*- coding: utf-8 -*-
"""中国能效标识网 产品备案查询 —— 抓取热泵相关类别的公开备案记录。

类别代码（由站点接口枚举得到）：
  11  热泵热水机（器）2013版
  12  水（地）源热泵机组 2014版
  17  低环境温度空气源热泵（冷水）机组 2019版
  72  冷水（热泵）机组 2024版
  73  低环境温度空气源热泵（冷水）机组 2024版
  74  水（地）源热泵机组 2024版

只读取站点前端白名单内（无需登录）的公开公示数据。
"""
import json
import os
import subprocess
import sys
import time

BASE = "https://www.energylabel.com.cn/admin-api/gateway/productRegistration/productRegistrationList"
DETAIL = "https://www.energylabel.com.cn/admin-api/gateway/productRegistration/productDetailById"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
HDRS = ["-H", "Content-Type: application/json;charset=utf-8",
        "-H", "Referer: https://www.energylabel.com.cn/productFiling",
        "-H", "Origin: https://www.energylabel.com.cn"]

CODES = {
    "11": "热泵热水机（器）2013版",
    "12": "水（地）源热泵机组 2014版",
    "17": "低环境温度空气源热泵（冷水）机组 2019版",
    "72": "冷水（热泵）机组 2024版",
    "73": "低环境温度空气源热泵（冷水）机组 2024版",
    "74": "水（地）源热泵机组 2024版",
}


def post(url, payload, timeout=60, tries=3):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    for i in range(tries):
        p = subprocess.run(["curl", "-sk", "--max-time", str(timeout), "-A", UA] + HDRS +
                           ["-X", "POST", "--data-binary", "@-", url],
                           input=body, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            return json.loads(p.stdout.decode("utf-8"))
        except Exception:
            time.sleep(1.5 * (i + 1))
    return {"code": None, "raw": p.stdout[:200].decode("utf-8", "replace")}


def harvest_list(code):
    out, page = [], 1
    while page <= 60:
        r = post(BASE, {"mark": 854, "productType": code, "productModel": "",
                        "registrationNumber": "", "producerName": "",
                        "current": page, "pageSize": 50, "isOld": 1})
        d = r.get("data") or {}
        lst = d.get("list") or []
        if not lst:
            break
        for it in lst:
            it["_code"] = code
        out.extend(lst)
        print(f"  code={code} page={page} +{len(lst)} (cum {len(out)})", flush=True)
        if len(lst) < 50:
            break
        page += 1
        time.sleep(0.2)
    return out


def main():
    allrec = {}
    for code, name in CODES.items():
        print(f"[{code}] {name}", flush=True)
        allrec[code] = harvest_list(code)
        print(f"  -> {len(allrec[code])} records", flush=True)
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "sources",
                     "能效标识网", "el_records_list.json")
    p = os.path.normpath(p)
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"codes": CODES, "records": allrec}, f, ensure_ascii=False, indent=1)
    tot = sum(len(v) for v in allrec.values())
    print("wrote", p, "total", tot)


if __name__ == "__main__":
    sys.exit(main())
