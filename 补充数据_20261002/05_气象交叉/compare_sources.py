# -*- coding: utf-8 -*-
"""NASA POWER(包内) vs Open-Meteo/ERA5(本包) 逐时交叉核对。"""
import csv, math, glob, statistics as st, os, json

PKG = r"C:\Users\13349\Desktop\小挑\三城市论文核验数据包_20261002\verified_public"
PAIRS = [("outdoor_temp","temperature_2m","气温 °C"),
         ("relative_humidity_pct","relative_humidity_2m","相对湿度 %"),
         ("wind_speed_10m_m_s","wind_speed_10m","风速 m/s"),
         ("solar_irradiation_wh_m2","shortwave_radiation","水平面总辐射 Wh/m2")]

def load_nasa(city):
    d = {}
    for f in sorted(glob.glob(os.path.join(PKG,"weather_hourly",f"{city}_weather_20*.csv"))):
        if "2025" in f: continue
        with open(f, encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                d[r["time"]] = r
    return d

def load_om(city):
    d = {}
    with open(f"tables/openmeteo_{city}_hourly_beijing.csv", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            t = r["timestamp_Asia_Shanghai"]
            d[t if len(t) > 16 else t + ":00"] = r
    return d

def stats(pairs):
    diffs = [a-b for a,b in pairs]
    n = len(diffs)
    mean_b = sum(diffs)/n
    mae = sum(abs(x) for x in diffs)/n
    rmse = math.sqrt(sum(x*x for x in diffs)/n)
    sd_a = st.pstdev([a for a,_ in pairs]); sd_b = st.pstdev([b for _,b in pairs])
    ma = sum(a for a,_ in pairs)/n; mb = sum(b for _,b in pairs)/n
    cov = sum((a-ma)*(b-mb) for a,b in pairs)/n
    r = cov/(sd_a*sd_b) if sd_a>0 and sd_b>0 else float('nan')
    return n, mean_b, mae, rmse, r

rows = []
for city in ["scenario1_urumqi","scenario2_guyuan","scenario3_wuhan"]:
    nasa, om = load_nasa(city), load_om(city)
    for nc, oc, label in PAIRS:
        pairs = []
        for t, nr in nasa.items():
            orow = om.get(t)
            if not orow: continue
            try: a, b = float(nr[nc]), float(orow[oc])
            except (ValueError, KeyError): continue
            if b is None: continue
            pairs.append((a,b))
        n, bias, mae, rmse, r = stats(pairs)
        rows.append({"城市/场景":city,"变量":label,"配对小时数":n,"均值差(ERA5-NASA)":round(bias,4),
                     "MAE":round(mae,4),"RMSE":round(rmse,4),"相关系数r":round(r,5),
                     "NASA均值":round(sum(a for a,_ in pairs)/n,4),"ERA5均值":round(sum(b for _,b in pairs)/n,4)})
        print(rows[-1])

with open("tables/交叉核对_NASA_vs_ERA5.csv","w",newline="",encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
print("written")
