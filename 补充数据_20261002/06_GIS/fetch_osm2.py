# -*- coding: utf-8 -*-
import json,time,urllib.request,urllib.parse,os,csv
PROXY="http://127.0.0.1:7897"
MIRRORS=["https://overpass.kumi.systems/api/interpreter","https://overpass-api.de/api/interpreter"]
CENTER={"scenario1_urumqi":(43.825,87.616),"scenario2_guyuan":(36.004,106.280),"scenario3_wuhan":(30.592,114.305)}
D=0.06
opener=urllib.request.build_opener(urllib.request.ProxyHandler({"http":PROXY,"https":PROXY}))
def Q(q):
    for m in MIRRORS:
        for attempt in range(2):
            try:
                req=urllib.request.Request(m,data=urllib.parse.urlencode({"data":q}).encode(),
                    headers={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
                with opener.open(req,timeout=240) as r: return json.loads(r.read().decode())
            except Exception as e:
                print("  fail",m,attempt,e,flush=True); time.sleep(5)
    return None
rows=[]
for city,(lat,lon) in CENTER.items():
    s,w,n,e=lat-D,lon-D,lat+D,lon+D; bbox=f"{s:.4f},{w:.4f},{n:.4f},{e:.4f}"
    d=Q(f'[out:json][timeout:200];way["building"]({bbox});out count;'); time.sleep(8)
    nb=d["elements"][0]["tags"]["ways"] if d else None
    print(city,"buildings",nb,flush=True)
    d2=Q(f'[out:json][timeout:200];(nwr["power"="plant"]({bbox});nwr["man_made"="chimney"]({bbox});nwr["landuse"="industrial"]({bbox});nwr["building"="industrial"]({bbox}););out center tags;'); time.sleep(8)
    fac=[]
    for el in (d2 or {}).get("elements",[]):
        t=el.get("tags",{}); c=el.get("center",{})
        fac.append({"osm_type":el["type"],"osm_id":el["id"],"name":t.get("name",""),
                    "power":t.get("power",""),"man_made":t.get("man_made",""),
                    "landuse":t.get("landuse",""),"building":t.get("building",""),
                    "plant:source":t.get("plant:source",""),
                    "lat":c.get("lat") or el.get("lat"),"lon":c.get("lon") or el.get("lon")})
    json.dump(d2,open(f"sources/osm_{city}_facilities.json","w",encoding="utf-8"),ensure_ascii=False)
    if fac:
        with open(f"tables/osm_{city}_facilities.csv","w",newline="",encoding="utf-8-sig") as fh:
            w2=csv.DictWriter(fh,fieldnames=list(fac[0].keys())); w2.writeheader(); w2.writerows(fac)
    rows.append({"城市/场景":city,"bbox":bbox,"中心坐标":"%.4f,%.4f"%(lat,lon),
                 "建筑数(way)":nb if nb is not None else "not_found",
                 "供热能源相关要素数":len(fac),
                 "抓取时间(Asia/Shanghai)":time.strftime("%Y-%m-%d %H:%M:%S"),
                 "数据源":"OpenStreetMap / Overpass API","许可":"ODbL"})
    print(city,"facilities",len(fac),flush=True); time.sleep(5)
with open("tables/osm_city_summary.csv","w",newline="",encoding="utf-8-sig") as fh:
    w=csv.DictWriter(fh,fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
print("DONE")
