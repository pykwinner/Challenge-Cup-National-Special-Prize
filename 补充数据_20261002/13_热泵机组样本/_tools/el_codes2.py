# -*- coding: utf-8 -*-
"""中国能效标识网 产品备案查询 —— 用 productType(代码字符串) 枚举类别名。

接口：POST /admin-api/gateway/productRegistration/productRegistrationList
该接口在站点前端白名单内（无需登录）即可读取公开公示数据。
"""
import json
import subprocess
import sys
import time

BASE = "https://www.energylabel.com.cn/admin-api/gateway/productRegistration/productRegistrationList"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
HDRS = ["-H", "Content-Type: application/json;charset=utf-8",
        "-H", "Referer: https://www.energylabel.com.cn/productFiling",
        "-H", "Origin: https://www.energylabel.com.cn"]


def post(payload, timeout=60):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    p = subprocess.run(["curl", "-sk", "--max-time", str(timeout), "-A", UA] + HDRS +
                       ["-X", "POST", "--data-binary", "@-", BASE],
                       input=body, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        return json.loads(p.stdout.decode("utf-8"))
    except Exception:
        return {"code": None, "raw": p.stdout[:200].decode("utf-8", "replace")}


def main():
    found = {}
    for code in range(1, 130):
        r = post({"mark": 854, "productType": str(code), "productModel": "",
                  "registrationNumber": "", "producerName": "",
                  "current": 1, "pageSize": 1, "isOld": 1})
        d = r.get("data")
        if isinstance(d, dict) and d.get("list"):
            it = d["list"][0]
            found[str(code)] = it.get("productType")
            print(f"{code:>3} | {it.get('productType')}", flush=True)
        time.sleep(0.12)
    with open("codes2.json", "w", encoding="utf-8") as f:
        json.dump(found, f, ensure_ascii=False, indent=1)
    print("wrote codes2.json", len(found))


if __name__ == "__main__":
    sys.exit(main())
