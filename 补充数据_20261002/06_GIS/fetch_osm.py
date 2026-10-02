# -*- coding: utf-8 -*-
"""OSM/Overpass：三城建筑规模与供热相关设施标签抓取。"""
import json, time, hashlib, urllib.request, urllib.parse, os

PROXY="http://127.0.0.1:7897"
MIRRORS=["https://overpass-api.de/api/interpreter","https://overpass.kumi.systems/api/interpreter"]
CITIES={"scenario1_urumqi":(43.70,87.45,43.95,87.75),
        "scenario2_guyuan":(35.95,106.15,36.10,106.35),
        "scenario3_wuhan": (30.45,114.15,30.70,114.45)}
opener=urllib.request.build_opener(urllib.request.ProxyHandler({"http":PROXY,"https":PROXY}))
os.makedirs("sources",exist_ok=True); os.makedirs("tables",exist_ok=True)

def query(q):
    for m in MIRRORS:
        try:
            req=urllib.request.Request(m,data=urllib.parse.urlencode({"data":q}).encode(),
                                       headers={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with opener.open(req,timeout=180) as r:
                return json.loads(r.read().decode("utf-8")), m
        except Exception as e:
            print("  mirror fail",m,e)
            time.sleep(3)
    return None,None

receipts=[]; summary=[]
for city,(s,w,n,e) in CITIES.items():
    bbox=f"{s},{w},{n},{e}"
    # 1) 建筑数量 + 有高度/层数标签的数量
    q=f'[out:json][timeout:180];way["building"]({bbox});out count;'
    d,m=query(q); time.sleep(4)
    n_bldg=d["elements"][0]["tags"]["ways"] if d else None
    q2=(f'[out:json][timeout:180];('
        f'way["building"]["height"]({bbox});way["building"]["building:levels"]({bbox}););out count;')
    d2,_=query(q2); time.sleep(4)
    n_h=d2["elements"][0]["tags"]["ways"] if d2 else None
    # 2) 供热/能源相关设施
    q3=(f'[out:json][timeout:180];('
        f'nwr["power"="plant"]({bbox});nwr["man_made"="chimney"]({bbox});'
        f'nwr["landuse"="industrial"]({bbox});nwr["building"="industrial"]({bbox});'
        f'nwr["plant:source"]({bbox});nwr["heating"]({bbox}););out center tags;')
    d3,m3=query(q3); time.sleep(4)
    fac=[]
    if d3:
        for el in d3.get("elements",[]):
            t=el.get("tags",{})
            fac.append({"osm_type":el["type"],"osm_id":el["id"],
                        "name":t.get("name",""),"power":t.get("power",""),
                        "man_made":t.get("man_made",""),"landuse":t.get("landuse",""),
                        "building":t.get("building",""),"plant:source":t.get("plant:source",""),
                        "lat":el.get("center",{}).get("lat") or el.get("lat"),
                        "lon":el.get("center",{}).get("lon") or el.get("lon")})
    fn=f"sources/osm_{city}_facilities.json"
    json.dump(d3,open(fn,"w",encoding="utf-8"),ensure_ascii=False)
    summary.append({"城市/场景":city,"bbox":bbox,"建筑数(way)":n_bldg,"带高度/层数标签的建筑数":n_h,
                    "供热能源相关要素数":len(fac),"设施原始件":fn,
                    "抓取时间(Asia/Shanghai)":time.strftime("%Y-%m-%d %H:%M:%S"),"数据源":"OpenStreetMap / Overpass API"})
    import csv
    with open(f"tables/osm_{city}_facilities.csv","w",newline="",encoding="utf-8-sig") as fh:
        if fac:
            w2=csv.DictWriter(fh,fieldnames=list(fac[0].keys())); w2.writeheader(); w2.writerows(fac)
    print(city,"buildings=",n_bldg,"tagged=",n_h,"facilities=",len(fac))
    time.sleep(5)

import csv
with open("tables/osm_city_summary.csv","w",newline="",encoding="utf-8-sig") as fh:
    w=csv.DictWriter(fh,fieldnames=list(summary[0].keys())); w.writeheader(); w.writerows(summary)
print("done")
