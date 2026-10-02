# -*- coding: utf-8 -*-
"""抓 Open-Meteo ERA5 历史逐时（三城 2020-01-01 ~ 2025-04-30 UTC），与包内 NASA POWER 交叉核对。"""
import json, time, hashlib, urllib.request, urllib.parse, os, sys, csv
from datetime import datetime, timezone, timedelta

PROXY = "http://127.0.0.1:7897"
CITIES = {
    "scenario1_urumqi": (43.83, 87.62),
    "scenario2_guyuan": (36.02, 106.24),
    "scenario3_wuhan":  (30.59, 114.31),
}
VARS = ["temperature_2m","relative_humidity_2m","dewpoint_2m","wind_speed_10m",
        "wind_speed_100m","shortwave_radiation","direct_normal_irradiance",
        "diffuse_radiation","surface_pressure","precipitation","cloud_cover"]
START, END = "2020-01-01", "2025-04-30"

opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))
receipts = []
os.makedirs("sources", exist_ok=True)
os.makedirs("tables", exist_ok=True)

for city, (lat, lon) in CITIES.items():
    q = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon, "start_date": START, "end_date": END,
        "hourly": ",".join(VARS), "timezone": "UTC", "wind_speed_unit": "ms",
    })
    url = f"https://archive-api.open-meteo.com/v1/archive?{q}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    t0 = time.time()
    try:
        with opener.open(req, timeout=180) as r:
            raw = r.read()
        j = json.loads(raw.decode("utf-8"))
        rawpath = f"sources/openmeteo_{city}_{START}_{END}_UTC.json"
        open(rawpath, "wb").write(raw)
        h = j["hourly"]; n = len(h["time"])
        # 转北京时间并落 CSV
        csvpath = f"tables/openmeteo_{city}_hourly_beijing.csv"
        with open(csvpath, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["timestamp_Asia_Shanghai"] + VARS)
            for i in range(n):
                ts = datetime.strptime(h["time"][i], "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc) + timedelta(hours=8)
                w.writerow([ts.strftime("%Y-%m-%d %H:%M")] + [h[v][i] for v in VARS])
        receipts.append({
            "数据类": f"Open-Meteo ERA5 逐时气象（{city}）", "url": url, "抓取时间": time.strftime("%Y-%m-%d %H:%M:%S"),
            "http状态": 200, "行数": n, "用户本地文件": rawpath, "csv": csvpath,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "站点": {"latitude": j.get("latitude"), "longitude": j.get("longitude"), "elevation": j.get("elevation")},
            "耗时s": round(time.time()-t0, 1),
        })
        print(f"[OK] {city} rows={n} coord=({j.get('latitude')},{j.get('longitude')}) elev={j.get('elevation')} {round(time.time()-t0,1)}s")
    except Exception as e:
        receipts.append({"数据类": f"Open-Meteo ERA5 逐时气象（{city}）", "url": url,
                         "抓取时间": time.strftime("%Y-%m-%d %H:%M:%S"), "http状态": f"ERROR {e}"})
        print(f"[FAIL] {city}: {e}", file=sys.stderr)
    time.sleep(2)

json.dump(receipts, open("receipts_openmeteo.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("done")
