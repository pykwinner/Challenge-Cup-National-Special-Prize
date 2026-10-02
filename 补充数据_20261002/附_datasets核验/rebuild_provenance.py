# -*- coding: utf-8 -*-
"""反推 datasets/osm 的抽取查询，并落盘当前快照。bbox 顺序=(南,西,北,东)。"""
import json,subprocess,os,sys,time,hashlib
sys.stdout.reconfigure(encoding='utf-8')
M="https://maps.mail.ru/osm/tools/overpass/api/interpreter"
env=dict(os.environ, https_proxy="http://127.0.0.1:7897", http_proxy="http://127.0.0.1:7897")
os.makedirs("osm_snapshot", exist_ok=True)
# (南,西,北,东) —— Overpass bbox 顺序
BB={'urumqi':('42.92,86.79,45.00,88.97',27194),
    'guyuan':('35.24,105.32,36.52,106.95',3531),
    'wuhan':('29.97,113.70,31.36,115.08',56207)}
def Q(q,tries=4):
    for i in range(tries):
        p=subprocess.run(["curl","-s","--max-time","420","-A","Mozilla/5.0","-X","POST",
                          "--data-urlencode",f"data={q}",M],capture_output=True,text=True,env=env)
        try: return json.loads(p.stdout), p.stdout
        except Exception: print(f"   retry {i+1}", flush=True); time.sleep(10)
    return None,None
rec=[]
for city,(bbox,expect) in BB.items():
    q=f'[out:json][timeout:400];way["building"]({bbox});out count;'
    d,raw=Q(q)
    try: got=int(d["elements"][0]["tags"]["ways"])
    except Exception: got=None
    print(f"{city}: bbox={bbox} 反推计数={got} 数据集={expect}", flush=True)
    rec.append({"城市":city,"bbox_南西北东":bbox,"反推查询":q,"反推计数":got,"数据集计数":expect})
    time.sleep(4)
json.dump(rec,open("osm_snapshot/反推查询记录.json","w",encoding="utf-8"),ensure_ascii=False,indent=2)
# 落一份当前快照（固原中心小框，带几何）
bbox='36.00,106.25,36.05,106.31'
d,raw=Q(f'[out:json][timeout:300];way["building"]({bbox});out geom;')
if raw and len(raw)>200:
    p="osm_snapshot/guyuan_central_raw.json"; open(p,"w",encoding="utf-8").write(raw)
    print("快照:",p,hashlib.sha256(raw.encode()).hexdigest()[:16],len(raw),"字节", flush=True)
print("DONE")
