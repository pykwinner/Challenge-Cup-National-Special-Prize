# -*- coding: utf-8 -*-
"""中国能效标识网 产品备案查询 —— 枚举 productTypeCode 对应的产品类别名。

站点：https://www.energylabel.com.cn/productFiling
接口：POST /admin-api/gateway/productRegistration/productRegistrationList
说明：该接口为站点白名单（无需登录）的公开查询接口，本脚本只读取公开公示数据。
"""
import json
import subprocess
import sys
import time

BASE = "https://www.energylabel.com.cn/admin-api/gateway/productRegistration/productRegistrationList"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
HDRS = [
    "-H", "Content-Type: application/json;charset=utf-8",
    "-H", "Referer: https://www.energylabel.com.cn/productFiling",
    "-H", "Origin: https://www.energylabel.com.cn",
]


def post(url, payload, timeout=60):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    p = subprocess.run(
        ["curl", "-sk", "--max-time", str(timeout), "-A", UA] + HDRS +
        ["-X", "POST", "--data-binary", "@-", url],
        input=body, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        return json.loads(p.stdout.decode("utf-8"))
    except Exception:
        return {"code": None, "raw": p.stdout[:300].decode("utf-8", "replace")}


def main():
    found = {}
    for code in range(0, 130):
        r = post(BASE, {"mark": 854, "productType": "", "productModel": "",
                        "registrationNumber": "", "producerName": "",
                        "productTypeCode": str(code), "current": 1, "pageSize": 1,
                        "isOld": 1})
        d = r.get("data")
        if isinstance(d, dict) and d.get("list"):
            it = d["list"][0]
            found[str(code)] = {"productType": it.get("productType"), "total": d.get("total")}
            print(f"code={code:>3}  total={d.get('total'):>6}  {it.get('productType')}", flush=True)
        elif isinstance(d, dict) and d.get("total"):
            found[str(code)] = {"productType": "", "total": d.get("total")}
            print(f"code={code:>3}  total={d.get('total'):>6}  (list empty)", flush=True)
        time.sleep(0.15)
    with open("codes.json", "w", encoding="utf-8") as f:
        json.dump(found, f, ensure_ascii=False, indent=1)
    print("wrote codes.json", len(found))


if __name__ == "__main__":
    sys.exit(main())
