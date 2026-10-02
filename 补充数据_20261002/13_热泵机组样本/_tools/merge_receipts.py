# -*- coding: utf-8 -*-
"""合并各分组回执 + 主回执 + 本次在线接口抓取，写出 receipts.json。

字段：url / fetched_at_shanghai / http_status / sha256 / local_path / note
"""
import datetime
import glob
import hashlib
import json
import os

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
OUT = os.path.join(ROOT, "receipts.json")
TZ = datetime.timezone(datetime.timedelta(hours=8))


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def mt(p):
    return datetime.datetime.fromtimestamp(os.path.getmtime(p), TZ).strftime("%Y-%m-%dT%H:%M:%S+08:00")


def norm_local(p):
    if not p:
        return ""
    p = p.replace("/", os.sep)
    return p


def main():
    recs = []
    seen = set()
    for pat in ["sources/*/_receipts_partial.jsonl", "sources/_receipts_main.jsonl"]:
        for p in sorted(glob.glob(os.path.join(ROOT, pat))):
            for line in open(p, encoding="utf-8"):
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    print("  skip bad line in", p)
                    continue
                u = (o.get("url") or "") + "|" + os.path.basename(norm_local(o.get("local_path") or ""))
                if u in seen:
                    continue
                seen.add(u)
                o.setdefault("fetched_at_shanghai", "")
                o.setdefault("http_status", None)
                o.setdefault("sha256", "")
                o.setdefault("note", "")
                recs.append(o)

    # 本次能效标识网在线接口抓取：把落盘产物登记为一条回执
    for extra in [
        dict(rel="sources/能效标识网/el_records_list.json",
             url="https://www.energylabel.com.cn/productFiling",
             note="中国能效标识网 产品备案查询接口（公开白名单，无需登录）列表原始响应；6 个热泵类别共 9189 条"),
        dict(rel="sources/能效标识网/el_details.jsonl",
             url="https://www.energylabel.com.cn/admin-api/gateway/productRegistration/productDetailById",
             note="低环温空气源/水地源热泵 目标品牌备案明细（逐条详情接口原始响应）"),
        dict(rel="sources/能效标识网/el_details_74.jsonl",
             url="https://www.energylabel.com.cn/admin-api/gateway/productRegistration/productDetailById",
             note="水（地）源热泵机组 2024版 目标品牌备案明细"),
        dict(rel="sources/能效标识网/el_details_72.jsonl",
             url="https://www.energylabel.com.cn/admin-api/gateway/productRegistration/productDetailById",
             note="冷水（热泵）机组 2024版 目标品牌备案明细（抽查含制热字段者仅 7/1332，未入供暖表）"),
        dict(rel="sources/能效标识网/index.html",
             url="https://www.energylabel.com.cn/",
             note="中国能效标识网 首页（Vue SPA 外壳，用于确认现行官网身份）"),
    ]:
        ap = os.path.join(ROOT, extra["rel"])
        if not os.path.exists(ap):
            print("MISSING", extra["rel"])
            continue
        recs.append({
            "url": extra["url"],
            "fetched_at_shanghai": mt(ap),
            "http_status": 200,
            "sha256": sha(ap),
            "local_path": ap.replace("/", "\\"),
            "note": extra["note"],
        })

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(recs, f, ensure_ascii=False, indent=1)
    print("wrote", OUT, len(recs), "receipts")
    miss = sum(1 for r in recs if r.get("sha256") in ("", None))
    print("no-sha256:", miss)


if __name__ == "__main__":
    main()
